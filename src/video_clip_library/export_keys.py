from __future__ import annotations

from typing import Any

from .job_index import effective_record_keys, filter_record, is_key_included
from .textutil import example_text


ANKI_EXPORT_KEY_DEFAULTS: dict[str, bool] = {
    "anki": True,
    "anki.profile": False,
    "anki.note_id": True,
    "anki.deck_name": False,
    "anki.model_name": False,
    "anki.sort_field_name": True,
    "anki.sort_field_value": True,
    "anki.field_set_index": True,
    "anki.fields": True,
    "anki.fields.video": True,
    "anki.fields.sentence": True,
    "anki.fields.secondary": True,
    "anki.fields.miscinfo": True,
    "anki.extra_fields": True,
    "anki.audio_fields": True,
}

REDUNDANCY_COLOR = "#c47b16"


def _extra_key_segment(field_name: str) -> str:
    return str(field_name or "").strip()


def extra_fields_from_config(config: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    key = "generic_fields" if kind == "generic" else "audio_fields"
    raw = config.get(key, [])
    return [item for item in raw if isinstance(item, dict)]


def anki_export_key_labels(config: dict[str, Any]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for role in config.get("clip_extra_roles") or []:
        if not isinstance(role, dict):
            continue
        role_id = str(role.get("id") or "").strip()
        name = str(role.get("name") or "").strip()
        if role_id and name:
            labels[f"anki.fields.{role_id}"] = name
    return labels


def export_key_examples(
    notes: list[dict[str, Any]],
    config: dict[str, Any],
    profile_name: str = "",
) -> dict[str, str]:
    """Example values for clip-library export keys, taken from notes already loaded."""
    from .config import extra_fields_from_config, field_sets_from_config

    examples: dict[str, str] = {}

    def consider(path: str, value: Any) -> None:
        if path in examples:
            return
        text = example_text(value)
        if text:
            examples[path] = text

    if profile_name:
        consider("anki.profile", profile_name)
    for note in notes:
        if not isinstance(note, dict):
            continue
        fields = note.get("fields") if isinstance(note.get("fields"), dict) else {}
        consider("anki.note_id", note.get("note_id"))
        consider("anki.deck_name", note.get("deck_name"))
        consider("anki.model_name", note.get("model_name"))
        consider("anki.sort_field_name", note.get("sort_field_name"))
        consider("anki.sort_field_value", note.get("sort_field_value"))
        section = {
            key: note.get(key)
            for key in ("note_id", "deck_name", "model_name", "sort_field_name", "sort_field_value")
            if example_text(note.get(key))
        }
        if section:
            consider("anki", section)
        mapped: dict[str, str] = {}
        for field_set in field_sets_from_config(config):
            if not field_set.enabled:
                continue
            consider("anki.field_set_index", field_set.index)
            for role, field_name in {**field_set.role_map(), **field_set.extra_map()}.items():
                if not field_name:
                    continue
                value = fields.get(field_name, "")
                consider(f"anki.fields.{role}", value)
                text = example_text(value)
                if text and role not in mapped:
                    mapped[role] = text
        if mapped:
            consider("anki.fields", mapped)
        extras: dict[str, str] = {}
        for extra in extra_fields_from_config(config, "generic"):
            if not extra.field:
                continue
            value = fields.get(extra.field, "")
            consider(f"anki.extra_fields.{extra.field}", value)
            text = example_text(value)
            if text:
                extras[extra.field] = text
        if extras:
            consider("anki.extra_fields", extras)
        audios: dict[str, str] = {}
        for extra in extra_fields_from_config(config, "audio"):
            if not extra.field:
                continue
            value = fields.get(extra.field, "")
            consider(f"anki.audio_fields.{extra.field}", value)
            text = example_text(value)
            if text:
                audios[extra.field] = text
        if audios:
            consider("anki.audio_fields", audios)
    return examples


def anki_export_key_tree(config: dict[str, Any]) -> list[str]:
    keys = list(ANKI_EXPORT_KEY_DEFAULTS.keys())
    for role in config.get("clip_extra_roles") or []:
        if not isinstance(role, dict):
            continue
        role_id = str(role.get("id") or "").strip()
        if role_id:
            keys.append(f"anki.fields.{role_id}")
    for extra in extra_fields_from_config(config, "generic"):
        if not extra.get("enabled"):
            continue
        field_name = _extra_key_segment(str(extra.get("field") or ""))
        if field_name:
            keys.append(f"anki.extra_fields.{field_name}")
    for extra in extra_fields_from_config(config, "audio"):
        if not extra.get("enabled"):
            continue
        field_name = _extra_key_segment(str(extra.get("field") or ""))
        if field_name:
            keys.append(f"anki.audio_fields.{field_name}")
    seen: set[str] = set()
    ordered: list[str] = []
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def effective_anki_keys(selection: dict[str, Any] | None, config: dict[str, Any] | None = None) -> dict[str, bool]:
    merged = dict(ANKI_EXPORT_KEY_DEFAULTS)
    if config is not None:
        for key in anki_export_key_tree(config):
            merged.setdefault(key, True)
    if isinstance(selection, dict):
        for key, value in selection.items():
            path = str(key or "").strip()
            if path:
                merged[path] = bool(value)
    return merged


def filter_anki_section(anki: dict[str, Any], selection: dict[str, bool]) -> dict[str, Any]:
    wrapped = filter_record({"anki": anki}, selection)
    filtered = wrapped.get("anki")
    return filtered if isinstance(filtered, dict) else {}


def include_anki_profile(selection: dict[str, bool]) -> bool:
    return is_key_included("anki.profile", selection)


def selected_job_redundancy_pairs(
    anki_selection: dict[str, bool],
    job_selection: dict[str, bool],
    pairs: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    selected: dict[str, str] = {}
    for pair in pairs or []:
        if not isinstance(pair, dict):
            continue
        anki_path = str(pair.get("clip_key") or "").strip()
        job_path = str(pair.get("job_key") or "").strip()
        if not anki_path or not job_path:
            continue
        if is_key_included(anki_path, anki_selection) and is_key_included(job_path, job_selection):
            selected[anki_path] = job_path
    return selected


def reverse_job_redundancy(pairs: list[dict[str, Any]] | None = None) -> dict[str, str]:
    reverse: dict[str, str] = {}
    for pair in pairs or []:
        if not isinstance(pair, dict):
            continue
        anki_path = str(pair.get("clip_key") or "").strip()
        job_path = str(pair.get("job_key") or "").strip()
        if anki_path and job_path:
            reverse[job_path] = anki_path
    return reverse


def reuse_job_key_selection(selection: dict[str, Any] | None) -> dict[str, bool]:
    return effective_record_keys(selection)
