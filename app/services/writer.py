# -*- coding: utf-8 -*-
"""
【V3.6 终极版】知识写入器模块 (Knowledge Writer Module)

本模块实现了最终的、高度健壮的写入逻辑。它能够利用完整的实体上下文，
对LLM返回的不完整信息进行智能补全，并始终如一地构建出正确的
“核心实体-角色-领域知识”三层模型，严格遵循“身份第一”原则。
"""
import json
import hashlib
import uuid as uuid_lib
from neo4j import GraphDatabase, exceptions
from typing import Dict, Any, List

# 确保能导入我们需要的模型和仓库
try:
    from app.models import IdentifiedEntity
    from app.repositories.static_repository import static_repo
    from app.config import settings
except ImportError:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from app.models import IdentifiedEntity
    from app.repositories.static_repository import static_repo
    from app.config import settings


class KnowledgeWriter:
    """【V3.6】实现了具备“信息补全”能力的、上下文感知的健壮写入逻辑。"""

    def __init__(self):
        """
        初始化写入器，建立与Neo4j的连接驱动。
        """
        # 从全局配置读取连接信息
        self.uri = settings.NEO4J_URI
        self.user = settings.NEO4J_USER
        self.password = settings.NEO4J_PASSWORD
        try:
            self._driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            print(f"--- [写入器 V3.6] 正在连接到 {self.uri}... ---")
            self._driver.verify_connectivity()
            print("--- [写入器 V3.6] 成功连接到Neo4j数据库 ---")
        except Exception as e:
            print(f"--- [写入器错误] 无法连接到Neo4j数据库: {e} ---")
            self._driver = None

    def close(self):
        """关闭数据库连接。"""
        if self._driver is not None:
            self._driver.close()

    def write_fragment(
            self,
            fragment: Dict[str, Any],
            domain: str,
            source_event_id: str,
            identified_entities: List[IdentifiedEntity]
    ):
        """
        【V3.7重构】将JSON-LD写入图数据库。
        新增特性：匿名节点内容哈希去重 (Content Hashing)，彻底解决冗余节点问题。
        """
        if self._driver is None:
            print("--- [写入器错误] 数据库未连接，跳过写入。 ---")
            return

        # 步骤 1: 准备所有必要的上下文信息
        # ------------------------------------
        domain_model = static_repo.get_schema(domain)
        role_map = domain_model.get("role_mapping", {})
        uuid_to_entity_map = {entity.uuid: entity for entity in identified_entities}

        cypher_parts = []
        params = {}
        processed_uuids_in_fragment = set()

        # --- 新增辅助函数：生成确定性内容哈希 ---
        def generate_content_hash(data: Dict) -> str:
            """
            根据字典内容生成确定性的MD5哈希，用于匿名节点ID去重。
            只对当前层的属性（非字典、非列表）进行哈希，忽略 @type 以避免干扰。
            """
            try:
                # 筛选出基本类型的属性进行哈希
                content_to_hash = {
                    k: v for k, v in data.items()
                    if not isinstance(v, (dict, list)) and k != "@type"
                }
                # sort_keys=True 保证键的顺序一致，ensure_ascii=False 处理中文
                canonical_str = json.dumps(content_to_hash, sort_keys=True, ensure_ascii=False)
                return hashlib.md5(canonical_str.encode('utf-8')).hexdigest()
            except Exception:
                # 如果发生任何序列化错误，回退到随机UUID以防崩溃
                return str(uuid_lib.uuid4())

        def process_node(node_data: Dict, parent_var: str = None, rel_name: str = None):
            """
            内部递归函数，负责将JSON-LD节点及其关系转换为Cypher语句。
            """
            # 步骤 A: 确定当前节点的基本信息
            # ---------------------------------
            node_id = node_data.get('@id')
            node_type = node_data.get('@type')

            if rel_name and not node_type:  # 如果是嵌套的匿名节点且没有类型
                node_type = "".join(word.capitalize() for word in rel_name.split())
                node_data['@type'] = node_type

            if not node_id:  # 如果是匿名节点
                if parent_var:
                    # --- 【V3.7 关键修改】 使用内容哈希替代随机UUID ---
                    content_hash = generate_content_hash(node_data)
                    # ID 格式: bnode_<类型>_<哈希>，既保证唯一性又保证可读性
                    # 加入类型是为了防止不同类型的节点因为属性巧合相同而被合并
                    safe_type = node_type if node_type else "Unknown"
                    node_id = f"bnode_{safe_type}_{content_hash}"
                else:
                    print(f"--- [写入器警告] 顶层知识片段缺少@id，跳过: {fragment} ---")
                    return

            # (以下代码保持原样，未做修改)
            current_var = f"n_{abs(hash(node_id)) % 10000}"
            params[f"id_{current_var}"] = node_id

            node_labels = f":`{node_type}`"
            if parent_var is None:  # 顶层节点增加领域标签
                domain_label = "".join(
                    word.capitalize() for word in domain.replace("领域", "").replace("/", "")) + "Node"
                node_labels += f":`{domain_label}`"

            # 步骤 B: 构建主节点的 MERGE 语句
            cypher_parts.append(f"MERGE ({current_var}{node_labels} {{fragmentId: $id_{current_var}}})")

            # 步骤 C: 分类处理节点的属性和关系
            properties = {}
            for key, value in node_data.items():
                if key.startswith('@'): continue

                if isinstance(value, dict):
                    target_uuid = value.get('@id')

                    if not target_uuid:  # 这是一个需要递归处理的匿名节点
                        process_node(value, parent_var=current_var, rel_name=key)
                        continue

                    # --- V3.6 终极身份与角色处理逻辑 (保持不变) ---
                    print(f"    - [写入器 V3.6] 正在处理关系 '{key}' -> (UUID: {target_uuid[:8]}...)")
                    processed_uuids_in_fragment.add(target_uuid)

                    target_entity_info = uuid_to_entity_map.get(target_uuid)
                    if not target_entity_info:
                        print(
                            f"        - [警告] LLM返回了一个未知的UUID '{target_uuid}'，无法为其创建关系。跳过关系 '{key}'。")
                        continue

                    target_business_key = target_entity_info.business_key
                    target_entity_type = target_entity_info.entity_type
                    role_label = role_map.get(target_entity_type, target_entity_type)

                    core_var = f"core_{abs(hash(target_uuid)) % 10000}"
                    id_var = f"id_{abs(hash(target_uuid)) % 10000}"
                    role_var = f"role_{abs(hash(target_uuid + role_label)) % 10000}"
                    params[f"bizKey_{id_var}"] = str(target_business_key)

                    cypher_parts.append(
                        f"// --- 通过业务ID '{target_business_key}' 聚合身份并创建角色 '{role_label}' ---")
                    cypher_parts.append(f"MERGE ({id_var}:Identifier {{value: $bizKey_{id_var}}})")
                    cypher_parts.append(f"MERGE ({id_var})-[:IDENTIFIES]->({core_var}:CoreEntity)")
                    cypher_parts.append(f"ON CREATE SET {core_var}.uuid = '{target_uuid}'")
                    cypher_parts.append(f"MERGE ({role_var}:{role_label} {{businessKey: $bizKey_{id_var}}})")
                    cypher_parts.append(f"MERGE ({role_var})-[:PLAYED_BY]->({core_var})")
                    cypher_parts.append(f"MERGE ({current_var})-[:`{key}`]->({role_var})")

                elif isinstance(value, list):
                    # --- [V3.6 修复幽灵节点] 混合列表去噪策略 (保持不变) ---
                    has_strong_ref = False
                    for item in value:
                        if isinstance(item, dict) and item.get('@id'):
                            if item.get('@id') in uuid_to_entity_map:
                                has_strong_ref = True
                                break

                    for item in value:
                        if not isinstance(item, dict): continue

                        target_uuid = item.get('@id')

                        # --- 分支 A: 处理强引用链接 (保持不变) ---
                        if target_uuid:
                            target_entity_info = uuid_to_entity_map.get(target_uuid)
                            if not target_entity_info:
                                print(f"        - [警告] 列表中的UUID '{target_uuid}' 未知，跳过。")
                                continue

                            print(f"    - [写入器 V3.6] 正在处理列表关系 '{key}' -> (UUID: {target_uuid[:8]}...)")
                            processed_uuids_in_fragment.add(target_uuid)

                            target_business_key = target_entity_info.business_key
                            target_entity_type = target_entity_info.entity_type
                            role_label = role_map.get(target_entity_type, target_entity_type)

                            import random
                            suffix = f"{abs(hash(target_uuid)) % 10000}_{random.randint(10, 99)}"
                            core_var = f"core_{suffix}"
                            id_var = f"id_{suffix}"
                            role_var = f"role_{suffix}"

                            params[f"bizKey_{id_var}"] = str(target_business_key)

                            cypher_parts.append(f"MERGE ({id_var}:Identifier {{value: $bizKey_{id_var}}})")
                            cypher_parts.append(f"MERGE ({id_var})-[:IDENTIFIES]->({core_var}:CoreEntity)")
                            cypher_parts.append(f"ON CREATE SET {core_var}.uuid = '{target_uuid}'")
                            cypher_parts.append(f"MERGE ({role_var}:{role_label} {{businessKey: $bizKey_{id_var}}})")
                            cypher_parts.append(f"MERGE ({role_var})-[:PLAYED_BY]->({core_var})")
                            cypher_parts.append(f"MERGE ({current_var})-[:`{key}`]->({role_var})")

                        # --- 分支 B: 处理匿名节点 ---
                        else:
                            if has_strong_ref:
                                print(f"    - [写入器去噪] 忽略列表中的冗余匿名节点，因为已存在强引用: {item}")
                                continue
                            process_node(item, parent_var=current_var, rel_name=key)
                else:
                    properties[key] = value

            # 步骤 D: 构建属性的 SET 语句 (保持不变)
            if properties:
                params[f"props_{current_var}"] = properties
                cypher_parts.append(f"SET {current_var} += $props_{current_var}")

            # 步骤 E: 如果是子节点（匿名节点），创建与父节点的联系 (保持不变)
            if parent_var and rel_name:
                cypher_parts.append(f"MERGE ({parent_var})-[:`{rel_name}`]->({current_var})")

        # --- 写入器主流程开始 ---

        # 步骤 I: 正常处理 (保持不变)
        process_node(fragment)

        # 步骤 II: 反向链接逻辑 (保持不变)
        print("--- [写入器 V3.6] 开始执行反向链接检查 ---")
        top_node_var = f"n_{abs(hash(fragment.get('@id'))) % 10000}"

        for entity in identified_entities:
            if entity.uuid not in processed_uuids_in_fragment:
                print(f"    - 发现实体 '{entity.label}' 未被显式链接，正在创建默认关系...")

                role_label = role_map.get(entity.entity_type, entity.entity_type)
                core_var = f"core_{abs(hash(entity.uuid)) % 10000}"
                id_var = f"id_{abs(hash(entity.uuid)) % 10000}"
                role_var = f"role_{abs(hash(entity.uuid + role_label)) % 10000}"
                params[f"bizKey_{id_var}"] = str(entity.business_key)

                cypher_parts.append(f"// --- 反向链接: 确保与实体 '{entity.label}' (角色: {role_label}) 的关联 ---")
                cypher_parts.append(f"MERGE ({id_var}:Identifier {{value: $bizKey_{id_var}}})")
                cypher_parts.append(f"MERGE ({id_var})-[:IDENTIFIES]->({core_var}:CoreEntity)")
                cypher_parts.append(f"ON CREATE SET {core_var}.uuid = '{entity.uuid}'")
                cypher_parts.append(f"MERGE ({role_var}:{role_label} {{businessKey: $bizKey_{id_var}}})")
                cypher_parts.append(f"MERGE ({role_var})-[:PLAYED_BY]->({core_var})")
                cypher_parts.append(f"MERGE ({top_node_var})-[:RELATES_TO]->({role_var})")

        # 步骤 III: 执行 (保持不变)
        params['source_event_id'] = source_event_id
        cypher_parts.append(f"SET {top_node_var}.sourceEventId = $source_event_id")

        if not cypher_parts:
            return

        final_cypher = "\n".join(cypher_parts)
        print(f"--- [写入器 V3.7] 正在为领域 '{domain}' 执行最终的Cypher查询 ---")
        # print("    - 查询语句:\n", final_cypher)
        # print("    - 参数:\n", params)

        with self._driver.session() as session:
            session.write_transaction(lambda tx: tx.run(final_cypher, **params))

        print(f"--- [写入器 V3.7] 知识片段 '{fragment.get('@id')}' 成功写入数据库 ---")