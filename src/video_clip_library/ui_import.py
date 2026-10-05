from __future__ import annotations

from html import escape
from typing import Any

from aqt.qt import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    Qt,
    QTimer,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)
from aqt.operations import CollectionOp, QueryOp
from aqt.utils import tooltip

from .anki_scan import find_note_ids_for_query, list_deck_names, list_model_fields, snapshot_notes_of_type
from .config import SORT_FIELD_ID, default_field_set_name, enabled_import_field_sets, load_config, save_config
from .identity import (
    NOTE_SAMPLE_PLACEHOLDER,
    SAMPLE_PLACEHOLDER,
    clip_identity_samples,
    ensure_identity_choices,
    format_note_label,
    identity_example_line,
)
from .ui_identity import edit_identity
from .ui_import_setup import ImportNoteTypeEditor
from .debuglog import log, timed
from .import_map import (
    apply_import_decisions,
    clip_label,
    clip_search_fields,
    describe_writes,
    insert_search_term,
    import_action_label,
    plan_imports,
    search_term_for_field,
    source_catalog,
    source_samples,
)
from .models import ImportCandidate, ImportDecision
from .ui_common import (
    apply_combo_example_tooltips,
    apply_dialog_screen_constraints,
    checked_deck_names,
    fill_deck_tree,
    icon_button,
)


POINT_CHOICES = (("High", "high"), ("Medium", "medium"), ("Low", "low"))
COMPARE_CHOICES = (("is exactly", "exact"), ("contains", "contains"))
DONT_IMPORT = "Don't import"


class _CollectionOpResult(dict):
    """Dict summary that also satisfies CollectionOp's result.changes contract."""

    changes: Any


def _collection_op_result(summary: dict[str, Any]) -> _CollectionOpResult:
    from anki.collection import OpChanges

    changes = summary.get("op_changes")
    if changes is None:
        changes = OpChanges()
        if summary.get("updated"):
            changes.note = True
            changes.note_text = True
            changes.browser_table = True
    result = _CollectionOpResult(
        {key: value for key, value in summary.items() if key != "op_changes"}
    )
    result.changes = changes
    return result


def _full_cell_tooltip(text: str, extra: str = "") -> str:
    """Tooltip that shows the full cell text, wrapping long values."""
    shown = str(text if text is not None else "")
    hint = str(extra or "").strip()
    if not shown and not hint:
        return ""
    body = f"{shown}\n\n{hint}" if shown and hint else (shown or hint)
    return f"<p style='white-space:pre-wrap; margin:0'>{escape(body)}</p>"


def _text_item(text: str, clip_id: str | None = None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    tip = _full_cell_tooltip(text)
    if tip:
        item.setToolTip(tip)
    if clip_id is not None:
        item.setData(Qt.ItemDataRole.UserRole, clip_id)
    return item


def _watch_cell_tooltip(widget: QComboBox, extra: str = "") -> None:
    def refresh(*_args: object) -> None:
        widget.setToolTip(_full_cell_tooltip(widget.currentText(), extra))
        for index in range(widget.count()):
            widget.setItemData(index, _full_cell_tooltip(widget.itemText(index)), Qt.ItemDataRole.ToolTipRole)

    refresh()
    widget.currentIndexChanged.connect(refresh)


def _notes_matching_text(notes: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Substring fallback used only when Anki's search cannot be called."""
    needle = " ".join(str(query or "").casefold().split())
    if not needle:
        return []
    found: list[dict[str, Any]] = []
    for note in notes:
        parts = [
            str(note.get("sort_field_value") or ""),
            str(note.get("deck_name") or ""),
            str(note.get("note_id") or ""),
            str(note.get("model_name") or ""),
        ]
        fields = note.get("fields")
        if not isinstance(fields, dict):
            fields = {}
        parts.extend(str(value or "") for value in fields.values())
        if needle in " ".join(parts).casefold():
            found.append(note)
    return found


def note_label(
    note: dict[str, Any],
    score: float | None = None,
    identity: list[dict[str, str]] | None = None,
) -> str:
    return format_note_label(note, identity, score)


class SuggestionCombo(QComboBox):
    """Shows the chosen note immediately and fills suggestions only when opened."""

    def __init__(
        self,
        *,
        suggestions: list[ImportCandidate],
        current_id: int | None,
        current_label: str,
        suggestion_total: int,
        identity: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__()
        self._suggestions = suggestions
        self._current_id = current_id
        self._current_label = current_label or "(no note)"
        self._suggestion_total = suggestion_total
        self._identity = identity
        self._ready = False
        self.setMaxVisibleItems(24)
        self.addItem(self._current_label, current_id)
        self._match_hint = (
            f"Showing the {len(suggestions)} best matches of {suggestion_total}. "
            "Use the ... button to search every note in the checked decks."
            if suggestion_total > len(suggestions)
            else "Suggested notes. Use the ... button to search every note in the checked decks."
        )
        self._apply_tooltip()
        self.currentIndexChanged.connect(lambda _index: self._apply_tooltip())

    def _apply_tooltip(self) -> None:
        shown = self.currentText() or self._current_label
        self.setToolTip(_full_cell_tooltip(shown, self._match_hint))
        for index in range(self.count()):
            self.setItemData(index, _full_cell_tooltip(self.itemText(index)), Qt.ItemDataRole.ToolTipRole)

    def showPopup(self) -> None:  # type: ignore[override]
        if not self._ready:
            self._ready = True
            current = self.currentData()
            self.blockSignals(True)
            self.clear()
            self.addItem("(no note)", None)
            shown: set[int] = set()
            for candidate in self._suggestions:
                note_id = int(candidate.note_id)
                self.addItem(
                    note_label(
                        {
                            "note_id": note_id,
                            "sort_field_value": candidate.sort_field_value,
                            "sort_field_name": candidate.sort_field_name,
                            "deck_name": candidate.deck_name,
                            "fields": candidate.field_values,
                        },
                        candidate.score,
                        self._identity,
                    ),
                    note_id,
                )
                shown.add(note_id)
            if self._current_id and int(self._current_id) not in shown:
                self.addItem(self._current_label, int(self._current_id))
            found = self.findData(current)
            self.setCurrentIndex(found if found >= 0 else 0)
            self.blockSignals(False)
            self._apply_tooltip()
        super().showPopup()


class NoteSearchDialog(QDialog):
    _RESULT_LIMIT = 200

    def __init__(
        self,
        parent: QWidget | None,
        notes: list[dict[str, Any]],
        clip: dict[str, Any] | None = None,
        identity: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose target note")
        self.note_id: int | None = None
        self._notes = list(notes)
        self._identity = identity
        layout = QVBoxLayout(self)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Anki browser search, limited to the checked decks")
        self.query.setToolTip(
            "Same search as the deck browser. "
            "Click a clip field to insert it as one search term. "
            "Separate terms are combined with AND."
        )
        self.query.setClearButtonEnabled(True)
        self.query.setMaxLength(1_000_000)
        layout.addWidget(self.query)
        self._add_field_buttons(layout, clip)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.results = QListWidget()
        layout.addWidget(self.results, 1)
        buttons = QHBoxLayout()
        choose = QPushButton("Use selected note")
        choose.clicked.connect(self._accept_current)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addStretch(1)
        buttons.addWidget(choose)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(lambda: self._filter(self.query.text()))
        self.query.textChanged.connect(lambda _text: self._search_timer.start())
        self.results.itemDoubleClicked.connect(lambda _item: self._accept_current())
        self._filter("")
        self.query.setFocus()
        apply_dialog_screen_constraints(self, preferred_width=760, preferred_height=520, minimum_width=520, minimum_height=360)

    def _add_field_buttons(self, layout: QVBoxLayout, clip: dict[str, Any] | None) -> None:
        fields = clip_search_fields(clip)
        if not fields:
            return
        layout.addWidget(QLabel("Insert from this clip:"))
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for label, value in fields:
            button = QPushButton(label)
            button.setAutoDefault(False)
            preview = value if len(value) <= 500 else value[:497] + "..."
            button.setToolTip(escape(f"{label}\n\n{preview}\n\nInserts this text into the search."))
            button.clicked.connect(lambda _checked=False, field_value=value: self._insert_field(field_value))
            row.addWidget(button)
        row.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(row_widget)
        scroll.setFixedHeight(max(44, row_widget.sizeHint().height() + 18))
        layout.addWidget(scroll)

    def _insert_field(self, value: str) -> None:
        term = search_term_for_field(value)
        if not term:
            return
        updated, cursor = insert_search_term(
            self.query.text(),
            term,
            self.query.cursorPosition(),
            self.query.selectionStart(),
            self.query.selectionEnd(),
        )
        self.query.setText(updated)
        self.query.setCursorPosition(cursor)
        self.query.setFocus()
        self._search_timer.stop()
        self._filter(updated)

    def _filter(self, text: str) -> None:
        query = str(text or "").strip()
        self.results.clear()
        total = len(self._notes)
        if not query:
            self.status.setText(
                f"Type to search {total} notes in the checked decks, or insert a clip field. "
                "This uses Anki's browser search."
            )
            return
        try:
            matched_ids = find_note_ids_for_query(query)
        except Exception as exc:
            self.status.setText(f"Anki could not run that search: {exc}")
            return
        if matched_ids is None:
            shown_notes = _notes_matching_text(self._notes, query)
            engine = "field text only; Anki search is unavailable"
        else:
            shown_notes = [note for note in self._notes if int(note.get("note_id") or 0) in matched_ids]
            engine = "Anki browser search, limited to the checked decks"
        limit = self._RESULT_LIMIT
        for note in shown_notes[:limit]:
            item = QListWidgetItem(note_label(note, identity=self._identity))
            item.setData(Qt.ItemDataRole.UserRole, int(note.get("note_id") or 0))
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)
        extra = "" if len(shown_notes) <= limit else f" Showing the first {limit}."
        self.status.setText(f"{len(shown_notes)} match(es). {engine}.{extra}")

    def _accept_current(self) -> None:
        item = self.results.currentItem()
        if item is None:
            return
        self.note_id = int(item.data(Qt.ItemDataRole.UserRole) or 0) or None
        if self.note_id:
            self.accept()


class ImportDialog(QDialog):
    def __init__(self, parent: QWidget | None, *, clips: list[dict[str, Any]], library_root: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import clip library")
        self.config = load_config()
        self.clips = clips
        self.library_root = library_root
        self.notes: list[dict[str, Any]] = []
        self.decisions: list[ImportDecision] = []
        self.overrides: dict[str, int | None] = {}
        self.skipped_ids: set[str] = set()
        self.overwrite_ids: set[str] = set()
        self._loading = False
        self._busy = False
        self._replan_again = False
        self._apply_pending = False
        self._notes_key: tuple[str, tuple[str, ...]] | None = None
        apply_dialog_screen_constraints(self, preferred_width=1180, preferred_height=820)

        layout = QVBoxLayout(self)
        self._source_samples = source_samples(self.clips)
        self.import_editor = ImportNoteTypeEditor(
            self.config,
            on_changed=self._on_import_setup_changed,
            sample_provider=self._note_identity_samples,
        )

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        intro = QLabel(
            "Choose the note type in this profile that should receive the clips. "
            "Rules guess which existing note receives each clip. "
            "The clip goes into the first import field set whose mapped fields are all empty. "
            "Import copies are written only when those target fields are empty too. "
            "If any field that would be written already has data, that destination is skipped. "
            "You can still choose Import (overwrite) when that skip is because a field already has data. "
            "Sentence, secondary, miscinfo, and any extra rows are written only when those slots name a field. "
            "Nothing is written until you apply."
        )
        intro.setWordWrap(True)
        left_layout.addWidget(intro)

        form = QFormLayout()
        form.addRow("Import note type", self.import_editor.note_type)
        identity_row = QHBoxLayout()
        clip_identity = QPushButton("Clip identity...")
        clip_identity.setToolTip("Names shown for clips in the mapping preview and the library viewer.")
        clip_identity.clicked.connect(self._edit_clip_identity)
        identity_row.addWidget(clip_identity)
        identity_row.addWidget(self.import_editor.note_identity_button)
        identity_row.addStretch(1)
        form.addRow("Identifiers", identity_row)
        self.clip_identity_preview = QLabel()
        self.clip_identity_preview.setWordWrap(True)
        form.addRow("Clip example", self.clip_identity_preview)
        form.addRow("Note example", self.import_editor.note_preview)
        self.minimum = QComboBox()
        for label, value in POINT_CHOICES:
            self.minimum.addItem(label, value)
        minimum = str(self.config.get("import_minimum") or "medium")
        index = self.minimum.findData(minimum)
        self.minimum.setCurrentIndex(index if index >= 0 else 1)
        self.minimum.currentIndexChanged.connect(self._queue_rebuild)
        form.addRow("Minimum score", self.minimum)
        left_layout.addLayout(form)

        left_layout.addWidget(QLabel("Match rules"))
        self.rules_box = QVBoxLayout()
        left_layout.addLayout(self.rules_box)
        add_rule = QPushButton("Add rule")
        add_rule.clicked.connect(lambda: self._add_rule_row())
        left_layout.addWidget(add_rule, alignment=Qt.AlignmentFlag.AlignLeft)

        left_layout.addWidget(QLabel("Also copy (optional)"))
        self.copies_box = QVBoxLayout()
        left_layout.addLayout(self.copies_box)
        add_copy = QPushButton("Add copy")
        add_copy.clicked.connect(lambda: self._add_copy_row())
        left_layout.addWidget(add_copy, alignment=Qt.AlignmentFlag.AlignLeft)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Clip", "Target note", "Why", "Field set", "Will write", "Action"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.viewport().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.viewport().customContextMenuRequested.connect(self._table_menu)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        left_layout.addWidget(self.status)
        left_layout.addWidget(self.table, 2)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMaximumHeight(120)
        left_layout.addWidget(self.report)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("Target decks"))
        deck_hint = QLabel("Subdecks are nested under their parents. Check each deck you want; a parent does not include its subdecks.")
        deck_hint.setWordWrap(True)
        right_layout.addWidget(deck_hint)
        self.decks = QTreeWidget()
        self._loading = True
        fill_deck_tree(
            self.decks,
            list_deck_names(),
            checkable=True,
            checked=set(self.config.get("import_decks") or []),
        )
        self._loading = False
        self.decks.itemChanged.connect(self._on_deck_changed)
        right_layout.addWidget(self.decks, 1)
        right_layout.addWidget(self.import_editor.field_panel, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        refresh = QPushButton("Recompute matches")
        refresh.clicked.connect(self._rebuild)
        apply = QPushButton("Apply import")
        apply.clicked.connect(self._apply)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons.addWidget(refresh)
        buttons.addStretch(1)
        buttons.addWidget(apply)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        self._loading = True
        self._refresh_clip_identity_preview()
        for rule in self.config.get("import_rules") or []:
            self._add_rule_row(rule)
        for copy in self.config.get("import_copies") or []:
            self._add_copy_row(copy)
        self._loading = False
        self._rebuild()

    def _target_fields(self) -> list[str]:
        return list_model_fields(str(self.config.get("import_note_type") or ""))

    def _on_import_setup_changed(self) -> None:
        if self._loading:
            return
        self.import_editor.config = self.config
        self._refresh_rule_targets()
        self._queue_replan()

    def _note_identity_samples(self) -> tuple[dict[str, str], str | None]:
        from .identity import note_identity_samples

        if not hasattr(self, "decks") or not self._selected_decks():
            return {}, NOTE_SAMPLE_PLACEHOLDER
        self._load_notes()
        notes = self._checked_notes()
        if not notes:
            return {}, NOTE_SAMPLE_PLACEHOLDER
        return note_identity_samples(notes, self._target_fields()), None

    def _refresh_clip_identity_preview(self) -> None:
        samples = clip_identity_samples(self.clips)
        self.clip_identity_preview.setText(
            identity_example_line(list(self.config.get("clip_identity") or []), samples)
        )
        self.clip_identity_preview.setToolTip(self.clip_identity_preview.text() or SAMPLE_PLACEHOLDER)

    def _edit_clip_identity(self) -> None:
        from .identity import clip_identity_choices

        samples = clip_identity_samples(self.clips)
        choices = ensure_identity_choices(clip_identity_choices(self.clips), self.config.get("clip_identity"))
        updated = edit_identity(
            self,
            "Clip identity",
            list(self.config.get("clip_identity") or []),
            choices,
            samples,
        )
        if updated is None:
            return
        self.config["clip_identity"] = updated
        self.config = save_config(self.config)
        self.import_editor.config = self.config
        self._refresh_clip_identity_preview()
        self._queue_replan()

    def _sources(self) -> list[tuple[str, str]]:
        catalog = source_catalog(self.clips)
        return catalog or [("video_filename", "Video filename")]

    def _fill_source(self, combo: QComboBox, current: str) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("(choose)", "")
        sources = list(self._sources())
        known = {source_id for source_id, _label in sources}
        if current and current not in known:
            label = "Sort field text" if current == SORT_FIELD_ID else current
            sources.append((current, label))
        for source_id, label in sources:
            combo.addItem(label, source_id)
        found = combo.findData(current)
        combo.setCurrentIndex(found if found >= 0 else 0)
        combo.blockSignals(False)
        apply_combo_example_tooltips(combo, self._source_samples, missing=SAMPLE_PLACEHOLDER)

    def _refresh_rule_targets(self) -> None:
        for combos in self._row_combos(self.rules_box):
            if len(combos) >= 3:
                self._fill_target(combos[2], str(combos[2].currentData() or ""), allow_skip=False)
        for combos in self._row_combos(self.copies_box):
            if len(combos) >= 2:
                self._fill_target(combos[1], str(combos[1].currentData() or ""), allow_skip=True)

    def _fill_target(self, combo: QComboBox, current: str, *, allow_skip: bool) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(DONT_IMPORT if allow_skip else "(choose field)", "")
        combo.addItem("Sort field", SORT_FIELD_ID)
        for name in self._target_fields():
            if name == SORT_FIELD_ID:
                continue
            combo.addItem(name, name)
        if current:
            found = combo.findData(current)
            if found < 0:
                combo.addItem(current, current)
                found = combo.findData(current)
            combo.setCurrentIndex(found)
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _fill_points(self, combo: QComboBox, current: str) -> None:
        combo.blockSignals(True)
        combo.clear()
        for label, value in POINT_CHOICES:
            combo.addItem(label, value)
        found = combo.findData(current or "medium")
        combo.setCurrentIndex(found if found >= 0 else 1)
        combo.blockSignals(False)

    def _fill_compare(self, combo: QComboBox, current: str) -> None:
        combo.blockSignals(True)
        combo.clear()
        for label, value in COMPARE_CHOICES:
            combo.addItem(label, value)
        found = combo.findData(current or "exact")
        combo.setCurrentIndex(found if found >= 0 else 0)
        combo.blockSignals(False)

    def _add_rule_row(self, rule: dict[str, Any] | None = None) -> None:
        rule = rule or {}
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        source = QComboBox()
        compare = QComboBox()
        target = QComboBox()
        points = QComboBox()
        self._fill_source(source, str(rule.get("source") or ""))
        self._fill_compare(compare, str(rule.get("compare") or "exact"))
        self._fill_target(target, str(rule.get("target_field") or ""), allow_skip=False)
        self._fill_points(points, str(rule.get("points") or "medium"))
        for widget in (source, compare, target, points):
            widget.currentIndexChanged.connect(self._queue_rebuild)
            layout.addWidget(widget)
        remove = icon_button("minus", "Remove rule")
        remove.clicked.connect(lambda _=False, widget=row: self._remove_row(self.rules_box, widget))
        layout.addWidget(remove)
        self.rules_box.addWidget(row)

    def _add_copy_row(self, copy: dict[str, Any] | None = None) -> None:
        copy = copy or {}
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        source = QComboBox()
        target = QComboBox()
        self._fill_source(source, str(copy.get("source") or ""))
        self._fill_target(target, str(copy.get("target_field") or ""), allow_skip=True)
        for widget in (source, target):
            widget.currentIndexChanged.connect(self._queue_rebuild)
            layout.addWidget(widget)
        remove = icon_button("minus", "Remove copy")
        remove.clicked.connect(lambda _=False, widget=row: self._remove_row(self.copies_box, widget))
        layout.addWidget(remove)
        self.copies_box.addWidget(row)

    def _remove_row(self, box: QVBoxLayout, widget: QWidget) -> None:
        box.removeWidget(widget)
        widget.deleteLater()
        self._queue_rebuild()

    def _row_combos(self, box: QVBoxLayout) -> list[list[QComboBox]]:
        rows: list[list[QComboBox]] = []
        for index in range(box.count()):
            widget = box.itemAt(index).widget()
            if widget is None:
                continue
            rows.append(widget.findChildren(QComboBox))
        return rows

    def _selected_decks(self) -> list[str]:
        return checked_deck_names(self.decks)

    def _on_deck_changed(self, _item, _column: int) -> None:
        if self._loading:
            return
        self._notes_key = None
        self._queue_replan()

    def _collect_rules(self) -> list[dict[str, str]]:
        rules: list[dict[str, str]] = []
        for combos in self._row_combos(self.rules_box):
            if len(combos) < 4:
                continue
            rules.append(
                {
                    "source": str(combos[0].currentData() or ""),
                    "compare": str(combos[1].currentData() or "exact"),
                    "target_field": str(combos[2].currentData() or ""),
                    "points": str(combos[3].currentData() or "medium"),
                }
            )
        return rules

    def _collect_copies(self) -> list[dict[str, str]]:
        copies: list[dict[str, str]] = []
        for combos in self._row_combos(self.copies_box):
            if len(combos) < 2:
                continue
            copies.append(
                {
                    "source": str(combos[0].currentData() or ""),
                    "target_field": str(combos[1].currentData() or ""),
                }
            )
        return copies

    def _queue_rebuild(self) -> None:
        self._queue_replan()

    def _queue_replan(self) -> None:
        if self._loading:
            return
        self._save_setup()
        if self._busy:
            self._replan_again = True
            return
        QTimer.singleShot(0, self._replan)

    def _save_setup(self) -> None:
        if self._loading:
            return
        self.import_editor.config = self.config
        self.import_editor.collect()
        self.config["import_mapping_initialized"] = True
        self.config["import_minimum"] = str(self.minimum.currentData() or "medium")
        self.config["import_decks"] = self._selected_decks()
        self.config["import_rules"] = self._collect_rules()
        self.config["import_copies"] = self._collect_copies()
        self.config["import_mode"] = "match"
        self.config = save_config(self.config)
        self.import_editor.config = self.config

    def _checked_notes(self) -> list[dict[str, Any]]:
        selected = set(self._selected_decks())
        return [note for note in self.notes if str(note.get("deck_name") or "") in selected]

    def _load_notes(self) -> None:
        note_type = str(self.config.get("import_note_type") or "")
        decks = tuple(self._selected_decks())
        key = (note_type, decks)
        if key == self._notes_key:
            return
        with timed(f"loading target notes ({len(decks)} decks)"):
            self.notes = snapshot_notes_of_type(note_type, decks)
        self._notes_key = key
        log(f"loaded {len(self.notes)} target notes")

    def _rebuild(self) -> None:
        self._notes_key = None
        self._replan()

    def _match_progress(self, current: int, total: int) -> None:
        from aqt import mw

        mw.progress.update(label=f"Matching clips {current}/{total}", value=current, max=total)

    def _update_match_progress(self, current: int, total: int) -> None:
        if self.isVisible():
            self.status.setText(f"Matching clips {current}/{total}...")

    def _replan(self) -> None:
        if self._busy:
            self._replan_again = True
            return
        self._busy = True
        self._save_setup()
        self.import_editor.refresh_note_preview()
        note_type = str(self.config.get("import_note_type") or "")
        selected_decks = tuple(self._selected_decks())
        config = dict(self.config)
        clips = list(self.clips)
        overrides = dict(self.overrides)
        skipped_ids = set(self.skipped_ids)
        from aqt import mw

        def progress(current: int, total: int) -> None:
            mw.taskman.run_on_main(lambda: self._update_match_progress(current, total))

        def operation(col):
            with timed("building mapping preview"):
                notes = snapshot_notes_of_type(note_type, selected_decks, collection=col)
                with timed("planning import matches"):
                    decisions = plan_imports(
                        clips,
                        notes,
                        config,
                        overrides,
                        skipped_ids,
                        progress=progress,
                        overwrite_ids=set(self.overwrite_ids),
                    )
                return notes, decisions

        def success(result) -> None:
            notes, decisions = result
            self.notes = notes
            self._notes_key = (note_type, selected_decks)
            self.decisions = decisions
            self._fill_table(notes)
            self._busy = False
            if self._replan_again:
                self._replan_again = False
                QTimer.singleShot(0, self._replan)
            elif self._apply_pending:
                self._apply_pending = False
                QTimer.singleShot(0, self._start_apply)

        def failure(error: Exception) -> None:
            self._busy = False
            self._apply_pending = False
            self.status.setText(f"Could not build mapping preview: {error}")
            if self._replan_again:
                self._replan_again = False
                QTimer.singleShot(0, self._replan)

        QueryOp(parent=mw, op=operation, success=success).failure(failure).with_progress().run_in_background()

    def _note_label(self, note: dict[str, Any]) -> str:
        return note_label(note, identity=self.config.get("import_note_identity"))

    def _why(self, decision: ImportDecision) -> str:
        if decision.conflict_resolution == "tie":
            text = "tie — choose a note"
        elif decision.conflict_resolution == "no_match" and not decision.note_id:
            text = "no match"
        elif decision.signals.get("chosen") == 0 and decision.note_id:
            text = "chosen by you"
        elif not decision.signals:
            text = ""
        else:
            text = ", ".join(f"{name} {value:g}" for name, value in decision.signals.items())
        if decision.suggestion_total > len(decision.suggestions):
            extra = f"top {len(decision.suggestions)} of {decision.suggestion_total}"
            text = f"{text}; {extra}" if text else extra
        return text

    def _slot_label(self, decision: ImportDecision) -> str:
        if not decision.target_field_set_index:
            if decision.conflict_resolution == "no_slot":
                return "no empty field set"
            if decision.conflict_resolution == "occupied_copy":
                return "copy target already has data"
            return ""
        for field_set in enabled_import_field_sets(self.config):
            if field_set.index == decision.target_field_set_index:
                return field_set.name or default_field_set_name(field_set.index)
        return str(decision.target_field_set_index)

    def _fill_table(self, notes: list[dict[str, Any]]) -> None:
        self._loading = True
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self.decisions))
        notes_by_id = {int(note.get("note_id") or 0): note for note in notes}
        field_sets = list(enabled_import_field_sets(self.config))
        identity = self.config.get("clip_identity")
        note_identity = self.config.get("import_note_identity")
        copies = self.config.get("import_copies")
        for row, decision in enumerate(self.decisions):
            clip_text = decision.label or clip_label({"id": decision.clip_id}, identity)
            self.table.setItem(row, 0, _text_item(clip_text, decision.clip_id))
            current_id = int(decision.note_id) if decision.note_id else None
            current_note = notes_by_id.get(current_id or 0)
            current_label = self._note_label(current_note) if current_note else "(no note)"
            note_box = SuggestionCombo(
                suggestions=list(decision.suggestions),
                current_id=current_id,
                current_label=current_label,
                suggestion_total=int(decision.suggestion_total or 0),
                identity=note_identity,
            )
            note_box.currentIndexChanged.connect(
                lambda _index, clip_id=decision.clip_id, widget=note_box: self._set_note(clip_id, widget)
            )
            holder = QWidget()
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.setSpacing(2)
            holder_layout.addWidget(note_box, 1)
            browse = QPushButton("...")
            browse.setFixedWidth(42)
            browse.setStyleSheet("QPushButton { padding: 0px; margin: 0px; font-size: 13px; }")
            browse.setToolTip("Search all notes in the checked decks")
            browse.setAccessibleName("Search the rest")
            browse.clicked.connect(lambda _checked=False, clip_id=decision.clip_id: self._browse_note(clip_id))
            holder_layout.addWidget(browse)
            self._forward_row_menu(holder, row)
            self._forward_row_menu(note_box, row)
            self._forward_row_menu(browse, row)
            self.table.setCellWidget(row, 1, holder)
            why = self._why(decision)
            slot = self._slot_label(decision)
            self.table.setItem(row, 2, _text_item(why))
            self.table.setItem(row, 3, _text_item(slot))
            field_set = next((item for item in field_sets if item.index == decision.target_field_set_index), None)
            writes = describe_writes(field_set, copies) if field_set else ""
            self.table.setItem(row, 4, _text_item(writes))
            action = QComboBox()
            action.blockSignals(True)
            action.addItem(import_action_label(decision), "import")
            action.addItem("Skip", "skip")
            can_import = bool(decision.note_id) and (
                bool(decision.target_field_set_index)
                or decision.conflict_resolution in {"no_slot", "occupied_copy"}
            )
            action.setCurrentIndex(0 if decision.action == "import" and can_import else 1)
            action.blockSignals(False)
            action.currentIndexChanged.connect(
                lambda _index, clip_id=decision.clip_id, widget=action: self._set_action(clip_id, widget)
            )
            _watch_cell_tooltip(action)
            self._forward_row_menu(action, row)
            self.table.setCellWidget(row, 5, action)
        self.table.setUpdatesEnabled(True)
        self._loading = False
        if not str(self.config.get("import_note_type") or "").strip():
            self.status.setText("Choose an import note type. Export settings are not used.")
        elif not enabled_import_field_sets(self.config):
            self.status.setText("Choose a video field for the import note type before clips can be written.")
        elif not self._selected_decks():
            self.status.setText("Choose at least one target deck, then recompute matches.")
        elif not notes:
            self.status.setText("No notes of this note type were found in the chosen decks.")
        else:
            matched = sum(1 for decision in self.decisions if decision.note_id)
            self.status.setText(
                f"{len(self.decisions)} clips against {len(notes)} notes, {matched} with a target. "
                "Dropdowns list the best matches. Use the ... button to search the rest."
            )

    def _forward_row_menu(self, widget: QWidget, row: int) -> None:
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        def show(pos, widget=widget, row=row) -> None:
            self._show_row_menu(row, widget.mapToGlobal(pos))

        widget.customContextMenuRequested.connect(show)

    def _table_menu(self, pos) -> None:
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        self._show_row_menu(index.row(), self.table.viewport().mapToGlobal(pos))

    def _show_row_menu(self, row: int, global_pos) -> None:
        if row < 0:
            return
        selected_rows = {item.row() for item in self.table.selectionModel().selectedRows()}
        if row not in selected_rows:
            self.table.selectRow(row)
        menu = QMenu(self.table)
        skip_action = menu.addAction("Skip")
        selected = [decision for decision in self.decisions if decision.clip_id in set(self._selected_clip_ids())]
        import_label = "Import (overwrite)" if any(import_action_label(decision) == "Import (overwrite)" for decision in selected) else "Import"
        import_action = menu.addAction(import_label)
        chosen = menu.exec(global_pos)
        if chosen is skip_action:
            self._set_selected_action("skip")
        elif chosen is import_action:
            self._set_selected_action("import")

    def _selected_clip_ids(self) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), 0)
            clip_id = str(item.data(Qt.ItemDataRole.UserRole) or "") if item is not None else ""
            if clip_id and clip_id not in seen:
                seen.add(clip_id)
                ids.append(clip_id)
        return ids

    def _set_selected_action(self, action: str) -> None:
        if self._loading or self._busy:
            return
        clip_ids = self._selected_clip_ids()
        if not clip_ids:
            return
        for clip_id in clip_ids:
            self._remember_action(clip_id, action)
        self._queue_replan()

    def _set_note(self, clip_id: str, widget: QComboBox) -> None:
        if self._loading:
            return
        note_id = widget.currentData()
        self.overrides[clip_id] = int(note_id) if note_id else None
        self._queue_replan()

    def _browse_note(self, clip_id: str) -> None:
        if self._busy:
            return
        clip = next((item for item in self.clips if str(item.get("id") or "") == clip_id), None)
        dialog = NoteSearchDialog(self, self._checked_notes(), clip, self.config.get("import_note_identity"))
        if not dialog.exec() or not dialog.note_id:
            return
        self.overrides[clip_id] = dialog.note_id
        self._queue_replan()

    def _remember_action(self, clip_id: str, action: str) -> None:
        if action == "skip":
            self.skipped_ids.add(clip_id)
            self.overwrite_ids.discard(clip_id)
            return
        self.skipped_ids.discard(clip_id)
        decision = next((item for item in self.decisions if item.clip_id == clip_id), None)
        if decision is not None and import_action_label(decision) == "Import (overwrite)":
            self.overwrite_ids.add(clip_id)
        else:
            self.overwrite_ids.discard(clip_id)

    def _set_action(self, clip_id: str, widget: QComboBox) -> None:
        if self._loading:
            return
        chosen = "skip" if str(widget.currentData() or "skip") == "skip" else "import"
        self._remember_action(clip_id, chosen)
        self._queue_replan()

    def _apply(self) -> None:
        if self._busy:
            return
        self._save_setup()
        if not str(self.config.get("import_note_type") or "").strip():
            QMessageBox.information(self, "Clip Library", "Choose an import note type before applying.")
            return
        if not enabled_import_field_sets(self.config):
            QMessageBox.information(self, "Clip Library", "Choose a video field on the import note type before applying.")
            return
        self._apply_pending = True
        self._replan()

    def _start_apply(self) -> None:
        from aqt import mw

        decisions = list(self.decisions)
        config = dict(self.config)
        library_root = self.library_root
        self._busy = True

        def operation(col):
            with timed("importing collection"):
                summary = apply_import_decisions(
                    clips_by_id={str(clip.get("id") or ""): clip for clip in self.clips},
                    decisions=decisions,
                    config=config,
                    library_root=library_root,
                    collection=col,
                )
            return _collection_op_result(summary)

        def success(result) -> None:
            self._busy = False
            lines = [
                f"Updated notes: {result['updated']}",
                f"Skipped: {result['skipped']}",
                f"Conflicts: {result['conflicts']}",
            ]
            if result["warnings"]:
                lines.append("")
                lines.extend(f"- {warning}" for warning in result["warnings"])
            tooltip("Clip library import finished.")
            self._notes_key = None
            self.report.setPlainText("\n".join(lines))
            self._replan()

        def failure(error: Exception) -> None:
            self._busy = False
            QMessageBox.critical(self, "Clip Library", f"Clip library import failed: {error}")

        CollectionOp(parent=mw, op=operation).success(success).failure(failure).run_in_background()
