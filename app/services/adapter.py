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
        【V3.2 终极重构版】
        统一处理所有预定义实体的身份解析和名称捕获。
        此方法现在是配置驱动的，不再包含硬编码的实体处理逻辑。
        """
        print(f"--- [适配器 V3.2] 开始处理事件: {event.event_id} ---")

        # 在需要时才创建服务实例 (保持按需连接的最佳实践)
        identity_service = EntityIdentityService(evaluation_mode=self.evaluation_mode)
        payload = event.payload
        identified_entities = []

        # --- 1. 【核心重构】统一的、配置驱动的多实体身份解析 ---

        # 定义我们能够识别的所有实体类型及其可能的ID和名称字段
        # 这个定义可以被移到类的配置或外部文件中，以获得更高的灵活性 原方案！！！！！！！
        # entity_definitions = {
        #     "古籍": {"id_keys": ["asset_system_id", "asset_id", "id", "book_id"], "name_keys": ["title", "name"]},
        #     "员工": {"id_keys": ["operator_id", "handler", "user_id", "trainee_id"],
        #              "name_keys": ["operator_name", "user_name"]},
        #     "读者": {"id_keys": ["reader_id"], "name_keys": ["reader_name", "user_name"]}
        #     # 未来可以轻松扩展，例如:
        #     # "出版社": {"id_keys": ["publisher_id"], "name_keys": ["publisher_name"]}
        # }
        # 修改 entity_definitions BPI方案
        entity_definitions = {
            "古籍": {"id_keys": ["asset_system_id", "asset_id", "id", "book_id"], "name_keys": ["title", "name"]},

            # 【BPI 适配】 员工ID在CSV中是 "org:resource"，在payload里被我们映射为 "operator_id"
            "员工": {
                "id_keys": ["operator_id", "handler", "user_id", "org:resource"],
                "name_keys": ["operator_name"]  # BPI数据没有名字，只有ID，这没关系
            },

            "读者": {"id_keys": ["reader_id"], "name_keys": ["reader_name", "user_name"]},

            # 【BPI 适配】 贷款申请ID
            "贷款申请": {
                "id_keys": ["application_id", "case:concept:name"],
                "name_keys": []
            }
        }

        # 遍历所有定义的实体类型，尝试在payload中找到它们
        for entity_type, definition in entity_definitions.items():

            # 查找业务ID (逻辑不变)
            business_key = next((payload.get(key) for key in definition["id_keys"] if payload.get(key) is not None),
                                None)

            # 如果找到了业务ID，就继续处理
            if business_key:
                # 查找名称 (逻辑不变)
                entity_name = next(
                    (payload.get(key) for key in definition["name_keys"] if payload.get(key) is not None), None)

                # 创建人类可读的标签 (逻辑不变)
                label = f"{entity_name}({business_key})" if entity_name else f"{entity_type}({business_key})"

                # 【核心修改】调用V3.2的身份服务，统一传递业务ID和名称（可能为None）
                uuid = identity_service.get_or_create_uuid(
                    business_key=business_key,
                    entity_name=entity_name
                )

                # 封装为IdentifiedEntity对象 (逻辑不变)
                identified_entities.append(IdentifiedEntity(
                    business_key=str(business_key),
                    entity_type=entity_type,
                    label=label,
                    uuid=uuid
                ))

                log_name_part = f" (名称: '{entity_name}')" if entity_name else ""
                print(f"    - 已解析实体 [{entity_type}]: 业务ID '{business_key}'{log_name_part} -> 内部UUID '{uuid}'")

        # --- 2. 数据格式化 (逻辑不变) ---
        key_to_name_map = {
            "asset_system_id": "珍善本统一编号", "title": "题名", "operator_id": "操作人员工号",
            "operator_name": "操作人员姓名", "scan_details": "数字化详情", "storage_info": "存储信息",
            "physical_condition": "物理状况", "copyright_assessment": "版权评估", "budget_code": "预算代码",
            "reader_id": "读者ID", "user_name": "用户名", "book_id": "图书ID", "due_date": "应还日期"
        }
        print("    - [输入] 适配器接收到的原始载荷: ", payload)
        markdown_rows = ["| 属性名 | 属性值 |", "|---|---|"]
        for key, value in payload.items():
            friendly_name = key_to_name_map.get(key, key)
            if isinstance(value, dict):
                value_str = ", ".join([f"{k}: {v}" for k, v in value.items()])
            else:
                value_str = str(value)
            markdown_rows.append(f"| {friendly_name} | {value_str} |")
        markdown_str = "\n".join(markdown_rows)
        print("    - [输出] 适配器格式化后的Markdown: \n", markdown_str)

        # --- 3. 封装为新的标准数据对象 (逻辑不变) ---
        # 注意：这里我们不再将 event_timestamp 加入 payload
        # 我们遵循V3.0.1的设计，让时间戳作为框架级的元数据在StandardizedDataObject中传递
        standardized_data = StandardizedDataObject(
            source_event_id=event.event_id,
            event_timestamp=event.event_timestamp,  # 确保传递时间戳
            identified_entities=identified_entities,
            source_data_markdown=markdown_str
        )
        print(f"--- [适配器 V3.2] 事件处理完成 ---")
        return standardized_data