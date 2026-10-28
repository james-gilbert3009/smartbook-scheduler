"""
config.py - every setting you might want to change lives here.

Change the services, opening hours or timezone below and the whole
application (scheduler, AI agent, UI, tests) follows automatically.
"""
import os
from datetime import datetime

try:  # optional: read a local .env file if python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BUSINESS_NAME = "SmartBook Demo"
BUSINESS_TAGLINE = "Appointments made simple"

# ---- Time ---------------------------------------------------------------
# All times are shown and stored as local business time (no timezone suffix).
TIMEZONE_NAME = os.getenv("BUSINESS_TIMEZONE", "Europe/Riga")
DATETIME_FORMAT = "%Y-%m-%d %H:%M"

# ---- Opening hours ------------------------------------------------------
# Weekday number (0 = Monday ... 6 = Sunday) -> (opens, closes) or None if closed.
OPENING_HOURS = {
    0: ("09:00", "17:00"),
    1: ("09:00", "17:00"),
    2: ("09:00", "17:00"),
    3: ("09:00", "17:00"),
    4: ("09:00", "17:00"),
    5: None,
    6: None,
}
SLOT_INTERVAL_MINUTES = 30      # appointments may start every 30 minutes
MAX_BOOKING_DAYS_AHEAD = 90     # do not allow bookings further ahead than this

# ---- Services (name, description, duration in minutes, price) ----------
CURRENCY = "EUR"
SERVICES = [
    ("Consultation", "A short introductory meeting to discuss your needs.", 30, 15.0),
    ("Standard appointment", "A regular appointment with one of our staff.", 30, 25.0),
    ("Extended appointment", "A longer session for more detailed work.", 60, 45.0),
]

# ---- AI settings (the API key itself is NEVER stored here) ---------------
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
MAX_USER_MESSAGE_CHARS = 500


def now() -> datetime:
    """Current local business time as a naive datetime (no tzinfo)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(TIMEZONE_NAME)).replace(tzinfo=None, second=0, microsecond=0)
    except Exception:  # tzdata missing -> fall back to the computer's clock
        return datetime.now().replace(second=0, microsecond=0)
