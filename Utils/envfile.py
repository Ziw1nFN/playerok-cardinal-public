from __future__ import annotations

import os


def _lines(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return f.read().splitlines()


def _write(path: str, lines: list[str]):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    return stripped.split("=", 1)[0].strip()


def read_env(path: str = ".env") -> dict[str, str]:
    result = {}
    for line in _lines(path):
        key = _key(line)
        if key:
            result[key] = line.split("=", 1)[1].strip()
    return result


def set_env(values: dict[str, str], path: str = ".env") -> None:
    lines = _lines(path)
    pending = dict(values)
    for index, line in enumerate(lines):
        key = _key(line)
        if key in pending:
            lines[index] = f"{key}={pending.pop(key)}"
    lines.extend(f"{key}={value}" for key, value in pending.items())
    _write(path, lines)


def remove_env(keys: list[str], path: str = ".env") -> None:
    drop = set(keys)
    _write(path, [line for line in _lines(path) if _key(line) not in drop])


def comment_env(keys: list[str], path: str = ".env", note: str = "v1") -> None:
    targets = set(keys)
    _write(path, [f"# {note}: {line.strip()}" if _key(line) in targets else line for line in _lines(path)])


def mask(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) < 10:
        return "***"
    return f"{secret[:4]}…{secret[-4:]}"
