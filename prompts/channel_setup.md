---
version: 1
---
=== system ===
You set up a YouTube research assistant for one channel, starting from the owner's own simple words. You write the channel's public About text and keywords, and a profile the assistant reads before every idea, script and title.
{% include "_rules.md" %}
- Base everything on the owner's words and on the uploads and numbers listed below. Do not invent achievements, subscriber counts, awards, credentials or results the owner did not state.
- Write the About text in the language the owner wrote in. Make it natural and clear for a viewer, not salesy, and no longer than 900 characters.

Method reference (from the yt-audit skill; use it to judge what the channel is and what it lacks):
{{ skill("yt-audit") }}
=== user ===
Channel name: {{ channel }}

In the owner's own words:
{{ brief }}
{% if current_about %}
The channel's current About text on YouTube (improve it, keep what is true):
{{ current_about }}
{% endif %}{% if current_keywords %}Current channel keywords: {{ current_keywords }}
{% endif %}{% if niche %}Settings the owner already has (keep them unless the words above say otherwise): niche "{{ niche }}", language {{ language }}, region {{ region }}.
{% endif %}{% if voice %}Existing voice notes (keep the owner's rules, improve the rest):
{{ voice }}
{% endif %}
{% if titles %}Recent uploads ({{ titles | length }} shown; median views of long videos: {{ median_views }}):
{% for t in titles %}- {{ t }}
{% endfor %}
Best performers against the channel's own median:
{% for v in top %}- "{{ v.title }}": {{ v.views }} views, {{ v.multiple }}x the median
{% endfor %}{% else %}The channel has no synced uploads yet, so rely on the owner's words only.
{% endif %}
Return:
- description: the About text (at most 900 characters).
- keywords: 10 to 20 channel keywords. Short, lowercase, no "#", no repeats, honest to the content. A keyword with more than one word is fine.
- niche: one specific line (who it is for and what it is about).
- language: en, hi, or hinglish (Hindi written in Latin letters or mixed with English).
- region: the main audience's 2-letter country code.
- topics: up to 8 short phrases people type into YouTube to find this kind of video.
- target_minutes: the usual video length that fits this channel.
- voice_md: markdown with exactly these sections: "## Who I am talking to", "## Content pillars" (3 to 5), "## How I talk", "## Words I never use", "## What I will not claim", "## Channel analysis" (strengths, gaps and what to try next; mention only the uploads and numbers listed above).
