"""
test_gmail_health.py — Test: search Gmail for health emails and read them aloud.

Run: python test_gmail_health.py
"""

from dotenv import load_dotenv
load_dotenv()

from utils.gmail import search_health_emails, _creds_present
from utils.voice import speak


def run():
    if not _creds_present():
        print(
            "\nGoogle credentials not set.\n"
            "1. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env\n"
            "2. Run: python utils/google_auth.py\n"
            "3. Copy GOOGLE_REFRESH_TOKEN into .env\n"
            "4. Re-run this script\n"
        )
        speak(
            "Google credentials are not set up yet. "
            "Run the google auth helper script to connect your Gmail."
        )
        return

    speak("Searching your email for recent health and insurance messages. Give me a moment.")
    print("\nSearching Gmail for health emails...")

    emails = search_health_emails(max_results=5)

    if not emails:
        speak(
            "I searched your inbox but could not find any recent health or insurance emails. "
            "Everything may be up to date, or your inbox may not have any in the last 90 days."
        )
        print("No health emails found.")
        return

    speak(f"I found {len(emails)} health related email{'s' if len(emails) != 1 else ''}. Let me go through them.")

    for i, email in enumerate(emails, 1):
        subject = email.get("subject", "(no subject)")
        sender  = email.get("from",    "(unknown sender)")
        snippet = email.get("snippet", "")
        body    = email.get("body",    "")

        print(f"\n[{i}] From: {sender}")
        print(f"     Subject: {subject}")
        print(f"     Snippet: {snippet[:200]}")

        # Read top ~300 chars of body aloud so it's not too long
        preview = (body or snippet)[:300].strip()
        speak(
            f"Email {i}. From {sender}. Subject: {subject}. "
            f"Here is what it says — {preview}"
        )

    speak(
        f"That is all {len(emails)} health email{'s' if len(emails) != 1 else ''} I found. "
        "Would you like me to take action on any of them?"
    )


if __name__ == "__main__":
    run()
