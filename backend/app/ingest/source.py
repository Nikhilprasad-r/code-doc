"""Resolve a repository source (git URL, uploaded zip, or local folder) to a directory on disk."""

import os
import re
import shutil
import subprocess
import zipfile
from collections.abc import Iterator
from pathlib import Path

from app.ingest.languages import IGNORED_DIRS, detect_language, is_ignored_file

_GIT_URL = re.compile(r"^https://[\w.-]+(:\d+)?/[\w.~-]+/[\w.~-]+?(\.git)?/?$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


class SourceError(Exception):
    pass


def is_git_url(source: str) -> bool:
    return bool(_GIT_URL.match(source.strip()))


def repo_name(source: str) -> str:
    cleaned = source.rstrip("/").removesuffix(".git").removesuffix(".zip")
    parts = [p for p in re.split(r"[/\\]", cleaned) if p]
    return "/".join(parts[-2:]) if is_git_url(source) else (parts[-1] if parts else "repo")


def _git(*args: str, cwd: Path | None = None, timeout: int = 600) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise SourceError(result.stderr.strip() or f"git {args[0]} failed")
    return result.stdout.strip()


def clone(url: str, dest: Path, ref: str | None = None) -> str:
    """Shallow-clone `url` into `dest` and return the checked-out commit SHA."""
    if not is_git_url(url):
        raise SourceError("Only https:// git URLs are supported")
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if ref and _COMMIT_SHA.match(ref):
        # A pinned commit: fetch just that commit (GitHub allows fetching by SHA).
        dest.mkdir()
        _git("init", "-q", cwd=dest)
        _git("fetch", "-q", "--depth", "1", url, ref, cwd=dest)
        _git("checkout", "-q", "FETCH_HEAD", cwd=dest)
        return _git("rev-parse", "HEAD", cwd=dest)
    args = ["clone", "--depth", "1", "--single-branch"]
    if ref:
        args += ["--branch", ref]
    _git(*args, url, str(dest))
    return _git("rev-parse", "HEAD", cwd=dest)


def extract_zip(zip_path: Path, dest: Path) -> Path:
    """Extract safely (no paths outside `dest`) and unwrap a single top-level folder."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    root = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            target = (dest / member.filename).resolve()
            if not target.is_relative_to(root):
                raise SourceError(f"Unsafe path in archive: {member.filename}")
        zf.extractall(dest)
    entries = [p for p in dest.iterdir() if p.name not in {"__MACOSX", ".DS_Store"}]
    return entries[0] if len(entries) == 1 and entries[0].is_dir() else dest


def local_commit(root: Path) -> str | None:
    try:
        return _git("rev-parse", "HEAD", cwd=root, timeout=10)
    except (SourceError, FileNotFoundError, subprocess.TimeoutExpired):
        return None


def walk(root: Path, max_bytes: int) -> Iterator[tuple[str, str, str | None]]:
    """Yield (relative_path, text, language) for indexable text files under `root`."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and not d.startswith("."))
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            rel = path.relative_to(root).as_posix()
            if is_ignored_file(rel) or path.is_symlink():
                continue
            language = detect_language(rel)
            if language is None and not filename.upper().startswith(("README", "LICENSE", "CHANGELOG")):
                continue
            try:
                if path.stat().st_size > max_bytes:
                    continue
                raw = path.read_bytes()
            except OSError:
                continue
            if b"\0" in raw[:8192]:
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            yield rel, text.replace("\r\n", "\n"), language or "text"
