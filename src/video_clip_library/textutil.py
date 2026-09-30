from __future__ import annotations

import html
import json
import os
import re
import unicodedata
from typing import Any


VIDEO_SRC_RE = re.compile(r"""<video\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
AUDIO_SRC_RE = re.compile(r"""<audio\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
SOURCE_SRC_RE = re.compile(r"""<source\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
SOUND_RE = re.compile(r"\[sound:([^\]]+)\]", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
TIME_RANGE_RE = re.compile(
    r"_(?P<start>(?:\d+h)?\d{2}m\d{2}s\d{3}ms)_(?P<end>(?:\d+h)?\d{2}m\d{2}s\d{3}ms)\.[^.]+$",
    re.IGNORECASE,
)
SOURCE_RE = re.compile(r"Source:\s*([^|]+)", re.IGNORECASE)
MEDIA_EXTENSIONS = {".mkv", ".mp4", ".webm", ".avi", ".mov", ".m4v", ".ts", ".ogv", ".wmv"}
AUDIO_EXTENSIONS = {".mp3", ".ogg", ".opus", ".wav", ".m4a", ".aac", ".flac", ".oga", ".mpga", ".wma"}
PATH_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def strip_html(value: str) -> str:
    return html.unescape(TAG_RE.sub(" ", value or "")).strip()


def normalize_text(value: str) -> str:
    value = strip_html(value)
    value = unicodedata.normalize("NFKC", value).lower()
    return " ".join(value.split())


def extract_video_filename(value: str) -> str:
    text = value or ""
    match = VIDEO_SRC_RE.search(text)
    if match:
        return os.path.basename(match.group(1).strip())
    match = SOUND_RE.search(text)
    if match:
        return os.path.basename(match.group(1).strip())
    stripped = strip_html(text)
    candidate = os.path.basename(stripped.strip().strip("\"'"))
    if candidate and os.path.splitext(candidate)[1].lower() in MEDIA_EXTENSIONS:
        return candidate
    return ""


def extract_audio_filenames(value: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        name = os.path.basename((raw or "").strip().strip("\"'"))
        if not name:
            return
        key = name.casefold()
        if key in seen:
            return
        seen.add(key)
        names.append(name)

    text = value or ""
    for match in SOUND_RE.finditer(text):
        add(match.group(1))
    for match in AUDIO_SRC_RE.finditer(text):
        add(match.group(1))
    for match in SOURCE_SRC_RE.finditer(text):
        add(match.group(1))
    if not names:
        stripped = strip_html(text)
        candidate = os.path.basename(stripped.strip().strip("\"'"))
        if candidate and os.path.splitext(candidate)[1].lower() in AUDIO_EXTENSIONS:
            add(candidate)
    return names


def parse_human_time(value: str) -> float | None:
    if not value:
        return None
    match = re.fullmatch(
        r"(?:(?P<hours>\d+)h)?(?P<minutes>\d{2})m(?P<seconds>\d{2})s(?P<millis>\d{3})ms",
        value,
    )
    if not match:
        return None
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes"))
    seconds = int(match.group("seconds"))
    millis = int(match.group("millis"))
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def extract_clip_times_from_filename(filename: str) -> tuple[float | None, float | None]:
    match = TIME_RANGE_RE.search(filename or "")
    if not match:
        return None, None
    return parse_human_time(match.group("start")), parse_human_time(match.group("end"))


def extract_miscinfo_source(value: str) -> str:
    match = SOURCE_RE.search(strip_html(value or ""))
    if not match:
        return ""
    return match.group(1).strip()


def normalize_filename(value: str) -> str:
    name = os.path.basename(str(value or "").replace("/", os.sep).replace("\\", os.sep)).strip()
    return name.casefold()


def looks_like_path(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if len(text) < 3:
        return False
    if PATH_DRIVE_RE.match(text) or text.startswith("\\\\") or text.startswith("/"):
        return True
    return ("\\" in text or text.count("/") >= 2) and bool(os.path.splitext(os.path.basename(text))[1])


def obscure_path(path: str, mode: str, parents: int = 1) -> str:
    text = str(path or "")
    if not text or mode in {"", "off", "none"}:
        return text
    separator = "\\" if "\\" in text else "/"
    normalized = text.replace("/", separator).replace("\\", separator)
    parts = [part for part in normalized.split(separator) if part]
    if not parts:
        return text
    filename = parts[-1]
    if mode == "filename_only":
        kept = [filename]
    else:
        keep = max(0, int(parents))
        kept = parts[-(keep + 1) :] if keep else [filename]
    if len(kept) >= len(parts):
        return text
    return "..." + separator + separator.join(kept)


def obscure_paths_in_data(value: Any, mode: str, parents: int = 1) -> Any:
    if mode in {"", "off", "none"}:
        return value
    if isinstance(value, str):
        return obscure_path(value, mode, parents) if looks_like_path(value) else value
    if isinstance(value, list):
        return [obscure_paths_in_data(item, mode, parents) for item in value]
    if isinstance(value, dict):
        return {key: obscure_paths_in_data(item, mode, parents) for key, item in value.items()}
    return value


def example_text(value: Any, limit: int = 180) -> str:
    """A real non-empty value for a tooltip or identity preview. Empty and nil values return ""."""
    if value is None:
        return ""
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, str):
        text = " ".join(value.split())
        if not text or text.casefold() == "null":
            return ""
    elif isinstance(value, (int, float)):
        text = str(value)
    elif isinstance(value, (dict, list)):
        if not value:
            return ""
        text = " ".join(json.dumps(value, ensure_ascii=False, default=str).split())
    else:
        text = " ".join(str(value).split())
        if not text or text.casefold() == "null":
            return ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
