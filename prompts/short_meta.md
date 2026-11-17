---
version: 2
---
=== system ===
You write titles and descriptions for YouTube Shorts.
{% include "_rules.md" %}
{% include "_upload_meta.md" %}
=== user ===
Language: {{ language }}.
{% for p in picks %}Short {{ loop.index }}: first line "{{ p.first_line }}". Content: {{ p.text }}
{% endfor %}
For each Short, in the same order, give the title, description, tags and hashtags by the upload text rules above (do not include #Shorts; it is added when it is uploaded).
