"""
Connector status system for Navigator AI.

Each connector is checked by inspecting environment tokens/keys and optionally
probing the service with a lightweight request. Checks are cached for the session
so repeated calls don't cause extra latency.

Adding a real connector:
  1. Add its token env var(s) to _TOKEN_VARS below.
  2. Add a probe function to _PROBES below (optional but recommended).
  3. The rest of the system picks it up automatically.
"""

import os
import speech_recognition as sr
from dotenv import load_dotenv

load_dotenv()

# ── Connector definitions ─────────────────────────────────────────────────────

# Friendly name → environment variable(s) that must be non-empty for the
# connector to be considered "configured". All listed vars must be present.
_TOKEN_VARS: dict[str, list[str]] = {
    "Gmail":            ["COMPOSIO_API_KEY", "COMPOSIO_USER_ID"],
    "Google Drive":     ["GOOGLE_OAUTH_TOKEN"],
    "Google Calendar":  ["GOOGLE_OAUTH_TOKEN"],
    "Google Docs":      ["GOOGLE_OAUTH_TOKEN"],
    "Messages":         ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"],
    "Phone":            ["ELEVENLABS_AGENT_ID"],
}

# Optional lightweight probe functions per connector.
# A probe takes no arguments and raises an exception if the connection is broken.
_PROBES: dict[str, callable] = {
    # Example:
    # "Gmail": _probe_gmail,
}

# Session-level cache: None = unchecked, True/False = last known status
_status_cache: dict[str, bool] = {}

ALL_CONNECTORS = list(_TOKEN_VARS.keys())


# ── Core check ────────────────────────────────────────────────────────────────

def check_connector(connector_name: str) -> bool:
    """
    Return True if the connector appears to be authorized and reachable.
    Result is cached for the session. Safe to call at any time — never raises.
    """
    if connector_name in _status_cache:
        return _status_cache[connector_name]

    try:
        # Step 1: all required token env vars must be non-empty
        required_vars = _TOKEN_VARS.get(connector_name, [])
        for var in required_vars:
            if not os.getenv(var, "").strip():
                _status_cache[connector_name] = False
                return False

        # Step 2: optional live probe
        probe = _PROBES.get(connector_name)
        if probe:
            probe()

        _status_cache[connector_name] = True
        return True

    except Exception:
        _status_cache[connector_name] = False
        return False


def invalidate_connector(connector_name: str) -> None:
    """Remove a connector from the cache so the next check re-probes it."""
    _status_cache.pop(connector_name, None)


def refresh_all_connectors() -> None:
    """Clear the entire cache and re-probe all connectors."""
    _status_cache.clear()
    for name in ALL_CONNECTORS:
        check_connector(name)


def get_connected_connectors() -> list[str]:
    """Return names of all connectors that are currently connected."""
    return [c for c in ALL_CONNECTORS if check_connector(c)]


def get_disconnected_connectors() -> list[str]:
    """Return names of all connectors that are not currently connected."""
    return [c for c in ALL_CONNECTORS if not check_connector(c)]


# ── Require gate ──────────────────────────────────────────────────────────────

def require_connector(connector_name: str, action_description: str) -> bool:
    """
    Gate an action on connector availability.

    If connected: returns True immediately — caller proceeds normally.
    If disconnected: speaks an explanation, offers a reminder, returns False.
    The caller should skip the action when False is returned.

    Usage:
        if not require_connector("Gmail", "read your emails"):
            return  # or offer an alternative
        # ... do the Gmail action
    """
    # Import here to avoid circular import (connectors ← voice ← connectors)
    from utils.voice import speak, listen, detect_intent, _SilenceOnly

    if check_connector(connector_name):
        return True

    speak(
        f"To {action_description} I need access to your {connector_name}. "
        f"You can connect it in the Navigator settings. "
        f"Would you like me to remind you to do that later?"
    )

    while True:
        try:
            response = listen()
        except _SilenceOnly:
            continue
        except sr.UnknownValueError:
            speak("Sorry, I didn't catch that — yes or no?")
            continue
        except sr.RequestError:
            break

        wants_reminder = detect_intent(
            response,
            f"Should Navigator set a reminder to connect {connector_name}?",
            ["yes", "no"],
        ) == "yes"

        if wants_reminder:
            speak(
                f"Got it. I will remind you to connect your {connector_name} "
                f"the next time we talk. I will skip that step for now."
            )
        else:
            speak(
                f"No problem. You can connect {connector_name} anytime in settings. "
                f"I will do what I can without it."
            )
        break

    return False


def check_mid_session(connector_name: str) -> bool:
    """
    Re-probe a connector that was previously connected.
    If it is now disconnected, speak a warning and return False.
    """
    from utils.voice import speak

    was_connected = _status_cache.get(connector_name, False)
    invalidate_connector(connector_name)
    still_connected = check_connector(connector_name)

    if was_connected and not still_connected:
        speak(
            f"It looks like I lost access to your {connector_name}. "
            f"Some features may not work until you reconnect it. "
            f"Want me to remind you to fix that?"
        )
        # Delegate the yes/no to the caller if desired; here we just warn
        return False

    return still_connected


# ── Startup report ────────────────────────────────────────────────────────────

def speak_connector_status_report() -> None:
    """
    Speak a plain-English report of all connectors.
    Called once after onboarding completes.
    """
    from utils.voice import speak, listen, detect_intent, _SilenceOnly

    speak("I want to make sure I can help you fully. Let me check what I have access to.")

    connected = []
    disconnected = []

    for connector in ALL_CONNECTORS:
        if check_connector(connector):
            connected.append(connector)
        else:
            disconnected.append(connector)

    # Speak connected ones first
    for name in connected:
        speak(f"I have access to your {name} — great.")

    # Speak disconnected ones with context
    _missing_context = {
        "Gmail":           "read and send health emails on your behalf",
        "Google Drive":    "save your documents and appeal letters",
        "Google Calendar": "set reminders and track appointments",
        "Google Docs":     "create and edit documents for you",
        "Messages":        "send you text message summaries",
        "Phone":           "call you for medication reminders",
    }

    for name in disconnected:
        context = _missing_context.get(name, "help you with certain tasks")
        speak(
            f"I do not have access to your {name}. "
            f"I will need that to {context}. "
            f"You can connect it in settings."
        )

    if not disconnected:
        speak("Everything looks good — I am fully connected.")
        return

    speak("Want me to walk you through connecting the missing ones?")

    while True:
        try:
            response = listen()
        except _SilenceOnly:
            continue
        except (sr.UnknownValueError, sr.RequestError):
            break

        wants_walkthrough = detect_intent(
            response,
            "Does the user want step-by-step instructions to connect missing services?",
            ["yes", "no"],
        ) == "yes"

        if wants_walkthrough:
            for name in disconnected:
                _speak_connection_instructions(name)
        else:
            speak(
                "No problem, you can connect them anytime in settings. "
                "I will work with what I have for now."
            )
        break


def _speak_connection_instructions(connector_name: str) -> None:
    """Speak brief connection instructions for a specific connector."""
    from utils.voice import speak

    instructions = {
        "Gmail": (
            "To connect Gmail, open the Navigator settings, tap Add Connection, "
            "choose Google, and sign in with the Gmail account you want to use."
        ),
        "Google Drive": (
            "To connect Google Drive, open the Navigator settings, tap Add Connection, "
            "choose Google Drive, and grant access when prompted."
        ),
        "Google Calendar": (
            "To connect Google Calendar, open the Navigator settings, tap Add Connection, "
            "choose Google Calendar, and sign in."
        ),
        "Google Docs": (
            "To connect Google Docs, open the Navigator settings, tap Add Connection, "
            "choose Google Docs, and grant access."
        ),
        "Messages": (
            "To enable text messages, open the Navigator settings and enter your phone number "
            "under SMS Notifications."
        ),
        "Phone": (
            "To enable phone call reminders, open the Navigator settings and add your phone number "
            "under Call Reminders."
        ),
    }
    speak(instructions.get(connector_name, f"To connect {connector_name}, open the Navigator settings."))


def speak_access_summary() -> None:
    """
    Speak a plain-English summary of what Navigator currently has access to.
    Triggered by the voice command 'what do you have access to'.
    """
    from utils.voice import speak

    connected = get_connected_connectors()
    disconnected = get_disconnected_connectors()

    if connected:
        names = ", ".join(connected)
        speak(f"I currently have access to: {names}.")
    else:
        speak("I am not connected to any external services right now.")

    if disconnected:
        names = ", ".join(disconnected)
        speak(
            f"I do not have access to: {names}. "
            f"You can connect these in the Navigator settings."
        )
