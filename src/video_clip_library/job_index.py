from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .models import DiscoveredClip, LinkInfo
from .textutil import example_text, extract_video_filename, normalize_filename, obscure_paths_in_data


NO_JOB_EXAMPLE = "No non-empty value in the analyzed records."


def collect_key_paths(value: Any, prefix: str = "") -> set[str]:
    found: set[str] = set()

    def walk(node: Any, current: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                segment = str(key)
                if not segment or "." in segment:
                    continue
                path = f"{current}.{segment}" if current else segment
                found.add(path)
                walk(child, path)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    walk(item, current)

    walk(value, prefix)
    return found


def record_sample_id(record: dict[str, Any] | None, record_path: str = "") -> str:
    record_id = str((record or {}).get("record_id") or "").strip()
    if record_id:
        return f"id:{record_id}"
    path = str(record_path or "").strip()
    return f"path:{path}" if path else ""


def empty_job_tree(deck_name: str, tree_id: str) -> dict[str, Any]:
    return {
        "id": tree_id,
        "name": deck_name,
        "deck_name": deck_name,
        "samples": {},
        "examples": {},
        "keys": {},
        "redundancy_pairs": [],
    }


def collect_key_examples(value: Any, prefix: str = "") -> dict[str, str]:
    """First non-empty, non-nil display value for each key path in one record."""
    found: dict[str, str] = {}

    def consider(path: str, node: Any) -> None:
        if not path or path in found:
            return
        text = example_text(node)
        if text:
            found[path] = text

    def walk(node: Any, current: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                segment = str(key)
                if not segment or "." in segment:
                    continue
                path = f"{current}.{segment}" if current else segment
                consider(path, child)
                walk(child, path)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    walk(item, current)

    walk(value, prefix)
    return found


def apply_key_examples(tree: dict[str, Any], updates: dict[str, str] | None) -> dict[str, Any]:
    """Keep a real example per key. A new non-empty value replaces the previous one."""
    examples: dict[str, str] = {}
    raw = tree.get("examples") if isinstance(tree.get("examples"), dict) else {}
    for path, value in raw.items():
        text = str(path or "").strip()
        cleaned = example_text(value)
        if text and cleaned:
            examples[text] = cleaned
    for path, value in (updates or {}).items():
        text = str(path or "").strip()
        cleaned = example_text(value)
        if text and cleaned:
            examples[text] = cleaned
    updated = dict(tree)
    updated["examples"] = examples
    return updated


def apply_record_samples(
    tree: dict[str, Any],
    updates: dict[str, list[str]],
    *,
    legacy_selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    samples = dict(tree.get("samples") or {})
    for sample_id, paths in updates.items():
        identity = str(sample_id or "").strip()
        if not identity:
            continue
        cleaned: list[str] = []
        seen: set[str] = set()
        for path in paths:
            text = str(path or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            cleaned.append(text)
        samples[identity] = cleaned
    counts: dict[str, int] = {}
    for paths in samples.values():
        for path in paths:
            counts[path] = counts.get(path, 0) + 1
    previous = tree.get("keys") if isinstance(tree.get("keys"), dict) else {}
    legacy = legacy_selection if isinstance(legacy_selection, dict) else {}
    keys: dict[str, dict[str, Any]] = {}
    for path in sorted(set(previous) | set(counts), key=lambda item: item.split(".")):
        old = previous.get(path) if isinstance(previous.get(path), dict) else None
        if old is not None and "enabled" in old:
            enabled = bool(old.get("enabled"))
        elif path in legacy:
            enabled = bool(legacy[path])
        else:
            enabled = True
        keys[path] = {"enabled": enabled, "count": int(counts.get(path, 0))}
    updated = dict(tree)
    updated["samples"] = samples
    updated["keys"] = keys
    return updated


def tree_key_selection(tree: dict[str, Any] | None) -> dict[str, bool]:
    if not isinstance(tree, dict):
        return {}
    raw = tree.get("keys") if isinstance(tree.get("keys"), dict) else {}
    selection: dict[str, bool] = {}
    for path, state in raw.items():
        text = str(path or "").strip()
        if not text:
            continue
        if isinstance(state, dict):
            selection[text] = bool(state.get("enabled", True))
        else:
            selection[text] = bool(state)
    return selection


def unknown_job_key_paths(record: Any, known: set[str]) -> set[str]:
    return {path for path in collect_key_paths(record) if path not in known}


def effective_record_keys(selection: dict[str, Any] | None) -> dict[str, bool]:
    merged: dict[str, bool] = {}
    if isinstance(selection, dict):
        for key, value in selection.items():
            path = str(key or "").strip()
            if path and not isinstance(value, dict):
                merged[path] = bool(value)
    return merged


def is_key_included(path: str, selection: dict[str, bool], *, default_include: bool = True) -> bool:
    parts = path.split(".")
    for index in range(1, len(parts)):
        parent = ".".join(parts[:index])
        if parent in selection and not selection[parent]:
            return False
    if path in selection:
        return bool(selection[path])
    for index in range(len(parts) - 1, 0, -1):
        parent = ".".join(parts[:index])
        if parent in selection:
            return bool(selection[parent])
    return default_include


def filter_record(
    record: Any,
    selection: dict[str, bool],
    prefix: str = "",
    *,
    default_include: bool = True,
) -> Any:
    if isinstance(record, list):
        return [
            filter_record(item, selection, prefix, default_include=default_include)
            if isinstance(item, (dict, list))
            else item
            for item in record
        ]
    if not isinstance(record, dict):
        return record
    filtered: dict[str, Any] = {}
    for key, value in record.items():
        segment = str(key)
        if not segment or "." in segment:
            continue
        path = f"{prefix}.{segment}" if prefix else segment
        if not is_key_included(path, selection, default_include=default_include):
            continue
        if isinstance(value, dict):
            filtered[key] = filter_record(value, selection, path, default_include=default_include)
        elif isinstance(value, list):
            filtered[key] = filter_record(value, selection, path, default_include=default_include)
        else:
            filtered[key] = value
    return filtered


def read_json_file(path: str) -> Any | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def _summary_filenames(summary: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for raw in (
        summary.get("output_path"),
        (summary.get("output") or {}).get("path") if isinstance(summary.get("output"), dict) else "",
        (summary.get("output") or {}).get("filename") if isinstance(summary.get("output"), dict) else "",
    ):
        name = normalize_filename(str(raw or ""))
        if name:
            names.add(name)
    return names


def _record_filenames(record: dict[str, Any]) -> set[str]:
    names = _summary_filenames(record)
    output = record.get("output") if isinstance(record.get("output"), dict) else {}
    anki = record.get("anki") if isinstance(record.get("anki"), dict) else {}
    fields = anki.get("fields") if isinstance(anki.get("fields"), dict) else {}
    video = fields.get("video") if isinstance(fields.get("video"), dict) else {}
    for raw in (
        output.get("filename"),
        output.get("path"),
        anki.get("previous_clip_filename"),
        extract_video_filename(str(video.get("value") or "")),
    ):
        name = normalize_filename(str(raw or ""))
        if name:
            names.add(name)
    return names


def _created_at_key(value: Any) -> str:
    return str(value or "")


@dataclass
class JobMatch:
    summary: dict[str, Any]
    record: dict[str, Any] | None
    record_path: str
    link: LinkInfo = field(default_factory=LinkInfo)


class JobIndex:
    def __init__(self, index_path: str) -> None:
        self.index_path = index_path
        self.base_dir = os.path.dirname(index_path) if index_path else ""
        self.summaries: list[dict[str, Any]] = []
        self.by_filename: dict[str, list[dict[str, Any]]] = {}
        self.errors: list[str] = []
        self._record_cache: dict[str, dict[str, Any] | None] = {}

    @classmethod
    def load(cls, index_path: str) -> "JobIndex":
        if index_path and os.path.isdir(index_path):
            index_path = os.path.join(index_path, "index.json")
        index = cls(index_path)
        if not index_path:
            index.errors.append("No job index path configured.")
            return index
        decoded = read_json_file(index_path)
        if not isinstance(decoded, dict) or not isinstance(decoded.get("records"), list):
            index.errors.append(f"Could not read job index: {index_path}")
            return index
        for summary in decoded.get("records", []):
            if not isinstance(summary, dict):
                continue
            index.summaries.append(summary)
            for name in _summary_filenames(summary):
                index.by_filename.setdefault(name, []).append(summary)
        return index

    def resolve_record_path(self, summary: dict[str, Any]) -> str:
        relative = str(summary.get("record_path") or "").replace("/", os.sep).replace("\\", os.sep)
        if not relative:
            return ""
        return os.path.join(self.base_dir, relative)

    def load_record(self, summary: dict[str, Any]) -> dict[str, Any] | None:
        path = self.resolve_record_path(summary)
        if not path:
            return None
        if path in self._record_cache:
            return self._record_cache[path]
        decoded = read_json_file(path)
        record = decoded if isinstance(decoded, dict) else None
        self._record_cache[path] = record
        return record

    def match_filename(self, filename: str) -> JobMatch | None:
        key = normalize_filename(filename)
        if not key:
            return None
        candidates = list(self.by_filename.get(key, []))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (_created_at_key(item.get("created_at")), str(item.get("record_id") or "")), reverse=True)
        best = candidates[0]
        record = self.load_record(best)
        if record and key not in _record_filenames(record):
            record_names = _record_filenames(record)
            if key not in record_names:
                pass
        return JobMatch(summary=best, record=record, record_path=self.resolve_record_path(best))

    def match_clip(self, clip: DiscoveredClip) -> JobMatch | None:
        match = self.match_filename(clip.filename)
        if not match:
            return None
        record = match.record or {}
        anki = record.get("anki") if isinstance(record.get("anki"), dict) else {}
        job = record.get("job") if isinstance(record.get("job"), dict) else {}
        matched_by = ["output_filename"]
        mismatches: list[str] = []

        record_note_id = anki.get("note_id")
        if record_note_id in (None, ""):
            note_ids = anki.get("note_ids") if isinstance(anki.get("note_ids"), list) else []
            record_note_id = note_ids[0] if note_ids else None
        try:
            record_note_id_int = int(record_note_id) if record_note_id not in (None, "") else None
        except (TypeError, ValueError):
            record_note_id_int = None
        if record_note_id_int is not None and int(clip.note_id) != record_note_id_int:
            mismatches.append("note_id")

        video_field = ""
        fields = anki.get("fields") if isinstance(anki.get("fields"), dict) else {}
        video = fields.get("video") if isinstance(fields.get("video"), dict) else {}
        video_field = str(video.get("name") or "")
        clip_video_field = clip.fields.get("video").name if clip.fields.get("video") else ""
        if video_field and clip_video_field and video_field != clip_video_field:
            mismatches.append("field_set")

        record_field_set = job.get("active_field_set_index")
        try:
            record_field_set_int = int(record_field_set) if record_field_set not in (None, "") else None
        except (TypeError, ValueError):
            record_field_set_int = None
        if record_field_set_int is not None and record_field_set_int != clip.field_set_index:
            if "field_set" not in mismatches:
                mismatches.append("field_set")

        if str(anki.get("deck_name") or "") and clip.deck_name and str(anki.get("deck_name")) != clip.deck_name:
            mismatches.append("deck_name")
        if str(anki.get("model_name") or "") and clip.model_name and str(anki.get("model_name")) != clip.model_name:
            mismatches.append("model_name")

        match.link = LinkInfo(
            status="partial" if mismatches else "linked",
            matched_by=matched_by,
            mismatches=mismatches,
        )
        return match


def prepare_job_payload(
    record: dict[str, Any] | None,
    *,
    selection: dict[str, Any] | None,
    privacy_mode: str,
    privacy_parents: int,
    strict: bool = False,
    apply_privacy: bool = True,
) -> dict[str, Any] | None:
    if not record:
        return None
    filtered = filter_record(record, effective_record_keys(selection), default_include=not strict)
    if not apply_privacy:
        return filtered if isinstance(filtered, dict) else None
    return obscure_paths_in_data(filtered, privacy_mode, privacy_parents)
