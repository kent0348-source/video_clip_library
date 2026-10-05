from __future__ import annotations

from collections.abc import Callable
from typing import Any

from aqt.qt import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    Qt,
)

from .anki_scan import list_model_fields, list_note_types
from .config import (
    blank_field_set,
    dedupe_field_names,
    default_field_set_name,
    remember_import_note_profile,
    switch_import_note_type,
)
from .models import FIELD_ROLES
from .identity import (
    NOTE_SAMPLE_PLACEHOLDER,
    ensure_identity_choices,
    identity_example_line,
    note_identity_choices,
)
from .ui_common import fill_field_combo, icon_button
from .ui_identity import edit_identity


SampleProvider = Callable[[], tuple[dict[str, str], str | None]]


class ImportNoteTypeEditor:
    """Import note type, field sets, and note identity shared by Settings and the import dialog."""

    def __init__(
        self,
        config: dict[str, Any],
        on_changed: Callable[[], None],
        sample_provider: SampleProvider | None = None,
    ) -> None:
        self.config = config
        self._on_changed = on_changed
        self._sample_provider = sample_provider or (lambda: ({}, NOTE_SAMPLE_PLACEHOLDER))
        self._loading = False
        self._widgets: list[dict[str, Any]] = []
        self.note_type = QComboBox()
        self.note_type.currentIndexChanged.connect(self._on_note_type_changed)
        self.note_identity_button = QPushButton("Note identity...")
        self.note_identity_button.setToolTip("Names shown for target notes in the mapping preview.")
        self.note_identity_button.clicked.connect(self._edit_note_identity)
        self.note_preview = QLabel()
        self.note_preview.setWordWrap(True)
        self.field_panel = self._build_field_panel()
        self._fill_note_types()
        self._rebuild_field_sets()
        self.refresh_note_preview()

    def settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        form.addRow("Import note type", self.note_type)
        form.addRow("Note identity", self.note_identity_button)
        layout.addLayout(form)
        layout.addWidget(self.note_preview)
        hint = QLabel(
            "These field sets, extra rows, and the note identity are remembered for this note type. "
            "Match rules, score, and target decks stay in the import window."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(self.field_panel, 1)
        return page

    def note_type_name(self) -> str:
        return str(self.note_type.currentData() or self.config.get("import_note_type") or "")

    def fields(self) -> list[str]:
        return list_model_fields(self.note_type_name())

    def _collect_fields(self) -> None:
        if not self._widgets:
            return
        sets: list[dict[str, Any]] = []
        for index, widgets in enumerate(self._widgets):
            entry: dict[str, Any] = {
                "enabled": True if index == 0 else widgets["enabled"].isChecked(),
                "name": widgets["name"].text().strip() or default_field_set_name(index + 1),
                "extras": {
                    role_id: str(combo.currentData() or "")
                    for role_id, combo in widgets.get("extra_combos", {}).items()
                },
            }
            for role, combo in widgets["combos"].items():
                entry[role] = str(combo.currentData() or "")
            sets.append(entry)
        dedupe_field_names(sets)
        self.config["import_field_sets"] = sets or [blank_field_set(1, self._role_ids())]
        if self._widgets:
            roles: list[dict[str, str]] = []
            names = self._widgets[0].get("extra_names", {})
            for role in self.config.get("import_extra_roles") or []:
                if not isinstance(role, dict):
                    continue
                role_id = str(role.get("id") or "")
                if not role_id:
                    continue
                editor = names.get(role_id)
                label = editor.text().strip() if editor is not None else str(role.get("name") or "")
                roles.append({"id": role_id, "name": label or str(role.get("name") or role_id)})
            self.config["import_extra_roles"] = roles

    def collect(self) -> None:
        self._collect_fields()
        self.config["import_note_type"] = self.note_type_name()
        self.config["import_mapping_initialized"] = True
        remember_import_note_profile(self.config)

    def refresh_note_preview(self) -> None:
        samples, unavailable = self._sample_provider()
        self._cached_samples = samples
        self._cached_unavailable = unavailable
        self.note_preview.setText(
            identity_example_line(
                list(self.config.get("import_note_identity") or []),
                samples,
                unavailable=unavailable,
            )
        )

    def _build_field_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Import field sets"))
        hint = QLabel(
            "A field already chosen in this note type is hidden from the other dropdowns. "
            "The + on a field set adds another field to every set."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self.import_sets_layout = QVBoxLayout(content)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        add_set = QPushButton("Add field set")
        add_set.clicked.connect(self._add_field_set)
        layout.addWidget(add_set, alignment=Qt.AlignmentFlag.AlignLeft)
        return panel

    def _fill_note_types(self) -> None:
        current = str(self.config.get("import_note_type") or "")
        self.note_type.blockSignals(True)
        self.note_type.clear()
        self.note_type.addItem("(choose)", "")
        for name in list_note_types():
            self.note_type.addItem(name, name)
        if current:
            found = self.note_type.findData(current)
            if found < 0:
                self.note_type.addItem(current, current)
                found = self.note_type.findData(current)
            self.note_type.setCurrentIndex(max(0, found))
        self.note_type.blockSignals(False)

    def _on_note_type_changed(self) -> None:
        if self._loading:
            return
        self._collect_fields()
        switch_import_note_type(self.config, str(self.note_type.currentData() or ""))
        self._rebuild_field_sets()
        self.refresh_note_preview()
        self._on_changed()

    def _edit_note_identity(self) -> None:
        samples, unavailable = self._sample_provider()
        self._cached_samples = samples
        self._cached_unavailable = unavailable
        choices = ensure_identity_choices(
            note_identity_choices(self.fields()),
            self.config.get("import_note_identity"),
        )
        updated = edit_identity(
            self.note_identity_button,
            "Target note identity",
            list(self.config.get("import_note_identity") or []),
            choices,
            samples,
            unavailable,
        )
        if updated is None:
            self.refresh_note_preview()
            return
        self.config["import_note_identity"] = updated
        remember_import_note_profile(self.config)
        self.refresh_note_preview()
        self._on_changed()

    def _clear_sets(self) -> None:
        while self.import_sets_layout.count():
            item = self.import_sets_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._widgets = []

    def _rebuild_field_sets(self) -> None:
        self._loading = True
        self._clear_sets()
        sets = list(self.config.get("import_field_sets") or [])
        if not sets:
            sets = [blank_field_set(1, self._role_ids())]
            self.config["import_field_sets"] = sets
        for index, field_set in enumerate(sets):
            self._add_set_row(index, field_set if isinstance(field_set, dict) else {})
        self._loading = False
        self._refresh_field_combos()

    def _add_set_row(self, index: int, field_set: dict[str, Any]) -> None:
        box = QGroupBox(str(field_set.get("name") or default_field_set_name(index + 1)))
        grid = QGridLayout(box)
        enabled = QCheckBox("Enabled")
        enabled.setChecked(True if index == 0 else bool(field_set.get("enabled", True)))
        enabled.setEnabled(index > 0)
        enabled.toggled.connect(self._on_fields_changed)
        name = QLineEdit(str(field_set.get("name") or default_field_set_name(index + 1)))
        name.editingFinished.connect(self._on_fields_changed)
        name.textChanged.connect(lambda text, group=box, number=index + 1: group.setTitle(text or default_field_set_name(number)))
        grid.addWidget(enabled, 0, 0)
        grid.addWidget(name, 0, 1)
        if index > 0:
            remove = QPushButton("Remove")
            remove.clicked.connect(lambda _checked=False, row_index=index: self._remove_field_set(row_index))
            grid.addWidget(remove, 0, 2)
        combos: dict[str, QComboBox] = {}
        labels = (
            ("video", "Video"),
            ("sentence", "Sentence"),
            ("secondary", "Secondary"),
            ("miscinfo", "Miscinfo"),
        )
        for row, (role, label) in enumerate(labels, start=1):
            combo = QComboBox()
            combo.setProperty("savedField", str(field_set.get(role) or ""))
            combo.currentIndexChanged.connect(self._on_fields_changed)
            combos[role] = combo
            grid.addWidget(QLabel(label), row, 0)
            grid.addWidget(combo, row, 1, 1, 2)
        extra_combos: dict[str, QComboBox] = {}
        extra_names: dict[str, QLineEdit] = {}
        extras = field_set.get("extras") if isinstance(field_set.get("extras"), dict) else {}
        next_row = len(FIELD_ROLES) + 1
        for offset, role in enumerate(self.config.get("import_extra_roles") or []):
            if not isinstance(role, dict):
                continue
            role_id = str(role.get("id") or "")
            if not role_id:
                continue
            label = QLineEdit(str(role.get("name") or role_id))
            label.editingFinished.connect(
                lambda role_id=role_id, editor=label: self._on_import_role_renamed(role_id, editor.text())
            )
            combo = QComboBox()
            combo.setProperty("savedField", str(extras.get(role_id) or ""))
            combo.currentIndexChanged.connect(self._on_fields_changed)
            remove_role = icon_button("minus", "Remove this field from every field set")
            remove_role.clicked.connect(lambda _checked=False, role_id=role_id: self._remove_import_role(role_id))
            row = next_row + offset
            grid.addWidget(label, row, 0)
            grid.addWidget(combo, row, 1)
            grid.addWidget(remove_role, row, 2)
            extra_names[role_id] = label
            extra_combos[role_id] = combo
        add_role = icon_button("plus", "Add a field to every field set")
        add_role.clicked.connect(self._add_import_role)
        grid.addWidget(add_role, next_row + len(extra_combos), 0)
        self.import_sets_layout.addWidget(box)
        self._widgets.append(
            {
                "enabled": enabled,
                "name": name,
                "combos": combos,
                "extra_combos": extra_combos,
                "extra_names": extra_names,
            }
        )

    def _refresh_field_combos(self) -> None:
        fields = self.fields()
        selected: dict[tuple[int, str], str] = {}
        for index, field_set in enumerate(self.config.get("import_field_sets") or []):
            if not isinstance(field_set, dict):
                continue
            for role in FIELD_ROLES:
                name = str(field_set.get(role) or "")
                if name:
                    selected[(index, role)] = name
            extras = field_set.get("extras") if isinstance(field_set.get("extras"), dict) else {}
            for role_id, name in extras.items():
                cleaned = str(name or "")
                if cleaned:
                    selected[(index, str(role_id))] = cleaned
        if not selected:
            for index, widgets in enumerate(self._widgets):
                for role, combo in widgets["combos"].items():
                    name = str(combo.currentData() or combo.property("savedField") or "")
                    if name:
                        selected[(index, role)] = name
                for role_id, combo in widgets.get("extra_combos", {}).items():
                    name = str(combo.currentData() or combo.property("savedField") or "")
                    if name:
                        selected[(index, role_id)] = name
        used = {name.casefold() for name in selected.values()}
        was_loading = self._loading
        self._loading = True
        for index, widgets in enumerate(self._widgets):
            for role, combo in widgets["combos"].items():
                current = selected.get((index, role), "")
                empty = "(choose video field)" if role == "video" else "(don't write)"
                fill_field_combo(combo, current, fields, used, empty)
                combo.setProperty("savedField", "")
            for role_id, combo in widgets.get("extra_combos", {}).items():
                current = selected.get((index, role_id), "")
                fill_field_combo(combo, current, fields, used, "(don't write)")
                combo.setProperty("savedField", "")
        self._loading = was_loading

    def _on_fields_changed(self) -> None:
        if self._loading:
            return
        self.collect()
        self._refresh_field_combos()
        self._on_changed()

    def _add_field_set(self) -> None:
        if self._loading:
            return
        self.collect()
        sets = list(self.config.get("import_field_sets") or [])
        sets.append(blank_field_set(len(sets) + 1, self._role_ids(), enabled=True))
        self.config["import_field_sets"] = sets
        remember_import_note_profile(self.config)
        self._rebuild_field_sets()
        self._on_changed()

    def _remove_field_set(self, index: int) -> None:
        if index <= 0 or self._loading:
            return
        self.collect()
        sets = list(self.config.get("import_field_sets") or [])
        if index < len(sets):
            del sets[index]
        self.config["import_field_sets"] = sets or [blank_field_set(1, self._role_ids())]
        remember_import_note_profile(self.config)
        self._rebuild_field_sets()
        self._on_changed()

    def _role_ids(self) -> list[str]:
        return [
            str(role.get("id") or "")
            for role in self.config.get("import_extra_roles") or []
            if isinstance(role, dict) and role.get("id")
        ]

    def _add_import_role(self) -> None:
        if self._loading:
            return
        self.collect()
        roles = [role for role in self.config.get("import_extra_roles") or [] if isinstance(role, dict)]
        existing = {str(role.get("id") or "") for role in roles}
        number = 5
        while f"field_{number}" in existing:
            number += 1
        role_id = f"field_{number}"
        roles.append({"id": role_id, "name": f"Field {number}"})
        self.config["import_extra_roles"] = roles
        for field_set in self.config.get("import_field_sets") or []:
            if not isinstance(field_set, dict):
                continue
            extras = dict(field_set.get("extras") or {})
            extras.setdefault(role_id, "")
            field_set["extras"] = extras
        remember_import_note_profile(self.config)
        self._rebuild_field_sets()
        self._on_changed()

    def _remove_import_role(self, role_id: str) -> None:
        if self._loading:
            return
        self.collect()
        self.config["import_extra_roles"] = [
            role
            for role in self.config.get("import_extra_roles") or []
            if str(role.get("id") or "") != role_id
        ]
        for field_set in self.config.get("import_field_sets") or []:
            if not isinstance(field_set, dict):
                continue
            extras = dict(field_set.get("extras") or {})
            extras.pop(role_id, None)
            field_set["extras"] = extras
        remember_import_note_profile(self.config)
        self._rebuild_field_sets()
        self._on_changed()

    def _on_import_role_renamed(self, role_id: str, text: str) -> None:
        if self._loading:
            return
        cleaned = text.strip()
        for widgets in self._widgets:
            editor = widgets.get("extra_names", {}).get(role_id)
            if editor is not None and editor.text() != cleaned:
                editor.blockSignals(True)
                editor.setText(cleaned)
                editor.blockSignals(False)
        self._on_fields_changed()
