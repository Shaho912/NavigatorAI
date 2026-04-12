"""
main.py — Navigator AI entry point.

Startup sequence:
  1. Start background monitor thread
  2. Speak welcome
  3. Run onboarding (skipped if profile exists)
  4. Speak connector status report on first run
  5. Enter main voice loop

Main loop:
  - Listens for user speech
  - Routes through agents/router.py
  - Dispatches to the correct agent
  - Wraps every agent call in try/except — nothing can crash the app
  - Handles silence, muting, connector checks, and clean exit
"""

import traceback
import speech_recognition as sr

from ui.server import start_ui_server
from utils.voice import clear_session_file
from agents.onboarding import run_onboarding
from agents.monitor import start_monitor
from agents.router import route
from utils.profile import load_profile, profile_exists
from utils.voice import (
    listen, speak, _SilenceOnly,
    _conversation_active, _muted,
)
from utils.connectors import (
    speak_connector_status_report,
    speak_access_summary,
    refresh_all_connectors,
)


def _dispatch(agent: str, user_input: str, monitor) -> bool:
    """
    Run the agent matching `agent`. Returns True to exit the main loop.
    All exceptions are caught here — nothing propagates to the loop.
    """
    try:
        if agent == "exit":
            return True

        elif agent == "silence":
            _muted.set()

        elif agent == "check_email":
            speak("Give me a moment to check your health emails.")
            monitor.check_emails_now()

        elif agent == "check_calendar":
            speak("Let me pull up your calendar now.")
            monitor.check_appointments_now()

        elif agent == "connector_status":
            speak_access_summary()

        elif agent == "insurance":
            from agents.insurance import run_insurance
            run_insurance(user_input)

        elif agent == "appointment":
            from agents.appointment import run_appointment
            run_appointment(user_input)

        elif agent == "medication":
            from agents.medication import run_medication
            run_medication(user_input)

        elif agent == "bills":
            from agents.bills import run_bills
            run_bills(user_input)

        elif agent == "documents":
            from agents.documents import run_documents
            run_documents(user_input)

        else:
            speak(
                "I want to help, but I'm not quite sure what you need. "
                "You can ask me about insurance, appointments, medications, "
                "bills, or a document you received."
            )

    except Exception:
        print(traceback.format_exc())
        speak(
            "Something went wrong on my end — sorry about that. "
            "Everything's fine, just let me know what you need."
        )

    return False


def main():
    clear_session_file()
    start_ui_server(port=5000)
    monitor = start_monitor()

    speak("Welcome to Navigator AI.")

    first_run = not profile_exists()
    run_onboarding()

    if first_run:
        speak_connector_status_report()

    profile = load_profile() or {}
    name    = profile.get("name", "")
    print(f"\nLogged in as: {name or 'User'}\n")

    while True:
        _conversation_active.clear()

        try:
            user_input = listen()
        except _SilenceOnly:
            continue
        except sr.UnknownValueError:
            speak("Sorry, I didn't quite catch that.")
            continue
        except sr.RequestError as e:
            print(f"STT error: {e}")
            continue

        _conversation_active.set()
        print(f"You said: {user_input}")

        # Connector refresh command — bypass router for speed
        lower = user_input.lower()
        if "check my connections" in lower or "reconnect" in lower:
            speak("Refreshing your connections now.")
            refresh_all_connectors()
            speak_access_summary()
            continue

        try:
            agent = route(user_input)
        except Exception:
            speak("I had a bit of trouble with that — could you say it again?")
            continue

        should_exit = _dispatch(agent, user_input, monitor)
        if should_exit:
            break

    _conversation_active.clear()
    monitor.stop()
    farewell = f"Goodbye, take care{' ' + name if name else ''}."
    speak(farewell)


if __name__ == "__main__":
    main()
