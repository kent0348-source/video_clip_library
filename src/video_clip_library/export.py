from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from typing import Any, Callable

from .config import active_job_tree, enabled_extra_fields, enabled_field_sets
from .exclusions import (
    ExclusionMatch,
    active_exclusion_rules,
    collect_export_key_paths,
    first_matching_rule,
)
from .export_keys import effective_anki_keys, filter_anki_section, include_anki_profile
from .job_index import (
    JobIndex,
    collect_key_examples,
    collect_key_paths,
    is_key_included,
    prepare_job_payload,
    tree_key_selection,
    unknown_job_key_paths,
)
from .models import (
    LIBRARY_FILENAME,
    LIBRARY_FORMAT,
    LIBRARY_SCHEMA_VERSION,
    MEDIA_DIRNAME,
    NOTE_LEVEL_ANKI_KEYS,
    DiscoveredClip,
    ExportReport,
    LinkInfo,
    library_clip_dict,
    merge_audio_media,
    partition_anki_section,
)
from .textutil import iso_now


ProgressCallback = Callable[[int, int, str], None]


def copy_media_file(source_path: str, dest_dir: str, filename: str) -> tuple[str, bool]:
    os.makedirs(dest_dir, exist_ok=True)
    destination = os.path.join(dest_dir, filename)
    if os.path.exists(destination):
        try:
            if os.path.getsize(destination) == os.path.getsize(source_path):
                return filename, False
        except OSError:
            pass
        stem, extension = os.path.splitext(filename)
        index = 2
        while True:
            candidate = f"{stem}_{index}{extension}"
            candidate_path = os.path.join(dest_dir, candidate)
            if not os.path.exists(candidate_path):
                shutil.copy2(source_path, candidate_path)
                return candidate, True
            try:
                if os.path.getsize(candidate_path) == os.path.getsize(source_path):
                    return candidate, False
            except OSError:
                pass
            index += 1
    shutil.copy2(source_path, destination)
    return filename, True


def stable_clip_id(clip: DiscoveredClip, record: dict[str, Any] | None) -> str:
    record_id = str((record or {}).get("record_id") or "").strip()
    if record_id:
        return f"record:{record_id}"
    return f"anki:{clip.note_id}:{clip.field_set_index}:{clip.filename}"


def empty_library(config: dict[str, Any], decks: list[str]) -> dict[str, Any]:
    return {
        "format": LIBRARY_FORMAT,
        "schema_version": LIBRARY_SCHEMA_VERSION,
        "exported_at": iso_now(),
        "updated_at": iso_now(),
        "source": {
            "anki_profile": "",
            "note_type": str(config.get("note_type") or ""),
            "decks": decks,
        },
        "field_sets": [field_set.library_dict() for field_set in enabled_field_sets(config)],
        "generic_fields": [item.library_dict() for item in enabled_extra_fields(config, "generic")],
        "audio_fields": [item.library_dict() for item in enabled_extra_fields(config, "audio")],
        "settings_snapshot": {
            "job_records_enabled": bool(config.get("job_records_enabled")),
            "path_privacy": {
                "mode": config.get("path_privacy_mode", "off"),
                "parents": int(config.get("path_privacy_parents", 1) or 1),
            },
        },
        "notes": {},
        "clips": [],
    }


def _note_key(note_id: Any) -> str:
    return str(note_id)


def normalize_library_notes(payload: dict[str, Any]) -> dict[str, Any]:
    """Lift note-constant anki fields into payload['notes'] and leave clip-local fields in place."""
    if not isinstance(payload.get("clips"), list):
        payload["clips"] = []
    raw_notes = payload.get("notes") if isinstance(payload.get("notes"), dict) else {}
    try:
        schema_number = int(payload.get("schema_version") or 0)
    except (TypeError, ValueError):
        schema_number = 0
    hoist_embedded = schema_number < 2 or not isinstance(payload.get("notes"), dict)
    notes: dict[str, dict[str, Any]] = {}
    for key, value in raw_notes.items():
        if isinstance(value, dict):
            notes[str(key)] = {
                field: value[field] for field in NOTE_LEVEL_ANKI_KEYS if field in value and field != "note_id"
            }
    for clip in payload["clips"]:
        if not isinstance(clip, dict):
            continue
        anki = clip.get("anki")
        if not isinstance(anki, dict):
            continue
        note_id = anki.get("note_id")
        if note_id in (None, ""):
            for field in NOTE_LEVEL_ANKI_KEYS:
                anki.pop(field, None)
            continue
        if hoist_embedded:
            key = _note_key(note_id)
            embedded = {field: anki[field] for field in NOTE_LEVEL_ANKI_KEYS if field in anki}
            merged = dict(embedded)
            merged.update(notes.get(key, {}))
            notes[key] = {field: merged[field] for field in NOTE_LEVEL_ANKI_KEYS if field in merged and field != "note_id"}
        for field in NOTE_LEVEL_ANKI_KEYS:
            if field != "note_id":
                anki.pop(field, None)
        anki["note_id"] = note_id
        media = clip.get("media") if isinstance(clip.get("media"), dict) else None
        audio_media = media.get("audio") if isinstance(media, dict) else None
        if isinstance(audio_media, list) and audio_media:
            key = _note_key(note_id)
            note = notes.setdefault(key, {})
            existing = note.get("audio_fields") if isinstance(note.get("audio_fields"), dict) else {}
            note["audio_fields"] = merge_audio_media(existing, audio_media)
            media.pop("audio", None)
    payload["notes"] = {key: _finalize_note(value) for key, value in notes.items()}
    payload["field_sets"] = _normalize_library_field_sets(payload.get("field_sets"))
    payload["generic_fields"] = _normalize_library_definitions(payload.get("generic_fields"))
    payload["audio_fields"] = _normalize_library_definitions(payload.get("audio_fields"))
    _repair_clip_field_sets(payload)
    payload["schema_version"] = LIBRARY_SCHEMA_VERSION
    return payload


def _normalize_library_field_sets(raw: Any) -> list[dict[str, Any]]:
    sets: list[dict[str, Any]] = []
    used: set[int] = set()
    next_index = 1
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            index = 0
        if index < 1 or index in used:
            while next_index in used:
                next_index += 1
            index = next_index
        used.add(index)
        next_index = max(next_index, index + 1)
        extras = item.get("extras") if isinstance(item.get("extras"), dict) else {}
        sets.append(
            {
                "index": index,
                "name": str(item.get("name") or f"Field Set {index}"),
                "video": str(item.get("video") or ""),
                "sentence": str(item.get("sentence") or ""),
                "secondary": str(item.get("secondary") or ""),
                "miscinfo": str(item.get("miscinfo") or ""),
                "extras": {str(role_id): str(name or "") for role_id, name in extras.items() if str(role_id).strip()},
            }
        )
    return sets


def _normalize_library_definitions(raw: Any) -> list[dict[str, str]]:
    definitions: list[dict[str, str]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        field_name = str(item.get("field") or "").strip()
        name = str(item.get("name") or "").strip() or field_name
        if not field_name and not name:
            continue
        definitions.append({"name": name, "field": field_name})
    return definitions


def _media_filename(item: dict[str, Any]) -> str:
    filename = str(item.get("filename") or "").strip()
    if filename:
        return filename
    relative = str(item.get("relative_path") or "").replace("\\", "/")
    return os.path.basename(relative) if relative else ""


def _stored_media_item(item: dict[str, Any]) -> dict[str, Any] | None:
    filename = _media_filename(item)
    if not filename:
        return None
    stored: dict[str, Any] = {"filename": filename}
    if "size_bytes" in item:
        stored["size_bytes"] = item.get("size_bytes")
    if "exists" in item:
        stored["exists"] = bool(item.get("exists"))
    return stored


def _normalize_audio_fields(raw: Any) -> dict[str, Any]:
    from .textutil import extract_audio_filenames

    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, item in raw.items():
        slot = item if isinstance(item, dict) else {"value": item}
        value = str(slot.get("value") or "")
        media: list[dict[str, Any]] = []
        for row in slot.get("media") or []:
            if isinstance(row, dict):
                stored = _stored_media_item(row)
                if stored:
                    media.append(stored)
        if not media:
            names = [str(name) for name in slot.get("files") or [] if str(name or "").strip()]
            if not names:
                names = extract_audio_filenames(value)
            media = [{"filename": name} for name in names]
        stored_slot: dict[str, Any] = {"value": value}
        if media:
            stored_slot["media"] = media
        cleaned[str(key)] = stored_slot
    return cleaned


def _normalize_extra_values(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, item in raw.items():
        if isinstance(item, dict):
            cleaned[str(key)] = {"value": item.get("value") or ""}
        else:
            cleaned[str(key)] = {"value": "" if item is None else item}
    return cleaned


def _finalize_note(note: dict[str, Any]) -> dict[str, Any]:
    stored = {key: value for key, value in note.items() if key != "note_id"}
    if "extra_fields" in stored:
        stored["extra_fields"] = _normalize_extra_values(stored.get("extra_fields"))
    if "audio_fields" in stored:
        stored["audio_fields"] = _normalize_audio_fields(stored.get("audio_fields"))
    return stored


def _repair_clip_field_sets(payload: dict[str, Any]) -> None:
    field_sets = payload.get("field_sets") if isinstance(payload.get("field_sets"), list) else []
    by_index: dict[int, dict[str, Any]] = {}
    by_name: dict[str, int] = {}
    for item in field_sets:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        by_index[index] = item
        name = str(item.get("name") or "").strip()
        if name and name not in by_name:
            by_name[name] = index
    for clip in payload.get("clips") or []:
        if not isinstance(clip, dict):
            continue
        media = clip.get("media")
        if isinstance(media, dict):
            filename = _media_filename(media)
            if filename:
                media["filename"] = filename
            media.pop("relative_path", None)
            audio = media.get("audio")
            if isinstance(audio, list):
                media["audio"] = [item for item in (_stored_media_item(row) for row in audio if isinstance(row, dict)) if item]
        anki = clip.get("anki")
        if not isinstance(anki, dict):
            continue
        try:
            current = int(anki.get("field_set_index"))
        except (TypeError, ValueError):
            current = 0
        if current not in by_index:
            matched = by_name.get(str(anki.get("field_set_name") or "").strip())
            if matched:
                anki["field_set_index"] = matched
        anki.pop("field_set_name", None)
        if isinstance(anki.get("extra_fields"), dict):
            anki["extra_fields"] = _normalize_extra_values(anki.get("extra_fields"))
        if isinstance(anki.get("audio_fields"), dict):
            anki["audio_fields"] = _normalize_audio_fields(anki.get("audio_fields"))


def referenced_field_set_indexes(payload: dict[str, Any]) -> set[int]:
    found: set[int] = set()
    for clip in payload.get("clips") or []:
        if not isinstance(clip, dict):
            continue
        anki = clip.get("anki") if isinstance(clip.get("anki"), dict) else {}
        try:
            found.add(int(anki.get("field_set_index")))
        except (TypeError, ValueError):
            continue
    return found


def merge_library_field_sets(
    previous: list[Any],
    current: list[dict[str, Any]],
    referenced: set[int],
) -> list[dict[str, Any]]:
    """Refresh sets from the current collection and keep indexes still used by older clips."""
    kept = {int(item["index"]): item for item in current if isinstance(item, dict) and item.get("index") not in (None, "")}
    for item in previous or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if index in kept or index not in referenced:
            continue
        kept[index] = item
    return [kept[index] for index in sorted(kept)]


def prune_unreferenced_notes(payload: dict[str, Any]) -> None:
    notes = payload.get("notes") if isinstance(payload.get("notes"), dict) else {}
    used: set[str] = set()
    for clip in payload.get("clips") or []:
        if not isinstance(clip, dict):
            continue
        anki = clip.get("anki") if isinstance(clip.get("anki"), dict) else {}
        note_id = anki.get("note_id")
        if note_id not in (None, ""):
            used.add(_note_key(note_id))
    payload["notes"] = {key: value for key, value in notes.items() if key in used}


def load_library(path: str) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            decoded = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict) or decoded.get("format") != LIBRARY_FORMAT:
        return None
    return normalize_library_notes(decoded)


def write_library(path: str, payload: dict[str, Any]) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    serializable = dict(payload)
    clips = []
    for clip in payload.get("clips") or []:
        if isinstance(clip, dict):
            clips.append({key: value for key, value in clip.items() if not str(key).startswith("_")})
        else:
            clips.append(clip)
    serializable["clips"] = clips
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(serializable, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temp_path, path)


def merge_clips(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = list(existing)
    by_id = {str(clip.get("id") or ""): index for index, clip in enumerate(merged) if clip.get("id")}
    by_filename: dict[str, int] = {}
    for index, clip in enumerate(merged):
        media = clip.get("media") if isinstance(clip.get("media"), dict) else {}
        filename = str(media.get("filename") or "")
        if filename and filename not in by_filename:
            by_filename[filename] = index
    for clip in incoming:
        clip_id = str(clip.get("id") or "")
        media = clip.get("media") if isinstance(clip.get("media"), dict) else {}
        filename = str(media.get("filename") or "")
        if clip_id and clip_id in by_id:
            merged[by_id[clip_id]] = clip
            continue
        if filename and filename in by_filename:
            index = by_filename[filename]
            merged[index] = clip
            if clip_id:
                by_id[clip_id] = index
            continue
        merged.append(clip)
        index = len(merged) - 1
        if clip_id:
            by_id[clip_id] = index
        if filename:
            by_filename[filename] = index
    return merged


def _export_job_context(
    config: dict[str, Any],
) -> tuple[JobIndex | None, dict[str, bool], list[str], dict[str, Any] | None]:
    if not config.get("job_records_enabled"):
        return None, {}, [], None
    job_index = JobIndex.load(str(config.get("job_index_path") or ""))
    tree = active_job_tree(config)
    return job_index, tree_key_selection(tree), list(job_index.errors), tree


def clip_match_document(
    clip: DiscoveredClip,
    *,
    record: dict[str, Any] | None,
    link: LinkInfo,
    anki_keys: dict[str, bool],
    job_selection: dict[str, bool],
) -> dict[str, Any]:
    """Clip object export would write, before note fields are hoisted and before path privacy."""
    job_payload = None
    if record:
        job_payload = prepare_job_payload(
            record,
            selection=job_selection,
            privacy_mode="off",
            privacy_parents=1,
            strict=True,
            apply_privacy=False,
        )
    exists = bool(clip.media_path) and os.path.isfile(clip.media_path)
    entry = library_clip_dict(
        clip_id=stable_clip_id(clip, record),
        filename=clip.filename,
        size_bytes=None,
        exists=exists,
        discovered=clip,
        job=job_payload,
        link=link,
    )
    raw_anki = entry.get("anki") if isinstance(entry.get("anki"), dict) else {}
    entry["anki"] = filter_anki_section(raw_anki, anki_keys)
    return entry


def _job_match_for_clip(
    clip: DiscoveredClip,
    job_index: JobIndex | None,
) -> tuple[dict[str, Any] | None, LinkInfo, bool]:
    if job_index is None:
        return None, LinkInfo(status="anki_only"), False
    match = job_index.match_clip(clip)
    if not match:
        return None, LinkInfo(status="anki_only"), True
    return match.record, match.link, False


def library_media_filenames(payload: dict[str, Any] | None) -> set[str]:
    names: set[str] = set()
    if not isinstance(payload, dict):
        return names
    for clip in payload.get("clips") or []:
        if not isinstance(clip, dict):
            continue
        media = clip.get("media") if isinstance(clip.get("media"), dict) else {}
        filename = str(media.get("filename") or "")
        if filename:
            names.add(filename)
    return names


def _exclusion_sample(filename: str, hit: ExclusionMatch) -> str:
    text = " ".join(hit.matched_text.split())
    if len(text) > 80:
        text = text[:79] + "…"
    return f"{filename} — {hit.path} — {text}"


def job_key_counts(documents: list[dict[str, Any]]) -> dict[str, int]:
    """How many clip objects contain each generated job-record key."""
    counts: dict[str, int] = {}
    for document in documents:
        job = document.get("job")
        if not isinstance(job, dict):
            continue
        counts["job"] = counts.get("job", 0) + 1
        for path in collect_key_paths(job, "job"):
            counts[path] = counts.get(path, 0) + 1
    return counts


@dataclass
class ExclusionPreview:
    hits: list[dict[str, Any]]
    key_paths: list[str]
    examples: dict[str, str]
    job_key_counts: dict[str, int]
    job_errors: list[str]
    rules_active: bool


def inspect_export_clips(clips: list[DiscoveredClip], config: dict[str, Any]) -> ExclusionPreview:
    """Build export clip objects without copying media. Used by the exclusion preview."""
    anki_keys = effective_anki_keys(config.get("anki_export_keys"), config)
    job_index, job_selection, errors, _tree = _export_job_context(config)
    rules = active_exclusion_rules(config)
    documents: list[dict[str, Any]] = []
    examples: dict[str, str] = {}
    hits: list[dict[str, Any]] = []
    for clip in clips:
        record, link, _missing = _job_match_for_clip(clip, job_index)
        document = clip_match_document(
            clip,
            record=record,
            link=link,
            anki_keys=anki_keys,
            job_selection=job_selection,
        )
        documents.append(document)
        for path, text in collect_key_examples(document).items():
            examples.setdefault(path, text)
        hit = first_matching_rule(document, rules)
        if hit is None:
            continue
        hits.append(
            {
                "filename": clip.filename,
                "note_id": clip.note_id,
                "path": hit.path,
                "matched_text": hit.matched_text,
            }
        )
    return ExclusionPreview(
        hits=hits,
        key_paths=collect_export_key_paths(documents),
        examples=examples,
        job_key_counts=job_key_counts(documents),
        job_errors=errors,
        rules_active=bool(rules),
    )


def resolve_library_paths(destination: str, mode: str) -> tuple[str, str]:
    destination = os.path.abspath(destination)
    if destination.lower().endswith(LIBRARY_FILENAME):
        library_path = destination
        root = os.path.dirname(destination)
    elif os.path.isfile(destination):
        library_path = destination
        root = os.path.dirname(destination)
    else:
        root = destination
        library_path = os.path.join(root, LIBRARY_FILENAME)
    if mode == "update" and os.path.isdir(destination):
        nested = os.path.join(destination, LIBRARY_FILENAME)
        if os.path.exists(nested):
            return destination, nested
    return root, library_path


def export_clips(
    clips: list[DiscoveredClip],
    *,
    config: dict[str, Any],
    destination: str,
    mode: str,
    profile_name: str = "",
    progress: ProgressCallback | None = None,
) -> ExportReport:
    report = ExportReport(clips_found=len(clips))
    root, library_path = resolve_library_paths(destination, mode)
    media_root = os.path.join(root, MEDIA_DIRNAME)
    decks = sorted({clip.deck_name for clip in clips if clip.deck_name})
    anki_keys = effective_anki_keys(config.get("anki_export_keys"), config)

    existing = load_library(library_path) if mode == "update" and os.path.exists(library_path) else None
    library = existing or empty_library(config, decks)
    previous_field_sets = list(library.get("field_sets") or []) if existing else []
    if existing:
        library["updated_at"] = iso_now()
        source = library.setdefault("source", {})
        source["note_type"] = str(config.get("note_type") or source.get("note_type") or "")
        source["decks"] = sorted(set(source.get("decks") or []) | set(decks))
    else:
        library["exported_at"] = iso_now()
        library["updated_at"] = library["exported_at"]
        library["source"]["decks"] = decks
    if include_anki_profile(anki_keys):
        library["source"]["anki_profile"] = profile_name or library.get("source", {}).get("anki_profile") or ""
    else:
        library.get("source", {}).pop("anki_profile", None)
    current_field_sets = [field_set.library_dict() for field_set in enabled_field_sets(config)]
    library["generic_fields"] = [item.library_dict() for item in enabled_extra_fields(config, "generic")]
    library["audio_fields"] = [item.library_dict() for item in enabled_extra_fields(config, "audio")]

    job_index, job_selection, job_errors, job_tree = _export_job_context(config)
    report.warnings.extend(job_errors)
    unknown_job_keys: set[str] = set()
    if config.get("job_records_enabled") and job_tree is None:
        report.job_key_notice = "No active job-record key tree. Job-record keys were omitted."
    rules = active_exclusion_rules(config)
    report.exclusions_applied = bool(rules)
    excluded_filenames: list[str] = []

    incoming: list[dict[str, Any]] = []
    note_updates: dict[str, dict[str, Any]] = {}
    note_conflicts: dict[str, list[str]] = {}
    audio_media_by_note: dict[str, list[dict[str, Any]]] = {}
    total = max(1, len(clips))
    for index, clip in enumerate(clips, start=1):
        record, link, job_missing = _job_match_for_clip(clip, job_index)
        if rules:
            hit = first_matching_rule(
                clip_match_document(
                    clip,
                    record=record,
                    link=link,
                    anki_keys=anki_keys,
                    job_selection=job_selection,
                ),
                rules,
            )
            if hit is not None:
                report.clips_excluded += 1
                excluded_filenames.append(clip.filename)
                if len(report.exclusion_samples) < 12:
                    report.exclusion_samples.append(_exclusion_sample(clip.filename, hit))
                if progress:
                    progress(index, total, f"excluded {clip.filename}")
                continue

        if progress:
            progress(index, total, clip.filename)
        exists = os.path.isfile(clip.media_path)
        used_filename = clip.filename
        copied = False
        size_bytes = None
        if exists:
            try:
                size_bytes = os.path.getsize(clip.media_path)
                used_filename, copied = copy_media_file(clip.media_path, media_root, clip.filename)
                if copied:
                    report.media_copied += 1
            except OSError as error:
                exists = False
                report.warnings.append(f"Failed to copy {clip.filename}: {error}")
        if not exists:
            report.media_missing += 1
            report.warnings.append(f"Missing media file: {clip.filename}")

        job_payload = None
        if job_index is not None:
            if not job_missing:
                if link.status == "linked":
                    report.records_linked += 1
                else:
                    report.records_partial += 1
                if record:
                    unknown_job_keys.update(unknown_job_key_paths(record, set(job_selection)))
                job_payload = prepare_job_payload(
                    record,
                    selection=job_selection,
                    privacy_mode=str(config.get("path_privacy_mode") or "off"),
                    privacy_parents=int(config.get("path_privacy_parents") or 1),
                    strict=True,
                )
            else:
                report.records_missing += 1

        audio_key = _note_key(clip.note_id)
        if audio_key not in audio_media_by_note:
            audio_media: list[dict[str, Any]] = []
            if is_key_included("anki.audio_fields", anki_keys):
                for field_name, payload in clip.audio_fields.items():
                    if not is_key_included(f"anki.audio_fields.{field_name}", anki_keys):
                        continue
                    for audio_name in payload.get("files") or []:
                        audio_source = os.path.join(os.path.dirname(clip.media_path), audio_name) if clip.media_path else audio_name
                        audio_exists = os.path.isfile(audio_source)
                        audio_size = None
                        used_audio = audio_name
                        if audio_exists:
                            try:
                                audio_size = os.path.getsize(audio_source)
                                used_audio, copied_audio = copy_media_file(audio_source, media_root, audio_name)
                                if copied_audio:
                                    report.media_copied += 1
                            except OSError as error:
                                audio_exists = False
                                report.warnings.append(f"Failed to copy {audio_name}: {error}")
                        if not audio_exists:
                            report.media_missing += 1
                            report.warnings.append(f"Missing audio file: {audio_name}")
                        audio_media.append(
                            {
                                "filename": used_audio,
                                "size_bytes": audio_size,
                                "exists": audio_exists,
                                "field": field_name,
                            }
                        )
            audio_media_by_note[audio_key] = audio_media
        audio_media = audio_media_by_note[audio_key]

        entry = library_clip_dict(
            clip_id=stable_clip_id(clip, record),
            filename=used_filename,
            size_bytes=size_bytes,
            exists=exists,
            discovered=clip,
            job=job_payload,
            link=link,
        )
        raw_anki = entry.get("anki") if isinstance(entry.get("anki"), dict) else {}
        filtered_anki = filter_anki_section(raw_anki, anki_keys)
        note_part, clip_anki = partition_anki_section(filtered_anki, raw_anki.get("note_id"))
        if note_part and audio_media and isinstance(note_part.get("audio_fields"), dict):
            note_part["audio_fields"] = merge_audio_media(note_part["audio_fields"], audio_media, overwrite=True)
        elif audio_media:
            entry["media"]["audio"] = audio_media
        entry["anki"] = clip_anki
        note_id = clip_anki.get("note_id")
        if note_part and note_id not in (None, ""):
            key = _note_key(note_id)
            note_part = _finalize_note(note_part)
            previous = note_updates.get(key)
            if previous is not None and previous != note_part:
                seen = set(note_conflicts.get(key, []))
                differed: list[str] = []
                for field in list(previous) + list(note_part):
                    if field in seen or previous.get(field) == note_part.get(field):
                        continue
                    seen.add(field)
                    differed.append(field)
                note_conflicts.setdefault(key, []).extend(differed)
            note_updates[key] = note_part
        incoming.append(entry)

    library["clips"] = merge_clips(list(library.get("clips") or []), incoming)
    notes = library.get("notes") if isinstance(library.get("notes"), dict) else {}
    notes.update(note_updates)
    library["notes"] = notes
    prune_unreferenced_notes(library)
    library["field_sets"] = merge_library_field_sets(
        previous_field_sets,
        current_field_sets,
        referenced_field_set_indexes(library),
    )
    if existing and excluded_filenames:
        already = library_media_filenames(existing)
        left = sum(1 for filename in excluded_filenames if filename in already)
        if left:
            report.warnings.append(
                f"{left} excluded clip(s) were already in the library and were left unchanged."
            )
    for key, fields in note_conflicts.items():
        shown = ", ".join(fields)
        report.warnings.append(
            f"Note {key} shared fields differed between clips ({shown}); kept the latest values."
        )
    if job_tree is not None and unknown_job_keys:
        shown = ", ".join(sorted(unknown_job_keys)[:12])
        extra = "" if len(unknown_job_keys) <= 12 else f" (+{len(unknown_job_keys) - 12} more)"
        report.job_key_notice = (
            f"Job records contained {len(unknown_job_keys)} key(s) not in the active tree. "
            f"Those keys were omitted: {shown}{extra}"
        )
    if report.job_key_notice:
        report.warnings.append(report.job_key_notice)
    write_library(library_path, library)
    report.library_path = library_path
    return report
