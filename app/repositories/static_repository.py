# -*- coding: utf-8 -*-
"""
伪仓库模块 (Mock Repository Module)

本模块提供了一个从静态JSON文件中读取领域模型和范例的实现。
它确保了每次访问都能获取到最新的文件内容，以适应开发过程中的频繁修改。
"""
import json
from pathlib import Path
from typing import Dict, Any, List

# 路径定义保持不变
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DOMAIN_MODELS_PATH = BASE_DIR / "data" / "domain_models.json"
KG_EXAMPLES_PATH = BASE_DIR / "data" / "kg_examples.json"

class StaticKnowledgeRepository:
    """
    一个从本地JSON文件加载知识的静态仓库。
    重构后的版本，放弃了类级别的缓存，以确保在并发和开发环境中
    总能加载到最新的数据。
    """
    def _load_json_file(self, file_path: Path) -> Dict:
        """一个通用的、用于加载JSON文件的私有辅助方法。"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"--- [仓库错误] 加载文件 {file_path}失败: {e} ---")
            return {}

    def get_schema(self, domain_name: str) -> Dict[str, Any]:
        """
        根据领域名称，加载并检索领域模型/Schema。
        """
        domain_models = self._load_json_file(DOMAIN_MODELS_PATH)
        return domain_models.get(domain_name, {})

    def get_examples(self, domain_name: str) -> List[Dict[str, Any]]:
        """
        根据领域名称，加载并检索知识图谱范例。
        """
        kg_examples = self._load_json_file(KG_EXAMPLES_PATH)
        return kg_examples.get(domain_name, [])

    def get_all_domain_definitions(self) -> Dict[str, str]:
        """加载并返回所有领域及其描述的字典。"""
        domain_models = self._load_json_file(DOMAIN_MODELS_PATH)
        return {name: data.get('description', '') for name, data in domain_models.items()}

    def get_all_domain_models(self) -> Dict[str, Any]:
        """
        【新增修复】获取所有领域的完整模型定义。
        用于 Monolithic 基线，需要一次性加载所有领域的规则来构建大Prompt。
        """
        return self._load_json_file(DOMAIN_MODELS_PATH)

# 创建一个单例实例，方便在其他地方直接导入使用
static_repo = StaticKnowledgeRepository()