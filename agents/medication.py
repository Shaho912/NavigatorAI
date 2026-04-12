# TODO: OUTBOUND CALL — ElevenLabs Conversational AI
# At the scheduled reminder time, initiate an outbound call to remind the user.

import logging
import speech_recognition as sr
from utils.profile import load_profile, save_profile
from utils.voice import listen, speak, detect_intent, _SilenceOnly
from utils.connectors import require_connector
from utils.gcal import create_reminder
from utils.sms import send_confirmation

logger = logging.getLogger(__name__)

# Time-string → hour mapping for calendar reminders
_TIME_TO_HOUR = {
    "morning": 8, "am": 8, "8am": 8, "9am": 9, "10am": 10,
    "noon": 12, "afternoon": 14, "evening": 18, "night": 20, "pm": 20,
    "8pm": 20, "9pm": 21, "10pm": 22,
}


class GoBack(Exception):
    pass


class StartOver(Exception):
    pass


def _ask(question: str, rephrase: str | None = None, allow_start_over: bool = False) -> str:
    last_spoken = question
    speak(question)
    while True:
        try:
            response = listen()
        except _SilenceOnly:
            continue
        except sr.UnknownValueError:
            speak("Didn't quite get that — could you say it again?")
            continue
        except sr.RequestError as e:
            print(f"STT error: {e}")
            speak("Having a little trouble hearing you. Please try once more.")
            continue

        intents = (
            ["start_over", "go_back", "repeat", "confused", "answer"]
            if allow_start_over
            else ["go_back", "repeat", "confused", "answer"]
        )
        intent = detect_intent(response, last_spoken, intents)

        if intent == "start_over":
            raise StartOver()
        if intent == "go_back":
            raise GoBack()
        if intent == "repeat":
            speak(last_spoken)
            continue
        if intent == "confused":
            simpler = rephrase or f"Let me put that more simply — {question}"
            speak(simpler)
            last_spoken = simpler
            continue

        return response


def _collect_one_medication() -> dict:
    med  = {}
    step = 0

    while step <= 3:
        try:
            if step == 0:
                med["name"] = _ask(
                    "What's the name of the medication? Just the name is fine.",
                    rephrase="What is this medication called?",
                    allow_start_over=True,
                )
                print(f"  > medication: {med['name']}")
                step += 1

            elif step == 1:
                med["frequency"] = _ask(
                    f"How often do you take {med['name']}? Once a day, twice a day?",
                    rephrase=f"How many times per day do you take {med['name']}?",
                    allow_start_over=True,
                )
                print(f"  > frequency: {med['frequency']}")
                step += 1

            elif step == 2:
                med["time"] = _ask(
                    "What time works best for the reminder? Like 8am, or morning and evening.",
                    rephrase="When in the day should I remind you?",
                    allow_start_over=True,
                )
                print(f"  > time: {med['time']}")
                step += 1

            elif step == 3:
                food_q = "Should you take it with food, or does that not matter?"
                food_r = _ask(
                    food_q,
                    rephrase="Do you need to eat something when you take this?",
                    allow_start_over=True,
                )
                print(f"  > with food: {food_r}")
                med["with_food"] = detect_intent(food_r, food_q, ["with_food", "without_food"]) == "with_food"
                step += 1

        except GoBack:
            step = max(0, step - 1)
            print("[Medication] Going back one step.")

    return med


def _schedule_medication_reminder(med: dict) -> None:
    """Create a Google Calendar reminder and send an SMS confirmation."""
    time_str = med.get("time", "morning").lower()
    hour = 8
    for key, val in _TIME_TO_HOUR.items():
        if key in time_str:
            hour = val
            break

    food_note = " (take with food)" if med.get("with_food") else ""
    create_reminder(
        title=f"Take {med['name']}{food_note}",
        description=f"Medication reminder — {med['name']}, {med.get('frequency', 'daily')}",
        days_from_now=1,
        hour=hour,
    )
    send_confirmation(
        "Medication reminder set",
        f"{med['name']} at {med['time']}{food_note} — {med.get('frequency', 'daily')}",
    )


def _confirm_medication(med: dict) -> None:
    food_note = " — and I'll remind you to take it with food" if med["with_food"] else ""
    speak(
        f"Perfect. I'll set a daily reminder for {med['name']} at {med['time']}{food_note}."
    )
    _schedule_medication_reminder(med)


def _save_medication(med: dict) -> None:
    profile     = load_profile() or {}
    medications = profile.get("medications") or []
    if isinstance(medications, str):
        medications = [medications] if medications else []
    medications.append(med)
    profile["medications"] = medications
    save_profile(profile)


def _build_summary(medications: list[dict]) -> str:
    parts = []
    for med in medications:
        food_note = " with food" if med.get("with_food") else ""
        parts.append(f"{med['name']} at {med['time']}{food_note}")
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def run_medication(trigger_message: str) -> None:
    profile = load_profile() or {}
    name    = profile.get("name", "")

    speak(
        f"Let's get your medication reminders set up{', ' + name if name else ''}. "
        "Tell me the name of your first medication."
    )

    added = []

    while True:
        try:
            med = _collect_one_medication()
        except StartOver:
            added.clear()
            print("[Medication] Starting over.")
            speak("No problem — let's start fresh. Tell me the name of your first medication.")
            continue

        _confirm_medication(med)
        _save_medication(med)
        added.append(med)

        another_q = "Got it. Do you have another medication to add, or is that all of them?"
        try:
            another = _ask(another_q, rephrase="Any more medications, or are we all done?")
        except (GoBack, StartOver):
            added.pop()
            continue

        if detect_intent(another, another_q, ["yes", "no"]) == "yes":
            speak("Sure, go ahead.")
            continue
        else:
            break

    if added:
        summary   = _build_summary(added)
        summary_q = (
            f"You're all set{', ' + name if name else ''}. "
            f"I have reminders ready for {summary}. "
            "Does that sound right?"
        )
        confirm = _ask(summary_q, rephrase="Does that summary sound correct?")
        print(f"  > confirmation: {confirm}")

        if detect_intent(confirm, summary_q, ["repeat", "confirm"]) == "repeat":
            speak(f"Reminders set for {summary}.")

    speak("Your reminders are set in Google Calendar and I've sent you a text summary. Is there anything else I can help with?")
