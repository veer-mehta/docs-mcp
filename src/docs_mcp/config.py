from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = PROJECT_ROOT / ".crawl-cache"

HOME_DIR = Path.home()
NPM_ENV = HOME_DIR / ".fathom-mcp" / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(NPM_ENV), str(PROJECT_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql://docs_mcp:docs_mcp@localhost:5432/docs_mcp"
    embedding_provider: str = "local"
    embedding_api_key: str | None = None
    embedding_base_url: str = ""
    embedding_model: str = ""
    embedding_dims: int = 1024
    local_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    local_embedding_max_tokens: int = 1024
    local_embedding_device: str = "auto"
    local_embedding_min_free_vram_gib: float = 4.0
    llm_api_key: str | None = None
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = ""
    llm_max_tokens: int = 2048
    crawl_max_depth: int = 2
    crawl_max_pages: int = 30
    crawl_delay: float = 0.5
    crawl_cache_dir: str = str(DEFAULT_CACHE_DIR)
    user_agent: str = "fathom-mcp/0.1 (documentation indexer)"
    mcp_transport: str = "stdio"
    api_host: str = "127.0.0.1"
    api_port: int = 8000


settings = Settings()
