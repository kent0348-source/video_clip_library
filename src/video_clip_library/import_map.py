from __future__ import annotations

import os
import re
import shutil
from typing import Any, Callable

from .config import (
    SORT_FIELD_ID,
    enabled_import_field_sets,
    import_point_value,
    normalize_import_copies,
    normalize_import_rules,
)
from .models import MEDIA_DIRNAME, ImportCandidate, ImportDecision
from .textutil import (
    extract_audio_filenames,
    extract_clip_times_from_filename,
    extract_miscinfo_source,
    extract_video_filename,
    field_has_content,
    normalize_filename,
    normalize_text,
    strip_html,
)


VIDEO_TAG_RE = re.compile(r"""(<video\b[^>]*\bsrc\s*=\s*["'])([^"']+)(["'])""", re.IGNORECASE)


def resolve_clip_anki(clip: dict[str, Any], notes: dict[str, Any] | None = None) -> dict[str, Any]:
    """Join clip-local anki fields with the shared note record.

    Clip keys win. notes may be passed explicitly or attached as clip['_library_notes'].
    """
    anki = clip.get("anki")
    local = dict(anki) if isinstance(anki, dict) else {}
    if notes is None:
        attached = clip.get("_library_notes")
        notes = attached if isinstance(attached, dict) else None
    if not isinstance(notes, dict):
        return local
    note_id = local.get("note_id")
    if note_id in (None, ""):
        return local
    shared = notes.get(str(note_id))
    if not isinstance(shared, dict):
        return local
    merged = dict(shared)
    merged.update(local)
    return merged


def clip_anki(clip: dict[str, Any]) -> dict[str, Any]:
    return resolve_clip_anki(clip)


def attach_library_notes(clip: dict[str, Any], notes: dict[str, Any] | None) -> dict[str, Any]:
    item = dict(clip)
    item["_library_notes"] = notes if isinstance(notes, dict) else {}
    return item


def clip_fields(clip: dict[str, Any]) -> dict[str, dict[str, str]]:
    fields = clip_anki(clip).get("fields")
    return fields if isinstance(fields, dict) else {}


def clip_field_value(clip: dict[str, Any], role: str) -> str:
    field = clip_fields(clip).get(role)
    if isinstance(field, dict):
        return str(field.get("value") or "")
    return ""


_SEARCH_QUOTE_RE = re.compile(r"(?i)^and$|^or$|^-.| |\u3000|\(|\)")
_CLIP_SEARCH_ROLES: tuple[tuple[str, str], ...] = (
    ("sentence", "Sentence"),
    ("secondary", "Secondary"),
    ("miscinfo", "Miscinfo"),
    ("video", "Video"),
)


def clip_search_fields(clip: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Non-empty clip fields the target-note search can insert, raw values included."""
    if not isinstance(clip, dict):
        return []
    anki = clip_anki(clip)
    found: list[tuple[str, str]] = []
    fields = anki.get("fields") if isinstance(anki.get("fields"), dict) else {}
    used_roles: set[str] = set()
    for role, label in _CLIP_SEARCH_ROLES:
        used_roles.add(role)
        _append_search_field(found, label, fields.get(role), role)
    for role, item in fields.items():
        role_id = str(role or "")
        if role_id in used_roles:
            continue
        _append_search_field(found, f"Field: {_field_name(item, role_id)}", item, role_id)
    extras = anki.get("extra_fields") if isinstance(anki.get("extra_fields"), dict) else {}
    for key, item in extras.items():
        _append_search_field(found, f"Extra: {_field_name(item, str(key))}", item, str(key))
    audios = anki.get("audio_fields") if isinstance(anki.get("audio_fields"), dict) else {}
    for key, item in audios.items():
        _append_search_field(found, f"Audio: {_field_name(item, str(key))}", item, str(key))
    return found


def _field_name(item: Any, fallback: str) -> str:
    if isinstance(item, dict):
        name = str(item.get("name") or "").strip()
        if name:
            return name
    return fallback or "field"


def _append_search_field(found: list[tuple[str, str]], label: str, item: Any, fallback: str) -> None:
    value = ""
    if isinstance(item, dict):
        value = str(item.get("value") or "")
    elif item is not None:
        value = str(item)
    if not value.strip():
        return
    found.append((label or fallback or "Field", value))


def quote_anki_search_term(text: str) -> str:
    """Quote one field value the way Anki's browser quotes a literal search term.

    Line breaks become spaces, matching the browser search box. ``*``, ``_``,
    ``\\``, ``:`` and quotes are escaped so the value is one term, including
    HTML such as a video tag.
    """
    collapsed = re.sub(r"\s", " ", str(text or "")).strip()
    if not collapsed:
        return ""
    escaped = re.sub(r"[\\*_]", lambda match: "\\" + match.group(0), collapsed)
    escaped = escaped.replace(":", "\\:")
    if _SEARCH_QUOTE_RE.search(escaped):
        return '"' + escaped.replace('"', '\\"') + '"'
    return escaped.replace('"', '\\"')


def search_term_for_field(text: str) -> str:
    """Build a browser search term, using Anki's own quoter when the collection is open."""
    collapsed = re.sub(r"\s", " ", str(text or "")).strip()
    if not collapsed:
        return ""
    try:
        from aqt import mw
        from anki.collection import SearchNode

        col = getattr(mw, "col", None)
        if col is not None and hasattr(col, "build_search_string"):
            built = str(col.build_search_string(SearchNode(literal_text=collapsed)) or "").strip()
            if built:
                return built
    except Exception:
        pass
    return quote_anki_search_term(collapsed)


def insert_search_term(
    query: str,
    term: str,
    cursor: int,
    sel_start: int = -1,
    sel_end: int = -1,
) -> tuple[str, int]:
    """Insert a search term at the cursor, or in place of the current selection."""
    if not term:
        return query, max(0, cursor)
    if sel_start >= 0 and sel_end >= 0 and sel_start != sel_end:
        start, end = sorted((sel_start, sel_end))
    else:
        start = end = max(0, min(int(cursor), len(query)))
    prefix = query[:start]
    suffix = query[end:]
    piece = term
    if prefix and not prefix[-1].isspace():
        piece = " " + piece
    if suffix and not suffix[0].isspace():
        piece += " "
    updated = prefix + piece + suffix
    return updated, len(prefix) + len(piece)


def clip_filename(clip: dict[str, Any]) -> str:
    media = clip.get("media") if isinstance(clip.get("media"), dict) else {}
    return str(media.get("filename") or "")


def clip_source_name(clip: dict[str, Any]) -> str:
    miscinfo = clip_field_value(clip, "miscinfo")
    source = extract_miscinfo_source(miscinfo)
    if source:
        return os.path.basename(source)
    job = clip.get("job") if isinstance(clip.get("job"), dict) else {}
    job_source = job.get("source") if isinstance(job.get("source"), dict) else {}
    for raw in (job_source.get("raw_filename"), job_source.get("path"), job_source.get("media_title")):
        name = os.path.basename(str(raw or ""))
        if name:
            return name
    return ""


def rewrite_video_html(original: str, filename: str) -> str:
    if original and VIDEO_TAG_RE.search(original):
        return VIDEO_TAG_RE.sub(lambda match: f"{match.group(1)}{filename}{match.group(3)}", original, count=1)
    return f'<video controls="" src="{filename}"></video>'


BASE_SOURCES: tuple[tuple[str, str], ...] = (
    ("sentence", "Sentence text"),
    ("secondary", "Secondary text"),
    ("miscinfo", "Miscinfo text"),
    ("video_filename", "Video filename"),
    ("source_filename", "Source filename"),
    ("sort_field", "Sort field text"),
    ("deck_name", "Deck name"),
    ("timestamps", "Clip timestamps"),
)


def _lookup_path(payload: Any, path: str) -> str:
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return ""
        current = current.get(part)
    if isinstance(current, str):
        return current
    return ""


def _job_string_paths(payload: Any, prefix: str = "", depth: int = 0) -> list[str]:
    if depth > 4 or not isinstance(payload, dict):
        return []
    paths: list[str] = []
    for key, value in payload.items():
        name = str(key or "").strip()
        if not name or name.startswith("_") or name == "note_id":
            continue
        path = f"{prefix}.{name}" if prefix else name
        if isinstance(value, str) and value.strip():
            paths.append(path)
        elif isinstance(value, dict):
            paths.extend(_job_string_paths(value, path, depth + 1))
    return paths


def clip_source_text(clip: dict[str, Any], source_id: str) -> str:
    source_id = str(source_id or "")
    if source_id == "note_id" or source_id.endswith(".note_id"):
        return ""
    if source_id == "sentence":
        return clip_field_value(clip, "sentence")
    if source_id == "secondary":
        return clip_field_value(clip, "secondary")
    if source_id == "miscinfo":
        return clip_field_value(clip, "miscinfo")
    if source_id == "video_filename":
        return clip_filename(clip)
    if source_id == "source_filename":
        return clip_source_name(clip)
    if source_id == "sort_field":
        return str(clip_anki(clip).get("sort_field_value") or "")
    if source_id == "deck_name":
        return str(clip_anki(clip).get("deck_name") or "")
    if source_id == "timestamps":
        return clip_filename(clip)
    if source_id.startswith("field:"):
        return clip_field_value(clip, source_id.split(":", 1)[1])
    if source_id.startswith("extra:"):
        key = source_id.split(":", 1)[1]
        extras = clip_anki(clip).get("extra_fields")
        item = extras.get(key) if isinstance(extras, dict) else None
        if isinstance(item, dict):
            return str(item.get("value") or "")
        return ""
    if source_id.startswith("audio:"):
        key = source_id.split(":", 1)[1]
        audios = clip_anki(clip).get("audio_fields")
        item = audios.get(key) if isinstance(audios, dict) else None
        if isinstance(item, dict):
            return str(item.get("value") or "")
        return ""
    if source_id.startswith("job:"):
        job = clip.get("job") if isinstance(clip.get("job"), dict) else {}
        return _lookup_path(job, source_id.split(":", 1)[1])
    return ""


def source_samples(clips: list[dict[str, Any]]) -> dict[str, str]:
    from .textutil import example_text

    samples: dict[str, str] = {}
    for source_id, _label in source_catalog(clips):
        for clip in clips:
            text = example_text(clip_source_text(clip, source_id))
            if text:
                samples[source_id] = text
                break
    return samples


def source_catalog(clips: list[dict[str, Any]]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(source_id: str, label: str) -> None:
        if not source_id or source_id in seen or source_id == "note_id" or source_id.endswith(".note_id"):
            return
        seen.add(source_id)
        found.append((source_id, label))

    for source_id, label in BASE_SOURCES:
        if source_id == SORT_FIELD_ID:
            if any("sort_field_value" in clip_anki(clip) for clip in clips):
                add(source_id, label)
            continue
        if any(clip_source_text(clip, source_id) for clip in clips):
            add(source_id, label)
    for clip in clips:
        anki = clip_anki(clip)
        fields = anki.get("fields") if isinstance(anki.get("fields"), dict) else {}
        for role, item in fields.items():
            role_id = str(role or "")
            if role_id in {"video", "sentence", "secondary", "miscinfo"} or not isinstance(item, dict):
                continue
            if str(item.get("value") or "").strip():
                add(f"field:{role_id}", f"Clip field: {item.get('name') or role_id}")
        extras = anki.get("extra_fields") if isinstance(anki.get("extra_fields"), dict) else {}
        for key, item in extras.items():
            if isinstance(item, dict) and str(item.get("value") or "").strip():
                add(f"extra:{key}", f"Extra field: {item.get('name') or key}")
        audios = anki.get("audio_fields") if isinstance(anki.get("audio_fields"), dict) else {}
        for key, item in audios.items():
            if isinstance(item, dict) and (str(item.get("value") or "").strip() or item.get("files") or item.get("media")):
                add(f"audio:{key}", f"Audio: {item.get('name') or key}")
        job = clip.get("job") if isinstance(clip.get("job"), dict) else {}
        for path in _job_string_paths(job):
            if "note_id" in path.split("."):
                continue
            add(f"job:{path}", f"Job: {path}")
            if len(found) >= 80:
                return found
    if not found:
        add("video_filename", "Video filename")
    return found


def _target_text(note: dict[str, Any], field_name: str) -> str:
    if field_name == SORT_FIELD_ID:
        return str(note.get("sort_field_value") or "")
    fields = note.get("fields") if isinstance(note.get("fields"), dict) else {}
    if field_name in fields:
        return str(fields.get(field_name) or "")
    if field_name and field_name == str(note.get("sort_field_name") or ""):
        return str(note.get("sort_field_value") or "")
    return ""


def _filename_text(value: str) -> str:
    return extract_video_filename(value) or os.path.basename(str(value or ""))


def _times_close(left: tuple[float | None, float | None], right: tuple[float | None, float | None], compare: str) -> bool:
    left_start, left_end = left
    right_start, right_end = right
    if None in (left_start, left_end, right_start, right_end):
        return False
    assert left_start is not None and left_end is not None and right_start is not None and right_end is not None
    if abs(left_start - right_start) < 0.05 and abs(left_end - right_end) < 0.05:
        return True
    if compare != "contains":
        return False
    overlap = max(0.0, min(left_end, right_end) - max(left_start, right_start))
    duration = max(left_end - left_start, right_end - right_start, 0.001)
    return overlap / duration >= 0.9


def rule_matches(clip: dict[str, Any], note: dict[str, Any], rule: dict[str, str]) -> bool:
    source_id = str(rule.get("source") or "")
    compare = str(rule.get("compare") or "exact")
    target = _target_text(note, str(rule.get("target_field") or ""))
    if source_id == "timestamps":
        left = extract_clip_times_from_filename(clip_filename(clip))
        right = extract_clip_times_from_filename(_filename_text(target) or target)
        return _times_close(left, right, compare)
    left = clip_source_text(clip, source_id)
    if source_id in {"video_filename", "source_filename"}:
        left_name = normalize_filename(_filename_text(left) or left)
        right_name = normalize_filename(_filename_text(target) or target)
        if not left_name or not right_name:
            return False
        if compare == "contains":
            return left_name in right_name or right_name in left_name
        return left_name == right_name
    left_text = normalize_text(left)
    right_text = normalize_text(target)
    if not left_text or not right_text:
        return False
    if compare == "contains":
        return left_text in right_text or right_text in left_text
    return left_text == right_text


SUGGESTION_LIMIT = 40


class _PreparedRule:
    def __init__(self, source_id: str, compare: str, target_field: str, points: float, kind: str) -> None:
        self.source_id = source_id
        self.compare = compare
        self.target_field = target_field
        self.points = points
        self.kind = kind


class _MatchIndex:
    def __init__(self, notes: list[dict[str, Any]], rules: list[_PreparedRule], config: dict[str, Any]) -> None:
        self.notes = notes
        self.rules = rules
        self.config = config
        self.targets: list[list[Any]] = []
        self.exact: list[dict[str, list[int]]] = [{} for _ in rules]
        self.cache: dict[tuple[Any, ...], list[tuple[float, int, int]]] = {}
        for note_index, note in enumerate(notes):
            row: list[Any] = []
            for rule_index, rule in enumerate(rules):
                prepared = _prepare_target(note, rule)
                row.append(prepared)
                if rule.kind != "timestamps" and rule.compare == "exact" and prepared:
                    self.exact[rule_index].setdefault(prepared, []).append(note_index)
            self.targets.append(row)


def _rule_kind(source_id: str) -> str:
    if source_id == "timestamps":
        return "timestamps"
    if source_id in {"video_filename", "source_filename"}:
        return "filename"
    return "text"


def _prepare_target(note: dict[str, Any], rule: _PreparedRule) -> Any:
    raw = _target_text(note, rule.target_field)
    if rule.kind == "timestamps":
        return extract_clip_times_from_filename(_filename_text(raw) or raw)
    if rule.kind == "filename":
        return normalize_filename(_filename_text(raw) or raw)
    return normalize_text(raw)


def _clip_comparable(clip: dict[str, Any], rule: _PreparedRule) -> Any:
    if rule.kind == "timestamps":
        return extract_clip_times_from_filename(clip_filename(clip))
    raw = clip_source_text(clip, rule.source_id)
    if rule.kind == "filename":
        return normalize_filename(_filename_text(raw) or raw)
    return normalize_text(raw)


def build_match_index(notes: list[dict[str, Any]], config: dict[str, Any]) -> _MatchIndex:
    rules: list[_PreparedRule] = []
    for rule in normalize_import_rules(config.get("import_rules")):
        points = import_point_value(str(rule.get("points") or "medium"))
        if points <= 0:
            continue
        source_id = str(rule.get("source") or "")
        rules.append(
            _PreparedRule(
                source_id=source_id,
                compare=str(rule.get("compare") or "exact"),
                target_field=str(rule.get("target_field") or ""),
                points=points,
                kind=_rule_kind(source_id),
            )
        )
    return _MatchIndex(notes, rules, config)


def _matching_note_indexes(index: _MatchIndex, rule_index: int, rule: _PreparedRule, left: Any) -> list[int]:
    if rule.kind == "timestamps":
        if not isinstance(left, tuple) or None in left:
            return []
        return [
            note_index
            for note_index, row in enumerate(index.targets)
            if _times_close(left, row[rule_index], rule.compare)
        ]
    if not left:
        return []
    if rule.compare == "exact":
        return list(index.exact[rule_index].get(left, ()))
    matches: list[int] = []
    for note_index, row in enumerate(index.targets):
        right = row[rule_index]
        if right and (left in right or right in left):
            matches.append(note_index)
    return matches


def _positive_order(clip: dict[str, Any], index: _MatchIndex) -> list[tuple[float, int, int]]:
    lefts = tuple(_clip_comparable(clip, rule) for rule in index.rules)
    cached = index.cache.get(lefts)
    if cached is not None:
        return cached
    totals = [0.0] * len(index.notes)
    for rule_index, rule in enumerate(index.rules):
        for note_index in _matching_note_indexes(index, rule_index, rule, lefts[rule_index]):
            totals[note_index] += rule.points
    order: list[tuple[float, int, int]] = []
    for note_index, total in enumerate(totals):
        if total <= 0:
            continue
        note_id = int(index.notes[note_index].get("note_id") or 0)
        order.append((round(total, 3), note_id, note_index))
    order.sort(key=lambda item: (item[0], item[1]), reverse=True)
    index.cache[lefts] = order
    return order


def _top_candidates(clip: dict[str, Any], index: _MatchIndex, limit: int | None) -> tuple[list[ImportCandidate], int]:
    order = _positive_order(clip, index)
    chosen = order if limit is None else order[:limit]
    return [score_note(clip, index.notes[item[2]], index.config) for item in chosen], len(order)


def score_note(clip: dict[str, Any], note: dict[str, Any], config: dict[str, Any]) -> ImportCandidate:
    breakdown: dict[str, float] = {}
    total = 0.0
    for index, rule in enumerate(normalize_import_rules(config.get("import_rules")), start=1):
        if not rule_matches(clip, note, rule):
            continue
        points = import_point_value(str(rule.get("points") or "medium"))
        if points <= 0:
            continue
        label = f"{rule.get('source')}={rule.get('target_field')}"
        if label in breakdown:
            label = f"{label}#{index}"
        breakdown[label] = points
        total += points
    fields = note.get("fields") if isinstance(note.get("fields"), dict) else {}
    return ImportCandidate(
        note_id=int(note.get("note_id") or 0),
        deck_name=str(note.get("deck_name") or ""),
        sort_field_value=str(note.get("sort_field_value") or ""),
        sort_field_name=str(note.get("sort_field_name") or ""),
        score=round(total, 3),
        signals=breakdown,
        field_values={str(name): str(value or "") for name, value in fields.items()},
    )


def rank_notes(clip: dict[str, Any], notes: list[dict[str, Any]], config: dict[str, Any], limit: int = 8) -> list[ImportCandidate]:
    ranked, _total = _top_candidates(clip, build_match_index(notes, config), limit)
    return ranked


def designated_import_fields(field_set) -> list[str]:
    used = field_set.used_fields() if hasattr(field_set, "used_fields") else []
    names: list[str] = []
    seen: set[str] = set()
    for name in used:
        cleaned = str(name or "").strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        names.append(cleaned)
    return names


def import_copy_targets(config: dict[str, Any], note: dict[str, Any] | None = None) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    sort_name = str((note or {}).get("sort_field_name") or "").strip()
    for item in normalize_import_copies(config.get("import_copies")):
        cleaned = str(item.get("target_field") or "").strip()
        if cleaned == SORT_FIELD_ID:
            cleaned = sort_name
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        names.append(cleaned)
    return names


def resolved_note_field(note: Any, field_name: str) -> str:
    """Map the sort-field token to the note's real sort field. Other names pass through."""
    name = str(field_name or "").strip()
    if name != SORT_FIELD_ID:
        return name
    if isinstance(note, dict):
        return str(note.get("sort_field_name") or "").strip()
    try:
        from .anki_scan import _sort_field

        resolved, _value = _sort_field(note)
        return str(resolved or "").strip()
    except Exception:
        return ""


def _field_blocked(name: str, note_fields: dict[str, str], blocked: set[str]) -> bool:
    if name.casefold() in blocked:
        return True
    return field_has_content(str(note_fields.get(name) or ""))


def _copy_targets_blocked(
    note: dict[str, Any],
    note_fields: dict[str, str],
    config: dict[str, Any],
    blocked: set[str],
) -> bool:
    for item in normalize_import_copies(config.get("import_copies")):
        target = str(item.get("target_field") or "").strip()
        if not target:
            continue
        if target == SORT_FIELD_ID:
            name = str(note.get("sort_field_name") or "").strip()
            if name:
                if _field_blocked(name, note_fields, blocked):
                    return True
            elif field_has_content(str(note.get("sort_field_value") or "")):
                return True
            continue
        if _field_blocked(target, note_fields, blocked):
            return True
    return False


def import_destination_block(
    note_fields: dict[str, str],
    field_set,
    config: dict[str, Any],
    reserved_names: set[str] | None = None,
    note: dict[str, Any] | None = None,
) -> str:
    """Why this destination cannot be written. Empty string means it is clear."""
    blocked = {str(name).casefold() for name in (reserved_names or set())}
    if _copy_targets_blocked(note or {}, note_fields, config, blocked):
        return "occupied_copy"
    if any(_field_blocked(name, note_fields, blocked) for name in designated_import_fields(field_set)):
        return "no_slot"
    return ""


def first_empty_field_set(
    note_fields: dict[str, str],
    config: dict[str, Any],
    reserved: set[int] | None = None,
    reserved_names: set[str] | None = None,
    note: dict[str, Any] | None = None,
) -> tuple[int, str]:
    taken = reserved or set()
    field_sets = enabled_import_field_sets(config)
    if not field_sets:
        return 0, "none"
    blocked = {str(name).casefold() for name in (reserved_names or set())}
    if _copy_targets_blocked(note or {}, note_fields, config, blocked):
        return 0, "occupied_copy"
    for field_set in field_sets:
        if field_set.index in taken:
            continue
        if any(_field_blocked(name, note_fields, blocked) for name in designated_import_fields(field_set)):
            continue
        return field_set.index, "first_empty"
    return 0, "no_slot"


def first_unreserved_field_set(
    config: dict[str, Any],
    reserved: set[int] | None = None,
) -> tuple[int, str]:
    """First enabled field set not already claimed in this batch, ignoring field contents."""
    taken = reserved or set()
    field_sets = enabled_import_field_sets(config)
    if not field_sets:
        return 0, "none"
    for field_set in field_sets:
        if field_set.index in taken:
            continue
        return field_set.index, "overwrite"
    return 0, "no_slot"


def choose_target_field_set(
    clip: dict[str, Any],
    note_fields: dict[str, str],
    config: dict[str, Any],
    *,
    prefer_empty: bool = False,
) -> tuple[int, str]:
    _ = clip, prefer_empty
    return first_empty_field_set(note_fields, config)


def copy_into_media(source_path: str, media_directory: str, filename: str) -> str:
    os.makedirs(media_directory, exist_ok=True)
    destination = os.path.join(media_directory, filename)
    if os.path.exists(destination):
        try:
            if os.path.getsize(destination) == os.path.getsize(source_path):
                return filename
        except OSError:
            pass
        stem, extension = os.path.splitext(filename)
        index = 2
        while True:
            candidate = f"{stem}_{index}{extension}"
            candidate_path = os.path.join(media_directory, candidate)
            if not os.path.exists(candidate_path):
                shutil.copy2(source_path, candidate_path)
                return candidate
            try:
                if os.path.getsize(candidate_path) == os.path.getsize(source_path):
                    return candidate
            except OSError:
                pass
            index += 1
    shutil.copy2(source_path, destination)
    return filename


def mapped_write_fields(field_set) -> list[tuple[str, str]]:
    """Optional role id and note field. Video is written separately."""
    mapped: list[tuple[str, str]] = []
    seen: set[str] = set()
    video_name = str(getattr(field_set, "video", "") or "").strip()
    if video_name:
        seen.add(video_name.casefold())
    for role in ("sentence", "secondary", "miscinfo"):
        name = str(getattr(field_set, role, "") or "").strip()
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        mapped.append((role, name))
    extra_map = getattr(field_set, "extra_map", None)
    extras = extra_map() if callable(extra_map) else getattr(field_set, "extras", {})
    if isinstance(extras, dict):
        for role_id, name in extras.items():
            role = str(role_id or "").strip()
            cleaned = str(name or "").strip()
            if not role or not cleaned or cleaned.casefold() in seen:
                continue
            seen.add(cleaned.casefold())
            mapped.append((role, cleaned))
    return mapped


def describe_writes(field_set, copies: list[dict[str, str]] | None = None) -> str:
    parts: list[str] = []
    video_name = str(getattr(field_set, "video", "") or "")
    if video_name:
        parts.append(video_name)
    parts.extend(name for _role, name in mapped_write_fields(field_set))
    for item in copies or []:
        name = str(item.get("target_field") or "")
        if name == SORT_FIELD_ID:
            name = "Sort field"
        if name and name not in parts:
            parts.append(name)
    return ", ".join(parts)


def rewrite_named_files(original: str, renames: dict[str, str]) -> str:
    updated = original or ""
    for old, new in renames.items():
        if old and new and old != new:
            updated = updated.replace(old, new)
    return updated


def apply_fields_to_note(
    note: Any,
    field_set,
    clip: dict[str, Any],
    media_filename: str,
    copies: list[dict[str, str]] | None = None,
    renames: dict[str, str] | None = None,
) -> list[str]:
    updated: list[str] = []
    file_renames = renames or {}
    video_name = str(getattr(field_set, "video", "") or "")
    if video_name and video_name in note:
        note[video_name] = rewrite_video_html(clip_field_value(clip, "video"), media_filename)
        updated.append(video_name)
    for role, field_name in mapped_write_fields(field_set):
        if not field_name or field_name not in note or field_name in updated:
            continue
        value = clip_field_value(clip, role)
        if not value:
            continue
        note[field_name] = value
        updated.append(field_name)
    for item in normalize_import_copies(copies):
        requested = str(item.get("target_field") or "")
        field_name = resolved_note_field(note, requested)
        source_id = str(item.get("source") or "")
        if requested == SORT_FIELD_ID and not field_name:
            continue
        if not field_name or field_name not in note:
            continue
        value = clip_source_text(clip, source_id)
        if not value:
            continue
        if source_id.startswith("audio:"):
            value = rewrite_named_files(value, file_renames)
        note[field_name] = value
        updated.append(field_name)
    return updated


def library_media_file(library_root: str, filename: str, relative_path: str = "") -> str:
    """On-disk path for a library media file. Filename under media/ is the contract."""
    name = str(filename or "").strip()
    if name:
        return os.path.join(library_root, MEDIA_DIRNAME, name)
    relative = str(relative_path or "").replace("\\", "/").strip()
    return os.path.join(library_root, relative.replace("/", os.sep)) if relative else ""


def library_media_path(library_root: str, clip: dict[str, Any]) -> str:
    media = clip.get("media") if isinstance(clip.get("media"), dict) else {}
    return library_media_file(library_root, str(media.get("filename") or ""), str(media.get("relative_path") or ""))


def field_set_record(clip: dict[str, Any], field_sets: list[Any] | None = None) -> dict[str, Any] | None:
    """Field set named by clips[].anki.field_set_index in the library field_sets list."""
    sets = field_sets if isinstance(field_sets, list) else clip.get("_library_field_sets")
    if not isinstance(sets, list):
        return None
    try:
        wanted = int(clip_anki(clip).get("field_set_index"))
    except (TypeError, ValueError):
        return None
    for item in sets:
        if not isinstance(item, dict):
            continue
        try:
            if int(item.get("index")) == wanted:
                return item
        except (TypeError, ValueError):
            continue
    return None


def field_set_display_name(clip: dict[str, Any], field_sets: list[Any] | None = None) -> str:
    found = field_set_record(clip, field_sets)
    if found:
        return str(found.get("name") or "")
    return str(clip_anki(clip).get("field_set_name") or "")


def clip_audio_media(clip: dict[str, Any]) -> list[dict[str, Any]]:
    """Audio file records for a clip, preferring the shared note record."""
    entries: list[dict[str, Any]] = []
    audios = clip_anki(clip).get("audio_fields")
    if isinstance(audios, dict):
        for field_name, item in audios.items():
            if not isinstance(item, dict):
                continue
            for media_item in item.get("media") or []:
                if not isinstance(media_item, dict):
                    continue
                row = dict(media_item)
                row.setdefault("field", str(field_name))
                entries.append(row)
    if entries:
        return entries
    media = clip.get("media") if isinstance(clip.get("media"), dict) else {}
    legacy = media.get("audio")
    return [item for item in legacy if isinstance(item, dict)] if isinstance(legacy, list) else []


def library_audio_fields(library_root: str, clip: dict[str, Any]) -> list[tuple[str, list[tuple[str, str]]]]:
    entries = clip_audio_media(clip)
    fields: dict[str, list[tuple[str, str]]] = {}
    seen: dict[str, set[str]] = {}

    def add(field_name: str, filename: str, relative_path: str = "") -> None:
        field_name = field_name.strip() or "Audio"
        filename = filename.strip()
        if not filename:
            return
        path = library_media_file(library_root, filename, relative_path)
        key = os.path.normcase(os.path.normpath(path))
        field_seen = seen.setdefault(field_name, set())
        if key not in field_seen:
            field_seen.add(key)
            fields.setdefault(field_name, []).append((filename, path))

    for item in entries:
        if isinstance(item, dict):
            add(
                str(item.get("field") or ""),
                str(item.get("filename") or ""),
                str(item.get("relative_path") or ""),
            )
    if fields:
        return list(fields.items())

    # Libraries exported before media.audio was added still retain note audio filenames.
    audios = clip_anki(clip).get("audio_fields")
    if isinstance(audios, dict):
        for field_name, item in audios.items():
            if not isinstance(item, dict):
                continue
            for filename in item.get("files") or []:
                add(str(field_name), str(filename))
            if not item.get("files") and not item.get("media"):
                for filename in extract_audio_filenames(str(item.get("value") or "")):
                    add(str(field_name), filename)
    return list(fields.items())


def library_audio_paths(library_root: str, clip: dict[str, Any]) -> list[tuple[str, str]]:
    paths: list[tuple[str, str]] = []
    seen: set[str] = set()
    for _, field_paths in library_audio_fields(library_root, clip):
        for filename, path in field_paths:
            key = os.path.normcase(os.path.normpath(path))
            if key not in seen:
                seen.add(key)
                paths.append((filename, path))
    return paths


POPULATED_SKIP_REASONS = {"no_slot", "occupied_copy", "overwrite"}


def import_action_label(decision: ImportDecision) -> str:
    """Combo label. Populated-field skips can be switched to an overwrite import."""
    if decision.overwrite or decision.conflict_resolution in POPULATED_SKIP_REASONS:
        return "Import (overwrite)"
    return "Import"


def plan_imports(
    clips: list[dict[str, Any]],
    notes: list[dict[str, Any]],
    config: dict[str, Any],
    overrides: dict[str, int | None] | None = None,
    skipped_ids: set[str] | None = None,
    progress: Callable[[int, int], None] | None = None,
    overwrite_ids: set[str] | None = None,
) -> list[ImportDecision]:
    chosen = overrides or {}
    skipped = set(skipped_ids or [])
    forced_overwrite = set(overwrite_ids or [])
    minimum = import_point_value(str(config.get("import_minimum") or "medium"))
    notes_by_id = {int(note.get("note_id") or 0): note for note in notes}
    match_index = build_match_index(notes, config)
    decisions: list[ImportDecision] = []
    total_clips = len(clips)
    for clip_index, clip in enumerate(clips, start=1):
        if progress and (clip_index == 1 or clip_index == total_clips or clip_index % 25 == 0):
            progress(clip_index, total_clips)
        clip_id = str(clip.get("id") or "")
        decision = ImportDecision(
            clip_id=clip_id,
            mode="match",
            action="import",
            label=clip_label(clip, config.get("clip_identity")),
        )
        order = _positive_order(clip, match_index)
        suggestions, suggestion_total = _top_candidates(clip, match_index, SUGGESTION_LIMIT)
        decision.suggestions = suggestions
        decision.suggestion_total = suggestion_total
        if clip_id in chosen:
            note_id = chosen[clip_id]
            if note_id and int(note_id) in notes_by_id:
                decision.note_id = int(note_id)
                decision.signals = {"chosen": 0.0}
            else:
                decision.action = "skip"
                decision.conflict_resolution = "no_match"
        elif not order or order[0][0] < minimum:
            decision.action = "skip"
            decision.conflict_resolution = "no_match"
        elif len(order) > 1 and order[1][0] == order[0][0]:
            decision.action = "skip"
            decision.conflict_resolution = "tie"
        else:
            best = suggestions[0]
            decision.note_id = best.note_id
            decision.score = best.score
            decision.signals = dict(best.signals)
        if clip_id in skipped:
            decision.action = "skip"
            if decision.conflict_resolution not in {"no_match", "tie", "no_slot"}:
                decision.conflict_resolution = "skip"
        decisions.append(decision)

    field_sets_by_index = {field_set.index: field_set for field_set in enabled_import_field_sets(config)}
    reserved: dict[int, set[int]] = {}
    reserved_names: dict[int, set[str]] = {}
    for decision in decisions:
        decision.overwrite = False
        if decision.action != "import" or not decision.note_id:
            decision.target_field_set_index = 0
            continue
        note_id = int(decision.note_id)
        note = notes_by_id.get(note_id, {})
        fields = note.get("fields") or {}
        note_fields = fields if isinstance(fields, dict) else {}
        taken = reserved.setdefault(note_id, set())
        blocked_names = reserved_names.setdefault(note_id, set())
        index, reason = first_empty_field_set(
            note_fields,
            config,
            taken,
            blocked_names,
            note,
        )
        if index == 0 and decision.clip_id in forced_overwrite and reason in {"no_slot", "occupied_copy"}:
            index, reason = first_unreserved_field_set(config, taken)
            if index:
                decision.overwrite = True
                decision.action = "import"
                decision.conflict_resolution = "overwrite"
            else:
                decision.action = "skip"
                decision.target_field_set_index = 0
                decision.conflict_resolution = reason or "no_slot"
                continue
        decision.target_field_set_index = index
        if index == 0:
            decision.action = "skip"
            decision.conflict_resolution = reason or "no_slot"
            continue
        taken.add(index)
        field_set = field_sets_by_index.get(index)
        if field_set is None:
            continue
        for name in designated_import_fields(field_set) + import_copy_targets(config, note):
            blocked_names.add(name.casefold())
    return decisions


def apply_import_decisions(
    *,
    clips_by_id: dict[str, dict[str, Any]],
    decisions: list[ImportDecision],
    config: dict[str, Any],
    library_root: str,
    create_deck_name: str = "",
    collection: Any | None = None,
) -> dict[str, Any]:
    from .anki_scan import media_dir

    _ = create_deck_name
    if collection is None:
        from aqt import mw

        collection = getattr(mw, "col", None)
    if collection is None:
        raise RuntimeError("Anki collection is not available")
    field_sets = {field_set.index: field_set for field_set in enabled_import_field_sets(config)}
    copies = normalize_import_copies(config.get("import_copies"))
    audio_fields_needed = {
        str(item.get("source") or "").split(":", 1)[1]
        for item in copies
        if str(item.get("source") or "").startswith("audio:")
    }
    media_directory = media_dir(collection)
    created = 0
    updated = 0
    skipped = 0
    conflicts = 0
    warnings: list[str] = []
    op_changes = None

    for decision in decisions:
        if decision.action != "import" or not decision.note_id or not decision.target_field_set_index:
            skipped += 1
            if decision.conflict_resolution in {"no_slot", "occupied_copy"}:
                conflicts += 1
            continue
        clip = clips_by_id.get(decision.clip_id)
        if not clip:
            skipped += 1
            warnings.append(f"Missing clip {decision.clip_id}")
            continue
        source_media = library_media_path(library_root, clip)
        if not source_media or not os.path.isfile(source_media):
            skipped += 1
            warnings.append(f"Missing library media for {clip_filename(clip)}")
            continue
        field_set = field_sets.get(int(decision.target_field_set_index))
        if field_set is None:
            skipped += 1
            warnings.append("No complete field set configured for import.")
            continue
        try:
            note = collection.get_note(int(decision.note_id))
        except Exception:
            skipped += 1
            warnings.append(f"Note {decision.note_id} was not found.")
            continue
        note_fields = {str(name): str(note[name]) for name in list(note.keys())}
        sort_name = resolved_note_field(note, SORT_FIELD_ID)
        block = import_destination_block(
            note_fields,
            field_set,
            config,
            note={"sort_field_name": sort_name, "sort_field_value": str(note[sort_name]) if sort_name and sort_name in note else ""},
        )
        if block and not decision.overwrite:
            skipped += 1
            conflicts += 1
            if block == "occupied_copy":
                warnings.append(f"Import copy target already has data on note {note.id}")
            else:
                warnings.append(f"Import field set already has data on note {note.id}")
            continue
        try:
            used_filename = copy_into_media(source_media, media_directory, clip_filename(clip) or os.path.basename(source_media))
            renames: dict[str, str] = {}
            for audio in clip_audio_media(clip):
                field_name = str(audio.get("field") or "")
                if field_name not in audio_fields_needed:
                    continue
                audio_name = str(audio.get("filename") or "")
                audio_source = library_media_file(
                    library_root,
                    audio_name,
                    str(audio.get("relative_path") or ""),
                )
                if audio_source and os.path.isfile(audio_source) and audio_name:
                    renames[audio_name] = copy_into_media(audio_source, media_directory, audio_name)
        except OSError as error:
            skipped += 1
            warnings.append(f"Could not copy media: {error}")
            continue

        if any(str(item.get("target_field") or "") == SORT_FIELD_ID and not sort_name for item in copies):
            warnings.append(f"Sort field could not be resolved on note {note.id}")
        apply_fields_to_note(note, field_set, clip, used_filename, copies, renames)
        op_changes = _merge_op_changes(op_changes, collection.update_note(note))
        updated += 1

    try:
        collection.save()
    except Exception:
        pass
    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "conflicts": conflicts,
        "warnings": warnings,
        "op_changes": op_changes,
    }


_OP_CHANGE_FIELDS = (
    "card",
    "note",
    "deck",
    "tag",
    "notetype",
    "config",
    "deck_config",
    "mtime",
    "browser_table",
    "browser_sidebar",
    "note_text",
    "study_queues",
)


def _merge_op_changes(current: Any, extra: Any) -> Any:
    if extra is None:
        return current
    if current is None:
        return extra
    for name in _OP_CHANGE_FIELDS:
        if getattr(extra, name, False):
            setattr(current, name, True)
    return current


def clip_label(clip: dict[str, Any], identity: list[dict[str, str]] | None = None) -> str:
    from .identity import format_clip_label

    return format_clip_label(clip, identity)
