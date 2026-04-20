"""Small JSON-backed store with atomic writes and private permissions."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


class JsonStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self, default: Any) -> Any:
        if not self.path.exists():
            return default
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return default

    def save(self, value: Any) -> None:
        ensure_private_dir(self.path.parent)
        payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True)
        fd, tmp_path = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.write("\n")
            os.replace(tmp_path, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
