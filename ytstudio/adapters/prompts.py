"""File prompt repository. Templates live in prompts/<name>.md:

    ---
    version: 3
    ---
    === system ===
    ...jinja...
    === user ===
    ...jinja...

`{{ skill("yt-script") }}` pulls in a vendored SKILL.md (frontmatter stripped). The recorded version is
"<template version>.<content hash>+<vendor commit>" so every LLM call can be traced to the exact prompt text.
"""

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import FileSystemLoader, StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from ytstudio.ports import PromptTemplate

FRONT = re.compile(r"^---\n(.*?)\n---\n", re.S)


class FilePromptRepository:
    def __init__(self, prompts_dir: Path, vendor_dir: Path):
        self.dir, self.vendor = prompts_dir, vendor_dir
        self.env = SandboxedEnvironment(
            loader=FileSystemLoader(str(prompts_dir)),
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )
        self.env.globals["skill"] = self.skill
        self.env.globals["hook_formulas"] = self.hook_formulas
        commit = vendor_dir / "VENDORED_COMMIT"
        self.vendor_version = commit.read_text().strip()[:7] if commit.exists() else "none"

    @lru_cache(maxsize=64)  # noqa: B019  (repository lives for the process)
    def skill(self, name: str) -> str:
        text = (self.vendor / "skills" / name / "SKILL.md").read_text()
        return FRONT.sub("", text).strip()

    @lru_cache(maxsize=1)  # noqa: B019
    def hook_formulas(self) -> str:
        data = json.loads((self.vendor / "skills" / "yt-script" / "hooks.json").read_text())
        rules = "\n".join(f"- {r}" for r in data["rules"])
        formulas = "\n".join(
            f"{i}. {h['name']}: {h['shape']} Example: {h['example']} Fails when: {h['fails_when']}"
            for i, h in enumerate(data["hooks"], 1)
        )
        return f"Rules:\n{rules}\n\nThe 21 formulas:\n{formulas}"

    @lru_cache(maxsize=64)  # noqa: B019
    def get(self, name: str) -> PromptTemplate:
        raw = (self.dir / f"{name}.md").read_text()
        shared = "".join(p.read_text() for p in sorted(self.dir.glob("_*.md")))  # included partials
        digest = hashlib.sha1((raw + shared).encode(), usedforsecurity=False).hexdigest()[:6]
        meta: dict[str, str] = {}
        if m := FRONT.match(raw):
            meta = dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)
            raw = raw[m.end() :]
        system, _, user = raw.partition("=== user ===")
        system = system.replace("=== system ===", "", 1)
        version = meta.get("version", "0").strip()
        return PromptTemplate(
            name=name, version=f"{version}.{digest}+{self.vendor_version}", system=system.strip(), user=user.strip()
        )

    def render(self, name: str, variables: dict[str, Any]) -> tuple[PromptTemplate, str, str]:
        tpl = self.get(name)
        return (
            tpl,
            self.env.from_string(tpl.system).render(**variables),
            self.env.from_string(tpl.user).render(**variables),
        )
