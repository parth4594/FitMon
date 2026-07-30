from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    supabase_db_host: str
    supabase_db_port: int = 5432
    supabase_db_name: str
    supabase_db_user: str
    supabase_db_password: str

    hevy_api_key: str

    # Email alerts (pipeline run outcomes + 25h staleness warning)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    smtp_user: str
    smtp_password: str
    alert_email_to: str


settings = Settings()
