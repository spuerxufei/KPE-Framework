# -*- coding: utf-8 -*-
"""
【V3.6.2 终极版本】BPI-2017 泛化性量化审计脚本

本脚本专门用于对 BPI-2017 金融数据集的生成结果进行全英文、量化审计。
通过继承 run_evaluation.py 的基础架构，确保了评估逻辑、鲁棒性和统计口径与主实验完全对齐。

解决的核心痛点：
1. 统一为 Holistic (整体性) 评估模式。
2. 注入局部 ID 映射上下文，确保 UUID 链接的可验证性。
3. 继承 V3.4+ 的 JSON 净化与重试逻辑，确保大规模运行的稳定性。
4. 本实现了与主评估脚本一致的 Token 统计功能
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Any

# 确保能正确导入 app 和 scripts
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.config import settings
# 导入 stats 以便读取由父类 Evaluator 自动累加的 Token 数据
from scripts.run_evaluation import Evaluator, stats, load_json, load_jsonl

# --- 1. 配置路径 ---
EVAL_DIR = BASE_DIR / "data" / "evaluation"
BPI_GENERATED_PATH = EVAL_DIR / "3_generated_results" / "generated_kgs_bpi_sample.json"
BPI_EVENTS_PATH = EVAL_DIR / "1_input_events" / "events_bpi_sample.jsonl"
BPI_MODELS_PATH = BASE_DIR / "data" / "bpi_domain_models.json"
BPI_ID_MAP_PATH = EVAL_DIR / "runtime_id_map_bpi.json"
BPI_FINAL_RESULTS_PATH = EVAL_DIR / "4_evaluation_results" / "bpi_evaluation_results.json"

# 裁判模型配置 (建议使用高性能模型以确保审计精度)
JUDGE_MODEL_NAME = "openai/gpt-4o"


# --- 2. BPI 专用全英文评估器 ---
class BPIEvaluator(Evaluator):
    """
    扩展主评估器，提供专门针对英文金融语境的整体评估方法。
    """

    def evaluate_holistic_en(self, original_data_md: str, domain_model: Dict, generated_fragment: Dict,
                             local_id_map: Dict) -> Dict:
        """
        全英文整体评估逻辑。将三个核心维度融合在一次推理中。
        """
        # 格式化局部 ID 映射
        id_context = "\n".join([f"- Business Key '{k}' matches UUID '{v}'" for k, v in local_id_map.items()])
        if not id_context:
            id_context = "No specific ID mappings for this event."

        prompt = f"""
You are a Senior Knowledge Engineering Auditor specialized in Financial Domain Modeling.
Your task is to audit a "Generated Knowledge" fragment against the "Raw Data" and the "Domain Schema Specification".

# AUDIT CONTEXT:
## 1. Raw Business Log (Markdown):
{original_data_md}

## 2. Identity Mapping (Ground Truth):
These mappings define the correct relationship between business keys in raw data and UUIDs in the graph:
{id_context}

## 3. Domain Schema Specification:
{json.dumps(domain_model, indent=2, ensure_ascii=False)}

# CANDIDATE TO EVALUATE (JSON-LD):
{json.dumps(generated_fragment, indent=2, ensure_ascii=False)}

# --- AUDIT INSTRUCTIONS ---
Please perform the evaluation in three steps and output a single JSON object:

Step 1: Factual Fidelity Assessment
- Verify if every field and relationship can be traced back to the Raw Data or the Identity Mapping.
- Randomized UUIDs are valid if they match the Identity Mapping Table.
- Score this dimension from 0.0 to 1.0.

Step 2: Schema Conformance Assessment
- Check if node types and edge predicates follow the Domain Schema.
- Accept semantically equivalent terms (e.g., 'LoanOfficer' instead of 'Staff').

Step 3: Information Completeness Assessment
- Check if any critical business information from the raw log is missing in the fragment.

# --- OUTPUT FORMAT (MANDATORY JSON) ---
{{
  "fidelity_assessment": {{
    "score": 0.95,
    "summary": "Overall evaluation of factual correctness.",
    "details": [ {{ "field": "name", "status": "supported/hallucinated", "reason": "..." }} ]
  }},
  "conformance_assessment": {{
    "overall": "Full/Partial/None",
    "violations": [ {{ "field": "type", "reason": "..." }} ]
  }},
  "completeness_assessment": {{
    "summary": "Evaluation of information coverage.",
    "missed_fields": []
  }}
}}
"""
        # 复用父类的 _invoke_judge_llm 方法，自动获得重试和净化功能
        return self._invoke_judge_llm(prompt)


# --- 3. 执行主逻辑 ---
def main():
    print(f"--- [BPI 泛化性评估] 启动 ---")
    print(f"  - 目标数据库连接: {settings.NEO4J_URI}")

    # 环境检查
    if not BPI_GENERATED_PATH.exists():
        print(f"错误: 找不到生成的 BPI 图谱文件: {BPI_GENERATED_PATH}")
        return

    # 加载所有数据
    generated_kgs = load_json(BPI_GENERATED_PATH)
    domain_models = load_json(BPI_MODELS_PATH)
    full_id_map = load_json(BPI_ID_MAP_PATH)

    raw_events = {}
    for ev in load_jsonl(BPI_EVENTS_PATH):
        raw_events[ev['event_id']] = ev

    evaluator = BPIEvaluator()
    results = {}

    # 抽取事件进行评估
    event_ids = list(generated_kgs.keys())
    print(f"  - 准备审计 {len(event_ids)} 个 BPI 事件...")

    from tqdm import tqdm
    for eid in tqdm(event_ids, desc="BPI 审计进度"):
        if eid not in raw_events: continue

        results[eid] = {}
        # 准备原始数据的 Markdown 表示
        payload = raw_events[eid].get('payload', {})
        raw_md = "| Field | Value |\n|---|---|\n" + "\n".join([f"| {k} | {v} |" for k, v in payload.items()])

        # --- 核心改进：提取局部 ID 映射上下文 ---
        local_map = {}
        # 扫描 payload 中的值，匹配全量 ID 映射表
        possible_ids = [str(v) for v in payload.values()]
        for pid in possible_ids:
            if pid in full_id_map:
                local_map[pid] = full_id_map[pid]

        for domain, frags in generated_kgs[eid].items():
            if not frags: continue

            # 获取对应的领域模型 (BPI 领域通常为大写，如 LOANBUSINESS)
            model = domain_models.get(domain, {})

            # 执行全英文整体评估
            audit_result = evaluator.evaluate_holistic_en(
                original_data_md=raw_md,
                domain_model=model,
                generated_fragment=frags[0],
                local_id_map=local_map
            )
            results[eid][domain] = audit_result

    # 4. 保存结果
    BPI_FINAL_RESULTS_PATH.parent.mkdir(exist_ok=True, parents=True)
    with open(BPI_FINAL_RESULTS_PATH, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n--- [BPI 评估完成] ---")
    print(f"结果已保存至: {BPI_FINAL_RESULTS_PATH}")

    # --- 新增：Token 成本分析报告 (响应 R1.7) ---‘
    print("\n" + "=" * 25 + " BPI Cost Analysis " + "=" * 25)
    print(f"Total BPI Events Audited: {len(event_ids)}")
    print(f"Prompt Tokens Consumed: {stats.judge_prompt_tokens}")
    print(f"Completion Tokens Consumed: {stats.judge_completion_tokens}")

    # 使用与主脚本一致的 gpt-4o 计价模型
    total_cost = (stats.judge_prompt_tokens / 1_000_000 * 5.0) + (stats.judge_completion_tokens / 1_000_000 * 15.0)
    print(f"Estimated Total Cost (USD): ${total_cost:.4f}")
    if len(event_ids) > 0:
        print(f"Average Cost Per Event: ${total_cost / len(event_ids):.4f}")
    print("=" * 69 + "\n")


if __name__ == "__main__":
    main()