from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


FIELD_ROLES = ("video", "sentence", "secondary", "miscinfo")
REQUIRED_ROLES = ("video",)
LIBRARY_FORMAT = "clip_library"
LIBRARY_SCHEMA_VERSION = 4
LIBRARY_FILENAME = "clip_library.json"
NOTE_LEVEL_ANKI_KEYS = (
    "note_id",
    "deck_name",
    "model_name",
    "sort_field_name",
    "sort_field_value",
    "extra_fields",
    "audio_fields",
)
MEDIA_DIRNAME = "media"
RECORDS_DIRNAME = "records"


@dataclass
class FieldSet:
    enabled: bool = True
    name: str = "Field Set"
    index: int = 1
    video: str = ""
    sentence: str = ""
    secondary: str = ""
    miscinfo: str = ""
    extras: dict[str, str] = field(default_factory=dict)

    def role_map(self) -> dict[str, str]:
        return {role: str(getattr(self, role, "") or "").strip() for role in FIELD_ROLES}

    def extra_map(self) -> dict[str, str]:
        return {str(role_id): str(name or "").strip() for role_id, name in self.extras.items() if str(role_id).strip()}

    def used_fields(self) -> list[str]:
        return [name for name in list(self.role_map().values()) + list(self.extra_map().values()) if name]

    def is_complete(self) -> bool:
        if not self.enabled:
            return False
        return bool(self.video.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "name": self.name,
            "index": self.index,
            "video": self.video,
            "sentence": self.sentence,
            "secondary": self.secondary,
            "miscinfo": self.miscinfo,
            "extras": dict(self.extra_map()),
        }

    def library_dict(self) -> dict[str, Any]:
        """Field-set record stored in a library. Enabled is implied by inclusion."""
        return {
            "index": int(self.index),
            "name": self.name,
            "video": self.video,
            "sentence": self.sentence,
            "secondary": self.secondary,
            "miscinfo": self.miscinfo,
            "extras": dict(self.extra_map()),
        }


@dataclass
class ExtraField:
    enabled: bool = True
    name: str = ""
    field: str = ""
    kind: str = "generic"
    index: int = 1

    def is_complete(self) -> bool:
        return bool(self.enabled and self.field.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "name": self.name,
            "field": self.field,
            "kind": self.kind,
        }

    def library_dict(self) -> dict[str, str]:
        """Extra-field definition stored in a library. Kind is implied by the parent array."""
        return {"name": self.name, "field": self.field}


@dataclass
class FieldValue:
    name: str = ""
    value: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "value": self.value}


@dataclass
class DiscoveredClip:
    note_id: int
    deck_name: str
    model_name: str
    sort_field_name: str
    sort_field_value: str
    field_set_index: int
    field_set_name: str
    filename: str
    media_path: str
    fields: dict[str, FieldValue] = field(default_factory=dict)
    extra_fields: dict[str, FieldValue] = field(default_factory=dict)
    audio_fields: dict[str, dict[str, Any]] = field(default_factory=dict)

    def field_text(self, role: str) -> str:
        item = self.fields.get(role)
        return item.value if item else ""

    def audio_filenames(self) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for payload in self.audio_fields.values():
            for filename in payload.get("files") or []:
                key = str(filename).casefold()
                if filename and key not in seen:
                    seen.add(key)
                    names.append(str(filename))
        return names


@dataclass
class LinkInfo:
    status: str = "anki_only"
    matched_by: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExportReport:
    notes_scanned: int = 0
    notes_skipped_wrong_type: int = 0
    clips_found: int = 0
    clips_excluded: int = 0
    exclusions_applied: bool = False
    exclusion_samples: list[str] = field(default_factory=list)
    media_copied: int = 0
    media_missing: int = 0
    records_linked: int = 0
    records_partial: int = 0
    records_missing: int = 0
    warnings: list[str] = field(default_factory=list)
    library_path: str = ""
    job_key_notice: str = ""

    def summary(self) -> str:
        lines = [
            f"Notes scanned: {self.notes_scanned}",
            f"Notes skipped (other note type): {self.notes_skipped_wrong_type}",
            f"Clips found: {self.clips_found}",
        ]
        if self.exclusions_applied:
            lines.append(f"Clips excluded: {self.clips_excluded}")
            if self.exclusion_samples:
                lines.append("Excluded:")
                lines.extend(f"- {sample}" for sample in self.exclusion_samples)
                remaining = self.clips_excluded - len(self.exclusion_samples)
                if remaining > 0:
                    lines.append(f"- … {remaining} more")
        lines.extend(
            [
                f"Media copied: {self.media_copied}",
                f"Media missing: {self.media_missing}",
                f"Job records linked: {self.records_linked}",
                f"Job records partial: {self.records_partial}",
                f"Job records missing: {self.records_missing}",
            ]
        )
        if self.library_path:
            lines.append(f"Library: {self.library_path}")
        if self.job_key_notice:
            lines.append("")
            lines.append(self.job_key_notice)
        if self.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines.extend(f"- {warning}" for warning in self.warnings)
        return "\n".join(lines)


@dataclass
class ImportCandidate:
    note_id: int
    deck_name: str
    sort_field_value: str
    score: float
    sort_field_name: str = ""
    signals: dict[str, float] = field(default_factory=dict)
    field_values: dict[str, str] = field(default_factory=dict)


@dataclass
class ImportDecision:
    clip_id: str
    mode: str
    action: str = "import"
    note_id: int | None = None
    target_field_set_index: int = 1
    conflict_resolution: str = ""
    score: float = 0.0
    signals: dict[str, float] = field(default_factory=dict)
    label: str = ""
    suggestions: list[ImportCandidate] = field(default_factory=list)
    suggestion_total: int = 0


def empty_role_fields() -> dict[str, FieldValue]:
    return {role: FieldValue() for role in FIELD_ROLES}


AUDIO_MEDIA_KEYS = ("filename", "size_bytes", "exists")


def merge_audio_media(
    audio_fields: dict[str, Any] | None,
    audio_media: list[Any] | None,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Nest exported audio file records under the matching note audio field."""
    merged: dict[str, Any] = {}
    for key, value in (audio_fields or {}).items():
        merged[str(key)] = dict(value) if isinstance(value, dict) else value
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in audio_media or []:
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename") or "").strip()
        if not filename:
            continue
        field = str(item.get("field") or "").strip()
        cleaned = {key: item[key] for key in AUDIO_MEDIA_KEYS if key in item}
        cleaned["filename"] = filename
        grouped.setdefault(field, []).append(cleaned)
    for field, items in grouped.items():
        slot = merged.get(field)
        if isinstance(slot, dict) and slot.get("media") and not overwrite:
            continue
        if not isinstance(slot, dict):
            slot = {"name": field, "value": "", "files": [item["filename"] for item in items]}
        else:
            slot = dict(slot)
        slot["media"] = items
        merged[field] = slot
    return merged


def partition_anki_section(anki: dict[str, Any], note_id: Any = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a filtered anki object into note-constant fields and clip-local fields.

    The clip keeps note_id as a foreign key when any note-level field is stored.
    """
    note = {key: anki[key] for key in NOTE_LEVEL_ANKI_KEYS if key in anki}
    local = {key: value for key, value in anki.items() if key not in NOTE_LEVEL_ANKI_KEYS}
    if not note:
        return {}, local
    if note_id in (None, ""):
        restored = dict(note)
        restored.update(local)
        return {}, restored
    local = {"note_id": note_id, **{key: value for key, value in local.items() if key != "note_id"}}
    return note, local


def library_audio_field(payload: dict[str, Any]) -> dict[str, Any]:
    """Audio field stored on a note. Filenames in value are not repeated as files."""
    stored: dict[str, Any] = {"value": str(payload.get("value") or "")}
    media = payload.get("media")
    if isinstance(media, list) and media:
        stored["media"] = [item for item in media if isinstance(item, dict)]
    return stored


def library_clip_dict(
    *,
    clip_id: str,
    filename: str,
    size_bytes: int | None,
    exists: bool,
    discovered: DiscoveredClip,
    job: dict[str, Any] | None,
    link: LinkInfo,
) -> dict[str, Any]:
    return {
        "id": clip_id,
        "media": {
            "filename": filename,
            "size_bytes": size_bytes,
            "exists": exists,
        },
        "anki": {
            "note_id": discovered.note_id,
            "deck_name": discovered.deck_name,
            "model_name": discovered.model_name,
            "sort_field_name": discovered.sort_field_name,
            "sort_field_value": discovered.sort_field_value,
            "field_set_index": discovered.field_set_index,
            "fields": {role: value.to_dict() for role, value in discovered.fields.items()},
            "extra_fields": {key: {"value": value.value} for key, value in discovered.extra_fields.items()},
            "audio_fields": {key: library_audio_field(value) for key, value in discovered.audio_fields.items()},
        },
        "job": job,
        "link": link.to_dict(),
    }
