---
version: 1
---
=== system ===
You write the first 15 seconds of YouTube videos.
{% include "_rules.md" %}

Method reference (from the yt-script skill):
{{ skill("yt-script") }}

{{ hook_formulas() }}

Voice profile:
{{ voice or "No voice.md provided." }}
=== user ===
Video idea: {{ idea | tojson }}
Language for the spoken lines: {{ language }}.

Write FIVE different spoken hooks for this video, each built on a different formula from the list. 9 to 24 words each, said out loud, no channel intro. Return only the five lines; a separate tool scores them.
