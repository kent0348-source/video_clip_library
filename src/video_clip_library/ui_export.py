from __future__ import annotations

from typing import Any, Iterable

from aqt.qt import (
    QAbstractItemView,
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    Qt,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QApplication,
    QComboBox,
)
from aqt.operations import QueryOp
from aqt.utils import tooltip

from .anki_scan import find_deck_note_ids, list_deck_names, scan_note_ids, selected_browser_note_ids
from .config import config_ready_for_export, load_config, save_config
from .debuglog import log, timed
from .exclusions import arrow_path
from .identity import SAMPLE_PLACEHOLDER
from .export import export_clips, inspect_export_clips, library_media_filenames, load_library, resolve_library_paths
from .models import LIBRARY_FILENAME
from .ui_common import apply_dialog_screen_constraints, fill_deck_tree, select_deck_in_tree, wrap_row
from .ui_exclusions import ExclusionRulesEditor


class ExportDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        *,
        note_ids: Iterable[int] | None = None,
        deck_name: str = "",
        title: str = "Export clip library",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.config = load_config()
        self._note_ids = [int(note_id) for note_id in (note_ids or [])]
        self._deck_name = deck_name
        apply_dialog_screen_constraints(self, preferred_width=920, preferred_height=860, minimum_width=640, minimum_height=520)

        layout = QVBoxLayout(self)
        ready, message = config_ready_for_export(self.config)
        if not ready:
            layout.addWidget(QLabel(message))
            close = QPushButton("Close")
            close.clicked.connect(self.reject)
            layout.addWidget(close)
            return

        form = QFormLayout()
        self.destination = QLineEdit(str(self.config.get("last_export_dir") or ""))
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse_destination)
        form.addRow("Destination folder", wrap_row(self.destination, browse))

        self.fresh = QRadioButton("Create a new library")
        self.update = QRadioButton("Update an existing library")
        if self.config.get("export_mode") == "update":
            self.update.setChecked(True)
        else:
            self.fresh.setChecked(True)
        mode_group = QButtonGroup(self)
        mode_group.addButton(self.fresh)
        mode_group.addButton(self.update)
        self.fresh.toggled.connect(self._persist_prefs)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.fresh)
        mode_row.addWidget(self.update)
        mode_row.addStretch(1)
        form.addRow("Mode", wrap_row(self.fresh, self.update))
        layout.addLayout(form)
        layout.addWidget(self._build_exclusion_group(), 2)

        self.preview = QLabel(self._preview_text())
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        layout.addWidget(self.report, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.export_button = QPushButton("Export")
        self.export_button.clicked.connect(self._run_export)
        cancel = QPushButton("Close")
        cancel.clicked.connect(self.accept)
        buttons.addWidget(self.export_button)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        self.destination.editingFinished.connect(self._persist_prefs)

    def _preview_text(self) -> str:
        if self._deck_name:
            return f"Deck: {self._deck_name}"
        return f"Selected notes: {len(self._note_ids)}"

    def _build_exclusion_group(self) -> QGroupBox:
        box = QGroupBox("Exclude clips")
        group = QVBoxLayout(box)
        hint = QLabel(
            "A rule excludes a clip when the value at that key path matches. "
            "Paths use the clip object this export writes, for example "
            "clip -> anki -> fields -> miscinfo -> value. "
            "Hover a discovered key to see a value from this export. "
            "Job keys show how many clips contain them. "
            "Use key puts the selected path into the selected rule. "
            "A missing path does not match. Updating a library does not delete clips already stored in it. "
            "Presets are saved in Clip Library settings. Apply preset replaces the rules in this table."
        )
        hint.setWordWrap(True)
        group.addWidget(hint)
        self.exclusion_editor = ExclusionRulesEditor(on_changed=self._persist_prefs)
        self.exclusion_editor.set_state(
            enabled=bool(self.config.get("export_exclusions_enabled")),
            rules=self.config.get("export_exclusions") or [],
        )
        group.addWidget(self.exclusion_editor)

        preset_row = QHBoxLayout()
        self.exclusion_preset = QComboBox()
        self.apply_preset_button = QPushButton("Apply preset")
        self.apply_preset_button.setToolTip("Replace the rules in this table with a preset saved in Clip Library settings.")
        self.apply_preset_button.clicked.connect(self._apply_exclusion_preset)
        self._fill_exclusion_presets()
        preset_row.addWidget(QLabel("Preset"))
        preset_row.addWidget(self.exclusion_preset, 1)
        preset_row.addWidget(self.apply_preset_button)
        preview_rules = QPushButton("Preview exclusions")
        preview_rules.clicked.connect(self._preview_exclusions)
        preset_row.addWidget(preview_rules)
        group.addLayout(preset_row)

        self.exclusion_status = QLabel("Preview lists clips these rules would leave out.")
        self.exclusion_status.setWordWrap(True)
        group.addWidget(self.exclusion_status)

        self.key_tree = QTreeWidget()
        self.key_tree.setHeaderLabel("Keys on this export")
        self.key_tree.itemDoubleClicked.connect(self._insert_key_path)
        self.key_tree.currentItemChanged.connect(lambda *_args: self._show_key_example())
        keys_panel = QWidget()
        keys_layout = QVBoxLayout(keys_panel)
        keys_layout.setContentsMargins(0, 0, 0, 0)
        keys_layout.addWidget(self.key_tree, 1)
        self.use_key_button = QPushButton("Use key")
        self.use_key_button.setToolTip("Put the selected key path into the selected rule. If no rule is selected, add one.")
        self.use_key_button.setEnabled(False)
        self.use_key_button.clicked.connect(self._use_selected_key)
        keys_layout.addWidget(self.use_key_button)
        self.key_example = QLabel("Example: select a key")
        self.key_example.setWordWrap(True)
        self.key_example.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        keys_layout.addWidget(self.key_example)
        self.excluded = QTableWidget(0, 4)
        self.excluded.setHorizontalHeaderLabels(["Filename", "Note", "Path", "Matched text"])
        excluded_header = self.excluded.horizontalHeader()
        excluded_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        excluded_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.excluded.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.excluded.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        splitter = QSplitter()
        splitter.addWidget(keys_panel)
        splitter.addWidget(self.excluded)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        group.addWidget(splitter, 1)
        return box

    def _fill_exclusion_presets(self) -> None:
        self.exclusion_preset.blockSignals(True)
        self.exclusion_preset.clear()
        presets = [item for item in self.config.get("export_exclusion_presets") or [] if isinstance(item, dict)]
        if not presets:
            self.exclusion_preset.addItem("No saved presets", "")
            self.apply_preset_button.setEnabled(False)
        else:
            self.exclusion_preset.addItem("Choose a preset", "")
            for preset in presets:
                self.exclusion_preset.addItem(str(preset.get("name") or ""), str(preset.get("id") or ""))
            self.apply_preset_button.setEnabled(True)
        self.exclusion_preset.blockSignals(False)

    def _apply_exclusion_preset(self) -> None:
        preset_id = str(self.exclusion_preset.currentData() or "")
        preset = next(
            (
                item
                for item in self.config.get("export_exclusion_presets") or []
                if isinstance(item, dict) and str(item.get("id") or "") == preset_id
            ),
            None,
        )
        if preset is None:
            return
        if self.exclusion_editor.rules():
            answer = QMessageBox.question(
                self,
                "Clip Library",
                f"Replace the rules in this table with \"{preset.get('name')}\"?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.exclusion_editor.set_state(enabled=bool(preset.get("enabled", True)), rules=preset.get("rules") or [])
        self._persist_prefs()

    def _use_selected_key(self) -> None:
        item = self.key_tree.currentItem()
        if item is not None:
            self._insert_key_path(item)

    def _show_key_example(self) -> None:
        item = self.key_tree.currentItem()
        if item is None:
            self.use_key_button.setEnabled(False)
            self.key_example.setText("Example: select a key")
            self.key_example.setToolTip("")
            return
        self.use_key_button.setEnabled(bool(item.data(0, Qt.ItemDataRole.UserRole)))
        example = str(item.data(0, Qt.ItemDataRole.UserRole + 1) or "")
        shown = example or SAMPLE_PLACEHOLDER
        self.key_example.setText(f"Example: {shown}")
        self.key_example.setToolTip(shown)

    def _insert_key_path(self, item: QTreeWidgetItem) -> None:
        path = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        if path:
            self.exclusion_editor.insert_path(path)

    def _fill_key_tree(
        self,
        dotted_paths: list[str],
        examples: dict[str, str] | None = None,
        counts: dict[str, int] | None = None,
    ) -> None:
        samples = examples or {}
        key_counts = counts or {}
        self.key_tree.clear()
        nodes: dict[str, QTreeWidgetItem] = {}
        for dotted in dotted_paths:
            parent: QTreeWidgetItem | None = None
            acc: list[str] = []
            for part in dotted.split("."):
                if not part:
                    continue
                acc.append(part)
                key = ".".join(acc)
                node = nodes.get(key)
                if node is None:
                    text = part
                    if key in key_counts:
                        text = f"{part} [{key_counts[key]}]"
                    node = QTreeWidgetItem([text])
                    shown = arrow_path(key)
                    sample = samples.get(key) or ""
                    node.setData(0, Qt.ItemDataRole.UserRole, shown)
                    node.setData(0, Qt.ItemDataRole.UserRole + 1, sample)
                    node.setToolTip(0, sample or SAMPLE_PLACEHOLDER)
                    if parent is None:
                        self.key_tree.addTopLevelItem(node)
                    else:
                        parent.addChild(node)
                    nodes[key] = node
                parent = node
        self.key_tree.expandToDepth(2)
        self._show_key_example()

    def _preview_exclusions(self) -> None:
        self._persist_prefs()
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.progress.setFormat("Scanning notes...")
        self.export_button.setEnabled(False)
        from aqt import mw

        config = dict(self.config)
        deck_name = self._deck_name
        selected_note_ids = list(self._note_ids)
        destination = self.destination.text().strip()
        mode = "update" if self.update.isChecked() else "fresh"

        def operation(col):
            note_ids = find_deck_note_ids(deck_name, col) if deck_name else selected_note_ids
            if not note_ids:
                return None
            with timed("scanning notes for exclusion preview"):
                clips, _scanned, _skipped = scan_note_ids(note_ids, config, collection=col)
            preview = inspect_export_clips(clips, config)
            already_names: set[str] = set()
            if mode == "update":
                _root, library_path = resolve_library_paths(destination, "update")
                already_names = library_media_filenames(load_library(library_path))
            return preview, already_names, len(clips)

        def success(result) -> None:
            self.progress.setVisible(False)
            self.export_button.setEnabled(True)
            self.exclusion_status.setText("No notes were found to scan.")
            self._fill_key_tree([])
            self.excluded.setRowCount(0)
            if result is None:
                return
            preview, already_names, clip_count = result
            self._fill_key_tree(preview.key_paths, preview.examples, preview.job_key_counts)
            self.excluded.setRowCount(0)
            left = 0
            for hit in preview.hits:
                row = self.excluded.rowCount()
                self.excluded.insertRow(row)
                filename = str(hit.get("filename") or "")
                if filename in already_names:
                    left += 1
                matched = " ".join(str(hit.get("matched_text") or "").split())
                short = matched if len(matched) <= 160 else matched[:159] + "…"
                values = [filename, str(hit.get("note_id") or ""), str(hit.get("path") or ""), short]
                for column, text in enumerate(values):
                    cell = QTableWidgetItem(text)
                    if column == 3:
                        cell.setToolTip(matched)
                    self.excluded.setItem(row, column, cell)
            remaining = clip_count - len(preview.hits)
            if not preview.rules_active:
                message = (
                    f"Exclusion rules are off. {clip_count} clip(s) would be exported. "
                    f"{len(preview.key_paths)} key path(s) discovered."
                )
            else:
                message = (
                    f"{len(preview.hits)} excluded, {remaining} will be exported. "
                    f"{len(preview.key_paths)} key path(s) discovered."
                )
                if left:
                    message += f" {left} excluded clip(s) are already in the library and will be left there."
            if preview.job_errors:
                message += " " + " ".join(preview.job_errors)
            self.exclusion_status.setText(message)
        def failure(error: Exception) -> None:
            self.export_button.setEnabled(True)
            self.progress.setVisible(False)
            QMessageBox.critical(self, "Clip Library", f"Exclusion preview failed: {error}")

        QueryOp(parent=mw, op=operation, success=success).failure(failure).with_progress().run_in_background()

    def _browse_destination(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose library folder", self.destination.text())
        if path:
            self.destination.setText(path)
            self._persist_prefs()

    def _persist_prefs(self) -> None:
        self.config["last_export_dir"] = self.destination.text().strip()
        self.config["export_mode"] = "update" if self.update.isChecked() else "fresh"
        if hasattr(self, "exclusion_editor"):
            self.config["export_exclusions_enabled"] = self.exclusion_editor.is_enabled()
            self.config["export_exclusions"] = self.exclusion_editor.rules()
        self.config = save_config(self.config)

    def _collect_note_ids(self, collection=None) -> list[int]:
        if self._deck_name:
            return find_deck_note_ids(self._deck_name, collection)
        return list(self._note_ids)

    def _run_export(self) -> None:
        destination = self.destination.text().strip()
        if not destination:
            QMessageBox.warning(self, "Clip Library", "Choose a destination folder.")
            return
        mode = "update" if self.update.isChecked() else "fresh"
        root, library_path = resolve_library_paths(destination, mode)
        if mode == "fresh" and load_library(library_path):
            answer = QMessageBox.question(
                self,
                "Clip Library",
                f"{LIBRARY_FILENAME} already exists in that folder. Overwrite it as a new library?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        if mode == "update" and not load_library(library_path):
            QMessageBox.warning(self, "Clip Library", "No existing clip_library.json was found to update.")
            return

        self._persist_prefs()
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.progress.setFormat("Scanning notes...")
        self.export_button.setEnabled(False)

        profile_name = ""
        try:
            from aqt import mw

            profile_name = str(getattr(mw.pm, "name", "") or "")
        except Exception:
            pass

        from aqt import mw

        config = dict(self.config)

        def progress(current: int, total: int, filename: str) -> None:
            mw.taskman.run_on_main(
                lambda: self._update_export_progress(current, total, filename)
            )

        def operation(col):
            with timed("exporting collection"):
                note_ids = self._collect_note_ids(col)
                if not note_ids:
                    return None
                with timed("scanning notes for export"):
                    clips, scanned, skipped = scan_note_ids(note_ids, config, collection=col)
                log(f"scanned {scanned} notes, found {len(clips)} clips")
                if not clips:
                    return (None, scanned, skipped)
                report = export_clips(
                    clips,
                    config=config,
                    destination=destination,
                    mode=mode,
                    profile_name=profile_name,
                    progress=progress,
                )
                return report, scanned, skipped

        def success(result) -> None:
            self.progress.setVisible(False)
            self.export_button.setEnabled(True)
            if result is None:
                QMessageBox.information(self, "Clip Library", "No notes were found to export.")
                return
            report, scanned, skipped = result
            if report is None:
                QMessageBox.information(self, "Clip Library", "No clips were found in the configured field sets.")
                return
            report.notes_scanned = scanned
            report.notes_skipped_wrong_type = skipped
            self.report.setPlainText(report.summary())
            tooltip("Clip library export finished.")
            if report.job_key_notice:
                QMessageBox.warning(self, "Clip Library", report.job_key_notice)

        def failure(error: Exception) -> None:
            self.export_button.setEnabled(True)
            self.progress.setVisible(False)
            QMessageBox.critical(self, "Clip Library", f"Clip library export failed: {error}")

        QueryOp(parent=mw, op=operation, success=success).failure(failure).with_progress().run_in_background()

    def _update_export_progress(self, current: int, total: int, filename: str) -> None:
        if not self.isVisible():
            return
        self.progress.setMaximum(total)
        self.progress.setValue(current)
        self.progress.setFormat(f"{current}/{total} {filename}")


def choose_deck(parent: QWidget | None, default: str = "") -> str:
    names = list_deck_names()
    if not names:
        return ""
    dialog = QDialog(parent)
    dialog.setWindowTitle("Export deck")
    layout = QVBoxLayout(dialog)
    hint = QLabel("Subdecks are nested under their parents.")
    hint.setWordWrap(True)
    layout.addWidget(hint)
    tree = QTreeWidget()
    fill_deck_tree(tree, names, checkable=False)
    select_deck_in_tree(tree, default if default in names else names[0])
    tree.itemDoubleClicked.connect(lambda *_args: dialog.accept())
    layout.addWidget(tree, 1)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.resize(420, 480)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return ""
    item = tree.currentItem()
    if item is None:
        return ""
    return str(item.data(0, Qt.ItemDataRole.UserRole) or "")


def open_export_selected(parent: QWidget | None, browser: Any) -> None:
    config = load_config()
    ready, message = config_ready_for_export(config)
    if not ready:
        QMessageBox.information(parent, "Clip Library", message)
        return
    note_ids = selected_browser_note_ids(browser)
    if not note_ids:
        QMessageBox.information(parent, "Clip Library", "Select one or more notes first.")
        return
    dialog = ExportDialog(parent, note_ids=note_ids, title="Clip Library: Export selected notes")
    dialog.exec()


def open_export_deck(parent: QWidget | None, browser: Any = None) -> None:
    config = load_config()
    ready, message = config_ready_for_export(config)
    if not ready:
        QMessageBox.information(parent, "Clip Library", message)
        return
    default = ""
    if browser is not None:
        note_ids = selected_browser_note_ids(browser)
        if note_ids:
            from .anki_scan import scan_note_ids

            clips, _, _ = scan_note_ids(note_ids[:1], config)
            if clips:
                default = clips[0].deck_name
    deck_name = choose_deck(parent, default)
    if not deck_name:
        return
    dialog = ExportDialog(parent, deck_name=deck_name, title=f"Clip Library: Export deck — {deck_name}")
    dialog.exec()
