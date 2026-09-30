from __future__ import annotations

from aqt import gui_hooks, mw
from aqt.qt import QAction

from .config import load_config
from .ui_export import open_export_deck, open_export_selected
from .ui_settings import SettingsDialog
from .ui_viewer import LibraryViewer


class ClipLibraryController:
    def __init__(self) -> None:
        self.config = load_config()
        self._viewer: LibraryViewer | None = None
        self._install_menu()
        gui_hooks.browser_menus_did_init.append(self._install_browser_menu)

    def _install_menu(self) -> None:
        tools_menu = mw.form.menuTools
        submenu = tools_menu.addMenu("Clip Library")

        configure = QAction("Configure", mw)
        configure.triggered.connect(self.open_settings)
        submenu.addAction(configure)

        viewer = QAction("Library Viewer", mw)
        viewer.triggered.connect(self.open_viewer)
        submenu.addAction(viewer)

    def _install_browser_menu(self, browser) -> None:
        menu = browser.form.menuEdit
        menu.addSeparator()
        export_selected = QAction("Clip Library: Export selected notes...", browser)
        export_selected.triggered.connect(lambda: open_export_selected(browser, browser))
        menu.addAction(export_selected)
        export_deck = QAction("Clip Library: Export deck...", browser)
        export_deck.triggered.connect(lambda: open_export_deck(browser, browser))
        menu.addAction(export_deck)

    def open_settings(self) -> None:
        self.config = load_config()
        dialog = SettingsDialog(mw, self.config)
        dialog.exec()
        self.config = load_config()

    def open_viewer(self) -> None:
        if self._reuse_open_viewer():
            return
        self.config = load_config()
        viewer = LibraryViewer(mw)
        viewer.destroyed.connect(self._clear_viewer)
        self._viewer = viewer
        viewer.show()

    def _reuse_open_viewer(self) -> bool:
        if self._viewer is None:
            return False
        try:
            if self._viewer.isVisible():
                self._viewer.raise_()
                self._viewer.activateWindow()
                return True
        except RuntimeError:
            pass
        self._viewer = None
        return False

    def _clear_viewer(self, *_args) -> None:
        self._viewer = None


def init_addon() -> ClipLibraryController:
    return ClipLibraryController()
