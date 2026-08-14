"""Tests for the image chooser preview pane."""

# ruff: noqa: E402

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPOSITORY_PATH = Path(__file__).resolve().parents[2]
if str(REPOSITORY_PATH) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_PATH))

from PySide6.QtGui import QColor, QImage
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication, QDialog

from pico_graphics_editor.image_dialog import (
    ImageOpenDialog,
    LibraryImageImportDialog,
    _format_file_size,
)
from pico_graphics_editor.ui_help import (
    INTERACTIVE_WIDGETS,
    _is_generated_help,
    _split_tooltip,
)


class ImageOpenDialogTests(unittest.TestCase):
    """Verify visible preview content without opening a desktop dialog."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create the shared offscreen Qt application."""
        cls.application = QApplication.instance() or QApplication([])

    def test_selected_image_updates_preview_and_metadata(self) -> None:
        """Show the actual image, dimensions, format, size, and alpha state."""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "transparent-icon.png"
            image = QImage(18, 12, QImage.Format.Format_RGBA8888)
            image.fill(QColor(255, 0, 0, 120))
            self.assertTrue(image.save(str(path), "PNG"))
            dialog = ImageOpenDialog(
                None,
                "Import image",
                temporary,
                "Images (*.png)",
                accept_label="Import",
            )

            dialog.update_preview(str(path))

            self.assertEqual(dialog.name_label.text(), "transparent-icon.png")
            self.assertEqual(
                (
                    dialog.preview_label.image().width(),
                    dialog.preview_label.image().height(),
                ),
                (18, 12),
            )
            self.assertIn("18 x 12 px", dialog.details_label.text())
            self.assertIn("PNG", dialog.details_label.text())
            self.assertIn("1 frame", dialog.details_label.text())
            self.assertIn("alpha", dialog.details_label.text())
            self.assertEqual(
                dialog.labelText(dialog.DialogLabel.Accept),
                "Import",
            )
            dialog.close()

    def test_directory_or_invalid_file_clears_stale_preview(self) -> None:
        """Do not leave the previous thumbnail visible for a folder selection."""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "icon.bmp"
            image = QImage(4, 3, QImage.Format.Format_RGB32)
            image.fill(QColor("blue"))
            self.assertTrue(image.save(str(path), "BMP"))
            dialog = ImageOpenDialog(None, "Open image", temporary, "Images (*.bmp)")
            dialog.update_preview(str(path))
            self.assertFalse(dialog.preview_label.image().isNull())

            dialog.update_preview(temporary)

            self.assertTrue(dialog.preview_label.image().isNull())
            self.assertEqual(dialog.name_label.text(), "No image selected")
            dialog.close()

    def test_file_size_format_is_compact(self) -> None:
        """Keep file metadata readable across common image sizes."""
        self.assertEqual(_format_file_size(512), "512 B")
        self.assertEqual(_format_file_size(1536), "1.5 KB")
        self.assertEqual(_format_file_size(2 * 1024 * 1024), "2.0 MB")

    def test_library_review_returns_exact_visible_rgb565_conversion(self) -> None:
        """Keep sizing, palette, dithering, timing, and result pixels explicit."""
        first = QImage(20, 10, QImage.Format.Format_RGBA8888)
        first.fill(QColor(255, 0, 0, 255))
        second = QImage(20, 10, QImage.Format.Format_RGBA8888)
        second.fill(QColor(0, 255, 0, 128))
        dialog = LibraryImageImportDialog(
            [first, second],
            "Reviewed Animation",
            color_count=8,
            interval_ms=140,
        )

        dialog.width_spin.setValue(10)
        dialog.colors_spin.setValue(4)
        dialog.dither_check.setChecked(True)
        dialog.interval_spin.setValue(180)
        dialog._refresh_preview()
        QThreadPool.globalInstance().waitForDone(5000)
        self.application.processEvents()
        result = dialog.result_value()

        self.assertEqual(result.name, "Reviewed Animation")
        self.assertEqual((result.width, result.height), (10, 5))
        self.assertEqual(len(result.frames), 2)
        self.assertEqual(result.durations, (180, 180))
        self.assertEqual(result.color_count, 4)
        self.assertTrue(result.dither)
        self.assertEqual(
            dialog.converted_preview.image().pixelColor(0, 0),
            QColor(255, 0, 0, 255),
        )
        self.assertIn("10 x 5 RGB565", dialog.summary_label.text())
        dialog.close()

    def test_library_review_preserves_original_animation_timing_by_default(
        self,
    ) -> None:
        """Keep variable source delays unless the user explicitly chooses uniform."""
        frames = []
        for color in (QColor("red"), QColor("green"), QColor("blue")):
            image = QImage(2, 2, QImage.Format.Format_ARGB32)
            image.fill(color)
            frames.append(image)
        dialog = LibraryImageImportDialog(
            frames,
            "Timed",
            original_durations=(80, 140, 260),
            interval_ms=200,
        )

        self.assertEqual(dialog.timing_mode_combo.currentData(), "original")
        self.assertFalse(dialog.interval_spin.isEnabled())
        self.assertEqual(dialog.result_value().durations, (80, 140, 260))
        self.assertIn("original timing 80–260 ms", dialog.summary_label.text())

        dialog.timing_mode_combo.setCurrentIndex(
            dialog.timing_mode_combo.findData("uniform")
        )
        dialog.interval_spin.setValue(190)
        self.assertTrue(dialog.interval_spin.isEnabled())
        self.assertEqual(dialog.result_value().durations, (190, 190, 190))
        dialog.close()

    def test_library_review_fields_explain_conversion_effects(self) -> None:
        """Cover every owned import field with semantic non-fallback help."""
        image = QImage(8, 6, QImage.Format.Format_ARGB32)
        image.fill(QColor("red"))
        dialog = LibraryImageImportDialog([image], "Badge")
        controls = [
            value
            for value in vars(dialog).values()
            if isinstance(value, INTERACTIVE_WIDGETS)
        ]

        failures = []
        for control in controls:
            tooltip = control.toolTip().strip()
            unused_description, example = _split_tooltip(tooltip)
            if not example or _is_generated_help(tooltip):
                failures.append((control.objectName(), tooltip))

        self.assertGreaterEqual(len(controls), 12)
        self.assertEqual(failures, [])
        self.assertIn("gradients", dialog.dither_check.toolTip())
        self.assertIn("per-frame timing", dialog.timing_mode_combo.toolTip())
        QThreadPool.globalInstance().waitForDone(5000)
        self.application.processEvents()
        dialog.close()

    def test_accept_converts_all_frames_without_blocking_the_dialog_thread(
        self,
    ) -> None:
        """Move the expensive final conversion off the interactive Qt thread."""
        frames = []
        for color in (QColor("red"), QColor("green"), QColor("blue")):
            image = QImage(2, 2, QImage.Format.Format_ARGB32)
            image.fill(color)
            frames.append(image)
        dialog = LibraryImageImportDialog(frames, "Background")
        QThreadPool.globalInstance().waitForDone(5000)
        self.application.processEvents()
        dialog._converted_cache = None

        started = time.perf_counter()
        dialog._accept_if_valid()
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.1)
        self.assertFalse(dialog.progress_row.isHidden())
        QThreadPool.globalInstance().waitForDone(5000)
        self.application.processEvents()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)


if __name__ == "__main__":
    unittest.main()
