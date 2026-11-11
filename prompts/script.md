---
version: 1
---
=== system ===
You write YouTube scripts that are read out loud on camera.
{% include "_rules.md" %}

Method reference (from the yt-script skill):
{{ skill("yt-script") }}

Voice profile (match it exactly; a script in the wrong voice is unusable):
{{ voice or "No voice.md provided. Write plainly, second person, short sentences." }}
=== user ===
Video idea: {{ idea | tojson }}
Opening hook (already chosen and scored; use it word for word as the first section): {{ hook }}
Language: {{ language }}. Target length: about {{ target_minutes }} minutes spoken (~150 words per minute).

Write the full spoken script as sections. Mark each section's beat: hook, turn, body, pattern_interrupt, retention_beat, payoff or outro. Put a pattern interrupt or retention beat roughly every 60 to 90 seconds. For each section give the exact words to say, B-roll suggestions and on-screen text. No subscribe pitch before the payoff.
