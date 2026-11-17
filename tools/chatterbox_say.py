"""Runs inside the Chatterbox virtualenv (CHATTERBOX_PYTHON), not the app's: reads a JSON list of sentences, writes one
24 kHz mono 16-bit WAV per sentence into a folder. The model loads once, so a whole script costs one start-up.

    python chatterbox_say.py --lines lines.json --out DIR --lang hi [--ref voice.wav] [--take 0]

Same calm, seeded sampling as chatterbox_server.py, so both sound the same.
"""

import argparse
import json
from pathlib import Path

import torch
import torchaudio

p = argparse.ArgumentParser()
p.add_argument("--lines", type=Path, required=True)
p.add_argument("--out", type=Path, required=True)
p.add_argument("--lang", default="en")
p.add_argument("--ref", type=Path)
p.add_argument("--take", type=int, default=0)
a = p.parse_args()

device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
_load = torch.load
torch.load = lambda *args, **kw: _load(
    *args, **{**kw, "map_location": torch.device(device)}
)  # checkpoints are saved for CUDA

from chatterbox.mtl_tts import ChatterboxMultilingualTTS  # noqa: E402

model = ChatterboxMultilingualTTS.from_pretrained(device=device)
STYLE = {"exaggeration": 0.4, "cfg_weight": 0.5}
if a.ref:
    model.prepare_conditionals(str(a.ref), exaggeration=STYLE["exaggeration"])
for i, text in enumerate(json.loads(a.lines.read_text(encoding="utf-8"))):
    torch.manual_seed(1234 + a.take)
    wav = model.generate(text, language_id=a.lang, temperature=0.6, **STYLE)
    torchaudio.save(str(a.out / f"{i}.wav"), wav.cpu(), model.sr, encoding="PCM_S", bits_per_sample=16)
    print(f"line {i + 1} done", flush=True)
