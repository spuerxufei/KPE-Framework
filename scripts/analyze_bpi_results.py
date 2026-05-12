# -*- coding: utf-8 -*-
"""
【BPI 结果分析脚本 V2.0 - 修复版】
增加了对嵌套字典结构的兼容性处理。
"""
import json
from pathlib import Path
import numpy as np

# --- 配置路径 ---
BASE_DIR = Path(__file__).resolve().parent.parent
BPI_RESULTS_PATH = BASE_DIR / "data" / "evaluation" / "4_evaluation_results" / "bpi_evaluation_results.json"


def analyze_bpi():
    print("--- 正在分析 BPI 泛化性评估结果 ---")

    if not BPI_RESULTS_PATH.exists():
        print(f"错误: 找不到评估结果文件 {BPI_RESULTS_PATH}。")
        return

    try:
        with open(BPI_RESULTS_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"错误: 读取JSON文件失败: {e}")
        return

    # 统计容器
    scores = {"fidelity": []}
    conformance_counts = {"Full": 0, "Partial": 0, "None": 0}
    all_hallucinations = {}
    all_missed_fields = {}
    total_fragments = 0

    for eid, domains in data.items():
        for dname, results in domains.items():
            total_fragments += 1

            # 1. 提取 Fidelity Score
            f_score = results.get("fidelity_assessment", {}).get("score")
            if f_score is not None:
                scores["fidelity"].append(f_score)

            # 2. 统计 Conformance
            conf = results.get("conformance_assessment", {}).get("overall", "None")
            if conf not in conformance_counts:
                conformance_counts[conf] = 0
            conformance_counts[conf] += 1

            # 3. 收集幻觉原因
            details = results.get("fidelity_assessment", {}).get("details", [])
            for d in details:
                if d.get("status") == "hallucinated":
                    reason = d.get("reason", "Unknown")
                    all_hallucinations[reason] = all_hallucinations.get(reason, 0) + 1

            # 4. 【关键修复逻辑】收集遗漏字段
            # 兼容处理：可能存的是字符串列表，也可能是对象列表
            missed = results.get("completeness_assessment", {}).get("missed_fields", [])
            if not missed:
                # 兼容另一种可能的 Key 名
                missed = results.get("completeness_assessment", {}).get("missed_information", [])

            for item in missed:
                if isinstance(item, dict):
                    # 如果是字典，尝试获取具体的字段名标识
                    field_name = item.get("info") or item.get("field") or str(item)
                else:
                    # 如果已经是字符串，直接使用
                    field_name = str(item)

                all_missed_fields[field_name] = all_missed_fields.get(field_name, 0) + 1

    # --- 输出报告 ---
    print("\n" + "=" * 40)
    print("BPI-2017 泛化性实验统计报告")
    print("=" * 40)
    print(f"总计评估片段数: {total_fragments}")

    if scores["fidelity"]:
        print(f"平均事实忠实度 (Fidelity): {np.mean(scores['fidelity']):.4f}")
    else:
        print("平均事实忠实度 (Fidelity): N/A")

    print(f"\nSchema 合规性分布:")
    for k, v in conformance_counts.items():
        print(f"  - {k}: {v} ({v / total_fragments * 100:.1f}%)")

    print(f"\n常见‘幻觉’原因分析 (Top 3):")
    sorted_hal = sorted(all_hallucinations.items(), key=lambda x: x[1], reverse=True)
    for k, v in sorted_hal[:3]:
        print(f"  - {k}: {v} 次")

    print(f"\n跨领域‘遗漏’字段分析 (Top 5):")
    sorted_missed = sorted(all_missed_fields.items(), key=lambda x: x[1], reverse=True)
    for k, v in sorted_missed[:5]:
        print(f"  - {k}: {v} 次")
    print("=" * 40)


if __name__ == "__main__":
    analyze_bpi()