"""YouTube channel metadata rules: the About description and the keywords field. Pure, no I/O."""

DESCRIPTION_MAX = 1000
KEYWORDS_MAX = 500


def clean_description(text: str, limit: int = DESCRIPTION_MAX) -> str:
    """At most `limit` characters, cut at a word boundary so a sentence is never split mid-word."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(None, 1)[0].rstrip(",;:")


def format_keywords(tags: list[str], limit: int = KEYWORDS_MAX) -> str:
    """YouTube's channel keywords: space separated, a multi-word keyword in quotes, at most `limit` characters
    in all. Duplicates (any case), '#' and stray quotes are dropped."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = " ".join(raw.replace('"', "").lstrip("#").split())
        if not tag or tag.lower() in seen:
            continue
        piece = f'"{tag}"' if " " in tag else tag
        if len(" ".join([*out, piece])) > limit:
            break
        seen.add(tag.lower())
        out.append(piece)
    return " ".join(out)


VIDEO_DESCRIPTION_MAX = 5000
VIDEO_TAGS_MAX = 500

COPYRIGHT = "© {channel}. All rights reserved. Please do not re-upload or reuse this video without permission."
MADE_HERE = """Original script and animation; narration uses an AI-generated voice.

Credits:
Emoji stickers: Microsoft Fluent Emoji (MIT License)
Fonts: Bebas Neue, Inter Tight, Playfair Display, Noto Sans Devanagari (SIL Open Font License)"""
DISCLAIMER = (
    "Disclaimer: This video is for educational and informational purposes only. "
    "It is not financial, legal or professional advice. Do your own research before acting on it."
)


def hashtag_list(text: str) -> list[str]:
    """Hashtags typed with commas or spaces, '#' optional: "rent, #money tips" -> ["#rent", "#money", "#tips"]."""
    return [f"#{w.lstrip('#')}" for w in text.replace(",", " ").split() if w.strip("#")]


def upload_description(text: str, hashtags: list[str], channel: str, made_here: bool) -> str:
    """The description as uploaded, the same for every video: the written text, its hashtags, the copyright line,
    the credits when the video was made here (AI voice, stickers, fonts), then the disclaimer. A text that already
    ends with the copyright line (pasted from an earlier upload) is not given a second one."""
    text = text.strip()
    tags = [f"#{''.join(h.lstrip('#').split())}" for h in hashtags if h.strip("# ")]
    tags = [h for h in dict.fromkeys(tags) if h.lower() not in text.lower()]
    footer = [] if "©" in text else [COPYRIGHT.format(channel=channel), MADE_HERE if made_here else "", DISCLAIMER]
    return "\n\n".join(p for p in [text, " ".join(tags), *footer] if p)


def clean_video_text(text: str, limit: int) -> str:
    """YouTube rejects '<' and '>' in a title or description."""
    return clean_description(text.replace("<", "").replace(">", ""), limit)


def video_tags(tags: list[str], limit: int = VIDEO_TAGS_MAX) -> list[str]:
    """Video tags YouTube accepts: no '#', no duplicates (any case), at most `limit` characters counted the way YouTube
    does (a comma between tags, quotes around a multi-word tag)."""
    out: list[str] = []
    seen: set[str] = set()
    used = 0
    for raw in tags:
        tag = " ".join(raw.replace('"', "").replace("<", "").replace(">", "").lstrip("#").split())
        if not tag or tag.lower() in seen:
            continue
        cost = len(tag) + (2 if " " in tag else 0) + (1 if out else 0)
        if used + cost > limit:
            break
        seen.add(tag.lower())
        out.append(tag)
        used += cost
    return out
