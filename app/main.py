# -*- coding: utf-8 -*-
"""
Web服务入口模块 (API Endpoint Module)

本文件使用FastAPI框架创建了知识增殖服务的Web API入口点。
它负责接收外部业务事件，并协调调用内部的业务逻辑服务来处理这些事件。
"""
from fastapi import FastAPI, HTTPException, status
from contextlib import asynccontextmanager
from app.models import RawBusinessEvent
from app.services.adapter import InputAdapter, EntityIdentityService
from app.services.identifier import DomainIdentifier
from app.services.dispatcher import TaskDispatcher
from app.config import settings # 导入settings

# --- FastAPI 应用实例化 ---
# 创建一个FastAPI应用实例，这是我们所有API路由的根。
# title 和 version 会显示在自动生成的API文档中。
app = FastAPI(
    title="知识增殖引擎 (Knowledge Proliferation Engine)",
    description="一个由DDD思想指导、LLM驱动的自动化多领域知识生成框架",
    version="0.1.0-mvp"
)

# --- 实例化我们的核心服务 ---
# 在真实的生产应用中，这些服务的实例化可能会通过更复杂的依赖注入系统来管理。
# 在MVP中，我们直接在这里实例化它们。
# 不再创建全局实例，而是在需要时创建或通过依赖注入
# input_adapter = InputAdapter()
# domain_identifier = DomainIdentifier()
# task_dispatcher = TaskDispatcher()


# --- API 路由定义 ---
@app.post("/v1/events/process",
          status_code=status.HTTP_202_ACCEPTED,
          summary="处理并增殖业务事件知识",
          tags=["Knowledge Proliferation"])
async def process_event(event: RawBusinessEvent):
    """
    接收一个原始业务事件，触发一个异步的知识增殖流程。

    本端点遵循异步接收模式：
    1.  它会快速地对输入进行初步处理和任务分发。
    2.  然后立刻返回一个 `2022 Accepted` 状态码，表示请求已被接受，正在后台处理。
    3.  真正的知识生成将在后台由Celery Worker异步完成。

    Args:
        event (RawBusinessEvent): 来自外部业务系统的原始事件数据。

    Returns:
        dict: 一个包含任务追踪信息的响应。
    """
    print(f"\n--- [API入口] 接收到新事件: {event.event_id} ---")
    try:
        # **关键修改：在这里实例化服务**
        # 在真实应用中，我们可以通过环境变量来控制这个模式
        # os.getenv("EVALUATION_MODE", "false").lower() == "true"
        is_eval_mode = False  # 默认是运行时模式
        # is_eval_mode = True  # 评估模式

        input_adapter = InputAdapter(evaluation_mode=False)
        domain_identifier = DomainIdentifier()
        task_dispatcher = TaskDispatcher()
        # 步骤 1: 调用防腐层进行标准化
        standardized_data = input_adapter.transform_and_standardize(event)

        # 步骤 2: 调用上下文映射器识别领域
        # 在真实应用中，为了进一步解耦，Identifier的调用也可以是异步的，
        # 但在MVP中，我们将其作为同步步骤以简化流程。在 'Naive' 和 'Monolithic' 模式下，这一步的输出 domains 将被 Dispatcher 忽略
        domains = domain_identifier.identify_domains(standardized_data)

        if not domains:
            print("--- [API警告] 未识别出任何相关领域，流程终止。---")
            return {"status": "skipped", "message": "No relevant domains identified for the event."}

        # 步骤 3: 调用分发器派发异步任务
        # Dispatcher内部会根据环境变量APP_MODE，决定如何处理domains列表
        task_dispatcher.dispatch_tasks(standardized_data, domains)

        print(f"--- [API完成] 事件 {event.event_id} 的任务已成功派发。---")

        return {"status": "accepted",
                "message": "Knowledge generation tasks have been dispatched for background processing."}

    except Exception as e:
        # 捕获任何在同步处理流程中发生的未知错误
        print(f"--- [API错误] 处理事件时发生严重错误: {e} ---")
        # 返回一个HTTP 500错误，并带上错误信息
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred while processing the event: {e}"
        )