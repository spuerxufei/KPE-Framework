# -*- coding: utf-8 -*-
"""
BPI Challenge 2017 真实数据提取器

功能：
1. 读取 data/evaluation/0_raw_sources/raw_bpi_2017.csv
2. 遍历所有行。
3. 筛选策略：只保留那些“信息量大”的行。
   标准：必须同时包含 'OfferedAmount' (有报价) 和 'CreditScore' (有信用分)。
   这通常对应 'O_Create Offer' 或后续的富集事件。
4. 格式化为 RawBusinessEvent 并保存。
"""
import csv
import json
from pathlib import Path

# --- 配置 ---
BASE_DIR = Path(__file__).resolve().parent.parent
INPUT_CSV_PATH = BASE_DIR / "data" / "evaluation" / "0_raw_sources" / "raw_bpi_2017.csv"
OUTPUT_JSONL_PATH = BASE_DIR / "data" / "evaluation" / "1_input_events" / "events_bpi.jsonl"


def extract_real_bpi_events(target_count=1000):
    print(f"--- 开始从真实 BPI 数据集提取 ({INPUT_CSV_PATH}) ---")

    events = []

    try:
        with open(INPUT_CSV_PATH, 'r', encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f)

            # 统计
            total_rows = 0
            matched_rows = 0

            for row in reader:
                total_rows += 1

                # --- 核心筛选逻辑 ---
                # 我们只提取那些信息最全的事件，通常是 Offer 阶段的事件
                # 必须有信用分，且必须有批准金额，且操作员不是机器(User_1通常是系统自动)
                if (row.get('CreditScore') and
                        row.get('OfferedAmount') and
                        row.get('case:LoanGoal') and
                        row.get('org:resource') != 'User_1'):

                    matched_rows += 1

                    # 为了避免重复（同一个Case可能有多个Offer事件），我们可以做个简单去重
                    # 或者为了实验多样性，我们每隔几个取一个
                    if matched_rows % 5 != 0:  # 简单的采样策略，避免数据过于集中
                        continue

                    # --- 真实字段映射 ---
                    # 严格使用 CSV 中存在的字段
                    payload = {
                        # 1. 信贷业务领域数据
                        "application_id": row.get('case:concept:name'),  # Case ID
                        "activity_name": row.get('concept:name'),  # Activity
                        "loan_goal": row.get('case:LoanGoal'),  # 贷款目的
                        "application_type": row.get('case:ApplicationType'),  # 申请类型
                        "requested_amount": row.get('case:RequestedAmount'),  # 申请金额
                        "offered_amount": row.get('OfferedAmount'),  # 批准金额
                        "offer_id": row.get('OfferID'),  # Offer ID
                        "number_of_terms": row.get('NumberOfTerms'),  # 分期数
                        "monthly_cost": row.get('MonthlyCost'),  # 月还款

                        # 2. 风险/客户领域数据
                        "credit_score": row.get('CreditScore'),  # 信用分
                        "accepted": row.get('Accepted'),  # 客户是否接受
                        "selected": row.get('Selected'),  # 是否选中

                        # 3. 人力资源领域数据
                        "operator_id": row.get('org:resource'),  # 员工ID
                        "lifecycle_status": row.get('lifecycle:transition'),  # 状态
                        "event_origin": row.get('EventOrigin')  # 事件来源
                    }

                    # 由于CSV可能没有ISO时间戳(通常BPI有，但如果您的CSV表头没显示，我们假设它存在或模拟)
                    # 您的表头里没有 timestamp，这里我们用 EventID 模拟一个唯一性，
                    # 并在 payload 里保留原始 EventID
                    # *注意*：如果您的CSV里其实有时间列（如 'time:timestamp'），请加上。
                    # 这里为了代码能跑，我暂时不放 timestamp 到 payload，
                    # 而是依赖 RawBusinessEvent 的 event_timestamp 字段（这里暂时模拟）

                    event = {
                        "event_id": f"BPI-REAL-{row.get('EventID')}",
                        "event_type": "LOAN_OFFER_PROCESS",
                        "event_timestamp": "2017-01-01T12:00:00Z",  # 占位，因为您提供的表头没时间
                        "payload": payload
                    }

                    events.append(event)

                    if len(events) >= target_count:
                        break

    except FileNotFoundError:
        print(f"错误：找不到文件 {INPUT_CSV_PATH}")
        return

    # 写入 JSONL
    OUTPUT_JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSONL_PATH, 'w', encoding='utf-8') as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    print(f"--- 提取完成！遍历了 {total_rows} 行，筛选出 {len(events)} 个高质量真实事件。 ---")
    print(f"文件已保存至: {OUTPUT_JSONL_PATH}")
    # 打印一条样本供检查
    if events:
        print("样本数据:")
        print(json.dumps(events[0], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    extract_real_bpi_events()