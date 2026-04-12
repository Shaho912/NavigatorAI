"""
Navigator AI — Background Monitor

Runs as a daemon thread alongside the main voice loop.
Polls Gmail and Google Calendar via Ara every hour, checks medication
reminder times every minute, and speaks proactive notifications to the user
via ElevenLabs when something health-related is detected.

Thread safety:
  - utils.voice._audio_lock prevents simultaneous audio from monitor + main loop.
  - utils.voice._conversation_active is checked before any notification so the
    monitor never interrupts an in-progress user conversation.
  - All JSON file writes go through _JSONStore which uses a per-file threading.Lock.
"""

import json
import logging
import os
import threading
import time
import traceback
from datetime import datetime, timedelta
from typing import Any

import anthropic
import speech_recognition as sr
from dotenv import load_dotenv
from elevenlabs import ElevenLabs
from elevenlabs.types import ConversationInitiationClientDataRequestInput

from utils.connectors import check_connector
from utils.profile import load_profile
from utils.voice import (
    _audio_lock,
    _conversation_active,
    _speaking,
    _listen_active,
    _append_to_file,
    detect_intent,
    listen,
    speak,
    _SilenceOnly,
)

load_dotenv()

logger = logging.getLogger(__name__)

ELEVENLABS_API_KEY      = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_AGENT_ID     = os.getenv("ELEVENLABS_AGENT_ID", "")
ELEVENLABS_PHONE_NUMBER_ID = os.getenv("ELEVENLABS_PHONE_NUMBER_ID", "")
RASHID_PHONE_NUMBER     = os.getenv("RASHID_PHONE_NUMBER", "")

_elevenlabs = ElevenLabs(api_key=ELEVENLABS_API_KEY)

# ── Paths ─────────────────────────────────────────────────────────────────────

_BASE = os.path.dirname(os.path.dirname(__file__))
PROCESSED_EMAILS_PATH = os.path.join(_BASE, "processed_emails.json")
SENT_REMINDERS_PATH = os.path.join(_BASE, "sent_reminders.json")

# ── Poll intervals ────────────────────────────────────────────────────────────

GMAIL_INTERVAL_SECONDS    = 20     # 20 seconds
CALENDAR_INTERVAL_SECONDS = 3600   # 1 hour
MEDICATION_INTERVAL_SECONDS = 60   # 1 minute — catches exact reminder times

# ── Appointment reminder lead-times (seconds before start) ───────────────────

APPOINTMENT_REMINDER_WINDOWS = [
    (86400, "24h",  "tomorrow"),
    (7200,  "2h",   "in 2 hours"),
    (1800,  "30min","in 30 minutes"),
]

# ── Claude client ─────────────────────────────────────────────────────────────

_anthropic = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


# ── Thread-safe JSON store ────────────────────────────────────────────────────

class _JSONStore:
    """Minimal thread-safe dict backed by a JSON file."""

    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        try:
            if os.path.exists(self._path):
                with open(self._path) as f:
                    self._data = json.load(f)
        except Exception:
            self._data = {}

    def _save(self) -> None:
        try:
            with open(self._path, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception as exc:
            logger.error("Failed to save %s: %s", self._path, exc)

    def contains(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def set(self, key: str, value: Any = True) -> None:
        with self._lock:
            self._data[key] = value
            self._save()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def all(self) -> dict:
        with self._lock:
            return dict(self._data)


# ── Ara data fetchers (connector-gated) ───────────────────────────────────────

def _fetch_health_emails() -> list[dict]:
    """
    Fetch recent unread health emails via Composio Gmail.
    Returns a list of normalised dicts: {id, from, subject, snippet, body, date}
    Returns [] if connector is unavailable or on any error.
    """
    if not check_connector("Gmail"):
        print("[Monitor] Gmail connector not available — COMPOSIO_API_KEY or COMPOSIO_USER_ID missing.")
        return []
    try:
        from utils.gmail import search_health_emails
        return search_health_emails(max_results=20)
    except Exception:
        print(f"[Monitor] ERROR fetching emails via Composio:\n{traceback.format_exc()}")
        return []


def _fetch_calendar_events() -> list[dict]:
    """
    Fetch upcoming calendar events via Ara Google Calendar connector.
    Returns a list of dicts: {id, title, start_time, end_time, attendees}
    Returns [] silently on any error or if connector is unavailable.
    """
    if not check_connector("Google Calendar"):
        return []
    try:
        from ara_sdk import AraClient
        client = AraClient.from_env()
        now = datetime.utcnow().isoformat() + "Z"
        lookahead = (datetime.utcnow() + timedelta(hours=25)).isoformat() + "Z"
        result = client.run(
            "calendar-reader",
            {"action": "list_events", "time_min": now, "time_max": lookahead},
        )
        return result.get("events", []) if isinstance(result, dict) else []
    except Exception as exc:
        logger.debug("Calendar fetch failed: %s", exc)
        return []


# ── Claude classifiers ────────────────────────────────────────────────────────

def _is_health_email(email: dict) -> bool:
    """Return True if Claude considers this email health-related."""
    try:
        msg = _anthropic.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=5,
            system=(
                "You decide whether an email is health-related. "
                "Health-related means: insurance, medical bills, doctor offices, "
                "prescriptions, appointments, lab results, or any medical topic. "
                "Reply with only: yes or no"
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"From: {email.get('from', '')}\n"
                    f"Subject: {email.get('subject', '')}\n"
                    f"Snippet: {email.get('snippet') or email.get('body', '')[:300]}"
                ),
            }],
        )
        result = "yes" in msg.content[0].text.strip().lower()
        print(f"[Monitor]   Health related: {'YES' if result else 'NO'} "
              f"(Claude said: '{msg.content[0].text.strip()}')")
        return result
    except Exception:
        print(f"[Monitor]   ERROR calling Claude for health check:\n{traceback.format_exc()}")
        return False


def _classify_email(email: dict) -> str:
    """
    Classify a health email into one of:
    denial | bill | appointment_confirmation | appointment_cancellation |
    prescription | general
    """
    try:
        msg = _anthropic.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            system=(
                "Classify this health email into exactly one category. "
                "Categories: denial, bill, appointment_confirmation, "
                "appointment_cancellation, prescription, general. "
                "Reply with only the category label."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"From: {email.get('from', '')}\n"
                    f"Subject: {email.get('subject', '')}\n"
                    f"Snippet: {email.get('snippet') or email.get('body', '')[:300]}"
                ),
            }],
        )
        raw = msg.content[0].text.strip().lower()
        for label in ("denial", "bill", "appointment_confirmation",
                      "appointment_cancellation", "prescription"):
            if label in raw:
                print(f"[Monitor]   Email type: {label}")
                return label
        print(f"[Monitor]   Email type: general (Claude said: '{msg.content[0].text.strip()}')")
        return "general"
    except Exception:
        print(f"[Monitor]   ERROR calling Claude for email classification:\n{traceback.format_exc()}")
        return "general"


# ── One-sentence email summary ────────────────────────────────────────────────

def _one_sentence_summary(email: dict) -> str:
    """Return a single spoken sentence describing what the email is about."""
    try:
        content = (
            f"Subject: {email.get('subject', '')}\n"
            f"From: {email.get('from', '')}\n"
            f"Preview: {email.get('snippet') or email.get('body', '')[:300]}"
        )
        msg = _anthropic.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=60,
            system=(
                "Summarise this health email in exactly one short sentence suitable for "
                "spoken delivery over the phone. Be direct and specific. "
                "Example: 'Your insurance denied your MRI claim from March 1st.' "
                "No preamble, no 'the email says'."
            ),
            messages=[{"role": "user", "content": content}],
        )
        summary = msg.content[0].text.strip()
        print(f"[Monitor]   Summary: {summary}")
        return summary
    except Exception:
        print(f"[Monitor]   ERROR generating email summary:\n{traceback.format_exc()}")
        fallback = f"You have a new health email: {email.get('subject', 'no subject')}."
        print(f"[Monitor]   Summary (fallback): {fallback}")
        return fallback


# ── Notification speaker ──────────────────────────────────────────────────────

def _wait_for_idle(timeout: float = 30.0) -> bool:
    """
    Block until the main loop is not in an active conversation, or timeout.
    Returns True if we got the window, False if we timed out.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _conversation_active.is_set():
            return True
        time.sleep(1.0)
    return False


def _notify(message: str, wait_for_response: bool = True) -> bool:
    """
    Speak a notification and optionally ask for yes/no.
    Acquires the audio lock so it never overlaps with the main thread.
    Returns True if user said yes (or if no response expected).
    """
    if not _wait_for_idle(timeout=60):
        logger.debug("Notification skipped — user in active conversation.")
        return False

    with _audio_lock:
        speak.__wrapped__(message) if hasattr(speak, "__wrapped__") else _speak_raw(message)

    if not wait_for_response:
        return True

    # Only ask for yes/no if the main loop is NOT currently in listen().
    # If _listen_active is set, the main loop already has the mic — calling
    # listen() here would create two concurrent listeners that both capture
    # the same user utterance and produce duplicate responses.
    if _listen_active.is_set():
        print("[Monitor] Skipping yes/no prompt — main loop is currently listening.")
        return False

    # Brief yes/no prompt
    while True:
        try:
            response = listen()
            intent = detect_intent(response, message, ["yes", "no"])
            return intent == "yes"
        except _SilenceOnly:
            continue
        except (sr.UnknownValueError, sr.RequestError):
            return False


def _speak_raw(text: str) -> None:
    """Speak without acquiring the lock (caller must hold it)."""
    import time as _time
    import numpy as np
    import sounddevice as sd
    from utils.voice import elevenlabs_client, PCM_SAMPLE_RATE
    print(f'  [Monitor] Speaking: "{text}"')
    audio_generator = elevenlabs_client.text_to_speech.convert(
        voice_id="JBFqnCBsd6RMkjVDRZzb",
        text=text,
        model_id="eleven_multilingual_v2",
        output_format="pcm_22050",
    )
    pcm_bytes = b"".join(audio_generator)
    audio_array = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    _speaking.set()
    try:
        sd.play(audio_array, samplerate=PCM_SAMPLE_RATE)
        sd.wait()
    finally:
        _time.sleep(1.5)
        _speaking.clear()


# ── Email notification scripts ────────────────────────────────────────────────

_EMAIL_SCRIPTS = {
    "denial": (
        "Hi {name}, I just got an email from your insurance company. "
        "It looks like they denied a claim. "
        "Want me to read it to you and help you figure out what to do?"
    ),
    "bill": (
        "Hi {name}, you just received a medical bill. "
        "Want me to read it to you and explain what you owe?"
    ),
    "appointment_confirmation": (
        "Hi {name}, your appointment has been confirmed. "
        "Want me to add it to your calendar and set a reminder?"
    ),
    "appointment_cancellation": (
        "Hi {name}, it looks like an appointment has been cancelled or rescheduled. "
        "Want me to read the details?"
    ),
    "prescription": (
        "Hi {name}, it looks like you have a prescription notification. "
        "Want me to read it to you?"
    ),
    "general": (
        "Hi {name}, you received a health related email. "
        "Want me to read it to you?"
    ),
}


# ── Monitor class ─────────────────────────────────────────────────────────────

class NavigatorMonitor:

    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._processed = _JSONStore(PROCESSED_EMAILS_PATH)
        self._reminders = _JSONStore(SENT_REMINDERS_PATH)
        self._last_gmail_check = 0.0
        self._last_calendar_check = 0.0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background monitor thread."""
        self._thread = threading.Thread(target=self._loop, daemon=True, name="navigator-monitor")
        self._thread.start()
        print("[Monitor] Background monitor started.")

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        print("[Monitor] Monitor started — polling Gmail every "
              f"{GMAIL_INTERVAL_SECONDS}s, calendar every "
              f"{CALENDAR_INTERVAL_SECONDS}s, medication every "
              f"{MEDICATION_INTERVAL_SECONDS}s")
        while not self._stop.is_set():
            try:
                now = time.monotonic()

                if now - self._last_gmail_check >= GMAIL_INTERVAL_SECONDS:
                    self._check_gmail()
                    self._last_gmail_check = now

                if now - self._last_calendar_check >= CALENDAR_INTERVAL_SECONDS:
                    self._check_calendar()
                    self._last_calendar_check = now

                self._check_medication_reminders()

            except Exception:
                print(f"[Monitor] ERROR in monitor cycle:\n{traceback.format_exc()}")

            self._stop.wait(timeout=min(GMAIL_INTERVAL_SECONDS, MEDICATION_INTERVAL_SECONDS))

    # ── On-demand public methods ───────────────────────────────────────────────

    def check_emails_now(self) -> None:
        """Immediately run a Gmail check regardless of interval."""
        print("[Monitor] On-demand Gmail check triggered.")
        try:
            self._check_gmail()
            self._last_gmail_check = time.monotonic()
        except Exception:
            print(f"[Monitor] ERROR in on-demand email check:\n{traceback.format_exc()}")

    def check_appointments_now(self) -> None:
        """Immediately run a Calendar check regardless of interval."""
        print("[Monitor] On-demand calendar check triggered.")
        try:
            self._check_calendar()
            self._last_calendar_check = time.monotonic()
        except Exception:
            print(f"[Monitor] ERROR in on-demand calendar check:\n{traceback.format_exc()}")

    # ── Gmail monitor ─────────────────────────────────────────────────────────

    def _check_gmail(self) -> None:
        print("[Monitor] Checking Gmail...")
        emails = _fetch_health_emails()
        print(f"[Monitor] Found {len(emails)} email(s)")
        if not emails:
            return

        profile = load_profile() or {}
        name = profile.get("name", "there")

        for i, email in enumerate(emails, 1):
            subject  = email.get("subject", "(no subject)")
            sender   = email.get("from", "(unknown sender)")
            email_id = email.get("id") or subject + email.get("received_at", "")
            print(f"[Monitor] [{i}/{len(emails)}] Subject: {subject}")
            print(f"[Monitor]   From: {sender}")
            print(f"[Monitor]   ID:   {email_id or '(no id)'}")

            if not email_id:
                print("[Monitor]   Skipping — no usable ID")
                continue

            already = self._processed.contains(email_id)
            print(f"[Monitor]   Already processed: {'YES — skipping' if already else 'NO'}")
            if already:
                continue

            # Mark processed immediately — before any async work — so it never fires twice
            self._processed.set(email_id, {
                "subject": subject,
                "processed_at": datetime.utcnow().isoformat(),
            })

            if not _is_health_email(email):
                continue

            email_type = _classify_email(email)
            print(f"[Monitor] Health email detected: {email_type} — {subject}")

            # Generate a one-sentence summary for both the call and the voice script
            summary = _one_sentence_summary(email)

            # ── Outbound phone call ───────────────────────────────────────────
            self._trigger_outbound_call(email, email_type, summary)

            # ── Local voice notification (if user is at the device) ───────────
            script  = _EMAIL_SCRIPTS.get(email_type, _EMAIL_SCRIPTS["general"])
            message = script.format(name=name)
            wants_details = _notify(message, wait_for_response=True)

            if wants_details:
                self._handle_email_followup(email, email_type, name)

    def _trigger_outbound_call(self, email: dict, email_type: str, summary: str) -> None:
        """
        Initiate an ElevenLabs Conversational AI outbound call to RASHID_PHONE_NUMBER.

        Injects {{summary}} as a dynamic variable so the agent's configured first
        message template can reference it directly, e.g.:
          "Hi Rashid, this is Navigator. {{summary}} Want me to walk you through it?"
        Also passes subject, sender, and email_type for use elsewhere in the conversation.
        Logs the attempt (success or failure) to session_log.json.
        """
        if not ELEVENLABS_AGENT_ID or not ELEVENLABS_PHONE_NUMBER_ID or not RASHID_PHONE_NUMBER:
            missing = [
                k for k, v in {
                    "ELEVENLABS_AGENT_ID":        ELEVENLABS_AGENT_ID,
                    "ELEVENLABS_PHONE_NUMBER_ID": ELEVENLABS_PHONE_NUMBER_ID,
                    "RASHID_PHONE_NUMBER":         RASHID_PHONE_NUMBER,
                }.items() if not v
            ]
            print(f"[Monitor] Outbound call SKIPPED — missing env vars: {', '.join(missing)}")
            _append_to_file("navigator", f"[Outbound call skipped — {', '.join(missing)} not set]")
            return

        subject    = email.get("subject", "a health email")
        sender_raw = email.get("from", "")
        # Strip angle-bracket format: "Blue Cross <bc@example.com>" → "Blue Cross"
        sender = sender_raw.split("<")[0].strip() or sender_raw or "your health provider"

        call_label = f"Calling {RASHID_PHONE_NUMBER} — {sender}: {subject}"
        print(f"[Monitor] Triggering call... {call_label}")
        print(f"[Monitor]   agent_id:              {ELEVENLABS_AGENT_ID}")
        print(f"[Monitor]   agent_phone_number_id: {ELEVENLABS_PHONE_NUMBER_ID}")
        print(f"[Monitor]   to_number:             {RASHID_PHONE_NUMBER}")
        print(f"[Monitor]   dynamic_variables:")
        print(f"[Monitor]     summary:       {summary}")
        print(f"[Monitor]     email_subject: {subject}")
        print(f"[Monitor]     email_sender:  {sender}")
        print(f"[Monitor]     email_type:    {email_type}")

        try:
            response = _elevenlabs.conversational_ai.twilio.outbound_call(
                agent_id=ELEVENLABS_AGENT_ID,
                agent_phone_number_id=ELEVENLABS_PHONE_NUMBER_ID,
                to_number=RASHID_PHONE_NUMBER,
                conversation_initiation_client_data=ConversationInitiationClientDataRequestInput(
                    dynamic_variables={
                        "summary":       summary,
                        "email_subject": subject,
                        "email_sender":  sender,
                        "email_type":    email_type,
                    },
                ),
            )
            print(f"[Monitor] ElevenLabs API response (raw): {response!r}")
            # Try to extract any useful fields from the response object
            response_dict = {}
            for attr in ("call_sid", "id", "status", "conversation_id", "agent_id"):
                val = getattr(response, attr, None)
                if val is not None:
                    response_dict[attr] = val
            if response_dict:
                print(f"[Monitor] ElevenLabs API response (fields): {response_dict}")
            call_sid = response_dict.get("call_sid") or response_dict.get("id", "unknown")
            log_msg  = f"[Outbound call initiated] {call_label} — SID: {call_sid}"
            print(f"[Monitor] {log_msg}")
            _append_to_file("navigator", log_msg)

        except Exception:
            full_tb = traceback.format_exc()
            log_msg = f"[Outbound call failed] {call_label}"
            print(f"[Monitor] ERROR — {log_msg}")
            print(f"[Monitor] Full traceback:\n{full_tb}")
            _append_to_file("navigator", f"{log_msg} — see console for details")

    def _handle_email_followup(self, email: dict, email_type: str, name: str) -> None:
        """Route to the appropriate agent after the user says yes to an email notification."""
        from agents.insurance import run_insurance
        from agents.bills import run_bills
        from agents.appointment import run_appointment

        subject = email.get("subject", "this email")

        if email_type == "denial":
            speak(f"Okay, let me pull that up for you.")
            run_insurance(f"insurance denial email: {subject}")

        elif email_type == "bill":
            speak(f"Okay, let me go through that bill with you.")
            run_bills(f"medical bill email: {subject}")

        elif email_type in ("appointment_confirmation", "appointment_cancellation"):
            speak(f"Okay, let me handle that appointment.")
            run_appointment(f"appointment email: {subject}")

        elif email_type == "prescription":
            snippet = email.get("snippet") or email.get("body", "No details available.")
            speak(f"Here is what the email says: {snippet}")

        else:
            snippet = email.get("snippet") or email.get("body", "No details available.")
            speak(f"Here is a summary of the email: {snippet}")

    # ── Calendar monitor ──────────────────────────────────────────────────────

    def _check_calendar(self) -> None:
        events = _fetch_calendar_events()
        if not events:
            return

        profile = load_profile() or {}
        name = profile.get("name", "there")
        now = datetime.utcnow()

        for event in events:
            event_id = event.get("id", "")
            title = event.get("title", "your appointment")
            start_str = event.get("start_time", "")

            if not event_id or not start_str:
                continue

            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                start_dt = start_dt.replace(tzinfo=None)  # naive for comparison
            except Exception:
                continue

            seconds_until = (start_dt - now).total_seconds()

            # Cancellation / rescheduled detection
            status = event.get("status", "").lower()
            cancel_key = f"cancel:{event_id}"
            if status in ("cancelled", "rescheduled") and not self._reminders.contains(cancel_key):
                self._reminders.set(cancel_key)
                time_str = start_dt.strftime("%I:%M %p")
                _notify(
                    f"Hi {name}, it looks like your appointment {title} "
                    f"scheduled for {time_str} has been {status}. "
                    f"Want me to help you reschedule?",
                )
                continue

            # Reminder windows
            for window_seconds, window_key, window_label in APPOINTMENT_REMINDER_WINDOWS:
                reminder_key = f"{event_id}:{window_key}"
                if self._reminders.contains(reminder_key):
                    continue

                lower = window_seconds - 120   # ±2 min tolerance
                upper = window_seconds + 120

                if lower <= seconds_until <= upper:
                    self._reminders.set(reminder_key)
                    time_str = start_dt.strftime("%I:%M %p")
                    message = self._build_appointment_reminder(
                        name, title, time_str, window_label, window_key
                    )
                    _notify(message)
                    break  # only one reminder window per cycle per event

    def _build_appointment_reminder(
        self, name: str, title: str, time_str: str, label: str, window_key: str
    ) -> str:
        if window_key == "24h":
            return (
                f"Hi {name}, you have an appointment tomorrow at {time_str} "
                f"with {title}. Want me to tell you the details?"
            )
        elif window_key == "2h":
            return (
                f"Hi {name}, just a reminder you have an appointment "
                f"in 2 hours at {time_str} with {title}. "
                f"Do you need help getting ready?"
            )
        else:
            return (
                f"Hi {name}, your appointment with {title} "
                f"is in 30 minutes. Do you need anything before you go?"
            )

    # ── Medication reminders ──────────────────────────────────────────────────

    def _check_medication_reminders(self) -> None:
        profile = load_profile() or {}
        medications = profile.get("medications", [])
        if not medications or not isinstance(medications, list):
            return

        name = profile.get("name", "there")
        now = datetime.now()
        current_time_str = now.strftime("%I:%M %p").lstrip("0").lower()  # e.g. "8:00 am"
        current_hour_min = now.strftime("%H:%M")

        for med in medications:
            if not isinstance(med, dict):
                continue

            med_name = med.get("name", "your medication")
            scheduled_time = str(med.get("time", "")).lower().strip()
            with_food = med.get("with_food", False)

            if not scheduled_time:
                continue

            # Normalize scheduled_time to HH:MM for comparison
            normalized = self._normalize_time(scheduled_time)
            if not normalized:
                continue

            reminder_key = f"med:{med_name}:{normalized}:{now.strftime('%Y-%m-%d')}"
            if self._reminders.contains(reminder_key):
                continue

            if normalized == current_hour_min:
                self._reminders.set(reminder_key)
                food_note = " Make sure to take it with food." if with_food else ""
                _notify(
                    f"Hi {name}, it's time to take your {med_name}.{food_note}",
                    wait_for_response=False,
                )

    @staticmethod
    def _normalize_time(time_str: str) -> str | None:
        """
        Convert natural time strings to HH:MM (24-hour) for comparison.
        Handles: '8am', '8:00am', '8:00 am', 'morning' (→ 08:00),
                 'evening' (→ 18:00), 'night' (→ 21:00), 'noon' (→ 12:00).
        Returns None if unparseable.
        """
        s = time_str.lower().strip()

        aliases = {"morning": "08:00", "evening": "18:00", "night": "21:00",
                   "noon": "12:00", "afternoon": "14:00", "bedtime": "21:00"}
        if s in aliases:
            return aliases[s]

        try:
            for fmt in ("%I:%M%p", "%I:%M %p", "%I%p", "%H:%M"):
                try:
                    t = datetime.strptime(s, fmt)
                    return t.strftime("%H:%M")
                except ValueError:
                    continue
        except Exception:
            pass

        return None


# ── Global singleton ──────────────────────────────────────────────────────────

_monitor: NavigatorMonitor | None = None


def get_monitor() -> NavigatorMonitor:
    """Return the global monitor instance, creating it if necessary."""
    global _monitor
    if _monitor is None:
        _monitor = NavigatorMonitor()
    return _monitor


def start_monitor() -> NavigatorMonitor:
    """Create and start the background monitor. Called from main.py."""
    m = get_monitor()
    m.start()
    return m
