import logging
import speech_recognition as sr
from dotenv import load_dotenv

from utils.profile import load_profile
from utils.voice import listen, speak, detect_intent, _SilenceOnly
from utils.connectors import require_connector
from utils.gmail import search_emails, _creds_present as gmail_ready
from utils.sms import send_confirmation

load_dotenv()
logger = logging.getLogger(__name__)

DRIVE_FOLDER = "Navigator Documents"


# ── Exceptions ────────────────────────────────────────────────────────────────

class GoBack(Exception):
    pass


# ── Core _ask helper ──────────────────────────────────────────────────────────

def _ask(question: str, rephrase: str | None = None) -> str:
    last_spoken = question
    speak(question)
    while True:
        try:
            response = listen()
        except _SilenceOnly:
            continue
        except sr.UnknownValueError:
            speak("Sorry, I didn't quite catch that — could you say it again?")
            continue
        except sr.RequestError as e:
            logger.debug("STT error: %s", e)
            speak("Having a little trouble hearing you. Please try once more.")
            continue

        intent = detect_intent(
            response, last_spoken,
            ["go_back", "repeat", "confused", "answer"],
        )
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


# ── Gmail search ──────────────────────────────────────────────────────────────

def _search_gmail(hint: str) -> dict | None:
    """Search real Gmail for a document matching the user's hint."""
    if not require_connector("Gmail", "search your emails for this document"):
        return None
    if not gmail_ready():
        speak("Gmail credentials are not set up yet. Add GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN to your dot-env file.")
        return None
    try:
        emails = search_emails(hint, max_results=5)
        return emails[0] if emails else None
    except Exception as exc:
        logger.debug("Gmail search failed: %s", exc)
        return None


# ── Claude document analysis ──────────────────────────────────────────────────

def _summarize_document(content: str) -> str:
    try:
        import anthropic, os
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=(
                "You summarize health documents for a voice assistant. "
                "The user cannot see the document — they will only hear your summary spoken aloud. "
                "Use plain, simple language. "
                "Never say 'as you can see', 'the document shows', 'notice that', or any phrase "
                "that implies the user can read or view anything. "
                "Speak directly: 'This letter says...', 'They are asking you to...', 'You owe...' "
                "Keep the summary under 100 words."
            ),
            messages=[{"role": "user", "content": f"Summarize this document:\n\n{content}"}],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        logger.debug("Claude summarize failed: %s", exc)
        return "I was not able to fully read this document. You may want to have someone go through it with you."


def _classify_document(content: str) -> str:
    try:
        import anthropic, os
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            system=(
                "Classify this health document into exactly one category: "
                "denial, bill, appointment, prescription, unknown. "
                "Reply with only the label."
            ),
            messages=[{"role": "user", "content": content[:1000]}],
        )
        raw = msg.content[0].text.strip().lower()
        for label in ("denial", "bill", "appointment", "prescription"):
            if label in raw:
                return label
        return "unknown"
    except Exception:
        return "unknown"


# ── Document action router ────────────────────────────────────────────────────

def _route_document_action(doc_type: str, content: str) -> None:
    if doc_type == "denial":
        speak("This looks like a denial letter. Let me help you figure out what to do with it.")
        from agents.insurance import run_insurance
        run_insurance(f"document: denial letter — {content[:200]}")

    elif doc_type == "bill":
        speak("This looks like a medical bill. Let me go through it with you.")
        from agents.bills import run_bills
        run_bills(f"document: medical bill — {content[:200]}")

    elif doc_type == "appointment":
        speak("This looks like an appointment letter. Let me handle that for you.")
        from agents.appointment import run_appointment
        run_appointment(f"document: appointment letter — {content[:200]}")

    elif doc_type == "prescription":
        speak("This looks like a prescription notice. Let me set that up for you.")
        from agents.medication import run_medication
        run_medication(f"document: prescription — {content[:200]}")

    else:
        save_question = (
            "I am not sure what action to take on this. "
            "Want me to note it down for you?"
        )
        save_response = _ask(save_question, rephrase="Should I save a note about this document?")
        wants_save = detect_intent(save_response, save_question, ["yes", "no"]) == "yes"
        if wants_save:
            send_confirmation("Document noted", content[:200])
            speak("Done. I have sent you a text summary of this document.")


# ── Document reading pipeline ─────────────────────────────────────────────────

def _read_and_act(content: str | None, source_label: str) -> None:
    if not content:
        speak(
            f"I was not able to retrieve the document from your {source_label}. "
            "It may have been moved or was not there. "
            "If you can find it and try again I will be ready."
        )
        return

    speak("Give me a moment to read it.")
    summary = _summarize_document(content)
    speak(f"Here is what it says. {summary}")

    doc_type = _classify_document(content)
    _route_document_action(doc_type, content)


# ── Source flows ──────────────────────────────────────────────────────────────

def _flow_email() -> None:
    hint_question = (
        "Okay, let me look through your email for it. "
        "Do you know roughly when it arrived or who it was from?"
    )
    hint = _ask(hint_question, rephrase="Who sent it, or roughly when did it arrive?")
    print(f"  > email hint: {hint}")

    speak("On it — give me a moment.")
    document = _search_gmail(hint)

    if not document:
        speak(
            "I searched your email but could not find a match. "
            "If you remember more details, say go back and try again."
        )
        return

    subject = document.get("subject", "")
    sender  = document.get("from", "")
    speak(f"I found it. It is from {sender} with subject: {subject}. Give me a moment to read it.")

    content = document.get("body") or document.get("snippet") or ""
    _read_and_act(content or None, "email")

    if content:
        after_question = "Do you want me to take any action on it, or is that all?"
        after_response = _ask(after_question, rephrase="Should I do something with this?")
        after_intent   = detect_intent(after_response, after_question, ["action", "nothing"])
        if after_intent == "action":
            doc_type = _classify_document(content)
            _route_document_action(doc_type, content)


def _flow_drive() -> None:
    hint_question = "Okay, what is the document about or do you know what it is called?"
    hint = _ask(hint_question, rephrase="What should I search for?")
    print(f"  > drive hint: {hint}")

    # Search Gmail with subject/filename hint as fallback
    speak("Searching now.")
    document = _search_gmail(hint)

    if not document:
        speak(
            "I could not find a matching document. "
            "If you remember more about it, say go back and try with different details."
        )
        return

    content = document.get("body") or document.get("snippet") or ""
    _read_and_act(content or None, "email")


def _flow_mail() -> None:
    helper_question = (
        "Is there someone with you right now who could help get this document "
        "into your email? I can walk them through exactly what to do."
    )
    helper_response = _ask(
        helper_question,
        rephrase="Is anyone nearby who can help send this document to your email?",
    )
    has_helper = detect_intent(helper_response, helper_question, ["yes", "no"]) == "yes"

    if has_helper:
        speak(
            "Great. Ask them to send a clear copy of the document to your email address. "
            "They can forward it, attach it, or forward a photo from their phone. "
            "Tell me when that is done and I will read it for you."
        )
        _ask(
            "Just say ready whenever the email has been sent.",
            rephrase="Let me know when the document has been emailed to you.",
        )
        speak("On it — checking your email now.")
        document = _search_gmail("recent document attachment")
        if document:
            content = document.get("body") or document.get("snippet") or ""
            speak("I found it, give me a moment to read it.")
            _read_and_act(content or None, "email")
        else:
            speak("I could not find it yet. Give it a minute and try again.")
    else:
        speak(
            "No problem. When you have someone who can help, "
            "just come back and say I have a document "
            "and we will pick up right here."
        )


# ── Main entry point ──────────────────────────────────────────────────────────

def run_documents(trigger_message: str) -> None:
    profile = load_profile() or {}
    name    = profile.get("name", "")

    step   = 0
    source: str | None = None

    while step <= 1:
        try:
            if step == 0:
                source_question = (
                    f"Of course{', ' + name if name else ''}. "
                    "Did this document arrive by email, is it in your Drive, "
                    "or did it come in the mail?"
                )
                source_response = _ask(
                    source_question,
                    rephrase="Did it come by email, is it in your Drive, or did it arrive as a physical letter?",
                )
                print(f"  > source: {source_response}")
                source = detect_intent(source_response, source_question, ["email", "drive", "mail"])
                step += 1

            elif step == 1:
                if source == "email":
                    _flow_email()
                elif source == "drive":
                    _flow_drive()
                else:
                    _flow_mail()
                step += 1

        except GoBack:
            step = max(0, step - 1)
            print("[Documents] Going back one step.")
            if step == 0:
                source = None
