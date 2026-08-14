"""Embedded Picoware simulator process and framebuffer view."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import shutil
import time

from PySide6.QtCore import (
    QDir,
    QEvent,
    QObject,
    QPointF,
    QProcess,
    QRectF,
    QTemporaryDir,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget


SIMULATOR_BOARDS = (
    "picocalc-pico2w",
    "picocalc-pico2",
    "picocalc-picow",
    "picocalc-pico",
    "cardputer",
    "flipper-zero",
    "waveshare-1.28-rp2350",
    "waveshare-1.43-rp2350",
    "waveshare-1.69-rp2350",
    "waveshare-2.06",
    "waveshare-3.49-rp2350",
    "pancake",
    "v8",
    "crowpanel",
)


@dataclass(frozen=True)
class LiveSimulatorConfig:
    """Describe one isolated Picoware simulator launch."""

    target_kind: str = "Current design"
    target_name: str = ""
    board: str = "picocalc-pico2w"
    apps_source: str = ""
    watch_path: str = ""
    auto_reload: bool = True
    design_source: str = ""
    design_files: tuple[tuple[str, str | bytes], ...] = ()


def rgb565_frame_image(data: bytes, width: int, height: int) -> QImage:
    """Convert one little-endian RGB565 framebuffer into an owned image."""
    if width <= 0 or height <= 0 or len(data) != width * height * 2:
        return QImage()
    image = QImage(data, width, height, width * 2, QImage.Format.Format_RGB16)
    return image.copy()


class LiveSimulatorController(QObject):
    """Manage a headless Picoware simulator and its file bridge."""

    frame_ready = Signal(QImage)
    status_changed = Signal(str)
    error_changed = Signal(str)
    running_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None):
        """Initialize process state and periodic bridge polling."""
        super().__init__(parent)
        self._repository = Path(__file__).resolve().parents[1]
        self._temporary = QTemporaryDir(QDir.tempPath() + "/pico-graphics-live-XXXXXX")
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.started.connect(self._process_started)
        self._process.finished.connect(self._process_finished)
        self._process.errorOccurred.connect(self._process_error)
        self._process.readyReadStandardOutput.connect(self._drain_output)
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._poll_bridge)
        self._timer.start()
        self._config = LiveSimulatorConfig()
        self._pending_config: LiveSimulatorConfig | None = None
        self._frame_size = (0, 0)
        self._last_frame_stamp: tuple[int, int] | None = None
        self._last_status = ""
        self._last_error = ""
        self._last_frame = QImage()
        self._watch_stamp: int | None = None
        self._last_watch_check = 0.0
        self._output_tail = ""

    def start(self, config: LiveSimulatorConfig) -> None:
        """Launch or restart the simulator with one configuration."""
        if self.is_running():
            self._pending_config = config
            self._request_stop()
            return
        executable = shutil.which("micropython")
        if not executable:
            self.error_changed.emit("MicroPython is not installed or not in PATH.")
            return
        if not self._temporary.isValid():
            self.error_changed.emit("Could not create the live simulator workspace.")
            return
        self._config = config
        self._pending_config = None
        self._frame_size = (0, 0)
        self._last_frame_stamp = None
        self._last_status = ""
        self._last_error = ""
        self._output_tail = ""
        self._watch_stamp = self._source_stamp(config.watch_path)
        bridge = self.bridge_path
        sd_path = Path(self._temporary.path()) / "sd"
        arguments = [
            str(self._repository / "simulator" / "run.py"),
            "--headless",
            "--bridge",
            str(bridge),
            "--sd",
            str(sd_path),
            "--board",
            config.board,
            "--audio",
            "silent",
            "--network",
            "offline",
            "--speed",
            "real",
        ]
        apps_source = config.apps_source.strip()
        target_kind = config.target_kind
        target_name = config.target_name.strip()
        if config.design_files:
            apps_source = self._write_design_files(config.design_files)
            if not apps_source:
                return
            target_kind = "Application"
            target_name = "GuiDesignerLive"
        elif config.design_source:
            apps_source = self._write_design_app(config.design_source)
            if not apps_source:
                return
            target_kind = "Application"
            target_name = "GuiDesignerLive"
        if apps_source:
            arguments.extend(("--apps-source", apps_source))
        target_option = {
            "Application": "--app",
            "Game": "--game",
            "Library": "--open",
        }.get(target_kind)
        if target_option and target_name:
            arguments.extend((target_option, target_name))
        self._process.setWorkingDirectory(str(self._repository))
        self._process.setProgram(executable)
        self._process.setArguments(arguments)
        self.status_changed.emit("Starting live Picoware simulator...")
        self._process.start()

    def stop(self) -> None:
        """Request a clean simulator shutdown without blocking the GUI."""
        self._pending_config = None
        self._request_stop()

    def _request_stop(self) -> None:
        """Signal the current child process to exit through its bridge."""
        if not self.is_running():
            return
        self._append_text(self.frame_path.with_suffix(".rgb565.quit"), "quit\n")
        QTimer.singleShot(1200, self._force_stop)

    def restart(self) -> None:
        """Restart the simulator with its current configuration."""
        self.start(self._config)

    def shutdown(self) -> None:
        """Stop the child process before the editor is destroyed."""
        self._pending_config = None
        if not self.is_running():
            return
        self._append_text(self.frame_path.with_suffix(".rgb565.quit"), "quit\n")
        if not self._process.waitForFinished(1000):
            self._process.kill()
            self._process.waitForFinished(500)

    def is_running(self) -> bool:
        """Return whether the simulator child process is active."""
        return self._process.state() != QProcess.ProcessState.NotRunning

    def send_key(self, code: int, pressed: bool, repeat: bool = False) -> None:
        """Send one Pico keyboard event through the simulator bridge."""
        if not self.is_running():
            return
        action = "down" if pressed else "up"
        self._append_text(
            self.keys_path,
            f"{action} {int(code)} {1 if repeat else 0}\n",
        )

    def send_touch(self, x: int, y: int, gesture: int) -> None:
        """Send one touch point and gesture through the simulator bridge."""
        if not self.is_running():
            return
        self._append_text(self.keys_path, f"touch {x} {y} {gesture}\n")

    def send_control(self, command: str) -> None:
        """Send one supported runtime control command."""
        if self.is_running():
            self._append_text(
                self.frame_path.with_suffix(".rgb565.control"),
                command.strip() + "\n",
            )

    def current_frame(self) -> QImage:
        """Return an owned copy of the newest live framebuffer."""
        return self._last_frame.copy()

    @property
    def bridge_path(self) -> Path:
        """Return the temporary directory used for bridge files."""
        return Path(self._temporary.path()) / "bridge"

    @property
    def frame_path(self) -> Path:
        """Return the raw framebuffer path used by the simulator."""
        return self.bridge_path / "sim_frame.rgb565"

    @property
    def keys_path(self) -> Path:
        """Return the append-only simulator input path."""
        return self.bridge_path / "sim_keys.txt"

    def _process_started(self) -> None:
        """Report successful child process startup."""
        self.running_changed.emit(True)
        self.status_changed.emit("Simulator running; waiting for its first frame...")

    def _process_finished(self, exit_code: int, exit_status: object) -> None:
        """Report simulator exit and apply a queued restart when requested."""
        self.running_changed.emit(False)
        self._drain_output()
        output = self._output_tail
        if exit_code and not self._last_error:
            detail = output.strip().splitlines()[-1] if output.strip() else ""
            message = f"Simulator exited with code {exit_code}."
            if detail:
                message += f" {detail}"
            self.error_changed.emit(message)
        elif not self._pending_config:
            self.status_changed.emit("Live simulator stopped.")
        pending = self._pending_config
        self._pending_config = None
        if pending is not None:
            QTimer.singleShot(0, lambda: self.start(pending))

    def _process_error(self, error: object) -> None:
        """Report a Qt child-process launch or runtime error."""
        self.error_changed.emit(f"Could not run the live simulator: {error}")

    def _drain_output(self) -> None:
        """Drain child output continuously and retain a bounded diagnostic tail."""
        data = bytes(self._process.readAllStandardOutput()).decode("utf-8", "replace")
        if data:
            self._output_tail = (self._output_tail + data)[-8192:]

    def _force_stop(self) -> None:
        """Terminate a simulator that ignored its clean quit signal."""
        if not self.is_running():
            return
        self._process.terminate()
        QTimer.singleShot(500, self._kill_process)

    def _kill_process(self) -> None:
        """Kill the simulator only when graceful termination failed."""
        if self.is_running():
            self._process.kill()

    def _poll_bridge(self) -> None:
        """Poll framebuffer, status, errors, and watched source changes."""
        self._read_metadata()
        self._read_frame()
        self._read_bridge_text(
            self.frame_path.with_suffix(".rgb565.status"),
            "status",
        )
        self._read_bridge_text(
            self.frame_path.with_suffix(".rgb565.error"),
            "error",
        )
        self._poll_source_changes()

    def _read_metadata(self) -> None:
        """Read bridge dimensions once they become available."""
        if self._frame_size != (0, 0):
            return
        path = self.frame_path.with_suffix(".rgb565.meta")
        try:
            values = {
                key.strip(): value.strip()
                for key, value in (
                    line.split("=", 1)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if "=" in line
                )
            }
            self._frame_size = (int(values["width"]), int(values["height"]))
        except (OSError, KeyError, ValueError):
            return

    def _read_frame(self) -> None:
        """Decode and publish an atomically completed RGB565 frame."""
        width, height = self._frame_size
        if width <= 0 or height <= 0:
            return
        try:
            stats = self.frame_path.stat()
            stamp = (stats.st_mtime_ns, stats.st_size)
            if stamp == self._last_frame_stamp:
                return
            data = self.frame_path.read_bytes()
        except OSError:
            return
        image = rgb565_frame_image(data, width, height)
        if image.isNull():
            return
        self._last_frame_stamp = stamp
        self._last_frame = image
        self.frame_ready.emit(image.copy())

    def _read_bridge_text(self, path: Path, kind: str) -> None:
        """Publish one changed simulator status or error text file."""
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            return
        if kind == "status" and text != self._last_status:
            self._last_status = text
            self.status_changed.emit(text)
        elif kind == "error" and text != self._last_error:
            self._last_error = text
            self.error_changed.emit(text)

    def _poll_source_changes(self) -> None:
        """Restart after a watched Python source changes when enabled."""
        if not self.is_running() or not self._config.auto_reload:
            return
        now = time.monotonic()
        if now - self._last_watch_check < 1.0:
            return
        self._last_watch_check = now
        stamp = self._source_stamp(self._config.watch_path)
        if stamp is None or self._watch_stamp is None:
            self._watch_stamp = stamp
            return
        if stamp != self._watch_stamp:
            self._watch_stamp = stamp
            self.status_changed.emit("Source changed; restarting simulator...")
            self.restart()

    def _source_stamp(self, source: str) -> int | None:
        """Return a stable aggregate timestamp for watched Python files."""
        if not source:
            return None
        root = Path(source)
        paths = [root] if root.is_file() else sorted(root.rglob("*.py"))
        values: list[tuple[str, int, int]] = []
        for path in paths:
            try:
                stats = path.stat()
            except OSError:
                continue
            values.append((str(path), stats.st_mtime_ns, stats.st_size))
        return hash(tuple(values)) if values else None

    def _append_text(self, path: Path, text: str) -> None:
        """Append one command to a bridge channel."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(text)
        except OSError as error:
            self.error_changed.emit(f"Simulator bridge write failed: {error}")

    def _write_design_app(self, source: str) -> str:
        """Write the in-memory GUI design as an isolated temporary app."""
        apps_path = Path(self._temporary.path()) / "design_apps"
        target = apps_path / "GuiDesignerLive.py"
        try:
            apps_path.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8")
        except OSError as error:
            self.error_changed.emit(f"Could not prepare the live GUI design: {error}")
            return ""
        return str(apps_path)

    def _write_design_files(
        self,
        files: tuple[tuple[str, str | bytes], ...],
    ) -> str:
        """Stage one validated multi-file live app and binary resources."""
        apps_path = Path(self._temporary.path()) / "design_apps"
        names = {name for name, unused_content in files}
        if "GuiDesignerLive.py" not in names:
            self.error_changed.emit("Live design bundle has no GuiDesignerLive.py entrypoint.")
            return ""
        try:
            for name, content in files:
                relative = PurePosixPath(name)
                if (
                    not relative.parts
                    or relative.is_absolute()
                    or any(part in {"", ".", ".."} for part in relative.parts)
                ):
                    raise ValueError(f"Unsafe live design path: {name}")
                target = apps_path.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, bytes):
                    target.write_bytes(content)
                else:
                    target.write_text(content, encoding="utf-8")
        except (OSError, ValueError) as error:
            self.error_changed.emit(f"Could not prepare the live GUI design: {error}")
            return ""
        return str(apps_path)


class LiveSimulatorView(QWidget):
    """Display a live framebuffer and forward keyboard and touch input."""

    key_event = Signal(int, bool, bool)
    touch_event = Signal(int, int, int)

    def __init__(self, parent: QWidget | None = None):
        """Initialize an empty focusable live framebuffer view."""
        super().__init__(parent)
        self._image = QImage()
        self._pressed_codes: dict[int, int] = {}
        self.setMinimumSize(260, 220)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setToolTip("Click the live framebuffer to send Picoware keys.")

    def set_frame(self, image: QImage) -> None:
        """Display an owned copy of the latest simulator frame."""
        self._image = image.copy()
        self.update()

    def frame(self) -> QImage:
        """Return an owned copy of the displayed live frame."""
        return self._image.copy()

    def paintEvent(self, event) -> None:
        """Paint the framebuffer with nearest-neighbor aspect fitting."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#15181c"))
        if self._image.isNull():
            painter.setPen(QColor("#aab2bd"))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Start the live simulator\nto view its framebuffer",
            )
        else:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            target = self._frame_target()
            painter.drawImage(target, self._image)
            color = QColor("#00bfff") if self.hasFocus() else QColor("#59636f")
            painter.setPen(QPen(color, 2 if self.hasFocus() else 1))
            painter.drawRect(target.adjusted(0, 0, -1, -1))
        painter.end()

    def event(self, event) -> bool:
        """Reserve Picoware keys and keep Tab inside the live framebuffer."""
        if isinstance(event, QKeyEvent):
            event_type = event.type()
            code = self._key_code(event)
            if event_type == QEvent.Type.ShortcutOverride and code is not None:
                event.accept()
                return True
            if event.key() in {Qt.Key.Key_Tab, Qt.Key.Key_Backtab}:
                if event_type == QEvent.Type.KeyPress:
                    self.keyPressEvent(event)
                    return True
                if event_type == QEvent.Type.KeyRelease:
                    self.keyReleaseEvent(event)
                    return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Forward supported Qt key presses as Pico keyboard codes."""
        code = self._key_code(event)
        if code is None:
            super().keyPressEvent(event)
            return
        self._pressed_codes[event.key()] = code
        self.key_event.emit(code, True, event.isAutoRepeat())
        event.accept()

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        """Forward a Qt key release using its remembered Pico code."""
        code = self._pressed_codes.pop(event.key(), None)
        if code is None:
            super().keyReleaseEvent(event)
            return
        self.key_event.emit(code, False, False)
        event.accept()

    def focusInEvent(self, event) -> None:
        """Highlight the framebuffer while it owns Picoware key input."""
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        """Release held Picoware inputs when framebuffer focus is lost."""
        self._release_pressed_keys()
        self.touch_event.emit(0, 0, 0)
        self.update()
        super().focusOutEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Forward a left-button press as a simulator touch gesture."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        point = self._frame_point(event.position())
        if point is None:
            return
        self.touch_event.emit(round(point.x()), round(point.y()), 6)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Forward a held left-button move as a touch drag."""
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        point = self._frame_point(event.position())
        if point is not None:
            self.touch_event.emit(round(point.x()), round(point.y()), 6)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Release the active simulator touch point."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.touch_event.emit(0, 0, 0)
            event.accept()

    def _frame_target(self) -> QRectF:
        """Return the fitted framebuffer rectangle inside this widget."""
        if self._image.isNull():
            return QRectF()
        available = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        scale = min(
            available.width() / self._image.width(),
            available.height() / self._image.height(),
        )
        width = self._image.width() * scale
        height = self._image.height() * scale
        return QRectF(
            available.x() + (available.width() - width) / 2,
            available.y() + (available.height() - height) / 2,
            width,
            height,
        )

    def _frame_point(self, point: QPointF) -> QPointF | None:
        """Map a widget point into native simulator framebuffer coordinates."""
        target = self._frame_target()
        if target.isEmpty() or not target.contains(point):
            return None
        return QPointF(
            (point.x() - target.x()) * self._image.width() / target.width(),
            (point.y() - target.y()) * self._image.height() / target.height(),
        )

    def _key_code(self, event: QKeyEvent) -> int | None:
        """Map one Qt key event to the Picoware keyboard protocol."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() == Qt.Key.Key_Up:
                return 0xC2
            if event.key() == Qt.Key.Key_Down:
                return 0xC3
        mapping = {
            Qt.Key.Key_Up: 0xB5,
            Qt.Key.Key_Down: 0xB6,
            Qt.Key.Key_Left: 0xB4,
            Qt.Key.Key_Right: 0xB7,
            Qt.Key.Key_Escape: 0xB1,
            Qt.Key.Key_Pause: 0xD0,
            Qt.Key.Key_Insert: 0xD1,
            Qt.Key.Key_Backspace: 8,
            Qt.Key.Key_Return: 13,
            Qt.Key.Key_Enter: 13,
            Qt.Key.Key_Tab: 9,
            Qt.Key.Key_Backtab: 9,
            Qt.Key.Key_Home: 0xD2,
            Qt.Key.Key_Delete: 0xD4,
            Qt.Key.Key_End: 0xD5,
            Qt.Key.Key_PageUp: 0xD6,
            Qt.Key.Key_PageDown: 0xD7,
        }
        if event.key() in mapping:
            return mapping[event.key()]
        if Qt.Key.Key_F1 <= event.key() <= Qt.Key.Key_F9:
            return 0x81 + event.key() - Qt.Key.Key_F1
        if event.key() == Qt.Key.Key_F10:
            return 0x90
        text = event.text()
        return ord(text) if len(text) == 1 and ord(text) < 256 else None

    def _release_pressed_keys(self) -> None:
        """Release every Picoware key still held by the live view."""
        for code in set(self._pressed_codes.values()):
            self.key_event.emit(code, False, False)
        self._pressed_codes.clear()
