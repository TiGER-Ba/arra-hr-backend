from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/hr_platform"
    SECRET_KEY: str = "changeme_at_least_32_chars_long_secret"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.1-8b-instant"

    EMBEDDING_PROVIDER: str = "huggingface"
    HF_API_KEY: str = ""
    HF_EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"

    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_EMBEDDING_MODEL: str = "nomic-embed-text"

    UPLOADS_DIR: str = "./uploads"
    CHROMA_DIR: str = "./chroma_db"
    DOCUMENTS_RH_DIR: str = "./app/documents_rh"

    # Domaine des emails générés automatiquement (ex. w.baba@arra-engineering.com)
    EMAIL_DOMAIN: str = "arra-engineering.com"


settings = Settings()
