import uuid
import time
import json
import csv
import io
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from pydub import AudioSegment

from app.core.config import cfg
from app.db import get_db
from app.models import DemoSession
from app.services.prompt import build_user_prompt
from app.services.llm import generate_speech
from app.services.tts import synth
from app.services.mix import mix as mix_audio
from app.utils.audio import load_audio, duration_ms

r = APIRouter(prefix="/api/demo", tags=["demo"])

MUSIC_FADEIN_MS = 5000   # Music fades in over 5 seconds (voice-only opening statements)
TAIL_BUFFER_MS = 2000    # Music plays 2 seconds after voice ends


# --- Request / Response schemas ---

class GenerateRequest(BaseModel):
    q1_wound: str
    q2_chills_trigger: str
    q3_hidden_truth: str


class GenerateResponse(BaseModel):
    session_id: str
    audio_url: str
    speech_format: str
    generation_time_seconds: float


class DemographicsRequest(BaseModel):
    session_id: str
    age: Optional[str] = None
    gender: Optional[str] = None
    ethnicity: Optional[str] = None


class FeedbackRequest(BaseModel):
    session_id: str
    felt_chills: Optional[bool] = None
    chills_count: Optional[int] = 0
    chills_timestamps_json: Optional[str] = None
    experience_driver: Optional[str] = None
    feedback_note: Optional[str] = None


class FeedbackResponse(BaseModel):
    status: str


# --- Helper functions ---

def _word_count(txt: str) -> int:
    return len((txt or "").strip().split())


def _within(ms: int, target: int, tol: float = 0.04) -> bool:
    return abs(ms - target) <= int(target * tol)


# --- Endpoints ---

@r.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest, db: Session = Depends(get_db)):
    """
    Takes 3 free-text answers.
    1. Calculate target words from music duration
    2. Build prompt with target word count
    3. Generate speech via Claude
    4. Synthesize via ElevenLabs TTS
    5. Check duration, correct if needed
    6. Pad silence at end so music plays after speech stops
    7. Mix with music (voice first, music fades in)
    8. Return audio URL
    """
    start = time.time()
    session_id = uuid.uuid4().hex[:16]

    # 1. Get music duration and calculate target words
    music_path = cfg.MUSIC_FILE
    if not music_path.exists():
        raise HTTPException(status_code=500, detail="Music file not found in assets")

    music_ms = duration_ms(load_audio(str(music_path)))
    # Voice fills most of the music duration, leaving tail buffer at the end
    spoken_target_ms = max(int(music_ms - TAIL_BUFFER_MS), int(0.75 * music_ms))
    # ElevenLabs v3 at style=1.0 speaks at ~2.0 words per second
    target_words = min(int((spoken_target_ms / 1000) * 2.0), 1200)

    print(f"[Demo] Music: {music_ms}ms, spoken target: {spoken_target_ms}ms, target words: {target_words}")

    # 2. Build prompt with target word count (AI selects format)
    user_prompt = build_user_prompt(
        q1_wound=req.q1_wound,
        q2_chills_trigger=req.q2_chills_trigger,
        q3_hidden_truth=req.q3_hidden_truth,
        target_words=target_words,
    )
    speech_format = "AI_SELECTED"

    # 3. Generate speech text via Claude
    print(f"[Demo] Generating speech for session {session_id}, target_words={target_words}")
    speech_text = generate_speech(user_prompt)
    print(f"[Demo] Speech generated, {_word_count(speech_text)} words")

    # 4. TTS via ElevenLabs
    print(f"[Demo] Synthesizing TTS...")
    voice_id = cfg.ELEVENLABS_VOICE_ID
    voice_wav_path = synth(
        text=speech_text,
        voice_id=voice_id,
        key=cfg.ELEVENLABS_API_KEY,
    )
    print(f"[Demo] TTS done: {voice_wav_path}")

    # 5. Check duration and correct if needed (up to 3 attempts)
    tts_ms = duration_ms(load_audio(voice_wav_path))
    ema_wps = 2.0
    best_script = speech_text
    best_wav = voice_wav_path

    print(f"[Demo] TTS duration: {tts_ms}ms (target: {spoken_target_ms}ms)")

    for attempt in range(3):
        wc = _word_count(best_script)
        observed_wps = wc / max(1.0, tts_ms / 1000.0)
        ema_wps = 0.7 * ema_wps + 0.3 * observed_wps

        if _within(tts_ms, spoken_target_ms):
            print(f"[Demo] Duration within tolerance, done.")
            break

        delta_ms = spoken_target_ms - tts_ms
        delta_words = int(abs(delta_ms) / 1000.0 * ema_wps)
        delta_words = max(30, min(delta_words, 200))

        if delta_ms > 0:
            # Too short -- generate more
            print(f"[Demo] Speech too short by {delta_ms}ms, extending by ~{delta_words} words")
            tail = " ".join(best_script.strip().split()[-40:])
            extend_prompt = f"""Continue this speech naturally. Write approximately {delta_words} more words. Maintain the same tone, style and emotional arc. Do not repeat what was already said. Pick up exactly where this left off:

...{tail}

Continue now. Only the continuation text. No preamble."""
            more = generate_speech(extend_prompt)
            if more and more not in best_script:
                best_script = (best_script + " " + more).strip()
        else:
            # Too long -- trim from the end
            print(f"[Demo] Speech too long by {abs(delta_ms)}ms, trimming ~{delta_words} words")
            words = best_script.strip().split()
            best_script = " ".join(words[:-delta_words])

        # Re-synthesize
        best_wav = synth(
            text=best_script,
            voice_id=voice_id,
            key=cfg.ELEVENLABS_API_KEY,
        )
        tts_ms = duration_ms(load_audio(best_wav))
        print(f"[Demo] Correction attempt {attempt + 1}: {tts_ms}ms (target: {spoken_target_ms}ms)")

    speech_text = best_script
    voice_wav_path = best_wav

    # 6. Pad silence at end of voice (music plays TAIL_BUFFER_MS after speech stops)
    voice_audio = AudioSegment.from_file(voice_wav_path)
    tail_silence = AudioSegment.silent(duration=TAIL_BUFFER_MS, frame_rate=voice_audio.frame_rate)
    padded = voice_audio + tail_silence
    padded_wav = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    padded.export(padded_wav.name, format="wav")
    voice_wav_path = padded_wav.name

    print(f"[Demo] Padded {TAIL_BUFFER_MS}ms silence at end. Voice file now {duration_ms(padded)}ms")

    # 7. Mix with music (voice starts first, music fades in)
    print(f"[Demo] Mixing with music...")
    audio_filename = f"{session_id}.mp3"
    out_path = cfg.out_dir_path / audio_filename

    mix_audio(
        voice_path=voice_wav_path,
        music_path=str(music_path),
        out_path=str(out_path),
        sync_mode="retime_music_to_voice",
        music_fadein_ms=MUSIC_FADEIN_MS,
        ffmpeg_bin=cfg.FFMPEG_BIN,
    )
    print(f"[Demo] Mix done: {out_path}")

    elapsed = round(time.time() - start, 2)

    # 8. Save session to DB
    session = DemoSession(
        session_id=session_id,
        q1_wound=req.q1_wound,
        q2_chills_trigger=req.q2_chills_trigger,
        q3_hidden_truth=req.q3_hidden_truth,
        speech_format=speech_format,
        speech_text=speech_text,
        voice_id=voice_id,
        music_track="heroes_wwii.mp3",
        audio_filename=audio_filename,
        generation_time_seconds=elapsed,
    )
    db.add(session)
    db.commit()

    # Build audio URL
    base = cfg.PUBLIC_BASE_URL.rstrip("/") if cfg.PUBLIC_BASE_URL else ""
    audio_url = f"{base}/api/demo/audio/{audio_filename}"

    print(f"[Demo] Session {session_id} complete in {elapsed}s")

    return GenerateResponse(
        session_id=session_id,
        audio_url=audio_url,
        speech_format=speech_format,
        generation_time_seconds=elapsed,
    )


@r.post("/demographics", response_model=FeedbackResponse)
def save_demographics(req: DemographicsRequest, db: Session = Depends(get_db)):
    """Save demographics collected during the wait."""
    session = db.query(DemoSession).filter(DemoSession.session_id == req.session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if req.age is not None:
        session.age = req.age
    if req.gender is not None:
        session.gender = req.gender
    if req.ethnicity is not None:
        session.ethnicity = req.ethnicity

    db.commit()
    return FeedbackResponse(status="ok")


@r.post("/feedback", response_model=FeedbackResponse)
def save_feedback(req: FeedbackRequest, db: Session = Depends(get_db)):
    """Save chills feedback after the experience."""
    session = db.query(DemoSession).filter(DemoSession.session_id == req.session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session.felt_chills = req.felt_chills
    session.chills_count = req.chills_count or 0
    session.chills_timestamps_json = req.chills_timestamps_json
    session.experience_driver = req.experience_driver
    session.feedback_note = req.feedback_note
    session.completed_at = datetime.now(timezone.utc)

    db.commit()
    return FeedbackResponse(status="ok")


@r.get("/audio/{filename}")
def serve_audio(filename: str):
    """Serve a generated audio file."""
    filepath = cfg.out_dir_path / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Audio not found")

    return StreamingResponse(
        open(filepath, "rb"),
        media_type="audio/mpeg",
        headers={"Content-Disposition": f"inline; filename={filename}"},
    )


@r.get("/export/csv")
def export_csv(db: Session = Depends(get_db)):
    """Export all demo sessions as CSV for Felix."""
    sessions = db.query(DemoSession).order_by(DemoSession.created_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "session_id",
        "q1_wound",
        "q2_chills_trigger",
        "q3_hidden_truth",
        "age",
        "gender",
        "ethnicity",
        "speech_format",
        "speech_text",
        "voice_id",
        "music_track",
        "audio_filename",
        "felt_chills",
        "chills_count",
        "chills_timestamps",
        "experience_driver",
        "feedback_note",
        "generation_time_seconds",
        "created_at",
        "completed_at",
    ])

    for s in sessions:
        writer.writerow([
            s.session_id,
            s.q1_wound,
            s.q2_chills_trigger,
            s.q3_hidden_truth,
            s.age,
            s.gender,
            s.ethnicity,
            s.speech_format,
            s.speech_text,
            s.voice_id,
            s.music_track,
            s.audio_filename,
            s.felt_chills,
            s.chills_count,
            s.chills_timestamps_json,
            s.experience_driver,
            s.feedback_note,
            s.generation_time_seconds,
            s.created_at,
            s.completed_at,
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=rewire_demo_sessions.csv"},
    )