"""Adapters over the vendored youtube-agent-skill tools (MIT, vendor/youtube-agent-skill, never modified).

Importable tools are called in-process; CLI-only tools run as a subprocess with JSON in and out.
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

from ytstudio.domain.errors import PermanentError
from ytstudio.domain.models import Cue, RetentionPoint


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"vendor_{name}", path)
    if not spec or not spec.loader:
        raise ImportError(path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(script: Path, args: list[str], timeout: int = 60) -> Any:
    # -I: isolated mode, so nothing next to the input files can shadow a module.
    p = subprocess.run(
        [sys.executable, "-I", str(script), *args, "--json"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError as e:
        raise PermanentError(f"{script.name} failed: {(p.stdout or p.stderr).strip()[:300]}") from e


def _srt(cues: list[Cue], path: Path) -> None:
    def ts(s: float) -> str:
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{int(h):02d}:{int(m):02d}:{sec:06.3f}".replace(".", ",")

    path.write_text("\n".join(f"{i}\n{ts(c.start)} --> {ts(c.end)}\n{c.text}\n" for i, c in enumerate(cues, 1)))


class VendorTools:
    def __init__(self, vendor_dir: Path):
        self.skills = vendor_dir / "skills"
        self._hook = _load(self.skills / "yt-script" / "hookscore.py", "hookscore")
        self._title = _load(self.skills / "yt-package" / "title.py", "title")


class VendorHookScorer(VendorTools):
    """ "Hook panel score": a heuristic over five properties. It is not a prediction of views."""

    def score(self, hooks: list[str]) -> list[dict[str, Any]]:
        out = []
        for h in hooks:
            parts, verdict, formula, _ = self._hook.score(h)
            out.append(
                {
                    "hook": h,
                    "panel_score": verdict,
                    "band": self._hook.band(verdict),
                    "formula": formula,
                    "properties": parts,
                    "weakest": min(parts, key=parts.get),
                    "fix": self._hook.FIX[min(parts, key=parts.get)],
                }
            )
        return sorted(out, key=lambda r: -r["panel_score"])


class VendorPackageLinter(VendorTools):
    def lint(self, title: str, thumb_text: str | None) -> dict[str, Any]:
        r = self._title.check(title, thumb_text or None)
        return r | {"issues": [{"kind": k, "message": m} for k, m in r["issues"]]}


class VendorOutlierRanker(VendorTools):
    def rank(self, videos: list[dict[str, Any]], min_multiple: float) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "collected.json"
            f.write_text(json.dumps(videos))
            return dict(_run(self.skills / "yt-viral" / "swipe.py", [str(f), "--min", str(min_multiple)]))


class VendorChapterBuilder(VendorTools):
    def build(self, cues: list[Cue], target: int = 7) -> dict[str, Any]:
        if len(cues) < 6:
            return {"valid": False, "chapters": [], "reason": "too few transcript cues to chapter"}
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "t.srt"
            _srt(cues, f)
            return dict(_run(self.skills / "yt-chapters" / "chapters.py", [str(f), "--target", str(target)]))


class VendorEditDecisionList(VendorTools):
    def build(self, cues: list[Cue]) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "t.srt"
            _srt(cues, f)
            return dict(_run(self.skills / "yt-edit" / "deadair.py", [str(f)]))


class VendorRetentionAnalyzer(VendorTools):
    def analyze(self, points: list[RetentionPoint], duration_s: float, cues: list[Cue] | None) -> dict[str, Any]:
        if len(points) < 8:
            return {"available": False, "reason": "YouTube returned fewer than 8 retention points for this video"}
        with tempfile.TemporaryDirectory() as d:
            csv = Path(d) / "r.csv"
            csv.write_text("\n".join(f"{p.ratio * 100:.2f},{p.watch_ratio * 100:.2f}" for p in points))
            args = [str(csv), "--duration", str(duration_s)]
            if cues:
                srt = Path(d) / "t.srt"
                _srt(cues, srt)
                args += ["--transcript", str(srt)]
            return dict(_run(self.skills / "yt-retention" / "retention.py", args)) | {"available": True}
