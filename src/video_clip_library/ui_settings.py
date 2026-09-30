from __future__ import annotations

import copy
import uuid
from typing import Any

from aqt.qt import (
    QAbstractItemView,
    QBrush,
    QCheckBox,
    QColor,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QInputDialog,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    Qt,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .anki_scan import find_deck_note_ids, list_model_fields, list_note_types, scan_note_ids
from .debuglog import timed
from .config import (
    active_job_tree,
    blank_field_set,
    load_config,
    normalize_config,
    save_config,
)
from .export_keys import (
    REDUNDANCY_COLOR,
    anki_export_key_labels,
    anki_export_key_tree,
    effective_anki_keys,
    export_key_examples,
    reverse_job_redundancy,
    selected_job_redundancy_pairs,
)
from .job_index import (
    NO_JOB_EXAMPLE,
    JobIndex,
    apply_key_examples,
    apply_record_samples,
    collect_key_examples,
    collect_key_paths,
    empty_job_tree,
    record_sample_id,
    tree_key_selection,
)
from .identity import SAMPLE_PLACEHOLDER
from .models import FIELD_ROLES, REQUIRED_ROLES
from .ui_common import apply_combo_example_tooltips, apply_dialog_screen_constraints, fill_field_combo, icon_button, wrap_row
from .ui_exclusions import ExclusionRulesEditor
from .ui_export import choose_deck
from .ui_import_setup import ImportNoteTypeEditor


SETTINGS_OWNED_KEYS = (
    "note_type",
    "field_sets",
    "generic_fields",
    "audio_fields",
    "clip_extra_roles",
    "job_records_enabled",
    "job_index_path",
    "job_record_keys",
    "job_record_trees",
    "active_job_record_tree",
    "anki_export_keys",
    "export_exclusions_enabled",
    "export_exclusions",
    "export_exclusion_presets",
    "path_privacy_mode",
    "path_privacy_parents",
    "mpv_executable",
    "import_note_type",
    "import_field_sets",
    "import_note_identity",
    "import_note_profiles",
    "import_mapping_initialized",
)


EMPTY_FIELD = "(none)"
DONT_IMPORT = "Don't import"
ROLE_LABELS = {
    "video": "Video (required)",
    "sentence": "Sentence (optional)",
    "secondary": "Secondary (optional)",
    "miscinfo": "Miscinfo (optional)",
}


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None, config: dict[str, Any]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Clip Library Settings")
        self.config = copy.deepcopy(config) if config else load_config()
        self._loading = True
        apply_dialog_screen_constraints(self, preferred_width=1020, preferred_height=800)

        layout = QVBoxLayout(self)
        tabs = QTabWidget(self)
        layout.addWidget(tabs)
        tabs.addTab(self._build_fields_tab(), "Fields")
        tabs.addTab(self._build_export_keys_tab(), "Export keys")
        tabs.addTab(self._build_exclusions_tab(), "Exclusions")
        self.import_editor = ImportNoteTypeEditor(
            self.config,
            on_changed=self._on_import_profile_changed,
            sample_provider=self._import_note_samples,
        )
        tabs.addTab(self.import_editor.settings_page(), "Import note type")
        tabs.addTab(self._build_general_settings_tab(), "General settings")

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        save_button = QPushButton("Save")
        save_button.clicked.connect(self._save_and_close)
        close_row.addWidget(close_button)
        close_row.addWidget(save_button)
        layout.addLayout(close_row)

        self._reload_note_types()
        self._rebuild_field_editors()
        self._populate_anki_tree()
        self._populate_record_tree()
        self._loading = False
        self._sync_from_widgets()
        self._opened_owned = self._owned_view(self.config)
        self._decided = False

    def _build_fields_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form = QFormLayout()
        self.note_type = QComboBox()
        self.note_type.currentTextChanged.connect(self._on_note_type_changed)
        self.note_type.setToolTip("Used when exporting clips from this profile. Import uses the note type on the Import note type tab.")
        form.addRow("Export note type", self.note_type)
        layout.addLayout(form)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self.fields_layout = QVBoxLayout(content)

        self.fields_layout.addWidget(QLabel("Video clip fields"))
        self.field_sets_box = QVBoxLayout()
        self.fields_layout.addLayout(self.field_sets_box)
        add_set = icon_button("plus", "Add another field set")
        add_set.clicked.connect(self._add_field_set)
        self.fields_layout.addWidget(add_set, alignment=Qt.AlignmentFlag.AlignLeft)

        self.fields_layout.addWidget(QLabel("Generic fields"))
        self.generic_box = QVBoxLayout()
        self.fields_layout.addLayout(self.generic_box)
        add_generic = icon_button("plus", "Add a generic field")
        add_generic.clicked.connect(lambda: self._add_extra_field("generic"))
        self.fields_layout.addWidget(add_generic, alignment=Qt.AlignmentFlag.AlignLeft)

        self.fields_layout.addWidget(QLabel("Audio fields"))
        self.audio_box = QVBoxLayout()
        self.fields_layout.addLayout(self.audio_box)
        add_audio = icon_button("plus", "Add an audio field")
        add_audio.clicked.connect(lambda: self._add_extra_field("audio"))
        self.fields_layout.addWidget(add_audio, alignment=Qt.AlignmentFlag.AlignLeft)

        self.fields_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.field_set_widgets: list[dict[str, Any]] = []
        self.generic_widgets: list[dict[str, Any]] = []
        self.audio_widgets: list[dict[str, Any]] = []
        return tab

    def _build_export_keys_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.job_index_path = QLineEdit(str(self.config.get("job_index_path") or ""))
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse_index)
        self.job_index_path.editingFinished.connect(self._on_index_path_changed)
        layout.addWidget(QLabel("index.json location"))
        layout.addWidget(wrap_row(self.job_index_path, browse))

        privacy_row = QHBoxLayout()
        self.privacy_mode = QComboBox()
        self.privacy_mode.addItem("Off", "off")
        self.privacy_mode.addItem("Filename only", "filename_only")
        self.privacy_mode.addItem("Keep parent folders", "keep_parents")
        current_mode = str(self.config.get("path_privacy_mode") or "off")
        index = max(0, self.privacy_mode.findData(current_mode))
        self.privacy_mode.setCurrentIndex(index)
        self.privacy_mode.currentIndexChanged.connect(self._save_jobs)
        self.privacy_parents = QSpinBox()
        self.privacy_parents.setRange(0, 6)
        self.privacy_parents.setValue(int(self.config.get("path_privacy_parents") or 1))
        self.privacy_parents.valueChanged.connect(self._save_jobs)
        privacy_row.addWidget(QLabel("Path privacy"))
        privacy_row.addWidget(self.privacy_mode)
        privacy_row.addWidget(QLabel("Parents to keep"))
        privacy_row.addWidget(self.privacy_parents)
        privacy_row.addStretch(1)
        layout.addLayout(privacy_row)

        trees = QHBoxLayout()
        anki_col = QVBoxLayout()
        anki_col.addWidget(QLabel("clip library-native keys"))
        self.anki_tree = QTreeWidget()
        self.anki_tree.setHeaderHidden(True)
        self.anki_tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.anki_tree.itemChanged.connect(self._on_anki_item_changed)
        anki_col.addWidget(self.anki_tree, 1)
        job_col = QVBoxLayout()
        job_col.addWidget(QLabel("Job-record keys"))
        tree_row = QHBoxLayout()
        self.job_tree_combo = QComboBox()
        self.job_tree_combo.currentIndexChanged.connect(self._on_job_tree_changed)
        build_tree = QPushButton("Build from deck...")
        build_tree.clicked.connect(self._build_job_tree)
        self.rebuild_tree_button = QPushButton("Rebuild")
        self.rebuild_tree_button.clicked.connect(self._rebuild_active_job_tree)
        self.delete_tree_button = QPushButton("Delete")
        self.delete_tree_button.clicked.connect(self._delete_active_job_tree)
        tree_row.addWidget(self.job_tree_combo, 1)
        tree_row.addWidget(build_tree)
        tree_row.addWidget(self.rebuild_tree_button)
        tree_row.addWidget(self.delete_tree_button)
        job_col.addLayout(tree_row)
        self.record_tree = QTreeWidget()
        self.record_tree.setHeaderHidden(True)
        self.record_tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.record_tree.itemChanged.connect(self._on_record_item_changed)
        self.record_tree_empty = QLabel(
            "Build a job-record key tree from a deck before keys can be selected. "
            "The tree is taken from job records linked to video clips in that deck, not from a fixed schema."
        )
        self.record_tree_empty.setWordWrap(True)
        job_col.addWidget(self.record_tree_empty)
        job_col.addWidget(self.record_tree, 1)
        trees.addLayout(anki_col, 1)
        trees.addLayout(job_col, 1)
        layout.addLayout(trees, 1)

        pair_box = QGroupBox("Redundant pairs")
        pair_layout = QVBoxLayout(pair_box)
        self.redundancy_list = QListWidget()
        pair_layout.addWidget(self.redundancy_list)
        pair_row = QHBoxLayout()
        self.clip_key_combo = QComboBox()
        self.job_key_combo = QComboBox()
        add_pair = QPushButton("Add pair")
        add_pair.clicked.connect(self._add_redundancy_pair)
        remove_pair = QPushButton("Remove pair")
        remove_pair.clicked.connect(self._remove_redundancy_pair)
        pair_row.addWidget(self.clip_key_combo, 1)
        pair_row.addWidget(self.job_key_combo, 1)
        pair_row.addWidget(add_pair)
        pair_row.addWidget(remove_pair)
        pair_layout.addLayout(pair_row)
        layout.addWidget(pair_box)
        self.redundancy_hint = QLabel(
            "Highlighted keys are pairs you defined between clip-library keys and keys in the active job-record tree. "
            "A pair is highlighted when job records are enabled and both keys are selected."
        )
        self.redundancy_hint.setWordWrap(True)
        layout.addWidget(self.redundancy_hint)
        return tab

    def _build_exclusions_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        hint = QLabel(
            "These are the same exclusion rules the export window uses. "
            "A rule excludes a clip when the value at that key path matches. "
            "Type the path yourself. This tab cannot list keys or example values from a deck. "
            "Arrow paths and dotted paths both work. Examples: "
            "clip -> anki -> fields -> miscinfo -> value, media -> filename, "
            "job -> source -> media title. "
            "A missing path does not match, and an empty value never matches. "
            "Uncheck On to keep a rule without using it. Apply exclusion rules turns the whole set off. "
            "Export uses the rules in this table after you save, not every saved preset."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.exclusion_editor = ExclusionRulesEditor(on_changed=self._on_exclusions_changed)
        self.exclusion_editor.set_state(
            enabled=bool(self.config.get("export_exclusions_enabled")),
            rules=self.config.get("export_exclusions") or [],
        )
        layout.addWidget(self.exclusion_editor, 1)

        presets = QGroupBox("Rule presets")
        preset_layout = QVBoxLayout(presets)
        preset_hint = QLabel(
            "A preset stores a named copy of the table above, including the apply checkbox. "
            "Load replaces the table. Update writes the table back over the selected preset. "
            "Delete removes the preset only."
        )
        preset_hint.setWordWrap(True)
        preset_layout.addWidget(preset_hint)
        row = QHBoxLayout()
        self.exclusion_preset_combo = QComboBox()
        self.exclusion_preset_combo.currentIndexChanged.connect(lambda *_args: self._update_preset_buttons())
        self.load_preset_button = QPushButton("Load")
        self.load_preset_button.clicked.connect(self._load_exclusion_preset)
        self.save_preset_button = QPushButton("Save as...")
        self.save_preset_button.clicked.connect(self._save_exclusion_preset)
        self.update_preset_button = QPushButton("Update")
        self.update_preset_button.clicked.connect(self._update_exclusion_preset)
        self.delete_preset_button = QPushButton("Delete")
        self.delete_preset_button.clicked.connect(self._delete_exclusion_preset)
        row.addWidget(self.exclusion_preset_combo, 1)
        row.addWidget(self.load_preset_button)
        row.addWidget(self.save_preset_button)
        row.addWidget(self.update_preset_button)
        row.addWidget(self.delete_preset_button)
        preset_layout.addLayout(row)
        layout.addWidget(presets)
        self._refresh_exclusion_presets()
        return tab

    def _build_general_settings_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form = QFormLayout()
        self.job_enabled = QCheckBox("Export matching clip-encode / mpvacious job records")
        self.job_enabled.setChecked(bool(self.config.get("job_records_enabled")))
        self.job_enabled.toggled.connect(self._save_jobs)
        form.addRow("Job records", self.job_enabled)

        self.mpv_path = QLineEdit(str(self.config.get("mpv_executable") or "mpv"))
        browse_mpv = QPushButton("Browse")
        browse_mpv.clicked.connect(self._browse_mpv)
        self.mpv_path.editingFinished.connect(self._save_import)
        form.addRow("mpv / libmpv path", wrap_row(self.mpv_path, browse_mpv))

        layout.addLayout(form)
        hint = QLabel(
            "The note type on the Fields tab is the export note type for this profile. "
            "It is not used when viewing or importing a collection. "
            "Import note type, field sets, and note identity can also be changed on the Import note type tab. "
            "Match rules stay in the import window."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch(1)
        return tab

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _rebuild_field_editors(self) -> None:
        was_loading = self._loading
        self._loading = True
        self._clear_layout(self.field_sets_box)
        self._clear_layout(self.generic_box)
        self._clear_layout(self.audio_box)
        self.field_set_widgets = []
        self.generic_widgets = []
        self.audio_widgets = []
        for index, field_set in enumerate(self.config.get("field_sets") or []):
            self._make_field_set_row(index, field_set)
        for extra in self.config.get("generic_fields") or []:
            self._make_extra_row("generic", extra)
        for extra in self.config.get("audio_fields") or []:
            self._make_extra_row("audio", extra)
        self._loading = was_loading
        self._refresh_field_dropdowns()

    def _make_field_set_row(self, index: int, field_set: dict[str, Any]) -> None:
        box = QGroupBox()
        grid = QGridLayout(box)
        enabled = QCheckBox(str(field_set.get("name") or f"Field Set {index + 1}"))
        enabled.setChecked(True if index == 0 else bool(field_set.get("enabled")))
        enabled.setEnabled(index > 0)
        enabled.toggled.connect(self._on_fields_changed)
        name = QLineEdit(str(field_set.get("name") or f"Field Set {index + 1}"))
        name.editingFinished.connect(self._on_fields_changed)
        name.textChanged.connect(lambda text, box=enabled: box.setText(text or "Field set"))
        grid.addWidget(enabled, 0, 0)
        grid.addWidget(name, 0, 1)
        if index > 0:
            remove = icon_button("minus", "Remove this field set")
            remove.clicked.connect(lambda _=False, i=index: self._remove_field_set(i))
            grid.addWidget(remove, 0, 2)
        combos: dict[str, QComboBox] = {}
        for row, role in enumerate(FIELD_ROLES, start=1):
            combo = QComboBox()
            combo.currentIndexChanged.connect(self._on_fields_changed)
            combos[role] = combo
            grid.addWidget(QLabel(ROLE_LABELS[role]), row, 0)
            grid.addWidget(combo, row, 1, 1, 2)
        extra_combos: dict[str, QComboBox] = {}
        extra_names: dict[str, QLineEdit] = {}
        extras = field_set.get("extras") if isinstance(field_set.get("extras"), dict) else {}
        next_row = len(FIELD_ROLES) + 1
        for offset, role in enumerate(self.config.get("clip_extra_roles") or []):
            if not isinstance(role, dict):
                continue
            role_id = str(role.get("id") or "")
            if not role_id:
                continue
            label = QLineEdit(str(role.get("name") or role_id))
            label.editingFinished.connect(lambda role_id=role_id, editor=label: self._on_clip_role_renamed(role_id, editor.text()))
            combo = QComboBox()
            combo.currentIndexChanged.connect(self._on_fields_changed)
            remove_role = icon_button("minus", "Remove this field from every field set")
            remove_role.clicked.connect(lambda _=False, role_id=role_id: self._remove_clip_role(role_id))
            row = next_row + offset
            grid.addWidget(label, row, 0)
            grid.addWidget(combo, row, 1)
            grid.addWidget(remove_role, row, 2)
            extra_names[role_id] = label
            extra_combos[role_id] = combo
            combo.setProperty("clipRoleValue", str(extras.get(role_id) or ""))
        add_role = icon_button("plus", "Add a field to every field set")
        add_role.clicked.connect(self._add_clip_role)
        grid.addWidget(add_role, next_row + len(extra_combos), 0)
        self.field_sets_box.addWidget(box)
        self.field_set_widgets.append(
            {"enabled": enabled, "name": name, "combos": combos, "extra_combos": extra_combos, "extra_names": extra_names}
        )

    def _make_extra_row(self, kind: str, extra: dict[str, Any]) -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        name = QLineEdit(str(extra.get("name") or ""))
        name.setPlaceholderText("Label")
        name.editingFinished.connect(self._on_fields_changed)
        combo = QComboBox()
        combo.currentIndexChanged.connect(self._on_fields_changed)
        enabled = QCheckBox()
        enabled.setChecked(bool(extra.get("enabled")))
        enabled.toggled.connect(self._on_fields_changed)
        remove = icon_button("minus", "Remove this field")
        widgets = self.generic_widgets if kind == "generic" else self.audio_widgets
        container = self.generic_box if kind == "generic" else self.audio_box
        remove.clicked.connect(lambda _=False, k=kind, item=row: self._remove_extra_row(k, item))
        layout.addWidget(name, 2)
        layout.addWidget(combo, 3)
        layout.addWidget(enabled)
        layout.addWidget(remove)
        container.addWidget(row)
        widgets.append({"name": name, "combo": combo, "enabled": enabled, "widget": row})

    def _add_field_set(self) -> None:
        self._collect_fields_into_config()
        index = len(self.config.get("field_sets") or [])
        role_ids = [str(role.get("id") or "") for role in self.config.get("clip_extra_roles") or [] if isinstance(role, dict)]
        self.config.setdefault("field_sets", []).append(blank_field_set(index + 1, role_ids, enabled=False))
        self._persist()
        self._rebuild_field_editors()
        self._populate_anki_tree()

    def _remove_field_set(self, index: int) -> None:
        if index <= 0:
            return
        self._collect_fields_into_config()
        sets = list(self.config.get("field_sets") or [])
        if index < len(sets):
            del sets[index]
        role_ids = [str(role.get("id") or "") for role in self.config.get("clip_extra_roles") or [] if isinstance(role, dict)]
        self.config["field_sets"] = sets or [blank_field_set(1, role_ids)]
        self._persist()
        self._rebuild_field_editors()
        self._populate_anki_tree()

    def _add_extra_field(self, kind: str) -> None:
        self._collect_fields_into_config()
        key = "generic_fields" if kind == "generic" else "audio_fields"
        label = "Field" if kind == "generic" else "Audio field"
        items = list(self.config.get(key) or [])
        items.append({"enabled": False, "name": f"{label} {len(items) + 1}", "field": "", "kind": kind})
        self.config[key] = items
        self._persist()
        self._rebuild_field_editors()
        self._populate_anki_tree()

    def _remove_extra_row(self, kind: str, widget: QWidget) -> None:
        self._collect_fields_into_config()
        key = "generic_fields" if kind == "generic" else "audio_fields"
        widgets = self.generic_widgets if kind == "generic" else self.audio_widgets
        index = next((i for i, item in enumerate(widgets) if item["widget"] is widget), -1)
        items = list(self.config.get(key) or [])
        if 0 <= index < len(items):
            del items[index]
        self.config[key] = items
        self._persist()
        self._rebuild_field_editors()
        self._populate_anki_tree()

    def _role_ids(self) -> list[str]:
        return [str(role.get("id") or "") for role in self.config.get("clip_extra_roles") or [] if isinstance(role, dict) and role.get("id")]

    def _add_clip_role(self) -> None:
        self._collect_fields_into_config()
        roles = [role for role in self.config.get("clip_extra_roles") or [] if isinstance(role, dict)]
        existing = {str(role.get("id") or "") for role in roles}
        number = 5
        while f"field_{number}" in existing:
            number += 1
        role_id = f"field_{number}"
        roles.append({"id": role_id, "name": f"Field {number}"})
        self.config["clip_extra_roles"] = roles
        for field_set in self.config.get("field_sets") or []:
            extras = dict(field_set.get("extras") or {})
            extras.setdefault(role_id, "")
            field_set["extras"] = extras
        self._persist()
        self._rebuild_field_editors()
        self._populate_anki_tree()

    def _remove_clip_role(self, role_id: str) -> None:
        self._collect_fields_into_config()
        self.config["clip_extra_roles"] = [
            role for role in self.config.get("clip_extra_roles") or [] if str(role.get("id") or "") != role_id
        ]
        for field_set in self.config.get("field_sets") or []:
            extras = dict(field_set.get("extras") or {})
            extras.pop(role_id, None)
            field_set["extras"] = extras
        self._persist()
        self._rebuild_field_editors()
        self._populate_anki_tree()

    def _on_clip_role_renamed(self, role_id: str, text: str) -> None:
        if self._loading:
            return
        cleaned = text.strip()
        for widgets in self.field_set_widgets:
            editor = widgets.get("extra_names", {}).get(role_id)
            if editor is not None and editor.text() != cleaned:
                editor.blockSignals(True)
                editor.setText(cleaned)
                editor.blockSignals(False)
        self._on_fields_changed()

    def _collect_fields_into_config(self) -> None:
        field_sets = []
        for index, widgets in enumerate(self.field_set_widgets):
            entry = {
                "enabled": True if index == 0 else widgets["enabled"].isChecked(),
                "name": widgets["name"].text().strip() or f"Field Set {index + 1}",
            }
            for role, combo in widgets["combos"].items():
                entry[role] = str(combo.currentData() or "")
            entry["extras"] = {
                role_id: str(combo.currentData() or "")
                for role_id, combo in widgets.get("extra_combos", {}).items()
            }
            field_sets.append(entry)
        role_ids = [str(role.get("id") or "") for role in self.config.get("clip_extra_roles") or [] if isinstance(role, dict)]
        self.config["field_sets"] = field_sets or [blank_field_set(1, role_ids)]
        if self.field_set_widgets:
            roles = []
            for role in self.config.get("clip_extra_roles") or []:
                if not isinstance(role, dict):
                    continue
                role_id = str(role.get("id") or "")
                editor = self.field_set_widgets[0].get("extra_names", {}).get(role_id)
                name = editor.text().strip() if editor is not None else str(role.get("name") or "")
                roles.append({"id": role_id, "name": name or str(role.get("name") or role_id)})
            self.config["clip_extra_roles"] = roles
        for kind, widgets in (("generic", self.generic_widgets), ("audio", self.audio_widgets)):
            extras = []
            prefix = "Field" if kind == "generic" else "Audio field"
            for index, item in enumerate(widgets, start=1):
                extras.append(
                    {
                        "enabled": item["enabled"].isChecked(),
                        "name": item["name"].text().strip() or f"{prefix} {index}",
                        "field": str(item["combo"].currentData() or ""),
                        "kind": kind,
                    }
                )
            self.config["generic_fields" if kind == "generic" else "audio_fields"] = extras

    def _reload_note_types(self) -> None:
        current = str(self.config.get("note_type") or "")
        names = list_note_types()
        self.note_type.blockSignals(True)
        self.note_type.clear()
        self.note_type.addItem("")
        for name in names:
            self.note_type.addItem(name)
        if current:
            index = self.note_type.findText(current)
            if index < 0:
                self.note_type.addItem(current)
                index = self.note_type.findText(current)
            self.note_type.setCurrentIndex(max(0, index))
        self.note_type.blockSignals(False)

    def _available_fields(self) -> list[str]:
        return list_model_fields(self.note_type.currentText().strip())

    def _current_used_fields(self) -> dict[Any, str]:
        selected: dict[Any, str] = {}
        for index, widgets in enumerate(self.field_set_widgets):
            for role, combo in widgets["combos"].items():
                value = str(combo.currentData() or "")
                if value:
                    selected[("set", index, role)] = value
            for role_id, combo in widgets.get("extra_combos", {}).items():
                value = str(combo.currentData() or "")
                if value:
                    selected[("set", index, role_id)] = value
        for kind, widgets in (("generic", self.generic_widgets), ("audio", self.audio_widgets)):
            for index, item in enumerate(widgets):
                value = str(item["combo"].currentData() or "")
                if value:
                    selected[(kind, index)] = value
        return selected

    def _fill_combo(
        self,
        combo: QComboBox,
        current: str,
        fields: list[str],
        used: set[str],
        required: bool,
        empty_label: str | None = None,
    ) -> None:
        if empty_label is None:
            empty_label = "(choose field)" if required else EMPTY_FIELD
        fill_field_combo(combo, current, fields, used, empty_label)

    def _refresh_field_dropdowns(self) -> None:
        fields = self._available_fields()
        selected: dict[Any, str] = {}
        for index, field_set in enumerate(self.config.get("field_sets") or []):
            for role in FIELD_ROLES:
                name = str(field_set.get(role) or "")
                if name:
                    selected[("set", index, role)] = name
            extras = field_set.get("extras") if isinstance(field_set.get("extras"), dict) else {}
            for role_id, name in extras.items():
                if name:
                    selected[("set", index, str(role_id))] = str(name)
        for kind, key in (("generic", "generic_fields"), ("audio", "audio_fields")):
            for index, extra in enumerate(self.config.get(key) or []):
                name = str(extra.get("field") or "")
                if name:
                    selected[(kind, index)] = name
        if not selected:
            selected = self._current_used_fields()
        used = {name.casefold() for name in selected.values()}
        was_loading = self._loading
        self._loading = True
        for index, widgets in enumerate(self.field_set_widgets):
            for role, combo in widgets["combos"].items():
                current = selected.get(("set", index, role), "")
                empty_label = None if role in REQUIRED_ROLES else DONT_IMPORT
                self._fill_combo(combo, current, fields, used, role in REQUIRED_ROLES, empty_label)
            for role_id, combo in widgets.get("extra_combos", {}).items():
                current = selected.get(("set", index, role_id), "")
                self._fill_combo(combo, current, fields, used, False)
        for kind, widgets in (("generic", self.generic_widgets), ("audio", self.audio_widgets)):
            for index, item in enumerate(widgets):
                current = selected.get((kind, index), "")
                self._fill_combo(item["combo"], current, fields, used, False)
        self._loading = was_loading

    def _on_note_type_changed(self, _text: str) -> None:
        if self._loading:
            return
        self.config["note_type"] = self.note_type.currentText().strip()
        self._persist()
        self._refresh_field_dropdowns()

    def _on_fields_changed(self) -> None:
        if self._loading:
            return
        self._collect_fields_into_config()
        self.config = normalize_config(self.config)
        self._persist()
        self._refresh_field_dropdowns()
        self._populate_anki_tree()

    def _browse_index(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose index.json", self.job_index_path.text(), "JSON (*.json)")
        if path:
            self.job_index_path.setText(path)
            self._on_index_path_changed()

    def _on_index_path_changed(self) -> None:
        self._save_jobs()
        self._populate_record_tree()

    def _fill_key_tree(
        self,
        tree: QTreeWidget,
        paths: list[str],
        selection: dict[str, bool],
        default: bool = True,
        labels: dict[str, str] | None = None,
        counts: dict[str, int] | None = None,
    ) -> dict[str, QTreeWidgetItem]:
        tree.blockSignals(True)
        tree.clear()
        items: dict[str, QTreeWidgetItem] = {}
        labels = labels or {}
        counts = counts or {}
        for path in paths:
            parts = path.split(".")
            parent: QTreeWidgetItem | None = None
            built: list[str] = []
            for part in parts:
                built.append(part)
                key = ".".join(built)
                if key not in items:
                    text = labels.get(key, part)
                    if key in counts:
                        text = f"{text} [{counts[key]}]"
                    node = QTreeWidgetItem([text])
                    node.setData(0, Qt.ItemDataRole.UserRole, key)
                    node.setFlags(node.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    node.setCheckState(0, Qt.CheckState.Checked if selection.get(key, default) else Qt.CheckState.Unchecked)
                    if parent is None:
                        tree.addTopLevelItem(node)
                    else:
                        parent.addChild(node)
                    items[key] = node
                parent = items[key]
        tree.expandToDepth(0)
        tree.blockSignals(False)
        return items

    def _populate_anki_tree(self) -> None:
        selection = effective_anki_keys(self.config.get("anki_export_keys"), self.config)
        self._anki_items = self._fill_key_tree(
            self.anki_tree,
            anki_export_key_tree(self.config),
            selection,
            labels=anki_export_key_labels(self.config),
        )
        self._refresh_redundancy_editor()
        self._apply_redundancy_colors()

    def _populate_record_tree(self) -> None:
        trees = [tree for tree in self.config.get("job_record_trees") or [] if isinstance(tree, dict)]
        active_id = str(self.config.get("active_job_record_tree") or "")
        self.job_tree_combo.blockSignals(True)
        self.job_tree_combo.clear()
        if not trees:
            self.job_tree_combo.addItem("No job-record tree", "")
        for tree in trees:
            label = str(tree.get("name") or tree.get("deck_name") or tree.get("id"))
            if str(tree.get("id") or "") == active_id:
                label = f"{label} (active)"
            self.job_tree_combo.addItem(label, str(tree.get("id") or ""))
        if active_id:
            index = self.job_tree_combo.findData(active_id)
            if index >= 0:
                self.job_tree_combo.setCurrentIndex(index)
        self.job_tree_combo.blockSignals(False)
        tree = active_job_tree(self.config)
        has_tree = tree is not None
        self.record_tree.setVisible(has_tree)
        self.record_tree_empty.setVisible(not has_tree)
        self.rebuild_tree_button.setEnabled(has_tree)
        self.delete_tree_button.setEnabled(has_tree)
        if not has_tree:
            self._job_items = {}
            self.record_tree.clear()
        else:
            states = tree.get("keys") if isinstance(tree.get("keys"), dict) else {}
            paths = sorted(states, key=lambda path: str(path).split("."))
            counts = {
                path: int(state.get("count") or 0)
                for path, state in states.items()
                if isinstance(state, dict)
            }
            self._job_items = self._fill_key_tree(self.record_tree, paths, tree_key_selection(tree), counts=counts)
        self._refresh_redundancy_editor()
        self._apply_redundancy_colors()

    def _refresh_export_examples(self) -> None:
        tree = active_job_tree(self.config)
        deck = str((tree or {}).get("deck_name") or "").strip()
        note_type = str(self.config.get("note_type") or "").strip()
        if not deck or not note_type:
            self._export_examples = {}
            self._export_sample_ready = False
            self._export_note_cache_key = None
            return
        key = (deck, note_type)
        if key != getattr(self, "_export_note_cache_key", None):
            from .anki_scan import snapshot_notes_of_type

            self._export_note_cache = snapshot_notes_of_type(note_type, [deck], limit=80)
            self._export_note_cache_key = key
        notes = list(getattr(self, "_export_note_cache", []) or [])
        if not notes:
            self._export_examples = {}
            self._export_sample_ready = False
            return
        profile = ""
        try:
            from aqt import mw

            profile = str(getattr(getattr(mw, "pm", None), "name", "") or "")
        except Exception:
            profile = ""
        self._export_examples = export_key_examples(notes, self.config, profile_name=profile)
        self._export_sample_ready = True

    def _export_example_map(self) -> dict[str, str]:
        if not getattr(self, "_export_sample_ready", False):
            self._refresh_export_examples()
        if not getattr(self, "_export_sample_ready", False):
            return {}
        return dict(getattr(self, "_export_examples", {}) or {})

    def _job_example_map(self) -> dict[str, str]:
        tree = active_job_tree(self.config)
        raw = tree.get("examples") if tree and isinstance(tree.get("examples"), dict) else {}
        return {str(path): str(value) for path, value in raw.items() if str(path or "").strip() and str(value or "").strip()}

    def _example_tip(self, kind: str, path: str) -> str:
        if kind == "anki":
            if not getattr(self, "_export_sample_ready", False):
                return SAMPLE_PLACEHOLDER
            return self._export_example_map().get(path) or SAMPLE_PLACEHOLDER
        return self._job_example_map().get(path) or NO_JOB_EXAMPLE

    def _apply_redundancy_colors(self) -> None:
        anki_items = getattr(self, "_anki_items", {})
        job_items = getattr(self, "_job_items", {})
        brush = QBrush(QColor(REDUNDANCY_COLOR))
        default_brush = QBrush()
        enabled = bool(self.job_enabled.isChecked())
        tree = active_job_tree(self.config)
        defined_pairs = list(tree.get("redundancy_pairs") or []) if tree else []
        pairs = selected_job_redundancy_pairs(
            effective_anki_keys(self.config.get("anki_export_keys"), self.config),
            tree_key_selection(tree),
            defined_pairs,
        )
        reverse = reverse_job_redundancy(defined_pairs)
        self._refresh_export_examples()
        self.anki_tree.blockSignals(True)
        self.record_tree.blockSignals(True)
        try:
            for path, item in anki_items.items():
                item.setForeground(0, default_brush)
                item.setToolTip(0, self._example_tip("anki", path))
            for path, item in job_items.items():
                item.setForeground(0, default_brush)
                item.setToolTip(0, self._example_tip("job", path))
            if enabled:
                for anki_path, job_path in pairs.items():
                    anki_item = anki_items.get(anki_path)
                    job_item = job_items.get(job_path)
                    if anki_item is None or job_item is None:
                        continue
                    anki_item.setForeground(0, brush)
                    job_item.setForeground(0, brush)
                    anki_item.setToolTip(
                        0,
                        f"{self._example_tip('anki', anki_path)}\n\nSimilar to job-record key {job_path}",
                    )
                    job_item.setToolTip(
                        0,
                        f"{self._example_tip('job', job_path)}\n\nSimilar to clip-library key {reverse.get(job_path, anki_path)}",
                    )
        finally:
            self.anki_tree.blockSignals(False)
            self.record_tree.blockSignals(False)

    def _on_anki_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._loading:
            return
        keys = dict(self.config.get("anki_export_keys") or {})
        self._set_child_checks(self.anki_tree, item, item.checkState(0))
        self._collect_tree_keys(self.anki_tree.invisibleRootItem(), keys)
        self.config["anki_export_keys"] = keys
        self._persist()
        self._apply_redundancy_colors()

    def _on_record_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._loading:
            return
        tree = active_job_tree(self.config)
        if tree is None:
            return
        checked: dict[str, bool] = {}
        self._set_child_checks(self.record_tree, item, item.checkState(0))
        self._collect_tree_keys(self.record_tree.invisibleRootItem(), checked)
        states = tree.setdefault("keys", {})
        for path, enabled in checked.items():
            state = states.get(path) if isinstance(states.get(path), dict) else {"count": 0}
            state["enabled"] = enabled
            states[path] = state
        self._replace_active_tree(tree)
        self._persist()
        self._apply_redundancy_colors()

    def _on_job_tree_changed(self, _index: int) -> None:
        if self._loading:
            return
        tree_id = str(self.job_tree_combo.currentData() or "")
        if not tree_id:
            return
        self.config["active_job_record_tree"] = tree_id
        self._persist()
        self._populate_record_tree()

    def _replace_active_tree(self, updated: dict[str, Any]) -> None:
        trees = []
        for tree in self.config.get("job_record_trees") or []:
            if isinstance(tree, dict) and str(tree.get("id") or "") == str(updated.get("id") or ""):
                trees.append(updated)
            else:
                trees.append(tree)
        self.config["job_record_trees"] = trees

    def _sample_job_records(self, deck_name: str) -> tuple[dict[str, list[str]], dict[str, str], list[str]]:
        warnings: list[str] = []
        index = JobIndex.load(self.job_index_path.text().strip())
        warnings.extend(index.errors)
        updates: dict[str, list[str]] = {}
        examples: dict[str, str] = {}
        try:
            from aqt import mw

            mw.progress.start(label="Reading job records...", immediate=True)
        except Exception:
            mw = None
        try:
            note_ids = find_deck_note_ids(deck_name)
            clips, _scanned, _skipped = scan_note_ids(note_ids, self.config)
            for clip in clips:
                match = index.match_clip(clip)
                if not match or not match.record:
                    continue
                sample_id = record_sample_id(match.record, match.record_path)
                if not sample_id:
                    continue
                updates[sample_id] = sorted(collect_key_paths(match.record))
                for path, text in collect_key_examples(match.record).items():
                    examples.setdefault(path, text)
        finally:
            if mw is not None:
                try:
                    mw.progress.finish()
                except Exception:
                    pass
        return updates, examples, warnings

    def _build_job_tree(self) -> None:
        from .config import config_ready_for_export

        self._collect_fields_into_config()
        ready, message = config_ready_for_export(self.config)
        if not ready:
            QMessageBox.information(self, "Clip Library", message)
            return
        if not self.job_index_path.text().strip():
            QMessageBox.information(self, "Clip Library", "Choose an index.json location before building a job-record key tree.")
            return
        deck_name = choose_deck(self)
        if not deck_name:
            return
        self._save_jobs()
        with timed("building job record export key tree"):
            self._build_job_tree_from_deck(deck_name)

    def _build_job_tree_from_deck(self, deck_name: str) -> None:
        existing = next(
            (
                tree
                for tree in self.config.get("job_record_trees") or []
                if isinstance(tree, dict) and str(tree.get("deck_name") or "") == deck_name
            ),
            None,
        )
        updates, examples, warnings = self._sample_job_records(deck_name)
        if existing is None and not updates:
            QMessageBox.information(
                self,
                "Clip Library",
                "No job records were found for video clips in that deck, so no tree was created.",
            )
            return
        if existing is None:
            existing = empty_job_tree(deck_name, uuid.uuid4().hex)
            legacy = self.config.get("job_record_keys") if not self.config.get("job_record_trees") else None
            updated = apply_key_examples(
                apply_record_samples(existing, updates, legacy_selection=legacy if isinstance(legacy, dict) else None),
                examples,
            )
            self.config.setdefault("job_record_trees", []).append(updated)
        else:
            updated = apply_key_examples(apply_record_samples(existing, updates), examples)
            self._replace_active_tree(updated)
        self.config["active_job_record_tree"] = str(updated.get("id") or "")
        self._persist()
        self._populate_record_tree()
        notice = (
            f"Job-record key tree for {deck_name} now includes {len(updated.get('samples') or {})} sampled record(s). "
            "Save settings to keep this tree."
        )
        if warnings:
            notice += "\n\n" + "\n".join(warnings)
        QMessageBox.information(self, "Clip Library", notice)

    def _rebuild_active_job_tree(self) -> None:
        tree = active_job_tree(self.config)
        if tree is None:
            return
        if not self.job_index_path.text().strip():
            QMessageBox.information(self, "Clip Library", "Choose an index.json location before rebuilding a job-record key tree.")
            return
        deck_name = str(tree.get("deck_name") or "")
        with timed("building job record export key tree"):
            updates, examples, warnings = self._sample_job_records(deck_name)
            updated = apply_key_examples(apply_record_samples(tree, updates), examples)
        self._replace_active_tree(updated)
        self._persist()
        self._populate_record_tree()
        notice = (
            f"Rebuilt the job-record key tree for {deck_name}. "
            f"Added or refreshed {len(updates)} record(s); the sample pile now has {len(updated.get('samples') or {})}. "
            "Save settings to keep this tree."
        )
        if not updates:
            notice = (
                f"No new job records were found in {deck_name}. Existing samples and key choices were kept. "
                "Save settings to keep this tree."
            )
        if warnings:
            notice += "\n\n" + "\n".join(warnings)
        QMessageBox.information(self, "Clip Library", notice)

    def _delete_active_job_tree(self) -> None:
        tree = active_job_tree(self.config)
        if tree is None:
            return
        answer = QMessageBox.question(self, "Clip Library", f"Delete the job-record key tree for {tree.get('deck_name')}?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        tree_id = str(tree.get("id") or "")
        self.config["job_record_trees"] = [
            item for item in self.config.get("job_record_trees") or [] if str(item.get("id") or "") != tree_id
        ]
        self.config["active_job_record_tree"] = ""
        self._persist()
        self._populate_record_tree()

    def _refresh_redundancy_editor(self) -> None:
        if not hasattr(self, "redundancy_list"):
            return
        self._refresh_export_examples()
        tree = active_job_tree(self.config)
        pairs = list(tree.get("redundancy_pairs") or []) if tree else []
        self.redundancy_list.clear()
        for pair in pairs:
            if isinstance(pair, dict):
                self.redundancy_list.addItem(f"{pair.get('clip_key')}  ↔  {pair.get('job_key')}")
        self.clip_key_combo.blockSignals(True)
        self.job_key_combo.blockSignals(True)
        self.clip_key_combo.clear()
        self.job_key_combo.clear()
        for path in anki_export_key_tree(self.config):
            label = anki_export_key_labels(self.config).get(path, path)
            self.clip_key_combo.addItem(label if label == path else f"{label} ({path})", path)
        job_keys = sorted((tree.get("keys") or {}) if tree else [], key=lambda item: str(item).split("."))
        for path in job_keys:
            self.job_key_combo.addItem(str(path), str(path))
        apply_combo_example_tooltips(self.clip_key_combo, self._export_example_map(), missing=SAMPLE_PLACEHOLDER)
        apply_combo_example_tooltips(self.job_key_combo, self._job_example_map(), missing=NO_JOB_EXAMPLE)
        enabled = tree is not None
        self.clip_key_combo.setEnabled(enabled)
        self.job_key_combo.setEnabled(enabled)
        self.redundancy_list.setEnabled(enabled)
        self.clip_key_combo.blockSignals(False)
        self.job_key_combo.blockSignals(False)

    def _add_redundancy_pair(self) -> None:
        tree = active_job_tree(self.config)
        if tree is None:
            return
        clip_key = str(self.clip_key_combo.currentData() or "")
        job_key = str(self.job_key_combo.currentData() or "")
        if not clip_key or not job_key:
            return
        pairs = [pair for pair in tree.get("redundancy_pairs") or [] if isinstance(pair, dict)]
        if any(pair.get("clip_key") == clip_key and pair.get("job_key") == job_key for pair in pairs):
            return
        pairs.append({"clip_key": clip_key, "job_key": job_key})
        tree["redundancy_pairs"] = pairs
        self._replace_active_tree(tree)
        self._persist()
        self._refresh_redundancy_editor()
        self._apply_redundancy_colors()

    def _remove_redundancy_pair(self) -> None:
        tree = active_job_tree(self.config)
        if tree is None:
            return
        row = self.redundancy_list.currentRow()
        pairs = [pair for pair in tree.get("redundancy_pairs") or [] if isinstance(pair, dict)]
        if row < 0 or row >= len(pairs):
            return
        del pairs[row]
        tree["redundancy_pairs"] = pairs
        self._replace_active_tree(tree)
        self._persist()
        self._refresh_redundancy_editor()
        self._apply_redundancy_colors()

    def _set_child_checks(self, tree: QTreeWidget, item: QTreeWidgetItem, state: Qt.CheckState) -> None:
        tree.blockSignals(True)
        for index in range(item.childCount()):
            child = item.child(index)
            child.setCheckState(0, state)
            self._set_child_checks(tree, child, state)
        tree.blockSignals(False)

    def _collect_tree_keys(self, item: QTreeWidgetItem, keys: dict[str, bool]) -> None:
        path = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        if path:
            keys[path] = item.checkState(0) == Qt.CheckState.Checked
        for index in range(item.childCount()):
            self._collect_tree_keys(item.child(index), keys)

    def _browse_mpv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose mpv executable", self.mpv_path.text())
        if path:
            self.mpv_path.setText(path)
            self._save_import()

    def _save_jobs(self) -> None:
        if self._loading:
            return
        self.config["job_records_enabled"] = self.job_enabled.isChecked()
        self.config["job_index_path"] = self.job_index_path.text().strip()
        self.config["path_privacy_mode"] = str(self.privacy_mode.currentData() or "off")
        self.config["path_privacy_parents"] = int(self.privacy_parents.value())
        self._persist()
        self._apply_redundancy_colors()

    def _save_import(self) -> None:
        if self._loading:
            return
        self.config["mpv_executable"] = self.mpv_path.text().strip() or "mpv"
        self.config["import_mode"] = "match"
        self._persist()

    def _persist(self) -> None:
        if self._loading:
            return
        if hasattr(self, "import_editor"):
            self.import_editor.config = self.config

    def _owned_view(self, config: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_config(config)
        return {key: normalized.get(key) for key in SETTINGS_OWNED_KEYS}

    def _sync_from_widgets(self) -> None:
        if hasattr(self, "field_set_widgets"):
            self._collect_fields_into_config()
        if hasattr(self, "import_editor"):
            self.import_editor.collect()
        if hasattr(self, "job_enabled"):
            self.config["job_records_enabled"] = self.job_enabled.isChecked()
            self.config["job_index_path"] = self.job_index_path.text().strip()
            self.config["path_privacy_mode"] = str(self.privacy_mode.currentData() or "off")
            self.config["path_privacy_parents"] = int(self.privacy_parents.value())
        if hasattr(self, "exclusion_editor"):
            self.config["export_exclusions_enabled"] = self.exclusion_editor.is_enabled()
            self.config["export_exclusions"] = self.exclusion_editor.rules()
        if hasattr(self, "mpv_path"):
            self.config["mpv_executable"] = self.mpv_path.text().strip() or "mpv"
        if hasattr(self, "note_type"):
            self.config["note_type"] = self.note_type.currentText().strip()

    def _is_dirty(self) -> bool:
        return self._owned_view(self.config) != getattr(self, "_opened_owned", {})

    def _write_settings(self) -> None:
        latest = load_config()
        draft = normalize_config(self.config)
        for key in SETTINGS_OWNED_KEYS:
            latest[key] = draft[key]
        self.config = save_config(latest)
        if hasattr(self, "import_editor"):
            self.import_editor.config = self.config
        self._opened_owned = self._owned_view(self.config)

    def _save_and_close(self) -> None:
        self._sync_from_widgets()
        self._write_settings()
        self._decided = True
        self.accept()

    def _confirm_discard(self) -> bool:
        self._sync_from_widgets()
        if not self._is_dirty():
            return True
        answer = QMessageBox.question(
            self,
            "Clip Library",
            "Save changes to Clip Library settings?\n\nClosing without saving discards changes made in this window.",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            self._write_settings()
            return True
        return answer == QMessageBox.StandardButton.Discard

    def reject(self) -> None:
        if self._decided or self._confirm_discard():
            self._decided = True
            super().reject()

    def closeEvent(self, event: Any) -> None:
        if not self._decided and not self._confirm_discard():
            event.ignore()
            return
        self._decided = True
        super().closeEvent(event)

    def _on_exclusions_changed(self) -> None:
        if self._loading:
            return
        self._sync_from_widgets()
        self._persist()

    def _preset_list(self) -> list[dict[str, Any]]:
        presets = self.config.get("export_exclusion_presets")
        if not isinstance(presets, list):
            return []
        return [preset for preset in presets if isinstance(preset, dict)]

    def _selected_preset_id(self) -> str:
        if not hasattr(self, "exclusion_preset_combo"):
            return ""
        return str(self.exclusion_preset_combo.currentData() or "")

    def _selected_preset(self) -> dict[str, Any] | None:
        preset_id = self._selected_preset_id()
        if not preset_id:
            return None
        return next((preset for preset in self._preset_list() if str(preset.get("id") or "") == preset_id), None)

    def _refresh_exclusion_presets(self, select_id: str = "") -> None:
        if not hasattr(self, "exclusion_preset_combo"):
            return
        if not select_id:
            select_id = self._selected_preset_id()
        combo = self.exclusion_preset_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Select a preset", "")
        for preset in self._preset_list():
            combo.addItem(str(preset.get("name") or ""), str(preset.get("id") or ""))
        index = combo.findData(select_id) if select_id else 0
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)
        self._update_preset_buttons()

    def _update_preset_buttons(self) -> None:
        has_preset = self._selected_preset() is not None
        self.load_preset_button.setEnabled(has_preset)
        self.update_preset_button.setEnabled(has_preset)
        self.delete_preset_button.setEnabled(has_preset)

    def _load_exclusion_preset(self) -> None:
        preset = self._selected_preset()
        if preset is None:
            return
        enabled = bool(preset.get("enabled", True))
        rules = list(preset.get("rules") or [])
        if self.exclusion_editor.rules() != rules or self.exclusion_editor.is_enabled() != enabled:
            answer = QMessageBox.question(
                self,
                "Clip Library",
                f"Replace the rules in the table with \"{preset.get('name')}\"?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.exclusion_editor.set_state(enabled=enabled, rules=rules)
        self._on_exclusions_changed()

    def _save_exclusion_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Clip Library", "Preset name:")
        if not ok:
            return
        name = " ".join(str(name or "").split())
        if not name:
            QMessageBox.warning(self, "Clip Library", "Enter a preset name.")
            return
        presets = [dict(preset) for preset in self._preset_list()]
        existing = next((preset for preset in presets if str(preset.get("name") or "").casefold() == name.casefold()), None)
        if existing is not None:
            answer = QMessageBox.question(
                self,
                "Clip Library",
                f"Replace the preset \"{existing.get('name')}\" with the rules in the table?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            existing["name"] = name
            existing["enabled"] = self.exclusion_editor.is_enabled()
            existing["rules"] = self.exclusion_editor.rules()
            preset_id = str(existing.get("id") or "")
        else:
            preset_id = uuid.uuid4().hex
            presets.append(
                {
                    "id": preset_id,
                    "name": name,
                    "enabled": self.exclusion_editor.is_enabled(),
                    "rules": self.exclusion_editor.rules(),
                }
            )
        self.config["export_exclusion_presets"] = presets
        self._refresh_exclusion_presets(preset_id)
        self._on_exclusions_changed()

    def _update_exclusion_preset(self) -> None:
        preset = self._selected_preset()
        if preset is None:
            return
        answer = QMessageBox.question(
            self,
            "Clip Library",
            f"Replace \"{preset.get('name')}\" with the rules in the table?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        preset["enabled"] = self.exclusion_editor.is_enabled()
        preset["rules"] = self.exclusion_editor.rules()
        self._on_exclusions_changed()

    def _delete_exclusion_preset(self) -> None:
        preset = self._selected_preset()
        if preset is None:
            return
        answer = QMessageBox.question(
            self,
            "Clip Library",
            f"Delete the preset \"{preset.get('name')}\"? The rules in the table stay.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        preset_id = str(preset.get("id") or "")
        self.config["export_exclusion_presets"] = [
            item for item in self._preset_list() if str(item.get("id") or "") != preset_id
        ]
        self._refresh_exclusion_presets("")
        self._on_exclusions_changed()

    def _on_import_profile_changed(self) -> None:
        if self._loading:
            return
        self._persist()

    def _import_note_samples(self) -> tuple[dict[str, str], str | None]:
        from .anki_scan import list_model_fields, snapshot_notes_of_type
        from .identity import NOTE_SAMPLE_PLACEHOLDER, note_identity_samples

        decks = [str(name) for name in self.config.get("import_decks") or [] if str(name or "").strip()]
        note_type = str(self.config.get("import_note_type") or "").strip()
        if not decks or not note_type:
            return {}, NOTE_SAMPLE_PLACEHOLDER
        notes = snapshot_notes_of_type(note_type, decks)
        if not notes:
            return {}, NOTE_SAMPLE_PLACEHOLDER
        return note_identity_samples(notes, list_model_fields(note_type)), None
