# -*- coding: utf-8 -*-
"""
【V3.5 终极版】异步任务定义模块

本文件定义了所有Celery异步任务。
此版本优化了服务实例化，增加了健壮的数据库写入重试逻辑，
并确保将完整的实体上下文信息传递给KnowledgeWriter。
"""
from app.celery_app import celery_app
from app.models import KnowledgeGenerationCommand, VerifiedKnowledgeFragment
from app.services.translator import DomainTranslator
from app.services.writer import KnowledgeWriter

# --- 服务实例化优化 ---
# 这是一个重要的优化：我们将服务的实例放在任务函数外部。
# 这意味着对于同一个Celery Worker进程，这些实例只会被创建一次，
# 而不是在每次任务执行时都重新创建。
# 这对于像KnowledgeWriter这样包含昂贵数据库连接池的对象尤其重要。
# 注意：这种模式要求服务类本身是线程安全的，我们的实现（按需创建连接/客户端）符合这个要求。
translator = DomainTranslator()
writer = KnowledgeWriter()


@celery_app.task(name="app.tasks.process_knowledge_generation_command", bind=True, max_retries=3,
                 default_retry_delay=60)
def process_knowledge_generation_command(self, command_dict: dict) -> dict:
    """
    一个Celery任务，用于处理单个知识生成命令。

    Args:
        self (celery.Task): Celery的任务实例，通过 `bind=True` 注入。用于访问任务上下文，如重试。
        command_dict (dict): 一个字典，其结构与KnowledgeGenerationCommand模型匹配。

    Returns:
        dict: 一个字典，其结构与VerifiedKnowledgeFragment模型匹配，代表任务的执行结果。
    """
    command = None
    try:
        # 1. 反序列化与日志记录
        command = KnowledgeGenerationCommand(**command_dict)
        print(f"--- [Celery任务开始] 接收到命令: {command.command_id}, 模式/领域: {command.target_domain} ---")

        # 2. 调用全局实例化的translator
        result = translator.translate(command)

        # 3. 对翻译结果进行分支处理
        if result.status != "SUCCESS":
            # 如果翻译阶段本身就失败了（例如，LLM返回了无效JSON或验证失败）
            # 我们将这个失败视为最终失败，不进行重试，直接返回错误信息。
            print(f"--- [Celery任务警告] 翻译阶段失败，命令: {command.command_id}, 原因: {result.error_message} ---")
            return result.model_dump()

        # 4. 增加了一个专门针对数据库写入的错误处理和重试逻辑
        try:
            # 【V3.5 终极核心修复】
            # ----------------------------------------------------
            # 调用全局实例化的writer，并传递`identified_entities`
            # 这是确保"反向链接"逻辑能够工作的关键一步。

            # Writer现在需要一个列表来处理Monolithic模式的输出
            payload_list = result.payload if isinstance(result.payload, list) else [result.payload]

            for fragment in payload_list:
                writer.write_fragment(
                    fragment=fragment,
                    domain=result.target_domain,
                    source_event_id=command.source_event_id,
                    identified_entities=command.identified_entities  # <-- 关键的上下文信息传递
                )
            # ----------------------------------------------------

        except Exception as db_error:
            # 如果只是数据库写入失败（例如，数据库临时不可用）
            print(f"--- [Celery任务错误] 写入数据库失败，命令: {command.command_id}, 错误: {db_error} ---")
            # Celery的自动重试机制将在这里被触发
            # self.retry()会抛出一个Retry异常，Celery会捕获它并稍后重试任务
            # 我们将异常传递给retry，以便Celery记录它
            raise self.retry(exc=db_error)

        # 5. 成功返回
        result_dict = result.model_dump()
        print(f"--- [Celery任务完成] 命令: {command.command_id}, 状态: {result.status} ---")
        return result_dict

    except Exception as e:
        # 这个except块现在主要捕获“不可重试”的严重错误，例如Pydantic模型验证失败或重试次数耗尽
        import traceback
        traceback.print_exc()  # 打印完整的错误堆栈以方便调试
        print(f"--- [Celery任务致命异常] 命令处理失败: {e}, 已达最大重试次数或发生不可恢复错误 ---")

        # 统一的错误处理
        error_result = VerifiedKnowledgeFragment(
            source_command_id=command.command_id if command else command_dict.get("command_id", "unknown"),
            target_domain=command.target_domain if command else command_dict.get("target_domain", "unknown"),
            status="VALIDATION_FAILED",
            error_message=f"任务执行时发生致命异常: {type(e).__name__}: {e}"
        )
        return error_result.model_dump()