from __future__ import annotations

import json
import os
from typing import Any

from aqt.qt import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    Qt,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .config import load_config, save_config
from .export import load_library
from .identity import clip_identity_choices, clip_identity_samples, ensure_identity_choices, identity_example_line
from .import_map import (
    attach_library_notes,
    clip_anki,
    clip_field_value,
    clip_filename,
    clip_label,
    field_set_display_name,
    library_audio_fields,
    library_media_path,
)
from .ui_identity import edit_identity
from .models import LIBRARY_FILENAME
from .player import ClipPlayer
from .textutil import strip_html
from .ui_common import apply_dialog_screen_constraints
from .ui_import import ImportDialog


class LibraryViewer(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Clip Library Viewer")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.config = load_config()
        self.libraries: list[dict[str, Any]] = []
        self.clips: list[dict[str, Any]] = []
        apply_dialog_screen_constraints(self, preferred_width=1200, preferred_height=800, minimum_width=800)

        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        add_button = QPushButton("Add library")
        add_button.clicked.connect(self._add_library)
        remove_button = QPushButton("Remove library")
        remove_button.clicked.connect(self._remove_library)
        import_button = QPushButton("Import selected")
        import_button.clicked.connect(self._import_selected)
        import_all = QPushButton("Import visible")
        import_all.clicked.connect(self._import_visible)
        identity_button = QPushButton("Clip identity...")
        identity_button.setToolTip("Names shown in this list and in the import mapping preview.")
        identity_button.clicked.connect(self._edit_clip_identity)
        self.autoplay = QCheckBox("Autoplay")
        self.autoplay.setChecked(bool(self.config.get("viewer_autoplay", True)))
        self.autoplay.setToolTip("When enabled, selecting a clip plays its video, or the first linked audio file if there is no video.")
        self.autoplay.toggled.connect(self._on_autoplay_toggled)
        toolbar.addWidget(add_button)
        toolbar.addWidget(remove_button)
        toolbar.addWidget(identity_button)
        toolbar.addWidget(self.autoplay)
        self.identity_preview = QLabel("Example:")
        self.identity_preview.setWordWrap(True)
        toolbar.addStretch(1)
        toolbar.addWidget(import_button)
        toolbar.addWidget(import_all)
        layout.addLayout(toolbar)
        layout.addWidget(self.identity_preview)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search identity, stored fields, filename...")
        self.search.textChanged.connect(self._refresh_list)
        self.library_filter = QComboBox()
        self.library_filter.currentIndexChanged.connect(self._refresh_list)
        self.link_filter = QComboBox()
        self.link_filter.addItem("All link statuses", "")
        self.link_filter.addItem("Linked", "linked")
        self.link_filter.addItem("Partial", "partial")
        self.link_filter.addItem("Anki only", "anki_only")
        self.link_filter.currentIndexChanged.connect(self._refresh_list)
        filters.addWidget(self.search, 2)
        filters.addWidget(self.library_filter, 1)
        filters.addWidget(self.link_filter)
        layout.addLayout(filters)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.currentItemChanged.connect(self._on_select)
        splitter.addWidget(self.list)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.player = ClipPlayer()
        self.player.set_mpv_path(str(self.config.get("mpv_executable") or "mpv"))
        self._audio_paths_by_field: dict[str, list[tuple[str, str]]] = {}
        self.audio_field = QComboBox()
        self.audio_field.setEnabled(False)
        self.audio_field.currentIndexChanged.connect(self._on_audio_field_changed)
        self.audio_files = QComboBox()
        self.audio_files.setEnabled(False)
        self.play_audio_button = QPushButton("Play audio")
        self.play_audio_button.setEnabled(False)
        self.play_audio_button.clicked.connect(self._play_selected_audio)
        audio_row = QHBoxLayout()
        audio_row.addWidget(QLabel("Linked audio"))
        audio_row.addWidget(self.audio_field, 1)
        audio_row.addWidget(self.audio_files, 2)
        audio_row.addWidget(self.play_audio_button)
        self.meta = QPlainTextEdit()
        self.meta.setReadOnly(True)
        self.job_tree = QTreeWidget()
        self.job_tree.setHeaderHidden(True)
        self.job_tree.setHeaderLabels(["Job record"])
        right_layout.addWidget(self.player, 2)
        right_layout.addLayout(audio_row)
        right_layout.addWidget(self.meta, 2)
        right_layout.addWidget(QLabel("Job record"))
        right_layout.addWidget(self.job_tree, 2)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)

        self.status = QLabel("")
        layout.addWidget(self.status)
        self._reload_libraries()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.player.stop()
        super().closeEvent(event)

    def _reload_libraries(self) -> None:
        self.libraries = []
        folders = list(self.config.get("library_folders") or [])
        valid: list[str] = []
        for folder in folders:
            library_path = folder if folder.lower().endswith(LIBRARY_FILENAME) else os.path.join(folder, LIBRARY_FILENAME)
            payload = load_library(library_path)
            if not payload:
                continue
            root = os.path.dirname(library_path)
            valid.append(root)
            self.libraries.append({"root": root, "path": library_path, "data": payload})
        if valid != folders:
            self.config["library_folders"] = valid
            self.config = save_config(self.config)
        self.library_filter.blockSignals(True)
        self.library_filter.clear()
        self.library_filter.addItem("All libraries", "")
        for library in self.libraries:
            self.library_filter.addItem(os.path.basename(library["root"]) or library["root"], library["root"])
        self.library_filter.blockSignals(False)
        self._refresh_identity_preview()
        self._refresh_list()

    def _visible_clips(self) -> list[dict[str, Any]]:
        query = self.search.text().strip().casefold()
        library_root = str(self.library_filter.currentData() or "")
        link_status = str(self.link_filter.currentData() or "")
        clips: list[dict[str, Any]] = []
        for library in self.libraries:
            if library_root and library["root"] != library_root:
                continue
            notes = library["data"].get("notes") if isinstance(library["data"].get("notes"), dict) else {}
            for index, clip in enumerate(library["data"].get("clips") or [], start=1):
                if not isinstance(clip, dict):
                    continue
                clip = attach_library_notes(clip, notes)
                clip["_library_root"] = library["root"]
                clip["_library_field_sets"] = library["data"].get("field_sets") or []
                clip["_clip_number"] = index
                link = clip.get("link") if isinstance(clip.get("link"), dict) else {}
                if link_status and str(link.get("status") or "") != link_status:
                    continue
                if query and query not in self._search_haystack(clip):
                    continue
                clips.append(clip)
        return clips

    def _refresh_list(self) -> None:
        current_id = ""
        current = self.list.currentItem()
        if current is not None:
            current_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        self.clips = self._visible_clips()
        self.list.blockSignals(True)
        self.list.clear()
        restore = None
        for clip in self.clips:
            item = QListWidgetItem(clip_label(clip, self.config.get("clip_identity")))
            item.setData(Qt.ItemDataRole.UserRole, clip.get("id"))
            self.list.addItem(item)
            if str(clip.get("id") or "") == current_id:
                restore = item
        self.list.blockSignals(False)
        self.status.setText(f"{len(self.clips)} clips")
        if restore is not None:
            self.list.setCurrentItem(restore)
        elif self.list.count():
            self.list.setCurrentRow(0)
        else:
            self.player.stop()
            self._set_audio_fields([])
            self.meta.clear()
            self.job_tree.clear()

    def _selected_clips(self) -> list[dict[str, Any]]:
        selected_ids = {str(item.data(Qt.ItemDataRole.UserRole) or "") for item in self.list.selectedItems()}
        if not selected_ids and self.list.currentItem() is not None:
            selected_ids = {str(self.list.currentItem().data(Qt.ItemDataRole.UserRole) or "")}
        return [clip for clip in self.clips if str(clip.get("id") or "") in selected_ids]

    def _on_select(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            self.player.stop()
            self._set_audio_fields([])
            self.meta.clear()
            self.job_tree.clear()
            return
        clip_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        clip = next((item for item in self.clips if str(item.get("id") or "") == clip_id), None)
        if not clip:
            return
        root = str(clip.get("_library_root") or "")
        audio_fields = library_audio_fields(root, clip)
        self._set_audio_fields(audio_fields)
        self._play_clip(clip, audio_fields)
        self.meta.setPlainText("\n".join(_preview_lines(clip)))
        fill_json_tree(self.job_tree, clip.get("job"))

    def _set_audio_fields(self, audio_fields: list[tuple[str, list[tuple[str, str]]]]) -> None:
        self._audio_paths_by_field = dict(audio_fields)
        self.audio_field.blockSignals(True)
        self.audio_field.clear()
        for field_name, _ in audio_fields:
            self.audio_field.addItem(field_name, field_name)
        self.audio_field.blockSignals(False)
        self.audio_field.setEnabled(bool(audio_fields))
        self._set_audio_files(audio_fields[0][1] if audio_fields else [])

    def _on_audio_field_changed(self, _index: int) -> None:
        field_name = str(self.audio_field.currentData() or "")
        self._set_audio_files(self._audio_paths_by_field.get(field_name, []))

    def _set_audio_files(self, audio_paths: list[tuple[str, str]]) -> None:
        self.audio_files.blockSignals(True)
        self.audio_files.clear()
        for filename, path in audio_paths:
            self.audio_files.addItem(filename, path)
        self.audio_files.blockSignals(False)
        has_audio = bool(audio_paths)
        self.audio_files.setEnabled(has_audio)
        self.play_audio_button.setEnabled(has_audio)

    def _play_clip(self, clip: dict[str, Any], audio_fields: list[tuple[str, list[tuple[str, str]]]]) -> None:
        if not self.autoplay.isChecked():
            self.player.stop()
            return
        media_path = library_media_path(str(clip.get("_library_root") or ""), clip)
        if media_path and os.path.isfile(media_path):
            self.player.play(media_path)
            return
        for _field_name, paths in audio_fields:
            if paths:
                self.player.play(paths[0][1])
                return
        self.player.stop()

    def _on_autoplay_toggled(self, checked: bool) -> None:
        self.config["viewer_autoplay"] = bool(checked)
        self.config = save_config(self.config)
        current = self.list.currentItem()
        if current is None:
            if not checked:
                self.player.stop()
            return
        if checked:
            self._on_select(current, None)
        else:
            self.player.stop()

    def _edit_clip_identity(self) -> None:
        clips = self._all_library_clips()
        samples = clip_identity_samples(clips)
        choices = ensure_identity_choices(clip_identity_choices(clips), self.config.get("clip_identity"))
        updated = edit_identity(self, "Clip identity", list(self.config.get("clip_identity") or []), choices, samples)
        if updated is None:
            self._refresh_identity_preview()
            return
        self.config["clip_identity"] = updated
        self.config = save_config(self.config)
        self._refresh_identity_preview()
        self._refresh_list()

    def _refresh_identity_preview(self) -> None:
        clips = self._all_library_clips()
        self.identity_preview.setText(
            identity_example_line(list(self.config.get("clip_identity") or []), clip_identity_samples(clips))
        )

    def _all_library_clips(self) -> list[dict[str, Any]]:
        clips: list[dict[str, Any]] = []
        for library in self.libraries:
            notes = library["data"].get("notes") if isinstance(library["data"].get("notes"), dict) else {}
            for index, clip in enumerate(library["data"].get("clips") or [], start=1):
                if not isinstance(clip, dict):
                    continue
                item = attach_library_notes(clip, notes)
                item["_library_root"] = library["root"]
                item["_library_field_sets"] = library["data"].get("field_sets") or []
                item["_clip_number"] = index
                clips.append(item)
        return clips

    def _search_haystack(self, clip: dict[str, Any]) -> str:
        anki = clip_anki(clip)
        parts = [
            clip_label(clip, self.config.get("clip_identity")),
            clip_filename(clip),
            str(clip.get("_clip_number") or ""),
            str(clip.get("id") or ""),
            field_set_display_name(clip),
        ]
        for key in ("deck_name", "model_name", "sort_field_name", "sort_field_value", "note_id"):
            if key in anki:
                parts.append(str(anki.get(key) or ""))
        fields = anki.get("fields") if isinstance(anki.get("fields"), dict) else {}
        for item in fields.values():
            value = item.get("value") if isinstance(item, dict) else item
            parts.append(strip_html(str(value or "")))
        extras = anki.get("extra_fields") if isinstance(anki.get("extra_fields"), dict) else {}
        for item in extras.values():
            value = item.get("value") if isinstance(item, dict) else item
            parts.append(strip_html(str(value or "")))
        if clip.get("job"):
            parts.append(json.dumps(clip.get("job") or {}, ensure_ascii=False))
        return " ".join(parts).casefold()

    def _play_selected_audio(self) -> None:
        path = str(self.audio_files.currentData() or "")
        if not path:
            return
        if self.player.play(path) == "missing":
            QMessageBox.warning(self, "Clip Library", f"Linked audio file was not found:\n{path}")

    def _add_library(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose exported library folder")
        if not path:
            return
        library_path = os.path.join(path, LIBRARY_FILENAME)
        if not load_library(library_path):
            QMessageBox.warning(self, "Clip Library", f"No {LIBRARY_FILENAME} was found in that folder.")
            return
        folders = list(self.config.get("library_folders") or [])
        if path not in folders:
            folders.append(path)
        self.config["library_folders"] = folders
        self.config = save_config(self.config)
        self._reload_libraries()

    def _remove_library(self) -> None:
        root = str(self.library_filter.currentData() or "")
        if not root:
            QMessageBox.information(self, "Clip Library", "Select a specific library to remove.")
            return
        folders = [folder for folder in self.config.get("library_folders") or [] if folder != root]
        self.config["library_folders"] = folders
        self.config = save_config(self.config)
        self._reload_libraries()

    def _import_selected(self) -> None:
        clips = self._selected_clips()
        if not clips:
            QMessageBox.information(self, "Clip Library", "Select one or more clips first.")
            return
        self._open_import(clips)

    def _import_visible(self) -> None:
        if not self.clips:
            QMessageBox.information(self, "Clip Library", "No clips are visible.")
            return
        self._open_import(self.clips)

    def _open_import(self, clips: list[dict[str, Any]]) -> None:
        roots = {str(clip.get("_library_root") or "") for clip in clips}
        if len(roots) != 1:
            QMessageBox.information(self, "Clip Library", "Import from one library at a time.")
            return
        dialog = ImportDialog(self, clips=clips, library_root=next(iter(roots)))
        dialog.exec()


def _preview_lines(clip: dict[str, Any]) -> list[str]:
    anki = clip_anki(clip)
    lines: list[str] = []
    if clip.get("id"):
        lines.append(f"ID: {clip.get('id')}")
    if clip.get("_clip_number") not in (None, ""):
        lines.append(f"Clip number: {clip.get('_clip_number')}")
    filename = clip_filename(clip)
    if filename:
        lines.append(f"File: {filename}")
    for key, label in (
        ("note_id", "Note"),
        ("sort_field_name", "Sort field"),
        ("sort_field_value", "Sort value"),
        ("deck_name", "Deck"),
        ("model_name", "Model"),
        ("field_set_index", "Field set index"),
    ):
        if key in anki and anki.get(key) not in (None, ""):
            lines.append(f"{label}: {anki.get(key)}")
    field_set_name = field_set_display_name(clip)
    if field_set_name:
        lines.append(f"Field set: {field_set_name}")
    link = clip.get("link") if isinstance(clip.get("link"), dict) else {}
    if link:
        lines.append(
            f"Link: {link.get('status')}  matched_by={link.get('matched_by')}  mismatches={link.get('mismatches')}"
        )
    fields = anki.get("fields") if isinstance(anki.get("fields"), dict) else {}
    for role, title in (("sentence", "Sentence"), ("secondary", "Secondary"), ("miscinfo", "Miscinfo"), ("video", "Video")):
        if role not in fields:
            continue
        value = strip_html(clip_field_value(clip, role))
        if not value.strip():
            continue
        lines.extend(["", f"{title}:", value])
    for role, item in fields.items():
        if role in {"sentence", "secondary", "miscinfo", "video"} or not isinstance(item, dict):
            continue
        value = strip_html(str(item.get("value") or ""))
        if value.strip():
            lines.extend(["", f"{item.get('name') or role}:", value])
    extras = anki.get("extra_fields") if isinstance(anki.get("extra_fields"), dict) else {}
    for key, item in extras.items():
        value = item.get("value") if isinstance(item, dict) else item
        text = strip_html(str(value or ""))
        if text.strip():
            name = item.get("name") if isinstance(item, dict) and item.get("name") else key
            lines.append(f"Extra {name}: {text}")
    audios = anki.get("audio_fields") if isinstance(anki.get("audio_fields"), dict) else {}
    for key, item in audios.items():
        if not isinstance(item, dict):
            continue
        files = item.get("files") if isinstance(item.get("files"), list) else []
        value = ", ".join(str(name) for name in files) or strip_html(str(item.get("value") or ""))
        if value.strip():
            lines.append(f"Audio {item.get('name') or key}: {value}")
    return lines


def fill_json_tree(tree: QTreeWidget, payload: Any) -> None:
    tree.clear()
    if payload in (None, "", [], {}):
        return
    if isinstance(payload, dict):
        for key, value in payload.items():
            _add_json_item(tree, None, str(key), value)
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            _add_json_item(tree, None, str(index), value)
    else:
        tree.addTopLevelItem(QTreeWidgetItem([_json_leaf(payload)]))
    tree.collapseAll()


def _add_json_item(tree: QTreeWidget, parent: QTreeWidgetItem | None, key: str, value: Any) -> None:
    item = QTreeWidgetItem([_json_node_label(key, value)])
    if parent is None:
        tree.addTopLevelItem(item)
    else:
        parent.addChild(item)
    item.setExpanded(False)
    if isinstance(value, dict):
        for child_key, child in value.items():
            _add_json_item(tree, item, str(child_key), child)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _add_json_item(tree, item, str(index), child)


def _json_node_label(key: str, value: Any) -> str:
    if isinstance(value, dict):
        return f"{key}: {{}}" if not value else key
    if isinstance(value, list):
        return f"{key}: []" if not value else key
    return f"{key}: {_json_leaf(value)}"


def _json_leaf(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return json.dumps(value, ensure_ascii=False)
