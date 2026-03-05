from pathlib import Path


from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    user_email: str
    user_password: str
    context_dir: Path = Path(".context")