# Knowledge Proliferation Engine (KPE)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/docker-compose-green.svg)](https://www.docker.com/)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.xxxxxxx.svg)](https://doi.org/10.5281/zenodo.xxxxxxx) <!-- Replace with your actual Zenodo DOI later -->

**Official implementation and dataset for the paper:**
> **Knowledge Proliferation: A Domain-Driven Framework for Real-time, Multi-faceted Knowledge Graph Generation from Business Data**

---

## 📖 Introduction

In modern enterprise architectures, data persistence often suffers from **"Semantic Reduction"**—the stripping of multidimensional business contexts into flat storage records. To address this, we propose the **Knowledge Proliferation Engine (KPE)**.

KPE is a Domain-Driven Design (DDD) guided architectural framework that transforms passive data persistence into **active knowledge creation**. By positioning Bounded Contexts as **"Cognitive Firewalls"** for Large Language Models (LLMs), KPE automatically translates raw business events into **Natively Federated Knowledge Graphs**, ensuring strict schema compliance and precise entity alignment across heterogeneous domains.

## 🌟 Key Features

*   **Architecture as Cognitive Firewall:** Decomposes complex generation tasks into domain-specific sub-tasks to prevent LLM cognitive overload.
*   **V3.1 Identity Model:** Implements a **Pure Shared Kernel** (`:CoreEntity`) + **Domain Proxy** (`:Role`) pattern to handle multi-identity entities (e.g., an employee acting as both an "Operator" and a "Reader").
*   **Dynamic Prompt Engineering:** Retrieves schema rules and few-shot examples dynamically based on the target Bounded Context.
*   **Self-Healing Mechanism:** Includes a recursive validation and type-inference loop to fix malformed LLM outputs (e.g., "Ghost Nodes") in real-time.
*   **Event-Driven & Asynchronous:** Built on **Celery** and **Redis** for high throughput and decoupling.

## 📂 Project Structure

```text
KPE-Framework/
├── app/                        # Core Application Code
│   ├── main.py                 # FastAPI Entry Point
│   ├── models.py               # Pydantic Data Contracts
│   ├── celery_app.py           # Async Task Configuration
│   ├── tasks.py                # Celery Task Definitions
│   ├── services/               # Core Logic
│   │   ├── adapter.py          # ACL & Identity Resolution
│   │   ├── identifier.py       # LLM-based Context Mapping
│   │   ├── dispatcher.py       # Pattern-Match Task Dispatching
│   │   ├── translator.py       # Dynamic Prompting & Translation
│   │   └── writer.py           # Idempotent Graph Persistence
│   └── repositories/           # Data Access Layer
├── data/                       # Datasets & Domain Models
│   ├── domain_models.json      # Declarative Domain Definitions (Schema & Rules)
│   └── evaluation/             # Evaluation Data
│       ├── 1_input_events/     # Raw Event Logs (RB-50, BPI-2017)
│       ├── 2_gold_standard_kgs/# Expert Annotations
│       ├── 3_generated_results/# Output from the system
│       └── 4_evaluation_results/# LLM-as-a-Judge Scores
├── scripts/                    # Utilities & Experiments
│   ├── run_evaluation.py       # Main Experiment Runner (Pipeline + Judging)
│   ├── ours_rag.py             # Advanced Graph-RAG Implementation
│   └── ... (Baseline scripts)
├── docker-compose.yml          # Infrastructure (Neo4j, Redis)
└── requirements.txt            # Python Dependencies

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
🔬 Reproducing Experiments
This repository contains all scripts to reproduce the results reported in the paper.
Experiment 1: Knowledge Generation Quality (RQ1)
To run the full pipeline (Generation + Evaluation) on the RB-50 dataset:
Start the Worker: Open a terminal and run the Celery worker.

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

Experiment 3: Generalization on BPI-2017 (RQ4)
To test on real-world financial logs:
1.Generate the BPI dataset subset:
  python scripts/extract_real_bpi.py
2.Update scripts/run_evaluation.py to point to events_bpi.jsonl.
3.Run the pipeline again to generate the financial knowledge graph.

📊 Datasets
We provide two datasets in data/evaluation/:
RB-50 (Rare Book 50): A curated dataset of 50 complex, cross-domain business events (Digitization, Repair, HR, IP, Finance). Includes manually annotated Gold Standard KGs.
BPI-2017 (Subset): Real-world loan application logs filtered for high information density, used for generalization studies.
🛡️ License
This project is licensed under the MIT License.

