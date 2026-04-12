import speech_recognition as sr
from utils.voice import listen, speak, detect_intent, _SilenceOnly
from utils.profile import save_profile, profile_exists


def _ask(question: str) -> str:
    """Speak a question, retry until a clear response is heard."""
    speak(question)
    while True:
        try:
            return listen()
        except _SilenceOnly:
            pass
        except sr.UnknownValueError:
            speak("Sorry, I didn't quite get that — could you say it again?")
        except sr.RequestError as e:
            print(f"STT error: {e}")
            speak("Having a little trouble hearing you. Please try once more.")


def run_onboarding() -> None:
    if profile_exists():
        print("Profile exists — skipping onboarding.")
        return

    speak(
        "Before we get started, I'd like to learn a little about you "
        "so I can help you as well as possible. It only takes a minute."
    )

    profile = {}

    profile["name"] = _ask("What's your name?")
    print(f"  > {profile['name']}")

    profile["city"] = _ask(f"Nice to meet you, {profile['name']}. What city do you live in?")
    print(f"  > {profile['city']}")

    insurance_response = _ask("Do you currently have health insurance?")
    print(f"  > {insurance_response}")
    has_insurance = detect_intent(
        insurance_response, "Do you currently have health insurance?", ["yes", "no"]
    ) == "yes"

    if has_insurance:
        profile["insurance_company"] = _ask(
            "What's the name of your insurance company? "
            "For example — Blue Cross, Aetna, Medicaid, or Medicare."
        )
        print(f"  > {profile['insurance_company']}")

        profile["insurance_source"] = _ask(
            "Is that through your job, or do you pay for it yourself?"
        )
        print(f"  > {profile['insurance_source']}")
    else:
        profile["insurance_company"] = None
        profile["insurance_source"]  = None

    doctor_response = _ask("Do you have a primary care doctor?")
    print(f"  > {doctor_response}")
    has_doctor = detect_intent(
        doctor_response, "Do you have a primary care doctor?", ["yes", "no"]
    ) == "yes"
    profile["has_primary_care_doctor"] = has_doctor

    if has_doctor:
        profile["doctor_name"] = _ask("What's their name?")
        print(f"  > {profile['doctor_name']}")
    else:
        profile["doctor_name"] = None

    profile["medications"] = _ask("Do you take any medications regularly?")
    print(f"  > {profile['medications']}")

    profile["conditions"] = _ask(
        "Do you have any ongoing health conditions? Feel free to say no if not."
    )
    print(f"  > {profile['conditions']}")

    profile["reason_for_joining"] = _ask(
        "Last one — what brought you to Navigator today?"
    )
    print(f"  > {profile['reason_for_joining']}")

    save_profile(profile)
    print("Profile saved.")

    speak(
        f"Thank you, {profile['name']} — I have everything I need. "
        "Let's get started."
    )


if __name__ == "__main__":
    run_onboarding()
