"""
agents/router.py — Intent-based routing for Navigator AI.

Uses Claude to classify any user utterance into a destination agent or
system action. All routing goes through detect_intent() — no keyword lists.

Intent map:
  insurance         → agents/insurance.py
  appointment       → agents/appointment.py
  medication        → agents/medication.py
  bills             → agents/bills.py
  documents         → agents/documents.py
  check_email       → monitor.check_emails_now()
  check_calendar    → monitor.check_appointments_now()
  connector_status  → utils/connectors.speak_access_summary()
  silence           → mute Navigator immediately
  exit              → clean shutdown
  unknown           → polite fallback
"""

from utils.profile import load_profile
from utils.voice import detect_intent

# TODO: INBOUND SMS/MESSAGE TRIGGER
# route() currently accepts voice-transcribed text. Extend to also accept
# messages from inbound SMS (e.g. Twilio webhook) or other text channels.
# The message source should be passed as an optional parameter so agents
# can respond via the same channel (e.g. reply by SMS if triggered by SMS).
# Example signature: route(message: str, source: str = "voice")
# Sources: "voice" | "sms" | "email" | "app"

AGENTS = [
    "insurance",        # denied claim, appeal, coverage, EOB
    "appointment",      # schedule, cancel, find a doctor
    "medication",       # reminders, refills, prescriptions
    "bills",            # what I owe, payment, statement
    "documents",        # letter, document, file, email attachment
    "check_email",      # any new health emails, check my inbox
    "check_calendar",   # upcoming appointments, what's on my calendar
    "connector_status", # what do you have access to, check connections
    "silence",          # shut up, stop, be quiet, that's enough
    "exit",             # goodbye, I'm done, close, exit
    "unknown",          # anything else
]


def route(message: str) -> str:
    """
    Use Claude to route the user's message to the correct agent or action.
    Session context from voice.py is automatically included in detect_intent.
    """
    load_profile()  # ensures profile is warm for downstream agents

    agent = detect_intent(
        response=message,
        context=(
            "A user is speaking to Navigator AI, a voice health assistant. "
            "Based on what they said, what do they need? "
            "Choose the most specific matching intent. "
            "Use 'documents' for letters, emails about specific documents, "
            "or anything that arrived by mail. "
            "Use 'check_email' only when the user is asking to check for new emails. "
            "Use 'check_calendar' only when asking about upcoming appointments or schedule. "
            "Use 'connector_status' when asking what Navigator has access to. "
            "Use 'silence' when the user wants Navigator to stop talking. "
            "Use 'exit' only when clearly saying goodbye or wanting to end the session."
        ),
        intents=AGENTS,
    )

    print(f"[Router] → {agent}")
    return agent
