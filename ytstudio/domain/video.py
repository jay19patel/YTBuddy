"""The choices on the "Generate video" form: who reads the script and how the subtitles look."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Look:
    id: str
    label: str
    bg: str  # hex colours, no #
    ink: str
    accent: str


@dataclass(frozen=True)
class Voice:
    id: str  # a Microsoft neural voice name; the offline fallback is picked from its language
    label: str
    language: str  # "en" or "hi"


LOOKS = (
    Look("clean", "Clean: white screen, black text", "FFFFFF", "141414", "E5322D"),
    Look("dark", "Dark: black screen, white text", "0E0E10", "FFFFFF", "FFD60A"),
    Look("vivid", "Vivid: red screen, white text", "E5322D", "FFFFFF", "FFE45E"),
)
VOICES = (
    Voice("en-IN-PrabhatNeural", "Indian English, man", "en"),
    Voice("en-IN-NeerjaNeural", "Indian English, woman", "en"),
    Voice("en-US-AndrewMultilingualNeural", "American English, man", "en"),
    Voice("en-US-AvaMultilingualNeural", "American English, woman", "en"),
    Voice("hi-IN-MadhurNeural", "Hindi, man", "hi"),
    Voice("hi-IN-SwaraNeural", "Hindi, woman", "hi"),
)


def default_voice(channel_language: str) -> str:
    """Hinglish is Hindi in Latin letters: an Indian English voice reads it best."""
    return "hi-IN-MadhurNeural" if channel_language == "hi" else "en-IN-PrabhatNeural"


def voice_language(voice_id: str) -> str:
    return "hi" if voice_id.startswith("hi-") else "en"


def look_of(look_id: str) -> Look:
    return next((x for x in LOOKS if x.id == look_id), LOOKS[0])
