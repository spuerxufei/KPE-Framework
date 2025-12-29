# -*- coding: utf-8 -*-
"""
配置模块 (Configuration Module)

本模块使用 Pydantic-settings 来集中管理应用的所有配置。
它能自动从环境变量或.env文件中读取配置项，实现了配置与代码的分离。
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field # <-- 确保导入 Field
from typing import Literal # <-- 导入 Literal


class Settings(BaseSettings):
    # Celery & Redis 配置
    REDIS_URL: str

    # LLM API 配置
    OPENROUTER_API_KEY: str
    HTTP_REFERER: str = "http://localhost" # 提供一个默认值
    X_TITLE: str = "Knowledge Proliferation Engine"

    # LLM 模型名称
    IDENTIFIER_MODEL_NAME: str
    TRANSLATOR_MODEL_NAME: str
    # --- ** 关键修复：新增数据库配置定义 ** ---
    NEO4J_URI: str
    NEO4J_USER: str
    NEO4J_PASSWORD: str
    # --- 修复结束 ---
    # --- 新增：应用运行模式配置 ---
    # 我们使用Literal来约束APP_MODE只能是这三个值之一，防止拼写错误
    # Field(default="Ours")设置了默认值。
    APP_MODE: Literal["Ours", "Naive", "Monolithic"] = Field(
        default="Ours",
        description="应用运行模式: Ours (最终方案), Naive (基线1), Monolithic (基线2)"
    )

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding='utf-8', extra='ignore')

settings = Settings()