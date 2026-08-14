"""Qt screen designer and navigation graph workspaces."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, field
import ast
import hashlib
import json
from math import ceil, sqrt
import os
from pathlib import Path

from PySide6.QtCore import (
    QItemSelectionModel,
    QMimeData,
    QPoint,
    QPointF,
    QRectF,
    QSettings,
    QSize,
    QStandardPaths,
    QTimer,
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
    QIcon,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QWheelEvent,
)
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
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .canvas import pixel_art_image, qcolor_from_rgb565
from .asset_library import LibraryAsset
from .standard_library import THEME_NAMES, is_standard_asset_id, standard_asset_metadata
from .behavior_operations import OPERATIONS, operation_spec, operations_for_kind
from .behavior_runtime import (
    BehaviorRuntime,
    BehaviorRuntimeError,
    _widget_event_payload,
)
from .designer_model import (
    FLOW_NODE_KINDS,
    DEVICE_PROFILES,
    ELEMENT_KINDS,
    BehaviorConnection,
    FlowDiagnostic,
    FlowConnection,
    FlowGroup,
    FlowNode,
    FlowPort,
    GuiElement,
    GuiProject,
    ProjectAsset,
    ScreenDesign,
    asset_element_runtime_scale,
    bake_asset_element,
    behavior_connection_error,
    flow_diagnostics,
    flow_stub_name,
    invalid_asset_scale_elements,
    new_identifier,
    preview_flow_node_kind_change,
    preview_flow_node_port_change,
    stable_identifier,
)
from .flow_library import FlowFragmentLibrary
from .generated_app import (
    ASSET_STORAGE_COMBINED,
    ASSET_STORAGE_INDIVIDUAL,
    build_live_preview_bundle,
    resolve_generated_app_paths,
)
from .live_simulator import (
    SIMULATOR_BOARDS,
    LiveSimulatorConfig,
    LiveSimulatorController,
    LiveSimulatorView,
)
from .model import PixelArt, rgb_to_rgb565
from .native_widgets import (
    NATIVE_WIDGET_IDS,
    NATIVE_WIDGET_SPECS,
    element_supports_ui_operation,
    native_widget_spec,
)
from .reference import prepare_reference_image, read_image_frames
from .thumbnail_cache import cached_pixel_art_pixmap, cached_pixel_frame_pixmap
from .ui_help import (
    install_widget_tooltips,
    set_collapsible_group_expanded,
    set_widget_tooltip,
)


ELEMENT_MIME_TYPE = "application/x-pico-gui-element"
PIXEL_ASSET_MIME_TYPE = "application/x-pico-gui-pixel-asset"
FLOW_ELEMENT_SEPARATOR = "::element::"
FOCUS_STYLES = (
    ("Outline", "outline"),
    ("Corner brackets", "corners"),
    ("Underline", "underline"),
    ("Hidden", "none"),
)


def _safe_native_widget_spec(widget_id: str):
    """Return a renderable native spec while preserving unknown model values."""
    return native_widget_spec(widget_id if widget_id in NATIVE_WIDGET_IDS else "menu")


def _is_screen_widget(element: GuiElement) -> bool:
    """Return whether an element is a Picoware screen-owning widget."""
    return bool(
        element.kind == "native"
        and element.native_widget in NATIVE_WIDGET_IDS
        and native_widget_spec(element.native_widget).full_screen
    )


def _element_has_behavior_value(element: GuiElement) -> bool:
    """Return whether an element exposes a meaningful public behavior value."""
    return element_supports_ui_operation(
        "ui.read_value",
        element.kind,
        element.native_widget,
        focusable=element.focusable,
    )


def _element_emits_behavior_event(element: GuiElement) -> bool:
    """Return whether activating an element can dispatch its stable event."""
    if element.kind != "native":
        return element.focusable
    return bool(
        element.native_widget in NATIVE_WIDGET_IDS
        and native_widget_spec(element.native_widget).emits_activation
    )


def _element_supports_operation(element: GuiElement, operation_id: str) -> bool:
    """Apply the shared generated-runtime capability contract in the editor."""
    return element_supports_ui_operation(
        operation_id,
        element.kind,
        element.native_widget,
        focusable=element.focusable,
    )


def _screen_alert_element(project: GuiProject, screen_id: str) -> GuiElement | None:
    """Return the sole native Alert that acknowledges a screen transition."""
    screen = project.screen(screen_id)
    if screen is None:
        return None
    alerts = [
        element
        for element in screen.elements
        if element.kind == "native" and element.native_widget == "alert"
    ]
    return alerts[0] if len(alerts) == 1 else None


@dataclass
class GuiPixelAsset:
    """Describe one source graphic offered to the GUI designer."""

    key: str
    name: str
    source_path: str
    function_name: str
    art: PixelArt
    fingerprint: str = ""
    frames: tuple[PixelArt, ...] = field(default_factory=tuple)
    durations: tuple[int, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Populate a stable content fingerprint when the caller omitted one."""
        if not self.frames:
            self.frames = (self.art.copy(),)
        first = self.frames[0]
        if any(
            frame.width != first.width
            or frame.height != first.height
            or frame.origin_x != first.origin_x
            or frame.origin_y != first.origin_y
            for frame in self.frames
        ):
            raise ValueError("GUI asset frames must share dimensions and origin")
        if self.durations and len(self.durations) != len(self.frames):
            raise ValueError("GUI asset durations must match its frames")
        self.art = first.copy()
        if not self.fingerprint:
            self.fingerprint = pixel_art_fingerprint(self.art)


def pixel_art_fingerprint(art: PixelArt) -> str:
    """Return a stable fingerprint for embedded RGB565 pixel content."""
    payload = [art.width, art.height, art.origin_x, art.origin_y, art.pixels]
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def project_asset_from_gui_asset(
    asset_id: str,
    asset: GuiPixelAsset,
    *,
    source_path: str = "",
    absolute_fallback: str = "",
    qualified_name: str = "",
    link_state: str = "detached",
) -> ProjectAsset:
    """Preserve every portable GUI asset frame in the project catalogue."""
    record = ProjectAsset(
        asset_id,
        asset.name,
        asset.art.width,
        asset.art.height,
        asset.art.origin_x,
        asset.art.origin_y,
        [list(frame.pixels) for frame in asset.frames],
        list(asset.durations),
        source_path,
        absolute_fallback,
        qualified_name,
        asset.fingerprint,
        link_state,
    )
    record.validate()
    return record


def flow_endpoint_key(screen_id: str, element_id: str = "") -> str:
    """Encode one screen or element endpoint for Qt combo item data."""
    if element_id:
        return f"{screen_id}{FLOW_ELEMENT_SEPARATOR}{element_id}"
    return screen_id


def parse_flow_endpoint(value: object) -> tuple[str, str]:
    """Decode a Qt combo endpoint into screen and optional element IDs."""
    text = str(value or "")
    if FLOW_ELEMENT_SEPARATOR not in text:
        return text, ""
    return tuple(text.split(FLOW_ELEMENT_SEPARATOR, 1))


class DesignerSession(QObject):
    """Share one editable GUI project between designer workspaces."""

    project_changed = Signal()
    dirty_changed = Signal(bool)
    active_screen_changed = Signal(str)
    history_changed = Signal(bool, bool)
    live_previews_changed = Signal()

    def __init__(self, parent: QObject | None = None):
        """Initialize a new unsaved GUI project."""
        super().__init__(parent)
        self.project = GuiProject.create()
        self.path: Path | None = None
        self.dirty = False
        self.active_screen_id = self.project.start_screen_id
        self._undo_stack: list[dict[str, object]] = []
        self._redo_stack: list[dict[str, object]] = []
        self._snapshot = self.project.to_dict()
        self._saved_snapshot = self.project.to_dict()
        self._transaction_snapshot: dict[str, object] | None = None
        self._transaction_depth = 0
        self.live_screen_images: dict[str, QImage] = {}

    def set_project(self, project: GuiProject, path: Path | None = None) -> None:
        """Replace the current project and reset edit state."""
        self.project = project
        self.path = path
        self.active_screen_id = project.start_screen_id
        self.dirty = False
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._snapshot = project.to_dict()
        self._saved_snapshot = project.to_dict()
        self._transaction_snapshot = None
        self._transaction_depth = 0
        self.live_screen_images.clear()
        self.project_changed.emit()
        self.active_screen_changed.emit(self.active_screen_id)
        self.dirty_changed.emit(False)
        self.history_changed.emit(False, False)
        self.live_previews_changed.emit()

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
        if self._transaction_depth == 0:
            current = self.project.to_dict()
            if current != self._snapshot:
                self._undo_stack.append(self._snapshot)
                del self._undo_stack[:-100]
                self._redo_stack.clear()
                self._snapshot = current
                self.history_changed.emit(self.can_undo(), self.can_redo())
        self._set_dirty(True)
        if refresh:
            self.project_changed.emit()

    def begin_transaction(self) -> None:
        """Begin one coalesced direct-manipulation history change."""
        if self._transaction_depth == 0:
            self._transaction_snapshot = self.project.to_dict()
        self._transaction_depth += 1

    def end_transaction(self) -> None:
        """Commit the current direct-manipulation history change."""
        if self._transaction_depth == 0:
            return
        self._transaction_depth -= 1
        if self._transaction_depth:
            return
        before = self._transaction_snapshot
        self._transaction_snapshot = None
        current = self.project.to_dict()
        if before is not None and current != before:
            self._undo_stack.append(before)
            del self._undo_stack[:-100]
            self._redo_stack.clear()
            self._snapshot = current
            self.history_changed.emit(self.can_undo(), self.can_redo())
        self._set_dirty(current != self._saved_snapshot)

    def can_undo(self) -> bool:
        """Return whether a designer change can be undone."""
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        """Return whether a designer change can be redone."""
        return bool(self._redo_stack)

    def undo(self) -> None:
        """Restore the previous complete designer project snapshot."""
        if not self._undo_stack:
            return
        current = self.project.to_dict()
        previous = self._undo_stack.pop()
        self._redo_stack.append(current)
        self._restore_snapshot(previous)

    def redo(self) -> None:
        """Restore the next complete designer project snapshot."""
        if not self._redo_stack:
            return
        current = self.project.to_dict()
        following = self._redo_stack.pop()
        self._undo_stack.append(current)
        self._restore_snapshot(following)

    def _restore_snapshot(self, values: dict[str, object]) -> None:
        """Restore one history snapshot and notify designer views."""
        active = self.active_screen_id
        self.project = GuiProject.from_dict(values)
        self.active_screen_id = (
            active
            if self.project.screen(active) is not None
            else self.project.start_screen_id
        )
        self._snapshot = self.project.to_dict()
        self._set_dirty(self._snapshot != self._saved_snapshot)
        self.project_changed.emit()
        self.active_screen_changed.emit(self.active_screen_id)
        self.history_changed.emit(self.can_undo(), self.can_redo())

    def _set_dirty(self, dirty: bool) -> None:
        """Update and emit the designer dirty state only when changed."""
        if self.dirty == dirty:
            return
        self.dirty = dirty
        self.dirty_changed.emit(dirty)

    def save(self, path: str | Path | None = None) -> Path:
        """Save the project and return its path."""
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ValueError("A GUI project path is required")
        self.project.save(target)
        self.path = target
        self._snapshot = self.project.to_dict()
        self._saved_snapshot = self.project.to_dict()
        self._set_dirty(False)
        return target

    def set_live_screen_image(self, screen_id: str, image: QImage) -> None:
        """Associate a transient live simulator frame with one screen."""
        if self.project.screen(screen_id) is None or image.isNull():
            return
        self.live_screen_images[screen_id] = image.copy()
        self.live_previews_changed.emit()

    def clear_live_screen_image(self, screen_id: str) -> None:
        """Remove a transient live simulator frame from one screen."""
        if self.live_screen_images.pop(screen_id, None) is not None:
            self.live_previews_changed.emit()


def draw_screen(
    painter: QPainter,
    screen: ScreenDesign,
    target: QRectF,
    selected_id: str | set[str] | None = None,
    reference: QImage | None = None,
    reference_opacity: float = 0.45,
    focused_id: str | None = None,
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
    selected_ids = (
        {selected_id}
        if isinstance(selected_id, str)
        else selected_id
        if selected_id is not None
        else set()
    )
    for element in screen.elements:
        if element.visible:
            if not element.enabled:
                painter.setOpacity(0.45)
            draw_element(
                painter,
                element,
                element.id in selected_ids,
                element.id == focused_id,
            )
            painter.setOpacity(1.0)
    painter.restore()


def screen_preview_image(screen: ScreenDesign, size: QSize) -> QImage:
    """Render a screen into a compact opaque thumbnail image."""
    image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#20242a"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setPen(QPen(QColor("#59636f"), 1))
    painter.drawRect(QRectF(image.rect()).adjusted(0, 0, -1, -1))
    draw_screen(painter, screen, QRectF(image.rect()).adjusted(4, 4, -4, -4))
    painter.end()
    return image


def live_preview_image(source: QImage, size: QSize) -> QImage:
    """Fit a captured live frame into a compact opaque thumbnail."""
    image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#20242a"))
    painter = QPainter(image)
    target = QRectF(image.rect()).adjusted(4, 4, -4, -4)
    draw_fitted_image(painter, source, target)
    painter.setPen(QPen(QColor("#00bfff"), 1))
    painter.drawRect(QRectF(image.rect()).adjusted(0, 0, -1, -1))
    painter.end()
    return image


def draw_fitted_image(painter: QPainter, source: QImage, target: QRectF) -> None:
    """Draw one image inside a target while preserving its aspect ratio."""
    scale = min(target.width() / source.width(), target.height() / source.height())
    width = source.width() * scale
    height = source.height() * scale
    fitted = QRectF(
        target.x() + (target.width() - width) / 2,
        target.y() + (target.height() - height) / 2,
        width,
        height,
    )
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
    painter.drawImage(fitted, source)


def draw_element(
    painter: QPainter,
    element: GuiElement,
    selected: bool = False,
    focused: bool = False,
) -> None:
    """Draw one GUI element and its selection handles."""
    rectangle = QRectF(element.x, element.y, element.width, element.height)
    fill = qcolor_from_rgb565(element.fill_color)
    border = qcolor_from_rgb565(element.border_color)
    text_color = qcolor_from_rgb565(element.text_color)
    painter.setPen(QPen(border, 1))
    if element.kind == "native":
        draw_native_widget(painter, element, rectangle, fill, border, text_color)
    elif element.kind == "label":
        painter.setPen(text_color)
        painter.drawText(
            rectangle,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            element.text,
        )
    elif element.kind == "icon" and (
        element.asset_runs or (element.asset_width > 0 and element.asset_height > 0)
    ):
        draw_embedded_asset(painter, element)
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
        if not _is_screen_widget(element):
            painter.fillRect(
                QRectF(
                    element.x + element.width - 5,
                    element.y + element.height - 5,
                    7,
                    7,
                ),
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
    elif element.editor_locked:
        painter.setPen(QPen(QColor("#9aa3ad"), 1, Qt.PenStyle.DashLine))
        painter.drawRect(rectangle.adjusted(1, 1, -1, -1))
        painter.drawText(
            rectangle.adjusted(3, 2, -3, -2),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
            "LOCK",
        )
    if focused:
        draw_focus_indicator(painter, element)


def draw_native_widget(
    painter: QPainter,
    element: GuiElement,
    rectangle: QRectF,
    fill: QColor,
    border: QColor,
    text_color: QColor,
) -> None:
    """Draw a faithful compact preview of one Picoware-native widget."""
    widget = element.native_widget or "menu"
    painter.fillRect(rectangle, fill)
    painter.setPen(QPen(border, 1))
    painter.drawRect(rectangle)
    inner = rectangle.adjusted(6, 6, -6, -6)
    items = element.widget_items or ["Item"]
    selected = element.widget_selected_index % len(items)

    if widget == "menu":
        painter.setPen(text_color)
        painter.drawText(
            QRectF(inner.x(), inner.y(), inner.width(), 24),
            Qt.AlignmentFlag.AlignCenter,
            element.text or "Menu",
        )
        painter.drawLine(
            QPointF(inner.x() + 20, inner.y() + 27),
            QPointF(inner.right() - 20, inner.y() + 27),
        )
        _draw_native_items(
            painter, items, selected, inner.adjusted(0, 34, 0, 0), border, text_color
        )
    elif widget == "list":
        _draw_native_items(painter, items, selected, inner, border, text_color)
    elif widget == "textbox":
        painter.setPen(text_color)
        painter.drawText(
            inner,
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignTop
            | Qt.TextFlag.TextWordWrap,
            element.text,
        )
        painter.fillRect(
            QRectF(inner.right() - 3, inner.y(), 3, inner.height()), border
        )
    elif widget == "toggle":
        painter.setPen(text_color)
        painter.drawText(
            QRectF(inner.x(), inner.y(), inner.width() * 0.65, inner.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            element.text or "Setting",
        )
        switch = QRectF(inner.right() - 54, inner.center().y() - 12, 50, 24)
        painter.setBrush(border if element.widget_state else fill)
        painter.drawRoundedRect(switch, 12, 12)
        knob_x = switch.right() - 20 if element.widget_state else switch.left() + 4
        painter.fillRect(QRectF(knob_x, switch.y() + 4, 16, 16), text_color)
    elif widget == "toggle_list":
        states = list(element.widget_item_states)
        row_height = max(20.0, inner.height() / max(1, min(8, len(items))))
        for index, item in enumerate(items[:8]):
            row = QRectF(
                inner.x(), inner.y() + index * row_height, inner.width(), row_height
            )
            if index == selected:
                painter.fillRect(row, border)
            painter.setPen(fill if index == selected else text_color)
            painter.drawText(
                row.adjusted(4, 0, -58, 0), Qt.AlignmentFlag.AlignVCenter, item
            )
            painter.drawText(
                row.adjusted(row.width() - 54, 0, -4, 0),
                Qt.AlignmentFlag.AlignVCenter,
                "ON" if index < len(states) and states[index] else "OFF",
            )
    elif widget == "choice":
        painter.setPen(text_color)
        painter.drawText(
            QRectF(inner.x(), inner.y(), inner.width(), 24),
            Qt.AlignmentFlag.AlignCenter,
            element.text or "Choose",
        )
        _draw_native_items(
            painter, items, selected, inner.adjusted(0, 32, 0, 0), border, text_color
        )
    elif widget == "keyboard":
        painter.setPen(text_color)
        painter.drawText(
            QRectF(inner.x(), inner.y(), inner.width(), 22),
            Qt.AlignmentFlag.AlignCenter,
            element.text or "Enter text",
        )
        painter.drawRect(QRectF(inner.x(), inner.y() + 28, inner.width(), 34))
        keys = (
            "Q W E R T Y U I O P",
            "A S D F G H J K L",
            "Z X C V B N M",
            "SPACE        SAVE",
        )
        for index, row in enumerate(keys):
            painter.drawText(
                QRectF(inner.x(), inner.y() + 72 + index * 28, inner.width(), 22),
                Qt.AlignmentFlag.AlignCenter,
                row,
            )
    elif widget == "search_bar":
        painter.setPen(text_color)
        painter.drawRect(QRectF(inner.x(), inner.y(), inner.width(), 34))
        painter.drawText(
            QRectF(inner.x() + 6, inner.y(), inner.width() - 12, 34),
            Qt.AlignmentFlag.AlignVCenter,
            "Search…",
        )
        _draw_native_items(
            painter, items, selected, inner.adjusted(0, 44, 0, 0), border, text_color
        )
    elif widget == "loading":
        center = inner.center()
        painter.setPen(QPen(border, 4))
        painter.drawArc(
            QRectF(center.x() - 24, center.y() - 24, 48, 48), 30 * 16, 280 * 16
        )
        painter.setPen(text_color)
        painter.drawText(
            QRectF(inner.x(), inner.y(), inner.width(), 24),
            Qt.AlignmentFlag.AlignCenter,
            element.text or "Loading…",
        )
    elif widget == "alert":
        painter.setPen(text_color)
        painter.drawText(
            QRectF(inner.x(), inner.y(), inner.width(), 26),
            Qt.AlignmentFlag.AlignCenter,
            element.name or "Alert",
        )
        painter.drawLine(
            QPointF(inner.x() + 16, inner.y() + 29),
            QPointF(inner.right() - 16, inner.y() + 29),
        )
        painter.drawText(
            inner.adjusted(8, 40, -8, -8),
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignTop
            | Qt.TextFlag.TextWordWrap,
            element.text,
        )


def _draw_native_items(
    painter: QPainter,
    items: list[str],
    selected: int,
    rectangle: QRectF,
    selected_color: QColor,
    text_color: QColor,
) -> None:
    """Draw bounded selectable rows used by native widget previews."""
    visible = items[:8]
    row_height = max(18.0, rectangle.height() / max(1, len(visible)))
    for index, item in enumerate(visible):
        row = QRectF(
            rectangle.x(),
            rectangle.y() + index * row_height,
            rectangle.width(),
            row_height,
        )
        if index == selected:
            painter.fillRect(row, selected_color)
        painter.setPen(
            QColor("#000000")
            if index == selected and selected_color.lightness() > 128
            else text_color
        )
        painter.drawText(row.adjusted(6, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter, item)


_EMBEDDED_ASSET_CACHE_LIMIT = 32 * 1024 * 1024
_embedded_asset_cache: OrderedDict[tuple[object, ...], QImage] = OrderedDict()
_embedded_asset_cache_bytes = 0


def _embedded_asset_image(element: GuiElement) -> QImage:
    """Return a bounded cached source raster for one embedded pixel asset."""
    source_width = max(1, element.asset_width or element.width)
    source_height = max(1, element.asset_height or element.height)
    identity: object = (
        (
            element.asset_fingerprint,
            element.asset_frame,
        )
        if element.asset_fingerprint
        else (element.id, id(element.asset_runs))
    )
    key = (identity, source_width, source_height)
    cached = _embedded_asset_cache.get(key)
    if cached is not None:
        _embedded_asset_cache.move_to_end(key)
        return cached

    image = QImage(source_width, source_height, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    image_painter = QPainter(image)
    image_painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    for run in element.asset_runs:
        if len(run) != 4:
            continue
        run_x, run_y, run_width, color = (int(value) for value in run)
        if run_width < 1:
            continue
        image_painter.fillRect(
            run_x,
            run_y,
            run_width,
            1,
            qcolor_from_rgb565(color & 0xFFFF),
        )
    image_painter.end()

    global _embedded_asset_cache_bytes
    cost = source_width * source_height * 4
    _embedded_asset_cache[key] = image
    _embedded_asset_cache_bytes += cost
    while (
        _embedded_asset_cache_bytes > _EMBEDDED_ASSET_CACHE_LIMIT
        and len(_embedded_asset_cache) > 1
    ):
        unused_key, unused_image = _embedded_asset_cache.popitem(last=False)
        del unused_key
        _embedded_asset_cache_bytes -= unused_image.width() * unused_image.height() * 4
    return image


def draw_embedded_asset(painter: QPainter, element: GuiElement) -> None:
    """Draw one cached embedded pixel asset scaled with nearest-neighbor sampling."""
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
    painter.drawImage(
        QRectF(element.x, element.y, element.width, element.height),
        _embedded_asset_image(element),
    )


def draw_focus_indicator(painter: QPainter, element: GuiElement) -> None:
    """Draw one configured keyboard focus indicator."""
    style = element.focus_style
    if style == "none":
        return
    color = qcolor_from_rgb565(element.focus_color)
    thickness = max(1, min(6, element.focus_thickness))
    padding = max(0, min(12, element.focus_padding))
    if style == "underline":
        painter.fillRect(
            QRectF(
                element.x - padding,
                element.y + element.height + padding,
                element.width + padding * 2,
                thickness,
            ),
            color,
        )
        return
    painter.setPen(QPen(color, 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for offset in range(thickness):
        pad = padding + offset
        left = element.x - pad
        top = element.y - pad
        right = element.x + element.width + pad
        bottom = element.y + element.height + pad
        if style == "corners":
            segment = max(3, min(10, min(element.width, element.height) // 3))
            for start, end in (
                (QPointF(left, top), QPointF(left + segment, top)),
                (QPointF(left, top), QPointF(left, top + segment)),
                (QPointF(right - segment, top), QPointF(right, top)),
                (QPointF(right, top), QPointF(right, top + segment)),
                (QPointF(left, bottom), QPointF(left + segment, bottom)),
                (QPointF(left, bottom - segment), QPointF(left, bottom)),
                (QPointF(right - segment, bottom), QPointF(right, bottom)),
                (QPointF(right, bottom - segment), QPointF(right, bottom)),
            ):
                painter.drawLine(start, end)
        else:
            painter.drawRect(QRectF(left, top, right - left, bottom - top))


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


class PixelAssetList(QListWidget):
    """Provide draggable source pixel assets for GUI screens."""

    def startDrag(self, supported_actions) -> None:
        """Start a copy drag for the selected pixel asset."""
        item = self.currentItem()
        if item is None:
            return
        key = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not key:
            return
        mime = QMimeData()
        mime.setData(PIXEL_ASSET_MIME_TYPE, key.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(item.icon().pixmap(self.iconSize()))
        drag.exec(Qt.DropAction.CopyAction)


class DesignCanvas(QWidget):
    """Provide direct selection, dragging, and resizing of GUI elements."""

    element_selected = Signal(str)
    selection_changed = Signal(object)
    geometry_changed = Signal()
    zoom_changed = Signal(int)
    element_dropped = Signal(str, int, int)
    asset_dropped = Signal(str, int, int)
    delete_requested = Signal()
    duplicate_requested = Signal()

    def __init__(
        self,
        session: DesignerSession,
        parent: QWidget | None = None,
    ):
        """Initialize the screen design canvas."""
        super().__init__(parent)
        self.session = session
        self.selected_id: str | None = None
        self.selected_ids: set[str] = set()
        self.zoom_percent = 180
        self.reference: QImage | None = None
        self.reference_opacity = 45
        self.grid_visible = False
        self.snap_enabled = False
        self.grid_size = 8
        self.show_focus_order = False
        self._drag_mode = ""
        self._drag_offset = QPointF()
        self._drag_start_point = QPointF()
        self._drag_originals: dict[str, tuple[int, int]] = {}
        self._marquee_start = QPointF()
        self._marquee_end = QPointF()
        self._marquee_base: set[str] = set()
        self._drop_kind = ""
        self._drop_point = QPointF()
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._update_size()

    def set_selected(self, element_id: str | None) -> None:
        """Select one element for drawing and manipulation."""
        self.selected_id = element_id
        self.selected_ids = {element_id} if element_id else set()
        self.update()

    def set_selection(
        self, element_ids: set[str], primary_id: str | None = None
    ) -> None:
        """Set a multi-element canvas selection and its primary element."""
        valid = {
            element.id
            for element in self.session.current_screen().elements
            if element.id in element_ids
        }
        self.selected_ids = valid
        self.selected_id = (
            primary_id if primary_id in valid else next(iter(valid), None)
        )
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

    def set_grid_visible(self, visible: bool) -> None:
        """Show or hide the GUI alignment grid."""
        self.grid_visible = visible
        self.update()

    def set_snap_enabled(self, enabled: bool) -> None:
        """Enable or disable geometry snapping to the GUI grid."""
        self.snap_enabled = enabled

    def set_grid_size(self, size: int) -> None:
        """Set the GUI grid spacing in device pixels."""
        self.grid_size = max(2, min(64, size))
        self.update()

    def set_focus_order_visible(self, visible: bool) -> None:
        """Show or hide keyboard focus order badges."""
        self.show_focus_order = visible
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
            self.selected_ids,
            self.reference,
            self.reference_opacity / 100,
        )
        if self.grid_visible:
            factor = self.zoom_percent / 100
            spacing = self.grid_size * factor
            if spacing >= 4:
                painter.setPen(QPen(QColor(255, 255, 255, 35), 1))
                x = spacing
                while x < self.width():
                    painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
                    x += spacing
                y = spacing
                while y < self.height():
                    painter.drawLine(QPointF(0, y), QPointF(self.width(), y))
                    y += spacing
        if self.show_focus_order:
            factor = self.zoom_percent / 100
            painter.setPen(QPen(QColor("#ffffff"), 1))
            painter.setBrush(QColor("#1565c0"))
            for element in self.session.current_screen().elements:
                if not element.visible or not element.focusable:
                    continue
                center = QPointF(
                    (element.x + 7) * factor,
                    (element.y + 7) * factor,
                )
                radius = max(7.0, min(13.0, 8.0 * factor))
                painter.drawEllipse(center, radius, radius)
                painter.drawText(
                    QRectF(
                        center.x() - radius,
                        center.y() - radius,
                        radius * 2,
                        radius * 2,
                    ),
                    Qt.AlignmentFlag.AlignCenter,
                    str(element.focus_order),
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
        if self._drag_mode == "marquee":
            factor = self.zoom_percent / 100
            marquee = QRectF(
                self._marquee_start.x() * factor,
                self._marquee_start.y() * factor,
                (self._marquee_end.x() - self._marquee_start.x()) * factor,
                (self._marquee_end.y() - self._marquee_start.y()) * factor,
            ).normalized()
            painter.setBrush(QColor(0, 170, 255, 35))
            painter.setPen(QPen(QColor("#00aaff"), 1, Qt.PenStyle.DashLine))
            painter.drawRect(marquee)
        painter.end()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Accept supported GUI element palette drags."""
        if (
            self._drag_kind(event.mimeData()) is not None
            or self._drag_asset_key(event.mimeData()) is not None
        ):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        """Preview a supported element at its prospective drop point."""
        kind = self._drag_kind(event.mimeData())
        asset_key = self._drag_asset_key(event.mimeData())
        if kind is None and asset_key is None:
            event.ignore()
            return
        self._drop_kind = kind or "icon"
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
        asset_key = self._drag_asset_key(event.mimeData())
        if kind is None and asset_key is None:
            event.ignore()
            return
        point = self._design_point(event.position())
        self._clear_drop_preview()
        if asset_key is not None:
            self.asset_dropped.emit(
                asset_key,
                round(point.x()),
                round(point.y()),
            )
        else:
            self.element_dropped.emit(
                kind or "icon", round(point.x()), round(point.y())
            )
        event.acceptProposedAction()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Select an element and begin moving or resizing it."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = self._design_point(event.position())
        element = self._element_at(point)
        if element is None:
            self._drag_mode = "marquee"
            self._marquee_start = point
            self._marquee_end = point
            self._marquee_base = (
                set(self.selected_ids)
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                else set()
            )
            if not self._marquee_base:
                self.selected_id = None
                self.selected_ids.clear()
                self._emit_selection()
            self.update()
            return
        shifted = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if shifted:
            if element.id in self.selected_ids:
                self.selected_ids.remove(element.id)
                self.selected_id = next(iter(self.selected_ids), None)
                self._emit_selection()
                self.update()
                return
            self.selected_ids.add(element.id)
        elif element.id not in self.selected_ids:
            self.selected_ids = {element.id}
        self.selected_id = element.id
        self._emit_selection()
        if element.locked or element.editor_locked or _is_screen_widget(element):
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
            "resize"
            if len(self.selected_ids) == 1
            and can_resize
            and resize_area.contains(point)
            else "move"
        )
        self._drag_offset = QPointF(point.x() - element.x, point.y() - element.y)
        self._drag_start_point = point
        self._drag_originals = {
            item.id: (item.x, item.y)
            for item in self._selected_elements()
            if not item.locked and not item.editor_locked
        }
        self.session.begin_transaction()
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Move or resize the selected element."""
        if not self._drag_mode or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        point = self._design_point(event.position())
        if self._drag_mode == "marquee":
            self._marquee_end = point
            self.update()
            return
        element = self._selected_element()
        if element is None:
            return
        screen = self.session.current_screen()
        if self._drag_mode == "move":
            delta_x = round(point.x() - self._drag_start_point.x())
            delta_y = round(point.y() - self._drag_start_point.y())
            primary_origin = self._drag_originals.get(self.selected_id or "")
            if self.snap_enabled and primary_origin is not None:
                delta_x = (
                    self.snap_value(primary_origin[0] + delta_x) - primary_origin[0]
                )
                delta_y = (
                    self.snap_value(primary_origin[1] + delta_y) - primary_origin[1]
                )
            for item in self._selected_elements():
                origin = self._drag_originals.get(item.id)
                if origin is None:
                    continue
                item.x = max(0, min(screen.width - item.width, origin[0] + delta_x))
                item.y = max(0, min(screen.height - item.height, origin[1] + delta_y))
        else:
            width = round(point.x() - element.x)
            height = round(point.y() - element.y)
            if self.snap_enabled:
                width = self.snap_value(element.x + width) - element.x
                height = self.snap_value(element.y + height) - element.y
            element.width = max(1, min(screen.width - element.x, width))
            element.height = max(1, min(screen.height - element.y, height))
        self.session.mark_changed(False)
        self.geometry_changed.emit()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Finish the current geometry change."""
        if event.button() == Qt.MouseButton.LeftButton:
            if self._drag_mode == "marquee":
                area = QRectF(self._marquee_start, self._marquee_end).normalized()
                selected = set(self._marquee_base)
                selected.update(
                    element.id
                    for element in self.session.current_screen().elements
                    if element.visible
                    and area.intersects(
                        QRectF(element.x, element.y, element.width, element.height)
                    )
                )
                self.selected_ids = selected
                self.selected_id = next(iter(selected), None)
                self._emit_selection()
            if self._drag_mode:
                if self._drag_mode != "marquee":
                    self.session.end_transaction()
            self._drag_mode = ""
            self._drag_originals.clear()
            self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Apply common keyboard operations to selected elements."""
        if event.key() in {Qt.Key.Key_Delete, Qt.Key.Key_Backspace}:
            self.delete_requested.emit()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Copy):
            event.ignore()
            return
        if (
            event.key() == Qt.Key.Key_D
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.duplicate_requested.emit()
            event.accept()
            return
        directions = {
            Qt.Key.Key_Left: (-1, 0),
            Qt.Key.Key_Right: (1, 0),
            Qt.Key.Key_Up: (0, -1),
            Qt.Key.Key_Down: (0, 1),
        }
        direction = directions.get(event.key())
        if direction is None or not self.selected_ids:
            super().keyPressEvent(event)
            return
        step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        self._nudge_selection(direction[0] * step, direction[1] * step)
        event.accept()

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

    def snap_value(self, value: int) -> int:
        """Snap one device coordinate when grid snapping is enabled."""
        if not self.snap_enabled:
            return value
        return round(value / self.grid_size) * self.grid_size

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

    def _selected_elements(self) -> list[GuiElement]:
        """Return selected elements in screen drawing order."""
        return [
            element
            for element in self.session.current_screen().elements
            if element.id in self.selected_ids
        ]

    def _emit_selection(self) -> None:
        """Notify inspector views about the complete canvas selection."""
        self.element_selected.emit(self.selected_id or "")
        self.selection_changed.emit(set(self.selected_ids))

    def _nudge_selection(self, delta_x: int, delta_y: int) -> None:
        """Move unlocked selected elements by a keyboard delta."""
        screen = self.session.current_screen()
        changed = False
        for element in self._selected_elements():
            if element.locked or element.editor_locked or _is_screen_widget(element):
                continue
            x = max(0, min(screen.width - element.width, element.x + delta_x))
            y = max(0, min(screen.height - element.height, element.y + delta_y))
            if (x, y) != (element.x, element.y):
                element.x, element.y = x, y
                changed = True
        if changed:
            self.session.mark_changed(False)
            self.geometry_changed.emit()
            self.update()

    def _drag_kind(self, mime: QMimeData) -> str | None:
        """Return a valid GUI element kind from drag data."""
        if not mime.hasFormat(ELEMENT_MIME_TYPE):
            return None
        kind = bytes(mime.data(ELEMENT_MIME_TYPE)).decode("utf-8", "ignore")
        return kind if kind in ELEMENT_KINDS else None

    def _drag_asset_key(self, mime: QMimeData) -> str | None:
        """Return a source pixel asset key from drag data."""
        if not mime.hasFormat(PIXEL_ASSET_MIME_TYPE):
            return None
        key = bytes(mime.data(PIXEL_ASSET_MIME_TYPE)).decode("utf-8", "ignore")
        return key or None

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

    pixel_asset_edit_requested = Signal(str)
    project_asset_edit_requested = Signal(str, int)
    library_asset_delete_requested = Signal(str)
    library_asset_rename_requested = Signal(str)
    library_element_save_requested = Signal(str)
    library_manage_requested = Signal()
    preview_requested = Signal()
    design_preview_requested = Signal()
    flow_edit_requested = Signal(str, str)
    starter_requested = Signal()

    def __init__(
        self,
        session: DesignerSession,
        parent: QWidget | None = None,
    ):
        """Build the screen designer workspace."""
        super().__init__(parent)
        self.session = session
        self.selected_element_id: str | None = None
        self.selected_element_ids: set[str] = set()
        self.pixel_assets: dict[str, GuiPixelAsset] = {}
        self.library_assets: dict[str, GuiPixelAsset] = {}
        self.library_records: dict[str, LibraryAsset] = {}
        self._library_items: dict[str, QListWidgetItem] = {}
        self._quick_thumbnail_generation = 0
        self._quick_thumbnail_queue: list[str] = []
        self.settings = QSettings("Picoware", "PicoGraphicsEditor")
        self._updating = False
        self._fitting_canvas = False
        self._build_interface()
        self._connect_signals()
        self._configure_tab_order()
        self.refresh()
        install_widget_tooltips(self)

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
        self.asset_storage_combo = QComboBox()
        self.asset_storage_combo.addItem("Combined PGA3", ASSET_STORAGE_COMBINED)
        self.asset_storage_combo.addItem("Individual files", ASSET_STORAGE_INDIVIDUAL)
        self.asset_storage_combo.setToolTip(
            "Choose how generated device assets are deployed.\n"
            "Example: choose Individual files to replace one image or WAV without "
            "rebuilding a combined PGA."
        )
        self.project_width_label = QLabel("Width")
        project_row.addWidget(self.project_width_label)
        self.project_width_spin = QSpinBox()
        self.project_width_spin.setRange(32, 2048)
        project_row.addWidget(self.project_width_spin)
        self.project_height_label = QLabel("Height")
        project_row.addWidget(self.project_height_label)
        self.project_height_spin = QSpinBox()
        self.project_height_spin.setRange(32, 2048)
        project_row.addWidget(self.project_height_spin)
        project_row.addWidget(QLabel("Canvas zoom"))
        self.zoom_spin = QSpinBox()
        self.zoom_spin.setRange(25, 500)
        self.zoom_spin.setValue(100)
        self.zoom_spin.setSuffix("%")
        project_row.addWidget(self.zoom_spin)
        self.fit_canvas_button = QPushButton("Fit")
        self.fit_canvas_button.setCheckable(True)
        self.fit_canvas_button.setChecked(True)
        self.fit_canvas_button.setToolTip(
            "Keep the complete device screen visible when the editor is resized.\n"
            "Example: Fit a 320 x 320 PicoCalc screen without scrollbars."
        )
        project_row.addWidget(self.fit_canvas_button)
        self.import_mode_label = QLabel()
        self.import_mode_label.setStyleSheet("color: #ef6c00; font-weight: 600;")
        project_row.addWidget(self.import_mode_label)
        self.design_preview_button = QPushButton("Preview Layout")
        self.design_preview_button.setToolTip(
            "Preview layout, navigation, and focus without executing application code.\n"
            "Example: Check this screen before using Run current design above."
        )
        project_row.addWidget(self.design_preview_button)
        self.preview_button = QPushButton("Run in Simulator", self)
        self.preview_button.setToolTip(
            "Run the current in-memory GUI project in the Device Simulator.\n"
            "Example: Use the persistent Run current design button above."
        )
        self.preview_button.setVisible(False)
        layout.addLayout(project_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter = splitter
        splitter.setChildrenCollapsible(True)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Screens · Right-click for actions"))
        self.screen_list = QListWidget()
        self.screen_list.setIconSize(QSize(76, 64))
        self.screen_list.setSpacing(2)
        self.screen_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.screen_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.screen_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.screen_list.setToolTip(
            "Drag screens to reorder them; right-click for screen actions.\n"
            "Example: Right-click Main to duplicate or preview it."
        )
        self.screen_list.setMinimumHeight(132)
        left_layout.addWidget(self.screen_list, 1)
        screen_buttons = QGridLayout()
        self.add_screen_button = QPushButton("Add Screen")
        self.duplicate_screen_button = QPushButton("Duplicate Screen")
        self.delete_screen_button = QPushButton("Delete Screen")
        screen_buttons.addWidget(self.add_screen_button, 0, 0)
        screen_buttons.addWidget(self.duplicate_screen_button, 0, 1)
        screen_buttons.addWidget(self.delete_screen_button, 1, 0, 1, 2)
        left_layout.addLayout(screen_buttons)
        self.quick_assets_group = QGroupBox("Assets")
        self.quick_assets_group.setCheckable(True)
        self.quick_assets_group.setChecked(False)
        asset_group_layout = QVBoxLayout(self.quick_assets_group)
        self.asset_tabs = QTabWidget()

        reusable_assets_tab = QWidget()
        reusable_assets_layout = QVBoxLayout(reusable_assets_tab)
        reusable_assets_layout.setContentsMargins(4, 4, 4, 4)
        self.library_asset_search = QLineEdit()
        self.library_asset_search.setPlaceholderText(
            "Search built-in and personal assets"
        )
        self.library_asset_search.setClearButtonEnabled(True)
        library_filter_row = QHBoxLayout()
        self.library_theme_combo = QComboBox()
        self.library_theme_combo.addItem("All themes", "all")
        self.library_theme_combo.addItem("Starter icons", "general")
        for theme, theme_name in THEME_NAMES:
            self.library_theme_combo.addItem(theme_name, theme)
        self.library_theme_combo.setToolTip(
            "Limit quick assets to one built-in visual theme.\n"
            "Example: Choose Playful to see its matching icons, controls, and backgrounds."
        )
        self.library_kind_combo = QComboBox()
        self.library_kind_combo.addItem("All types", "all")
        self.library_kind_combo.addItem("Icons", "icon")
        self.library_kind_combo.addItem("Buttons", "button")
        self.library_kind_combo.addItem("Widgets", "widget")
        self.library_kind_combo.addItem("Backgrounds", "background")
        self.library_kind_combo.setToolTip(
            "Limit quick assets to one design type.\n"
            "Example: Choose Buttons to compare compact and wide button skins."
        )
        library_filter_row.addWidget(self.library_theme_combo)
        library_filter_row.addWidget(self.library_kind_combo)
        self.library_asset_list = QListWidget()
        self.library_asset_list.setIconSize(QSize(44, 44))
        self.library_asset_list.setSortingEnabled(True)
        self.library_asset_list.setMinimumHeight(96)
        self.library_asset_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.library_asset_list.setToolTip(
            "Built-in and personal assets available to every GUI project.\n"
            "Example: Double-click Home to add an independent copy to this screen."
        )
        self.library_empty_label = QLabel(
            "No reusable assets are available.\nOpen Asset Library to import or create one."
        )
        self.library_empty_label.setWordWrap(True)
        self.library_empty_label.setStyleSheet("color: #666;")
        self.library_import_button = QPushButton("Add selected asset to screen")
        self.library_import_button.setEnabled(False)
        self.library_save_selected_button = QPushButton(
            "Save selected screen asset to library"
        )
        self.library_save_selected_button.setEnabled(False)
        self.library_save_selected_button.setToolTip(
            "Store the selected placed pixel asset for reuse in other projects.\n"
            "Example: Select an icon on the canvas, then click this button."
        )
        self.library_manage_button = QPushButton("Open Asset Library")
        reusable_assets_layout.addWidget(self.library_asset_search)
        reusable_assets_layout.addLayout(library_filter_row)
        reusable_assets_layout.addWidget(self.library_empty_label)
        reusable_assets_layout.addWidget(self.library_asset_list, 1)
        reusable_assets_layout.addWidget(self.library_import_button)
        reusable_assets_layout.addWidget(self.library_save_selected_button)
        reusable_assets_layout.addWidget(self.library_manage_button)
        self.reusable_assets_tab_index = self.asset_tabs.addTab(
            reusable_assets_tab, "Library (0)"
        )

        source_assets_tab = QWidget()
        asset_layout = QVBoxLayout(source_assets_tab)
        asset_layout.setContentsMargins(4, 4, 4, 4)
        source_asset_help = QLabel(
            "Advanced: graphics discovered in an opened Python source."
        )
        source_asset_help.setWordWrap(True)
        source_asset_help.setStyleSheet("color: #666;")
        self.pixel_asset_search = QLineEdit()
        self.pixel_asset_search.setPlaceholderText("Search assets")
        self.pixel_asset_search.setClearButtonEnabled(True)
        self.pixel_asset_state_filter = QComboBox()
        self.pixel_asset_state_filter.addItems(
            ("All link states", "Current", "Modified", "Missing", "Draft", "Detached")
        )
        self.pixel_asset_list = PixelAssetList()
        self.pixel_asset_list.setIconSize(QSize(44, 44))
        self.pixel_asset_list.setSortingEnabled(True)
        self.pixel_asset_list.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.pixel_asset_list.setMaximumHeight(190)
        self.pixel_asset_list.setToolTip(
            "Drag a pixel asset onto the screen, or double-click to add it."
        )
        self.add_pixel_asset_button = QPushButton("Add selected asset")
        self.add_pixel_asset_button.setEnabled(False)
        self.refresh_all_pixel_assets_button = QPushButton("Refresh All Linked")
        asset_layout.addWidget(source_asset_help)
        asset_layout.addWidget(self.pixel_asset_search)
        asset_layout.addWidget(self.pixel_asset_state_filter)
        asset_layout.addWidget(self.pixel_asset_list)
        asset_layout.addWidget(self.add_pixel_asset_button)
        asset_layout.addWidget(self.refresh_all_pixel_assets_button)
        self.source_assets_tab_index = self.asset_tabs.addTab(
            source_assets_tab, "Python (0)"
        )
        asset_group_layout.addWidget(self.asset_tabs)
        left_layout.addWidget(self.quick_assets_group, 1)
        self.quick_assets_group.toggled.connect(
            lambda expanded: set_collapsible_group_expanded(
                self.quick_assets_group, expanded
            )
        )
        set_collapsible_group_expanded(self.quick_assets_group, False)
        self.screen_reference_group = QGroupBox("Screen reference")
        self.screen_reference_group.setCheckable(True)
        self.screen_reference_group.setChecked(False)
        reference_layout = QVBoxLayout(self.screen_reference_group)
        self.open_reference_button = QPushButton("Open image...")
        self.clear_reference_button = QPushButton("Clear")
        self.reference_opacity_spin = QSpinBox()
        self.reference_opacity_spin.setRange(0, 100)
        self.reference_opacity_spin.setValue(45)
        self.reference_opacity_spin.setSuffix("%")
        reference_layout.addWidget(self.open_reference_button)
        reference_layout.addWidget(self.clear_reference_button)
        reference_layout.addWidget(self.reference_opacity_spin)
        left_layout.addWidget(self.screen_reference_group)
        self.screen_reference_group.toggled.connect(
            lambda expanded: set_collapsible_group_expanded(
                self.screen_reference_group, expanded
            )
        )
        set_collapsible_group_expanded(self.screen_reference_group, False)
        self.output_settings_group = QGroupBox("Advanced project output")
        self.output_settings_group.setCheckable(True)
        self.output_settings_group.setChecked(False)
        output_settings_form = QFormLayout(self.output_settings_group)
        output_settings_form.addRow("Asset packaging", self.asset_storage_combo)
        left_layout.addWidget(self.output_settings_group)
        self.output_settings_group.toggled.connect(
            lambda expanded: set_collapsible_group_expanded(
                self.output_settings_group, expanded
            )
        )
        set_collapsible_group_expanded(self.output_settings_group, False)
        splitter.addWidget(left)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        self.workflow_hint = QLabel(
            "Build the screen: add elements → adjust Content and Layout → "
            "Preview Layout → connect interactions in Screen Flow."
        )
        self.workflow_hint.setWordWrap(True)
        self.workflow_hint.setStyleSheet(
            "background: #e8f1fb; color: #174a75; padding: 5px; border-radius: 3px;"
        )
        center_layout.addWidget(self.workflow_hint)
        drawn_tools = QGridLayout()
        drawn_tools.addWidget(QLabel("Drawn elements"), 0, 0, 1, 4)
        self.element_buttons: dict[str, ElementPaletteButton] = {}
        for index, kind in enumerate(
            item for item in ELEMENT_KINDS if item != "native"
        ):
            button = ElementPaletteButton(kind)
            self.element_buttons[kind] = button
            drawn_tools.addWidget(button, 1 + index // 4, index % 4)
        drawn_tools.setColumnStretch(4, 1)
        center_layout.addLayout(drawn_tools)
        native_tools = QGridLayout()
        native_tools.addWidget(QLabel("Picoware widget or control"), 0, 0)
        self.native_widget_combo = QComboBox()
        for spec in NATIVE_WIDGET_SPECS:
            self.native_widget_combo.addItem(spec.name, spec.id)
            self.native_widget_combo.setItemData(
                self.native_widget_combo.count() - 1,
                spec.summary,
                Qt.ItemDataRole.ToolTipRole,
            )
        self.add_native_widget_button = QPushButton("Add widget")
        self.add_native_widget_button.setToolTip(
            "Add a real Picoware widget, kept distinct from custom drawing elements.\n"
            "Example: choose Menu to start a native keyboard-selectable screen."
        )
        native_tools.addWidget(self.native_widget_combo, 0, 1)
        native_tools.addWidget(self.add_native_widget_button, 0, 2)
        self.native_kind_label = QLabel()
        self.native_kind_label.setStyleSheet("color: #666;")
        native_tools.addWidget(self.native_kind_label, 1, 0, 1, 3)
        native_tools.setColumnStretch(1, 1)
        center_layout.addLayout(native_tools)
        self.empty_screen_actions = QGroupBox("Start this screen")
        empty_actions_layout = QGridLayout(self.empty_screen_actions)
        self.empty_add_screen_widget_button = QPushButton("Add screen widget...")
        screen_widget_menu = QMenu(self.empty_add_screen_widget_button)
        for spec in (item for item in NATIVE_WIDGET_SPECS if item.full_screen):
            action = screen_widget_menu.addAction(spec.name)
            action.triggered.connect(
                lambda checked=False, widget_id=spec.id: self._add_native_widget(
                    widget_id
                )
            )
        self.empty_add_screen_widget_button.setMenu(screen_widget_menu)
        self.empty_custom_layout_button = QPushButton("Build custom layout")
        self.empty_starter_button = QPushButton("Use workflow starter...")
        empty_actions_layout.addWidget(self.empty_add_screen_widget_button, 0, 0)
        empty_actions_layout.addWidget(self.empty_custom_layout_button, 0, 1)
        empty_actions_layout.addWidget(self.empty_starter_button, 1, 0, 1, 2)
        empty_actions_layout.setColumnStretch(0, 1)
        empty_actions_layout.setColumnStretch(1, 1)
        center_layout.addWidget(self.empty_screen_actions)
        self.native_preview_notice_label = QLabel()
        self.native_preview_notice_label.setWordWrap(True)
        self.native_preview_notice_label.setStyleSheet(
            "background: #fff5d6; color: #6c4b00; padding: 4px; border-radius: 3px;"
        )
        center_layout.addWidget(self.native_preview_notice_label)
        self.canvas_context_hint = QLabel("Right-click canvas for actions")
        self.canvas_context_hint.setStyleSheet("color: #666;")
        center_layout.addWidget(
            self.canvas_context_hint, 0, Qt.AlignmentFlag.AlignRight
        )
        layout_tools = QHBoxLayout()
        self.grid_visible_check = QCheckBox("Grid")
        self.snap_check = QCheckBox("Snap")
        self.focus_order_visible_check = QCheckBox("Focus order")
        self.grid_size_spin = QSpinBox()
        self.grid_size_spin.setRange(2, 64)
        self.grid_size_spin.setValue(8)
        self.grid_size_spin.setSuffix(" px")
        layout_tools.addWidget(self.grid_visible_check)
        layout_tools.addWidget(self.snap_check)
        layout_tools.addWidget(self.focus_order_visible_check)
        layout_tools.addWidget(self.grid_size_spin)
        layout_tools.addStretch(1)
        center_layout.addLayout(layout_tools)
        self.alignment_group = QGroupBox("Align multiple selected elements")
        self.alignment_group.setCheckable(True)
        self.alignment_group.setChecked(False)
        alignment_layout = QGridLayout(self.alignment_group)
        self.alignment_buttons: dict[str, QPushButton] = {}
        for index, (key, label) in enumerate(
            (
                ("left", "Left"),
                ("hcenter", "Center X"),
                ("top", "Top"),
                ("vcenter", "Center Y"),
                ("distribute_h", "Space X"),
                ("distribute_v", "Space Y"),
            )
        ):
            button = QPushButton(label)
            button.setToolTip(f"Align selected elements: {label}")
            self.alignment_buttons[key] = button
            alignment_layout.addWidget(button, index // 3, index % 3)
        self.alignment_group.toggled.connect(
            lambda expanded: set_collapsible_group_expanded(
                self.alignment_group, expanded
            )
        )
        set_collapsible_group_expanded(self.alignment_group, False)
        center_layout.addWidget(self.alignment_group)
        self.canvas_scroll = QScrollArea()
        self.canvas_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas = DesignCanvas(self.session)
        self.canvas.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.canvas.setToolTip(
            "Arrange screen elements; right-click for common element actions.\n"
            "Example: Select a button, then right-click to duplicate it."
        )
        self.canvas_scroll.setWidget(self.canvas)
        center_layout.addWidget(self.canvas_scroll, 1)
        splitter.addWidget(center)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("Element hierarchy · Right-click for actions"))
        self.drawing_order_label = QLabel(
            "Drawing order: top row is back · bottom row is front"
        )
        self.drawing_order_label.setStyleSheet("color: #666;")
        self.drawing_order_label.setToolTip(
            "Elements are drawn from the first row to the last row.\n"
            "Example: move a badge lower in this list to draw it over a panel."
        )
        right_layout.addWidget(self.drawing_order_label)
        self.element_list = QListWidget()
        self.element_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.element_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.element_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.element_list.setToolTip(
            "Shift-select multiple layers. Drag layers to change drawing order."
        )
        self.element_list.setWordWrap(True)
        self.element_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.element_list.setMinimumHeight(72)
        self.element_list.setMaximumHeight(140)
        right_layout.addWidget(self.element_list)
        self.delete_element_button = QPushButton("Delete")
        self.duplicate_element_button = QPushButton("Duplicate")
        self.lock_element_button = QPushButton("Lock")
        self.visibility_element_button = QPushButton("Hide")
        self.bring_front_button = QPushButton("Bring to Front")
        self.move_forward_button = QPushButton("Move Forward")
        self.move_backward_button = QPushButton("Move Backward")
        self.send_back_button = QPushButton("Send to Back")
        self.bring_front_button.setToolTip(
            "Move selected elements above every other layer.\n"
            "Example: place a label over an overlapping panel."
        )
        self.move_forward_button.setToolTip(
            "Move selected elements one drawing layer toward the front."
        )
        self.move_backward_button.setToolTip(
            "Move selected elements one drawing layer toward the back."
        )
        self.send_back_button.setToolTip(
            "Move selected elements behind every other layer.\n"
            "Example: send a full-screen background image behind the controls."
        )
        selection_actions = QGridLayout()
        selection_actions.addWidget(self.delete_element_button, 0, 0)
        selection_actions.addWidget(self.duplicate_element_button, 0, 1)
        selection_actions.addWidget(self.lock_element_button, 1, 0)
        selection_actions.addWidget(self.visibility_element_button, 1, 1)
        right_layout.addLayout(selection_actions)
        self.layer_order_group = QGroupBox("Advanced layer order")
        self.layer_order_group.setCheckable(True)
        self.layer_order_group.setChecked(False)
        layer_actions = QGridLayout(self.layer_order_group)
        layer_actions.addWidget(self.bring_front_button, 0, 0)
        layer_actions.addWidget(self.move_forward_button, 0, 1)
        layer_actions.addWidget(self.move_backward_button, 1, 0)
        layer_actions.addWidget(self.send_back_button, 1, 1)
        right_layout.addWidget(self.layer_order_group)
        self.layer_order_group.toggled.connect(
            lambda expanded: set_collapsible_group_expanded(
                self.layer_order_group, expanded
            )
        )
        set_collapsible_group_expanded(self.layer_order_group, False)
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
        left_layout.insertWidget(3, self.screen_group)
        self.property_group = QGroupBox("Element properties")
        property_sections = QVBoxLayout(self.property_group)
        self.element_name_edit = QLineEdit()
        self.kind_combo = QComboBox()
        for kind in (item for item in ELEMENT_KINDS if item != "native"):
            self.kind_combo.addItem(kind.title(), kind)
        self.x_spin = self._coordinate_spin()
        self.y_spin = self._coordinate_spin()
        self.width_spin = self._coordinate_spin(1)
        self.height_spin = self._coordinate_spin(1)
        self.element_text_edit = QLineEdit()
        self.element_text_edit.setPlaceholderText("Use \\n for list rows")
        self.asset_call_edit = QLineEdit()
        self.asset_call_edit.setPlaceholderText("Optional icon function")
        self.refresh_pixel_asset_button = QPushButton("Refresh pixel asset")
        self.edit_pixel_asset_button = QPushButton("Edit in Pixel Art")
        self.relink_pixel_asset_button = QPushButton("Relink...")
        self.detach_pixel_asset_button = QPushButton("Detach")
        self.asset_link_state_label = QLabel("Detached")
        self.asset_size_status_label = QLabel()
        self.asset_size_status_label.setWordWrap(True)
        self.asset_size_status_label.setToolTip(
            "Device rendering supports natural size or one uniform integer scale.\n"
            "Example: a 16 x 16 asset may be 16 x 16, 32 x 32, or 48 x 48."
        )
        self.asset_natural_size_button = QPushButton("Use natural asset size")
        self.asset_natural_size_button.setToolTip(
            "Restore the selected placement to the source asset dimensions.\n"
            "Example: change a distorted 30 x 24 icon back to its natural 16 x 16."
        )
        self.asset_bake_size_button = QPushButton("Bake current size...")
        self.asset_bake_size_button.setToolTip(
            "Create an independent nearest-neighbor asset at the current element size.\n"
            "Example: bake a 64 x 40 image placement without changing the library original."
        )
        self.visible_check = QCheckBox("Visible")
        self.enabled_check = QCheckBox("Input enabled")
        self.focusable_check = QCheckBox("Keyboard focusable")
        self.focus_order_spin = QSpinBox()
        self.focus_order_spin.setRange(0, 999)
        self.focus_style_combo = QComboBox()
        for label, style in FOCUS_STYLES:
            self.focus_style_combo.addItem(label, style)
        self.focus_style_combo.setToolTip(
            "Choose how keyboard focus is drawn around this element."
        )
        self.focus_color_button = QPushButton("Focus color...")
        self.focus_thickness_spin = QSpinBox()
        self.focus_thickness_spin.setRange(1, 6)
        self.focus_thickness_spin.setToolTip(
            "Line thickness in device pixels. Example: 2."
        )
        self.focus_padding_spin = QSpinBox()
        self.focus_padding_spin.setRange(0, 12)
        self.focus_padding_spin.setToolTip(
            "Space between the element and indicator. Example: 2."
        )
        self.event_name_edit = QLineEdit()
        self.event_name_edit.setPlaceholderText("Uses the element name")
        self.event_name_edit.setToolTip(
            "Event emitted when this element is clicked or activated."
        )
        self.element_flow_label = QLabel()
        self.element_flow_label.setWordWrap(True)
        self.open_flow_button = QPushButton("Create behavior from this element...")
        self.open_flow_button.setToolTip(
            "Open Screen Flow with this element selected as the interaction source.\n"
            "Example: connect a Settings button to the Settings screen."
        )
        self.fill_color_button = QPushButton("Fill...")
        self.border_color_button = QPushButton("Border...")
        self.text_color_button = QPushButton("Text...")

        self.native_properties_group = QGroupBox("Picoware widget")
        native_form = QFormLayout(self.native_properties_group)
        self.native_type_combo = QComboBox()
        for spec in NATIVE_WIDGET_SPECS:
            self.native_type_combo.addItem(spec.name, spec.id)
        self.native_summary_label = QLabel()
        self.native_summary_label.setWordWrap(True)
        self.native_role_label = QLabel()
        self.native_role_label.setStyleSheet("color: #245b85; font-weight: 600;")
        self.widget_items_edit = QPlainTextEdit()
        self.widget_items_edit.setPlaceholderText("One item per line")
        self.widget_items_edit.setMaximumHeight(100)
        self.widget_selected_combo = QComboBox()
        self.widget_state_check = QCheckBox("On")
        self.widget_item_states_list = QListWidget()
        self.widget_item_states_list.setMaximumHeight(110)
        self.widget_item_states_list.setToolTip(
            "Choose the initial On or Off state for every Toggle List row."
        )
        native_form.addRow("Native type", self.native_type_combo)
        native_form.addRow("Role", self.native_role_label)
        native_form.addRow(self.native_summary_label)
        native_form.addRow("Items/options", self.widget_items_edit)
        native_form.addRow("Initially selected", self.widget_selected_combo)
        native_form.addRow("Initial state", self.widget_state_check)
        native_form.addRow("Initial item states", self.widget_item_states_list)

        self.content_properties_group = QGroupBox("Content && appearance")
        self.content_property_form = QFormLayout(self.content_properties_group)
        self.content_property_form.addRow("Name", self.element_name_edit)
        self.content_property_form.addRow("Type", self.kind_combo)
        self.content_property_form.addRow("Text", self.element_text_edit)
        self.content_property_form.addRow(self.visible_check)
        self.content_property_form.addRow("Fill color", self.fill_color_button)
        self.content_property_form.addRow("Border color", self.border_color_button)
        self.content_property_form.addRow("Text color", self.text_color_button)
        property_sections.addWidget(self.content_properties_group)
        property_sections.addWidget(self.native_properties_group)

        self.layout_properties_group = QGroupBox("Layout")
        self.layout_property_form = QFormLayout(self.layout_properties_group)
        self.layout_property_form.addRow("X", self.x_spin)
        self.layout_property_form.addRow("Y", self.y_spin)
        self.layout_property_form.addRow("Width", self.width_spin)
        self.layout_property_form.addRow("Height", self.height_spin)
        property_sections.addWidget(self.layout_properties_group)

        self.interaction_properties_group = QGroupBox("Interaction && focus")
        self.interaction_properties_group.setCheckable(True)
        self.interaction_properties_group.setChecked(False)
        self.interaction_property_form = QFormLayout(self.interaction_properties_group)
        self.interaction_property_form.addRow(self.enabled_check)
        self.interaction_property_form.addRow(self.focusable_check)
        self.interaction_property_form.addRow("Activation event", self.event_name_edit)
        self.interaction_property_form.addRow("Interactions", self.element_flow_label)
        self.interaction_property_form.addRow(self.open_flow_button)
        self.interaction_property_form.addRow("Focus order", self.focus_order_spin)
        self.interaction_property_form.addRow("Focus style", self.focus_style_combo)
        self.interaction_property_form.addRow(self.focus_color_button)
        self.interaction_property_form.addRow(
            "Focus thickness", self.focus_thickness_spin
        )
        self.interaction_property_form.addRow("Focus spacing", self.focus_padding_spin)
        property_sections.addWidget(self.interaction_properties_group)

        self.asset_properties_group = QGroupBox("Advanced asset link")
        self.asset_properties_group.setCheckable(True)
        self.asset_properties_group.setChecked(False)
        self.asset_property_form = QFormLayout(self.asset_properties_group)
        self.asset_property_form.addRow("Source call", self.asset_call_edit)
        self.asset_property_form.addRow("Link status", self.asset_link_state_label)
        self.asset_property_form.addRow("Device size", self.asset_size_status_label)
        self.asset_property_form.addRow(self.asset_natural_size_button)
        self.asset_property_form.addRow(self.asset_bake_size_button)
        self.asset_property_form.addRow(self.refresh_pixel_asset_button)
        self.asset_property_form.addRow(self.edit_pixel_asset_button)
        self.asset_property_form.addRow(self.relink_pixel_asset_button)
        self.asset_property_form.addRow(self.detach_pixel_asset_button)
        property_sections.addWidget(self.asset_properties_group)
        property_sections.addStretch(1)

        for group in (
            self.interaction_properties_group,
            self.asset_properties_group,
        ):
            group.toggled.connect(self._property_section_toggled)
            set_collapsible_group_expanded(group, False)
        self.property_scroll = QScrollArea()
        self.property_scroll.setWidgetResizable(True)
        self.property_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        property_panel = QWidget()
        property_layout = QVBoxLayout(property_panel)
        property_layout.setContentsMargins(0, 0, 0, 0)
        self.property_empty_label = QLabel(
            "Select an element on the canvas or in the hierarchy to edit it."
        )
        self.property_empty_label.setWordWrap(True)
        self.property_empty_label.setStyleSheet("color: #666; padding: 8px;")
        property_layout.addWidget(self.property_empty_label)
        property_layout.addWidget(self.property_group)
        property_layout.addStretch(1)
        self.property_scroll.setWidget(property_panel)
        right_layout.addWidget(self.property_scroll, 2)
        splitter.addWidget(right)
        splitter.setSizes((220, 820, 300))
        splitter.setStretchFactor(1, 1)
        saved = self.settings.value("designer/splitter")
        if saved:
            splitter.restoreState(saved)
        splitter.splitterMoved.connect(
            lambda position, index: self.settings.setValue(
                "designer/splitter", splitter.saveState()
            )
        )
        layout.addWidget(splitter, 1)

    def _connect_signals(self) -> None:
        """Connect designer controls to the shared project."""
        self.session.project_changed.connect(self.refresh)
        self.session.live_previews_changed.connect(self.refresh)
        self.session.active_screen_changed.connect(self._active_screen_changed)
        self.screen_list.currentRowChanged.connect(self._screen_selected)
        self.screen_list.model().rowsMoved.connect(self._screens_reordered)
        self.screen_list.customContextMenuRequested.connect(
            self._show_screen_context_menu
        )
        self.element_list.itemSelectionChanged.connect(
            self._element_list_selection_changed
        )
        self.element_list.model().rowsMoved.connect(self._layers_reordered)
        self.element_list.customContextMenuRequested.connect(
            self._show_element_context_menu
        )
        self.canvas.customContextMenuRequested.connect(self._show_element_context_menu)
        self.canvas.selection_changed.connect(self._canvas_selection_changed)
        self.canvas.geometry_changed.connect(self._canvas_geometry_changed)
        self.canvas.zoom_changed.connect(self._canvas_zoom_changed)
        self.canvas.element_dropped.connect(self._drop_element)
        self.canvas.asset_dropped.connect(self._drop_pixel_asset)
        self.pixel_asset_list.itemSelectionChanged.connect(
            self._pixel_asset_selection_changed
        )
        self.pixel_asset_list.itemDoubleClicked.connect(self._pixel_asset_activated)
        self.pixel_asset_search.textChanged.connect(self._filter_pixel_assets)
        self.pixel_asset_state_filter.currentTextChanged.connect(
            lambda text: self._filter_pixel_assets(self.pixel_asset_search.text())
        )
        self.add_pixel_asset_button.clicked.connect(self._add_selected_pixel_asset)
        self.library_asset_list.itemSelectionChanged.connect(
            self._library_asset_selection_changed
        )
        self.library_asset_search.textChanged.connect(self._filter_library_assets)
        self.library_theme_combo.currentIndexChanged.connect(
            lambda unused_index: self._filter_library_assets(
                self.library_asset_search.text()
            )
        )
        self.library_kind_combo.currentIndexChanged.connect(
            lambda unused_index: self._filter_library_assets(
                self.library_asset_search.text()
            )
        )
        self.asset_tabs.currentChanged.connect(self._asset_tab_changed)
        self.library_asset_list.itemDoubleClicked.connect(
            lambda unused_item: self._add_selected_library_asset()
        )
        self.library_asset_list.customContextMenuRequested.connect(
            self._show_library_context_menu
        )
        self.library_import_button.clicked.connect(self._add_selected_library_asset)
        self.library_save_selected_button.clicked.connect(
            self._save_selected_element_to_library
        )
        self.library_manage_button.clicked.connect(self.library_manage_requested.emit)
        self.open_flow_button.clicked.connect(self._open_selected_element_flow)
        self.refresh_all_pixel_assets_button.clicked.connect(
            self._refresh_all_pixel_assets
        )
        self.refresh_pixel_asset_button.clicked.connect(
            self._refresh_selected_pixel_asset
        )
        self.edit_pixel_asset_button.clicked.connect(self._edit_selected_pixel_asset)
        self.relink_pixel_asset_button.clicked.connect(
            self._relink_selected_pixel_asset
        )
        self.detach_pixel_asset_button.clicked.connect(
            self._detach_selected_pixel_asset
        )
        self.asset_natural_size_button.clicked.connect(
            self._use_selected_asset_natural_size
        )
        self.asset_bake_size_button.clicked.connect(self._bake_selected_asset_size)
        self.zoom_spin.valueChanged.connect(self.canvas.set_zoom)
        self.fit_canvas_button.clicked.connect(self._fit_canvas_clicked)
        self.empty_custom_layout_button.clicked.connect(
            lambda: self._add_element("button")
        )
        self.empty_starter_button.clicked.connect(self.starter_requested.emit)
        self.grid_visible_check.toggled.connect(self.canvas.set_grid_visible)
        self.snap_check.toggled.connect(self.canvas.set_snap_enabled)
        self.focus_order_visible_check.toggled.connect(
            self.canvas.set_focus_order_visible
        )
        self.grid_size_spin.valueChanged.connect(self.canvas.set_grid_size)
        for mode, button in self.alignment_buttons.items():
            button.clicked.connect(
                lambda checked=False, alignment=mode: self._align_selection(alignment)
            )
        self.add_screen_button.clicked.connect(self._add_screen)
        self.duplicate_screen_button.clicked.connect(self._duplicate_screen)
        self.delete_screen_button.clicked.connect(self._delete_screen)
        self.delete_element_button.clicked.connect(self._delete_element)
        self.duplicate_element_button.clicked.connect(self._duplicate_elements)
        self.lock_element_button.clicked.connect(self._toggle_element_lock)
        self.visibility_element_button.clicked.connect(self._toggle_element_visibility)
        self.bring_front_button.clicked.connect(
            lambda: self._reorder_selected_elements("front")
        )
        self.move_forward_button.clicked.connect(
            lambda: self._reorder_selected_elements("forward")
        )
        self.move_backward_button.clicked.connect(
            lambda: self._reorder_selected_elements("backward")
        )
        self.send_back_button.clicked.connect(
            lambda: self._reorder_selected_elements("back")
        )
        self.canvas.delete_requested.connect(self._delete_element)
        self.canvas.duplicate_requested.connect(self._duplicate_elements)
        for kind, button in self.element_buttons.items():
            button.clicked.connect(
                lambda checked=False, element_kind=kind: self._add_element(element_kind)
            )
        self.add_native_widget_button.clicked.connect(
            lambda: self._add_native_widget(
                str(self.native_widget_combo.currentData() or "menu")
            )
        )
        self.native_widget_combo.currentIndexChanged.connect(
            self._native_palette_changed
        )
        self.project_name_edit.editingFinished.connect(self._project_settings_changed)
        self.profile_combo.currentIndexChanged.connect(self._profile_changed)
        self.asset_storage_combo.currentIndexChanged.connect(
            self._asset_storage_changed
        )
        self.project_width_spin.valueChanged.connect(self._custom_size_changed)
        self.project_height_spin.valueChanged.connect(self._custom_size_changed)
        self.screen_name_edit.editingFinished.connect(self._screen_properties_changed)
        self.screen_background_button.clicked.connect(self._choose_screen_background)
        for widget in (
            self.element_name_edit,
            self.element_text_edit,
            self.asset_call_edit,
            self.event_name_edit,
        ):
            widget.editingFinished.connect(self._element_properties_changed)
        self.kind_combo.currentIndexChanged.connect(self._element_properties_changed)
        self.native_type_combo.currentIndexChanged.connect(
            self._native_widget_type_changed
        )
        self.widget_items_edit.textChanged.connect(self._element_properties_changed)
        self.widget_selected_combo.currentIndexChanged.connect(
            self._element_properties_changed
        )
        self.widget_state_check.toggled.connect(self._element_properties_changed)
        self.widget_item_states_list.itemChanged.connect(
            self._widget_item_state_changed
        )
        for widget in (self.x_spin, self.y_spin, self.width_spin, self.height_spin):
            widget.valueChanged.connect(self._element_properties_changed)
        self.visible_check.toggled.connect(self._element_properties_changed)
        self.enabled_check.toggled.connect(self._element_properties_changed)
        self.focusable_check.toggled.connect(self._element_properties_changed)
        self.focus_order_spin.valueChanged.connect(self._element_properties_changed)
        self.focus_style_combo.currentIndexChanged.connect(
            self._element_properties_changed
        )
        self.focus_thickness_spin.valueChanged.connect(self._element_properties_changed)
        self.focus_padding_spin.valueChanged.connect(self._element_properties_changed)
        self.focus_color_button.clicked.connect(
            lambda: self._choose_element_color("focus_color")
        )
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
        self.design_preview_button.clicked.connect(self.design_preview_requested.emit)
        self.preview_button.clicked.connect(self.preview_requested.emit)

    def _configure_tab_order(self) -> None:
        """Follow the visible App GUI workflow instead of construction order."""
        controls: list[QWidget] = [
            self.project_name_edit,
            self.profile_combo,
            self.zoom_spin,
            self.fit_canvas_button,
            self.design_preview_button,
            self.screen_list,
            self.add_screen_button,
            self.duplicate_screen_button,
            self.delete_screen_button,
            self.empty_add_screen_widget_button,
            self.empty_custom_layout_button,
            self.empty_starter_button,
            *self.element_buttons.values(),
            self.native_widget_combo,
            self.add_native_widget_button,
            self.grid_visible_check,
            self.snap_check,
            self.focus_order_visible_check,
            self.grid_size_spin,
            *self.alignment_buttons.values(),
            self.canvas,
            self.element_list,
            self.delete_element_button,
            self.duplicate_element_button,
            self.lock_element_button,
            self.visibility_element_button,
            self.element_name_edit,
            self.kind_combo,
            self.element_text_edit,
            self.visible_check,
            self.native_type_combo,
            self.widget_items_edit,
            self.widget_selected_combo,
            self.widget_state_check,
            self.widget_item_states_list,
            self.x_spin,
            self.y_spin,
            self.width_spin,
            self.height_spin,
            self.enabled_check,
            self.focusable_check,
            self.event_name_edit,
            self.open_flow_button,
            self.asset_tabs,
            self.library_asset_search,
            self.library_asset_list,
            self.library_import_button,
            self.library_save_selected_button,
            self.library_manage_button,
            self.screen_reference_group,
            self.output_settings_group,
        ]
        for current, following in zip(controls, controls[1:]):
            QWidget.setTabOrder(current, following)

    def _property_section_toggled(self, expanded: bool) -> None:
        """Expand one advanced property section and restore contextual row visibility."""
        group = self.sender()
        if isinstance(group, QGroupBox):
            set_collapsible_group_expanded(group, expanded)
            self._refresh_element_properties()

    def resizeEvent(self, event) -> None:
        """Keep a fitted device screen visible as the workspace changes size."""
        super().resizeEvent(event)
        if self.fit_canvas_button.isChecked():
            QTimer.singleShot(0, self._fit_canvas_to_view)

    def _fit_canvas_clicked(self, checked: bool) -> None:
        """Enable or disable automatic whole-screen canvas fitting."""
        if checked:
            self._fit_canvas_to_view()

    def _fit_canvas_to_view(self) -> None:
        """Choose the largest practical zoom that avoids canvas scrollbars."""
        if not self.fit_canvas_button.isChecked() or not self.isVisible():
            return
        screen = self.session.current_screen()
        viewport = self.canvas_scroll.viewport().size()
        if screen.width <= 0 or screen.height <= 0 or viewport.isEmpty():
            return
        factor = min(
            max(1, viewport.width() - 24) / screen.width,
            max(1, viewport.height() - 24) / screen.height,
        )
        zoom = max(25, min(500, int(factor * 100) // 5 * 5))
        self._fitting_canvas = True
        try:
            self.zoom_spin.setValue(zoom)
            self.canvas.set_zoom(zoom)
        finally:
            self._fitting_canvas = False

    def _canvas_zoom_changed(self, zoom: int) -> None:
        """Synchronize manual canvas zoom and leave Fit mode when appropriate."""
        if self.zoom_spin.value() != zoom:
            self.zoom_spin.setValue(zoom)
        if not self._fitting_canvas:
            self.fit_canvas_button.setChecked(False)

    def _native_palette_changed(self) -> None:
        """Explain whether the selected Picoware widget owns a screen."""
        widget_id = str(self.native_widget_combo.currentData() or "menu")
        spec = native_widget_spec(widget_id)
        self.native_kind_label.setText(
            "Screen widget · use on an empty screen"
            if spec.full_screen
            else "Inline control · combine in a custom layout"
        )

    def _widget_item_state_changed(self, unused_item: QListWidgetItem) -> None:
        """Persist per-row Toggle List initial states."""
        if self._updating:
            return
        element = self._selected_element()
        if element is None or element.kind != "native":
            return
        spec = _safe_native_widget_spec(element.native_widget)
        if not spec.supports_item_states:
            return
        element.widget_item_states = [
            self.widget_item_states_list.item(index).checkState()
            == Qt.CheckState.Checked
            for index in range(self.widget_item_states_list.count())
        ]
        self.session.mark_changed(False)

    def _open_selected_element_flow(self) -> None:
        """Hand the selected interactive element to the Screen Flow workspace."""
        element = self._selected_element()
        if element is None:
            return
        self.flow_edit_requested.emit(self.session.current_screen().id, element.id)

    def refresh(self) -> None:
        """Refresh all controls from the current project."""
        self._updating = True
        project = self.session.project
        valid_elements = {
            element.id for element in self.session.current_screen().elements
        }
        self.selected_element_ids.intersection_update(valid_elements)
        if self.selected_element_id not in self.selected_element_ids:
            self.selected_element_id = next(iter(self.selected_element_ids), None)
        self.project_name_edit.setText(project.name)
        self.profile_combo.setCurrentText(project.profile)
        storage_mode = str(project.generated_app.get("asset_storage", "combined"))
        storage_index = self.asset_storage_combo.findData(storage_mode)
        self.asset_storage_combo.setCurrentIndex(max(0, storage_index))
        self.project_width_spin.setValue(project.width)
        self.project_height_spin.setValue(project.height)
        custom = project.profile == "Custom"
        self.project_width_label.setVisible(custom)
        self.project_height_label.setVisible(custom)
        self.project_width_spin.setVisible(custom)
        self.project_height_spin.setVisible(custom)
        self.project_width_spin.setEnabled(custom)
        self.project_height_spin.setEnabled(custom)
        self.import_mode_label.setText(
            "SOURCE-BACKED APP" if project.imported_sources else ""
        )
        self.screen_list.clear()
        selected_row = 0
        for index, screen in enumerate(project.screens):
            live_image = self.session.live_screen_images.get(screen.id)
            preview_image = (
                live_preview_image(live_image, QSize(76, 64))
                if live_image is not None
                else screen_preview_image(screen, QSize(76, 64))
            )
            item = QListWidgetItem(
                QIcon(QPixmap.fromImage(preview_image)),
                screen.name,
            )
            item.setSizeHint(QSize(190, 70))
            item.setData(Qt.ItemDataRole.UserRole, screen.id)
            incoming = sum(
                connection.target_id == screen.id for connection in project.connections
            )
            outgoing = sum(
                connection.source_id == screen.id for connection in project.connections
            )
            details = f"{incoming} incoming, {outgoing} outgoing"
            if live_image is not None:
                details += "\nLive simulator capture"
            if screen.source_path:
                details += f"\n{screen.source_path}:{screen.source_line}"
            item.setToolTip(details)
            self.screen_list.addItem(item)
            if screen.id == self.session.active_screen_id:
                selected_row = index
        self.screen_list.setCurrentRow(selected_row)
        self._refresh_element_list()
        self._refresh_screen_properties()
        self._refresh_element_properties()
        screen = self.session.current_screen()
        self.empty_screen_actions.setVisible(not screen.elements)
        screen_widget = next(
            (element for element in screen.elements if _is_screen_widget(element)),
            None,
        )
        self.native_preview_notice_label.setVisible(screen_widget is not None)
        if screen_widget is not None:
            spec = _safe_native_widget_spec(screen_widget.native_widget)
            self.native_preview_notice_label.setText(
                f"Desktop approximation · Runtime uses Picoware {spec.class_name}. "
                "This widget owns the screen and cannot be layered with other controls."
            )
        self._native_palette_changed()
        self.canvas._update_size()
        self.canvas.update()
        self._updating = False
        if self.fit_canvas_button.isChecked():
            QTimer.singleShot(0, self._fit_canvas_to_view)

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
            self.selected_element_ids.clear()
            self.session.set_active_screen(str(item.data(Qt.ItemDataRole.UserRole)))

    def _active_screen_changed(self, screen_id: str) -> None:
        """Refresh the designer for a shared screen selection."""
        self.selected_element_id = None
        self.selected_element_ids.clear()
        self.refresh()

    def _screens_reordered(self, *args: object) -> None:
        """Apply screen-list drag order to the designer project."""
        if self._updating:
            return
        by_id = {screen.id: screen for screen in self.session.project.screens}
        ordered = [
            by_id[str(self.screen_list.item(row).data(Qt.ItemDataRole.UserRole))]
            for row in range(self.screen_list.count())
            if str(self.screen_list.item(row).data(Qt.ItemDataRole.UserRole)) in by_id
        ]
        if [screen.id for screen in ordered] == [
            screen.id for screen in self.session.project.screens
        ]:
            return
        self.session.project.screens = ordered
        self.session.mark_changed()

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
            element.event_id = new_identifier("event")
            element.source_path = ""
            element.source_line = 0
            element.source_call = ""
            element.source_segment = ""
            element.source_values.clear()
            if element.asset_id and element.asset_link_state in {"detached", "draft"}:
                source_asset = self.session.project.asset(element.asset_id)
                element.asset_id = f"snapshot_{element.id.lower()}"
                if source_asset is not None:
                    snapshot = ProjectAsset.from_dict(asdict(source_asset))
                    snapshot.id = element.asset_id
                    self.session.project.upsert_asset(snapshot)
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
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
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
        screen_widget = next(
            (element for element in screen.elements if _is_screen_widget(element)),
            None,
        )
        if screen_widget is not None:
            QMessageBox.information(
                self,
                "Screen widget owns this screen",
                f"{screen_widget.name} uses the complete screen. Add a new screen "
                "for drawn elements or inline controls.",
            )
            return
        element = GuiElement.create(kind, len(screen.elements) + 1)
        element.x, element.y = self._next_unoccupied_position(element)
        screen.elements.append(element)
        self.selected_element_id = element.id
        self.selected_element_ids = {element.id}
        self.session.mark_changed()

    def _add_native_widget(self, widget_id: str) -> None:
        """Add one configured Picoware-native widget to the active screen."""
        spec = native_widget_spec(widget_id)
        screen = self.session.current_screen()
        existing_screen_widget = next(
            (element for element in screen.elements if _is_screen_widget(element)), None
        )
        if spec.full_screen and screen.elements:
            QMessageBox.information(
                self,
                "Screen widget needs an empty screen",
                f"Picoware {spec.name} owns the complete screen. Add it to an empty "
                "screen, or remove the existing layers first.",
            )
            return
        if existing_screen_widget is not None:
            QMessageBox.information(
                self,
                "Screen widget owns this screen",
                f"{existing_screen_widget.name} uses the complete screen. Add a new "
                "custom-layout screen for {spec.name}.",
            )
            self._select_element(existing_screen_widget.id)
            return
        element = GuiElement.create("native", len(screen.elements) + 1)
        element.native_widget = spec.id
        element.name = spec.name
        element.text = spec.default_text
        element.widget_items = list(spec.default_items)
        element.widget_item_states = [False] * len(element.widget_items)
        element.focusable = spec.interactive
        element.enabled = spec.interactive
        if spec.full_screen:
            element.x = 0
            element.y = 0
            element.width = screen.width
            element.height = screen.height
        else:
            element.width = max(80, min(screen.width - 20, spec.default_width))
            element.height = max(32, min(screen.height - 20, spec.default_height))
            element.x, element.y = self._next_unoccupied_position(element)
        screen.elements.append(element)
        self.selected_element_id = element.id
        self.selected_element_ids = {element.id}
        self.session.mark_changed()

    def _next_unoccupied_position(self, element: GuiElement) -> tuple[int, int]:
        """Find a visible, non-overlapping cascade position for a new element."""
        screen = self.session.current_screen()
        step = max(8, self.canvas.grid_size)
        for y in range(step, max(step + 1, screen.height - element.height + 1), step):
            for x in range(step, max(step + 1, screen.width - element.width + 1), step):
                if all(
                    x + element.width <= other.x
                    or other.x + other.width <= x
                    or y + element.height <= other.y
                    or other.y + other.height <= y
                    for other in screen.elements
                ):
                    return x, y
        offset = (len(screen.elements) * step) % max(step, screen.width // 2)
        return min(offset, screen.width - element.width), min(
            offset, screen.height - element.height
        )

    def set_library_assets(self, assets: list[GuiPixelAsset]) -> None:
        """Replace materialized reusable assets and defer their thumbnails."""
        self.library_records = {}
        self.library_assets = {asset.key: asset for asset in assets}
        entries = [
            (
                asset.key,
                asset.name,
                asset.art.width,
                asset.art.height,
                len(asset.frames),
                asset.fingerprint,
            )
            for asset in assets
        ]
        self._set_library_entries(entries)

    def set_library_records(
        self,
        records: tuple[LibraryAsset, ...],
        select_record_id: str = "",
    ) -> None:
        """Expose lightweight library records without decoding every animation."""
        self.library_assets = {}
        self.library_records = {f"library::{record.id}": record for record in records}
        entries = [
            (
                f"library::{record.id}",
                record.name,
                record.width,
                record.height,
                len(record.frames),
                record.fingerprint or f"{record.id}:{hash(record.frames[0])}",
            )
            for record in records
        ]
        self._set_library_entries(
            entries,
            selected_key=f"library::{select_record_id}" if select_record_id else "",
        )

    def _set_library_entries(
        self,
        entries: list[tuple[str, str, int, int, int, str]],
        selected_key: str = "",
    ) -> None:
        """Diff-update reusable asset metadata while preserving its selection."""
        selected = selected_key or self._selected_library_asset_key()
        self.asset_tabs.setTabText(
            self.reusable_assets_tab_index,
            f"Library ({len(entries)})",
        )
        existing = dict(self._library_items)
        blocked = self.library_asset_list.blockSignals(True)
        try:
            active_keys = {entry[0] for entry in entries}
            for key, item in tuple(existing.items()):
                if key not in active_keys:
                    self.library_asset_list.takeItem(self.library_asset_list.row(item))
                    existing.pop(key)
            for key, name, width, height, frame_count, revision in entries:
                item = existing.get(key)
                if item is None:
                    item = QListWidgetItem()
                    self.library_asset_list.addItem(item)
                    existing[key] = item
                if item.data(Qt.ItemDataRole.UserRole + 1) != revision:
                    item.setIcon(QIcon())
                    item.setData(Qt.ItemDataRole.UserRole + 1, revision)
                    item.setData(Qt.ItemDataRole.UserRole + 2, False)
                item.setText(name)
                item.setData(Qt.ItemDataRole.UserRole, key)
                item.setToolTip(
                    f"{width} x {height}, {frame_count} frame(s).\n"
                    "Example: Double-click to add an independent copy to the current screen."
                )
                if key == selected:
                    self.library_asset_list.setCurrentItem(item)
            if (
                self.library_asset_list.currentItem() is None
                and self.library_asset_list.count()
            ):
                self.library_asset_list.setCurrentRow(0)
        finally:
            self.library_asset_list.blockSignals(blocked)
        self._library_items = existing
        self.library_empty_label.setVisible(not entries)
        self.library_asset_list.setVisible(bool(entries))
        self._filter_library_assets(self.library_asset_search.text())
        self._library_asset_selection_changed()
        self._schedule_quick_thumbnails()

    def _asset_tab_changed(self, index: int) -> None:
        """Populate thumbnails only while reusable assets are visible."""
        if index == self.reusable_assets_tab_index:
            self._schedule_quick_thumbnails()

    def _filter_library_assets(self, text: str) -> None:
        """Filter reusable assets without hiding the built-in catalogue itself."""
        needle = text.strip().casefold()
        theme = str(self.library_theme_combo.currentData() or "all")
        asset_kind = str(self.library_kind_combo.currentData() or "all")
        visible_count = 0
        for row in range(self.library_asset_list.count()):
            item = self.library_asset_list.item(row)
            key = str(item.data(Qt.ItemDataRole.UserRole) or "")
            record = self.library_records.get(key)
            metadata = standard_asset_metadata(record.id) if record else None
            if metadata:
                item_theme = metadata.theme
                item_kind = metadata.kind
            elif record and is_standard_asset_id(record.id):
                item_theme = "general"
                item_kind = "icon"
            else:
                item_theme = "personal"
                item_kind = "personal"
            visible = bool(
                (not needle or needle in item.text().casefold())
                and (theme == "all" or item_theme == theme)
                and (asset_kind == "all" or item_kind == asset_kind)
            )
            item.setHidden(not visible)
            visible_count += int(visible)
        current = self.library_asset_list.currentItem()
        if current is not None and current.isHidden():
            self.library_asset_list.setCurrentItem(None)
        has_assets = self.library_asset_list.count() > 0
        if not has_assets:
            self.library_empty_label.setText(
                "No reusable assets are available.\n"
                "Open Asset Library to import or create one."
            )
            self.library_empty_label.setVisible(True)
        elif visible_count == 0:
            self.library_empty_label.setText(
                "No built-in or personal assets match this search."
            )
            self.library_empty_label.setVisible(True)
        else:
            self.library_empty_label.setVisible(False)
        self.library_asset_list.setVisible(has_assets)
        self.library_import_button.setEnabled(
            visible_count > 0 and self.library_asset_list.currentItem() is not None
        )

    def _schedule_quick_thumbnails(self) -> None:
        """Queue lightweight Quick Asset icons while keeping the UI responsive."""
        self._quick_thumbnail_generation += 1
        if self.asset_tabs.currentIndex() != self.reusable_assets_tab_index:
            self._quick_thumbnail_queue = []
            return
        generation = self._quick_thumbnail_generation
        self._quick_thumbnail_queue = list(self._library_items)
        selected = self._selected_library_asset_key()
        if selected in self._quick_thumbnail_queue:
            self._quick_thumbnail_queue.remove(selected)
            self._quick_thumbnail_queue.insert(0, selected)
        QTimer.singleShot(0, lambda: self._render_quick_thumbnail_batch(generation))

    def _render_quick_thumbnail_batch(self, generation: int) -> None:
        """Render a small Quick Assets batch and yield to other GUI events."""
        if (
            generation != self._quick_thumbnail_generation
            or self.asset_tabs.currentIndex() != self.reusable_assets_tab_index
        ):
            return
        target = self.library_asset_list.iconSize()
        for _ in range(min(8, len(self._quick_thumbnail_queue))):
            key = self._quick_thumbnail_queue.pop(0)
            item = self._library_items.get(key)
            if item is None or item.data(Qt.ItemDataRole.UserRole + 2):
                continue
            asset = self.library_assets.get(key)
            record = self.library_records.get(key)
            if asset is not None:
                pixmap = cached_pixel_art_pixmap(asset.fingerprint, asset.art, target)
            elif record is not None:
                revision = record.fingerprint or f"{record.id}:{hash(record.frames[0])}"
                pixmap = cached_pixel_frame_pixmap(
                    revision,
                    record.width,
                    record.height,
                    record.origin_x,
                    record.origin_y,
                    record.frames[0],
                    target,
                )
            else:
                continue
            item.setIcon(QIcon(pixmap))
            item.setData(Qt.ItemDataRole.UserRole + 2, True)
        if self._quick_thumbnail_queue:
            QTimer.singleShot(0, lambda: self._render_quick_thumbnail_batch(generation))

    def _selected_library_asset_key(self) -> str:
        """Return the selected stable personal-library key."""
        item = self.library_asset_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def _library_asset_selection_changed(self) -> None:
        """Enable library operations only for a valid stored asset."""
        available = self._library_asset_available(self._selected_library_asset_key())
        self.library_import_button.setEnabled(available)

    def _library_asset_available(self, key: str) -> bool:
        """Return whether a materialized asset or lightweight record exists."""
        return key in self.library_assets or key in self.library_records

    def _add_selected_library_asset(self) -> None:
        """Import an independent library copy into the active project screen."""
        key = self._selected_library_asset_key()
        asset = self.library_assets.get(key)
        record = self.library_records.get(key)
        if asset is None and record is not None:
            frames = record.pixel_frames()
            asset = GuiPixelAsset(
                key,
                record.name,
                (
                    "Built-in Standard Library"
                    if record.id.startswith("builtin_icon_")
                    else "Personal Asset Library"
                ),
                record.name,
                frames[0],
                record.fingerprint,
                frames,
                record.durations,
            )
        if asset is not None:
            self.place_pixel_asset(asset, "detached")

    def _rename_selected_library_asset(self) -> None:
        """Request a persistent rename for the selected personal asset."""
        key = self._selected_library_asset_key()
        if key:
            self.library_asset_rename_requested.emit(key.removeprefix("library::"))

    def _delete_selected_library_asset(self) -> None:
        """Request deletion of the selected personal asset."""
        key = self._selected_library_asset_key()
        if key:
            self.library_asset_delete_requested.emit(key.removeprefix("library::"))

    def _save_selected_element_to_library(self) -> None:
        """Request persistent storage for the selected placed pixel asset."""
        element = self._selected_element()
        if element is not None and element.asset_id:
            self.library_element_save_requested.emit(element.id)

    def _show_library_context_menu(self, position: QPoint) -> None:
        """Show common personal-library actions at the pointer."""
        item = self.library_asset_list.itemAt(position)
        if item is not None:
            self.library_asset_list.setCurrentItem(item)
        self._library_context_menu().exec(
            self.library_asset_list.viewport().mapToGlobal(position)
        )

    def _library_context_menu(self) -> QMenu:
        """Build the reusable personal-library context menu."""
        menu = QMenu(self)
        add_action = menu.addAction("Add to current screen")
        rename_action = menu.addAction("Rename library asset")
        delete_action = menu.addAction("Delete from library")
        available = self._library_asset_available(self._selected_library_asset_key())
        for action in (add_action, rename_action, delete_action):
            action.setEnabled(available)
        add_action.triggered.connect(self._add_selected_library_asset)
        rename_action.triggered.connect(self._rename_selected_library_asset)
        delete_action.triggered.connect(self._delete_selected_library_asset)
        return menu

    def _show_screen_context_menu(self, position: QPoint) -> None:
        """Show common screen operations at the pointer."""
        item = self.screen_list.itemAt(position)
        if item is not None:
            self.screen_list.setCurrentItem(item)
        self._screen_context_menu().exec(
            self.screen_list.viewport().mapToGlobal(position)
        )

    def _screen_context_menu(self) -> QMenu:
        """Build the App GUI screen-list context menu."""
        menu = QMenu(self)
        menu.addAction("Add screen", self._add_screen)
        menu.addAction("Duplicate screen", self._duplicate_screen)
        delete_action = menu.addAction("Delete screen", self._delete_screen)
        delete_action.setEnabled(len(self.session.project.screens) > 1)
        menu.addSeparator()
        menu.addAction("Preview Layout (Safe)", self.design_preview_requested.emit)
        menu.addAction("Run current design in Simulator", self.preview_requested.emit)
        return menu

    def _show_element_context_menu(self, position: QPoint) -> None:
        """Show common element and canvas operations at the pointer."""
        sender = self.sender()
        source = sender if isinstance(sender, QWidget) else self.canvas
        if source is self.canvas:
            point = self.canvas._design_point(QPointF(position))
            element = self.canvas._element_at(point)
            if element is not None:
                self.canvas.selected_id = element.id
                if element.id not in self.selected_element_ids:
                    self.canvas.selected_ids = {element.id}
                self.canvas._emit_selection()
        elif isinstance(source, QListWidget):
            item = source.itemAt(position)
            if item is not None:
                source.setCurrentItem(item)
        coordinate_widget = (
            source.viewport() if isinstance(source, QListWidget) else source
        )
        self._element_context_menu().exec(coordinate_widget.mapToGlobal(position))

    def _element_context_menu(self) -> QMenu:
        """Build the App GUI canvas and hierarchy context menu."""
        menu = QMenu(self)
        add_menu = menu.addMenu("Add element")
        for kind in (item for item in ELEMENT_KINDS if item != "native"):
            action = add_menu.addAction(kind.title())
            action.triggered.connect(
                lambda checked=False, element_kind=kind: self._add_element(element_kind)
            )
        native_menu = add_menu.addMenu("Picoware widget")
        for spec in NATIVE_WIDGET_SPECS:
            action = native_menu.addAction(spec.name)
            action.setToolTip(spec.summary)
            action.triggered.connect(
                lambda checked=False, widget_id=spec.id: self._add_native_widget(
                    widget_id
                )
            )
        menu.addSeparator()
        duplicate_action = menu.addAction("Duplicate selected")
        open_pixel_action = menu.addAction("Open Asset in Pixel Editor")
        lock_action = menu.addAction("Lock or unlock selected")
        visibility_action = menu.addAction("Show or hide selected")
        save_action = menu.addAction("Save selected asset to personal library")
        natural_size_action = menu.addAction("Use natural asset size")
        bake_size_action = menu.addAction("Bake asset at current size...")
        menu.addSeparator()
        bring_front_action = menu.addAction("Bring to Front")
        move_forward_action = menu.addAction("Move Forward One Layer")
        move_backward_action = menu.addAction("Move Backward One Layer")
        send_back_action = menu.addAction("Send to Back")
        menu.addSeparator()
        delete_action = menu.addAction("Delete selected")
        selected = bool(self.selected_element_ids)
        duplicate_action.setEnabled(selected)
        open_pixel_action.setEnabled(False)
        lock_action.setEnabled(selected)
        visibility_action.setEnabled(selected)
        delete_action.setEnabled(selected)
        element = self._selected_element()
        save_action.setEnabled(bool(element and element.asset_id))
        asset = (
            self.session.project.asset(element.asset_id)
            if element is not None and element.asset_id
            else None
        )
        open_pixel_action.setEnabled(asset is not None)
        screen = self.session.current_screen()
        natural_size_action.setEnabled(
            asset is not None
            and asset.width <= screen.width
            and asset.height <= screen.height
        )
        bake_size_action.setEnabled(
            asset is not None
            and 1 <= element.width <= 320
            and 1 <= element.height <= 320
        )
        current_order = [item.id for item in self.session.current_screen().elements]
        for mode, action in (
            ("front", bring_front_action),
            ("forward", move_forward_action),
            ("backward", move_backward_action),
            ("back", send_back_action),
        ):
            proposed = [item.id for item in self._ordered_elements_for_layer_move(mode)]
            action.setEnabled(selected and proposed != current_order)
        duplicate_action.triggered.connect(self._duplicate_elements)
        open_pixel_action.triggered.connect(self._edit_selected_pixel_asset)
        lock_action.triggered.connect(self._toggle_element_lock)
        visibility_action.triggered.connect(self._toggle_element_visibility)
        delete_action.triggered.connect(self._delete_element)
        save_action.triggered.connect(self._save_selected_element_to_library)
        natural_size_action.triggered.connect(self._use_selected_asset_natural_size)
        bake_size_action.triggered.connect(self._bake_selected_asset_size)
        bring_front_action.triggered.connect(
            lambda: self._reorder_selected_elements("front")
        )
        move_forward_action.triggered.connect(
            lambda: self._reorder_selected_elements("forward")
        )
        move_backward_action.triggered.connect(
            lambda: self._reorder_selected_elements("backward")
        )
        send_back_action.triggered.connect(
            lambda: self._reorder_selected_elements("back")
        )
        return menu

    def set_pixel_assets(self, assets: list[GuiPixelAsset]) -> None:
        """Replace the source pixel assets offered by the designer."""
        selected = self._selected_pixel_asset_key()
        self.pixel_assets = {asset.key: asset for asset in assets}
        self.pixel_asset_list.clear()
        for asset in assets:
            self.pixel_asset_list.addItem(self._pixel_asset_item(asset))
        self.asset_tabs.setTabText(
            self.source_assets_tab_index, f"Python ({len(assets)})"
        )
        restored = self._pixel_asset_item_for_key(selected)
        if restored is not None:
            self.pixel_asset_list.setCurrentItem(restored)
        elif self.pixel_asset_list.count():
            self.pixel_asset_list.setCurrentRow(0)
        self._synchronize_asset_link_states()
        self._filter_pixel_assets(self.pixel_asset_search.text())
        self._pixel_asset_selection_changed()

    def upsert_pixel_asset(self, asset: GuiPixelAsset) -> None:
        """Add or refresh one source pixel asset in the designer."""
        self.pixel_assets[asset.key] = asset
        existing = self._pixel_asset_item_for_key(asset.key)
        replacement = self._pixel_asset_item(asset)
        if existing is None:
            self.pixel_asset_list.addItem(replacement)
            if self.pixel_asset_list.currentItem() is None:
                self.pixel_asset_list.setCurrentItem(replacement)
        else:
            existing.setText(replacement.text())
            existing.setIcon(replacement.icon())
            existing.setToolTip(replacement.toolTip())
        self.asset_tabs.setTabText(
            self.source_assets_tab_index,
            f"Python ({len(self.pixel_assets)})",
        )
        self._synchronize_asset_link_states()
        self._pixel_asset_selection_changed()

    def _filter_pixel_assets(self, text: str) -> None:
        """Filter source assets by name, function, path, or link state."""
        needle = text.strip().lower()
        state_filter = self.pixel_asset_state_filter.currentText().lower()
        linked_states = {
            element.asset_key: element.asset_link_state
            for screen in self.session.project.screens
            for element in screen.elements
            if element.asset_key
        }
        for row in range(self.pixel_asset_list.count()):
            item = self.pixel_asset_list.item(row)
            key = str(item.data(Qt.ItemDataRole.UserRole))
            asset = self.pixel_assets.get(key)
            haystack = (
                ""
                if asset is None
                else (f"{asset.name} {asset.function_name} {asset.source_path}").lower()
            )
            state_visible = (
                state_filter == "all link states"
                or linked_states.get(key) == state_filter
            )
            item.setHidden(
                bool((needle and needle not in haystack) or not state_visible)
            )

    def _synchronize_asset_link_states(self) -> None:
        """Mark embedded links current, modified, or missing without replacing pixels."""
        for screen in self.session.project.screens:
            for element in screen.elements:
                if element.kind != "icon" or element.asset_link_state in {
                    "draft",
                    "detached",
                }:
                    continue
                asset = self.pixel_assets.get(element.asset_key)
                if asset is None:
                    element.asset_link_state = "missing"
                elif element.asset_fingerprint == asset.fingerprint:
                    element.asset_link_state = "current"
                else:
                    element.asset_link_state = "modified"

    def select_pixel_asset(self, key: str) -> bool:
        """Select one source pixel asset in the designer catalogue."""
        item = self._pixel_asset_item_for_key(key)
        if item is None:
            return False
        self.pixel_asset_list.setCurrentItem(item)
        self.pixel_asset_list.scrollToItem(item)
        return True

    def _pixel_asset_item(self, asset: GuiPixelAsset) -> QListWidgetItem:
        """Build one source pixel asset catalogue item."""
        image = pixel_art_image(asset.art, checker=True)
        pixmap = QPixmap.fromImage(image).scaled(
            self.pixel_asset_list.iconSize(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        states = sorted(
            {
                element.asset_link_state
                for screen in self.session.project.screens
                for element in screen.elements
                if element.asset_key == asset.key
            }
        )
        badge = f" [{' / '.join(states)}]" if states else ""
        item = QListWidgetItem(
            QIcon(pixmap), f"{Path(asset.source_path).name} / {asset.name}{badge}"
        )
        item.setData(Qt.ItemDataRole.UserRole, asset.key)
        item.setToolTip(
            f"{asset.function_name}\n{asset.source_path}\n"
            "Drag onto the screen or double-click to add."
        )
        return item

    def _pixel_asset_item_for_key(self, key: str) -> QListWidgetItem | None:
        """Return the catalogue item matching one pixel asset key."""
        if not key:
            return None
        for row in range(self.pixel_asset_list.count()):
            item = self.pixel_asset_list.item(row)
            if str(item.data(Qt.ItemDataRole.UserRole) or "") == key:
                return item
        return None

    def _selected_pixel_asset_key(self) -> str:
        """Return the selected source pixel asset key."""
        item = self.pixel_asset_list.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def _pixel_asset_selection_changed(self) -> None:
        """Enable pixel asset insertion for a valid selection."""
        key = self._selected_pixel_asset_key()
        self.add_pixel_asset_button.setEnabled(key in self.pixel_assets)

    def _pixel_asset_activated(self, item: QListWidgetItem) -> None:
        """Insert a double-clicked pixel asset into the active screen."""
        self.pixel_asset_list.setCurrentItem(item)
        self._add_selected_pixel_asset()

    def _add_selected_pixel_asset(self) -> None:
        """Insert the selected pixel asset at the screen center."""
        asset = self.pixel_assets.get(self._selected_pixel_asset_key())
        if asset is None:
            return
        self.place_pixel_asset(asset)

    def place_pixel_asset(
        self, asset: GuiPixelAsset, link_state: str = "current"
    ) -> GuiElement | None:
        """Place one asset at the current screen center and return its element."""
        screen = self.session.current_screen()
        if any(_is_screen_widget(element) for element in screen.elements):
            QMessageBox.information(
                self,
                "Screen widget owns this screen",
                "Add image assets to a separate custom-layout screen.",
            )
            return None
        return self._insert_pixel_asset(
            asset, screen.width // 2, screen.height // 2, link_state
        )

    def place_pixel_assets(
        self,
        assets: tuple[GuiPixelAsset, ...],
        link_state: str = "current",
    ) -> tuple[GuiElement, ...]:
        """Place several assets as one undoable centered grid."""
        if not assets:
            return ()
        screen = self.session.current_screen()
        if any(_is_screen_widget(element) for element in screen.elements):
            QMessageBox.information(
                self,
                "Screen widget owns this screen",
                "Add image assets to a separate custom-layout screen.",
            )
            return ()
        columns = min(5, max(1, ceil(sqrt(len(assets)))))
        rows = ceil(len(assets) / columns)
        cell_width = max(asset.art.width for asset in assets) + 8
        cell_height = max(asset.art.height for asset in assets) + 8
        start_x = screen.width // 2 - ((columns - 1) * cell_width) // 2
        start_y = screen.height // 2 - ((rows - 1) * cell_height) // 2
        created: list[GuiElement] = []
        self.session.begin_transaction()
        try:
            for index, asset in enumerate(assets):
                column = index % columns
                row = index // columns
                created.append(
                    self._insert_pixel_asset(
                        asset,
                        start_x + column * cell_width,
                        start_y + row * cell_height,
                        link_state,
                    )
                )
        finally:
            self.session.end_transaction()
        self.selected_element_ids = {element.id for element in created}
        self.selected_element_id = created[-1].id
        self.refresh()
        return tuple(created)

    def _drop_pixel_asset(self, key: str, x: int, y: int) -> None:
        """Insert a source pixel asset at its canvas drop point."""
        if any(
            _is_screen_widget(element)
            for element in self.session.current_screen().elements
        ):
            QMessageBox.information(
                self,
                "Screen widget owns this screen",
                "Add image assets to a separate custom-layout screen.",
            )
            return
        asset = self.pixel_assets.get(key)
        if asset is not None:
            self._insert_pixel_asset(asset, x, y)

    def _insert_pixel_asset(
        self, asset: GuiPixelAsset, x: int, y: int, link_state: str = "current"
    ) -> GuiElement:
        """Create one portable icon element from a source pixel asset."""
        screen = self.session.current_screen()
        element = GuiElement.create("icon", len(screen.elements) + 1)
        element.name = f"{asset.name}_{len(screen.elements) + 1}"
        element.text = ""
        element.asset_call = asset.function_name
        if link_state == "detached" or link_state == "draft":
            element.asset_key = ""
            element.asset_link_state = link_state
        else:
            element.asset_key = asset.key
            element.asset_link_state = "current"
            element.asset_qualified_name = asset.function_name
            element.asset_absolute_fallback = asset.source_path
            if self.session.path is not None:
                try:
                    element.asset_source_path = os.path.relpath(
                        asset.source_path, self.session.path.parent
                    )
                except ValueError:
                    element.asset_source_path = asset.source_path
            else:
                element.asset_source_path = asset.source_path
            element.asset_fingerprint = asset.fingerprint
        element.asset_width = asset.art.width
        element.asset_height = asset.art.height
        blank = PixelArt(
            asset.art.width,
            asset.art.height,
            asset.art.origin_x,
            asset.art.origin_y,
        )
        element.asset_runs = [list(run) for run in asset.art.horizontal_runs(blank)]
        if link_state in {"detached", "draft"}:
            element.asset_id = f"snapshot_{element.id.lower()}"
        else:
            element.asset_id = stable_identifier("asset", f"linked:{asset.key}")
        self.session.project.upsert_asset(
            project_asset_from_gui_asset(
                element.asset_id,
                asset,
                source_path=element.asset_source_path,
                absolute_fallback=element.asset_absolute_fallback,
                qualified_name=element.asset_qualified_name,
                link_state=element.asset_link_state,
            )
        )
        element.width = min(screen.width, asset.art.width)
        element.height = min(screen.height, asset.art.height)
        element.x = max(
            0,
            min(
                screen.width - element.width,
                self.canvas.snap_value(x - element.width // 2),
            ),
        )
        element.y = max(
            0,
            min(
                screen.height - element.height,
                self.canvas.snap_value(y - element.height // 2),
            ),
        )
        screen.elements.append(element)
        self.selected_element_id = element.id
        self.selected_element_ids = {element.id}
        self.session.mark_changed()
        return element

    def _refresh_selected_pixel_asset(self) -> None:
        """Refresh selected icon pixels from its linked catalogue asset."""
        element = self._selected_element()
        if element is None or element.kind != "icon" or not element.asset_key:
            return
        asset = self.pixel_assets.get(element.asset_key)
        if asset is None:
            QMessageBox.information(
                self,
                "Pixel asset unavailable",
                "Open or rescan the Python file containing this pixel asset.",
            )
            return
        old_size = (element.asset_width, element.asset_height)
        new_size = (asset.art.width, asset.art.height)
        resize_geometry = False
        if old_size != new_size:
            message = QMessageBox(self)
            message.setWindowTitle("Asset dimensions changed")
            message.setText(
                f"The linked asset changed from {old_size[0]} x {old_size[1]} "
                f"to {new_size[0]} x {new_size[1]}."
            )
            keep_button = message.addButton(
                "Keep element geometry", QMessageBox.ButtonRole.AcceptRole
            )
            resize_button = message.addButton(
                "Resize element to asset", QMessageBox.ButtonRole.ActionRole
            )
            message.addButton(QMessageBox.StandardButton.Cancel)
            message.exec()
            if message.clickedButton() not in {keep_button, resize_button}:
                return
            resize_geometry = message.clickedButton() is resize_button
        blank = PixelArt(
            asset.art.width,
            asset.art.height,
            asset.art.origin_x,
            asset.art.origin_y,
        )
        element.asset_call = asset.function_name
        element.asset_width = asset.art.width
        element.asset_height = asset.art.height
        element.asset_runs = [list(run) for run in asset.art.horizontal_runs(blank)]
        element.asset_fingerprint = asset.fingerprint
        element.asset_link_state = "current"
        if not element.asset_id:
            element.asset_id = stable_identifier("asset", f"linked:{asset.key}")
        self.session.project.upsert_asset(
            project_asset_from_gui_asset(
                element.asset_id,
                asset,
                source_path=element.asset_source_path,
                absolute_fallback=element.asset_absolute_fallback,
                qualified_name=element.asset_qualified_name,
                link_state="current",
            )
        )
        if resize_geometry:
            screen = self.session.current_screen()
            element.width = min(screen.width - element.x, asset.art.width)
            element.height = min(screen.height - element.y, asset.art.height)
        self.session.mark_changed()

    def _refresh_all_pixel_assets(self) -> None:
        """Refresh available linked snapshots while preserving element geometry."""
        changed = False
        skipped_dimensions = 0
        for screen in self.session.project.screens:
            for element in screen.elements:
                asset = self.pixel_assets.get(element.asset_key)
                if element.kind != "icon" or asset is None:
                    continue
                if (element.asset_width, element.asset_height) != (
                    asset.art.width,
                    asset.art.height,
                ):
                    skipped_dimensions += 1
                    continue
                blank = PixelArt(
                    asset.art.width,
                    asset.art.height,
                    asset.art.origin_x,
                    asset.art.origin_y,
                )
                element.asset_call = asset.function_name
                element.asset_width = asset.art.width
                element.asset_height = asset.art.height
                element.asset_runs = [
                    list(run) for run in asset.art.horizontal_runs(blank)
                ]
                element.asset_fingerprint = asset.fingerprint
                element.asset_link_state = "current"
                if not element.asset_id:
                    element.asset_id = stable_identifier("asset", f"linked:{asset.key}")
                self.session.project.upsert_asset(
                    project_asset_from_gui_asset(
                        element.asset_id,
                        asset,
                        source_path=element.asset_source_path,
                        absolute_fallback=element.asset_absolute_fallback,
                        qualified_name=element.asset_qualified_name,
                        link_state="current",
                    )
                )
                changed = True
        if changed:
            self.session.mark_changed()
        if skipped_dimensions:
            QMessageBox.information(
                self,
                "Dimension decisions required",
                f"Skipped {skipped_dimensions} size-changing link(s). "
                "Select each one and choose Refresh pixel asset to decide its geometry.",
            )

    def _relink_selected_pixel_asset(self) -> None:
        """Choose new source metadata without replacing embedded pixels."""
        element = self._selected_element()
        if element is None or element.kind != "icon":
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "Relink pixel asset", str(Path.cwd()), "Python files (*.py)"
        )
        if not filename:
            return
        source = str(Path(filename).resolve())
        element.asset_absolute_fallback = source
        element.asset_source_path = (
            os.path.relpath(source, self.session.path.parent)
            if self.session.path is not None
            else source
        )
        element.asset_key = (
            f"{source}::{element.asset_qualified_name or element.asset_call}"
        )
        element.asset_link_state = "modified"
        old_asset = self.session.project.asset(element.asset_id)
        element.asset_id = stable_identifier("asset", f"linked:{element.asset_key}")
        if old_asset is not None:
            relinked = ProjectAsset.from_dict(asdict(old_asset))
            relinked.id = element.asset_id
            relinked.source_path = element.asset_source_path
            relinked.absolute_fallback = source
            relinked.qualified_name = element.asset_qualified_name or element.asset_call
            relinked.link_state = "modified"
            self.session.project.upsert_asset(relinked)
        self.session.mark_changed()

    def _detach_selected_pixel_asset(self) -> None:
        """Detach the selected icon while preserving its embedded snapshot."""
        element = self._selected_element()
        if element is None or element.kind != "icon":
            return
        element.asset_key = ""
        element.asset_source_path = ""
        element.asset_absolute_fallback = ""
        element.asset_fingerprint = ""
        element.asset_link_state = "detached"
        previous = self.session.project.asset(element.asset_id)
        element.asset_id = f"snapshot_{element.id.lower()}"
        if previous is not None:
            snapshot = ProjectAsset.from_dict(asdict(previous))
            snapshot.id = element.asset_id
            snapshot.source_path = ""
            snapshot.absolute_fallback = ""
            snapshot.qualified_name = ""
            snapshot.fingerprint = ""
            snapshot.link_state = "detached"
            self.session.project.upsert_asset(snapshot)
        self.session.mark_changed()

    def _use_selected_asset_natural_size(self) -> None:
        """Restore one placement to its canonical asset dimensions."""
        element = self._selected_element()
        asset = (
            self.session.project.asset(element.asset_id)
            if element is not None and element.asset_id
            else None
        )
        if element is None or asset is None:
            return
        screen = self.session.current_screen()
        if asset.width > screen.width or asset.height > screen.height:
            QMessageBox.information(
                self,
                "Asset is larger than the screen",
                "Natural size does not fit this screen. Choose Bake current size instead.",
            )
            return
        element.width = asset.width
        element.height = asset.height
        element.x = max(0, min(element.x, screen.width - element.width))
        element.y = max(0, min(element.y, screen.height - element.height))
        self.session.mark_changed()

    def _bake_selected_asset_size(self) -> None:
        """Explicitly bake one placement as an independent nearest-neighbor asset."""
        element = self._selected_element()
        asset = (
            self.session.project.asset(element.asset_id)
            if element is not None and element.asset_id
            else None
        )
        if element is None or asset is None:
            return
        answer = QMessageBox.question(
            self,
            "Bake asset at current size?",
            (
                f"Create an independent {element.width} x {element.height} "
                f"nearest-neighbor copy of {asset.name}?\n\n"
                "The linked or library original remains unchanged."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            bake_asset_element(self.session.project, element)
        except ValueError as error:
            QMessageBox.warning(self, "Cannot bake asset", str(error))
            return
        self.session.mark_changed()

    def _edit_selected_pixel_asset(self) -> None:
        """Open a linked source or project snapshot in the Pixel Editor."""
        element = self._selected_element()
        if element is None or not element.asset_id:
            return
        source_text, separator, unused_name = element.asset_key.rpartition("::")
        if separator and Path(source_text).is_file():
            self.pixel_asset_edit_requested.emit(element.asset_key)
            return
        if self.session.project.asset(element.asset_id) is not None:
            self.project_asset_edit_requested.emit(
                element.asset_id,
                max(0, int(element.asset_frame)),
            )

    def _drop_element(self, kind: str, x: int, y: int) -> None:
        """Add one palette element centered at a canvas drop point."""
        screen = self.session.current_screen()
        if any(_is_screen_widget(element) for element in screen.elements):
            QMessageBox.information(
                self,
                "Screen widget owns this screen",
                "Add drawn elements to a separate custom-layout screen.",
            )
            return
        element = GuiElement.create(kind, len(screen.elements) + 1)
        element.x = max(
            0,
            min(
                screen.width - element.width,
                self.canvas.snap_value(x - element.width // 2),
            ),
        )
        element.y = max(
            0,
            min(
                screen.height - element.height,
                self.canvas.snap_value(y - element.height // 2),
            ),
        )
        screen.elements.append(element)
        self.selected_element_id = element.id
        self.selected_element_ids = {element.id}
        self.session.mark_changed()

    def _delete_element(self) -> None:
        """Delete the selected screen element."""
        if not self.selected_element_ids:
            return
        screen = self.session.current_screen()
        selected = [
            element
            for element in screen.elements
            if element.id in self.selected_element_ids
        ]
        if any(element.source_path for element in selected):
            QMessageBox.information(
                self,
                "Source element retained",
                "Imported source calls cannot be deleted. Remove them from the selection or set Visible off.",
            )
            return
        screen.elements = [
            item for item in screen.elements if item.id not in self.selected_element_ids
        ]
        project = self.session.project
        project.connections = [
            connection
            for connection in project.connections
            if connection.source_element_id not in self.selected_element_ids
            and connection.target_element_id not in self.selected_element_ids
        ]
        self.selected_element_id = None
        self.selected_element_ids.clear()
        self.session.mark_changed()

    def _duplicate_elements(self) -> None:
        """Duplicate selected design elements with a small offset."""
        screen = self.session.current_screen()
        if any(
            _is_screen_widget(element)
            for element in screen.elements
            if element.id in self.selected_element_ids
        ):
            QMessageBox.information(
                self,
                "Screen widget cannot be duplicated here",
                "Duplicate the complete screen instead, or add an inline control to a "
                "custom-layout screen.",
            )
            return
        selected = [
            element
            for element in screen.elements
            if element.id in self.selected_element_ids
            and not element.source_path
            and not element.locked
        ]
        if not selected:
            return
        duplicates: list[GuiElement] = []
        next_focus_order = (
            max(
                (element.focus_order for element in screen.elements),
                default=-1,
            )
            + 1
        )
        for source in selected:
            duplicate = GuiElement.from_dict(asdict(source))
            duplicate.id = new_identifier("element")
            duplicate.event_id = new_identifier("event")
            duplicate.name = f"{source.name}_copy"
            duplicate.x = max(0, min(screen.width - duplicate.width, source.x + 8))
            duplicate.y = max(0, min(screen.height - duplicate.height, source.y + 8))
            duplicate.editor_locked = False
            if duplicate.focusable:
                duplicate.focus_order = next_focus_order
                next_focus_order += 1
            if duplicate.asset_id and duplicate.asset_link_state in {
                "detached",
                "draft",
            }:
                source_asset = self.session.project.asset(source.asset_id)
                duplicate.asset_id = f"snapshot_{duplicate.id.lower()}"
                if source_asset is not None:
                    snapshot = ProjectAsset.from_dict(asdict(source_asset))
                    snapshot.id = duplicate.asset_id
                    self.session.project.upsert_asset(snapshot)
            duplicates.append(duplicate)
        screen.elements.extend(duplicates)
        self.selected_element_ids = {element.id for element in duplicates}
        self.selected_element_id = duplicates[-1].id
        self.session.mark_changed()

    def _toggle_element_lock(self) -> None:
        """Toggle editor locking for selected design elements."""
        elements = [
            element
            for element in self.session.current_screen().elements
            if element.id in self.selected_element_ids and not element.locked
        ]
        if not elements:
            return
        locked = not all(element.editor_locked for element in elements)
        for element in elements:
            element.editor_locked = locked
        self.session.mark_changed()

    def _toggle_element_visibility(self) -> None:
        """Toggle visibility for all selected elements."""
        elements = [
            element
            for element in self.session.current_screen().elements
            if element.id in self.selected_element_ids
        ]
        if not elements:
            return
        visible = not all(element.visible for element in elements)
        for element in elements:
            element.visible = visible
        self.session.mark_changed()

    def _ordered_elements_for_layer_move(self, mode: str) -> list[GuiElement]:
        """Return drawing order after one stable selected-layer operation."""
        elements = list(self.session.current_screen().elements)
        selected = {
            element.id
            for element in elements
            if element.id in self.selected_element_ids
        }
        if not selected:
            return elements
        if mode == "front":
            return [item for item in elements if item.id not in selected] + [
                item for item in elements if item.id in selected
            ]
        if mode == "back":
            return [item for item in elements if item.id in selected] + [
                item for item in elements if item.id not in selected
            ]
        ordered = list(elements)
        if mode == "forward":
            for index in range(len(ordered) - 2, -1, -1):
                if (
                    ordered[index].id in selected
                    and ordered[index + 1].id not in selected
                ):
                    ordered[index], ordered[index + 1] = (
                        ordered[index + 1],
                        ordered[index],
                    )
            return ordered
        if mode == "backward":
            for index in range(1, len(ordered)):
                if (
                    ordered[index].id in selected
                    and ordered[index - 1].id not in selected
                ):
                    ordered[index], ordered[index - 1] = (
                        ordered[index - 1],
                        ordered[index],
                    )
            return ordered
        raise ValueError(f"Unknown layer move {mode}")

    def _reorder_selected_elements(self, mode: str) -> None:
        """Move selected elements within the back-to-front drawing stack."""
        screen = self.session.current_screen()
        ordered = self._ordered_elements_for_layer_move(mode)
        if [item.id for item in ordered] == [item.id for item in screen.elements]:
            return
        screen.elements = ordered
        self.session.mark_changed()

    def _align_selection(self, mode: str) -> None:
        """Align or distribute the unlocked selected elements."""
        elements = [
            element
            for element in self.session.current_screen().elements
            if element.id in self.selected_element_ids
            and not element.locked
            and not element.editor_locked
        ]
        if len(elements) < 2:
            return
        left = min(element.x for element in elements)
        right = max(element.x + element.width for element in elements)
        top = min(element.y for element in elements)
        bottom = max(element.y + element.height for element in elements)
        if mode == "left":
            for element in elements:
                element.x = left
        elif mode == "hcenter":
            center = (left + right) / 2
            for element in elements:
                element.x = round(center - element.width / 2)
        elif mode == "top":
            for element in elements:
                element.y = top
        elif mode == "vcenter":
            center = (top + bottom) / 2
            for element in elements:
                element.y = round(center - element.height / 2)
        elif mode == "distribute_h" and len(elements) >= 3:
            ordered = sorted(elements, key=lambda element: element.x)
            width = sum(element.width for element in ordered)
            gap = (right - left - width) / (len(ordered) - 1)
            position = float(left)
            for element in ordered:
                element.x = round(position)
                position += element.width + gap
        elif mode == "distribute_v" and len(elements) >= 3:
            ordered = sorted(elements, key=lambda element: element.y)
            height = sum(element.height for element in ordered)
            gap = (bottom - top - height) / (len(ordered) - 1)
            position = float(top)
            for element in ordered:
                element.y = round(position)
                position += element.height + gap
        else:
            return
        self.session.mark_changed()

    def _refresh_element_list(self) -> None:
        """Refresh the active screen hierarchy list."""
        self.element_list.clear()
        current_item: QListWidgetItem | None = None
        for element in self.session.current_screen().elements:
            if element.locked:
                prefix = "[code] "
            elif element.editor_locked:
                prefix = "[lock] "
            elif not element.visible:
                prefix = "[hidden] "
            elif not element.enabled:
                prefix = "[disabled] "
            else:
                prefix = ""
            badges: list[str] = []
            if not element.visible:
                badges.append("Hidden")
            if element.locked or element.editor_locked:
                badges.append("Locked")
            if element.focusable:
                badges.append(f"Focus {element.focus_order}")
            if element.kind == "icon" and element.asset_runs:
                badges.append(element.asset_link_state.title())
            status = f"  [{' · '.join(badges)}]" if badges else ""
            kind_label = (
                _safe_native_widget_spec(element.native_widget).name
                if element.kind == "native"
                else element.kind.title()
            )
            identity = (
                kind_label
                if element.name.strip().casefold() == kind_label.casefold()
                else f"{element.name} · {kind_label}"
            )
            item = QListWidgetItem(f"{prefix}{identity}{status}")
            item.setData(Qt.ItemDataRole.UserRole, element.id)
            if element.source_path:
                state = (
                    "Locked dynamic code" if element.locked else "Editable source call"
                )
                item.setToolTip(f"{state}\n{element.source_path}:{element.source_line}")
            self.element_list.addItem(item)
            item.setSelected(element.id in self.selected_element_ids)
            if element.id == self.selected_element_id:
                current_item = item
        if current_item is not None:
            self.element_list.setCurrentItem(
                current_item, QItemSelectionModel.SelectionFlag.NoUpdate
            )
        self.canvas.set_selection(
            set(self.selected_element_ids), self.selected_element_id
        )

    def _element_list_selection_changed(self) -> None:
        """Synchronize the hierarchy multi-selection into the canvas."""
        if self._updating:
            return
        selected = {
            str(item.data(Qt.ItemDataRole.UserRole))
            for item in self.element_list.selectedItems()
        }
        current = self.element_list.currentItem()
        primary = (
            str(current.data(Qt.ItemDataRole.UserRole))
            if current is not None and current.isSelected()
            else next(iter(selected), None)
        )
        self.selected_element_ids = selected
        self.selected_element_id = primary
        self.canvas.set_selection(selected, primary)
        self._refresh_element_properties()

    def _canvas_selection_changed(self, values: object) -> None:
        """Synchronize a canvas multi-selection into the hierarchy."""
        if self._updating:
            return
        selected = set(values) if isinstance(values, set) else set()
        self.selected_element_ids = selected
        self.selected_element_id = self.canvas.selected_id
        self._updating = True
        for row in range(self.element_list.count()):
            item = self.element_list.item(row)
            item.setSelected(item.data(Qt.ItemDataRole.UserRole) in selected)
            if item.data(Qt.ItemDataRole.UserRole) == self.selected_element_id:
                self.element_list.setCurrentItem(
                    item, QItemSelectionModel.SelectionFlag.NoUpdate
                )
        self._refresh_element_properties()
        self._updating = False

    def _layers_reordered(self, *args: object) -> None:
        """Apply hierarchy drag order to the screen drawing order."""
        if self._updating:
            return
        screen = self.session.current_screen()
        by_id = {element.id: element for element in screen.elements}
        ordered = [
            by_id[str(self.element_list.item(row).data(Qt.ItemDataRole.UserRole))]
            for row in range(self.element_list.count())
            if str(self.element_list.item(row).data(Qt.ItemDataRole.UserRole)) in by_id
        ]
        if [element.id for element in ordered] == [
            element.id for element in screen.elements
        ]:
            return
        screen.elements = ordered
        self.session.mark_changed()

    def _select_element(self, element_id: str | None) -> None:
        """Select one active-screen element."""
        self.selected_element_id = element_id
        self.selected_element_ids = {element_id} if element_id else set()
        self.canvas.set_selection(self.selected_element_ids, element_id)
        self._updating = True
        for row in range(self.element_list.count()):
            item = self.element_list.item(row)
            selected = item.data(Qt.ItemDataRole.UserRole) == element_id
            item.setSelected(selected)
            if selected:
                self.element_list.setCurrentItem(
                    item, QItemSelectionModel.SelectionFlag.NoUpdate
                )
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
        set_widget_tooltip(
            self.screen_background_button,
            "screen_background_button",
            self,
            (
                "Edit the imported background draw element instead."
                if screen.source_path
                else "Choose the designer screen background."
            ),
        )

    def _refresh_element_properties(self) -> None:
        """Refresh controls for the selected element."""
        element = self._selected_element()
        selected = [
            item
            for item in self.session.current_screen().elements
            if item.id in self.selected_element_ids
        ]
        single_editable = (
            len(selected) == 1
            and element is not None
            and not element.locked
            and not element.editor_locked
        )
        self.property_group.setEnabled(single_editable)
        self.property_group.setVisible(element is not None and len(selected) == 1)
        self.property_empty_label.setVisible(element is None or len(selected) != 1)
        self.property_empty_label.setText(
            f"{len(selected)} elements selected. Use the selection actions above."
            if len(selected) > 1
            else "Select an element on the canvas or in the hierarchy to edit it."
        )
        linked_asset = bool(
            single_editable
            and element is not None
            and element.kind == "icon"
            and element.asset_key
        )
        asset_key = element.asset_key if element is not None else ""
        self.refresh_pixel_asset_button.setEnabled(
            linked_asset and asset_key in self.pixel_assets
        )
        self.edit_pixel_asset_button.setEnabled(linked_asset)
        self.library_save_selected_button.setEnabled(
            bool(element is not None and element.asset_id)
        )
        self.delete_element_button.setEnabled(
            bool(selected) and not any(item.source_path for item in selected)
        )
        self.duplicate_element_button.setEnabled(
            any(
                not item.source_path and not item.locked and not _is_screen_widget(item)
                for item in selected
            )
        )
        editable_locks = [item for item in selected if not item.locked]
        self.lock_element_button.setEnabled(bool(editable_locks))
        self.lock_element_button.setText(
            "Unlock"
            if editable_locks and all(item.editor_locked for item in editable_locks)
            else "Lock"
        )
        self.visibility_element_button.setEnabled(bool(selected))
        self.visibility_element_button.setText(
            "Show"
            if selected and not any(item.visible for item in selected)
            else "Hide"
        )
        current_order = [item.id for item in self.session.current_screen().elements]
        for mode, button in (
            ("front", self.bring_front_button),
            ("forward", self.move_forward_button),
            ("backward", self.move_backward_button),
            ("back", self.send_back_button),
        ):
            proposed = [item.id for item in self._ordered_elements_for_layer_move(mode)]
            button.setEnabled(bool(selected) and proposed != current_order)
        for mode, button in self.alignment_buttons.items():
            minimum = 3 if mode.startswith("distribute") else 2
            button.setEnabled(
                sum(not item.locked and not item.editor_locked for item in selected)
                >= minimum
            )
        if element is None:
            self.source_notice_label.clear()
            self.element_flow_label.clear()
            return
        if len(selected) > 1:
            locked_count = sum(item.locked or item.editor_locked for item in selected)
            self.source_notice_label.setText(
                f"{len(selected)} elements selected. {locked_count} locked."
            )
            return
        if element.locked:
            self.source_notice_label.setText(
                f"Locked dynamic code. Preserved unchanged.\n{element.source_path}:{element.source_line}"
            )
        elif element.source_path:
            self.source_notice_label.setText(
                f"Editable source call. Changes create a narrow patch.\n{element.source_path}:{element.source_line}"
            )
        elif element.editor_locked:
            self.source_notice_label.setText("Editor-locked layer. Unlock it to edit.")
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
        native = element.kind == "native"
        native_id = element.native_widget or "menu"
        native_spec = _safe_native_widget_spec(native_id)
        self.native_type_combo.setCurrentIndex(
            max(0, self.native_type_combo.findData(native_id))
        )
        self.native_summary_label.setText(native_spec.summary)
        self.native_role_label.setText(
            "Screen widget · owns the complete screen"
            if native_spec.full_screen
            else "Inline control · can share a custom layout"
        )
        self.widget_items_edit.setPlainText("\n".join(element.widget_items))
        self.widget_selected_combo.clear()
        self.widget_selected_combo.addItems(element.widget_items)
        if element.widget_items:
            self.widget_selected_combo.setCurrentIndex(
                min(element.widget_selected_index, len(element.widget_items) - 1)
            )
        self.widget_state_check.setChecked(element.widget_state)
        states = list(element.widget_item_states)
        if len(states) < len(element.widget_items):
            states.extend([False] * (len(element.widget_items) - len(states)))
        self.widget_item_states_list.clear()
        for index, text in enumerate(element.widget_items):
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if index < len(states) and states[index]
                else Qt.CheckState.Unchecked
            )
            self.widget_item_states_list.addItem(item)
        self.asset_call_edit.setText(element.asset_call)
        self.asset_call_edit.setEnabled(not bool(element.source_path))
        self.visible_check.setChecked(element.visible)
        self.enabled_check.setChecked(element.enabled)
        self.focusable_check.setChecked(element.focusable)
        self.focus_order_spin.setValue(element.focus_order)
        self.focus_order_spin.setEnabled(element.focusable)
        focus_style_index = self.focus_style_combo.findData(element.focus_style)
        self.focus_style_combo.setCurrentIndex(max(0, focus_style_index))
        self.focus_style_combo.setEnabled(element.focusable)
        self.focus_thickness_spin.setValue(element.focus_thickness)
        self.focus_padding_spin.setValue(element.focus_padding)
        focus_visible = element.focusable and element.focus_style != "none"
        self.focus_color_button.setEnabled(focus_visible)
        self.focus_thickness_spin.setEnabled(focus_visible)
        self.focus_padding_spin.setEnabled(focus_visible)
        self.event_name_edit.setText(element.event_name)
        self.asset_link_state_label.setText(element.asset_link_state.title())
        icon = element.kind == "icon"
        text_kind = element.kind in {"button", "label", "list"} or (
            native and native_spec.uses_text
        )
        interactive = element.kind in {"button", "icon", "list"} or (
            native and native_spec.interactive
        )
        self.native_properties_group.setVisible(native)
        native_form = self.native_properties_group.layout()
        if isinstance(native_form, QFormLayout):
            native_form.setRowVisible(self.widget_items_edit, native_spec.item_based)
            native_form.setRowVisible(
                self.widget_selected_combo, native_spec.supports_initial_selection
            )
            native_form.setRowVisible(
                self.widget_state_check, native_spec.supports_boolean_state
            )
            native_form.setRowVisible(
                self.widget_item_states_list, native_spec.supports_item_states
            )
        self.asset_properties_group.setVisible(icon)
        self.interaction_properties_group.setVisible(interactive)
        self.layout_properties_group.setVisible(
            not (native and native_spec.full_screen)
        )
        asset = (
            self.session.project.asset(element.asset_id) if element.asset_id else None
        )
        source_text, separator, unused_name = element.asset_key.rpartition("::")
        linked_source_available = bool(separator and Path(source_text).is_file())
        self.edit_pixel_asset_button.setEnabled(
            asset is not None or linked_source_available
        )
        self.edit_pixel_asset_button.setText(
            "Edit linked source in Pixel Editor"
            if linked_source_available
            else "Open asset in Pixel Editor"
        )
        set_widget_tooltip(
            self.edit_pixel_asset_button,
            "edit_pixel_asset_button",
            self,
            (
                "Open the linked Python graphic for reviewed source editing."
                if linked_source_available
                else "Open the project asset's lossless pixels for editing."
            ),
        )
        runtime_scale = asset_element_runtime_scale(element, asset)
        if icon and asset is not None:
            if runtime_scale is None:
                self.asset_size_status_label.setText(
                    f"Cannot run: element {element.width} x {element.height}; "
                    f"asset {asset.width} x {asset.height}. Use natural size or bake."
                )
                self.asset_size_status_label.setStyleSheet(
                    "color: #c62828; font-weight: 600;"
                )
            else:
                self.asset_size_status_label.setText(
                    f"Ready: {asset.width} x {asset.height} at {runtime_scale}x scale."
                )
                self.asset_size_status_label.setStyleSheet("color: #2e7d32;")
            screen = self.session.current_screen()
            self.asset_natural_size_button.setEnabled(
                asset.width <= screen.width and asset.height <= screen.height
            )
            self.asset_bake_size_button.setEnabled(
                1 <= element.width <= 320 and 1 <= element.height <= 320
            )
        else:
            self.asset_size_status_label.setText("No managed asset is attached.")
            self.asset_size_status_label.setStyleSheet("color: #666;")
            self.asset_natural_size_button.setEnabled(False)
            self.asset_bake_size_button.setEnabled(False)
        self.content_property_form.setRowVisible(self.element_text_edit, text_kind)
        self.content_property_form.setRowVisible(self.kind_combo, not native)
        self.content_property_form.setRowVisible(
            self.fill_color_button,
            not native or native_spec.uses_fill_color,
        )
        self.content_property_form.setRowVisible(
            self.border_color_button,
            not native or native_spec.uses_border_color,
        )
        self.content_property_form.setRowVisible(
            self.text_color_button,
            text_kind and (not native or native_spec.uses_text_color),
        )
        self.interaction_property_form.setRowVisible(self.enabled_check, interactive)
        show_focus = interactive and not (native and native_spec.full_screen)
        self.interaction_property_form.setRowVisible(self.focusable_check, show_focus)
        self.interaction_property_form.setRowVisible(self.event_name_edit, interactive)
        self.interaction_property_form.setRowVisible(
            self.element_flow_label, interactive
        )
        self.interaction_property_form.setRowVisible(self.open_flow_button, interactive)
        for widget in (
            self.focus_order_spin,
            self.focus_style_combo,
            self.focus_color_button,
            self.focus_thickness_spin,
            self.focus_padding_spin,
        ):
            self.interaction_property_form.setRowVisible(
                widget, show_focus and element.focusable
            )
        connection_count = sum(
            connection.source_element_id == element.id
            or connection.target_element_id == element.id
            for connection in self.session.project.connections
        )
        self.element_flow_label.setText(
            "No interactions yet."
            if connection_count == 0
            else f"{connection_count} interaction"
            f"{'s' if connection_count != 1 else ''}."
        )
        self.open_flow_button.setText(
            "Add interaction in Screen Flow..."
            if connection_count == 0
            else "Edit interactions in Screen Flow..."
        )
        self.open_flow_button.setEnabled(interactive and single_editable)
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

    def _asset_storage_changed(self) -> None:
        """Persist the selected generated-resource deployment strategy."""
        if self._updating:
            return
        mode = self.asset_storage_combo.currentData()
        if mode not in (ASSET_STORAGE_COMBINED, ASSET_STORAGE_INDIVIDUAL):
            return
        current = self.session.project.generated_app.get(
            "asset_storage", ASSET_STORAGE_COMBINED
        )
        if current == mode:
            return
        self.session.project.generated_app["asset_storage"] = mode
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
        """Apply dimensions while preserving every screen's relative layout."""
        project = self.session.project
        old_width = max(1, project.width)
        old_height = max(1, project.height)
        project.profile = profile
        project.width = width
        project.height = height
        for screen in project.screens:
            source_width = max(1, screen.width or old_width)
            source_height = max(1, screen.height or old_height)
            for element in screen.elements:
                self._resize_element_for_screen(
                    element,
                    source_width,
                    source_height,
                    width,
                    height,
                )
            screen.width = width
            screen.height = height
        self.session.mark_changed()

    @staticmethod
    def _resize_element_for_screen(
        element: GuiElement,
        old_width: int,
        old_height: int,
        new_width: int,
        new_height: int,
    ) -> None:
        """Map one element from the old screen coordinate space to the new one."""
        fills_screen = (
            element.x <= 0
            and element.y <= 0
            and element.width >= old_width
            and element.height >= old_height
        )
        if element.kind == "native":
            spec = _safe_native_widget_spec(element.native_widget)
            fills_screen = fills_screen or spec.full_screen
        if fills_screen:
            element.x, element.y = 0, 0
            element.width, element.height = new_width, new_height
            return

        left = round(element.x * new_width / old_width)
        top = round(element.y * new_height / old_height)
        right = round((element.x + element.width) * new_width / old_width)
        bottom = round((element.y + element.height) * new_height / old_height)

        if element.kind == "icon":
            scale = min(new_width / old_width, new_height / old_height)
            scaled_width = max(1, round(element.width * scale))
            scaled_height = max(1, round(element.height * scale))
            center_x = (left + right) / 2
            center_y = (top + bottom) / 2
            element.width = min(new_width, scaled_width)
            element.height = min(new_height, scaled_height)
            element.x = round(center_x - element.width / 2)
            element.y = round(center_y - element.height / 2)
        else:
            element.x = left
            element.y = top
            element.width = min(new_width, max(1, right - left))
            element.height = min(new_height, max(1, bottom - top))

        element.x = max(0, min(element.x, new_width - element.width))
        element.y = max(0, min(element.y, new_height - element.height))

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
        if element is None or element.locked or element.editor_locked:
            return
        old_event = element.activation_event()
        element.name = self.element_name_edit.text().strip() or element.name
        if element.kind != "native":
            element.kind = str(self.kind_combo.currentData())
        element.x = self.x_spin.value()
        element.y = self.y_spin.value()
        element.width = self.width_spin.value()
        element.height = self.height_spin.value()
        element.text = self.element_text_edit.text().replace("\\n", "\n")
        element.asset_call = self.asset_call_edit.text().strip()
        element.visible = self.visible_check.isChecked()
        element.enabled = self.enabled_check.isChecked()
        element.focusable = self.focusable_check.isChecked()
        element.focus_order = self.focus_order_spin.value()
        element.focus_style = str(self.focus_style_combo.currentData() or "outline")
        element.focus_thickness = self.focus_thickness_spin.value()
        element.focus_padding = self.focus_padding_spin.value()
        element.event_name = self.event_name_edit.text().strip()
        if element.kind == "native":
            element.native_widget = str(self.native_type_combo.currentData() or "menu")
            element.widget_items = [
                item.strip()
                for item in self.widget_items_edit.toPlainText().splitlines()
                if item.strip()
            ]
            spec = _safe_native_widget_spec(element.native_widget)
            selected_index = (
                self.widget_selected_combo.currentIndex()
                if spec.supports_initial_selection
                else 0
            )
            element.widget_selected_index = max(0, selected_index)
            element.widget_state = self.widget_state_check.isChecked()
            if spec.supports_item_states:
                element.widget_item_states = [
                    self.widget_item_states_list.item(index).checkState()
                    == Qt.CheckState.Checked
                    for index in range(self.widget_item_states_list.count())
                ]
            else:
                element.widget_item_states = []
        new_event = element.activation_event()
        if new_event != old_event:
            for connection in self.session.project.connections:
                if connection.source_element_id == element.id:
                    connection.trigger = new_event
        self.session.mark_changed()

    def _native_widget_type_changed(self) -> None:
        """Apply a changed native subtype and its appropriate interaction defaults."""
        if self._updating:
            return
        element = self._selected_element()
        if element is None or element.kind != "native":
            return
        widget_id = str(self.native_type_combo.currentData() or "menu")
        spec = native_widget_spec(widget_id)
        old_spec = _safe_native_widget_spec(element.native_widget)
        screen = self.session.current_screen()
        if spec.full_screen and any(item.id != element.id for item in screen.elements):
            QMessageBox.information(
                self,
                "Screen widget needs an empty screen",
                f"Picoware {spec.name} cannot replace this inline control while other "
                "layers remain on the screen.",
            )
            self._updating = True
            self.native_type_combo.setCurrentIndex(
                max(0, self.native_type_combo.findData(element.native_widget))
            )
            self._updating = False
            return
        old_text_was_default = element.text == old_spec.default_text
        old_items_were_default = tuple(element.widget_items) == old_spec.default_items
        element.native_widget = widget_id
        element.focusable = spec.interactive
        element.enabled = spec.interactive
        if not element.text or old_text_was_default:
            element.text = spec.default_text
        if spec.item_based and (not element.widget_items or old_items_were_default):
            element.widget_items = list(spec.default_items)
        elif not spec.item_based:
            element.widget_items = []
        element.widget_selected_index = min(
            element.widget_selected_index,
            max(0, len(element.widget_items) - 1),
        )
        element.widget_item_states = (
            [False] * len(element.widget_items) if spec.supports_item_states else []
        )
        if not spec.supports_boolean_state:
            element.widget_state = False
        if spec.full_screen:
            element.x, element.y = 0, 0
            element.width, element.height = screen.width, screen.height
        elif old_spec.full_screen:
            element.width = max(80, min(screen.width - 20, spec.default_width))
            element.height = max(32, min(screen.height - 20, spec.default_height))
            element.x = max(0, (screen.width - element.width) // 2)
            element.y = max(0, (screen.height - element.height) // 2)
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
        if element is None or element.locked or element.editor_locked:
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
            (self.focus_color_button, element.focus_color),
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

    event_requested = Signal(str)
    focus_changed = Signal(str)

    def __init__(self, session: DesignerSession, parent: QWidget | None = None):
        """Initialize the GUI flow preview."""
        super().__init__(parent)
        self.session = session
        self.preview_screen_id = session.project.start_screen_id
        self.focused_element_id: str | None = None
        self.setMinimumSize(260, 220)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_screen(self, screen_id: str, focused_element_id: str = "") -> None:
        """Set the screen shown in the simulator preview."""
        self.preview_screen_id = screen_id
        self.focused_element_id = focused_element_id or None
        if self._focused_element() is None:
            self.focused_element_id = None
            self._move_focus(0)
        else:
            element = self._focused_element()
            self.focus_changed.emit(element.name if element is not None else "")
        self.update()

    def paintEvent(self, event) -> None:
        """Paint the simulated active screen."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#202020"))
        screen = self.session.project.screen(self.preview_screen_id)
        if screen is not None:
            draw_screen(
                painter,
                screen,
                self._screen_target(screen),
                focused_id=self.focused_element_id,
            )
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Focus and activate a clicked interactive preview element."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        screen = self.session.project.screen(self.preview_screen_id)
        if screen is None:
            return
        point = self._screen_point(screen, event.position())
        element = next(
            (
                item
                for item in reversed(screen.elements)
                if item.visible
                and item.enabled
                and item.focusable
                and QRectF(item.x, item.y, item.width, item.height).contains(point)
            ),
            None,
        )
        if element is None:
            return
        self.focused_element_id = element.id
        self.focus_changed.emit(element.name)
        self.event_requested.emit(element.activation_event())
        self.update()
        self.setFocus()
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Navigate and activate focusable preview elements by keyboard."""
        if event.key() in {
            Qt.Key.Key_Tab,
            Qt.Key.Key_Right,
            Qt.Key.Key_Down,
        }:
            self._move_focus(1)
            event.accept()
            return
        if event.key() in {Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_Backtab}:
            self._move_focus(-1)
            event.accept()
            return
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space}:
            element = self._focused_element()
            if element is not None:
                self.event_requested.emit(element.activation_event())
                event.accept()
                return
        super().keyPressEvent(event)

    def _focusable_elements(self) -> list[GuiElement]:
        """Return visible interactive elements in keyboard focus order."""
        screen = self.session.project.screen(self.preview_screen_id)
        if screen is None:
            return []
        indexed = [
            (index, element)
            for index, element in enumerate(screen.elements)
            if element.visible and element.enabled and element.focusable
        ]
        return [
            element
            for index, element in sorted(
                indexed, key=lambda item: (item[1].focus_order, item[0])
            )
        ]

    def _move_focus(self, offset: int) -> None:
        """Move preview focus by one ordered element offset."""
        elements = self._focusable_elements()
        if not elements:
            self.focused_element_id = None
            self.focus_changed.emit("")
            self.update()
            return
        current = next(
            (
                index
                for index, element in enumerate(elements)
                if element.id == self.focused_element_id
            ),
            -1,
        )
        index = 0 if current < 0 else (current + offset) % len(elements)
        self.focused_element_id = elements[index].id
        self.focus_changed.emit(elements[index].name)
        self.update()

    def _focused_element(self) -> GuiElement | None:
        """Return the currently focused preview element."""
        return next(
            (
                element
                for element in self._focusable_elements()
                if element.id == self.focused_element_id
            ),
            None,
        )

    def _screen_target(self, screen: ScreenDesign) -> QRectF:
        """Return the fitted preview rectangle for one screen."""
        available = QRectF(self.rect()).adjusted(10, 10, -10, -10)
        scale = min(
            available.width() / screen.width,
            available.height() / screen.height,
        )
        width = screen.width * scale
        height = screen.height * scale
        return QRectF(
            available.x() + (available.width() - width) / 2,
            available.y() + (available.height() - height) / 2,
            width,
            height,
        )

    def _screen_point(self, screen: ScreenDesign, point: QPointF) -> QPointF:
        """Map one preview point into device screen coordinates."""
        target = self._screen_target(screen)
        if not target.contains(point):
            return QPointF(-1, -1)
        return QPointF(
            (point.x() - target.x()) * screen.width / target.width(),
            (point.y() - target.y()) * screen.height / target.height(),
        )


class FlowCanvas(QWidget):
    """Draw and directly arrange screen nodes and their relationships."""

    screen_selected = Signal(str)
    screen_activated = Signal(str)
    connection_requested = Signal(str, str, str, str)
    element_behavior_dropped = Signal(str, str, int, int)
    connection_selected = Signal(str)
    connection_delete_requested = Signal(str)
    behavior_node_selected = Signal(str)
    behavior_connection_requested = Signal(str, str, str, str)
    behavior_connection_dropped = Signal(str, str, int, int)
    behavior_connection_selected = Signal(str)
    behavior_nodes_delete_requested = Signal(object)
    behavior_connection_delete_requested = Signal(str)
    scroll_requested = Signal(int, int)
    zoom_changed = Signal(float)
    interaction_feedback = Signal(str, str)
    geometry_changed = Signal()

    NODE_WIDTH = 200
    NODE_HEIGHT = 140
    ELEMENT_ROW_HEIGHT = 22
    PORT_RADIUS = 7
    BEHAVIOR_NODE_WIDTH = 190
    BEHAVIOR_HEADER_HEIGHT = 34
    BEHAVIOR_PORT_HEIGHT = 24
    MIN_ZOOM = 0.05
    MAX_ZOOM = 2.0

    def __init__(self, session: DesignerSession, parent: QWidget | None = None):
        """Initialize the draggable screen graph."""
        super().__init__(parent)
        self.session = session
        self.selected_screen_id: str | None = None
        self.selected_connection_id: str | None = None
        self.selected_behavior_node_ids: set[str] = set()
        self.primary_behavior_node_id: str | None = None
        self.selected_behavior_connection_id: str | None = None
        self.active_trace_node_ids: set[str] = set()
        self.active_trace_connection_ids: set[str] = set()
        self.active_trace_screen_id = ""
        self.node_diagnostic_severity: dict[str, str] = {}
        self.visibility_mode = "both"
        self.zoom = 1.0
        self._drag_offset = QPointF()
        self._node_dragging = False
        self._behavior_node_dragging = False
        self._behavior_drag_origins: dict[str, tuple[int, int]] = {}
        self._panning = False
        self._pan_position = QPointF()
        self._connection_source_id: str | None = None
        self._connection_source_element_id: str | None = None
        self._connection_point = QPointF()
        self._connection_target_id: str | None = None
        self._connection_target_element_id: str | None = None
        self._behavior_connection_source: tuple[str, str] | None = None
        self._behavior_connection_target: tuple[str, str] | None = None
        self._behavior_connection_rejected_target: tuple[str, str] | None = None
        self._behavior_connection_point = QPointF()
        self._behavior_clipboard: dict[str, object] = {}
        self._marquee_start = QPointF()
        self._marquee_end = QPointF()
        self._marquee_modifiers = Qt.KeyboardModifier.NoModifier
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(1400, 900)
        self.refresh_geometry()

    def paintEvent(self, event) -> None:
        """Paint graph connections followed by screen nodes."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#282c32"))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.scale(self.zoom, self.zoom)
        visible = QRectF(event.rect())
        visible = QRectF(
            visible.x() / self.zoom,
            visible.y() / self.zoom,
            visible.width() / self.zoom,
            visible.height() / self.zoom,
        ).adjusted(-80, -80, 80, 80)
        project = self.session.project
        if self.visibility_mode != "screens":
            for group in project.flow_groups:
                if self._flow_group_rectangle(group).intersects(visible):
                    self._draw_flow_group(painter, group)
        if self.visibility_mode != "behavior":
            for connection in project.connections:
                source = project.screen(connection.source_id)
                target = project.screen(connection.target_id)
                if source is not None and target is not None:
                    self._draw_connection(
                        painter, source, target, connection, visible_rectangle=visible
                    )
        if self.visibility_mode != "screens":
            visible_node_ids = {node.id for node in self._visible_behavior_nodes()}
            for connection in project.behavior_connections:
                if (
                    connection.source_node_id in visible_node_ids
                    and connection.target_node_id in visible_node_ids
                ):
                    self._draw_behavior_connection(
                        painter, connection, visible_rectangle=visible
                    )
        source = project.screen(self._connection_source_id or "")
        if source is not None:
            self._draw_connection_preview(
                painter,
                source,
                self._connection_source_element_id or "",
            )
        if self._behavior_connection_source is not None:
            self._draw_behavior_connection_preview(painter)
        for screen in self._visible_screens():
            if self._screen_node_rectangle(screen).intersects(visible):
                self._draw_node(painter, screen)
        for node in self._visible_behavior_nodes():
            if self._behavior_node_rectangle(node).intersects(visible):
                self._draw_behavior_node(painter, node)
        if not self._marquee_start.isNull() and not self._marquee_end.isNull():
            marquee = QRectF(self._marquee_start, self._marquee_end).normalized()
            painter.setBrush(QColor(0, 191, 255, 35))
            painter.setPen(QPen(QColor("#00bfff"), 1, Qt.PenStyle.DashLine))
            painter.drawRect(marquee)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Begin a node move or connection drag."""
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_position = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = self._graph_point(event.position())
        behavior_output = self._behavior_output_at(point)
        if behavior_output is not None:
            node, port = behavior_output
            self._select_behavior_node(node.id, event.modifiers())
            self._behavior_connection_source = (node.id, port.id)
            self._behavior_connection_target = None
            self._behavior_connection_rejected_target = None
            self._behavior_connection_point = point
            self._node_dragging = False
            self._behavior_node_dragging = False
            self.interaction_feedback.emit(
                f"Connecting {node.name}.{port.name}. Choose a highlighted input or drop on empty space to add a compatible node.",
                "info",
            )
            self.update()
            event.accept()
            return
        output = self._output_endpoint_at(point)
        if output is not None:
            output_screen, output_element_id = output
            self.selected_screen_id = output_screen.id
            self.selected_connection_id = None
            self._connection_source_id = output_screen.id
            self._connection_source_element_id = output_element_id
            self._connection_point = point
            self._connection_target_id = None
            self._connection_target_element_id = None
            self._node_dragging = False
            self._behavior_node_dragging = False
            self.screen_selected.emit(output_screen.id)
            if output_element_id:
                element = self.session.project.element(
                    output_screen.id, output_element_id
                )
                element_name = element.name if element is not None else "element"
                self.interaction_feedback.emit(
                    f"Dragging {element_name}. Drop on a screen input to navigate, or on empty space to choose an action.",
                    "info",
                )
            self.update()
            event.accept()
            return
        behavior_node = self._behavior_node_at(point)
        if behavior_node is not None:
            self._select_behavior_node(behavior_node.id, event.modifiers())
            self.selected_screen_id = None
            self.selected_connection_id = None
            self.selected_behavior_connection_id = None
            self._behavior_node_dragging = not behavior_node.locked
            self._node_dragging = False
            if self._behavior_node_dragging:
                self.session.begin_transaction()
                self._drag_offset = QPointF(
                    point.x() - behavior_node.node_x,
                    point.y() - behavior_node.node_y,
                )
                self._behavior_drag_origins = {
                    node.id: (node.node_x, node.node_y)
                    for node in self.session.project.behavior_nodes
                    if node.id in self.selected_behavior_node_ids and not node.locked
                }
            self.update()
            self.setFocus()
            event.accept()
            return
        screen = self._screen_at(point)
        if screen is None:
            behavior_connection = self._behavior_connection_at(point)
            connection = (
                self._connection_at(point) if behavior_connection is None else None
            )
            self.selected_screen_id = None
            self.selected_behavior_node_ids.clear()
            self.primary_behavior_node_id = None
            self.selected_connection_id = (
                connection.id if connection is not None else None
            )
            self.selected_behavior_connection_id = (
                behavior_connection.id if behavior_connection is not None else None
            )
            self._node_dragging = False
            self._behavior_node_dragging = False
            if connection is not None:
                self.connection_selected.emit(connection.id)
            elif behavior_connection is not None:
                self.behavior_connection_selected.emit(behavior_connection.id)
            else:
                self._marquee_start = point
                self._marquee_end = point
                self._marquee_modifiers = event.modifiers()
            self.update()
            self.setFocus()
            return
        self.selected_screen_id = screen.id
        self.selected_connection_id = None
        self.selected_behavior_node_ids.clear()
        self.primary_behavior_node_id = None
        self.selected_behavior_connection_id = None
        self._node_dragging = True
        self.session.begin_transaction()
        self._drag_offset = QPointF(
            point.x() - screen.node_x, point.y() - screen.node_y
        )
        self.screen_selected.emit(screen.id)
        self.update()
        self.setFocus()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Move a node or preview a dragged connection."""
        if self._panning and event.buttons() & Qt.MouseButton.MiddleButton:
            delta = event.position() - self._pan_position
            self._pan_position = event.position()
            self.scroll_requested.emit(-round(delta.x()), -round(delta.y()))
            event.accept()
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        point = self._graph_point(event.position())
        if not self._marquee_start.isNull():
            self._marquee_end = point
            self.update()
            return
        if self._behavior_connection_source is not None:
            raw_target = self._behavior_input_at(point, compatible_only=False)
            issue = (
                self._behavior_input_issue(raw_target[0], raw_target[1])
                if raw_target is not None
                else ""
            )
            target = raw_target if raw_target is not None and not issue else None
            self._behavior_connection_target = (
                (target[0].id, target[1].id) if target is not None else None
            )
            self._behavior_connection_rejected_target = (
                (raw_target[0].id, raw_target[1].id)
                if raw_target is not None and issue
                else None
            )
            self._behavior_connection_point = (
                self._behavior_port_position(target[0], target[1])
                if target is not None
                else point
            )
            if target is not None:
                self.interaction_feedback.emit(
                    f"Release to connect to {target[0].name}.{target[1].name}.",
                    "success",
                )
            elif issue:
                self.interaction_feedback.emit(issue, "error")
            else:
                self.interaction_feedback.emit(
                    "Choose a highlighted compatible input, or release on empty space for suggestions.",
                    "info",
                )
            self.update()
            return
        if self._connection_source_id is not None:
            target = self._connection_target_at(point)
            self._connection_target_id = target[0].id if target is not None else None
            self._connection_target_element_id = (
                target[1] if target is not None else None
            )
            self._connection_point = (
                self._endpoint_input_port(target[0], target[1])
                if target is not None
                else point
            )
            if target is not None:
                self.interaction_feedback.emit(
                    f"Release to navigate to {target[0].name}.", "success"
                )
            elif self._connection_source_element_id:
                self.interaction_feedback.emit(
                    "Release on empty space to choose an action for this widget event.",
                    "info",
                )
            self.update()
            return
        if self._behavior_node_dragging:
            primary = self.session.project.flow_node(
                self.primary_behavior_node_id or ""
            )
            if primary is None:
                return
            next_x = max(10, round(point.x() - self._drag_offset.x()))
            next_y = max(10, round(point.y() - self._drag_offset.y()))
            origin = self._behavior_drag_origins.get(primary.id)
            if origin is None:
                return
            delta_x = next_x - origin[0]
            delta_y = next_y - origin[1]
            for node_id, (start_x, start_y) in self._behavior_drag_origins.items():
                node = self.session.project.flow_node(node_id)
                if node is not None:
                    node.node_x = max(10, start_x + delta_x)
                    node.node_y = max(10, start_y + delta_y)
            self.refresh_geometry()
            self.session.mark_changed(False)
            self.update()
            return
        if not self._node_dragging:
            return
        screen = self.session.project.screen(self.selected_screen_id or "")
        if screen is None:
            return
        screen.node_x = max(10, round(point.x() - self._drag_offset.x()))
        screen.node_y = max(10, round(point.y() - self._drag_offset.y()))
        self.refresh_geometry()
        self.session.mark_changed(False)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Finish a node move or create the dragged connection."""
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            self._pan_position = QPointF()
            self.unsetCursor()
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        source_id = self._connection_source_id
        source_element_id = self._connection_source_element_id or ""
        target_id = self._connection_target_id
        target_element_id = self._connection_target_element_id or ""
        node_dragging = self._node_dragging
        behavior_node_dragging = self._behavior_node_dragging
        behavior_source = self._behavior_connection_source
        behavior_target = self._behavior_connection_target
        behavior_drop_point = self._graph_point(event.position())
        rejected_target = self._behavior_input_at(
            behavior_drop_point, compatible_only=False
        )
        rejected_issue = (
            self._behavior_input_issue(rejected_target[0], rejected_target[1])
            if behavior_source and rejected_target is not None
            else ""
        )
        marquee_start = self._marquee_start
        marquee_end = self._marquee_end
        marquee_modifiers = self._marquee_modifiers
        self._connection_source_id = None
        self._connection_source_element_id = None
        self._connection_target_id = None
        self._connection_target_element_id = None
        self._connection_point = QPointF()
        self._node_dragging = False
        self._behavior_node_dragging = False
        self._behavior_drag_origins.clear()
        self._behavior_connection_source = None
        self._behavior_connection_target = None
        self._behavior_connection_rejected_target = None
        self._behavior_connection_point = QPointF()
        self._marquee_start = QPointF()
        self._marquee_end = QPointF()
        self._marquee_modifiers = Qt.KeyboardModifier.NoModifier
        self.update()
        if behavior_node_dragging:
            self._sync_selected_group_bounds()
        if node_dragging or behavior_node_dragging:
            self.session.end_transaction()
            self.geometry_changed.emit()
        if source_id and target_id:
            self.connection_requested.emit(
                source_id,
                source_element_id,
                target_id,
                target_element_id,
            )
        elif source_id and source_element_id:
            self.element_behavior_dropped.emit(
                source_id,
                source_element_id,
                round(behavior_drop_point.x()),
                round(behavior_drop_point.y()),
            )
        if behavior_source and behavior_target:
            self.behavior_connection_requested.emit(
                behavior_source[0],
                behavior_source[1],
                behavior_target[0],
                behavior_target[1],
            )
            self.interaction_feedback.emit(
                "Connection created. Validation updated automatically.", "success"
            )
        elif behavior_source and rejected_target is not None:
            self.interaction_feedback.emit(
                rejected_issue,
                "error",
            )
        elif behavior_source:
            self.behavior_connection_dropped.emit(
                behavior_source[0],
                behavior_source[1],
                round(behavior_drop_point.x()),
                round(behavior_drop_point.y()),
            )
        if not marquee_start.isNull() and not marquee_end.isNull():
            marquee = QRectF(marquee_start, marquee_end).normalized()
            selected = {
                node.id
                for node in self._visible_behavior_nodes()
                if marquee.intersects(self._behavior_node_rectangle(node))
            }
            if marquee_modifiers & Qt.KeyboardModifier.ControlModifier:
                self.selected_behavior_node_ids ^= selected
            else:
                self.selected_behavior_node_ids = selected
            self.primary_behavior_node_id = next(
                iter(self.selected_behavior_node_ids), None
            )
            self.behavior_node_selected.emit(self.primary_behavior_node_id or "")
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Open a double-clicked screen in the GUI designer."""
        point = self._graph_point(event.position())
        group = self._flow_group_at(point)
        if group is not None and group.collapsed:
            group.collapsed = False
            self.session.mark_changed()
            self.interaction_feedback.emit(f"Expanded group {group.name}.", "success")
            event.accept()
            return
        screen = self._screen_at(point)
        if screen is not None:
            self.screen_activated.emit(screen.id)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Zoom the node graph with the mouse wheel."""
        delta = event.angleDelta().y()
        if not delta:
            super().wheelEvent(event)
            return
        old_zoom = self.zoom
        new_zoom = max(
            self.MIN_ZOOM,
            min(self.MAX_ZOOM, round(old_zoom + (0.1 if delta > 0 else -0.1), 2)),
        )
        if new_zoom != old_zoom:
            position = event.position()
            factor = new_zoom / old_zoom
            self.set_zoom(new_zoom)
            self.scroll_requested.emit(
                round(position.x() * (factor - 1)),
                round(position.y() * (factor - 1)),
            )
        event.accept()

    def set_zoom(self, zoom: float, *, allow_fit_overview: bool = False) -> None:
        """Set graph zoom within its manual or fit-overview range."""
        minimum = 0.01 if allow_fit_overview else self.MIN_ZOOM
        value = max(minimum, min(self.MAX_ZOOM, float(zoom)))
        if value == self.zoom:
            return
        self.zoom = value
        self.refresh_geometry()
        self.zoom_changed.emit(value)
        self.update()

    def refresh_geometry(self) -> None:
        """Resize the canvas to include all scaled graph nodes."""
        project = self.session.project
        right = max(
            (screen.node_x + self.NODE_WIDTH for screen in self._visible_screens()),
            default=0,
        )
        bottom = max(
            (
                screen.node_y + self._node_height(screen)
                for screen in self._visible_screens()
            ),
            default=0,
        )
        for node in self._visible_behavior_nodes():
            right = max(right, node.node_x + self.BEHAVIOR_NODE_WIDTH)
            bottom = max(bottom, node.node_y + self._behavior_node_height(node))
        if self.visibility_mode != "screens":
            for group in project.flow_groups:
                right = max(right, group.node_x + group.width)
                bottom = max(bottom, group.node_y + group.height)
        width = max(1400, round((right + 120) * self.zoom))
        height = max(900, round((bottom + 120) * self.zoom))
        required = QSize(width, height)
        if self.size() != required:
            self.resize(required)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Delete the selected design relation from the graph."""
        if event.matches(QKeySequence.StandardKey.Copy):
            self._copy_selected_behavior_nodes()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Paste):
            self._paste_behavior_nodes()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.SelectAll):
            self.selected_behavior_node_ids = {
                node.id for node in self._visible_behavior_nodes()
            }
            self.primary_behavior_node_id = next(
                iter(self.selected_behavior_node_ids), None
            )
            self.update()
            event.accept()
            return
        if (
            event.key()
            in {
                Qt.Key.Key_Left,
                Qt.Key.Key_Right,
                Qt.Key.Key_Up,
                Qt.Key.Key_Down,
            }
            and self.selected_behavior_node_ids
        ):
            step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            delta = {
                Qt.Key.Key_Left: (-step, 0),
                Qt.Key.Key_Right: (step, 0),
                Qt.Key.Key_Up: (0, -step),
                Qt.Key.Key_Down: (0, step),
            }[event.key()]
            changed = False
            for node in self.session.project.behavior_nodes:
                if node.id in self.selected_behavior_node_ids and not node.locked:
                    node.node_x = max(10, node.node_x + delta[0])
                    node.node_y = max(10, node.node_y + delta[1])
                    changed = True
            if changed:
                self.session.mark_changed(False)
                self.refresh_geometry()
                self.update()
            event.accept()
            return
        if event.key() in {Qt.Key.Key_Delete, Qt.Key.Key_Backspace}:
            if self.selected_behavior_node_ids:
                self.behavior_nodes_delete_requested.emit(
                    set(self.selected_behavior_node_ids)
                )
                event.accept()
                return
            if self.selected_behavior_connection_id:
                self.behavior_connection_delete_requested.emit(
                    self.selected_behavior_connection_id
                )
                event.accept()
                return
            if self.selected_connection_id:
                self.connection_delete_requested.emit(self.selected_connection_id)
                event.accept()
                return
        super().keyPressEvent(event)

    def _draw_node(self, painter: QPainter, screen: ScreenDesign) -> None:
        """Draw one screen node."""
        rectangle = QRectF(
            screen.node_x,
            screen.node_y,
            self.NODE_WIDTH,
            self._node_height(screen),
        )
        selected = screen.id == self.selected_screen_id
        traced = screen.id == self.active_trace_screen_id
        start = screen.id == self.session.project.start_screen_id
        color = QColor("#43a047") if start else QColor("#4c566a")
        painter.setBrush(color)
        painter.setPen(
            QPen(
                QColor("#ffeb3b")
                if traced
                else QColor("#00bfff")
                if selected
                else QColor("#d8dee9"),
                4 if traced else 3 if selected else 1,
            )
        )
        painter.drawRoundedRect(rectangle, 8, 8)
        preview = QRectF(
            screen.node_x + 8,
            screen.node_y + 28,
            self.NODE_WIDTH - 16,
            self.NODE_HEIGHT - 36,
        )
        live_image = self.session.live_screen_images.get(screen.id)
        if live_image is None:
            draw_screen(painter, screen, preview)
        else:
            painter.fillRect(preview, QColor("#20242a"))
            draw_fitted_image(painter, live_image, preview)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#7f8b99"), 1))
        painter.drawRect(preview)
        painter.setPen(QColor("white"))
        painter.drawText(
            QRectF(screen.node_x + 28, screen.node_y + 3, self.NODE_WIDTH - 56, 22),
            Qt.AlignmentFlag.AlignCenter,
            screen.name,
        )
        if screen.source_path:
            painter.drawText(
                QPointF(screen.node_x + self.NODE_WIDTH - 72, screen.node_y + 17),
                "SOURCE",
            )
        if live_image is not None:
            painter.drawText(
                QPointF(screen.node_x + self.NODE_WIDTH - 35, screen.node_y + 17),
                "LIVE",
            )
        if start:
            painter.drawText(QPointF(screen.node_x + 7, screen.node_y + 17), "START")
        input_color = (
            QColor("#ebcb8b")
            if screen.id == self._connection_target_id
            and not self._connection_target_element_id
            else QColor("#a3be8c")
        )
        painter.setPen(QPen(QColor("#20242a"), 1))
        painter.setBrush(input_color)
        painter.drawEllipse(
            self._input_port(screen), self.PORT_RADIUS, self.PORT_RADIUS
        )
        output_color = (
            QColor("#00bfff")
            if screen.id == self._connection_source_id
            and not self._connection_source_element_id
            else QColor("#5e81ac")
        )
        painter.setBrush(output_color)
        painter.drawEllipse(
            self._output_port(screen), self.PORT_RADIUS, self.PORT_RADIUS
        )
        painter.setPen(QColor("white"))
        painter.drawText(self._input_port(screen) + QPointF(10, 4), "IN")
        painter.drawText(self._output_port(screen) + QPointF(-31, 4), "OUT")
        for index, element in enumerate(self._navigation_elements(screen)):
            active = element.visible and element.enabled and element.focusable
            row = QRectF(
                screen.node_x + 8,
                screen.node_y + self.NODE_HEIGHT + index * self.ELEMENT_ROW_HEIGHT + 2,
                self.NODE_WIDTH - 16,
                self.ELEMENT_ROW_HEIGHT - 4,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#343a43" if active else "#292d33"))
            painter.drawRoundedRect(row, 3, 3)
            painter.setPen(QColor("#eceff4" if active else "#7f8b99"))
            label = f"{element.kind}: {element.name}  [{element.activation_event()}]"
            painter.drawText(
                row.adjusted(5, 0, -5, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                label,
            )
            input_color = (
                QColor("#ebcb8b")
                if screen.id == self._connection_target_id
                and element.id == self._connection_target_element_id
                else QColor("#a3be8c")
            )
            painter.setPen(QPen(QColor("#20242a"), 1))
            painter.setBrush(input_color)
            painter.drawEllipse(
                self._element_input_port(screen, element),
                self.PORT_RADIUS - 1,
                self.PORT_RADIUS - 1,
            )
            output_color = (
                QColor("#00bfff")
                if screen.id == self._connection_source_id
                and element.id == self._connection_source_element_id
                else QColor("#5e81ac")
            )
            painter.setBrush(output_color)
            painter.drawEllipse(
                self._element_output_port(screen, element),
                self.PORT_RADIUS - 1,
                self.PORT_RADIUS - 1,
            )

    def _screen_node_rectangle(self, screen: ScreenDesign) -> QRectF:
        """Return the complete graph-space bounds of one screen node."""
        return QRectF(
            screen.node_x,
            screen.node_y,
            self.NODE_WIDTH,
            self._node_height(screen),
        )

    def _behavior_node_rectangle(self, node: FlowNode) -> QRectF:
        """Return the complete graph-space bounds of one behavior node."""
        return QRectF(
            node.node_x,
            node.node_y,
            self.BEHAVIOR_NODE_WIDTH,
            self._behavior_node_height(node),
        )

    @staticmethod
    def _flow_group_rectangle(group: FlowGroup) -> QRectF:
        """Return the graph-space bounds of one visual flow group."""
        return QRectF(group.node_x, group.node_y, group.width, group.height)

    def _flow_group_at(self, point: QPointF) -> FlowGroup | None:
        """Return the topmost visual group at one graph point."""
        if self.visibility_mode == "screens":
            return None
        for group in reversed(self.session.project.flow_groups):
            if self._flow_group_rectangle(group).contains(point):
                return group
        return None

    def _sync_selected_group_bounds(self) -> None:
        """Move visual group bounds with directly moved member nodes."""
        group_ids = {
            node.group_id
            for node in self.session.project.behavior_nodes
            if node.id in self.selected_behavior_node_ids and node.group_id
        }
        for group_id in group_ids:
            group = self.session.project.flow_group(group_id)
            members = [
                node
                for node in self.session.project.behavior_nodes
                if node.group_id == group_id
            ]
            if group is None or not members:
                continue
            group.node_x = min(node.node_x for node in members) - 30
            group.node_y = min(node.node_y for node in members) - 36
            group.width = (
                max(node.node_x + self.BEHAVIOR_NODE_WIDTH for node in members)
                - group.node_x
                + 30
            )
            group.height = (
                max(node.node_y + self._behavior_node_height(node) for node in members)
                - group.node_y
                + 30
            )

    def _draw_flow_group(self, painter: QPainter, group: FlowGroup) -> None:
        """Draw one visual organization region behind behavior nodes."""
        rectangle = self._flow_group_rectangle(group)
        color = QColor(group.color)
        color.setAlpha(55)
        painter.setBrush(color)
        painter.setPen(QPen(QColor(group.color), 2, Qt.PenStyle.DashLine))
        painter.drawRoundedRect(rectangle, 10, 10)
        painter.setPen(QColor("white"))
        suffix = " (collapsed)" if group.collapsed else ""
        painter.drawText(
            rectangle.adjusted(8, 4, -8, -4),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
            group.name + suffix,
        )

    def _draw_behavior_node(self, painter: QPainter, node: FlowNode) -> None:
        """Draw one typed behavior contract and its named ports."""
        rectangle = self._behavior_node_rectangle(node)
        colors = {
            "event": "#1565c0",
            "condition": "#8e24aa",
            "action": "#ef6c00",
            "state": "#00897b",
            "timer": "#5d4037",
            "data": "#546e7a",
            "component": "#3949ab",
            "comment": "#6d6d45",
        }
        selected = node.id in self.selected_behavior_node_ids
        traced = node.id in self.active_trace_node_ids
        diagnostic = self.node_diagnostic_severity.get(node.id, "")
        diagnostic_color = {
            "error": QColor("#ff5252"),
            "warning": QColor("#ffb300"),
            "info": QColor("#90a4ae"),
        }.get(diagnostic)
        painter.setBrush(QColor(colors.get(node.kind, "#546e7a")))
        painter.setPen(
            QPen(
                QColor("#ffeb3b")
                if traced
                else QColor("#00bfff")
                if selected
                else diagnostic_color
                if diagnostic_color is not None
                else QColor("#d8dee9"),
                4 if traced else 3 if selected or diagnostic_color is not None else 1,
            )
        )
        painter.drawRoundedRect(rectangle, 7, 7)
        painter.setPen(QColor("white"))
        painter.drawText(
            QRectF(
                node.node_x + 8,
                node.node_y + 4,
                self.BEHAVIOR_NODE_WIDTH - 16,
                self.BEHAVIOR_HEADER_HEIGHT - 8,
            ),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            f"{node.kind.upper()} · {node.name}",
        )
        if node.pinned:
            painter.drawText(
                QPointF(node.node_x + self.BEHAVIOR_NODE_WIDTH - 28, node.node_y + 20),
                "PIN",
            )
        if node.breakpoint:
            painter.setBrush(QColor("#ef5350"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(
                QPointF(node.node_x + self.BEHAVIOR_NODE_WIDTH - 12, node.node_y + 12),
                5,
                5,
            )
        if diagnostic_color is not None:
            painter.setBrush(diagnostic_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(node.node_x + 12, node.node_y + 12), 7, 7)
            painter.setPen(QColor("#101418"))
            painter.drawText(
                QRectF(node.node_x + 7, node.node_y + 4, 10, 16),
                Qt.AlignmentFlag.AlignCenter,
                "!",
            )
        for port in node.ports:
            position = self._behavior_port_position(node, port)
            painter.setPen(QPen(QColor("#20242a"), 1))
            color = {
                "event": QColor("#a3be8c"),
                "data": QColor("#64b5f6"),
                "any": QColor("#4dd0e1"),
                "string": QColor("#42a5f5"),
                "boolean": QColor("#ffca28"),
                "integer": QColor("#ab47bc"),
            }.get(port.data_type, QColor("#90a4ae"))
            if port.direction == "in" and self._behavior_connection_source:
                source = self.session.project.flow_node(
                    self._behavior_connection_source[0]
                )
                source_port = (
                    source.port(self._behavior_connection_source[1]) if source else None
                )
                if source_port and not (
                    source_port.data_type == port.data_type
                    or "any" in {source_port.data_type, port.data_type}
                ):
                    color = QColor("#4b5159")
            if self._behavior_connection_target == (node.id, port.id):
                color = QColor("#69f0ae")
            elif self._behavior_connection_rejected_target == (node.id, port.id):
                color = QColor("#ff5252")
            painter.setBrush(color)
            radius = (
                self.PORT_RADIUS + 2
                if self._behavior_connection_target == (node.id, port.id)
                or self._behavior_connection_rejected_target == (node.id, port.id)
                else self.PORT_RADIUS - 1
            )
            painter.drawEllipse(position, radius, radius)
            painter.setPen(QColor("white"))
            if port.direction == "in":
                text_rectangle = QRectF(
                    node.node_x + 10,
                    position.y() - 10,
                    self.BEHAVIOR_NODE_WIDTH / 2 - 15,
                    20,
                )
                alignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            else:
                text_rectangle = QRectF(
                    node.node_x + self.BEHAVIOR_NODE_WIDTH / 2,
                    position.y() - 10,
                    self.BEHAVIOR_NODE_WIDTH / 2 - 10,
                    20,
                )
                alignment = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            painter.drawText(text_rectangle, alignment, port.name)

    def _draw_behavior_connection(
        self,
        painter: QPainter,
        connection: BehaviorConnection,
        visible_rectangle: QRectF | None = None,
    ) -> None:
        """Draw one typed behavior edge with a readable contract label."""
        source = self.session.project.flow_node(connection.source_node_id)
        target = self.session.project.flow_node(connection.target_node_id)
        if source is None or target is None:
            return
        source_port = source.port(connection.source_port_id)
        target_port = target.port(connection.target_port_id)
        if source_port is None or target_port is None:
            return
        start = self._behavior_port_position(source, source_port)
        end = self._behavior_port_position(target, target_port)
        path, approach = self._connection_path(start, end)
        if visible_rectangle is not None and not path.boundingRect().adjusted(
            -16, -16, 16, 16
        ).intersects(visible_rectangle):
            return
        selected = connection.id == self.selected_behavior_connection_id
        traced = connection.id in self.active_trace_connection_ids
        invalid = bool(behavior_connection_error(self.session.project, connection))
        color = (
            QColor("#ffeb3b")
            if traced
            else QColor("#00bfff")
            if selected
            else QColor("#ef5350")
            if invalid
            else QColor("#64b5f6")
            if source_port.data_type == "data"
            else QColor("#a3be8c")
        )
        painter.setPen(QPen(color, 4 if traced or selected else 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        direction = end - approach
        length = max(1.0, (direction.x() ** 2 + direction.y() ** 2) ** 0.5)
        unit = QPointF(direction.x() / length, direction.y() / length)
        normal = QPointF(-unit.y(), unit.x())
        painter.setBrush(color)
        painter.drawPolygon(
            QPolygonF(
                [
                    end,
                    end - unit * 12 + normal * 5,
                    end - unit * 12 - normal * 5,
                ]
            )
        )
        label = connection.label or source_port.name
        painter.setPen(QColor("#eceff4"))
        painter.drawText(path.pointAtPercent(0.5) + QPointF(4, -5), label)

    def _draw_behavior_connection_preview(self, painter: QPainter) -> None:
        """Draw a temporary typed edge during direct port connection."""
        source_id, port_id = self._behavior_connection_source or ("", "")
        source = self.session.project.flow_node(source_id)
        port = source.port(port_id) if source is not None else None
        if source is None or port is None:
            return
        painter.setBrush(Qt.BrushStyle.NoBrush)
        color = (
            QColor("#69f0ae")
            if self._behavior_connection_target is not None
            else QColor("#ff5252")
            if self._behavior_connection_rejected_target is not None
            else QColor("#00bfff")
        )
        painter.setPen(QPen(color, 3, Qt.PenStyle.DashLine))
        painter.drawLine(
            self._behavior_port_position(source, port),
            self._behavior_connection_point,
        )

    def _draw_connection(
        self,
        painter: QPainter,
        source: ScreenDesign,
        target: ScreenDesign,
        connection: FlowConnection,
        visible_rectangle: QRectF | None = None,
    ) -> None:
        """Draw one labeled directional graph edge."""
        start = self._endpoint_output_port(source, connection.source_element_id)
        end = self._endpoint_input_port(target, connection.target_element_id)
        path, approach = self._connection_path(start, end)
        if visible_rectangle is not None and not path.boundingRect().adjusted(
            -16, -16, 16, 16
        ).intersects(visible_rectangle):
            return
        selected = connection.id == self.selected_connection_id
        edge_color = (
            QColor("#00bfff")
            if selected
            else QColor("#ff9800")
            if connection.locked
            else QColor("#88c0d0")
        )
        painter.setPen(QPen(edge_color, 4 if selected else 2))
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

    def _draw_connection_preview(
        self,
        painter: QPainter,
        source: ScreenDesign,
        source_element_id: str,
    ) -> None:
        """Draw the temporary edge while the mouse chooses a target."""
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#00bfff"), 2, Qt.PenStyle.DashLine))
        painter.drawLine(
            self._endpoint_output_port(source, source_element_id),
            self._connection_point,
        )

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

    def _connection_at(self, point: QPointF) -> FlowConnection | None:
        """Return the nearest graph edge below one point."""
        if self.visibility_mode == "behavior":
            return None
        nearest: tuple[float, FlowConnection] | None = None
        for connection in self.session.project.connections:
            source = self.session.project.screen(connection.source_id)
            target = self.session.project.screen(connection.target_id)
            if source is None or target is None:
                continue
            path, _ = self._connection_path(
                self._endpoint_output_port(source, connection.source_element_id),
                self._endpoint_input_port(target, connection.target_element_id),
            )
            for index in range(41):
                sample = path.pointAtPercent(index / 40)
                distance = (
                    (sample.x() - point.x()) ** 2 + (sample.y() - point.y()) ** 2
                ) ** 0.5
                if distance <= 9 and (nearest is None or distance < nearest[0]):
                    nearest = (distance, connection)
        return nearest[1] if nearest is not None else None

    def _behavior_connection_at(self, point: QPointF) -> BehaviorConnection | None:
        """Return the nearest typed behavior edge below one point."""
        if self.visibility_mode == "screens":
            return None
        visible_node_ids = {node.id for node in self._visible_behavior_nodes()}
        nearest: tuple[float, BehaviorConnection] | None = None
        for connection in self.session.project.behavior_connections:
            if (
                connection.source_node_id not in visible_node_ids
                or connection.target_node_id not in visible_node_ids
            ):
                continue
            source = self.session.project.flow_node(connection.source_node_id)
            target = self.session.project.flow_node(connection.target_node_id)
            source_port = source.port(connection.source_port_id) if source else None
            target_port = target.port(connection.target_port_id) if target else None
            if (
                source is None
                or target is None
                or source_port is None
                or target_port is None
            ):
                continue
            path, _ = self._connection_path(
                self._behavior_port_position(source, source_port),
                self._behavior_port_position(target, target_port),
            )
            for index in range(41):
                sample = path.pointAtPercent(index / 40)
                distance = (
                    (sample.x() - point.x()) ** 2 + (sample.y() - point.y()) ** 2
                ) ** 0.5
                if distance <= 9 and (nearest is None or distance < nearest[0]):
                    nearest = (distance, connection)
        return nearest[1] if nearest is not None else None

    def _behavior_node_at(self, point: QPointF) -> FlowNode | None:
        """Return the topmost visible behavior node at one point."""
        for node in reversed(self._visible_behavior_nodes()):
            rectangle = QRectF(
                node.node_x,
                node.node_y,
                self.BEHAVIOR_NODE_WIDTH,
                self._behavior_node_height(node),
            )
            if rectangle.contains(point):
                return node
        return None

    def _behavior_output_at(self, point: QPointF) -> tuple[FlowNode, object] | None:
        """Return a behavior output port below one graph point."""
        for node in reversed(self._visible_behavior_nodes()):
            for port in reversed(node.ports):
                if port.direction == "out" and self._port_contains(
                    self._behavior_port_position(node, port), point, 5
                ):
                    return node, port
        return None

    def _behavior_input_at(
        self, point: QPointF, compatible_only: bool = True
    ) -> tuple[FlowNode, object] | None:
        """Return a behavior input port below one graph point."""
        source_node_id, source_port_id = self._behavior_connection_source or ("", "")
        source_node = self.session.project.flow_node(source_node_id)
        source_port = source_node.port(source_port_id) if source_node else None
        for node in reversed(self._visible_behavior_nodes()):
            for port in reversed(node.ports):
                compatible = bool(
                    source_port is None
                    or source_port.data_type == port.data_type
                    or "any" in {source_port.data_type, port.data_type}
                )
                if (
                    port.direction == "in"
                    and (compatible or not compatible_only)
                    and self._port_contains(
                        self._behavior_port_position(node, port), point, 7
                    )
                ):
                    return node, port
        return None

    def _behavior_input_issue(self, target_node: FlowNode, target_port) -> str:
        """Explain why the current output cannot connect to one input port."""
        source_node_id, source_port_id = self._behavior_connection_source or ("", "")
        source_node = self.session.project.flow_node(source_node_id)
        source_port = source_node.port(source_port_id) if source_node else None
        if source_node is None or source_port is None:
            return "The source port is unavailable."
        if not (
            source_port.data_type == target_port.data_type
            or "any" in {source_port.data_type, target_port.data_type}
        ):
            return (
                f"Cannot connect {source_port.name} ({source_port.data_type}) to "
                f"{target_node.name}.{target_port.name} ({target_port.data_type})."
            )
        if not target_port.multiple and any(
            connection.target_node_id == target_node.id
            and connection.target_port_id == target_port.id
            for connection in self.session.project.behavior_connections
        ):
            return f"{target_node.name}.{target_port.name} already has an input."
        return ""

    def _behavior_port_position(self, node: FlowNode, port) -> QPointF:
        """Return one behavior port center from its directional row."""
        directional = [item for item in node.ports if item.direction == port.direction]
        index = directional.index(port)
        x = (
            node.node_x
            if port.direction == "in"
            else node.node_x + self.BEHAVIOR_NODE_WIDTH
        )
        return QPointF(
            x,
            node.node_y
            + self.BEHAVIOR_HEADER_HEIGHT
            + self.BEHAVIOR_PORT_HEIGHT * (index + 0.5),
        )

    def _behavior_node_height(self, node: FlowNode) -> float:
        """Return behavior-node height for its larger directional port set."""
        input_count = sum(port.direction == "in" for port in node.ports)
        output_count = sum(port.direction == "out" for port in node.ports)
        rows = max(1, input_count, output_count)
        return self.BEHAVIOR_HEADER_HEIGHT + rows * self.BEHAVIOR_PORT_HEIGHT + 8

    def _visible_behavior_nodes(self) -> list[FlowNode]:
        """Return behavior nodes not hidden by collapsed visual groups."""
        if self.visibility_mode == "screens":
            return []
        collapsed = {
            group.id for group in self.session.project.flow_groups if group.collapsed
        }
        return [
            node
            for node in self.session.project.behavior_nodes
            if not node.group_id or node.group_id not in collapsed
        ]

    def _visible_screens(self) -> list[ScreenDesign]:
        """Return screens enabled by the current graph visibility mode."""
        return (
            []
            if self.visibility_mode == "behavior"
            else list(self.session.project.screens)
        )

    def _select_behavior_node(
        self,
        node_id: str,
        modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
    ) -> None:
        """Select one behavior node, optionally extending the selection."""
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            if node_id in self.selected_behavior_node_ids:
                self.selected_behavior_node_ids.remove(node_id)
            else:
                self.selected_behavior_node_ids.add(node_id)
        else:
            self.selected_behavior_node_ids = {node_id}
        self.primary_behavior_node_id = (
            node_id
            if node_id in self.selected_behavior_node_ids
            else next(iter(self.selected_behavior_node_ids), None)
        )
        self.behavior_node_selected.emit(self.primary_behavior_node_id or "")

    def _copy_selected_behavior_nodes(self) -> None:
        """Copy selected behavior contracts and their internal edges."""
        selected = set(self.selected_behavior_node_ids)
        if not selected:
            return
        nodes = [
            asdict(node)
            for node in self.session.project.behavior_nodes
            if node.id in selected
        ]
        connections = [
            asdict(connection)
            for connection in self.session.project.behavior_connections
            if connection.source_node_id in selected
            and connection.target_node_id in selected
        ]
        self._behavior_clipboard = {"nodes": nodes, "connections": connections}

    def _paste_behavior_nodes(self) -> None:
        """Paste independent behavior-node copies with remapped identities."""
        records = self._behavior_clipboard.get("nodes", [])
        if not isinstance(records, list) or not records:
            return
        nodes = [FlowNode.from_dict(record) for record in records]
        node_map = {node.id: new_identifier("node") for node in nodes}
        self.selected_behavior_node_ids.clear()
        for node in nodes:
            old_id = node.id
            node.id = node_map[old_id]
            node.name += " Copy"
            node.node_x += 40
            node.node_y += 40
            node.group_id = ""
            self.session.project.behavior_nodes.append(node)
            self.selected_behavior_node_ids.add(node.id)
        connection_records = self._behavior_clipboard.get("connections", [])
        if isinstance(connection_records, list):
            for record in connection_records:
                connection = BehaviorConnection.from_dict(record)
                connection.id = new_identifier("behavior")
                connection.source_node_id = node_map[connection.source_node_id]
                connection.target_node_id = node_map[connection.target_node_id]
                self.session.project.behavior_connections.append(connection)
        self.primary_behavior_node_id = next(
            iter(self.selected_behavior_node_ids), None
        )
        self.refresh_geometry()
        self.session.mark_changed()

    def _screen_at(self, point: QPointF) -> ScreenDesign | None:
        """Return the topmost graph node at one point."""
        for screen in reversed(self._visible_screens()):
            if QRectF(
                screen.node_x,
                screen.node_y,
                self.NODE_WIDTH,
                self._node_height(screen),
            ).contains(point):
                return screen
        return None

    def _output_endpoint_at(
        self,
        point: QPointF,
    ) -> tuple[ScreenDesign, str] | None:
        """Return the screen or element output port below one point."""
        for screen in reversed(self._visible_screens()):
            for element in reversed(self._navigation_elements(screen)):
                port = self._element_output_port(screen, element)
                if self._port_contains(port, point, 5):
                    return screen, element.id
            port = self._output_port(screen)
            if self._port_contains(port, point, 5):
                return screen, ""
        return None

    def _connection_target_at(
        self,
        point: QPointF,
    ) -> tuple[ScreenDesign, str] | None:
        """Return a screen or element connection target below one point."""
        for screen in reversed(self._visible_screens()):
            for element in reversed(self._navigation_elements(screen)):
                port = self._element_input_port(screen, element)
                if self._port_contains(port, point, 7):
                    return screen, element.id
            port = self._input_port(screen)
            if self._port_contains(port, point, 7):
                return screen, ""
        screen = self._screen_at(point)
        return (screen, "") if screen is not None else None

    def _input_port(self, screen: ScreenDesign) -> QPointF:
        """Return the center of a screen node input port."""
        return QPointF(screen.node_x, screen.node_y + self.NODE_HEIGHT / 2)

    def _output_port(self, screen: ScreenDesign) -> QPointF:
        """Return the center of a screen node output port."""
        return QPointF(
            screen.node_x + self.NODE_WIDTH,
            screen.node_y + self.NODE_HEIGHT / 2,
        )

    def _element_input_port(
        self,
        screen: ScreenDesign,
        element: GuiElement,
    ) -> QPointF:
        """Return the input port for one configurable GUI element."""
        index = self._navigation_elements(screen).index(element)
        return QPointF(
            screen.node_x,
            screen.node_y
            + self.NODE_HEIGHT
            + index * self.ELEMENT_ROW_HEIGHT
            + self.ELEMENT_ROW_HEIGHT / 2,
        )

    def _element_output_port(
        self,
        screen: ScreenDesign,
        element: GuiElement,
    ) -> QPointF:
        """Return the output port for one configurable GUI element."""
        point = self._element_input_port(screen, element)
        return QPointF(screen.node_x + self.NODE_WIDTH, point.y())

    def _endpoint_input_port(
        self,
        screen: ScreenDesign,
        element_id: str,
    ) -> QPointF:
        """Return the input port for a screen or one of its elements."""
        element = self.session.project.element(screen.id, element_id)
        if element is not None and element in self._navigation_elements(screen):
            return self._element_input_port(screen, element)
        return self._input_port(screen)

    def _endpoint_output_port(
        self,
        screen: ScreenDesign,
        element_id: str,
    ) -> QPointF:
        """Return the output port for a screen or one of its elements."""
        element = self.session.project.element(screen.id, element_id)
        if element is not None and element in self._navigation_elements(screen):
            return self._element_output_port(screen, element)
        return self._output_port(screen)

    def _navigation_elements(self, screen: ScreenDesign) -> list[GuiElement]:
        """Return elements exposed as configurable graph endpoints."""
        connected_ids = {
            element_id
            for connection in self.session.project.connections
            for element_id in (
                connection.source_element_id,
                connection.target_element_id,
            )
            if element_id
        }
        return [
            element
            for element in screen.elements
            if element.focusable or element.id in connected_ids
        ]

    def _node_height(self, screen: ScreenDesign) -> float:
        """Return node height including its configurable element rows."""
        return (
            self.NODE_HEIGHT
            + len(self._navigation_elements(screen)) * self.ELEMENT_ROW_HEIGHT
        )

    def _port_contains(self, port: QPointF, point: QPointF, padding: int) -> bool:
        """Return whether a point lies within a padded graph port."""
        readable_target = 11 / max(self.zoom, 0.05)
        radius = max(self.PORT_RADIUS + padding, readable_target)
        return (point.x() - port.x()) ** 2 + (point.y() - port.y()) ** 2 <= (
            radius
        ) ** 2

    def _graph_point(self, point: QPointF) -> QPointF:
        """Convert widget coordinates into zoomed graph coordinates."""
        return QPointF(point.x() / self.zoom, point.y() / self.zoom)


class FlowMiniMap(QWidget):
    """Show a compact clickable overview of screen and behavior nodes."""

    center_requested = Signal(float, float)

    def __init__(self, session: DesignerSession, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.viewport_bounds = QRectF()
        self.visibility_mode = "both"
        self.setFixedSize(150, 76)
        self.setToolTip(
            "Overview of every flow node; click to center the graph.\n"
            "Example: Click a distant node cluster to jump to it."
        )

    def paintEvent(self, event) -> None:
        """Paint scaled screen, behavior-node, and group bounds."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#20242a"))
        scale_x, scale_y = self._scales()
        if self.visibility_mode != "screens":
            for group in self.session.project.flow_groups:
                painter.setBrush(QColor(group.color))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRect(
                    QRectF(
                        group.node_x * scale_x,
                        group.node_y * scale_y,
                        max(3, group.width * scale_x),
                        max(3, group.height * scale_y),
                    )
                )
        if self.visibility_mode != "behavior":
            painter.setBrush(QColor("#43a047"))
            for screen in self.session.project.screens:
                painter.drawRect(
                    QRectF(
                        screen.node_x * scale_x,
                        screen.node_y * scale_y,
                        max(4, FlowCanvas.NODE_WIDTH * scale_x),
                        max(4, FlowCanvas.NODE_HEIGHT * scale_y),
                    )
                )
        if self.visibility_mode != "screens":
            painter.setBrush(QColor("#ef6c00"))
            for node in self.session.project.behavior_nodes:
                painter.drawRect(
                    QRectF(
                        node.node_x * scale_x,
                        node.node_y * scale_y,
                        max(4, FlowCanvas.BEHAVIOR_NODE_WIDTH * scale_x),
                        max(4, 72 * scale_y),
                    )
                )
        if not self.viewport_bounds.isNull():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#ffffff"), 1))
            painter.drawRect(
                QRectF(
                    self.viewport_bounds.x() * scale_x,
                    self.viewport_bounds.y() * scale_y,
                    max(3, self.viewport_bounds.width() * scale_x),
                    max(3, self.viewport_bounds.height() * scale_y),
                )
            )
        painter.end()

    def set_viewport(self, x: float, y: float, width: float, height: float) -> None:
        """Show the logical graph region currently visible in the main viewport."""
        self.viewport_bounds = QRectF(x, y, width, height)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Request graph centering at the clicked overview position."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        scale_x, scale_y = self._scales()
        self.center_requested.emit(
            event.position().x() / max(scale_x, 0.0001),
            event.position().y() / max(scale_y, 0.0001),
        )

    def _scales(self) -> tuple[float, float]:
        """Return overview scales for all persisted graph positions."""
        project = self.session.project
        screens = project.screens if self.visibility_mode != "behavior" else []
        nodes = project.behavior_nodes if self.visibility_mode != "screens" else []
        groups = project.flow_groups if self.visibility_mode != "screens" else []
        right = max(
            [screen.node_x + FlowCanvas.NODE_WIDTH for screen in screens]
            + [node.node_x + FlowCanvas.BEHAVIOR_NODE_WIDTH for node in nodes]
            + [group.node_x + group.width for group in groups]
            + [1]
        )
        bottom = max(
            [screen.node_y + FlowCanvas.NODE_HEIGHT for screen in screens]
            + [node.node_y + 100 for node in nodes]
            + [group.node_y + group.height for group in groups]
            + [1]
        )
        return self.width() / right, self.height() / bottom


class SimulatorWorkspace(QWidget):
    """Run the shared in-memory GUI project in an isolated device simulator."""

    running_changed = Signal(bool)
    status_changed = Signal(str)
    error_changed = Signal(str)

    def __init__(
        self,
        session: DesignerSession,
        parent: QWidget | None = None,
    ):
        """Build a simulator-first workspace around the shared project session."""
        super().__init__(parent)
        self.session = session
        self.live_controller = LiveSimulatorController(self)
        self._live_import_root = ""
        self._last_error = ""
        self._build_interface()
        self._connect_signals()
        self.refresh()
        install_widget_tooltips(self)

    def _build_interface(self) -> None:
        """Build primary run controls, framebuffer, feedback, and details."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)

        heading_row = QHBoxLayout()
        heading = QLabel("Device Simulator")
        heading.setStyleSheet("font-size: 18px; font-weight: 700;")
        description = QLabel(
            "Run the current in-memory GUI project without saving it first."
        )
        description.setWordWrap(True)
        self.preview_mode_combo = QComboBox()
        self.preview_mode_combo.addItem("Device simulator", "live")
        self.preview_mode_combo.addItem("Design preview", "designer")
        self.preview_mode_combo.addItem("Compare", "compare")
        self.preview_mode_combo.setToolTip(
            "Choose the real device simulator, safe design preview, or both.\n"
            "Example: Choose Compare after a runtime error."
        )
        heading_row.addWidget(heading)
        heading_row.addWidget(description, 1)
        heading_row.addWidget(QLabel("View"))
        heading_row.addWidget(self.preview_mode_combo)
        layout.addLayout(heading_row)

        primary_group = QGroupBox("Run current design")
        primary_layout = QGridLayout(primary_group)
        self.target_summary_label = QLabel("Current design · picocalc-pico2w")
        self.target_summary_label.setStyleSheet("font-weight: 600;")
        self.project_state_label = QLabel(
            "The in-memory project is used; unsaved changes are included."
        )
        self.project_state_label.setStyleSheet("color: #607d8b;")
        self.project_state_label.setWordWrap(True)
        self.start_live_button = QPushButton("▶ Run current design")
        self.start_live_button.setDefault(True)
        self.start_live_button.setToolTip(
            "Build and run the current in-memory GUI project in an isolated process.\n"
            "Example: Change a button label, then run without saving the project."
        )
        self.restart_live_button = QPushButton("Restart")
        self.restart_live_button.setEnabled(False)
        self.restart_live_button.setToolTip(
            "Restart the active simulator with the same target.\n"
            "Example: Restart after testing a navigation path."
        )
        self.stop_live_button = QPushButton("Stop")
        self.stop_live_button.setEnabled(False)
        self.stop_live_button.setToolTip(
            "Stop the isolated simulator process.\n"
            "Example: Stop it when runtime testing is complete."
        )
        primary_layout.addWidget(self.target_summary_label, 0, 0, 1, 2)
        primary_layout.addWidget(self.start_live_button, 0, 2)
        primary_layout.addWidget(self.restart_live_button, 0, 3)
        primary_layout.addWidget(self.stop_live_button, 0, 4)
        primary_layout.addWidget(self.project_state_label, 1, 0, 1, 5)
        primary_layout.setColumnStretch(1, 1)
        layout.addWidget(primary_group)

        self.error_panel = QGroupBox("Simulator error")
        error_layout = QHBoxLayout(self.error_panel)
        self.error_summary_label = QLabel()
        self.error_summary_label.setWordWrap(True)
        self.error_summary_label.setStyleSheet("color: #c62828; font-weight: 600;")
        self.copy_error_button = QPushButton("Copy error")
        self.show_details_button = QPushButton("Show details")
        self.error_restart_button = QPushButton("Restart")
        error_layout.addWidget(self.error_summary_label, 1)
        error_layout.addWidget(self.copy_error_button)
        error_layout.addWidget(self.show_details_button)
        error_layout.addWidget(self.error_restart_button)
        self.error_panel.hide()
        layout.addWidget(self.error_panel)

        self.preview_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.preview = GuiPreview(self.session)
        self.live_preview = LiveSimulatorView()
        self.live_preview.setMinimumSize(320, 320)
        self.live_preview.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.live_preview.setToolTip(
            "Click to send keyboard or touch input to Picoware.\n"
            "Example: Click the frame, then use arrows, Enter, Escape, or Tab."
        )
        self.preview_splitter.addWidget(self.preview)
        self.preview_splitter.addWidget(self.live_preview)
        self.preview_splitter.setSizes((500, 700))
        self.preview_splitter.setStretchFactor(0, 1)
        self.preview_splitter.setStretchFactor(1, 2)
        layout.addWidget(self.preview_splitter, 1)

        self.input_hint_label = QLabel(
            "Input: click the framebuffer, then use arrows, Enter, Escape, Tab, "
            "function keys, typing, or mouse/touch. A cyan frame means input is active."
        )
        self.input_hint_label.setWordWrap(True)
        self.input_hint_label.setStyleSheet("color: #607d8b;")
        layout.addWidget(self.input_hint_label)

        self.status_banner = QLabel("Ready to run the current design.")
        self.status_banner.setWordWrap(True)
        self.status_banner.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.status_banner.setStyleSheet(
            "background: #263238; color: #eceff1; border-radius: 4px; padding: 7px;"
        )
        layout.addWidget(self.status_banner)

        self.details_tabs = QTabWidget()
        self.details_tabs.setMaximumHeight(190)
        self.runtime_log = QPlainTextEdit()
        self.runtime_log.setReadOnly(True)
        self.runtime_log.setPlaceholderText(
            "Simulator status and diagnostics appear here."
        )
        self.details_tabs.addTab(self.runtime_log, "Runtime details")

        capture_tab = QWidget()
        capture_layout = QHBoxLayout(capture_tab)
        capture_layout.addWidget(QLabel("Capture live frame as"))
        self.capture_screen_combo = QComboBox()
        self.capture_screen_combo.setToolTip(
            "Choose which design screen receives the current framebuffer snapshot.\n"
            "Example: Capture the running Home screen for Compare view."
        )
        self.capture_live_button = QPushButton("Capture current frame")
        self.clear_live_capture_button = QPushButton("Clear capture")
        capture_layout.addWidget(self.capture_screen_combo, 1)
        capture_layout.addWidget(self.capture_live_button)
        capture_layout.addWidget(self.clear_live_capture_button)
        self.details_tabs.addTab(capture_tab, "Capture")

        advanced_tab = QWidget()
        advanced_layout = QFormLayout(advanced_tab)
        self.live_target_kind_combo = QComboBox()
        self.live_target_kind_combo.addItems(
            ("Current design", "Desktop", "Application", "Game", "Library")
        )
        self.live_target_kind_combo.setToolTip(
            "Choose what the isolated simulator launches.\n"
            "Example: Keep Current design for unsaved GUI work."
        )
        self.live_target_edit = QLineEdit()
        self.live_target_edit.setPlaceholderText("Application or Library name")
        self.live_board_combo = QComboBox()
        self.live_board_combo.addItems(SIMULATOR_BOARDS)
        self.live_auto_reload_check = QCheckBox("Reload when imported source changes")
        self.live_auto_reload_check.setChecked(True)
        self.launch_selected_button = QPushButton("Run selected target")
        self.live_safety_label = QLabel(
            "Application code runs in an isolated MicroPython process with "
            "network offline and audio silent."
        )
        self.live_safety_label.setWordWrap(True)
        self.live_safety_label.setStyleSheet("color: #ef6c00;")
        advanced_layout.addRow("Launch", self.live_target_kind_combo)
        advanced_layout.addRow("Target name", self.live_target_edit)
        advanced_layout.addRow("Board", self.live_board_combo)
        advanced_layout.addRow(self.live_auto_reload_check)
        advanced_layout.addRow(self.launch_selected_button)
        advanced_layout.addRow(self.live_safety_label)
        self.details_tabs.addTab(advanced_tab, "Advanced launch")
        layout.addWidget(self.details_tabs)

        # Compatibility aliases for code that still describes the prior embedded UI.
        self.live_status_label = self.status_banner

    def _connect_signals(self) -> None:
        """Connect simulator controls, shared project updates, and input."""
        self.session.project_changed.connect(self.refresh)
        self.session.dirty_changed.connect(self._update_project_state)
        self.preview_mode_combo.currentIndexChanged.connect(self._update_preview_mode)
        self.live_target_kind_combo.currentTextChanged.connect(
            self._live_target_kind_changed
        )
        self.live_board_combo.currentTextChanged.connect(self._update_target_summary)
        self.start_live_button.clicked.connect(self.run_current_design)
        self.launch_selected_button.clicked.connect(self._start_live_simulator)
        self.restart_live_button.clicked.connect(self.restart_live_simulator)
        self.error_restart_button.clicked.connect(self.restart_live_simulator)
        self.stop_live_button.clicked.connect(self.live_controller.stop)
        self.capture_live_button.clicked.connect(self._capture_live_frame)
        self.clear_live_capture_button.clicked.connect(self._clear_live_capture)
        self.copy_error_button.clicked.connect(self.copy_error)
        self.show_details_button.clicked.connect(
            lambda: self.details_tabs.setCurrentWidget(self.runtime_log)
        )
        self.live_preview.customContextMenuRequested.connect(
            self._show_simulator_context_menu
        )
        self.live_preview.key_event.connect(self.live_controller.send_key)
        self.live_preview.touch_event.connect(self.live_controller.send_touch)
        self.live_controller.frame_ready.connect(self.live_preview.set_frame)
        self.live_controller.status_changed.connect(self._live_status_changed)
        self.live_controller.error_changed.connect(self._live_error_changed)
        self.live_controller.running_changed.connect(self._live_running_changed)
        self._update_preview_mode()
        self._live_target_kind_changed(self.live_target_kind_combo.currentText())

    def refresh(self) -> None:
        """Refresh capture targets and launch defaults from the shared project."""
        selected_capture = self.capture_screen_combo.currentData()
        self.capture_screen_combo.clear()
        for screen in self.session.project.screens:
            self.capture_screen_combo.addItem(screen.name, screen.id)
        self._restore_combo(
            self.capture_screen_combo,
            selected_capture or self.session.active_screen_id,
        )
        self.preview.set_screen(self.session.active_screen_id)
        self._update_live_target_defaults()
        self._update_project_state(self.session.dirty)

    @staticmethod
    def _restore_combo(combo: QComboBox, value: object) -> None:
        """Restore one combo selection by item data when available."""
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def run_current_design(self) -> bool:
        """Run the current in-memory project, including unsaved edits."""
        self.live_target_kind_combo.setCurrentText("Current design")
        return self._start_live_simulator()

    def show_design_preview(self) -> None:
        """Show the safe visual renderer without starting application code."""
        self.preview.set_screen(self.session.active_screen_id)
        self.preview_mode_combo.setCurrentIndex(
            self.preview_mode_combo.findData("designer")
        )

    def restart_live_simulator(self) -> bool:
        """Restart the active target, or run the current design when stopped."""
        if self.live_controller.is_running():
            self._clear_error()
            self.live_controller.restart()
            return True
        return self._start_live_simulator()

    def stop_live_simulator(self) -> None:
        """Stop the active isolated process."""
        self.live_controller.stop()

    def shutdown_live_simulator(self) -> None:
        """Stop the simulator before the parent window closes."""
        self.live_controller.shutdown()

    def is_running(self) -> bool:
        """Return whether the isolated simulator process is active."""
        return self.live_controller.is_running()

    def last_error(self) -> str:
        """Return the most recent actionable simulator error."""
        return self._last_error

    def copy_error(self) -> None:
        """Copy the most recent simulator error for reporting or debugging."""
        if self._last_error:
            QApplication.clipboard().setText(self._last_error)

    def capture_current_frame(self) -> None:
        """Capture the current framebuffer for the selected design screen."""
        self._capture_live_frame()

    def _update_preview_mode(self) -> None:
        """Show the design preview, device framebuffer, or both."""
        mode = str(self.preview_mode_combo.currentData())
        self.preview.setVisible(mode in {"designer", "compare"})
        self.live_preview.setVisible(mode in {"live", "compare"})
        self.input_hint_label.setVisible(mode in {"live", "compare"})
        if self.live_controller.is_running() and mode in {"live", "compare"}:
            self.live_preview.setFocus(Qt.FocusReason.OtherFocusReason)

    def _update_project_state(self, dirty: bool) -> None:
        """Make in-memory and unsaved launch behavior explicit."""
        state = (
            "Unsaved changes are included."
            if dirty
            else "Saved and in-memory state match."
        )
        self.project_state_label.setText(f"The in-memory project is used. {state}")

    def _update_target_summary(self, unused: str = "") -> None:
        """Show the primary current-design route without exposing advanced fields."""
        self.target_summary_label.setText(
            f"Current design · {self.live_board_combo.currentText()}"
        )

    def _live_target_kind_changed(self, kind: str) -> None:
        """Enable a name only for routes that require one."""
        self.live_target_edit.setEnabled(kind not in {"Current design", "Desktop"})

    def _update_live_target_defaults(self) -> None:
        """Infer an imported route while keeping Current design as the default."""
        import_root = self.session.project.import_root
        if import_root == self._live_import_root:
            self._update_target_summary()
            return
        self._live_import_root = import_root
        kind, name = self._infer_live_target(import_root)
        self.live_target_kind_combo.setCurrentText("Current design")
        placeholder = f"Suggested {kind}: {name}" if name else "Application name"
        self.live_target_edit.setPlaceholderText(placeholder)
        self.live_target_edit.setText(name)
        self._update_target_summary()

    def _infer_live_target(self, import_root: str) -> tuple[str, str]:
        """Infer the closest simulator route from an import path."""
        if not import_root:
            return "Desktop", ""
        root = Path(import_root)
        parts = list(root.parts)
        game_index = next(
            (index for index, part in enumerate(parts) if part.lower() == "games"),
            -1,
        )
        if game_index >= 0:
            games_path = Path(*parts[: game_index + 1])
            base = parts[game_index + 1] if len(parts) > game_index + 1 else root.stem
            launcher = self._matching_launcher(games_path, base)
            return "Game", launcher or (root.stem if root.is_file() else root.name)
        base = root.stem if root.is_file() else root.name
        launcher = self._matching_launcher(root.parent, base)
        return "Application", launcher or base

    @staticmethod
    def _matching_launcher(container: Path, base: str) -> str:
        """Return a sibling Python launcher matching a file or package name."""
        normalized = "".join(
            character for character in base.lower() if character.isalnum()
        )
        try:
            candidates = sorted(container.glob("*.py"))
        except OSError:
            return ""
        for path in candidates:
            candidate = "".join(
                character for character in path.stem.lower() if character.isalnum()
            )
            if candidate == normalized:
                return path.stem
        return ""

    def _live_apps_source(self) -> str:
        """Return the application catalogue root used by a live launch."""
        default = (
            Path(__file__).resolve().parents[1]
            / "builds"
            / "MicroPython"
            / "apps_unfrozen"
        )
        import_root = self.session.project.import_root
        if not import_root:
            return str(default)
        root = Path(import_root)
        try:
            root.resolve().relative_to(default.resolve())
            return str(default)
        except (OSError, ValueError):
            pass
        for parent in (root, *root.parents):
            if parent.name.lower() == "games":
                return str(parent.parent)
        return str(root.parent)

    def _start_live_simulator(self) -> bool:
        """Start an isolated simulator for the selected route and board."""
        kind = self.live_target_kind_combo.currentText()
        target = self.live_target_edit.text().strip()
        if kind not in {"Current design", "Desktop"} and not target:
            QMessageBox.information(
                self,
                "Simulator target required",
                "Enter the application, game, or Library route name.",
            )
            return False
        design_files: tuple[tuple[str, str | bytes], ...] = ()
        if kind == "Current design":
            if not self._resolve_invalid_asset_sizes():
                return False
            try:
                design_files = build_live_preview_bundle(
                    self.session.project,
                    self.session.active_screen_id,
                ).files
            except (SyntaxError, ValueError) as error:
                self._live_error_changed(str(error))
                return False
        self._clear_error()
        self.preview_mode_combo.setCurrentIndex(
            self.preview_mode_combo.findData("live")
        )
        config = LiveSimulatorConfig(
            target_kind=kind,
            target_name=target,
            board=self.live_board_combo.currentText(),
            apps_source=self._live_apps_source(),
            watch_path=self.session.project.import_root,
            auto_reload=self.live_auto_reload_check.isChecked(),
            design_files=design_files,
        )
        self.live_controller.start(config)
        return True

    def _resolve_invalid_asset_sizes(self) -> bool:
        """Offer an explicit one-step repair before current-design generation."""
        invalid = invalid_asset_scale_elements(self.session.project)
        if not invalid:
            return True
        details = [
            (
                f"• {screen.name} / {element.name}: element "
                f"{element.width} x {element.height}, asset {asset.width} x {asset.height}"
            )
            for screen, element, asset in invalid[:6]
        ]
        if len(invalid) > len(details):
            details.append(f"• and {len(invalid) - len(details)} more")
        message = QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Warning)
        message.setWindowTitle("Asset sizes need a device-safe decision")
        message.setText(
            f"{len(invalid)} placed asset(s) cannot use uniform integer scaling."
        )
        message.setInformativeText(
            "\n".join(details)
            + "\n\nBake creates independent nearest-neighbor copies; originals remain unchanged."
        )
        bake_button = message.addButton(
            "Bake Invalid Assets and Run", QMessageBox.ButtonRole.AcceptRole
        )
        bake_button.setEnabled(
            all(
                1 <= element.width <= 320 and 1 <= element.height <= 320
                for unused_screen, element, unused_asset in invalid
            )
        )
        natural_possible = all(
            asset.width <= screen.width and asset.height <= screen.height
            for screen, unused_element, asset in invalid
        )
        natural_button = message.addButton(
            "Use Natural Sizes and Run", QMessageBox.ButtonRole.ActionRole
        )
        natural_button.setEnabled(natural_possible)
        message.addButton(QMessageBox.StandardButton.Cancel)
        message.exec()
        if message.clickedButton() is bake_button:
            try:
                for unused_screen, element, unused_asset in invalid:
                    bake_asset_element(self.session.project, element)
            except ValueError as error:
                QMessageBox.warning(self, "Cannot bake asset", str(error))
                return False
            self.session.mark_changed()
            return True
        if message.clickedButton() is natural_button:
            for screen, element, asset in invalid:
                element.width = asset.width
                element.height = asset.height
                element.x = max(0, min(element.x, screen.width - element.width))
                element.y = max(0, min(element.y, screen.height - element.height))
            self.session.mark_changed()
            return True
        return False

    def _capture_live_frame(self) -> None:
        """Associate the current live framebuffer with a designer screen."""
        image = self.live_controller.current_frame()
        screen_id = str(self.capture_screen_combo.currentData() or "")
        if image.isNull() or not screen_id:
            QMessageBox.information(
                self,
                "No live frame",
                "Start the simulator and wait for a frame before capturing it.",
            )
            return
        self.session.set_live_screen_image(screen_id, image)
        screen = self.session.project.screen(screen_id)
        name = screen.name if screen is not None else "screen"
        self._set_status_banner(f"Captured the current framebuffer for {name}.")

    def _clear_live_capture(self) -> None:
        """Clear the selected screen's transient live framebuffer capture."""
        screen_id = str(self.capture_screen_combo.currentData() or "")
        if screen_id:
            self.session.clear_live_screen_image(screen_id)
            self._set_status_banner("Cleared the selected screen capture.")

    def _live_status_changed(self, status: str) -> None:
        """Keep detailed telemetry in the log and a concise visible summary."""
        self._append_runtime_detail(status)
        lowered = status.lower()
        if "starting" in lowered or "restarting" in lowered:
            summary = f"Starting {self.live_target_kind_combo.currentText()}…"
        elif "stopped" in lowered:
            summary = "Simulator stopped."
        elif self.live_controller.is_running():
            summary = (
                f"Running {self.live_target_kind_combo.currentText()} on "
                f"{self.live_board_combo.currentText()}."
            )
        else:
            summary = status
        self._set_status_banner(summary)
        self.status_changed.emit(summary)

    def _live_error_changed(self, error: str) -> None:
        """Expose a persistent actionable error without hiding diagnostics."""
        self._last_error = error.strip() or "Unknown simulator error."
        self.error_summary_label.setText(self._last_error.splitlines()[0])
        self.error_panel.show()
        self._append_runtime_detail(f"ERROR: {self._last_error}")
        self._set_status_banner(
            "Simulator failed. Review the error or restart.", error=True
        )
        self.preview_mode_combo.setCurrentIndex(
            self.preview_mode_combo.findData("compare")
        )
        self.error_changed.emit(self._last_error)

    def _live_running_changed(self, running: bool) -> None:
        """Update local controls and publish persistent process state."""
        self.start_live_button.setEnabled(not running)
        self.restart_live_button.setEnabled(running)
        self.stop_live_button.setEnabled(running)
        self.error_restart_button.setEnabled(True)
        if running:
            self._set_status_banner(
                f"Running {self.live_target_kind_combo.currentText()} on "
                f"{self.live_board_combo.currentText()}."
            )
            if self.preview_mode_combo.currentData() != "designer":
                self.live_preview.setFocus(Qt.FocusReason.OtherFocusReason)
        elif not self._last_error:
            self._set_status_banner("Simulator stopped.")
        self.running_changed.emit(running)

    def _clear_error(self) -> None:
        """Clear stale error presentation before a new launch."""
        had_error = bool(self._last_error)
        self._last_error = ""
        self.error_summary_label.clear()
        self.error_panel.hide()
        self.status_banner.setStyleSheet(
            "background: #263238; color: #eceff1; border-radius: 4px; padding: 7px;"
        )
        if had_error:
            self.error_changed.emit("")

    def _set_status_banner(self, text: str, error: bool = False) -> None:
        """Show one concise high-level simulator state."""
        self.status_banner.setText(text)
        self.status_banner.setStyleSheet(
            (
                "background: #4a1f1f; color: #ffcdd2; border-radius: 4px; padding: 7px;"
                if error
                else "background: #263238; color: #eceff1; border-radius: 4px; padding: 7px;"
            )
        )

    def _append_runtime_detail(self, text: str) -> None:
        """Append one bounded human-readable runtime message."""
        if not text.strip():
            return
        self.runtime_log.appendPlainText(text.strip())
        content = self.runtime_log.toPlainText()
        if len(content) > 16000:
            self.runtime_log.setPlainText(content[-16000:])

    def _show_simulator_context_menu(self, position: QPoint) -> None:
        """Show the most-used simulator operations at the framebuffer."""
        menu = QMenu(self)
        run_action = menu.addAction("Run current design")
        restart_action = menu.addAction("Restart simulator")
        stop_action = menu.addAction("Stop simulator")
        menu.addSeparator()
        capture_action = menu.addAction("Capture current frame")
        copy_error_action = menu.addAction("Copy last error")
        running = self.live_controller.is_running()
        run_action.setEnabled(not running)
        restart_action.setEnabled(running)
        stop_action.setEnabled(running)
        capture_action.setEnabled(running and not self.live_preview.frame().isNull())
        copy_error_action.setEnabled(bool(self._last_error))
        run_action.triggered.connect(self.run_current_design)
        restart_action.triggered.connect(self.restart_live_simulator)
        stop_action.triggered.connect(self.stop_live_simulator)
        capture_action.triggered.connect(self._capture_live_frame)
        copy_error_action.triggered.connect(self.copy_error)
        menu.exec(self.live_preview.mapToGlobal(position))


class _FlowDebugUi:
    """Provide deterministic editor-only UI values for behavior debugging."""

    def __init__(self, project: GuiProject):
        self.project = project
        self.values: dict[str, object] = {}
        self.text_values: dict[str, str] = {}
        self.visible: dict[str, bool] = {}
        self.enabled: dict[str, bool] = {}
        self.screen_id = project.start_screen_id

    def _element(self, element_id: str) -> GuiElement | None:
        return next(
            (
                element
                for screen in self.project.screens
                for element in screen.elements
                if element.id == element_id
            ),
            None,
        )

    def read_value(self, element_id: str):
        if element_id in self.values:
            return self.values[element_id]
        element = self._element(element_id)
        if element is None:
            return None
        widget = element.native_widget if element.kind == "native" else element.kind
        if widget in {"menu", "list", "choice", "search_bar"}:
            if not element.widget_items:
                return None
            index = max(
                0,
                min(element.widget_selected_index, len(element.widget_items) - 1),
            )
            return element.widget_items[index]
        if widget == "toggle":
            return bool(element.widget_state)
        if widget == "toggle_list":
            if not element.widget_items:
                return None
            index = max(
                0,
                min(element.widget_selected_index, len(element.widget_items) - 1),
            )
            checked = (
                bool(element.widget_item_states[index])
                if index < len(element.widget_item_states)
                else False
            )
            return index, element.widget_items[index], checked
        if widget in {"loading", "alert", "panel", "rectangle"}:
            return None
        if widget == "progress":
            return 50
        return element.text

    def read_index(self, element_id: str):
        element = self._element(element_id)
        if element is None:
            return None
        widget = element.native_widget if element.kind == "native" else element.kind
        return (
            element.widget_selected_index
            if widget in {"menu", "list", "choice", "toggle_list"}
            else None
        )

    def widget_type(self, element_id: str) -> str:
        element = self._element(element_id)
        return (
            (
                element.native_widget
                if element and element.kind == "native"
                else element.kind
            )
            if element
            else ""
        )

    def navigate(self, screen_id: str) -> bool:
        if self.project.screen(screen_id) is None:
            return False
        self.screen_id = screen_id
        return True

    def back(self) -> bool:
        return True

    def set_value(self, element_id: str, value) -> bool:
        element = self._element(element_id)
        if element is None or not _element_supports_operation(element, "ui.set_value"):
            return False
        widget = element.native_widget if element.kind == "native" else element.kind
        if widget in {"menu", "list", "choice"}:
            if isinstance(value, bool):
                return False
            if isinstance(value, int):
                if value < 0 or value >= len(element.widget_items):
                    return False
                value = element.widget_items[value]
            elif value not in element.widget_items:
                return False
        elif widget in {"toggle", "toggle_list"}:
            if value is not True and value is not False:
                return False
        elif widget == "progress":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return False
            value = max(0, min(100, int(value)))
        elif widget in {"textbox", "keyboard", "button", "label", "icon"}:
            value = str(value)
        self.values[element_id] = value
        return True

    def set_text(self, element_id: str, text) -> bool:
        element = self._element(element_id)
        if element is None or not _element_supports_operation(element, "ui.set_text"):
            return False
        widget = element.native_widget if element.kind == "native" else element.kind
        self.text_values[element_id] = str(text)
        if widget in {"textbox", "button", "label", "icon", "list"}:
            self.values[element_id] = str(text)
        return True

    def set_progress(self, element_id: str, value) -> bool:
        element = self._element(element_id)
        if (
            element is None
            or not _element_supports_operation(element, "ui.set_progress")
            or not isinstance(value, (int, float))
            or isinstance(value, bool)
        ):
            return False
        self.values[element_id] = max(0, min(100, int(value)))
        return True

    def show(self, element_id: str) -> bool:
        if self._element(element_id) is None:
            return False
        self.visible[element_id] = True
        return True

    def hide(self, element_id: str) -> bool:
        if self._element(element_id) is None:
            return False
        self.visible[element_id] = False
        return True

    def enable(self, element_id: str, enabled: bool = True) -> bool:
        if self._element(element_id) is None:
            return False
        self.enabled[element_id] = bool(enabled)
        return True

    def focus(self, element_id: str) -> bool:
        element = self._element(element_id)
        return bool(element and _element_supports_operation(element, "ui.focus"))

    def alert(self, message: str) -> dict[str, object]:
        return {"message": message}


class _FlowDebugService:
    """Record external operations and return a chosen deterministic outcome."""

    def __init__(self, outcome: str = "success", response: object = None):
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.outcome = (
            outcome if outcome in {"success", "error", "cancel"} else "success"
        )
        self.response = response

    def __getattr__(self, name: str):
        def invoke(**values):
            self.calls.append((name, dict(values)))
            response = dict(values) if self.response is None else self.response
            return self.outcome, response

        return invoke


class _FlowDebugTimer(_FlowDebugService):
    """Record timers and let the user fire callbacks at a node boundary."""

    def __init__(self, response: object = None):
        super().__init__("success", response)
        self.callbacks: list[object] = []

    def start(self, **values):
        safe = {key: value for key, value in values.items() if key != "callback"}
        self.calls.append(("start", safe))
        callback = values.get("callback")
        if callable(callback):
            self.callbacks.append(callback)
        return "scheduled"

    def cancel(self, **values):
        self.calls.append(("cancel", dict(values)))
        return values.get("timer_id")

    def fire_all(self) -> int:
        """Queue every retained timer callback exactly once."""
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks:
            callback(self.response)
        return len(callbacks)


class ScreenFlowWidget(QWidget):
    """Edit screen relationships and test structural navigation events."""

    open_screen_requested = Signal(str)
    open_simulator_requested = Signal()
    run_simulator_requested = Signal()

    def __init__(
        self,
        session: DesignerSession,
        parent: QWidget | None = None,
        flow_library: FlowFragmentLibrary | None = None,
    ):
        """Build the node graph and relationship controls."""
        super().__init__(parent)
        self.session = session
        self._updating = False
        self.simulated_screen_id = session.project.start_screen_id
        self.simulated_element_id = ""
        self.simulation_history: list[tuple[str, str, str, str]] = []
        self.simulation_history_index = -1
        self._flow_diagnostics: list[FlowDiagnostic] = []
        self._diagnostic_cursor = -1
        self._debug_runtime: BehaviorRuntime | None = None
        self._debug_ui: _FlowDebugUi | None = None
        self._debug_timer: _FlowDebugTimer | None = None
        self._context_flow_group_id = ""
        library_root = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        if not library_root:
            library_root = str(Path.home() / ".local" / "share" / "PicoGraphicsEditor")
        self.flow_library = flow_library or FlowFragmentLibrary(
            Path(library_root) / "personal-flow-library.json"
        )
        self._build_interface()
        self._connect_signals()
        self.refresh()
        install_widget_tooltips(self)

    def _build_interface(self) -> None:
        """Build the graph, relationship editor, and structural flow test."""
        layout = QHBoxLayout(self)
        self.flow_splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.flow_splitter)
        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        mouse_hint = QLabel(
            "Connect screens: drag an OUT port to an IN port. Arrows show direction."
        )
        mouse_hint.setWordWrap(True)
        controls_layout.addWidget(mouse_hint)
        self.manual_relation_group = QGroupBox("Advanced: add relation manually")
        self.manual_relation_group.setCheckable(True)
        self.manual_relation_group.setChecked(False)
        relation_form = QFormLayout(self.manual_relation_group)
        connection_hint = QLabel(
            "Drag a blue screen or element port to a green screen or element port."
        )
        connection_hint.setWordWrap(True)
        relation_form.addRow(connection_hint)
        self.source_combo = QComboBox()
        self.target_combo = QComboBox()
        self.trigger_edit = QLineEdit("select")
        navigation_logic_help = QLabel(
            "Use typed Condition and Action nodes for executable logic. "
            "Navigation relations only describe where an event goes."
        )
        navigation_logic_help.setWordWrap(True)
        self.condition_edit = QLineEdit()
        self.condition_edit.setReadOnly(True)
        self.action_edit = QLineEdit()
        self.action_edit.setReadOnly(True)
        self.legacy_navigation_logic_group = QGroupBox(
            "Legacy non-executable relation logic"
        )
        legacy_navigation_layout = QFormLayout(self.legacy_navigation_logic_group)
        legacy_navigation_layout.addRow("Condition", self.condition_edit)
        legacy_navigation_layout.addRow("Action", self.action_edit)
        self.clear_navigation_logic_button = QPushButton("Clear legacy fields")
        legacy_navigation_layout.addRow(self.clear_navigation_logic_button)
        self.legacy_navigation_logic_group.hide()
        self.transition_combo = QComboBox()
        self.transition_combo.addItems(("replace", "push", "modal", "back"))
        relation_form.addRow("From", self.source_combo)
        relation_form.addRow("To", self.target_combo)
        relation_form.addRow("Trigger", self.trigger_edit)
        relation_form.addRow("Transition", self.transition_combo)
        relation_form.addRow(navigation_logic_help)
        relation_form.addRow(self.legacy_navigation_logic_group)
        self.add_relation_button = QPushButton("Add relation")
        relation_form.addRow(self.add_relation_button)
        controls_layout.addWidget(self.manual_relation_group)
        self.manual_relation_group.toggled.connect(
            lambda expanded: set_collapsible_group_expanded(
                self.manual_relation_group, expanded
            )
        )
        set_collapsible_group_expanded(self.manual_relation_group, False)
        controls_layout.addWidget(QLabel("Relations · Right-click for actions"))
        self.connection_list = QListWidget()
        self.connection_list.setWordWrap(True)
        self.connection_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.connection_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        controls_layout.addWidget(self.connection_list, 1)
        relation_buttons = QHBoxLayout()
        self.update_relation_button = QPushButton("Update")
        self.delete_relation_button = QPushButton("Delete")
        relation_buttons.addWidget(self.update_relation_button)
        relation_buttons.addWidget(self.delete_relation_button)
        controls_layout.addLayout(relation_buttons)
        self.start_screen_button = QPushButton("Set selected as start")
        self.open_screen_button = QPushButton("Open selected screen")
        self.auto_layout_button = QPushButton("Auto-layout graph")
        controls_layout.addWidget(self.start_screen_button)
        controls_layout.addWidget(self.open_screen_button)
        controls_layout.addWidget(self.auto_layout_button)
        self.flow_test_group = QGroupBox("Flow test")
        simulator_layout = QVBoxLayout(self.flow_test_group)
        self.flow_test_tabs = QTabWidget()
        event_test_tab = QWidget()
        event_test_layout = QVBoxLayout(event_test_tab)
        event_test_layout.setContentsMargins(4, 4, 4, 4)
        self.simulator_label = QLabel()
        self.simulator_label.setWordWrap(True)
        self.simulator_event_edit = QLineEdit()
        self.simulator_event_edit.setPlaceholderText("Enter event trigger")
        simulator_buttons = QHBoxLayout()
        self.send_event_button = QPushButton("Send event")
        self.reset_simulator_button = QPushButton("Reset")
        simulator_buttons.addWidget(self.send_event_button)
        simulator_buttons.addWidget(self.reset_simulator_button)
        self.simulator_result_label = QLabel("Ready")
        self.simulator_result_label.setWordWrap(True)
        simulation_history_buttons = QHBoxLayout()
        self.simulator_back_button = QPushButton("Back")
        self.simulator_forward_button = QPushButton("Forward")
        self.trace_behavior_button = QPushButton("Run structural trace")
        simulation_history_buttons.addWidget(self.simulator_back_button)
        simulation_history_buttons.addWidget(self.simulator_forward_button)
        self.simulator_history_list = QListWidget()
        self.simulator_history_list.setMaximumHeight(90)
        event_test_layout.addWidget(self.simulator_label)
        event_test_layout.addWidget(self.simulator_event_edit)
        event_test_layout.addLayout(simulator_buttons)
        event_test_layout.addLayout(simulation_history_buttons)
        event_test_layout.addWidget(self.trace_behavior_button)
        event_test_layout.addWidget(self.simulator_history_list)
        event_test_layout.addWidget(self.simulator_result_label)
        self.flow_test_tabs.addTab(event_test_tab, "Navigation")
        runtime_trace_tab = QWidget()
        runtime_trace_layout = QVBoxLayout(runtime_trace_tab)
        runtime_help = QLabel(
            "Run the selected node with deterministic editor services. Step executes "
            "one node; Continue runs until completion or a breakpoint."
        )
        runtime_help.setWordWrap(True)
        self.runtime_selected_label = QLabel("Entry: select a behavior node")
        self.runtime_selected_label.setWordWrap(True)
        self.runtime_payload_edit = QPlainTextEdit()
        self.runtime_payload_edit.setMaximumHeight(70)
        self.runtime_payload_edit.setPlaceholderText(
            "Optional JSON payload. Leave empty to use the selected widget value."
        )
        runtime_scenario_layout = QGridLayout()
        self.runtime_outcome_combo = QComboBox()
        self.runtime_outcome_combo.addItem("Service succeeds", "success")
        self.runtime_outcome_combo.addItem("Service returns error", "error")
        self.runtime_outcome_combo.addItem("Service is cancelled", "cancel")
        self.runtime_service_response_edit = QLineEdit()
        self.runtime_service_response_edit.setPlaceholderText(
            "Optional JSON service/timer response"
        )
        runtime_scenario_layout.addWidget(QLabel("Scenario"), 0, 0)
        runtime_scenario_layout.addWidget(self.runtime_outcome_combo, 0, 1)
        runtime_scenario_layout.addWidget(QLabel("Response"), 1, 0)
        runtime_scenario_layout.addWidget(self.runtime_service_response_edit, 1, 1)
        self.runtime_trace_list = QListWidget()
        self.runtime_trace_list.setWordWrap(True)
        self.runtime_payload_view = QPlainTextEdit()
        self.runtime_payload_view.setReadOnly(True)
        self.runtime_payload_view.setMaximumHeight(80)
        self.runtime_payload_view.setPlaceholderText(
            "Select a trace row to inspect its redacted payload."
        )
        self.runtime_trace_limit_label = QLabel("Retaining up to 250 entries")
        self.runtime_trace_limit_label.setWordWrap(True)
        runtime_buttons = QGridLayout()
        self.runtime_start_button = QPushButton("Start")
        self.runtime_step_button = QPushButton("Step")
        self.runtime_continue_button = QPushButton("Continue")
        self.runtime_stop_button = QPushButton("Stop")
        self.runtime_fire_timer_button = QPushButton("Fire timer")
        self.runtime_clear_button = QPushButton("Clear Trace")
        runtime_buttons.addWidget(self.runtime_start_button, 0, 0)
        runtime_buttons.addWidget(self.runtime_step_button, 0, 1)
        runtime_buttons.addWidget(self.runtime_continue_button, 0, 2)
        runtime_buttons.addWidget(self.runtime_stop_button, 1, 0)
        runtime_buttons.addWidget(self.runtime_fire_timer_button, 1, 1)
        runtime_buttons.addWidget(self.runtime_clear_button, 1, 2)
        runtime_trace_layout.addWidget(runtime_help)
        runtime_trace_layout.addWidget(self.runtime_selected_label)
        runtime_trace_layout.addWidget(self.runtime_payload_edit)
        runtime_trace_layout.addLayout(runtime_scenario_layout)
        runtime_trace_layout.addWidget(self.runtime_trace_list, 1)
        runtime_trace_layout.addWidget(self.runtime_payload_view)
        runtime_trace_layout.addWidget(self.runtime_trace_limit_label)
        runtime_trace_layout.addLayout(runtime_buttons)
        self.flow_test_tabs.addTab(runtime_trace_tab, "Debugger")
        self.preview = GuiPreview(self.session)
        self.preview.setToolTip(
            "Interact with the safe structural preview without executing app code.\n"
            "Example: Focus a button and press Enter to follow its event relation."
        )
        preview_tab = QWidget()
        preview_layout = QVBoxLayout(preview_tab)
        preview_layout.setContentsMargins(4, 4, 4, 4)
        preview_notice = QLabel(
            "Structural preview only: navigation and focus work here; executable "
            "behavior runs in Debugger or Device Simulator."
        )
        preview_notice.setWordWrap(True)
        preview_layout.addWidget(preview_notice)
        preview_layout.addWidget(self.preview, 1)
        self.flow_test_tabs.addTab(preview_tab, "Preview")
        simulator_layout.addWidget(self.flow_test_tabs)
        controls_layout.addWidget(self.flow_test_group)
        controls.setMinimumWidth(240)
        controls.setMaximumWidth(340)
        self.flow_splitter.addWidget(controls)

        graph_panel = QWidget()
        graph_layout = QVBoxLayout(graph_panel)
        graph_layout.setContentsMargins(0, 0, 0, 0)
        assistant_row = QHBoxLayout()
        self.flow_assistant_banner = QLabel()
        self.flow_assistant_banner.setWordWrap(True)
        self.validate_flow_button = QPushButton("Validate flow")
        self.next_issue_button = QPushButton("Next issue")
        self.debug_selected_button = QPushButton("Debug selected")
        assistant_row.addWidget(self.flow_assistant_banner, 1)
        assistant_row.addWidget(self.validate_flow_button)
        assistant_row.addWidget(self.next_issue_button)
        assistant_row.addWidget(self.debug_selected_button)
        graph_help_row = QHBoxLayout()
        graph_action_row = QHBoxLayout()
        simulator_action_row = QHBoxLayout()
        self.graph_hint = QLabel(
            "Mouse wheel: zoom · Hold middle mouse: pan · Right-click: actions"
        )
        self.graph_zoom_label = QLabel("Zoom: 100%")
        self.fit_graph_button = QPushButton("Fit visible")
        self.fit_selection_button = QPushButton("Zoom selection")
        self.open_simulator_button = QPushButton("Open Device Simulator")
        self.open_simulator_button.setToolTip(
            "Open the dedicated device simulator without starting it.\n"
            "Example: Open it to inspect launch or capture settings."
        )
        self.run_simulator_button = QPushButton("▶ Run current design")
        self.run_simulator_button.setToolTip(
            "Open the Device Simulator and run the current in-memory project.\n"
            "Example: Test unsaved Screen Flow changes immediately."
        )
        self.add_behavior_kind_combo = QComboBox()
        self.add_behavior_kind_combo.hide()
        for kind in FLOW_NODE_KINDS:
            self.add_behavior_kind_combo.addItem(kind.title(), kind)
        self.add_behavior_operation_combo = QComboBox()
        self.add_behavior_operation_combo.setEditable(True)
        self.add_behavior_operation_combo.setInsertPolicy(
            QComboBox.InsertPolicy.NoInsert
        )
        for operation in OPERATIONS:
            self.add_behavior_operation_combo.addItem(
                f"{operation.category} · {operation.label}", operation.id
            )
        for kind in ("component", "comment"):
            self.add_behavior_operation_combo.addItem(
                f"Structural · {kind.title()}", f"structural:{kind}"
            )
        self._restore_combo(self.add_behavior_operation_combo, "custom.handler")
        self.add_behavior_button = QPushButton("Add operation")
        self.flow_visibility_combo = QComboBox()
        self.flow_visibility_combo.addItem("Screens + behavior", "both")
        self.flow_visibility_combo.addItem("Screens only", "screens")
        self.flow_visibility_combo.addItem("Behavior only", "behavior")
        self.flow_search_combo = QComboBox()
        self.flow_search_combo.setEditable(True)
        self.flow_search_combo.setMinimumWidth(150)
        self.flow_search_combo.lineEdit().setPlaceholderText("Find node")
        self.layout_direction_combo = QComboBox()
        self.layout_direction_combo.addItem("Layout →", "horizontal")
        self.layout_direction_combo.addItem("Layout ↓", "vertical")
        graph_help_row.addWidget(self.graph_hint)
        graph_help_row.addStretch(1)
        graph_help_row.addWidget(self.graph_zoom_label)
        graph_help_row.addWidget(self.fit_graph_button)
        graph_help_row.addWidget(self.fit_selection_button)
        graph_action_row.addWidget(QLabel("Add"))
        graph_action_row.addWidget(self.add_behavior_operation_combo, 1)
        graph_action_row.addWidget(self.add_behavior_button)
        graph_action_row.addWidget(QLabel("Show"))
        graph_action_row.addWidget(self.flow_visibility_combo)
        graph_navigation_row = QHBoxLayout()
        graph_navigation_row.addWidget(QLabel("Find / jump"))
        graph_navigation_row.addWidget(self.flow_search_combo, 1)
        graph_navigation_row.addWidget(QLabel("Arrange"))
        graph_navigation_row.addWidget(self.layout_direction_combo)
        simulator_action_row.addWidget(QLabel("Final executable check"))
        simulator_action_row.addStretch(1)
        simulator_action_row.addWidget(self.open_simulator_button)
        simulator_action_row.addWidget(self.run_simulator_button)
        graph_layout.addLayout(assistant_row)
        graph_layout.addLayout(graph_help_row)
        graph_layout.addLayout(graph_action_row)
        graph_layout.addLayout(graph_navigation_row)
        graph_layout.addLayout(simulator_action_row)
        graph_legend = QLabel(
            "Green/gray nodes: screens and navigation · Blue/orange nodes: executable behavior"
        )
        graph_legend.setWordWrap(True)
        graph_layout.addWidget(graph_legend)
        self.graph_scroll = QScrollArea()
        self.graph = FlowCanvas(self.session)
        self.graph.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.graph.setToolTip(
            "Connect and arrange screen nodes; right-click for common graph actions.\n"
            "Example: Select a screen, then right-click to mark it as the start."
        )
        self.graph_scroll.setWidget(self.graph)
        self.graph_scroll.setWidgetResizable(False)
        graph_layout.addWidget(self.graph_scroll, 1)
        graph_panel.setMinimumWidth(300)
        self.flow_splitter.addWidget(graph_panel)
        inspector = self._build_flow_inspector()
        inspector.setMinimumWidth(280)
        inspector.setMaximumWidth(390)
        self.flow_splitter.addWidget(inspector)
        self.flow_splitter.setSizes((300, 720, 340))
        self.flow_splitter.setStretchFactor(1, 1)
        self.setMinimumSize(900, 650)

    def focus_element_interaction(self, screen_id: str, element_id: str) -> None:
        """Prepare the relation editor for one App GUI element endpoint."""
        self.refresh()
        endpoint = flow_endpoint_key(screen_id, element_id)
        source_index = self.source_combo.findData(endpoint)
        if source_index < 0:
            return
        self.manual_relation_group.setChecked(True)
        self.source_combo.setCurrentIndex(source_index)
        for target_index in range(self.target_combo.count()):
            target_screen_id, unused_element_id = parse_flow_endpoint(
                self.target_combo.itemData(target_index)
            )
            if target_screen_id and target_screen_id != screen_id:
                self.target_combo.setCurrentIndex(target_index)
                break
        self.graph.selected_screen_id = screen_id
        self.graph.update()
        self._source_endpoint_changed()
        self.source_combo.setFocus()

    def create_behavior_from_element_dialog(
        self, screen_id: str, element_id: str
    ) -> bool:
        """Create a bound behavior through one compact guided dialog."""
        element = self.session.project.element(screen_id, element_id)
        if element is None:
            return False
        dialog = QDialog(self)
        dialog.setWindowTitle("Create behavior from this element")
        layout = QFormLayout(dialog)
        source_label = QLabel(f"{element.name} · {element.activation_event()}")
        source_label.setWordWrap(True)
        outcome = QComboBox()
        outcome.addItem("Run custom handler", "custom.handler")
        outcome.addItem("Handle widget value", "guided.handle_value")
        outcome.addItem("Branch by current value", "guided.branch_value")
        if not _element_has_behavior_value(element):
            for item_index in (1, 2):
                item = outcome.model().item(item_index)
                if item is not None:
                    item.setEnabled(False)
                    item.setToolTip("This element has no readable runtime value.")
        outcome.addItem("Navigate to screen", "navigation.navigate")
        outcome.addItem("Set status text", "ui.set_text")
        outcome.addItem("Publish MQTT message", "mqtt.publish")
        outcome.insertSeparator(outcome.count())
        preset_ids = {
            "custom.handler",
            "navigation.navigate",
            "ui.set_text",
            "mqtt.publish",
        }
        for operation in OPERATIONS:
            if operation.kind == "event" or operation.id in preset_ids:
                continue
            if any(port.direction == "in" for port in operation.ports):
                outcome.addItem(
                    f"{operation.category} · {operation.label}", operation.id
                )
        target = QComboBox()
        for screen in self.session.project.screens:
            if screen.id != screen_id:
                target.addItem(screen.name, screen.id)
        target_element = QComboBox()

        def refresh_targets() -> None:
            selected_operation = operation_spec(str(outcome.currentData() or ""))
            needs_element = bool(
                selected_operation
                and any(
                    field.value_type == "element"
                    for field in selected_operation.properties
                )
            )
            target_element.clear()
            if selected_operation is not None and needs_element:
                for candidate_screen in self.session.project.screens:
                    for candidate in candidate_screen.elements:
                        if _element_supports_operation(
                            candidate, selected_operation.id
                        ):
                            target_element.addItem(
                                f"{candidate_screen.name} · {candidate.name}",
                                candidate.id,
                            )
            target.setEnabled(outcome.currentData() == "navigation.navigate")
            target_element.setEnabled(needs_element)

        layout.addRow("From", source_label)
        layout.addRow("Outcome", outcome)
        layout.addRow("Target screen", target)
        layout.addRow("Target element", target_element)
        refresh_targets()
        outcome.currentIndexChanged.connect(
            lambda unused_index: refresh_targets()
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        operation_id = str(outcome.currentData() or "custom.handler")
        return self.create_behavior_from_element(
            screen_id,
            element_id,
            operation_id,
            str(target.currentData() or ""),
            target_element_id=str(target_element.currentData() or ""),
        )

    def create_behavior_from_element(
        self,
        screen_id: str,
        element_id: str,
        operation_id: str = "custom.handler",
        target_screen_id: str = "",
        *,
        target_element_id: str = "",
        action_position: tuple[int, int] | None = None,
    ) -> bool:
        """Atomically bind a stable UI event and connect one chosen operation."""
        project = self.session.project
        element = project.element(screen_id, element_id)
        guided_mode = operation_id if operation_id.startswith("guided.") else ""
        if (
            guided_mode
            and element is not None
            and not _element_has_behavior_value(element)
        ):
            return False
        resolved_operation_id = {
            "guided.handle_value": "custom.handler",
            "guided.branch_value": "logic.compare",
        }.get(guided_mode, operation_id)
        operation = operation_spec(resolved_operation_id)
        if element is None or operation is None or operation.kind == "event":
            return False
        if not _element_emits_behavior_event(element):
            return False
        if (
            operation.id == "navigation.navigate"
            and project.screen(target_screen_id) is None
        ):
            return False
        element_property = next(
            (
                field
                for field in operation.properties
                if field.value_type == "element"
            ),
            None,
        )
        if element_property is not None:
            if not target_element_id and _element_supports_operation(
                element, operation.id
            ):
                target_element_id = element.id
            target_element = next(
                (
                    candidate
                    for screen in project.screens
                    for candidate in screen.elements
                    if candidate.id == target_element_id
                ),
                None,
            )
            if target_element is None or not _element_supports_operation(
                target_element, operation.id
            ):
                return False
        if not element.event_id:
            element.event_id = new_identifier("event")
        event_node = next(
            (
                node
                for node in project.behavior_nodes
                if node.operation == "event.ui"
                and node.binding.get("event_id") == element.event_id
            ),
            None,
        )
        self.session.begin_transaction()
        try:
            if event_node is None:
                event_x = (
                    max(20, action_position[0] - 280)
                    if action_position is not None
                    else 360
                )
                event_y = (
                    max(20, action_position[1]) if action_position is not None else 420
                )
                event_node = FlowNode.create(
                    "event",
                    len(project.behavior_nodes) + 1,
                    event_x,
                    event_y,
                )
                event_node.name = f"{element.name} activated"
                event_node.set_operation("event.ui")
                event_node.binding = {
                    "screen_id": screen_id,
                    "element_id": element_id,
                    "event_id": element.event_id,
                    "widget_type": element.native_widget or element.kind,
                }
                project.behavior_nodes.append(event_node)
            else:
                event_node.binding.setdefault(
                    "widget_type", element.native_widget or element.kind
                )
            action = FlowNode.create(
                operation.kind,
                len(project.behavior_nodes) + 1,
                max(20, action_position[0])
                if action_position is not None
                else event_node.node_x + 280,
                max(20, action_position[1])
                if action_position is not None
                else event_node.node_y,
            )
            action.name = operation.label
            action.set_operation(operation.id)
            if element_property is not None:
                action.properties[element_property.id] = target_element_id
            if operation.id == "custom.handler":
                action.name = f"Handle {element.name}"
                action.properties["handler"] = flow_stub_name(action)
            elif operation.id == "navigation.navigate":
                action.properties["screen_id"] = target_screen_id
                existing_relation = next(
                    (
                        relation
                        for relation in project.connections
                        if relation.source_id == screen_id
                        and relation.source_element_id == element_id
                        and relation.target_id == target_screen_id
                    ),
                    None,
                )
                if existing_relation is None:
                    relation = FlowConnection.create(
                        screen_id,
                        target_screen_id,
                        element.activation_event(),
                        element_id,
                    )
                    relation.trigger_event_id = element.event_id
                    project.connections.append(relation)
            elif operation.id == "logic.compare":
                widget_type = element.native_widget or element.kind
                if widget_type in {"toggle", "toggle_list"}:
                    action.properties.update(
                        {"field": "checked", "comparison": "true", "value": ""}
                    )
                elif widget_type in {"keyboard", "search_bar", "textbox"}:
                    action.properties.update(
                        {"field": "text", "comparison": "non_empty", "value": ""}
                    )
                else:
                    selected = (
                        element.widget_items[element.widget_selected_index]
                        if element.widget_items
                        and 0
                        <= element.widget_selected_index
                        < len(element.widget_items)
                        else element.text
                    )
                    action.properties.update(
                        {"field": "value", "comparison": "equal", "value": selected}
                    )
            elif operation.id == "ui.set_text":
                action.properties["text"] = "$value"
            elif operation.id == "mqtt.publish":
                action.properties["payload"] = "$value"
            project.behavior_nodes.append(action)
            input_port = next(
                (port for port in action.ports if port.direction == "in"), None
            )
            if input_port is None:
                return False
            project.behavior_connections.append(
                BehaviorConnection.create(
                    event_node.id, "event", action.id, input_port.id
                )
            )
            if guided_mode == "guided.handle_value":
                handler = action
                read_value = FlowNode.create(
                    "action",
                    len(project.behavior_nodes) + 1,
                    event_node.node_x + 280,
                    event_node.node_y,
                )
                read_value.name = f"Read {element.name} value"
                read_value.set_operation("ui.read_value")
                read_value.properties["element_id"] = element_id
                handler.node_x += 280
                project.behavior_nodes.insert(-1, read_value)
                project.behavior_connections[-1] = BehaviorConnection.create(
                    event_node.id, "event", read_value.id, "in"
                )
                project.behavior_connections.append(
                    BehaviorConnection.create(read_value.id, "done", handler.id, "in")
                )
            project.flow_standard_version = 2
            self.graph.selected_behavior_node_ids = {
                event_node.id,
                action.id,
                *({read_value.id} if guided_mode == "guided.handle_value" else set()),
            }
            self.graph.primary_behavior_node_id = action.id
            self.session.mark_changed()
        finally:
            self.session.end_transaction()
        self._refresh_behavior_inspector()
        self._center_graph_at(action.node_x, action.node_y)
        return True

    def _build_flow_inspector(self) -> QWidget:
        """Build node contracts, diagnostics, overview, and fragment controls."""
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        self.flow_minimap = FlowMiniMap(self.session)
        panel_layout.addWidget(self.flow_minimap, 0, Qt.AlignmentFlag.AlignHCenter)
        self.flow_inspector_tabs = QTabWidget()

        node_tab = QWidget()
        node_layout = QVBoxLayout(node_tab)
        self.selected_behavior_label = QLabel("No behavior node selected")
        self.selected_behavior_label.setWordWrap(True)
        node_layout.addWidget(self.selected_behavior_label)
        node_form = QFormLayout()
        self.behavior_kind_combo = QComboBox()
        for kind in FLOW_NODE_KINDS:
            self.behavior_kind_combo.addItem(kind.title(), kind)
        self.behavior_name_edit = QLineEdit()
        self.behavior_operation_combo = QComboBox()
        self.behavior_operation_combo.addItem("Structural only", "")
        self.behavior_operation_fields: dict[str, QWidget] = {}
        self.behavior_operation_group = QGroupBox("Operation properties")
        self.behavior_operation_form = QFormLayout(self.behavior_operation_group)
        self.behavior_id_label = QLabel("—")
        self.behavior_id_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.behavior_description_edit = QPlainTextEdit()
        self.behavior_description_edit.setMaximumHeight(64)
        self.behavior_properties_edit = QPlainTextEdit()
        self.behavior_properties_edit.setMaximumHeight(80)
        self.behavior_properties_edit.setPlaceholderText('{"advanced": "value"}')
        self.behavior_advanced_group = QGroupBox("Advanced properties JSON")
        self.behavior_advanced_group.setCheckable(True)
        self.behavior_advanced_group.setChecked(False)
        advanced_layout = QVBoxLayout(self.behavior_advanced_group)
        advanced_layout.addWidget(self.behavior_properties_edit)
        set_collapsible_group_expanded(self.behavior_advanced_group, False)
        self.behavior_advanced_group.toggled.connect(
            lambda expanded: set_collapsible_group_expanded(
                self.behavior_advanced_group, expanded
            )
        )
        self.behavior_pinned_check = QCheckBox("Pin during auto-layout")
        self.behavior_locked_check = QCheckBox("Lock editing and movement")
        self.behavior_breakpoint_check = QCheckBox("Pause debugger after this node")
        self.behavior_ports_label = QLabel("Ports: —")
        self.behavior_ports_label.setWordWrap(True)
        self.behavior_stub_label = QLabel("Handler: —")
        self.behavior_stub_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        node_form.addRow("Stable ID", self.behavior_id_label)
        node_form.addRow("Kind", self.behavior_kind_combo)
        node_form.addRow("Operation", self.behavior_operation_combo)
        node_form.addRow("Name", self.behavior_name_edit)
        node_form.addRow("Description", self.behavior_description_edit)
        node_form.addRow(self.behavior_operation_group)
        node_form.addRow(self.behavior_advanced_group)
        node_form.addRow(self.behavior_pinned_check)
        node_form.addRow(self.behavior_locked_check)
        node_form.addRow(self.behavior_breakpoint_check)
        node_form.addRow(self.behavior_ports_label)
        node_form.addRow(self.behavior_stub_label)
        node_layout.addLayout(node_form)
        node_buttons = QGridLayout()
        self.apply_behavior_button = QPushButton("Apply node")
        self.duplicate_behavior_button = QPushButton("Duplicate")
        self.delete_behavior_button = QPushButton("Delete")
        self.group_behavior_button = QPushButton("Group selected")
        self.ungroup_behavior_button = QPushButton("Ungroup")
        self.collapse_behavior_group_button = QPushButton("Collapse group")
        node_buttons.addWidget(self.apply_behavior_button, 0, 0)
        node_buttons.addWidget(self.duplicate_behavior_button, 0, 1)
        node_buttons.addWidget(self.delete_behavior_button, 1, 0, 1, 2)
        node_buttons.addWidget(self.group_behavior_button, 2, 0)
        node_buttons.addWidget(self.ungroup_behavior_button, 2, 1)
        node_buttons.addWidget(self.collapse_behavior_group_button, 3, 0, 1, 2)
        node_layout.addLayout(node_buttons)

        node_layout.addStretch(1)
        node_scroll = QScrollArea()
        node_scroll.setWidgetResizable(True)
        node_scroll.setWidget(node_tab)
        self.flow_inspector_tabs.addTab(node_scroll, "Node")

        connection_tab = QWidget()
        connection_layout = QVBoxLayout(connection_tab)
        connection_help = QLabel(
            "Drag compatible ports on the graph, or choose explicit endpoints here."
        )
        connection_help.setWordWrap(True)
        connection_layout.addWidget(connection_help)
        connection_group = QGroupBox("Typed behavior connection")
        connection_form = QFormLayout(connection_group)
        self.behavior_source_node_combo = QComboBox()
        self.behavior_source_port_combo = QComboBox()
        self.behavior_target_node_combo = QComboBox()
        self.behavior_target_port_combo = QComboBox()
        self.behavior_connection_label_edit = QLineEdit()
        self.behavior_connection_label_edit.setPlaceholderText("Optional edge label")
        connection_branch_help = QLabel(
            "Branching is executable only through a Condition node and its True / "
            "False output ports."
        )
        connection_branch_help.setWordWrap(True)
        self.behavior_connection_condition_edit = QLineEdit()
        self.behavior_connection_condition_edit.setReadOnly(True)
        self.legacy_behavior_condition_group = QGroupBox(
            "Unsupported legacy connection condition"
        )
        legacy_condition_layout = QVBoxLayout(self.legacy_behavior_condition_group)
        legacy_condition_layout.addWidget(self.behavior_connection_condition_edit)
        self.clear_behavior_condition_button = QPushButton(
            "Clear condition and use a Condition node"
        )
        legacy_condition_layout.addWidget(self.clear_behavior_condition_button)
        self.legacy_behavior_condition_group.hide()
        self.add_behavior_connection_button = QPushButton("Connect typed ports")
        connection_form.addRow("From node", self.behavior_source_node_combo)
        connection_form.addRow("From port", self.behavior_source_port_combo)
        connection_form.addRow("To node", self.behavior_target_node_combo)
        connection_form.addRow("To port", self.behavior_target_port_combo)
        connection_form.addRow("Label", self.behavior_connection_label_edit)
        connection_form.addRow(connection_branch_help)
        connection_form.addRow(self.add_behavior_connection_button)
        connection_layout.addWidget(connection_group)
        connection_layout.addWidget(self.legacy_behavior_condition_group)
        self.behavior_connection_list = QListWidget()
        self.behavior_connection_list.setWordWrap(True)
        self.behavior_connection_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.delete_behavior_connection_button = QPushButton(
            "Delete selected behavior connection"
        )
        self.update_behavior_connection_button = QPushButton(
            "Reconnect / update selected connection"
        )
        connection_layout.addWidget(QLabel("Existing behavior connections"))
        connection_layout.addWidget(self.behavior_connection_list, 1)
        connection_layout.addWidget(self.update_behavior_connection_button)
        connection_layout.addWidget(self.delete_behavior_connection_button)
        self.flow_inspector_tabs.addTab(connection_tab, "Connect")

        diagnostics_tab = QWidget()
        diagnostics_layout = QVBoxLayout(diagnostics_tab)
        diagnostics_help = QLabel(
            "Errors block generation. Warnings identify incomplete structural contracts."
        )
        diagnostics_help.setWordWrap(True)
        self.flow_diagnostic_summary_label = QLabel()
        self.flow_diagnostic_summary_label.setWordWrap(True)
        self.flow_diagnostic_filter = QComboBox()
        self.flow_diagnostic_filter.addItem("All findings", "all")
        self.flow_diagnostic_filter.addItem("Errors", "error")
        self.flow_diagnostic_filter.addItem("Warnings", "warning")
        self.flow_diagnostic_filter.addItem("Information", "info")
        self.flow_diagnostics_list = QListWidget()
        self.flow_diagnostics_list.setWordWrap(True)
        self.flow_diagnostic_detail = QLabel("Select a finding for a suggested fix.")
        self.flow_diagnostic_detail.setWordWrap(True)
        diagnostics_buttons = QHBoxLayout()
        self.refresh_diagnostics_button = QPushButton("Validate now")
        self.next_diagnostic_button = QPushButton("Next issue")
        self.go_to_diagnostic_button = QPushButton("Go to issue")
        diagnostics_buttons.addWidget(self.refresh_diagnostics_button)
        diagnostics_buttons.addWidget(self.next_diagnostic_button)
        diagnostics_buttons.addWidget(self.go_to_diagnostic_button)
        diagnostics_layout.addWidget(diagnostics_help)
        diagnostics_layout.addWidget(self.flow_diagnostic_summary_label)
        diagnostics_layout.addWidget(self.flow_diagnostic_filter)
        diagnostics_layout.addWidget(self.flow_diagnostics_list, 1)
        diagnostics_layout.addWidget(self.flow_diagnostic_detail)
        diagnostics_layout.addLayout(diagnostics_buttons)
        self.flow_inspector_tabs.addTab(diagnostics_tab, "Issues")

        library_tab = QWidget()
        library_layout = QVBoxLayout(library_tab)
        library_help = QLabel(
            "Save selected behavior nodes as a reusable fragment for other projects."
        )
        library_help.setWordWrap(True)
        self.flow_fragment_source_combo = QComboBox()
        self.flow_fragment_source_combo.addItem("Personal", "personal")
        self.flow_fragment_source_combo.addItem("Built in", "built-in")
        self.flow_fragment_source_combo.addItem("All", "all")
        self.flow_fragment_search = QLineEdit()
        self.flow_fragment_search.setPlaceholderText("Search recipes and fragments")
        self.flow_fragment_search.setClearButtonEnabled(True)
        self.flow_fragment_list = QListWidget()
        self.flow_fragment_list.setWordWrap(True)
        self.flow_fragment_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.flow_fragment_preview = QLabel("Select a reusable flow.")
        self.flow_fragment_preview.setWordWrap(True)
        self.save_flow_fragment_button = QPushButton("Save selection as fragment")
        self.insert_flow_fragment_button = QPushButton("Insert fragment")
        fragment_actions = QHBoxLayout()
        self.rename_flow_fragment_button = QPushButton("Rename")
        self.delete_flow_fragment_button = QPushButton("Delete")
        fragment_actions.addWidget(self.rename_flow_fragment_button)
        fragment_actions.addWidget(self.delete_flow_fragment_button)
        library_layout.addWidget(library_help)
        library_layout.addWidget(self.flow_fragment_source_combo)
        library_layout.addWidget(self.flow_fragment_search)
        library_layout.addWidget(self.flow_fragment_list, 1)
        library_layout.addWidget(self.flow_fragment_preview)
        library_layout.addWidget(self.save_flow_fragment_button)
        library_layout.addWidget(self.insert_flow_fragment_button)
        library_layout.addLayout(fragment_actions)
        self.flow_inspector_tabs.addTab(library_tab, "Recipes")

        panel_layout.addWidget(self.flow_inspector_tabs, 1)
        return panel

    def _connect_signals(self) -> None:
        """Connect graph controls and structural flow-test events."""
        self.session.project_changed.connect(self.refresh)
        self.session.live_previews_changed.connect(self.graph.update)
        self.graph.screen_selected.connect(self._graph_screen_selected)
        self.graph.screen_activated.connect(self._open_screen)
        self.graph.connection_requested.connect(self._graph_connection_requested)
        self.graph.element_behavior_dropped.connect(self._open_element_action_palette)
        self.graph.connection_selected.connect(self._select_connection_id)
        self.graph.connection_delete_requested.connect(self._delete_connection_id)
        self.graph.behavior_node_selected.connect(self._behavior_node_selected)
        self.graph.behavior_connection_requested.connect(
            self._create_behavior_connection
        )
        self.graph.behavior_connection_dropped.connect(
            self._open_compatible_operation_palette
        )
        self.graph.behavior_connection_selected.connect(
            self._select_behavior_connection_id
        )
        self.graph.behavior_nodes_delete_requested.connect(self._delete_behavior_nodes)
        self.graph.behavior_connection_delete_requested.connect(
            self._delete_behavior_connection_id
        )
        self.graph.scroll_requested.connect(self._scroll_graph)
        self.graph.zoom_changed.connect(self._graph_zoom_changed)
        self.graph.interaction_feedback.connect(self._set_flow_assistant_message)
        self.graph.geometry_changed.connect(self._graph_geometry_changed)
        self.graph.customContextMenuRequested.connect(self._show_graph_context_menu)
        self.connection_list.customContextMenuRequested.connect(
            self._show_relation_context_menu
        )
        self.fit_graph_button.clicked.connect(self._fit_graph_nodes)
        self.fit_selection_button.clicked.connect(self._zoom_selected_graph)
        self.flow_visibility_combo.currentIndexChanged.connect(
            self._flow_visibility_changed
        )
        self.open_simulator_button.clicked.connect(self.open_simulator_requested)
        self.run_simulator_button.clicked.connect(self.run_simulator_requested)
        self.add_behavior_button.clicked.connect(self._add_behavior_node)
        self.flow_search_combo.activated.connect(self._jump_to_flow_search)
        self.flow_minimap.center_requested.connect(self._center_graph_at)
        self.graph_scroll.horizontalScrollBar().valueChanged.connect(
            self._update_minimap_viewport
        )
        self.graph_scroll.verticalScrollBar().valueChanged.connect(
            self._update_minimap_viewport
        )
        self.add_relation_button.clicked.connect(self._add_relation)
        self.update_relation_button.clicked.connect(self._update_relation)
        self.delete_relation_button.clicked.connect(self._delete_relation)
        self.clear_navigation_logic_button.clicked.connect(
            self._clear_selected_navigation_logic
        )
        self.connection_list.currentRowChanged.connect(self._connection_selected)
        self.source_combo.currentIndexChanged.connect(self._source_endpoint_changed)
        self.start_screen_button.clicked.connect(self._set_start_screen)
        self.open_screen_button.clicked.connect(self._open_selected_screen)
        self.auto_layout_button.clicked.connect(self._auto_layout_nodes)
        self.send_event_button.clicked.connect(self._send_simulator_event)
        self.reset_simulator_button.clicked.connect(self._reset_simulator)
        self.simulator_back_button.clicked.connect(
            lambda: self._move_simulation_history(-1)
        )
        self.simulator_forward_button.clicked.connect(
            lambda: self._move_simulation_history(1)
        )
        self.trace_behavior_button.clicked.connect(self._trace_selected_behavior)
        self.runtime_start_button.clicked.connect(self._start_flow_debugger)
        self.runtime_step_button.clicked.connect(self._step_flow_debugger)
        self.runtime_continue_button.clicked.connect(self._continue_flow_debugger)
        self.runtime_stop_button.clicked.connect(self._stop_flow_debugger)
        self.runtime_fire_timer_button.clicked.connect(self._fire_debug_timers)
        self.runtime_clear_button.clicked.connect(self._clear_flow_debugger)
        self.runtime_trace_list.currentItemChanged.connect(
            self._runtime_trace_item_changed
        )
        self.validate_flow_button.clicked.connect(self._show_flow_validation)
        self.next_issue_button.clicked.connect(self._jump_to_next_diagnostic)
        self.debug_selected_button.clicked.connect(self._start_flow_debugger)
        self.simulator_event_edit.returnPressed.connect(self._send_simulator_event)
        self.preview.event_requested.connect(self._preview_event_requested)
        self.preview.focus_changed.connect(self._preview_focus_changed)
        self.apply_behavior_button.clicked.connect(self._apply_behavior_node)
        self.behavior_kind_combo.currentIndexChanged.connect(
            self._behavior_kind_choice_changed
        )
        self.behavior_operation_combo.currentIndexChanged.connect(
            self._behavior_operation_choice_changed
        )
        self.duplicate_behavior_button.clicked.connect(
            self._duplicate_selected_behavior_nodes
        )
        self.delete_behavior_button.clicked.connect(
            lambda: self._delete_behavior_nodes(
                set(self.graph.selected_behavior_node_ids)
            )
        )
        self.group_behavior_button.clicked.connect(self._group_selected_behavior_nodes)
        self.ungroup_behavior_button.clicked.connect(
            self._ungroup_selected_behavior_nodes
        )
        self.collapse_behavior_group_button.clicked.connect(
            self._toggle_selected_behavior_group
        )
        self.behavior_source_node_combo.currentIndexChanged.connect(
            self._refresh_behavior_source_ports
        )
        self.behavior_source_port_combo.currentIndexChanged.connect(
            self._refresh_behavior_target_nodes
        )
        self.behavior_target_node_combo.currentIndexChanged.connect(
            self._refresh_behavior_target_ports
        )
        self.behavior_target_port_combo.currentIndexChanged.connect(
            self._update_behavior_connection_controls
        )
        self.add_behavior_connection_button.clicked.connect(
            self._add_behavior_connection_from_inspector
        )
        self.clear_behavior_condition_button.clicked.connect(
            self._clear_selected_behavior_condition
        )
        self.behavior_connection_list.currentRowChanged.connect(
            self._behavior_connection_list_selected
        )
        self.delete_behavior_connection_button.clicked.connect(
            self._delete_selected_behavior_connection
        )
        self.update_behavior_connection_button.clicked.connect(
            self._update_selected_behavior_connection
        )
        self.refresh_diagnostics_button.clicked.connect(self._refresh_flow_diagnostics)
        self.flow_diagnostics_list.itemDoubleClicked.connect(self._jump_to_diagnostic)
        self.flow_diagnostics_list.currentItemChanged.connect(
            self._diagnostic_item_changed
        )
        self.flow_diagnostic_filter.currentIndexChanged.connect(
            self._refresh_flow_diagnostics
        )
        self.next_diagnostic_button.clicked.connect(self._jump_to_next_diagnostic)
        self.go_to_diagnostic_button.clicked.connect(
            lambda: self._jump_to_diagnostic(self.flow_diagnostics_list.currentItem())
            if self.flow_diagnostics_list.currentItem() is not None
            else None
        )
        self.flow_fragment_list.itemSelectionChanged.connect(
            self._flow_fragment_selection_changed
        )
        self.flow_fragment_source_combo.currentIndexChanged.connect(
            self._refresh_flow_fragments
        )
        self.flow_fragment_search.textChanged.connect(self._refresh_flow_fragments)
        self.save_flow_fragment_button.clicked.connect(self._save_flow_fragment)
        self.insert_flow_fragment_button.clicked.connect(self._insert_flow_fragment)
        self.rename_flow_fragment_button.clicked.connect(self._rename_flow_fragment)
        self.delete_flow_fragment_button.clicked.connect(self._delete_flow_fragment)

    def _scroll_graph(self, horizontal: int, vertical: int) -> None:
        """Move the graph viewport by physical canvas pixels."""
        horizontal_bar = self.graph_scroll.horizontalScrollBar()
        vertical_bar = self.graph_scroll.verticalScrollBar()
        horizontal_bar.setValue(horizontal_bar.value() + horizontal)
        vertical_bar.setValue(vertical_bar.value() + vertical)

    def _graph_zoom_changed(self, zoom: float) -> None:
        """Show the current node graph zoom percentage."""
        self.graph_zoom_label.setText(f"Zoom: {round(zoom * 100)}%")
        self._update_minimap_viewport()

    def _graph_geometry_changed(self) -> None:
        """Refresh overview geometry after direct node movement."""
        self.flow_minimap.update()
        self._update_minimap_viewport()

    def _flow_visibility_changed(self) -> None:
        """Show screens, behavior, or both without mutating the project."""
        mode = str(self.flow_visibility_combo.currentData() or "both")
        self.graph.visibility_mode = mode
        self.flow_minimap.visibility_mode = mode
        if mode == "screens":
            self.graph.selected_behavior_node_ids.clear()
            self.graph.primary_behavior_node_id = None
            self.graph.selected_behavior_connection_id = None
        elif mode == "behavior":
            self.graph.selected_screen_id = None
            self.graph.selected_connection_id = None
        self.graph.refresh_geometry()
        self.graph.update()
        self.flow_minimap.update()
        self._fit_graph_nodes()

    def _zoom_selected_graph(self) -> None:
        """Bring the current selection to a readable editing zoom."""
        node = self._selected_behavior_node()
        screen = self.session.project.screen(self.graph.selected_screen_id or "")
        if node is not None:
            x = node.node_x + self.graph.BEHAVIOR_NODE_WIDTH / 2
            y = node.node_y + self.graph._behavior_node_height(node) / 2
        elif screen is not None:
            x = screen.node_x + self.graph.NODE_WIDTH / 2
            y = screen.node_y + self.graph._node_height(screen) / 2
        else:
            self._set_flow_assistant_message(
                "Select a screen or behavior node before choosing Zoom selection.",
                "info",
            )
            return
        if self.graph.zoom < 0.75:
            self.graph.set_zoom(0.75)
        self._center_graph_at(x, y)

    def set_runtime_trace(self, entries: list[object]) -> None:
        """Display bounded trace records received from generated behavior runtime."""
        self.runtime_trace_list.clear()
        for entry in entries[-250:]:
            if hasattr(entry, "node_id"):
                text = (
                    f"{entry.order} · {entry.node_id} · {entry.input_port} → "
                    f"{entry.output_port} · {entry.outcome} · {entry.duration_ms} ms · "
                    f"in {entry.payload} · out {entry.result}"
                )
            else:
                text = " · ".join(str(value) for value in entry)
            item = QListWidgetItem(text)
            if hasattr(entry, "node_id"):
                item.setData(
                    Qt.ItemDataRole.UserRole,
                    (
                        entry.node_id,
                        entry.output_port,
                        entry.payload,
                        entry.result,
                    ),
                )
            self.runtime_trace_list.addItem(item)
        if self.runtime_trace_list.count():
            self.runtime_trace_list.setCurrentRow(self.runtime_trace_list.count() - 1)

    def _set_runtime_trace_status(self, message: str) -> None:
        """Show one explicit runtime trace control result."""
        self.runtime_trace_limit_label.setText(message + " Retaining up to 250 entries")

    def _debug_entry_node(self) -> FlowNode | None:
        """Return the selected debugger entry or a useful first Event node."""
        selected = self._selected_behavior_node()
        if selected is not None:
            return selected
        return next(
            (
                node
                for node in self.session.project.behavior_nodes
                if node.operation.startswith("event.")
            ),
            None,
        )

    def _debug_payload(self, node: FlowNode, ui: _FlowDebugUi):
        """Return user JSON or an automatically constructed widget event payload."""
        source = self.runtime_payload_edit.toPlainText().strip()
        if source:
            try:
                return json.loads(source)
            except json.JSONDecodeError as error:
                raise BehaviorRuntimeError(
                    f"Invalid debugger payload JSON: {error}"
                ) from error
        if node.operation == "event.ui":
            return _widget_event_payload(
                ui, node.binding, node.binding.get("event_id", "")
            )
        return None

    def _debug_service_response(self):
        """Return the optional deterministic service/timer response value."""
        source = self.runtime_service_response_edit.text().strip()
        if not source:
            return None
        try:
            return json.loads(source)
        except json.JSONDecodeError as error:
            raise BehaviorRuntimeError(
                f"Invalid service response JSON: {error}"
            ) from error

    def _build_debug_runtime(self) -> BehaviorRuntime:
        """Create a safe deterministic runtime with no network or file writes."""
        self._debug_ui = _FlowDebugUi(self.session.project)
        response = self._debug_service_response()
        outcome = str(self.runtime_outcome_combo.currentData() or "success")
        external = _FlowDebugService(outcome, response)
        self._debug_timer = _FlowDebugTimer(response)
        services = {
            "ui": self._debug_ui,
            "state": {},
            "mqtt": external,
            "wifi": external,
            "storage": external,
            "timer": self._debug_timer,
        }
        handlers = {
            str(node.properties.get("handler") or flow_stub_name(node)): (
                lambda payload, runtime: payload
            )
            for node in self.session.project.behavior_nodes
            if node.operation == "custom.handler"
        }
        return BehaviorRuntime(
            self.session.project.behavior_nodes,
            self.session.project.behavior_connections,
            services,
            handlers,
        )

    def _start_flow_debugger(self) -> None:
        """Validate and queue a real deterministic debugger session."""
        diagnostics = flow_diagnostics(self.session.project)
        errors = [item for item in diagnostics if item.severity == "error"]
        if errors:
            self._refresh_flow_diagnostics()
            self.flow_inspector_tabs.setCurrentIndex(2)
            self._set_flow_assistant_message(
                f"Debugger blocked: fix {len(errors)} validation error(s) first.",
                "error",
            )
            return
        node = self._debug_entry_node()
        if node is None:
            self._set_flow_assistant_message(
                "Add or select a behavior node before starting the debugger.",
                "warning",
            )
            return
        if not node.operation:
            self._set_flow_assistant_message(
                f"{node.name} is structural only. Choose an Operation before debugging.",
                "warning",
            )
            self.flow_inspector_tabs.setCurrentIndex(0)
            return
        try:
            runtime = self._build_debug_runtime()
            payload = self._debug_payload(node, self._debug_ui)
            input_port = next(
                (port.id for port in node.ports if port.direction == "in"), "event"
            )
            runtime.queue(node.id, payload, input_port)
        except BehaviorRuntimeError as error:
            self._set_runtime_trace_status(str(error))
            self._set_flow_assistant_message(str(error), "error")
            return
        self._debug_runtime = runtime
        self.graph.active_trace_node_ids.clear()
        self.graph.active_trace_connection_ids.clear()
        self.runtime_trace_list.clear()
        self.runtime_payload_view.clear()
        self.flow_test_tabs.setCurrentIndex(1)
        self.runtime_selected_label.setText(f"Entry: {node.name} · {node.operation}")
        self._set_runtime_trace_status(
            f"Ready at {node.name}. Choose Step or Continue."
        )
        self._set_flow_assistant_message(
            f"Debugger ready at {node.name}. Step executes one node.", "success"
        )
        self._update_debugger_controls()

    def _step_flow_debugger(self) -> None:
        """Execute one queued node and expose its output and payload."""
        if self._debug_runtime is None:
            self._start_flow_debugger()
            return
        try:
            entry = self._debug_runtime.step_execution()
        except BehaviorRuntimeError as error:
            self._debugger_failed(error)
            return
        if entry is None:
            self._set_runtime_trace_status("Debugger complete.")
        else:
            self._set_runtime_trace_status(
                f"Executed {entry.node_id} → {entry.output_port or 'no output'}."
            )
        self._refresh_debugger_trace()

    def _continue_flow_debugger(self) -> None:
        """Run until completion or a configured node breakpoint."""
        if self._debug_runtime is None:
            self._start_flow_debugger()
            if self._debug_runtime is None:
                return
        try:
            self._debug_runtime.continue_execution()
        except BehaviorRuntimeError as error:
            self._debugger_failed(error)
            return
        status = (
            f"Paused at breakpoint; next node: {self._debug_runtime.next_node_id}."
            if self._debug_runtime.paused
            else "Debugger complete."
        )
        self._set_runtime_trace_status(status)
        self._refresh_debugger_trace()

    def _stop_flow_debugger(self) -> None:
        """Stop queued execution while retaining visible trace evidence."""
        if self._debug_runtime is not None:
            self._debug_runtime.stop()
        self._set_runtime_trace_status("Debugger stopped.")
        self._update_debugger_controls()

    def _fire_debug_timers(self) -> None:
        """Queue retained timer callbacks for subsequent Step or Continue."""
        if self._debug_runtime is None or self._debug_timer is None:
            return
        self._debug_runtime.paused = True
        count = self._debug_timer.fire_all()
        self._set_runtime_trace_status(
            f"Queued {count} timer callback(s). Choose Step or Continue."
        )
        self._update_debugger_controls()

    def _clear_flow_debugger(self) -> None:
        """Clear the current debug session and every visual trace highlight."""
        if self._debug_runtime is not None:
            self._debug_runtime.clear_trace()
        self._debug_runtime = None
        self._debug_ui = None
        self._debug_timer = None
        self.runtime_trace_list.clear()
        self.runtime_payload_view.clear()
        self.graph.active_trace_node_ids.clear()
        self.graph.active_trace_connection_ids.clear()
        self.graph.update()
        self._set_runtime_trace_status("Trace cleared.")
        self._update_debugger_controls()

    def _debugger_failed(self, error: Exception) -> None:
        """Keep a debugger failure visible and actionable without a modal dialog."""
        message = f"Debugger stopped: {error}"
        self._set_runtime_trace_status(message)
        self._set_flow_assistant_message(message, "error")
        self._refresh_debugger_trace()

    def _refresh_debugger_trace(self) -> None:
        """Synchronize trace rows, graph highlights, and control availability."""
        if self._debug_runtime is None:
            return
        self.set_runtime_trace(list(self._debug_runtime.trace))
        node_ids = {entry.node_id for entry in self._debug_runtime.trace}
        connection_ids: set[str] = set()
        for entry in self._debug_runtime.trace:
            connection_ids.update(
                connection.id
                for connection in self.session.project.behavior_connections
                if connection.source_node_id == entry.node_id
                and connection.source_port_id == entry.output_port
            )
        self.graph.active_trace_node_ids = node_ids
        self.graph.active_trace_connection_ids = connection_ids
        self.graph.update()
        self._update_debugger_controls()

    def _update_debugger_controls(self) -> None:
        """Enable only debugger actions that can currently do useful work."""
        runtime = self._debug_runtime
        has_pending = bool(runtime and runtime.pending_count)
        self.runtime_step_button.setEnabled(has_pending)
        self.runtime_continue_button.setEnabled(has_pending)
        self.runtime_stop_button.setEnabled(bool(runtime))
        self.runtime_clear_button.setEnabled(
            bool(runtime and runtime.trace) or self.runtime_trace_list.count() > 0
        )
        self.runtime_fire_timer_button.setEnabled(
            bool(self._debug_timer and self._debug_timer.callbacks)
        )

    def _runtime_trace_item_changed(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        """Reveal a trace payload and center the corresponding executed node."""
        del previous
        record = current.data(Qt.ItemDataRole.UserRole) if current else None
        if not isinstance(record, tuple) or len(record) not in {3, 4}:
            self.runtime_payload_view.clear()
            return
        node_id, output_port, payload = record[:3]
        result = record[3] if len(record) == 4 else "Unavailable in legacy trace"
        self.runtime_payload_view.setPlainText(
            f"Node: {node_id}\nOutput port: {output_port or 'none'}\n"
            f"Input payload: {payload}\nOutput payload: {result}"
        )
        node = self.session.project.flow_node(str(node_id))
        if node is not None:
            self.graph.selected_behavior_node_ids = {node.id}
            self.graph.primary_behavior_node_id = node.id
            self._center_graph_at(node.node_x, node.node_y)

    def _update_minimap_viewport(self) -> None:
        """Keep the minimap's visible-region outline synchronized."""
        viewport = self.graph_scroll.viewport().size()
        zoom = max(self.graph.zoom, 0.0001)
        self.flow_minimap.set_viewport(
            self.graph_scroll.horizontalScrollBar().value() / zoom,
            self.graph_scroll.verticalScrollBar().value() / zoom,
            viewport.width() / zoom,
            viewport.height() / zoom,
        )

    def _fit_graph_nodes(self) -> None:
        """Fit every graph node into the current viewport."""
        project = self.session.project
        screens = self.graph._visible_screens()
        nodes = self.graph._visible_behavior_nodes()
        groups = project.flow_groups if self.graph.visibility_mode != "screens" else []
        if not screens and not nodes:
            return
        left_values = (
            [screen.node_x for screen in screens]
            + [node.node_x for node in nodes]
            + [group.node_x for group in groups]
        )
        top_values = (
            [screen.node_y for screen in screens]
            + [node.node_y for node in nodes]
            + [group.node_y for group in groups]
        )
        right_values = (
            [screen.node_x + self.graph.NODE_WIDTH for screen in screens]
            + [node.node_x + self.graph.BEHAVIOR_NODE_WIDTH for node in nodes]
            + [group.node_x + group.width for group in groups]
        )
        bottom_values = (
            [screen.node_y + self.graph._node_height(screen) for screen in screens]
            + [node.node_y + self.graph._behavior_node_height(node) for node in nodes]
            + [group.node_y + group.height for group in groups]
        )
        left = min(left_values)
        top = min(top_values)
        right = max(right_values)
        bottom = max(bottom_values)
        viewport = self.graph_scroll.viewport().size()
        available_width = max(1, viewport.width() - 60)
        available_height = max(1, viewport.height() - 60)
        content_width = max(1, right - left)
        content_height = max(1, bottom - top)
        zoom = min(
            1.0,
            available_width / content_width,
            available_height / content_height,
        )
        self.graph.set_zoom(zoom, allow_fit_overview=True)
        horizontal_bar = self.graph_scroll.horizontalScrollBar()
        vertical_bar = self.graph_scroll.verticalScrollBar()
        horizontal_bar.setValue(max(0, round(left * self.graph.zoom - 30)))
        vertical_bar.setValue(max(0, round(top * self.graph.zoom - 30)))

    def _show_graph_context_menu(self, position: QPoint) -> None:
        """Show the most-used Screen Flow operations at the pointer."""
        point = self.graph._graph_point(QPointF(position))
        behavior_node = self.graph._behavior_node_at(point)
        screen = self.graph._screen_at(point)
        behavior_connection = (
            self.graph._behavior_connection_at(point)
            if behavior_node is None and screen is None
            else None
        )
        connection = (
            self.graph._connection_at(point)
            if behavior_node is None and screen is None and behavior_connection is None
            else None
        )
        group = (
            self.graph._flow_group_at(point)
            if behavior_node is None
            and screen is None
            and behavior_connection is None
            and connection is None
            else None
        )
        self._context_flow_group_id = group.id if group is not None else ""
        if behavior_node is not None:
            self.graph._select_behavior_node(behavior_node.id)
            self.graph.selected_screen_id = None
            self.graph.selected_connection_id = None
            self.graph.selected_behavior_connection_id = None
            self._behavior_node_selected(behavior_node.id)
        elif screen is not None:
            self.graph.selected_screen_id = screen.id
            self.graph.selected_connection_id = None
            self.graph.selected_behavior_node_ids.clear()
            self.graph.selected_behavior_connection_id = None
            self._graph_screen_selected(screen.id)
        elif behavior_connection is not None:
            self.graph.selected_screen_id = None
            self.graph.selected_connection_id = None
            self.graph.selected_behavior_connection_id = behavior_connection.id
            self._select_behavior_connection_id(behavior_connection.id)
        elif connection is not None:
            self.graph.selected_screen_id = None
            self.graph.selected_connection_id = connection.id
            self._select_connection_id(connection.id)
        self.graph.update()
        self._graph_context_menu().exec(self.graph.mapToGlobal(position))

    def _graph_context_menu(self) -> QMenu:
        """Build the Screen Flow graph context menu."""
        menu = QMenu(self)
        add_menu = menu.addMenu("Add operation")
        category_menus: dict[str, QMenu] = {}
        for operation in OPERATIONS:
            category_menu = category_menus.get(operation.category)
            if category_menu is None:
                category_menu = add_menu.addMenu(operation.category)
                category_menus[operation.category] = category_menu
            action = category_menu.addAction(operation.label)
            action.triggered.connect(
                lambda checked=False,
                operation_id=operation.id: self._add_behavior_operation(operation_id)
            )
        structural_menu = add_menu.addMenu("Structural")
        for kind in ("component", "comment"):
            action = structural_menu.addAction(kind.title())
            action.triggered.connect(
                lambda checked=False, node_kind=kind: self._add_behavior_kind(node_kind)
            )
        add_behavior_action = menu.addAction("Add behavior node")
        add_behavior_action.setToolTip(
            "Add the operation currently selected in the graph toolbar."
        )
        add_behavior_action.triggered.connect(self._add_behavior_node)
        menu.addSeparator()
        duplicate_node_action = menu.addAction("Duplicate selected behavior nodes")
        group_action = menu.addAction("Group selected behavior nodes")
        context_group = self.session.project.flow_group(self._context_flow_group_id)
        toggle_group_action = menu.addAction(
            "Expand group"
            if context_group is not None and context_group.collapsed
            else "Collapse group"
        )
        trace_action = menu.addAction("Debug selected behavior")
        delete_node_action = menu.addAction("Delete selected behavior nodes")
        delete_behavior_edge_action = menu.addAction(
            "Delete selected behavior connection"
        )
        insert_action = menu.addAction("Insert Action into behavior connection")
        align_action = menu.addAction("Align selected behavior nodes left")
        distribute_action = menu.addAction(
            "Distribute selected behavior nodes vertically"
        )
        layout_selected_horizontal = menu.addAction("Layout selected →")
        layout_selected_vertical = menu.addAction("Layout selected ↓")
        menu.addSeparator()
        open_action = menu.addAction("Open selected screen")
        start_action = menu.addAction("Set selected screen as start")
        delete_action = menu.addAction("Delete selected relation")
        menu.addSeparator()
        menu.addAction("Fit all nodes", self._fit_graph_nodes)
        menu.addAction("Auto-layout graph", self._auto_layout_nodes)
        menu.addAction("Reset flow test", self._reset_simulator)
        menu.addSeparator()
        menu.addAction("Run current design", self.run_simulator_requested.emit)
        menu.addAction("Open Device Simulator", self.open_simulator_requested.emit)
        selected_screen = bool(self.graph.selected_screen_id)
        selected_nodes = bool(self.graph.selected_behavior_node_ids)
        duplicate_node_action.setEnabled(selected_nodes)
        group_action.setEnabled(selected_nodes)
        toggle_group_action.setEnabled(context_group is not None)
        trace_action.setEnabled(selected_nodes)
        delete_node_action.setEnabled(selected_nodes)
        delete_behavior_edge_action.setEnabled(
            bool(self.graph.selected_behavior_connection_id)
        )
        selected_behavior_connection = self.session.project.behavior_connection(
            self.graph.selected_behavior_connection_id or ""
        )
        insert_action.setEnabled(
            self._can_insert_action_into_connection(selected_behavior_connection)
        )
        align_action.setEnabled(len(self.graph.selected_behavior_node_ids) >= 2)
        distribute_action.setEnabled(len(self.graph.selected_behavior_node_ids) >= 3)
        layout_selected_horizontal.setEnabled(
            len(self.graph.selected_behavior_node_ids) >= 2
        )
        layout_selected_vertical.setEnabled(
            len(self.graph.selected_behavior_node_ids) >= 2
        )
        open_action.setEnabled(selected_screen)
        start_action.setEnabled(selected_screen)
        delete_action.setEnabled(bool(self.graph.selected_connection_id))
        duplicate_node_action.triggered.connect(self._duplicate_selected_behavior_nodes)
        group_action.triggered.connect(self._group_selected_behavior_nodes)
        toggle_group_action.triggered.connect(self._toggle_context_behavior_group)
        trace_action.triggered.connect(self._start_flow_debugger)
        delete_node_action.triggered.connect(
            lambda: self._delete_behavior_nodes(
                set(self.graph.selected_behavior_node_ids)
            )
        )
        delete_behavior_edge_action.triggered.connect(
            lambda: self._delete_behavior_connection_id(
                self.graph.selected_behavior_connection_id or ""
            )
        )
        insert_action.triggered.connect(self._insert_action_into_behavior_connection)
        align_action.triggered.connect(self._align_selected_behavior_nodes_left)
        distribute_action.triggered.connect(
            self._distribute_selected_behavior_nodes_vertically
        )
        layout_selected_horizontal.triggered.connect(
            lambda: self._layout_selected_behavior_nodes(True)
        )
        layout_selected_vertical.triggered.connect(
            lambda: self._layout_selected_behavior_nodes(False)
        )
        open_action.triggered.connect(self._open_selected_screen)
        start_action.triggered.connect(self._set_start_screen)
        delete_action.triggered.connect(
            lambda: self._delete_connection_id(self.graph.selected_connection_id or "")
        )
        return menu

    def _can_insert_action_into_connection(
        self, connection: BehaviorConnection | None
    ) -> bool:
        """Return whether an event edge can accept an intermediate Action node."""
        if connection is None or connection.locked:
            return False
        source = self.session.project.flow_node(connection.source_node_id)
        target = self.session.project.flow_node(connection.target_node_id)
        source_port = source.port(connection.source_port_id) if source else None
        target_port = target.port(connection.target_port_id) if target else None
        return bool(
            source_port
            and target_port
            and source_port.data_type == "event"
            and target_port.data_type == "event"
        )

    def _add_behavior_kind(self, kind: str) -> None:
        """Add one advanced structural node kind from the context menu."""
        self._add_behavior_at_visible_center(kind, "")

    def _add_behavior_operation(self, operation_id: str) -> None:
        """Add one allowlisted operation from a context or toolbar chooser."""
        operation = operation_spec(operation_id)
        if operation is not None:
            self._add_behavior_at_visible_center(operation.kind, operation.id)

    def _add_behavior_at_visible_center(
        self, kind: str, operation_id: str = ""
    ) -> FlowNode:
        """Create one node at the visible graph center with optional operation."""
        horizontal = self.graph_scroll.horizontalScrollBar().value()
        vertical = self.graph_scroll.verticalScrollBar().value()
        viewport = self.graph_scroll.viewport().size()
        x = round((horizontal + viewport.width() / 2) / self.graph.zoom)
        y = round((vertical + viewport.height() / 2) / self.graph.zoom)
        node = FlowNode.create(
            kind,
            len(self.session.project.behavior_nodes) + 1,
            max(20, x - self.graph.BEHAVIOR_NODE_WIDTH // 2),
            max(20, y - 45),
        )
        if operation_id:
            operation = operation_spec(operation_id)
            if operation is not None:
                node.name = operation.label
                node.set_operation(operation.id)
                if operation.id == "custom.handler":
                    node.properties["handler"] = flow_stub_name(node)
        self.session.project.behavior_nodes.append(node)
        self.session.project.flow_standard_version = 2
        self.graph.selected_behavior_node_ids = {node.id}
        self.graph.primary_behavior_node_id = node.id
        self.graph.selected_screen_id = None
        self.session.mark_changed()
        return node

    def _choose_compatible_operation(
        self,
        source_data_type: str,
        x: int,
        y: int,
        *,
        title: str = "Add compatible operation",
        allowed_kinds: set[str] | None = None,
    ) -> tuple[str, str] | None:
        """Return one user-selected operation and compatible input port."""
        compatible = []
        for operation in OPERATIONS:
            if allowed_kinds is not None and operation.kind not in allowed_kinds:
                continue
            target = next(
                (
                    port
                    for port in operation.ports
                    if port.direction == "in"
                    and (
                        port.data_type == source_data_type
                        or "any" in {port.data_type, source_data_type}
                    )
                ),
                None,
            )
            if target is not None:
                compatible.append((operation, target))
        if not compatible:
            return None
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        layout = QVBoxLayout(dialog)
        search = QLineEdit()
        search.setPlaceholderText("Search actions")
        choices = QListWidget()
        for operation, target in compatible:
            item = QListWidgetItem(f"{operation.category} · {operation.label}")
            item.setData(Qt.ItemDataRole.UserRole, (operation.id, target.id))
            choices.addItem(item)
        choices.setCurrentRow(0)
        search.textChanged.connect(
            lambda text: [
                choices.item(row).setHidden(
                    text.strip().casefold() not in choices.item(row).text().casefold()
                )
                for row in range(choices.count())
            ]
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        choices.itemDoubleClicked.connect(lambda unused_item: dialog.accept())
        layout.addWidget(search)
        layout.addWidget(choices)
        layout.addWidget(buttons)
        graph_position = QPoint(round(x * self.graph.zoom), round(y * self.graph.zoom))
        dialog.move(self.graph.mapToGlobal(graph_position))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        item = choices.currentItem()
        selected = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(selected, tuple) or len(selected) != 2:
            return None
        return str(selected[0]), str(selected[1])

    def _open_compatible_operation_palette(
        self, source_node_id: str, source_port_id: str, x: int, y: int
    ) -> None:
        """Offer only operations that accept the dropped typed connection."""
        source = self.session.project.flow_node(source_node_id)
        source_port = source.port(source_port_id) if source else None
        if source_port is None:
            return
        selected = self._choose_compatible_operation(source_port.data_type, x, y)
        if selected is None:
            return
        operation = operation_spec(str(selected[0]))
        if operation is None:
            return
        target = next(
            (port for port in operation.ports if port.id == str(selected[1])), None
        )
        if target is None:
            return
        node = FlowNode.create(
            operation.kind,
            len(self.session.project.behavior_nodes) + 1,
            max(20, x),
            max(20, y),
        )
        node.name = operation.label
        node.set_operation(operation.id)
        if operation.id == "custom.handler":
            node.properties["handler"] = flow_stub_name(node)
        self.session.project.behavior_nodes.append(node)
        self.session.project.behavior_connections.append(
            BehaviorConnection.create(
                source_node_id, source_port_id, node.id, target.id
            )
        )
        self.graph.selected_behavior_node_ids = {node.id}
        self.graph.primary_behavior_node_id = node.id
        self.session.mark_changed()

    def _open_element_action_palette(
        self, screen_id: str, element_id: str, x: int, y: int
    ) -> None:
        """Create a bound UI Event and chosen action after an empty-space drop."""
        element = self.session.project.element(screen_id, element_id)
        if element is None:
            return
        selected = self._choose_compatible_operation(
            "event",
            x,
            y,
            title=f"Choose action for {element.name}",
            allowed_kinds=set(FLOW_NODE_KINDS) - {"event", "comment"},
        )
        if selected is None:
            self._set_flow_assistant_message(
                f"No action added for {element.name}.", "info"
            )
            return
        operation_id = selected[0]
        target_screen_id = ""
        if operation_id == "navigation.navigate":
            targets = [
                screen
                for screen in self.session.project.screens
                if screen.id != screen_id
            ]
            if not targets:
                self._set_flow_assistant_message(
                    "Add another screen before choosing Navigate to screen.",
                    "warning",
                )
                return
            names = [screen.name for screen in targets]
            name, accepted = QInputDialog.getItem(
                self,
                "Navigate to screen",
                "Target screen",
                names,
                0,
                False,
            )
            if not accepted:
                return
            target_screen_id = targets[names.index(name)].id
        if self.create_behavior_from_element(
            screen_id,
            element_id,
            operation_id,
            target_screen_id,
            action_position=(x, y),
        ):
            operation = operation_spec(operation_id)
            label = operation.label if operation is not None else "action"
            self._set_flow_assistant_message(
                f"Added {label} for {element.name}. Configure its properties in the Node inspector.",
                "success",
            )

    def _layout_selected_behavior_nodes(self, horizontal: bool) -> None:
        """Lay out selected unlocked nodes while preserving pinned positions."""
        nodes = [
            node
            for node in self.session.project.behavior_nodes
            if node.id in self.graph.selected_behavior_node_ids
            and not node.locked
            and not node.pinned
        ]
        if len(nodes) < 2:
            return
        nodes.sort(key=lambda node: (node.node_x, node.node_y))
        left = min(node.node_x for node in nodes)
        top = min(node.node_y for node in nodes)
        for index, node in enumerate(nodes):
            if horizontal:
                node.node_x = left + index * (self.graph.BEHAVIOR_NODE_WIDTH + 70)
                node.node_y = top
            else:
                node.node_x = left
                node.node_y = top + index * 140
        self.session.mark_changed()

    def _insert_action_into_behavior_connection(self) -> None:
        """Replace one typed event edge with an intermediate Action contract."""
        connection = self.session.project.behavior_connection(
            self.graph.selected_behavior_connection_id or ""
        )
        if not self._can_insert_action_into_connection(connection):
            return
        assert connection is not None
        source = self.session.project.flow_node(connection.source_node_id)
        target = self.session.project.flow_node(connection.target_node_id)
        if source is None or target is None:
            return
        node = FlowNode.create(
            "action",
            len(self.session.project.behavior_nodes) + 1,
            round((source.node_x + target.node_x) / 2),
            round((source.node_y + target.node_y) / 2),
        )
        first = BehaviorConnection.create(
            source.id, connection.source_port_id, node.id, "in"
        )
        second = BehaviorConnection.create(
            node.id,
            "done",
            target.id,
            connection.target_port_id,
            connection.label,
        )
        second.condition = connection.condition
        self.session.project.behavior_connections = [
            item
            for item in self.session.project.behavior_connections
            if item.id != connection.id
        ]
        self.session.project.behavior_nodes.append(node)
        self.session.project.behavior_connections.extend((first, second))
        self.graph.selected_behavior_connection_id = None
        self.graph.selected_behavior_node_ids = {node.id}
        self.graph.primary_behavior_node_id = node.id
        self.session.mark_changed()

    def _align_selected_behavior_nodes_left(self) -> None:
        """Align editable selected behavior nodes to their leftmost position."""
        nodes = [
            node
            for node in self.session.project.behavior_nodes
            if node.id in self.graph.selected_behavior_node_ids and not node.locked
        ]
        if len(nodes) < 2:
            return
        left = min(node.node_x for node in nodes)
        for node in nodes:
            node.node_x = left
        self.session.mark_changed()

    def _distribute_selected_behavior_nodes_vertically(self) -> None:
        """Evenly distribute at least three selected behavior nodes vertically."""
        nodes = sorted(
            (
                node
                for node in self.session.project.behavior_nodes
                if node.id in self.graph.selected_behavior_node_ids and not node.locked
            ),
            key=lambda node: node.node_y,
        )
        if len(nodes) < 3:
            return
        span = nodes[-1].node_y - nodes[0].node_y
        for index, node in enumerate(nodes[1:-1], 1):
            node.node_y = round(nodes[0].node_y + span * index / (len(nodes) - 1))
        self.session.mark_changed()

    def _show_relation_context_menu(self, position: QPoint) -> None:
        """Show common relation-list operations at the pointer."""
        item = self.connection_list.itemAt(position)
        if item is not None:
            self.connection_list.setCurrentItem(item)
        self._relation_context_menu().exec(
            self.connection_list.viewport().mapToGlobal(position)
        )

    def _relation_context_menu(self) -> QMenu:
        """Build the selected relation context menu."""
        menu = QMenu(self)
        update_action = menu.addAction("Update selected relation")
        delete_action = menu.addAction("Delete selected relation")
        connection = self._selected_connection()
        update_action.setEnabled(bool(connection and not connection.locked))
        delete_action.setEnabled(bool(connection and not connection.source_path))
        update_action.triggered.connect(self._update_relation)
        delete_action.triggered.connect(self._delete_relation)
        return menu

    def _selected_behavior_node(self) -> FlowNode | None:
        """Return the primary selected behavior node."""
        return self.session.project.flow_node(self.graph.primary_behavior_node_id or "")

    def _add_behavior_node(self) -> None:
        """Add the selected executable operation or advanced structural node."""
        selected = str(self.add_behavior_operation_combo.currentData() or "")
        if selected.startswith("structural:"):
            self._add_behavior_at_visible_center(selected.split(":", 1)[1], "")
            return
        operation = operation_spec(selected)
        if operation is None:
            return
        self._add_behavior_at_visible_center(operation.kind, operation.id)

    def _behavior_node_selected(self, node_id: str) -> None:
        """Refresh the inspector for a directly selected behavior node."""
        self.connection_list.clearSelection()
        self.graph.selected_connection_id = None
        self._refresh_behavior_inspector()
        node = self.session.project.flow_node(node_id)
        self.runtime_selected_label.setText(
            f"Entry: {node.name} · {node.operation or 'structural only'}"
            if node is not None
            else "Entry: select a behavior node"
        )

    def _behavior_kind_choice_changed(self) -> None:
        """Offer only operations compatible with the currently chosen node kind."""
        if self._updating:
            return
        self._refresh_behavior_operation_choices(
            str(self.behavior_operation_combo.currentData() or "")
        )

    def _refresh_behavior_operation_choices(self, selected: str = "") -> None:
        """Populate operation choices from the shared allowlist."""
        kind = str(self.behavior_kind_combo.currentData() or "action")
        self.behavior_operation_combo.blockSignals(True)
        self.behavior_operation_combo.clear()
        self.behavior_operation_combo.addItem("Structural only", "")
        for operation in operations_for_kind(kind):
            self.behavior_operation_combo.addItem(
                f"{operation.category} · {operation.label}", operation.id
            )
        self._restore_combo(self.behavior_operation_combo, selected)
        self.behavior_operation_combo.blockSignals(False)
        self._rebuild_behavior_operation_form()

    def _behavior_operation_choice_changed(self) -> None:
        """Rebuild schema-driven operation controls after a deliberate choice."""
        if not self._updating:
            self._rebuild_behavior_operation_form()

    def _rebuild_behavior_operation_form(
        self, values: dict[str, object] | None = None
    ) -> None:
        """Render operation properties without requiring raw JSON editing."""
        while self.behavior_operation_form.rowCount():
            self.behavior_operation_form.removeRow(0)
        self.behavior_operation_fields.clear()
        operation = operation_spec(
            str(self.behavior_operation_combo.currentData() or "")
        )
        self.behavior_operation_group.setVisible(
            operation is not None and bool(operation.properties)
        )
        if operation is None:
            return
        current = values or {}
        for property_spec in operation.properties:
            value = current.get(property_spec.id, property_spec.default)
            if property_spec.value_type == "boolean":
                editor: QWidget = QCheckBox()
                editor.setChecked(bool(value))
            elif property_spec.value_type == "integer":
                editor = QSpinBox()
                editor.setRange(-2_147_483_648, 2_147_483_647)
                editor.setValue(int(value or 0))
            elif property_spec.value_type == "choice":
                editor = QComboBox()
                for choice in property_spec.choices:
                    editor.addItem(choice.replace("_", " ").title(), choice)
                self._restore_combo(editor, value)
            elif property_spec.value_type == "screen":
                editor = QComboBox()
                for screen in self.session.project.screens:
                    editor.addItem(screen.name, screen.id)
                self._restore_combo(editor, value)
            elif property_spec.value_type == "element":
                editor = QComboBox()
                for screen in self.session.project.screens:
                    for element in screen.elements:
                        if _element_supports_operation(element, operation.id):
                            editor.addItem(
                                f"{screen.name} · {element.name}", element.id
                            )
                if value and editor.findData(value) < 0:
                    editor.addItem(f"Unsupported current target · {value}", value)
                    unsupported = editor.model().item(editor.count() - 1)
                    if unsupported is not None:
                        unsupported.setEnabled(False)
                self._restore_combo(editor, value)
            else:
                editor = QLineEdit(str(value or ""))
                if property_spec.value_type in {"state-key", "settings-key"}:
                    editor.setPlaceholderText(
                        property_spec.value_type.replace("-", " ").title()
                    )
            help_text = property_spec.help_text or (
                f"Sets {property_spec.label.lower()} for the "
                f"{operation.label} operation."
            )
            set_widget_tooltip(
                editor,
                f"behavior_{operation.id}_{property_spec.id}",
                self,
                help_text,
            )
            self.behavior_operation_fields[property_spec.id] = editor
            self.behavior_operation_form.addRow(property_spec.label, editor)

    def _behavior_form_properties(self) -> dict[str, object]:
        """Read validated primitive values from schema-driven controls."""
        values: dict[str, object] = {}
        for identifier, editor in self.behavior_operation_fields.items():
            if isinstance(editor, QCheckBox):
                values[identifier] = editor.isChecked()
            elif isinstance(editor, QSpinBox):
                values[identifier] = editor.value()
            elif isinstance(editor, QComboBox):
                values[identifier] = editor.currentData()
            elif isinstance(editor, QLineEdit):
                values[identifier] = editor.text().strip()
        return values

    def _refresh_behavior_inspector(self) -> None:
        """Load selected behavior-node values and operation availability."""
        node = self._selected_behavior_node()
        selected = bool(self.graph.selected_behavior_node_ids)
        editable = bool(node and not node.locked)
        self.selected_behavior_label.setText(
            f"{len(self.graph.selected_behavior_node_ids)} node(s) selected"
            if selected
            else "No behavior node selected"
        )
        for widget in (
            self.behavior_kind_combo,
            self.behavior_name_edit,
            self.behavior_description_edit,
            self.behavior_properties_edit,
            self.behavior_pinned_check,
            self.behavior_locked_check,
            self.behavior_breakpoint_check,
            self.apply_behavior_button,
        ):
            widget.setEnabled(
                bool(node) and (editable or widget is self.behavior_locked_check)
            )
        self.duplicate_behavior_button.setEnabled(selected)
        self.delete_behavior_button.setEnabled(selected)
        self.group_behavior_button.setEnabled(selected)
        self.ungroup_behavior_button.setEnabled(
            bool(
                node
                and any(
                    item.group_id
                    for item in self.session.project.behavior_nodes
                    if item.id in self.graph.selected_behavior_node_ids
                )
            )
        )
        group = self.session.project.flow_group(node.group_id) if node else None
        self.collapse_behavior_group_button.setEnabled(group is not None)
        self.collapse_behavior_group_button.setText(
            "Expand group"
            if group is not None and group.collapsed
            else "Collapse group"
        )
        if node is None:
            self.behavior_operation_group.hide()
            self.behavior_id_label.setText("—")
            self.behavior_name_edit.clear()
            self.behavior_description_edit.clear()
            self.behavior_properties_edit.clear()
            self.behavior_stub_label.setText("Handler: —")
            self.behavior_ports_label.setText("Ports: —")
            return
        self.behavior_id_label.setText(node.id)
        self._restore_combo(self.behavior_kind_combo, node.kind)
        self._refresh_behavior_operation_choices(node.operation)
        self._rebuild_behavior_operation_form(node.properties)
        self.behavior_name_edit.setText(node.name)
        self.behavior_description_edit.setPlainText(node.description)
        self.behavior_properties_edit.setPlainText(
            json.dumps(node.properties, indent=2, sort_keys=True)
        )
        self.behavior_pinned_check.setChecked(node.pinned)
        self.behavior_locked_check.setChecked(node.locked)
        self.behavior_breakpoint_check.setChecked(node.breakpoint)
        self.behavior_ports_label.setText(
            "Ports: "
            + ", ".join(
                f"{port.direction} {port.name}:{port.data_type}" for port in node.ports
            )
        )
        operation = operation_spec(node.operation)
        if operation is None:
            status = "Structural only"
        elif operation.capability == "custom-handler":
            status = self._custom_handler_status(node)
        else:
            status = "Built in"
        handler = str(node.properties.get("handler") or flow_stub_name(node))
        self.behavior_stub_label.setText(f"Handler: {status} · {handler}")

    def _custom_handler_status(self, node: FlowNode) -> str:
        """Report whether the last configured export contains a handler function."""
        destination = str(
            self.session.project.generated_app.get("destination", "") or ""
        )
        if not destination:
            return "Missing"
        path = resolve_generated_app_paths(
            self.session.project.name, destination
        ).behavior_handlers
        if not path.is_file():
            return "Missing"
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            return "Invalid"
        handler = str(node.properties.get("handler") or flow_stub_name(node))
        function = next(
            (
                item
                for item in tree.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name == handler
            ),
            None,
        )
        if function is None:
            return "Missing"
        if any(
            isinstance(item, ast.Raise)
            and isinstance(item.exc, ast.Call)
            and isinstance(item.exc.func, ast.Name)
            and item.exc.func.id == "NotImplementedError"
            for item in function.body
        ):
            return "Missing"
        return "Implemented"

    def _apply_behavior_node(self) -> None:
        """Apply edited structural contract values to the selected node."""
        node = self._selected_behavior_node()
        if node is None:
            return
        name = self.behavior_name_edit.text().strip()
        if not name:
            QMessageBox.information(self, "Node name required", "Enter a node name.")
            return
        try:
            properties = json.loads(
                self.behavior_properties_edit.toPlainText().strip() or "{}"
            )
        except json.JSONDecodeError as error:
            QMessageBox.information(self, "Invalid properties JSON", str(error))
            return
        if not isinstance(properties, dict):
            QMessageBox.information(
                self,
                "Invalid properties JSON",
                "Node properties must be a JSON object.",
            )
            return
        kind = str(self.behavior_kind_combo.currentData() or node.kind)
        if kind != node.kind:
            preview = preview_flow_node_kind_change(self.session.project, node, kind)
            if preview.locked_connection_ids:
                QMessageBox.information(
                    self,
                    "Locked connections prevent this change",
                    "Unlock these behavior connections first:\n"
                    + "\n".join(preview.locked_connection_ids),
                )
                return
            if preview.removed_connection_ids:
                answer = QMessageBox.question(
                    self,
                    "Change node kind and remove connections?",
                    "The new port contract cannot preserve these connections:\n"
                    + "\n".join(preview.removed_connection_ids)
                    + "\n\nContinue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            updates = {
                connection_id: (source_port_id, target_port_id)
                for connection_id, source_port_id, target_port_id in preview.endpoint_updates
            }
            removed = set(preview.removed_connection_ids)
            node.kind = preview.kind
            node.ports = list(preview.ports)
            retained = []
            for connection in self.session.project.behavior_connections:
                if connection.id in removed:
                    continue
                endpoints = updates.get(connection.id)
                if endpoints is not None:
                    connection.source_port_id, connection.target_port_id = endpoints
                retained.append(connection)
            self.session.project.behavior_connections = retained
        operation_id = str(self.behavior_operation_combo.currentData() or "")
        if operation_id:
            operation = operation_spec(operation_id)
            if operation is None or operation.kind != node.kind:
                QMessageBox.information(
                    self, "Invalid operation", "Choose an operation for this node kind."
                )
                return
            operation_ports = [
                FlowPort(
                    port.id,
                    port.label,
                    port.direction,
                    port.data_type,
                    port.required,
                    port.multiple,
                )
                for port in operation.ports
            ]
            if operation_id != node.operation:
                port_preview = preview_flow_node_port_change(
                    self.session.project, node, node.kind, operation_ports
                )
                if port_preview.locked_connection_ids:
                    QMessageBox.information(
                        self,
                        "Locked connections prevent this operation",
                        "Unlock these behavior connections first:\n"
                        + "\n".join(port_preview.locked_connection_ids),
                    )
                    return
                if port_preview.removed_connection_ids:
                    answer = QMessageBox.question(
                        self,
                        "Change operation and remove connections?",
                        "The operation cannot preserve these connections:\n"
                        + "\n".join(port_preview.removed_connection_ids)
                        + "\n\nContinue?",
                        QMessageBox.StandardButton.Yes
                        | QMessageBox.StandardButton.Cancel,
                        QMessageBox.StandardButton.Cancel,
                    )
                    if answer != QMessageBox.StandardButton.Yes:
                        return
                updates = {
                    connection_id: (source_port_id, target_port_id)
                    for connection_id, source_port_id, target_port_id in port_preview.endpoint_updates
                }
                removed = set(port_preview.removed_connection_ids)
                retained = []
                for connection in self.session.project.behavior_connections:
                    if connection.id in removed:
                        continue
                    if connection.id in updates:
                        connection.source_port_id, connection.target_port_id = updates[
                            connection.id
                        ]
                    retained.append(connection)
                self.session.project.behavior_connections = retained
                node.operation = operation_id
                node.ports = operation_ports
            properties.update(self._behavior_form_properties())
        else:
            node.operation = ""
        node.name = name
        node.description = self.behavior_description_edit.toPlainText().strip()
        node.properties = properties
        node.pinned = self.behavior_pinned_check.isChecked()
        node.locked = self.behavior_locked_check.isChecked()
        node.breakpoint = self.behavior_breakpoint_check.isChecked()
        self.session.mark_changed()

    def _duplicate_selected_behavior_nodes(self) -> None:
        """Duplicate selected behavior nodes and their internal typed edges."""
        self.graph._copy_selected_behavior_nodes()
        self.graph._paste_behavior_nodes()

    def _delete_behavior_nodes(self, node_ids: object) -> None:
        """Delete editable behavior nodes and their internal or external edges."""
        selected = set(node_ids) if isinstance(node_ids, (set, list, tuple)) else set()
        locked = {
            node.id
            for node in self.session.project.behavior_nodes
            if node.id in selected and node.locked
        }
        removable = selected - locked
        if not removable:
            return
        self.session.project.behavior_nodes = [
            node
            for node in self.session.project.behavior_nodes
            if node.id not in removable
        ]
        self.session.project.behavior_connections = [
            connection
            for connection in self.session.project.behavior_connections
            if connection.source_node_id not in removable
            and connection.target_node_id not in removable
        ]
        used_groups = {node.group_id for node in self.session.project.behavior_nodes}
        self.session.project.flow_groups = [
            group
            for group in self.session.project.flow_groups
            if group.id in used_groups
        ]
        self.graph.selected_behavior_node_ids -= removable
        self.graph.primary_behavior_node_id = next(
            iter(self.graph.selected_behavior_node_ids), None
        )
        self.session.mark_changed()

    def _group_selected_behavior_nodes(self) -> None:
        """Place selected behavior nodes in one named visual group."""
        nodes = [
            node
            for node in self.session.project.behavior_nodes
            if node.id in self.graph.selected_behavior_node_ids
        ]
        if not nodes:
            return
        name, accepted = QInputDialog.getText(
            self, "Group behavior nodes", "Group name", text="Flow section"
        )
        if not accepted or not name.strip():
            return
        group_id = new_identifier("group")
        left = min(node.node_x for node in nodes) - 30
        top = min(node.node_y for node in nodes) - 36
        right = max(node.node_x + self.graph.BEHAVIOR_NODE_WIDTH for node in nodes) + 30
        bottom = (
            max(node.node_y + self.graph._behavior_node_height(node) for node in nodes)
            + 30
        )
        group = FlowGroup(group_id, name.strip(), left, top, right - left, bottom - top)
        self.session.project.flow_groups.append(group)
        for node in nodes:
            node.group_id = group_id
        self.session.mark_changed()

    def _ungroup_selected_behavior_nodes(self) -> None:
        """Detach selected behavior nodes from their visual groups."""
        for node in self.session.project.behavior_nodes:
            if node.id in self.graph.selected_behavior_node_ids:
                node.group_id = ""
        used = {
            node.group_id
            for node in self.session.project.behavior_nodes
            if node.group_id
        }
        self.session.project.flow_groups = [
            group for group in self.session.project.flow_groups if group.id in used
        ]
        self.session.mark_changed()

    def _toggle_selected_behavior_group(self) -> None:
        """Collapse or expand the selected node's visual group."""
        node = self._selected_behavior_node()
        group = self.session.project.flow_group(node.group_id) if node else None
        if group is None:
            return
        group.collapsed = not group.collapsed
        self.session.mark_changed()

    def _toggle_context_behavior_group(self) -> None:
        """Collapse or expand the group directly under the context pointer."""
        group = self.session.project.flow_group(self._context_flow_group_id)
        if group is None:
            return
        group.collapsed = not group.collapsed
        self.session.mark_changed()

    def _create_behavior_connection(
        self,
        source_node_id: str,
        source_port_id: str,
        target_node_id: str,
        target_port_id: str,
        label: str = "",
        condition: str = "",
    ) -> None:
        """Create one validated typed behavior connection."""
        connection = BehaviorConnection.create(
            source_node_id,
            source_port_id,
            target_node_id,
            target_port_id,
            label,
        )
        connection.condition = condition
        self.session.project.behavior_connections.append(connection)
        error = behavior_connection_error(self.session.project, connection)
        if error:
            self.session.project.behavior_connections.remove(connection)
            self._set_flow_assistant_message(error.capitalize() + ".", "error")
            return
        self.graph.selected_behavior_connection_id = connection.id
        self.graph.selected_behavior_node_ids.clear()
        self.graph.primary_behavior_node_id = None
        self.session.mark_changed()

    def _refresh_behavior_source_ports(self) -> None:
        """Offer output ports from the selected manual source node."""
        selected = self.behavior_source_port_combo.currentData()
        node = self.session.project.flow_node(
            str(self.behavior_source_node_combo.currentData() or "")
        )
        self.behavior_source_port_combo.blockSignals(True)
        self.behavior_source_port_combo.clear()
        if node is not None:
            for port in node.ports:
                if port.direction == "out":
                    self.behavior_source_port_combo.addItem(
                        f"{port.name} · {port.data_type}", port.id
                    )
        self._restore_combo(self.behavior_source_port_combo, selected)
        self.behavior_source_port_combo.blockSignals(False)
        self._refresh_behavior_target_nodes()

    def _refresh_behavior_target_nodes(self) -> None:
        """Offer only nodes with a compatible input for the selected output."""
        selected = self.behavior_target_node_combo.currentData()
        source = self.session.project.flow_node(
            str(self.behavior_source_node_combo.currentData() or "")
        )
        source_port = (
            source.port(str(self.behavior_source_port_combo.currentData() or ""))
            if source is not None
            else None
        )
        self.behavior_target_node_combo.blockSignals(True)
        self.behavior_target_node_combo.clear()
        if source_port is not None:
            candidates = []
            for node in self.session.project.behavior_nodes:
                compatible = any(
                    port.direction == "in"
                    and (
                        port.data_type == source_port.data_type
                        or "any" in {port.data_type, source_port.data_type}
                    )
                    for port in node.ports
                )
                if compatible and node.id != source.id:
                    candidates.append(node)
            if not candidates and source is not None:
                candidates = [
                    source
                    for port in source.ports
                    if port.direction == "in"
                    and (
                        port.data_type == source_port.data_type
                        or "any" in {port.data_type, source_port.data_type}
                    )
                ][:1]
            for node in candidates:
                self.behavior_target_node_combo.addItem(
                    f"{node.kind.title()} · {node.name}", node.id
                )
        self._restore_combo(self.behavior_target_node_combo, selected)
        self.behavior_target_node_combo.blockSignals(False)
        self._refresh_behavior_target_ports()

    def _refresh_behavior_target_ports(self) -> None:
        """Offer input ports from the selected manual target node."""
        selected = self.behavior_target_port_combo.currentData()
        source = self.session.project.flow_node(
            str(self.behavior_source_node_combo.currentData() or "")
        )
        source_port = (
            source.port(str(self.behavior_source_port_combo.currentData() or ""))
            if source is not None
            else None
        )
        node = self.session.project.flow_node(
            str(self.behavior_target_node_combo.currentData() or "")
        )
        self.behavior_target_port_combo.blockSignals(True)
        self.behavior_target_port_combo.clear()
        if node is not None and source_port is not None:
            for port in node.ports:
                if port.direction == "in" and (
                    port.data_type == source_port.data_type
                    or "any" in {port.data_type, source_port.data_type}
                ):
                    self.behavior_target_port_combo.addItem(
                        f"{port.name} · {port.data_type}", port.id
                    )
        self._restore_combo(self.behavior_target_port_combo, selected)
        self.behavior_target_port_combo.blockSignals(False)
        self._update_behavior_connection_controls()

    def _update_behavior_connection_controls(self) -> None:
        """Enable manual connection creation only for a complete valid edge."""
        values = (
            str(self.behavior_source_node_combo.currentData() or ""),
            str(self.behavior_source_port_combo.currentData() or ""),
            str(self.behavior_target_node_combo.currentData() or ""),
            str(self.behavior_target_port_combo.currentData() or ""),
        )
        self.add_behavior_connection_button.setEnabled(all(values))

    def _add_behavior_connection_from_inspector(self) -> None:
        """Create a typed edge from explicit inspector endpoint choices."""
        self._create_behavior_connection(
            str(self.behavior_source_node_combo.currentData() or ""),
            str(self.behavior_source_port_combo.currentData() or ""),
            str(self.behavior_target_node_combo.currentData() or ""),
            str(self.behavior_target_port_combo.currentData() or ""),
            self.behavior_connection_label_edit.text().strip(),
            "",
        )

    def _clear_selected_behavior_condition(self) -> None:
        """Remove a non-executable legacy edge condition without touching its edge."""
        connection = self.session.project.behavior_connection(
            self.graph.selected_behavior_connection_id or ""
        )
        if connection is None or not connection.condition:
            return
        connection.condition = ""
        self.behavior_connection_condition_edit.clear()
        self.legacy_behavior_condition_group.hide()
        self.session.mark_changed()

    def _select_behavior_connection_id(self, connection_id: str) -> None:
        """Select one typed edge in both graph and inspector list."""
        connection = self.session.project.behavior_connection(connection_id)
        if connection is None:
            return
        for row in range(self.behavior_connection_list.count()):
            item = self.behavior_connection_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == connection_id:
                self.behavior_connection_list.setCurrentRow(row)
                break
        self.graph.selected_behavior_connection_id = connection_id
        self.graph.selected_connection_id = None
        self.graph.selected_behavior_node_ids.clear()
        self.graph.primary_behavior_node_id = None
        self._restore_combo(self.behavior_source_node_combo, connection.source_node_id)
        self._refresh_behavior_source_ports()
        self._restore_combo(self.behavior_source_port_combo, connection.source_port_id)
        self._restore_combo(self.behavior_target_node_combo, connection.target_node_id)
        self._refresh_behavior_target_ports()
        self._restore_combo(self.behavior_target_port_combo, connection.target_port_id)
        self.behavior_connection_label_edit.setText(connection.label)
        self.behavior_connection_condition_edit.setText(connection.condition)
        self.legacy_behavior_condition_group.setVisible(bool(connection.condition))
        self.update_behavior_connection_button.setEnabled(not connection.locked)
        self.delete_behavior_connection_button.setEnabled(not connection.locked)
        self.graph.update()

    def _behavior_connection_list_selected(self, row: int) -> None:
        """Synchronize typed-edge list selection with the graph."""
        if self._updating or row < 0:
            return
        item = self.behavior_connection_list.item(row)
        if item is not None:
            self._select_behavior_connection_id(
                str(item.data(Qt.ItemDataRole.UserRole) or "")
            )

    def _delete_selected_behavior_connection(self) -> None:
        """Delete the typed edge selected in the inspector list."""
        item = self.behavior_connection_list.currentItem()
        if item is not None:
            self._delete_behavior_connection_id(
                str(item.data(Qt.ItemDataRole.UserRole) or "")
            )

    def _update_selected_behavior_connection(self) -> None:
        """Reconnect or relabel the selected typed behavior edge."""
        item = self.behavior_connection_list.currentItem()
        connection = self.session.project.behavior_connection(
            str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
        )
        if connection is None or connection.locked:
            return
        original = (
            connection.source_node_id,
            connection.source_port_id,
            connection.target_node_id,
            connection.target_port_id,
            connection.label,
            connection.condition,
        )
        connection.source_node_id = str(
            self.behavior_source_node_combo.currentData() or ""
        )
        connection.source_port_id = str(
            self.behavior_source_port_combo.currentData() or ""
        )
        connection.target_node_id = str(
            self.behavior_target_node_combo.currentData() or ""
        )
        connection.target_port_id = str(
            self.behavior_target_port_combo.currentData() or ""
        )
        connection.label = self.behavior_connection_label_edit.text().strip()
        error = behavior_connection_error(self.session.project, connection)
        if error:
            (
                connection.source_node_id,
                connection.source_port_id,
                connection.target_node_id,
                connection.target_port_id,
                connection.label,
                connection.condition,
            ) = original
            QMessageBox.information(
                self, "Incompatible behavior ports", error.capitalize() + "."
            )
            return
        self.session.mark_changed()

    def _delete_behavior_connection_id(self, connection_id: str) -> None:
        """Delete one unlocked typed behavior edge."""
        connection = self.session.project.behavior_connection(connection_id)
        if connection is None or connection.locked:
            return
        self.session.project.behavior_connections = [
            item
            for item in self.session.project.behavior_connections
            if item.id != connection_id
        ]
        self.graph.selected_behavior_connection_id = None
        self.session.mark_changed()

    def _refresh_flow_diagnostics(self) -> None:
        """Populate live validation, canvas badges, and assisted next steps."""
        self._flow_diagnostics = flow_diagnostics(self.session.project)
        severity_rank = {"info": 1, "warning": 2, "error": 3}
        node_severity: dict[str, str] = {}
        for diagnostic in self._flow_diagnostics:
            if diagnostic.target_kind != "node" or not diagnostic.target_id:
                continue
            current = node_severity.get(diagnostic.target_id, "")
            if severity_rank.get(diagnostic.severity, 0) > severity_rank.get(
                current, 0
            ):
                node_severity[diagnostic.target_id] = diagnostic.severity
        self.graph.node_diagnostic_severity = node_severity
        self.flow_diagnostics_list.clear()
        colors = {"error": "#d32f2f", "warning": "#ef6c00", "info": "#607d8b"}
        selected_filter = str(self.flow_diagnostic_filter.currentData() or "all")
        visible_diagnostics = [
            diagnostic
            for diagnostic in self._flow_diagnostics
            if selected_filter == "all" or diagnostic.severity == selected_filter
        ]
        for diagnostic in visible_diagnostics:
            item = QListWidgetItem(
                f"{diagnostic.severity.upper()} · {diagnostic.message}"
            )
            item.setForeground(QColor(colors.get(diagnostic.severity, "#607d8b")))
            item.setData(
                Qt.ItemDataRole.UserRole,
                (
                    diagnostic.target_kind,
                    diagnostic.target_id,
                    diagnostic.code,
                    diagnostic.message,
                ),
            )
            item.setToolTip(
                f"{diagnostic.code}\n{self._flow_fix_hint(diagnostic.code)}\n"
                "Double-click to locate the target."
            )
            self.flow_diagnostics_list.addItem(item)
        if not self.flow_diagnostics_list.count():
            message = (
                "OK · No structural flow problems"
                if not self._flow_diagnostics
                else "No findings match this filter"
            )
            item = QListWidgetItem(message)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.flow_diagnostics_list.addItem(item)
        counts = {
            severity: sum(
                diagnostic.severity == severity for diagnostic in self._flow_diagnostics
            )
            for severity in ("error", "warning", "info")
        }
        self.flow_diagnostic_summary_label.setText(
            f"{counts['error']} errors · {counts['warning']} warnings · "
            f"{counts['info']} information"
        )
        self.next_issue_button.setEnabled(bool(self._flow_diagnostics))
        self.next_diagnostic_button.setEnabled(bool(self._flow_diagnostics))
        self.go_to_diagnostic_button.setEnabled(bool(visible_diagnostics))
        if counts["error"]:
            first = next(
                item for item in self._flow_diagnostics if item.severity == "error"
            )
            self._set_flow_assistant_message(
                f"Flow blocked by {counts['error']} error(s). Next: {first.message}",
                "error",
            )
        elif counts["warning"]:
            first = next(
                item for item in self._flow_diagnostics if item.severity == "warning"
            )
            self._set_flow_assistant_message(
                f"Flow can be debugged, with {counts['warning']} warning(s). "
                f"Next: {first.message}",
                "warning",
            )
        elif not self.session.project.behavior_nodes:
            self._set_flow_assistant_message(
                "Start in App GUI: select an interactive element and choose "
                "Create behavior from this element...",
                "info",
            )
        else:
            self._set_flow_assistant_message(
                "Flow is valid. Select an Event node and choose Debug selected.",
                "success",
            )
        self.graph.update()

    @staticmethod
    def _flow_fix_hint(code: str) -> str:
        """Return one concise, actionable repair hint for a validator code."""
        hints = {
            "required-input-unconnected": "Connect a highlighted output to the required input.",
            "condition-branch-unconnected": "Connect both True and False outputs, or remove the unused condition.",
            "orphan-node": "Connect the node into an Event path or delete it.",
            "invalid-operation-property": "Select the node and complete its Operation properties.",
            "invalid-operation-reference": "Choose an existing screen or element in the node form.",
            "invalid-ui-event-binding": "Recreate the behavior from the source App GUI element.",
            "invalid-behavior-connection": "Reconnect the red edge using highlighted compatible ports.",
            "unsupported-behavior-condition": "Clear the legacy edge condition and branch with a Condition node.",
            "unknown-behavior-operation": "Choose a supported operation in the Node inspector.",
            "operation-kind-mismatch": "Choose an operation matching the node kind.",
            "structural-only-node": "Choose an executable Operation in the Node inspector.",
            "missing-behavior-entry": "Bind an Event to an App GUI element or add a service Event.",
            "event-unconnected": "Drag an Event output to a compatible operation input.",
            "unreachable-behavior-node": "Connect this node to a path beginning at an Event.",
            "invalid-payload-reference": "Use an offered exact token such as $value or $text.",
            "unreachable-screen": "Add a navigation relation from a reachable screen.",
            "terminal-screen": "Add a navigation relation or intentionally handle Back in the app.",
            "unbounded-cycle": "Insert an explicit Timer or remove the synchronous cycle.",
        }
        return hints.get(code, "Open the target and review the highlighted contract.")

    def _set_flow_assistant_message(self, message: str, severity: str = "info") -> None:
        """Show immediate, color-independent flow editing feedback."""
        colors = {
            "error": ("#4a1f1f", "#ffcdd2"),
            "warning": ("#4a3615", "#ffe0a3"),
            "success": ("#173d2a", "#b9f6ca"),
            "info": ("#17354a", "#d1ecff"),
        }
        background, foreground = colors.get(severity, colors["info"])
        self.flow_assistant_banner.setText(message)
        self.flow_assistant_banner.setStyleSheet(
            f"background: {background}; color: {foreground}; border-radius: 4px; "
            "padding: 7px;"
        )

    def _show_flow_validation(self) -> None:
        """Refresh validation and reveal the actionable findings tab."""
        self._refresh_flow_diagnostics()
        self.flow_inspector_tabs.setCurrentIndex(2)
        if self.flow_diagnostics_list.count():
            self.flow_diagnostics_list.setCurrentRow(0)

    def _jump_to_next_diagnostic(self) -> None:
        """Cycle through findings and center each referenced graph target."""
        if not self._flow_diagnostics:
            self._refresh_flow_diagnostics()
        if not self._flow_diagnostics:
            return
        self.flow_diagnostic_filter.setCurrentIndex(0)
        self._diagnostic_cursor = (self._diagnostic_cursor + 1) % len(
            self._flow_diagnostics
        )
        self.flow_inspector_tabs.setCurrentIndex(2)
        self.flow_diagnostics_list.setCurrentRow(self._diagnostic_cursor)
        item = self.flow_diagnostics_list.currentItem()
        if item is not None:
            self._jump_to_diagnostic(item)

    def _diagnostic_item_changed(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        """Explain the selected finding and its recommended correction."""
        del previous
        target = current.data(Qt.ItemDataRole.UserRole) if current else None
        if not isinstance(target, tuple) or len(target) < 4:
            self.flow_diagnostic_detail.setText("Select a finding for a suggested fix.")
            return
        unused_kind, unused_id, code, message = target
        del unused_kind, unused_id
        self.flow_diagnostic_detail.setText(
            f"{message}\n\nSuggested fix: {self._flow_fix_hint(str(code))}"
        )

    def _jump_to_diagnostic(self, item: QListWidgetItem) -> None:
        """Select and center the graph target referenced by one diagnostic."""
        target = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(target, tuple) or len(target) < 2:
            return
        kind, target_id = target[:2]
        if kind == "node":
            node = self.session.project.flow_node(target_id)
            if node is not None:
                self.graph.selected_behavior_node_ids = {node.id}
                self.graph.primary_behavior_node_id = node.id
                self._behavior_node_selected(node.id)
                self._center_graph_at(node.node_x, node.node_y)
        elif kind == "behavior-connection":
            self._select_behavior_connection_id(target_id)
        elif kind == "screen":
            screen = self.session.project.screen(target_id)
            if screen is not None:
                self.graph.selected_screen_id = screen.id
                self._center_graph_at(screen.node_x, screen.node_y)
        elif kind == "navigation-connection":
            self._select_connection_id(target_id)

    def _refresh_flow_fragments(self) -> None:
        """Reload the personal flow-fragment library into the inspector."""
        selected = self.flow_fragment_list.currentItem()
        selected_id = (
            str(selected.data(Qt.ItemDataRole.UserRole) or "") if selected else ""
        )
        self.flow_fragment_list.clear()
        try:
            fragments = self.flow_library.all_fragments()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            item = QListWidgetItem(f"Library unavailable: {error}")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.flow_fragment_list.addItem(item)
            self._flow_fragment_selection_changed()
            return
        source_filter = str(self.flow_fragment_source_combo.currentData() or "personal")
        query = self.flow_fragment_search.text().strip().casefold()
        fragments = tuple(
            fragment
            for fragment in fragments
            if (source_filter == "all" or fragment.source == source_filter)
            and (
                not query
                or query
                in " ".join(
                    (
                        fragment.name,
                        fragment.description,
                        fragment.category,
                        *fragment.tags,
                    )
                ).casefold()
            )
        )
        for fragment in fragments:
            source = "Built in" if fragment.source == "built-in" else "Personal"
            item = QListWidgetItem(
                f"{fragment.name} · {len(fragment.nodes)} node(s) · {source}"
            )
            item.setData(Qt.ItemDataRole.UserRole, fragment.id)
            item.setData(Qt.ItemDataRole.UserRole + 1, fragment.source)
            self.flow_fragment_list.addItem(item)
            if fragment.id == selected_id:
                self.flow_fragment_list.setCurrentItem(item)
        if self.flow_fragment_list.currentItem() is None and fragments:
            self.flow_fragment_list.setCurrentRow(0)
        self._flow_fragment_selection_changed()

    def _flow_fragment_selection_changed(self) -> None:
        """Enable fragment operations only for a valid stored selection."""
        available = bool(
            self.flow_fragment_list.currentItem()
            and self.flow_fragment_list.currentItem().data(Qt.ItemDataRole.UserRole)
        )
        item = self.flow_fragment_list.currentItem()
        source = str(item.data(Qt.ItemDataRole.UserRole + 1) or "") if item else ""
        fragment_id = self._selected_flow_fragment_id()
        fragment = next(
            (
                candidate
                for candidate in self.flow_library.all_fragments()
                if candidate.id == fragment_id
            ),
            None,
        )
        if fragment is not None:
            anchors = ", ".join(
                str(anchor.get("label", anchor.get("id", "anchor")))
                for anchor in fragment.anchors
            )
            self.flow_fragment_preview.setText(
                f"{fragment.category} · v{fragment.version}\n"
                f"{fragment.description or 'Reusable behavior structure.'}\n"
                f"Anchors: {anchors or 'none'}"
            )
        else:
            self.flow_fragment_preview.setText("Select a reusable flow.")
        self.insert_flow_fragment_button.setEnabled(available)
        self.rename_flow_fragment_button.setEnabled(available and source == "personal")
        self.delete_flow_fragment_button.setEnabled(available and source == "personal")
        self.save_flow_fragment_button.setEnabled(
            bool(self.graph.selected_behavior_node_ids)
        )

    def _save_flow_fragment(self) -> None:
        """Store selected behavior nodes in the personal fragment library."""
        if not self.graph.selected_behavior_node_ids:
            return
        name, accepted = QInputDialog.getText(
            self, "Save flow fragment", "Fragment name", text="Reusable flow"
        )
        if not accepted or not name.strip():
            return
        try:
            self.flow_library.add(
                name,
                self.session.project,
                set(self.graph.selected_behavior_node_ids),
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot save flow fragment", str(error))
            return
        self._refresh_flow_fragments()

    def _selected_flow_fragment_id(self) -> str:
        """Return the selected personal flow-fragment identifier."""
        item = self.flow_fragment_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def _insert_flow_fragment(self) -> None:
        """Insert a detached clone of the selected reusable fragment."""
        fragment_id = self._selected_flow_fragment_id()
        if not fragment_id:
            return
        try:
            horizontal = self.graph_scroll.horizontalScrollBar().value()
            vertical = self.graph_scroll.verticalScrollBar().value()
            viewport = self.graph_scroll.viewport().size()
            x = round((horizontal + viewport.width() / 2) / self.graph.zoom)
            y = round((vertical + viewport.height() / 2) / self.graph.zoom)
            inserted = self.flow_library.insert(fragment_id, self.session.project, x, y)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot insert flow fragment", str(error))
            return
        self.graph.selected_behavior_node_ids = set(inserted)
        self.graph.primary_behavior_node_id = inserted[0] if inserted else None
        self.session.mark_changed()

    def _rename_flow_fragment(self) -> None:
        """Rename the selected personal flow fragment."""
        fragment_id = self._selected_flow_fragment_id()
        fragment = next(
            (item for item in self.flow_library.fragments() if item.id == fragment_id),
            None,
        )
        if fragment is None:
            return
        name, accepted = QInputDialog.getText(
            self, "Rename flow fragment", "Fragment name", text=fragment.name
        )
        if not accepted or not name.strip():
            return
        try:
            self.flow_library.rename(fragment_id, name)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot rename flow fragment", str(error))
            return
        self._refresh_flow_fragments()

    def _delete_flow_fragment(self) -> None:
        """Delete the selected fragment after explicit confirmation."""
        fragment_id = self._selected_flow_fragment_id()
        if not fragment_id:
            return
        if (
            QMessageBox.question(
                self,
                "Delete flow fragment",
                "Delete this reusable flow fragment? Existing project copies remain intact.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self.flow_library.remove(fragment_id)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Cannot delete flow fragment", str(error))
            return
        self._refresh_flow_fragments()

    def _center_graph_at(self, x: float, y: float) -> None:
        """Center the graph viewport around one logical graph coordinate."""
        viewport = self.graph_scroll.viewport().size()
        self.graph_scroll.horizontalScrollBar().setValue(
            max(0, round(x * self.graph.zoom - viewport.width() / 2))
        )
        self.graph_scroll.verticalScrollBar().setValue(
            max(0, round(y * self.graph.zoom - viewport.height() / 2))
        )
        self._update_minimap_viewport()

    def _jump_to_flow_search(self, index: int) -> None:
        """Select and center a screen or behavior node from graph search."""
        target = self.flow_search_combo.itemData(index)
        if not isinstance(target, tuple) or len(target) != 2:
            return
        kind, target_id = target
        if kind == "screen":
            screen = self.session.project.screen(target_id)
            if screen is not None:
                self.graph.selected_screen_id = screen.id
                self.graph.selected_behavior_node_ids.clear()
                self._center_graph_at(screen.node_x, screen.node_y)
        else:
            node = self.session.project.flow_node(target_id)
            if node is not None:
                self.graph.selected_screen_id = None
                self.graph.selected_behavior_node_ids = {node.id}
                self.graph.primary_behavior_node_id = node.id
                self._behavior_node_selected(node.id)
                self._center_graph_at(node.node_x, node.node_y)
        self.graph.update()

    def _trace_selected_behavior(self) -> None:
        """Trace one deterministic structural path without executing business logic."""
        node = self._selected_behavior_node()
        if node is None:
            self.simulator_result_label.setText("Select a behavior node to trace.")
            return
        node_ids: list[str] = []
        connection_ids: list[str] = []
        labels: list[str] = []
        current = node
        visited: set[str] = set()
        for unused_step in range(100):
            del unused_step
            if current.id in visited:
                labels.append(f"cycle:{current.name}")
                break
            visited.add(current.id)
            node_ids.append(current.id)
            label = current.name
            if current.kind == "state" and "value" in current.properties:
                label += f" [value={current.properties['value']!r}]"
            labels.append(label)
            if current.breakpoint:
                labels.append("BREAKPOINT")
                break
            outgoing = [
                connection
                for connection in self.session.project.behavior_connections
                if connection.source_node_id == current.id
            ]
            if current.kind == "condition":
                branch = str(current.properties.get("default_branch", "true"))
                branch_matches = [
                    connection
                    for connection in outgoing
                    if connection.source_port_id == branch
                ]
                outgoing = branch_matches or outgoing
            if not outgoing:
                break
            connection = outgoing[0]
            connection_ids.append(connection.id)
            target = self.session.project.flow_node(connection.target_node_id)
            if target is None:
                break
            current = target
        self.graph.active_trace_node_ids = set(node_ids)
        self.graph.active_trace_connection_ids = set(connection_ids)
        result = "Behavior trace: " + " → ".join(labels)
        self.simulator_result_label.setText(result)
        self.simulator_history_list.addItem(result)
        self.graph.update()

    def refresh(self) -> None:
        """Refresh graph controls from the shared project."""
        if self._debug_runtime is not None:
            self._debug_runtime = None
            self._debug_ui = None
            self._debug_timer = None
            self.runtime_trace_list.clear()
            self.runtime_payload_view.clear()
            self.graph.active_trace_node_ids.clear()
            self.graph.active_trace_connection_ids.clear()
        self._updating = True
        selected_connection_id = self.graph.selected_connection_id
        selected_source = self.source_combo.currentData()
        selected_target = self.target_combo.currentData()
        selected_behavior_connection_id = self.graph.selected_behavior_connection_id
        selected_behavior_source = self.behavior_source_node_combo.currentData()
        selected_behavior_target = self.behavior_target_node_combo.currentData()
        self.source_combo.clear()
        self.target_combo.clear()
        for screen in self.session.project.screens:
            screen_key = flow_endpoint_key(screen.id)
            self.source_combo.addItem(f"Screen · {screen.name}", screen_key)
            self.target_combo.addItem(f"Screen · {screen.name}", screen_key)
            for element in self.graph._navigation_elements(screen):
                endpoint_key = flow_endpoint_key(screen.id, element.id)
                label = (
                    f"{screen.name} / {element.kind} · {element.name}"
                    f" [{element.activation_event()}]"
                )
                self.source_combo.addItem(label, endpoint_key)
                self.target_combo.addItem(label, endpoint_key)
        self._restore_combo(self.source_combo, selected_source)
        self._restore_combo(self.target_combo, selected_target)
        self.connection_list.clear()
        self.condition_edit.clear()
        self.action_edit.clear()
        self.legacy_navigation_logic_group.hide()
        self.update_relation_button.setEnabled(True)
        self.delete_relation_button.setEnabled(True)
        self._set_relation_fields_enabled(True, False, False)
        for connection in self.session.project.connections:
            source = self.session.project.screen(connection.source_id)
            target = self.session.project.screen(connection.target_id)
            if source is None or target is None:
                continue
            prefix = "[code] " if connection.source_path else ""
            if connection.locked:
                prefix = "[locked] "
            source_element = self.session.project.element(
                connection.source_id,
                connection.source_element_id,
            )
            target_element = self.session.project.element(
                connection.target_id,
                connection.target_element_id,
            )
            source_label = (
                f"{source.name}/{source_element.name}"
                if source_element is not None
                else source.name
            )
            target_label = (
                f"{target.name}/{target_element.name}"
                if target_element is not None
                else target.name
            )
            item = QListWidgetItem(
                f"{prefix}{source_label} -- {connection.trigger} --> {target_label}"
            )
            item.setData(Qt.ItemDataRole.UserRole, connection.id)
            if connection.source_path:
                item.setToolTip(f"{connection.source_path}:{connection.source_line}")
            self.connection_list.addItem(item)
        valid_node_ids = {node.id for node in self.session.project.behavior_nodes}
        self.graph.selected_behavior_node_ids.intersection_update(valid_node_ids)
        if self.graph.primary_behavior_node_id not in valid_node_ids:
            self.graph.primary_behavior_node_id = next(
                iter(self.graph.selected_behavior_node_ids), None
            )
        self.behavior_source_node_combo.clear()
        self.behavior_target_node_combo.clear()
        self.flow_search_combo.clear()
        for screen in self.session.project.screens:
            self.flow_search_combo.addItem(
                f"Screen · {screen.name}", ("screen", screen.id)
            )
        for node in self.session.project.behavior_nodes:
            label = f"{node.kind.title()} · {node.name}"
            self.behavior_source_node_combo.addItem(label, node.id)
            self.flow_search_combo.addItem(label, ("node", node.id))
        self._restore_combo(self.behavior_source_node_combo, selected_behavior_source)
        self._refresh_behavior_source_ports()
        self._restore_combo(self.behavior_target_node_combo, selected_behavior_target)
        self._refresh_behavior_target_ports()
        self.behavior_connection_list.clear()
        self.update_behavior_connection_button.setEnabled(False)
        self.delete_behavior_connection_button.setEnabled(False)
        self.legacy_behavior_condition_group.hide()
        for connection in self.session.project.behavior_connections:
            source = self.session.project.flow_node(connection.source_node_id)
            target = self.session.project.flow_node(connection.target_node_id)
            source_port = source.port(connection.source_port_id) if source else None
            target_port = target.port(connection.target_port_id) if target else None
            source_label = (
                f"{source.name}.{source_port.name}"
                if source is not None and source_port is not None
                else "Missing source"
            )
            target_label = (
                f"{target.name}.{target_port.name}"
                if target is not None and target_port is not None
                else "Missing target"
            )
            item = QListWidgetItem(
                f"{source_label} → {target_label}"
                + (f" · {connection.label}" if connection.label else "")
            )
            item.setData(Qt.ItemDataRole.UserRole, connection.id)
            self.behavior_connection_list.addItem(item)
        if not any(
            connection.id == selected_behavior_connection_id
            for connection in self.session.project.behavior_connections
        ):
            selected_behavior_connection_id = None
            self.graph.selected_behavior_connection_id = None
        if self.session.project.screen(self.simulated_screen_id) is None:
            self.simulated_screen_id = self.session.project.start_screen_id
        if not any(
            connection.id == selected_connection_id
            for connection in self.session.project.connections
        ):
            selected_connection_id = None
            self.graph.selected_connection_id = None
        self._update_simulator()
        self.graph.refresh_geometry()
        self.graph.update()
        self.flow_minimap.update()
        self._refresh_behavior_inspector()
        self._refresh_flow_diagnostics()
        self._refresh_flow_fragments()
        self._update_debugger_controls()
        self._updating = False
        self._update_minimap_viewport()
        if selected_connection_id:
            self._select_connection_id(selected_connection_id)
        else:
            self._source_endpoint_changed()
        if selected_behavior_connection_id:
            self._select_behavior_connection_id(selected_behavior_connection_id)

    def _restore_combo(self, combo: QComboBox, value: object) -> None:
        """Restore a combo selection by item data."""
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    def _source_trigger(self, screen_id: str, element_id: str) -> str:
        """Return the configured trigger for a screen or element source."""
        element = self.session.project.element(screen_id, element_id)
        if element is not None:
            return element.activation_event()
        return self.trigger_edit.text().strip()

    def _source_endpoint_changed(self) -> None:
        """Load an element activation event when its endpoint is selected."""
        if self._updating:
            return
        screen_id, element_id = parse_flow_endpoint(self.source_combo.currentData())
        element = self.session.project.element(screen_id, element_id)
        if element is not None:
            self.trigger_edit.setText(element.activation_event())
            self.trigger_edit.setEnabled(False)
            set_widget_tooltip(
                self.trigger_edit,
                "trigger_edit",
                self,
                "Edit this event in the element properties workspace.",
            )
        else:
            self.trigger_edit.setEnabled(True)
            set_widget_tooltip(
                self.trigger_edit,
                "trigger_edit",
                self,
                "Event name for a screen-level relation.",
            )

    def _add_relation(self) -> None:
        """Add a relationship from the editor controls."""
        source_id, source_element_id = parse_flow_endpoint(
            self.source_combo.currentData()
        )
        target_id, target_element_id = parse_flow_endpoint(
            self.target_combo.currentData()
        )
        trigger = self._source_trigger(source_id, source_element_id)
        if not source_id or not target_id or not trigger:
            QMessageBox.information(
                self,
                "Incomplete relation",
                "Choose endpoints and configure an activation event.",
            )
            return
        self._create_relation(
            source_id,
            target_id,
            trigger,
            source_element_id,
            target_element_id,
        )

    def _create_relation(
        self,
        source_id: str,
        target_id: str,
        trigger: str,
        source_element_id: str = "",
        target_element_id: str = "",
    ) -> None:
        """Create one design relation from form or mouse-selected nodes."""
        if not source_element_id:
            alert = _screen_alert_element(self.session.project, source_id)
            if alert is not None:
                source_element_id = alert.id
                trigger = alert.activation_event()
        duplicate = next(
            (
                connection
                for connection in self.session.project.connections
                if connection.source_id == source_id
                and connection.target_id == target_id
                and connection.trigger == trigger
                and connection.source_element_id == source_element_id
                and connection.target_element_id == target_element_id
            ),
            None,
        )
        if duplicate is not None:
            self._select_connection_id(duplicate.id)
            return
        connection = FlowConnection.create(
            source_id,
            target_id,
            trigger,
            source_element_id,
            target_element_id,
        )
        source_element = self.session.project.element(source_id, source_element_id)
        if source_element is not None:
            connection.trigger_event_id = source_element.event_id
        connection.condition = ""
        connection.action = ""
        connection.transition = self.transition_combo.currentText()
        self.session.project.connections.append(connection)
        self.session.mark_changed()
        self._select_connection_id(connection.id)

    def _graph_connection_requested(
        self,
        source_id: str,
        source_element_id: str,
        target_id: str,
        target_element_id: str,
    ) -> None:
        """Create a relation from a mouse-drawn graph connection."""
        self._restore_combo(
            self.source_combo,
            flow_endpoint_key(source_id, source_element_id),
        )
        self._restore_combo(
            self.target_combo,
            flow_endpoint_key(target_id, target_element_id),
        )
        trigger = self._source_trigger(source_id, source_element_id) or "select"
        self.trigger_edit.setText(trigger)
        self._create_relation(
            source_id,
            target_id,
            trigger,
            source_element_id,
            target_element_id,
        )

    def _select_connection_id(self, connection_id: str) -> None:
        """Select a relation list item by project identifier."""
        self.graph.selected_connection_id = connection_id
        self.graph.selected_screen_id = None
        self.graph.update()
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
        target_id, target_element_id = parse_flow_endpoint(
            self.target_combo.currentData()
        )
        target = self.session.project.screen(target_id)
        if connection.source_path and (target is None or target.source_state is None):
            QMessageBox.information(
                self,
                "Target is design-only",
                "An imported relation needs a target with a source state.",
            )
            return
        connection.target_id = target_id
        connection.target_element_id = target_element_id
        if not connection.source_path:
            source_id, source_element_id = parse_flow_endpoint(
                self.source_combo.currentData()
            )
            if not source_element_id:
                alert = _screen_alert_element(self.session.project, source_id)
                if alert is not None:
                    source_element_id = alert.id
            connection.source_id = source_id
            connection.source_element_id = source_element_id
            connection.trigger = (
                self._source_trigger(source_id, source_element_id) or connection.trigger
            )
            source_element = self.session.project.element(
                source_id, source_element_id
            )
            if source_element is not None:
                connection.trigger_event_id = source_element.event_id
            connection.transition = self.transition_combo.currentText()
        else:
            connection.trigger = self.trigger_edit.text().strip() or connection.trigger
        self.session.mark_changed()

    def _delete_relation(self) -> None:
        """Delete the selected relationship."""
        connection = self._selected_connection()
        if connection is None:
            return
        self._delete_connection_id(connection.id)

    def _delete_connection_id(self, connection_id: str) -> None:
        """Delete one design relationship by graph identifier."""
        connection = next(
            (
                item
                for item in self.session.project.connections
                if item.id == connection_id
            ),
            None,
        )
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
        self.graph.selected_connection_id = None
        self.session.mark_changed()

    def _connection_selected(self, row: int) -> None:
        """Load one relationship into the editor controls."""
        if self._updating or row < 0:
            return
        connection = self._selected_connection()
        if connection is None:
            return
        self.graph.selected_connection_id = connection.id
        self.graph.selected_screen_id = None
        self.graph.update()
        self.update_relation_button.setEnabled(not connection.locked)
        self.delete_relation_button.setEnabled(not bool(connection.source_path))
        self._restore_combo(
            self.source_combo,
            flow_endpoint_key(
                connection.source_id,
                connection.source_element_id,
            ),
        )
        self._restore_combo(
            self.target_combo,
            flow_endpoint_key(
                connection.target_id,
                connection.target_element_id,
            ),
        )
        self.trigger_edit.setText(connection.trigger)
        self.condition_edit.setText(connection.condition)
        self.action_edit.setText(connection.action)
        self.legacy_navigation_logic_group.setVisible(
            bool(connection.condition or connection.action)
        )
        self.transition_combo.setCurrentText(connection.transition)
        source_backed = bool(connection.source_path)
        self._set_relation_fields_enabled(
            not connection.locked,
            source_backed,
            bool(connection.source_element_id),
        )

    def _set_relation_fields_enabled(
        self,
        editable: bool,
        source_backed: bool,
        element_source: bool,
    ) -> None:
        """Limit relation controls to fields that can reach source code."""
        self.source_combo.setEnabled(editable and not source_backed)
        self.target_combo.setEnabled(editable)
        self.trigger_edit.setEnabled(editable and not element_source)
        self.condition_edit.setEnabled(False)
        self.action_edit.setEnabled(False)
        self.clear_navigation_logic_button.setEnabled(
            editable
            and not source_backed
            and bool(self.condition_edit.text() or self.action_edit.text())
        )
        self.transition_combo.setEnabled(editable and not source_backed)

    def _clear_selected_navigation_logic(self) -> None:
        """Remove legacy navigation fields that the runtime cannot execute."""
        connection = self._selected_connection()
        if connection is None or connection.locked or connection.source_path:
            return
        connection.condition = ""
        connection.action = ""
        self.condition_edit.clear()
        self.action_edit.clear()
        self.legacy_navigation_logic_group.hide()
        self.session.mark_changed()
        self._set_flow_assistant_message(
            "Legacy relation logic cleared. Add typed Condition and Action nodes "
            "to the behavior flow."
        )

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
        self.connection_list.clearSelection()
        self.graph.selected_connection_id = None
        self._restore_combo(self.source_combo, flow_endpoint_key(screen_id))

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

    def _auto_layout_nodes(self) -> None:
        """Arrange screen and behavior nodes by graph depth and chosen direction."""
        project = self.session.project
        if not project.screens and not project.behavior_nodes:
            return
        horizontal = self.layout_direction_combo.currentData() == "horizontal"
        levels = {project.start_screen_id: 0}
        queue = [project.start_screen_id]
        while queue:
            source_id = queue.pop(0)
            level = levels[source_id]
            for connection in project.connections:
                if connection.source_id != source_id:
                    continue
                if connection.target_id not in levels:
                    levels[connection.target_id] = level + 1
                    queue.append(connection.target_id)
        fallback_level = max(levels.values(), default=-1) + 1
        for screen in project.screens:
            levels.setdefault(screen.id, fallback_level)
        rows: dict[int, int] = {}
        vertical_step = (
            max(
                (self.graph._node_height(screen) for screen in project.screens),
                default=140,
            )
            + 60
        )
        changed = False
        for screen in project.screens:
            level = levels[screen.id]
            row = rows.get(level, 0)
            rows[level] = row + 1
            if horizontal:
                x = 60 + level * (self.graph.NODE_WIDTH + 100)
                y = round(60 + row * vertical_step)
            else:
                x = 60 + row * (self.graph.NODE_WIDTH + 100)
                y = round(60 + level * vertical_step)
            if (screen.node_x, screen.node_y) != (x, y):
                screen.node_x, screen.node_y = x, y
                changed = True

        incoming_ids = {
            connection.target_node_id for connection in project.behavior_connections
        }
        behavior_levels = {
            node.id: 0 for node in project.behavior_nodes if node.id not in incoming_ids
        }
        queue = list(behavior_levels)
        while queue:
            source_id = queue.pop(0)
            level = behavior_levels[source_id]
            for connection in project.behavior_connections:
                if connection.source_node_id != source_id:
                    continue
                next_level = level + 1
                if next_level > behavior_levels.get(connection.target_node_id, -1):
                    behavior_levels[connection.target_node_id] = next_level
                    if next_level <= len(project.behavior_nodes):
                        queue.append(connection.target_node_id)
        behavior_fallback = max(behavior_levels.values(), default=-1) + 1
        for node in project.behavior_nodes:
            behavior_levels.setdefault(node.id, behavior_fallback)
        behavior_rows: dict[int, int] = {}
        screen_bottom = max(
            (
                screen.node_y + self.graph._node_height(screen)
                for screen in project.screens
            ),
            default=20,
        )
        screen_right = max(
            (screen.node_x + self.graph.NODE_WIDTH for screen in project.screens),
            default=20,
        )
        for node in project.behavior_nodes:
            if node.pinned:
                continue
            level = behavior_levels[node.id]
            row = behavior_rows.get(level, 0)
            behavior_rows[level] = row + 1
            if horizontal:
                x = 60 + level * (self.graph.BEHAVIOR_NODE_WIDTH + 90)
                y = round(screen_bottom + 100 + row * 130)
            else:
                x = round(screen_right + 100 + row * 240)
                y = 60 + level * 140
            if (node.node_x, node.node_y) != (x, y):
                node.node_x, node.node_y = x, y
                changed = True
        for group in project.flow_groups:
            members = [
                node for node in project.behavior_nodes if node.group_id == group.id
            ]
            if not members:
                continue
            left = min(node.node_x for node in members) - 30
            top = min(node.node_y for node in members) - 36
            right = (
                max(node.node_x + self.graph.BEHAVIOR_NODE_WIDTH for node in members)
                + 30
            )
            bottom = (
                max(
                    node.node_y + self.graph._behavior_node_height(node)
                    for node in members
                )
                + 30
            )
            group.node_x, group.node_y = left, top
            group.width, group.height = right - left, bottom - top
        if changed:
            self.session.mark_changed()

    def _send_simulator_event(self) -> None:
        """Send one event through the navigation graph."""
        event = self.simulator_event_edit.text().strip()
        if not event:
            return
        self._dispatch_simulator_event(event)

    def _preview_event_requested(self, event: str) -> None:
        """Dispatch an event from the safe interactive flow preview."""
        self.simulator_event_edit.setText(event)
        self._dispatch_simulator_event(event)

    def _dispatch_simulator_event(self, event: str) -> None:
        """Apply one named event to the navigation simulator."""
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
        self.simulated_element_id = connection.target_element_id
        result = f"Transition: {connection.transition}"
        if connection.condition:
            result += f" | condition: {connection.condition}"
        if connection.action:
            result += f" | action: {connection.action}"
        self.simulator_result_label.setText(result)
        if self.simulation_history_index < len(self.simulation_history) - 1:
            self.simulation_history = self.simulation_history[
                : self.simulation_history_index + 1
            ]
        self.simulation_history.append(
            (
                self.simulated_screen_id,
                self.simulated_element_id,
                result,
                event,
            )
        )
        self.simulation_history_index = len(self.simulation_history) - 1
        self.simulator_history_list.addItem(f"{event} · {result}")
        self._update_simulator()

    def _preview_focus_changed(self, name: str) -> None:
        """Show the current safe-preview focus in the Flow test label."""
        screen = self.session.project.screen(self.simulated_screen_id)
        screen_name = screen.name if screen is not None else "Missing screen"
        suffix = f" | Focus: {name}" if name else ""
        self.simulator_label.setText(f"Current screen: {screen_name}{suffix}")

    def _reset_simulator(self) -> None:
        """Reset navigation simulation to the start screen."""
        self.simulated_screen_id = self.session.project.start_screen_id
        self.simulated_element_id = ""
        self.simulation_history.clear()
        self.simulation_history_index = -1
        self.simulator_history_list.clear()
        self.simulator_result_label.setText("Ready")
        self.graph.active_trace_node_ids.clear()
        self.graph.active_trace_connection_ids.clear()
        self._update_simulator()

    def _move_simulation_history(self, step: int) -> None:
        """Move backward or forward through recorded structural navigation."""
        index = self.simulation_history_index + step
        if not 0 <= index < len(self.simulation_history):
            return
        self.simulation_history_index = index
        screen_id, element_id, result, event = self.simulation_history[index]
        self.simulated_screen_id = screen_id
        self.simulated_element_id = element_id
        self.simulator_result_label.setText(f"History {event}: {result}")
        self.simulator_history_list.setCurrentRow(index)
        self._update_simulator()

    def _update_simulator(self) -> None:
        """Refresh the structural flow-test label and graph highlight."""
        screen = self.session.project.screen(self.simulated_screen_id)
        name = screen.name if screen is not None else "Missing screen"
        self.simulator_label.setText(f"Current screen: {name}")
        self.preview.set_screen(
            self.simulated_screen_id,
            self.simulated_element_id,
        )
        self.graph.active_trace_screen_id = self.simulated_screen_id
        self.graph.update()
        self.simulator_back_button.setEnabled(self.simulation_history_index > 0)
        self.simulator_forward_button.setEnabled(
            0 <= self.simulation_history_index < len(self.simulation_history) - 1
        )
