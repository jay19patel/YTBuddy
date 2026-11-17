"""Chatterbox as a small local server, run on the Mac itself (not in Docker) with the Chatterbox virtualenv's python, so
it uses the Mac's GPU and keeps the model loaded between videos. The media worker in Docker calls it through
CHATTERBOX_URL=http://host.docker.internal:8765.

    ~/…/chatterbox-venv/bin/python tools/chatterbox_server.py [--port 8765] [--ref my-voice.wav]

POST /speak {"lines": [...], "lang": "hi", "take": 0} -> {"wavs": [base64 24 kHz mono 16-bit WAV, one per line]}.
--ref clones the voice in a 10 s WAV of you speaking; without it Chatterbox's default voice is used.
Every line of a take is sampled from the same seed with calm settings, so the voice keeps one tone and pace across
the script instead of changing from sentence to sentence; "take" changes the seed when a sentence is said again.
"""

import argparse
import base64
import hashlib
import io
import json
import logging
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

import torch
import torchaudio

p = argparse.ArgumentParser()
p.add_argument("--host", default="127.0.0.1")  # Docker Desktop reaches it as host.docker.internal
p.add_argument("--port", type=int, default=8765)
p.add_argument("--ref")
a = p.parse_args()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
_load = torch.load
torch.load = lambda *args, **kw: _load(
    *args, **{**kw, "map_location": torch.device(device)}
)  # checkpoints are saved for CUDA

from chatterbox.mtl_tts import ChatterboxMultilingualTTS  # noqa: E402

model = ChatterboxMultilingualTTS.from_pretrained(device=device)
STYLE = {"exaggeration": 0.4, "cfg_weight": 0.5}  # calm, even narration: more exaggeration swings the tone line to line
SEED = 1234
# Each finished sentence is kept here, so making a video again re-says only the sentences that changed.
CACHE = Path.home() / ".cache" / "ytstudio-chatterbox"
CACHE.mkdir(parents=True, exist_ok=True)
REF_ID = f"{a.ref}:{Path(a.ref).stat().st_mtime}" if a.ref else ""  # a new recording means new audio
if a.ref:
    model.prepare_conditionals(a.ref, exaggeration=STYLE["exaggeration"])  # once, not again for every sentence
TOKENS_PER_S = 25  # Chatterbox's speech tokens
cap = {"tokens": 1000}
_inference = model.t3.inference


def _capped(*args, **kw):  # generate() always allows 1000 tokens (40 s): a take that never stops costs minutes of GPU
    return _inference(*args, **{**kw, "max_new_tokens": min(kw.get("max_new_tokens") or 1000, cap["tokens"])})


model.t3.inference = _capped
logging.info("Chatterbox ready on %s, listening on http://%s:%d", device, a.host, a.port)


LIMIT_VERSION = 2


def say(text: str, lang: str, take: int = 0) -> torch.Tensor:
    """One sentence. Chatterbox sometimes rambles or repeats after the words (a known failure): a take far longer than
    the text needs is made again, cooler, and the shortest take is kept."""
    # seconds: calm Hindi narration runs about 4 to 5 characters a second (25 characters ~ 6 s); babble runs to twice
    # that or more. A tighter limit re-said most good Hindi takes for nothing, tripling the time.
    limit = 2.5 + len(text) / 5  # change LIMIT_VERSION with it: cached takes were judged by the old limit
    best: tuple[torch.Tensor, float] | None = None
    cap["tokens"] = int(TOKENS_PER_S * limit * 1.3)  # a runaway take stops just past the limit and is said again
    for temperature in (0.6, 0.5, 0.4):
        torch.manual_seed(SEED + take)
        wav = model.generate(text, language_id=lang, temperature=temperature, **STYLE)
        seconds = wav.shape[-1] / model.sr
        if best is None or seconds < best[1]:
            best = (wav, seconds)
        if seconds <= limit:
            break
        logging.warning("%.1f s for %d characters (limit %.1f s): saying it again", seconds, len(text), limit)
    assert best is not None
    return best[0]


class Speak(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # health check
        self._reply(200, {"ok": True, "device": device})

    def do_POST(self) -> None:
        if self.path != "/speak":
            return self._reply(404, {"error": "POST /speak"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            lines, lang = [str(x) for x in body["lines"]], str(body.get("lang", "en"))
            take = int(body.get("take", 0))
        except (ValueError, KeyError, TypeError) as e:
            return self._reply(400, {"error": f"bad request: {e}"})
        wavs = []
        for i, text in enumerate(lines):
            key = json.dumps([text, lang, take, SEED, STYLE, REF_ID, LIMIT_VERSION])
            hit = CACHE / f"{hashlib.sha256(key.encode()).hexdigest()}.wav"
            cached = hit.exists()
            if cached:
                raw = hit.read_bytes()
            else:
                buf = io.BytesIO()
                wav = say(text, lang, take)
                torchaudio.save(buf, wav.cpu(), model.sr, format="wav", encoding="PCM_S", bits_per_sample=16)
                raw = buf.getvalue()
                hit.write_bytes(raw)
            wavs.append(base64.b64encode(raw).decode())
            logging.info("line %d/%d %s", i + 1, len(lines), "from cache" if cached else "done")
        self._reply(200, {"wavs": wavs})

    def _reply(self, code: int, data: dict) -> None:
        raw = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


HTTPServer((a.host, a.port), Speak).serve_forever()  # one request at a time: the GPU does one script at a time
