"""Genera capturas Qt de ambos temas con datos de ejemplo, sin tocar config.json."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from window import MainWindow


def main() -> None:
    """Usa recursos reales y una configuración temporal para no alterar preferencias."""
    app = QApplication([])
    app.setStyle("Fusion")
    # El plugin offscreen de Windows no descubre las fuentes del sistema solo.
    if os.name == "nt":
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "segoeuib.ttf", "consola.ttf", "consolab.ttf"):
            QFontDatabase.addApplicationFont(str(fonts / name))
        app.setFont(QFont("Segoe UI", 10))
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for folder in ("icons", "themes"):
            shutil.copytree(ROOT / folder, root / folder)
        window = MainWindow(root)
        window.port_timer.stop()
        window._system("Vista previa · datos de ejemplo, sin conexión física")
        window._record("TX", b"status\r\n")
        window._record("RX", "Dispositivo listo · temperatura: 24.6 °C\r\n".encode("utf-8"))
        window._record("TX", b"LED ON\r\n")
        window._record("RX", b"OK\r\n")
        window.show()
        for name in ("Oscuro", "Claro"):
            window._change_theme(name)
            app.processEvents()
            path = ROOT / "docs" / f"preview_{name.lower()}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            if not window.grab().save(str(path)):
                raise OSError(f"No se pudo guardar {path}")
            print(path)
        window.close()


if __name__ == "__main__":
    main()
