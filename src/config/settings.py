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


settings = Settings()
