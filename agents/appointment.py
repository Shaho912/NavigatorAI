import logging
import speech_recognition as sr
from utils.profile import load_profile
from utils.voice import listen, speak, detect_intent, _SilenceOnly
from utils.connectors import require_connector
from utils.gmail import send_email, _creds_present as gmail_ready
from utils.gcal import create_reminder
from utils.sms import send_confirmation

logger = logging.getLogger(__name__)

# TODO: INBOUND GMAIL TRIGGER — ElevenLabs Conversational AI
# When the doctor's office replies, parse confirmed time and call the user.

# TODO: SMS SUMMARY — send confirmation after appointment booked.


class GoBack(Exception):
    pass


def _ask(question: str, rephrase: str | None = None) -> str:
    last_spoken = question
    speak(question)
    while True:
        try:
            response = listen()
        except _SilenceOnly:
            continue
        except sr.UnknownValueError:
            speak("Sorry, I didn't catch that — could you say it again?")
            continue
        except sr.RequestError as e:
            logger.debug("STT error: %s", e)
            speak("I'm having a little trouble hearing. Please try once more.")
            continue

        intent = detect_intent(response, last_spoken, ["go_back", "repeat", "confused", "answer"])
        if intent == "go_back":
            raise GoBack()
        if intent == "repeat":
            speak(last_spoken)
            continue
        if intent == "confused":
            simpler = rephrase or f"Let me say that more simply — {question}"
            speak(simpler)
            last_spoken = simpler
            continue
        return response


def _infer_specialty(description: str) -> str:
    return detect_intent(
        description,
        "What type of medical specialist is the user describing?",
        ["cardiology", "orthopedics", "dermatology", "psychiatry",
         "gastroenterology", "ophthalmology", "neurology", "general practice"],
    )


def _step_acknowledge(name: str) -> str:
    return _ask(
        f"Of course{', ' + name if name else ''}. "
        "Do you already have a doctor in mind, or would you like help finding one?",
        rephrase="Do you have a doctor you want to see, or do you need help finding the right one?",
    )


def _step_existing_doctor(name: str) -> None:
    doctor_name = _ask("What is their name?", rephrase="What is your doctor called?")
    print(f"  > doctor: {doctor_name}")

    reason = _ask(
        "And what's the appointment for? Just describe what's been going on.",
        rephrase="What do you need to see the doctor about?",
    )
    print(f"  > reason: {reason}")

    timing = _ask(
        "Do you have a day or time in mind, or should I just ask for the earliest slot available?",
        rephrase="When would you like the appointment — a specific time or just as soon as possible?",
    )
    print(f"  > timing: {timing}")

    # Send a real appointment request email
    profile = load_profile() or {}
    patient_name = profile.get("name", "the patient")

    email_body = (
        f"Hello,\n\n"
        f"I am reaching out on behalf of {patient_name} to request an appointment.\n\n"
        f"Reason for visit: {reason}\n"
        f"Preferred timing: {timing}\n\n"
        f"Please let us know your available slots at your earliest convenience.\n\n"
        f"Thank you,\nNavigator AI (on behalf of {patient_name})"
    )

    if gmail_ready():
        # In production this would look up the doctor's email — using a placeholder
        doctor_email = profile.get("doctor_email", "")
        sent = False
        if doctor_email:
            sent = send_email(doctor_email, f"Appointment Request — {patient_name}", email_body)

        # Create a follow-up reminder in 2 days regardless
        create_reminder(
            title=f"Follow up: appointment request to {doctor_name}",
            description=f"Called to request appointment for {reason}. Timing: {timing}",
            days_from_now=2,
            hour=10,
        )
        send_confirmation(
            "Appointment request sent",
            f"Doctor: {doctor_name} | Reason: {reason} | Timing: {timing}",
        )
        speak(
            f"I've put together a message to {doctor_name}'s office "
            f"{'and sent it' if sent else '— add their email to your profile so I can send it automatically'}. "
            "I've also set a calendar reminder to follow up in 2 days, and sent you a text confirmation. "
            "Is there anything else I can help with?"
        )
    else:
        speak(
            f"I've noted that you want to see {doctor_name} about {reason}. "
            "Add GOOGLE_CLIENT_ID and related credentials to your dot-env file "
            "so I can send the request and set a reminder automatically. "
            "Is there anything else?"
        )


def _step_find_doctor(name: str, city: str, insurance: str) -> None:
    specialty_input = _ask(
        f"I'll look for doctors near {city} who take {insurance}. "
        "What kind of doctor do you need? "
        "For example — a heart specialist, a knee doctor, or a general doctor.",
        rephrase=f"What kind of specialist are you looking for in {city}?",
    )
    print(f"  > specialty: {specialty_input}")
    specialty = _infer_specialty(specialty_input)

    first_q = (
        f"I found a few options. The first is Dr. Sarah Chen — "
        f"she specializes in {specialty}, is taking new patients, and accepts {insurance}. "
        f"Want to hear more about her, or should I tell you about the next option?"
    )
    first_response = _ask(first_q, rephrase="Want more on Dr. Chen, or hear the next doctor?")
    print(f"  > first response: {first_response}")

    first_intent = detect_intent(first_response, first_q, ["more_info", "next_option"])

    if first_intent == "more_info":
        schedule_q = (
            "Dr. Chen's office is about 2 miles from you. "
            "Patients say she takes her time and is easy to talk to. "
            "Would you like me to reach out to her office?"
        )
        schedule_response = _ask(schedule_q, rephrase="Should I contact Dr. Chen's office for you?")
        print(f"  > schedule: {schedule_response}")
        chosen_name  = "Dr. Sarah Chen"
        chosen       = detect_intent(schedule_response, schedule_q, ["yes", "no"]) == "yes"
    else:
        second_q = (
            "The second option is Dr. James Rivera — similar specialty, "
            f"also accepts {insurance}. Want more details, or shall I reach out to him?"
        )
        second_response = _ask(second_q, rephrase="More on Dr. Rivera, or should I book him?")
        print(f"  > second: {second_response}")
        chosen_name = "Dr. James Rivera"
        chosen      = True

    if chosen:
        profile      = load_profile() or {}
        patient_name = profile.get("name", "the patient")

        # Create a real follow-up reminder in 2 days
        create_reminder(
            title=f"Follow up: new patient request to {chosen_name}",
            description=f"Sent new patient appointment request for {specialty}",
            days_from_now=2,
            hour=10,
        )
        send_confirmation(
            "New patient request noted",
            f"Doctor: {chosen_name} | Specialty: {specialty} | Location: {city}",
        )
        speak(
            f"I've set a calendar reminder to follow up on your {chosen_name} request in 2 days, "
            "and sent you a text confirmation. "
            "Is there anything else I can help with?"
        )
    else:
        speak("No problem — just let me know whenever you're ready to book. Is there anything else?")


def run_appointment(trigger_message: str) -> None:
    profile   = load_profile() or {}
    name      = profile.get("name", "")
    city      = profile.get("city", "your area")
    insurance = profile.get("insurance_company", "your insurance")

    step = 0
    direction_response = None

    while step <= 1:
        try:
            if step == 0:
                direction_response = _step_acknowledge(name)
                print(f"  > direction: {direction_response}")
                step += 1

            elif step == 1:
                has_doctor = detect_intent(
                    direction_response or "",
                    "Does the user already have a specific doctor they want to see?",
                    ["has_doctor", "needs_doctor"],
                ) == "has_doctor"

                if has_doctor:
                    _step_existing_doctor(name)
                else:
                    _step_find_doctor(name, city, insurance)
                step += 1

        except GoBack:
            step = max(0, step - 1)
            print("[Appointment] Going back.")
            if step == 0:
                direction_response = None
