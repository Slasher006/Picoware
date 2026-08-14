"""Image file chooser with an always-visible preview pane."""

from __future__ import annotations

from pathlib import Path
import threading

from dataclasses import dataclass

from PySide6.QtCore import (
    QObject,
    QRunnable,
    QSignalBlocker,
    QThreadPool,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QImage, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QProgressBar,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .canvas import pixel_art_image
from .model import PixelArt
from .reference import image_to_pixel_art, prepare_reference_image
from .ui_help import install_widget_tooltips


class ImagePreviewLabel(QWidget):
    """Render an image over a checkerboard without smoothing pixel art."""

    def __init__(self, parent: QWidget | None = None):
        """Create an empty preview surface."""
        super().__init__(parent)
        self._image = QImage()
        self._empty_message = "Select an image to preview"
        self.setMinimumSize(280, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Selected image preview")

    def set_image(
        self,
        image: QImage | None,
        empty_message: str = "Select an image to preview",
    ) -> None:
        """Set the source image displayed by the preview."""
        self._image = image.copy() if image is not None else QImage()
        self._empty_message = empty_message
        self.update()

    def image(self) -> QImage:
        """Return a copy of the currently previewed image."""
        return self._image.copy()

    def paintEvent(self, event) -> None:  # noqa: N802
        """Paint a checkerboard and a centered, aspect-fitted image."""
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.white)
        tile = 12
        light = Qt.GlobalColor.lightGray
        for y in range(0, self.height(), tile):
            for x in range(0, self.width(), tile):
                if (x // tile + y // tile) % 2:
                    painter.fillRect(x, y, tile, tile, light)
        if self._image.isNull():
            painter.setPen(Qt.GlobalColor.darkGray)
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                self._empty_message,
            )
            return
        available = self.rect().adjusted(12, 12, -12, -12)
        pixmap = QPixmap.fromImage(self._image).scaled(
            available.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        x = available.x() + (available.width() - pixmap.width()) // 2
        y = available.y() + (available.height() - pixmap.height()) // 2
        painter.drawPixmap(x, y, pixmap)


class ImageOpenDialog(QFileDialog):
    """Choose one image while showing pixels and useful file metadata."""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        directory: str,
        name_filter: str,
        *,
        accept_label: str = "Open",
    ):
        """Build a resizable non-native image chooser with a preview pane."""
        super().__init__(parent, title, directory, name_filter)
        self.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        self.setFileMode(QFileDialog.FileMode.ExistingFile)
        self.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        self.setViewMode(QFileDialog.ViewMode.Detail)
        self.setLabelText(QFileDialog.DialogLabel.Accept, accept_label)
        self.resize(1040, 650)

        self.preview_panel = QFrame(self)
        self.preview_panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel_layout = QVBoxLayout(self.preview_panel)
        heading = QLabel("Image preview")
        heading.setStyleSheet("font-weight: 600; font-size: 14px;")
        self.preview_label = ImagePreviewLabel()
        self.name_label = QLabel("No image selected")
        self.name_label.setWordWrap(True)
        self.name_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.details_label = QLabel("Select an image to see its details.")
        self.details_label.setWordWrap(True)
        self.details_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        panel_layout.addWidget(heading)
        panel_layout.addWidget(self.preview_label, 1)
        panel_layout.addWidget(self.name_label)
        panel_layout.addWidget(self.details_label)

        dialog_layout = self.layout()
        if isinstance(dialog_layout, QGridLayout):
            preview_column = dialog_layout.columnCount()
            dialog_layout.addWidget(
                self.preview_panel,
                0,
                preview_column,
                max(1, dialog_layout.rowCount()),
                1,
            )
            dialog_layout.setColumnStretch(preview_column, 1)

        self.currentChanged.connect(self.update_preview)

    def update_preview(self, filename: str) -> None:
        """Refresh the preview and metadata for the highlighted file."""
        path = Path(filename)
        if not path.is_file():
            self._clear_preview("Select an image to see its details.")
            return
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            self._clear_preview("No image preview is available for this file.")
            self.name_label.setText(path.name)
            return
        self.preview_label.set_image(image)
        self.name_label.setText(path.name)
        frame_count = reader.imageCount()
        frames = (
            f"{frame_count} frames"
            if frame_count > 1
            else "Animated image"
            if reader.supportsAnimation()
            else "1 frame"
        )
        image_format = bytes(reader.format()).decode("ascii", errors="replace").upper()
        image_format = (
            image_format or path.suffix.removeprefix(".").upper() or "Unknown"
        )
        alpha = "alpha" if image.hasAlphaChannel() else "opaque"
        try:
            file_size = _format_file_size(path.stat().st_size)
        except OSError:
            file_size = "unknown size"
        self.details_label.setText(
            f"{image.width()} x {image.height()} px  |  {image_format}  |  "
            f"{frames}\n{file_size}  |  {alpha}"
        )

    def _clear_preview(self, message: str) -> None:
        """Reset the preview pane to an informative empty state."""
        self.preview_label.set_image(None)
        self.name_label.setText("No image selected")
        self.details_label.setText(message)


@dataclass(frozen=True)
class LibraryImageImportResult:
    """Return the exact reviewed library image conversion."""

    name: str
    frames: tuple[PixelArt, ...]
    durations: tuple[int, ...]
    width: int
    height: int
    color_count: int
    dither: bool
    interval_ms: int


class _ImportConversionSignals(QObject):
    """Publish background conversion results back to the dialog thread."""

    finished = Signal(int, object)
    progress = Signal(int, int)
    failed = Signal(int, str)


class _ImportConversionJob(QRunnable):
    """Convert one or more import frames without blocking Qt's GUI thread."""

    def __init__(
        self,
        generation: int,
        indexed_images: tuple[tuple[int, QImage], ...],
        width: int,
        height: int,
        colors: int,
        dither: bool,
        cancelled: threading.Event | None = None,
    ):
        super().__init__()
        self.generation = generation
        self.indexed_images = indexed_images
        self.width = width
        self.height = height
        self.colors = colors
        self.dither = dither
        self.cancelled = cancelled or threading.Event()
        self.signals = _ImportConversionSignals()

    def run(self) -> None:
        """Convert requested frames and report progress cooperatively."""
        try:
            results: list[tuple[int, PixelArt]] = []
            total = len(self.indexed_images)
            for completed, (index, image) in enumerate(self.indexed_images, 1):
                if self.cancelled.is_set():
                    self.signals.finished.emit(self.generation, ())
                    return
                frame = image_to_pixel_art(
                    prepare_reference_image(image, self.width, self.height, "contain"),
                    self.width,
                    self.height,
                    self.colors,
                    self.dither,
                )
                results.append((index, frame))
                self.signals.progress.emit(completed, total)
            self.signals.finished.emit(
                self.generation,
                tuple(results) if not self.cancelled.is_set() else (),
            )
        except Exception as error:  # pragma: no cover - defensive worker boundary
            self.signals.failed.emit(self.generation, str(error))


class LibraryImageImportDialog(QDialog):
    """Review source images beside the exact RGB565 library result."""

    def __init__(
        self,
        images: list[QImage] | tuple[QImage, ...],
        name: str,
        parent: QWidget | None = None,
        *,
        replace: bool = False,
        color_count: int = 16,
        dither: bool = False,
        interval_ms: int = 250,
        original_durations: tuple[int, ...] = (),
    ):
        """Build local conversion controls and source/result previews."""
        super().__init__(parent)
        if not images or images[0].isNull():
            raise ValueError("At least one readable image frame is required")
        self.images = tuple(image.copy() for image in images)
        self.original_durations = (
            tuple(original_durations)
            if len(original_durations) == len(self.images)
            and all(value > 0 for value in original_durations)
            else ()
        )
        self._replace = replace
        self._syncing_dimensions = False
        self._conversion_generation = 0
        self._conversion_jobs: set[_ImportConversionJob] = set()
        self._frame_cache: dict[tuple[tuple[object, ...], int], PixelArt] = {}
        self._preview_cancel: threading.Event | None = None
        self._batch_cancel: threading.Event | None = None
        self._converted_cache: (
            tuple[tuple[object, ...], tuple[PixelArt, ...]] | None
        ) = None
        self.setWindowTitle(
            "Review library asset replacement"
            if replace
            else "Review library image import"
        )
        self.resize(1040, 720)
        root = QVBoxLayout(self)

        explanation = QLabel(
            "Review the exact RGB565 pixels before the Personal Asset Library is "
            + ("replaced." if replace else "updated.")
        )
        explanation.setWordWrap(True)
        root.addWidget(explanation)

        controls = QFormLayout()
        self.name_edit = QLineEdit(name.strip() or "Untitled Asset")
        self.name_edit.setReadOnly(replace)
        controls.addRow("Asset name", self.name_edit)
        dimension_row = QHBoxLayout()
        source_width = images[0].width()
        source_height = images[0].height()
        scale = min(1.0, 320 / source_width, 320 / source_height)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 320)
        self.width_spin.setValue(max(1, round(source_width * scale)))
        self.height_spin = QSpinBox()
        self.height_spin.setRange(1, 320)
        self.height_spin.setValue(max(1, round(source_height * scale)))
        self.aspect_check = QCheckBox("Keep source aspect ratio")
        self.aspect_check.setChecked(True)
        dimension_row.addWidget(QLabel("W"))
        dimension_row.addWidget(self.width_spin)
        dimension_row.addWidget(QLabel("H"))
        dimension_row.addWidget(self.height_spin)
        dimension_row.addWidget(self.aspect_check)
        dimension_row.addStretch(1)
        controls.addRow("Target size", dimension_row)
        conversion_row = QHBoxLayout()
        self.colors_spin = QSpinBox()
        self.colors_spin.setRange(2, 256)
        self.colors_spin.setValue(max(2, min(256, color_count)))
        self.dither_check = QCheckBox("Floyd-Steinberg dithering")
        self.dither_check.setChecked(dither)
        conversion_row.addWidget(QLabel("Palette colors"))
        conversion_row.addWidget(self.colors_spin)
        conversion_row.addWidget(self.dither_check)
        conversion_row.addStretch(1)
        controls.addRow("RGB565 conversion", conversion_row)
        timing_row = QHBoxLayout()
        self.timing_mode_combo = QComboBox()
        if self.original_durations:
            self.timing_mode_combo.addItem("Keep original frame timing", "original")
        self.timing_mode_combo.addItem("Use one interval for every frame", "uniform")
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(40, 5000)
        self.interval_spin.setValue(max(40, min(5000, interval_ms)))
        self.interval_spin.setSuffix(" ms per frame")
        timing_row.addWidget(self.timing_mode_combo, 1)
        timing_row.addWidget(self.interval_spin)
        self.interval_spin.setEnabled(
            len(self.images) > 1 and self.timing_mode_combo.currentData() == "uniform"
        )
        controls.addRow("Animation timing", timing_row)
        root.addLayout(controls)

        previews = QHBoxLayout()
        source_panel, self.source_preview = self._preview_panel("Source image")
        converted_panel, self.converted_preview = self._preview_panel(
            "Stored RGB565 result"
        )
        previews.addWidget(source_panel, 1)
        previews.addWidget(converted_panel, 1)
        root.addLayout(previews, 1)

        frame_row = QHBoxLayout()
        self.previous_button = QPushButton("Previous frame")
        self.frame_spin = QSpinBox()
        self.frame_spin.setRange(1, len(self.images))
        self.frame_spin.setSuffix(f" of {len(self.images)}")
        self.next_button = QPushButton("Next frame")
        frame_row.addWidget(self.previous_button)
        frame_row.addWidget(self.frame_spin, 1)
        frame_row.addWidget(self.next_button)
        root.addLayout(frame_row)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)
        self.progress_row = QWidget()
        progress_layout = QHBoxLayout(self.progress_row)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        self.progress_bar = QProgressBar()
        self.cancel_conversion_button = QPushButton("Cancel conversion")
        progress_layout.addWidget(self.progress_bar, 1)
        progress_layout.addWidget(self.cancel_conversion_button)
        self.progress_row.setVisible(False)
        root.addWidget(self.progress_row)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Replace asset" if replace else "Import to library"
        )
        self.buttons.accepted.connect(self._accept_if_valid)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(100)
        self.preview_timer.timeout.connect(self._refresh_preview)
        self.width_spin.valueChanged.connect(self._width_changed)
        self.height_spin.valueChanged.connect(self._height_changed)
        self.colors_spin.valueChanged.connect(self._schedule_preview)
        self.dither_check.toggled.connect(self._schedule_preview)
        self.frame_spin.valueChanged.connect(self._schedule_preview)
        self.timing_mode_combo.currentIndexChanged.connect(self._timing_mode_changed)
        self.name_edit.textChanged.connect(self._update_accept_state)
        self.previous_button.clicked.connect(
            lambda: self.frame_spin.setValue(max(1, self.frame_spin.value() - 1))
        )
        self.next_button.clicked.connect(
            lambda: self.frame_spin.setValue(
                min(len(self.images), self.frame_spin.value() + 1)
            )
        )
        self.cancel_conversion_button.clicked.connect(self._cancel_batch_conversion)
        self._refresh_preview()
        install_widget_tooltips(self)

    def reject(self) -> None:
        """Cancel pending workers before closing the review without importing."""
        if self._batch_cancel is not None:
            self._batch_cancel.set()
        if self._preview_cancel is not None:
            self._preview_cancel.set()
        self._conversion_generation += 1
        super().reject()

    @staticmethod
    def _preview_panel(title: str) -> tuple[QFrame, ImagePreviewLabel]:
        """Build one labelled preview surface."""
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(panel)
        heading = QLabel(title)
        heading.setStyleSheet("font-weight: 600;")
        preview = ImagePreviewLabel()
        preview.setMinimumSize(260, 260)
        layout.addWidget(heading)
        layout.addWidget(preview, 1)
        return panel, preview

    def _width_changed(self, width: int) -> None:
        """Keep the first source frame aspect ratio when requested."""
        if self._syncing_dimensions:
            return
        if self.aspect_check.isChecked():
            self._syncing_dimensions = True
            with QSignalBlocker(self.height_spin):
                height = round(width * self.images[0].height() / self.images[0].width())
                self.height_spin.setValue(max(1, min(320, height)))
            self._syncing_dimensions = False
        self._schedule_preview()

    def _height_changed(self, height: int) -> None:
        """Keep the first source frame aspect ratio when requested."""
        if self._syncing_dimensions:
            return
        if self.aspect_check.isChecked():
            self._syncing_dimensions = True
            with QSignalBlocker(self.width_spin):
                width = round(height * self.images[0].width() / self.images[0].height())
                self.width_spin.setValue(max(1, min(320, width)))
            self._syncing_dimensions = False
        self._schedule_preview()

    def _schedule_preview(self, unused: object = None) -> None:
        """Debounce palette conversion while controls are changing."""
        del unused
        if self._preview_cancel is not None:
            self._preview_cancel.set()
        self._conversion_generation += 1
        self._converted_cache = None
        self._frame_cache.clear()
        self.preview_timer.start()

    def _timing_mode_changed(self, unused: object = None) -> None:
        """Enable the uniform interval only when it will be stored."""
        del unused
        self.interval_spin.setEnabled(
            len(self.images) > 1 and self.timing_mode_combo.currentData() == "uniform"
        )
        self._refresh_summary()

    def _conversion_key(self) -> tuple[object, ...]:
        """Return the settings that determine converted pixels."""
        return (
            self.width_spin.value(),
            self.height_spin.value(),
            self.colors_spin.value(),
            self.dither_check.isChecked(),
        )

    def converted_frames(self) -> tuple[PixelArt, ...]:
        """Return every frame converted with the reviewed settings."""
        key = self._conversion_key()
        if self._converted_cache is not None and self._converted_cache[0] == key:
            return tuple(frame.copy() for frame in self._converted_cache[1])
        width, height, colors, dither = key
        frames = tuple(
            image_to_pixel_art(
                prepare_reference_image(image, width, height, "contain"),
                width,
                height,
                colors,
                dither,
            )
            for image in self.images
        )
        self._converted_cache = key, frames
        return tuple(frame.copy() for frame in frames)

    def _refresh_preview(self) -> None:
        """Render the selected source frame and its exact stored result."""
        index = self.frame_spin.value() - 1
        self.source_preview.set_image(self.images[index])
        cache_key = (self._conversion_key(), index)
        cached = self._frame_cache.get(cache_key)
        if cached is not None:
            self.converted_preview.set_image(pixel_art_image(cached))
        else:
            self.converted_preview.set_image(
                None,
                f"Converting frame {index + 1} of {len(self.images)}…",
            )
            self._start_preview_conversion(index)
        self._refresh_summary(cached)
        self.previous_button.setEnabled(index > 0)
        self.next_button.setEnabled(index + 1 < len(self.images))
        self._update_accept_state()

    def _refresh_summary(self, frame: PixelArt | None = None) -> None:
        """Describe source, conversion, and timing without doing conversion work."""
        source = self.images[0]
        resized = (source.width(), source.height()) != (
            self.width_spin.value(),
            self.height_spin.value(),
        )
        size_note = " · resized" if resized else " · original dimensions"
        timing = self._selected_durations()
        timing_text = ""
        if len(self.images) > 1:
            timing_text = (
                f" · original timing {min(timing)}–{max(timing)} ms"
                if self.timing_mode_combo.currentData() == "original"
                else f" · {self.interval_spin.value()} ms per frame"
            )
        target_width = frame.width if frame is not None else self.width_spin.value()
        target_height = frame.height if frame is not None else self.height_spin.value()
        self.summary_label.setText(
            f"{len(self.images)} frame(s) · {source.width()} x {source.height()} source "
            f"→ {target_width} x {target_height} RGB565{size_note} · "
            f"{self.colors_spin.value()} colors · "
            f"{'dithered' if self.dither_check.isChecked() else 'no dithering'}"
            + timing_text
        )

    def _start_preview_conversion(self, index: int) -> None:
        """Convert only the visible frame in the background."""
        generation = self._conversion_generation
        width, height, colors, dither = self._conversion_key()
        self._preview_cancel = threading.Event()
        job = _ImportConversionJob(
            generation,
            ((index, self.images[index].copy()),),
            width,
            height,
            colors,
            dither,
            self._preview_cancel,
        )
        self._conversion_jobs.add(job)
        job.signals.finished.connect(
            lambda result_generation, results, worker=job: self._preview_ready(
                worker, result_generation, results
            )
        )
        job.signals.failed.connect(
            lambda result_generation, message, worker=job: self._conversion_failed(
                worker, result_generation, message
            )
        )
        QThreadPool.globalInstance().start(job)

    def _preview_ready(
        self,
        job: _ImportConversionJob,
        generation: int,
        results: tuple[tuple[int, PixelArt], ...],
    ) -> None:
        """Publish a current background preview and ignore stale generations."""
        self._conversion_jobs.discard(job)
        if generation != self._conversion_generation or not results:
            return
        self._preview_cancel = None
        index, frame = results[0]
        self._frame_cache[(self._conversion_key(), index)] = frame
        if index == self.frame_spin.value() - 1:
            self.converted_preview.set_image(pixel_art_image(frame))
            self._refresh_summary(frame)

    def _start_batch_conversion(self) -> None:
        """Convert all frames in the background before accepting the import."""
        self.preview_timer.stop()
        if self._preview_cancel is not None:
            self._preview_cancel.set()
            self._preview_cancel = None
        self._conversion_generation += 1
        generation = self._conversion_generation
        width, height, colors, dither = self._conversion_key()
        self._batch_cancel = threading.Event()
        job = _ImportConversionJob(
            generation,
            tuple((index, image.copy()) for index, image in enumerate(self.images)),
            width,
            height,
            colors,
            dither,
            self._batch_cancel,
        )
        self._conversion_jobs.add(job)
        job.signals.progress.connect(self._batch_progress)
        job.signals.finished.connect(
            lambda result_generation, results, worker=job: self._batch_ready(
                worker, result_generation, results
            )
        )
        job.signals.failed.connect(
            lambda result_generation, message, worker=job: self._conversion_failed(
                worker, result_generation, message
            )
        )
        self.progress_bar.setRange(0, len(self.images))
        self.progress_bar.setValue(0)
        self.progress_row.setVisible(True)
        self._set_conversion_controls_enabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.summary_label.setText("Preparing every frame for the exact RGB565 import…")
        QThreadPool.globalInstance().start(job)

    def _batch_progress(self, completed: int, total: int) -> None:
        """Update bounded import progress from the worker."""
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(completed)

    def _batch_ready(
        self,
        job: _ImportConversionJob,
        generation: int,
        results: tuple[tuple[int, PixelArt], ...],
    ) -> None:
        """Cache the complete reviewed result and finish the dialog."""
        self._conversion_jobs.discard(job)
        if generation != self._conversion_generation:
            return
        ordered = tuple(frame for unused_index, frame in sorted(results))
        self._converted_cache = self._conversion_key(), ordered
        self._batch_cancel = None
        self.progress_row.setVisible(False)
        self.accept()

    def _cancel_batch_conversion(self) -> None:
        """Cancel a pending full conversion without discarding dialog settings."""
        if self._batch_cancel is not None:
            self._batch_cancel.set()
        self._conversion_generation += 1
        self._batch_cancel = None
        self.progress_row.setVisible(False)
        self.converted_preview.set_image(None, "Conversion failed")
        self._set_conversion_controls_enabled(True)
        self._update_accept_state()
        self._refresh_preview()

    def _conversion_failed(
        self, job: _ImportConversionJob, generation: int, message: str
    ) -> None:
        """Recover the dialog after a worker conversion failure."""
        self._conversion_jobs.discard(job)
        if generation != self._conversion_generation:
            return
        self._batch_cancel = None
        self.progress_row.setVisible(False)
        self._set_conversion_controls_enabled(True)
        self.summary_label.setText(f"Conversion failed: {message}")
        self._update_accept_state()

    def _set_conversion_controls_enabled(self, enabled: bool) -> None:
        """Freeze conversion inputs while the accepted result is prepared."""
        self.name_edit.setEnabled(enabled)
        self.width_spin.setEnabled(enabled)
        self.height_spin.setEnabled(enabled)
        self.aspect_check.setEnabled(enabled)
        self.colors_spin.setEnabled(enabled)
        self.dither_check.setEnabled(enabled)
        self.timing_mode_combo.setEnabled(enabled and len(self.images) > 1)
        self.interval_spin.setEnabled(
            enabled
            and len(self.images) > 1
            and self.timing_mode_combo.currentData() == "uniform"
        )
        self.frame_spin.setEnabled(enabled)
        self.previous_button.setEnabled(enabled and self.frame_spin.value() > 1)
        self.next_button.setEnabled(
            enabled and self.frame_spin.value() < len(self.images)
        )

    def _update_accept_state(self, unused: object = None) -> None:
        """Require a non-empty name before an automatic library write."""
        del unused
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            bool(self.name_edit.text().strip())
        )

    def _accept_if_valid(self) -> None:
        """Finish pending preview work and accept only a complete conversion."""
        if not self.name_edit.text().strip():
            return
        if (
            self._converted_cache is not None
            and self._converted_cache[0] == self._conversion_key()
        ):
            self.accept()
            return
        self._start_batch_conversion()

    def _selected_durations(self) -> tuple[int, ...]:
        """Return the original or explicitly uniform reviewed frame timing."""
        if len(self.images) < 2:
            return ()
        if self.timing_mode_combo.currentData() == "original":
            return self.original_durations
        return (self.interval_spin.value(),) * len(self.images)

    def result_value(self) -> LibraryImageImportResult:
        """Return the complete reviewed conversion after acceptance."""
        frames = self.converted_frames()
        durations = self._selected_durations()
        return LibraryImageImportResult(
            self.name_edit.text().strip(),
            frames,
            durations,
            self.width_spin.value(),
            self.height_spin.value(),
            self.colors_spin.value(),
            self.dither_check.isChecked(),
            self.interval_spin.value(),
        )


def get_open_image_filename(
    parent: QWidget | None,
    title: str,
    directory: str,
    name_filter: str,
    *,
    accept_label: str = "Open",
) -> tuple[str, str]:
    """Run the preview chooser and return a QFileDialog-compatible result."""
    dialog = ImageOpenDialog(
        parent,
        title,
        directory,
        name_filter,
        accept_label=accept_label,
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return "", ""
    selected = dialog.selectedFiles()
    return (selected[0] if selected else "", dialog.selectedNameFilter())


def _format_file_size(size: int) -> str:
    """Format a byte count compactly for preview metadata."""
    value = float(max(0, size))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0 or unit == "GB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} GB"
