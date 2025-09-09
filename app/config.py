from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List

class Settings(BaseSettings):
    fmp_api_key: str = Field(..., alias="FMP_API_KEY")
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    action_key: str = Field("", alias="ACTION_KEY")
    openai_model: str = Field("gpt-5.1", alias="OPENAI_MODEL")
    cors_origins: List[str] = Field(default_factory=lambda: ["*"], alias="CORS_ORIGINS")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
