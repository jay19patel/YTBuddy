---
version: 2
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

The script becomes a kinetic-typography video: every word you write is shown big on screen at the moment it is said, over bold colour scenes with stickers. There is no camera footage, so write for the eye as well as the ear:
- One idea per sentence, 4 to 12 words, so each sentence lands as its own punchy on-screen line.
- Put the strongest word (the number, the surprise, the thing) near the end of its sentence; that is the word that gets boxed or coloured.
- Prefer concrete nouns a sticker can show (money, phone, clock, fire, brain, key) over abstract talk.
- Spoken text only: no stage directions, no [brackets], no markdown, no emoji. Write numbers as digits.

- When a step involves code, a command, a file, a setting or a tool, say its real name in the sentence (pip install fastapi, main.py, the requirements file): the video shows it in a code window or terminal at that word.
- Say steps, checklists, pros and cons as short spoken lists ("step 1 ...", "first ... second ... third ..."): each item ticks on screen as it is said.

Write the full spoken script as sections. Mark each section's beat: hook, turn, body, pattern_interrupt, retention_beat, payoff or outro. Put a pattern interrupt or retention beat roughly every 60 to 90 seconds. For each section give the exact words to say, and short on-screen text. No subscribe pitch before the payoff.
The last section has beat outro: one or two short lines that tease what is next and ask the viewer to follow, using the word "follow" (for example "aisi aur videos ke liye follow kar lo." in Hinglish, "follow for more like this." in English). The video's follow button is tapped on that word, then the end screen plays.
