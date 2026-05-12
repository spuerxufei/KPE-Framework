# Knowledge Proliferation Engine (KPE)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/docker-compose-green.svg)](https://www.docker.com/)

**Official implementation and dataset for the paper:**

> **Knowledge Proliferation: A Domain-Driven Framework for Real-time, Multi-faceted Knowledge Graph Generation from Business Data**

---

## 📖 Introduction

The Knowledge Proliferation Engine (KPE) is a **Domain-Driven Design (DDD)** guided architectural framework that transforms passive business data persistence into **active, real-time knowledge creation**.

By treating DDD Bounded Contexts as **"Cognitive Firewalls"** for Large Language Models, KPE automatically translates raw business events (JSON logs) into a **Natively Federated Knowledge Graph** with high factual fidelity, strict schema conformance, and precise cross-domain entity alignment.

## 🌟 Key Features

- **Cognitive Firewall Architecture**: Bounded Contexts isolate LLM reasoning, dramatically reducing cognitive overload and hallucinations.
- **Federated Identity-Role Model**: Pure Shared Kernel (`:CoreEntity`) + Domain Proxies (`:Role`) for accurate multi-faceted identity resolution.
- **Dynamic Prompting + Recursive Repair**: Context-aware schema injection and self-healing mechanism for "Ghost Nodes".
- **Deterministic Robustness**: 100% programmatic JSON-LD compliance and relational link integrity.
- **Event-Driven & Scalable**: Built on Celery + Redis + Neo4j, supports large-scale enterprise logs.
- **Zero-Shot Generalization**: Successfully validated on both curated (RB-50) and real-world (BPI-2017) datasets.

## 📂 Project Structure

```text
KPE-Framework/
├── app/                          # Core Application
│   ├── main.py
│   ├── models.py
│   ├── celery_app.py
│   ├── tasks.py
│   ├── services/
│   │   ├── adapter.py            # Anticorruption Layer + Identity Resolution
│   │   ├── identifier.py         # Context Mapping
│   │   ├── dispatcher.py         # Task Dispatching (supports ablation modes)
│   │   ├── translator.py         # Dynamic Prompting + Recursive Repair
│   │   └── ...
├── data/
│   ├── domain_models.json
│   └── evaluation/
│       ├── 1_input_events/       # Raw events (RB-50 + BPI-2017)
│       ├── 2_gold_standard_kgs/
│       ├── 3_generated_results/  # generated_kgs_bpi_sample.json, etc.
│       ├── 4_evaluation_results/ # All evaluation JSONs
│       └── baseline_results/
├── scripts/                      # Experiment & Analysis Scripts
│   ├── run_evaluation.py
│   ├── analyze_bpi_results.py
│   ├── evaluate_bpi_generalization.py
│   └── ...
├── docker-compose.yml
├── .env.example
├── requirements.txt
└── README.md

🚀 Quick Start
1. Prerequisites
Python 3.10+
Docker & Docker Compose
An API Key for an LLM provider (e.g., OpenRouter, OpenAI).
2. Installation
# Clone the repository
git clone https://github.com/[YOUR_USERNAME]/Knowledge-Proliferation-Engine.git
cd Knowledge-Proliferation-Engine

# Create and activate virtual environment
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

3. Configuration
Copy the example configuration file and edit it with your credentials:
cp .env.example .env

Edit .env:
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password123

REDIS_URL=redis://localhost:6379/0

# Required for LLM inference
OPENROUTER_API_KEY=sk-or-v1-your-key-here
# Default mode
APP_MODE=Ours

4. Launch Services
Start Neo4j (Graph DB) and Redis (Message Broker):
docker-compose up -d

Neo4j Browser: http://localhost:7474 (User: neo4j, Pass: password123)
## 🔬 Reproducing Experiments

This repository contains all scripts and data necessary to fully reproduce the experimental results reported in the paper.

### Experiment 1: Knowledge Generation Quality (RB-50, RQ1 & RQ3)
```bash
# Run the full pipeline (generation + evaluation)
python -m scripts.run_evaluation --run-mvp --evaluate

1.Start the Worker: Open a terminal and run the Celery worker.
# Windows
  celery -A app.celery_app worker --loglevel=INFO -P solo
# Linux/Mac
celery -A app.celery_app worker --loglevel=INFO
2.Start the API: Open another terminal.
  uvicorn app.main:app --host 0.0.0.0 --port 8000
3.Run Evaluation Script: Open a third terminal.
# This command clears the DB, sends 50 events, waits for processing, 
# extracts results, and runs the LLM-as-a-Judge evaluation.
  python -m scripts.run_evaluation --run-mvp
  Output: Results will be saved to data/evaluation/4_evaluation_results/evaluation_results.json.

Experiment 2: RAG Effectiveness (RQ2)
To benchmark the downstream reasoning capability:
1.Ensure the graph has been generated (Step 1 above).
2.Run the Ours-RAG script (Hybrid Retrieval + Graph Traversal):
  python -m scripts.ours_rag
3.(Optional) Run Baselines:
To run baselines like Naive-KG or Monolithic-KG, change APP_MODE in .env and repeat the generation process, then run their respective scripts in scripts/.

Experiment 3: BPI-2017 Zero-Shot Generalization (RQ4)
To test on real-world financial logs:
1.Generate the BPI dataset subset:
  python scripts/extract_real_bpi.py
2.Update scripts/run_evaluation.py to point to events_bpi.jsonl.
3.Run the pipeline again to generate the financial knowledge graph.
# Run BPI-2017 generalization evaluation
python -m scripts.evaluate_bpi_generalization
# Or analyze the results
python -m scripts.analyze_bpi_results

Experiment 4:Ablation Studies (NoDynamicPrompt / NoRepair)
1、Edit .env and set APP_MODE to one of the following:
Ours_No_DynamicPrompt
Ours_No_Repair
2、Run the evaluation pipeline:
python -m scripts.run_evaluation --run-mvp --evaluate

📊 Datasets
We provide two datasets in data/evaluation/:
RB-50 (Rare Book 50): A curated dataset of 50 complex, cross-domain business events (Digitization, Repair, HR, IP, Finance). Includes manually annotated Gold Standard KGs.
BPI-2017 (Subset): Real-world loan application logs filtered for high information density, used for generalization studies.
🛡️ License
This project is licensed under the MIT License.

