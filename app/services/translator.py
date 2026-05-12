# -*- coding: utf-8 -*-
"""
【V3.6 终极统一版 - 模式驱动】领域翻译器模块

本模块实现了一个统一的、模式驱动的DomainTranslator。
它能够根据传入命令的`target_domain`字段，智能地选择
'Naive', 'Monolithic', 或 'Ours' (领域驱动) 中的一种Prompt生成策略。
"""
import json
from openai import OpenAI
from typing import Dict, Any, Union, List
from app.models import KnowledgeGenerationCommand, VerifiedKnowledgeFragment
from app.repositories.static_repository import static_repo
from app.config import settings
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
    start_tuple = (response_content.find('{'), response_content.find('['))
    start = min(i for i in start_tuple if i != -1) if any(i != -1 for i in start_tuple) else -1
    if start == -1: return response_content  # 没找到JSON结构

    end_brace = response_content.rfind('}')
    end_bracket = response_content.rfind(']')
    end = max(end_brace, end_bracket)

    if end == -1 or end < start: return response_content  # 结束符号异常

    return response_content[start:end + 1]


class DomainTranslator:

    def __init__(self):
        """
        初始化翻译器，创建可复用的OpenAI客户端。
        """
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.OPENROUTER_API_KEY,
        )
        self.headers = {
            "HTTP-Referer": settings.HTTP_REFERER,
            "X-Title": f"{settings.X_TITLE} - Translator",
        }
        # 【新增】获取当前运行模式
        # 模式：'Ours', 'Naive', 'Monolithic', 'Ours_No_DynamicPrompt', 'Ours_No_Repair'
        self.mode = getattr(settings, "APP_MODE", "Ours")
        print(f"--- [翻译器] 初始化完成，当前执行模式: {self.mode} ---")

    def translate(self, command: KnowledgeGenerationCommand) -> VerifiedKnowledgeFragment:
        """
        【已重构】: 增加对LLM返回列表的处理逻辑，以适应不同模式。
        """
        try:
            prompt = self._generate_prompt(command)
            raw_llm_output = self._invoke_llm(prompt, settings.TRANSLATOR_MODEL_NAME)

            # --- ** 关键修复：根据模式处理不同的输出类型 ** ---
            validated_payload: Union[Dict, List[Dict]]  # 类型提示

            if command.target_domain == "Monolithic":
                # Monolithic模式可能返回列表
                if isinstance(raw_llm_output, list):
                    print("    - [验证] 检测到LLM返回了对象列表，将逐个处理...")
                    validated_payload = [self._validate_and_correct(item) for item in raw_llm_output]
                elif isinstance(raw_llm_output, dict):
                    validated_payload = [self._validate_and_correct(raw_llm_output)]  # 包装成列表
                else:
                    raise TypeError(f"Monolithic模式下LLM返回了非预期的类型: {type(raw_llm_output)}")
            else:
                # Naive 和 Ours 模式都期望返回单个对象
                if isinstance(raw_llm_output, dict):
                    validated_payload = self._validate_and_correct(raw_llm_output)
                else:
                    raise TypeError(f"非Monolithic模式下LLM返回了非预期的类型: {type(raw_llm_output)}")

            return VerifiedKnowledgeFragment(
                source_command_id=command.command_id,
                target_domain=command.target_domain,
                status="SUCCESS",
                payload=validated_payload
            )

        except Exception as e:
            print(f"--- [Celery任务异常] 命令 {command.command_id} 处理失败: {e} ---")
            return VerifiedKnowledgeFragment(
                source_command_id=command.command_id,
                target_domain=command.target_domain,
                status="VALIDATION_FAILED",
                error_message=f"任务执行时发生异常: {e}"
            )

    def _generate_prompt(self, command: KnowledgeGenerationCommand) -> str:
        """
        根据当前系统模式和目标领域，选择对应的 Prompt 生成策略。
        """
        domain = command.target_domain

        # --- 通用部分：准备所有Prompt都可能需要的信息 ---
        entities_info = "\n".join(
            [f"- 实体 '{e.label}' (类型: {e.entity_type}, 业务ID: {e.business_key}) 的内部唯一ID是 '{e.uuid}'。" for e in
             command.identified_entities]
        )
        role_modeling_instruction = (
            "\n# 角色建模全局指令:\n"
            "--- (规则开始) ---\n"
            "1. **原则**: 任何一个指向“已知实体”的关系，其值都必须是一个包含详细信息的JSON对象。\n"
            "2. **内容**: 这个JSON对象必须同时包含三个字段：'@id' (其值为该实体的内部唯一ID)，'entityType' (其值为该实体的业务类型)，以及 'businessKey' (其值为该实体的原始业务ID)。\n"
            "--- (规则结束) ---"
        )
        event_metadata_str = f"注意：该事件发生于 {command.event_timestamp}。"

        # ====================================================================
        #  模式一：Naive-KG (无领域模型基线)
        # ====================================================================
        if domain == "Naive":
            print("    - [策略: Naive] 正在生成一个极简的、无指导的Prompt...")
            return f"""
你是一位强大的信息抽取和知识图谱构建专家。
你的任务是，从以下“原始业务数据”中，抽取出所有你认为有价值的实体和它们之间的关系，并以JSON-LD的格式输出。

# 重要的规则:
1.  为你发现的所有新实体（事件、对象等），请自行生成一个唯一的`@id`。
2.  为你发现的所有实体和关系，请自行赋予你认为最合适的、符合CamelCase命名规范的`@type`和关系名称。
3.  当需要链接到“已知实体”时，必须遵循“角色建模全局指令”。

# 已知实体:
{entities_info}
{role_modeling_instruction}

# 原始业务数据:
{event_metadata_str}
{command.source_data_markdown} 

# 请生成JSON-LD输出 (必须是一个合法的JSON对象):
"""

        # ====================================================================
        #  模式二：Monolithic-KG (单体知识图谱基线)
        # ====================================================================
        elif domain == "Monolithic":
            print("    - [策略: Monolithic] 正在生成一个巨大的、混合领域的Prompt...")
            all_domain_models = static_repo.get_all_domain_models()
            all_schema_rules_str = ""
            all_relationship_guidance_str = ""
            present_entity_types = {e.entity_type for e in command.identified_entities}

            for domain_name, model_data in all_domain_models.items():
                rules = model_data.get("schema_rules", [])
                if rules:
                    all_schema_rules_str += f"\n### {domain_name}的Schema规则:\n"
                    all_schema_rules_str += "\n".join([f"- {rule}" for rule in rules])

                guidance_rules = model_data.get("relationship_guidance", [])
                applicable_instructions = []
                for rule in guidance_rules:
                    condition = rule.get("condition", {})
                    required_types = condition.get("contains_entity_type")
                    if not required_types: continue
                    required_types_set = set(required_types if isinstance(required_types, list) else [required_types])
                    if required_types_set.issubset(present_entity_types):
                        applicable_instructions.append(rule["instruction"])
                if applicable_instructions:
                    all_relationship_guidance_str += f"\n### {domain_name}的关系指令:\n"
                    all_relationship_guidance_str += "\n".join([f"- {inst}" for inst in applicable_instructions])

            return f"""
你是一位全知全能的知识图谱构建专家，你需要理解所有业务领域。
你的任务是，根据“原始业务数据”，从以下“所有可用领域的Schema和指令”中，自主选择所有相关的规则，生成一个统一的、包含所有可能知识的JSON-LD图谱片段。
这个片段可以是一个单一的JSON对象，也可以是一个包含多个JSON对象的列表，以最能表达全部信息为准。

# 已知实体的内部ID:
{entities_info}
{role_modeling_instruction}

# 所有可用领域的Schema和指令:
{all_schema_rules_str}
{all_relationship_guidance_str}

# 原始业务数据:
{event_metadata_str}
{command.source_data_markdown} 

# 请生成一个统一的JSON-LD输出 (可以是一个对象，也可以是包含多个对象的列表):
"""
        # ====================================================================
        #  模式三：消融 A (Ours_No_DynamicPrompt)
        #  保持领域路由，但禁用动态 Prompt 优化（无范例、无规则筛选）
        # ====================================================================
        elif self.mode == "Ours_No_DynamicPrompt":
            print(f"    - [策略: {self.mode}] 正在为领域 '{domain}' 生成静态全量Prompt...")
            schema_info = static_repo.get_schema(domain)
            # 直接将整个领域定义转为 JSON，不做任何精简和动态指引，也不提供 Few-shot 示例
            static_schema_dump = json.dumps(schema_info, indent=2, ensure_ascii=False)

            return f"""
        你是一位严谨的、专注于“{domain}”的知识图谱构建专家。
        你的任务是根据“原始业务数据”，参考下面的“静态全量领域Schema”，将其翻译成一个JSON-LD格式的知识图谱片段。你必须生成一个唯一的`@id`。

        # 已知实体的内部ID:
        {entities_info}
        {role_modeling_instruction}

        # 静态全量领域Schema (包含所有规则，需自行判断适用性):
        {static_schema_dump}

        # 原始业务数据:
        {event_metadata_str}
        {command.source_data_markdown} 

        # 请生成JSON-LD输出:
        """
            # ====================================================================
            #  模式四 & 五：Ours (最终方案)与 消融 B (无修复)
            #  这两者在 Prompt 生成阶段是完全一样的
            # ====================================================================
        else:
            print(f"    - [策略: {self.mode}] 正在为领域 '{domain}' 生成标准动态Prompt...")
            schema_info = static_repo.get_schema(domain)
            examples = static_repo.get_examples(domain)
            schema_rules_str = "\n".join([f"- {rule}" for rule in schema_info.get("schema_rules", [])])
            example_str = json.dumps(examples[0], indent=2, ensure_ascii=False) if examples else "{}"

            # --- 语言检测 ---
            # 如果领域名称全是英文字母或符号（针对BPI），则判定为英文环境
            is_english = domain.isascii()

            # --- 关系指令检索逻辑 (通用于中英文) ---
            present_entity_types = {e.entity_type for e in command.identified_entities}
            guidance_rules = schema_info.get("relationship_guidance", [])
            applicable_instructions = []
            for rule in guidance_rules:
                condition = rule.get("condition", {})
                required_types = condition.get("contains_entity_type")
                if not required_types: continue
                required_types_set = set(required_types if isinstance(required_types, list) else [required_types])
                if required_types_set.issubset(present_entity_types):
                    applicable_instructions.append(rule["instruction"])

            # --- 核心实体焦点提取 (通用于中英文) ---
            focus_entity_uuid = command.core_entity_uuid
            focus_entity = next((e for e in command.identified_entities if e.uuid == focus_entity_uuid), None)

            # ==============================
            #  全英文 Prompt 组装 (BPI 模式)
            # ==============================
            if is_english:
                entities_info = "\n".join([
                                              f"- Internal unique ID for entity '{e.label}' (Type: {e.entity_type}, BusinessID: {e.business_key}) is '{e.uuid}'."
                                              for e in command.identified_entities])

                relationship_guidance = ""
                if applicable_instructions:
                    relationship_guidance = "\n# Special Relationship Modeling Instructions:\n" + "\n".join(
                        [f"- {inst}" for inst in applicable_instructions])

                role_modeling_instruction = (
                    "\n# Global Role Modeling Instructions:\n"
                    "--- (Rules Start) ---\n"
                    "1. **Principle**: Any relationship pointing to a 'Known Entity' MUST have a JSON object as its value, NOT a simple ID string.\n"
                    "2. **Content**: This JSON object MUST contain exactly three fields: '@id' (its internal unique ID), 'entityType' (its business type), and 'businessKey' (its original business ID).\n"
                    "--- (Rules End) ---"
                )

                event_metadata_str = f"Note: This event occurred at {command.event_timestamp}."

                task_focus_instruction = ""
                if focus_entity:
                    task_focus_instruction = (
                        f"\n# Core Focus of this Task:\n"
                        f"--- (Rules Start) ---\n"
                        f"1. **Task Subject**: The core subject of this knowledge generation is the entity '{focus_entity.label}' (Type: {focus_entity.entity_type}).\n"
                        f"2. **Modeling Requirement**: The generated top-level JSON-LD object, including its `@type` and main content, MUST be about this core subject.\n"
                        f"3. **Relationship Direction**: Other entities (if any) should be treated as properties or relationships relative to this core subject.\n"
                        f"--- (Rules End) ---"
                    )

                return f"""
        You are a rigorous Knowledge Graph construction expert specializing in the "{domain}" domain.
        Your task is to translate the "Raw Business Data" into a JSON-LD formatted knowledge graph fragment, strictly adhering to all instructions and examples. You must generate a unique `@id`.

        {task_focus_instruction}

        # Internal IDs of Known Entities (Must use these IDs for relations):
        {entities_info}
        {relationship_guidance}
        {role_modeling_instruction}

        # Domain Schema (Rules):
        {schema_rules_str}

        # Output Format Example:
        {example_str}

        # Raw Business Data:
        {event_metadata_str}
        {command.source_data_markdown} 

        # Please generate JSON-LD output (Must be a valid JSON object):
        """
            # ==============================
            #  中文 Prompt 组装 (RB-50 模式，保持您原有代码)
            # ==============================
            else:
                entities_info = "\n".join(
                    [f"- 实体 '{e.label}' (类型: {e.entity_type}, 业务ID: {e.business_key}) 的内部唯一ID是 '{e.uuid}'。"
                     for e in command.identified_entities])

                relationship_guidance = ""
                if applicable_instructions:
                    relationship_guidance = "\n# 关系建模特别指令:\n" + "\n".join(
                        [f"- {inst}" for inst in applicable_instructions])

                role_modeling_instruction = (
                    "\n# 角色建模全局指令:\n"
                    "--- (规则开始) ---\n"
                    "1. **原则**: 任何一个指向“已知实体”的关系，其值都必须是一个包含详细信息的JSON对象，不能是简单的字符串ID。\n"
                    "2. **内容**: 这个JSON对象必须同时包含三个字段：'@id' (内部唯一ID)，'entityType' (业务类型)，以及 'businessKey' (原始业务ID)。\n"
                    "--- (规则结束) ---"
                )

                event_metadata_str = f"注意：该事件发生于 {command.event_timestamp}。"

                task_focus_instruction = ""
                if focus_entity:
                    task_focus_instruction = (
                        f"\n# 本次任务的核心焦点:\n"
                        f"--- (规则开始) ---\n"
                        f"1. **任务主体**: 本次知识生成的核心主体是实体 '{focus_entity.label}' (类型: {focus_entity.entity_type})。\n"
                        f"2. **建模要求**: 你生成的顶层JSON-LD对象，其`@type`和主要内容，必须是关于这个核心主体的。\n"
                        f"3. **关系方向**: 其他实体（如果有的话）都应被视为与这个核心主体相关的属性或关系。\n"
                        f"--- (规则结束) ---"
                    )

                return f"""
        你是一位严谨的、专注于“{domain}”的知识图谱构建专家。
        你的任务是根据“原始业务数据”，将其中的信息严格按照所有指令和范例，翻译成一个JSON-LD格式的知识图谱片段。你必须生成一个唯一的`@id`。

        {task_focus_instruction}

        # 已知实体的内部ID:
        {entities_info}
        {relationship_guidance}
        {role_modeling_instruction}

        # 领域Schema (规则):
        {schema_rules_str}

        # 输出格式范例:
        {example_str}

        # 原始业务数据:
        {event_metadata_str}
        {command.source_data_markdown} 

        # 请生成JSON-LD输出 (必须是一个合法的JSON对象):
        """

    def _invoke_llm(self, prompt: str, model_name: str, is_json_mode: bool = True) -> Any:
        """
        【通用】调用LLM API的核心函数。
        """
        print(f"    - [输入] 正在向模型 '{model_name}' 发送Prompt...")

        try:
            request_params = {
                "extra_headers": self.headers,
                "model": model_name,
                "messages": [
                    {"role": "system",
                     "content": "你是一个精确的、遵循指令的助手。你的回答必须严格遵循用户要求的JSON格式。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.1,
            }
            if is_json_mode:
                request_params["response_format"] = {"type": "json_object"}

            completion = self.client.chat.completions.create(**request_params)
            response_content = completion.choices[0].message.content

            print(f"    - [输出] LLM返回的原始字符串: {response_content}")

            if is_json_mode:
                return json.loads(response_content)
            else:
                return response_content.strip()

        except Exception as e:
            print(f"    - [错误] 调用LLM API时发生错误: {e}")
            raise

    def _generate_type_inference_prompt(self, parent_type: str, relation_name: str, nested_object: dict) -> str:
        """
        当检测到嵌套对象缺少@type时，创建一个新的、专门用于类型推断的Prompt。
        """
        object_keys = ", ".join(nested_object.keys())
        return f"""
你是一位精准的本体建模专家。
在一个类型为 '{parent_type}' 的对象中，有一个名为 '{relation_name}' 的属性，它指向一个嵌套的对象。
这个嵌套对象包含以下字段: [{object_keys}]。

请为这个嵌套对象推断一个最合适的、符合CamelCase命名规范的类型名称（@type）。
你的回答必须只包含这个类型名称字符串，不包含任何解释或引号。

例如，如果父类型是 'IT数据资产'，关系名是 'digitizationDetails'，你的回答应该是：DigitizationDetails
"""

    def _validate_and_correct(self, llm_output: dict) -> dict:
        """
        递归地验证LLM的输出，并根据运行模式智能修复缺失的@type字段。

        【V3.8 消融支持版】：
        1. 支持 @graph 结构。
        2. 根据 self.mode 判断是否跳过递归修复逻辑（用于消融实验B）。
        3. 强制执行最后的 @id 和 @type 存在性检查。
        """
        # --- 1. 确定需要验证和修复的目标节点列表 ---
        # 兼容 JSON-LD 的标准单体结构与 @graph 批量结构
        nodes_to_process = []
        if "@graph" in llm_output and isinstance(llm_output["@graph"], list):
            print("    - [验证] 检测到 @graph 结构，将遍历验证所有子节点。")
            nodes_to_process = llm_output["@graph"]
        else:
            nodes_to_process = [llm_output]

        # --- 2. 策略执行：判断是否启动递归修复 ---
        if self.mode == "Ours_No_Repair":
            # 消融实验 B：故意不执行修复，以观察原始生成的缺陷率
            print(f"    - [验证模式: {self.mode}] 警告：智能修复机制已禁用，仅执行基础合法性检查。")
        else:
            # 正常模式 (Ours) 或其他模式：执行完整的递归检查与修复
            print(f"    - [验证模式: {self.mode}] 正在执行递归验证与智能修复...")

            def recursive_fix(node_data: dict, parent_type: str = "Unknown"):
                """内部递归辅助函数：用于检测并修复嵌套对象的类型缺失。"""
                # 遍历所有键值对的副本，允许在发现缺失时进行修改
                for key, value in list(node_data.items()):
                    if isinstance(value, dict):
                        # 情况A：发现嵌套对象
                        if '@type' not in value:
                            print(f"    - [验证发现] 嵌套对象 (关系: {key}) 缺少 @type。正在启动类型推断...")

                            # 获取当前节点的类型作为上下文
                            current_parent_type = node_data.get('@type', parent_type)
                            # 生成类型推断专属的 Prompt
                            type_prompt = self._generate_type_inference_prompt(current_parent_type, key, value)

                            # 调用轻量级 LLM 进行类型推断 (注意：is_json_mode=False)
                            inferred_type = self._invoke_llm(type_prompt, settings.IDENTIFIER_MODEL_NAME,
                                                             is_json_mode=False)

                            # 清理 LLM 可能返回的引号或空白字符
                            if inferred_type:
                                inferred_type = inferred_type.strip().replace('"', '').replace("'", "")
                            else:
                                # 极端情况下的兜底策略：使用关系名作为类型名称
                                inferred_type = "".join(word.capitalize() for word in key.split())

                            # 将推断出的类型注入到数据中
                            value['@type'] = inferred_type
                            print(f"    - [验证修复] 已为关系 '{key}' 注入推断类型: {inferred_type}")

                        # 继续递归向下扫描
                        recursive_fix(value, parent_type=value.get('@type', 'Unknown'))

                    elif isinstance(value, list):
                        # 情况B：发现对象列表 (多值关系)
                        for item in value:
                            if isinstance(item, dict):
                                recursive_fix(item, parent_type=node_data.get('@type', 'Unknown'))

            # 对所有目标节点运行修复逻辑
            for node in nodes_to_process:
                if isinstance(node, dict):
                    recursive_fix(node)

            print("    - [验证] 递归修复流程执行完毕。")

        # --- 3. 执行最终硬性验证 (所有模式共通) ---
        # 无论是否执行了修复，最终产出的业务节点必须拥有 @id 和 @type 才能被 Writer 处理
        if not isinstance(llm_output, dict):
            raise ValueError(f"LLM输出格式错误：期望字典，实际得到 {type(llm_output)}")

        for node in nodes_to_process:
            if not isinstance(node, dict):
                continue

            # 检查核心标识符
            if "@id" not in node or "@type" not in node:
                # 记录详细的失败现场，用于后续的错误分析（回应审稿人 R1.5）
                print(f"    - [验证失败] 发现无效节点结构 (模式: {self.mode}): {json.dumps(node, ensure_ascii=False)}")
                # 在消融模式B下，如果LLM原始输出不给 @type，这里将直接抛出异常，记录该事件处理失败
                raise ValueError(f"节点验证未通过：缺少必须的属性 '@id' 或 '@type'。")

        return llm_output