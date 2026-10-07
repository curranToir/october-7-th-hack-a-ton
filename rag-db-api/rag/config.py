import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    database_url: str
    embed_base_url: str
    embed_model: str
    rerank_base_url: str
    rerank_model: str
    answer_base_url: str
    answer_api_key: str
    answer_model: str


def load_settings() -> Settings:
    env_file = PROJECT_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key:
                os.environ.setdefault(key, value)
    return Settings(
        database_url=os.getenv("DATABASE_URL", "postgresql://rag:rag@127.0.0.1:5432/rag"),
        embed_base_url=os.getenv("EMBED_BASE_URL", "http://127.0.0.1:8101/v1"),
        embed_model=os.getenv("EMBED_MODEL", "nemotron-embed"),
        rerank_base_url=os.getenv("RERANK_BASE_URL", "http://127.0.0.1:8102"),
        rerank_model=os.getenv("RERANK_MODEL", "nemotron-rerank"),
        answer_base_url=os.getenv("ANSWER_BASE_URL", ""),
        answer_api_key=os.getenv("ANSWER_API_KEY", ""),
        answer_model=os.getenv("ANSWER_MODEL", ""),
    )


settings: Settings = load_settings()
