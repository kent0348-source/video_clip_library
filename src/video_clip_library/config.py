from __future__ import annotations

import copy
from typing import Any

from .exclusions import normalize_export_exclusion_presets, normalize_export_exclusions
from .models import FIELD_ROLES, ExtraField, FieldSet
from .textutil import example_text


ADDON_ID = __name__.split(".", 1)[0]

IMPORT_SIGNALS = (
    "note_id",
    "sort_field",
    "sentence",
    "secondary",
    "video_filename",
    "source_filename",
    "deck_name",
    "field_set_index",
    "timestamps",
    "miscinfo_source",
)

DEFAULT_IMPORT_SIGNALS: dict[str, dict[str, Any]] = {
    "note_id": {"enabled": True, "weight": 40},
    "sort_field": {"enabled": True, "weight": 30},
    "sentence": {"enabled": True, "weight": 20},
    "secondary": {"enabled": True, "weight": 10},
    "video_filename": {"enabled": True, "weight": 25},
    "source_filename": {"enabled": True, "weight": 15},
    "deck_name": {"enabled": True, "weight": 5},
    "field_set_index": {"enabled": True, "weight": 5},
    "timestamps": {"enabled": True, "weight": 10},
    "miscinfo_source": {"enabled": True, "weight": 10},
}

SIGNAL_LABELS = {
    "note_id": "Note ID",
    "sort_field": "Sort field (e.g. expression)",
    "sentence": "Sentence text",
    "secondary": "Secondary / translation text",
    "video_filename": "Existing video filename",
    "source_filename": "Source video filename",
    "deck_name": "Deck name",
    "field_set_index": "Field set index / names",
    "timestamps": "Clip timestamps",
    "miscinfo_source": "Miscinfo source title",
}

DEFAULT_PATH_PRIVACY_MODE = "filename_only"
PATH_PRIVACY_MODES = ("off", "filename_only", "keep_parents")


def default_field_set_name(index: int) -> str:
    number = index if index > 0 else 1
    return f"Video clip {number}"


DEFAULT_FIELD_SETS = [
    {
        "enabled": True,
        "name": default_field_set_name(1),
        "video": "",
        "sentence": "",
        "secondary": "",
        "miscinfo": "",
        "extras": {},
    },
]

DEFAULT_CLIP_IDENTITY: list[dict[str, str]] = [
    {"source": "sort_field"},
    {"source": "video_filename"},
]

DEFAULT_NOTE_IDENTITY: list[dict[str, str]] = [
    {"source": "sort_field"},
    {"source": "note_id"},
]

DEFAULT_CONFIG: dict[str, Any] = {
    "note_type": "",
    "field_sets": copy.deepcopy(DEFAULT_FIELD_SETS),
    "import_note_type": "",
    "import_field_sets": copy.deepcopy(DEFAULT_FIELD_SETS),
    "import_mapping_initialized": False,
    "clip_identity": copy.deepcopy(DEFAULT_CLIP_IDENTITY),
    "import_note_identity": copy.deepcopy(DEFAULT_NOTE_IDENTITY),
    "import_extra_roles": [],
    "import_note_profiles": {},
    "viewer_autoplay": True,
    "generic_fields": [],
    "audio_fields": [],
    "clip_extra_roles": [],
    "job_records_enabled": False,
    "job_index_path": "",
    "job_record_keys": {},
    "job_record_trees": [],
    "active_job_record_tree": "",
    "anki_export_keys": {},
    "path_privacy_mode": DEFAULT_PATH_PRIVACY_MODE,
    "path_privacy_parents": 1,
    "mpv_executable": "mpv",
    "library_folders": [],
    "last_export_dir": "",
    "export_mode": "fresh",
    "export_exclusions_enabled": False,
    "export_exclusions": [],
    "export_exclusion_presets": [],
    "import_mode": "match",
    "conflict_mode": "silent",
    "create_target_deck": "",
    "import_signals": copy.deepcopy(DEFAULT_IMPORT_SIGNALS),
    "import_rules": [],
    "import_copies": [],
    "import_decks": [],
    "import_minimum": "medium",
}


def default_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_CONFIG)


def normalize_clip_extra_roles(value: Any) -> list[dict[str, str]]:
    raw = [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for source in raw:
        role_id = _clean_role_id(str(source.get("id") or ""))
        if not role_id or role_id in seen or role_id in FIELD_ROLES:
            role_id = _new_role_id(seen)
        seen.add(role_id)
        number = 4 + len(normalized) + 1
        name = str(source.get("name") or "").strip() or f"Field {number}"
        normalized.append({"id": role_id, "name": name})
    return normalized


def _clean_role_id(value: str) -> str:
    cleaned = "".join(character for character in value.strip() if character.isalnum() or character == "_")
    return cleaned


def _new_role_id(existing: set[str]) -> str:
    number = 5
    while True:
        role_id = f"field_{number}"
        if role_id not in existing and role_id not in FIELD_ROLES:
            return role_id
        number += 1


def roles_covering_field_sets(roles: Any, field_sets: Any) -> list[dict[str, str]]:
    """Role list plus any extra-field ids already stored on the sets."""
    normalized = normalize_clip_extra_roles(roles)
    known = {role["id"] for role in normalized}
    raw_sets = field_sets if isinstance(field_sets, list) else []
    for source in raw_sets:
        if not isinstance(source, dict):
            continue
        extras = source.get("extras") if isinstance(source.get("extras"), dict) else {}
        for role_id in extras:
            cleaned = _clean_role_id(str(role_id))
            if not cleaned or cleaned in known or cleaned in FIELD_ROLES:
                continue
            known.add(cleaned)
            number = 4 + len(normalized) + 1
            normalized.append({"id": cleaned, "name": f"Field {number}"})
    return normalized


def import_role_ids(config: dict[str, Any]) -> list[str]:
    return [
        str(role.get("id") or "")
        for role in config.get("import_extra_roles") or []
        if isinstance(role, dict) and str(role.get("id") or "").strip()
    ]


def blank_field_set(index: int, role_ids: list[str] | None = None, *, enabled: bool | None = None) -> dict[str, Any]:
    return {
        "enabled": index == 0 if enabled is None else bool(enabled),
        "name": default_field_set_name(index),
        "video": "",
        "sentence": "",
        "secondary": "",
        "miscinfo": "",
        "extras": {role_id: "" for role_id in role_ids or []},
    }


def _positive_index(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def assign_field_set_indexes(raw_sets: list[dict[str, Any]]) -> list[int]:
    """Keep stored indexes. New sets get the next unused positive index, in list order."""
    used: set[int] = set()
    chosen: list[int | None] = []
    for source in raw_sets:
        number = _positive_index(source.get("index"))
        if number is not None and number not in used:
            used.add(number)
            chosen.append(number)
        else:
            chosen.append(None)
    next_index = 1
    assigned: list[int] = []
    for number in chosen:
        if number is None:
            while next_index in used:
                next_index += 1
            number = next_index
            used.add(number)
            next_index += 1
        assigned.append(number)
    return assigned


def normalize_field_sets(value: Any, role_ids: list[str] | None = None) -> list[dict[str, Any]]:
    raw_sets = [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    if not raw_sets:
        raw_sets = copy.deepcopy(DEFAULT_FIELD_SETS)
    indexes = assign_field_set_indexes(raw_sets)
    normalized: list[dict[str, Any]] = []
    for index, source in enumerate(raw_sets):
        raw_extras = source.get("extras") if isinstance(source.get("extras"), dict) else {}
        if role_ids is None:
            extras = {
                _clean_role_id(str(role_id)): str(name or "").strip()
                for role_id, name in raw_extras.items()
                if _clean_role_id(str(role_id)) and _clean_role_id(str(role_id)) not in FIELD_ROLES
            }
        else:
            extras = {role_id: str(raw_extras.get(role_id) or "").strip() for role_id in role_ids}
        entry = {
            "enabled": bool(source.get("enabled", index == 0)),
            "index": indexes[index],
            "name": str(source.get("name", "") or "").strip() or default_field_set_name(index + 1),
            "video": str(source.get("video", "") or "").strip(),
            "sentence": str(source.get("sentence", "") or "").strip(),
            "secondary": str(source.get("secondary", "") or "").strip(),
            "miscinfo": str(source.get("miscinfo", "") or "").strip(),
            "extras": extras,
        }
        if index == 0:
            entry["enabled"] = True
        normalized.append(entry)
    return normalized


def normalize_extra_fields(value: Any, *, kind: str, label_prefix: str) -> list[dict[str, Any]]:
    raw = [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    normalized: list[dict[str, Any]] = []
    for index, source in enumerate(raw, start=1):
        normalized.append(
            {
                "enabled": bool(source.get("enabled", False)),
                "name": str(source.get("name", "") or "").strip() or f"{label_prefix} {index}",
                "field": str(source.get("field", "") or "").strip(),
                "kind": kind,
            }
        )
    return normalized


def dedupe_field_names(field_sets: list[dict[str, Any]]) -> None:
    used: set[str] = set()

    def take(name: str) -> str:
        cleaned = str(name or "").strip()
        key = cleaned.casefold()
        if not cleaned or key in used:
            return ""
        used.add(key)
        return cleaned

    for field_set in field_sets:
        if not isinstance(field_set, dict):
            continue
        for role in FIELD_ROLES:
            field_set[role] = take(str(field_set.get(role, "") or ""))
        extras = field_set.get("extras") if isinstance(field_set.get("extras"), dict) else {}
        for role_id in list(extras):
            extras[role_id] = take(str(extras.get(role_id, "") or ""))


def visible_field_choices(fields: list[str], used: set[str], current: str) -> list[str]:
    """Field names still available. `used` is casefolded. The current choice stays visible."""
    current_key = str(current or "").casefold()
    shown: list[str] = []
    seen: set[str] = set()
    for name in fields:
        cleaned = str(name or "").strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        if key in used and key != current_key:
            continue
        seen.add(key)
        shown.append(cleaned)
    return shown


def _dedupe_configured_fields(config: dict[str, Any]) -> None:
    used: set[str] = set()

    def take(name: str) -> str:
        cleaned = str(name or "").strip()
        key = cleaned.casefold()
        if not cleaned or key in used:
            return ""
        used.add(key)
        return cleaned

    for field_set in config.get("field_sets", []):
        for role in FIELD_ROLES:
            field_set[role] = take(field_set.get(role, ""))
        extras = field_set.get("extras") if isinstance(field_set.get("extras"), dict) else {}
        for role_id in list(extras):
            extras[role_id] = take(extras.get(role_id, ""))
    for extra in list(config.get("generic_fields", [])) + list(config.get("audio_fields", [])):
        extra["field"] = take(extra.get("field", ""))


def field_sets_from_config(config: dict[str, Any]) -> list[FieldSet]:
    sets: list[FieldSet] = []
    role_ids = None
    if "clip_extra_roles" in config:
        role_ids = [
            str(role.get("id") or "")
            for role in config.get("clip_extra_roles") or []
            if isinstance(role, dict) and str(role.get("id") or "").strip()
        ]
    for position, raw in enumerate(normalize_field_sets(config.get("field_sets", []), role_ids), start=1):
        sets.append(
            FieldSet(
                enabled=bool(raw.get("enabled")),
                name=str(raw.get("name") or default_field_set_name(position)),
                index=int(raw.get("index") or position),
                video=str(raw.get("video") or ""),
                sentence=str(raw.get("sentence") or ""),
                secondary=str(raw.get("secondary") or ""),
                miscinfo=str(raw.get("miscinfo") or ""),
                extras={
                    str(role_id): str(name or "")
                    for role_id, name in (raw.get("extras") or {}).items()
                    if str(role_id).strip()
                }
                if isinstance(raw.get("extras"), dict)
                else {},
            )
        )
    return sets


def enabled_field_sets(config: dict[str, Any]) -> list[FieldSet]:
    return [field_set for field_set in field_sets_from_config(config) if field_set.is_complete()]


def import_field_sets_from_config(config: dict[str, Any]) -> list[FieldSet]:
    sets: list[FieldSet] = []
    role_ids = import_role_ids(config) if "import_extra_roles" in config else None
    for position, raw in enumerate(normalize_field_sets(config.get("import_field_sets", []), role_ids), start=1):
        sets.append(
            FieldSet(
                enabled=bool(raw.get("enabled")),
                name=str(raw.get("name") or default_field_set_name(position)),
                index=int(raw.get("index") or position),
                video=str(raw.get("video") or ""),
                sentence=str(raw.get("sentence") or ""),
                secondary=str(raw.get("secondary") or ""),
                miscinfo=str(raw.get("miscinfo") or ""),
                extras={
                    str(role_id): str(name or "")
                    for role_id, name in (raw.get("extras") or {}).items()
                    if str(role_id).strip()
                }
                if isinstance(raw.get("extras"), dict)
                else {},
            )
        )
    return sets


def enabled_import_field_sets(config: dict[str, Any]) -> list[FieldSet]:
    return [field_set for field_set in import_field_sets_from_config(config) if field_set.is_complete()]


def extra_fields_from_config(config: dict[str, Any], kind: str) -> list[ExtraField]:
    key = "generic_fields" if kind == "generic" else "audio_fields"
    extras: list[ExtraField] = []
    for index, raw in enumerate(config.get(key, []), start=1):
        if not isinstance(raw, dict):
            continue
        extras.append(
            ExtraField(
                enabled=bool(raw.get("enabled")),
                name=str(raw.get("name") or f"{'Field' if kind == 'generic' else 'Audio field'} {index}"),
                field=str(raw.get("field") or ""),
                kind=kind,
                index=index,
            )
        )
    return extras


def enabled_extra_fields(config: dict[str, Any], kind: str) -> list[ExtraField]:
    return [item for item in extra_fields_from_config(config, kind) if item.is_complete()]


def used_field_names(config: dict[str, Any], *, except_role: tuple[int, str] | None = None) -> set[str]:
    used: set[str] = set()
    for index, field_set in enumerate(field_sets_from_config(config)):
        for role, name in field_set.role_map().items():
            if not name:
                continue
            if except_role and except_role == (index, role):
                continue
            used.add(name.casefold())
        for role_id, name in field_set.extra_map().items():
            if not name:
                continue
            if except_role and except_role == (index, role_id):
                continue
            used.add(name.casefold())
    for extra in extra_fields_from_config(config, "generic") + extra_fields_from_config(config, "audio"):
        if extra.field:
            used.add(extra.field.casefold())
    return used


IMPORT_POINT_LEVELS = ("high", "medium", "low")
IMPORT_POINT_VALUES = {"high": 30.0, "medium": 20.0, "low": 10.0}
IMPORT_COMPARES = ("exact", "contains")
SORT_FIELD_ID = "sort_field"
DEFAULT_IMPORT_RULE: dict[str, str] = {
    "source": SORT_FIELD_ID,
    "compare": "contains",
    "target_field": SORT_FIELD_ID,
    "points": "medium",
}


def import_point_value(level: str) -> float:
    return float(IMPORT_POINT_VALUES.get(str(level or ""), 20.0))


def normalize_import_rules(value: Any) -> list[dict[str, str]]:
    raw = [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    rules: list[dict[str, str]] = []
    for source in raw:
        source_id = str(source.get("source") or "").strip()
        target_field = str(source.get("target_field") or "").strip()
        if not source_id or not target_field or source_id == "note_id" or source_id.endswith(".note_id"):
            continue
        compare = str(source.get("compare") or "exact").strip()
        if compare not in IMPORT_COMPARES:
            compare = "exact"
        points = str(source.get("points") or "medium").strip()
        if points not in IMPORT_POINT_LEVELS:
            points = "medium"
        rules.append(
            {
                "source": source_id,
                "compare": compare,
                "target_field": target_field,
                "points": points,
            }
        )
    return rules


def normalize_import_copies(value: Any) -> list[dict[str, str]]:
    raw = [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    copies: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source in raw:
        source_id = str(source.get("source") or "").strip()
        target_field = str(source.get("target_field") or "").strip()
        if not source_id or not target_field:
            continue
        token = (source_id, target_field.casefold())
        if token in seen:
            continue
        seen.add(token)
        copies.append({"source": source_id, "target_field": target_field})
    return copies


def normalize_import_decks(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    decks: list[str] = []
    seen: set[str] = set()
    for item in value:
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        decks.append(name)
    return decks


def normalize_import_signals(value: Any) -> dict[str, dict[str, Any]]:
    incoming = value if isinstance(value, dict) else {}
    normalized: dict[str, dict[str, Any]] = {}
    for signal in IMPORT_SIGNALS:
        raw = incoming.get(signal, {})
        if not isinstance(raw, dict):
            raw = {}
        default = DEFAULT_IMPORT_SIGNALS[signal]
        try:
            weight = float(raw.get("weight", default["weight"]))
        except (TypeError, ValueError):
            weight = float(default["weight"])
        normalized[signal] = {
            "enabled": bool(raw.get("enabled", default["enabled"])),
            "weight": max(0.0, min(100.0, weight)),
        }
    return normalized


def normalize_library_folders(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    folders: list[str] = []
    seen: set[str] = set()
    for item in value:
        path = str(item or "").strip()
        if not path:
            continue
        key = path.casefold()
        if key in seen:
            continue
        seen.add(key)
        folders.append(path)
    return folders


def normalize_job_record_trees(value: Any) -> list[dict[str, Any]]:
    raw_trees = [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_decks: set[str] = set()
    for source in raw_trees:
        deck_name = str(source.get("deck_name") or "").strip()
        tree_id = str(source.get("id") or "").strip()
        if not deck_name or not tree_id:
            continue
        deck_key = deck_name.casefold()
        if tree_id in seen_ids or deck_key in seen_decks:
            continue
        seen_ids.add(tree_id)
        seen_decks.add(deck_key)
        samples: dict[str, list[str]] = {}
        raw_samples = source.get("samples") if isinstance(source.get("samples"), dict) else {}
        for sample_id, paths in raw_samples.items():
            identity = str(sample_id or "").strip()
            if not identity or not isinstance(paths, list):
                continue
            cleaned: list[str] = []
            seen_paths: set[str] = set()
            for path in paths:
                text = str(path or "").strip()
                if not text or text in seen_paths:
                    continue
                seen_paths.add(text)
                cleaned.append(text)
            samples[identity] = cleaned
        keys: dict[str, dict[str, Any]] = {}
        raw_keys = source.get("keys") if isinstance(source.get("keys"), dict) else {}
        for path, state in raw_keys.items():
            text = str(path or "").strip()
            if not text:
                continue
            if isinstance(state, dict):
                try:
                    count = int(state.get("count") or 0)
                except (TypeError, ValueError):
                    count = 0
                enabled = bool(state.get("enabled", True))
            else:
                enabled = bool(state)
                count = 0
            keys[text] = {"enabled": enabled, "count": max(0, count)}
        pairs: list[dict[str, str]] = []
        seen_pairs: set[tuple[str, str]] = set()
        raw_pairs = source.get("redundancy_pairs") if isinstance(source.get("redundancy_pairs"), list) else []
        for pair in raw_pairs:
            if not isinstance(pair, dict):
                continue
            clip_key = str(pair.get("clip_key") or "").strip()
            job_key = str(pair.get("job_key") or "").strip()
            token = (clip_key, job_key)
            if not clip_key or not job_key or token in seen_pairs:
                continue
            seen_pairs.add(token)
            pairs.append({"clip_key": clip_key, "job_key": job_key})
        examples: dict[str, str] = {}
        raw_examples = source.get("examples") if isinstance(source.get("examples"), dict) else {}
        for path, value in raw_examples.items():
            text = str(path or "").strip()
            cleaned = example_text(value)
            if text and cleaned:
                examples[text] = cleaned
        normalized.append(
            {
                "id": tree_id,
                "name": str(source.get("name") or "").strip() or deck_name,
                "deck_name": deck_name,
                "samples": samples,
                "examples": examples,
                "keys": keys,
                "redundancy_pairs": pairs,
            }
        )
    return normalized


def active_job_tree(config: dict[str, Any]) -> dict[str, Any] | None:
    active_id = str(config.get("active_job_record_tree") or "").strip()
    if not active_id:
        return None
    for tree in config.get("job_record_trees") or []:
        if isinstance(tree, dict) and str(tree.get("id") or "") == active_id:
            return tree
    return None


def normalize_import_note_profiles(value: Any) -> dict[str, dict[str, Any]]:
    raw = value if isinstance(value, dict) else {}
    profiles: dict[str, dict[str, Any]] = {}
    for name, item in raw.items():
        note_type = str(name or "").strip()
        if not note_type or not isinstance(item, dict):
            continue
        extra_roles = roles_covering_field_sets(item.get("extra_roles"), item.get("field_sets"))
        role_ids = [role["id"] for role in extra_roles]
        field_sets = normalize_field_sets(item.get("field_sets"), role_ids)
        dedupe_field_names(field_sets)
        profiles[note_type] = {
            "field_sets": field_sets,
            "note_identity": normalize_identity(item.get("note_identity")),
            "extra_roles": extra_roles,
        }
    return profiles


def remember_import_note_profile(config: dict[str, Any], note_type: str | None = None) -> None:
    name = str(config.get("import_note_type") if note_type is None else note_type or "").strip()
    if not name:
        return
    profiles = config.get("import_note_profiles")
    if not isinstance(profiles, dict):
        profiles = {}
        config["import_note_profiles"] = profiles
    field_sets = copy.deepcopy(config.get("import_field_sets") or [blank_field_set(1)])
    dedupe_field_names(field_sets)
    profiles[name] = {
        "field_sets": field_sets,
        "note_identity": normalize_identity(config.get("import_note_identity")),
        "extra_roles": copy.deepcopy(config.get("import_extra_roles") or []),
    }


def switch_import_note_type(config: dict[str, Any], new_type: str) -> dict[str, Any]:
    new = str(new_type or "").strip()
    old = str(config.get("import_note_type") or "").strip()
    if new == old:
        return config
    if old:
        remember_import_note_profile(config, old)
    config["import_note_type"] = new
    config["import_mapping_initialized"] = True
    profiles = config.get("import_note_profiles") if isinstance(config.get("import_note_profiles"), dict) else {}
    saved = profiles.get(new) if new else None
    if isinstance(saved, dict):
        config["import_field_sets"] = copy.deepcopy(saved.get("field_sets") or [blank_field_set(1)])
        config["import_note_identity"] = copy.deepcopy(saved.get("note_identity") or DEFAULT_NOTE_IDENTITY)
        config["import_extra_roles"] = copy.deepcopy(saved.get("extra_roles") or [])
    else:
        config["import_field_sets"] = [blank_field_set(1)]
        config["import_note_identity"] = copy.deepcopy(DEFAULT_NOTE_IDENTITY)
        config["import_extra_roles"] = []
    config["import_extra_roles"] = roles_covering_field_sets(config.get("import_extra_roles"), config.get("import_field_sets"))
    config["import_field_sets"] = normalize_field_sets(config["import_field_sets"], import_role_ids(config))
    dedupe_field_names(config["import_field_sets"])
    config["import_note_identity"] = normalize_identity(config["import_note_identity"])
    if new:
        remember_import_note_profile(config, new)
    return config


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


def normalize_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    merged = default_config()
    incoming = raw if isinstance(raw, dict) else {}
    mapping_initialized = "import_mapping_initialized" in incoming
    for key, value in incoming.items():
        merged[key] = value
    merged.pop("include_job_records_on_export", None)
    merged["note_type"] = str(merged.get("note_type", "") or "").strip()
    if not mapping_initialized:
        merged["import_note_type"] = str(incoming.get("note_type") or "").strip()
        source_sets = incoming.get("field_sets")
        merged["import_field_sets"] = copy.deepcopy(source_sets) if isinstance(source_sets, list) and source_sets else copy.deepcopy(DEFAULT_FIELD_SETS)
        if "import_extra_roles" not in incoming:
            merged["import_extra_roles"] = copy.deepcopy(incoming.get("clip_extra_roles") or [])
        merged["import_mapping_initialized"] = True
    else:
        merged["import_note_type"] = str(merged.get("import_note_type") or "").strip()
        merged["import_mapping_initialized"] = bool(merged.get("import_mapping_initialized"))
    merged["import_extra_roles"] = roles_covering_field_sets(
        merged.get("import_extra_roles"),
        merged.get("import_field_sets"),
    )
    merged["import_field_sets"] = normalize_field_sets(merged.get("import_field_sets", []), import_role_ids(merged))
    if "clip_identity" not in incoming:
        merged["clip_identity"] = copy.deepcopy(DEFAULT_CLIP_IDENTITY)
    else:
        merged["clip_identity"] = normalize_identity(merged.get("clip_identity"))
    if "import_note_identity" not in incoming:
        merged["import_note_identity"] = copy.deepcopy(DEFAULT_NOTE_IDENTITY)
    else:
        merged["import_note_identity"] = normalize_identity(merged.get("import_note_identity"))
    dedupe_field_names(merged["import_field_sets"])
    merged["import_note_profiles"] = normalize_import_note_profiles(merged.get("import_note_profiles"))
    active_import_type = str(merged.get("import_note_type") or "").strip()
    if active_import_type:
        merged["import_note_profiles"][active_import_type] = {
            "field_sets": copy.deepcopy(merged["import_field_sets"]),
            "note_identity": copy.deepcopy(merged["import_note_identity"]),
            "extra_roles": copy.deepcopy(merged["import_extra_roles"]),
        }
    merged["viewer_autoplay"] = bool(merged.get("viewer_autoplay", True))
    merged["clip_extra_roles"] = normalize_clip_extra_roles(merged.get("clip_extra_roles", []))
    role_ids = [str(role.get("id") or "") for role in merged["clip_extra_roles"]]
    merged["field_sets"] = normalize_field_sets(merged.get("field_sets", []), role_ids)
    merged["generic_fields"] = normalize_extra_fields(merged.get("generic_fields", []), kind="generic", label_prefix="Field")
    merged["audio_fields"] = normalize_extra_fields(merged.get("audio_fields", []), kind="audio", label_prefix="Audio field")
    _dedupe_configured_fields(merged)
    merged["job_records_enabled"] = bool(merged.get("job_records_enabled", False))
    merged["job_index_path"] = str(merged.get("job_index_path", "") or "").strip()
    merged["job_record_keys"] = dict(merged.get("job_record_keys") or {}) if isinstance(merged.get("job_record_keys"), dict) else {}
    merged["job_record_trees"] = normalize_job_record_trees(merged.get("job_record_trees", []))
    active_tree_id = str(merged.get("active_job_record_tree") or "").strip()
    known_tree_ids = {str(tree.get("id") or "") for tree in merged["job_record_trees"]}
    merged["active_job_record_tree"] = active_tree_id if active_tree_id in known_tree_ids else ""
    merged["anki_export_keys"] = dict(merged.get("anki_export_keys") or {}) if isinstance(merged.get("anki_export_keys"), dict) else {}
    mode = str(merged.get("path_privacy_mode", DEFAULT_PATH_PRIVACY_MODE) or DEFAULT_PATH_PRIVACY_MODE).strip()
    if mode not in PATH_PRIVACY_MODES:
        mode = DEFAULT_PATH_PRIVACY_MODE
    merged["path_privacy_mode"] = mode
    try:
        parents = int(merged.get("path_privacy_parents", 1))
    except (TypeError, ValueError):
        parents = 1
    merged["path_privacy_parents"] = max(0, min(6, parents))
    merged["mpv_executable"] = str(merged.get("mpv_executable", "mpv") or "mpv").strip() or "mpv"
    merged["library_folders"] = normalize_library_folders(merged.get("library_folders", []))
    merged["last_export_dir"] = str(merged.get("last_export_dir", "") or "").strip()
    export_mode = str(merged.get("export_mode", "fresh") or "fresh")
    merged["export_mode"] = export_mode if export_mode in {"fresh", "update"} else "fresh"
    merged["export_exclusions_enabled"] = bool(merged.get("export_exclusions_enabled", False))
    merged["export_exclusion_presets"] = normalize_export_exclusion_presets(merged.get("export_exclusion_presets"))
    merged["export_exclusions"] = normalize_export_exclusions(merged.get("export_exclusions"))
    merged["import_mode"] = "match"
    merged["conflict_mode"] = "silent"
    merged["create_target_deck"] = ""
    merged["import_signals"] = normalize_import_signals(merged.get("import_signals", {}))
    rules_were_seeded = "import_rules_seeded" in incoming
    merged["import_rules"] = normalize_import_rules(merged.get("import_rules"))
    if not rules_were_seeded and not merged["import_rules"]:
        merged["import_rules"] = normalize_import_rules([DEFAULT_IMPORT_RULE])
    merged["import_rules_seeded"] = True
    merged["import_copies"] = normalize_import_copies(merged.get("import_copies"))
    merged["import_decks"] = normalize_import_decks(merged.get("import_decks"))
    minimum = str(merged.get("import_minimum") or "medium").strip()
    merged["import_minimum"] = minimum if minimum in IMPORT_POINT_LEVELS else "medium"
    return merged


def config_ready_for_export(config: dict[str, Any]) -> tuple[bool, str]:
    if not str(config.get("note_type", "") or "").strip():
        return False, "Choose an export note type in Clip Library settings."
    if not enabled_field_sets(config):
        return False, "The export field set needs a video field."
    return True, ""


def load_config() -> dict[str, Any]:
    raw: Any = {}
    loaded = False
    try:
        from aqt import mw

        raw = mw.addonManager.getConfig(ADDON_ID) or {}
        loaded = True
    except Exception:
        raw = {}
    incoming = raw if isinstance(raw, dict) else {}
    normalized = normalize_config(incoming)
    if loaded and "import_mapping_initialized" not in incoming:
        return save_config(normalized)
    return normalized


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_config(config)
    try:
        from aqt import mw

        mw.addonManager.writeConfig(ADDON_ID, normalized)
    except Exception:
        pass
    return normalized


def update_config(**changes: Any) -> dict[str, Any]:
    config = load_config()
    config.update(changes)
    return save_config(config)
