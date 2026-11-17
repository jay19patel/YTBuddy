"""Pipeline settings: config/kinetic.yaml validated by pydantic. A typo there is an error, not a silent default."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "kinetic.yaml"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BrandCfg(Strict):
    handle: str = "@njtechstudio"
    avatar: str | None = "brand/avatar.svg"
    character: str | None = "brand/character.svg"


class TranscribeCfg(Strict):
    model: str = "small"
    language: str = "hinglish"


class PlanCfg(Strict):
    llm_model: str = "anthropic/claude-sonnet-5-5"
    min_scene_s: float = 3
    max_scene_s: float = 8


class AudioCfg(Strict):
    lufs: float = -14
    true_peak: float = -1.5
    music_db: float = -22
    duck: bool = True
    sfx_db: float = -10


class VideoCfg(Strict):
    crf: int = Field(18, ge=0, le=51)
    timeout_s: int = 3600
    draft_scale: float = Field(0.5, gt=0, le=1)  # a draft is drawn at this share of the width and height
    draft_crf: int = Field(26, ge=0, le=51)


class Settings(Strict):
    brand: BrandCfg = Field(default_factory=lambda: BrandCfg())
    fps: int = Field(30, gt=0, le=120)
    formats: dict[str, tuple[int, int]] = Field(default_factory=lambda: {"16x9": (1920, 1080), "9x16": (1080, 1920)})
    transcribe: TranscribeCfg = Field(default_factory=lambda: TranscribeCfg())
    plan: PlanCfg = Field(default_factory=lambda: PlanCfg())
    audio: AudioCfg = Field(default_factory=lambda: AudioCfg())
    video: VideoCfg = Field(default_factory=lambda: VideoCfg.model_validate({}))
    renderer: Path = ROOT / "renderer"
    assets: Path = ROOT / "assets"

    def size(self, fmt: str) -> tuple[int, int]:
        if fmt not in self.formats:
            raise ValueError(f"unknown format {fmt!r}; known: {', '.join(self.formats)}")
        return self.formats[fmt]


def load(path: Path = CONFIG) -> Settings:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return Settings.model_validate(data or {})
