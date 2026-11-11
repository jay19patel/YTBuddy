"""Local-disk storage behind the Storage port (masters, rendered Shorts, covers)."""

import os
import shutil
from pathlib import Path

from ytstudio.domain.errors import PermanentError


def _safe_key(key: str) -> str:
    if key.startswith("/") or ".." in Path(key).parts:
        raise PermanentError(f"bad storage key {key!r}")
    return key


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def put(self, local: Path, key: str) -> str:
        dest = self.root / _safe_key(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(f".{dest.name}.part")
        shutil.copyfile(local, part)
        os.replace(part, dest)  # atomic on one filesystem
        return key

    def get(self, key: str) -> Path:
        path = self.root / _safe_key(key)
        if not path.exists():
            raise PermanentError(f"missing file {key}")
        return path

    def delete(self, key: str) -> None:
        (self.root / _safe_key(key)).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return (self.root / _safe_key(key)).exists()
