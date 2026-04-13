import os
from pathlib import Path


class Config:
    """Minimal config for ReWire Demo backend."""

    # --- API Keys ---
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY", "")

    # --- ElevenLabs ---
    ELEVENLABS_VOICE_ID: str = os.getenv("ELEVENLABS_VOICE_ID", "0yXkuUWXDHdmdQJugJLb")
    ELEVENLABS_MODEL_ID: str = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")

    # --- Claude ---
    CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

    # --- FFmpeg ---
    FFMPEG_BIN: str = os.getenv("FFMPEG_BIN", "ffmpeg")
    FFPROBE_BIN: str = os.getenv("FFPROBE_BIN", "ffprobe")

    # --- Paths ---
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    ASSETS_DIR: Path = BASE_DIR / "assets"
    MUSIC_FILE: Path = ASSETS_DIR / "heroes_wwii.mp3"
    OUT_DIR: str = os.getenv("OUT_DIR", "/tmp/rewire-demo-output")

    @property
    def out_dir_path(self) -> Path:
        p = Path(self.OUT_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p

    # --- DB ---
    DB_URL: str = os.getenv("DB_URL", "sqlite:///./demo.db")

    # --- CORS ---
    ALLOWED_ORIGINS: list = os.getenv(
        "ALLOWED_ORIGINS", "*"
    ).split(",")

    # --- Public URL for serving audio ---
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "")


cfg = Config()