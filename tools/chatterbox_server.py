"""Chatterbox as a small local server, run on the Mac itself (not in Docker) with the Chatterbox virtualenv's python, so
it uses the Mac's GPU and keeps the model loaded between videos. The media worker in Docker calls it through
CHATTERBOX_URL=http://host.docker.internal:8765.

    ~/…/chatterbox-venv/bin/python tools/chatterbox_server.py [--port 8765] [--ref my-voice.wav]

POST /speak {"lines": [...], "lang": "hi"} -> {"wavs": [base64 24 kHz mono 16-bit WAV, one per line]}.
--ref clones the voice in a 10 s WAV of you speaking; without it Chatterbox's default voice is used.
"""

import argparse
import base64
import io
import json
import logging
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
TOKENS_PER_S = 25  # Chatterbox's speech tokens
cap = {"tokens": 1000}
_inference = model.t3.inference


def _capped(*args, **kw):  # generate() always allows 1000 tokens (40 s): a take that never stops costs minutes of GPU
    return _inference(*args, **{**kw, "max_new_tokens": min(kw.get("max_new_tokens") or 1000, cap["tokens"])})


model.t3.inference = _capped
logging.info("Chatterbox ready on %s, listening on http://%s:%d", device, a.host, a.port)


def say(text: str, lang: str) -> torch.Tensor:
    """One sentence. Chatterbox sometimes rambles or repeats after the words (a known failure): a take far longer than
    the text needs is made again, cooler, and the shortest take is kept."""
    limit = 1.5 + len(text) / 8  # seconds: speech runs about 13 characters a second; babble runs well past this
    best: tuple[torch.Tensor, float] | None = None
    cap["tokens"] = int(TOKENS_PER_S * limit * 1.3)  # a runaway take stops just past the limit and is said again
    for temperature in (0.8, 0.6, 0.45):
        wav = model.generate(text, language_id=lang, audio_prompt_path=a.ref, temperature=temperature)
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
        except (ValueError, KeyError, TypeError) as e:
            return self._reply(400, {"error": f"bad request: {e}"})
        wavs = []
        for i, text in enumerate(lines):
            wav = say(text, lang)
            buf = io.BytesIO()
            torchaudio.save(buf, wav.cpu(), model.sr, format="wav", encoding="PCM_S", bits_per_sample=16)
            wavs.append(base64.b64encode(buf.getvalue()).decode())
            logging.info("line %d/%d done", i + 1, len(lines))
        self._reply(200, {"wavs": wavs})

    def _reply(self, code: int, data: dict) -> None:
        raw = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


HTTPServer((a.host, a.port), Speak).serve_forever()  # one request at a time: the GPU does one script at a time
