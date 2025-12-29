# -*- coding: utf-8 -*-
"""
【Ours-RAG 终极评估脚本】

本脚本实现了针对我们高质量、多领域知识图谱的“混合增强检索”策略。
它结合了精确的图遍历（利用DDD的结构优势）和语义向量搜索，以实现最高的问答准确率。

核心策略：
1. 实体锚定 (Entity Anchoring): 尝试从问题中识别业务ID或名称。
2. 结构化扩展 (Structured Expansion): 一旦锚定核心实体，利用 CoreEntity->Role->Event 的结构进行精确的子图提取。
3. 语义补全 (Semantic Completion): 使用向量索引检索相关的非实体描述性信息。
"""
import json
import re
import sys
import time
from pathlib import Path
from typing import List, Dict, Set

from neo4j import GraphDatabase, exceptions
from sentence_transformers import SentenceTransformer
from openai import OpenAI
from tqdm import tqdm

# 尝试导入配置
try:
    from app.config import settings
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.config import settings

# --- 配置 ---
BASE_DIR = Path(__file__).resolve().parent.parent
# 使用增强版的15个问题集
QUESTIONS_PATH = BASE_DIR / "data" / "evaluation" / "cross_domain_questions.json"
# 输出路径
OUTPUT_ANSWERS_PATH = BASE_DIR / "data" / "evaluation" / "baseline_results" / "ours_rag_answers.json"

# 连接到主数据库 (Ours 方案生成的图谱)
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "password123"

EMBEDDING_MODEL_NAME = 'BAAI/bge-m3'
QA_MODEL_NAME = 'google/gemini-2.5-flash'  # 或 openai/gpt-4o

# --- LLM 客户端初始化 ---
try:
    qa_client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=settings.OPENROUTER_API_KEY,
    )
    qa_headers = {
        "HTTP-Referer": settings.HTTP_REFERER,
        "X-Title": f"{settings.X_TITLE} - OursRAG",
    }
    print("--- [Ours-RAG] LLM客户端初始化成功 ---")
except Exception as e:
    raise RuntimeError(f"LLM客户端初始化失败: {e}")


def create_graph_aware_vector_index(driver: GraphDatabase.driver, model: SentenceTransformer):
    """
    【步骤1】构建图感知的向量索引。
    不仅索引节点属性，还将节点的“领域标签”和“链接的核心实体信息”编入向量。
    这使得向量检索也能感知到DDD的结构。
    """
    print("\n--- [索引构建] 开始构建图感知向量索引 ---")
    with driver.session() as session:
        # 1. 清理旧索引
        try:
            session.run("CALL db.index.vector.drop('oursKgEmbeddings')")
            print("  - 旧索引已删除。")
        except Exception:
            pass

        # 2. 构建富文本表示 (Rich Text Representation)
        # 这里的Cypher查询非常关键：
        # 它会查找所有领域节点 (以Node结尾的标签)，并尝试找到它们关联的 CoreEntity
        # 从而将 "节点信息" + "它属于谁(CoreEntity)" 结合在一起。
        print("  - 正在生成节点的富文本描述...")
        query = """
        MATCH (n)
        WHERE any(l IN labels(n) WHERE l ENDS WITH 'Node') // 只索引领域知识节点

        // 尝试寻找该节点关联的核心实体 (通过直接关系或Role间接关系)
        OPTIONAL MATCH (n)-[]->(role)-[:PLAYED_BY]->(core:CoreEntity)
        OPTIONAL MATCH (core)<-[:IDENTIFIES]-(id:Identifier)
        OPTIONAL MATCH (core)-[:HAS_ALIAS]->(name:Name)

        WITH n, 
             collect(DISTINCT id.value) as ids, 
             collect(DISTINCT name.value) as names,
             [k in keys(n) | k + ": " + toString(n[k])] as props,
             labels(n) as lbls

        // 构建文本：类型 + 属性 + 关联的核心实体信息
        // 【关键修正】：这里必须使用 Cypher 的列表推导语法 [l IN lbls WHERE ...]
        WITH n, 
             "领域类型: " + apoc.text.join([l IN lbls WHERE l ENDS WITH 'Node'], ',') + ". " +
             "内容详情: " + apoc.text.join(props, ", ") + ". " +
             (CASE WHEN size(ids) > 0 THEN "关联实体ID: " + apoc.text.join(ids, ", ") ELSE "" END) + ". " +
             (CASE WHEN size(names) > 0 THEN "关联实体名称: " + apoc.text.join(names, ", ") ELSE "" END) 
             as text

        SET n.text_repr = text, n:VectorIndexed
        RETURN elementId(n) as id, text
        """
        results = session.run(query).data()

        if not results:
            print("  - [警告] 未找到任何领域知识节点！请检查数据库是否已填充。")
            return

        print(f"  - 准备向量化 {len(results)} 个知识节点...")

        # 3. 批量向量化
        texts = [r['text'] for r in results]
        embeddings = model.encode(texts, show_progress_bar=True, normalize_embeddings=True)

        # 4. 写入向量
        print("  - 正在将向量写入Neo4j...")
        unwind_data = [{"id": results[i]['id'], "vec": embeddings[i].tolist()} for i in range(len(results))]
        session.run("""
        UNWIND $data AS row
        MATCH (n) WHERE elementId(n) = row.id
        SET n.embedding = row.vec
        """, data=unwind_data)

        # 5. 创建索引
        print("  - 创建向量索引 'oursKgEmbeddings'...")
        session.run("""
        CREATE VECTOR INDEX oursKgEmbeddings IF NOT EXISTS
        FOR (n:VectorIndexed) ON (n.embedding)
        OPTIONS { indexConfig: { `vector.dimensions`: 1024, `vector.similarity_function`: 'cosine' }}
        """)

        # 等待索引上线
        print("  - 等待索引上线...")
        while True:
            status = session.run("SHOW INDEXES WHERE name = 'oursKgEmbeddings'").single()
            if status and status['state'] == 'ONLINE': break
            time.sleep(1)
        print("  - [索引构建] 完成。")


def search_graph_hybrid(query: str, model: SentenceTransformer, driver: GraphDatabase.driver) -> str:
    """
    【核心检索逻辑 - 调试增强版】混合检索策略
    增加详细日志，打印 Cypher 查询和原始返回数据。
    """
    context_set = set()

    # 提取可能的实体/ID
    potential_entities = re.findall(r"['\"‘“’]([^'\"‘“’]+)['\"‘“’]", query)
    potential_ids = re.findall(r"\b[A-Za-z0-9-]{3,}\b", query)
    # 过滤掉一些常见的停用词或短词，避免过度匹配
    filtered_ids = [pid for pid in potential_ids if
                    len(pid) > 4 and not pid.lower() in ['what', 'which', 'where', 'when']]
    all_candidates = list(set(potential_entities + filtered_ids))

    print(f"\n--- [调试] 1. 实体锚定阶段 ---")
    print(f"用户问题: {query}")
    print(f"提取到的潜在锚点 (ID/名称): {all_candidates}")

    with driver.session() as session:
        # --- 策略 A: 基于实体链接的精确图遍历 ---
        if all_candidates:
            traversal_query = """
            UNWIND $candidates AS token

            // 1. 尝试匹配 Identifier 或 Name
            MATCH (start)
            WHERE (start:Identifier AND start.value CONTAINS token) 
               OR (start:Name AND start.value CONTAINS token)

            // 2. 导航到 CoreEntity
            MATCH (start)-[:IDENTIFIES|HAS_ALIAS]-(core:CoreEntity)

            // 3. 扩展到 Role (领域代理)
            MATCH (role)-[:PLAYED_BY]->(core)

            // 4. 扩展到具体的领域知识 (事件、对象等)
            MATCH (knowledge)-[]-(role)
            WHERE any(l IN labels(knowledge) WHERE l ENDS WITH 'Node') // 确保是领域知识节点

            // 5. 返回丰富的上下文
            RETURN DISTINCT 
                labels(knowledge) as types,
                knowledge.text_repr as text,
                knowledge.sourceEventId as source_event,
                role.entityType as role_type,
                core.uuid as core_uuid
            LIMIT 20
            """

            print(f"\n--- [调试] 执行精确图遍历 Cypher ---")
            print(traversal_query)
            print(f"Parameters: {all_candidates}")

            results = session.run(traversal_query, candidates=all_candidates).data()

            print(f"\n--- [调试] 精确遍历返回原始数据 ({len(results)}条) ---")
            # 打印完整的原始数据，方便检查
            print(json.dumps(results, indent=2, ensure_ascii=False))

            if results:
                for r in results:
                    ctx = f"[精确匹配] 核心实体:{r['core_uuid']} | 角色:{r['role_type']} | 类型:{r['types']} | 内容:{r['text']}"
                    context_set.add(ctx)
            else:
                print("  - (未匹配到任何精确路径)")

        # --- 策略 B: 语义向量检索 ---
        print(f"\n--- [调试] 2. 向量检索阶段 ---")
        query_embedding = model.encode([query], normalize_embeddings=True)[0].tolist()

        vector_query = """
        CALL db.index.vector.queryNodes('oursKgEmbeddings', 10, $embedding) 
        YIELD node, score
        WHERE score > 0.6 // 阈值
        RETURN node.text_repr as text, score, labels(node) as labels, elementId(node) as id
        """

        print(f"--- [调试] 执行向量检索 Cypher ---")
        print(vector_query)
        # Embedding太长，不打印

        vector_results = session.run(vector_query, embedding=query_embedding).data()

        print(f"\n--- [调试] 向量检索返回原始数据 ({len(vector_results)}条) ---")
        print(json.dumps(vector_results, indent=2, ensure_ascii=False))

        for r in vector_results:
            ctx = f"[向量补充] (相似度 {r['score']:.2f}) {r['text']}"
            context_set.add(ctx)

    # --- 结果整合 ---
    print(f"\n--- [调试] 最终整合的上下文 ({len(context_set)}条) ---")
    final_context = "\n\n".join(list(context_set))
    # 打印前500个字符预览
    print(final_context[:500] + "..." if len(final_context) > 500 else final_context)

    if not context_set:
        return "在知识图谱中未找到相关信息。"

    return final_context

def generate_answer(question: str, context: str) -> str:
    """调用LLM生成最终答案"""
    prompt = f"""
你是一个基于知识图谱的智能问答助手。请根据以下检索到的“上下文信息”，回答用户的“问题”。

# 上下文信息 (来源于多领域知识图谱):
{context}

# 用户问题:
{question}

# 回答要求:
1. 综合不同领域的知识来回答。
2. 如果有具体的数据（如时间、金额、人员），请列出。
3. 如果信息中有关于不同角色的描述（如既是员工又是读者），请明确指出。
4. 如果上下文不足以回答问题，请诚实回答“无法回答”。
"""
    try:
        resp = qa_client.chat.completions.create(
            extra_headers=qa_headers,
            model=QA_MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        return resp.choices[0].message.content
    except Exception as e:
        return f"LLM调用错误: {e}"


def main():
    print("--- Ours-RAG (最终方案) 评估启动 ---")
    OUTPUT_ANSWERS_PATH.parent.mkdir(exist_ok=True, parents=True)

    # 1. 加载增强版的问题集
    with open(QUESTIONS_PATH, 'r', encoding='utf-8') as f:
        questions_data = json.load(f)
    print(f"加载了 {len(questions_data)} 个测试问题。")

    # 2. 初始化模型和数据库
    print("正在初始化嵌入模型...")
    model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    # 3. 预处理：构建图感知索引
    create_graph_aware_vector_index(driver, model)

    # 4. 问答循环
    results = []
    print("\n--- 开始 RAG 问答 ---")
    for item in tqdm(questions_data):
        qid = item['qid']
        question_text = item['question']

        print(f"\n=== 处理问题 {qid}: {question_text} ===")

        # 执行混合检索
        context = search_graph_hybrid(question_text, model, driver)
        print(f"--- 检索到的上下文摘要 ---\n{context[:300]}...\n(共 {len(context)} 字符)")

        # 生成答案
        answer = generate_answer(question_text, context)
        print(f"--- 生成的答案 ---\n{answer}\n")

        results.append({
            "qid": qid,
            "question": question_text,
            "ideal_answer": item['ideal_answer'],
            "generated_answer": answer,
            "retrieved_context": context
        })

    # 5. 保存结果
    with open(OUTPUT_ANSWERS_PATH, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    driver.close()
    print(f"\n--- Ours-RAG 评估完成！结果已保存至: {OUTPUT_ANSWERS_PATH} ---")


if __name__ == "__main__":
    main()