# -*- coding: utf-8 -*-
"""
Celery 应用模块

本文件定义并配置了Celery的全局应用实例。
它指定了使用Redis作为消息代理(Broker)和结果后端(Backend)，
并设置了任务的自动发现机制。
"""
from celery import Celery
from app.config import settings


# 创建Celery应用实例
# 第一个参数 'knowledge_proliferation_engine' 是当前项目的名称，Celery建议使用。
# broker: 指定了任务队列的中间人，所有任务都会先发送到这里。
# backend: 指定了任务执行结果的存储位置。
celery_app = Celery(
    "knowledge_proliferation_engine",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks"]  # 关键配置：指定Celery启动时需要自动寻找任务的模块列表
)

# 可选的Celery配置
celery_app.conf.update(
    task_serializer="json",         # 任务序列化方式
    result_serializer="json",       # 结果序列化方式
    accept_content=["json"],        # 只接受json格式的内容
    timezone="Asia/Shanghai",       # 设置时区
    enable_utc=True,
)