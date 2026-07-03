from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    db_path: Path = Path("data/db/pathosphere.db")
    parquet_dir: Path = Path("data/parquet")

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_llm_model: str = "qwen3:4b"
    ollama_embed_model: str = "nomic-embed-text"

    # Local embedding
    embed_model_name: str = "intfloat/multilingual-e5-small"

    # Logging
    log_level: str = "INFO"
    log_dir: Path = Path("data/logs")

    # Geocoding
    nominatim_user_agent: str = "pathosphere/0.1"

    # NASA FIRMS (free MAP_KEY from https://firms.modaps.eosdis.nasa.gov/api/)
    firms_map_key: str | None = None

    # Tor SOCKS proxy for geo-blocked feeds (e.g. RT, sanctioned in the EU).
    # Tor Browser exposes 9150; a standalone tor daemon uses 9050.
    tor_socks_proxy: str = "socks5://127.0.0.1:9150"

    # LLM backend for reasoning tasks (agent/brief/thesis)
    reasoning_model: str = "claude"  # "claude" | "qwen-local"

    # Predictions v2: timing penalty per day of |resolved_date - horizon_date|
    # in time_adjusted_score (0.001 → ~1000 days off = full penalty)
    timing_penalty_alpha: float = 0.001

    @field_validator("log_level")
    @classmethod
    def log_level_upper(cls, v: str) -> str:
        return v.upper()


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
