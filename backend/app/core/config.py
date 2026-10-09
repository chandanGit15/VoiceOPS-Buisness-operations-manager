"""
Central application settings.

All configuration comes from environment variables (12-factor style), loaded
from a local .env file during development. Never hardcode secrets here.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, resolved once per process."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    app_env: str = "development"
    log_level: str = "info"
    api_v1_prefix: str = "/api/v1"

    # HTTP server. PORT is set by Cloud Run; the container must listen on it.
    port: int = 8000

    # Production hardening: when app_env=production these must be set —
    # app_secret_key (JWT signing), token_encryption_key (OAuth at rest),
    # postgres_password, and at least one non-localhost CORS origin.
    require_production_settings: bool = False

    # CORS: comma-separated list of allowed browser origins
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # LLM provider layer (Part 2)
    llm_provider: str = "gemini"
    gemini_api_key: str = ""  # never logged, never sent to the frontend
    gemini_model: str = "gemini-2.5-flash"

    # Tools (Part 3)
    web_search_provider: str = "tavily"
    tavily_api_key: str = ""
    tool_max_retries: int = 2
    tool_retry_delay_seconds: float = 1.0

    # PostgreSQL (used in a later part, declared now so config is stable)
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "voiceops"
    postgres_password: str = ""
    postgres_db: str = "voiceops"
    # Explicit full URL override (e.g. Cloud SQL connection string); takes
    # precedence over the POSTGRES_* parts when set.
    database_url_override: str = ""

    # Redis (used in a later part)
    redis_url: str = "redis://localhost:6379/0"

    # Agent orchestration (Part 3)
    supervisor_max_steps: int = 6

    # Voice (Part 5)
    stt_provider: str = "deepgram"
    deepgram_api_key: str = ""
    deepgram_stt_model: str = "nova-2"
    tts_provider: str = "elevenlabs"
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"  # "Rachel" default voice
    elevenlabs_tts_model: str = "eleven_multilingual_v2"

    # Google OAuth (Part 6)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/v1/auth/google/callback"
    # Fernet key used to encrypt OAuth tokens at rest (generate:
    # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    token_encryption_key: str = ""

    # RAG / Knowledge Agent (Part 7)
    embedding_provider: str = "gemini"
    embedding_model: str = "text-embedding-004"
    embedding_dimensions: int = 768
    chunk_size_chars: int = 1000
    chunk_overlap_chars: int = 150
    retrieval_top_k: int = 5
    # Below this cosine similarity a chunk counts as irrelevant (no grounding).
    retrieval_min_similarity: float = 0.2
    upload_max_mb: int = 10

    # Authentication & rate limiting (Part 8)
    app_secret_key: str = "dev-only-insecure-secret-change-me"  # JWT signing key
    access_token_expire_minutes: int = 60 * 24
    chat_rate_limit_per_min: int = 30
    voice_rate_limit_per_min: int = 15
    upload_rate_limit_per_min: int = 10

    # Error monitoring (Part 9): optional Sentry DSN (from cloud Secret Manager).
    # Empty disables Sentry entirely — local dev is unaffected.
    sentry_dsn: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def validate_production(self) -> None:
        """Fail fast at startup when production requirements are unmet.

        Called from create_app() when APP_ENV=production. Prevents the
        classic accidents: default JWT secret, empty DB password, wildcard
        CORS, or HTTP-only cookies in a public deployment.
        """
        problems: list[str] = []
        if not self.app_secret_key or "dev-only" in self.app_secret_key:
            problems.append("APP_SECRET_KEY must be a strong random value in production")
        if not self.postgres_password:
            problems.append("POSTGRES_PASSWORD must be set in production")
        if not self.cors_origins_list or any(
            origin.startswith(("http://localhost", "http://127.0.0.1"))
            and len(self.cors_origins_list) == 1
            for origin in self.cors_origins_list
        ):
            problems.append("CORS_ORIGINS must list the production frontend origin")
        if problems:
            raise RuntimeError("Production configuration invalid: " + "; ".join(problems))

    @property
    def database_url(self) -> str:
        """Asynchronous database URL, assembled in one place for later parts."""
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    """Return the cached Settings instance for the current process."""
    return Settings()
