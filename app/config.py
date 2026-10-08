"""Central configuration, read from environment variables with sane defaults."""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./certforge.db")
    storage_dir: Path = Path(os.getenv("STORAGE_DIR", "./storage"))
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    max_recipients: int = int(os.getenv("MAX_RECIPIENTS", "1000"))


settings = Settings()
