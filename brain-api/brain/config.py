import os
from pathlib import Path
from dotenv import load_dotenv
from .model_config import configure_llm_environment

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
# Cognee 1.6.3 reloads dotenv with override=True; prevent it undoing enforced ACL and defaults.
os.environ["COGNEE_ENV_FILE"] = str(Path(__file__).with_name("cognee.env"))
os.environ["ENABLE_BACKEND_ACCESS_CONTROL"] = "true"
os.environ.pop("MONITORING_TOOL", None)  # Unpublished Cognee adapter; use respan-ai spans directly.
DEFAULTS = {
    "LLM_PROVIDER": "custom", "LLM_ENDPOINT": "https://api.respan.ai/api",
    "LLM_MODEL": "openai/gpt-5-mini",
    "EMBEDDING_PROVIDER": "openai_compatible", "EMBEDDING_ENDPOINT": "http://127.0.0.1:8101/v1",
    "EMBEDDING_MODEL": "nemotron-embed", "EMBEDDING_API_KEY": ".", "EMBEDDING_DIMENSIONS": "2048",
    "SYSTEM_ROOT_DIRECTORY": str(ROOT / ".cognee/system"),
    "DATA_ROOT_DIRECTORY": str(ROOT / ".cognee/data"),
    "BRAIN_RESEARCH_LEDGER": str(ROOT / "data/research-ingestion.sqlite3"),
    "BRAIN_API_HOST": "100.87.113.122", "BRAIN_API_PORT": "8200", "JUDGE_MODEL": "gpt-5-mini",
    # Ubuntu SQLite 3.45.1 crashes on Cognee's nested user joins; use the existing local Postgres.
    "DB_PROVIDER": "postgres", "DB_HOST": "127.0.0.1", "DB_PORT": "5432",
    "DB_USERNAME": "rag", "DB_NAME": "cognee",
    # A kept-alive engine holds the Ladybug file lock, so a second user's worker can't open a shared dataset.
    # ponytail: close engines at release; slower cold opens, revisit with a graph server if latency matters.
    "SUBPROCESS_IDLE_TTL_SECONDS": "0",
}
for key, value in DEFAULTS.items():
    if not os.environ.get(key):
        os.environ[key] = value
configure_llm_environment(os.environ)
os.environ["LLM_API_KEY"] = os.environ.get("RESPAN_API_KEY", "")
if not os.environ.get("DB_PASSWORD"):
    os.environ["DB_PASSWORD"] = os.environ.get("POSTGRES_PASSWORD", "")
if os.environ["BRAIN_API_HOST"] not in {"100.87.113.122", "127.0.0.1"}:
    raise ValueError("BRAIN_API_HOST must be a tailnet or loopback address")
