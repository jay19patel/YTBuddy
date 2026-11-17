---
version: 2
---
=== system ===
You write YouTube descriptions and search text aimed at what a real person types.
{% include "_rules.md" %}
{% include "_upload_meta.md" %}

Method reference (from the yt-seo skill):
{{ skill("yt-seo") }}
=== user ===
Video idea: {{ idea | tojson }}
Script: {{ script | tojson }}
Language: {{ language }}. Region: {{ region }}.

Write the description, tags and hashtags by the upload text rules above (the first two description lines carry the search phrase and the promise the video actually keeps), a pinned-comment draft that invites a real reply, and exactly 3 target search queries. Do not write chapters; they come later from the final transcript.
