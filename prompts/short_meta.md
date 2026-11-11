---
version: 1
---
=== system ===
You write titles and descriptions for YouTube Shorts.
{% include "_rules.md" %}
=== user ===
Language: {{ language }}.
{% for p in picks %}Short {{ loop.index }}: first line "{{ p.first_line }}". Content: {{ p.text }}
{% endfor %}
For each Short, in the same order, give a title under 60 characters and a two-line description. No hashtags beyond #Shorts.
