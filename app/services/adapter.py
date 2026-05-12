# -*- coding: utf-8 -*-
"""
输入适配器（防腐层）模块

【V3.0 终极重构版】
本模块实现了防腐层（Anticorruption Layer）的最终设计。
EntityIdentityService现在以“统一业务ID”为唯一锚点，来聚合多重领域身份，
确保一个业务实体在系统中只拥有一个核心身份（CoreEntity）。
"""
from app.models import RawBusinessEvent, StandardizedDataObject, IdentifiedEntity
from neo4j import GraphDatabase, exceptions
import json
from pathlib import Path
from app.config import settings

# --- 静态UUID加载路径 (保持不变) ---
BASE_DIR = Path(__file__).resolve().parent.parent.parent
GOLD_STANDARD_UUIDS_PATH = BASE_DIR / "data" / "evaluation" / "gold_standard_uuids.json"


# --- 用以下代码替换掉您原来的整个 EntityIdentityService 类 ---
class EntityIdentityService:
    """
    【V3.2 终极版】实体身份服务。
    此版本统一了所有核心实体的身份和名称管理逻辑。
    """

    def __init__(self, evaluation_mode: bool = False):
        """
        初始化身份服务。
        【已修改】: __init__不再创建数据库连接，只保存连接信息和模式。
        """
        self.evaluation_mode = evaluation_mode
        self._static_uuid_map = None

        # 保存连接信息，而不是连接实例
        self.uri = settings.NEO4J_URI  # 注意：这里是Naive基线的端口，请根据需要修改
        self.user = settings.NEO4J_USER
        self.password = settings.NEO4J_PASSWORD

        if self.evaluation_mode:
            print("--- [身份服务] 运行在评估模式下，将使用静态UUID映射。---")
            try:
                with open(GOLD_STANDARD_UUIDS_PATH, 'r', encoding='utf-8') as f:
                    self._static_uuid_map = json.load(f)
                print("    - 静态UUID映射文件加载成功。")
            except FileNotFoundError:
                print(f"--- [身份服务错误] 评估模式需要文件，但未找到: {GOLD_STANDARD_UUIDS_PATH}")
                self._static_uuid_map = {}
        else:
            # 应用启动时，仍然可以进行一次性地检查和设置，但这不再是必须的
            # 并且这个检查本身也应该使用临时连接
            self._setup_database_constraints()

    def _setup_database_constraints(self):
        """【新增】使用临时连接来设置数据库约束，确保进程安全。"""
        driver = None
        try:
            driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            with driver.session() as session:
                print("--- [身份服务] 正在验证数据库连接并设置约束...")
                driver.verify_connectivity()
                session.run(
                    "CREATE CONSTRAINT identifier_value_unique IF NOT EXISTS FOR (i:Identifier) REQUIRE i.value IS UNIQUE")
                session.run(
                    "CREATE CONSTRAINT core_entity_uuid_unique IF NOT EXISTS FOR (c:CoreEntity) REQUIRE c.uuid IS UNIQUE")
                session.run("CREATE INDEX core_entity_uuid_idx IF NOT EXISTS FOR (c:CoreEntity) ON (c.uuid)")
                print("--- [身份服务] 数据库连接正常，约束和索引已设置。 ---")
        except Exception as e:
            print(f"--- [身份服务错误] 初始化数据库约束时失败: {e}")
        finally:
            if driver:
                driver.close()

    def get_or_create_uuid(self, business_key: str, entity_name: str = None) -> str:
        """
        【已修改】: 现在在方法内部创建和销毁数据库连接。
        """
        business_key_str = str(business_key)

        if self.evaluation_mode:
            # ... (evaluation_mode 的逻辑不变) ...
            for entity_type, mapping in self._static_uuid_map.items():
                if business_key_str in mapping:
                    return mapping[business_key_str]
            print(f"--- [身份服务警告] 在评估模式下，未在静态文件中找到业务ID '{business_key_str}' 的UUID！")
            return f"UNKNOWN-UUID-{business_key_str}"
        else:
            # 在运行时模式下，按需创建和关闭连接
            driver = None
            try:
                driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
                with driver.session() as session:
                    result = session.write_transaction(self._merge_identity_v3, business_key_str,entity_name)
                return result
            except Exception as e:
                print(f"--- [身份服务错误] 数据库事务执行失败: {e}")
                return f"ERROR-DB-TRANSACTION-FOR-{business_key_str}"
            finally:
                if driver:
                    driver.close()

    @staticmethod
    def _merge_identity_v3(tx, business_key, entity_name):
        """
        【V3.2 终极重构版】
        统一处理所有核心实体的身份聚合与名称关联。
        """
        # 步骤 1: 确保核心身份结构 (Identifier -> CoreEntity) 的存在
        # 这部分查询确保了无论如何，我们都能得到一个唯一的CoreEntity UUID
        tx.run(
            """
            MERGE (idNode:Identifier {value: $business_key})
            MERGE (idNode)-[:IDENTIFIES]->(core:CoreEntity)
            ON CREATE SET core.uuid = randomUUID()
            """,
            business_key=business_key
        )

        # 步骤 2: 如果提供了名称，则处理名称节点和关系
        if entity_name:
            tx.run(
                """
                MATCH (core:CoreEntity)<-[:IDENTIFIES]-(:Identifier {value: $business_key})
                MERGE (nameNode:Name {value: $entity_name})
                MERGE (core)-[:HAS_ALIAS]->(nameNode)
                """,
                business_key=business_key, entity_name=entity_name
            )

        # 步骤 3: 再次查询并返回最终的UUID
        # 我们分两步走，是为了确保逻辑的清晰性和在事务中的健壮性
        result = tx.run(
            """
            MATCH (:Identifier {value: $business_key})-[:IDENTIFIES]->(core:CoreEntity)
            RETURN core.uuid AS uuid
            """,
            business_key=business_key
        )

        return result.single()["uuid"]


# --- 全局实例化的修改 ---

class InputAdapter:
    """
    【V3.0.1 修复版】实现了防腐层（Anticorruption Layer）的核心逻辑。
    """

    def __init__(self, evaluation_mode: bool = False):
        """
        初始化适配器。
        【已修改】: __init__不再创建和持有identity_service实例。
        """
        self.evaluation_mode = evaluation_mode

    def transform_and_standardize(self, event: RawBusinessEvent) -> StandardizedDataObject:
        """
        【V3.3 全英文适配版】
        统一处理所有预定义实体的身份解析和名称捕获。
        """
        print(f"--- [适配器] 开始处理事件: {event.event_id} ---")

        identity_service = EntityIdentityService(evaluation_mode=self.evaluation_mode)
        payload = event.payload
        identified_entities = []

        # --- 1. 【英文实体定义适配】 ---
        entity_definitions = {
            "LoanApplication": {
                "id_keys": ["application_id", "case_id"],
                "name_keys": ["application_type"]
            },
            "Employee": {
                "id_keys": ["operator_id", "org:resource"],
                "name_keys": ["operator_name"]
            }
        }

        for entity_type, defn in entity_definitions.items():
            business_key = next((payload.get(k) for k in defn["id_keys"] if payload.get(k) is not None), None)

            if business_key:
                entity_name = next((payload.get(k) for k in defn["name_keys"] if payload.get(k) is not None), None)
                label = f"{entity_name}({business_key})" if entity_name else f"{entity_type}({business_key})"

                uuid = identity_service.get_or_create_uuid(business_key=business_key, entity_name=entity_name)

                identified_entities.append(IdentifiedEntity(
                    business_key=str(business_key),
                    entity_type=entity_type,
                    label=label,
                    uuid=uuid
                ))
                log_name_part = f" (Name: '{entity_name}')" if entity_name else ""
                print(f"    - 已解析实体 [{entity_type}]: 业务ID '{business_key}'{log_name_part} -> 内部UUID '{uuid}'")

        # --- 2. 【全英文数据格式化】 ---
        key_to_name_map = {
            "loan_goal": "Loan Purpose",
            "application_type": "Application Type",
            "requested_amount": "Requested Loan Amount",
            "offered_amount": "Offered Amount by Bank",
            "number_of_terms": "Number of Terms",
            "monthly_cost": "Monthly Payment",
            "credit_score": "Credit Score",
            "accepted": "Offer Accepted by Client",
            "selected": "Offer Selected",
            "activity_name": "Activity Name",
            "lifecycle_status": "Lifecycle Status",
            "event_origin": "Event Origin System",
            "operator_id": "Operator ID",
            "application_id": "Loan Application ID"
        }

        print("    - [输入] 原始载荷: ", payload)
        # Markdown 表头也改为英文
        markdown_rows = ["| Business Attribute | Value |", "|---|---|"]
        for key, value in payload.items():
            friendly_name = key_to_name_map.get(key, key)
            if isinstance(value, dict):
                value_str = ", ".join([f"{k}: {v}" for k, v in value.items()])
            else:
                value_str = str(value)
            markdown_rows.append(f"| {friendly_name} | {value_str} |")
        markdown_str = "\n".join(markdown_rows)
        print("    - [输出] 格式化后的 Markdown: \n", markdown_str)

        standardized_data = StandardizedDataObject(
            source_event_id=event.event_id,
            event_timestamp=event.event_timestamp,
            identified_entities=identified_entities,
            source_data_markdown=markdown_str
        )
        return standardized_data