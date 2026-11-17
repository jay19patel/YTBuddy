"""What language a script is in, and which voices can read it."""

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Voice:
    id: str
    label: str
    language: str  # "en" or "hi"; "hinglish" for a voice offered only for Hindi written in Roman letters


VOICES = (  # Chatterbox only: the most natural of the voices tried
    Voice("chatterbox-hi", "Chatterbox, Hindi", "hi"),
    Voice("chatterbox-hinglish", "Chatterbox, Hinglish (Hindi voice)", "hinglish"),
    Voice("chatterbox-en", "Chatterbox, English", "en"),
)
# Hinglish is read by the Hindi model from a Devanagari copy of the script (see SpeechText): it garbles Roman letters
CHATTERBOX_LANGUAGE = {"chatterbox-en": "en", "chatterbox-hi": "hi", "chatterbox-hinglish": "hi"}


LANGUAGES = (
    ("hi", "Hindi (हिंदी)"),
    ("en", "English"),
    ("hinglish", "Hindi + English mix (Roman letters)"),
)
LANGUAGE_IDS = tuple(i for i, _ in LANGUAGES)
DEVANAGARI = re.compile("[\u0900-\u097f]")
ROMAN_WORD = re.compile(r"[A-Za-z]+")
# Common Hindi words in Roman letters that are not English words: what tells Hinglish from English.
HINDI_ROMAN = frozenset(
    "hai hain ka ki ke ko se mein nahi nahin aur yeh ye woh wo kya kaise kyun kyunki bhi par lekin agar jab tab karo "  # noqa: SIM905
    "karna kare karte kar hota hoga hogi raha rahi rahe tha thi mera meri apna apne aap hum tum abhi bahut sabse sirf "
    "ek liye wala wali banao dekho chahiye kuch koi sab jo toh isse unhe maine tumhe aapka aapki".split()
)


def detect_language(text: str) -> str:
    """hi (mostly Devanagari), hinglish (Roman letters with Hindi words in them) or en. Only a rule of thumb: the voice
    depends on Devanagari or not, which this tells exactly."""
    devanagari = len(DEVANAGARI.findall(text))
    roman = ROMAN_WORD.findall(text)
    if devanagari and devanagari >= sum(map(len, roman)):
        return "hi"
    if roman and sum(w.lower() in HINDI_ROMAN for w in roman) / len(roman) >= 0.1:
        return "hinglish"
    return "en"


# Script length. Long videos are in-depth 2 to 3 minute explainers, Shorts 40 to 60 seconds; the narrator reads about
# 140 words a minute. A draft outside the range is written again once (see long_script / short_script).
LONG_MINUTES = (2, 3)
WORDS_PER_MINUTE = 140
SHORT_WORDS = (100, 150)


def long_minutes(target: int) -> int:
    """The channel's target length, kept within what a long video here is: 2 to 3 minutes."""
    return min(max(target, LONG_MINUTES[0]), LONG_MINUTES[1])


def long_words(target: int) -> tuple[int, int]:
    """Words for the long script: half a minute of slack below the target, e.g. 3 minutes -> 350 to 420 words."""
    m = long_minutes(target)
    return m * WORDS_PER_MINUTE - WORDS_PER_MINUTE // 2, m * WORDS_PER_MINUTE


def word_count(spoken: list[str]) -> int:
    return sum(len(s.split()) for s in spoken)


def spoken_text(data: dict[str, Any]) -> str:
    return " ".join(str(sec.get("spoken", "")) for sec in (data.get("script") or {}).get("sections", []))


def script_language(data: dict[str, Any], channel_language: str = "en") -> str:
    """The language chosen for this script, else what its words are in, else the channel's setting."""
    for v in (data.get("language"), (data.get("idea") or {}).get("language")):
        if v in LANGUAGE_IDS:
            return str(v)
    text = spoken_text(data)
    return detect_language(text) if text.strip() else (channel_language if channel_language in LANGUAGE_IDS else "en")


def voices_for(text: str) -> list[Voice]:
    """Voices that can read this text: Hindi voices for Devanagari, English ones for Roman letters (a Hindi voice reads
    English badly and the other way round). Hinglish also gets the voices made for it."""
    lang = detect_language(text)
    family = "hi" if lang == "hi" else "en"
    return [v for v in VOICES if v.language in (family, lang)]


def voice_for(text: str, requested: str = "") -> str:
    """The chosen voice if it fits the text, otherwise the default one for the text's language."""
    fits = voices_for(text)
    return requested if requested in {v.id for v in fits} else default_voice(detect_language(text))


def default_voice(language: str) -> str:
    return {"hi": "chatterbox-hi", "hinglish": "chatterbox-hinglish"}.get(language, "chatterbox-en")
