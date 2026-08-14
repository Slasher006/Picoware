"""Qt main window for the graphics editor."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, replace as dataclass_replace
from enum import Enum
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings, QSize, Qt, QStandardPaths, QTimer
from PySide6.QtGui import QAction, QActionGroup, QIcon, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSizePolicy,
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
from .asset_library import AssetLibrary, LibraryAsset
from .asset_codegen import asset_fingerprint, decode_asset_resource, encode_asset
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
    SimulatorWorkspace,
    screen_preview_image,
)
from .designer_model import (
    DEVICE_PROFILES,
    ELEMENT_KINDS,
    FLOW_NODE_KINDS,
    GuiProject,
    backup_project,
    build_designer_patch,
)
from .app_presets import APP_PRESETS, app_preset, build_app_preset
from .generated_app import (
    GeneratedAppError,
    GeneratedAppPatchSet,
    apply_generated_app_patchset,
    build_live_preview_bundle,
    build_generated_app_patchset,
    project_preflight_diagnostics,
)
from .image_dialog import (
    LibraryImageImportDialog,
    LibraryImageImportResult,
    get_open_image_filename,
)
from . import __version__
from .library_workspace import PersonalAssetLibraryWidget
from .library_operations import plan_png_exports, write_png_exports
from .model import PixelArt, rgb_to_rgb565
from .native_widgets import NATIVE_WIDGET_SPECS
from .reference import (
    image_to_pixel_art,
    prepare_reference_image,
    read_image_frames,
    read_image_frames_with_durations,
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
from .standard_library import (
    STANDARD_ASSET_NAMES,
    is_standard_asset_id,
    standard_library_assets,
)
from .ui_help import (
    install_action_tooltips,
    install_widget_tooltips,
    set_collapsible_group_expanded,
    set_widget_tooltip,
)


class WorkspaceId(str, Enum):
    """Identify a workspace independently from its visible tab position."""

    APP_GUI = "app_gui"
    SCREEN_FLOW = "screen_flow"
    SIMULATOR = "simulator"
    PIXEL_ART = "pixel_art"
    ASSET_LIBRARY = "asset_library"


WORKSPACE_ORDER = (
    (WorkspaceId.APP_GUI, "App GUI"),
    (WorkspaceId.SCREEN_FLOW, "Screen Flow"),
    (WorkspaceId.SIMULATOR, "Simulator"),
    (WorkspaceId.PIXEL_ART, "Pixel Art"),
    (WorkspaceId.ASSET_LIBRARY, "Asset Library"),
)


@dataclass(frozen=True)
class LibraryHistorySnapshot:
    """Capture one validated library state and its content revision."""

    assets: tuple[LibraryAsset, ...]
    selected_asset_id: str
    revision: str


@dataclass(frozen=True)
class LibraryHistoryEntry:
    """Store one recoverable complete-library mutation."""

    description: str
    before: tuple[LibraryAsset, ...]
    after: tuple[LibraryAsset, ...]
    selected_before: str
    selected_after: str
    before_revision: str
    after_revision: str


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
        install_widget_tooltips(self)


class TextReportDialog(QDialog):
    """Show selectable bundled documentation or a diagnostic report."""

    def __init__(
        self,
        title: str,
        text: str,
        parent: QWidget | None = None,
        summary: str = "",
    ):
        """Build a readable, resizable plain-text report."""
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(860, 640)
        layout = QVBoxLayout(self)
        if summary:
            label = QLabel(summary)
            label.setWordWrap(True)
            layout.addWidget(label)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setPlainText(text)
        layout.addWidget(editor, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        install_widget_tooltips(self)


class AppPresetDialog(QDialog):
    """Choose and preview one small Picoware App GUI starter."""

    def __init__(self, parent: QWidget | None = None):
        """Build the preset browser and project settings."""
        super().__init__(parent)
        self.setWindowTitle("Create App GUI from Picoware Starter")
        self.resize(920, 640)
        self._suggested_name = ""
        self._name_was_edited = False
        layout = QVBoxLayout(self)
        introduction = QLabel(
            "Choose a compact workflow shell, not a finished app. Starters provide "
            "recognizable screens, navigation, and native input wiring; application "
            "behavior remains yours."
        )
        introduction.setWordWrap(True)
        layout.addWidget(introduction)

        content = QHBoxLayout()
        self.preset_list = QListWidget()
        self.preset_list.setMinimumWidth(250)
        self.preset_list.setIconSize(QSize(96, 72))
        for preset in APP_PRESETS:
            project = build_app_preset(preset.id)
            preview = screen_preview_image(project.screens[0], QSize(96, 72))
            item = QListWidgetItem(QIcon(QPixmap.fromImage(preview)), preset.name)
            item.setData(Qt.ItemDataRole.UserRole, preset.id)
            item.setToolTip(preset.summary)
            self.preset_list.addItem(item)
        content.addWidget(self.preset_list)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        self.preset_title = QLabel()
        self.preset_title.setStyleSheet("font-size: 20px; font-weight: 600;")
        self.preset_summary = QLabel()
        self.preset_summary.setWordWrap(True)
        self.capability_label = QLabel()
        self.capability_label.setWordWrap(True)
        self.capability_label.setStyleSheet("color: #245b85; font-weight: 600;")
        self.requirement_label = QLabel()
        self.requirement_label.setWordWrap(True)
        self.requirement_label.setStyleSheet("color: #8a4b08; font-weight: 600;")
        self.preset_description = QLabel()
        self.preset_description.setWordWrap(True)
        self.preview_screen_combo = QComboBox()
        self.preview_label = QLabel()
        self.preview_label.setMinimumSize(300, 180)
        self.preview_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet(
            "background: #20242a; border: 1px solid #59636f;"
        )
        self.screen_summary = QLabel()
        self.screen_summary.setWordWrap(True)
        form = QFormLayout()
        self.project_name_edit = QLineEdit()
        self.device_combo = QComboBox()
        self.device_combo.addItems(
            tuple(name for name in DEVICE_PROFILES if name != "Custom")
        )
        form.addRow("Project name", self.project_name_edit)
        form.addRow("Target device", self.device_combo)
        detail_layout.addWidget(self.preset_title)
        detail_layout.addWidget(self.preset_summary)
        detail_layout.addWidget(self.capability_label)
        detail_layout.addWidget(self.requirement_label)
        detail_layout.addWidget(self.preset_description)
        preview_screen_row = QHBoxLayout()
        preview_screen_row.addWidget(QLabel("Preview screen"))
        preview_screen_row.addWidget(self.preview_screen_combo, 1)
        detail_layout.addLayout(preview_screen_row)
        detail_layout.addWidget(self.preview_label, 1)
        detail_layout.addWidget(self.screen_summary)
        detail_layout.addLayout(form)
        content.addWidget(detail, 1)
        layout.addLayout(content, 1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Create Project"
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.preset_list.currentItemChanged.connect(self._preset_changed)
        self.device_combo.currentTextChanged.connect(self._refresh_preview)
        self.preview_screen_combo.currentIndexChanged.connect(self._refresh_preview)
        self.project_name_edit.textEdited.connect(self._project_name_edited)
        self.project_name_edit.textChanged.connect(self._update_create_enabled)
        self.preset_list.setCurrentRow(0)
        install_widget_tooltips(self)

    def _project_name_edited(self, unused_text: str) -> None:
        """Remember that preset changes must no longer replace the user's name."""
        self._name_was_edited = True

    def _preset_changed(self, current: QListWidgetItem | None) -> None:
        """Show complete metadata for the selected preset."""
        if current is None:
            self._update_create_enabled()
            return
        preset = app_preset(str(current.data(Qt.ItemDataRole.UserRole)))
        self.preset_title.setText(preset.name)
        self.preset_summary.setText(preset.summary)
        included = [
            value
            for value in preset.capabilities
            if not value.startswith(("Needs ", "Optional "))
        ]
        required = [
            value.removeprefix("Needs ").removeprefix("Optional ")
            for value in preset.capabilities
            if value.startswith(("Needs ", "Optional "))
        ]
        self.capability_label.setText(f"Included: {' · '.join(included)}")
        self.requirement_label.setText(
            f"You implement: {' · '.join(required)}" if required else ""
        )
        self.requirement_label.setVisible(bool(required))
        self.preset_description.setText(preset.description)
        self.preview_screen_combo.blockSignals(True)
        self.preview_screen_combo.clear()
        self.preview_screen_combo.addItems([screen.name for screen in preset.screens])
        self.preview_screen_combo.blockSignals(False)
        if not self._name_was_edited or not self.project_name_edit.text().strip():
            self._suggested_name = preset.name
            self.project_name_edit.setText(self._suggested_name)
        self._update_create_enabled()
        self._refresh_preview()

    def _update_create_enabled(self, unused_text: str = "") -> None:
        """Require both a selected preset and a visible project name."""
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            self.preset_list.currentItem() is not None
            and bool(self.project_name_edit.text().strip())
        )

    def _refresh_preview(self, unused_text: str = "") -> None:
        """Render the first screen for the current target device."""
        current = self.preset_list.currentItem()
        if current is None:
            return
        preset = app_preset(str(current.data(Qt.ItemDataRole.UserRole)))
        project = build_app_preset(
            preset.id,
            self.project_name_edit.text().strip() or preset.name,
            self.device_combo.currentText(),
        )
        screen_index = min(
            max(0, self.preview_screen_combo.currentIndex()),
            len(project.screens) - 1,
        )
        screen = project.screens[screen_index]
        image = screen_preview_image(screen, QSize(320, 200))
        self.preview_label.setPixmap(QPixmap.fromImage(image))
        self.preview_label.setToolTip(
            f"Previewing screen: {screen.name}.\n"
            "Example: Create the project, then edit this screen in App GUI."
        )
        self.screen_summary.setText(
            f"{len(project.screens)} screens · {len(project.connections)} navigation "
            f"links\nScreens: {', '.join(item.name for item in project.screens)}"
        )

    def settings(self) -> tuple[str, str, str]:
        """Return preset ID, project name, and target device profile."""
        current = self.preset_list.currentItem()
        preset_id = (
            str(current.data(Qt.ItemDataRole.UserRole)) if current is not None else ""
        )
        return (
            preset_id,
            self.project_name_edit.text().strip(),
            self.device_combo.currentText(),
        )


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
        install_widget_tooltips(self)

    def settings(self) -> tuple[int, int, int, int]:
        """Return frame width, height, margin, and spacing."""
        return (
            self.width_spin.value(),
            self.height_spin.value(),
            self.margin_spin.value(),
            self.spacing_spin.value(),
        )


class NewGraphicDialog(QDialog):
    """Collect dimensions and naming for a new editable pixel asset."""

    def __init__(
        self,
        width: int,
        height: int,
        has_reference: bool,
        imported_frame_count: int,
        parent: QWidget | None = None,
        initial_mode: str | None = None,
    ):
        """Build new graphic creation controls."""
        super().__init__(parent)
        self.setWindowTitle("Create new pixel asset")
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
        initial_index = self.mode_combo.findData(initial_mode)
        if initial_index >= 0:
            self.mode_combo.setCurrentIndex(initial_index)
        layout.addRow("Create from", self.mode_combo)
        self.name_edit = QLineEdit("New Asset")
        layout.addRow("Asset name", self.name_edit)
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
        install_widget_tooltips(self)

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
        install_widget_tooltips(self)

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
        install_widget_tooltips(self)

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
        install_widget_tooltips(self)


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
        install_widget_tooltips(self)


class GeneratedAppReviewDialog(QDialog):
    """Review the complete v1 ownership plan and generated file diffs."""

    def __init__(
        self,
        patchset: GeneratedAppPatchSet,
        parent: QWidget | None = None,
    ):
        """Build the code-and-resource generation confirmation dialog."""
        super().__init__(parent)
        self.setWindowTitle("Review Generated App Structure v1")
        self.resize(1080, 760)
        layout = QVBoxLayout(self)
        resource = patchset.asset_resource
        storage_label = (
            "Individual files"
            if resource.storage_mode == "individual"
            else "Combined PGA3"
        )
        summary_lines = [
            f"Destination: {patchset.paths.root}",
            (
                f"Asset storage: {storage_label} · "
                f"{resource.asset_count} referenced images, "
                f"{resource.audio_count} WAV files, "
                f"{resource.frame_count} frames, {resource.payload_size} bytes on disk, "
                f"{resource.maximum_row_bytes} bytes maximum streamed pixel row"
            ),
            (
                "Only referenced assets are generated; obsolete individual resources "
                "are reviewed below, while output from another storage mode is preserved."
            ),
            "",
        ]
        summary_lines.extend(
            f"{patch.action.upper():20} {patch.ownership:12} {patch.path}"
            for patch in patchset.patches
        )
        summary = QPlainTextEdit()
        summary.setReadOnly(True)
        summary.setMaximumHeight(160)
        summary.setPlainText("\n".join(summary_lines))
        layout.addWidget(summary)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        diffs = [patch.diff for patch in patchset.patches if patch.diff]
        editor.setPlainText("\n".join(diffs) or "No generated source changes.")
        layout.addWidget(editor, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel
        )
        apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        apply_button.setEnabled(not patchset.blocked)
        apply_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        install_widget_tooltips(self)


class MainWindow(QMainWindow):
    """Provide source discovery and mouse pixel editing."""

    @property
    def asset_library(self) -> AssetLibrary:
        """Return the personal store configured against the built-in catalogue."""
        return self._asset_library

    @asset_library.setter
    def asset_library(self, library: AssetLibrary) -> None:
        """Apply the shared name and ID policy to every replacement store."""
        standards = standard_library_assets()
        library.set_reserved_catalogue(
            STANDARD_ASSET_NAMES,
            tuple(asset.id for asset in standards),
        )
        self._asset_library = library

    def _workspace_index(self, workspace_id: WorkspaceId) -> int:
        """Return the current visible position for one semantic workspace."""
        return self._workspace_indices[workspace_id]

    def _current_workspace(self) -> WorkspaceId:
        """Return the semantic identity of the selected top-level tab."""
        index = self.workspace_tabs.currentIndex()
        return next(
            workspace_id
            for workspace_id, workspace_index in self._workspace_indices.items()
            if workspace_index == index
        )

    def _activate_workspace(self, workspace_id: WorkspaceId | str) -> None:
        """Select one workspace without exposing its positional index to callers."""
        normalized = (
            workspace_id
            if isinstance(workspace_id, WorkspaceId)
            else WorkspaceId(str(workspace_id))
        )
        self.workspace_tabs.setCurrentIndex(self._workspace_index(normalized))

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
        self._composite_view_art: PixelArt | None = None
        self._pending_image_path: Path | None = None
        self._pending_image_uses_canvas = False
        self._draft_asset_name = ""
        self._portable_frames: list[PixelArt] = []
        self._portable_durations: list[int] = []
        self._portable_frame_index = 0
        self._editing_project_asset_id = ""
        self._editing_project_asset_frame = 0
        self._editing_library_asset_id = ""
        self._editing_library_asset_revision = ""
        self.settings = QSettings("Picoware", "PicoGraphicsEditor")
        self.asset_library = AssetLibrary(self._asset_library_path())
        self.standard_library_assets = standard_library_assets()
        self._standard_library_by_id = {
            asset.id: asset for asset in self.standard_library_assets
        }
        self._library_known_revision: str | None = None
        self._library_undo_stack: list[LibraryHistoryEntry] = []
        self._library_redo_stack: list[LibraryHistoryEntry] = []
        self._build_actions()
        self._build_interface()
        self._configure_main_tab_order()
        self._connect_actions()
        self._set_color(self._current_color)
        self._set_background(self._background_color)
        self._clear_editor()
        self._update_recovery_action()
        self._update_history_actions()
        self._update_pixel_action_state()
        self._refresh_personal_asset_library()
        self._workspace_changed(self.workspace_tabs.currentIndex())
        install_widget_tooltips(self)
        install_action_tooltips(self.findChildren(QAction))

    def open_path(self, path: str | Path) -> None:
        """Open a Python file or source folder."""
        source_path = Path(path).expanduser().resolve()
        if source_path.name.endswith(".picogui.json"):
            self._load_gui_project(source_path)
        elif source_path.is_dir():
            self._activate_workspace(WorkspaceId.PIXEL_ART)
            self._scan_folder = True
            self._scan_path = source_path
            self._scan(source_path, True)
        elif source_path.suffix.lower() == ".py":
            self._activate_workspace(WorkspaceId.PIXEL_ART)
            self._scan_folder = False
            self._scan_path = source_path
            self._scan(source_path, False)
        else:
            QMessageBox.warning(
                self, "Unsupported path", "Choose a Python file or folder."
            )

    def _configure_main_tab_order(self) -> None:
        """Place persistent App GUI actions before workspace-specific controls."""
        controls = (
            self.document_run_button,
            self.document_simulator_button,
            self.document_save_button,
            self.workspace_tabs,
            self.screen_designer.project_name_edit,
        )
        for current, following in zip(controls, controls[1:]):
            QWidget.setTabOrder(current, following)

    def closeEvent(self, event) -> None:
        """Confirm before closing with unsaved painting."""
        if self._confirm_designer_discard() and self._confirm_discard():
            if hasattr(self, "pixel_splitter"):
                self.settings.setValue(
                    "pixel/splitter", self.pixel_splitter.saveState()
                )
            if hasattr(self, "library_workspace"):
                self.library_workspace.save_ui_state(self.settings)
            self.simulator_workspace.shutdown_live_simulator()
            event.accept()
        else:
            event.ignore()

    def _asset_library_path(self) -> Path:
        """Return the versioned local personal-library file."""
        location = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        if not location:
            location = str(Path.home() / ".local" / "share" / "PicoGraphicsEditor")
        return Path(location) / "asset-library-v1.json"

    def _build_actions(self) -> None:
        """Create reusable window actions."""
        self.open_active_action = QAction("Open Pixel Art Source...", self)
        self.open_active_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_file_action = QAction("Open Python File...", self)
        self.open_folder_action = QAction("Open Python Folder...", self)
        self.close_source_action = QAction("Close Source Folder / File", self)
        self.close_source_action.setEnabled(False)
        self.close_active_action = QAction("Close Pixel Art Document", self)
        self.close_active_action.setShortcut(QKeySequence("Ctrl+W"))
        self.close_active_action.setEnabled(False)
        self.rescan_action = QAction("Rescan", self)
        self.rescan_action.setShortcut(QKeySequence("F5"))
        self.rescan_action.setEnabled(False)
        self.export_action = QAction("Export PNG...", self)
        self.apply_action = QAction("Save Asset", self)
        self.save_active_action = QAction("Save", self)
        self.save_active_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_as_active_action = QAction("Save As / Export...", self)
        self.save_as_active_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
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
        self.add_frame_action = QAction("Add Frame", self)
        self.duplicate_frame_action = QAction("Duplicate Current Frame", self)
        self.delete_frame_action = QAction("Delete Current Frame", self)
        self.move_frame_left_action = QAction("Move Frame Left", self)
        self.move_frame_right_action = QAction("Move Frame Right", self)
        self.play_animation_action = QAction("Play Animation", self)
        self.play_animation_action.setCheckable(True)
        self.place_in_gui_action = QAction("Place on Current Screen", self)
        self.generate_python_action = QAction("Generate Python Asset...", self)
        self.generate_python_action.setToolTip(
            "Generate or update a reviewed Python drawing function from the current pixels.\n"
            "Example: Finish drawing, then choose a Python file and review the exact diff."
        )
        self.save_to_library_action = QAction("Save Asset to Personal Library...", self)
        self.save_to_library_action.setToolTip(
            "Store a reusable copy outside the current source file and GUI project.\n"
            "Example: Import an image, then choose Save to Library before creating "
            "another project."
        )
        self.select_in_gui_action = QAction("Select in App GUI", self)
        self.use_in_gui_action = self.select_in_gui_action
        self.use_in_gui_action.setEnabled(False)
        self.recover_pixel_action = QAction("Recover Autosaved Pixel Asset...", self)
        self.recover_gui_action = QAction("Recover Autosaved GUI Project...", self)
        self.import_image_asset_action = QAction("Import Image as Pixel Asset...", self)
        self.open_reference_action = QAction("Add Tracing Reference...", self)
        self.clear_reference_action = QAction("Clear Reference Image", self)
        self.import_frames_action = QAction("Import Animation Frames...", self)
        self.import_pga_action = QAction("Import Images from PGA...", self)
        self.import_pga_action.setToolTip(
            "Decode all pixel images from a generated PGA2 or PGA3 resource into "
            "the Personal Asset Library. WAV entries remain in the PGA3 file.\n"
            "Example: Recover reusable images from generated_assets.pga."
        )
        self.new_graphic_action = QAction("New Blank Asset...", self)
        self.new_graphic_action.setIconText("New Asset")
        self.new_graphic_action.setToolTip(
            "Create an unsaved editable pixel canvas before choosing its destination.\n"
            "Example: Create a 32 x 32 asset, draw it, then save it to the library."
        )
        self.new_gui_action = QAction("New GUI Project", self)
        self.new_preset_action = QAction("New from Picoware Starter...", self)
        self.new_preset_action.setToolTip(
            "Create a small editable workflow using real Picoware widgets.\n"
            "Example: start with Quick Note, then connect submitted text to storage."
        )
        self.open_gui_action = QAction("Open GUI Project...", self)
        self.open_mqtt_example_action = QAction("Open MQTT Client Example", self)
        self.open_mqtt_example_action.setToolTip(
            "Open a safe, unsaved copy of the bundled five-screen MQTT example.\n"
            "Example: inspect its custom dashboard, native widgets, pixel asset, "
            "and Screen Flow without changing the bundled original."
        )
        self.save_gui_action = QAction("Save GUI Project", self)
        self.save_gui_as_action = QAction("Save GUI Project As...", self)
        self.recover_saved_gui_action = QAction("Recover Saved GUI Backup...", self)
        self.export_generated_app_action = QAction(
            "Export Generated App Structure v1...", self
        )
        self.export_gui_action = QAction("Export GUI to Python (Legacy)...", self)
        self.import_existing_app_action = QAction("Import Existing App...", self)
        self.apply_imported_app_action = QAction("Apply Edits to Existing App...", self)
        self.apply_imported_app_action.setEnabled(False)
        self.validate_project_action = QAction("Validate / Preflight Project...", self)
        self.validate_project_action.setShortcut(QKeySequence("Ctrl+Shift+V"))
        self.project_properties_action = QAction("Open Project Properties", self)
        self.open_simulator_action = QAction("Open Device Simulator", self)
        self.open_simulator_action.setShortcut(QKeySequence("F6"))
        self.open_simulator_action.setToolTip(
            "Open the dedicated simulator workspace without starting a process.\n"
            "Example: Press F6 to inspect logs or advanced launch settings."
        )
        self.run_current_design_action = QAction("Run Current GUI Project", self)
        self.run_current_design_action.setShortcut(QKeySequence("Ctrl+Return"))
        self.run_current_design_action.setToolTip(
            "Run the current in-memory GUI project, including unsaved changes.\n"
            "Example: Press Ctrl+Enter after editing a screen."
        )
        self.restart_simulator_action = QAction("Restart Simulator", self)
        self.restart_simulator_action.setEnabled(False)
        self.stop_simulator_action = QAction("Stop Simulator", self)
        self.stop_simulator_action.setEnabled(False)
        self.capture_simulator_action = QAction("Capture Current Frame", self)
        self.capture_simulator_action.setEnabled(False)
        self.copy_simulator_error_action = QAction("Copy Last Simulator Error", self)
        self.copy_simulator_error_action.setEnabled(False)

        self.workspace_actions: list[QAction] = []
        self.workspace_actions_by_id: dict[WorkspaceId, QAction] = {}
        self.workspace_action_group = QActionGroup(self)
        self.workspace_action_group.setExclusive(True)
        for index, (workspace_id, label) in enumerate(WORKSPACE_ORDER):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setData(workspace_id.value)
            action.setShortcut(QKeySequence(f"Ctrl+{index + 1}"))
            self.workspace_action_group.addAction(action)
            self.workspace_actions.append(action)
            self.workspace_actions_by_id[workspace_id] = action
        self.toggle_grid_action = QAction("Show Pixel Grid", self)
        self.toggle_grid_action.setCheckable(True)
        self.toggle_grid_action.setChecked(True)
        self.toggle_catalogue_action = QAction("Show Source Catalogue", self)
        self.toggle_catalogue_action.setCheckable(True)
        self.toggle_inspector_action = QAction("Show Pixel Inspector", self)
        self.toggle_inspector_action.setCheckable(True)
        self.toggle_inspector_action.setChecked(True)

        self.app_add_screen_action = QAction("Add Screen", self)
        self.app_duplicate_screen_action = QAction("Duplicate Screen", self)
        self.app_delete_screen_action = QAction("Delete Screen", self)
        self.app_duplicate_elements_action = QAction(
            "Duplicate Selected Elements", self
        )
        self.app_edit_asset_action = QAction("Open Asset in Pixel Editor", self)
        self.app_lock_action = QAction("Lock or Unlock Selected", self)
        self.app_visibility_action = QAction("Show or Hide Selected", self)
        self.app_save_asset_action = QAction("Save Selected Asset to Library", self)
        self.app_natural_size_action = QAction("Use Natural Asset Size", self)
        self.app_bake_size_action = QAction("Bake Asset at Current Size...", self)
        self.app_delete_elements_action = QAction("Delete Selected Elements", self)
        self.app_design_preview_action = QAction("Preview Layout (Safe)", self)
        self.app_layer_actions: dict[str, QAction] = {
            "front": QAction("Bring to Front", self),
            "forward": QAction("Move Forward One Layer", self),
            "backward": QAction("Move Backward One Layer", self),
            "back": QAction("Send to Back", self),
        }
        self.app_alignment_actions: dict[str, QAction] = {
            "left": QAction("Align Left", self),
            "hcenter": QAction("Align Horizontal Centers", self),
            "top": QAction("Align Top", self),
            "vcenter": QAction("Align Vertical Centers", self),
            "distribute_h": QAction("Distribute Horizontally", self),
            "distribute_v": QAction("Distribute Vertically", self),
        }

        self.flow_duplicate_nodes_action = QAction(
            "Duplicate Selected Behavior Nodes", self
        )
        self.flow_group_nodes_action = QAction("Group Selected Behavior Nodes...", self)
        self.flow_trace_action = QAction("Trace Selected Behavior", self)
        self.flow_delete_nodes_action = QAction("Delete Selected Behavior Nodes", self)
        self.flow_delete_edge_action = QAction(
            "Delete Selected Behavior Connection", self
        )
        self.flow_insert_action_action = QAction(
            "Insert Action into Behavior Connection", self
        )
        self.flow_align_action = QAction("Align Selected Behavior Nodes Left", self)
        self.flow_distribute_action = QAction(
            "Distribute Selected Behavior Nodes Vertically", self
        )
        self.flow_open_screen_action = QAction("Open Selected Screen", self)
        self.flow_start_screen_action = QAction("Set Selected Screen as Start", self)
        self.flow_add_relation_action = QAction("Add Navigation Relation", self)
        self.flow_update_relation_action = QAction("Update Selected Relation", self)
        self.flow_delete_relation_action = QAction("Delete Selected Relation", self)
        self.flow_add_behavior_connection_action = QAction(
            "Add Behavior Connection", self
        )
        self.flow_update_behavior_connection_action = QAction(
            "Update Selected Behavior Connection", self
        )
        self.flow_fit_action = QAction("Fit All Nodes", self)
        self.flow_auto_layout_action = QAction("Auto-layout Graph", self)
        self.flow_reset_test_action = QAction("Reset Flow Test", self)

        self.library_import_image_action = QAction("Import Image...", self)
        self.library_add_action = QAction("Add to Current App GUI", self)
        self.library_edit_action = QAction("Edit Complete Asset in Pixel Art", self)
        self.library_replace_action = QAction("Replace from Image...", self)
        self.library_duplicate_action = QAction("Duplicate Asset...", self)
        self.library_export_action = QAction("Export Current Frame PNG...", self)
        self.library_rename_action = QAction("Rename Asset...", self)
        self.library_delete_action = QAction("Delete from Library", self)
        self.library_undo_action = QAction("Undo Last Library Change", self)
        self.library_redo_action = QAction("Redo Last Library Change", self)
        self.library_copy_path_action = QAction("Copy Library Path", self)
        self.library_refresh_action = QAction("Refresh Library", self)

        self.pixel_workflow_help_action = QAction("Pixel Art Workflow", self)
        self.mqtt_tutorial_help_action = QAction("MQTT Client Tutorial", self)
        self.app_flow_help_action = QAction("App Flow Standard v2", self)
        self.generated_app_help_action = QAction("Generated App Structure", self)
        self.pga_help_action = QAction("PGA3 Resource Format", self)
        self.shortcuts_help_action = QAction("Keyboard Shortcuts", self)
        self.about_action = QAction("About Pico Graphics Editor", self)

    def _build_interface(self) -> None:
        """Build the catalogue, canvas, and inspector."""
        self.file_menu = self.menuBar().addMenu("File")
        self.new_menu = self.file_menu.addMenu("New")
        self.new_menu.addActions((self.new_gui_action, self.new_preset_action))
        self.new_menu.addSeparator()
        self.new_menu.addAction(self.new_graphic_action)
        self.open_menu = self.file_menu.addMenu("Open")
        self.open_menu.addAction(self.open_active_action)
        self.open_menu.addSeparator()
        self.open_menu.addActions(
            (self.open_file_action, self.open_folder_action, self.open_gui_action)
        )
        self.open_menu.addSeparator()
        self.open_menu.addAction(self.open_mqtt_example_action)
        self.open_menu.addSeparator()
        self.open_menu.addAction(self.rescan_action)
        self.recent_menu = self.file_menu.addMenu("Open Recent")
        self.file_menu.addAction(self.close_active_action)
        self.file_menu.addSeparator()
        self.file_menu.addActions((self.save_active_action, self.save_as_active_action))
        self.import_menu = self.file_menu.addMenu("Import")
        self.import_menu.addActions(
            (
                self.import_image_asset_action,
                self.import_frames_action,
                self.import_pga_action,
                self.import_existing_app_action,
            )
        )
        self.export_menu = self.file_menu.addMenu("Export")
        self.export_menu.addActions(
            (
                self.export_action,
                self.generate_python_action,
                self.export_generated_app_action,
                self.export_gui_action,
            )
        )
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.quit_action)

        self.edit_menu = self.menuBar().addMenu("Edit")
        self.edit_menu.addActions((self.undo_action, self.redo_action))
        self.edit_menu.addSeparator()
        self.pixel_edit_actions = (
            self.cut_pixels_action,
            self.copy_pixels_action,
            self.paste_pixels_action,
            self.delete_pixels_action,
            self.select_all_pixels_action,
            self.clear_selection_action,
        )
        self.edit_menu.addActions((*self.pixel_edit_actions,))

        self.view_menu = self.menuBar().addMenu("View")
        self.workspace_menu = self.view_menu.addMenu("Workspaces")
        self.workspace_menu.addActions(self.workspace_actions)
        self.pixel_view_menu = self.view_menu.addMenu("Pixel Canvas")

        self.gui_menu = self.menuBar().addMenu("Project")
        self.gui_menu.addAction(self.project_properties_action)
        self.gui_menu.addAction(self.validate_project_action)
        self.gui_menu.addSeparator()
        self.gui_menu.addActions(
            (self.recover_gui_action, self.recover_saved_gui_action)
        )
        self.gui_menu.addSeparator()
        self.gui_menu.addActions(
            (self.import_existing_app_action, self.apply_imported_app_action)
        )
        self.gui_menu.addSeparator()
        self.gui_menu.addActions(
            (self.export_generated_app_action, self.export_gui_action)
        )

        self.pixel_menu = self.menuBar().addMenu("Pixel Art")
        self.pixel_menu.addActions(
            (
                self.flip_horizontal_action,
                self.flip_vertical_action,
                self.rotate_clockwise_action,
                self.crop_selection_action,
            )
        )
        self.pixel_menu.addSeparator()
        self.pixel_frame_menu = self.pixel_menu.addMenu("Animation Frames")
        self.pixel_frame_menu.addActions(
            (
                self.add_frame_action,
                self.duplicate_frame_action,
                self.delete_frame_action,
                self.move_frame_left_action,
                self.move_frame_right_action,
                self.play_animation_action,
                self.import_frames_action,
            )
        )
        self.pixel_reference_menu = self.pixel_menu.addMenu("Tracing Reference")
        self.pixel_reference_menu.addActions(
            (self.open_reference_action, self.clear_reference_action)
        )
        self.pixel_menu.addSeparator()
        self.pixel_menu.addActions(
            (
                self.resize_canvas_action,
                self.scale_artwork_action,
                self.clear_canvas_action,
            )
        )
        self.pixel_menu.addSeparator()
        self.pixel_menu.addActions(
            (
                self.place_in_gui_action,
                self.select_in_gui_action,
                self.save_to_library_action,
                self.generate_python_action,
                self.recover_pixel_action,
            )
        )

        self.app_menu = self.menuBar().addMenu("App GUI")
        self.app_menu.addAction(self.new_preset_action)
        self.app_menu.addSeparator()
        self.app_menu.addActions(
            (
                self.app_add_screen_action,
                self.app_duplicate_screen_action,
                self.app_delete_screen_action,
            )
        )
        self.app_add_element_menu = self.app_menu.addMenu("Add Element")
        self.app_add_element_actions: list[QAction] = []
        for kind in (item for item in ELEMENT_KINDS if item != "native"):
            action = self.app_add_element_menu.addAction(kind.title())
            action.setData(kind)
            self.app_add_element_actions.append(action)
        self.app_add_native_menu = self.app_add_element_menu.addMenu("Picoware Widget")
        self.app_add_native_actions: list[QAction] = []
        for spec in NATIVE_WIDGET_SPECS:
            action = self.app_add_native_menu.addAction(spec.name)
            action.setData(spec.id)
            action.setToolTip(spec.summary)
            self.app_add_native_actions.append(action)
        self.app_menu.addSeparator()
        self.app_menu.addActions(
            (
                self.app_duplicate_elements_action,
                self.app_edit_asset_action,
                self.app_lock_action,
                self.app_visibility_action,
                self.app_save_asset_action,
                self.app_natural_size_action,
                self.app_bake_size_action,
            )
        )
        self.app_arrange_menu = self.app_menu.addMenu("Arrange")
        self.app_arrange_menu.addActions(tuple(self.app_layer_actions.values()))
        self.app_arrange_menu.addSeparator()
        self.app_arrange_menu.addActions(tuple(self.app_alignment_actions.values()))
        self.app_menu.addAction(self.app_delete_elements_action)
        self.app_menu.addSeparator()
        self.app_menu.addAction(self.app_design_preview_action)
        self.app_menu.addAction(self.open_simulator_action)
        self.app_menu.addAction(self.run_current_design_action)

        self.flow_menu = self.menuBar().addMenu("Screen Flow")
        self.flow_add_node_menu = self.flow_menu.addMenu("Add Behavior Node")
        self.flow_add_node_actions: list[QAction] = []
        for kind in FLOW_NODE_KINDS:
            action = self.flow_add_node_menu.addAction(kind.title())
            action.setData(kind)
            self.flow_add_node_actions.append(action)
        self.flow_menu.addActions(
            (
                self.flow_duplicate_nodes_action,
                self.flow_group_nodes_action,
                self.flow_trace_action,
                self.flow_delete_nodes_action,
                self.flow_align_action,
                self.flow_distribute_action,
            )
        )
        self.flow_menu.addSeparator()
        self.flow_navigation_menu = self.flow_menu.addMenu("Navigation Relations")
        self.flow_navigation_menu.addActions(
            (
                self.flow_add_relation_action,
                self.flow_update_relation_action,
                self.flow_delete_relation_action,
            )
        )
        self.flow_behavior_connections_menu = self.flow_menu.addMenu(
            "Behavior Connections"
        )
        self.flow_behavior_connections_menu.addActions(
            (
                self.flow_add_behavior_connection_action,
                self.flow_update_behavior_connection_action,
                self.flow_delete_edge_action,
                self.flow_insert_action_action,
            )
        )
        self.flow_menu.addActions(
            (
                self.flow_open_screen_action,
                self.flow_start_screen_action,
            )
        )
        self.flow_menu.addSeparator()
        self.flow_menu.addActions(
            (
                self.flow_fit_action,
                self.flow_auto_layout_action,
                self.flow_reset_test_action,
            )
        )

        self.library_menu = self.menuBar().addMenu("Asset Library")
        self.library_menu.addActions(
            (self.library_import_image_action, self.import_pga_action)
        )
        self.library_menu.addSeparator()
        self.library_menu.addActions(
            (
                self.library_add_action,
                self.library_edit_action,
                self.library_replace_action,
                self.library_duplicate_action,
                self.library_export_action,
                self.library_rename_action,
                self.library_delete_action,
            )
        )
        self.library_menu.addSeparator()
        self.library_menu.addActions(
            (
                self.library_undo_action,
                self.library_redo_action,
                self.library_copy_path_action,
                self.library_refresh_action,
            )
        )

        self.run_menu = self.menuBar().addMenu("Simulator")
        self.run_menu.addAction(self.run_current_design_action)
        self.run_menu.addAction(self.open_simulator_action)
        self.run_menu.addSeparator()
        self.run_menu.addAction(self.restart_simulator_action)
        self.run_menu.addAction(self.stop_simulator_action)
        self.run_menu.addAction(self.capture_simulator_action)
        self.run_menu.addSeparator()
        self.run_menu.addAction(self.copy_simulator_error_action)

        self.help_menu = self.menuBar().addMenu("Help")
        self.help_menu.addActions(
            (
                self.mqtt_tutorial_help_action,
                self.pixel_workflow_help_action,
                self.app_flow_help_action,
                self.generated_app_help_action,
                self.pga_help_action,
            )
        )
        self.help_menu.addSeparator()
        self.help_menu.addAction(self.shortcuts_help_action)
        self.help_menu.addSeparator()
        self.help_menu.addAction(self.about_action)

        self.tool_bar = QToolBar("Pixel tools")
        self.tool_bar.setMovable(False)
        self.addToolBar(self.tool_bar)
        self.tool_bar.addAction(self.new_graphic_action)
        self.tool_bar.addAction(self.import_image_asset_action)
        self.tool_bar.addAction(self.place_in_gui_action)
        self.tool_bar.addAction(self.select_in_gui_action)
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
        self.fit_canvas_action = QAction("Fit", self)
        self.one_to_one_action = QAction("1:1", self)
        self.center_canvas_action = QAction("Center", self)
        self.tool_bar.addAction(self.fit_canvas_action)
        self.tool_bar.addAction(self.one_to_one_action)
        self.tool_bar.addAction(self.center_canvas_action)
        self.grid_check = QCheckBox("Grid")
        self.grid_check.setChecked(True)
        self.tool_bar.addWidget(self.grid_check)
        self.pixel_view_menu.addActions(
            (
                self.fit_canvas_action,
                self.one_to_one_action,
                self.center_canvas_action,
                self.toggle_grid_action,
            )
        )
        self.pixel_view_menu.addSeparator()
        self.pixel_view_menu.addActions(
            (self.toggle_catalogue_action, self.toggle_inspector_action)
        )
        self.pixel_toolbar_action = self.tool_bar.toggleViewAction()
        self.pixel_toolbar_action.setText("Show Pixel Toolbar")
        self.pixel_view_menu.addAction(self.pixel_toolbar_action)

        self.pixel_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.pixel_splitter.setChildrenCollapsible(True)
        self.catalogue_panel = self._build_catalogue()
        self.catalogue_panel.setVisible(False)
        self.pixel_splitter.addWidget(self.catalogue_panel)
        self.pixel_splitter.addWidget(self._build_canvas_panel())
        self.inspector_panel = self._build_inspector()
        self.pixel_splitter.addWidget(self.inspector_panel)
        self.pixel_splitter.setSizes((260, 760, 320))
        self.pixel_splitter.setStretchFactor(1, 1)
        saved_splitter = self.settings.value("pixel/splitter")
        if saved_splitter:
            self.pixel_splitter.restoreState(saved_splitter)
        self.workspace_tabs = QTabWidget()
        self.screen_designer = ScreenDesignerWidget(self.designer_session)
        self.screen_designer.setMinimumSize(0, 0)
        self.screen_designer.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        self.screen_flow = ScreenFlowWidget(self.designer_session)
        self.screen_flow.setMinimumSize(0, 0)
        self.screen_flow.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        self.simulator_workspace = SimulatorWorkspace(self.designer_session)
        self.simulator_workspace.setMinimumSize(0, 0)
        self.simulator_workspace.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        self.library_workspace = PersonalAssetLibraryWidget()
        self.library_workspace.setMinimumSize(0, 0)
        self.library_workspace.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        self.library_workspace.set_library_path(self.asset_library.path)
        self.library_workspace.restore_ui_state(self.settings)
        workspace_widgets = {
            WorkspaceId.APP_GUI: self.screen_designer,
            WorkspaceId.SCREEN_FLOW: self.screen_flow,
            WorkspaceId.SIMULATOR: self.simulator_workspace,
            WorkspaceId.PIXEL_ART: self.pixel_splitter,
            WorkspaceId.ASSET_LIBRARY: self.library_workspace,
        }
        self._workspace_indices: dict[WorkspaceId, int] = {}
        for workspace_id, label in WORKSPACE_ORDER:
            self._workspace_indices[workspace_id] = self.workspace_tabs.addTab(
                workspace_widgets[workspace_id], label
            )
        self.app_gui_tab_index = self._workspace_indices[WorkspaceId.APP_GUI]
        self.screen_flow_tab_index = self._workspace_indices[WorkspaceId.SCREEN_FLOW]
        self.simulator_tab_index = self._workspace_indices[WorkspaceId.SIMULATOR]
        self.pixel_art_tab_index = self._workspace_indices[WorkspaceId.PIXEL_ART]
        self.library_tab_index = self._workspace_indices[WorkspaceId.ASSET_LIBRARY]
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        document_row = QHBoxLayout()
        document_row.setContentsMargins(8, 5, 8, 5)
        self.document_workspace_label = QLabel("App GUI")
        self.document_workspace_label.setStyleSheet("font-weight: 600;")
        self.document_name_label = QLabel("No asset selected")
        self.document_name_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.document_state_label = QLabel("Saved")
        self.document_run_button = QPushButton("▶ Run current design")
        self.document_run_button.setToolTip(
            "Run the current in-memory GUI project in the Device Simulator.\n"
            "Example: Unsaved screen and flow changes are included."
        )
        self.document_simulator_button = QPushButton("Simulator · Stopped")
        self.document_simulator_button.setToolTip(
            "Open the Device Simulator. Its process continues across workspace changes.\n"
            "Example: Click while it is running to return to the framebuffer."
        )
        self.document_library_button = QPushButton("Save to Library")
        self.document_library_button.setToolTip(
            "Store the current Pixel Art asset in the personal library for reuse.\n"
            "Example: Import an image, then click Save to Library."
        )
        self.document_python_button = QPushButton("Generate Python…")
        self.document_python_button.setToolTip(
            "Generate a reviewed Python drawing function from the visible pixels.\n"
            "Example: Save the editable master to the library, then generate Python."
        )
        self.document_save_button = QPushButton("Save Asset")
        self.document_save_button.setDefault(True)
        document_row.addWidget(self.document_workspace_label)
        document_row.addWidget(self.document_name_label, 1)
        document_row.addWidget(self.document_state_label)
        document_row.addWidget(self.document_run_button)
        document_row.addWidget(self.document_simulator_button)
        document_row.addWidget(self.document_python_button)
        document_row.addWidget(self.document_library_button)
        document_row.addWidget(self.document_save_button)
        central_layout.addLayout(document_row)
        central_layout.addWidget(self.workspace_tabs, 1)
        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage(
            "Start in App GUI, or open a Python source to edit pixel assets."
        )
        self._update_document_strip()

    def _build_catalogue(self) -> QWidget:
        """Build the graphics catalogue panel."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QLabel("Detected graphics")
        title.setStyleSheet("font-weight: 600; font-size: 15px;")
        source_row = QHBoxLayout()
        source_row.addWidget(title)
        self.source_scope_label = QLabel("No source open")
        self.source_scope_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.close_source_button = QPushButton("Close")
        self.close_source_button.setEnabled(False)
        source_row.addWidget(self.source_scope_label, 1)
        source_row.addWidget(self.close_source_button)
        layout.addLayout(source_row)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter graphics")
        self.search_edit.setClearButtonEnabled(True)
        layout.addWidget(self.search_edit)
        filter_row = QGridLayout()
        self.asset_filter_checks: dict[str, QCheckBox] = {}
        for index, label in enumerate(
            ("Managed", "Source-backed", "Static", "Animated")
        ):
            check = QCheckBox(label)
            check.setChecked(True)
            self.asset_filter_checks[label.lower()] = check
            filter_row.addWidget(check, index // 2, index % 2)
        layout.addLayout(filter_row)
        self.asset_list = QListWidget()
        self.asset_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
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
        self.pixel_context_hint = QLabel(
            "Right-click the canvas or asset list for more actions"
        )
        self.pixel_context_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pixel_context_hint.setStyleSheet("color: #666;")
        layout.addWidget(self.pixel_context_hint)
        self.pixel_empty_widget = QWidget()
        empty_layout = QVBoxLayout(self.pixel_empty_widget)
        empty_title = QLabel("Start a pixel asset")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_title.setStyleSheet("font-weight: 600; font-size: 16px;")
        empty_help = QLabel(
            "Create a transparent canvas, import an image as editable RGB565 pixels, "
            "or open Python graphics to edit existing drawing functions."
        )
        empty_help.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_help.setWordWrap(True)
        empty_actions = QHBoxLayout()
        self.empty_new_button = QPushButton("New Blank Asset…")
        self.empty_import_button = QPushButton("Import Image as Asset…")
        self.empty_open_button = QPushButton("Open Python Graphics…")
        empty_actions.addStretch(1)
        empty_actions.addWidget(self.empty_new_button)
        empty_actions.addWidget(self.empty_import_button)
        empty_actions.addWidget(self.empty_open_button)
        empty_actions.addStretch(1)
        empty_layout.addWidget(empty_title)
        empty_layout.addWidget(empty_help)
        empty_layout.addLayout(empty_actions)
        layout.addWidget(self.pixel_empty_widget)
        view_row = QHBoxLayout()
        view_row.addStretch(1)
        self.source_view_combo = QComboBox()
        self.source_view_combo.addItem("Composite", "composite")
        self.source_view_combo.addItem("Original", "original")
        self.source_view_combo.addItem("Edits", "edits")
        self.source_view_combo.setToolTip(
            "Compare handwritten source pixels with the generated edit overlay."
        )
        self.source_view_combo.setVisible(False)
        self.source_view_label = QLabel("View")
        self.source_view_label.setVisible(False)
        view_row.addWidget(self.source_view_label)
        view_row.addWidget(self.source_view_combo)
        view_row.addStretch(1)
        layout.addLayout(view_row)
        self.scroll_area = QScrollArea()
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.setWidgetResizable(False)
        self.canvas = PixelCanvas()
        self.canvas.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.canvas.setToolTip(
            "Edit RGB565 pixels with the selected tool; right-click for common actions.\n"
            "Example: Choose Pencil, draw a pixel, then right-click to save it."
        )
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
        self.frame_timeline = QListWidget()
        self.frame_timeline.setViewMode(QListWidget.ViewMode.IconMode)
        self.frame_timeline.setFlow(QListWidget.Flow.LeftToRight)
        self.frame_timeline.setWrapping(False)
        self.frame_timeline.setIconSize(QSize(48, 48))
        self.frame_timeline.setMaximumHeight(86)
        self.frame_timeline.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.frame_timeline.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.frame_timeline.setToolTip(
            "Drag frames to reorder them. Locked source frames cannot be deleted."
        )
        animation_layout.addWidget(self.frame_timeline)
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

        self.reference_import_group = QGroupBox("Tracing reference…")
        self.reference_import_group.setCheckable(True)
        self.reference_import_group.setChecked(False)
        reference_layout = QVBoxLayout(self.reference_import_group)
        reference_buttons = QHBoxLayout()
        self.open_reference_button = QPushButton("Add reference image…")
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
        self.new_graphic_button = QPushButton("New blank asset…")
        reference_layout.addWidget(self.convert_reference_button)
        reference_layout.addWidget(self.import_frames_button)
        reference_layout.addWidget(self.new_graphic_button)
        layout.addWidget(self.reference_import_group)
        self.reference_import_group.toggled.connect(
            lambda expanded: set_collapsible_group_expanded(
                self.reference_import_group, expanded
            )
        )
        set_collapsible_group_expanded(self.reference_import_group, False)

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
        self.place_in_gui_button = QPushButton("Place on Current Screen")
        self.place_in_gui_button.setEnabled(False)
        layout.addWidget(self.place_in_gui_button)
        self.use_in_gui_button = QPushButton("Select in App GUI")
        self.use_in_gui_button.setEnabled(False)
        layout.addWidget(self.use_in_gui_button)
        layout.addStretch(1)
        scroll.setWidget(panel)
        return scroll

    def _connect_actions(self) -> None:
        """Connect actions and widget signals."""
        self.open_active_action.triggered.connect(self._open_active_document)
        self.close_active_action.triggered.connect(self._close_active_document)
        self.save_active_action.triggered.connect(self._save_active_workspace)
        self.save_as_active_action.triggered.connect(self._save_as_active_workspace)
        self.document_save_button.clicked.connect(self._save_active_workspace)
        self.document_library_button.clicked.connect(
            self.save_to_library_action.trigger
        )
        self.document_python_button.clicked.connect(self.generate_python_action.trigger)
        self.document_run_button.clicked.connect(self.run_current_design_action.trigger)
        self.document_simulator_button.clicked.connect(
            self.open_simulator_action.trigger
        )
        self.open_file_action.triggered.connect(self._open_file)
        self.open_folder_action.triggered.connect(self._open_folder)
        self.close_source_action.triggered.connect(self._close_source)
        self.close_source_button.clicked.connect(self._close_source)
        self.rescan_action.triggered.connect(self._rescan)
        self.export_action.triggered.connect(self._export_png)
        self.apply_action.triggered.connect(self._apply_to_source)
        self.generate_python_action.triggered.connect(self._generate_python_asset)
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
        self.place_in_gui_action.triggered.connect(self._place_current_asset_in_gui)
        self.recover_pixel_action.triggered.connect(self._recover_pixel_asset)
        self.use_in_gui_button.clicked.connect(self.use_in_gui_action.trigger)
        self.place_in_gui_button.clicked.connect(self.place_in_gui_action.trigger)
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
        self.fit_canvas_action.triggered.connect(self._fit_pixel_canvas)
        self.one_to_one_action.triggered.connect(lambda: self.zoom_spin.setValue(1))
        self.center_canvas_action.triggered.connect(self._center_pixel_canvas)
        self.grid_check.toggled.connect(self.canvas.set_grid_visible)
        self.grid_check.toggled.connect(self.toggle_grid_action.setChecked)
        self.toggle_grid_action.toggled.connect(self.grid_check.setChecked)
        self.toggle_catalogue_action.toggled.connect(self.catalogue_panel.setVisible)
        self.toggle_inspector_action.toggled.connect(self.inspector_panel.setVisible)
        self.workspace_action_group.triggered.connect(
            lambda action: self._activate_workspace(str(action.data()))
        )
        self.search_edit.textChanged.connect(self._filter_assets)
        for check in self.asset_filter_checks.values():
            check.toggled.connect(
                lambda checked=False: self._filter_assets(self.search_edit.text())
            )
        self.asset_list.currentRowChanged.connect(self._select_asset)
        self.asset_list.customContextMenuRequested.connect(
            self._show_asset_catalogue_context_menu
        )
        self.canvas.customContextMenuRequested.connect(
            self._show_pixel_canvas_context_menu
        )
        self.canvas.color_picked.connect(self._set_color)
        self.canvas.document_changed.connect(self._canvas_changed)
        self.source_view_combo.currentIndexChanged.connect(self._source_view_changed)
        self.canvas.selection_changed.connect(self._pixel_selection_changed)
        self.canvas.cursor_changed.connect(self._cursor_changed)
        QApplication.clipboard().dataChanged.connect(self._update_pixel_action_state)
        self.apply_button.clicked.connect(self._apply_to_source)
        self.export_button.clicked.connect(self._export_png)
        self.previous_frame_button.clicked.connect(self._previous_animation_frame)
        self.next_frame_button.clicked.connect(self._next_animation_frame)
        self.frame_combo.currentIndexChanged.connect(self._animation_frame_changed)
        self.frame_timeline.currentRowChanged.connect(self._timeline_frame_changed)
        self.frame_timeline.model().rowsMoved.connect(self._timeline_reordered)
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
        self.frame_interval_spin.valueChanged.connect(self._frame_interval_changed)
        self.add_frame_action.triggered.connect(self._add_animation_frame)
        self.duplicate_frame_action.triggered.connect(self._duplicate_animation_frame)
        self.delete_frame_action.triggered.connect(self._delete_animation_frame)
        self.move_frame_left_action.triggered.connect(
            lambda: self._move_animation_frame(-1)
        )
        self.move_frame_right_action.triggered.connect(
            lambda: self._move_animation_frame(1)
        )
        self.play_animation_action.toggled.connect(self.play_button.setChecked)
        self.play_button.toggled.connect(self.play_animation_action.setChecked)
        self.import_image_asset_action.triggered.connect(self._import_image_asset)
        self.open_reference_action.triggered.connect(self._open_reference_image)
        self.clear_reference_action.triggered.connect(self._clear_reference_image)
        self.import_frames_action.triggered.connect(self._import_animation_frames)
        self.import_pga_action.triggered.connect(self._import_pga_to_asset_library)
        self.new_graphic_action.triggered.connect(self._create_new_graphic)
        self.save_to_library_action.triggered.connect(
            self._save_current_asset_to_library
        )
        self.open_reference_button.clicked.connect(self._open_reference_image)
        self.clear_reference_button.clicked.connect(self._clear_reference_image)
        self.convert_reference_button.clicked.connect(self._convert_reference_image)
        self.import_frames_button.clicked.connect(self._import_animation_frames)
        self.new_graphic_button.clicked.connect(self._create_new_graphic)
        self.empty_new_button.clicked.connect(self._create_new_graphic)
        self.empty_import_button.clicked.connect(self._import_image_asset)
        self.empty_open_button.clicked.connect(self._open_file)
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
        self.new_preset_action.triggered.connect(self._new_gui_project_from_preset)
        self.open_gui_action.triggered.connect(self._open_gui_project)
        self.save_gui_action.triggered.connect(self._save_gui_project)
        self.save_gui_as_action.triggered.connect(self._save_gui_project_as)
        self.recover_gui_action.triggered.connect(self._recover_gui_project)
        self.recover_saved_gui_action.triggered.connect(self._recover_saved_gui_project)
        self.export_generated_app_action.triggered.connect(
            self._export_generated_app_structure
        )
        self.export_gui_action.triggered.connect(self._export_gui_python)
        self.import_existing_app_action.triggered.connect(self._import_existing_app)
        self.apply_imported_app_action.triggered.connect(self._apply_imported_app_edits)
        self.project_properties_action.triggered.connect(self._open_project_properties)
        self.validate_project_action.triggered.connect(self._validate_gui_project)
        self.open_simulator_action.triggered.connect(self._open_simulator_workspace)
        self.run_current_design_action.triggered.connect(self._run_current_design)
        self.restart_simulator_action.triggered.connect(
            self.simulator_workspace.restart_live_simulator
        )
        self.stop_simulator_action.triggered.connect(
            self.simulator_workspace.stop_live_simulator
        )
        self.capture_simulator_action.triggered.connect(
            self.simulator_workspace.capture_current_frame
        )
        self.copy_simulator_error_action.triggered.connect(
            self.simulator_workspace.copy_error
        )
        self.app_add_screen_action.triggered.connect(self.screen_designer._add_screen)
        self.app_duplicate_screen_action.triggered.connect(
            self.screen_designer._duplicate_screen
        )
        self.app_delete_screen_action.triggered.connect(
            self.screen_designer._delete_screen
        )
        for action in self.app_add_element_actions:
            action.triggered.connect(
                lambda checked=False, item=action: self.screen_designer._add_element(
                    str(item.data())
                )
            )
        for action in self.app_add_native_actions:
            action.triggered.connect(
                lambda checked=False,
                item=action: self.screen_designer._add_native_widget(str(item.data()))
            )
        self.app_duplicate_elements_action.triggered.connect(
            self.screen_designer._duplicate_elements
        )
        self.app_edit_asset_action.triggered.connect(
            self.screen_designer._edit_selected_pixel_asset
        )
        self.app_lock_action.triggered.connect(
            self.screen_designer._toggle_element_lock
        )
        self.app_visibility_action.triggered.connect(
            self.screen_designer._toggle_element_visibility
        )
        self.app_save_asset_action.triggered.connect(
            self.screen_designer._save_selected_element_to_library
        )
        self.app_natural_size_action.triggered.connect(
            self.screen_designer._use_selected_asset_natural_size
        )
        self.app_bake_size_action.triggered.connect(
            self.screen_designer._bake_selected_asset_size
        )
        self.app_delete_elements_action.triggered.connect(
            self.screen_designer._delete_element
        )
        self.app_design_preview_action.triggered.connect(self._open_design_preview)
        for mode, action in self.app_layer_actions.items():
            action.triggered.connect(
                lambda checked=False, layer_mode=mode: (
                    self.screen_designer._reorder_selected_elements(layer_mode)
                )
            )
        for mode, action in self.app_alignment_actions.items():
            action.triggered.connect(
                lambda checked=False, align_mode=mode: (
                    self.screen_designer._align_selection(align_mode)
                )
            )

        for action in self.flow_add_node_actions:
            action.triggered.connect(
                lambda checked=False, item=action: self.screen_flow._add_behavior_kind(
                    str(item.data())
                )
            )
        self.flow_duplicate_nodes_action.triggered.connect(
            self.screen_flow._duplicate_selected_behavior_nodes
        )
        self.flow_group_nodes_action.triggered.connect(
            self.screen_flow._group_selected_behavior_nodes
        )
        self.flow_trace_action.triggered.connect(
            self.screen_flow._trace_selected_behavior
        )
        self.flow_delete_nodes_action.triggered.connect(
            lambda: self.screen_flow._delete_behavior_nodes(
                set(self.screen_flow.graph.selected_behavior_node_ids)
            )
        )
        self.flow_delete_edge_action.triggered.connect(
            lambda: self.screen_flow._delete_behavior_connection_id(
                self.screen_flow.graph.selected_behavior_connection_id or ""
            )
        )
        self.flow_insert_action_action.triggered.connect(
            self.screen_flow._insert_action_into_behavior_connection
        )
        self.flow_align_action.triggered.connect(
            self.screen_flow._align_selected_behavior_nodes_left
        )
        self.flow_distribute_action.triggered.connect(
            self.screen_flow._distribute_selected_behavior_nodes_vertically
        )
        self.flow_open_screen_action.triggered.connect(
            self.screen_flow._open_selected_screen
        )
        self.flow_start_screen_action.triggered.connect(
            self.screen_flow._set_start_screen
        )
        self.flow_add_relation_action.triggered.connect(self.screen_flow._add_relation)
        self.flow_update_relation_action.triggered.connect(
            self.screen_flow._update_relation
        )
        self.flow_delete_relation_action.triggered.connect(
            lambda: self.screen_flow._delete_connection_id(
                self.screen_flow.graph.selected_connection_id or ""
            )
        )
        self.flow_add_behavior_connection_action.triggered.connect(
            self.screen_flow._add_behavior_connection_from_inspector
        )
        self.flow_update_behavior_connection_action.triggered.connect(
            self.screen_flow._update_selected_behavior_connection
        )
        self.flow_fit_action.triggered.connect(self.screen_flow._fit_graph_nodes)
        self.flow_auto_layout_action.triggered.connect(
            self.screen_flow._auto_layout_nodes
        )
        self.flow_reset_test_action.triggered.connect(self.screen_flow._reset_simulator)

        self.library_import_image_action.triggered.connect(
            self._import_image_to_asset_library
        )
        self.library_add_action.triggered.connect(
            self.library_workspace._request_add_to_project
        )
        self.library_edit_action.triggered.connect(
            self.library_workspace._request_edit_copy
        )
        self.library_replace_action.triggered.connect(
            self.library_workspace._request_replace
        )
        self.library_duplicate_action.triggered.connect(
            self.library_workspace._request_duplicate
        )
        self.library_export_action.triggered.connect(
            self.library_workspace._request_export
        )
        self.library_rename_action.triggered.connect(
            self.library_workspace._request_rename
        )
        self.library_delete_action.triggered.connect(
            self.library_workspace._request_delete
        )
        self.library_undo_action.triggered.connect(self._undo_library_change)
        self.library_redo_action.triggered.connect(self._redo_library_change)
        self.library_copy_path_action.triggered.connect(
            self.library_workspace.copy_path_button.click
        )
        self.library_refresh_action.triggered.connect(
            self._refresh_personal_asset_library
        )

        self.pixel_workflow_help_action.triggered.connect(
            lambda: self._show_bundled_document(
                "Pixel Art Workflow", "PIXEL_ART_WORKFLOW_PLAN.md"
            )
        )
        self.mqtt_tutorial_help_action.triggered.connect(
            lambda: self._show_bundled_document(
                "MQTT Client Tutorial", "MQTT_CLIENT_TUTORIAL.md"
            )
        )
        self.app_flow_help_action.triggered.connect(
            lambda: self._show_bundled_document(
                "App Flow Standard v2", "APP_FLOW_STANDARD_V2.md"
            )
        )
        self.generated_app_help_action.triggered.connect(
            lambda: self._show_bundled_document(
                "Generated App Structure", "GENERATED_APP_STRUCTURE.md"
            )
        )
        self.pga_help_action.triggered.connect(
            lambda: self._show_bundled_document(
                "PGA3 Resource Format", "PGA3_FORMAT.md"
            )
        )
        self.shortcuts_help_action.triggered.connect(self._show_keyboard_shortcuts)
        self.about_action.triggered.connect(self._show_about)
        self.open_mqtt_example_action.triggered.connect(
            self._open_mqtt_client_example
        )
        self.file_menu.aboutToShow.connect(self._update_file_menu)
        self.recent_menu.aboutToShow.connect(self._rebuild_recent_menu)
        self.pixel_menu.aboutToShow.connect(self._update_workspace_menu_actions)
        self.app_menu.aboutToShow.connect(self._update_workspace_menu_actions)
        self.flow_menu.aboutToShow.connect(self._update_workspace_menu_actions)
        self.library_menu.aboutToShow.connect(self._update_workspace_menu_actions)
        self.workspace_tabs.currentChanged.connect(self._workspace_changed)
        self.screen_flow.open_screen_requested.connect(self._open_designed_screen)
        self.screen_flow.open_simulator_requested.connect(
            self._open_simulator_workspace
        )
        self.screen_flow.run_simulator_requested.connect(self._run_current_design)
        self.screen_designer.pixel_asset_edit_requested.connect(
            self._edit_gui_pixel_asset
        )
        self.screen_designer.project_asset_edit_requested.connect(
            self._edit_project_asset
        )
        self.screen_designer.library_asset_delete_requested.connect(
            self._delete_library_asset
        )
        self.screen_designer.library_asset_rename_requested.connect(
            self._rename_library_asset
        )
        self.library_workspace.add_to_project_requested.connect(
            self._add_library_asset_to_current_project
        )
        self.library_workspace.edit_copy_requested.connect(
            self._edit_library_asset_copy
        )
        self.library_workspace.replace_requested.connect(
            self._replace_library_asset_from_image
        )
        self.library_workspace.duplicate_requested.connect(
            self._duplicate_library_asset
        )
        self.library_workspace.export_requested.connect(
            self._export_library_asset_frame
        )
        self.library_workspace.rename_requested.connect(self._rename_library_asset)
        self.library_workspace.delete_requested.connect(self._delete_library_asset)
        self.library_workspace.import_requested.connect(
            self._import_image_to_asset_library
        )
        self.library_workspace.import_pga_requested.connect(
            self._import_pga_to_asset_library
        )
        self.library_workspace.refresh_requested.connect(
            self._refresh_personal_asset_library
        )
        self.library_workspace.undo_requested.connect(self._undo_library_change)
        self.library_workspace.redo_requested.connect(self._redo_library_change)
        self.library_workspace.asset_list.itemSelectionChanged.connect(
            self._update_workspace_menu_actions
        )
        self.screen_designer.library_element_save_requested.connect(
            self._save_project_element_to_library
        )
        self.screen_designer.library_manage_requested.connect(
            lambda: self._activate_workspace(WorkspaceId.ASSET_LIBRARY)
        )
        self.screen_designer.flow_edit_requested.connect(self._open_element_flow)
        self.screen_designer.starter_requested.connect(
            self._new_gui_project_from_preset
        )
        self.screen_designer.design_preview_requested.connect(self._open_design_preview)
        self.screen_designer.preview_requested.connect(self._run_current_design)
        self.simulator_workspace.running_changed.connect(
            self._simulator_running_changed
        )
        self.simulator_workspace.status_changed.connect(self._simulator_status_changed)
        self.simulator_workspace.error_changed.connect(self._simulator_error_changed)
        self.designer_session.dirty_changed.connect(self._designer_dirty_changed)
        self.designer_session.dirty_changed.connect(self._update_document_strip)
        self.designer_session.project_changed.connect(self._update_document_strip)
        self.designer_session.dirty_changed.connect(self._schedule_designer_recovery)
        self.designer_session.project_changed.connect(self._update_import_actions)
        self.designer_session.project_changed.connect(self._schedule_designer_recovery)
        self.designer_session.history_changed.connect(self._update_history_actions)
        self.designer_session.history_changed.connect(self._schedule_designer_recovery)
        self.designer_recovery_timer.timeout.connect(self._write_designer_recovery)
        self.pixel_recovery_timer.timeout.connect(self._write_pixel_recovery)

    def _open_active_document(self) -> None:
        """Open the document type owned by the visible workspace."""
        workspace = self._current_workspace()
        if workspace == WorkspaceId.PIXEL_ART:
            self._open_file()
        elif workspace == WorkspaceId.ASSET_LIBRARY:
            self._import_image_to_asset_library()
        else:
            self._open_gui_project()

    def _close_active_document(self) -> None:
        """Close only the active workspace document after its normal confirmation."""
        workspace = self._current_workspace()
        if workspace == WorkspaceId.ASSET_LIBRARY:
            return
        if workspace == WorkspaceId.PIXEL_ART:
            if self._scan_path is not None:
                self._close_source()
                return
            if not self._pixel_document_available() or not self._confirm_discard():
                return
            self.current_asset = None
            self.current_trace = None
            self.variant_values.clear()
            self.animation_asset_key = None
            self.animation_images.clear()
            self.animation_drafts.clear()
            self._pending_image_path = None
            self._pending_image_uses_canvas = False
            self._editing_project_asset_id = ""
            self._editing_library_asset_id = ""
            self._editing_library_asset_revision = ""
            self.canvas.set_reference_image(None)
            self.reference_status_label.setText("No reference loaded")
            self._clear_editor()
            self._clear_pixel_recovery()
            self.statusBar().showMessage("Closed Pixel Art document.")
            self._update_file_menu()
            return
        if not self._confirm_designer_discard():
            return
        self.simulator_workspace.stop_live_simulator()
        self._clear_designer_recovery()
        self.designer_session.set_project(GuiProject.create())
        self._activate_workspace(WorkspaceId.APP_GUI)
        self.statusBar().showMessage("Closed GUI project; ready for a new project.")

    def _pixel_document_available(self) -> bool:
        """Return whether Pixel Art currently owns a source or editable document."""
        return bool(
            self._scan_path is not None
            or self.current_asset is not None
            or self._is_portable_pixel_asset()
            or self._editing_project_asset_id
            or self._editing_library_asset_id
        )

    def _update_file_menu(self) -> None:
        """Give global document commands exact active-workspace semantics."""
        workspace = self._current_workspace()
        pixel_workspace = workspace == WorkspaceId.PIXEL_ART
        library_workspace = workspace == WorkspaceId.ASSET_LIBRARY
        project_workspace = not pixel_workspace and not library_workspace
        if pixel_workspace:
            self.open_active_action.setText("Open Pixel Art Source...")
            self.close_active_action.setText("Close Pixel Art Document")
            self.save_active_action.setText(
                "Save Asset to Library"
                if self.current_asset is None and self._is_portable_pixel_asset()
                else "Save Pixel Asset"
            )
            self.save_as_active_action.setText("Export Pixel Asset as PNG...")
        elif library_workspace:
            self.open_active_action.setText("Import Image into Asset Library...")
            self.close_active_action.setText("Close Active Document")
            self.save_active_action.setText("Library Saves Automatically")
            self.save_as_active_action.setText("Save As...")
        else:
            self.open_active_action.setText("Open GUI Project...")
            self.close_active_action.setText("Close GUI Project")
            self.save_active_action.setText("Save GUI Project")
            self.save_as_active_action.setText("Save GUI Project As...")
        self.close_active_action.setEnabled(
            self._pixel_document_available() if pixel_workspace else project_workspace
        )
        self.rescan_action.setVisible(pixel_workspace)
        self.import_image_asset_action.setVisible(pixel_workspace)
        self.import_frames_action.setVisible(pixel_workspace)
        self.import_pga_action.setVisible(pixel_workspace or library_workspace)
        self.import_existing_app_action.setVisible(project_workspace)
        self.export_action.setVisible(pixel_workspace)
        self.generate_python_action.setVisible(pixel_workspace)
        self.export_generated_app_action.setVisible(project_workspace)
        self.export_gui_action.setVisible(project_workspace)
        self.import_menu.menuAction().setVisible(
            pixel_workspace or project_workspace or library_workspace
        )
        self.export_menu.menuAction().setVisible(pixel_workspace or project_workspace)

    def _recent_paths(self) -> list[Path]:
        """Return existing recent paths in stable most-recent-first order."""
        stored = self.settings.value("recent/paths", [])
        values = [stored] if isinstance(stored, str) else list(stored or [])
        paths: list[Path] = []
        seen: set[str] = set()
        for value in values:
            path = Path(str(value)).expanduser()
            key = str(path)
            if key in seen or not path.exists():
                continue
            seen.add(key)
            paths.append(path)
        return paths[:8]

    def _remember_recent_path(self, path: str | Path) -> None:
        """Persist one successfully opened source or GUI project path."""
        resolved = Path(path).expanduser().resolve()
        paths = [item for item in self._recent_paths() if item != resolved]
        self.settings.setValue(
            "recent/paths", [str(item) for item in [resolved, *paths][:8]]
        )

    def _rebuild_recent_menu(self) -> None:
        """Rebuild the bounded recent list without retaining missing paths."""
        self.recent_menu.clear()
        paths = self._recent_paths()
        if not paths:
            empty = self.recent_menu.addAction("No Recent Documents")
            empty.setEnabled(False)
            return
        for path in paths:
            action = self.recent_menu.addAction(str(path))
            action.setData(str(path))
            action.triggered.connect(
                lambda checked=False, recent=path: self._open_recent_path(recent)
            )
        self.recent_menu.addSeparator()
        self.recent_menu.addAction("Clear Recent List", self._clear_recent_paths)

    def _clear_recent_paths(self) -> None:
        """Forget recent-document metadata without deleting any document."""
        self.settings.remove("recent/paths")
        self._rebuild_recent_menu()

    def _open_recent_path(self, path: Path) -> None:
        """Open a recent source or GUI project with the correct dirty guard."""
        if not path.exists():
            self._rebuild_recent_menu()
            QMessageBox.warning(self, "Recent document missing", str(path))
            return
        if path.name.endswith(".picogui.json"):
            if not self._confirm_designer_discard():
                return
        elif not self._confirm_discard():
            return
        self.open_path(path)

    def _update_workspace_menu_actions(self) -> None:
        """Enable contextual menu commands from the current live selection."""
        selected = bool(self.screen_designer.selected_element_ids)
        selected_count = len(self.screen_designer.selected_element_ids)
        element = self.screen_designer._selected_element()
        asset = (
            self.designer_session.project.asset(element.asset_id)
            if element is not None and element.asset_id
            else None
        )
        self.app_delete_screen_action.setEnabled(
            len(self.designer_session.project.screens) > 1
        )
        for action in (
            self.app_duplicate_elements_action,
            self.app_lock_action,
            self.app_visibility_action,
            self.app_delete_elements_action,
        ):
            action.setEnabled(selected)
        self.app_edit_asset_action.setEnabled(asset is not None)
        self.app_save_asset_action.setEnabled(asset is not None)
        screen = self.designer_session.current_screen()
        self.app_natural_size_action.setEnabled(
            asset is not None
            and asset.width <= screen.width
            and asset.height <= screen.height
        )
        self.app_bake_size_action.setEnabled(
            asset is not None
            and element is not None
            and 1 <= element.width <= 320
            and 1 <= element.height <= 320
        )
        current_order = [item.id for item in screen.elements]
        for mode, action in self.app_layer_actions.items():
            proposed = [
                item.id
                for item in self.screen_designer._ordered_elements_for_layer_move(mode)
            ]
            action.setEnabled(selected and proposed != current_order)
        for mode, action in self.app_alignment_actions.items():
            required = 3 if mode.startswith("distribute") else 2
            action.setEnabled(selected_count >= required)

        graph = self.screen_flow.graph
        selected_nodes = bool(graph.selected_behavior_node_ids)
        selected_node_count = len(graph.selected_behavior_node_ids)
        selected_connection = self.designer_session.project.behavior_connection(
            graph.selected_behavior_connection_id or ""
        )
        self.flow_duplicate_nodes_action.setEnabled(selected_nodes)
        self.flow_group_nodes_action.setEnabled(selected_nodes)
        self.flow_trace_action.setEnabled(selected_nodes)
        self.flow_delete_nodes_action.setEnabled(selected_nodes)
        self.flow_delete_edge_action.setEnabled(
            bool(graph.selected_behavior_connection_id)
        )
        self.flow_insert_action_action.setEnabled(
            self.screen_flow._can_insert_action_into_connection(selected_connection)
        )
        self.flow_align_action.setEnabled(selected_node_count >= 2)
        self.flow_distribute_action.setEnabled(selected_node_count >= 3)
        self.flow_open_screen_action.setEnabled(bool(graph.selected_screen_id))
        self.flow_start_screen_action.setEnabled(bool(graph.selected_screen_id))
        navigation_connection = self.screen_flow._selected_connection()
        self.flow_add_relation_action.setEnabled(
            bool(self.designer_session.project.screens)
        )
        self.flow_update_relation_action.setEnabled(
            bool(navigation_connection and not navigation_connection.locked)
        )
        self.flow_delete_relation_action.setEnabled(
            bool(navigation_connection and not navigation_connection.source_path)
        )
        self.flow_add_behavior_connection_action.setEnabled(
            bool(
                self.screen_flow.behavior_source_node_combo.currentData()
                and self.screen_flow.behavior_source_port_combo.currentData()
                and self.screen_flow.behavior_target_node_combo.currentData()
                and self.screen_flow.behavior_target_port_combo.currentData()
            )
        )
        self.flow_update_behavior_connection_action.setEnabled(
            bool(selected_connection and not selected_connection.locked)
        )

        for action, button in (
            (self.library_add_action, self.library_workspace.add_to_project_button),
            (self.library_edit_action, self.library_workspace.edit_copy_button),
            (self.library_replace_action, self.library_workspace.replace_button),
            (self.library_duplicate_action, self.library_workspace.duplicate_button),
            (self.library_export_action, self.library_workspace.export_button),
            (self.library_rename_action, self.library_workspace.rename_button),
            (self.library_delete_action, self.library_workspace.delete_button),
        ):
            action.setEnabled(button.isEnabled())
        self.library_import_image_action.setEnabled(
            self.library_workspace.storage_available()
        )
        self.import_pga_action.setEnabled(self.library_workspace.storage_available())
        history_available = self.library_workspace.storage_available()
        self.library_undo_action.setEnabled(
            bool(self._library_undo_stack) and history_available
        )
        self.library_redo_action.setEnabled(
            bool(self._library_redo_stack) and history_available
        )

        frame_count = (
            len(self._portable_frames)
            if self._is_portable_pixel_asset()
            else self.frame_combo.count()
            if self.animation_parameter is not None
            else 1
            if self._pixel_document_available()
            else 0
        )
        frame_index = (
            self._portable_frame_index
            if self._is_portable_pixel_asset()
            else max(0, self.frame_combo.currentIndex())
        )
        frame_available = self._current_asset_is_managed()
        self.add_frame_action.setEnabled(frame_available)
        self.duplicate_frame_action.setEnabled(frame_available)
        self.delete_frame_action.setEnabled(frame_available and frame_count > 1)
        self.move_frame_left_action.setEnabled(
            frame_available and frame_count > 1 and frame_index > 0
        )
        self.move_frame_right_action.setEnabled(
            frame_available and frame_count > 1 and frame_index < frame_count - 1
        )
        self.play_animation_action.setEnabled(frame_count > 1)

    def _show_bundled_document(self, title: str, filename: str) -> None:
        """Open one shipped Markdown guide in a selectable viewer."""
        path = Path(__file__).with_name(filename)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            QMessageBox.critical(self, "Cannot open documentation", str(error))
            return
        TextReportDialog(
            title, text, self, f"Bundled documentation: {path.name}"
        ).exec()

    def _open_project_properties(self) -> None:
        """Focus the project identity and device controls in App GUI."""
        self._activate_workspace(WorkspaceId.APP_GUI)
        self.screen_designer.project_name_edit.setFocus()
        self.screen_designer.project_name_edit.selectAll()

    def _show_keyboard_shortcuts(self) -> None:
        """Show every named application shortcut without duplicating menu discovery."""
        entries: dict[str, str] = {}
        for action in self.findChildren(QAction):
            shortcut = action.shortcut().toString(
                QKeySequence.SequenceFormat.NativeText
            )
            text = action.text().replace("&", "").strip()
            if shortcut and text:
                entries.setdefault(shortcut, text)
        lines = [f"{shortcut:<18} {entries[shortcut]}" for shortcut in sorted(entries)]
        TextReportDialog(
            "Keyboard Shortcuts",
            "\n".join(lines),
            self,
            "Shortcuts follow the active workspace; hidden documents are never targeted.",
        ).exec()

    def _show_about(self) -> None:
        """Show the installed editor version and product scope."""
        QMessageBox.about(
            self,
            "About Pico Graphics Editor",
            f"Pico Graphics and GUI Designer {__version__}\n\n"
            "Pixel Art, GUI project, Screen Flow, simulator, and reusable asset "
            "library tooling for Picoware.",
        )

    def _validate_gui_project(self) -> bool:
        """Run complete in-memory generation preflight and show all known issues."""
        project = self.designer_session.project
        diagnostics = project_preflight_diagnostics(project)
        generation_error = ""
        bundle_size = 0
        if not any(item.severity == "error" for item in diagnostics):
            try:
                bundle = build_live_preview_bundle(
                    project, self.designer_session.active_screen_id
                )
                bundle_size = sum(
                    len(content.encode("utf-8"))
                    if isinstance(content, str)
                    else len(content)
                    for unused_name, content in bundle.files
                )
            except (OSError, SyntaxError, TypeError, ValueError) as error:
                generation_error = str(error)
        errors = sum(item.severity == "error" for item in diagnostics) + bool(
            generation_error
        )
        warnings = sum(item.severity == "warning" for item in diagnostics)
        information = sum(item.severity == "info" for item in diagnostics)
        lines = [
            f"Project: {project.name}",
            f"Screens: {len(project.screens)}",
            f"Assets: {len(project.assets)}",
            f"Errors: {errors}   Warnings: {warnings}   Information: {information}",
            "",
        ]
        if diagnostics:
            for item in diagnostics:
                target = (
                    f" [{item.target_kind}: {item.target_id}]" if item.target_id else ""
                )
                lines.append(
                    f"{item.severity.upper():7} {item.code}{target}\n"
                    f"        {item.message}"
                )
        if generation_error:
            lines.append(f"ERROR   generation\n        {generation_error}")
        if not errors:
            lines.extend(
                (
                    "PASS    generation-ready",
                    f"        Live bundle built successfully ({bundle_size} bytes).",
                )
            )
        summary = (
            "Preflight passed. The current project can be generated in memory."
            if not errors
            else "Preflight found blocking problems. Resolve the ERROR entries before running or exporting."
        )
        TextReportDialog("Project Preflight", "\n".join(lines), self, summary).exec()
        return not errors

    def _save_active_workspace(self) -> bool:
        """Save only the dirty document owned by the active workspace."""
        workspace = self._current_workspace()
        if workspace == WorkspaceId.PIXEL_ART:
            if self._editing_library_asset_id:
                return self._apply_library_asset_edit() if self._dirty else False
            if self.current_asset is None and self._is_portable_pixel_asset():
                if self._editing_project_asset_id:
                    return self._apply_project_asset_edit() if self._dirty else False
                return self._save_current_asset_to_library()
            return self._apply_to_source() if self._dirty else False
        if workspace == WorkspaceId.ASSET_LIBRARY:
            return False
        return (
            self._save_gui_project()
            if self.designer_session.dirty or self.designer_session.path is None
            else False
        )

    def _save_as_active_workspace(self) -> bool:
        """Run the context-appropriate Save As or export operation."""
        workspace = self._current_workspace()
        if workspace == WorkspaceId.PIXEL_ART:
            return self._export_png()
        if workspace == WorkspaceId.ASSET_LIBRARY:
            return False
        return self._save_gui_project_as()

    def _open_simulator_workspace(self) -> None:
        """Focus the dedicated simulator workspace without changing process state."""
        self._activate_workspace(WorkspaceId.SIMULATOR)

    def _run_current_design(self) -> bool:
        """Open the simulator and run the current in-memory GUI project."""
        errors = [
            item
            for item in project_preflight_diagnostics(self.designer_session.project)
            if item.severity == "error"
        ]
        if errors:
            QMessageBox.critical(
                self,
                "Project preflight failed",
                f"{errors[0].message}\n\n"
                f"{len(errors)} blocking problem(s) found. Use Project > "
                "Validate / Preflight Project to review the complete report.",
            )
            return False
        self._open_simulator_workspace()
        return self.simulator_workspace.run_current_design()

    def _open_design_preview(self) -> None:
        """Open the simulator workspace in safe non-executing preview mode."""
        self._open_simulator_workspace()
        self.simulator_workspace.show_design_preview()

    def _simulator_running_changed(self, running: bool) -> None:
        """Keep global actions, tab badge, and document strip synchronized."""
        self.run_current_design_action.setEnabled(not running)
        self.restart_simulator_action.setEnabled(running)
        self.stop_simulator_action.setEnabled(running)
        self.capture_simulator_action.setEnabled(running)
        self.document_run_button.setEnabled(not running)
        self.screen_designer.preview_button.setEnabled(not running)
        self.screen_flow.run_simulator_button.setEnabled(not running)
        if running:
            self.workspace_tabs.setTabText(self.simulator_tab_index, "Simulator ●")
            self.document_simulator_button.setText("Simulator ● Running")
            self.document_simulator_button.setStyleSheet(
                "color: #1b5e20; font-weight: 600;"
            )
        else:
            if self.simulator_workspace.last_error():
                self.workspace_tabs.setTabText(self.simulator_tab_index, "Simulator !")
                self.document_simulator_button.setText("Simulator ! Error")
                self.document_simulator_button.setStyleSheet(
                    "color: #b71c1c; font-weight: 600;"
                )
            else:
                self.workspace_tabs.setTabText(self.simulator_tab_index, "Simulator")
                self.document_simulator_button.setText("Simulator · Stopped")
                self.document_simulator_button.setStyleSheet("")

    def _simulator_status_changed(self, status: str) -> None:
        """Expose the concise simulator state outside its workspace."""
        self.document_simulator_button.setToolTip(
            f"{status}\nExample: Click to open the Device Simulator."
        )
        if self.simulator_workspace.is_running():
            self.statusBar().showMessage(status)

    def _simulator_error_changed(self, error: str) -> None:
        """Keep a simulator failure visible after users switch workspaces."""
        self.copy_simulator_error_action.setEnabled(bool(error))
        if not error:
            self._simulator_running_changed(self.simulator_workspace.is_running())
            return
        self.workspace_tabs.setTabText(self.simulator_tab_index, "Simulator !")
        self.document_simulator_button.setText("Simulator ! Error")
        self.document_simulator_button.setStyleSheet(
            "color: #b71c1c; font-weight: 600;"
        )
        self.document_simulator_button.setToolTip(
            f"{error}\nExample: Click to review details and restart."
        )
        self.statusBar().showMessage("Simulator error. Open Simulator for details.")

    def _fit_pixel_canvas(self) -> None:
        """Fit the complete pixel document inside the canvas viewport."""
        art = self._editable_pixel_art()
        viewport = self.scroll_area.viewport().size()
        zoom = min(
            max(1, (viewport.width() - 24) // max(1, art.width)),
            max(1, (viewport.height() - 24) // max(1, art.height)),
        )
        self.zoom_spin.setValue(min(self.zoom_spin.maximum(), zoom))
        self._center_pixel_canvas()

    def _center_pixel_canvas(self) -> None:
        """Center both scroll bars on the pixel canvas."""
        for bar in (
            self.scroll_area.horizontalScrollBar(),
            self.scroll_area.verticalScrollBar(),
        ):
            bar.setValue((bar.minimum() + bar.maximum()) // 2)

    def _update_document_strip(self, *args: object) -> None:
        """Show the active save target and modified state persistently."""
        if not hasattr(self, "workspace_tabs"):
            return
        workspace_id = self._current_workspace()
        workspace = dict(WORKSPACE_ORDER)[workspace_id]
        if workspace_id == WorkspaceId.PIXEL_ART:
            project_asset = self.designer_session.project.asset(
                self._editing_project_asset_id
            )
            if project_asset is not None:
                path = self.designer_session.path
                name = f"{project_asset.name} (GUI project asset)"
                button = "Update Project Asset"
                saved_state = "In GUI project"
            elif self._editing_library_asset_id:
                path = self.asset_library.path
                name = f"{self._active_pixel_name()} (library asset)"
                button = "Update Library Asset"
                saved_state = "In Personal Library"
            elif self.current_asset is not None:
                path = self.current_asset.document.path
                name = (
                    f"{self.current_asset.record.name} · "
                    f"{self.current_asset.document.path.name}"
                )
                button = "Save Python Asset"
                saved_state = "Saved"
            elif self._is_portable_pixel_asset():
                path = self._pending_image_path
                name = f"{self._active_pixel_name()} (unsaved asset)"
                button = "Save to Library"
                saved_state = "Not saved"
            else:
                path = None
                name = "No asset selected"
                button = "Save Asset"
                saved_state = "No document"
            dirty = self._dirty
            state = "Modified" if dirty else saved_state
            save_enabled = dirty or (
                self._is_portable_pixel_asset()
                and not self._editing_project_asset_id
                and not self._editing_library_asset_id
            )
            save_as_enabled = (
                self.current_asset is not None or self._is_portable_pixel_asset()
            )
        elif workspace_id in {
            WorkspaceId.APP_GUI,
            WorkspaceId.SCREEN_FLOW,
            WorkspaceId.SIMULATOR,
        }:
            path = self.designer_session.path
            name = (
                path.name if path else f"{self.designer_session.project.name} (unsaved)"
            )
            dirty = self.designer_session.dirty
            button = "Save GUI Project"
            state = (
                "Modified" if dirty else ("Saved" if path is not None else "Not saved")
            )
            save_enabled = dirty or path is None
            save_as_enabled = True
        else:
            path = self.asset_library.path
            name = "Personal Asset Library"
            dirty = False
            button = "Saved automatically"
            state = "Auto-saved"
            save_enabled = False
            save_as_enabled = False
        self.document_workspace_label.setText(workspace)
        self.document_name_label.setText(name)
        self.document_name_label.setToolTip(str(path) if path else "No save path yet")
        self.document_state_label.setText(state)
        self.document_state_label.setAccessibleName(f"Document state: {state}")
        self.document_save_button.setText(button)
        self.document_save_button.setVisible(workspace_id != WorkspaceId.ASSET_LIBRARY)
        self.document_save_button.setEnabled(
            save_enabled and workspace_id != WorkspaceId.ASSET_LIBRARY
        )
        pixel_available = workspace_id == WorkspaceId.PIXEL_ART and (
            self.current_asset is not None or self._is_portable_pixel_asset()
        )
        self.document_python_button.setVisible(pixel_available)
        self.document_python_button.setEnabled(pixel_available)
        self.document_library_button.setText(
            "Save Copy to Library"
            if self._editing_library_asset_id
            else "Save to Library"
        )
        self.document_library_button.setVisible(
            workspace_id == WorkspaceId.PIXEL_ART
            and pixel_available
            and not (
                self._is_portable_pixel_asset()
                and not self._editing_project_asset_id
                and not self._editing_library_asset_id
            )
        )
        self.save_active_action.setEnabled(save_enabled)
        self.save_as_active_action.setEnabled(save_as_enabled)
        self._update_file_menu()

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

    def _close_source(self) -> None:
        """Close the scanned source scope without changing GUI or library data."""
        if self._scan_path is None:
            return
        if not self._confirm_discard():
            return
        self._thumbnail_generation += 1
        self._thumbnail_queue.clear()
        self._stop_animation()
        self._scan_path = None
        self._scan_folder = False
        self.assets.clear()
        self.current_asset = None
        self.current_trace = None
        self.variant_values.clear()
        self.animation_asset_key = None
        self.animation_images.clear()
        self.animation_drafts.clear()
        self._animation_structure_dirty = False
        self._pending_image_path = None
        self._pending_image_uses_canvas = False
        self._draft_asset_name = ""
        self._portable_frames.clear()
        self._portable_durations.clear()
        self._portable_frame_index = 0
        self._editing_project_asset_id = ""
        self._editing_project_asset_frame = 0
        self._editing_library_asset_id = ""
        self._editing_library_asset_revision = ""
        self._dirty = False
        self.search_edit.clear()
        self.asset_list.clear()
        self.asset_count_label.setText("No folder or Python file open")
        self.source_scope_label.setText("No source open")
        self.source_scope_label.setToolTip(
            "No Python source folder or file is currently scanned.\n"
            "Example: Choose File > Open > Open Python Folder to start another source session."
        )
        self.screen_designer.set_pixel_assets([])
        self._clear_editor()
        self.warning_text.setPlainText(
            "No source is open. Open a Python file or folder to discover graphics."
        )
        self.close_source_action.setEnabled(False)
        self.close_source_button.setEnabled(False)
        self.rescan_action.setEnabled(False)
        self._clear_pixel_recovery()
        self._update_document_strip()
        self.statusBar().showMessage(
            "Source folder closed. GUI projects and personal-library assets were kept."
        )
        self.toggle_catalogue_action.setChecked(False)
        self.catalogue_panel.setVisible(False)

    def _is_portable_pixel_asset(self) -> bool:
        """Return whether an in-memory draft, library, or project asset is open."""
        return self.current_asset is None and bool(self._portable_frames)

    def _active_pixel_name(self) -> str:
        """Return the best user-facing name for the visible pixel document."""
        if self.current_asset is not None:
            return self.current_asset.record.name
        if self._editing_project_asset_id:
            asset = self.designer_session.project.asset(self._editing_project_asset_id)
            if asset is not None:
                return asset.name
        if self._editing_library_asset_id:
            return self._draft_asset_name or "Library Asset"
        return self._draft_asset_name or "Untitled Asset"

    def _capture_portable_frame(self) -> None:
        """Copy current canvas pixels into the active portable animation frame."""
        if not self._portable_frames:
            return
        index = max(0, min(self._portable_frame_index, len(self._portable_frames) - 1))
        self._portable_frames[index] = self.canvas.art().copy()

    def _open_portable_pixel_asset(
        self,
        name: str,
        frames: list[PixelArt] | tuple[PixelArt, ...],
        durations: list[int] | tuple[int, ...] | None = None,
        *,
        source_path: Path | None = None,
        frame_index: int = 0,
        dirty: bool = True,
        source_label: str = "Unsaved pixel asset",
        mode_label: str = "UNSAVED · editable pixel asset",
    ) -> None:
        """Open complete lossless frames as one editable in-memory asset."""
        source_frames = [frame.copy() for frame in frames]
        encode_asset(source_frames, durations)
        self._stop_animation()
        self.current_asset = None
        self.current_trace = None
        self._draft_asset_name = name.strip() or "Untitled Asset"
        self._pending_image_path = source_path
        self._pending_image_uses_canvas = True
        self._portable_frames = source_frames
        self._portable_durations = list(durations or ())
        self._portable_frame_index = max(
            0, min(int(frame_index), len(source_frames) - 1)
        )
        self.animation_parameter = "frame" if len(source_frames) > 1 else None
        self.variant_values = {"frame": self._portable_frame_index}
        self.frame_combo.blockSignals(True)
        self.frame_combo.clear()
        for index in range(len(source_frames)):
            self.frame_combo.addItem(f"Frame {index + 1}", index)
        self.frame_combo.setCurrentIndex(self._portable_frame_index)
        self.frame_combo.blockSignals(False)
        if self._portable_durations:
            self.frame_interval_spin.blockSignals(True)
            self.frame_interval_spin.setValue(
                self._portable_durations[self._portable_frame_index]
            )
            self.frame_interval_spin.blockSignals(False)
        self.animation_group.setVisible(len(source_frames) > 1)
        self.variant_group.setVisible(False)
        self._suppress_changes = True
        self.canvas.set_art(source_frames[self._portable_frame_index].copy())
        self._suppress_changes = False
        self.canvas.set_reference_image(None)
        self.canvas.set_transparent_eraser(True)
        self.canvas.setEnabled(True)
        self.asset_title.setText(self._draft_asset_name)
        self.source_label.setText(source_label)
        self.asset_mode_label.setText(mode_label)
        self.asset_mode_label.setStyleSheet("color: #2e7d32; font-weight: 600;")
        self.source_view_combo.setVisible(False)
        self.source_view_label.setVisible(False)
        self.warning_text.setPlainText(
            "The visible canvas contains the actual editable RGB565 pixels. "
            "Save to the library, place it in App GUI, generate Python, or export PNG."
        )
        self._dirty = dirty
        self._animation_structure_dirty = False
        self._refresh_animation_labels()
        self._update_palette(self.canvas.art())
        self._update_preview()
        self._update_apply_state()
        self._update_pixel_action_state()
        self.pixel_empty_widget.setVisible(False)
        self.catalogue_panel.setVisible(self._scan_path is not None)
        self._activate_workspace(WorkspaceId.PIXEL_ART)
        if dirty:
            self._schedule_pixel_recovery()

    def _import_image_asset(self) -> None:
        """Import an image or animation directly as actual editable RGB565 pixels."""
        if not self._confirm_discard():
            return
        filename, _ = get_open_image_filename(
            self,
            "Import image as pixel asset",
            str(Path.cwd()),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)",
            accept_label="Import",
        )
        if not filename:
            return
        images = read_image_frames(filename)
        if not images:
            QMessageBox.warning(
                self, "Cannot import image", "The selected image could not be decoded."
            )
            return
        frames = self._library_frames_from_images(images)
        durations = (
            [self.frame_interval_spin.value()] * len(frames)
            if len(frames) > 1
            else None
        )
        self._editing_project_asset_id = ""
        self._editing_library_asset_id = ""
        self._editing_library_asset_revision = ""
        self._open_portable_pixel_asset(
            Path(filename).stem,
            frames,
            durations,
            source_path=Path(filename).expanduser().resolve(),
            source_label=f"Imported from {Path(filename).name}",
            mode_label="UNSAVED · imported editable RGB565 asset",
        )
        self.statusBar().showMessage(
            f"Imported {Path(filename).name} as {len(frames)} editable frame(s). "
            "The canvas now shows the actual pixels that will be saved."
        )

    def _open_reference_image(self) -> None:
        """Open one image as a movable tracing reference."""
        if self.current_asset is None and not self._is_portable_pixel_asset():
            QMessageBox.information(
                self,
                "Open an asset first",
                "Create or import a pixel asset before adding a tracing reference.",
            )
            return
        if self.current_asset is not None and self.animation_parameter is None:
            QMessageBox.information(
                self,
                "Select an animation",
                "The selected Python graphic has no frame or timing parameter.",
            )
            return
        filename, _ = get_open_image_filename(
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
        self.reference_import_group.setChecked(True)
        self.statusBar().showMessage(
            f"Tracing reference loaded: {filename}. It is not part of saved pixels "
            "until Convert to editable pixels is used."
        )

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
        if self.current_asset is None and not self._is_portable_pixel_asset():
            QMessageBox.information(
                self,
                "Open an asset first",
                "Create or import a pixel asset before adding animation frames.",
            )
            return
        filename, _ = get_open_image_filename(
            self,
            "Import animation frames",
            str(Path.cwd()),
            "Images (*.gif *.webp *.png *.bmp *.jpg *.jpeg)",
            accept_label="Import frames",
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
        if self._is_portable_pixel_asset():
            self._capture_portable_frame()
            art = self.canvas.art()
            converted = self._convert_new_graphic_images(
                frames,
                art.width,
                art.height,
            )
            self._portable_frames.extend(converted)
            interval = self.frame_interval_spin.value()
            if not self._portable_durations:
                self._portable_durations = [interval] * (
                    len(self._portable_frames) - len(converted)
                )
            self._portable_durations.extend([interval] * len(converted))
            self.animation_parameter = "frame"
            self.animation_group.setVisible(True)
            self.frame_combo.blockSignals(True)
            self.frame_combo.clear()
            for index in range(len(self._portable_frames)):
                self.frame_combo.addItem(f"Frame {index + 1}", index)
            target_index = len(self._portable_frames) - len(converted)
            self.frame_combo.blockSignals(False)
            self.frame_combo.setCurrentIndex(target_index)
            self._mark_animation_structure_changed()
            self._refresh_animation_labels()
            self.statusBar().showMessage(
                f"Added {len(converted)} editable animation frame(s)."
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

    def _create_new_graphic(self) -> bool:
        """Create an editable in-memory asset before choosing an output target."""
        if not self._confirm_discard():
            return False
        current = self.canvas.art()
        dialog = NewGraphicDialog(
            current.width,
            current.height,
            self.canvas.has_reference_image(),
            len(self.animation_images),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        asset_name, width, height, creation_mode = dialog.settings()
        if not asset_name:
            QMessageBox.information(
                self, "Asset name required", "Enter a name for the editable asset."
            )
            return False
        if creation_mode == "blank":
            frames = [PixelArt(width, height)]
        elif creation_mode == "current":
            source = pixel_art_image(current)
            frames = self._convert_new_graphic_images([source], width, height)
        else:
            images = self._new_graphic_images(creation_mode, width, height)
            if not images:
                return False
            frames = self._convert_new_graphic_images(images, width, height)
        durations = (
            [self.frame_interval_spin.value()] * len(frames)
            if len(frames) > 1
            else None
        )
        self._editing_project_asset_id = ""
        self._editing_library_asset_id = ""
        self._editing_library_asset_revision = ""
        self._open_portable_pixel_asset(
            asset_name,
            frames,
            durations,
            source_label="Unsaved in-memory pixel asset",
            mode_label="UNSAVED · editable pixel asset",
        )
        self.statusBar().showMessage(
            "New editable asset created. Draw first, then save to Library, place in "
            "App GUI, generate Python, or export PNG."
        )
        return True

    def _generate_python_asset(self) -> bool:
        """Generate reviewed Python source from any visible editable pixel asset."""
        if self.current_asset is not None:
            return self._apply_to_source()
        if not self._is_portable_pixel_asset():
            return False
        self._capture_portable_frame()
        frames = [frame.copy() for frame in self._portable_frames]
        safe_name = (
            "".join(
                character if character.isalnum() else "_"
                for character in self._active_pixel_name()
            ).strip("_")
            or "pixel_asset"
        )
        function_name, accepted = QInputDialog.getText(
            self,
            "Generate Python pixel asset",
            "Drawing function name",
            text=f"draw_{safe_name.lower()}",
        )
        if not accepted or not function_name.strip():
            return False
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Choose Python destination",
            str(Path.cwd() / "graphics.py"),
            "Python files (*.py)",
        )
        if not filename:
            return False
        if not filename.endswith(".py"):
            filename += ".py"
        try:
            patch = build_new_graphic_patch(
                filename,
                function_name.strip(),
                frames,
            )
        except (OSError, SyntaxError, ValueError) as error:
            QMessageBox.critical(self, "Cannot generate Python asset", str(error))
            return False
        review = DiffDialog(patch, self)
        if review.exec() != QDialog.DialogCode.Accepted:
            return False
        try:
            backup = patch.apply(self._source_backup_root())
        except (OSError, SyntaxError, ValueError) as error:
            QMessageBox.critical(self, "Python generation failed", str(error))
            return False
        loaded = self._open_created_graphic(patch.path, patch.key)
        detail = f"Backup: {backup}" if backup else "Created a new Python file."
        QMessageBox.information(
            self,
            "Python asset generated",
            f"Updated {patch.path}.\n{detail}\n"
            + (
                "The generated function is now open for source-linked editing."
                if loaded
                else "Open the generated file manually to edit its source link."
            ),
        )
        return loaded

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
        filename, _ = get_open_image_filename(
            self,
            "Choose animation or sprite sheet",
            str(Path.cwd()),
            "Images (*.gif *.webp *.png *.bmp *.jpg *.jpeg)",
            accept_label="Use image",
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
        self._activate_workspace(WorkspaceId.PIXEL_ART)
        self.statusBar().showMessage(f"Created and loaded pixel asset: {target}")
        return True

    def _workspace_changed(self, index: int) -> None:
        """Show tools relevant to the selected workspace."""
        workspace = self._current_workspace()
        if (
            hasattr(self, "library_workspace")
            and workspace != WorkspaceId.ASSET_LIBRARY
        ):
            self.library_workspace.stop_playback()
        pixel_workspace = workspace == WorkspaceId.PIXEL_ART
        library_workspace = workspace == WorkspaceId.ASSET_LIBRARY
        project_workspace = not pixel_workspace and not library_workspace
        self.tool_bar.setVisible(pixel_workspace)
        self.document_run_button.setVisible(project_workspace)
        self.document_simulator_button.setVisible(project_workspace)
        self.pixel_menu.menuAction().setVisible(pixel_workspace)
        self.app_menu.menuAction().setVisible(workspace == WorkspaceId.APP_GUI)
        self.flow_menu.menuAction().setVisible(workspace == WorkspaceId.SCREEN_FLOW)
        self.library_menu.menuAction().setVisible(library_workspace)
        self.edit_menu.menuAction().setVisible(
            workspace
            in {
                WorkspaceId.APP_GUI,
                WorkspaceId.SCREEN_FLOW,
                WorkspaceId.PIXEL_ART,
                WorkspaceId.ASSET_LIBRARY,
            }
        )
        self.pixel_view_menu.menuAction().setVisible(pixel_workspace)
        for action in self.pixel_edit_actions:
            action.setVisible(pixel_workspace)
        if pixel_workspace:
            self._update_pixel_action_state()
        else:
            for action in (*self.pixel_edit_actions, *self.tool_group.actions()):
                action.setEnabled(False)
        self.gui_menu.menuAction().setVisible(project_workspace)
        self.project_properties_action.setEnabled(project_workspace)
        self.validate_project_action.setEnabled(project_workspace)
        for workspace_id, action in self.workspace_actions_by_id.items():
            action.setChecked(workspace_id == workspace)
        self.toggle_catalogue_action.setEnabled(
            pixel_workspace and self._scan_path is not None
        )
        self.statusBar().showMessage(f"Workspace: {dict(WORKSPACE_ORDER)[workspace]}")
        self._update_file_menu()
        self._update_workspace_menu_actions()
        self._update_history_actions()
        self._update_document_strip()

    def _undo_current(self) -> None:
        """Undo in the currently visible editor workspace."""
        workspace = self._current_workspace()
        if workspace == WorkspaceId.PIXEL_ART:
            self.canvas.undo()
        elif workspace in {WorkspaceId.APP_GUI, WorkspaceId.SCREEN_FLOW}:
            self.designer_session.undo()
        elif workspace == WorkspaceId.ASSET_LIBRARY:
            self._undo_library_change()
        self._update_history_actions()

    def _redo_current(self) -> None:
        """Redo in the currently visible editor workspace."""
        workspace = self._current_workspace()
        if workspace == WorkspaceId.PIXEL_ART:
            self.canvas.redo()
        elif workspace in {WorkspaceId.APP_GUI, WorkspaceId.SCREEN_FLOW}:
            self.designer_session.redo()
        elif workspace == WorkspaceId.ASSET_LIBRARY:
            self._redo_library_change()
        self._update_history_actions()

    def _update_history_actions(self, *args: object) -> None:
        """Enable undo and redo for the active workspace history."""
        if not hasattr(self, "workspace_tabs"):
            return
        workspace = self._current_workspace()
        if workspace == WorkspaceId.PIXEL_ART:
            self.undo_action.setEnabled(self.canvas.can_undo())
            self.redo_action.setEnabled(self.canvas.can_redo())
        elif workspace in {WorkspaceId.APP_GUI, WorkspaceId.SCREEN_FLOW}:
            self.undo_action.setEnabled(self.designer_session.can_undo())
            self.redo_action.setEnabled(self.designer_session.can_redo())
        elif workspace == WorkspaceId.ASSET_LIBRARY:
            available = self.library_workspace.storage_available()
            self.undo_action.setEnabled(bool(self._library_undo_stack) and available)
            self.redo_action.setEnabled(bool(self._library_redo_stack) and available)
        else:
            self.undo_action.setEnabled(False)
            self.redo_action.setEnabled(False)
        if hasattr(self, "library_workspace"):
            self._sync_library_history_state()

    def _open_designed_screen(self, screen_id: str) -> None:
        """Open a graph screen in the visual GUI designer."""
        self.designer_session.set_active_screen(screen_id)
        self._activate_workspace(WorkspaceId.APP_GUI)

    def _open_element_flow(self, screen_id: str, element_id: str) -> None:
        """Continue one selected App GUI interaction in Screen Flow."""
        self._activate_workspace(WorkspaceId.SCREEN_FLOW)
        self.screen_flow.focus_element_interaction(screen_id, element_id)
        created = self.screen_flow.create_behavior_from_element_dialog(
            screen_id, element_id
        )
        self.statusBar().showMessage(
            "Created a bound behavior from the selected App GUI element."
            if created
            else "Screen Flow is ready to connect the selected App GUI element."
        )

    def _designer_dirty_changed(self, dirty: bool) -> None:
        """Report designer project save state."""
        if dirty:
            self.statusBar().showMessage("GUI project has unsaved changes.")
        elif self.designer_session.path is None:
            self.statusBar().showMessage("GUI project is not saved yet.")
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
        self._activate_workspace(WorkspaceId.APP_GUI)

    def _new_gui_project_from_preset(self) -> None:
        """Create an editable multi-screen project from a built-in preset."""
        dialog = AppPresetDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        preset_id, name, profile = dialog.settings()
        if not preset_id or not name:
            return
        if not self._confirm_designer_discard():
            return
        try:
            project = build_app_preset(preset_id, name, profile)
        except (KeyError, TypeError, ValueError) as error:
            QMessageBox.critical(self, "Cannot create Picoware starter", str(error))
            return
        self._clear_designer_recovery()
        self.designer_session.set_project(project)
        self._activate_workspace(WorkspaceId.APP_GUI)
        preset = app_preset(preset_id)
        self.statusBar().showMessage(
            f"Created {preset.name} starter: {len(project.screens)} editable "
            f"{'screen' if len(project.screens) == 1 else 'screens'} for "
            f"{profile}."
        )

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

    def _open_mqtt_client_example(self) -> None:
        """Open an editable copy of the bundled MQTT client example."""
        if not self._confirm_designer_discard():
            return
        path = (
            Path(__file__).with_name("examples")
            / "mqtt_client"
            / "MQTT Client.picogui.json"
        )
        try:
            project = GuiProject.load(path)
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.critical(self, "Cannot open MQTT example", str(error))
            return
        project.generated_app = dict(project.generated_app)
        project.generated_app["destination"] = ""
        self._clear_designer_recovery()
        self.designer_session.set_project(project)
        self._activate_workspace(WorkspaceId.APP_GUI)
        self.statusBar().showMessage(
            "Opened an unsaved copy of the bundled MQTT Client example. "
            "Use Help > MQTT Client Tutorial for the guided workflow."
        )

    def _load_gui_project(self, path: Path) -> None:
        """Load a GUI project path with error reporting."""
        try:
            project = GuiProject.load(path)
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.critical(self, "Cannot open GUI project", str(error))
            return
        self.designer_session.set_project(project, path)
        self._remember_recent_path(path)
        self._activate_workspace(WorkspaceId.APP_GUI)
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
            self._make_gui_asset_links_relative(path)
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

    def _make_gui_asset_links_relative(self, project_path: Path) -> None:
        """Store portable source paths while retaining absolute recovery fallbacks."""
        for screen in self.designer_session.project.screens:
            for element in screen.elements:
                if not element.asset_qualified_name:
                    continue
                source_text, separator, unused_name = element.asset_key.rpartition("::")
                absolute = element.asset_absolute_fallback or (
                    source_text if separator else ""
                )
                if not absolute:
                    continue
                resolved = Path(absolute).expanduser().resolve()
                element.asset_absolute_fallback = str(resolved)
                try:
                    element.asset_source_path = os.path.relpath(
                        resolved, project_path.resolve().parent
                    )
                except ValueError:
                    element.asset_source_path = str(resolved)

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
        if not self._dirty or (
            self.current_asset is None and not self._is_portable_pixel_asset()
        ):
            return
        if self._is_portable_pixel_asset():
            self._capture_portable_frame()
            payload = {
                "format_version": 2,
                "kind": "portable",
                "name": self._active_pixel_name(),
                "durations": self._portable_durations,
                "frame_index": self._portable_frame_index,
                "frames": [
                    {
                        "width": frame.width,
                        "height": frame.height,
                        "origin_x": frame.origin_x,
                        "origin_y": frame.origin_y,
                        "pixels": frame.pixels,
                    }
                    for frame in self._portable_frames
                ],
            }
        else:
            art = self._editable_pixel_art()
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
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, text=True
            )
        except OSError as error:
            self.statusBar().showMessage(f"Pixel recovery failed: {error}")
            return
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
            if int(payload.get("format_version", 0)) == 2:
                records = payload.get("frames", [])
                if not isinstance(records, list) or not records:
                    raise ValueError("Recovered portable frames are invalid")
                frames = []
                for record in records:
                    width = int(record["width"])
                    height = int(record["height"])
                    pixels = record["pixels"]
                    if not 1 <= width <= 320 or not 1 <= height <= 320:
                        raise ValueError("Recovered pixel dimensions are invalid")
                    if not isinstance(pixels, list) or len(pixels) != width * height:
                        raise ValueError("Recovered pixel data is invalid")
                    frames.append(
                        PixelArt(
                            width,
                            height,
                            int(record.get("origin_x", 0)),
                            int(record.get("origin_y", 0)),
                            [
                                None if value is None else int(value) & 0xFFFF
                                for value in pixels
                            ],
                        )
                    )
                durations = [int(value) for value in payload.get("durations", [])]
                frame_index = int(payload.get("frame_index", 0))
                name = str(payload.get("name", "Recovered Asset"))
                if not self._confirm_discard():
                    return
                self._editing_project_asset_id = ""
                self._editing_library_asset_id = ""
                self._editing_library_asset_revision = ""
                self._open_portable_pixel_asset(
                    name,
                    frames,
                    durations or None,
                    frame_index=frame_index,
                    dirty=True,
                    source_label="Recovered unsaved pixel asset",
                    mode_label="RECOVERED · editable pixel asset",
                )
                self.statusBar().showMessage(f"Recovered unsaved pixel asset: {name}")
                return
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
        self._activate_workspace(WorkspaceId.PIXEL_ART)
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
        self._activate_workspace(WorkspaceId.APP_GUI)
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
        self._activate_workspace(WorkspaceId.APP_GUI)
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
        self._activate_workspace(WorkspaceId.APP_GUI)
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

    def _export_generated_app_structure(self) -> None:
        """Review and atomically export Generated App Structure v1."""
        project = self.designer_session.project
        remembered = str(project.generated_app.get("destination", ""))
        destination = QFileDialog.getExistingDirectory(
            self,
            "Export Generated App Structure v1",
            remembered or str(Path.cwd()),
        )
        if not destination:
            return
        try:
            patchset = build_generated_app_patchset(project, destination)
        except (OSError, SyntaxError, ValueError) as error:
            QMessageBox.critical(self, "Cannot export generated app", str(error))
            return
        if patchset.blocked:
            details = "\n".join(
                f"{patch.path}: {patch.message or patch.action}"
                for patch in patchset.patches
                if patch.action in {"conflict", "unsupported-version"}
            )
            QMessageBox.critical(
                self,
                "Generated app export blocked",
                details,
            )
            return
        dialog = GeneratedAppReviewDialog(patchset, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        backup_root = (
            Path(
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.AppDataLocation
                )
            )
            / "generated-app-backups"
        )
        try:
            report = apply_generated_app_patchset(patchset, backup_root)
        except (OSError, SyntaxError, ValueError, GeneratedAppError) as error:
            QMessageBox.critical(self, "Generated app export failed", str(error))
            return
        asset_storage = str(project.generated_app.get("asset_storage", "combined"))
        project.generated_app = {
            "destination": str(patchset.paths.root),
            "package_name": patchset.paths.package_name,
            "structure_version": 1,
            "asset_storage": asset_storage,
        }
        self.designer_session.mark_changed()
        lines = [
            f"Created: {len(report.created)}",
            f"Regenerated: {len(report.regenerated)}",
            f"Deleted stale resources: {len(report.deleted)}",
            f"Preserved: {len(report.preserved)}",
            "",
            *(str(patch.path) for patch in patchset.patches),
        ]
        if report.backup_directory is not None:
            lines.extend(("", f"Backup: {report.backup_directory}"))
        QMessageBox.information(
            self,
            "Generated app exported",
            "\n".join(lines),
        )

    def _export_gui_python(self) -> None:
        """Review and export the legacy single-file GUI renderer."""
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
        self._editing_project_asset_id = ""
        self._editing_project_asset_frame = 0
        self._editing_library_asset_id = ""
        self._editing_library_asset_revision = ""
        self._pending_image_path = None
        self._pending_image_uses_canvas = False
        self._draft_asset_name = ""
        self._portable_frames.clear()
        self._portable_durations.clear()
        self._portable_frame_index = 0
        self.catalogue_panel.setVisible(True)
        self.toggle_catalogue_action.setChecked(True)
        self.close_source_action.setEnabled(True)
        self.close_source_button.setEnabled(True)
        self.rescan_action.setEnabled(True)
        scope_kind = "Folder" if folder else "File"
        self.source_scope_label.setText(f"{scope_kind}: {path.name}")
        self.source_scope_label.setToolTip(
            f"Open source {scope_kind.lower()}: {path}\n"
            "Example: Click Close to leave this source without deleting any files."
        )
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
        self._remember_recent_path(path)
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
            managed = is_managed_graphic(asset)
            animated = any(
                name in asset.variants for name in ("frame", "phase", "animation_time")
            )
            badges = [
                "Managed" if managed else "Source",
                "Animated" if animated else "Static",
            ]
            item.setText(
                f"{asset.document.path.name}\n{asset.record.name}  [{' | '.join(badges)}]"
            )
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
            managed = is_managed_graphic(asset)
            animated = any(
                name in asset.variants for name in ("frame", "phase", "animation_time")
            )
            haystack = f"{asset.category} {asset.record.qualified_name} {asset.document.path}".lower()
            mode_visible = self.asset_filter_checks[
                "managed" if managed else "source-backed"
            ].isChecked()
            motion_visible = self.asset_filter_checks[
                "animated" if animated else "static"
            ].isChecked()
            hidden = bool(
                (needle and needle not in haystack)
                or not mode_visible
                or not motion_visible
            )
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
        self._editing_project_asset_id = ""
        self._editing_project_asset_frame = 0
        self._editing_library_asset_id = ""
        self._editing_library_asset_revision = ""
        self._draft_asset_name = ""
        self._pending_image_path = None
        self._pending_image_uses_canvas = False
        self._portable_frames.clear()
        self._portable_durations.clear()
        self._portable_frame_index = 0
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
            self.asset_mode_label.setText("MANAGED · full pixel editing")
            self.asset_mode_label.setStyleSheet("color: #2e7d32; font-weight: 600;")
        else:
            self.asset_mode_label.setText("SOURCE-BACKED · generated overlay edits")
            self.asset_mode_label.setStyleSheet("color: #ef6c00; font-weight: 600;")
        self.source_view_combo.setVisible(not managed)
        self.source_view_label.setVisible(not managed)
        self.source_view_combo.setCurrentIndex(0)
        self.canvas.setEnabled(True)
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
        install_widget_tooltips(self)

    def _animation_frame_label(self, parameter: str, value: Any) -> str:
        """Return a readable inferred frame label."""
        if parameter == "animation_time":
            return f"{value} ms"
        return f"Frame {value}"

    def _refresh_animation_labels(self) -> None:
        """Mark externally imported frames in the selector."""
        if self.animation_parameter is None:
            self.frame_timeline.clear()
            return
        current_value = self.frame_combo.currentData()
        self.frame_timeline.blockSignals(True)
        self.frame_timeline.clear()
        for index in range(self.frame_combo.count()):
            value = self.frame_combo.itemData(index)
            label = (
                f"Frame {index + 1}"
                if self._is_portable_pixel_asset()
                else self._animation_frame_label(self.animation_parameter, value)
            )
            if self._is_portable_pixel_asset() and index < len(
                self._portable_durations
            ):
                label += f" · {self._portable_durations[index]} ms"
            if value in self.animation_images:
                label += " - imported"
            self.frame_combo.setItemText(index, label)
            source_locked = (
                not self._is_portable_pixel_asset()
                and value not in self.animation_images
                and not self._current_asset_is_managed()
            )
            timeline_label = ("🔒 " if source_locked else "") + label
            if value in self.animation_drafts:
                timeline_label += " •"
            item = QListWidgetItem(timeline_label)
            item.setData(Qt.ItemDataRole.UserRole, value)
            art = self.animation_drafts.get(value)
            if self._is_portable_pixel_asset() and index < len(self._portable_frames):
                art = (
                    self.canvas.art()
                    if index == self._portable_frame_index
                    else self._portable_frames[index]
                )
            if art is None and self.current_asset is not None:
                values = dict(self.variant_values)
                values[self.animation_parameter] = value
                try:
                    art = self._animation_art(
                        self.tracer.render(self.current_asset, values), value
                    )
                except Exception:
                    art = None
            if art is not None:
                thumbnail = QPixmap.fromImage(pixel_art_image(art, True)).scaled(
                    48,
                    48,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation,
                )
                item.setIcon(QIcon(thumbnail))
            item.setToolTip(
                "Protected handwritten source frame" if source_locked else label
            )
            self.frame_timeline.addItem(item)
            if value == current_value:
                self.frame_timeline.setCurrentRow(index)
        self.frame_timeline.blockSignals(False)

    def _timeline_frame_changed(self, row: int) -> None:
        """Select the combo frame represented by a timeline thumbnail."""
        if row < 0:
            return
        item = self.frame_timeline.item(row)
        index = self.frame_combo.findData(item.data(Qt.ItemDataRole.UserRole))
        if index >= 0 and index != self.frame_combo.currentIndex():
            self.frame_combo.setCurrentIndex(index)

    def _timeline_reordered(self, *args: object) -> None:
        """Apply drag-to-reorder timeline order to managed animation output."""
        values = [
            self.frame_timeline.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.frame_timeline.count())
        ]
        current = self.frame_timeline.currentItem()
        current_value = (
            current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        )
        if self._is_portable_pixel_asset():
            self._capture_portable_frame()
            frames = [self._portable_frames[int(value)] for value in values]
            durations = (
                [self._portable_durations[int(value)] for value in values]
                if self._portable_durations
                else []
            )
            selected_index = (
                values.index(current_value) if current_value in values else 0
            )
            self._portable_frames = [frame.copy() for frame in frames]
            self._portable_durations = durations
            self._portable_frame_index = selected_index
            self.frame_combo.blockSignals(True)
            self.frame_combo.clear()
            for index in range(len(frames)):
                self.frame_combo.addItem(f"Frame {index + 1}", index)
            self.frame_combo.setCurrentIndex(selected_index)
            self.frame_combo.blockSignals(False)
            self._mark_animation_structure_changed()
            self._refresh_animation_labels()
            return
        labels = {
            self.frame_combo.itemData(index): self.frame_combo.itemText(index)
            for index in range(self.frame_combo.count())
        }
        self.frame_combo.blockSignals(True)
        self.frame_combo.clear()
        for value in values:
            self.frame_combo.addItem(labels.get(value, str(value)), value)
        self.frame_combo.setCurrentIndex(
            max(0, self.frame_combo.findData(current_value))
        )
        self.frame_combo.blockSignals(False)
        if self._current_asset_is_managed():
            self._mark_animation_structure_changed()

    def _mark_current_timeline_dirty(self) -> None:
        """Update the active frame thumbnail and non-color dirty marker."""
        item = self.frame_timeline.currentItem()
        if item is None:
            return
        if not item.text().endswith(" •"):
            item.setText(item.text() + " •")
        thumbnail = QPixmap.fromImage(pixel_art_image(self.canvas.art(), True)).scaled(
            48,
            48,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        item.setIcon(QIcon(thumbnail))

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
        if self._is_portable_pixel_asset():
            self._capture_portable_frame()
            index = self.frame_combo.currentIndex()
            self._portable_frame_index = index
            self.variant_values["frame"] = index
            self._suppress_changes = True
            self.canvas.set_art(self._portable_frames[index].copy())
            self._suppress_changes = False
            if index < len(self._portable_durations):
                self.frame_interval_spin.blockSignals(True)
                self.frame_interval_spin.setValue(self._portable_durations[index])
                self.frame_interval_spin.blockSignals(False)
            self.frame_timeline.blockSignals(True)
            self.frame_timeline.setCurrentRow(index)
            self.frame_timeline.blockSignals(False)
            self._update_palette(self.canvas.art())
            self._update_preview()
            self._update_onion_skin()
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
        self.frame_timeline.blockSignals(True)
        self.frame_timeline.setCurrentRow(self.frame_combo.currentIndex())
        self.frame_timeline.blockSignals(False)
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
        if self._is_portable_pixel_asset():
            self._capture_portable_frame()
            art = self.canvas.art()
            self._portable_frames.append(
                PixelArt(art.width, art.height, art.origin_x, art.origin_y)
            )
            if self._portable_durations:
                self._portable_durations.append(self.frame_interval_spin.value())
            elif len(self._portable_frames) == 2:
                self._portable_durations = [
                    self.frame_interval_spin.value(),
                    self.frame_interval_spin.value(),
                ]
            self.animation_parameter = "frame"
            self.animation_group.setVisible(True)
            index = len(self._portable_frames) - 1
            self.frame_combo.addItem(f"Frame {index + 1}", index)
            self.frame_combo.setCurrentIndex(index)
            self._mark_animation_structure_changed()
            self._refresh_animation_labels()
            return
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
        if self._is_portable_pixel_asset():
            self._capture_portable_frame()
            self._portable_frames.append(self.canvas.art().copy())
            if self._portable_durations:
                self._portable_durations.append(self.frame_interval_spin.value())
            elif len(self._portable_frames) == 2:
                self._portable_durations = [
                    self.frame_interval_spin.value(),
                    self.frame_interval_spin.value(),
                ]
            self.animation_parameter = "frame"
            self.animation_group.setVisible(True)
            index = len(self._portable_frames) - 1
            self.frame_combo.addItem(f"Frame {index + 1}", index)
            self.frame_combo.setCurrentIndex(index)
            self._mark_animation_structure_changed()
            self._refresh_animation_labels()
            return
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
        if self._is_portable_pixel_asset():
            self._capture_portable_frame()
            index = self.frame_combo.currentIndex()
            self._portable_frames.pop(index)
            if self._portable_durations:
                self._portable_durations.pop(index)
            self._portable_frame_index = min(index, len(self._portable_frames) - 1)
            self.frame_combo.blockSignals(True)
            self.frame_combo.clear()
            for frame_index in range(len(self._portable_frames)):
                self.frame_combo.addItem(f"Frame {frame_index + 1}", frame_index)
            self.frame_combo.setCurrentIndex(self._portable_frame_index)
            self.frame_combo.blockSignals(False)
            self._suppress_changes = True
            self.canvas.set_art(
                self._portable_frames[self._portable_frame_index].copy()
            )
            self._suppress_changes = False
            self.animation_parameter = (
                "frame" if len(self._portable_frames) > 1 else None
            )
            self.animation_group.setVisible(len(self._portable_frames) > 1)
            self._mark_animation_structure_changed()
            self._refresh_animation_labels()
            self._update_preview()
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
        self._refresh_animation_labels()
        if self._current_asset_is_managed():
            self._mark_animation_structure_changed()

    def _move_animation_frame(self, direction: int) -> None:
        """Move the current frame within preview playback order."""
        index = self.frame_combo.currentIndex()
        target = index + direction
        if index < 0 or target < 0 or target >= self.frame_combo.count():
            return
        if self._is_portable_pixel_asset():
            self._capture_portable_frame()
            frame = self._portable_frames.pop(index)
            self._portable_frames.insert(target, frame)
            if self._portable_durations:
                duration = self._portable_durations.pop(index)
                self._portable_durations.insert(target, duration)
            self._portable_frame_index = target
            self.frame_combo.blockSignals(True)
            self.frame_combo.clear()
            for frame_index in range(len(self._portable_frames)):
                self.frame_combo.addItem(f"Frame {frame_index + 1}", frame_index)
            self.frame_combo.setCurrentIndex(target)
            self.frame_combo.blockSignals(False)
            self._mark_animation_structure_changed()
            self._refresh_animation_labels()
            return
        text = self.frame_combo.itemText(index)
        value = self.frame_combo.itemData(index)
        self.frame_combo.blockSignals(True)
        self.frame_combo.removeItem(index)
        self.frame_combo.insertItem(target, text, value)
        self.frame_combo.setCurrentIndex(target)
        self.frame_combo.blockSignals(False)
        self._refresh_animation_labels()
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
        if playing and self._dirty and not self._is_portable_pixel_asset():
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
        if self._dirty and not self._is_portable_pixel_asset():
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
            or self.animation_parameter is None
            or self.frame_combo.count() < 2
        ):
            self.canvas.set_onion_art(None)
            return
        current_index = self.frame_combo.currentIndex()
        if current_index <= 0:
            self.canvas.set_onion_art(None)
            return
        if self._is_portable_pixel_asset():
            self._capture_portable_frame()
            self.canvas.set_onion_art(self._portable_frames[current_index - 1])
            return
        if self.current_asset is None:
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

    def _frame_interval_changed(self, value: int) -> None:
        """Update playback and the selected portable-frame duration."""
        self.animation_timer.setInterval(value)
        if not self._is_portable_pixel_asset() or not self._portable_durations:
            return
        index = max(
            0, min(self._portable_frame_index, len(self._portable_durations) - 1)
        )
        if self._portable_durations[index] == value:
            return
        self._portable_durations[index] = value
        self._dirty = True
        self._update_apply_state()
        self._refresh_animation_labels()
        self._schedule_pixel_recovery()

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
        self._composite_view_art = displayed_art.copy()
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

    def _source_view_changed(self) -> None:
        """Switch between original, composite, and generated edit layers."""
        if self.current_trace is None or self._current_asset_is_managed():
            return
        mode = str(self.source_view_combo.currentData())
        if mode == "composite":
            art = self._composite_view_art or self.current_trace.current_art
            enabled = True
        elif mode == "original":
            if self.canvas.isEnabled():
                self._composite_view_art = self.canvas.art().copy()
            art = self.current_trace.current_art
            enabled = False
        else:
            if self.canvas.isEnabled():
                self._composite_view_art = self.canvas.art().copy()
            composite = self._composite_view_art or self.current_trace.current_art
            art = PixelArt(
                composite.width,
                composite.height,
                composite.origin_x,
                composite.origin_y,
            )
            for x, y, color in composite.changed_pixels(self.current_trace.current_art):
                art.set_pixel(x, y, color)
            enabled = False
        self._suppress_changes = True
        self.canvas.set_art(art)
        self._suppress_changes = False
        self.canvas.setEnabled(enabled)
        self.statusBar().showMessage(f"Source-backed view: {mode.title()}")

    def _canvas_changed(self) -> None:
        """Update dirty state and previews after painting."""
        self._update_history_actions()
        if self._suppress_changes:
            return
        if self.current_trace is None:
            if self._is_portable_pixel_asset():
                self._dirty = True
                self._update_preview()
                self._update_apply_state()
                self._schedule_pixel_recovery()
            return
        if str(self.source_view_combo.currentData()) != "composite":
            return
        self._composite_view_art = self.canvas.art().copy()
        try:
            self._dirty = self._animation_structure_dirty or bool(
                self.canvas.art().changed_pixels(self.current_trace.current_art)
            )
        except ValueError:
            self._dirty = True
        if self.animation_parameter is not None:
            frame_value = self.variant_values.get(self.animation_parameter)
            self.animation_drafts[frame_value] = self.canvas.art().copy()
            self._mark_current_timeline_dirty()
        self._update_preview()
        self._update_apply_state()
        self._schedule_pixel_recovery()

    def _update_apply_state(self) -> None:
        """Enable source applying when an edit exists."""
        enabled = self._dirty and (
            (self.current_asset is not None and self.current_trace is not None)
            or self._is_portable_pixel_asset()
            or bool(self._editing_project_asset_id)
            or bool(self._editing_library_asset_id)
        )
        self.apply_button.setEnabled(enabled)
        self.apply_action.setEnabled(enabled)
        apply_text = (
            "Update Project Asset"
            if self._editing_project_asset_id
            else "Update Library Asset"
            if self._editing_library_asset_id
            else "Save Python Asset"
            if self.current_asset is not None
            else "Generate Python…"
        )
        self.apply_button.setText(apply_text)
        set_widget_tooltip(
            self.apply_button,
            "apply_button",
            self,
            (
                "Update the lossless asset in the GUI project; save the project afterward."
                if self._editing_project_asset_id
                else "Atomically update every frame in the Personal Asset Library."
                if self._editing_library_asset_id
                else "Review the exact Python diff before writing."
            ),
        )
        self.apply_action.setText(apply_text)
        title = "Pico Graphics and GUI Designer"
        if self.current_asset is not None:
            title += f" - {self.current_asset.record.name}"
        elif self._editing_project_asset_id:
            project_asset = self.designer_session.project.asset(
                self._editing_project_asset_id
            )
            if project_asset is not None:
                title += f" - {project_asset.name}"
        self.setWindowTitle(title + (" *" if self._dirty else ""))
        self._update_document_strip()

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
        return (
            bool(self._editing_project_asset_id)
            or bool(self._editing_library_asset_id)
            or self._is_portable_pixel_asset()
            or self._pending_image_path is not None
            or (
                self.current_asset is not None
                and is_managed_graphic(self.current_asset)
            )
        )

    def _editable_pixel_art(self) -> PixelArt:
        """Return editable composite pixels even while a comparison view is shown."""
        if (
            not self._current_asset_is_managed()
            and str(self.source_view_combo.currentData()) != "composite"
            and self._composite_view_art is not None
        ):
            return self._composite_view_art
        return self.canvas.art()

    def _library_history_snapshot(
        self,
    ) -> LibraryHistorySnapshot | None:
        """Capture the complete validated library before one mutation."""
        try:
            assets = self.asset_library.assets()
            revision = self.asset_library.revision(assets)
            self._library_known_revision = revision
            return LibraryHistorySnapshot(
                assets,
                self.library_workspace.selected_asset_id(),
                revision,
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.library_workspace.set_storage_error(
                str(error), self.standard_library_assets
            )
            self.statusBar().showMessage(f"Asset library unavailable: {error}")
            return None

    def _record_library_change(
        self,
        description: str,
        before: LibraryHistorySnapshot | None,
        selected_after: str = "",
    ) -> None:
        """Push one successful automatic library write onto bounded history."""
        if before is None:
            return
        try:
            after = self.asset_library.assets()
        except (OSError, ValueError, json.JSONDecodeError):
            return
        after_revision = self.asset_library.revision(after)
        entry = LibraryHistoryEntry(
            description,
            before.assets,
            after,
            before.selected_asset_id,
            selected_after,
            before.revision,
            after_revision,
        )
        self._library_undo_stack.append(entry)
        self._library_undo_stack = self._library_undo_stack[-20:]
        self._library_redo_stack.clear()
        self._library_known_revision = after_revision
        self._sync_library_history_state()
        self._update_history_actions()

    def _sync_library_history_state(self) -> None:
        """Synchronize menu and workspace undo affordances."""
        if not hasattr(self, "library_workspace"):
            return
        available = self.library_workspace.storage_available()
        description = (
            self._library_undo_stack[-1].description
            if self._library_undo_stack
            else self._library_redo_stack[-1].description
            if self._library_redo_stack
            else ""
        )
        self.library_workspace.set_history_state(
            bool(self._library_undo_stack) and available,
            bool(self._library_redo_stack) and available,
            description,
        )
        if hasattr(self, "library_undo_action"):
            self.library_undo_action.setEnabled(
                bool(self._library_undo_stack) and available
            )
        if hasattr(self, "library_redo_action"):
            self.library_redo_action.setEnabled(
                bool(self._library_redo_stack) and available
            )

    def _clear_library_history(self, reason: str = "") -> None:
        """Discard snapshots that no longer match the persistent library."""
        had_history = bool(self._library_undo_stack or self._library_redo_stack)
        self._library_undo_stack.clear()
        self._library_redo_stack.clear()
        self._sync_library_history_state()
        self._update_history_actions()
        if had_history and reason:
            self.statusBar().showMessage(reason)

    def _restore_library_history(
        self, entry: LibraryHistoryEntry, *, redo: bool
    ) -> bool | None:
        """Atomically restore one before/after library snapshot."""
        snapshot = entry.after if redo else entry.before
        selected = entry.selected_after if redo else entry.selected_before
        try:
            current_revision = self.asset_library.revision()
            expected_revision = entry.before_revision if redo else entry.after_revision
            if current_revision != expected_revision:
                self._clear_library_history(
                    "Library history was cleared because the file changed outside "
                    "this editor session."
                )
                QMessageBox.warning(
                    self,
                    "Library changed outside this session",
                    "Undo and redo were cancelled so newer library changes are not "
                    "overwritten. Refresh the library and continue from its current "
                    "contents.",
                )
                return None
            self.asset_library.restore_snapshot(snapshot)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot restore asset library", str(error))
            return False
        self._library_known_revision = self.asset_library.revision(snapshot)
        self._refresh_personal_asset_library(selected, detect_external_change=False)
        verb = "Redid" if redo else "Undid"
        self.statusBar().showMessage(f"{verb} library change: {entry.description}")
        return True

    def _undo_library_change(self) -> None:
        """Undo the last complete Personal Asset Library mutation."""
        if not self._library_undo_stack:
            return
        entry = self._library_undo_stack.pop()
        restored = self._restore_library_history(entry, redo=False)
        if restored is None:
            return
        if not restored:
            self._library_undo_stack.append(entry)
            self._sync_library_history_state()
            return
        self._library_redo_stack.append(entry)
        self._update_history_actions()

    def _redo_library_change(self) -> None:
        """Redo the last undone Personal Asset Library mutation."""
        if not self._library_redo_stack:
            return
        entry = self._library_redo_stack.pop()
        restored = self._restore_library_history(entry, redo=True)
        if restored is None:
            return
        if not restored:
            self._library_redo_stack.append(entry)
            self._sync_library_history_state()
            return
        self._library_undo_stack.append(entry)
        self._update_history_actions()

    def _refresh_personal_asset_library(
        self,
        select_asset_id: str = "",
        *,
        detect_external_change: bool = True,
    ) -> None:
        """Publish lightweight records to both library browser surfaces."""
        try:
            records = self.asset_library.assets()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.library_workspace.set_storage_error(
                str(error), self.standard_library_assets
            )
            self._library_known_revision = None
            self._clear_library_history()
            self.screen_designer.set_library_records(self.standard_library_assets)
            if hasattr(self, "statusBar"):
                self.statusBar().showMessage(f"Asset library unavailable: {error}")
            self._update_workspace_menu_actions()
            return
        revision = self.asset_library.revision(records)
        if (
            detect_external_change
            and self._library_known_revision is not None
            and revision != self._library_known_revision
        ):
            self._clear_library_history(
                "Library history was cleared because refreshed contents changed "
                "outside this editor session."
            )
        self._library_known_revision = revision
        self._reconcile_open_library_master(records)
        display_standards = self._display_standard_library(records)
        combined_records = (*display_standards, *records)
        self.screen_designer.set_library_records(combined_records, select_asset_id)
        self.library_workspace.set_library_path(self.asset_library.path)
        self.library_workspace.set_assets(records, display_standards)
        if select_asset_id:
            self.library_workspace.select_asset(select_asset_id)
        self._sync_library_history_state()
        self._update_workspace_menu_actions()

    def _display_standard_library(
        self, personal_records: tuple[LibraryAsset, ...]
    ) -> tuple[LibraryAsset, ...]:
        """Disambiguate legacy personal names without silently rewriting storage."""
        occupied = {
            self.asset_library.name_key(record.name) for record in personal_records
        }
        displayed: list[LibraryAsset] = []
        for record in self.standard_library_assets:
            name = record.name
            if self.asset_library.name_key(name) in occupied:
                base = f"{name} · Built-in"
                name = base
                suffix = 2
                while self.asset_library.name_key(name) in occupied:
                    name = f"{base} {suffix}"
                    suffix += 1
            occupied.add(self.asset_library.name_key(name))
            displayed.append(
                record if name == record.name else dataclass_replace(record, name=name)
            )
        return tuple(displayed)

    @staticmethod
    def _library_asset_ids(value: object) -> tuple[str, ...]:
        """Normalize legacy single IDs and new ordered multi-selection payloads."""
        if isinstance(value, str):
            return (value,) if value else ()
        if isinstance(value, (tuple, list)):
            return tuple(str(item) for item in value if str(item))
        return ()

    def _library_asset_record(self, asset_id: str) -> LibraryAsset | None:
        """Resolve a built-in or personal library identity."""
        standard = self._standard_library_by_id.get(asset_id)
        if standard is not None:
            return standard
        return self.asset_library.asset(asset_id)

    def _add_library_asset_to_current_project(self, value: object) -> None:
        """Add selected library records as one arranged App GUI batch."""
        asset_ids = self._library_asset_ids(value)
        try:
            records = tuple(
                record
                for asset_id in asset_ids
                if (record := self._library_asset_record(asset_id)) is not None
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot read asset library", str(error))
            return
        assets: list[GuiPixelAsset] = []
        for record in records:
            frames = record.pixel_frames()
            source = (
                "Built-in Standard Library"
                if is_standard_asset_id(record.id)
                else "Personal Asset Library"
            )
            assets.append(
                GuiPixelAsset(
                    f"library::{record.id}",
                    record.name,
                    source,
                    record.name,
                    frames[0],
                    record.fingerprint,
                    frames,
                    record.durations,
                )
            )
        if not assets:
            return
        self.screen_designer.place_pixel_assets(tuple(assets), "detached")
        self._activate_workspace(WorkspaceId.APP_GUI)
        self.statusBar().showMessage(
            f"Added {len(assets)} independent library asset"
            f"{'s' if len(assets) != 1 else ''} to the current screen."
        )

    def _edit_library_asset_copy(self, asset_id: str) -> None:
        """Open the complete library asset for lossless Pixel Art editing."""
        if not self._confirm_discard():
            return
        self._editing_project_asset_id = ""
        self._editing_project_asset_frame = 0
        try:
            record = self._library_asset_record(asset_id)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot read asset library", str(error))
            return
        if record is None:
            return
        frame_index = min(
            self.library_workspace.selected_frame_index(), len(record.frames) - 1
        )
        if is_standard_asset_id(record.id):
            self._editing_library_asset_id = ""
            self._editing_library_asset_revision = ""
            self._open_portable_pixel_asset(
                record.name,
                record.pixel_frames(),
                record.durations or None,
                frame_index=frame_index,
                dirty=False,
                source_label="Built-in Standard Library · read-only original",
                mode_label="BUILT-IN COPY · edit freely, then save to Personal Library",
            )
            self.warning_text.setPlainText(
                "Built-in icons never change. Your open pixels are an independent "
                "editable copy; choose Save to Library to keep it."
            )
            return
        self._editing_library_asset_id = record.id
        self._editing_library_asset_revision = record.fingerprint
        self._open_portable_pixel_asset(
            record.name,
            record.pixel_frames(),
            record.durations or None,
            frame_index=frame_index,
            dirty=False,
            source_label=f"Personal Asset Library · stable ID {record.id}",
            mode_label="LIBRARY ASSET · edits update the complete stored asset",
        )
        self.warning_text.setPlainText(
            "All frames remain together. Choose Update Library Asset to replace the "
            "stored pixels atomically, or Save Copy to Library for a new identity."
        )

    def _detach_open_library_master(self, reason: str) -> None:
        """Keep current pixels as a safe draft after their library master diverges."""
        if not self._editing_library_asset_id:
            return
        self._capture_portable_frame()
        self._editing_library_asset_id = ""
        self._editing_library_asset_revision = ""
        self._pending_image_path = None
        self.source_label.setText("Detached library copy · not yet stored")
        self.asset_mode_label.setText(
            "UNLINKED LIBRARY COPY · save it as a new library asset"
        )
        self.warning_text.setPlainText(
            f"{reason} Your open pixels were kept unchanged as an independent copy. "
            "Choose Save to Library to give this copy a new stable identity."
        )
        self._update_apply_state()
        self._update_document_strip()
        self.statusBar().showMessage(reason)

    def _reconcile_open_library_master(self, records: tuple[LibraryAsset, ...]) -> None:
        """Prevent an open Pixel Art master from overwriting a changed record."""
        if not self._editing_library_asset_id:
            return
        record = next(
            (item for item in records if item.id == self._editing_library_asset_id),
            None,
        )
        if record is None:
            self._detach_open_library_master(
                "The library master was deleted while it was open in Pixel Art."
            )
            return
        if (
            self._editing_library_asset_revision
            and record.fingerprint != self._editing_library_asset_revision
        ):
            self._detach_open_library_master(
                "The library master changed while it was open in Pixel Art."
            )
            return
        self._draft_asset_name = record.name

    def _apply_library_asset_edit(self) -> bool:
        """Atomically replace the complete library asset from Pixel Art frames."""
        if not self._editing_library_asset_id:
            return False
        try:
            current = self.asset_library.asset(self._editing_library_asset_id)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot read asset library", str(error))
            return False
        if current is None or (
            self._editing_library_asset_revision
            and current.fingerprint != self._editing_library_asset_revision
        ):
            reason = (
                "The library master was deleted while it was open in Pixel Art."
                if current is None
                else "The library master changed while it was open in Pixel Art."
            )
            self._detach_open_library_master(reason)
            QMessageBox.warning(
                self,
                "Library master changed",
                f"{reason}\n\nYour current pixels were kept as an independent copy. "
                "Use Save to Library instead of overwriting the newer stored asset.",
            )
            return False
        self._capture_portable_frame()
        before = self._library_history_snapshot()
        try:
            updated = self.asset_library.replace(
                self._editing_library_asset_id,
                self._portable_frames,
                self._portable_durations or None,
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot update library asset", str(error))
            return False
        self._record_library_change(f"Updated {updated.name}", before, updated.id)
        self._editing_library_asset_revision = updated.fingerprint
        self._refresh_personal_asset_library(updated.id)
        self._dirty = False
        self._animation_structure_dirty = False
        self._clear_pixel_recovery()
        self._update_apply_state()
        self.statusBar().showMessage(
            f"Updated {updated.name} in the Personal Asset Library."
        )
        return True

    def _review_library_images(
        self,
        images: list[QImage],
        name: str,
        *,
        replace: bool = False,
        original_durations: tuple[int, ...] = (),
    ) -> LibraryImageImportResult | None:
        """Review exact local RGB565 conversion settings before a library write."""
        dialog = LibraryImageImportDialog(
            images,
            name,
            self,
            replace=replace,
            color_count=int(self.settings.value("library/import-colors", 16)),
            dither=str(self.settings.value("library/import-dither", "false")).lower()
            in {"1", "true", "yes"},
            interval_ms=int(self.settings.value("library/import-interval-ms", 250)),
            original_durations=original_durations,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        result = dialog.result_value()
        self.settings.setValue("library/import-colors", result.color_count)
        self.settings.setValue("library/import-dither", result.dither)
        self.settings.setValue("library/import-interval-ms", result.interval_ms)
        return result

    def _replace_library_asset_from_image(self, asset_id: str) -> None:
        """Replace one stable library record from a reviewed image selection."""
        try:
            record = self.asset_library.asset(asset_id)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot read asset library", str(error))
            return
        if record is None:
            return
        filename, _ = get_open_image_filename(
            self,
            f"Replace {record.name} from image",
            str(Path.cwd()),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)",
            accept_label="Use replacement",
        )
        if not filename:
            return
        images, original_durations = read_image_frames_with_durations(filename)
        if not images:
            QMessageBox.warning(
                self, "Cannot replace asset", "The selected image could not be decoded."
            )
            return
        review = self._review_library_images(
            images,
            record.name,
            replace=True,
            original_durations=original_durations,
        )
        if review is None:
            return
        answer = QMessageBox.question(
            self,
            "Replace library asset?",
            f"Replace the stored pixels for {record.name}?\n"
            f"Reviewed result: {review.width} × {review.height}, "
            f"{len(review.frames)} frame(s).\n"
            "Its stable ID is preserved. Existing project copies remain unchanged. "
            "This change can be undone during the current editor session.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        before = self._library_history_snapshot()
        try:
            updated = self.asset_library.replace(
                asset_id,
                review.frames,
                review.durations or None,
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot replace library asset", str(error))
            return
        self._record_library_change(
            f"Replaced {record.name} from image", before, updated.id
        )
        self._refresh_personal_asset_library(updated.id)
        self.statusBar().showMessage(f"Replaced {record.name}; its stable ID was kept.")

    def _duplicate_library_asset(self, value: object) -> None:
        """Copy one or several selected records into personal storage atomically."""
        asset_ids = self._library_asset_ids(value)
        try:
            records = tuple(
                record
                for asset_id in asset_ids
                if (record := self._library_asset_record(asset_id)) is not None
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot read asset library", str(error))
            return
        if not records:
            return
        if len(records) == 1:
            record = records[0]
            requested_name = (
                record.name
                if is_standard_asset_id(record.id)
                else f"{record.name} Copy"
            )
            try:
                default_name = self.asset_library.available_name(requested_name)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                QMessageBox.critical(self, "Cannot read asset library", str(error))
                return
            name, accepted = QInputDialog.getText(
                self,
                "Copy to Personal Asset Library",
                "Personal copy name",
                text=default_name,
            )
            if not accepted or not name.strip():
                return
            entries = ((name, record.pixel_frames(), record.durations or None),)
        else:
            answer = QMessageBox.question(
                self,
                "Copy selected assets?",
                f"Copy {len(records)} selected assets into the Personal Asset "
                "Library? Built-in originals remain unchanged, and personal records "
                "receive independent IDs.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            entries = tuple(
                (
                    record.name
                    if is_standard_asset_id(record.id)
                    else f"{record.name} Copy",
                    record.pixel_frames(),
                    record.durations or None,
                )
                for record in records
            )
        before = self._library_history_snapshot()
        try:
            copies = self.asset_library.add_many(entries)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot copy library assets", str(error))
            return
        self._record_library_change(
            f"Copied {len(copies)} asset{'s' if len(copies) != 1 else ''} to Personal Library",
            before,
            copies[0].id if copies else "",
        )
        self._refresh_personal_asset_library(copies[0].id if copies else "")
        self.statusBar().showMessage(
            f"Created {len(copies)} independent personal library "
            f"cop{'ies' if len(copies) != 1 else 'y'}."
        )

    def _export_library_asset_frame(self, value: object) -> None:
        """Export one previewed frame or all frames from a multi-selection."""
        asset_ids = self._library_asset_ids(value)
        try:
            records = tuple(
                record
                for asset_id in asset_ids
                if (record := self._library_asset_record(asset_id)) is not None
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot read asset library", str(error))
            return
        if not records:
            return
        if len(records) > 1:
            directory = QFileDialog.getExistingDirectory(
                self,
                f"Export {len(records)} selected library assets",
                str(Path.cwd()),
            )
            if not directory:
                return
            destination = Path(directory)
            try:
                exports = plan_png_exports(records, destination)
                write_png_exports(exports)
            except OSError as error:
                QMessageBox.critical(
                    self,
                    "Export failed",
                    f"No batch files were kept.\n\n{error}",
                )
                return
            self.statusBar().showMessage(
                f"Exported {len(exports)} PNG file"
                f"{'s' if len(exports) != 1 else ''} to "
                f"{destination}."
            )
            return
        record = records[0]
        index = min(
            self.library_workspace.selected_frame_index(), len(record.frames) - 1
        )
        suffix = f"-frame-{index + 1}" if len(record.frames) > 1 else ""
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export library asset frame",
            str(Path.cwd() / f"{record.name}{suffix}.png"),
            "PNG images (*.png)",
        )
        if not filename:
            return
        if not filename.lower().endswith(".png"):
            filename += ".png"
        frame = record.pixel_frames()[index]
        if not pixel_art_image(frame).save(filename, "PNG"):
            QMessageBox.critical(self, "Export failed", "The PNG could not be written.")
            return
        self.statusBar().showMessage(f"Exported {filename}")

    def _import_image_to_asset_library(self) -> None:
        """Import a static or animated image directly into personal storage."""
        filename, _ = get_open_image_filename(
            self,
            "Import image into personal asset library",
            str(Path.cwd()),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)",
            accept_label="Import",
        )
        if not filename:
            return
        images, original_durations = read_image_frames_with_durations(filename)
        if not images:
            QMessageBox.warning(
                self, "Cannot import image", "The selected image could not be decoded."
            )
            return
        try:
            suggested_name = self.asset_library.available_name(Path(filename).stem)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot read asset library", str(error))
            return
        review = self._review_library_images(
            images,
            suggested_name,
            original_durations=original_durations,
        )
        if review is None:
            return
        before = self._library_history_snapshot()
        try:
            stored = self.asset_library.add(
                review.name,
                review.frames,
                review.durations or None,
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot import library asset", str(error))
            return
        self._record_library_change(
            f"Imported {stored.name} from image", before, stored.id
        )
        self._refresh_personal_asset_library(stored.id)
        self.statusBar().showMessage(
            f"Imported {stored.name} into the personal asset library."
        )

    def _import_pga_to_asset_library(self) -> None:
        """Recover every PGA2/PGA3 image as an independent library copy."""
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Import images from PGA resource",
            str(Path.cwd()),
            "Picoware generated resources (*.pga);;All files (*)",
        )
        if not filename:
            return
        try:
            resource = decode_asset_resource(Path(filename).read_bytes())
            existing = self.asset_library.assets()
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot import PGA images", str(error))
            return
        if not resource.assets:
            QMessageBox.information(
                self,
                "No images in PGA resource",
                (
                    "The resource is valid, but it contains no images. "
                    f"It contains {len(resource.audio_assets)} WAV file(s), which "
                    "stay inside the PGA3 resource and are not pixel-library assets."
                ),
            )
            return

        existing_fingerprints = {
            record.fingerprint for record in existing if record.fingerprint
        }
        duplicate_count = sum(
            asset_fingerprint(encode_asset(entry.frames, entry.durations or None))
            in existing_fingerprints
            for entry in resource.assets
        )
        frame_count = sum(len(entry.frames) for entry in resource.assets)
        duplicate_note = (
            f"\n{duplicate_count} asset(s) match pixels already in the library; "
            "they will still be imported as independent copies."
            if duplicate_count
            else ""
        )
        answer = QMessageBox.question(
            self,
            f"Import PGA{resource.format_version} images?",
            f"Project: {resource.project_id}\n"
            f"Resource: {Path(filename).name}\n"
            f"Images: {len(resource.assets)} · Frames: {frame_count} · "
            f"WAV files left in resource: {len(resource.audio_assets)}"
            f"{duplicate_note}\n\n"
            "Pixels, transparency, origins, and animation durations will be "
            "preserved. Occupied names receive the next number. New library IDs "
            "will be assigned, and the PGA file will not be modified.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        before = self._library_history_snapshot()
        try:
            additions = self.asset_library.add_many(
                tuple(
                    (
                        entry.name or entry.asset_id,
                        entry.frames,
                        entry.durations or None,
                    )
                    for entry in resource.assets
                ),
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot import PGA images", str(error))
            return
        selected = additions[0].id if additions else ""
        self._record_library_change(
            f"Imported {len(additions)} PGA images", before, selected
        )
        self._refresh_personal_asset_library(selected)
        self._activate_workspace(WorkspaceId.ASSET_LIBRARY)
        self.statusBar().showMessage(
            f"Imported {len(additions)} PGA{resource.format_version} images "
            f"({frame_count} frames) from "
            f"{Path(filename).name}."
        )
        QMessageBox.information(
            self,
            "PGA images imported",
            f"Added {len(additions)} independent images with {frame_count} total "
            "frames to the Personal Asset Library. The source resource was not "
            "changed.",
        )

    def _library_frames_from_images(self, images: list[QImage]) -> list[PixelArt]:
        """Convert imported images to one shared, bounded RGB565 canvas."""
        scale = min(1.0, 320 / images[0].width(), 320 / images[0].height())
        width = max(1, round(images[0].width() * scale))
        height = max(1, round(images[0].height() * scale))
        return [
            image_to_pixel_art(
                prepare_reference_image(image, width, height, "contain"),
                width,
                height,
                self.reference_colors_spin.value(),
                self.reference_dither_check.isChecked(),
            )
            for image in images
        ]

    def _current_library_frames(self) -> tuple[list[PixelArt], list[int]]:
        """Return the current static asset or complete visible animation."""
        if self._is_portable_pixel_asset():
            self._capture_portable_frame()
            return (
                [frame.copy() for frame in self._portable_frames],
                list(self._portable_durations),
            )
        if self._editing_project_asset_id:
            asset = self.designer_session.project.asset(self._editing_project_asset_id)
            if asset is not None:
                frames = asset.pixel_frames()
                frame_index = max(
                    0,
                    min(self._editing_project_asset_frame, len(frames) - 1),
                )
                frames[frame_index] = self._editable_pixel_art().copy()
                return frames, list(asset.durations)
        if self.current_asset is None or self.animation_parameter is None:
            return [self._editable_pixel_art().copy()], []
        frames: list[PixelArt] = []
        current_value = self.variant_values.get(self.animation_parameter)
        for index in range(self.frame_combo.count()):
            value = self.frame_combo.itemData(index)
            if value == current_value:
                frames.append(self._editable_pixel_art().copy())
                continue
            draft = self.animation_drafts.get(value)
            if draft is not None:
                frames.append(draft.copy())
                continue
            values = dict(self.variant_values)
            values[self.animation_parameter] = value
            trace = self.tracer.render(self.current_asset, values)
            frames.append(self._animation_art(trace, value).copy())
        left = min(frame.origin_x for frame in frames)
        top = min(frame.origin_y for frame in frames)
        right = max(frame.origin_x + frame.width for frame in frames)
        bottom = max(frame.origin_y + frame.height for frame in frames)
        canvas = PixelArt(right - left, bottom - top, left, top)
        frames = [self._library_frame_on_canvas(frame, canvas) for frame in frames]
        durations = [self.frame_interval_spin.value()] * len(frames)
        return frames, durations

    @staticmethod
    def _library_frame_on_canvas(frame: PixelArt, canvas: PixelArt) -> PixelArt:
        """Align one traced frame to the reusable animation canvas and origin."""
        if (
            frame.width == canvas.width
            and frame.height == canvas.height
            and frame.origin_x == canvas.origin_x
            and frame.origin_y == canvas.origin_y
        ):
            return frame.copy()
        aligned = PixelArt(
            canvas.width,
            canvas.height,
            canvas.origin_x,
            canvas.origin_y,
        )
        for y in range(frame.height):
            for x in range(frame.width):
                color = frame.pixel(x, y)
                if color is None:
                    continue
                target_x = frame.origin_x + x - canvas.origin_x
                target_y = frame.origin_y + y - canvas.origin_y
                aligned.set_pixel(target_x, target_y, color)
        return aligned

    def _save_current_asset_to_library(self) -> bool:
        """Store the active pixel document as a reusable personal asset."""
        project_asset = self.designer_session.project.asset(
            self._editing_project_asset_id
        )
        if (
            self.current_asset is None
            and not self._is_portable_pixel_asset()
            and project_asset is None
        ):
            return False
        default_name = (
            self.current_asset.record.name
            if self.current_asset is not None
            else project_asset.name
            if project_asset is not None
            else self._active_pixel_name()
        )
        try:
            default_name = self.asset_library.available_name(default_name)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot read asset library", str(error))
            return False
        name, accepted = QInputDialog.getText(
            self,
            "Save to personal asset library",
            "Library asset name",
            text=default_name,
        )
        if not accepted or not name.strip():
            return False
        before = self._library_history_snapshot()
        try:
            frames, durations = self._current_library_frames()
            stored = self.asset_library.add(
                name,
                frames,
                durations or None,
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot save library asset", str(error))
            return False
        self._record_library_change(
            f"Saved {stored.name} to library", before, stored.id
        )
        self._refresh_personal_asset_library(stored.id)
        is_unsaved_draft = (
            self.current_asset is None
            and not self._editing_project_asset_id
            and not self._editing_library_asset_id
            and self._is_portable_pixel_asset()
        )
        if is_unsaved_draft:
            self._editing_library_asset_id = stored.id
            self._editing_library_asset_revision = stored.fingerprint
            self._pending_image_path = None
            self._draft_asset_name = stored.name
            self._dirty = False
            self.source_label.setText(f"Personal Asset Library · stable ID {stored.id}")
            self.asset_mode_label.setText(
                "LIBRARY ASSET · edits update the complete stored asset"
            )
            self.warning_text.setPlainText(
                "This asset is now stored in the Personal Asset Library. Edit any "
                "frame and choose Update Library Asset, or generate Python separately."
            )
            self._clear_pixel_recovery()
            self._update_apply_state()
        self.statusBar().showMessage(
            f"Saved {stored.name} to the personal asset library."
        )
        return True

    def _save_project_element_to_library(self, element_id: str) -> None:
        """Store one selected project asset as an independent library copy."""
        element = next(
            (
                item
                for screen in self.designer_session.project.screens
                for item in screen.elements
                if item.id == element_id
            ),
            None,
        )
        asset = (
            self.designer_session.project.asset(element.asset_id)
            if element is not None
            else None
        )
        if asset is None:
            return
        try:
            default_name = self.asset_library.available_name(asset.name)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot read asset library", str(error))
            return
        name, accepted = QInputDialog.getText(
            self,
            "Save to personal asset library",
            "Library asset name",
            text=default_name,
        )
        if not accepted or not name.strip():
            return
        before = self._library_history_snapshot()
        try:
            stored = self.asset_library.add(
                name,
                asset.pixel_frames(),
                asset.durations or None,
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot save library asset", str(error))
            return
        self._record_library_change(
            f"Saved {stored.name} from App GUI", before, stored.id
        )
        self._refresh_personal_asset_library(stored.id)

    def _rename_library_asset(self, asset_id: str) -> None:
        """Rename one persistent personal asset after explicit confirmation."""
        try:
            asset = self.asset_library.asset(asset_id)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot read asset library", str(error))
            return
        if asset is None:
            return
        name, accepted = QInputDialog.getText(
            self,
            "Rename library asset",
            "New name",
            text=asset.name,
        )
        if not accepted or not name.strip():
            return
        before = self._library_history_snapshot()
        try:
            renamed = self.asset_library.rename(asset_id, name)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot rename library asset", str(error))
            return
        self._record_library_change(
            f"Renamed {asset.name} to {renamed.name}", before, renamed.id
        )
        self._refresh_personal_asset_library(renamed.id)
        if renamed.name != name.strip():
            self.statusBar().showMessage(
                f'"{name.strip()}" was already used; renamed to {renamed.name}.'
            )
        else:
            self.statusBar().showMessage(f"Renamed library asset to {renamed.name}.")

    def _delete_library_asset(self, value: object) -> None:
        """Delete selected personal assets as one recoverable mutation."""
        asset_ids = tuple(
            asset_id
            for asset_id in self._library_asset_ids(value)
            if not is_standard_asset_id(asset_id)
        )
        if not asset_ids:
            self.statusBar().showMessage("Built-in standard icons cannot be deleted.")
            return
        try:
            assets = tuple(
                asset
                for asset_id in asset_ids
                if (asset := self.asset_library.asset(asset_id)) is not None
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot read asset library", str(error))
            return
        if not assets:
            return
        names = ", ".join(asset.name for asset in assets[:5])
        if len(assets) > 5:
            names += f", and {len(assets) - 5} more"
        answer = QMessageBox.question(
            self,
            "Delete personal library assets?",
            f"Delete {len(assets)} selected personal asset"
            f"{'s' if len(assets) != 1 else ''}?\n{names}\n\n"
            "Existing project copies remain unchanged. This deletion can be undone "
            "during the current editor session.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        before = self._library_history_snapshot()
        try:
            removed = self.asset_library.remove_many(
                tuple(asset.id for asset in assets)
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot delete library assets", str(error))
            return
        self._record_library_change(
            f"Deleted {len(removed)} asset{'s' if len(removed) != 1 else ''}",
            before,
        )
        self._refresh_personal_asset_library()

    def _show_pixel_canvas_context_menu(self, position) -> None:
        """Show the most-used Pixel Art operations at the pointer."""
        self._pixel_context_menu().exec(self.canvas.mapToGlobal(position))

    def _show_asset_catalogue_context_menu(self, position) -> None:
        """Select the pointed asset and show common catalogue operations."""
        item = self.asset_list.itemAt(position)
        if item is not None:
            self.asset_list.setCurrentItem(item)
        self._pixel_context_menu().exec(
            self.asset_list.viewport().mapToGlobal(position)
        )

    def _pixel_context_menu(self) -> QMenu:
        """Build the shared Pixel Art canvas and catalogue context menu."""
        menu = QMenu(self)
        menu.addAction(self.undo_action)
        menu.addAction(self.redo_action)
        menu.addSeparator()
        menu.addAction(self.select_all_pixels_action)
        menu.addAction(self.copy_pixels_action)
        menu.addAction(self.paste_pixels_action)
        menu.addAction(self.clear_selection_action)
        menu.addSeparator()
        menu.addAction(self.apply_action)
        menu.addAction(self.save_to_library_action)
        menu.addAction(self.place_in_gui_action)
        menu.addAction(self.export_action)
        menu.addSeparator()
        menu.addAction(self.new_graphic_action)
        menu.addAction(self.rescan_action)
        return menu

    def _pixel_selection_changed(self, selected: bool) -> None:
        """Update pixel editing controls for selection availability."""
        self._update_pixel_action_state()

    def _update_pixel_action_state(self) -> None:
        """Enable pixel operations supported by the active asset mode."""
        available = (
            self.current_asset is not None
            or self._is_portable_pixel_asset()
            or bool(self._editing_project_asset_id)
            or bool(self._editing_library_asset_id)
        )
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
        if self._editing_project_asset_id:
            self.crop_selection_action.setEnabled(False)
        self.paste_pixels_action.setEnabled(managed and self.canvas.has_clipboard())
        dimensions_editable = (
            managed
            and self.animation_parameter is None
            and not self._editing_project_asset_id
        )
        self.resize_canvas_action.setEnabled(dimensions_editable)
        self.scale_artwork_action.setEnabled(dimensions_editable)
        self.clear_canvas_action.setEnabled(managed)
        source_asset_available = self.current_asset is not None
        self.use_in_gui_action.setEnabled(source_asset_available)
        self.use_in_gui_button.setEnabled(source_asset_available)
        self.place_in_gui_action.setEnabled(available)
        self.place_in_gui_button.setEnabled(available)
        self.save_to_library_action.setEnabled(available)
        self.document_library_button.setEnabled(available)
        self.generate_python_action.setEnabled(available)
        self.document_python_button.setEnabled(available)
        self.open_reference_action.setEnabled(available)
        self.export_action.setEnabled(available)
        self.export_button.setEnabled(available)
        for action in self.tool_group.actions():
            action.setEnabled(available)
        for widget in (
            self.primary_button,
            self.background_button,
            self.zoom_spin,
            self.grid_check,
        ):
            widget.setEnabled(available)
        for action in (
            self.fit_canvas_action,
            self.one_to_one_action,
            self.center_canvas_action,
        ):
            action.setEnabled(available)
        self.pixel_empty_widget.setVisible(not available)
        self._update_color_label()

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
        key = self._publish_gui_pixel_asset(
            self.current_asset, self._editable_pixel_art()
        )
        self._activate_workspace(WorkspaceId.APP_GUI)
        self.screen_designer.select_pixel_asset(key)
        self.statusBar().showMessage(
            "Pixel asset selected in App GUI. Drag it onto the active screen."
        )

    def _place_current_asset_in_gui(self) -> None:
        """Place the current asset with an explicit dirty-document decision."""
        if self.current_asset is None:
            if not self._is_portable_pixel_asset():
                return
            self._capture_portable_frame()
            frames = tuple(frame.copy() for frame in self._portable_frames)
            durations = tuple(self._portable_durations)
            fingerprint = asset_fingerprint(encode_asset(frames, durations or None))
            key = f"portable::{fingerprint}"
            asset = GuiPixelAsset(
                key,
                self._active_pixel_name(),
                "Pixel Art workspace",
                self._active_pixel_name(),
                frames[0],
                fingerprint,
                frames,
                durations,
            )
            self.screen_designer.place_pixel_asset(asset, "detached")
            self._activate_workspace(WorkspaceId.APP_GUI)
            self.statusBar().showMessage(
                f"Placed an independent copy of {asset.name} on "
                f"{self.designer_session.current_screen().name}."
            )
            return
        link_state = "current"
        if self._dirty:
            message = QMessageBox(self)
            message.setWindowTitle("Place unsaved pixel asset")
            message.setText(
                "Save the Pixel Art changes first, or embed an intentionally detached draft?"
            )
            save_button = message.addButton(
                "Save and Place", QMessageBox.ButtonRole.AcceptRole
            )
            draft_button = message.addButton(
                "Embed Detached Draft", QMessageBox.ButtonRole.ActionRole
            )
            message.addButton(QMessageBox.StandardButton.Cancel)
            message.exec()
            if message.clickedButton() is save_button:
                if not self._apply_to_source() or self.current_asset is None:
                    return
            elif message.clickedButton() is draft_button:
                link_state = "draft"
            else:
                return
        key = self._publish_gui_pixel_asset(
            self.current_asset, self._editable_pixel_art()
        )
        asset = self.screen_designer.pixel_assets.get(key)
        if asset is None:
            return
        self.screen_designer.place_pixel_asset(asset, link_state)
        self._activate_workspace(WorkspaceId.APP_GUI)
        self.statusBar().showMessage(
            f"Placed {asset.name} on {self.designer_session.current_screen().name}."
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
        self._activate_workspace(WorkspaceId.PIXEL_ART)

    def _edit_project_asset(self, asset_id: str, frame_index: int = 0) -> None:
        """Open a complete placed project asset as a managed Pixel Art document."""
        asset = self.designer_session.project.asset(asset_id)
        if asset is None:
            QMessageBox.information(
                self,
                "Pixel asset unavailable",
                "The placed asset is no longer present in this GUI project.",
            )
            return
        if not self._confirm_discard():
            return
        frames = asset.pixel_frames()
        frame_index = max(0, min(int(frame_index), len(frames) - 1))
        self._editing_project_asset_id = asset.id
        self._editing_project_asset_frame = frame_index
        self._editing_library_asset_id = ""
        self._editing_library_asset_revision = ""
        self._open_portable_pixel_asset(
            asset.name,
            frames,
            asset.durations or None,
            frame_index=frame_index,
            dirty=False,
            source_label=f"GUI project asset {asset.id}",
            mode_label="PROJECT ASSET · edits return to App GUI",
        )
        self.warning_text.setPlainText(
            "Edit any frame, then choose Update Project Asset. "
            "Save the GUI project to persist the change. The generated .pga file is "
            "rebuilt later and is not edited directly."
        )

    def _apply_project_asset_edit(self) -> bool:
        """Write Pixel Editor changes back to one lossless GUI project asset."""
        asset = self.designer_session.project.asset(self._editing_project_asset_id)
        if asset is None:
            QMessageBox.warning(
                self,
                "Cannot update project asset",
                "The target asset is no longer present in the GUI project.",
            )
            return False
        self._capture_portable_frame()
        if any(
            frame.width != asset.width
            or frame.height != asset.height
            or frame.origin_x != asset.origin_x
            or frame.origin_y != asset.origin_y
            for frame in self._portable_frames
        ):
            QMessageBox.warning(
                self,
                "Project asset geometry changed",
                "Placed project assets must keep their canvas dimensions and origin. "
                "Undo the crop or resize, then update the asset.",
            )
            return False
        asset.frames = [list(frame.pixels) for frame in self._portable_frames]
        asset.durations = list(self._portable_durations)
        asset.source_path = ""
        asset.absolute_fallback = ""
        asset.qualified_name = ""
        asset.fingerprint = ""
        asset.link_state = "detached"
        art = self._portable_frames[0]
        blank = PixelArt(
            art.width,
            art.height,
            art.origin_x,
            art.origin_y,
        )
        runs = [list(run) for run in art.horizontal_runs(blank)]
        for screen in self.designer_session.project.screens:
            for element in screen.elements:
                if element.asset_id != asset.id:
                    continue
                element.asset_key = ""
                element.asset_source_path = ""
                element.asset_absolute_fallback = ""
                element.asset_qualified_name = ""
                element.asset_fingerprint = ""
                element.asset_link_state = "detached"
                element.asset_runs = [list(run) for run in runs]
        asset.validate()
        self.designer_session.mark_changed()
        self._dirty = False
        self._clear_pixel_recovery()
        self._update_preview()
        self._update_apply_state()
        self._update_pixel_action_state()
        self.statusBar().showMessage(
            f"Updated {asset.name} in the GUI project. Save the GUI project to persist it."
        )
        return True

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
                f"Select RGB565 color 0x{color:04X} for painting.\n"
                f"Example: Click it, then draw with Pencil; use Picker to sample the canvas."
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
            erase = (
                "Transparent"
                if self._current_asset_is_managed()
                else f"0x{self._background_color:04X}"
            )
            self.color_label.setText(
                f"Paint 0x{self._current_color:04X}   Erase {erase}"
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
        if not self._dirty:
            return False
        if self._editing_library_asset_id:
            return self._apply_library_asset_edit()
        if self._editing_project_asset_id:
            return self._apply_project_asset_edit()
        if self.current_asset is None or self.current_trace is None:
            return (
                self._generate_python_asset()
                if self._is_portable_pixel_asset()
                else False
            )
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
                self._editable_pixel_art(),
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

    def _export_png(self) -> bool:
        """Export the current pixel art as PNG."""
        if self.current_asset is None and not self._is_portable_pixel_asset():
            return False
        default_name = f"{self._active_pixel_name().lstrip('_')}.png"
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export PNG",
            str(Path.cwd() / default_name),
            "PNG images (*.png)",
        )
        if not filename:
            return False
        if not filename.lower().endswith(".png"):
            filename += ".png"
        if not pixel_art_image(self.canvas.art()).save(filename, "PNG"):
            QMessageBox.critical(
                self, "Export failed", "The PNG file could not be written."
            )
            return False
        self.statusBar().showMessage(f"Exported {filename}")
        return True

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
            if self._editing_library_asset_id:
                return self._apply_library_asset_edit()
            if self._editing_project_asset_id:
                return self._apply_project_asset_edit()
            if self.current_asset is None and self._is_portable_pixel_asset():
                return self._save_current_asset_to_library()
            return self._apply_to_source()
        if answer == QMessageBox.StandardButton.Discard:
            self._dirty = False
            if self.current_asset is None and self._is_portable_pixel_asset():
                self._pending_image_path = None
                self._pending_image_uses_canvas = False
                self._draft_asset_name = ""
                self._portable_frames.clear()
                self._portable_durations.clear()
                self._portable_frame_index = 0
                self._editing_project_asset_id = ""
                self._editing_library_asset_id = ""
                self._editing_library_asset_revision = ""
                self.canvas.set_reference_image(None)
                self.reference_status_label.setText("No reference loaded")
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
        self.canvas.setEnabled(False)
        self.asset_title.setText("No graphic selected")
        self.source_label.setText("No source selected")
        self.asset_mode_label.setText("No asset selected")
        self.asset_mode_label.setStyleSheet("")
        self.source_view_combo.setVisible(False)
        self.source_view_label.setVisible(False)
        self.warning_text.setPlainText("No supported drawing functions were found.")
        self.apply_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.apply_action.setEnabled(False)
        self.export_action.setEnabled(False)
        self.canvas.set_transparent_eraser(False)
        self._draft_asset_name = ""
        self._portable_frames.clear()
        self._portable_durations.clear()
        self._portable_frame_index = 0
        self._editing_library_asset_id = ""
        self._editing_library_asset_revision = ""
        self.pixel_empty_widget.setVisible(True)
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
