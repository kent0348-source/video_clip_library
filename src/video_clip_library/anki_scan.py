from __future__ import annotations

import os
from typing import Any, Iterable

from .debuglog import log

from .config import enabled_extra_fields, enabled_field_sets
from .models import DiscoveredClip, ExtraField, FieldSet, FieldValue
from .textutil import extract_audio_filenames, extract_video_filename


def list_note_types() -> list[str]:
    try:
        from aqt import mw
    except Exception:
        return []
    col = getattr(mw, "col", None)
    if col is None:
        return []
    models = col.models
    if hasattr(models, "all_names"):
        return list(models.all_names())
    return [str(model.get("name") or "") for model in models.all() if model.get("name")]


def list_model_fields(note_type: str) -> list[str]:
    try:
        from aqt import mw
    except Exception:
        return []
    col = getattr(mw, "col", None)
    if col is None or not note_type:
        return []
    model = col.models.by_name(note_type)
    if not model:
        return []
    if hasattr(col.models, "field_names"):
        return list(col.models.field_names(model))
    return [str(item.get("name") or "") for item in model.get("flds", []) if item.get("name")]


DECK_SEPARATOR = "::"


def group_deck_names(names: Iterable[str]) -> list[dict[str, Any]]:
    """Nest deck names on '::' so parents sit above their subdecks.

    A path segment that is not itself a deck is kept so the branch still
    shows, with an empty full_name.
    """
    roots: list[dict[str, Any]] = []
    index: dict[tuple[str, ...], dict[str, Any]] = {}
    for raw in names:
        full_name = str(raw or "").strip()
        if not full_name:
            continue
        parts = [part.strip() for part in full_name.split(DECK_SEPARATOR)]
        parts = [part for part in parts if part]
        if not parts:
            continue
        for depth in range(1, len(parts) + 1):
            key = tuple(parts[:depth])
            node = index.get(key)
            if node is None:
                node = {"name": parts[depth - 1], "full_name": "", "children": []}
                index[key] = node
                if depth == 1:
                    roots.append(node)
                else:
                    index[key[:-1]]["children"].append(node)
            if depth == len(parts):
                node["full_name"] = full_name

    def sort_nodes(nodes: list[dict[str, Any]]) -> None:
        nodes.sort(key=lambda item: (item["name"].casefold(), item["full_name"].casefold()))
        for node in nodes:
            sort_nodes(node["children"])

    sort_nodes(roots)
    return roots


def list_deck_names() -> list[str]:
    try:
        from aqt import mw
    except Exception:
        return []
    col = getattr(mw, "col", None)
    if col is None:
        return []
    decks = col.decks
    if hasattr(decks, "all_names_and_ids"):
        return [str(item.name) for item in decks.all_names_and_ids()]
    if hasattr(decks, "all_names"):
        return list(decks.all_names())
    return [str(deck.get("name") or "") for deck in decks.all() if deck.get("name")]


def media_dir(collection: Any | None = None) -> str:
    try:
        if collection is None:
            from aqt import mw

            collection = getattr(mw, "col", None)
        return os.path.abspath(collection.media.dir()) if collection is not None else ""
    except Exception:
        return ""


def _note_type_name(note: Any, collection: Any | None = None) -> str:
    model = note.note_type() if hasattr(note, "note_type") else None
    if isinstance(model, dict):
        return str(model.get("name") or "")
    try:
        if collection is None:
            from aqt import mw

            collection = getattr(mw, "col", None)
        model = collection.models.get(note.mid) if collection is not None else None
        return str((model or {}).get("name") or "")
    except Exception:
        return ""


def _sort_field(note: Any, collection: Any | None = None) -> tuple[str, str]:
    model = note.note_type() if hasattr(note, "note_type") else None
    if not isinstance(model, dict):
        try:
            if collection is None:
                from aqt import mw

                collection = getattr(mw, "col", None)
            model = collection.models.get(note.mid) if collection is not None else None
        except Exception:
            model = None
    if not isinstance(model, dict):
        return "", ""
    fields = model.get("flds") or []
    try:
        sort_index = int(model.get("sortf", 0))
    except (TypeError, ValueError):
        sort_index = 0
    if not fields:
        return "", ""
    sort_index = max(0, min(sort_index, len(fields) - 1))
    name = str(fields[sort_index].get("name") or "")
    value = str(note[name]) if name and name in note else ""
    return name, value


def _note_deck_name(note: Any, collection: Any | None = None) -> str:
    try:
        if collection is None:
            from aqt import mw

            collection = getattr(mw, "col", None)
        col = collection
        if col is None:
            return ""
        if hasattr(col, "card_ids_of_note"):
            card_ids = col.card_ids_of_note(note.id)
        else:
            card_ids = col.find_cards(f"nid:{note.id}")
        if not card_ids:
            return ""
        card = col.get_card(card_ids[0])
        return str(col.decks.name(card.did) or "")
    except Exception:
        return ""


def _field_text(note: Any, field_name: str) -> str:
    if not field_name:
        return ""
    try:
        return str(note[field_name]) if field_name in note else ""
    except Exception:
        return ""


def clips_from_note_values(
    *,
    note_id: int,
    deck_name: str,
    model_name: str,
    sort_field_name: str,
    sort_field_value: str,
    values: dict[str, str],
    field_sets: Iterable[FieldSet],
    media_directory: str,
    extra_fields: Iterable[ExtraField] | None = None,
    audio_fields: Iterable[ExtraField] | None = None,
) -> list[DiscoveredClip]:
    extras = {
        extra.field: FieldValue(extra.field, values.get(extra.field, ""))
        for extra in extra_fields or []
        if extra.is_complete()
    }
    audios: dict[str, dict[str, Any]] = {}
    for extra in audio_fields or []:
        if not extra.is_complete():
            continue
        raw_value = values.get(extra.field, "")
        audios[extra.field] = {
            "name": extra.field,
            "value": raw_value,
            "files": extract_audio_filenames(raw_value),
        }
    clips: list[DiscoveredClip] = []
    for field_set in field_sets:
        if not field_set.is_complete():
            continue
        video_html = values.get(field_set.video, "")
        filename = extract_video_filename(video_html)
        if not filename:
            continue
        fields = {
            "video": FieldValue(field_set.video, video_html),
            "sentence": FieldValue(field_set.sentence, values.get(field_set.sentence, "") if field_set.sentence else ""),
            "secondary": FieldValue(field_set.secondary, values.get(field_set.secondary, "") if field_set.secondary else ""),
            "miscinfo": FieldValue(field_set.miscinfo, values.get(field_set.miscinfo, "") if field_set.miscinfo else ""),
        }
        for role_id, field_name in field_set.extra_map().items():
            if field_name:
                fields[role_id] = FieldValue(field_name, values.get(field_name, ""))
        clips.append(
            DiscoveredClip(
                note_id=int(note_id),
                deck_name=deck_name,
                model_name=model_name,
                sort_field_name=sort_field_name,
                sort_field_value=sort_field_value,
                field_set_index=field_set.index,
                field_set_name=field_set.name,
                filename=filename,
                media_path=os.path.join(media_directory, filename) if media_directory else filename,
                fields=fields,
                extra_fields=dict(extras),
                audio_fields=dict(audios),
            )
        )
    return clips


_NOTE_SQL = """
SELECT n.id, n.mid, n.flds, (
    SELECT c.did FROM cards c WHERE c.nid = n.id ORDER BY c.id LIMIT 1
)
FROM notes n
WHERE {where}
"""


def _chunks(values: list[int], size: int = 400) -> Iterable[list[int]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _db_all(col: Any, sql: str, args: list[Any]) -> list[Any] | None:
    db = getattr(col, "db", None)
    if db is None or not hasattr(db, "all"):
        return None
    try:
        return list(db.all(sql, *args))
    except TypeError:
        try:
            return list(db.all(sql, args))
        except Exception as error:
            log(f"collection query failed ({error})")
            return None
    except Exception as error:
        log(f"collection query failed ({error})")
        return None


def _model_lookup(col: Any) -> dict[int, tuple[str, list[str], int]]:
    lookup: dict[int, tuple[str, list[str], int]] = {}
    models = getattr(col, "models", None)
    if models is None or not hasattr(models, "all"):
        return lookup
    try:
        all_models = list(models.all())
    except Exception:
        return lookup
    for model in all_models:
        if not isinstance(model, dict):
            continue
        try:
            mid = int(model.get("id") or 0)
            sortf = int(model.get("sortf") or 0)
        except (TypeError, ValueError):
            continue
        fields = [str(item.get("name") or "") for item in model.get("flds") or [] if isinstance(item, dict)]
        lookup[mid] = (str(model.get("name") or ""), fields, sortf)
    return lookup


def _deck_id_to_name(col: Any) -> dict[int, str]:
    names: dict[int, str] = {}
    decks = getattr(col, "decks", None)
    if decks is None:
        return names
    if hasattr(decks, "all_names_and_ids"):
        try:
            for item in decks.all_names_and_ids():
                names[int(item.id)] = str(item.name)
            return names
        except Exception:
            names.clear()
    try:
        for deck in decks.all():
            if isinstance(deck, dict) and deck.get("id") is not None:
                names[int(deck["id"])] = str(deck.get("name") or "")
    except Exception:
        return {}
    return names


def _parse_note_row(row: Any, models: dict[int, tuple[str, list[str], int]], decks: dict[int, str]) -> dict[str, Any] | None:
    try:
        note_id = int(row[0])
        mid = int(row[1])
    except (TypeError, ValueError, IndexError):
        return None
    raw_fields = row[2] if len(row) > 2 else ""
    if isinstance(raw_fields, bytes):
        raw_fields = raw_fields.decode("utf-8", "replace")
    parts = str(raw_fields or "").split("\x1f")
    model_name, field_names, sortf = models.get(mid, ("", [], 0))
    fields: dict[str, str] = {}
    for index, name in enumerate(field_names):
        if name:
            fields[name] = parts[index] if index < len(parts) else ""
    sort_name = field_names[sortf] if field_names and 0 <= sortf < len(field_names) else ""
    did = row[3] if len(row) > 3 else None
    deck_name = ""
    if did not in (None, ""):
        try:
            deck_name = decks.get(int(did), "")
        except (TypeError, ValueError):
            deck_name = ""
    return {
        "note_id": note_id,
        "deck_name": deck_name,
        "model_name": model_name,
        "sort_field_name": sort_name,
        "sort_field_value": fields.get(sort_name, ""),
        "fields": fields,
    }


def _query_note_rows(
    col: Any,
    *,
    mid: int | None = None,
    deck_ids: list[int] | None = None,
    note_ids: list[int] | None = None,
) -> list[Any] | None:
    if note_ids is not None:
        rows: list[Any] = []
        if not note_ids:
            return rows
        for chunk in _chunks(note_ids):
            marks = ",".join("?" for _ in chunk)
            found = _db_all(col, _NOTE_SQL.format(where=f"n.id IN ({marks})"), list(chunk))
            if found is None:
                return None
            rows.extend(found)
        return rows
    if mid is None:
        return None
    if deck_ids is None:
        return _db_all(col, _NOTE_SQL.format(where="n.mid = ?"), [mid])
    rows = []
    if not deck_ids:
        return rows
    for chunk in _chunks(deck_ids):
        marks = ",".join("?" for _ in chunk)
        where = (
            "n.mid = ? AND (SELECT c.did FROM cards c WHERE c.nid = n.id ORDER BY c.id LIMIT 1) "
            f"IN ({marks})"
        )
        found = _db_all(col, _NOTE_SQL.format(where=where), [mid, *chunk])
        if found is None:
            return None
        rows.extend(found)
    return rows


def _rows_to_snapshots(
    rows: list[Any],
    models: dict[int, tuple[str, list[str], int]],
    decks: dict[int, str],
    *,
    note_type: str = "",
    deck_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for row in rows:
        item = _parse_note_row(row, models, decks)
        if item is None:
            continue
        if note_type and item["model_name"] != note_type:
            continue
        if deck_names is not None and item["deck_name"] not in deck_names:
            continue
        snapshots.append(item)
    return snapshots


def scan_note_ids(
    note_ids: Iterable[int],
    config: dict[str, Any],
    collection: Any | None = None,
) -> tuple[list[DiscoveredClip], int, int]:
    if collection is None:
        from aqt import mw

        collection = getattr(mw, "col", None)
    note_type = str(config.get("note_type") or "").strip()
    field_sets = enabled_field_sets(config)
    generic_fields = enabled_extra_fields(config, "generic")
    audio_fields = enabled_extra_fields(config, "audio")
    media_directory = media_dir(collection)
    wanted_ids = [int(note_id) for note_id in note_ids]
    snapshots = None
    if collection is not None:
        rows = _query_note_rows(collection, note_ids=wanted_ids)
        if rows is not None:
            snapshots = _rows_to_snapshots(rows, _model_lookup(collection), _deck_id_to_name(collection))
        else:
            log("scanning notes fell back to per-note reads")
    if snapshots is None:
        return _scan_note_ids_slowly(wanted_ids, config, collection)

    by_id = {int(item["note_id"]): item for item in snapshots}
    clips: list[DiscoveredClip] = []
    scanned = 0
    skipped = 0
    for note_id in wanted_ids:
        item = by_id.get(note_id)
        if item is None:
            continue
        scanned += 1
        model_name = str(item.get("model_name") or "")
        if note_type and model_name != note_type:
            skipped += 1
            continue
        values = item.get("fields") if isinstance(item.get("fields"), dict) else {}
        clips.extend(
            clips_from_note_values(
                note_id=note_id,
                deck_name=str(item.get("deck_name") or ""),
                model_name=model_name,
                sort_field_name=str(item.get("sort_field_name") or ""),
                sort_field_value=str(item.get("sort_field_value") or ""),
                values=values,
                field_sets=field_sets,
                media_directory=media_directory,
                extra_fields=generic_fields,
                audio_fields=audio_fields,
            )
        )
    return clips, scanned, skipped


def _scan_note_ids_slowly(
    note_ids: Iterable[int],
    config: dict[str, Any],
    collection: Any | None = None,
) -> tuple[list[DiscoveredClip], int, int]:
    if collection is None:
        from aqt import mw

        collection = getattr(mw, "col", None)
    note_type = str(config.get("note_type") or "").strip()
    field_sets = enabled_field_sets(config)
    generic_fields = enabled_extra_fields(config, "generic")
    audio_fields = enabled_extra_fields(config, "audio")
    media_directory = media_dir(collection)
    clips: list[DiscoveredClip] = []
    scanned = 0
    skipped = 0
    wanted_fields = {name for field_set in field_sets for name in field_set.used_fields()}
    wanted_fields.update(extra.field for extra in generic_fields + audio_fields)
    for note_id in note_ids:
        try:
            if collection is None:
                continue
            note = collection.get_note(int(note_id))
        except Exception:
            continue
        scanned += 1
        model_name = _note_type_name(note, collection)
        if note_type and model_name != note_type:
            skipped += 1
            continue
        sort_name, sort_value = _sort_field(note, collection)
        values = {name: _field_text(note, name) for name in wanted_fields}
        clips.extend(
            clips_from_note_values(
                note_id=int(note.id),
                deck_name=_note_deck_name(note, collection),
                model_name=model_name,
                sort_field_name=sort_name,
                sort_field_value=sort_value,
                values=values,
                field_sets=field_sets,
                media_directory=media_directory,
                extra_fields=generic_fields,
                audio_fields=audio_fields,
            )
        )
    return clips, scanned, skipped


def find_note_ids_for_query(query: str) -> set[int] | None:
    """Run the deck-browser search. None when the collection is not open.

    A bad query is raised so the caller can show Anki's own error.
    """
    try:
        from aqt import mw
    except Exception:
        return None
    col = getattr(mw, "col", None)
    if col is None or not hasattr(col, "find_notes"):
        return None
    return {int(note_id) for note_id in col.find_notes(str(query or ""))}


def find_deck_note_ids(deck_name: str, collection: Any | None = None) -> list[int]:
    if collection is None:
        from aqt import mw

        collection = getattr(mw, "col", None)

    query = f'deck:"{deck_name}"' if deck_name else ""
    if not query or collection is None:
        return []
    return [int(note_id) for note_id in collection.find_notes(query)]


def selected_browser_note_ids(browser: Any) -> list[int]:
    if browser is None:
        return []
    if hasattr(browser, "selected_notes"):
        return [int(note_id) for note_id in browser.selected_notes()]
    if hasattr(browser, "selectedNotes"):
        return [int(note_id) for note_id in browser.selectedNotes()]
    return []


def snapshot_notes_of_type(
    note_type: str,
    deck_names: Iterable[str] | None = None,
    limit: int | None = None,
    collection: Any | None = None,
) -> list[dict[str, Any]]:
    if not note_type:
        return []
    wanted_decks = None if deck_names is None else {str(name) for name in deck_names if str(name or "").strip()}
    if wanted_decks is not None and not wanted_decks:
        return []
    if collection is None:
        from aqt import mw

        collection = getattr(mw, "col", None)
    col = collection
    if col is None:
        return []
    models = _model_lookup(col)
    mid = next((model_id for model_id, (name, _fields, _sortf) in models.items() if name == note_type), None)
    decks = _deck_id_to_name(col)
    deck_ids = None
    if wanted_decks is not None:
        deck_ids = [deck_id for deck_id, name in decks.items() if name in wanted_decks]
        if not deck_ids:
            return []
    if mid is not None:
        rows = _query_note_rows(col, mid=mid, deck_ids=deck_ids)
        if rows is not None:
            snapshots = _rows_to_snapshots(rows, models, decks, note_type=note_type, deck_names=wanted_decks)
            return snapshots[:limit] if limit is not None else snapshots
        log("loading target notes fell back to per-note reads")
    snapshots = _snapshot_notes_slowly(col, note_type, wanted_decks)
    return snapshots[:limit] if limit is not None else snapshots


def _snapshot_notes_slowly(col: Any, note_type: str, deck_names: set[str] | None) -> list[dict[str, Any]]:
    try:
        note_ids = [int(note_id) for note_id in col.find_notes(f'note:"{note_type}"')]
    except Exception:
        return []
    snapshots: list[dict[str, Any]] = []
    field_cache: dict[str, list[str]] = {}
    for note_id in note_ids:
        try:
            note = col.get_note(note_id)
        except Exception:
            continue
        model_name = _note_type_name(note)
        if model_name != note_type:
            continue
        deck_name = _note_deck_name(note)
        if deck_names is not None and deck_name not in deck_names:
            continue
        if model_name not in field_cache:
            field_cache[model_name] = list_model_fields(model_name)
        sort_name, sort_value = _sort_field(note)
        values = {name: _field_text(note, name) for name in field_cache[model_name]}
        snapshots.append(
            {
                "note_id": int(note.id),
                "deck_name": deck_name,
                "model_name": model_name,
                "sort_field_name": sort_name,
                "sort_field_value": sort_value,
                "fields": values,
            }
        )
    return snapshots
