from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Float, DateTime
)
from datetime import datetime, timezone
from app.db import Base


class DemoSession(Base):
    __tablename__ = "demo_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), unique=True, nullable=False, index=True)

    # --- 3 Questions ---
    q1_wound = Column(Text)
    q2_chills_trigger = Column(Text)
    q3_hidden_truth = Column(Text)

    # --- Demographics (filled during wait) ---
    age = Column(String(20))
    gender = Column(String(40))
    ethnicity = Column(String(80))

    # --- AI Generation ---
    speech_format = Column(String(40))
    speech_text = Column(Text)
    voice_id = Column(String(60))
    music_track = Column(String(120))
    audio_filename = Column(String(200))

    # --- Chills Feedback ---
    felt_chills = Column(Boolean)
    chills_count = Column(Integer, default=0)
    chills_timestamps_json = Column(Text)
    experience_driver = Column(String(40))
    feedback_note = Column(Text)

    # --- Timing ---
    generation_time_seconds = Column(Float)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime)