"""Qt main window for the graphics editor."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QStandardPaths, QTimer
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence, QPixmap
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
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QPlainTextEdit,
)

from .canvas import PixelCanvas, pixel_art_image, qcolor_from_rgb565
from .model import PixelArt, rgb_to_rgb565
from .source import (
    GraphicsAsset,
    SourceExporter,
    SourcePatch,
    SourceScanner,
    TraceInterpreter,
    TraceResult,
)


class DiffDialog(QDialog):
    """Show the exact source patch before applying it."""

    def __init__(self, patch: SourcePatch, parent: QWidget | None = None):
        """Build the source diff confirmation dialog."""
        super().__init__(parent)
        self.setWindowTitle("Review Python changes")
        self.resize(960, 680)
        layout = QVBoxLayout(self)
        summary = QLabel(
            f"{patch.path}\n{patch.run_count} optimized pixel runs will be written."
        )
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
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class MainWindow(QMainWindow):
    """Provide source discovery and mouse pixel editing."""

    def __init__(self):
        """Initialize editor state and user interface."""
        super().__init__()
        self.setWindowTitle("Pico Graphics Editor")
        self.resize(1480, 900)
        self.scanner = SourceScanner()
        self.tracer = TraceInterpreter()
        self.thumbnail_tracer = TraceInterpreter(800, 300, 48)
        self.exporter = SourceExporter()
        self.assets: list[GraphicsAsset] = []
        self.current_asset: GraphicsAsset | None = None
        self.current_trace: TraceResult | None = None
        self.variant_values: dict[str, Any] = {}
        self.variant_controls: dict[str, QComboBox] = {}
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

    def open_path(self, path: str | Path) -> None:
        """Open a Python file or source folder."""
        source_path = Path(path).expanduser().resolve()
        if source_path.is_dir():
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
        if self._confirm_discard():
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

        self.tool_bar = QToolBar("Pixel tools")
        self.tool_bar.setMovable(False)
        self.addToolBar(self.tool_bar)
        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        tool_specs = (
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
        self.setCentralWidget(splitter)
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
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        self.source_label = QLabel("No source selected")
        self.source_label.setWordWrap(True)
        self.source_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.source_label)

        self.variant_group = QGroupBox("Variants")
        self.variant_form = QFormLayout(self.variant_group)
        layout.addWidget(self.variant_group)

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
        layout.addStretch(1)
        return panel

    def _connect_actions(self) -> None:
        """Connect actions and widget signals."""
        self.open_file_action.triggered.connect(self._open_file)
        self.open_folder_action.triggered.connect(self._open_folder)
        self.rescan_action.triggered.connect(self._rescan)
        self.export_action.triggered.connect(self._export_png)
        self.apply_action.triggered.connect(self._apply_to_source)
        self.quit_action.triggered.connect(self.close)
        self.undo_action.triggered.connect(self.canvas.undo)
        self.redo_action.triggered.connect(self.canvas.redo)
        self.tool_group.triggered.connect(self._tool_changed)
        self.primary_button.clicked.connect(self._choose_primary_color)
        self.background_button.clicked.connect(self._choose_background_color)
        self.zoom_spin.valueChanged.connect(self.canvas.set_zoom)
        self.grid_check.toggled.connect(self.canvas.set_grid_visible)
        self.search_edit.textChanged.connect(self._filter_assets)
        self.asset_list.currentRowChanged.connect(self._select_asset)
        self.canvas.color_picked.connect(self._set_color)
        self.canvas.document_changed.connect(self._canvas_changed)
        self.canvas.cursor_changed.connect(self._cursor_changed)
        self.apply_button.clicked.connect(self._apply_to_source)
        self.export_button.clicked.connect(self._export_png)

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

    def _scan(self, path: Path, folder: bool) -> None:
        """Scan a selected source path and refresh the catalogue."""
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
        self._thumbnail_generation += 1
        generation = self._thumbnail_generation
        self._thumbnail_queue = list(range(len(assets)))
        self.current_asset = None
        self.current_trace = None
        self._dirty = False
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
            self.asset_list.setCurrentRow(0)
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
        self.current_asset = asset
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

    def _rebuild_variants(self, asset: GraphicsAsset) -> None:
        """Rebuild controls for inferred renderer variants."""
        while self.variant_form.rowCount():
            self.variant_form.removeRow(0)
        self.variant_controls.clear()
        for name, values in asset.variants.items():
            combo = QComboBox()
            for value in values:
                combo.addItem(str(value), value)
            current = self.variant_values.get(name)
            index = combo.findData(current)
            combo.setCurrentIndex(max(0, index))
            combo.currentIndexChanged.connect(self._variant_changed)
            self.variant_form.addRow(name, combo)
            self.variant_controls[name] = combo
        self.variant_group.setVisible(bool(asset.variants))

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
        self._suppress_changes = True
        self.canvas.set_art(trace.current_art)
        self._suppress_changes = False
        self._dirty = False
        self._update_palette(trace.current_art)
        self._update_preview()
        notes = trace.warnings[:20]
        if not trace.primitives:
            notes.insert(0, "No supported drawing calls rendered for this variant.")
        self.warning_text.setPlainText(
            "\n".join(notes) if notes else "Ready for pixel editing."
        )
        self._update_apply_state()

    def _canvas_changed(self) -> None:
        """Update dirty state and previews after painting."""
        if self._suppress_changes or self.current_trace is None:
            return
        try:
            self._dirty = bool(
                self.canvas.art().changed_pixels(self.current_trace.current_art)
            )
        except ValueError:
            self._dirty = True
        self._update_preview()
        self._update_apply_state()

    def _update_apply_state(self) -> None:
        """Enable source applying when an edit exists."""
        enabled = (
            self._dirty
            and self.current_asset is not None
            and self.current_trace is not None
        )
        self.apply_button.setEnabled(enabled)
        self.apply_action.setEnabled(enabled)
        title = "Pico Graphics Editor"
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

    def _apply_to_source(self) -> None:
        """Review, back up, and apply edited pixels."""
        if not self._dirty or self.current_asset is None or self.current_trace is None:
            return
        try:
            disk_source = self.current_asset.document.path.read_text(encoding="utf-8")
        except OSError as error:
            QMessageBox.critical(self, "Cannot read source", str(error))
            return
        if disk_source != self.current_asset.document.source:
            QMessageBox.warning(
                self,
                "Source changed",
                "The Python file changed after scanning. Rescan before applying edits.",
            )
            return
        try:
            patch = self.exporter.build_patch(
                self.current_asset,
                self.current_trace,
                self.canvas.art(),
                self.variant_values,
            )
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Cannot build patch", str(error))
            return
        if not patch.diff:
            QMessageBox.information(
                self, "No changes", "The edited pixels match the source rendering."
            )
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
            backup_path = patch.apply(backup_root)
        except Exception as error:
            QMessageBox.critical(self, "Apply failed", str(error))
            return
        self._dirty = False
        QMessageBox.information(
            self,
            "Python updated",
            f"Updated {patch.path.name}.\nBackup: {backup_path}",
        )
        self._scan(self._scan_path or patch.path, self._scan_folder)

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
        """Confirm discarding an unapplied pixel edit."""
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Discard pixel edits?",
            "The current pixel edits have not been applied to Python. Discard them?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Discard:
            self._dirty = False
            return True
        return False

    def _clear_editor(self) -> None:
        """Reset the canvas and inspector for no results."""
        self._suppress_changes = True
        self.canvas.set_art(PixelArt(32, 32))
        self._suppress_changes = False
        self.asset_title.setText("No graphic selected")
        self.source_label.setText("No source selected")
        self.warning_text.setPlainText("No supported drawing functions were found.")
        self.apply_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.apply_action.setEnabled(False)
        self.export_action.setEnabled(False)


def color_button_style(color: int) -> str:
    """Return readable foreground and background styling."""
    qt_color = qcolor_from_rgb565(color)
    luminance = (
        qt_color.red() * 299 + qt_color.green() * 587 + qt_color.blue() * 114
    ) // 1000
    foreground = "#000000" if luminance >= 145 else "#ffffff"
    return f"background: {qt_color.name()}; color: {foreground};"
