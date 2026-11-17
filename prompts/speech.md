---
version: 3
---
=== system ===
You prepare text for a Hindi text-to-speech voice that can only read Devanagari. You change the writing, never the words.
=== user ===
Each line below is one spoken sentence in Hinglish (Hindi and English written in Roman letters). Some words are already in Devanagari: those are fixed spellings, copy them exactly. Write each line fully in Devanagari exactly as a Hindi speaker says it out loud:
- Same words, same order, nothing added, dropped or translated. Hindi words in normal, correct Hindi spelling (aaj → आज, kholo → खोलो, banaya → बनाया, kyunki → क्योंकि).
- English words as Indians pronounce them, in Devanagari (connect → कनेक्ट, generate → जनरेट, combine → कंबाइन, localhost → लोकलहोस्ट, main.py → मेन डॉट पाई).
- No digits and no symbols at all, not even Devanagari digits: numbers in Hindi words (10 → दस, 90% → नब्बे परसेंट, 2024 → दो हज़ार चौबीस, 8000 → आठ हज़ार), symbols as the words said (C++ → सी प्लस प्लस).
- Keep the sentence's punctuation (। for a full stop, ? and ,) so the voice pauses in the same places.
Return exactly one line per sentence, in the same order.

{% for s in sentences %}{{ loop.index }}. {{ s }}
{% endfor %}