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
