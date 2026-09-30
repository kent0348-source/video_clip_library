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
    remember_import_note_profile,
    switch_import_note_type,
)
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
            "These field sets and the note identity are remembered for this note type. "
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
                "name": widgets["name"].text().strip() or f"Field Set {index + 1}",
                "extras": {},
            }
            for role, combo in widgets["combos"].items():
                entry[role] = str(combo.currentData() or "")
            sets.append(entry)
        dedupe_field_names(sets)
        self.config["import_field_sets"] = sets or [blank_field_set(1)]

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
        hint = QLabel("A field already chosen in this note type is hidden from the other dropdowns.")
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
            sets = [blank_field_set(1)]
            self.config["import_field_sets"] = sets
        for index, field_set in enumerate(sets):
            self._add_set_row(index, field_set if isinstance(field_set, dict) else {})
        self._loading = False
        self._refresh_field_combos()

    def _add_set_row(self, index: int, field_set: dict[str, Any]) -> None:
        box = QGroupBox(str(field_set.get("name") or f"Field Set {index + 1}"))
        grid = QGridLayout(box)
        enabled = QCheckBox("Enabled")
        enabled.setChecked(True if index == 0 else bool(field_set.get("enabled", True)))
        enabled.setEnabled(index > 0)
        enabled.toggled.connect(self._on_fields_changed)
        name = QLineEdit(str(field_set.get("name") or f"Field Set {index + 1}"))
        name.editingFinished.connect(self._on_fields_changed)
        name.textChanged.connect(lambda text, group=box: group.setTitle(text or "Field set"))
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
        self.import_sets_layout.addWidget(box)
        self._widgets.append({"enabled": enabled, "name": name, "combos": combos})

    def _refresh_field_combos(self) -> None:
        fields = self.fields()
        selected: dict[tuple[int, str], str] = {}
        for index, field_set in enumerate(self.config.get("import_field_sets") or []):
            if not isinstance(field_set, dict):
                continue
            for role in ("video", "sentence", "secondary", "miscinfo"):
                name = str(field_set.get(role) or "")
                if name:
                    selected[(index, role)] = name
        if not selected:
            for index, widgets in enumerate(self._widgets):
                for role, combo in widgets["combos"].items():
                    name = str(combo.currentData() or combo.property("savedField") or "")
                    if name:
                        selected[(index, role)] = name
        used = {name.casefold() for name in selected.values()}
        was_loading = self._loading
        self._loading = True
        for index, widgets in enumerate(self._widgets):
            for role, combo in widgets["combos"].items():
                current = selected.get((index, role), "")
                empty = "(choose video field)" if role == "video" else "(don't write)"
                fill_field_combo(combo, current, fields, used, empty)
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
        sets.append(blank_field_set(len(sets) + 1, enabled=True))
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
        self.config["import_field_sets"] = sets or [blank_field_set(1)]
        remember_import_note_profile(self.config)
        self._rebuild_field_sets()
        self._on_changed()
