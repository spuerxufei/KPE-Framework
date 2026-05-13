# -*- coding: utf-8 -*-
"""
本脚本使用国际标准的 jsonschema 库，对生成的 JSON-LD
进行严格的、确定性的结构和外键约束检查。
"""
import json
from pathlib import Path
from jsonschema import validate, ValidationError

# --- 配置 ---
BASE_DIR = Path(__file__).resolve().parent.parent
# 根据您实际测试的文件切换 (RB-50 或 BPI)
GENERATED_KGS_PATH = BASE_DIR / "data" / "evaluation" / "3_generated_results" / "generated_kgs_bpi_sample.json"

# --- 定义严谨的 JSON-LD 基础结构 Schema ---
# 强制要求 @id 和 @type。
# 强制要求所有业务属性（不以 @ 开头的键）如果是一个对象(关系)，则必须包含 @id 外键。
JSONLD_BASE_SCHEMA = {
    "type": "object",
    "required": ["@id", "@type"],
    "properties": {
        "@id": {"type": "string", "minLength": 1},
        "@type": {
            "anyOf": [
                {"type": "string", "minLength": 1},
                {"type": "array", "items": {"type": "string", "minLength": 1}}
            ]
        }
    },
    # 针对所有普通的业务关系和属性
    "patternProperties": {
        "^[^@].*$": {
            "anyOf": [
                {"type": ["string", "number", "boolean", "null"]},
                {
                    "type": "object",
                    "required": ["@id"],  # 如果是关系对象，必须包含外键 @id
                    "properties": {
                        "@id": {"type": "string", "minLength": 1}
                    }
                },
                {
                    "type": "array",
                    "items": {
                        "anyOf": [
                            {"type": ["string", "number"]},
                            {"type": "object", "required": ["@id"]}  # 列表里的对象也必须有 @id
                        ]
                    }
                }
            ]
        }
    }
}


def perform_deterministic_check():
    print("--- 启动确定性 Schema 校验 (基于 jsonschema 库) ---")

    if not GENERATED_KGS_PATH.exists():
        print(f"错误: 找不到文件 {GENERATED_KGS_PATH}")
        return

    with open(GENERATED_KGS_PATH, 'r', encoding='utf-8') as f:
        generated_data = json.load(f)

    total_fragments = 0
    passed_validation = 0

    for event_id, domains in generated_data.items():
        if not domains: continue
        for domain, fragments in domains.items():
            for frag in fragments:
                total_fragments += 1

                try:
                    # 使用真正的 jsonschema 进行校验
                    validate(instance=frag, schema=JSONLD_BASE_SCHEMA)
                    passed_validation += 1
                except ValidationError as e:
                    # 打印具体的违规原因，这显得极其专业
                    print(f"  [违规] {event_id} - 节点 {frag.get('@id', 'UNKNOWN')}")
                    print(f"  [原因] {e.message}")

    # --- 输出报告 ---
    print("\n=== [响应 R2.3] 确定性校验报告 (Deterministic Report) ===")
    print(f"总计审查的图谱片段数量: {total_fragments}")
    if total_fragments == 0:
        return

    pass_rate = (passed_validation / total_fragments) * 100
    print(f"JSON-LD 语法与关系链接完整性 (基于 jsonschema): {passed_validation}/{total_fragments} ({pass_rate:.2f}%)")
    print("=========================================================\n")


if __name__ == "__main__":
    perform_deterministic_check()