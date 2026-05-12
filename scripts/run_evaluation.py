# -*- coding: utf-8 -*-
"""
【V3.3 终极评估脚本】

本脚本实现了完整的 "LLM-as-a-Judge" 四维评估框架，并通过命令行参数提供灵活的执行模式。
此版本包含了动态ID映射捕获机制和完全泛化的评估Prompt。

工作流程:
1. (可选) 预处理: 捕获本次运行的动态ID映射。
2. (可选) 批量运行MVP: 遍历输入事件，通过API触发知识生成。
3. (可选) 提取生成结果: 连接Neo4j，根据事件ID精确提取所有生成的知识片段。
4. (可选) 执行四维评估，并将动态ID映射作为上下文提供给裁判LLM。
5. 汇总并保存评估结果。

使用方法:
- 完整流程 (清空、生成、提取、评估):
  python -m scripts.run_evaluation --run-mvp --evaluate

- 仅评估 (基于已有的 generated_kgs.json):
  python -m scripts.run_evaluation --evaluate

- 仅从Neo4j提取数据:
  python -m scripts.run_evaluation --extract-only
- bpi图谱提取
  python -m scripts.run_evaluation --run-mvp --bpi-stats
"""
import json
import os
import time
import argparse
from pathlib import Path
from typing import List, Dict, Any
# 在文件顶部，需要导入 re 模块
import re

import requests
from neo4j import GraphDatabase, exceptions
from openai import OpenAI
from tqdm import tqdm

# 关键修复：确保脚本可以直接运行，也能作为模块导入
try:
    from app.config import settings
    from app.models import RawBusinessEvent
    from app.services.adapter import InputAdapter
except ImportError:
    import sys
    # 将项目根目录添加到Python路径中
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.config import settings
    from app.models import RawBusinessEvent
    from app.services.adapter import InputAdapter


# --- 1. 配置与路径定义 ---
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
EVAL_DIR = DATA_DIR / "evaluation"

# 默认使用 events.jsonl，如果检测到 events_bpi.jsonl 存在且指定了 --bpi-stats，会自动切换
# --- 1. 配置与路径定义 ---
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
EVAL_DIR = DATA_DIR / "evaluation"

# 默认使用 events.jsonl
INPUT_EVENTS_PATH_DEFAULT = EVAL_DIR / "1_input_events" / "events.jsonl"
INPUT_EVENTS_BPI_PATH = EVAL_DIR / "1_input_events" / "events_bpi_sample.jsonl" # 确保这里是 _sample.jsonl

# 【修复】分离领域模型路径
DOMAIN_MODELS_RB50_PATH = DATA_DIR / "domain_models.json"
DOMAIN_MODELS_BPI_PATH = DATA_DIR / "bpi_domain_models.json"

GOLD_KGS_PATH = EVAL_DIR / "2_gold_standard_kgs" / "gold_kgs.json"
RUNTIME_ID_MAP_PATH = EVAL_DIR / "runtime_id_map.json"
GENERATED_KGS_PATH = EVAL_DIR / "3_generated_results" / "generated_kgs.json"
EVALUATION_RESULTS_PATH = EVAL_DIR / "4_evaluation_results" / "evaluation_results.json"

# 初始化全局变量（将在 main 函数中被动态赋予真实路径）
INPUT_EVENTS_PATH = None
GOLD_KGS_PATH = None
RUNTIME_ID_MAP_PATH = None
GENERATED_KGS_PATH = None
EVALUATION_RESULTS_PATH = None
DOMAIN_MODELS_PATH = None # <--- 动态模型路径

API_ENDPOINT = "http://localhost:8000/v1/events/process"
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "password123"

JUDGE_MODEL_NAME = "google/gemini-2.5-flash"
# JUDGE_MODEL_NAME = "openai/gpt-4o"

# --- 辅助类: 性能统计器 ---
class PerformanceStats:
    def __init__(self):
        self.total_events = 0
        self.api_failures = 0
        self.start_time = 0
        self.end_time = 0

        # [回应 R1.5: 记录被过滤的事件]
        self.filtered_events = []

        # [回应 R1.7: Token 成本统计]
        self.judge_prompt_tokens = 0
        self.judge_completion_tokens = 0

        self.total_nodes = 0
        self.total_rels = 0
        self.domain_nodes = 0


stats = PerformanceStats()

# --- LLM 客户端 (仅在需要评估时初始化) ---
def init_judge_client():
    try:
        if not settings.OPENROUTER_API_KEY or "sk-or-v1" not in settings.OPENROUTER_API_KEY:
            raise ValueError("OPENROUTER_API_KEY 未设置")
        return OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.OPENROUTER_API_KEY,
        ), {
            "HTTP-Referer": settings.HTTP_REFERER,
            "X-Title": f"{settings.X_TITLE} - Judge",
        }
    except Exception as e:
        print(f"警告: 裁判LLM初始化失败 ({e})。如果只运行 --bpi-stats，这可以忽略。")
        return None, None


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
# --- 2. 核心评估逻辑 ---
class Evaluator:
    def __init__(self):
        self.judge_client, self.headers = init_judge_client()
        if not self.judge_client:
            raise RuntimeError("评估器无法工作，因为LLM客户端未初始化。")

    def _invoke_judge_llm(self, prompt: str) -> Dict:
        """
        【已重构 V3.4】调用裁判LLM，包含重试和从Markdown代码块中提取JSON的净化逻辑。
        """
        response_content = ""
        last_exception = None
        # 增加重试机制
        for attempt in range(3):
            try:
                completion = self.judge_client.chat.completions.create(
                    extra_headers=self.headers,
                    model=JUDGE_MODEL_NAME,
                    messages=[
                        {"role": "system", "content": "你是一个精确的、遵循指令的JSON评估助手。"},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.0,
                    timeout=120.0,  # 延长超时时间以应对复杂的评估任务
                )

                if not completion.choices:
                    raise ValueError("LLM API返回了空的 choices 列表。")

                # [回应 R1.7: 累加 Token 消耗]
                if completion.usage:
                    stats.judge_prompt_tokens += completion.usage.prompt_tokens
                    stats.judge_completion_tokens += completion.usage.completion_tokens

                response_content = completion.choices[0].message.content

                # 【V3.4 关键修复】 在解析之前，先调用净化函数
                clean_json_str = extract_json_from_llm_response(response_content)

                if not clean_json_str:
                    raise ValueError(f"从LLM响应中提取JSON后，内容为空。原始响应: '{response_content}'")

                # 尝试解析净化后的JSON字符串
                return json.loads(clean_json_str)

            except Exception as e:
                last_exception = e
                print(f"    - [评估警告] 裁判LLM调用尝试 {attempt + 1}/3 失败: {type(e).__name__}: {e}。正在重试...")
                time.sleep(5)  # 在重试前等待5秒

        # 如果3次尝试都失败了，则在循环外构造一个错误返回
        final_error_message = f"经过3次尝试后，裁判LLM调用仍然失败: {type(last_exception).__name__}: {last_exception}"
        print(f"    - [评估错误] {final_error_message}")
        print(f"    - [调试信息] 最后的原始响应是: '{response_content}'")
        return {"error": final_error_message}

    def evaluate_holistic(self, original_data_md: str, domain_model: Dict, generated_fragment: Dict,
                          id_mappings: Dict) -> Dict:
        """
        【V3.4 终极版】整体性评估方法。
        将事实忠实度、Schema合规性、信息完整性融合到一个Prompt中，
        为裁判LLM提供最完整的上下文，进行一次性的综合评估。
        """
        mappings_str = "\n".join([f"- 业务ID '{biz_id}' 对应内部UUID '{uuid}'" for biz_id, uuid in id_mappings.items()])

        prompt = f"""
你是一位极其严谨、理解业务语义、并且精通知识图谱建模的终极评估专家。
你的任务是，基于提供的所有上下文信息（原始数据、ID映射、领域模型），对“生成知识”进行一次全面的、三位一体的评估。

# 所有可用上下文:

## 1. 原始数据 (Markdown格式):
{original_data_md}

## 2. ID映射关系:
{mappings_str}

## 3. 领域模型规范:
{json.dumps(domain_model, indent=2, ensure_ascii=False)}

# 待评估的生成知识 (JSON-LD格式):
{json.dumps(generated_fragment, indent=2, ensure_ascii=False)}

# --- 你的评估任务 ---

请分三步进行评估，并最终输出一个包含所有评估结果的JSON对象。

## 步骤一：事实忠实度评估 (Fidelity)
逐一检查“生成知识”中的每个事实断言（属性和关系）。一个断言是“忠实的”，如果它满足以下任一条件：
- (A) 它可以直接在“原始数据”中找到。
- (B) 它是对“原始数据”中一或多个信息的合理概括、转换或推断（例如，从`scan_details`可以推断出操作是“数字化”）。
- (C) 它是一个关系，其指向的内部UUID可以通过“ID映射关系”对应到“原始数据”中的某个业务ID。

## 步骤二：Schema合规性评估 (Conformance)
判断“生成知识”是否严格遵循了“领域模型规范”。
**重要：** 如果生成的类型或关系名与规范在语义上等价或更优（例如，规范是“古籍维护员”，生成的是“特藏维护员”），则不应视为违规。

## 步骤三：信息完整性评估 (Completeness)
检查“原始数据”中的每一条关键信息，是否都在“生成知识”中得到了直接或间接的体现。特别注意那些通过关系链接到正确实体的ID信息。

# --- 输出格式 ---

你的输出必须是一个单一的JSON对象，严格遵循以下结构：
{{
  "fidelity_assessment": {{
    "summary": "对事实忠实度的总体评价（例如：高度忠实，存在少量合理推断）。",
    "details": [
      {{ "field": "字段名", "is_supported": true/false, "reason": "详细说明支撑或不支持的理由，必须结合溯源方法论。" }}
    ],
    "score": "一个0.0到1.0之间的浮点数，代表忠实字段的比例"
  }},
  "conformance_assessment": {{
    "overall_conformance": "Full/Partial/None",
    "violations": [
      {{ "field": "字段名", "reason": "违规原因。" }}
    ],
    "positive_notes": "记录语义等价或更优的表述。"
  }},
  "completeness_assessment": {{
    "summary": "对信息完整性的总体评价。",
    "missed_information": [
      {{ "info": "被遗漏的关键信息点", "reason": "为什么认为它被遗漏了。" }}
    ]
  }}
}}
"""
        return self._invoke_judge_llm(prompt)

    def evaluate_elegance(self, original_data_md: str, domain_model: Dict, gold_fragment: Dict,
                          generated_fragment: Dict, id_mappings: Dict) -> Dict:
        """
        【V3.3 泛化版】维度四：建模优雅性评估 (保持独立)
        """
        mappings_str = "\n".join([f"- 业务ID '{biz_id}' 对应内部UUID '{uuid}'" for biz_id, uuid in id_mappings.items()])
        prompt = f"""
你是一位具有极高审美和深刻洞察力的世界级本体建模大师。
你的任务不是检查对错，而是对一份“系统生成知识”的建模质量进行一次专家级的评审。

# ID映射关系:
{mappings_str}

# 创作素材 (原始数据):
{original_data_md}

# 设计规范 (领域模型):
{json.dumps(domain_model, indent=2, ensure_ascii=False)}

# 专家参考作品 (黄金标准知识):
{json.dumps(gold_fragment, indent=2, ensure_ascii=False)}

# 待评作品 (系统生成知识):
{json.dumps(generated_fragment, indent=2, ensure_ascii=False)}

# 你的任务:
请从一个建模专家的角度，比较“待评作品”和“专家参考作品”。
你需要评估“待评作品”在简洁性、表达力、创造性/洞察力和术语专业性方面的表现，并给出一个1-5分的综合评分。
重要：如果“待评作品”在结构上与“专家参考作品”不同，但这种不同是一种合理的、甚至更优的建模选择，你应该给予加分。

# 请输出你的评估结果 (JSON格式):
{{
  "elegance_score": "1-5之间的整数",
  "reasoning": "详细的、专家级的评审意见。"
}}
"""
        return self._invoke_judge_llm(prompt)


# --- 3. 辅助函数 ---

def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_jsonl(path):
    with open(path, 'r', encoding='utf-8') as f:
        return [json.loads(line) for line in f]


def reconstruct_jsonld_from_neo4j(session, start_node_id: str) -> Dict:
    """
    【V3.3.1 微调版】
    增加了对仅有 '...Node' 标签节点的类型推断兜底逻辑。
    """
    visited_nodes_cache = {}

    def build_obj_from_element_id(element_id: str) -> Dict:
        if element_id in visited_nodes_cache:
            cached_obj = visited_nodes_cache[element_id]
            ref_id = cached_obj.get('@id')
            if ref_id:
                return {"@id": ref_id}
            else:
                return {"@ref_elementId": element_id}

        query = """
        MATCH (n) WHERE elementId(n) = $element_id
        OPTIONAL MATCH (n)-[r]->(t)
        RETURN n, collect({rel_type: type(r), target_node: t}) AS relationships
        """
        result = session.run(query, element_id=element_id).single()

        if not result or not result['n']: return None

        node = result['n']
        node_props = dict(node)

        jsonld_obj = {}
        visited_nodes_cache[element_id] = jsonld_obj

        # 1. 确定 @id
        node_main_id = node_props.get('fragmentId', node_props.get('uuid'))
        if node_main_id: jsonld_obj['@id'] = node_main_id

        # 2. 确定 @type (微调逻辑)
        # 过滤掉系统标签
        all_labels = list(node.labels)
        system_labels = ['CoreEntity', 'Identifier', 'Name']

        # 优先找非Node后缀的业务标签 (如 '抢救性保护事件')
        specific_labels = [l for l in all_labels if l not in system_labels and not l.endswith('Node')]

        if specific_labels:
            jsonld_obj['@type'] = specific_labels[0]
        else:
            # 如果没找到，尝试从 '...Node' 标签中还原 (如 '特藏保护Node' -> '特藏保护')
            # 或者对于嵌套对象，可能就是 'DigitizationDetailsNode' -> 'DigitizationDetails'
            domain_node_label = next((l for l in all_labels if l.endswith('Node')), None)
            if domain_node_label:
                # 去掉最后的 "Node"
                jsonld_obj['@type'] = domain_node_label[:-4]
            else:
                jsonld_obj['@type'] = 'Thing'

        # 3. 添加属性
        for k, v in node_props.items():
            if k not in ['fragmentId', 'uuid', 'businessKey', 'coreEntityUuid', 'value', 'label', 'sourceEventId']:
                jsonld_obj[k] = v

        # 4. 递归处理关系 (保持不变)
        relationships = result['relationships']
        if relationships:
            for rel in relationships:
                if not rel['rel_type']: continue
                rel_type = rel['rel_type']
                target_node = rel['target_node']

                # 检查是否角色节点
                role_check = "MATCH (r)-[:PLAYED_BY]->(c:CoreEntity) WHERE elementId(r)=$id RETURN c.uuid as uuid"
                role_res = session.run(role_check, id=target_node.element_id).single()

                if role_res:
                    # 角色节点处理逻辑
                    core_uuid = role_res['uuid']
                    role_node_labels = list(target_node.labels)
                    # 同样的类型提取逻辑
                    role_specific = [l for l in role_node_labels if l not in system_labels and not l.endswith('Node')]
                    role_type = role_specific[0] if role_specific else "Role"

                    child_obj = {
                        "@id": core_uuid,
                        "@type": role_type
                    }
                else:
                    # 普通递归
                    child_obj = build_obj_from_element_id(target_node.element_id)

                if rel_type in jsonld_obj:
                    if not isinstance(jsonld_obj[rel_type], list):
                        jsonld_obj[rel_type] = [jsonld_obj[rel_type]]
                    jsonld_obj[rel_type].append(child_obj)
                else:
                    jsonld_obj[rel_type] = child_obj

        visited_nodes_cache[element_id].update(jsonld_obj)
        return jsonld_obj

    # (起始节点的查找逻辑不变)
    start_query = "MATCH (n {fragmentId: $fragment_id}) RETURN elementId(n) as element_id"
    start_result = session.run(start_query, fragment_id=start_node_id).single()
    if start_result and start_result['element_id']:
        return build_obj_from_element_id(start_result['element_id'])
    else:
        return {"error": f"Node with fragmentId '{start_node_id}' not found."}


# --- 4. 主执行流程函数 ---
def run_mvp_pipeline_and_build_map(input_events: List[Dict]) -> Dict:
    """
    【V3.5.1 增强版】
    增加了 API 请求的重试机制和更长的超时时间，以应对大规模处理时的网络波动。
    """
    print("\n--- 步骤 1: 运行MVP并构建动态ID映射表 ---")

    # 记录开始时间
    stats.start_time = time.time()
    stats.total_events = len(input_events)

    runtime_id_map = {}
    driver = None
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        with driver.session() as session:
            print("  - 正在清空Neo4j数据库...")
            session.run("MATCH (n) DETACH DELETE n")
            session.run(
                "CREATE CONSTRAINT identifier_value_unique IF NOT EXISTS FOR (i:Identifier) REQUIRE i.value IS UNIQUE")
            session.run(
                "CREATE CONSTRAINT core_entity_uuid_unique IF NOT EXISTS FOR (c:CoreEntity) REQUIRE c.uuid IS UNIQUE")
            session.run("CREATE INDEX core_entity_uuid_idx IF NOT EXISTS FOR (c:CoreEntity) ON (c.uuid)")
            print("  - Neo4j数据库已清空并准备就绪。")

        # 批量发送API请求 (增强版)
        print(f"  - 开始发送 {len(input_events)} 个事件 (超时设置: 60s, 间隔: 1s)...")

        for event in tqdm(input_events, desc="发送事件到API"):
            max_retries = 3
            success = False

            for attempt in range(max_retries):
                try:
                    # 【修改1】超时时间延长至 60 秒
                    response = requests.post(API_ENDPOINT, json=event, timeout=60)
                    if response.status_code == 202:
                        success = True
                        break  # 成功，跳出重试循环
                    else:
                        print(
                            f"  - [重试 {attempt + 1}/{max_retries}] 事件 {event['event_id']} 状态码异常: {response.status_code}")
                except requests.exceptions.RequestException as e:
                    print(f"  - [重试 {attempt + 1}/{max_retries}] 事件 {event['event_id']} 请求异常: {e}")

                # 如果失败，等待 5 秒再重试
                time.sleep(5)

            if not success:
                print(f"  - 错误: 事件 {event['event_id']} 在 {max_retries} 次尝试后最终失败。")
                stats.api_failures += 1

            # 【修改2】增加请求间隔到 1 秒，减轻 LLM API 压力
            time.sleep(1.0)

            # 等待后台任务完成
        # 粗略估计：对于1000个事件，可能需要较长时间
        print("  - 所有事件已发送，等待后台Celery任务完成...")

        # 智能轮询：每10秒检查一次数据库是否有新数据写入，或者是否长时间无变化
        last_node_count = 0
        stable_count = 0
        max_wait_loops = 600  # 最多等待 600 * 5秒 = 50分钟

        with driver.session() as session:
            for _ in tqdm(range(max_wait_loops), desc="监控数据库写入"):
                try:
                    result = session.run("MATCH (n) RETURN count(n) as count").single()
                    current_count = result["count"]

                    if current_count > 0 and current_count == last_node_count:
                        stable_count += 1
                    else:
                        stable_count = 0

                    last_node_count = current_count

                    # 如果连续 12 次 (1分钟) 节点数没有变化，且已经有数据了，认为处理完毕
                    if stable_count >= 12 and current_count > 0:
                        print(f"  - 数据库写入已稳定 (节点数: {current_count})，认为任务已完成。")
                        break
                except Exception as e:
                    print(f"  - 监控数据库时发生瞬时错误 (忽略): {e}")

                time.sleep(5)

        stats.end_time = time.time()

        # 提取ID映射
        print("  - 正在从数据库中提取真实ID映射...")
        with driver.session() as session:
            query = "MATCH (i:Identifier)-[:IDENTIFIES]->(c:CoreEntity) RETURN i.value AS businessKey, c.uuid AS uuid"
            results = session.run(query)
            for record in results:
                runtime_id_map[record['businessKey']] = record['uuid']

        RUNTIME_ID_MAP_PATH.parent.mkdir(exist_ok=True, parents=True)
        with open(RUNTIME_ID_MAP_PATH, 'w', encoding='utf-8') as f:
            json.dump(runtime_id_map, f, indent=2, ensure_ascii=False)

        return runtime_id_map

    except Exception as e:
        print(f"  - 严重错误: MVP运行或ID映射构建失败: {e}")
        return {}
    finally:
        if driver: driver.close()


def collect_bpi_statistics():
    """
    【新增 V3.5】收集并打印宏观统计指标。
    """
    print("\n=== BPI-2017 宏观指标统计报告 ===")

    # 1. 计算吞吐量
    total_time = stats.end_time - stats.start_time
    throughput = total_time / stats.total_events if stats.total_events > 0 else 0
    print(f"1. 吞吐量 (Throughput):")
    print(f"   - 总耗时: {total_time:.2f} 秒")
    print(f"   - 处理事件数: {stats.total_events}")
    print(f"   - 平均耗时: {throughput:.2f} 秒/事件")

    # 2. 统计图谱规模
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with driver.session() as session:
            # 节点总数
            node_count = session.run("MATCH (n) RETURN count(n) as c").single()["c"]
            # 关系总数
            rel_count = session.run("MATCH ()-[r]->() RETURN count(r) as c").single()["c"]
            # 领域知识节点数 (排除CoreEntity, Identifier, Name)
            domain_node_count = \
            session.run("MATCH (n) WHERE any(l in labels(n) WHERE l ENDS WITH 'Node') RETURN count(n) as c").single()[
                "c"]

            print(f"2. 图谱规模 (Topology):")
            print(f"   - 总节点数: {node_count}")
            print(f"   - 总关系数: {rel_count}")
            print(f"   - 领域知识节点数: {domain_node_count}")
            if stats.total_events > 0:
                print(f"   - 平均知识产出: {domain_node_count / stats.total_events:.2f} 个节点/事件")

    finally:
        driver.close()

    # 3. 错误率
    error_rate = (stats.api_failures / stats.total_events) * 100 if stats.total_events > 0 else 0
    print(f"3. 错误率 (Robustness):")
    print(f"   - API调用失败数: {stats.api_failures}")
    print(f"   - 错误率: {error_rate:.2f}%")
    print("===================================\n")

def extract_results_from_neo4j(input_events: List[Dict]) -> Dict:
    """步骤2的独立函数：从Neo4j提取所有生成的知识片段。"""
    print("\n--- 步骤 2: 从Neo4j提取所有生成的知识片段 ---")
    generated_kgs = {}
    driver = None
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
        with driver.session() as session:
            for event in tqdm(input_events, desc="从Neo4j提取结果"):
                event_id = event['event_id']
                query = """
                MATCH (n) WHERE n.sourceEventId = $event_id AND any(label IN labels(n) WHERE label ENDS WITH 'Node')
                RETURN n.fragmentId AS fragmentId, labels(n) AS labels
                """
                results = session.run(query, event_id=event_id)

                fragments_for_event = {}
                for record in results:
                    fragment_id = record['fragmentId']
                    if not fragment_id: continue

                    domain_label = next((l for l in record['labels'] if l.endswith('Node')), None)
                    if not domain_label: continue

                    # 从领域标签反向推导领域名称 (中英文自适应逻辑)
                    domain_name_parts = domain_label.replace("Node", "")
                    domain_name_map = {"It/数据管理": "IT/数据管理领域"}  # 处理特殊情况

                    if domain_name_parts in domain_name_map:
                        domain_name = domain_name_map[domain_name_parts]
                    elif domain_name_parts.isascii():
                        # 【V3.6 关键修复】如果是纯英文字符（如 BPI 的 LoanBusiness），则不添加"领域"后缀
                        domain_name = domain_name_parts
                    else:
                        # 是中文，且不在特殊映射表里，加上"领域"后缀
                        domain_name = f"{domain_name_parts}领域"

                    reconstructed_obj = reconstruct_jsonld_from_neo4j(session, fragment_id)
                    if reconstructed_obj:
                        if domain_name not in fragments_for_event:
                            fragments_for_event[domain_name] = []
                        fragments_for_event[domain_name].append(reconstructed_obj)
                generated_kgs[event_id] = fragments_for_event
    finally:
        if driver: driver.close()

    GENERATED_KGS_PATH.parent.mkdir(exist_ok=True, parents=True)
    with open(GENERATED_KGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(generated_kgs, f, indent=2, ensure_ascii=False)
    print(f"  - 生成的知识图谱已提取并保存到: {GENERATED_KGS_PATH}")
    return generated_kgs


def run_llm_as_judge_evaluation(generated_kgs: Dict, input_events: List[Dict], gold_kgs: Dict, domain_models: Dict,
                                runtime_id_map: Dict):
    """
    【V3.4 终极版】执行LLM-as-a-Judge评估。
    此版本使用一个统一的holistic评估来处理前三个维度。
    """
    print("\n--- 步骤 4: 开始执行LLM-as-a-Judge终极评估 ---")
    evaluator = Evaluator()
    evaluation_results = {}

    # 将输入事件从列表转换为字典，方便通过event_id快速查找
    input_events_map = {event['event_id']: event for event in input_events}
    # 将原始payload转换为Markdown，也存入字典
    input_events_md_map = {
        event['event_id']: "| 属性名 | 属性值 |\n|---|---|\n" + "\n".join(
            [f"| {k} | {v} |" for k, v in event.get('payload', {}).items()])
        for event in input_events
    }

    # 收集所有需要评估的任务单元 (event_id, domain)
    eval_tasks = []
    for event_id, domains in generated_kgs.items():
        if not domains: continue
        for domain, gen_fragments in domains.items():
            # 确保生成的片段和黄金标准都存在
            if gen_fragments and gold_kgs.get(event_id, {}).get(domain):
                # 我们只评估每个领域的第一个生成片段（MVP简化）
                eval_tasks.append((event_id, domain))

    # 创建总进度条
    pbar = tqdm(total=len(eval_tasks), desc="LLM裁判评估进度")

    for event_id, domain in eval_tasks:
        pbar.set_description(f"评估: {event_id[:10]}... @ {domain}")

        # 初始化结果存储结构
        evaluation_results.setdefault(event_id, {})[domain] = {}

        # 1. 准备该任务所需的所有上下文信息
        gen_fragment = generated_kgs[event_id][domain][0]
        gold_fragment = gold_kgs[event_id][domain]
        original_md = input_events_md_map.get(event_id, "")

        # 修复领域名称不匹配的问题 (e.g., "IT数据管理" vs "IT/数据管理领域")
        domain_model_key = next((k for k in domain_models if k.startswith(domain.replace("领域", ""))), None)
        if not domain_model_key:
            print(f"  - 警告: 找不到领域 '{domain}' 对应的领域模型，跳过评估。")
            pbar.update(1)
            continue
        domain_model = domain_models[domain_model_key]

        # --- **核心修改** ---
        # 2. 调用统一的整体性评估方法
        holistic_result = evaluator.evaluate_holistic(original_md, domain_model, gen_fragment, runtime_id_map)

        # 3. 将返回的大JSON对象，拆分并赋值给结果
        if "error" not in holistic_result:
            evaluation_results[event_id][domain]['fidelity'] = holistic_result.get('fidelity_assessment', {})
            evaluation_results[event_id][domain]['conformance'] = holistic_result.get('conformance_assessment', {})

            # 注意：完整性评估现在只针对当前领域，因为holistic prompt的上下文是单个领域
            # 这是一个设计上的权衡，简化了Prompt，但完整性评估的范围变小了
            # 如果要评估全局完整性，仍需单独调用
            evaluation_results[event_id][domain]['completeness'] = holistic_result.get('completeness_assessment', {})
        else:
            # 如果holistic调用失败，则记录错误
            error_msg = {"error": holistic_result.get("error")}
            evaluation_results[event_id][domain]['fidelity'] = error_msg
            evaluation_results[event_id][domain]['conformance'] = error_msg
            evaluation_results[event_id][domain]['completeness'] = error_msg

        # 4. 独立调用建模优雅性评估（因为它需要黄金标准作为对比）
        elegance_result = evaluator.evaluate_elegance(original_md, domain_model, gold_fragment, gen_fragment,
                                                      runtime_id_map)
        evaluation_results[event_id][domain]['elegance'] = elegance_result

        pbar.update(1)
        # time.sleep(1) # 在生产运行时可以增加延迟以避免API速率限制

    pbar.close()

    print("\n--- 步骤 5: 保存最终评估结果 ---")
    EVALUATION_RESULTS_PATH.parent.mkdir(exist_ok=True, parents=True)
    with open(EVALUATION_RESULTS_PATH, 'w', encoding='utf-8') as f:
        json.dump(evaluation_results, f, indent=2, ensure_ascii=False)
    print(f"  - 评估结果已保存到: {EVALUATION_RESULTS_PATH}")


def main():
    """主函数，通过命令行参数控制执行流程。"""
    parser = argparse.ArgumentParser(description="知识增殖引擎 V3.6 评估脚本")
    parser.add_argument("--run-mvp", action="store_true", help="清空数据库并重新运行MVP生成所有数据。")
    parser.add_argument("--extract-only", action="store_true", help="只从Neo4j提取数据并保存，不进行LLM评估。")
    parser.add_argument("--evaluate-only", action="store_true", help="只进行LLM评估，基于已有的生成结果文件。")
    parser.add_argument("--bpi-stats", action="store_true", help="【启用BPI模式】使用BPI抽样数据，将输出隔离到BPI专属文件。")
    args = parser.parse_args()

    print("--- 终极评估脚本 V3.6 启动 ---")

    # 声明全局变量
    global INPUT_EVENTS_PATH, GOLD_KGS_PATH, RUNTIME_ID_MAP_PATH, GENERATED_KGS_PATH, EVALUATION_RESULTS_PATH, DOMAIN_MODELS_PATH

    # 【动态路由逻辑】
    if args.bpi_stats:
        print(f"--- ⚠️ 模式: BPI 金融数据集泛化性验证 ---")
        INPUT_EVENTS_PATH = INPUT_EVENTS_BPI_PATH
        RUNTIME_ID_MAP_PATH = EVAL_DIR / "runtime_id_map_bpi.json"
        GENERATED_KGS_PATH = EVAL_DIR / "3_generated_results" / "generated_kgs_bpi_sample.json"
        DOMAIN_MODELS_PATH = DOMAIN_MODELS_BPI_PATH  # <--- BPI模型
    else:
        print(f"--- 模式: RB-50 标准评估 ---")
        INPUT_EVENTS_PATH = INPUT_EVENTS_PATH_DEFAULT
        GOLD_KGS_PATH = EVAL_DIR / "2_gold_standard_kgs" / "gold_kgs.json"
        RUNTIME_ID_MAP_PATH = EVAL_DIR / "runtime_id_map.json"
        GENERATED_KGS_PATH = EVAL_DIR / "3_generated_results" / "generated_kgs.json"
        EVALUATION_RESULTS_PATH = EVAL_DIR / "4_evaluation_results" / "evaluation_results.json"
        DOMAIN_MODELS_PATH = DOMAIN_MODELS_RB50_PATH  # <--- 古籍模型

    # ---------------- 路径配置完成 ----------------

    input_events = load_jsonl(INPUT_EVENTS_PATH)

    runtime_id_map = {}

    # 【V3.4 终极逻辑】
    if args.run_mvp:
        # 如果要运行MVP，那么ID映射必须在运行后重新生成
        runtime_id_map = run_mvp_pipeline_and_build_map(input_events)
        # 如果是 BPI 模式，在生成结束后直接统计并退出，不进行 LLM 裁判评估
        if args.bpi_stats:
            extract_results_from_neo4j(input_events)  # 提取生成的JSON
            collect_bpi_statistics()  # 打印统计信息
            print("--- BPI 泛化性运行及统计完成，脚本安全退出。 ---")
            return
    elif RUNTIME_ID_MAP_PATH.exists():
        # 否则，尝试从文件中加载
        print(f"\n--- 预处理步骤: 加载已有的ID映射表从 {RUNTIME_ID_MAP_PATH} ---")
        runtime_id_map = load_json(RUNTIME_ID_MAP_PATH)
    else:
        # 如果文件不存在，也无法运行MVP，则报错
        print(f"--- 错误: 找不到ID映射文件 {RUNTIME_ID_MAP_PATH}，请先使用 --run-mvp 运行一次以生成它。 ---")
        return

    if not runtime_id_map:
        print("--- 错误: 未能构建或加载ID映射表，评估无法继续。 ---")
        return

    generated_kgs = {}
    if args.run_mvp or args.extract_only or not GENERATED_KGS_PATH.exists():
        generated_kgs = extract_results_from_neo4j(input_events)

    else:
        print("\n--- 步骤 2: 跳过提取，直接加载已有的生成结果文件 ---")
        generated_kgs = load_json(GENERATED_KGS_PATH)

    if args.extract_only:
        print("\n--- --extract-only 参数已指定，脚本执行完毕！ ---")
        return

    if args.evaluate_only or args.run_mvp:
        print("\n--- 步骤 3: 加载评估所需的所有数据 ---")
        gold_kgs = load_json(GOLD_KGS_PATH)
        domain_models = load_json(DOMAIN_MODELS_PATH)
        print("  - 黄金标准、领域模型和输入事件已加载。")

        run_llm_as_judge_evaluation(generated_kgs, input_events, gold_kgs, domain_models, runtime_id_map)
    # [回应 R1.7: 输出 Token 成本报告]
    print("\n=== [响应 R1.7] 裁判 LLM Token 成本分析 ===")
    print(f"总计消耗 Prompt Tokens: {stats.judge_prompt_tokens}")
    print(f"总计消耗 Completion Tokens: {stats.judge_completion_tokens}")
    # 假设使用 gpt-4o 的大致价格 (每1M tokens: $5 input, $15 output)
    estimated_cost = (stats.judge_prompt_tokens / 1_000_000 * 5.0) + (stats.judge_completion_tokens / 1_000_000 * 15.0)
    print(f"估算总成本 (基于 gpt-4o 定价): ${estimated_cost:.4f}")
    print("=========================================\n")
    print("\n--- 评估脚本执行完毕！ ---")


if __name__ == "__main__":
    main()