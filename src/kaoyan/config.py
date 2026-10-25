from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"

    embedding_api_key: str = ""
    embedding_base_url: str = ""
    embedding_model: str = "qwen3-embedding:0.6b"
    embedding_batch_size: int = 8

    export_dir: str = "data/exports"
    memory_db: str = "data/memory.db"

    kaoyan_db: str = "data/kaoyan.db"
    kaoyan_data_dir: str = "data/kaoyan"
    kaoyan_current_cycle: int | None = None

    @field_validator("kaoyan_current_cycle", mode="before")
    @classmethod
    def _empty_cycle(cls, value: object) -> object:
        return None if value in ("", None) else value
    kaoyan_chroma_dir: str = "data/chroma_kaoyan"
    kaoyan_collection: str = "kaoyan_docs"
    ocr_backend: str = "none"
    ocr_cache_dir: str = "data/kaoyan/ocr_cache"
    vision_base_url: str = ""
    vision_model: str = ""
    vision_api_key: str = ""
    vision_timeout: float = 60.0

    crawl_user_agent: str = "kaoyan-info-agent/0.4 (+https://github.com/ddmashawty/kaoyan)"
    crawl_contact: str = ""
    crawl_min_interval_sec: float = 3.0
    crawl_respect_robots: bool = True
    crawl_timeout: float = 20.0
    crawl_max_retries: int = 2
    crawl_backoff_sec: float = 2.0
    crawl_max_pages: int = 20
    crawl_probe_ids: int = 3
    crawl_cache_dir: str = "data/kaoyan/cache"

    chunk_size: int = 800
    chunk_overlap: int = 120
    top_k: int = 8
    max_tool_calls: int = 5
    request_timeout: int = 120

    @property
    def root(self) -> Path:
        return ROOT

    def resolve(self, path: str | Path) -> Path:
        p = Path(path)
        return p if p.is_absolute() else ROOT / p

    @property
    def chroma_path(self) -> Path:
        return self.resolve(self.kaoyan_chroma_dir)

    @property
    def collection_name(self) -> str:
        return self.kaoyan_collection

    @property
    def export_path(self) -> Path:
        return self.resolve(self.export_dir)

    @property
    def memory_path(self) -> Path:
        return self.resolve(self.memory_db)

    @property
    def kaoyan_db_path(self) -> Path:
        return self.resolve(self.kaoyan_db)

    @property
    def kaoyan_data_path(self) -> Path:
        return self.resolve(self.kaoyan_data_dir)

    @property
    def ocr_cache_path(self) -> Path:
        return self.resolve(self.ocr_cache_dir)

    @property
    def crawl_cache_path(self) -> Path:
        return self.resolve(self.crawl_cache_dir)

    @property
    def crawl_user_agent_full(self) -> str:
        contact = self.crawl_contact.strip()
        return f"{self.crawl_user_agent} ({contact})" if contact else self.crawl_user_agent

    @property
    def embedding_enabled(self) -> bool:
        # Ollama often ignores the key; require only a base URL.
        return bool(self.embedding_base_url.strip())

    @property
    def resolved_embedding_api_key(self) -> str:
        return self.embedding_api_key.strip() or "ollama"

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
