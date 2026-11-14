"""Words → sentences → scenes. A scene starts on a sentence boundary and lasts 3-8 s; a sentence longer than the
maximum is split at its longest pause."""

from pipeline.transcribe import TimedWord

ENDS = (".", "?", "!", "।", "—")
PAUSE = 0.45  # a breath this long ends a sentence even without punctuation


def sentences(words: list[TimedWord]) -> list[list[int]]:
    out: list[list[int]] = []
    for i, w in enumerate(words):
        if out and not (words[i - 1].word.endswith(ENDS) or w.start - words[i - 1].end > PAUSE):
            out[-1].append(i)
        else:
            out.append([i])
    return out


def _span(words: list[TimedWord], idx: list[int]) -> float:
    return words[idx[-1]].end - words[idx[0]].start


def _split(words: list[TimedWord], idx: list[int], max_s: float) -> list[list[int]]:
    if _span(words, idx) <= max_s or len(idx) < 2:
        return [idx]
    cut = max(range(1, len(idx)), key=lambda k: (words[idx[k]].start - words[idx[k - 1]].end, -abs(k - len(idx) / 2)))
    return _split(words, idx[:cut], max_s) + _split(words, idx[cut:], max_s)


def scenes(words: list[TimedWord], min_s: float = 3, max_s: float = 8) -> list[list[int]]:
    """Word indices of each scene, in order, covering every word exactly once."""
    out: list[list[int]] = []
    for sent in (part for s in sentences(words) for part in _split(words, s, max_s)):
        if out and _span(words, out[-1]) < min_s and _span(words, out[-1] + sent) <= max_s:
            out[-1] += sent
        else:
            out.append(sent)
    if len(out) > 1 and _span(words, out[-1]) < min_s and _span(words, out[-2] + out[-1]) <= max_s:
        last = out.pop()  # not `out[-2] += out.pop()`: that writes to the wrong index after the pop
        out[-1] += last
    return out
