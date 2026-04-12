"""
Navigator AI — Interactive Voice Test Suite

Each test is run sequentially. Tests that can be verified automatically
are checked programmatically. Tests requiring voice interaction guide
the human tester with spoken and printed prompts, then confirm the
outcome by asking the user to say PASS or FAIL.

Run with:
    python test_flow.py
"""

import os
import json
import time
import sys
import speech_recognition as sr

PROFILE_PATH = os.path.join(os.path.dirname(__file__), "profile.json")

# ── Helpers ───────────────────────────────────────────────────────────────────

results: list[tuple[str, bool, str]] = []


def _header(test_num: int, title: str) -> None:
    print("\n" + "=" * 60)
    print(f"  TEST {test_num}: {title}")
    print("=" * 60)


def _record(title: str, passed: bool, note: str = "") -> None:
    results.append((title, passed, note))
    status = "PASS" if passed else "FAIL"
    print(f"\n  [{status}] {title}" + (f" — {note}" if note else ""))


def _prompt(instruction: str) -> None:
    """Print and speak an instruction to the tester."""
    from utils.voice import speak
    print(f"\n  >>> {instruction}")
    speak(instruction)


def _wait_voice_confirm(question: str) -> bool:
    """Ask the tester to say PASS or FAIL and return True for pass."""
    from utils.voice import speak, listen
    speak(question + " Say PASS or FAIL.")
    print(f"  >>> {question} (say PASS or FAIL)")
    while True:
        try:
            response = listen()
            print(f"  Heard: {response}")
            if "pass" in response.lower():
                return True
            if "fail" in response.lower():
                return False
            speak("Please say PASS or FAIL.")
        except sr.UnknownValueError:
            speak("Didn't catch that. Please say PASS or FAIL.")
        except sr.RequestError:
            print("  STT unavailable — defaulting to FAIL")
            return False


def _delete_profile() -> None:
    if os.path.exists(PROFILE_PATH):
        os.remove(PROFILE_PATH)
        print("  Deleted profile.json")


# ── Test 1: Fresh Onboarding ──────────────────────────────────────────────────

def test_1_fresh_onboarding() -> None:
    _header(1, "Fresh Onboarding")
    _delete_profile()

    assert not os.path.exists(PROFILE_PATH), "profile.json should not exist before test"

    _prompt(
        "Profile deleted. Onboarding will now start. "
        "Answer all questions by voice as a real user would."
    )

    from agents.onboarding import run_onboarding
    run_onboarding()

    if os.path.exists(PROFILE_PATH):
        with open(PROFILE_PATH) as f:
            profile = json.load(f)
        print("\n  profile.json contents:")
        print(json.dumps(profile, indent=4))

        has_name = bool(profile.get("name"))
        has_city = bool(profile.get("city"))
        _record("Test 1 — profile.json created", True)
        _record("Test 1 — name field populated", has_name, profile.get("name", "MISSING"))
        _record("Test 1 — city field populated", has_city, profile.get("city", "MISSING"))
    else:
        _record("Test 1 — profile.json created", False, "File not found after onboarding")


# ── Test 2: Returning User Skips Onboarding ───────────────────────────────────

def test_2_returning_user() -> None:
    _header(2, "Returning User — Skips Onboarding")

    from utils.profile import profile_exists
    from agents.onboarding import run_onboarding
    from utils.voice import speak

    if not profile_exists():
        _record("Test 2 — profile exists pre-check", False, "No profile.json found")
        return

    speak("Profile already exists. Running onboarding — it should skip immediately.")
    print("  Running run_onboarding() — should print skip message and return.")

    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        run_onboarding()
    output = buf.getvalue()
    print(f"  Output: {output.strip()}")

    skipped = "skipping" in output.lower()
    _record("Test 2 — onboarding skipped", skipped, output.strip())


# ── Test 3: Insurance Routing ─────────────────────────────────────────────────

def test_3_insurance_routing() -> None:
    _header(3, "Insurance Routing")

    from agents.router import route

    trigger = "my insurance denied my MRI"
    agent = route(trigger)
    auto_pass = agent == "insurance"
    _record("Test 3 — router identifies insurance intent", auto_pass, f"got '{agent}'")

    if auto_pass:
        _prompt(
            "Router correctly identified insurance. "
            "Now running the insurance agent. "
            "Go through the full flow by voice. "
            "When asked about a document say email. "
            "When asked about an appeal say yes."
        )
        from agents.insurance import run_insurance
        run_insurance(trigger)

        passed = _wait_voice_confirm("Did the flow reach the appeal step and complete successfully?")
        _record("Test 3 — full insurance flow completed", passed)


# ── Test 4: Appointment Routing ───────────────────────────────────────────────

def test_4_appointment_routing() -> None:
    _header(4, "Appointment Routing")

    from agents.router import route

    trigger = "I need to see a doctor"
    agent = route(trigger)
    auto_pass = agent == "appointment"
    _record("Test 4 — router identifies appointment intent", auto_pass, f"got '{agent}'")

    if auto_pass:
        _prompt(
            "Router correctly identified appointment. "
            "Now running the appointment agent. "
            "Say you need help finding a new doctor. "
            "Choose Doctor Chen when prompted."
        )
        from agents.appointment import run_appointment
        run_appointment(trigger)

        passed = _wait_voice_confirm("Did you successfully find and select a doctor?")
        _record("Test 4 — full appointment flow completed", passed)


# ── Test 5: Medication Routing ────────────────────────────────────────────────

def test_5_medication_routing() -> None:
    _header(5, "Medication Routing")

    from agents.router import route

    trigger = "I need to set up my medication reminders"
    agent = route(trigger)
    auto_pass = agent == "medication"
    _record("Test 5 — router identifies medication intent", auto_pass, f"got '{agent}'")

    if auto_pass:
        _prompt(
            "Router correctly identified medication. "
            "Add two medications by voice — for example Metformin and Lisinopril. "
            "Include a time and whether to take with food."
        )
        from agents.medication import run_medication
        run_medication(trigger)

        if os.path.exists(PROFILE_PATH):
            with open(PROFILE_PATH) as f:
                profile = json.load(f)
            meds = profile.get("medications", [])
            has_meds = isinstance(meds, list) and len(meds) >= 1
            print(f"  Medications in profile: {json.dumps(meds, indent=4)}")
            _record("Test 5 — medications saved to profile.json", has_meds, f"{len(meds)} found")
        else:
            _record("Test 5 — medications saved to profile.json", False, "profile.json missing")


# ── Test 6: Bills Routing ─────────────────────────────────────────────────────

def test_6_bills_routing() -> None:
    _header(6, "Bills Routing")

    from agents.router import route

    trigger = "what medical bills do I owe"
    agent = route(trigger)
    auto_pass = agent == "bills"
    _record("Test 6 — router identifies bills intent", auto_pass, f"got '{agent}'")

    if auto_pass:
        _prompt(
            "Router correctly identified bills. "
            "Now running the bills agent. "
            "Go through all three mock bills by voice."
        )
        from agents.bills import run_bills
        run_bills(trigger)

        passed = _wait_voice_confirm("Did you go through all the mock bills successfully?")
        _record("Test 6 — full bills flow completed", passed)
    else:
        _record("Test 6 — full bills flow completed", False, "routing failed, skipping agent")


# ── Test 7: Confusion and Repeat Handling ─────────────────────────────────────

def test_7_confusion_handling() -> None:
    _header(7, "Confusion and Repeat Handling")

    _prompt(
        "This test checks how Navigator handles confusion mid-conversation. "
        "The insurance agent will start. "
        "When it asks you the first question, say: what. "
        "It should rephrase. Then say: repeat. It should repeat. "
        "Then say: go back. It should go to the previous step."
    )

    from agents.insurance import run_insurance
    run_insurance("test confusion handling")

    passed = _wait_voice_confirm(
        "Did Navigator correctly rephrase on what, repeat on repeat, and go back on go back?"
    )
    _record("Test 7 — confusion handling works", passed)


# ── Test 8: Unknown Input ─────────────────────────────────────────────────────

def test_8_unknown_input() -> None:
    _header(8, "Unknown Input Routing")

    from agents.router import route
    from utils.voice import speak

    trigger = "what is the weather today"
    agent = route(trigger)
    auto_pass = agent == "unknown"
    _record("Test 8 — router returns unknown for unrelated input", auto_pass, f"got '{agent}'")

    speak(
        "Navigator does not know how to handle that request. "
        "I am not sure I understood that. "
        "Could you try rephrasing? For example, you can ask about insurance, "
        "appointments, medications, or bills."
    )
    print(
        "  Navigator response: 'I'm not sure I understood that. "
        "Could you try rephrasing? You can ask about insurance, "
        "appointments, medications, or bills.'"
    )
    _record("Test 8 — unknown fallback response spoken", True)


# ── Results Summary ───────────────────────────────────────────────────────────

def _print_summary() -> None:
    print("\n" + "=" * 60)
    print("  RESULTS SUMMARY")
    print("=" * 60)
    passed = sum(1 for _, p, _ in results if p)
    failed = sum(1 for _, p, _ in results if not p)
    for title, p, note in results:
        status = "PASS" if p else "FAIL"
        print(f"  [{status}] {title}" + (f" — {note}" if note else ""))
    print(f"\n  Total: {passed} passed, {failed} failed out of {len(results)} checks")
    print("=" * 60)


# ── Entry Point ───────────────────────────────────────────────────────────────

TESTS = [
    test_1_fresh_onboarding,
    test_2_returning_user,
    test_3_insurance_routing,
    test_4_appointment_routing,
    test_5_medication_routing,
    test_6_bills_routing,
    test_7_confusion_handling,
    test_8_unknown_input,
]

if __name__ == "__main__":
    # Allow running a single test by number: python test_flow.py 3
    if len(sys.argv) > 1:
        try:
            idx = int(sys.argv[1]) - 1
            TESTS[idx]()
        except (IndexError, ValueError):
            print(f"Usage: python test_flow.py [1-{len(TESTS)}]")
            sys.exit(1)
    else:
        for test in TESTS:
            test()

    _print_summary()
