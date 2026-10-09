from __future__ import annotations

import random
from typing import Any

DIGIT_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")

FACE_STEPS: dict[str, tuple[str, str]] = {
    "blink": ("Blink twice", "Close and open your eyes two times."),
    "turn_left": ("Turn your head left", "Slowly turn your head to your left and then back to the middle."),
    "turn_right": ("Turn your head right", "Slowly turn your head to your right and then back to the middle."),
    "smile": ("Smile", "Give a natural smile and hold it for a moment."),
    "open_mouth": ("Open your mouth", "Open your mouth wide for a moment and then close it."),
}

SENTENCES = (
    "Please say the code {a} {b} {c} to confirm this application is mine.",
    "My confirmation code is {a} {b} {c} and I am applying today.",
    "I am the person who applied and my code is {a} {b} {c}.",
    "I confirm that I wrote this application with code {a} {b} {c}.",
    "The numbers {a} {b} {c} are my code for this live check.",
    "I am speaking now and the code I was given is {a} {b} {c}.",
    "Here is my spoken code {a} {b} {c} for the hiring team.",
    "This is really me and my short code is {a} {b} {c}.",
)

CONSENT_TEXT = (
    "This quick check uses your camera and your microphone. "
    "Your browser looks at your face while you follow three simple prompts and then you read one sentence aloud. "
    "No video, no pictures and no audio are saved. They are checked in memory and thrown away. "
    "Only a few numbers and a short plain note are kept for 90 days. "
    "A person on the hiring team reads that note. It is advice only and it does not change your score or your result. "
    "You can stop at any time."
)


def pick_steps(rng: random.Random) -> list[dict[str, str]]:
    ids = rng.sample(sorted(FACE_STEPS), 3)
    return [{"id": step, "label": FACE_STEPS[step][0], "hint": FACE_STEPS[step][1]} for step in ids]


def pick_sentence(rng: random.Random) -> tuple[str, list[str]]:
    template = rng.choice(SENTENCES)
    digits = [DIGIT_WORDS[rng.randrange(10)] for _ in range(3)]
    return template.format(a=digits[0], b=digits[1], c=digits[2]), digits


def public_steps(ids: list[str]) -> list[dict[str, Any]]:
    return [{"id": step, "label": FACE_STEPS[step][0], "hint": FACE_STEPS[step][1]} for step in ids]
