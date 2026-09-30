from __future__ import annotations

from typing import Any

from aqt.qt import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .identity import SAMPLE_PLACEHOLDER, identity_example_line
from .ui_common import apply_combo_example_tooltips, apply_dialog_screen_constraints


class IdentityDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        parts: list[dict[str, str]],
        choices: list[tuple[str, str]],
        samples: dict[str, str] | None = None,
        sample_unavailable: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._choices = list(choices)
        self._samples = dict(samples or {})
        self._unavailable = sample_unavailable
        self._sources = [str(part.get("source") or "") for part in parts if isinstance(part, dict) and part.get("source")]
        if not self._sources:
            self._sources = ["", ""]
        self._rows: list[QComboBox] = []
        apply_dialog_screen_constraints(self, preferred_width=560, preferred_height=420, minimum_width=420, minimum_height=280)

        layout = QVBoxLayout(self)
        hint = QLabel(
            "Parts are shown from first to last. Empty or missing values are skipped when a name is built. "
            "The example below uses values from the collection being worked with."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._preview = QLabel()
        self._preview.setWordWrap(True)
        layout.addWidget(self._preview)
        self._rows_layout = QVBoxLayout()
        layout.addLayout(self._rows_layout)
        add = QPushButton("Add field")
        add.clicked.connect(self._add_source)
        layout.addWidget(add)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Save")
        ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)
        self._rebuild()

    def parts(self) -> list[dict[str, str]]:
        self._capture()
        return [{"source": source} for source in self._sources if source]

    def _add_source(self) -> None:
        self._capture()
        self._sources.append("")
        self._rebuild()

    def _set_source(self, index: int, combo: QComboBox) -> None:
        if 0 <= index < len(self._sources):
            self._sources[index] = str(combo.currentData() or "")
        self._refresh_preview()

    def _move(self, index: int, delta: int) -> None:
        self._capture()
        target = index + delta
        if target < 0 or target >= len(self._sources):
            return
        self._sources[index], self._sources[target] = self._sources[target], self._sources[index]
        self._rebuild()

    def _remove(self, index: int) -> None:
        self._capture()
        if 0 <= index < len(self._sources):
            del self._sources[index]
        self._rebuild()

    def _capture(self) -> None:
        for index, combo in enumerate(self._rows):
            if index < len(self._sources):
                self._sources[index] = str(combo.currentData() or "")

    def _example_map(self) -> dict[str, str]:
        if self._unavailable:
            return {source: self._unavailable for source, _label in self._choices}
        return {source: self._samples.get(source) or SAMPLE_PLACEHOLDER for source, _label in self._choices}

    def _refresh_preview(self) -> None:
        parts = [{"source": source} for source in self._sources if source]
        self._preview.setText(identity_example_line(parts, self._samples, unavailable=self._unavailable))

    def _rebuild(self) -> None:
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows = []
        examples = self._example_map()
        missing = self._unavailable or SAMPLE_PLACEHOLDER
        for index, source in enumerate(self._sources):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            combo = QComboBox()
            combo.addItem("(choose)", "")
            for source_id, label in self._choices:
                combo.addItem(label, source_id)
            if source and combo.findData(source) < 0:
                combo.addItem(source, source)
            found = combo.findData(source)
            combo.setCurrentIndex(found if found >= 0 else 0)
            apply_combo_example_tooltips(combo, examples, missing=missing)
            combo.currentIndexChanged.connect(lambda _index, row_index=index, box=combo: self._set_source(row_index, box))
            up = QPushButton("Up")
            down = QPushButton("Down")
            remove = QPushButton("Remove")
            up.setEnabled(index > 0)
            down.setEnabled(index < len(self._sources) - 1)
            up.clicked.connect(lambda _checked=False, row_index=index: self._move(row_index, -1))
            down.clicked.connect(lambda _checked=False, row_index=index: self._move(row_index, 1))
            remove.clicked.connect(lambda _checked=False, row_index=index: self._remove(row_index))
            row_layout.addWidget(combo, 1)
            row_layout.addWidget(up)
            row_layout.addWidget(down)
            row_layout.addWidget(remove)
            self._rows_layout.addWidget(row)
            self._rows.append(combo)
        self._refresh_preview()


def edit_identity(
    parent: QWidget | None,
    title: str,
    parts: list[dict[str, Any]],
    choices: list[tuple[str, str]],
    samples: dict[str, str] | None = None,
    sample_unavailable: str | None = None,
) -> list[dict[str, str]] | None:
    dialog = IdentityDialog(
        parent,
        title,
        [part for part in parts if isinstance(part, dict)],
        choices,
        samples,
        sample_unavailable,
    )
    if not dialog.exec():
        return None
    return dialog.parts()
