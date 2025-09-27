from typing import Optional
from pydantic import Field, AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Pydantic v2: читаем .env, игнорируем любые незадействованные ключи
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

    # === Telegram creds (поддерживаем старые имена) ===
    tg_api_id: int = Field(validation_alias=AliasChoices("TG_API_ID", "API_ID"))
    tg_api_hash: str = Field(validation_alias=AliasChoices("TG_API_HASH", "API_HASH"))
    tg_bot_token: str = Field(validation_alias=AliasChoices("TG_BOT_TOKEN", "BOT_TOKEN"))

    # === Targets (принимаем альтернативные имена) ===
    target_politics: str = Field(validation_alias=AliasChoices("TARGET_POLITICS", "TARGET_POLITIC", "TARGET_POL"))
    target_trading: str = Field(validation_alias=AliasChoices("TARGET_TRADING", "TARGET_TRADE"))
    target_pf: str = Field(validation_alias=AliasChoices("TARGET_PF", "TARGET_PERSONAL"))
    target_infosec: str = Field(validation_alias=AliasChoices("TARGET_INFOSEC", "TARGET_IB", "TARGET_SECURITY"))

    # === Behaviour Flags ===
    ui_variant: str = Field(default="reply", validation_alias=AliasChoices("UI_VARIANT"))
    publish_mode: str = Field(default="llm", validation_alias=AliasChoices("PUBLISH_MODE"))

    llm_enabled: bool = Field(default=True, validation_alias=AliasChoices("LLM_ENABLED"))
    llm_temperature: float = Field(default=0.2, validation_alias=AliasChoices("LLM_TEMPERATURE"))
    llm_max_tokens: int = Field(default=800, validation_alias=AliasChoices("LLM_MAX_TOKENS"))
    prompt_version: str = Field(default="V2", validation_alias=AliasChoices("PROMPT_VERSION"))

    # === Storage / logging ===
    data_dir: str = Field(default="./data", validation_alias=AliasChoices("DATA_DIR"))
    log_level: str = Field(default="INFO", validation_alias=AliasChoices("LOG_LEVEL"))

    # === Совместимость: опциональные поля из твоего .env ===
    session_name: Optional[str] = Field(default="quadridigest_session", validation_alias=AliasChoices("SESSION_NAME"))
    tz: Optional[str] = Field(default=None, validation_alias=AliasChoices("TZ"))
    ollama_host: Optional[str] = Field(default=None, validation_alias=AliasChoices("OLLAMA_HOST"))
    llm_model: Optional[str] = Field(default=None, validation_alias=AliasChoices("LLM_MODEL"))
    llm_weight: Optional[float] = Field(default=None, validation_alias=AliasChoices("LLM_WEIGHT"))
    llm_cache_db: Optional[str] = Field(default=None, validation_alias=AliasChoices("LLM_CACHE_DB"))
    llm_cache_ttl_days: Optional[int] = Field(default=None, validation_alias=AliasChoices("LLM_CACHE_TTL_DAYS"))

    # Прочие опциональные ключи из .env (не критично, но пусть подхватываются)
    window: Optional[str] = Field(default=None, validation_alias=AliasChoices("WINDOW"))
    max_items: Optional[int] = Field(default=None, validation_alias=AliasChoices("MAX_ITEMS"))
    min_score: Optional[float] = Field(default=None, validation_alias=AliasChoices("MIN_SCORE"))
    details_mode: Optional[str] = Field(default=None, validation_alias=AliasChoices("DETAILS_MODE"))
    details_max: Optional[int] = Field(default=None, validation_alias=AliasChoices("DETAILS_MAX"))
    fetch_concurrency: Optional[int] = Field(default=None, validation_alias=AliasChoices("FETCH_CONCURRENCY"))
    history_concurrency: Optional[int] = Field(default=None, validation_alias=AliasChoices("HISTORY_CONCURRENCY"))
    per_channel_timeout: Optional[int] = Field(default=None, validation_alias=AliasChoices("PER_CHANNEL_TIMEOUT"))
    per_channel_retries: Optional[int] = Field(default=None, validation_alias=AliasChoices("PER_CHANNEL_RETRIES"))
    flood_cap_seconds: Optional[int] = Field(default=None, validation_alias=AliasChoices("FLOOD_CAP_SECONDS"))
    flood_jitter_max: Optional[int] = Field(default=None, validation_alias=AliasChoices("FLOOD_JITTER_MAX"))
    max_per_channel: Optional[int] = Field(default=None, validation_alias=AliasChoices("MAX_PER_CHANNEL"))
    debug_fetch: Optional[bool] = Field(default=None, validation_alias=AliasChoices("DEBUG_FETCH"))
    dry_run_env: Optional[bool] = Field(default=None, validation_alias=AliasChoices("DRY_RUN"))
    llm_trace_enabled: Optional[bool] = Field(default=None, validation_alias=AliasChoices("LLM_TRACE_ENABLED"))
    llm_trace_path: Optional[str] = Field(default=None, validation_alias=AliasChoices("LLM_TRACE_PATH"))

settings = Settings()

# Маппинг старого DETAILS_MODE -> нового ui_variant (на лету, без правки .env)
if settings.details_mode:
    if settings.details_mode == "reply":
        settings.ui_variant = "reply"
    elif settings.details_mode == "spoiler":
        # ближайший аналог — edit или reply; оставим reply как default для стабильности
        settings.ui_variant = "reply"
    elif settings.details_mode == "off":
        settings.ui_variant = "reply"
