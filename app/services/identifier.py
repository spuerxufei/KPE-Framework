# -*- coding: utf-8 -*-
"""
领域识别器（上下文映射器）模块
"""
import json
from typing import List
from openai import OpenAI
from app.models import StandardizedDataObject
from app.repositories.static_repository import static_repo
from app.config import settings  # 导入全局配置
import re


def extract_json_from_llm_response(response_content: str) -> str:
    """
    【新增 V3.4】从LLM可能返回的Markdown代码块中，稳健地提取出纯粹的JSON字符串。
    """
    if not response_content:
        return ""
    # 优先匹配带json标识的markdown块
    match = re.search(r"```json\s*(\{.*\}|\[.*\])\s*```", response_content, re.DOTALL)
    if match:
        return match.group(1)
    # 其次匹配不带标识的markdown块
    match = re.search(r"```\s*(\{.*\}|\[.*\])\s*```", response_content, re.DOTALL)
    if match:
        return match.group(1)
    # 如果没有找到代码块，假设整个字符串就是JSON（去除可能的前后文）
    # 查找第一个 '{' 或 '[' 和最后一个 '}' 或 ']'
    start = min(response_content.find('{'), response_content.find('['))
    if start == -1: return response_content  # 没找到JSON结构

    end_brace = response_content.rfind('}')
    end_bracket = response_content.rfind(']')
    end = max(end_brace, end_bracket)

    if end == -1 or end < start: return response_content  # 结束符号异常

    return response_content[start:end + 1]

class DomainIdentifier:
    """
    实现了上下文映射器（Context Mapper）的核心逻辑。
    其核心是利用LLM的语义理解能力来识别领域。
    """

    def __init__(self):
        # 在服务实例化时，创建OpenAI客户端
        # 这是一种最佳实践，可以复用客户端连接
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.OPENROUTER_API_KEY,
        )
        self.headers = {
            "HTTP-Referer": settings.HTTP_REFERER,
            "X-Title": settings.X_TITLE,
        }

    def _generate_meta_prompt(self, data: StandardizedDataObject) -> str:
        # ... (这个方法完全不需要修改，保持原样) ...
        domain_definitions = static_repo.get_all_domain_definitions()
        domain_list_str = "\n".join(
            [f"- {name}: {desc}" for name, desc in domain_definitions.items()]
        )
        prompt = f"""
你是一位顶级的业务分析专家，擅长从业务数据中识别其跨领域的业务含义。
你的任务是，根据我提供的“业务数据事件”，从“可用领域列表”中，识别出所有与该事件相关的业务领域。

# 可用领域列表:
{domain_list_str}

# 任务要求:
1. 仔细分析“业务数据事件”中的每一条信息，特别是操作类型和涉及的实体。
2. 你的输出必须是一个JSON格式的数组，其中只包含识别出的领域名称字符串。
3. 如果一个领域与数据完全无关，绝对不要包含它。

---
## 业务数据事件:
{data.source_data_markdown}

# 分析与输出 (JSON数组格式):
"""
        return prompt

    def _invoke_llm_for_identification(self, prompt: str) -> List[str]:
        """
        【V3.4 最终修复版】 调用真实的LLM API进行领域识别，并对返回结果进行净化。
        """
        print(f"    - [输入] 正在向模型 '{settings.IDENTIFIER_MODEL_NAME}' 发送真实的元Prompt...")

        response_content = ""  # 初始化为空字符串
        clean_json_str = ""
        try:
            completion = self.client.chat.completions.create(
                extra_headers=self.headers,
                model=settings.IDENTIFIER_MODEL_NAME,
                messages=[
                    {"role": "system", "content": "你是一个精确的、遵循指令的JSON生成助手。"},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1
            )

            if completion.choices:
                response_content = completion.choices[0].message.content
            else:
                print("    - [错误] LLM API返回了空的 choices 列表。")
                return []

            print(f"    - [输出] LLM(分析模型)返回的原始JSON字符串: {response_content}")

            # 关键修复：在解析之前，先调用净化函数
            clean_json_str = extract_json_from_llm_response(response_content)

            # 提前检查净化后是否为空
            if not clean_json_str:
                raise ValueError("从LLM响应中提取JSON后，内容为空。")

            response_data = json.loads(clean_json_str)

            if isinstance(response_data, dict):
                # 尝试从常见的键中找到列表，例如 "domains", "result", "output"
                for key in ["domains", "result", "output"]:
                    if key in response_data and isinstance(response_data[key], list):
                        return response_data[key]
                # 如果标准键找不到，就遍历所有键
                for key in response_data.keys():
                    if isinstance(response_data[key], list):
                        return response_data[key]
                raise ValueError(f"LLM返回了字典，但其中不包含任何列表。内容: {response_content}")
            elif isinstance(response_data, list):
                return response_data
            else:
                raise ValueError(f"LLM返回的不是一个JSON列表或包含列表的字典。内容: {response_content}")

        except json.JSONDecodeError as e:
            # 专门捕获JSON解析错误
            print(f"    - [错误] JSON解析失败: {e}")
            print(f"    - [调试信息] 原始字符串: '{response_content}'")
            print(f"    - [调试信息] 净化后字符串: '{clean_json_str}'")
            return []
        except Exception as e:
            # 捕获所有其他错误，例如API连接错误、认证错误等
            # 打印异常的类型和详细信息
            print(f"    - [错误] 调用LLM API时发生未知异常: {type(e).__name__}: {e}")
            print(f"    - [调试信息] 此时的响应内容是: '{response_content}'")
            return []

    def identify_domains(self, data: StandardizedDataObject) -> List[str]:
        # ... (这个方法也完全不需要修改，保持原样) ...
        print(f"--- [识别器] 开始识别领域... ---")
        meta_prompt = self._generate_meta_prompt(data)
        identified_domains = self._invoke_llm_for_identification(meta_prompt)
        all_defined_domains = static_repo.get_all_domain_definitions().keys()
        validated_domains = [d for d in identified_domains if d in all_defined_domains]
        print(f"    - [输出] 识别器最终验证后的领域列表: {validated_domains}")
        print(f"--- [识别器] 领域识别完成 ---")
        return validated_domains