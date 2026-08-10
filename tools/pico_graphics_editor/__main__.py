"""Launch the Pico Graphics Editor application."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from .window import MainWindow


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        description="Edit Python renderer graphics with a mouse-driven pixel canvas."
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        help="Python file or project folder to scan on startup",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Create the Qt application and run its event loop."""
    arguments = build_parser().parse_args(argv)
    QCoreApplication.setOrganizationName("Picoware")
    QCoreApplication.setApplicationName("Pico Graphics Editor")
    application = QApplication(sys.argv[:1])
    application.setStyle("Fusion")
    window = MainWindow()
    window.show()
    if arguments.path is not None:
        window.open_path(arguments.path)
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
