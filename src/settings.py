"""Configuración única y escritura atómica, conservando claves del lanzador."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

from protocol import ENDINGS, MODES


DEFAULTS = {
    "show_console": False,
    "debug": False,
    "serial": {"port": "", "baudrate": 115200, "data_bits": 8, "parity": "Ninguna", "stop_bits": "1", "flow_control": "Ninguno"},
    "terminal": {"tx_mode": "UTF-8", "rx_mode": "UTF-8", "line_ending": "Ninguno", "timestamp": "Ambos", "history_limit": 1000, "interpret_controls": False},
    "view": {"theme": "Oscuro", "font_family": "Consolas", "font_size": 11, "bold": False, "italic": False, "word_wrap": True, "autoscroll": True},
    "window": {"width": 1180, "height": 760, "maximized": False},
}


class Settings:
    """Valida valores conocidos y no sobrescribe un archivo JSON ilegible."""

    def __init__(self, path: Path):
        self.path = path
        self.data = copy.deepcopy(DEFAULTS)
        self.error = ""
        self.writable = True
        try:
            saved = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(saved, dict):
                raise ValueError("La raíz de config.json debe ser un objeto.")
            for key, value in saved.items():
                if key in self.data and isinstance(self.data[key], dict):
                    if isinstance(value, dict):
                        self.data[key].update(value)
                else:
                    self.data[key] = value
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as error:
            self.error = f"No se pudo leer config.json: {error}. Se usan valores predeterminados sin sobrescribirlo."
            self.writable = False
        self._validate()

    def _validate(self) -> None:
        """Descarta valores fuera de rango antes de entregarlos a Qt."""
        for key in ("show_console", "debug"):
            if type(self.data[key]) is not bool:
                self.data[key] = DEFAULTS[key]
        choices = {
            ("serial", "data_bits"): (5, 6, 7, 8),
            ("serial", "parity"): ("Ninguna", "Par", "Impar", "Marca", "Espacio"),
            ("serial", "stop_bits"): ("1", "1.5", "2"),
            ("serial", "flow_control"): ("Ninguno", "RTS/CTS", "XON/XOFF"),
            ("terminal", "tx_mode"): MODES,
            ("terminal", "rx_mode"): MODES,
            ("terminal", "line_ending"): tuple(ENDINGS),
            ("terminal", "timestamp"): ("Ninguna", "RX", "TX", "Ambos"),
        }
        for section, defaults in DEFAULTS.items():
            if not isinstance(defaults, dict):
                continue
            for key, default in defaults.items():
                value = self.data[section][key]
                if type(value) is not type(default) or (
                    (section, key) in choices and value not in choices[section, key]
                ):
                    self.data[section][key] = default
        for section, key, low, high in (
            ("serial", "baudrate", 50, 4000000),
            ("terminal", "history_limit", 100, 10000),
            ("view", "font_size", 6, 72),
            ("window", "width", 780, 7680),
            ("window", "height", 500, 4320),
        ):
            if not low <= self.data[section][key] <= high:
                self.data[section][key] = DEFAULTS[section][key]

    def save(self) -> None:
        """Reemplaza solo tras escribir el JSON completo, incluso en OneDrive."""
        if not self.writable:
            raise OSError(self.error)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(self.data, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
