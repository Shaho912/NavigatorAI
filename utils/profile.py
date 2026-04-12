import json
import os

PROFILE_PATH = os.path.join(os.path.dirname(__file__), "..", "profile.json")

PROFILE_FIELDS = [
    "name",
    "city",
    "insurance_company",
    "insurance_source",
    "has_primary_care_doctor",
    "doctor_name",
    "medications",
    "conditions",
    "reason_for_joining",
]


def save_profile(data: dict) -> None:
    """Save user profile data to profile.json."""
    profile = load_profile() or {}
    profile.update({k: data[k] for k in PROFILE_FIELDS if k in data})
    with open(PROFILE_PATH, "w") as f:
        json.dump(profile, f, indent=2)


def load_profile() -> dict | None:
    """Load profile from profile.json. Returns None if it doesn't exist."""
    if not profile_exists():
        return None
    with open(PROFILE_PATH, "r") as f:
        return json.load(f)


def profile_exists() -> bool:
    """Return True if profile.json exists and is non-empty."""
    return os.path.isfile(PROFILE_PATH) and os.path.getsize(PROFILE_PATH) > 0
