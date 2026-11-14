"""Runs inside the Chatterbox virtualenv (CHATTERBOX_PYTHON), not the app's: reads a JSON list of sentences, writes one
24 kHz mono 16-bit WAV per sentence into a folder. The model loads once, so a whole script costs one start-up.

    python chatterbox_say.py --lines lines.json --out DIR --lang hi [--ref voice.wav]
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
a = p.parse_args()

device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
_load = torch.load
torch.load = lambda *args, **kw: _load(
    *args, **{**kw, "map_location": torch.device(device)}
)  # checkpoints are saved for CUDA

from chatterbox.mtl_tts import ChatterboxMultilingualTTS  # noqa: E402

model = ChatterboxMultilingualTTS.from_pretrained(device=device)
for i, text in enumerate(json.loads(a.lines.read_text(encoding="utf-8"))):
    wav = model.generate(text, language_id=a.lang, audio_prompt_path=str(a.ref) if a.ref else None)
    torchaudio.save(str(a.out / f"{i}.wav"), wav.cpu(), model.sr, encoding="PCM_S", bits_per_sample=16)
    print(f"line {i + 1} done", flush=True)
