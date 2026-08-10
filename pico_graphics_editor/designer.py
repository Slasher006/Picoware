"""Qt screen designer and navigation graph workspaces."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import (
    QMimeData,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QObject,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDrag,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QImage,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
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
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .canvas import qcolor_from_rgb565
from .designer_model import (
    DEVICE_PROFILES,
    ELEMENT_KINDS,
    FlowConnection,
    GuiElement,
    GuiProject,
    ScreenDesign,
    new_identifier,
)
from .model import rgb_to_rgb565
from .reference import prepare_reference_image, read_image_frames


ELEMENT_MIME_TYPE = "application/x-pico-gui-element"


class DesignerSession(QObject):
    """Share one editable GUI project between designer workspaces."""

    project_changed = Signal()
    dirty_changed = Signal(bool)
    active_screen_changed = Signal(str)

    def __init__(self, parent: QObject | None = None):
        """Initialize a new unsaved GUI project."""
        super().__init__(parent)
        self.project = GuiProject.create()
        self.path: Path | None = None
        self.dirty = False
        self.active_screen_id = self.project.start_screen_id

    def set_project(self, project: GuiProject, path: Path | None = None) -> None:
        """Replace the current project and reset edit state."""
        self.project = project
        self.path = path
        self.active_screen_id = project.start_screen_id
        self.dirty = False
        self.project_changed.emit()
        self.active_screen_changed.emit(self.active_screen_id)
        self.dirty_changed.emit(False)

    def current_screen(self) -> ScreenDesign:
        """Return the active screen with a safe fallback."""
        screen = self.project.screen(self.active_screen_id)
        if screen is None:
            screen = self.project.screens[0]
            self.active_screen_id = screen.id
        return screen

    def set_active_screen(self, screen_id: str) -> None:
        """Select a project screen by identifier."""
        if self.project.screen(screen_id) is None:
            return
        self.active_screen_id = screen_id
        self.active_screen_changed.emit(screen_id)

    def mark_changed(self, refresh: bool = True) -> None:
        """Mark the project dirty and optionally refresh views."""
        if not self.dirty:
            self.dirty = True
            self.dirty_changed.emit(True)
        if refresh:
            self.project_changed.emit()

    def save(self, path: str | Path | None = None) -> Path:
        """Save the project and return its path."""
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ValueError("A GUI project path is required")
        self.project.save(target)
        self.path = target
        self.dirty = False
        self.dirty_changed.emit(False)
        return target


def draw_screen(
    painter: QPainter,
    screen: ScreenDesign,
    target: QRectF,
    selected_id: str | None = None,
    reference: QImage | None = None,
    reference_opacity: float = 0.45,
) -> None:
    """Draw one designed screen into a target rectangle."""
    scale = min(target.width() / screen.width, target.height() / screen.height)
    draw_width = screen.width * scale
    draw_height = screen.height * scale
    left = target.x() + (target.width() - draw_width) / 2
    top = target.y() + (target.height() - draw_height) / 2
    painter.save()
    painter.translate(left, top)
    painter.scale(scale, scale)
    painter.setClipRect(QRectF(0, 0, screen.width, screen.height))
    painter.fillRect(
        QRectF(0, 0, screen.width, screen.height),
        qcolor_from_rgb565(screen.background_color),
    )
    if reference is not None:
        prepared = prepare_reference_image(reference, screen.width, screen.height)
        painter.setOpacity(reference_opacity)
        painter.drawImage(QRectF(0, 0, screen.width, screen.height), prepared)
        painter.setOpacity(1.0)
    for element in screen.elements:
        if element.visible:
            draw_element(painter, element, element.id == selected_id)
    painter.restore()


def draw_element(
    painter: QPainter, element: GuiElement, selected: bool = False
) -> None:
    """Draw one GUI element and its selection handles."""
    rectangle = QRectF(element.x, element.y, element.width, element.height)
    fill = qcolor_from_rgb565(element.fill_color)
    border = qcolor_from_rgb565(element.border_color)
    text_color = qcolor_from_rgb565(element.text_color)
    painter.setPen(QPen(border, 1))
    if element.kind == "label":
        painter.setPen(text_color)
        painter.drawText(
            rectangle,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            element.text,
        )
    elif element.kind == "rectangle":
        painter.fillRect(rectangle, fill)
    else:
        painter.fillRect(rectangle, fill)
        painter.drawRect(rectangle)
        if element.kind == "list":
            painter.setPen(text_color)
            for index, text in enumerate(element.text.splitlines()):
                painter.drawText(
                    QPointF(element.x + 4, element.y + 14 + index * 14), text
                )
        elif element.kind == "progress":
            painter.fillRect(
                QRectF(
                    element.x, element.y, max(1, element.width // 2), element.height
                ),
                border,
            )
        elif element.text:
            painter.setPen(text_color)
            painter.drawText(
                rectangle.adjusted(3, 2, -3, -2),
                Qt.AlignmentFlag.AlignCenter,
                element.text,
            )
    if selected:
        painter.setPen(QPen(QColor("#00aaff"), 1, Qt.PenStyle.DashLine))
        painter.drawRect(rectangle.adjusted(-2, -2, 2, 2))
        painter.fillRect(
            QRectF(element.x + element.width - 5, element.y + element.height - 5, 7, 7),
            QColor("#00aaff"),
        )
    if element.locked:
        painter.setPen(QPen(QColor("#ff9800"), 1, Qt.PenStyle.DashLine))
        painter.drawRect(rectangle.adjusted(1, 1, -1, -1))
        painter.drawText(
            rectangle.adjusted(3, 2, -3, -2),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
            "CODE",
        )


class ElementPaletteButton(QPushButton):
    """Provide click and drag creation for one GUI element kind."""

    def __init__(self, kind: str, parent: QWidget | None = None):
        """Initialize a draggable palette button for the given kind."""
        super().__init__(kind.title(), parent)
        self.kind = kind
        self._drag_start = QPoint()
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(f"Drag a {kind} onto the canvas, or click to add it.")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Remember where a possible palette drag started."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Start a copy drag after the platform movement threshold."""
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        distance = (event.position().toPoint() - self._drag_start).manhattanLength()
        if distance < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return
        mime = QMimeData()
        mime.setData(ELEMENT_MIME_TYPE, self.kind.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.setHotSpot(event.position().toPoint())
        drag.exec(Qt.DropAction.CopyAction)


class DesignCanvas(QWidget):
    """Provide direct selection, dragging, and resizing of GUI elements."""

    element_selected = Signal(str)
    geometry_changed = Signal()
    zoom_changed = Signal(int)
    element_dropped = Signal(str, int, int)

    def __init__(self, session: DesignerSession, parent: QWidget | None = None):
        """Initialize the screen design canvas."""
        super().__init__(parent)
        self.session = session
        self.selected_id: str | None = None
        self.zoom_percent = 180
        self.reference: QImage | None = None
        self.reference_opacity = 45
        self._drag_mode = ""
        self._drag_offset = QPointF()
        self._drop_kind = ""
        self._drop_point = QPointF()
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._update_size()

    def set_selected(self, element_id: str | None) -> None:
        """Select one element for drawing and manipulation."""
        self.selected_id = element_id
        self.update()

    def set_zoom(self, percent: int) -> None:
        """Set the screen canvas zoom percentage."""
        value = max(25, min(500, percent))
        if value == self.zoom_percent:
            return
        self.zoom_percent = value
        self._update_size()
        self.update()
        self.zoom_changed.emit(value)

    def set_reference(self, image: QImage | None) -> None:
        """Set a screen-wide tracing reference."""
        self.reference = None if image is None else image.copy()
        self.update()

    def set_reference_opacity(self, percent: int) -> None:
        """Set screen reference opacity."""
        self.reference_opacity = max(0, min(100, percent))
        self.update()

    def sizeHint(self) -> QSize:
        """Return the zoomed active-screen size."""
        screen = self.session.current_screen()
        factor = self.zoom_percent / 100
        return QSize(round(screen.width * factor), round(screen.height * factor))

    def paintEvent(self, event) -> None:
        """Paint the current designed screen."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        draw_screen(
            painter,
            self.session.current_screen(),
            QRectF(0, 0, self.width(), self.height()),
            self.selected_id,
            self.reference,
            self.reference_opacity / 100,
        )
        if self._drop_kind:
            factor = self.zoom_percent / 100
            point = QPointF(
                self._drop_point.x() * factor, self._drop_point.y() * factor
            )
            guide = QRectF(point.x() - 46, point.y() - 16, 92, 32)
            painter.setBrush(QColor(0, 170, 255, 45))
            painter.setPen(QPen(QColor("#00aaff"), 2, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(guide, 5, 5)
            painter.drawText(
                guide, Qt.AlignmentFlag.AlignCenter, self._drop_kind.title()
            )
        painter.end()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Accept supported GUI element palette drags."""
        if self._drag_kind(event.mimeData()) is not None:
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        """Preview a supported element at its prospective drop point."""
        kind = self._drag_kind(event.mimeData())
        if kind is None:
            event.ignore()
            return
        self._drop_kind = kind
        self._drop_point = self._design_point(event.position())
        self.update()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        """Clear the palette drop preview when the drag leaves."""
        self._clear_drop_preview()
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        """Create a palette element at the dropped canvas position."""
        kind = self._drag_kind(event.mimeData())
        if kind is None:
            event.ignore()
            return
        point = self._design_point(event.position())
        self._clear_drop_preview()
        self.element_dropped.emit(kind, round(point.x()), round(point.y()))
        event.acceptProposedAction()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Select an element and begin moving or resizing it."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = self._design_point(event.position())
        element = self._element_at(point)
        if element is None:
            self.selected_id = None
            self.element_selected.emit("")
            self.update()
            return
        self.selected_id = element.id
        self.element_selected.emit(element.id)
        if element.locked:
            self._drag_mode = ""
            self.update()
            return
        resize_area = QRectF(
            element.x + element.width - 8,
            element.y + element.height - 8,
            12,
            12,
        )
        source_call = str(element.source_values.get("call_type", ""))
        can_resize = not (element.source_path and source_call == "text")
        self._drag_mode = (
            "resize" if can_resize and resize_area.contains(point) else "move"
        )
        self._drag_offset = QPointF(point.x() - element.x, point.y() - element.y)
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Move or resize the selected element."""
        if not self._drag_mode or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        element = self._selected_element()
        if element is None:
            return
        point = self._design_point(event.position())
        screen = self.session.current_screen()
        if self._drag_mode == "move":
            element.x = max(
                0,
                min(
                    screen.width - element.width,
                    round(point.x() - self._drag_offset.x()),
                ),
            )
            element.y = max(
                0,
                min(
                    screen.height - element.height,
                    round(point.y() - self._drag_offset.y()),
                ),
            )
        else:
            element.width = max(
                1, min(screen.width - element.x, round(point.x() - element.x))
            )
            element.height = max(
                1, min(screen.height - element.y, round(point.y() - element.y))
            )
        self.session.mark_changed(False)
        self.geometry_changed.emit()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Finish the current geometry change."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_mode = ""

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Zoom the GUI canvas with the mouse wheel."""
        delta = event.angleDelta().y()
        if not delta:
            super().wheelEvent(event)
            return
        self.set_zoom(self.zoom_percent + (10 if delta > 0 else -10))
        event.accept()

    def _design_point(self, point: QPointF) -> QPointF:
        """Convert widget coordinates to screen coordinates."""
        factor = self.zoom_percent / 100
        return QPointF(point.x() / factor, point.y() / factor)

    def _element_at(self, point: QPointF) -> GuiElement | None:
        """Return the topmost element below one point."""
        screen = self.session.current_screen()
        for element in reversed(screen.elements):
            if element.visible and QRectF(
                element.x, element.y, element.width, element.height
            ).contains(point):
                return element
        return None

    def _selected_element(self) -> GuiElement | None:
        """Return the selected element from the active screen."""
        return next(
            (
                element
                for element in self.session.current_screen().elements
                if element.id == self.selected_id
            ),
            None,
        )

    def _drag_kind(self, mime: QMimeData) -> str | None:
        """Return a valid GUI element kind from drag data."""
        if not mime.hasFormat(ELEMENT_MIME_TYPE):
            return None
        kind = bytes(mime.data(ELEMENT_MIME_TYPE)).decode("utf-8", "ignore")
        return kind if kind in ELEMENT_KINDS else None

    def _clear_drop_preview(self) -> None:
        """Clear the active palette drop guide."""
        self._drop_kind = ""
        self._drop_point = QPointF()
        self.update()

    def _update_size(self) -> None:
        """Update the scrollable canvas size."""
        self.setFixedSize(self.sizeHint())


class ScreenDesignerWidget(QWidget):
    """Design complete application screens with direct manipulation."""

    def __init__(self, session: DesignerSession, parent: QWidget | None = None):
        """Build the screen designer workspace."""
        super().__init__(parent)
        self.session = session
        self.selected_element_id: str | None = None
        self._updating = False
        self._build_interface()
        self._connect_signals()
        self.refresh()

    def _build_interface(self) -> None:
        """Build screen, canvas, hierarchy, and property panels."""
        layout = QVBoxLayout(self)
        project_row = QHBoxLayout()
        project_row.addWidget(QLabel("Project"))
        self.project_name_edit = QLineEdit()
        project_row.addWidget(self.project_name_edit, 1)
        project_row.addWidget(QLabel("Device"))
        self.profile_combo = QComboBox()
        for name in DEVICE_PROFILES:
            self.profile_combo.addItem(name)
        project_row.addWidget(self.profile_combo)
        project_row.addWidget(QLabel("Width"))
        self.project_width_spin = QSpinBox()
        self.project_width_spin.setRange(32, 2048)
        project_row.addWidget(self.project_width_spin)
        project_row.addWidget(QLabel("Height"))
        self.project_height_spin = QSpinBox()
        self.project_height_spin.setRange(32, 2048)
        project_row.addWidget(self.project_height_spin)
        project_row.addWidget(QLabel("Canvas zoom"))
        self.zoom_spin = QSpinBox()
        self.zoom_spin.setRange(25, 500)
        self.zoom_spin.setValue(180)
        self.zoom_spin.setSuffix("%")
        project_row.addWidget(self.zoom_spin)
        self.import_mode_label = QLabel()
        self.import_mode_label.setStyleSheet("color: #ef6c00; font-weight: 600;")
        project_row.addWidget(self.import_mode_label)
        layout.addLayout(project_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Screens"))
        self.screen_list = QListWidget()
        left_layout.addWidget(self.screen_list, 1)
        screen_buttons = QGridLayout()
        self.add_screen_button = QPushButton("Add")
        self.duplicate_screen_button = QPushButton("Duplicate")
        self.delete_screen_button = QPushButton("Delete")
        screen_buttons.addWidget(self.add_screen_button, 0, 0)
        screen_buttons.addWidget(self.duplicate_screen_button, 0, 1)
        screen_buttons.addWidget(self.delete_screen_button, 1, 0, 1, 2)
        left_layout.addLayout(screen_buttons)
        reference_group = QGroupBox("Screen reference")
        reference_layout = QVBoxLayout(reference_group)
        self.open_reference_button = QPushButton("Open image...")
        self.clear_reference_button = QPushButton("Clear")
        self.reference_opacity_spin = QSpinBox()
        self.reference_opacity_spin.setRange(0, 100)
        self.reference_opacity_spin.setValue(45)
        self.reference_opacity_spin.setSuffix("%")
        reference_layout.addWidget(self.open_reference_button)
        reference_layout.addWidget(self.clear_reference_button)
        reference_layout.addWidget(self.reference_opacity_spin)
        left_layout.addWidget(reference_group)
        splitter.addWidget(left)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        tools = QHBoxLayout()
        tools.addWidget(QLabel("Drag onto canvas"))
        self.element_buttons: dict[str, ElementPaletteButton] = {}
        for kind in ELEMENT_KINDS:
            button = ElementPaletteButton(kind)
            self.element_buttons[kind] = button
            tools.addWidget(button)
        tools.addStretch(1)
        center_layout.addLayout(tools)
        self.canvas_scroll = QScrollArea()
        self.canvas_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas = DesignCanvas(self.session)
        self.canvas_scroll.setWidget(self.canvas)
        center_layout.addWidget(self.canvas_scroll, 1)
        splitter.addWidget(center)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("Element hierarchy"))
        self.element_list = QListWidget()
        right_layout.addWidget(self.element_list, 1)
        self.delete_element_button = QPushButton("Delete selected element")
        right_layout.addWidget(self.delete_element_button)
        self.source_notice_label = QLabel()
        self.source_notice_label.setWordWrap(True)
        self.source_notice_label.setStyleSheet("color: #ef6c00;")
        right_layout.addWidget(self.source_notice_label)
        self.screen_group = QGroupBox("Screen properties")
        screen_form = QFormLayout(self.screen_group)
        self.screen_name_edit = QLineEdit()
        self.screen_background_button = QPushButton("Choose...")
        screen_form.addRow("Name", self.screen_name_edit)
        screen_form.addRow("Background", self.screen_background_button)
        right_layout.addWidget(self.screen_group)
        self.property_group = QGroupBox("Element properties")
        property_form = QFormLayout(self.property_group)
        self.element_name_edit = QLineEdit()
        self.kind_combo = QComboBox()
        for kind in ELEMENT_KINDS:
            self.kind_combo.addItem(kind.title(), kind)
        self.x_spin = self._coordinate_spin()
        self.y_spin = self._coordinate_spin()
        self.width_spin = self._coordinate_spin(1)
        self.height_spin = self._coordinate_spin(1)
        self.element_text_edit = QLineEdit()
        self.element_text_edit.setPlaceholderText("Use \\n for list rows")
        self.asset_call_edit = QLineEdit()
        self.asset_call_edit.setPlaceholderText("Optional icon function")
        self.visible_check = QCheckBox("Visible")
        self.fill_color_button = QPushButton("Fill...")
        self.border_color_button = QPushButton("Border...")
        self.text_color_button = QPushButton("Text...")
        property_form.addRow("Name", self.element_name_edit)
        property_form.addRow("Type", self.kind_combo)
        property_form.addRow("X", self.x_spin)
        property_form.addRow("Y", self.y_spin)
        property_form.addRow("Width", self.width_spin)
        property_form.addRow("Height", self.height_spin)
        property_form.addRow("Text", self.element_text_edit)
        property_form.addRow("Asset call", self.asset_call_edit)
        property_form.addRow(self.visible_check)
        property_form.addRow(self.fill_color_button)
        property_form.addRow(self.border_color_button)
        property_form.addRow(self.text_color_button)
        right_layout.addWidget(self.property_group)
        splitter.addWidget(right)
        splitter.setSizes((220, 820, 300))
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

    def _connect_signals(self) -> None:
        """Connect designer controls to the shared project."""
        self.session.project_changed.connect(self.refresh)
        self.session.active_screen_changed.connect(self._active_screen_changed)
        self.screen_list.currentRowChanged.connect(self._screen_selected)
        self.element_list.currentRowChanged.connect(self._element_row_selected)
        self.canvas.element_selected.connect(self._canvas_element_selected)
        self.canvas.geometry_changed.connect(self._canvas_geometry_changed)
        self.canvas.zoom_changed.connect(self.zoom_spin.setValue)
        self.canvas.element_dropped.connect(self._drop_element)
        self.zoom_spin.valueChanged.connect(self.canvas.set_zoom)
        self.add_screen_button.clicked.connect(self._add_screen)
        self.duplicate_screen_button.clicked.connect(self._duplicate_screen)
        self.delete_screen_button.clicked.connect(self._delete_screen)
        self.delete_element_button.clicked.connect(self._delete_element)
        for kind, button in self.element_buttons.items():
            button.clicked.connect(
                lambda checked=False, element_kind=kind: self._add_element(element_kind)
            )
        self.project_name_edit.editingFinished.connect(self._project_settings_changed)
        self.profile_combo.currentIndexChanged.connect(self._profile_changed)
        self.project_width_spin.valueChanged.connect(self._custom_size_changed)
        self.project_height_spin.valueChanged.connect(self._custom_size_changed)
        self.screen_name_edit.editingFinished.connect(self._screen_properties_changed)
        self.screen_background_button.clicked.connect(self._choose_screen_background)
        for widget in (
            self.element_name_edit,
            self.element_text_edit,
            self.asset_call_edit,
        ):
            widget.editingFinished.connect(self._element_properties_changed)
        self.kind_combo.currentIndexChanged.connect(self._element_properties_changed)
        for widget in (self.x_spin, self.y_spin, self.width_spin, self.height_spin):
            widget.valueChanged.connect(self._element_properties_changed)
        self.visible_check.toggled.connect(self._element_properties_changed)
        self.fill_color_button.clicked.connect(
            lambda: self._choose_element_color("fill_color")
        )
        self.border_color_button.clicked.connect(
            lambda: self._choose_element_color("border_color")
        )
        self.text_color_button.clicked.connect(
            lambda: self._choose_element_color("text_color")
        )
        self.open_reference_button.clicked.connect(self._open_reference)
        self.clear_reference_button.clicked.connect(
            lambda: self.canvas.set_reference(None)
        )
        self.reference_opacity_spin.valueChanged.connect(
            self.canvas.set_reference_opacity
        )

    def refresh(self) -> None:
        """Refresh all controls from the current project."""
        self._updating = True
        project = self.session.project
        self.project_name_edit.setText(project.name)
        self.profile_combo.setCurrentText(project.profile)
        self.project_width_spin.setValue(project.width)
        self.project_height_spin.setValue(project.height)
        custom = project.profile == "Custom"
        self.project_width_spin.setEnabled(custom)
        self.project_height_spin.setEnabled(custom)
        self.import_mode_label.setText(
            "SOURCE-BACKED APP" if project.imported_sources else ""
        )
        self.screen_list.clear()
        selected_row = 0
        for index, screen in enumerate(project.screens):
            item = QListWidgetItem(screen.name)
            item.setData(Qt.ItemDataRole.UserRole, screen.id)
            if screen.source_path:
                item.setToolTip(f"{screen.source_path}:{screen.source_line}")
            self.screen_list.addItem(item)
            if screen.id == self.session.active_screen_id:
                selected_row = index
        self.screen_list.setCurrentRow(selected_row)
        self._refresh_element_list()
        self._refresh_screen_properties()
        self._refresh_element_properties()
        self.canvas._update_size()
        self.canvas.update()
        self._updating = False

    def _coordinate_spin(self, minimum: int = 0) -> QSpinBox:
        """Create a bounded geometry spin box."""
        spin = QSpinBox()
        spin.setRange(minimum, 4096)
        return spin

    def _screen_selected(self, row: int) -> None:
        """Select the screen represented by one list row."""
        if self._updating or row < 0:
            return
        item = self.screen_list.item(row)
        if item is not None:
            self.selected_element_id = None
            self.session.set_active_screen(str(item.data(Qt.ItemDataRole.UserRole)))

    def _active_screen_changed(self, screen_id: str) -> None:
        """Refresh the designer for a shared screen selection."""
        self.selected_element_id = None
        self.refresh()

    def _add_screen(self) -> None:
        """Prompt for and add a new application screen."""
        name, accepted = QInputDialog.getText(self, "Add screen", "Screen name")
        if not accepted or not name.strip():
            return
        project = self.session.project
        screen = ScreenDesign.create(
            name.strip(), project.width, project.height, len(project.screens)
        )
        project.screens.append(screen)
        self.session.active_screen_id = screen.id
        self.session.mark_changed()

    def _duplicate_screen(self) -> None:
        """Duplicate the active screen and its elements."""
        source = self.session.current_screen()
        duplicate = ScreenDesign.from_dict(asdict(source))
        duplicate.id = new_identifier("screen")
        duplicate.name = f"{source.name} Copy"
        duplicate.node_x += 40
        duplicate.node_y += 40
        duplicate.source_path = ""
        duplicate.source_name = ""
        duplicate.source_line = 0
        duplicate.source_state = None
        duplicate.elements = [
            element for element in duplicate.elements if not element.locked
        ]
        for element in duplicate.elements:
            element.id = new_identifier("element")
            element.source_path = ""
            element.source_line = 0
            element.source_call = ""
            element.source_segment = ""
            element.source_values.clear()
        self.session.project.screens.append(duplicate)
        self.session.active_screen_id = duplicate.id
        self.session.mark_changed()

    def _delete_screen(self) -> None:
        """Delete the active screen after confirmation."""
        project = self.session.project
        if len(project.screens) <= 1:
            QMessageBox.information(
                self, "Keep one screen", "A project needs at least one screen."
            )
            return
        screen = self.session.current_screen()
        if screen.source_path:
            QMessageBox.information(
                self,
                "Source screen retained",
                "Imported source screens cannot be deleted automatically.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Delete screen?",
            f"Delete {screen.name} and all of its flow connections?",
            QMessageBox.StandardButton.Delete | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Delete:
            return
        project.screens = [item for item in project.screens if item.id != screen.id]
        project.connections = [
            connection
            for connection in project.connections
            if connection.source_id != screen.id and connection.target_id != screen.id
        ]
        if project.start_screen_id == screen.id:
            project.start_screen_id = project.screens[0].id
        self.session.active_screen_id = project.screens[0].id
        self.session.mark_changed()

    def _add_element(self, kind: str) -> None:
        """Add one element to the active screen."""
        screen = self.session.current_screen()
        element = GuiElement.create(kind, len(screen.elements) + 1)
        screen.elements.append(element)
        self.selected_element_id = element.id
        self.session.mark_changed()

    def _drop_element(self, kind: str, x: int, y: int) -> None:
        """Add one palette element centered at a canvas drop point."""
        screen = self.session.current_screen()
        element = GuiElement.create(kind, len(screen.elements) + 1)
        element.x = max(0, min(screen.width - element.width, x - element.width // 2))
        element.y = max(0, min(screen.height - element.height, y - element.height // 2))
        screen.elements.append(element)
        self.selected_element_id = element.id
        self.session.mark_changed()

    def _delete_element(self) -> None:
        """Delete the selected screen element."""
        if self.selected_element_id is None:
            return
        screen = self.session.current_screen()
        selected = self._selected_element()
        if selected is not None and selected.source_path:
            QMessageBox.information(
                self,
                "Source element retained",
                "Imported source calls cannot be deleted. Set Visible off for editable calls.",
            )
            return
        screen.elements = [
            item for item in screen.elements if item.id != self.selected_element_id
        ]
        self.selected_element_id = None
        self.session.mark_changed()

    def _refresh_element_list(self) -> None:
        """Refresh the active screen hierarchy list."""
        self.element_list.clear()
        selected_row = -1
        for index, element in enumerate(self.session.current_screen().elements):
            prefix = "[code] " if element.locked else ""
            item = QListWidgetItem(f"{prefix}{element.kind}: {element.name}")
            item.setData(Qt.ItemDataRole.UserRole, element.id)
            if element.source_path:
                state = (
                    "Locked dynamic code" if element.locked else "Editable source call"
                )
                item.setToolTip(f"{state}\n{element.source_path}:{element.source_line}")
            self.element_list.addItem(item)
            if element.id == self.selected_element_id:
                selected_row = index
        self.element_list.setCurrentRow(selected_row)
        self.canvas.set_selected(self.selected_element_id)

    def _element_row_selected(self, row: int) -> None:
        """Select an element from the hierarchy."""
        if self._updating or row < 0:
            return
        item = self.element_list.item(row)
        self._select_element(str(item.data(Qt.ItemDataRole.UserRole)) if item else None)

    def _canvas_element_selected(self, element_id: str) -> None:
        """Synchronize a canvas selection into the hierarchy."""
        self._select_element(element_id or None)

    def _select_element(self, element_id: str | None) -> None:
        """Select one active-screen element."""
        self.selected_element_id = element_id
        self.canvas.set_selected(element_id)
        self._updating = True
        for row in range(self.element_list.count()):
            item = self.element_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == element_id:
                self.element_list.setCurrentRow(row)
                break
        if element_id is None:
            self.element_list.setCurrentRow(-1)
        self._refresh_element_properties()
        self._updating = False

    def _selected_element(self) -> GuiElement | None:
        """Return the selected active-screen element."""
        return next(
            (
                element
                for element in self.session.current_screen().elements
                if element.id == self.selected_element_id
            ),
            None,
        )

    def _refresh_screen_properties(self) -> None:
        """Refresh active screen property controls."""
        screen = self.session.current_screen()
        self.screen_name_edit.setText(screen.name)
        self.screen_background_button.setStyleSheet(
            f"background: {qcolor_from_rgb565(screen.background_color).name()};"
        )
        self.screen_background_button.setEnabled(not bool(screen.source_path))
        self.screen_background_button.setToolTip(
            "Edit the imported background draw element instead."
            if screen.source_path
            else "Choose the designer screen background."
        )

    def _refresh_element_properties(self) -> None:
        """Refresh controls for the selected element."""
        element = self._selected_element()
        self.property_group.setEnabled(element is not None and not element.locked)
        self.delete_element_button.setEnabled(
            element is not None and not bool(element.source_path)
        )
        if element is None:
            self.source_notice_label.clear()
            return
        if element.locked:
            self.source_notice_label.setText(
                f"Locked dynamic code. Preserved unchanged.\n{element.source_path}:{element.source_line}"
            )
        elif element.source_path:
            self.source_notice_label.setText(
                f"Editable source call. Changes create a narrow patch.\n{element.source_path}:{element.source_line}"
            )
        else:
            self.source_notice_label.clear()
        self.element_name_edit.setText(element.name)
        self.kind_combo.setCurrentIndex(max(0, self.kind_combo.findData(element.kind)))
        self.kind_combo.setEnabled(not bool(element.source_path))
        self.x_spin.setValue(element.x)
        self.y_spin.setValue(element.y)
        self.width_spin.setValue(element.width)
        self.height_spin.setValue(element.height)
        self.element_text_edit.setText(element.text.replace("\n", "\\n"))
        self.asset_call_edit.setText(element.asset_call)
        self.asset_call_edit.setEnabled(not bool(element.source_path))
        self.visible_check.setChecked(element.visible)
        self._update_color_buttons(element)
        source_call = str(element.source_values.get("call_type", ""))
        source_backed = bool(element.source_path)
        self.width_spin.setEnabled(not source_backed or source_call != "text")
        self.height_spin.setEnabled(not source_backed or source_call != "text")
        self.element_text_edit.setEnabled(not source_backed or source_call == "text")
        self.fill_color_button.setEnabled(
            not source_backed or source_call == "fill_rect"
        )
        self.border_color_button.setEnabled(not source_backed or source_call == "rect")
        self.text_color_button.setEnabled(not source_backed or source_call == "text")

    def _canvas_geometry_changed(self) -> None:
        """Refresh geometry fields after direct manipulation."""
        self._updating = True
        self._refresh_element_properties()
        self._updating = False

    def _project_settings_changed(self) -> None:
        """Apply the edited project name."""
        if self._updating:
            return
        name = self.project_name_edit.text().strip()
        if name and name != self.session.project.name:
            self.session.project.name = name
            self.session.mark_changed(False)

    def _profile_changed(self) -> None:
        """Apply a device profile to every project screen."""
        if self._updating:
            return
        profile = self.profile_combo.currentText()
        if profile == "Custom":
            width = self.project_width_spin.value()
            height = self.project_height_spin.value()
        else:
            width, height = DEVICE_PROFILES[profile]
        self._apply_project_size(profile, width, height)

    def _custom_size_changed(self) -> None:
        """Apply editable dimensions for the custom device profile."""
        if self._updating or self.profile_combo.currentText() != "Custom":
            return
        self._apply_project_size(
            "Custom",
            self.project_width_spin.value(),
            self.project_height_spin.value(),
        )

    def _apply_project_size(self, profile: str, width: int, height: int) -> None:
        """Apply profile dimensions to the whole GUI project."""
        project = self.session.project
        project.profile = profile
        project.width = width
        project.height = height
        for screen in project.screens:
            screen.width = width
            screen.height = height
            for element in screen.elements:
                element.x = max(0, min(element.x, width - 1))
                element.y = max(0, min(element.y, height - 1))
                element.width = max(1, min(element.width, width - element.x))
                element.height = max(1, min(element.height, height - element.y))
        self.session.mark_changed()

    def _screen_properties_changed(self) -> None:
        """Apply active screen name changes."""
        if self._updating:
            return
        screen = self.session.current_screen()
        name = self.screen_name_edit.text().strip()
        if name:
            screen.name = name
            self.session.mark_changed()

    def _element_properties_changed(self) -> None:
        """Apply property controls to the selected element."""
        if self._updating:
            return
        element = self._selected_element()
        if element is None or element.locked:
            return
        element.name = self.element_name_edit.text().strip() or element.name
        element.kind = str(self.kind_combo.currentData())
        element.x = self.x_spin.value()
        element.y = self.y_spin.value()
        element.width = self.width_spin.value()
        element.height = self.height_spin.value()
        element.text = self.element_text_edit.text().replace("\\n", "\n")
        element.asset_call = self.asset_call_edit.text().strip()
        element.visible = self.visible_check.isChecked()
        self.session.mark_changed()

    def _choose_screen_background(self) -> None:
        """Choose the active screen background color."""
        screen = self.session.current_screen()
        chosen = QColorDialog.getColor(
            qcolor_from_rgb565(screen.background_color), self
        )
        if chosen.isValid():
            screen.background_color = rgb_to_rgb565(
                chosen.red(), chosen.green(), chosen.blue()
            )
            self.session.mark_changed()

    def _choose_element_color(self, field_name: str) -> None:
        """Choose one selected-element RGB565 color."""
        element = self._selected_element()
        if element is None:
            return
        chosen = QColorDialog.getColor(
            qcolor_from_rgb565(getattr(element, field_name)), self
        )
        if chosen.isValid():
            setattr(
                element,
                field_name,
                rgb_to_rgb565(chosen.red(), chosen.green(), chosen.blue()),
            )
            self.session.mark_changed()

    def _update_color_buttons(self, element: GuiElement) -> None:
        """Update selected-element color button swatches."""
        for button, color in (
            (self.fill_color_button, element.fill_color),
            (self.border_color_button, element.border_color),
            (self.text_color_button, element.text_color),
        ):
            button.setStyleSheet(f"background: {qcolor_from_rgb565(color).name()};")

    def _open_reference(self) -> None:
        """Open a tracing image behind the active GUI screen."""
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Open screen reference",
            str(Path.cwd()),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)",
        )
        if not filename:
            return
        frames = read_image_frames(filename)
        if frames:
            self.canvas.set_reference(frames[0])


class GuiPreview(QWidget):
    """Render the active simulator screen at display aspect ratio."""

    def __init__(self, session: DesignerSession, parent: QWidget | None = None):
        """Initialize the GUI flow preview."""
        super().__init__(parent)
        self.session = session
        self.preview_screen_id = session.project.start_screen_id
        self.setMinimumSize(260, 220)

    def set_screen(self, screen_id: str) -> None:
        """Set the screen shown in the simulator preview."""
        self.preview_screen_id = screen_id
        self.update()

    def paintEvent(self, event) -> None:
        """Paint the simulated active screen."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#202020"))
        screen = self.session.project.screen(self.preview_screen_id)
        if screen is not None:
            draw_screen(painter, screen, QRectF(self.rect()).adjusted(10, 10, -10, -10))
        painter.end()


class FlowCanvas(QWidget):
    """Draw and directly arrange screen nodes and their relationships."""

    screen_selected = Signal(str)
    screen_activated = Signal(str)
    connection_requested = Signal(str, str)

    NODE_WIDTH = 160
    NODE_HEIGHT = 70
    PORT_RADIUS = 7

    def __init__(self, session: DesignerSession, parent: QWidget | None = None):
        """Initialize the draggable screen graph."""
        super().__init__(parent)
        self.session = session
        self.selected_screen_id: str | None = None
        self.zoom = 1.0
        self._drag_offset = QPointF()
        self._node_dragging = False
        self._connection_source_id: str | None = None
        self._connection_point = QPointF()
        self._connection_target_id: str | None = None
        self.setMouseTracking(True)
        self.setMinimumSize(1400, 900)

    def paintEvent(self, event) -> None:
        """Paint graph connections followed by screen nodes."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#282c32"))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.scale(self.zoom, self.zoom)
        project = self.session.project
        for connection in project.connections:
            source = project.screen(connection.source_id)
            target = project.screen(connection.target_id)
            if source is not None and target is not None:
                self._draw_connection(painter, source, target, connection)
        source = project.screen(self._connection_source_id or "")
        if source is not None:
            self._draw_connection_preview(painter, source)
        for screen in project.screens:
            self._draw_node(painter, screen)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Begin a node move or connection drag."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = self._graph_point(event.position())
        output_screen = self._output_screen_at(point)
        if output_screen is not None:
            self.selected_screen_id = output_screen.id
            self._connection_source_id = output_screen.id
            self._connection_point = point
            self._connection_target_id = None
            self._node_dragging = False
            self.screen_selected.emit(output_screen.id)
            self.update()
            event.accept()
            return
        screen = self._screen_at(point)
        if screen is None:
            self.selected_screen_id = None
            self._node_dragging = False
            self.update()
            return
        self.selected_screen_id = screen.id
        self._node_dragging = True
        self._drag_offset = QPointF(
            point.x() - screen.node_x, point.y() - screen.node_y
        )
        self.screen_selected.emit(screen.id)
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Move a node or preview a dragged connection."""
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        point = self._graph_point(event.position())
        if self._connection_source_id is not None:
            target = self._connection_target_at(point)
            self._connection_target_id = target.id if target is not None else None
            self._connection_point = (
                self._input_port(target) if target is not None else point
            )
            self.update()
            return
        if not self._node_dragging:
            return
        screen = self.session.project.screen(self.selected_screen_id or "")
        if screen is None:
            return
        screen.node_x = max(10, round(point.x() - self._drag_offset.x()))
        screen.node_y = max(10, round(point.y() - self._drag_offset.y()))
        self.session.mark_changed(False)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Finish a node move or create the dragged connection."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        source_id = self._connection_source_id
        target_id = self._connection_target_id
        self._connection_source_id = None
        self._connection_target_id = None
        self._connection_point = QPointF()
        self._node_dragging = False
        self.update()
        if source_id and target_id:
            self.connection_requested.emit(source_id, target_id)
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Open a double-clicked screen in the GUI designer."""
        point = self._graph_point(event.position())
        screen = self._screen_at(point)
        if screen is not None:
            self.screen_activated.emit(screen.id)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Zoom the node graph with the mouse wheel."""
        delta = event.angleDelta().y()
        if not delta:
            super().wheelEvent(event)
            return
        self.zoom = max(0.5, min(2.0, self.zoom + (0.1 if delta > 0 else -0.1)))
        self.update()
        event.accept()

    def _draw_node(self, painter: QPainter, screen: ScreenDesign) -> None:
        """Draw one screen node."""
        rectangle = QRectF(
            screen.node_x, screen.node_y, self.NODE_WIDTH, self.NODE_HEIGHT
        )
        selected = screen.id == self.selected_screen_id
        start = screen.id == self.session.project.start_screen_id
        color = QColor("#43a047") if start else QColor("#4c566a")
        painter.setBrush(color)
        painter.setPen(
            QPen(
                QColor("#00bfff") if selected else QColor("#d8dee9"),
                3 if selected else 1,
            )
        )
        painter.drawRoundedRect(rectangle, 8, 8)
        painter.setPen(QColor("white"))
        painter.drawText(
            rectangle.adjusted(8, 8, -8, -8), Qt.AlignmentFlag.AlignCenter, screen.name
        )
        if screen.source_path:
            painter.drawText(QPointF(screen.node_x + 6, screen.node_y + 62), "SOURCE")
        if start:
            painter.drawText(QPointF(screen.node_x + 6, screen.node_y + 14), "START")
        input_color = (
            QColor("#ebcb8b")
            if screen.id == self._connection_target_id
            else QColor("#a3be8c")
        )
        painter.setPen(QPen(QColor("#20242a"), 1))
        painter.setBrush(input_color)
        painter.drawEllipse(
            self._input_port(screen), self.PORT_RADIUS, self.PORT_RADIUS
        )
        painter.setBrush(QColor("#5e81ac"))
        painter.drawEllipse(
            self._output_port(screen), self.PORT_RADIUS, self.PORT_RADIUS
        )

    def _draw_connection(
        self,
        painter: QPainter,
        source: ScreenDesign,
        target: ScreenDesign,
        connection: FlowConnection,
    ) -> None:
        """Draw one labeled directional graph edge."""
        start = self._output_port(source)
        end = self._input_port(target)
        path, approach = self._connection_path(start, end)
        edge_color = QColor("#ff9800") if connection.locked else QColor("#88c0d0")
        painter.setPen(QPen(edge_color, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        direction = end - approach
        length = max(1.0, (direction.x() ** 2 + direction.y() ** 2) ** 0.5)
        unit = QPointF(direction.x() / length, direction.y() / length)
        normal = QPointF(-unit.y(), unit.x())
        arrow = QPolygonF(
            [
                end,
                end - unit * 12 + normal * 5,
                end - unit * 12 - normal * 5,
            ]
        )
        painter.setBrush(edge_color)
        painter.drawPolygon(arrow)
        midpoint = path.pointAtPercent(0.5)
        painter.setPen(QColor("#eceff4"))
        painter.drawText(midpoint + QPointF(4, -5), connection.trigger)

    def _draw_connection_preview(self, painter: QPainter, source: ScreenDesign) -> None:
        """Draw the temporary edge while the mouse chooses a target."""
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#00bfff"), 2, Qt.PenStyle.DashLine))
        painter.drawLine(self._output_port(source), self._connection_point)

    def _connection_path(
        self, start: QPointF, end: QPointF
    ) -> tuple[QPainterPath, QPointF]:
        """Return a readable curved edge and its final approach point."""
        gap = end.x() - start.x()
        handle = max(30.0, min(120.0, abs(gap) * 0.5))
        if gap < 0:
            vertical_offset = 90.0
            first = QPointF(start.x() + 80, start.y() + vertical_offset)
            second = QPointF(end.x() - 80, end.y() + vertical_offset)
        else:
            vertical_offset = -70.0 if gap > self.NODE_WIDTH else 0.0
            first = QPointF(start.x() + handle, start.y() + vertical_offset)
            second = QPointF(end.x() - handle, end.y() + vertical_offset)
        path = QPainterPath(start)
        path.cubicTo(first, second, end)
        return path, second

    def _screen_at(self, point: QPointF) -> ScreenDesign | None:
        """Return the topmost graph node at one point."""
        for screen in reversed(self.session.project.screens):
            if QRectF(
                screen.node_x,
                screen.node_y,
                self.NODE_WIDTH,
                self.NODE_HEIGHT,
            ).contains(point):
                return screen
        return None

    def _output_screen_at(self, point: QPointF) -> ScreenDesign | None:
        """Return the screen whose output port contains the point."""
        for screen in reversed(self.session.project.screens):
            port = self._output_port(screen)
            if (point.x() - port.x()) ** 2 + (point.y() - port.y()) ** 2 <= (
                self.PORT_RADIUS + 5
            ) ** 2:
                return screen
        return None

    def _connection_target_at(self, point: QPointF) -> ScreenDesign | None:
        """Return a connection target below an input port or node body."""
        for screen in reversed(self.session.project.screens):
            port = self._input_port(screen)
            if (point.x() - port.x()) ** 2 + (point.y() - port.y()) ** 2 <= (
                self.PORT_RADIUS + 7
            ) ** 2:
                return screen
        return self._screen_at(point)

    def _input_port(self, screen: ScreenDesign) -> QPointF:
        """Return the center of a screen node input port."""
        return QPointF(screen.node_x, screen.node_y + self.NODE_HEIGHT / 2)

    def _output_port(self, screen: ScreenDesign) -> QPointF:
        """Return the center of a screen node output port."""
        return QPointF(
            screen.node_x + self.NODE_WIDTH,
            screen.node_y + self.NODE_HEIGHT / 2,
        )

    def _graph_point(self, point: QPointF) -> QPointF:
        """Convert widget coordinates into zoomed graph coordinates."""
        return QPointF(point.x() / self.zoom, point.y() / self.zoom)


class ScreenFlowWidget(QWidget):
    """Edit screen relationships and simulate navigation events."""

    open_screen_requested = Signal(str)

    def __init__(self, session: DesignerSession, parent: QWidget | None = None):
        """Build the node graph and relationship controls."""
        super().__init__(parent)
        self.session = session
        self._updating = False
        self.simulated_screen_id = session.project.start_screen_id
        self._build_interface()
        self._connect_signals()
        self.refresh()

    def _build_interface(self) -> None:
        """Build graph, relationship editor, and simulator."""
        layout = QHBoxLayout(self)
        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        relation_group = QGroupBox("Screen relation")
        relation_form = QFormLayout(relation_group)
        connection_hint = QLabel(
            "Drag from a node's blue right port to another node's green left port."
        )
        connection_hint.setWordWrap(True)
        relation_form.addRow(connection_hint)
        self.source_combo = QComboBox()
        self.target_combo = QComboBox()
        self.trigger_edit = QLineEdit("select")
        self.condition_edit = QLineEdit()
        self.condition_edit.setPlaceholderText("Optional condition name")
        self.action_edit = QLineEdit()
        self.action_edit.setPlaceholderText("Optional action name")
        self.transition_combo = QComboBox()
        self.transition_combo.addItems(("replace", "push", "modal", "back"))
        relation_form.addRow("From", self.source_combo)
        relation_form.addRow("To", self.target_combo)
        relation_form.addRow("Trigger", self.trigger_edit)
        relation_form.addRow("Condition", self.condition_edit)
        relation_form.addRow("Action", self.action_edit)
        relation_form.addRow("Transition", self.transition_combo)
        self.add_relation_button = QPushButton("Add relation")
        relation_form.addRow(self.add_relation_button)
        controls_layout.addWidget(relation_group)
        controls_layout.addWidget(QLabel("Relations"))
        self.connection_list = QListWidget()
        controls_layout.addWidget(self.connection_list, 1)
        relation_buttons = QHBoxLayout()
        self.update_relation_button = QPushButton("Update")
        self.delete_relation_button = QPushButton("Delete")
        relation_buttons.addWidget(self.update_relation_button)
        relation_buttons.addWidget(self.delete_relation_button)
        controls_layout.addLayout(relation_buttons)
        self.start_screen_button = QPushButton("Set selected as start")
        self.open_screen_button = QPushButton("Open selected screen")
        controls_layout.addWidget(self.start_screen_button)
        controls_layout.addWidget(self.open_screen_button)
        simulator_group = QGroupBox("Navigation simulator")
        simulator_layout = QVBoxLayout(simulator_group)
        self.simulator_label = QLabel()
        self.simulator_event_edit = QLineEdit()
        self.simulator_event_edit.setPlaceholderText("Enter event trigger")
        simulator_buttons = QHBoxLayout()
        self.send_event_button = QPushButton("Send event")
        self.reset_simulator_button = QPushButton("Reset")
        simulator_buttons.addWidget(self.send_event_button)
        simulator_buttons.addWidget(self.reset_simulator_button)
        self.simulator_result_label = QLabel("Ready")
        self.simulator_result_label.setWordWrap(True)
        simulator_layout.addWidget(self.simulator_label)
        simulator_layout.addWidget(self.simulator_event_edit)
        simulator_layout.addLayout(simulator_buttons)
        simulator_layout.addWidget(self.simulator_result_label)
        controls_layout.addWidget(simulator_group)
        controls.setMaximumWidth(340)
        layout.addWidget(controls)

        graph_splitter = QSplitter(Qt.Orientation.Vertical)
        graph_scroll = QScrollArea()
        self.graph = FlowCanvas(self.session)
        graph_scroll.setWidget(self.graph)
        graph_scroll.setWidgetResizable(False)
        graph_splitter.addWidget(graph_scroll)
        self.preview = GuiPreview(self.session)
        graph_splitter.addWidget(self.preview)
        graph_splitter.setSizes((620, 260))
        layout.addWidget(graph_splitter, 1)

    def _connect_signals(self) -> None:
        """Connect graph controls and simulator events."""
        self.session.project_changed.connect(self.refresh)
        self.graph.screen_selected.connect(self._graph_screen_selected)
        self.graph.screen_activated.connect(self._open_screen)
        self.graph.connection_requested.connect(self._graph_connection_requested)
        self.add_relation_button.clicked.connect(self._add_relation)
        self.update_relation_button.clicked.connect(self._update_relation)
        self.delete_relation_button.clicked.connect(self._delete_relation)
        self.connection_list.currentRowChanged.connect(self._connection_selected)
        self.start_screen_button.clicked.connect(self._set_start_screen)
        self.open_screen_button.clicked.connect(self._open_selected_screen)
        self.send_event_button.clicked.connect(self._send_simulator_event)
        self.reset_simulator_button.clicked.connect(self._reset_simulator)
        self.simulator_event_edit.returnPressed.connect(self._send_simulator_event)

    def refresh(self) -> None:
        """Refresh graph controls from the shared project."""
        self._updating = True
        selected_source = self.source_combo.currentData()
        selected_target = self.target_combo.currentData()
        self.source_combo.clear()
        self.target_combo.clear()
        for screen in self.session.project.screens:
            self.source_combo.addItem(screen.name, screen.id)
            self.target_combo.addItem(screen.name, screen.id)
        self._restore_combo(self.source_combo, selected_source)
        self._restore_combo(self.target_combo, selected_target)
        self.connection_list.clear()
        self.update_relation_button.setEnabled(True)
        self.delete_relation_button.setEnabled(True)
        self._set_relation_fields_enabled(True, False)
        for connection in self.session.project.connections:
            source = self.session.project.screen(connection.source_id)
            target = self.session.project.screen(connection.target_id)
            if source is None or target is None:
                continue
            prefix = "[code] " if connection.source_path else ""
            if connection.locked:
                prefix = "[locked] "
            item = QListWidgetItem(
                f"{prefix}{source.name} -- {connection.trigger} --> {target.name}"
            )
            item.setData(Qt.ItemDataRole.UserRole, connection.id)
            if connection.source_path:
                item.setToolTip(f"{connection.source_path}:{connection.source_line}")
            self.connection_list.addItem(item)
        if self.session.project.screen(self.simulated_screen_id) is None:
            self.simulated_screen_id = self.session.project.start_screen_id
        self._update_simulator()
        self.graph.update()
        self._updating = False

    def _restore_combo(self, combo: QComboBox, value: object) -> None:
        """Restore a combo selection by item data."""
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    def _add_relation(self) -> None:
        """Add a relationship from the editor controls."""
        source_id = str(self.source_combo.currentData())
        target_id = str(self.target_combo.currentData())
        trigger = self.trigger_edit.text().strip()
        if not source_id or not target_id or not trigger:
            QMessageBox.information(
                self, "Incomplete relation", "Choose screens and enter a trigger."
            )
            return
        self._create_relation(source_id, target_id, trigger)

    def _create_relation(self, source_id: str, target_id: str, trigger: str) -> None:
        """Create one design relation from form or mouse-selected nodes."""
        duplicate = next(
            (
                connection
                for connection in self.session.project.connections
                if connection.source_id == source_id
                and connection.target_id == target_id
                and connection.trigger == trigger
            ),
            None,
        )
        if duplicate is not None:
            self._select_connection_id(duplicate.id)
            return
        connection = FlowConnection.create(source_id, target_id, trigger)
        connection.condition = self.condition_edit.text().strip()
        connection.action = self.action_edit.text().strip()
        connection.transition = self.transition_combo.currentText()
        self.session.project.connections.append(connection)
        self.session.mark_changed()
        self._select_connection_id(connection.id)

    def _graph_connection_requested(self, source_id: str, target_id: str) -> None:
        """Create a relation from a mouse-drawn graph connection."""
        self._restore_combo(self.source_combo, source_id)
        self._restore_combo(self.target_combo, target_id)
        trigger = self.trigger_edit.text().strip() or "select"
        self.trigger_edit.setText(trigger)
        self._create_relation(source_id, target_id, trigger)

    def _select_connection_id(self, connection_id: str) -> None:
        """Select a relation list item by project identifier."""
        for row in range(self.connection_list.count()):
            item = self.connection_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == connection_id:
                self.connection_list.setCurrentRow(row)
                return

    def _update_relation(self) -> None:
        """Update the selected relationship."""
        connection = self._selected_connection()
        if connection is None or connection.locked:
            return
        target_id = str(self.target_combo.currentData())
        target = self.session.project.screen(target_id)
        if connection.source_path and (target is None or target.source_state is None):
            QMessageBox.information(
                self,
                "Target is design-only",
                "An imported relation needs a target with a source state.",
            )
            return
        connection.target_id = target_id
        connection.trigger = self.trigger_edit.text().strip() or connection.trigger
        if not connection.source_path:
            connection.source_id = str(self.source_combo.currentData())
            connection.condition = self.condition_edit.text().strip()
            connection.action = self.action_edit.text().strip()
            connection.transition = self.transition_combo.currentText()
        self.session.mark_changed()

    def _delete_relation(self) -> None:
        """Delete the selected relationship."""
        connection = self._selected_connection()
        if connection is None:
            return
        if connection.source_path:
            QMessageBox.information(
                self,
                "Source relation retained",
                "Imported source relations cannot be deleted automatically.",
            )
            return
        self.session.project.connections = [
            item
            for item in self.session.project.connections
            if item.id != connection.id
        ]
        self.session.mark_changed()

    def _connection_selected(self, row: int) -> None:
        """Load one relationship into the editor controls."""
        if self._updating or row < 0:
            return
        connection = self._selected_connection()
        if connection is None:
            return
        self.update_relation_button.setEnabled(not connection.locked)
        self.delete_relation_button.setEnabled(not bool(connection.source_path))
        self._restore_combo(self.source_combo, connection.source_id)
        self._restore_combo(self.target_combo, connection.target_id)
        self.trigger_edit.setText(connection.trigger)
        self.condition_edit.setText(connection.condition)
        self.action_edit.setText(connection.action)
        self.transition_combo.setCurrentText(connection.transition)
        source_backed = bool(connection.source_path)
        self._set_relation_fields_enabled(not connection.locked, source_backed)

    def _set_relation_fields_enabled(self, editable: bool, source_backed: bool) -> None:
        """Limit relation controls to fields that can reach source code."""
        self.source_combo.setEnabled(editable and not source_backed)
        self.target_combo.setEnabled(editable)
        self.trigger_edit.setEnabled(editable)
        self.condition_edit.setEnabled(editable and not source_backed)
        self.action_edit.setEnabled(editable and not source_backed)
        self.transition_combo.setEnabled(editable and not source_backed)

    def _selected_connection(self) -> FlowConnection | None:
        """Return the selected graph relationship."""
        item = self.connection_list.currentItem()
        if item is None:
            return None
        connection_id = item.data(Qt.ItemDataRole.UserRole)
        return next(
            (
                connection
                for connection in self.session.project.connections
                if connection.id == connection_id
            ),
            None,
        )

    def _graph_screen_selected(self, screen_id: str) -> None:
        """Load a selected graph node as relation source."""
        self._restore_combo(self.source_combo, screen_id)

    def _set_start_screen(self) -> None:
        """Set the selected graph screen as project start."""
        screen_id = self.graph.selected_screen_id
        if screen_id and self.session.project.screen(screen_id):
            self.session.project.start_screen_id = screen_id
            self.session.mark_changed()
            self._reset_simulator()

    def _open_selected_screen(self) -> None:
        """Open the selected graph node in the screen designer."""
        if self.graph.selected_screen_id:
            self._open_screen(self.graph.selected_screen_id)

    def _open_screen(self, screen_id: str) -> None:
        """Request navigation to a screen in the GUI designer."""
        self.session.set_active_screen(screen_id)
        self.open_screen_requested.emit(screen_id)

    def _send_simulator_event(self) -> None:
        """Send one event through the navigation graph."""
        event = self.simulator_event_edit.text().strip()
        if not event:
            return
        connection = next(
            (
                item
                for item in self.session.project.connections
                if item.source_id == self.simulated_screen_id and item.trigger == event
            ),
            None,
        )
        if connection is None:
            self.simulator_result_label.setText(f"No transition handles {event!r}.")
            return
        self.simulated_screen_id = connection.target_id
        result = f"Transition: {connection.transition}"
        if connection.condition:
            result += f" | condition: {connection.condition}"
        if connection.action:
            result += f" | action: {connection.action}"
        self.simulator_result_label.setText(result)
        self._update_simulator()

    def _reset_simulator(self) -> None:
        """Reset navigation simulation to the start screen."""
        self.simulated_screen_id = self.session.project.start_screen_id
        self.simulator_result_label.setText("Ready")
        self._update_simulator()

    def _update_simulator(self) -> None:
        """Refresh simulator label and visual preview."""
        screen = self.session.project.screen(self.simulated_screen_id)
        name = screen.name if screen is not None else "Missing screen"
        self.simulator_label.setText(f"Current screen: {name}")
        self.preview.set_screen(self.simulated_screen_id)
