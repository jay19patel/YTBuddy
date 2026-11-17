"""How English words are said by the Hindi voice. Chatterbox's Hindi model reads only Devanagari, and an LLM spelling
"AI" or "version 1" in Devanagari picks a different spelling each time, so the voice says them differently from one
sentence to the next. These fixed spellings are applied first, so the same word always sounds the same. Pure, no I/O."""

import re

LETTERS = dict(
    zip(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        [
            "ए",
            "बी",
            "सी",
            "डी",
            "ई",
            "एफ़",
            "जी",
            "एच",
            "आई",
            "जे",
            "के",
            "एल",
            "एम",
            "एन",
            "ओ",
            "पी",
            "क्यू",
            "आर",
            "एस",
            "टी",
            "यू",
            "वी",
            "डब्ल्यू",
            "एक्स",
            "वाई",
            "ज़ेड",
        ],
        strict=True,
    )
)
ONES = [
    "ज़ीरो",
    "वन",
    "टू",
    "थ्री",
    "फ़ोर",
    "फ़ाइव",
    "सिक्स",
    "सेवन",
    "एट",
    "नाइन",
    "टेन",
    "इलेवन",
    "ट्वेल्व",
    "थर्टीन",
    "फ़ोर्टीन",
    "फ़िफ़्टीन",
    "सिक्सटीन",
    "सेवनटीन",
    "एटीन",
    "नाइनटीन",
]
TENS = ["_", "_", "ट्वेंटी", "थर्टी", "फ़ोर्टी", "फ़िफ़्टी", "सिक्स्टी", "सेवेंटी", "एटी", "नाइंटी"]

# Names and words the Hindi voice gets wrong or says differently each time. Lowercase keys; matched as whole words.
WORDS = {
    "youtube": "यूट्यूब",
    "python": "पायथन",
    "ffmpeg": "एफ़ एफ़ एम्पेग",
    "chatgpt": "चैट जी पी टी",
    "openai": "ओपन ए आई",
    "github": "गिटहब",
    "google": "गूगल",
    "gemini": "जेमिनी",
    "claude": "क्लॉड",
    "javascript": "जावास्क्रिप्ट",
    "fastapi": "फ़ास्ट ए पी आई",
    "django": "जैंगो",
    "docker": "डॉकर",
    "linux": "लिनक्स",
    "windows": "विंडोज़",
    "mac": "मैक",
    "chatterbox": "चैटरबॉक्स",
    "remotion": "रिमोशन",
    "whisper": "व्हिस्पर",
    "shorts": "शॉर्ट्स",
    "version": "वर्ज़न",
    "automation": "ऑटोमेशन",
    "automated": "ऑटोमेटेड",
    "automatic": "ऑटोमैटिक",
    "platform": "प्लेटफ़ॉर्म",
    "insights": "इनसाइट्स",
    "thumbnail": "थंबनेल",
    "script": "स्क्रिप्ट",
    "content": "कंटेंट",
    "creator": "क्रिएटर",
    "analysis": "एनालिसिस",
    "analyze": "एनालाइज़",
    "model": "मॉडल",
    "models": "मॉडल्स",
    "video": "वीडियो",
    "videos": "वीडियोज़",
    "audio": "ऑडियो",
    "upload": "अपलोड",
    "download": "डाउनलोड",
    "follow": "फ़ॉलो",
    "subscribe": "सब्सक्राइब",
    "text": "टेक्स्ट",
    "speech": "स्पीच",
    "voice": "वॉइस",
    "tool": "टूल",
    "tools": "टूल्स",
    "data": "डेटा",
    "code": "कोड",
    "app": "ऐप",
    "website": "वेबसाइट",
    "online": "ऑनलाइन",
    "free": "फ़्री",
    "step": "स्टेप",
    "trading": "ट्रेडिंग",
    "trend": "ट्रेंड",
    "trends": "ट्रेंड्स",
    "topic": "टॉपिक",
    "title": "टाइटल",
    "tags": "टैग्स",
    "json": "जेसन",
    "nasa": "नासा",
}
# English labels after which a number is said in English ("step 2" -> "स्टेप टू", not "स्टेप दो").
LABELS = r"step|version|v|part|level|phase|chapter|episode|tip|rule|number|day|top|class|grade|gen"


def english_number(n: str) -> str:
    """0 to 99 in English, in Devanagari; longer numbers digit by digit; "2.0" -> "टू पॉइंट ज़ीरो"."""
    if "." in n:
        return " पॉइंट ".join(english_number(p) for p in n.split(".") if p)
    if len(n) > 2:
        return " ".join(ONES[int(d)] for d in n)
    i = int(n)
    if i < 20:
        return ONES[i]
    return TENS[i // 10] + ("" if i % 10 == 0 else " " + ONES[i % 10])


def spell(acronym: str) -> str:
    """AI -> ए आई; a plural "APIs" -> ए पी आईज़."""
    plural = acronym.endswith("s") and acronym[:-1].isupper()
    letters = " ".join(LETTERS[c] for c in (acronym[:-1] if plural else acronym))
    return letters + ("ज़" if plural else "")


def pronounce(sentence: str) -> str:
    """Fixed Devanagari for known names, acronyms (2 to 4 capitals) and numbers after an English label; the rest of
    the sentence is left for the Devanagari copy (or is already Devanagari)."""

    def labelled(m: re.Match[str]) -> str:
        label = m.group(1)
        said = WORDS.get(label.lower(), spell(label.upper()) if len(label) == 1 else label)
        return f"{said} {english_number(m.group(2))}"

    s = re.sub(rf"\b({LABELS})\s*(\d+(?:\.\d+)?)\b", labelled, sentence, flags=re.IGNORECASE)
    s = re.sub(r"\b[A-Z]{2,4}s?\b", lambda m: WORDS.get(m.group(0).lower()) or spell(m.group(0)), s)
    return re.sub(r"\b[A-Za-z]+\b", lambda m: WORDS.get(m.group(0).lower(), m.group(0)), s)
