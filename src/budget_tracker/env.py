from __future__ import annotations

import os
from pathlib import Path


def load_project_env() -> None:
    package_root = Path(__file__).resolve().parents[2]
    candidates = [
        package_root.parent / ".env",
        package_root / ".env",
    ]
    for path in candidates:
        if path.exists():
            _load_env_file(path)


def _load_env_file(path: Path) -> None:
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _strip_quotes(value)


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
