from __future__ import annotations

from typing import Any

from .import_map import clip_anki, clip_field_value, clip_filename, field_set_display_name
from .textutil import example_text, strip_html


IDENTITY_SEPARATOR = " — "
SAMPLE_PLACEHOLDER = "(no sample)"
NOTE_SAMPLE_PLACEHOLDER = "(check a deck with notes first)"

DEFAULT_CLIP_IDENTITY: list[dict[str, str]] = [
    {"source": "sort_field"},
    {"source": "video_filename"},
]

DEFAULT_NOTE_IDENTITY: list[dict[str, str]] = [
    {"source": "sort_field"},
    {"source": "note_id"},
]

_CLIP_KEY_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("note_id", "note_id", "Note id"),
    ("deck_name", "deck_name", "Deck name"),
    ("model_name", "model_name", "Model name"),
    ("sort_field_name", "sort_field_name", "Sort field name"),
    ("field_set_index", "field_set_index", "Field set index"),
    ("field_set_name", "field_set_name", "Field set name"),
)

_ROLE_LABELS = {
    "sentence": "Sentence",
    "secondary": "Secondary",
    "miscinfo": "Miscinfo",
    "video": "Video field",
}


def normalize_identity(value: Any) -> list[dict[str, str]]:
    raw = value if isinstance(value, list) else []
    parts: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        source = item if isinstance(item, str) else ""
        if isinstance(item, dict):
            source = str(item.get("source") or "")
        source = str(source or "").strip()
        if not source or source in seen:
            continue
        seen.add(source)
        parts.append({"source": source})
    return parts


def clip_identity_choices(clips: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Sources that can name a clip. Structural parts are always offered."""
    choices: list[tuple[str, str]] = [
        ("clip_number", "Clip number"),
        ("clip_id", "Clip id"),
        ("video_filename", "Video filename"),
    ]
    seen = {source for source, _label in choices}
    has_sort = False

    def add(source: str, label: str) -> None:
        if not source or source in seen:
            return
        seen.add(source)
        choices.append((source, label))

    for clip in clips:
        if not isinstance(clip, dict):
            continue
        anki = clip_anki(clip)
        if "sort_field_value" in anki:
            has_sort = True
        for source, key, label in _CLIP_KEY_SOURCES:
            if source == "field_set_name":
                if key in anki or anki.get("field_set_index") not in (None, "") or clip.get("_library_field_sets"):
                    add(source, label)
                continue
            if key in anki:
                add(source, label)
        fields = anki.get("fields") if isinstance(anki.get("fields"), dict) else {}
        for role, label in _ROLE_LABELS.items():
            if role in fields:
                add(role, label)
        for role, item in fields.items():
            role_id = str(role or "")
            if role_id in _ROLE_LABELS or not isinstance(item, dict):
                continue
            add(f"field:{role_id}", f"Clip field: {item.get('name') or role_id}")
        extras = anki.get("extra_fields") if isinstance(anki.get("extra_fields"), dict) else {}
        for key, item in extras.items():
            name = item.get("name") if isinstance(item, dict) else key
            add(f"extra:{key}", f"Extra: {name or key}")
        audios = anki.get("audio_fields") if isinstance(anki.get("audio_fields"), dict) else {}
        for key, item in audios.items():
            name = item.get("name") if isinstance(item, dict) else key
            add(f"audio:{key}", f"Audio: {name or key}")
    if has_sort:
        choices.insert(3, ("sort_field", "Sort field value"))
    return choices


def note_identity_choices(field_names: list[str]) -> list[tuple[str, str]]:
    choices = [
        ("sort_field", "Sort field"),
        ("note_id", "Note id"),
        ("deck_name", "Deck name"),
    ]
    seen = {source for source, _label in choices}
    for name in field_names:
        field_name = str(name or "").strip()
        source = f"field:{field_name}"
        if not field_name or source in seen:
            continue
        seen.add(source)
        choices.append((source, field_name))
    return choices


_KNOWN_SOURCE_LABELS = {
    "clip_number": "Clip number",
    "clip_id": "Clip id",
    "video_filename": "Video filename",
    "sort_field": "Sort field value",
    "note_id": "Note id",
    "deck_name": "Deck name",
    "sentence": "Sentence",
    "secondary": "Secondary",
    "miscinfo": "Miscinfo",
    "video": "Video field",
}


def ensure_identity_choices(
    choices: list[tuple[str, str]],
    parts: list[dict[str, str]] | None,
) -> list[tuple[str, str]]:
    seen = {source for source, _label in choices}
    extra: list[tuple[str, str]] = []
    for part in parts or []:
        source = str(part.get("source") or "").strip()
        if source and source not in seen:
            seen.add(source)
            extra.append((source, _KNOWN_SOURCE_LABELS.get(source, source)))
    return extra + list(choices)


def preview_identity(
    parts: list[dict[str, str]] | None,
    samples: dict[str, str] | None = None,
    *,
    unavailable: str | None = None,
) -> str:
    """Join selected parts using real samples. Missing samples stay visible as placeholder text."""
    values = samples or {}
    pieces: list[str] = []
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        source = str(part.get("source") or "").strip()
        if not source:
            continue
        if unavailable:
            pieces.append(unavailable)
            continue
        pieces.append(str(values.get(source) or "") or SAMPLE_PLACEHOLDER)
    return IDENTITY_SEPARATOR.join(pieces)


def identity_example_line(
    parts: list[dict[str, str]] | None,
    samples: dict[str, str] | None = None,
    *,
    unavailable: str | None = None,
) -> str:
    text = preview_identity(parts, samples, unavailable=unavailable)
    if text:
        return f"Example: {text}"
    if unavailable:
        return f"Example: {unavailable}"
    return "Example:"


def clip_identity_samples(clips: list[dict[str, Any]]) -> dict[str, str]:
    samples: dict[str, str] = {}
    choices = clip_identity_choices(clips)
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        for source, _label in choices:
            if source in samples:
                continue
            text = example_text(_clip_part_text(clip, source))
            if text:
                samples[source] = text
    return samples


def note_identity_samples(notes: list[dict[str, Any]], field_names: list[str]) -> dict[str, str]:
    samples: dict[str, str] = {}
    choices = note_identity_choices(field_names)
    for note in notes:
        if not isinstance(note, dict):
            continue
        for source, _label in choices:
            if source in samples:
                continue
            text = example_text(_note_part_text(note, source))
            if text:
                samples[source] = text
    return samples


def format_clip_label(clip: dict[str, Any], identity: list[dict[str, str]] | None = None) -> str:
    spec = DEFAULT_CLIP_IDENTITY if identity is None else identity
    parts = [_clip_part_text(clip, str(part.get("source") or "")) for part in spec if isinstance(part, dict)]
    label = IDENTITY_SEPARATOR.join(part for part in parts if part)
    return label or str(clip.get("id") or "clip")


def format_note_label(
    note: dict[str, Any],
    identity: list[dict[str, str]] | None = None,
    score: float | None = None,
) -> str:
    spec = DEFAULT_NOTE_IDENTITY if identity is None else identity
    parts = [
        _short(_note_part_text(note, str(part.get("source") or "")))
        for part in spec
        if isinstance(part, dict)
    ]
    label = IDENTITY_SEPARATOR.join(part for part in parts if part) or "(empty)"
    if score is not None and score > 0:
        label = f"{label} · {score:g}"
    return label


def _clip_part_text(clip: dict[str, Any], source: str) -> str:
    if source == "clip_number":
        number = clip.get("_clip_number")
        return "" if number in (None, "") else str(number)
    if source == "clip_id":
        return str(clip.get("id") or "").strip()
    if source == "video_filename":
        return clip_filename(clip)
    anki = clip_anki(clip)
    if source == "sort_field":
        if "sort_field_value" not in anki:
            return ""
        return _one_line(strip_html(str(anki.get("sort_field_value") or "")))
    if source == "field_set_name":
        return _one_line(field_set_display_name(clip))
    if source in _CLIP_KEY_SOURCES_BY_ID:
        key = _CLIP_KEY_SOURCES_BY_ID[source]
        if key not in anki:
            return ""
        return _one_line(str(anki.get(key) or ""))
    if source in _ROLE_LABELS or source.startswith("field:"):
        role = source.split(":", 1)[1] if source.startswith("field:") else source
        fields = anki.get("fields") if isinstance(anki.get("fields"), dict) else {}
        if role not in fields:
            return ""
        return _one_line(strip_html(clip_field_value(clip, role)))
    if source.startswith("extra:"):
        return _mapped_value(anki.get("extra_fields"), source.split(":", 1)[1])
    if source.startswith("audio:"):
        return _audio_value(anki.get("audio_fields"), source.split(":", 1)[1])
    return ""


_CLIP_KEY_SOURCES_BY_ID = {source: key for source, key, _label in _CLIP_KEY_SOURCES}


def _note_part_text(note: dict[str, Any], source: str) -> str:
    if source == "sort_field":
        return _one_line(strip_html(str(note.get("sort_field_value") or "")))
    if source == "note_id":
        return str(note.get("note_id") or "")
    if source == "deck_name":
        return _one_line(str(note.get("deck_name") or ""))
    if source.startswith("field:"):
        fields = note.get("fields") if isinstance(note.get("fields"), dict) else {}
        return _one_line(strip_html(str(fields.get(source.split(":", 1)[1]) or "")))
    return ""


def _mapped_value(mapping: Any, key: str) -> str:
    if not isinstance(mapping, dict) or key not in mapping:
        return ""
    item = mapping.get(key)
    if isinstance(item, dict):
        return _one_line(strip_html(str(item.get("value") or "")))
    return _one_line(strip_html(str(item or "")))


def _audio_value(mapping: Any, key: str) -> str:
    if not isinstance(mapping, dict) or key not in mapping:
        return ""
    item = mapping.get(key)
    if not isinstance(item, dict):
        return _one_line(str(item or ""))
    value = _one_line(strip_html(str(item.get("value") or "")))
    if value:
        return value
    files = [str(name) for name in item.get("files") or [] if str(name or "").strip()]
    return ", ".join(files)


def _one_line(value: str) -> str:
    return " ".join(str(value or "").split())


def _short(value: str, limit: int = 48) -> str:
    text = _one_line(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
