from __future__ import annotations

from typing import Any, Callable

from aqt.qt import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    Qt,
    QVBoxLayout,
    QWidget,
)

from .exclusions import EXPORT_OPERATORS


class ExclusionRulesEditor(QWidget):
    """Rule table shared by export and settings. Callers supply key browsing when they have a deck."""

    def __init__(self, parent: QWidget | None = None, *, on_changed: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self._on_changed = on_changed
        self._loading = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.enabled = QCheckBox("Apply exclusion rules")
        self.enabled.toggled.connect(self._notify)
        layout.addWidget(self.enabled)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["On", "Path", "Operator", "Value", "Match case"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setMinimumHeight(120)
        self.table.itemChanged.connect(lambda *_args: self._notify())
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        add_rule = QPushButton("Add rule")
        add_rule.clicked.connect(lambda: self.add_rule())
        remove_rule = QPushButton("Remove rule")
        remove_rule.clicked.connect(self.remove_selected)
        buttons.addWidget(add_rule)
        buttons.addWidget(remove_rule)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    def set_state(self, *, enabled: bool, rules: list[Any] | None) -> None:
        self._loading = True
        self.enabled.blockSignals(True)
        self.table.blockSignals(True)
        try:
            self.enabled.setChecked(bool(enabled))
            self.table.setRowCount(0)
            for rule in rules or []:
                if isinstance(rule, dict):
                    self._insert_rule(rule)
        finally:
            self.table.blockSignals(False)
            self.enabled.blockSignals(False)
            self._loading = False

    def is_enabled(self) -> bool:
        return self.enabled.isChecked()

    def rules(self) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        for row in range(self.table.rowCount()):
            enabled = self.table.item(row, 0)
            path = self.table.item(row, 1)
            value = self.table.item(row, 3)
            case_item = self.table.item(row, 4)
            operator = self.table.cellWidget(row, 2)
            operator_value = "contains"
            if isinstance(operator, QComboBox):
                operator_value = str(operator.currentData() or "contains")
            collected.append(
                {
                    "enabled": enabled is not None and enabled.checkState() == Qt.CheckState.Checked,
                    "path": path.text().strip() if path is not None else "",
                    "operator": operator_value,
                    "value": value.text() if value is not None else "",
                    "case_sensitive": case_item is not None and case_item.checkState() == Qt.CheckState.Checked,
                }
            )
        return collected

    def add_rule(self, rule: dict[str, Any] | None = None) -> None:
        self.table.blockSignals(True)
        try:
            self._insert_rule(rule or {})
        finally:
            self.table.blockSignals(False)
        if rule is None:
            self._notify()

    def insert_path(self, path: str) -> None:
        text = str(path or "").strip()
        if not text:
            return
        row = self.table.currentRow()
        if row < 0:
            self.table.blockSignals(True)
            try:
                self._insert_rule({"path": text, "operator": "contains", "value": "", "enabled": True})
            finally:
                self.table.blockSignals(False)
            self._notify()
            return
        path_item = self.table.item(row, 1)
        if path_item is None:
            self.table.setItem(row, 1, QTableWidgetItem(text))
        else:
            path_item.setText(text)
        self._notify()

    def remove_selected(self) -> None:
        rows = sorted({item.row() for item in self.table.selectedItems()}, reverse=True)
        if not rows and self.table.currentRow() >= 0:
            rows = [self.table.currentRow()]
        if not rows:
            return
        for row in rows:
            self.table.removeRow(row)
        self._notify()

    def _insert_rule(self, rule: dict[str, Any]) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, self._check_item(bool(rule.get("enabled", True))))
        self.table.setItem(row, 1, QTableWidgetItem(str(rule.get("path") or "")))
        operator = QComboBox()
        current = str(rule.get("operator") or "contains")
        for value, label in (
            ("contains", "contains"),
            ("starts_with", "starts with"),
            ("ends_with", "ends with"),
            ("equals", "equals"),
        ):
            operator.addItem(label, value)
        index = operator.findData(current if current in EXPORT_OPERATORS else "contains")
        operator.setCurrentIndex(index if index >= 0 else 0)
        self.table.setCellWidget(row, 2, operator)
        operator.currentIndexChanged.connect(lambda *_args: self._notify())
        self.table.setItem(row, 3, QTableWidgetItem(str(rule.get("value") if rule.get("value") is not None else "")))
        self.table.setItem(row, 4, self._check_item(bool(rule.get("case_sensitive", False))))

    def _check_item(self, checked: bool) -> QTableWidgetItem:
        item = QTableWidgetItem("")
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        return item

    def _notify(self) -> None:
        if self._loading or self._on_changed is None:
            return
        self._on_changed()
