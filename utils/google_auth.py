"""
utils/google_auth.py — One-time script to get your Google OAuth refresh token.

Usage:
  python utils/google_auth.py

It will open a browser, ask you to log in and grant access, then print
the three values you need to add to your .env file.
"""

import os
import json
from google_auth_oauthlib.flow import InstalledAppFlow
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
]


def main():
    client_id     = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

    if not client_id or not client_secret:
        print(
            "\nERROR: Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in your .env file first.\n"
            "Get them from console.cloud.google.com → APIs & Services → Credentials → "
            "Create OAuth 2.0 Client ID (Desktop App).\n"
        )
        return

    client_config = {
        "installed": {
            "client_id":     client_id,
            "client_secret": client_secret,
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0)

    print("\n── Add these to your .env file ────────────────────────────────\n")
    print(f"GOOGLE_CLIENT_ID={client_id}")
    print(f"GOOGLE_CLIENT_SECRET={client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    print("\n──────────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
