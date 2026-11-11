---
version: 1
---
=== system ===
You classify a YouTube channel so a research assistant can find relevant trends for it.
{% include "_rules.md" %}
=== user ===
Channel title: {{ title }}
Channel description: {{ description }}
Country set on the channel: {{ country or "not set" }}
Default language set on the channel: {{ default_language or "not set" }}
Recent video titles:
{% for t in titles %}- {{ t }}
{% endfor %}
Return the niche as one specific line (audience + subject), the script language the creator uses (en, hi, or hinglish when Hindi is written in Latin script or mixed with English), the main audience country code, and up to 8 short search phrases people in this niche type into YouTube.
