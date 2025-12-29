# -*- coding: utf-8 -*-
"""
数据契约模块 (Data Contracts Module)

【V2.0 重构版】
本文件定义了整个知识增殖流程中所有核心的数据结构。
CoreEntity 被重构为纯粹的身份标识，并引入了IdentifiedEntity
用于在流程早期传递更丰富的上下文信息。
"""
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Literal, Union
from datetime import datetime
import uuid

# --- 1. 事件入口与防腐层 (Phase 1: Ingress & ACL) ---

class RawBusinessEvent(BaseModel):
    """
    对应架构图 (a): 原始业务事件
    (此模型保持不变)
    """
    event_id: str = Field(..., description="事件的唯一标识符，来自源系统")
    event_type: str = Field(..., description="事件类型，如 'DIGITIZATION_RARE_BOOK'")
    event_timestamp: str = Field(..., description="事件发生的时间戳")
    payload: Dict[str, Any] = Field(..., description="事件的核心载荷，结构可变")

    class Config:
        json_schema_extra = {
            "example": {
                "event_id": "RAW-EVT-20240521-001",
                "event_type": "DIGITIZATION_RARE_BOOK",
                "event_timestamp": "2024-05-21T09:55:00Z",
                "payload": {
                    "asset_system_id": "GJ-001", "title": "永乐大典嘉靖副本残卷",
                    "operator_id": "1009", "operator_name": "大飞",
                    "scan_details": {"file_count": 1200, "resolution_dpi": 600, "format": "TIFF"},
                    "storage_info": {"server_name": "SRV-RARE-01", "path": "/data/rare_books/digitized/GJ-001/"}
                }
            }
        }

class CoreEntity(BaseModel):
    """
    【已重构 V2.0】
    纯粹的核心实体模型，仅代表一个领域无关的、唯一的身份标识。
    这是实现“纯粹共享内核 (Pure Shared Kernel)”的技术载体。
    """
    uuid: str = Field(description="系统内部的全局唯一标识符 (UUID)")

class IdentifiedEntity(BaseModel):
    """
    【新增 V2.0】
    已识别的实体信息模型。
    用于在适配器和分发器之间，传递比纯粹CoreEntity更丰富的上下文信息，
    但这些信息不会被持久化到共享内核中。
    """
    business_key: str = Field(description="来自源系统的业务主键")
    entity_type: str = Field(description="在源上下文中识别出的业务类型, 如 '古籍', '员工'")
    label: str = Field(description="一个用于人类可读的标签")
    uuid: str = Field(description="已解析出的全局唯一身份UUID")


class StandardizedDataObject(BaseModel):
    """
    【已重构 V3.2 - 注入时间维度】
    对应架构图 (b): 标准化内部数据对象。
    这是在防腐层（ACL）处理后，我们系统内部信息流转的核心载体。
    它封装了关于一个业务事件的所有被标准化的、可供下游消费的信息。
    """

    # --- 事件元数据 (Event Metadata) ---
    source_event_id: str = Field(
        ...,
        description="触发流程的原始业务事件的ID。这是实现数据血缘（Data Lineage）和端到端可追溯性的关键字段。"
    )

    event_timestamp: str = Field(
        ...,
        description="原始业务事件的时间戳。这是为知识图谱增加时间维度的关键字段。"
    )

    # --- 实体身份信息 (Entity Identity Information) ---
    identified_entities: List[IdentifiedEntity] = Field(
        ...,
        description="从原始事件中解析出的所有核心实体及其身份信息（业务ID、内部UUID、上下文类型等）。"
    )

    # --- 内容载荷 (Content Payload) ---
    source_data_markdown: str = Field(
        ...,
        description="原始事件载荷被格式化为对LLM友好的Markdown字符串。这是提供给LLM进行语义理解的主要“食材”。"
    )


# --- 2. 任务分发 (Phase 2: Dispatching) ---

class KnowledgeGenerationCommand(BaseModel):
    """
    【已重构 V2.0】
    对应架构图 (d): 知识生成命令
    现在它携带的是 IdentifiedEntity 列表，而不是 CoreEntity 列表。
    """
    command_id: str = Field(default_factory=lambda: f"CMD-{uuid.uuid4()}", description="命令的唯一ID")
    source_event_id: str = Field(..., description="来源业务事件的ID")
    event_timestamp: str = Field(..., description="来源业务事件的时间戳")
    target_domain: str = Field(..., description="知识生成的目标领域名称")
    core_entity_uuid: str = Field(..., description="本次任务聚焦的核心实体的UUID")
    identified_entities: List[IdentifiedEntity] = Field(..., description="事件中涉及的所有实体及其丰富信息")
    source_data_markdown: str = Field(..., description="格式化后的源数据")

# --- 3. 领域翻译 (Phase 3: Translation) ---

class VerifiedKnowledgeFragment(BaseModel):
    """
    对应架构图 (e): 经过验证的知识片段
    (此模型保持不变)
    """
    source_command_id: str = Field(..., description="来源命令的ID，用于端到端追踪")
    target_domain: str = Field(..., description="知识片段所属的目标领域")
    status: Literal["SUCCESS", "VALIDATION_FAILED"] = Field(..., description="处理结果状态")
    # 修改 payload 字段的类型定义
    payload: Union[Dict[str, Any], List[Dict[str, Any]]] = Field(None, description="符合JSON-LD格式的知识片段(或片段列表)")
    error_message: str = Field(None, description="错误信息，仅在失败时存在")