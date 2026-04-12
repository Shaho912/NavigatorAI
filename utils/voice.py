"""
utils/voice.py — Audio I/O, intent detection, and session memory for Navigator AI.

Session memory:
  _session_log holds every exchange this session.
  detect_intent() passes the last MAX_CONTEXT_EXCHANGES turns to Claude so
  Navigator can refer back to what was said earlier in the conversation.

Thread safety:
  _audio_lock    — one thread plays/records at a time.
  _conversation_active — set while main loop is mid-turn; monitor waits.
  _muted         — set when user says "shut up"; speak() skips until next listen().
"""

import json
import os
import threading
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf
import speech_recognition as sr
import anthropic
from dotenv import load_dotenv
from elevenlabs import ElevenLabs

load_dotenv()

SESSION_LOG_PATH = Path(__file__).parent.parent / "session_log.json"
_log_lock = threading.Lock()


def _append_to_file(speaker: str, message: str) -> None:
    entry = {
        "speaker":   speaker,
        "message":   message,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with _log_lock:
        with open(SESSION_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def clear_session_file() -> None:
    """Truncate session_log.json. Called once at startup."""
    SESSION_LOG_PATH.write_text("", encoding="utf-8")

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")

elevenlabs_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
anthropic_client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ── Threading primitives ──────────────────────────────────────────────────────

_audio_lock          = threading.Lock()
_conversation_active = threading.Event()
_muted               = threading.Event()   # set → speak() is silenced until next listen()
_speaking            = threading.Event()   # set while TTS audio is playing; blocks mic pickup
_listen_active       = threading.Event()   # set while any listen() call is running

# ── Audio constants ───────────────────────────────────────────────────────────

SAMPLE_RATE              = 16000
PCM_SAMPLE_RATE          = 22050
CHUNK_DURATION           = 0.05
CHUNK_SAMPLES            = int(SAMPLE_RATE * CHUNK_DURATION)
SILENCE_THRESHOLD        = 500
SPEECH_THRESHOLD         = 800
SILENCE_AFTER_SPEECH     = 2.0
MAX_RECORD_SECONDS       = 15
PRE_SPEECH_BUFFER_CHUNKS = 4

# ── Session memory ────────────────────────────────────────────────────────────

_session_log: list[dict] = []   # {"role": "user"|"navigator", "text": "..."}
MAX_CONTEXT_EXCHANGES = 5       # keep last N user+navigator pairs


def _session_context() -> str:
    """Return the last MAX_CONTEXT_EXCHANGES turns as a formatted string."""
    recent = _session_log[-(MAX_CONTEXT_EXCHANGES * 2):]
    if not recent:
        return ""
    lines = [f"{e['role'].capitalize()}: {e['text']}" for e in recent]
    return "Recent conversation:\n" + "\n".join(lines)


def clear_session_log() -> None:
    """Reset session memory. Called between test runs."""
    _session_log.clear()


# ── Intent detection ──────────────────────────────────────────────────────────

def detect_intent(response: str, context: str, intents: list[str]) -> str:
    """
    Use Claude Haiku to classify `response` into one of `intents`.

    The last MAX_CONTEXT_EXCHANGES turns from the session log are included so
    Claude can resolve pronouns, callbacks, and follow-up questions correctly.
    Returns exactly one string from `intents`; falls back to the last entry.
    """
    intent_list = ", ".join(intents)
    session_ctx  = _session_context()

    user_content = (
        f"{session_ctx}\n\n" if session_ctx else ""
    ) + (
        f"The assistant just said: \"{context}\"\n"
        f"The user replied: \"{response}\"\n"
        f"Intents: {intent_list}\n"
        f"Which intent best matches?"
    )

    try:
        message = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,
            system=(
                "You classify user voice responses for a voice health assistant called Navigator. "
                "Reply with only the single exact intent label — nothing else."
            ),
            messages=[{"role": "user", "content": user_content}],
        )
        raw = message.content[0].text.strip().lower()
        for intent in intents:
            if intent.lower() in raw:
                return intent
    except Exception:
        pass

    return intents[-1]


# ── Audio helpers ─────────────────────────────────────────────────────────────

def _rms(chunk: np.ndarray) -> float:
    return float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))


def _beep() -> None:
    """Short 880 Hz tone signals that Navigator is now listening."""
    duration = 0.12
    t    = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
    tone = (0.3 * np.sin(2 * np.pi * 880 * t)).astype(np.float32)
    sd.play(tone, samplerate=SAMPLE_RATE)
    sd.wait()


# ── listen() ─────────────────────────────────────────────────────────────────

def listen() -> str:
    """
    Record from mic using voice-activity detection.

    Clears _muted when a new utterance starts (user speaking again un-silences
    Navigator). Logs the transcription to _session_log before returning.
    Only one listen() call may run at a time (_listen_active gates concurrent callers).
    """
    _listen_active.set()
    try:
        print("  [waiting for speech]")

        pre_buffer: list[np.ndarray] = []
        recorded:   list[np.ndarray] = []
        speech_detected = False
        silence_counter = 0.0
        total_recorded  = 0.0

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                            blocksize=CHUNK_SAMPLES) as stream:
            while True:
                chunk, _ = stream.read(CHUNK_SAMPLES)
                chunk = chunk.flatten()

                # Discard audio while Navigator is speaking — prevents TTS feedback loop
                if _speaking.is_set():
                    pre_buffer.clear()
                    continue

                level = _rms(chunk)

                if not speech_detected:
                    pre_buffer.append(chunk.copy())
                    if len(pre_buffer) > PRE_SPEECH_BUFFER_CHUNKS:
                        pre_buffer.pop(0)
                    if level > SPEECH_THRESHOLD:
                        speech_detected = True
                        _muted.clear()          # user spoke — un-silence Navigator
                        _beep()
                        print("  Listening...")
                        recorded.extend(pre_buffer)
                        recorded.append(chunk.copy())
                        pre_buffer.clear()
                else:
                    recorded.append(chunk.copy())
                    total_recorded += CHUNK_DURATION
                    if level < SILENCE_THRESHOLD:
                        silence_counter += CHUNK_DURATION
                    else:
                        silence_counter = 0.0
                    if silence_counter >= SILENCE_AFTER_SPEECH:
                        break
                    if total_recorded >= MAX_RECORD_SECONDS:
                        print("  [max recording time reached]")
                        break

        if not speech_detected:
            raise _SilenceOnly()

        print("  Transcribing...")
        audio_data = np.concatenate(recorded)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            sf.write(f.name, audio_data, SAMPLE_RATE, subtype="PCM_16")
            temp_path = f.name

        recognizer = sr.Recognizer()
        with sr.AudioFile(temp_path) as source:
            audio = recognizer.record(source)
        os.unlink(temp_path)

        text = recognizer.recognize_google(audio)
        _session_log.append({"role": "user", "text": text})
        _append_to_file("user", text)
        return text

    finally:
        _listen_active.clear()


class _SilenceOnly(Exception):
    """Raised when listen() detects only background noise — no speech onset."""
    pass


def listen_or_wait() -> str | None:
    """Returns None on silence; raises sr.UnknownValueError on unclear speech."""
    try:
        return listen()
    except _SilenceOnly:
        return None


# ── speak() ──────────────────────────────────────────────────────────────────

def speak(text: str) -> None:
    """
    Convert text to speech via ElevenLabs and play through speakers.

    Skips silently if _muted is set (user said "shut up").
    Logs every utterance to _session_log.
    """
    if _muted.is_set():
        print(f'  [muted, skipped]: "{text}"')
        return

    _session_log.append({"role": "navigator", "text": text})
    _append_to_file("navigator", text)
    print(f'  Speaking: "{text}"')

    try:
        import time as _time
        audio_generator = elevenlabs_client.text_to_speech.convert(
            voice_id="JBFqnCBsd6RMkjVDRZzb",
            text=text,
            model_id="eleven_multilingual_v2",
            output_format="pcm_22050",
        )
        pcm_bytes  = b"".join(audio_generator)
        audio_array = (
            np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        )
        with _audio_lock:
            _speaking.set()
            try:
                sd.play(audio_array, samplerate=PCM_SAMPLE_RATE)
                sd.wait()
            finally:
                # Cooldown so the mic doesn't pick up residual speaker echo
                _time.sleep(1.5)
                _speaking.clear()
    except Exception as exc:
        _speaking.clear()
        print(f"  [speak error]: {exc}")


# ── Test entry point ──────────────────────────────────────────────────────────

def main():
    try:
        transcription = listen()
        print(f"  You said: {transcription}")
    except _SilenceOnly:
        print("  No speech detected.")
    except sr.UnknownValueError:
        print("  Speech detected but not understood.")
    except sr.RequestError as e:
        print(f"  STT error: {e}")

    speak("Hi, I'm Navigator. How can I help you today?")


if __name__ == "__main__":
    main()
