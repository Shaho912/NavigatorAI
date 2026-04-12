"""
utils/gcal.py — Real Google Calendar integration.

Requires in .env:
  GOOGLE_CLIENT_ID
  GOOGLE_CLIENT_SECRET
  GOOGLE_REFRESH_TOKEN

Scope needed:
  https://www.googleapis.com/auth/calendar.events
"""

import logging
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def _build_service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=os.getenv("GOOGLE_REFRESH_TOKEN"),
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token",
        scopes=_SCOPES,
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _creds_present() -> bool:
    return all(os.getenv(k) for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"))


# ── Create event ──────────────────────────────────────────────────────────────

def create_event(
    title: str,
    description: str = "",
    start_dt: datetime | None = None,
    duration_minutes: int = 60,
    calendar_id: str = "primary",
) -> dict | None:
    """
    Create a calendar event. Defaults to tomorrow at 9am if start_dt is None.
    Returns the created event dict or None on failure.
    """
    if not _creds_present():
        logger.debug("Calendar credentials not set — skipping")
        return None
    try:
        service = _build_service()

        if start_dt is None:
            now      = datetime.now(timezone.utc)
            start_dt = (now + timedelta(days=1)).replace(
                hour=9, minute=0, second=0, microsecond=0
            )

        end_dt = start_dt + timedelta(minutes=duration_minutes)

        event = {
            "summary":     title,
            "description": description,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "UTC"},
            "end":   {"dateTime": end_dt.isoformat(),   "timeZone": "UTC"},
        }
        created = service.events().insert(calendarId=calendar_id, body=event).execute()
        logger.debug("Calendar event created: %s", created.get("id"))
        return created
    except Exception as exc:
        logger.debug("Calendar create error: %s", exc)
        return None


def create_reminder(
    title: str,
    description: str = "",
    days_from_now: int = 1,
    hour: int = 9,
) -> dict | None:
    """Convenience wrapper: reminder N days from now at a given hour."""
    now   = datetime.now(timezone.utc)
    start = (now + timedelta(days=days_from_now)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    return create_event(title, description=description, start_dt=start, duration_minutes=30)


# ── List upcoming ─────────────────────────────────────────────────────────────

def get_upcoming_events(max_results: int = 10) -> list[dict]:
    """Return the next N calendar events from now."""
    if not _creds_present():
        return []
    try:
        service  = _build_service()
        now_iso  = datetime.now(timezone.utc).isoformat()
        result   = service.events().list(
            calendarId="primary",
            timeMin=now_iso,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        events = result.get("items", [])
        return [
            {
                "id":          e.get("id", ""),
                "title":       e.get("summary", "(no title)"),
                "description": e.get("description", ""),
                "start":       e.get("start", {}).get("dateTime") or e.get("start", {}).get("date", ""),
                "end":         e.get("end",   {}).get("dateTime") or e.get("end",   {}).get("date", ""),
            }
            for e in events
        ]
    except Exception as exc:
        logger.debug("Calendar list error: %s", exc)
        return []
