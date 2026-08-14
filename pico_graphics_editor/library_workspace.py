"""Dedicated management workspace for reusable personal pixel assets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .asset_library import LibraryAsset
from .standard_library import THEME_NAMES, standard_asset_metadata
from .thumbnail_cache import cached_pixel_frame_pixmap
from .ui_help import (
    install_widget_tooltips,
    set_collapsible_group_expanded,
)


@dataclass(frozen=True)
class LibrarySelectionCapabilities:
    """Describe every action allowed for the current catalogue selection."""

    count: int
    personal_count: int
    storage_available: bool

    @property
    def any(self) -> bool:
        return self.count > 0

    @property
    def single(self) -> bool:
        return self.count == 1

    @property
    def can_write_personal(self) -> bool:
        return self.storage_available and self.personal_count > 0


class PersonalAssetLibraryWidget(QWidget):
    """Browse and manage project-independent static and animated assets."""

    add_to_project_requested = Signal(object)
    edit_copy_requested = Signal(str)
    replace_requested = Signal(str)
    duplicate_requested = Signal(object)
    export_requested = Signal(object)
    rename_requested = Signal(str)
    delete_requested = Signal(object)
    import_requested = Signal()
    import_pga_requested = Signal()
    refresh_requested = Signal()
    undo_requested = Signal()
    redo_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        """Build the searchable catalogue, preview, metadata, and actions."""
        super().__init__(parent)
        self.assets: dict[str, LibraryAsset] = {}
        self.personal_asset_ids: set[str] = set()
        self.standard_asset_ids: set[str] = set()
        self._catalogue_items: dict[str, QListWidgetItem] = {}
        self._thumbnail_generation = 0
        self._thumbnail_queue: list[str] = []
        self._storage_error = ""
        self._history_description = ""
        self._build_interface()
        self._connect_signals()
        install_widget_tooltips(self)

    def _build_interface(self) -> None:
        """Build a dedicated library-management layout."""
        root = QVBoxLayout(self)
        import_row = QHBoxLayout()
        self.import_button = QPushButton("Import image…")
        self.import_pga_button = QPushButton("Import PGA images…")
        self.import_pga_button.setToolTip(
            "Recover reusable pixel images from a generated PGA2 or PGA3 resource. "
            "PGA3 WAV entries stay in the resource.\n"
            "Example: Import generated_assets.pga from an exported app."
        )
        self.refresh_button = QPushButton("Refresh")
        import_row.addWidget(self.import_button)
        import_row.addWidget(self.import_pga_button)
        import_row.addStretch(1)
        root.addLayout(import_row)

        self.workflow_hint = QLabel(
            "Choose a reusable master, then add an independent project copy or edit "
            "the complete asset in Pixel Art. Hold Ctrl to select several assets, or "
            "drag a box across empty catalogue space. Enter adds the selection; "
            "Delete removes selected personal assets."
        )
        self.workflow_hint.setWordWrap(True)
        root.addWidget(self.workflow_hint)

        self.state_panel = QFrame()
        self.state_panel.setFrameShape(QFrame.Shape.StyledPanel)
        state_layout = QHBoxLayout(self.state_panel)
        state_layout.setContentsMargins(8, 5, 8, 5)
        self.state_label = QLabel()
        self.state_label.setWordWrap(True)
        self.retry_button = QPushButton("Retry")
        self.undo_button = QPushButton("Undo last change")
        self.redo_button = QPushButton("Redo")
        state_layout.addWidget(self.state_label, 1)
        state_layout.addWidget(self.retry_button)
        state_layout.addWidget(self.undo_button)
        state_layout.addWidget(self.redo_button)
        self.state_panel.setVisible(False)
        root.addWidget(self.state_panel)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        catalogue = QWidget()
        catalogue_layout = QVBoxLayout(catalogue)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter library assets")
        self.search_edit.setClearButtonEnabled(True)
        display_row = QHBoxLayout()
        display_row.addWidget(QLabel("Display"))
        self.display_mode_combo = QComboBox()
        self.display_mode_combo.addItem("Medium thumbnails", "medium")
        self.display_mode_combo.addItem("Large thumbnails", "large")
        self.display_mode_combo.addItem("Compact thumbnails", "compact")
        self.display_mode_combo.addItem("List", "list")
        display_row.addWidget(self.display_mode_combo)
        display_row.addWidget(QLabel("Collection"))
        self.collection_combo = QComboBox()
        self.collection_combo.addItem("All assets", "all")
        self.collection_combo.addItem("Built-in standard", "standard")
        self.collection_combo.addItem("Personal", "personal")
        display_row.addWidget(self.collection_combo)
        display_row.addStretch(1)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Theme"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("All themes", "all")
        self.theme_combo.addItem("Starter icons", "general")
        for theme, theme_name in THEME_NAMES:
            self.theme_combo.addItem(theme_name, theme)
        self.theme_combo.setToolTip(
            "Show one built-in visual theme without changing or deleting assets.\n"
            "Example: Choose Industrial to compare its icons, buttons, widgets, and backgrounds."
        )
        filter_row.addWidget(self.theme_combo)
        filter_row.addWidget(QLabel("Type"))
        self.asset_kind_combo = QComboBox()
        self.asset_kind_combo.addItem("All types", "all")
        self.asset_kind_combo.addItem("Icons", "icon")
        self.asset_kind_combo.addItem("Buttons", "button")
        self.asset_kind_combo.addItem("Widgets", "widget")
        self.asset_kind_combo.addItem("Backgrounds", "background")
        self.asset_kind_combo.setToolTip(
            "Show one reusable asset type within the selected collection and theme.\n"
            "Example: Choose Backgrounds, then select Cardputer Stripes for a 240×135 screen."
        )
        filter_row.addWidget(self.asset_kind_combo)
        filter_row.addStretch(1)
        self.asset_list = QListWidget()
        self.asset_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.asset_list.setIconSize(QSize(88, 88))
        self.asset_list.setGridSize(QSize(154, 132))
        self.asset_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.asset_list.setMovement(QListWidget.Movement.Static)
        self.asset_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.asset_list.setSelectionRectVisible(True)
        self.asset_list.setDragEnabled(False)
        self.asset_list.setWordWrap(True)
        self.asset_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.empty_label = QLabel(
            "No reusable assets yet. Import an image or PGA resource here, or use "
            "Save to Library from Pixel Art or App GUI."
        )
        self.empty_label.setWordWrap(True)
        self.count_label = QLabel("0 assets")
        self.selection_label = QLabel("No selection")
        catalogue_layout.addWidget(self.search_edit)
        catalogue_layout.addLayout(display_row)
        catalogue_layout.addLayout(filter_row)
        catalogue_layout.addWidget(self.asset_list, 1)
        catalogue_layout.addWidget(self.empty_label)
        count_row = QHBoxLayout()
        count_row.addWidget(self.count_label)
        count_row.addStretch(1)
        count_row.addWidget(self.selection_label)
        catalogue_layout.addLayout(count_row)
        self.splitter.addWidget(catalogue)

        details = QWidget()
        details_layout = QVBoxLayout(details)
        self.preview_label = QLabel("Select an asset")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(220, 220)
        self.preview_label.setStyleSheet("background: #202020; border: 1px solid #555;")
        self.name_label = QLabel("No asset selected")
        self.name_label.setStyleSheet("font-weight: 600; font-size: 15px;")
        self.metadata_label = QLabel("—")
        self.metadata_label.setWordWrap(True)
        self.metadata_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        metadata_actions = QHBoxLayout()
        self.copy_id_button = QPushButton("Copy ID")
        self.copy_fingerprint_button = QPushButton("Copy fingerprint")
        metadata_actions.addWidget(self.copy_id_button)
        metadata_actions.addWidget(self.copy_fingerprint_button)
        frame_row = QHBoxLayout()
        self.previous_frame_button = QPushButton("Previous frame")
        self.frame_label = QLabel("Frame —")
        self.frame_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next_frame_button = QPushButton("Next frame")
        self.play_button = QPushButton("Play (Space)")
        self.play_button.setCheckable(True)
        self.frame_combo = QComboBox()
        frame_row.addWidget(self.previous_frame_button)
        frame_row.addWidget(self.play_button)
        frame_row.addWidget(self.frame_combo, 1)
        frame_row.addWidget(self.next_frame_button)
        primary_actions = QGridLayout()
        self.add_to_project_button = QPushButton("Add to current App GUI (Enter)")
        self.edit_copy_button = QPushButton("Edit asset in Pixel Art")
        primary_actions.addWidget(self.add_to_project_button, 0, 0)
        primary_actions.addWidget(self.edit_copy_button, 0, 1)

        self.management_group = QGroupBox("▸ Manage asset")
        self.management_group.setProperty("disclosure_label", "Manage asset")
        self.management_group.setCheckable(True)
        self.management_group.setChecked(False)
        actions = QGridLayout(self.management_group)
        self.replace_button = QPushButton("Replace from image…")
        self.duplicate_button = QPushButton("Duplicate")
        self.export_button = QPushButton("Export current frame PNG…")
        self.rename_button = QPushButton("Rename (F2)")
        self.delete_button = QPushButton("Delete from library (Delete)")
        actions.addWidget(self.replace_button, 0, 0)
        actions.addWidget(self.duplicate_button, 0, 1)
        actions.addWidget(self.export_button, 1, 0)
        actions.addWidget(self.rename_button, 1, 1)
        actions.addWidget(self.delete_button, 2, 0, 1, 2)

        self.technical_group = QGroupBox("▸ Technical details and storage")
        self.technical_group.setProperty(
            "disclosure_label", "Technical details and storage"
        )
        self.technical_group.setCheckable(True)
        self.technical_group.setChecked(False)
        technical_layout = QVBoxLayout(self.technical_group)
        self.library_path_label = QLabel("Library file: unavailable")
        self.library_path_label.setStyleSheet("color: #666;")
        self.library_path_label.setWordWrap(True)
        self.library_path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        path_actions = QHBoxLayout()
        self.copy_path_button = QPushButton("Copy path")
        path_actions.addWidget(self.copy_path_button)
        path_actions.addWidget(self.refresh_button)
        technical_layout.addWidget(self.metadata_label)
        technical_layout.addLayout(metadata_actions)
        technical_layout.addWidget(self.library_path_label)
        technical_layout.addLayout(path_actions)

        details_layout.addWidget(self.name_label)
        details_layout.addWidget(self.preview_label, 1)
        details_layout.addLayout(frame_row)
        details_layout.addWidget(self.frame_label)
        details_layout.addLayout(primary_actions)
        details_layout.addWidget(self.management_group)
        details_layout.addWidget(self.technical_group)
        self.splitter.addWidget(details)
        self.splitter.setSizes((780, 420))
        self.splitter.setStretchFactor(0, 1)
        root.addWidget(self.splitter, 1)

        self.management_group.toggled.connect(
            lambda expanded: self._set_disclosure_expanded(
                self.management_group, expanded
            )
        )
        self.technical_group.toggled.connect(
            lambda expanded: self._set_disclosure_expanded(
                self.technical_group, expanded
            )
        )
        self._set_disclosure_expanded(self.management_group, False)
        self._set_disclosure_expanded(self.technical_group, False)

        self.play_timer = QTimer(self)
        self.play_timer.setSingleShot(True)

        self._set_action_state(False)

    def _connect_signals(self) -> None:
        """Connect catalogue navigation and management requests."""
        self.search_edit.textChanged.connect(self._filter_assets)
        self.display_mode_combo.currentIndexChanged.connect(self._apply_display_mode)
        self.collection_combo.currentIndexChanged.connect(self._filter_assets)
        self.theme_combo.currentIndexChanged.connect(self._filter_assets)
        self.asset_kind_combo.currentIndexChanged.connect(self._filter_assets)
        self.asset_list.currentItemChanged.connect(self._selection_changed)
        self.asset_list.itemSelectionChanged.connect(
            lambda: self._selection_changed(self.asset_list.currentItem())
        )
        self.asset_list.itemDoubleClicked.connect(
            lambda unused_item: self._request_edit_copy()
        )
        self.asset_list.customContextMenuRequested.connect(self._show_context_menu)
        self.previous_frame_button.clicked.connect(lambda: self._move_frame(-1))
        self.next_frame_button.clicked.connect(lambda: self._move_frame(1))
        self.frame_combo.currentIndexChanged.connect(self._frame_selected)
        self.play_button.toggled.connect(self._playback_toggled)
        self.play_timer.timeout.connect(self._advance_playback)
        self.add_to_project_button.clicked.connect(self._request_add_to_project)
        self.edit_copy_button.clicked.connect(self._request_edit_copy)
        self.replace_button.clicked.connect(self._request_replace)
        self.duplicate_button.clicked.connect(self._request_duplicate)
        self.export_button.clicked.connect(self._request_export)
        self.rename_button.clicked.connect(self._request_rename)
        self.delete_button.clicked.connect(self._request_delete)
        self.copy_path_button.clicked.connect(
            lambda: QApplication.clipboard().setText(
                str(self.property("library_path") or "")
            )
        )
        self.copy_id_button.clicked.connect(
            lambda: QApplication.clipboard().setText(self.selected_asset_id())
        )
        self.copy_fingerprint_button.clicked.connect(
            lambda: QApplication.clipboard().setText(
                self.selected_asset().fingerprint if self.selected_asset() else ""
            )
        )
        self.import_button.clicked.connect(self.import_requested.emit)
        self.import_pga_button.clicked.connect(self.import_pga_requested.emit)
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        self.retry_button.clicked.connect(self.refresh_requested.emit)
        self.undo_button.clicked.connect(self.undo_requested.emit)
        self.redo_button.clicked.connect(self.redo_requested.emit)

        self.search_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self.search_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.search_shortcut.activated.connect(self._focus_search)
        self.add_shortcut = QShortcut(QKeySequence("Return"), self.asset_list)
        self.add_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.add_shortcut.activated.connect(self._request_add_to_project)
        self.rename_shortcut = QShortcut(QKeySequence("F2"), self.asset_list)
        self.rename_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.rename_shortcut.activated.connect(self._request_rename)
        self.delete_shortcut = QShortcut(QKeySequence("Delete"), self.asset_list)
        self.delete_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.delete_shortcut.activated.connect(self._request_delete)
        self.play_shortcut = QShortcut(QKeySequence("Space"), self.asset_list)
        self.play_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.play_shortcut.activated.connect(self.play_button.toggle)

    def set_library_path(self, path: str | Path) -> None:
        """Show the actual persistent library file without editing it."""
        resolved = str(Path(path).expanduser())
        self.setProperty("library_path", resolved)
        self.library_path_label.setText(f"Library file: {Path(resolved).name}")
        self.library_path_label.setToolTip(
            f"Personal library storage: {resolved}\n"
            "Example: This file remains available after closing a source folder."
        )

    def restore_ui_state(self, settings) -> None:
        """Restore persistent catalogue density and splitter geometry."""
        mode = str(settings.value("library/display-mode", "medium") or "medium")
        index = self.display_mode_combo.findData(mode)
        if index >= 0:
            self.display_mode_combo.setCurrentIndex(index)
        collection = str(settings.value("library/collection", "all") or "all")
        collection_index = self.collection_combo.findData(collection)
        if collection_index >= 0:
            self.collection_combo.setCurrentIndex(collection_index)
        theme = str(settings.value("library/theme", "all") or "all")
        theme_index = self.theme_combo.findData(theme)
        if theme_index >= 0:
            self.theme_combo.setCurrentIndex(theme_index)
        asset_kind = str(settings.value("library/type", "all") or "all")
        kind_index = self.asset_kind_combo.findData(asset_kind)
        if kind_index >= 0:
            self.asset_kind_combo.setCurrentIndex(kind_index)
        splitter_state = settings.value("library/splitter")
        if splitter_state:
            self.splitter.restoreState(splitter_state)
        self.management_group.setChecked(
            str(settings.value("library/manage-expanded", "false")).lower()
            in {"1", "true", "yes"}
        )
        self.technical_group.setChecked(
            str(settings.value("library/technical-expanded", "false")).lower()
            in {"1", "true", "yes"}
        )

    def save_ui_state(self, settings) -> None:
        """Persist catalogue density and splitter geometry."""
        settings.setValue(
            "library/display-mode", str(self.display_mode_combo.currentData())
        )
        settings.setValue(
            "library/collection", str(self.collection_combo.currentData())
        )
        settings.setValue("library/theme", str(self.theme_combo.currentData()))
        settings.setValue("library/type", str(self.asset_kind_combo.currentData()))
        settings.setValue("library/splitter", self.splitter.saveState())
        settings.setValue("library/manage-expanded", self.management_group.isChecked())
        settings.setValue(
            "library/technical-expanded", self.technical_group.isChecked()
        )

    def storage_available(self) -> bool:
        """Return whether the current library snapshot loaded successfully."""
        return not self._storage_error

    def set_storage_error(
        self,
        message: str,
        standard_assets: tuple[LibraryAsset, ...] = (),
    ) -> None:
        """Show a persistent unavailable state and disable library writes."""
        self.set_assets((), standard_assets)
        self.stop_playback()
        self._storage_error = message.strip()
        self.count_label.setText("Personal library unavailable")
        self.empty_label.setText(
            "The personal library could not be loaded. Built-in icons remain usable. "
            "Use Retry after repairing or restoring the personal library file."
        )
        self.empty_label.setVisible(not standard_assets)
        self.import_button.setEnabled(False)
        self.import_pga_button.setEnabled(False)
        self.retry_button.setVisible(True)
        self._selection_changed(None)
        self._update_state_panel()

    def set_history_state(
        self,
        can_undo: bool,
        can_redo: bool = False,
        description: str = "",
    ) -> None:
        """Expose whether the last automatic library change is recoverable."""
        self._history_description = description.strip()
        self.undo_button.setEnabled(can_undo and self.storage_available())
        self.redo_button.setEnabled(can_redo and self.storage_available())
        self._update_state_panel()

    def _update_state_panel(self) -> None:
        """Show durable errors or the current recoverable operation."""
        if self._storage_error:
            self.state_label.setText(f"Library unavailable: {self._storage_error}")
            self.state_label.setStyleSheet("color: #b00020; font-weight: 600;")
            self.retry_button.setVisible(True)
            self.undo_button.setVisible(False)
            self.redo_button.setVisible(False)
            self.state_panel.setVisible(True)
            return
        self.retry_button.setVisible(False)
        self.undo_button.setVisible(self.undo_button.isEnabled())
        self.redo_button.setVisible(self.redo_button.isEnabled())
        self.state_label.setText(
            f"Last change: {self._history_description}"
            if self._history_description
            else ""
        )
        self.state_label.setStyleSheet("")
        self.state_panel.setVisible(bool(self._history_description))

    @staticmethod
    def _set_disclosure_expanded(group: QGroupBox, expanded: bool) -> None:
        """Render a disclosure arrow instead of a setting-style checkbox."""
        label = str(group.property("disclosure_label") or group.title())
        group.setTitle(f"{'▾' if expanded else '▸'} {label}")
        set_collapsible_group_expanded(group, expanded)
        group.setStyleSheet(
            group.styleSheet()
            + " QGroupBox::indicator { width: 0; height: 0; image: none; }"
        )

    def select_asset(self, asset_id: str, *, reveal: bool = True) -> bool:
        """Select and scroll to one stable record after a completed operation."""
        item = self._catalogue_items.get(asset_id)
        if item is None:
            return False
        if reveal and item.isHidden():
            self.search_edit.clear()
            self.collection_combo.setCurrentIndex(0)
            self.theme_combo.setCurrentIndex(0)
            self.asset_kind_combo.setCurrentIndex(0)
        self.asset_list.clearSelection()
        item.setSelected(True)
        self.asset_list.setCurrentItem(item)
        self.asset_list.scrollToItem(item)
        return True

    def set_assets(
        self,
        assets: tuple[LibraryAsset, ...],
        standard_assets: tuple[LibraryAsset, ...] = (),
    ) -> None:
        """Diff-update the catalogue and populate thumbnails in small batches."""
        self._storage_error = ""
        self.import_button.setEnabled(True)
        self.import_pga_button.setEnabled(True)
        previously_had_personal_assets = bool(self.personal_asset_ids)
        selected_ids = set(self.selected_asset_ids())
        selected_id = self.selected_asset_id()
        combined_assets = (*standard_assets, *assets)
        self.personal_asset_ids = {asset.id for asset in assets}
        self.standard_asset_ids = {asset.id for asset in standard_assets}
        self.assets = {asset.id: asset for asset in combined_assets}
        existing = {
            str(
                self.asset_list.item(row).data(Qt.ItemDataRole.UserRole) or ""
            ): self.asset_list.item(row)
            for row in range(self.asset_list.count())
        }
        blocked = self.asset_list.blockSignals(True)
        try:
            for asset_id, item in tuple(existing.items()):
                if asset_id not in self.assets:
                    self.asset_list.takeItem(self.asset_list.row(item))
                    existing.pop(asset_id)
            for target_row, asset in enumerate(combined_assets):
                item = existing.get(asset.id)
                if item is None:
                    item = QListWidgetItem()
                    self.asset_list.insertItem(target_row, item)
                    existing[asset.id] = item
                else:
                    current_row = self.asset_list.row(item)
                    if current_row != target_row:
                        self.asset_list.takeItem(current_row)
                        self.asset_list.insertItem(target_row, item)
                revision = self._asset_revision(asset)
                if item.data(Qt.ItemDataRole.UserRole + 1) != revision:
                    item.setIcon(QIcon())
                    item.setData(Qt.ItemDataRole.UserRole + 1, revision)
                    item.setData(Qt.ItemDataRole.UserRole + 2, 0)
                motion = (
                    f" · {len(asset.frames)} frames" if len(asset.frames) > 1 else ""
                )
                source = (
                    "Built-in" if asset.id in self.standard_asset_ids else "Personal"
                )
                metadata = standard_asset_metadata(asset.id)
                if metadata:
                    theme = metadata.theme
                    kind = metadata.kind
                elif asset.id in self.standard_asset_ids:
                    theme = "general"
                    kind = "icon"
                else:
                    theme = "personal"
                    kind = "personal"
                item.setText(
                    f"{asset.name}\n{asset.width}×{asset.height}{motion} · {source}"
                )
                item.setData(Qt.ItemDataRole.UserRole, asset.id)
                item.setData(
                    Qt.ItemDataRole.UserRole + 3,
                    "standard" if asset.id in self.standard_asset_ids else "personal",
                )
                item.setData(Qt.ItemDataRole.UserRole + 4, theme)
                item.setData(Qt.ItemDataRole.UserRole + 5, kind)
                profile = (
                    f" · {metadata.device_profile}" if metadata and metadata.device_profile else ""
                )
                item.setToolTip(
                    f"{asset.name}\n{asset.width} x {asset.height}, "
                    f"{len(asset.frames)} frame(s) · {source}{profile}.\n"
                    "Example: Ctrl-click or drag a selection box for multiple assets; "
                    "press Enter to add the selection to App GUI."
                )
            self.asset_list.clearSelection()
            first_personal_id = assets[0].id if assets else ""
            select_first_personal = (
                bool(first_personal_id)
                and not previously_had_personal_assets
                and not (selected_ids & self.personal_asset_ids)
            )
            if select_first_personal:
                selected_ids = {first_personal_id}
                selected_id = first_personal_id
            if selected_id in existing:
                self.asset_list.setCurrentItem(existing[selected_id])
            for asset_id in selected_ids:
                if asset_id in existing:
                    existing[asset_id].setSelected(True)
            if self.asset_list.currentItem() is None and self.asset_list.count():
                self.asset_list.setCurrentRow(0)
        finally:
            self.asset_list.blockSignals(blocked)
        self._catalogue_items = existing
        self.empty_label.setVisible(not combined_assets)
        if select_first_personal:
            self.search_edit.clear()
            self.collection_combo.setCurrentIndex(0)
            self.theme_combo.setCurrentIndex(0)
            self.asset_kind_combo.setCurrentIndex(0)
        self._filter_assets(self.search_edit.text())
        self._selection_changed(self.asset_list.currentItem())
        self._schedule_thumbnails()
        self._update_state_panel()

    @staticmethod
    def _asset_revision(asset: LibraryAsset) -> str:
        """Return a process-stable revision key for thumbnail invalidation."""
        return asset.fingerprint or f"{asset.id}:{hash(asset.frames[0])}"

    def _schedule_thumbnails(self) -> None:
        """Queue catalogue thumbnails without blocking the current UI event."""
        self._thumbnail_generation += 1
        generation = self._thumbnail_generation
        selected = self.selected_asset_id()
        self._thumbnail_queue = list(self.assets)
        if selected in self._thumbnail_queue:
            self._thumbnail_queue.remove(selected)
            self._thumbnail_queue.insert(0, selected)
        QTimer.singleShot(0, lambda: self._render_thumbnail_batch(generation))

    def _render_thumbnail_batch(self, generation: int) -> None:
        """Render a bounded number of queued icons, then yield to the event loop."""
        if generation != self._thumbnail_generation:
            return
        target = self.asset_list.iconSize()
        for _ in range(min(8, len(self._thumbnail_queue))):
            asset_id = self._thumbnail_queue.pop(0)
            asset = self.assets.get(asset_id)
            if asset is None:
                continue
            item = self._catalogue_items.get(asset_id)
            if item is None:
                continue
            requested_size = max(target.width(), target.height())
            if item.data(Qt.ItemDataRole.UserRole + 2) == requested_size:
                continue
            icon = cached_pixel_frame_pixmap(
                self._asset_revision(asset),
                asset.width,
                asset.height,
                asset.origin_x,
                asset.origin_y,
                asset.frames[0],
                target,
            )
            item.setIcon(QIcon(icon))
            item.setData(Qt.ItemDataRole.UserRole + 2, requested_size)
        if self._thumbnail_queue:
            QTimer.singleShot(0, lambda: self._render_thumbnail_batch(generation))

    def selected_asset_id(self) -> str:
        """Return the stable identity selected in the catalogue."""
        item = self.asset_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def selected_asset_ids(self) -> tuple[str, ...]:
        """Return selected visible identities in catalogue order."""
        selected = {
            str(item.data(Qt.ItemDataRole.UserRole) or "")
            for item in self.asset_list.selectedItems()
            if not item.isHidden()
        }
        ordered: list[str] = []
        for row in range(self.asset_list.count()):
            asset_id = str(
                self.asset_list.item(row).data(Qt.ItemDataRole.UserRole) or ""
            )
            if asset_id in selected:
                ordered.append(asset_id)
        return tuple(ordered)

    def selected_assets(self) -> tuple[LibraryAsset, ...]:
        """Return every visible selected record in catalogue order."""
        return tuple(
            self.assets[asset_id]
            for asset_id in self.selected_asset_ids()
            if asset_id in self.assets
        )

    def selected_asset(self) -> LibraryAsset | None:
        """Return the selected reusable record."""
        selected = self.selected_assets()
        return selected[0] if len(selected) == 1 else None

    def selected_frame_index(self) -> int:
        """Return the currently previewed frame index."""
        return int(self.preview_label.property("frame_index") or 0)

    def _apply_display_mode(self) -> None:
        """Change catalogue density without changing stored records."""
        mode = str(self.display_mode_combo.currentData() or "medium")
        if mode == "list":
            self.asset_list.setViewMode(QListWidget.ViewMode.ListMode)
            self.asset_list.setIconSize(QSize(48, 48))
            self.asset_list.setGridSize(QSize())
        else:
            self.asset_list.setViewMode(QListWidget.ViewMode.IconMode)
            icon_size, grid_size = {
                "compact": (56, QSize(122, 102)),
                "medium": (88, QSize(154, 132)),
                "large": (128, QSize(208, 178)),
            }[mode]
            self.asset_list.setIconSize(QSize(icon_size, icon_size))
            self.asset_list.setGridSize(grid_size)
        self._schedule_thumbnails()

    def _filter_assets(self, text: object = "") -> None:
        """Apply a non-destructive display-name and metadata filter."""
        del text
        filter_text = self.search_edit.text().strip()
        needle = filter_text.casefold()
        collection = str(self.collection_combo.currentData() or "all")
        theme = str(self.theme_combo.currentData() or "all")
        asset_kind = str(self.asset_kind_combo.currentData() or "all")
        visible = 0
        first_visible: QListWidgetItem | None = None
        first_visible_personal: QListWidgetItem | None = None
        for row in range(self.asset_list.count()):
            item = self.asset_list.item(row)
            asset = self.assets.get(str(item.data(Qt.ItemDataRole.UserRole) or ""))
            haystack = (
                f"{asset.name} {asset.width} {asset.height} {len(asset.frames)} "
                f"{asset.origin_x} {asset.origin_y} {asset.id} {asset.fingerprint}"
                if asset
                else ""
            ).casefold()
            source = str(item.data(Qt.ItemDataRole.UserRole + 3) or "")
            item_theme = str(item.data(Qt.ItemDataRole.UserRole + 4) or "general")
            item_kind = str(item.data(Qt.ItemDataRole.UserRole + 5) or "icon")
            hidden = bool(
                (needle and needle not in haystack)
                or (collection != "all" and source != collection)
                or (theme != "all" and item_theme != theme)
                or (asset_kind != "all" and item_kind != asset_kind)
            )
            item.setHidden(hidden)
            if hidden:
                item.setSelected(False)
            visible += not hidden
            if not hidden and first_visible is None:
                first_visible = item
            if not hidden and source == "personal" and first_visible_personal is None:
                first_visible_personal = item
        total = len(self.assets)
        self.count_label.setText(
            f"{visible} of {total} assets"
            if needle or collection != "all" or theme != "all" or asset_kind != "all"
            else f"{total} assets"
        )
        current = self.asset_list.currentItem()
        if current is not None and current.isHidden():
            self.stop_playback()
            self.asset_list.setCurrentRow(-1)
            current = None
        if current is None and first_visible is not None:
            current = first_visible_personal or first_visible
            self.asset_list.setCurrentItem(current)
        self.empty_label.setVisible(total == 0 or visible == 0)
        if total == 0:
            self.empty_label.setText(
                "No reusable assets yet. Import an image or PGA resource here, or "
                "use Save to Library from Pixel Art or App GUI."
            )
        elif visible == 0:
            self.empty_label.setText(
                f'No assets match "{filter_text}". Clear the filter to show all '
                f"{total} assets."
            )
        self._selection_changed(current)

    def _selection_changed(
        self,
        item: QListWidgetItem | None,
        previous: QListWidgetItem | None = None,
    ) -> None:
        """Render the selected record and reset its frame preview."""
        del previous
        self.stop_playback()
        selected = self.selected_assets()
        self._set_action_state(selected)
        self.selection_label.setText(
            f"{len(selected)} selected" if selected else "No selection"
        )
        if not selected:
            self.name_label.setText("No asset selected")
            self.metadata_label.setText("—")
            self.preview_label.clear()
            self.preview_label.setText("Select an asset")
            self.preview_label.setToolTip("")
            self.preview_label.setProperty("frame_index", 0)
            self.frame_label.setText("Frame —")
            self.frame_combo.clear()
            return
        if len(selected) > 1:
            built_in = sum(asset.id in self.standard_asset_ids for asset in selected)
            personal = len(selected) - built_in
            self.name_label.setText(f"{len(selected)} assets selected")
            self.metadata_label.setText(
                f"Built-in: {built_in}\nPersonal: {personal}\n"
                "Press Enter to add all selected assets to App GUI."
            )
            self._render_batch_preview(selected)
            self.preview_label.setProperty("frame_index", 0)
            self.frame_label.setText("Batch selection")
            self.frame_combo.clear()
            return
        self.preview_label.setProperty("frame_index", 0)
        asset = self.selected_asset()
        self.frame_combo.blockSignals(True)
        self.frame_combo.clear()
        if asset is not None:
            for index, duration in enumerate(
                asset.durations or (0,) * len(asset.frames)
            ):
                suffix = f" · {duration} ms" if duration else ""
                self.frame_combo.addItem(f"Frame {index + 1}{suffix}", index)
        self.frame_combo.blockSignals(False)
        self._render_frame()

    def _render_batch_preview(self, selected: tuple[LibraryAsset, ...]) -> None:
        """Show a compact visual summary instead of an empty batch preview."""
        visible = selected[:9]
        columns = min(3, len(visible))
        rows = (len(visible) + columns - 1) // columns
        cell_width = 96
        cell_height = 92
        preview = QPixmap(columns * cell_width, rows * cell_height + 22)
        preview.fill(QColor("#202020"))
        painter = QPainter(preview)
        painter.setPen(QColor("#f0f0f0"))
        for index, asset in enumerate(visible):
            column = index % columns
            row = index // columns
            thumbnail = cached_pixel_frame_pixmap(
                self._asset_revision(asset),
                asset.width,
                asset.height,
                asset.origin_x,
                asset.origin_y,
                asset.frames[0],
                QSize(64, 64),
            )
            x = column * cell_width + (cell_width - thumbnail.width()) // 2
            y = row * cell_height + 4
            painter.drawPixmap(x, y, thumbnail)
            painter.drawText(
                column * cell_width + 3,
                y + 68,
                cell_width - 6,
                18,
                Qt.AlignmentFlag.AlignCenter,
                asset.name,
            )
        if len(selected) > len(visible):
            painter.setPen(QColor("#bdbdbd"))
            painter.drawText(
                0,
                rows * cell_height,
                preview.width(),
                20,
                Qt.AlignmentFlag.AlignCenter,
                f"+ {len(selected) - len(visible)} more",
            )
        painter.end()
        self.preview_label.setPixmap(preview)
        self.preview_label.setToolTip(
            "Selected assets:\n" + "\n".join(asset.name for asset in selected)
        )

    def _set_action_state(self, selected: tuple[LibraryAsset, ...] | bool) -> None:
        """Enable single and batch actions according to selection ownership."""
        if isinstance(selected, bool):
            records = self.selected_assets() if selected else ()
        else:
            records = selected
        capabilities = self._selection_capabilities(records)
        count = capabilities.count
        self.add_to_project_button.setEnabled(capabilities.any)
        self.add_to_project_button.setText(
            f"Add {count} assets to current App GUI (Enter)"
            if count > 1
            else "Add to current App GUI (Enter)"
        )
        self.edit_copy_button.setEnabled(capabilities.single)
        self.edit_copy_button.setText(
            "Edit built-in copy in Pixel Art"
            if capabilities.single and records[0].id in self.standard_asset_ids
            else "Edit asset in Pixel Art"
        )
        can_edit_personal = (
            capabilities.single
            and capabilities.personal_count == 1
            and capabilities.storage_available
        )
        self.replace_button.setEnabled(can_edit_personal)
        self.rename_button.setEnabled(can_edit_personal)
        self.duplicate_button.setEnabled(
            capabilities.any and capabilities.storage_available
        )
        self.duplicate_button.setText(
            f"Copy {count} assets to Personal Library"
            if count > 1
            else "Copy to Personal Library"
            if capabilities.single and records[0].id in self.standard_asset_ids
            else "Duplicate"
        )
        self.export_button.setEnabled(capabilities.any)
        self.export_button.setText(
            f"Export {count} selected PNGs…"
            if count > 1
            else "Export current frame PNG…"
        )
        self.delete_button.setEnabled(capabilities.can_write_personal)
        self.delete_button.setText(
            f"Delete {capabilities.personal_count} personal assets (Delete)"
            if count > 1
            else "Delete from library (Delete)"
        )
        for widget in (
            self.copy_id_button,
            self.copy_fingerprint_button,
            self.previous_frame_button,
            self.next_frame_button,
            self.play_button,
            self.frame_combo,
        ):
            widget.setEnabled(capabilities.single)

    def _selection_capabilities(
        self, records: tuple[LibraryAsset, ...]
    ) -> LibrarySelectionCapabilities:
        """Calculate button and menu permissions from one shared policy."""
        return LibrarySelectionCapabilities(
            len(records),
            sum(asset.id in self.personal_asset_ids for asset in records),
            self.storage_available(),
        )

    def _move_frame(self, direction: int) -> None:
        """Move through a selected animation without changing stored data."""
        asset = self.selected_asset()
        if asset is None:
            return
        current = int(self.preview_label.property("frame_index") or 0)
        self.preview_label.setProperty(
            "frame_index", (current + direction) % len(asset.frames)
        )
        self.frame_combo.blockSignals(True)
        self.frame_combo.setCurrentIndex(
            int(self.preview_label.property("frame_index") or 0)
        )
        self.frame_combo.blockSignals(False)
        self._render_frame()

    def _frame_selected(self, index: int) -> None:
        """Jump directly to one stored animation frame."""
        asset = self.selected_asset()
        if asset is None or index < 0:
            return
        self.preview_label.setProperty("frame_index", min(index, len(asset.frames) - 1))
        self._render_frame()

    def _playback_toggled(self, playing: bool) -> None:
        """Start or stop stored-duration animation playback."""
        asset = self.selected_asset()
        if not playing or asset is None or len(asset.frames) < 2:
            self.stop_playback()
            return
        self.play_button.setText("Pause")
        self._schedule_playback()

    def _schedule_playback(self) -> None:
        """Schedule the next frame using its stored duration."""
        asset = self.selected_asset()
        if asset is None or not self.play_button.isChecked():
            return
        index = self.selected_frame_index()
        duration = asset.durations[index] if index < len(asset.durations) else 250
        self.play_timer.start(max(40, duration or 250))

    def _advance_playback(self) -> None:
        """Advance one frame and continue playback."""
        if not self.play_button.isChecked():
            return
        self._move_frame(1)
        self._schedule_playback()

    def stop_playback(self) -> None:
        """Stop library animation playback without changing selection."""
        self.play_timer.stop()
        self.play_button.blockSignals(True)
        self.play_button.setChecked(False)
        self.play_button.blockSignals(False)
        self.play_button.setText("Play (Space)")

    def _focus_search(self) -> None:
        """Focus and select the catalogue filter."""
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def _render_frame(self) -> None:
        """Render the selected frame and descriptive metadata."""
        asset = self.selected_asset()
        if asset is None:
            return
        index = min(
            int(self.preview_label.property("frame_index") or 0),
            len(asset.frames) - 1,
        )
        pixmap = cached_pixel_frame_pixmap(
            self._asset_revision(asset),
            asset.width,
            asset.height,
            asset.origin_x,
            asset.origin_y,
            asset.frames[index],
            QSize(300, 300),
            frame_index=index,
        )
        self.preview_label.setPixmap(pixmap)
        self.preview_label.setToolTip(
            f"{asset.name} · frame {index + 1} of {len(asset.frames)}"
        )
        self.name_label.setText(asset.name)
        duration = asset.durations[index] if index < len(asset.durations) else 0
        duration_text = f" · {duration} ms" if duration else ""
        self.frame_label.setText(
            f"Frame {index + 1} of {len(asset.frames)}{duration_text}"
        )
        if self.frame_combo.currentIndex() != index:
            self.frame_combo.blockSignals(True)
            self.frame_combo.setCurrentIndex(index)
            self.frame_combo.blockSignals(False)
        self.metadata_label.setText(
            f"Collection: {'Built-in standard' if asset.id in self.standard_asset_ids else 'Personal'}\n"
            f"Size: {asset.width} × {asset.height}\n"
            f"Origin: {asset.origin_x}, {asset.origin_y}\n"
            f"Stable ID: {asset.id}\n"
            f"Fingerprint: {asset.fingerprint}"
        )
        multiple = len(asset.frames) > 1
        self.previous_frame_button.setEnabled(multiple)
        self.next_frame_button.setEnabled(multiple)
        self.play_button.setEnabled(multiple)

    def _request_add_to_project(self) -> None:
        """Request a detached project copy of the selected asset."""
        asset_ids = self.selected_asset_ids()
        if asset_ids:
            self.add_to_project_requested.emit(asset_ids)

    def _request_rename(self) -> None:
        """Request persistent renaming of the selected record."""
        asset_id = self.selected_asset_id()
        if (
            asset_id
            and len(self.selected_asset_ids()) == 1
            and asset_id in self.personal_asset_ids
        ):
            self.rename_requested.emit(asset_id)

    def _request_edit_copy(self) -> None:
        """Request a detached editable copy of the previewed frame."""
        asset_id = self.selected_asset_id()
        if asset_id and len(self.selected_asset_ids()) == 1:
            self.edit_copy_requested.emit(asset_id)

    def _request_replace(self) -> None:
        """Request replacement pixels for the selected stable record."""
        asset_id = self.selected_asset_id()
        if (
            asset_id
            and len(self.selected_asset_ids()) == 1
            and asset_id in self.personal_asset_ids
        ):
            self.replace_requested.emit(asset_id)

    def _request_duplicate(self) -> None:
        """Request an independent duplicate of the selected record."""
        asset_ids = self.selected_asset_ids()
        if asset_ids:
            self.duplicate_requested.emit(asset_ids)

    def _request_export(self) -> None:
        """Request PNG export of the currently previewed frame."""
        asset_ids = self.selected_asset_ids()
        if asset_ids:
            self.export_requested.emit(asset_ids)

    def _request_delete(self) -> None:
        """Request confirmed removal of the selected record."""
        asset_ids = self.selected_asset_ids()
        if asset_ids:
            self.delete_requested.emit(asset_ids)

    def _show_context_menu(self, position) -> None:
        """Expose common record operations at the pointed asset."""
        item = self.asset_list.itemAt(position)
        if item is not None and not item.isSelected():
            self.asset_list.clearSelection()
            item.setSelected(True)
            self.asset_list.setCurrentItem(item)
        menu = QMenu(self)
        edit_action = menu.addAction("Edit complete asset in Pixel Art")
        add_action = menu.addAction("Add independent copy to current App GUI")
        menu.addSeparator()
        replace_action = menu.addAction("Replace from image…")
        duplicate_action = menu.addAction("Duplicate library asset")
        export_action = menu.addAction("Export current frame PNG…")
        rename_action = menu.addAction("Rename library asset")
        delete_action = menu.addAction("Delete from library")
        selected = self.selected_assets()
        capabilities = self._selection_capabilities(selected)
        add_action.setText(
            f"Add {capabilities.count} independent copies to current App GUI"
            if capabilities.count > 1
            else "Add independent copy to current App GUI"
        )
        can_edit_personal = (
            capabilities.single
            and capabilities.personal_count == 1
            and capabilities.storage_available
        )
        edit_action.setEnabled(capabilities.single)
        add_action.setEnabled(capabilities.any)
        replace_action.setEnabled(can_edit_personal)
        duplicate_action.setEnabled(capabilities.any and capabilities.storage_available)
        export_action.setEnabled(capabilities.any)
        rename_action.setEnabled(can_edit_personal)
        delete_action.setEnabled(capabilities.can_write_personal)
        edit_action.triggered.connect(self._request_edit_copy)
        add_action.triggered.connect(self._request_add_to_project)
        replace_action.triggered.connect(self._request_replace)
        duplicate_action.triggered.connect(self._request_duplicate)
        export_action.triggered.connect(self._request_export)
        rename_action.triggered.connect(self._request_rename)
        delete_action.triggered.connect(self._request_delete)
        menu.exec(self.asset_list.viewport().mapToGlobal(position))
