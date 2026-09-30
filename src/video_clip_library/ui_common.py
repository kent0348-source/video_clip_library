from __future__ import annotations

from typing import Any

from aqt.qt import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QIcon,
    QLayout,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QPushButton,
    QSize,
    Qt,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from .config import visible_field_choices


def apply_dialog_screen_constraints(
    dialog: QDialog,
    *,
    preferred_width: int,
    preferred_height: int,
    minimum_width: int = 640,
    minimum_height: int = 420,
) -> None:
    screen = QApplication.primaryScreen()
    available = screen.availableGeometry() if screen else None
    if available is None:
        dialog.resize(preferred_width, preferred_height)
        return
    max_width = max(minimum_width, available.width() - 40)
    max_height = max(minimum_height, available.height() - 40)
    dialog.setMinimumSize(min(minimum_width, max_width), min(minimum_height, max_height))
    dialog.resize(min(preferred_width, max_width), min(preferred_height, max_height))


def _palette_text_role() -> Any:
    color_role = getattr(QPalette, "ColorRole", None)
    if color_role is not None and hasattr(color_role, "ButtonText"):
        return color_role.ButtonText
    return QPalette.ButtonText


def plus_minus_icon(kind: str) -> QIcon:
    size = 16
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    color = QApplication.palette().color(_palette_text_role())
    pen = QPen(color)
    pen.setWidth(2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    middle = size // 2
    painter.drawLine(3, middle, size - 4, middle)
    if kind != "minus":
        painter.drawLine(middle, 3, middle, size - 4)
    painter.end()
    return QIcon(pixmap)


def icon_button(kind: str, tooltip: str) -> QPushButton:
    button = QPushButton()
    button.setIcon(plus_minus_icon(kind))
    button.setIconSize(QSize(16, 16))
    button.setFixedSize(32, 32)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    button.setStyleSheet("QPushButton { padding: 0px; margin: 0px; icon-size: 16px; }")
    return button


def fill_deck_tree(tree: QTreeWidget, names: list[str], *, checkable: bool, checked: set[str] | None = None) -> None:
    from .anki_scan import group_deck_names

    tree.clear()
    tree.setHeaderHidden(True)
    tree.setColumnCount(1)
    tree.setUniformRowHeights(True)
    selected = checked or set()

    def add(parent: QTreeWidgetItem, node: dict[str, Any]) -> None:
        item = QTreeWidgetItem(parent)
        item.setText(0, str(node.get("name") or ""))
        full_name = str(node.get("full_name") or "")
        item.setData(0, Qt.ItemDataRole.UserRole, full_name)
        item.setToolTip(0, full_name or str(node.get("name") or ""))
        if checkable:
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            state = Qt.CheckState.Checked if full_name and full_name in selected else Qt.CheckState.Unchecked
            item.setCheckState(0, state)
        for child in node.get("children") or []:
            add(item, child)

    root = tree.invisibleRootItem()
    for node in group_deck_names(names):
        add(root, node)
    if len(names) <= 80:
        tree.expandAll()
    else:
        tree.expandToDepth(0)


def checked_deck_names(tree: QTreeWidget) -> list[str]:
    names: list[str] = []

    def walk(item: QTreeWidgetItem) -> None:
        full_name = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        if full_name and item.checkState(0) == Qt.CheckState.Checked:
            names.append(full_name)
        for index in range(item.childCount()):
            walk(item.child(index))

    root = tree.invisibleRootItem()
    for index in range(root.childCount()):
        walk(root.child(index))
    return names


def select_deck_in_tree(tree: QTreeWidget, full_name: str) -> None:
    wanted = str(full_name or "")

    def walk(item: QTreeWidgetItem) -> bool:
        if wanted and str(item.data(0, Qt.ItemDataRole.UserRole) or "") == wanted:
            tree.setCurrentItem(item)
            tree.scrollToItem(item)
            return True
        for index in range(item.childCount()):
            if walk(item.child(index)):
                return True
        return False

    root = tree.invisibleRootItem()
    for index in range(root.childCount()):
        if walk(root.child(index)):
            return
    first = root.child(0) if root.childCount() else None
    if first is not None:
        tree.setCurrentItem(first)


def fill_field_combo(
    combo: QComboBox,
    current: str,
    fields: list[str],
    used: set[str],
    empty_label: str,
) -> None:
    combo.blockSignals(True)
    combo.clear()
    combo.addItem(empty_label, "")
    for name in visible_field_choices(fields, used, current):
        combo.addItem(name, name)
    if current:
        found = combo.findData(current)
        if found < 0:
            combo.addItem(current, current)
            found = combo.findData(current)
        combo.setCurrentIndex(max(0, found))
    else:
        combo.setCurrentIndex(0)
    combo.blockSignals(False)


def apply_combo_example_tooltips(combo: QComboBox, examples: dict[str, str], *, missing: str) -> None:
    for index in range(combo.count()):
        key = str(combo.itemData(index) or "")
        if not key:
            combo.setItemData(index, "", Qt.ItemDataRole.ToolTipRole)
            continue
        combo.setItemData(index, examples.get(key) or missing, Qt.ItemDataRole.ToolTipRole)

    def refresh(_index: int = 0, box: QComboBox = combo) -> None:
        key = str(box.currentData() or "")
        box.setToolTip(examples.get(key) or (missing if key else ""))

    refresh()
    if not combo.property("exampleTipsBound"):
        combo.currentIndexChanged.connect(refresh)
        combo.setProperty("exampleTipsBound", True)


def wrap_row(*widgets: QWidget) -> QWidget:
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    for widget in widgets:
        layout.addWidget(widget)
    return container


def wrap_layout(layout: QLayout) -> QWidget:
    container = QWidget()
    container.setLayout(layout)
    return container
