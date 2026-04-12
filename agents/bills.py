import logging
import speech_recognition as sr
from utils.profile import load_profile
from utils.voice import listen, speak, detect_intent, _SilenceOnly
from utils.connectors import require_connector
from utils.gmail import search_health_emails
from utils.gcal import create_reminder
from utils.sms import send_confirmation

logger = logging.getLogger(__name__)

MOCK_BILLS = [
    {"provider": "Jefferson Hospital", "date": "March 3rd",    "amount": "$240", "reason": "knee appointment", "paid": False},
    {"provider": "CVS Pharmacy",       "date": "March 10th",   "amount": "$45",  "reason": "prescription",     "paid": False},
    {"provider": "Lab Corp",           "date": "February 28th","amount": "$120", "reason": "blood work",       "paid": True},
]

ORDINALS = ["first", "second", "third", "fourth", "fifth"]


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
            speak("Sorry, I missed that — could you say it one more time?")
            continue
        except sr.RequestError as e:
            logger.debug("STT error: %s", e)
            speak("Having a little trouble hearing. Please try again.")
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


def _fetch_real_bills() -> list[dict]:
    """
    Try to pull real billing emails from Gmail.
    Returns a list of bill dicts, falling back to MOCK_BILLS on failure.
    """
    from utils.gmail import _creds_present
    if not _creds_present():
        return []

    try:
        emails = search_health_emails(max_results=10)
        bills  = []
        for e in emails:
            subject = e.get("subject", "")
            snippet = e.get("snippet", "")
            # Only include emails that look like bills
            text = (subject + " " + snippet).lower()
            if any(k in text for k in ("bill", "statement", "amount due", "balance", "payment", "owe")):
                bills.append({
                    "provider": e.get("from", "Unknown sender"),
                    "date":     e.get("date", "recent"),
                    "amount":   "see email",
                    "reason":   subject,
                    "paid":     False,
                    "email_id": e.get("id"),
                })
        return bills
    except Exception as exc:
        logger.debug("Bill fetch error: %s", exc)
        return []


def _handle_one_bill(bill: dict, position: int) -> dict:
    ordinal       = ORDINALS[position] if position < len(ORDINALS) else f"number {position + 1}"
    step          = 0
    remember_resp = None
    paid_resp     = None
    action_resp   = None

    while step <= 3:
        try:
            if step == 0:
                remember_q = (
                    f"The {ordinal} one is from {bill['provider']}, dated {bill['date']}, "
                    f"for {bill['amount']}. It looks like it's for your {bill['reason']}. "
                    f"Does that ring a bell?"
                )
                remember_resp = _ask(
                    remember_q,
                    rephrase=f"Do you remember a {bill['reason']} at {bill['provider']} around {bill['date']}?",
                )
                print(f"  > remembers: {remember_resp}")
                step += 1

            elif step == 1:
                remembers = detect_intent(
                    remember_resp,
                    f"Does the user remember the {bill['reason']} at {bill['provider']}?",
                    ["yes", "no"],
                ) == "yes"

                if not remembers:
                    speak(
                        f"Okay — I'll flag that {bill['provider']} charge for review. "
                        "It's always worth calling them if something doesn't look familiar."
                    )
                    send_confirmation(
                        "Bill flagged for review",
                        f"{bill['provider']} — {bill['amount']} dated {bill['date']}",
                    )
                    return {"action": "flagged", "bill": bill}

                paid_q    = "Have you already taken care of this one?"
                paid_resp = _ask(
                    paid_q,
                    rephrase=f"Has the {bill['amount']} to {bill['provider']} been paid?",
                )
                print(f"  > paid: {paid_resp}")
                step += 1

            elif step == 2:
                already_paid = detect_intent(
                    paid_resp,
                    f"Has the user already paid the {bill['provider']} bill?",
                    ["yes", "no"],
                ) == "yes"

                if already_paid:
                    speak(f"Great — I'll mark the {bill['provider']} bill as paid.")
                    return {"action": "paid", "bill": bill}

                action_q = (
                    f"Okay, so {bill['amount']} to {bill['provider']} is still outstanding. "
                    "Want me to set a reminder to pay it, or would you like help "
                    "understanding what this charge is for?"
                )
                action_resp = _ask(
                    action_q,
                    rephrase=f"Should I set a reminder for the {bill['amount']} bill, "
                             "or help you look into the charge?",
                )
                print(f"  > action: {action_resp}")
                step += 1

            elif step == 3:
                action_intent = detect_intent(
                    action_resp,
                    "Does the user want a payment reminder or to understand/dispute the charge?",
                    ["set_reminder", "dispute_charge"],
                )

                if action_intent == "set_reminder":
                    # Create a real Google Calendar reminder
                    created = create_reminder(
                        title=f"Pay {bill['provider']} bill — {bill['amount']}",
                        description=f"Bill for {bill['reason']} dated {bill['date']}",
                        days_from_now=3,
                        hour=9,
                    )
                    # Send SMS confirmation
                    send_confirmation(
                        "Payment reminder set",
                        f"{bill['provider']} — {bill['amount']} (due ~{bill['date']})",
                    )
                    if created:
                        speak(
                            f"Done — I've set a Google Calendar reminder to pay the {bill['provider']} "
                            f"bill of {bill['amount']} in 3 days, and sent you a text confirmation."
                        )
                    else:
                        speak(
                            f"I've noted the {bill['provider']} bill of {bill['amount']} and sent you a text. "
                            "Connect Google Calendar in settings to get calendar reminders too."
                        )
                    return {"action": "reminder", "bill": bill}
                else:
                    speak(
                        f"The {bill['provider']} charge is for {bill['reason']} on {bill['date']}. "
                        "If anything looks off, call their billing department and give them that date — "
                        "they can walk you through it. I'll flag this one for follow-up."
                    )
                    send_confirmation(
                        "Bill flagged for dispute",
                        f"{bill['provider']} — {bill['amount']} for {bill['reason']}",
                    )
                    return {"action": "dispute", "bill": bill}

        except GoBack:
            step = max(0, step - 1)
            print("[Bills] Going back one step.")

    return {"action": "skip", "bill": bill}


def _build_summary(outcomes: list[dict]) -> str:
    outstanding = [o for o in outcomes if o["action"] in ("reminder", "flagged", "dispute")]
    paid        = [o for o in outcomes if o["action"] == "paid"]
    parts       = []
    if outstanding:
        names = ", ".join(o["bill"]["provider"] for o in outstanding)
        parts.append(f"{len(outstanding)} outstanding: {names}")
    if paid:
        names = ", ".join(o["bill"]["provider"] for o in paid)
        parts.append(f"{len(paid)} marked as paid: {names}")
    return ". ".join(parts) if parts else "nothing outstanding"


def run_bills(trigger_message: str) -> None:
    profile = load_profile() or {}
    name    = profile.get("name", "")

    speak(
        f"Let me take a look at your recent medical bills{', ' + name if name else ''}. "
        "Give me just a moment."
    )

    # Try real Gmail first, fall back to mock
    real_bills = _fetch_real_bills()
    if real_bills:
        speak(f"I found {len(real_bills)} billing email{'s' if len(real_bills) != 1 else ''} in your inbox.")
        all_bills = real_bills
    else:
        all_bills = MOCK_BILLS

    unpaid     = [b for b in all_bills if not b.get("paid")]
    paid_bills = [b for b in all_bills if b.get("paid")]

    print(f"\n[Bills] {len(unpaid)} unpaid, {len(paid_bills)} paid\n")

    speak(
        f"Okay, I found {len(unpaid)} unpaid "
        f"bill{'s' if len(unpaid) != 1 else ''} "
        f"and {len(paid_bills)} that "
        f"{'are' if len(paid_bills) != 1 else 'is'} already taken care of. "
        "Let me go through them one at a time."
    )

    outcomes = []
    for i, bill in enumerate(unpaid):
        try:
            outcomes.append(_handle_one_bill(bill, i))
        except Exception as e:
            logger.debug("Bill %d error: %s", i + 1, e)
            continue

    for bill in paid_bills:
        outcomes.append({"action": "already_paid", "bill": bill})

    summary = _build_summary(outcomes)
    speak(
        f"That covers everything{', ' + name if name else ''}. "
        f"Here's where things stand — {summary}. "
        "Is there anything else I can help with?"
    )
