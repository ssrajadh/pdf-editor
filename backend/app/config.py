from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash-image"
    model_provider: str = "gemini"
    model_timeout_seconds: int = 60
    planning_model: str = "gemini-2.5-flash"
    planning_model_temperature: float = 0.1
    storage_path: Path = Path("/data")
    max_file_size_mb: int = 50
    allowed_origins: str = "http://localhost:5173"

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]

    model_config = {"env_file": ("../.env", ".env"), "env_file_encoding": "utf-8"}


settings = Settings()
