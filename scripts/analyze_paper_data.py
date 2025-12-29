import json
import numpy as np
from pathlib import Path

# --- 配置路径 ---
BASE_DIR = Path(__file__).resolve().parent.parent
EVAL_DIR = BASE_DIR / "data" / "evaluation"

# 结果文件路径映射
FILES = {
    "Naive-KG": EVAL_DIR / "4_evaluation_results" / "naive_evaluation_results.json",  # 假设您保存了Naive的评估结果
    "Monolithic-KG": EVAL_DIR / "4_evaluation_results" / "monolithic_evaluation_results.json",  # 假设您保存了Monolithic的评估结果
    "Ours": EVAL_DIR / "4_evaluation_results" / "evaluation_results.json"  # Ours的结果
}

# RAG 答案路径映射
RAG_FILES = {
    "Raw-RAG": EVAL_DIR / "baseline_results" / "raw_rag_answers.json",
    "Naive-KG": EVAL_DIR / "baseline_results" / "naive_kg_answers.json",
    "Monolithic-KG": EVAL_DIR / "baseline_results" / "monolithic_kg_answers.json",
    "Ours": EVAL_DIR / "baseline_results" / "ours_rag_answers.json"
}


# 定义总事件数，这是计算成功率的分母
TOTAL_EVENTS = 50


def analyze_kg_quality():
    print("\n=== RQ1: KG Generation Quality Analysis ===")
    print("-" * 80)
    print(f"{'Method':<15} | {'Success Rate':<12} | {'Fidelity':<10} | {'Conformance':<12} | {'Elegance':<10}")
    print("-" * 80)

    latex_rows = []

    for method, path in FILES.items():
        if not path.exists():
            print(f"Skipping {method} (File not found)")
            continue

        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # --- 1. 计算生成成功率 (关键修改) ---
        # 统计有多少个事件ID在结果文件中，并且该事件下有非空的内容
        successful_events = 0
        for eid, domains in data.items():
            # 只要该事件下有任何一个领域的评估结果，就算该事件生成成功
            if domains and len(domains) > 0:
                successful_events += 1

        success_rate = (successful_events / TOTAL_EVENTS) * 100

        # --- 2. 计算质量指标 (仅基于成功的事件) ---
        scores = {"fidelity": [], "conformance": [], "completeness": [], "elegance": []}

        for eid, domains in data.items():
            for dname, metrics in domains.items():
                # Fidelity
                if 'fidelity' in metrics and isinstance(metrics['fidelity'], dict):
                    val = metrics['fidelity'].get('score')
                    if val is not None: scores["fidelity"].append(val)

                # Conformance
                if 'conformance' in metrics:
                    conf = metrics['conformance'].get('overall_conformance', 'None')
                    val = 1.0 if conf == 'Full' else (0.5 if conf == 'Partial' else 0.0)
                    scores["conformance"].append(val)

                # Elegance
                if 'elegance' in metrics and 'elegance_score' in metrics['elegance']:
                    scores["elegance"].append(metrics['elegance']['elegance_score'])

        # 计算平均值 (如果没有数据，默认为0)
        avg_fid = np.mean(scores["fidelity"]) if scores["fidelity"] else 0.0
        avg_conf = np.mean(scores["conformance"]) if scores["conformance"] else 0.0
        avg_ele = np.mean(scores["elegance"]) if scores["elegance"] else 0.0

        # 打印控制台表格
        print(
            f"{method:<15} | {success_rate:.1f}%        | {avg_fid:.2f}       | {avg_conf:.2f}         | {avg_ele:.2f}")

        # 生成 LaTeX 行
        # 格式: Method & Success Rate & Fidelity & Conformance & Elegance \\
        latex_row = f"\\textbf{{{method}}} & {success_rate:.1f}\\% & {avg_fid:.2f} & {avg_conf:.2f} & {avg_ele:.2f} \\\\"
        latex_rows.append(latex_row)

    print("\n--- LaTeX Table Row Code ---")
    print("\n".join(latex_rows))


def analyze_rag_effectiveness():
    print("\n\n=== RQ2: RAG Effectiveness Analysis (Manual/LLM Scoring Needed) ===")
    print("注意：此处我们统计的是生成的答案数量和长度，作为初步指标。")
    print("真实的质量评分需要您运行 evaluate_rag_answers.py 或手动打分。")

    for method, path in RAG_FILES.items():
        if not path.exists(): continue
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        valid_answers = 0
        total_len = 0
        for item in data:
            ans = item.get('generated_answer', '')
            if "无法回答" not in ans and "未找到" not in ans and len(ans) > 5:
                valid_answers += 1
                total_len += len(ans)

        print(f"{method}: 成功回答数(估算) = {valid_answers}/15, 平均长度 = {total_len / 15:.1f} 字")


if __name__ == "__main__":
    analyze_kg_quality()
    analyze_rag_effectiveness()