from __future__ import annotations

import re
import stat
import zipfile
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath

MAX_FILES = 1500
MAX_EXPANDED = 100 * 1024 * 1024
MAX_FILE = 20 * 1024 * 1024
ALLOWED_DIRS = {"Utils", "tg_bot", "playerokapi"}
ALLOWED_FILES = {"main.py", "hub.py", "cardinal.py", "accounts.py", "handlers.py", "plugin_manager.py",
                 "requirements.txt", "README.md", "first_setup.py", "control_server.py"}


class ReleaseError(ValueError):
    pass


def validate_archive(path: str) -> list[str]:
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseError("Invalid ZIP archive") from exc
    with archive:
        entries = archive.infolist()
        if not entries or len(entries) > MAX_FILES:
            raise ReleaseError("Invalid number of files")
        seen, names, total = set(), [], 0
        for entry in entries:
            name = entry.filename
            parts = PurePosixPath(name).parts
            if (not name or name.startswith("/") or "\\" in name or not parts
                    or any(part in (".", "..") for part in name.split("/"))
                    or any(":" in part for part in parts)):
                raise ReleaseError("Unsafe archive path")
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode) or entry.flag_bits & 1:
                raise ReleaseError("Links and encrypted files are not supported")
            if entry.is_dir():
                continue
            if not (len(parts) == 1 and name in ALLOWED_FILES or len(parts) > 1 and parts[0] in ALLOWED_DIRS):
                raise ReleaseError("File is outside the allowed code paths")
            if not re.fullmatch(r"[A-Za-z0-9_./-]+", name) or any(part.startswith(".") for part in parts):
                raise ReleaseError("Invalid filename")
            key = name.casefold()
            if key in seen:
                raise ReleaseError("Duplicate filename")
            seen.add(key)
            total += entry.file_size
            if entry.file_size > MAX_FILE or total > MAX_EXPANDED:
                raise ReleaseError("Archive is too large")
            names.append(name)
        if not names:
            raise ReleaseError("Archive contains no files")
        try:
            if archive.testzip() is not None:
                raise ReleaseError("Damaged file in archive")
        except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
            raise ReleaseError("Damaged archive") from exc
        return names


def install_archive(path: str, root: str, backup_root: str) -> str:
    names = validate_archive(path)
    root_path = Path(root).resolve()
    backup_path = Path(backup_root).resolve()
    backup_path.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="release-stage-", dir=backup_path))
    backup = Path(tempfile.mkdtemp(prefix="release-backup-", dir=backup_path))
    changed = []
    with zipfile.ZipFile(path) as archive:
        for name in names:
            target = root_path.joinpath(*PurePosixPath(name).parts)
            for parent in [target, *target.parents]:
                if parent == root_path:
                    break
                if parent.is_symlink():
                    raise ReleaseError("Symbolic links in destination are not supported")
            staged = stage.joinpath(*PurePosixPath(name).parts)
            staged.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, staged.open("wb") as destination:
                shutil.copyfileobj(source, destination)
            if target.exists():
                if not target.is_file():
                    raise ReleaseError("Destination is not a file")
                saved = backup.joinpath(*PurePosixPath(name).parts)
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
    try:
        for name in names:
            target = root_path.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            previous = target.exists()
            os.replace(stage.joinpath(*PurePosixPath(name).parts), target)
            changed.append((name, previous))
    except OSError:
        for name, previous in reversed(changed):
            target = root_path.joinpath(*PurePosixPath(name).parts)
            if previous:
                shutil.copy2(backup.joinpath(*PurePosixPath(name).parts), target)
            else:
                target.rename(backup / ("new-" + name.replace("/", "-")))
        raise
    return str(backup)
