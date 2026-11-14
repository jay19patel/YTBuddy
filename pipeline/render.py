"""Remotion from Python: `npx remotion render|still` as list-argument subprocesses, with progress in the log."""

import logging
import re
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)
ENTRY = "src/index.ts"
PROGRESS = re.compile(r"Rendered (\d+)/(\d+)")


class RenderError(RuntimeError):
    pass


def preflight(renderer: Path) -> None:
    for tool in ("node", "npx", "ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RenderError(f"{tool} is not installed or not on PATH (see README: Setup)")
    if not (renderer / "node_modules" / "remotion").exists():
        raise RenderError(f"the renderer's packages are missing: run `npm ci` in {renderer}")


def render_cmd(composition: str, props: Path, out: Path, crf: int, concurrency: int | None = None) -> list[str]:
    cmd = ["npx", "remotion", "render", ENTRY, composition, str(out), f"--props={props}",
           "--codec=h264", "--pixel-format=yuv420p", f"--crf={crf}", "--log=info"]  # fmt: skip
    return cmd + ([f"--concurrency={concurrency}"] if concurrency else [])


def still_cmd(composition: str, props: Path | None, frame: int, out: Path) -> list[str]:
    return ["npx", "remotion", "still", ENTRY, composition, str(out), f"--frame={frame}"] + (
        [f"--props={props}"] if props else []
    )


def run(cmd: list[str], renderer: Path, timeout: int) -> None:
    """Runs in the renderer folder, logging progress every 10 %. Remotion's last lines are kept for the error."""
    log.info("remotion: %s", " ".join(cmd[2:4]))
    tail: list[str] = []
    shown = -10
    try:
        with subprocess.Popen(cmd, cwd=renderer, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as p:
            assert p.stdout is not None
            for line in p.stdout:
                tail = [*tail[-30:], line.rstrip()]
                if m := PROGRESS.search(line):
                    pct = 100 * int(m.group(1)) // max(1, int(m.group(2)))
                    if pct >= shown + 10:
                        shown = pct
                        log.info("rendered %s%% (%s/%s frames)", pct, m.group(1), m.group(2))
            code = p.wait(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RenderError(f"remotion did not finish within {timeout} s") from e
    if code:
        raise RenderError("remotion failed:\n" + "\n".join(tail[-15:]))
