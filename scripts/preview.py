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
        window.interpret_button.setChecked(True)
        window._record("TX", b"$STATE\r\n")
        window._record("RX", b"$STATE LISTO\r\n")
        window._record("TX", b"$STATS\r\n")
        window._record("RX", b"$STATS TX=0 TX_BYTES=0 RX=0 RX_BYTES=0 TX_ERR=2 INIT_ERR=52 UART")
        window._record("RX", b"_DROP=0 OUT_DROP=0 TX_QUEUE=0\r\n")
        window._record("RX", b"Respuesta larga: " + b"TX_BYTES=12345 RX_BYTES=67890 UART_DROP=0 OUT_DROP=0 " * 5 + b"\r\n")
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
