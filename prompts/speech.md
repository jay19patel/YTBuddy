---
version: 2
---
=== system ===
You prepare text for a Hindi text-to-speech voice that can only read Devanagari. You change the writing, never the words.
=== user ===
Each line below is one spoken sentence in Hinglish (Hindi and English written in Roman letters). Write each one in Devanagari exactly as a Hindi speaker says it out loud:
- Same words, same order, nothing added, dropped or translated. Hindi words in normal Hindi spelling (aaj → आज, kholo → खोलो).
- English words and tech names as they are pronounced, in Devanagari (FastAPI → फ़ास्ट ए पी आई, pip install → पिप इंस्टॉल, main.py → मेन डॉट पाई, localhost → लोकलहोस्ट).
- No digits and no symbols at all, not even Devanagari digits: write numbers and symbols as the words that are said (10 → दस, 90% → नब्बे परसेंट, 8000 → आठ हज़ार, Step 1 → स्टेप वन, C++ → सी प्लस प्लस).
- Keep the sentence's punctuation (। for a full stop, ? and ,) so the voice pauses in the same places.
Return exactly one line per sentence, in the same order.

{% for s in sentences %}{{ loop.index }}. {{ s }}
{% endfor %}