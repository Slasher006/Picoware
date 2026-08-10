"""Qt main window for the graphics editor."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QStandardPaths, QTimer
from PySide6.QtGui import QAction, QActionGroup, QIcon, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QPlainTextEdit,
)

from .canvas import PixelCanvas, pixel_art_image, qcolor_from_rgb565
from .app_importer import (
    AppImportResult,
    ExistingAppImporter,
    build_imported_app_patches,
    refresh_import_metadata,
)
from .designer import (
    DesignerSession,
    GuiPixelAsset,
    ScreenDesignerWidget,
    ScreenFlowWidget,
)
from .designer_model import GuiProject, backup_project, build_designer_patch
from .model import PixelArt, rgb_to_rgb565
from .reference import (
    image_to_pixel_art,
    prepare_reference_image,
    read_image_frames,
    split_sprite_sheet,
)
from .source import (
    GraphicsAsset,
    SourceExporter,
    SourcePatch,
    SourceScanner,
    TraceInterpreter,
    TraceResult,
    build_new_graphic_patch,
    is_managed_graphic,
)


class DiffDialog(QDialog):
    """Show the exact source patch before applying it."""

    def __init__(self, patch: SourcePatch, parent: QWidget | None = None):
        """Build the source diff confirmation dialog."""
        super().__init__(parent)
        self.setWindowTitle("Review Python changes")
        self.resize(960, 680)
        layout = QVBoxLayout(self)
        detail = (
            f"{patch.run_count} generated GUI elements will be written."
            if patch.key == "gui-designer"
            else f"{patch.run_count} optimized pixel runs will be written."
        )
        summary = QLabel(f"{patch.path}\n{detail}")
        summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(summary)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setPlainText(patch.diff or "No source changes.")
        layout.addWidget(editor, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(
            self.accept
        )
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class SpriteSheetDialog(QDialog):
    """Collect regular sprite-sheet slicing dimensions."""

    def __init__(
        self,
        image: QImage,
        suggested_width: int,
        suggested_height: int,
        parent: QWidget | None = None,
    ):
        """Build sprite-sheet slicing controls."""
        super().__init__(parent)
        self.setWindowTitle("Import sprite sheet")
        layout = QFormLayout(self)
        summary = QLabel(f"Image size: {image.width()} x {image.height()}")
        layout.addRow(summary)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, max(1, image.width()))
        self.width_spin.setValue(min(max(1, suggested_width), image.width()))
        layout.addRow("Frame width", self.width_spin)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(1, max(1, image.height()))
        self.height_spin.setValue(min(max(1, suggested_height), image.height()))
        layout.addRow("Frame height", self.height_spin)
        self.margin_spin = QSpinBox()
        self.margin_spin.setRange(0, max(image.width(), image.height()))
        layout.addRow("Outer margin", self.margin_spin)
        self.spacing_spin = QSpinBox()
        self.spacing_spin.setRange(0, max(image.width(), image.height()))
        layout.addRow("Frame spacing", self.spacing_spin)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def settings(self) -> tuple[int, int, int, int]:
        """Return frame width, height, margin, and spacing."""
        return (
            self.width_spin.value(),
            self.height_spin.value(),
            self.margin_spin.value(),
            self.spacing_spin.value(),
        )


class NewGraphicDialog(QDialog):
    """Collect dimensions and naming for a new Python graphic."""

    def __init__(
        self,
        width: int,
        height: int,
        has_reference: bool,
        imported_frame_count: int,
        parent: QWidget | None = None,
    ):
        """Build new graphic creation controls."""
        super().__init__(parent)
        self.setWindowTitle("Create new Python graphic")
        layout = QFormLayout(self)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Blank transparent asset", "blank")
        self.mode_combo.addItem("Current canvas pixels", "current")
        if has_reference:
            self.mode_combo.addItem("Current reference image", "reference")
        self.mode_combo.addItem("Animation file or sprite sheet", "animation_file")
        if imported_frame_count > 1:
            self.mode_combo.addItem(
                f"Current {imported_frame_count} imported frames",
                "imported_frames",
            )
        layout.addRow("Create from", self.mode_combo)
        self.name_edit = QLineEdit("draw_new_graphic")
        layout.addRow("Function name", self.name_edit)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 320)
        self.width_spin.setValue(min(320, max(1, width)))
        layout.addRow("Width", self.width_spin)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(1, 320)
        self.height_spin.setValue(min(320, max(1, height)))
        layout.addRow("Height", self.height_spin)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def settings(self) -> tuple[str, int, int, str]:
        """Return function name, dimensions, and creation mode."""
        return (
            self.name_edit.text().strip(),
            self.width_spin.value(),
            self.height_spin.value(),
            str(self.mode_combo.currentData()),
        )


class PixelSizeDialog(QDialog):
    """Collect target dimensions for a pixel canvas operation."""

    def __init__(
        self,
        title: str,
        width: int,
        height: int,
        allow_centering: bool,
        parent: QWidget | None = None,
    ):
        """Build pixel dimensions and optional centering controls."""
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QFormLayout(self)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 320)
        self.width_spin.setValue(width)
        layout.addRow("Width", self.width_spin)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(1, 320)
        self.height_spin.setValue(height)
        layout.addRow("Height", self.height_spin)
        self.center_check = QCheckBox("Center existing pixels")
        self.center_check.setVisible(allow_centering)
        layout.addRow(self.center_check)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def settings(self) -> tuple[int, int, bool]:
        """Return target width, height, and centering choice."""
        return (
            self.width_spin.value(),
            self.height_spin.value(),
            self.center_check.isChecked(),
        )


class AppImportTargetDialog(QDialog):
    """Choose an existing Python application file or folder."""

    def __init__(self, parent: QWidget | None = None):
        """Build file and folder selection controls."""
        super().__init__(parent)
        self.setWindowTitle("Import existing application")
        self.selected_path: Path | None = None
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "Choose one Python app file or an application folder. Source is parsed but never executed."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.path_label = QLabel("No application selected")
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.path_label)
        choices = QHBoxLayout()
        file_button = QPushButton("Choose Python file...")
        folder_button = QPushButton("Choose app folder...")
        file_button.clicked.connect(self._choose_file)
        folder_button.clicked.connect(self._choose_folder)
        choices.addWidget(file_button)
        choices.addWidget(folder_button)
        layout.addLayout(choices)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Scan app")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _choose_file(self) -> None:
        """Choose one Python application file."""
        filename, _ = QFileDialog.getOpenFileName(
            self, "Choose Python app", str(Path.cwd()), "Python files (*.py)"
        )
        if filename:
            self._set_path(Path(filename))

    def _choose_folder(self) -> None:
        """Choose one Python application folder."""
        folder = QFileDialog.getExistingDirectory(
            self, "Choose app folder", str(Path.cwd())
        )
        if folder:
            self._set_path(Path(folder))

    def _set_path(self, path: Path) -> None:
        """Set the chosen import path."""
        self.selected_path = path
        self.path_label.setText(str(path))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)


class AppImportReviewDialog(QDialog):
    """Review existing-app discovery before opening it."""

    def __init__(self, result: AppImportResult, parent: QWidget | None = None):
        """Build import counts and scanner warning review."""
        super().__init__(parent)
        self.setWindowTitle("Review app import")
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        summary = QLabel(
            f"{result.files_scanned} Python files scanned\n"
            f"{len(result.project.screens)} screens and "
            f"{len(result.project.connections)} relations found\n"
            f"{result.editable_count} editable elements, "
            f"{result.locked_count} locked code elements"
        )
        layout.addWidget(summary)
        notes = QPlainTextEdit()
        notes.setReadOnly(True)
        notes.setPlainText(
            "\n".join(result.warnings) if result.warnings else "No import warnings."
        )
        layout.addWidget(notes, 1)
        safety = QLabel(
            "Locked elements remain visible but are never rewritten. Editable calls retain exact source anchors."
        )
        safety.setWordWrap(True)
        layout.addWidget(safety)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Open in designer")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class MultiPatchDialog(QDialog):
    """Review source patches spanning imported application files."""

    def __init__(self, patches: list[SourcePatch], parent: QWidget | None = None):
        """Build a combined multi-file diff confirmation dialog."""
        super().__init__(parent)
        self.setWindowTitle("Review existing app changes")
        self.resize(1080, 760)
        layout = QVBoxLayout(self)
        change_count = sum(patch.run_count for patch in patches)
        layout.addWidget(
            QLabel(
                f"{len(patches)} Python files contain {change_count} source-backed changes."
            )
        )
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setPlainText("\n".join(patch.diff for patch in patches))
        layout.addWidget(editor, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(
            self.accept
        )
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class MainWindow(QMainWindow):
    """Provide source discovery and mouse pixel editing."""

    def __init__(self):
        """Initialize editor state and user interface."""
        super().__init__()
        self.setWindowTitle("Pico Graphics and GUI Designer")
        self.resize(1480, 900)
        self.scanner = SourceScanner()
        self.tracer = TraceInterpreter()
        self.thumbnail_tracer = TraceInterpreter(800, 300, 48)
        self.exporter = SourceExporter()
        self.app_importer = ExistingAppImporter()
        self.designer_session = DesignerSession(self)
        self.assets: list[GraphicsAsset] = []
        self.current_asset: GraphicsAsset | None = None
        self.current_trace: TraceResult | None = None
        self.variant_values: dict[str, Any] = {}
        self.variant_controls: dict[str, QComboBox] = {}
        self.animation_parameter: str | None = None
        self.animation_timer = QTimer(self)
        self.animation_timer.setInterval(250)
        self.designer_recovery_timer = QTimer(self)
        self.designer_recovery_timer.setSingleShot(True)
        self.designer_recovery_timer.setInterval(800)
        self.pixel_recovery_timer = QTimer(self)
        self.pixel_recovery_timer.setSingleShot(True)
        self.pixel_recovery_timer.setInterval(800)
        self.animation_asset_key: tuple[Path, str] | None = None
        self.animation_images: dict[Any, QImage] = {}
        self.animation_drafts: dict[Any, PixelArt] = {}
        self._animation_structure_dirty = False
        self._scan_path: Path | None = None
        self._scan_folder = False
        self._dirty = False
        self._suppress_changes = False
        self._current_color = 0xFFFF
        self._background_color = 0x0000
        self._thumbnail_generation = 0
        self._thumbnail_queue: list[int] = []
        self._build_actions()
        self._build_interface()
        self._connect_actions()
        self._set_color(self._current_color)
        self._set_background(self._background_color)
        self._update_recovery_action()
        self._update_history_actions()
        self._update_pixel_action_state()

    def open_path(self, path: str | Path) -> None:
        """Open a Python file or source folder."""
        source_path = Path(path).expanduser().resolve()
        if source_path.name.endswith(".picogui.json"):
            self._load_gui_project(source_path)
            self.workspace_tabs.setCurrentIndex(1)
        elif source_path.is_dir():
            self._scan_folder = True
            self._scan_path = source_path
            self._scan(source_path, True)
        elif source_path.suffix.lower() == ".py":
            self._scan_folder = False
            self._scan_path = source_path
            self._scan(source_path, False)
        else:
            QMessageBox.warning(
                self, "Unsupported path", "Choose a Python file or folder."
            )

    def closeEvent(self, event) -> None:
        """Confirm before closing with unsaved painting."""
        if self._confirm_designer_discard() and self._confirm_discard():
            self.screen_flow.shutdown_live_simulator()
            event.accept()
        else:
            event.ignore()

    def _build_actions(self) -> None:
        """Create reusable window actions."""
        self.open_file_action = QAction("Open Python File...", self)
        self.open_file_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_folder_action = QAction("Open Folder...", self)
        self.rescan_action = QAction("Rescan", self)
        self.rescan_action.setShortcut(QKeySequence("F5"))
        self.export_action = QAction("Export PNG...", self)
        self.export_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.apply_action = QAction("Apply to Python...", self)
        self.apply_action.setShortcut(QKeySequence.StandardKey.Save)
        self.quit_action = QAction("Quit", self)
        self.quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self.undo_action = QAction("Undo", self)
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.redo_action = QAction("Redo", self)
        self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        self.cut_pixels_action = QAction("Cut Pixels", self)
        self.cut_pixels_action.setShortcut(QKeySequence.StandardKey.Cut)
        self.copy_pixels_action = QAction("Copy Pixels", self)
        self.copy_pixels_action.setShortcut(QKeySequence.StandardKey.Copy)
        self.paste_pixels_action = QAction("Paste Pixels", self)
        self.paste_pixels_action.setShortcut(QKeySequence.StandardKey.Paste)
        self.select_all_pixels_action = QAction("Select All Pixels", self)
        self.select_all_pixels_action.setShortcut(QKeySequence.StandardKey.SelectAll)
        self.delete_pixels_action = QAction("Delete Selected Pixels", self)
        self.delete_pixels_action.setShortcut(QKeySequence("Delete"))
        self.clear_selection_action = QAction("Clear Selection", self)
        self.clear_selection_action.setShortcut(QKeySequence("Escape"))
        self.flip_horizontal_action = QAction("Flip Horizontally", self)
        self.flip_vertical_action = QAction("Flip Vertically", self)
        self.rotate_clockwise_action = QAction("Rotate 90° Clockwise", self)
        self.crop_selection_action = QAction("Crop to Selection", self)
        self.resize_canvas_action = QAction("Resize Canvas...", self)
        self.scale_artwork_action = QAction("Scale Artwork...", self)
        self.clear_canvas_action = QAction("Clear Canvas...", self)
        self.use_in_gui_action = QAction("Use in App GUI", self)
        self.use_in_gui_action.setEnabled(False)
        self.recover_pixel_action = QAction("Recover Autosaved Pixel Asset...", self)
        self.recover_gui_action = QAction("Recover Autosaved GUI Project...", self)
        self.open_reference_action = QAction("Open Reference Image...", self)
        self.clear_reference_action = QAction("Clear Reference Image", self)
        self.import_frames_action = QAction("Import Animation Frames...", self)
        self.new_graphic_action = QAction("Create New Python Graphic...", self)
        self.new_graphic_action.setIconText("New Asset")
        self.new_graphic_action.setToolTip("Create a new editable Python pixel asset.")
        self.new_gui_action = QAction("New GUI Project", self)
        self.open_gui_action = QAction("Open GUI Project...", self)
        self.save_gui_action = QAction("Save GUI Project", self)
        self.save_gui_action.setShortcut(QKeySequence("Ctrl+Alt+S"))
        self.save_gui_as_action = QAction("Save GUI Project As...", self)
        self.recover_saved_gui_action = QAction("Recover Saved GUI Backup...", self)
        self.export_gui_action = QAction("Export GUI to Python...", self)
        self.import_existing_app_action = QAction("Import Existing App...", self)
        self.apply_imported_app_action = QAction("Apply Edits to Existing App...", self)
        self.apply_imported_app_action.setEnabled(False)

    def _build_interface(self) -> None:
        """Build the catalogue, canvas, and inspector."""
        file_menu = self.menuBar().addMenu("File")
        file_menu.addActions(
            (self.open_file_action, self.open_folder_action, self.rescan_action)
        )
        file_menu.addSeparator()
        file_menu.addActions((self.export_action, self.apply_action))
        file_menu.addSeparator()
        file_menu.addAction(self.quit_action)
        edit_menu = self.menuBar().addMenu("Edit")
        edit_menu.addActions((self.undo_action, self.redo_action))
        edit_menu.addSeparator()
        edit_menu.addActions(
            (
                self.cut_pixels_action,
                self.copy_pixels_action,
                self.paste_pixels_action,
                self.delete_pixels_action,
                self.select_all_pixels_action,
                self.clear_selection_action,
            )
        )
        pixel_menu = self.menuBar().addMenu("Pixel Art")
        pixel_menu.addActions(
            (
                self.flip_horizontal_action,
                self.flip_vertical_action,
                self.rotate_clockwise_action,
                self.crop_selection_action,
            )
        )
        pixel_menu.addSeparator()
        pixel_menu.addActions(
            (
                self.resize_canvas_action,
                self.scale_artwork_action,
                self.clear_canvas_action,
            )
        )
        pixel_menu.addSeparator()
        pixel_menu.addActions((self.use_in_gui_action, self.recover_pixel_action))
        reference_menu = self.menuBar().addMenu("Reference")
        reference_menu.addActions(
            (
                self.open_reference_action,
                self.clear_reference_action,
                self.import_frames_action,
                self.new_graphic_action,
            )
        )
        gui_menu = self.menuBar().addMenu("GUI Project")
        gui_menu.addActions(
            (
                self.new_gui_action,
                self.open_gui_action,
                self.save_gui_action,
                self.save_gui_as_action,
                self.recover_gui_action,
                self.recover_saved_gui_action,
            )
        )
        gui_menu.addSeparator()
        gui_menu.addActions(
            (self.import_existing_app_action, self.apply_imported_app_action)
        )
        gui_menu.addSeparator()
        gui_menu.addAction(self.export_gui_action)

        self.tool_bar = QToolBar("Pixel tools")
        self.tool_bar.setMovable(False)
        self.addToolBar(self.tool_bar)
        self.tool_bar.addAction(self.new_graphic_action)
        self.tool_bar.addAction(self.use_in_gui_action)
        self.tool_bar.addSeparator()
        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        tool_specs = (
            ("Select", "select", "S"),
            ("Pencil", "pencil", "P"),
            ("Eraser", "eraser", "E"),
            ("Fill", "fill", "F"),
            ("Line", "line", "L"),
            ("Rectangle", "rectangle", "R"),
            ("Picker", "picker", "I"),
        )
        for label, value, shortcut in tool_specs:
            action = QAction(label, self)
            action.setCheckable(True)
            action.setData(value)
            action.setShortcut(QKeySequence(shortcut))
            self.tool_group.addAction(action)
            self.tool_bar.addAction(action)
            if value == "pencil":
                action.setChecked(True)
        self.tool_bar.addSeparator()
        self.tool_bar.addAction(self.undo_action)
        self.tool_bar.addAction(self.redo_action)
        self.tool_bar.addSeparator()
        self.primary_button = QToolButton()
        self.primary_button.setText("Paint")
        self.primary_button.setToolTip("Choose the paint color")
        self.tool_bar.addWidget(self.primary_button)
        self.background_button = QToolButton()
        self.background_button.setText("Erase")
        self.background_button.setToolTip("Choose the eraser color")
        self.tool_bar.addWidget(self.background_button)
        self.tool_bar.addSeparator()
        self.tool_bar.addWidget(QLabel("Zoom"))
        self.zoom_spin = QSpinBox()
        self.zoom_spin.setRange(1, 40)
        self.zoom_spin.setValue(12)
        self.zoom_spin.setSuffix("x")
        self.tool_bar.addWidget(self.zoom_spin)
        self.grid_check = QCheckBox("Grid")
        self.grid_check.setChecked(True)
        self.tool_bar.addWidget(self.grid_check)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_catalogue())
        splitter.addWidget(self._build_canvas_panel())
        splitter.addWidget(self._build_inspector())
        splitter.setSizes((300, 820, 360))
        splitter.setStretchFactor(1, 1)
        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.addTab(splitter, "Pixel Art")
        self.screen_designer = ScreenDesignerWidget(self.designer_session)
        self.workspace_tabs.addTab(self.screen_designer, "App GUI")
        self.screen_flow = ScreenFlowWidget(self.designer_session)
        self.workspace_tabs.addTab(self.screen_flow, "Screen Flow")
        self.setCentralWidget(self.workspace_tabs)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Open a Python file or folder to begin.")

    def _build_catalogue(self) -> QWidget:
        """Build the graphics catalogue panel."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QLabel("Detected graphics")
        title.setStyleSheet("font-weight: 600; font-size: 15px;")
        layout.addWidget(title)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter graphics")
        self.search_edit.setClearButtonEnabled(True)
        layout.addWidget(self.search_edit)
        self.asset_list = QListWidget()
        self.asset_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.asset_list.setIconSize(QSize(72, 72))
        self.asset_list.setGridSize(QSize(132, 112))
        self.asset_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.asset_list.setMovement(QListWidget.Movement.Static)
        self.asset_list.setWordWrap(True)
        layout.addWidget(self.asset_list, 1)
        self.asset_count_label = QLabel("No graphics loaded")
        layout.addWidget(self.asset_count_label)
        return panel

    def _build_canvas_panel(self) -> QWidget:
        """Build the scrollable pixel canvas panel."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 8, 0, 8)
        self.asset_title = QLabel("No graphic selected")
        self.asset_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.asset_title.setStyleSheet("font-weight: 600; font-size: 15px;")
        layout.addWidget(self.asset_title)
        self.scroll_area = QScrollArea()
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.setWidgetResizable(False)
        self.canvas = PixelCanvas()
        self.scroll_area.setWidget(self.canvas)
        layout.addWidget(self.scroll_area, 1)
        return panel

    def _build_inspector(self) -> QWidget:
        """Build variants, palette, preview, and warnings."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        self.source_label = QLabel("No source selected")
        self.source_label.setWordWrap(True)
        self.source_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.source_label)
        self.asset_mode_label = QLabel("No asset selected")
        self.asset_mode_label.setWordWrap(True)
        layout.addWidget(self.asset_mode_label)

        self.variant_group = QGroupBox("Variants")
        self.variant_form = QFormLayout(self.variant_group)
        layout.addWidget(self.variant_group)

        self.animation_group = QGroupBox("Animation frames")
        animation_layout = QVBoxLayout(self.animation_group)
        frame_layout = QGridLayout()
        self.previous_frame_button = QPushButton("Previous")
        self.frame_combo = QComboBox()
        self.next_frame_button = QPushButton("Next")
        frame_layout.addWidget(self.previous_frame_button, 0, 0)
        frame_layout.addWidget(self.frame_combo, 0, 1)
        frame_layout.addWidget(self.next_frame_button, 0, 2)
        animation_layout.addLayout(frame_layout)
        playback_layout = QGridLayout()
        self.play_button = QPushButton("Play")
        self.play_button.setCheckable(True)
        self.onion_skin_check = QCheckBox("Show previous frame")
        self.onion_skin_check.setToolTip(
            "Overlay the previous frame while drawing the current frame."
        )
        playback_layout.addWidget(self.play_button, 0, 0)
        playback_layout.addWidget(self.onion_skin_check, 0, 1)
        animation_layout.addLayout(playback_layout)
        frame_edit_layout = QGridLayout()
        self.add_frame_button = QPushButton("Add frame")
        self.duplicate_frame_button = QPushButton("Duplicate")
        self.delete_frame_button = QPushButton("Delete")
        self.move_frame_left_button = QPushButton("Move left")
        self.move_frame_right_button = QPushButton("Move right")
        frame_edit_layout.addWidget(self.add_frame_button, 0, 0)
        frame_edit_layout.addWidget(self.duplicate_frame_button, 0, 1)
        frame_edit_layout.addWidget(self.delete_frame_button, 0, 2)
        frame_edit_layout.addWidget(self.move_frame_left_button, 1, 0)
        frame_edit_layout.addWidget(self.move_frame_right_button, 1, 1)
        self.frame_interval_spin = QSpinBox()
        self.frame_interval_spin.setRange(40, 5000)
        self.frame_interval_spin.setValue(250)
        self.frame_interval_spin.setSuffix(" ms")
        frame_edit_layout.addWidget(self.frame_interval_spin, 1, 2)
        animation_layout.addLayout(frame_edit_layout)
        self.animation_group.setVisible(False)
        layout.addWidget(self.animation_group)

        reference_group = QGroupBox("Reference image")
        reference_layout = QVBoxLayout(reference_group)
        reference_buttons = QHBoxLayout()
        self.open_reference_button = QPushButton("Open image...")
        self.clear_reference_button = QPushButton("Clear")
        reference_buttons.addWidget(self.open_reference_button)
        reference_buttons.addWidget(self.clear_reference_button)
        reference_layout.addLayout(reference_buttons)
        self.reference_status_label = QLabel("No reference loaded")
        self.reference_status_label.setWordWrap(True)
        reference_layout.addWidget(self.reference_status_label)
        reference_form = QFormLayout()
        self.reference_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.reference_opacity_slider.setRange(0, 100)
        self.reference_opacity_slider.setValue(55)
        reference_form.addRow("Opacity", self.reference_opacity_slider)
        self.reference_fit_combo = QComboBox()
        self.reference_fit_combo.addItem("Contain", "contain")
        self.reference_fit_combo.addItem("Cover", "cover")
        self.reference_fit_combo.addItem("Stretch", "stretch")
        reference_form.addRow("Fit", self.reference_fit_combo)
        self.reference_rotation_combo = QComboBox()
        for rotation in (0, 90, 180, 270):
            self.reference_rotation_combo.addItem(f"{rotation} degrees", rotation)
        reference_form.addRow("Rotation", self.reference_rotation_combo)
        self.reference_scale_spin = QSpinBox()
        self.reference_scale_spin.setRange(10, 500)
        self.reference_scale_spin.setValue(100)
        self.reference_scale_spin.setSuffix("%")
        reference_form.addRow("Scale", self.reference_scale_spin)
        self.reference_x_spin = QSpinBox()
        self.reference_x_spin.setRange(-1024, 1024)
        reference_form.addRow("X offset", self.reference_x_spin)
        self.reference_y_spin = QSpinBox()
        self.reference_y_spin.setRange(-1024, 1024)
        reference_form.addRow("Y offset", self.reference_y_spin)
        reference_layout.addLayout(reference_form)
        reference_flags = QGridLayout()
        self.reference_flip_horizontal = QCheckBox("Flip horizontally")
        self.reference_flip_vertical = QCheckBox("Flip vertically")
        self.reference_foreground_check = QCheckBox("Overlay above pixels")
        reference_flags.addWidget(self.reference_flip_horizontal, 0, 0)
        reference_flags.addWidget(self.reference_flip_vertical, 0, 1)
        reference_flags.addWidget(self.reference_foreground_check, 1, 0, 1, 2)
        reference_layout.addLayout(reference_flags)
        conversion_form = QFormLayout()
        self.reference_colors_spin = QSpinBox()
        self.reference_colors_spin.setRange(2, 256)
        self.reference_colors_spin.setValue(16)
        conversion_form.addRow("Palette colors", self.reference_colors_spin)
        self.reference_dither_check = QCheckBox("Floyd-Steinberg dithering")
        conversion_form.addRow("Conversion", self.reference_dither_check)
        reference_layout.addLayout(conversion_form)
        self.convert_reference_button = QPushButton("Convert to editable pixels")
        self.import_frames_button = QPushButton("Import GIF or sprite sheet...")
        self.new_graphic_button = QPushButton("Create new Python graphic...")
        reference_layout.addWidget(self.convert_reference_button)
        reference_layout.addWidget(self.import_frames_button)
        reference_layout.addWidget(self.new_graphic_button)
        layout.addWidget(reference_group)

        selection_group = QGroupBox("Selection and canvas")
        selection_layout = QGridLayout(selection_group)
        selection_actions = (
            self.select_all_pixels_action,
            self.clear_selection_action,
            self.cut_pixels_action,
            self.copy_pixels_action,
            self.paste_pixels_action,
            self.delete_pixels_action,
            self.flip_horizontal_action,
            self.flip_vertical_action,
            self.rotate_clockwise_action,
            self.crop_selection_action,
            self.resize_canvas_action,
            self.scale_artwork_action,
        )
        for index, action in enumerate(selection_actions):
            button = QPushButton(action.text().replace("...", ""))
            button.setEnabled(action.isEnabled())
            button.clicked.connect(action.trigger)
            action.changed.connect(
                lambda selected_action=action,
                selected_button=button: selected_button.setEnabled(
                    selected_action.isEnabled()
                )
            )
            selection_layout.addWidget(button, index // 2, index % 2)
        self.clear_canvas_button = QPushButton("Clear canvas")
        self.clear_canvas_button.setEnabled(self.clear_canvas_action.isEnabled())
        self.clear_canvas_button.clicked.connect(self.clear_canvas_action.trigger)
        self.clear_canvas_action.changed.connect(
            lambda: self.clear_canvas_button.setEnabled(
                self.clear_canvas_action.isEnabled()
            )
        )
        selection_layout.addWidget(
            self.clear_canvas_button,
            len(selection_actions) // 2,
            0,
            1,
            2,
        )
        layout.addWidget(selection_group)

        palette_group = QGroupBox("RGB565 palette")
        palette_layout = QVBoxLayout(palette_group)
        self.color_label = QLabel("Paint 0xFFFF   Erase 0x0000")
        palette_layout.addWidget(self.color_label)
        self.palette_widget = QWidget()
        self.palette_grid = QGridLayout(self.palette_widget)
        self.palette_grid.setContentsMargins(0, 0, 0, 0)
        palette_layout.addWidget(self.palette_widget)
        custom_button = QPushButton("Choose color...")
        custom_button.clicked.connect(self._choose_primary_color)
        palette_layout.addWidget(custom_button)
        layout.addWidget(palette_group)

        preview_group = QGroupBox("Actual-size preview")
        preview_layout = QVBoxLayout(preview_group)
        self.preview_label = QLabel()
        self.preview_label.setMinimumSize(160, 160)
        self.preview_label.setMaximumSize(320, 320)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("background: #202020; border: 1px solid #555;")
        preview_layout.addWidget(self.preview_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(preview_group)

        warning_group = QGroupBox("Scanner notes")
        warning_layout = QVBoxLayout(warning_group)
        self.warning_text = QPlainTextEdit()
        self.warning_text.setReadOnly(True)
        self.warning_text.setMaximumHeight(120)
        warning_layout.addWidget(self.warning_text)
        layout.addWidget(warning_group)

        self.apply_button = QPushButton("Review and apply to Python")
        self.apply_button.setEnabled(False)
        layout.addWidget(self.apply_button)
        self.export_button = QPushButton("Export PNG")
        self.export_button.setEnabled(False)
        layout.addWidget(self.export_button)
        self.use_in_gui_button = QPushButton("Use in App GUI")
        self.use_in_gui_button.setEnabled(False)
        layout.addWidget(self.use_in_gui_button)
        layout.addStretch(1)
        scroll.setWidget(panel)
        return scroll

    def _connect_actions(self) -> None:
        """Connect actions and widget signals."""
        self.open_file_action.triggered.connect(self._open_file)
        self.open_folder_action.triggered.connect(self._open_folder)
        self.rescan_action.triggered.connect(self._rescan)
        self.export_action.triggered.connect(self._export_png)
        self.apply_action.triggered.connect(self._apply_to_source)
        self.quit_action.triggered.connect(self.close)
        self.undo_action.triggered.connect(self._undo_current)
        self.redo_action.triggered.connect(self._redo_current)
        self.tool_group.triggered.connect(self._tool_changed)
        self.cut_pixels_action.triggered.connect(self._cut_pixels)
        self.copy_pixels_action.triggered.connect(self._copy_pixels)
        self.paste_pixels_action.triggered.connect(self._paste_pixels)
        self.delete_pixels_action.triggered.connect(self._delete_pixels)
        self.select_all_pixels_action.triggered.connect(self.canvas.select_all)
        self.clear_selection_action.triggered.connect(self.canvas.clear_selection)
        self.flip_horizontal_action.triggered.connect(
            lambda: self.canvas.flip_selection(True)
        )
        self.flip_vertical_action.triggered.connect(
            lambda: self.canvas.flip_selection(False)
        )
        self.rotate_clockwise_action.triggered.connect(
            self.canvas.rotate_selection_clockwise
        )
        self.crop_selection_action.triggered.connect(self._crop_pixel_selection)
        self.resize_canvas_action.triggered.connect(self._resize_pixel_canvas)
        self.scale_artwork_action.triggered.connect(self._scale_pixel_artwork)
        self.clear_canvas_action.triggered.connect(self._clear_pixel_canvas)
        self.use_in_gui_action.triggered.connect(self._use_current_asset_in_gui)
        self.recover_pixel_action.triggered.connect(self._recover_pixel_asset)
        self.use_in_gui_button.clicked.connect(self.use_in_gui_action.trigger)
        self.canvas.addActions(
            (
                self.cut_pixels_action,
                self.copy_pixels_action,
                self.paste_pixels_action,
                self.delete_pixels_action,
                self.select_all_pixels_action,
                self.clear_selection_action,
            )
        )
        self.primary_button.clicked.connect(self._choose_primary_color)
        self.background_button.clicked.connect(self._choose_background_color)
        self.zoom_spin.valueChanged.connect(self.canvas.set_zoom)
        self.canvas.zoom_changed.connect(self.zoom_spin.setValue)
        self.grid_check.toggled.connect(self.canvas.set_grid_visible)
        self.search_edit.textChanged.connect(self._filter_assets)
        self.asset_list.currentRowChanged.connect(self._select_asset)
        self.canvas.color_picked.connect(self._set_color)
        self.canvas.document_changed.connect(self._canvas_changed)
        self.canvas.selection_changed.connect(self._pixel_selection_changed)
        self.canvas.cursor_changed.connect(self._cursor_changed)
        self.apply_button.clicked.connect(self._apply_to_source)
        self.export_button.clicked.connect(self._export_png)
        self.previous_frame_button.clicked.connect(self._previous_animation_frame)
        self.next_frame_button.clicked.connect(self._next_animation_frame)
        self.frame_combo.currentIndexChanged.connect(self._animation_frame_changed)
        self.play_button.toggled.connect(self._toggle_animation)
        self.onion_skin_check.toggled.connect(self._update_onion_skin)
        self.animation_timer.timeout.connect(self._advance_animation)
        self.add_frame_button.clicked.connect(self._add_animation_frame)
        self.duplicate_frame_button.clicked.connect(self._duplicate_animation_frame)
        self.delete_frame_button.clicked.connect(self._delete_animation_frame)
        self.move_frame_left_button.clicked.connect(
            lambda: self._move_animation_frame(-1)
        )
        self.move_frame_right_button.clicked.connect(
            lambda: self._move_animation_frame(1)
        )
        self.frame_interval_spin.valueChanged.connect(self.animation_timer.setInterval)
        self.open_reference_action.triggered.connect(self._open_reference_image)
        self.clear_reference_action.triggered.connect(self._clear_reference_image)
        self.import_frames_action.triggered.connect(self._import_animation_frames)
        self.new_graphic_action.triggered.connect(self._create_new_graphic)
        self.open_reference_button.clicked.connect(self._open_reference_image)
        self.clear_reference_button.clicked.connect(self._clear_reference_image)
        self.convert_reference_button.clicked.connect(self._convert_reference_image)
        self.import_frames_button.clicked.connect(self._import_animation_frames)
        self.new_graphic_button.clicked.connect(self._create_new_graphic)
        self.reference_opacity_slider.valueChanged.connect(
            self.canvas.set_reference_opacity
        )
        self.reference_foreground_check.toggled.connect(
            self.canvas.set_reference_foreground
        )
        self.reference_fit_combo.currentIndexChanged.connect(
            self._reference_options_changed
        )
        self.reference_rotation_combo.currentIndexChanged.connect(
            self._reference_options_changed
        )
        self.reference_scale_spin.valueChanged.connect(self._reference_options_changed)
        self.reference_x_spin.valueChanged.connect(self._reference_options_changed)
        self.reference_y_spin.valueChanged.connect(self._reference_options_changed)
        self.reference_flip_horizontal.toggled.connect(self._reference_options_changed)
        self.reference_flip_vertical.toggled.connect(self._reference_options_changed)
        self.new_gui_action.triggered.connect(self._new_gui_project)
        self.open_gui_action.triggered.connect(self._open_gui_project)
        self.save_gui_action.triggered.connect(self._save_gui_project)
        self.save_gui_as_action.triggered.connect(self._save_gui_project_as)
        self.recover_gui_action.triggered.connect(self._recover_gui_project)
        self.recover_saved_gui_action.triggered.connect(self._recover_saved_gui_project)
        self.export_gui_action.triggered.connect(self._export_gui_python)
        self.import_existing_app_action.triggered.connect(self._import_existing_app)
        self.apply_imported_app_action.triggered.connect(self._apply_imported_app_edits)
        self.workspace_tabs.currentChanged.connect(self._workspace_changed)
        self.screen_flow.open_screen_requested.connect(self._open_designed_screen)
        self.screen_designer.pixel_asset_edit_requested.connect(
            self._edit_gui_pixel_asset
        )
        self.designer_session.dirty_changed.connect(self._designer_dirty_changed)
        self.designer_session.dirty_changed.connect(self._schedule_designer_recovery)
        self.designer_session.project_changed.connect(self._update_import_actions)
        self.designer_session.project_changed.connect(self._schedule_designer_recovery)
        self.designer_session.history_changed.connect(self._update_history_actions)
        self.designer_session.history_changed.connect(self._schedule_designer_recovery)
        self.designer_recovery_timer.timeout.connect(self._write_designer_recovery)
        self.pixel_recovery_timer.timeout.connect(self._write_pixel_recovery)

    def _open_file(self) -> None:
        """Prompt for one Python source file."""
        if not self._confirm_discard():
            return
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Open Python renderer",
            str(
                self._scan_path.parent
                if self._scan_path and self._scan_path.is_file()
                else self._scan_path or Path.cwd()
            ),
            "Python files (*.py)",
        )
        if filename:
            self.open_path(filename)

    def _open_folder(self) -> None:
        """Prompt for a Python project folder."""
        if not self._confirm_discard():
            return
        folder = QFileDialog.getExistingDirectory(
            self,
            "Open Python project",
            str(
                self._scan_path.parent
                if self._scan_path and self._scan_path.is_file()
                else self._scan_path or Path.cwd()
            ),
        )
        if folder:
            self.open_path(folder)

    def _open_reference_image(self) -> None:
        """Open one image as a movable tracing reference."""
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Open reference image",
            str(Path.cwd()),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)",
        )
        if not filename:
            return
        frames = read_image_frames(filename)
        if not frames:
            QMessageBox.warning(
                self, "Cannot open image", "The selected image could not be decoded."
            )
            return
        image = frames[0]
        self.canvas.set_reference_image(image)
        self.reference_status_label.setText(
            f"{Path(filename).name} - {image.width()} x {image.height()}"
        )
        self.statusBar().showMessage(f"Reference loaded: {filename}")

    def _clear_reference_image(self) -> None:
        """Remove the current tracing reference."""
        self.canvas.set_reference_image(None)
        self.reference_status_label.setText("No reference loaded")

    def _reference_options_changed(self) -> None:
        """Apply reference placement controls to the canvas."""
        self.canvas.set_reference_options(
            str(self.reference_fit_combo.currentData()),
            int(self.reference_rotation_combo.currentData()),
            self.reference_flip_horizontal.isChecked(),
            self.reference_flip_vertical.isChecked(),
            self.reference_scale_spin.value(),
            self.reference_x_spin.value(),
            self.reference_y_spin.value(),
        )

    def _convert_reference_image(self) -> None:
        """Convert the positioned reference into editable RGB565 pixels."""
        converted = self.canvas.reference_art(
            self.reference_colors_spin.value(),
            self.reference_dither_check.isChecked(),
        )
        if converted is None:
            QMessageBox.information(
                self, "No reference", "Open a reference image before converting it."
            )
            return
        merged = self.canvas.art().copy()
        for y in range(converted.height):
            for x in range(converted.width):
                color = converted.pixel(x, y)
                if color is not None:
                    merged.set_pixel(x, y, color)
        self.canvas.apply_art(merged)
        self.statusBar().showMessage("Reference converted to editable RGB565 pixels.")

    def _import_animation_frames(self) -> None:
        """Import animated-image or sprite-sheet frames for editing."""
        if self.current_asset is None or self.animation_parameter is None:
            QMessageBox.information(
                self,
                "Select an animation",
                "Select a graphic with a frame, phase, or animation_time variant first.",
            )
            return
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Import animation frames",
            str(Path.cwd()),
            "Images (*.gif *.webp *.png *.bmp *.jpg *.jpeg)",
        )
        if not filename:
            return
        frames = read_image_frames(filename)
        if not frames:
            QMessageBox.warning(
                self, "Cannot open image", "The selected image could not be decoded."
            )
            return
        if len(frames) == 1:
            art = self.canvas.art()
            dialog = SpriteSheetDialog(frames[0], art.width, art.height, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            frames = split_sprite_sheet(frames[0], *dialog.settings())
        if not frames:
            QMessageBox.warning(
                self, "No frames", "The sprite-sheet settings produced no frames."
            )
            return
        self.animation_asset_key = self._asset_key(self.current_asset)
        self.animation_images.clear()
        self.animation_drafts.clear()
        values = [
            self.frame_combo.itemData(index)
            for index in range(self.frame_combo.count())
        ]
        for index in range(len(frames)):
            if index >= len(values):
                values.append(index)
                self.frame_combo.addItem(f"Frame {index}", index)
        for value, frame in zip(values, frames):
            self.animation_images[value] = frame.copy()
        self._refresh_animation_labels()
        self.frame_combo.setCurrentIndex(0)
        self._render_current()
        self.statusBar().showMessage(
            f"Imported {len(frames)} frames from {Path(filename).name}."
        )

    def _create_new_graphic(self) -> None:
        """Create a new Python graphic from reference pixels or frames."""
        current = self.canvas.art()
        dialog = NewGraphicDialog(
            current.width,
            current.height,
            self.canvas.has_reference_image(),
            len(self.animation_images),
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        function_name, width, height, creation_mode = dialog.settings()
        if not function_name:
            QMessageBox.information(
                self, "Function name required", "Enter a Python function name."
            )
            return
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Choose Python destination",
            str(Path.cwd() / "graphics.py"),
            "Python files (*.py)",
        )
        if not filename:
            return
        if not filename.endswith(".py"):
            filename += ".py"
        if creation_mode == "blank":
            frames = [PixelArt(width, height)]
        else:
            images = self._new_graphic_images(creation_mode, width, height)
            if not images:
                return
            frames = self._convert_new_graphic_images(images, width, height)
        try:
            patch = build_new_graphic_patch(filename, function_name, frames)
        except (OSError, SyntaxError, ValueError) as error:
            QMessageBox.critical(self, "Cannot create graphic", str(error))
            return
        review = DiffDialog(patch, self)
        if review.exec() != QDialog.DialogCode.Accepted:
            return
        backup_root = self._source_backup_root()
        try:
            backup = patch.apply(backup_root)
        except (OSError, SyntaxError, ValueError) as error:
            QMessageBox.critical(self, "Graphic creation failed", str(error))
            return
        detail = f"Backup: {backup}" if backup else "Created a new Python file."
        loaded = self._open_created_graphic(patch.path, patch.key)
        next_step = (
            "The new asset is loaded. Edit pixels and use Apply to Python."
            if loaded
            else "The file was saved, but you must open it manually before editing."
        )
        QMessageBox.information(
            self,
            "Graphic created",
            f"Updated {patch.path}.\n{detail}\n{next_step}",
        )

    def _new_graphic_images(
        self, creation_mode: str, width: int, height: int
    ) -> list[QImage]:
        """Return source images for one explicit new-asset mode."""
        if creation_mode == "current":
            return [pixel_art_image(self.canvas.art())]
        if creation_mode == "reference":
            reference = self.canvas.reference_source_image()
            return [reference] if reference is not None else []
        if creation_mode == "imported_frames":
            return [image.copy() for image in self.animation_images.values()]
        if creation_mode != "animation_file":
            return []
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Choose animation or sprite sheet",
            str(Path.cwd()),
            "Images (*.gif *.webp *.png *.bmp *.jpg *.jpeg)",
        )
        if not filename:
            return []
        images = read_image_frames(filename)
        if len(images) == 1:
            sheet_dialog = SpriteSheetDialog(images[0], width, height, self)
            if sheet_dialog.exec() != QDialog.DialogCode.Accepted:
                return []
            images = split_sprite_sheet(images[0], *sheet_dialog.settings())
        if not images:
            QMessageBox.warning(
                self,
                "No animation frames",
                "The selected file produced no animation frames.",
            )
        return images

    def _convert_new_graphic_images(
        self, images: list[QImage], width: int, height: int
    ) -> list[PixelArt]:
        """Convert new-asset source images into RGB565 frames."""
        frames: list[PixelArt] = []
        for image in images:
            prepared = prepare_reference_image(
                image,
                width,
                height,
                str(self.reference_fit_combo.currentData()),
                int(self.reference_rotation_combo.currentData()),
                self.reference_flip_horizontal.isChecked(),
                self.reference_flip_vertical.isChecked(),
                self.reference_scale_spin.value(),
                self.reference_x_spin.value(),
                self.reference_y_spin.value(),
            )
            frames.append(
                image_to_pixel_art(
                    prepared,
                    width,
                    height,
                    self.reference_colors_spin.value(),
                    self.reference_dither_check.isChecked(),
                )
            )
        return frames

    def _open_created_graphic(self, path: Path, patch_key: str) -> bool:
        """Scan and select the newly written pixel graphic function."""
        target = path.resolve()
        if (
            self._scan_folder
            and self._scan_path is not None
            and self._scan_path.resolve() in target.parents
        ):
            scan_path = self._scan_path
            scan_folder = True
        else:
            self._scan_path = target
            self._scan_folder = False
            scan_path = target
            scan_folder = False
        self._scan(scan_path, scan_folder)
        expected_name = patch_key.removeprefix("new-graphic-")
        row = next(
            (
                index
                for index, asset in enumerate(self.assets)
                if asset.document.path.resolve() == target
                and asset.record.name == expected_name
            ),
            None,
        )
        if row is None:
            QMessageBox.warning(
                self,
                "Graphic saved but not loaded",
                f"Saved {target}, but its generated function was not discovered.",
            )
            return False
        self.asset_list.setCurrentRow(row)
        self.workspace_tabs.setCurrentIndex(0)
        self.statusBar().showMessage(f"Created and loaded pixel asset: {target}")
        return True

    def _workspace_changed(self, index: int) -> None:
        """Show tools relevant to the selected workspace."""
        self.tool_bar.setVisible(index == 0)
        labels = ("Pixel Art", "App GUI", "Screen Flow")
        self.statusBar().showMessage(f"Workspace: {labels[index]}")
        self._update_history_actions()

    def _undo_current(self) -> None:
        """Undo in the currently visible editor workspace."""
        if self.workspace_tabs.currentIndex() == 0:
            self.canvas.undo()
        else:
            self.designer_session.undo()
        self._update_history_actions()

    def _redo_current(self) -> None:
        """Redo in the currently visible editor workspace."""
        if self.workspace_tabs.currentIndex() == 0:
            self.canvas.redo()
        else:
            self.designer_session.redo()
        self._update_history_actions()

    def _update_history_actions(self, *args: object) -> None:
        """Enable undo and redo for the active workspace history."""
        if not hasattr(self, "workspace_tabs"):
            return
        if self.workspace_tabs.currentIndex() == 0:
            self.undo_action.setEnabled(self.canvas.can_undo())
            self.redo_action.setEnabled(self.canvas.can_redo())
        else:
            self.undo_action.setEnabled(self.designer_session.can_undo())
            self.redo_action.setEnabled(self.designer_session.can_redo())

    def _open_designed_screen(self, screen_id: str) -> None:
        """Open a graph screen in the visual GUI designer."""
        self.designer_session.set_active_screen(screen_id)
        self.workspace_tabs.setCurrentIndex(1)

    def _designer_dirty_changed(self, dirty: bool) -> None:
        """Report designer project save state."""
        if dirty:
            self.statusBar().showMessage("GUI project has unsaved changes.")
        else:
            self.statusBar().showMessage("GUI project saved.")

    def _new_gui_project(self) -> None:
        """Create a new visual GUI project."""
        if not self._confirm_designer_discard():
            return
        name, accepted = QInputDialog.getText(
            self, "New GUI project", "Project name", text="Untitled GUI"
        )
        if not accepted or not name.strip():
            return
        self.designer_session.set_project(GuiProject.create(name.strip()))
        self.workspace_tabs.setCurrentIndex(1)

    def _open_gui_project(self) -> None:
        """Open a persisted GUI designer project."""
        if not self._confirm_designer_discard():
            return
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Open GUI project",
            str(Path.cwd()),
            "Pico GUI projects (*.picogui.json);;JSON files (*.json)",
        )
        if filename:
            self._load_gui_project(Path(filename))

    def _load_gui_project(self, path: Path) -> None:
        """Load a GUI project path with error reporting."""
        try:
            project = GuiProject.load(path)
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.critical(self, "Cannot open GUI project", str(error))
            return
        self.designer_session.set_project(project, path)
        self.workspace_tabs.setCurrentIndex(1)
        self.statusBar().showMessage(f"Opened GUI project: {path}")

    def _save_gui_project(self) -> bool:
        """Save the GUI project to its current path."""
        if self.designer_session.path is None:
            return self._save_gui_project_as()
        return self._save_gui_project_to(self.designer_session.path)

    def _save_gui_project_as(self) -> bool:
        """Prompt for a new GUI project path and save it."""
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save GUI project",
            str(self.designer_session.path or Path.cwd() / "gui-design.picogui.json"),
            "Pico GUI projects (*.picogui.json)",
        )
        if not filename:
            return False
        if not filename.endswith(".picogui.json"):
            filename += ".picogui.json"
        return self._save_gui_project_to(Path(filename))

    def _save_gui_project_to(self, path: Path) -> bool:
        """Save a GUI project path with error reporting."""
        backup_root = self._gui_backup_root()
        try:
            if path.exists():
                backup_project(path, backup_root)
            self.designer_session.save(path)
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.critical(self, "Cannot save GUI project", str(error))
            return False
        try:
            saved_backup = backup_project(path, backup_root)
        except OSError as error:
            QMessageBox.warning(
                self,
                "GUI project saved without backup",
                f"Saved {path}, but the safety copy failed:\n{error}",
            )
            saved_backup = None
        self._clear_designer_recovery()
        detail = f" | Safety copy: {saved_backup}" if saved_backup else ""
        self.statusBar().showMessage(f"Saved GUI project: {path}{detail}")
        return True

    def _gui_backup_root(self) -> Path:
        """Return the independent saved-project backup directory."""
        return (
            Path(
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.AppDataLocation
                )
            )
            / "backups"
            / "gui-projects"
        )

    def _source_backup_root(self) -> Path:
        """Return the source-edit backup directory."""
        return (
            Path(
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.AppDataLocation
                )
            )
            / "backups"
        )

    def _designer_recovery_path(self) -> Path:
        """Return the autosaved GUI recovery project path."""
        return (
            Path(
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.AppDataLocation
                )
            )
            / "recovery"
            / "last-gui-session.picogui.json"
        )

    def _pixel_recovery_path(self) -> Path:
        """Return the autosaved pixel draft recovery path."""
        return (
            Path(
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.AppDataLocation
                )
            )
            / "recovery"
            / "last-pixel-session.json"
        )

    def _schedule_pixel_recovery(self) -> None:
        """Debounce recovery writes for a dirty pixel asset."""
        if self._dirty:
            self.pixel_recovery_timer.start()

    def _write_pixel_recovery(self) -> None:
        """Atomically write the current dirty pixel asset draft."""
        if not self._dirty or self.current_asset is None:
            return
        art = self.canvas.art()
        payload = {
            "format_version": 1,
            "source_path": str(self.current_asset.document.path.resolve()),
            "qualified_name": self.current_asset.record.qualified_name,
            "variants": self.variant_values,
            "width": art.width,
            "height": art.height,
            "origin_x": art.origin_x,
            "origin_y": art.origin_y,
            "pixels": art.pixels,
        }
        target = self._pixel_recovery_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, text=True
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
                json.dump(payload, temporary, indent=2)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, target)
        except OSError as error:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
            self.statusBar().showMessage(f"Pixel recovery failed: {error}")
            return
        self._update_recovery_action()

    def _recover_pixel_asset(self) -> None:
        """Restore the most recent autosaved pixel asset draft."""
        recovery_path = self._pixel_recovery_path()
        try:
            payload = json.loads(recovery_path.read_text(encoding="utf-8"))
            source_path = Path(str(payload["source_path"])).resolve()
            qualified_name = str(payload["qualified_name"])
            width = int(payload["width"])
            height = int(payload["height"])
            pixels = payload["pixels"]
            if not source_path.is_file():
                raise ValueError("The recovered source file no longer exists")
            if not 1 <= width <= 320 or not 1 <= height <= 320:
                raise ValueError("Recovered pixel dimensions are invalid")
            if not isinstance(pixels, list) or len(pixels) != width * height:
                raise ValueError("Recovered pixel data is invalid")
            normalized_pixels = [
                None if value is None else int(value) & 0xFFFF for value in pixels
            ]
        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            QMessageBox.critical(self, "Cannot recover pixel asset", str(error))
            self._update_recovery_action()
            return
        if not self._confirm_discard():
            return
        self._scan_path = source_path
        self._scan_folder = False
        self._scan(source_path, False)
        row = next(
            (
                index
                for index, asset in enumerate(self.assets)
                if asset.record.qualified_name == qualified_name
            ),
            None,
        )
        if row is None:
            QMessageBox.critical(
                self,
                "Cannot recover pixel asset",
                "The recovered graphic function was not discovered.",
            )
            return
        self.asset_list.setCurrentRow(row)
        variants = payload.get("variants", {})
        if isinstance(variants, dict):
            for name, value in variants.items():
                if name == self.animation_parameter:
                    index = self.frame_combo.findData(value)
                    if index >= 0:
                        self.frame_combo.setCurrentIndex(index)
                elif name in self.variant_controls:
                    index = self.variant_controls[name].findData(value)
                    if index >= 0:
                        self.variant_controls[name].setCurrentIndex(index)
        recovered = PixelArt(
            width,
            height,
            int(payload.get("origin_x", 0)),
            int(payload.get("origin_y", 0)),
            normalized_pixels,
        )
        self._suppress_changes = True
        self.canvas.set_art(recovered)
        self._suppress_changes = False
        self._dirty = True
        self._update_preview()
        self._update_apply_state()
        self.workspace_tabs.setCurrentIndex(0)
        self.statusBar().showMessage(f"Recovered unsaved pixel asset: {source_path}")

    def _clear_pixel_recovery(self) -> None:
        """Remove pixel recovery after saving or explicit discard."""
        self.pixel_recovery_timer.stop()
        try:
            self._pixel_recovery_path().unlink(missing_ok=True)
        except OSError:
            pass
        self._update_recovery_action()

    def _schedule_designer_recovery(self, *args: object) -> None:
        """Debounce recovery writes for a dirty GUI project."""
        if self.designer_session.dirty:
            self.designer_recovery_timer.start()

    def _write_designer_recovery(self) -> None:
        """Write the current dirty GUI project as an atomic recovery file."""
        if not self.designer_session.dirty:
            return
        try:
            self.designer_session.project.save(self._designer_recovery_path())
        except OSError as error:
            self.statusBar().showMessage(f"GUI recovery failed: {error}")
            return
        self._update_recovery_action()

    def _recover_gui_project(self) -> None:
        """Open the most recent autosaved GUI designer project."""
        path = self._designer_recovery_path()
        try:
            project = GuiProject.load(path)
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.critical(self, "Cannot recover GUI project", str(error))
            self._update_recovery_action()
            return
        if not self._confirm_designer_discard():
            return
        self.designer_session.set_project(project)
        self.designer_session.mark_changed(False)
        self.workspace_tabs.setCurrentIndex(1)
        self.statusBar().showMessage(f"Recovered GUI project from {path}")

    def _recover_saved_gui_project(self) -> None:
        """Recover one independently stored explicit-save backup."""
        backup_root = self._gui_backup_root()
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Recover saved GUI backup",
            str(backup_root),
            "Pico GUI backups (*.bak *.picogui.json);;All files (*)",
        )
        if not filename:
            return
        path = Path(filename)
        try:
            project = GuiProject.load(path)
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.critical(self, "Cannot recover GUI backup", str(error))
            return
        if not self._confirm_designer_discard():
            return
        self.designer_session.set_project(project)
        self.designer_session.mark_changed(False)
        self.workspace_tabs.setCurrentIndex(1)
        self.statusBar().showMessage(f"Recovered saved GUI backup: {path}")

    def _clear_designer_recovery(self) -> None:
        """Remove the recovery project after a successful explicit save."""
        self.designer_recovery_timer.stop()
        try:
            self._designer_recovery_path().unlink(missing_ok=True)
        except OSError:
            pass
        self._update_recovery_action()

    def _update_recovery_action(self) -> None:
        """Enable recovery only when an autosave is available."""
        self.recover_gui_action.setEnabled(self._designer_recovery_path().is_file())
        self.recover_pixel_action.setEnabled(self._pixel_recovery_path().is_file())

    def _import_existing_app(self) -> None:
        """Scan an existing Python app into source-backed GUI screens."""
        if not self._confirm_designer_discard():
            return
        target_dialog = AppImportTargetDialog(self)
        if target_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        path = target_dialog.selected_path
        if path is None:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.statusBar().showMessage(f"Scanning existing app: {path}")
        QApplication.processEvents()
        try:
            result = self.app_importer.import_path(
                path, self.designer_session.project.profile
            )
        except (OSError, SyntaxError, ValueError) as error:
            QMessageBox.critical(self, "App import failed", str(error))
            return
        finally:
            QApplication.restoreOverrideCursor()
        review = AppImportReviewDialog(result, self)
        if review.exec() != QDialog.DialogCode.Accepted:
            return
        self.designer_session.set_project(result.project)
        self.designer_session.mark_changed(False)
        self.workspace_tabs.setCurrentIndex(1)
        self.statusBar().showMessage(
            f"Imported {len(result.project.screens)} screens from {path}."
        )

    def _apply_imported_app_edits(self) -> None:
        """Review and apply narrow patches to an imported application."""
        project = self.designer_session.project
        if not project.imported_sources:
            QMessageBox.information(
                self,
                "No imported app",
                "Import an existing application before applying source-backed edits.",
            )
            return
        try:
            patches = build_imported_app_patches(project)
        except (OSError, SyntaxError, ValueError) as error:
            QMessageBox.critical(self, "Cannot build app patches", str(error))
            return
        if not patches:
            QMessageBox.information(
                self,
                "No source changes",
                "No editable imported calls or relations have changed.",
            )
            return
        review = MultiPatchDialog(patches, self)
        if review.exec() != QDialog.DialogCode.Accepted:
            return
        for patch in patches:
            try:
                current = patch.path.read_text(encoding="utf-8")
            except OSError as error:
                QMessageBox.critical(self, "Cannot verify app source", str(error))
                return
            if current != patch.original:
                QMessageBox.warning(
                    self,
                    "App source changed",
                    f"{patch.path} changed while the diff was open. Import it again.",
                )
                return
        backup_root = (
            Path(
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.AppDataLocation
                )
            )
            / "backups"
        )
        backups: list[Path] = []
        applied: list[SourcePatch] = []
        try:
            for patch in patches:
                backup = patch.apply(backup_root)
                if backup is not None:
                    backups.append(backup)
                applied.append(patch)
        except (OSError, SyntaxError, ValueError) as error:
            restored = 0
            for patch in reversed(applied):
                try:
                    rollback = SourcePatch(
                        patch.path,
                        patch.updated,
                        patch.original,
                        "",
                        "rollback",
                        0,
                    )
                    rollback.apply(backup_root)
                    restored += 1
                except (OSError, SyntaxError, ValueError):
                    break
            QMessageBox.critical(
                self,
                "Existing app update failed",
                f"Applied {len(applied)} of {len(patches)} files and restored {restored}.\n{error}",
            )
            return
        refresh_import_metadata(project, patches)
        self.designer_session.mark_changed(False)
        detail = f"{len(backups)} backups created in {backup_root}."
        QMessageBox.information(
            self,
            "Existing app updated",
            f"Updated {len(patches)} Python files.\n{detail}",
        )

    def _update_import_actions(self) -> None:
        """Enable existing-app actions for imported projects."""
        self.apply_imported_app_action.setEnabled(
            bool(self.designer_session.project.imported_sources)
        )

    def _export_gui_python(self) -> None:
        """Review and export the GUI design as marked Python code."""
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export GUI to Python",
            str(Path.cwd() / "generated_gui.py"),
            "Python files (*.py)",
        )
        if not filename:
            return
        if not filename.endswith(".py"):
            filename += ".py"
        try:
            patch = build_designer_patch(self.designer_session.project, filename)
        except (OSError, SyntaxError, ValueError) as error:
            QMessageBox.critical(self, "Cannot export GUI", str(error))
            return
        dialog = DiffDialog(patch, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        backup_root = (
            Path(
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.AppDataLocation
                )
            )
            / "backups"
        )
        try:
            backup = patch.apply(backup_root)
        except (OSError, SyntaxError, ValueError) as error:
            QMessageBox.critical(self, "GUI export failed", str(error))
            return
        detail = f"Backup: {backup}" if backup else "Created a new Python file."
        QMessageBox.information(
            self, "GUI exported", f"Updated {patch.path}.\n{detail}"
        )

    def _confirm_designer_discard(self) -> bool:
        """Confirm closing or replacing an unsaved GUI project."""
        if not self.designer_session.dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Save GUI project?",
            "The GUI project has unsaved changes.",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self._save_gui_project()
        if answer == QMessageBox.StandardButton.Discard:
            self._clear_designer_recovery()
            return True
        return False

    def _scan(self, path: Path, folder: bool) -> None:
        """Scan a selected source path and refresh the catalogue."""
        previous_key = self._asset_key(self.current_asset)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.statusBar().showMessage(f"Scanning {path}...")
        QApplication.processEvents()
        try:
            assets = (
                self.scanner.scan_folder(path)
                if folder
                else self.scanner.scan_file(path)
            )
        except (OSError, SyntaxError, UnicodeError) as error:
            QMessageBox.critical(self, "Scan failed", str(error))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.assets = assets
        self.screen_designer.set_pixel_assets([])
        self._thumbnail_generation += 1
        generation = self._thumbnail_generation
        self._thumbnail_queue = list(range(len(assets)))
        self.current_asset = None
        self.current_trace = None
        self._dirty = False
        self._animation_structure_dirty = False
        self.asset_list.clear()
        for index, asset in enumerate(assets):
            item = QListWidgetItem()
            item.setText(f"{asset.category}\n{asset.record.name}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setToolTip(
                f"{asset.record.qualified_name}\n{asset.document.path}:{asset.record.node.lineno}"
            )
            self.asset_list.addItem(item)
        self.asset_count_label.setText(f"{len(assets)} graphics found")
        self.statusBar().showMessage(f"Found {len(assets)} graphics in {path}")
        if assets:
            preferred_row = next(
                (
                    index
                    for index, asset in enumerate(assets)
                    if self._asset_key(asset) == previous_key
                ),
                0,
            )
            self.asset_list.setCurrentRow(preferred_row)
            QTimer.singleShot(
                0,
                lambda current_generation=generation: self._render_next_thumbnail(
                    current_generation
                ),
            )
        else:
            self._clear_editor()

    def _render_next_thumbnail(self, generation: int) -> None:
        """Render one catalogue thumbnail without blocking the UI."""
        if generation != self._thumbnail_generation or not self._thumbnail_queue:
            return
        index = self._thumbnail_queue.pop(0)
        if index < len(self.assets) and index < self.asset_list.count():
            asset = self.assets[index]
            item = self.asset_list.item(index)
            try:
                trace = self.thumbnail_tracer.render(asset)
                image = pixel_art_image(trace.current_art, False)
                pixmap = QPixmap.fromImage(image).scaled(
                    64,
                    64,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation,
                )
                item.setIcon(QIcon(pixmap))
                self._publish_gui_pixel_asset(asset, trace.current_art)
            except Exception:
                pass
        if self._thumbnail_queue:
            QTimer.singleShot(0, lambda: self._render_next_thumbnail(generation))

    def _rescan(self) -> None:
        """Rescan the last opened source path."""
        if self._scan_path is None or not self._confirm_discard():
            return
        self._scan(self._scan_path, self._scan_folder)

    def _filter_assets(self, text: str) -> None:
        """Filter catalogue items by label and source path."""
        needle = text.strip().lower()
        visible = 0
        for row in range(self.asset_list.count()):
            item = self.asset_list.item(row)
            index = item.data(Qt.ItemDataRole.UserRole)
            asset = self.assets[index]
            haystack = f"{asset.category} {asset.record.qualified_name} {asset.document.path}".lower()
            hidden = bool(needle and needle not in haystack)
            item.setHidden(hidden)
            if not hidden:
                visible += 1
        self.asset_count_label.setText(f"{visible} of {len(self.assets)} graphics")

    def _select_asset(self, row: int) -> None:
        """Load the selected catalogue asset."""
        if row < 0:
            return
        item = self.asset_list.item(row)
        if item is None:
            return
        index = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(index, int) or index >= len(self.assets):
            return
        if self._dirty and not self._confirm_discard():
            self._restore_asset_selection()
            return
        self._load_asset(self.assets[index])

    def _restore_asset_selection(self) -> None:
        """Restore the catalogue row for the active asset."""
        if self.current_asset is None:
            return
        try:
            index = self.assets.index(self.current_asset)
        except ValueError:
            return
        self.asset_list.blockSignals(True)
        self.asset_list.setCurrentRow(index)
        self.asset_list.blockSignals(False)

    def _load_asset(self, asset: GraphicsAsset) -> None:
        """Render one asset and populate its controls."""
        self._stop_animation()
        asset_key = self._asset_key(asset)
        if self.animation_asset_key not in {None, asset_key}:
            self.animation_asset_key = None
            self.animation_images.clear()
            self.animation_drafts.clear()
        self.current_asset = asset
        managed = is_managed_graphic(asset)
        self.canvas.set_transparent_eraser(managed)
        if managed:
            self.asset_mode_label.setText(
                "Managed asset: fully editable, including transparency and canvas size."
            )
            self.asset_mode_label.setStyleSheet("color: #2e7d32; font-weight: 600;")
        else:
            self.asset_mode_label.setText(
                "Source-backed asset: drawing overlays are safe; transparent deletion and resizing are disabled."
            )
            self.asset_mode_label.setStyleSheet("color: #ef6c00; font-weight: 600;")
        self.variant_values = {
            name: asset.parameters.get(name, values[0] if values else 0)
            for name, values in asset.variants.items()
        }
        self._rebuild_variants(asset)
        self._render_current()
        self.asset_title.setText(f"{asset.category}: {asset.record.qualified_name}")
        self.source_label.setText(
            f"{asset.document.path}\nLine {asset.record.node.lineno}"
        )
        self.export_button.setEnabled(True)
        self.export_action.setEnabled(True)
        self._update_pixel_action_state()

    def _rebuild_variants(self, asset: GraphicsAsset) -> None:
        """Rebuild controls for inferred renderer variants."""
        while self.variant_form.rowCount():
            self.variant_form.removeRow(0)
        self.variant_controls.clear()
        self.animation_parameter = next(
            (
                name
                for name in ("frame", "phase", "animation_time")
                if name in asset.variants
            ),
            None,
        )
        self.frame_combo.blockSignals(True)
        self.frame_combo.clear()
        if self.animation_parameter is not None:
            for value in asset.variants[self.animation_parameter]:
                self.frame_combo.addItem(
                    self._animation_frame_label(self.animation_parameter, value), value
                )
            if self.animation_asset_key == self._asset_key(asset):
                existing = {
                    self.frame_combo.itemData(index)
                    for index in range(self.frame_combo.count())
                }
                for value in self.animation_images:
                    if value not in existing:
                        self.frame_combo.addItem(
                            self._animation_frame_label(
                                self.animation_parameter, value
                            ),
                            value,
                        )
            current = self.variant_values.get(self.animation_parameter)
            self.frame_combo.setCurrentIndex(max(0, self.frame_combo.findData(current)))
        self.frame_combo.blockSignals(False)
        self._refresh_animation_labels()
        self.animation_group.setVisible(self.animation_parameter is not None)
        for name, values in asset.variants.items():
            if name == self.animation_parameter:
                continue
            combo = QComboBox()
            for value in values:
                combo.addItem(str(value), value)
            current = self.variant_values.get(name)
            index = combo.findData(current)
            combo.setCurrentIndex(max(0, index))
            combo.currentIndexChanged.connect(self._variant_changed)
            self.variant_form.addRow(name, combo)
            self.variant_controls[name] = combo
        self.variant_group.setVisible(bool(self.variant_controls))

    def _animation_frame_label(self, parameter: str, value: Any) -> str:
        """Return a readable inferred frame label."""
        if parameter == "animation_time":
            return f"{value} ms"
        return f"Frame {value}"

    def _refresh_animation_labels(self) -> None:
        """Mark externally imported frames in the selector."""
        if self.animation_parameter is None:
            return
        for index in range(self.frame_combo.count()):
            value = self.frame_combo.itemData(index)
            label = self._animation_frame_label(self.animation_parameter, value)
            if value in self.animation_images:
                label += " - imported"
            self.frame_combo.setItemText(index, label)

    def _variant_changed(self) -> None:
        """Render the newly selected parameter variant."""
        if self._dirty and not self._confirm_discard():
            for name, combo in self.variant_controls.items():
                combo.blockSignals(True)
                combo.setCurrentIndex(
                    max(0, combo.findData(self.variant_values.get(name)))
                )
                combo.blockSignals(False)
            return
        for name, combo in self.variant_controls.items():
            self.variant_values[name] = combo.currentData()
        self._render_current()

    def _animation_frame_changed(self) -> None:
        """Render the selected animation frame."""
        if self.animation_parameter is None or self.frame_combo.currentIndex() < 0:
            return
        if self._dirty and not self._confirm_discard():
            self.frame_combo.blockSignals(True)
            self.frame_combo.setCurrentIndex(
                max(
                    0,
                    self.frame_combo.findData(
                        self.variant_values.get(self.animation_parameter)
                    ),
                )
            )
            self.frame_combo.blockSignals(False)
            self._stop_animation()
            return
        self.variant_values[self.animation_parameter] = self.frame_combo.currentData()
        self._render_current()

    def _previous_animation_frame(self) -> None:
        """Select the preceding animation frame."""
        count = self.frame_combo.count()
        if count:
            self.frame_combo.setCurrentIndex(
                (self.frame_combo.currentIndex() - 1) % count
            )

    def _next_animation_frame(self) -> None:
        """Select the following animation frame."""
        count = self.frame_combo.count()
        if count:
            self.frame_combo.setCurrentIndex(
                (self.frame_combo.currentIndex() + 1) % count
            )

    def _add_animation_frame(self) -> None:
        """Add a new editable animation frame value."""
        if self.current_asset is None or self.animation_parameter is None:
            return
        value = self._next_animation_value()
        self.animation_asset_key = self._asset_key(self.current_asset)
        draft = (
            self.current_trace.current_art.copy()
            if self.current_trace
            else self.canvas.art().copy()
        )
        self.animation_drafts[value] = draft
        self.animation_images[value] = pixel_art_image(draft)
        self.frame_combo.addItem(
            self._animation_frame_label(self.animation_parameter, value), value
        )
        self._refresh_animation_labels()
        self.frame_combo.setCurrentIndex(self.frame_combo.count() - 1)
        if self._current_asset_is_managed():
            self._mark_animation_structure_changed()

    def _duplicate_animation_frame(self) -> None:
        """Duplicate the current animation frame into a new value."""
        if self.current_asset is None or self.animation_parameter is None:
            return
        value = self._next_animation_value()
        self.animation_asset_key = self._asset_key(self.current_asset)
        draft = self.canvas.art().copy()
        self.animation_drafts[value] = draft
        self.animation_images[value] = pixel_art_image(draft)
        self.frame_combo.addItem(
            self._animation_frame_label(self.animation_parameter, value), value
        )
        self._refresh_animation_labels()
        self.frame_combo.setCurrentIndex(self.frame_combo.count() - 1)
        if self._current_asset_is_managed():
            self._mark_animation_structure_changed()

    def _delete_animation_frame(self) -> None:
        """Remove one imported or newly added animation frame."""
        if self.frame_combo.count() <= 1:
            return
        if self._dirty and not self._confirm_discard():
            return
        value = self.frame_combo.currentData()
        if value not in self.animation_images and not self._current_asset_is_managed():
            QMessageBox.information(
                self,
                "Source frame retained",
                "Frames inferred from handwritten Python cannot be deleted here.",
            )
            return
        index = self.frame_combo.currentIndex()
        self.animation_images.pop(value, None)
        self.animation_drafts.pop(value, None)
        self.frame_combo.removeItem(index)
        self.frame_combo.setCurrentIndex(min(index, self.frame_combo.count() - 1))
        if self._current_asset_is_managed():
            self._mark_animation_structure_changed()

    def _move_animation_frame(self, direction: int) -> None:
        """Move the current frame within preview playback order."""
        index = self.frame_combo.currentIndex()
        target = index + direction
        if index < 0 or target < 0 or target >= self.frame_combo.count():
            return
        text = self.frame_combo.itemText(index)
        value = self.frame_combo.itemData(index)
        self.frame_combo.blockSignals(True)
        self.frame_combo.removeItem(index)
        self.frame_combo.insertItem(target, text, value)
        self.frame_combo.setCurrentIndex(target)
        self.frame_combo.blockSignals(False)
        if self._current_asset_is_managed():
            self._mark_animation_structure_changed()

    def _mark_animation_structure_changed(self) -> None:
        """Mark managed frame additions, deletions, or ordering as unsaved."""
        self._animation_structure_dirty = True
        self._dirty = True
        self._update_apply_state()
        self._schedule_pixel_recovery()

    def _next_animation_value(self) -> int:
        """Return the next numeric frame or timing value."""
        values = [
            self.frame_combo.itemData(index)
            for index in range(self.frame_combo.count())
        ]
        integers = [value for value in values if type(value) is int]
        current_max = max(integers, default=-1)
        if self.animation_parameter == "animation_time":
            return current_max + self.frame_interval_spin.value()
        return current_max + 1

    def _toggle_animation(self, playing: bool) -> None:
        """Start or stop animation preview playback."""
        if playing and self._dirty:
            self.statusBar().showMessage(
                "Apply or discard the current frame edit before playback."
            )
            self._stop_animation()
            return
        self.play_button.setText("Stop" if playing else "Play")
        if playing and self.frame_combo.count() > 1:
            self.animation_timer.start()
        else:
            self.animation_timer.stop()
            if playing:
                self.play_button.blockSignals(True)
                self.play_button.setChecked(False)
                self.play_button.blockSignals(False)
                self.play_button.setText("Play")

    def _advance_animation(self) -> None:
        """Advance playback to the next frame."""
        if self._dirty:
            self._stop_animation()
            return
        self._next_animation_frame()

    def _stop_animation(self) -> None:
        """Stop frame preview playback."""
        self.animation_timer.stop()
        self.play_button.blockSignals(True)
        self.play_button.setChecked(False)
        self.play_button.blockSignals(False)
        self.play_button.setText("Play")

    def _update_onion_skin(self) -> None:
        """Overlay the preceding animation frame when requested."""
        if (
            not self.onion_skin_check.isChecked()
            or self.current_asset is None
            or self.animation_parameter is None
            or self.frame_combo.count() < 2
        ):
            self.canvas.set_onion_art(None)
            return
        current_index = self.frame_combo.currentIndex()
        if current_index <= 0:
            self.canvas.set_onion_art(None)
            return
        previous_values = dict(self.variant_values)
        previous_values[self.animation_parameter] = self.frame_combo.itemData(
            current_index - 1
        )
        try:
            previous_trace = self.tracer.render(self.current_asset, previous_values)
        except Exception:
            self.canvas.set_onion_art(None)
            return
        self.canvas.set_onion_art(
            self._animation_art(
                previous_trace,
                previous_values[self.animation_parameter],
            )
        )

    def _render_current(self) -> None:
        """Trace and display the current graphics function."""
        if self.current_asset is None:
            return
        try:
            trace = self.tracer.render(self.current_asset, self.variant_values)
        except Exception as error:
            self.warning_text.setPlainText(f"Preview failed: {error}")
            self.apply_button.setEnabled(False)
            return
        self.current_trace = trace
        displayed_art = self._animation_art(trace)
        self._publish_gui_pixel_asset(self.current_asset, displayed_art)
        self._suppress_changes = True
        self.canvas.set_art(displayed_art)
        self._suppress_changes = False
        self._update_onion_skin()
        self._dirty = self._animation_structure_dirty
        self._update_palette(trace.current_art)
        self._update_preview()
        notes = trace.warnings[:20]
        if not trace.primitives:
            notes.insert(0, "No supported drawing calls rendered for this variant.")
        if self.animation_parameter is not None:
            notes.append(
                "Edit one frame at a time and apply it before selecting another frame."
            )
        self.warning_text.setPlainText(
            "\n".join(notes) if notes else "Ready for pixel editing."
        )
        self._update_apply_state()

    def _canvas_changed(self) -> None:
        """Update dirty state and previews after painting."""
        self._update_history_actions()
        if self._suppress_changes or self.current_trace is None:
            return
        try:
            self._dirty = self._animation_structure_dirty or bool(
                self.canvas.art().changed_pixels(self.current_trace.current_art)
            )
        except ValueError:
            self._dirty = True
        if self.animation_parameter is not None:
            frame_value = self.variant_values.get(self.animation_parameter)
            if frame_value in self.animation_images:
                self.animation_drafts[frame_value] = self.canvas.art().copy()
        self._update_preview()
        self._update_apply_state()
        self._schedule_pixel_recovery()

    def _update_apply_state(self) -> None:
        """Enable source applying when an edit exists."""
        enabled = (
            self._dirty
            and self.current_asset is not None
            and self.current_trace is not None
        )
        self.apply_button.setEnabled(enabled)
        self.apply_action.setEnabled(enabled)
        apply_text = (
            "Review and save managed asset"
            if self._current_asset_is_managed()
            else "Review and apply to Python"
        )
        self.apply_button.setText(apply_text)
        self.apply_action.setText(apply_text + "...")
        title = "Pico Graphics and GUI Designer"
        if self.current_asset is not None:
            title += f" - {self.current_asset.record.name}"
        self.setWindowTitle(title + (" *" if self._dirty else ""))

    def _update_preview(self) -> None:
        """Refresh the actual-size pixel preview."""
        art = self.canvas.art()
        pixmap = QPixmap.fromImage(pixel_art_image(art))
        if pixmap.width() > 300 or pixmap.height() > 300:
            pixmap = pixmap.scaled(
                300,
                300,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        self.preview_label.setPixmap(pixmap)
        self.preview_label.setToolTip(
            f"{art.width} x {art.height}, origin {art.origin_x}, {art.origin_y}"
        )

    def _current_asset_is_managed(self) -> bool:
        """Return whether the selected asset is fully editor-managed."""
        return self.current_asset is not None and is_managed_graphic(self.current_asset)

    def _pixel_selection_changed(self, selected: bool) -> None:
        """Update pixel editing controls for selection availability."""
        self._update_pixel_action_state()

    def _update_pixel_action_state(self) -> None:
        """Enable pixel operations supported by the active asset mode."""
        available = self.current_asset is not None
        selected = self.canvas.selection() is not None
        managed = self._current_asset_is_managed()
        self.select_all_pixels_action.setEnabled(available)
        self.clear_selection_action.setEnabled(selected)
        self.copy_pixels_action.setEnabled(selected)
        for action in (
            self.cut_pixels_action,
            self.delete_pixels_action,
            self.flip_horizontal_action,
            self.flip_vertical_action,
            self.rotate_clockwise_action,
            self.crop_selection_action,
        ):
            action.setEnabled(managed and selected)
        self.paste_pixels_action.setEnabled(managed and self.canvas.has_clipboard())
        dimensions_editable = managed and self.animation_parameter is None
        self.resize_canvas_action.setEnabled(dimensions_editable)
        self.scale_artwork_action.setEnabled(dimensions_editable)
        self.clear_canvas_action.setEnabled(managed)
        self.use_in_gui_action.setEnabled(available)
        self.use_in_gui_button.setEnabled(available)

    def _copy_pixels(self) -> None:
        """Copy the current pixel selection."""
        if self.canvas.copy_selection():
            self.statusBar().showMessage("Copied selected pixels.")
        self._update_pixel_action_state()

    def _cut_pixels(self) -> None:
        """Cut selected pixels from a managed asset."""
        if self._current_asset_is_managed() and self.canvas.cut_selection():
            self.statusBar().showMessage("Cut selected pixels.")
        self._update_pixel_action_state()

    def _paste_pixels(self) -> None:
        """Paste pixels into a managed asset."""
        if self._current_asset_is_managed() and self.canvas.paste_selection():
            self.statusBar().showMessage("Pasted pixels.")
        self._update_pixel_action_state()

    def _delete_pixels(self) -> None:
        """Clear selected managed-asset pixels to transparency."""
        if self._current_asset_is_managed() and self.canvas.delete_selection():
            self.statusBar().showMessage("Cleared selected pixels.")

    def _crop_pixel_selection(self) -> None:
        """Crop a managed asset to its selected pixels."""
        if self._current_asset_is_managed() and self.canvas.crop_to_selection():
            self.statusBar().showMessage("Cropped managed asset to selection.")

    def _resize_pixel_canvas(self) -> None:
        """Resize a managed asset canvas without scaling its pixels."""
        if not self._current_asset_is_managed():
            return
        art = self.canvas.art()
        dialog = PixelSizeDialog(
            "Resize pixel canvas", art.width, art.height, True, self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        width, height, center = dialog.settings()
        self.canvas.resize_canvas(width, height, center)

    def _scale_pixel_artwork(self) -> None:
        """Scale a managed asset using nearest-neighbor pixels."""
        if not self._current_asset_is_managed():
            return
        art = self.canvas.art()
        dialog = PixelSizeDialog(
            "Scale pixel artwork", art.width, art.height, False, self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        width, height, unused = dialog.settings()
        self.canvas.scale_artwork(width, height)

    def _clear_pixel_canvas(self) -> None:
        """Clear a managed asset after confirmation."""
        if not self._current_asset_is_managed():
            return
        answer = QMessageBox.question(
            self,
            "Clear pixel canvas?",
            "Clear every pixel to transparency? This can be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.canvas.clear_art()

    def _use_current_asset_in_gui(self) -> None:
        """Select the current pixel asset in the App GUI workspace."""
        if self.current_asset is None:
            return
        key = self._publish_gui_pixel_asset(self.current_asset, self.canvas.art())
        self.workspace_tabs.setCurrentIndex(1)
        self.screen_designer.select_pixel_asset(key)
        self.statusBar().showMessage(
            "Pixel asset selected in App GUI. Drag it onto the active screen."
        )

    def _edit_gui_pixel_asset(self, key: str) -> None:
        """Open a linked GUI icon in the Pixel Art workspace."""
        source_text, separator, qualified_name = key.rpartition("::")
        source_path = Path(source_text)
        if not separator or not source_path.is_file():
            QMessageBox.information(
                self,
                "Pixel asset unavailable",
                "The linked Python source file is not available.",
            )
            return
        if not self._confirm_discard():
            return
        self._scan_path = source_path.resolve()
        self._scan_folder = False
        self._scan(self._scan_path, False)
        row = next(
            (
                index
                for index, asset in enumerate(self.assets)
                if asset.record.qualified_name == qualified_name
            ),
            None,
        )
        if row is None:
            QMessageBox.information(
                self,
                "Pixel asset unavailable",
                "The linked graphic function was not discovered in its source file.",
            )
            return
        self.asset_list.setCurrentRow(row)
        self.workspace_tabs.setCurrentIndex(0)

    def _update_palette(self, art: PixelArt) -> None:
        """Populate palette buttons from current graphic colors."""
        while self.palette_grid.count():
            item = self.palette_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        colors = art.used_colors()
        for standard in (
            0x0000,
            0xFFFF,
            0xF800,
            0x07E0,
            0x001F,
            0xFFE0,
            0x07FF,
            0xF81F,
        ):
            if standard not in colors:
                colors.append(standard)
        for index, color in enumerate(colors[:40]):
            button = QToolButton()
            button.setFixedSize(30, 30)
            button.setToolTip(
                f"0x{color:04X}\nLeft: paint color\nRight-click canvas: pick"
            )
            button.setStyleSheet(
                f"background: {qcolor_from_rgb565(color).name()}; border: 1px solid #777;"
            )
            button.clicked.connect(
                lambda checked=False, value=color: self._set_color(value)
            )
            self.palette_grid.addWidget(button, index // 8, index % 8)

    def _tool_changed(self, action: QAction) -> None:
        """Activate the selected mouse tool."""
        self.canvas.set_tool(str(action.data()))
        self.statusBar().showMessage(f"Tool: {action.text()}")

    def _choose_primary_color(self) -> None:
        """Choose a new RGB565 paint color."""
        chosen = QColorDialog.getColor(
            qcolor_from_rgb565(self._current_color), self, "Choose paint color"
        )
        if chosen.isValid():
            self._set_color(rgb_to_rgb565(chosen.red(), chosen.green(), chosen.blue()))

    def _choose_background_color(self) -> None:
        """Choose the eraser replacement color."""
        chosen = QColorDialog.getColor(
            qcolor_from_rgb565(self._background_color),
            self,
            "Choose eraser color",
        )
        if chosen.isValid():
            self._set_background(
                rgb_to_rgb565(chosen.red(), chosen.green(), chosen.blue())
            )

    def _set_color(self, color: int) -> None:
        """Set paint color and update its button."""
        self._current_color = color & 0xFFFF
        self.canvas.set_color(self._current_color)
        self.primary_button.setStyleSheet(color_button_style(self._current_color))
        self._update_color_label()

    def _set_background(self, color: int) -> None:
        """Set eraser color and update its button."""
        self._background_color = color & 0xFFFF
        self.canvas.set_background_color(self._background_color)
        self.background_button.setStyleSheet(color_button_style(self._background_color))
        self._update_color_label()

    def _update_color_label(self) -> None:
        """Update exact active palette values."""
        if hasattr(self, "color_label"):
            self.color_label.setText(
                f"Paint 0x{self._current_color:04X}   Erase 0x{self._background_color:04X}"
            )

    def _cursor_changed(self, x: int, y: int, color: object) -> None:
        """Show the current pixel coordinates and color."""
        art = self.canvas.art()
        source_x = art.origin_x + x
        source_y = art.origin_y + y
        color_text = "transparent" if color is None else f"0x{int(color):04X}"
        self.statusBar().showMessage(
            f"Pixel {x}, {y}   Source {source_x}, {source_y}   {color_text}"
        )

    def _apply_to_source(self) -> bool:
        """Review, back up, and apply edited pixels."""
        if not self._dirty or self.current_asset is None or self.current_trace is None:
            return False
        try:
            disk_source = self.current_asset.document.path.read_text(encoding="utf-8")
        except OSError as error:
            QMessageBox.critical(self, "Cannot read source", str(error))
            return False
        if disk_source != self.current_asset.document.source:
            QMessageBox.warning(
                self,
                "Source changed",
                "The Python file changed after scanning. Rescan before applying edits.",
            )
            return False
        try:
            patch = self.exporter.build_patch(
                self.current_asset,
                self.current_trace,
                self.canvas.art(),
                self.variant_values,
                self._managed_frames_for_save(),
            )
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Cannot build patch", str(error))
            return False
        if not patch.diff:
            QMessageBox.information(
                self, "No changes", "The edited pixels match the source rendering."
            )
            return False
        dialog = DiffDialog(patch, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        backup_root = self._source_backup_root()
        try:
            backup_path = patch.apply(backup_root)
        except Exception as error:
            QMessageBox.critical(self, "Apply failed", str(error))
            return False
        self._dirty = False
        self._animation_structure_dirty = False
        self._clear_pixel_recovery()
        QMessageBox.information(
            self,
            "Python updated",
            (
                f"Updated {patch.path.name}.\nBackup: {backup_path}"
                if backup_path
                else f"Created {patch.path.name}."
            ),
        )
        self._scan(self._scan_path or patch.path, self._scan_folder)
        return True

    def _managed_frames_for_save(self) -> list[PixelArt] | None:
        """Return every managed frame with the current edit included."""
        if not self._current_asset_is_managed():
            return None
        if self.animation_parameter != "frame":
            return [self.canvas.art().copy()]
        current_value = self.variant_values.get("frame")
        frames: list[PixelArt] = []
        for index in range(self.frame_combo.count()):
            value = self.frame_combo.itemData(index)
            if value == current_value:
                frames.append(self.canvas.art().copy())
                continue
            draft = self.animation_drafts.get(value)
            if draft is not None:
                frames.append(draft.copy())
                continue
            values = dict(self.variant_values)
            values["frame"] = value
            trace = self.tracer.render(self.current_asset, values)
            frames.append(self._animation_art(trace, value).copy())
        return frames

    def _export_png(self) -> None:
        """Export the current pixel art as PNG."""
        if self.current_asset is None:
            return
        default_name = f"{self.current_asset.record.name.lstrip('_')}.png"
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export PNG",
            str(Path.cwd() / default_name),
            "PNG images (*.png)",
        )
        if not filename:
            return
        if not filename.lower().endswith(".png"):
            filename += ".png"
        if not pixel_art_image(self.canvas.art()).save(filename, "PNG"):
            QMessageBox.critical(
                self, "Export failed", "The PNG file could not be written."
            )
            return
        self.statusBar().showMessage(f"Exported {filename}")

    def _confirm_discard(self) -> bool:
        """Offer save, discard, or cancel for an unapplied pixel edit."""
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Save pixel edits?",
            "The current pixel asset has unsaved changes.",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self._apply_to_source()
        if answer == QMessageBox.StandardButton.Discard:
            self._dirty = False
            if self._animation_structure_dirty:
                self._animation_structure_dirty = False
                self.animation_asset_key = None
                self.animation_images.clear()
                self.animation_drafts.clear()
                if self.current_asset is not None:
                    self._rebuild_variants(self.current_asset)
                    self._render_current()
            self._clear_pixel_recovery()
            return True
        return False

    def _clear_editor(self) -> None:
        """Reset the canvas and inspector for no results."""
        self._stop_animation()
        self.animation_parameter = None
        self.animation_group.setVisible(False)
        self.onion_skin_check.setChecked(False)
        self._suppress_changes = True
        self.canvas.set_art(PixelArt(32, 32))
        self._suppress_changes = False
        self.asset_title.setText("No graphic selected")
        self.source_label.setText("No source selected")
        self.asset_mode_label.setText("No asset selected")
        self.asset_mode_label.setStyleSheet("")
        self.warning_text.setPlainText("No supported drawing functions were found.")
        self.apply_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.apply_action.setEnabled(False)
        self.export_action.setEnabled(False)
        self.canvas.set_transparent_eraser(False)
        self._update_pixel_action_state()

    def _animation_art(self, trace: TraceResult, value: Any = None) -> PixelArt:
        """Return an imported or source-rendered animation frame."""
        if self.animation_parameter is None:
            return trace.current_art
        if value is None:
            value = self.variant_values.get(self.animation_parameter)
        if value not in self.animation_images:
            return trace.current_art
        draft = self.animation_drafts.get(value)
        if (
            draft is not None
            and draft.width == trace.current_art.width
            and draft.height == trace.current_art.height
            and draft.origin_x == trace.current_art.origin_x
            and draft.origin_y == trace.current_art.origin_y
        ):
            return draft
        prepared = prepare_reference_image(
            self.animation_images[value],
            trace.current_art.width,
            trace.current_art.height,
            "contain",
        )
        converted = image_to_pixel_art(
            prepared,
            trace.current_art.width,
            trace.current_art.height,
            self.reference_colors_spin.value(),
            self.reference_dither_check.isChecked(),
        )
        converted.origin_x = trace.current_art.origin_x
        converted.origin_y = trace.current_art.origin_y
        merged = trace.current_art.copy()
        for y in range(converted.height):
            for x in range(converted.width):
                color = converted.pixel(x, y)
                if color is not None:
                    merged.set_pixel(x, y, color)
        self.animation_drafts[value] = merged
        return merged

    def _asset_key(self, asset: GraphicsAsset | None) -> tuple[Path, str] | None:
        """Return a stable source asset identity."""
        if asset is None:
            return None
        return asset.document.path, asset.record.qualified_name

    def _publish_gui_pixel_asset(self, asset: GraphicsAsset, art: PixelArt) -> str:
        """Make one traced graphic available and return its stable key."""
        source_path = asset.document.path.resolve()
        key = f"{source_path}::{asset.record.qualified_name}"
        self.screen_designer.upsert_pixel_asset(
            GuiPixelAsset(
                key,
                asset.record.name,
                str(source_path),
                asset.record.name,
                art.copy(),
            )
        )
        return key


def color_button_style(color: int) -> str:
    """Return readable foreground and background styling."""
    qt_color = qcolor_from_rgb565(color)
    luminance = (
        qt_color.red() * 299 + qt_color.green() * 587 + qt_color.blue() * 114
    ) // 1000
    foreground = "#000000" if luminance >= 145 else "#ffffff"
    return f"background: {qt_color.name()}; color: {foreground};"
