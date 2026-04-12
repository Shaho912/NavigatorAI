import logging
import speech_recognition as sr
from utils.profile import load_profile
from utils.voice import listen, speak, detect_intent, _SilenceOnly
from utils.connectors import require_connector
from utils.gmail import search_emails, send_email, _creds_present as gmail_ready
from utils.gcal import create_reminder
from utils.sms import send_confirmation

logger = logging.getLogger(__name__)

MOCK_DENIAL_LETTER = (
    "Dear member, your claim for MRI services dated March 1st 2026 "
    "has been denied. Reason: not medically necessary. "
    "You have 30 days to appeal. Reference number 12345."
)


def _ask(question: str, rephrase: str | None = None) -> str:
    last_spoken = question
    speak(question)
    while True:
        try:
            response = listen()
        except _SilenceOnly:
            continue
        except sr.UnknownValueError:
            speak("I didn't quite catch that — could you say it again?")
            continue
        except sr.RequestError as e:
            logger.debug("STT error: %s", e)
            speak("Having a little trouble hearing you. Please try once more.")
            continue

        intent = detect_intent(response, last_spoken, ["repeat", "confused", "answer"])
        if intent == "repeat":
            speak(last_spoken)
            continue
        if intent == "confused":
            simpler = rephrase or f"Let me say that differently — {question}"
            speak(simpler)
            last_spoken = simpler
            continue
        return response


def _find_insurance_email() -> dict | None:
    """Search Gmail for recent insurance denial or claim emails."""
    if not gmail_ready():
        return None
    try:
        emails = search_emails(
            "insurance (denial OR denied OR claim OR appeal OR authorization) newer_than:90d",
            max_results=5,
        )
        return emails[0] if emails else None
    except Exception as exc:
        logger.debug("Insurance email search failed: %s", exc)
        return None


def run_insurance(trigger_message: str) -> None:
    profile = load_profile() or {}
    name    = profile.get("name", "")
    insurer = profile.get("insurance_company", "your insurance company")

    speak(f"Alright{', ' + name if name else ''}, let's work through this together.")

    # Step 1 — find the document
    step1        = "Do you have a letter or email about this, or are you going off memory?"
    doc_response = _ask(step1, rephrase="Is there a letter or email you received about this?")
    print(f"  > {doc_response}")

    doc_type = detect_intent(doc_response, step1, ["email", "physical_letter", "memory", "other"])

    denial_content = MOCK_DENIAL_LETTER  # default fallback

    if doc_type == "email":
        speak("Let me check your email for anything from your insurance company.")
        email = _find_insurance_email()
        if email:
            denial_content = email.get("body") or email.get("snippet") or MOCK_DENIAL_LETTER
            subject        = email.get("subject", "insurance letter")
            speak(f"I found something — subject: {subject}. Give me a moment to read it.")
        else:
            speak("I couldn't find a recent insurance email. I'll work from what I have.")

    elif doc_type == "physical_letter":
        follow_up    = "Can you get that letter nearby? If someone can send it to your email I can read it for you."
        doc_location = _ask(follow_up, rephrase="Is there a way to get that letter into your email so I can read it?")
        print(f"  > {doc_location}")

    elif doc_type == "memory":
        speak("That's fine — let's work through it together based on what you know.")

    # Step 2 — summarise
    print(f"\n[Insurance] Content:\n{denial_content[:300]}\n")
    speak("Give me a moment to go through it.")

    # Step 3 — plain English explanation
    explanation = (
        f"Okay, here is what it says in plain terms. "
        f"{insurer} denied your MRI claim — they said it wasn't medically necessary. "
        f"The good news is you have 30 days to appeal that decision. "
        f"Your reference number is 12345. "
        f"Would you like me to help you appeal this?"
    )
    appeal_response = _ask(
        explanation,
        rephrase=(
            "Your insurance said no to the MRI. You can push back within 30 days. "
            "Want me to help write an appeal?"
        ),
    )
    print(f"  > {appeal_response}")

    wants_appeal = detect_intent(
        appeal_response,
        "Does the user want to appeal the insurance denial?",
        ["yes", "no"],
    ) == "yes"

    if wants_appeal:
        doctor_name = _ask(
            "To write the appeal I just need your doctor's name. What is it?",
            rephrase="What is your doctor called?",
        )
        print(f"  > {doctor_name}")

        confirm = _ask(
            f"Got it. I'm putting together a letter to {insurer} asking them to take another look. "
            f"I'll also set a reminder in 5 days so we can follow up. Does that work for you?",
            rephrase="Should I send the appeal and set a reminder to follow up in 5 days?",
        )
        print(f"  > {confirm}")

        # Send the appeal email
        appeal_body = (
            f"To Whom It May Concern at {insurer},\n\n"
            f"I am writing to formally appeal the denial of my MRI claim (Reference: 12345), "
            f"dated March 1st 2026. My treating physician, {doctor_name}, has determined this "
            f"procedure to be medically necessary for my ongoing care.\n\n"
            f"I respectfully request a full review of this decision. Please contact me at your "
            f"earliest convenience to discuss the next steps.\n\n"
            f"Sincerely,\n{name or 'The Patient'}"
        )

        email_sent = False
        insurer_email = profile.get("insurance_email", "")
        if gmail_ready() and insurer_email:
            email_sent = send_email(insurer_email, f"Appeal: Claim Denial Reference 12345", appeal_body)

        # Create a 5-day follow-up reminder
        create_reminder(
            title=f"Follow up: insurance appeal to {insurer}",
            description="Check on appeal status for MRI claim denial (Ref 12345)",
            days_from_now=5,
            hour=10,
        )
        send_confirmation(
            "Insurance appeal drafted",
            f"Insurer: {insurer} | Doctor: {doctor_name} | Ref: 12345 | Follow-up reminder: 5 days",
        )

        speak(
            f"We're all set{', ' + name if name else ''}. "
            f"{'Your appeal letter has been sent. ' if email_sent else 'Your appeal letter is ready — add your insurer email to your profile to send it automatically. '}"
            "I've set a calendar reminder to follow up in 5 days and sent you a text summary. "
            "Is there anything else I can help with?"
        )
    else:
        speak(
            "No problem at all. If you change your mind the offer stands — "
            "just say you want help with your insurance and we'll pick up right here. "
            "Is there anything else I can do for you?"
        )
