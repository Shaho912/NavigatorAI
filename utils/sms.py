"""
utils/sms.py — Real SMS via Twilio.

Requires in .env:
  TWILIO_ACCOUNT_SID
  TWILIO_AUTH_TOKEN
  TWILIO_FROM_NUMBER   (e.g. +15005550006)
  USER_PHONE_NUMBER    (e.g. +12125551234)
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def _creds_present() -> bool:
    return all(os.getenv(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
                                       "TWILIO_FROM_NUMBER", "USER_PHONE_NUMBER"))


def send_sms(message: str, to: str | None = None) -> bool:
    """
    Send an SMS to `to` (defaults to USER_PHONE_NUMBER).
    Returns True on success, False on failure or missing credentials.
    """
    if not _creds_present():
        logger.debug("Twilio credentials not set — skipping SMS")
        return False
    try:
        from twilio.rest import Client
        client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
        client.messages.create(
            body=message,
            from_=os.getenv("TWILIO_FROM_NUMBER"),
            to=to or os.getenv("USER_PHONE_NUMBER"),
        )
        logger.debug("SMS sent to %s", to or os.getenv("USER_PHONE_NUMBER"))
        return True
    except Exception as exc:
        logger.debug("SMS send error: %s", exc)
        return False


def send_confirmation(action: str, details: str) -> bool:
    """Send a standardised Navigator AI confirmation SMS."""
    message = f"Navigator AI: {action}\n{details}"
    return send_sms(message)
