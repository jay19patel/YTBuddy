---
version: 1
---
=== system ===
You pick Shorts out of a long video.
{% include "_rules.md" %}

Method reference (from the yt-shorts skill):
{{ skill("yt-shorts") }}
=== user ===
Candidate windows from the transcript. Each is 15 to 58 seconds and ends on a sentence end. retention_score is the average relativeRetentionPerformance across the window when YouTube provided it (above 1 means viewers stayed better than for similar videos; 0 means unknown).
{% for w in windows %}[{{ loop.index0 }}] {{ w.start }}-{{ w.end }} s, retention_score {{ w.retention_score }}: {{ w.text }}
{% endfor %}
Language: {{ language }}.

Pick up to 5 windows that make sense with no context from the rest of the video. Return each pick's window index. For each, write a new first line (spoken or on screen) that makes it self-contained and gives the viewer a reason to stay. Say briefly why you picked it.
