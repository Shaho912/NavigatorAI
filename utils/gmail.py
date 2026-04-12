"""
utils/gmail.py — Gmail integration via Composio.

Requires in .env:
  COMPOSIO_API_KEY   — from app.composio.dev
  COMPOSIO_USER_ID   — your external_user_id (e.g. "rashid" or a UUID)

Gmail must be connected for this user in the Composio dashboard.
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

COMPOSIO_API_KEY = os.getenv("COMPOSIO_API_KEY", "")
COMPOSIO_USER_ID = os.getenv("COMPOSIO_USER_ID", "")


def _creds_present() -> bool:
    return bool(COMPOSIO_API_KEY and COMPOSIO_USER_ID)


GMAIL_TOOLKIT_VERSION = "20260410_00"


def _client():
    """Return a Composio SDK instance pinned to the known Gmail toolkit version."""
    from composio import Composio
    return Composio(
        api_key=COMPOSIO_API_KEY,
        toolkit_versions={"gmail": GMAIL_TOOLKIT_VERSION},
    )


def _execute(slug: str, arguments: dict) -> dict:
    """Run a Composio tool and return the data dict. Raises on failure."""
    composio = _client()
    response = composio.tools.execute(
        slug=slug,
        arguments=arguments,
        user_id=COMPOSIO_USER_ID,
    )
    # Response is a plain dict: {"data": {...}, "successful": true, ...}
    if isinstance(response, dict):
        return response.get("data") or response
    # Fallback for object-style responses
    data = getattr(response, "data", response)
    if isinstance(data, str):
        import json as _json
        try:
            data = _json.loads(data)
        except Exception:
            data = {"raw": data}
    return data or {}


# ── Search ────────────────────────────────────────────────────────────────────

def search_emails(query: str, max_results: int = 5) -> list[dict]:
    """
    Search Gmail via Composio and return a list of message dicts:
      {id, subject, from, date, snippet, body}
    Returns [] on failure or missing credentials.
    """
    if not _creds_present():
        logger.debug("Composio credentials not set — skipping Gmail search")
        return []
    try:
        data = _execute(
            "GMAIL_FETCH_EMAILS",
            {
                "query":       query,
                "max_results": max_results,
            },
        )
        raw_emails = (
            data.get("messages")
            or data.get("emails")
            or data.get("data", {}).get("messages")
            or data.get("data", {}).get("emails")
            or []
        )
        return [_normalise_email(e) for e in raw_emails]
    except Exception as exc:
        logger.debug("Composio Gmail search error: %s", exc)
        return []


def search_health_emails(max_results: int = 10) -> list[dict]:
    """Search for recent health/insurance related emails."""
    query = (
        "is:unread ("
        "insurance OR claim OR denial OR appointment OR prescription "
        "OR hospital OR clinic OR pharmacy OR Medicare OR Medicaid OR lab OR billing"
        ") newer_than:90d"
    )
    return search_emails(query, max_results=max_results)


# ── Send ──────────────────────────────────────────────────────────────────────

def send_email(to: str, subject: str, body: str) -> bool:
    """
    Send a plain-text email via Composio Gmail.
    Returns True on success.
    """
    if not _creds_present():
        logger.debug("Composio credentials not set — cannot send email")
        return False
    try:
        data = _execute(
            "GMAIL_SEND_EMAIL",
            {
                "to":      to,
                "subject": subject,
                "body":    body,
            },
        )
        # Any non-error response counts as success
        success = data.get("successful", data.get("success", True))
        return bool(success)
    except Exception as exc:
        logger.debug("Composio Gmail send error: %s", exc)
        return False


# ── Normaliser ────────────────────────────────────────────────────────────────

def _normalise_email(raw: dict) -> dict:
    """
    Map Composio's Gmail response shape into a consistent dict:
      {id, subject, from, date, snippet, body}

    Composio returns fields like messageId, messageText, messageTimestamp,
    with headers nested under payload.headers.
    """
    # Extract headers from payload
    headers: dict = {}
    for h in raw.get("payload", {}).get("headers", []):
        if isinstance(h, dict):
            headers[h.get("name", "").lower()] = h.get("value", "")

    return {
        "id":      raw.get("messageId") or raw.get("id", ""),
        "subject": headers.get("subject") or raw.get("subject", "(no subject)"),
        "from":    headers.get("from")    or raw.get("from", ""),
        "date":    (
            raw.get("messageTimestamp")
            or headers.get("date")
            or raw.get("date", "")
        ),
        "snippet": raw.get("snippet", ""),
        "body":    raw.get("messageText") or raw.get("body", ""),
    }
