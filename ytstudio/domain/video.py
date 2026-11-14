"""The choice on the "Generate video" form: who reads the script."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Voice:
    id: str  # a Microsoft neural voice name; the offline fallback is picked from its language
    label: str
    language: str  # "en" or "hi"


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
