# -*- coding: utf-8 -*-
"""
【V3.6 终极版 - 实验控制器】任务分发器模块

本模块的核心职责是扮演DDD中的“上下文映射器（Context Mapper）”角色，
并将一个宏观的业务事件，分解、封装并异步派发为多个处理任务。

此版本通过读取环境变量 APP_MODE，可以动态地在 'Ours', 'Naive', 'Monolithic'
三种不同的派发策略之间切换，从而控制整个系统的行为模式。
"""
import os
from typing import List
from app.models import StandardizedDataObject, KnowledgeGenerationCommand, IdentifiedEntity
from app.tasks import process_knowledge_generation_command
from app.repositories.static_repository import static_repo
from app.config import settings


class TaskDispatcher:
    """
    【V3.6】任务分发器服务。
    它现在是一个模式驱动的实验控制器。
    """

    def __init__(self):
        """
        初始化分发器，并从环境变量中读取当前的运行模式。
        """
        # 直接从已经经过验证和类型转换的settings对象中读取模式
        self.mode = settings.APP_MODE
        print(f"--- [分发器] 初始化完成，当前运行模式: '{self.mode}' ---")

    def dispatch_tasks(self, data: StandardizedDataObject, domains: List[str]):
        """
        根据初始化时确定的模式，执行相应的任务派发逻辑。
        """

        # ==========================================================
        # 模式一 & 二：Naive 和 Monolithic 基线
        # 这两种模式的派发逻辑相同：都只派发一个通用任务。
        # ==========================================================
        if self.mode in ["Naive", "Monolithic"]:
            print(f"--- [分发器] 正在以 '{self.mode}' 模式派发单个通用任务... ---")

            # 健壮性检查：确保至少有一个实体可以作为核心
            if not data.identified_entities:
                print("    - [警告] 事件中未解析出任何核心实体，无法派发任务。流程终止。")
                return

            # 创建一个“通用”命令，其target_domain就是模式名称
            command = KnowledgeGenerationCommand(
                source_event_id=data.source_event_id,
                event_timestamp=data.event_timestamp,
                target_domain=self.mode,  # <-- 关键！将模式名作为领域传递
                core_entity_uuid=data.identified_entities[0].uuid,  # 随便选第一个作为名义上的核心
                identified_entities=data.identified_entities,
                source_data_markdown=data.source_data_markdown
            )

            # 异步调用Celery任务
            process_knowledge_generation_command.delay(command_dict=command.model_dump())
            print(f"    - 已成功为事件 {data.source_event_id} 派发 '{self.mode}' 模式任务，命令ID: {command.command_id}")

        # ==========================================================
        # 模式三：Ours (我们的最终方案)
        # 采用模型驱动的、分领域派发逻辑。
        # ==========================================================
        elif self.mode == "Ours":
            print(f"--- [分发器] 正在以 'Ours' 模式为识别出的 {len(domains)} 个领域派发任务... ---")

            # 获取当前事件中存在的所有实体类型，用于匹配焦点规则
            present_entity_types = {e.entity_type for e in data.identified_entities}
            print(f"    - 上下文分析: 当前事件包含的实体类型: {present_entity_types}")

            for domain in domains:
                # 1. 从领域模型中获取该领域的“焦点规则”列表
                domain_model = static_repo.get_schema(domain)
                focus_rules = domain_model.get("focus_rules", [])

                # 2. 查找所有被当前事件激活的规则
                applicable_rules = []
                for rule in focus_rules:
                    if rule.get("if_contains") in present_entity_types:
                        applicable_rules.append(rule)

                if not applicable_rules:
                    print(f"    - [警告] 在领域 '{domain}' 中，当前事件未匹配任何焦点规则。跳过该领域的任务派发。")
                    continue

                # 3. 根据优先级，选出最佳规则以确定“主角”
                best_rule = sorted(applicable_rules, key=lambda r: r.get("priority", 99))[0]
                focus_entity_type = best_rule.get("focus_on")

                if not focus_entity_type:
                    print(f"    - [警告] 领域 '{domain}' 的最佳匹配规则中缺少 'focus_on' 定义。跳过。")
                    continue

                # 4. 在输入数据中查找该“主角”实体
                core_entity_for_task = next((e for e in data.identified_entities if e.entity_type == focus_entity_type),
                                            None)

                if not core_entity_for_task:
                    print(
                        f"    - [错误] 内部逻辑错误：在事件中未找到已匹配规则所需的核心实体 '{focus_entity_type}'。跳过。")
                    continue

                # 5. 创建并派发针对该领域的命令
                command = KnowledgeGenerationCommand(
                    source_event_id=data.source_event_id,
                    event_timestamp=data.event_timestamp,
                    target_domain=domain,
                    core_entity_uuid=core_entity_for_task.uuid,
                    identified_entities=data.identified_entities,
                    source_data_markdown=data.source_data_markdown
                )

                process_knowledge_generation_command.delay(command_dict=command.model_dump())

                print(
                    f"    - 已成功为领域 '{domain}' (决策核心: {focus_entity_type}) 派发任务，命令ID: {command.command_id}")

        else:
            print(
                f"--- [分发器错误] 未知的APP_MODE: '{self.mode}'。有效的模式为 'Ours', 'Naive', 'Monolithic'。无法派发任务。---")

        print(f"--- [分发器] 所有任务派发完成 ---")