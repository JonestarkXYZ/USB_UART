"""Punto de entrada de la terminal USB-UART."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from window import MainWindow


def main() -> int:
    """Inicia Qt; las rutas se resuelven desde el proyecto, no desde la consola."""
    application = QApplication(sys.argv)
    application.setApplicationName("USB UART")
    application.setStyle("Fusion")
    window = MainWindow(Path(__file__).resolve().parent.parent)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
