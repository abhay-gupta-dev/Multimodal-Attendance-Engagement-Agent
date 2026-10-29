from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/attendance.db"
    cors_origins: str = "http://localhost:5173"
    demo_mode: bool = True
    upload_max_mb: int = 100
    max_video_duration_seconds: int = 1800
    max_video_frames: int = 60000
    mediapipe_model_path: str = ""
    roster_import_token: str = ""
    auth_bootstrap_token: str = ""
    auth_bootstrap_token_expires_at: str = ""
    auth_session_ttl_hours: int = 8

    # Resolve the backend environment file from this module so settings do not
    # silently change when Uvicorn is launched from the repository root.
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")


settings = Settings()
BASE_DIR = BACKEND_DIR

