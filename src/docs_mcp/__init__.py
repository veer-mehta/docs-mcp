import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

__version__ = "0.1.0"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = PROJECT_ROOT / ".crawl-cache"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql://docs_mcp:docs_mcp@localhost:5432/docs_mcp"
    embedding_provider: str = "local"
    embedding_api_key: str | None = None
    embedding_base_url: str = ""
    embedding_model: str = ""
    embedding_dims: int = 1024
    local_embedding_model: str = "BAAI/bge-small-en-v1.5"
    local_embedding_max_tokens: int = 512
    local_embedding_device: str = "auto"
    local_embedding_min_free_vram_gib: float = 1.0
    llm_api_key: str | None = None
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = ""
    llm_max_tokens: int = 2048
    crawl_max_depth: int = 2
    crawl_max_pages: int = 30
    crawl_delay: float = 0.5
    crawl_cache_dir: str = str(DEFAULT_CACHE_DIR)
    user_agent: str = "docs-mcp/0.1 (documentation indexer)"
    mcp_transport: str = "stdio"
    api_host: str = "0.0.0.0"
    api_port: int = int(os.environ.get("PORT", 8000))


settings = Settings()
