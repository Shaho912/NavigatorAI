"""
test_composio_gmail.py — Test Composio Gmail integration.
Run: python test_composio_gmail.py
"""

from dotenv import load_dotenv
load_dotenv()

from utils.gmail import search_health_emails, _creds_present, COMPOSIO_USER_ID

def run():
    if not _creds_present():
        print("\nMissing credentials. Add to .env:")
        print("  COMPOSIO_API_KEY=...")
        print("  COMPOSIO_USER_ID=...")
        return

    print(f"\nSearching Gmail for health emails (user: {COMPOSIO_USER_ID})...\n")

    emails = search_health_emails(max_results=5)

    if not emails:
        print("No health emails found (inbox may be empty or Gmail not connected in Composio).")
        return

    print(f"Found {len(emails)} email(s):\n")
    for i, email in enumerate(emails, 1):
        print(f"[{i}] From:    {email['from']}")
        print(f"     Subject: {email['subject']}")
        print(f"     Date:    {email['date']}")
        print(f"     Snippet: {email['snippet'][:120]}")
        print()

if __name__ == "__main__":
    run()
