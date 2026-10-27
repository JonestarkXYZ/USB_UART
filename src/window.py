"""Interfaz de terminal UART; separada del transporte y del formato de bytes."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QPainter, QPixmap, QTextCharFormat, QTextCursor, QValidator
from PySide6.QtSerialPort import QSerialPortInfo
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFontDialog, QFrame, QGridLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QPlainTextEdit,
    QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from protocol import ENDINGS, MODES, StreamFormatter, encode_payload, grouped_input
from settings import Settings
from transport import SerialTransport


@dataclass
class Entry:
    """El historial conserva bytes; cambiar su apariencia no cambia lo transmitido."""

    direction: str
    timestamp: datetime
    payload: bytes
    message: str = ""


class ByteValidator(QValidator):
    """Impide introducir o pegar caracteres ajenos al formato activo."""

    def __init__(self, mode: str, parent=None):
        super().__init__(parent)
        self.mode = mode

    def validate(self, text: str, position: int):
        if len("".join(text.split())) > 8192 and self.mode in ("Binario", "Hexadecimal"):
            return QValidator.Invalid, text, position
        if self.mode == "ASCII":
            valid = all(32 <= ord(character) < 127 for character in text)
        elif self.mode in ("Binario", "Hexadecimal"):
            alphabet = "01" if self.mode == "Binario" else "0123456789abcdefABCDEF"
            valid = all(character in alphabet or character.isspace() for character in text)
        else:
            valid = True
        if not valid:
            return QValidator.Invalid, text, position
        # Intermediate permite escribir el siguiente byte sin permitir su envío incompleto.
        try:
            encode_payload(text, self.mode)
            state = QValidator.Acceptable
        except ValueError:
            state = QValidator.Intermediate
        return state, text.upper() if self.mode == "Hexadecimal" else text, position


class MainWindow(QMainWindow):
    """Ventana principal con persistencia diferida y recepción mediante señales Qt."""

    def __init__(self, root: Path):
        super().__init__()
        self.root = root
        self.settings = Settings(root / "config.json")
        self.config = self.settings.data
        self.records = deque(maxlen=self.config["terminal"]["history_limit"])
        self.rx_bytes = 0
        self.tx_bytes = 0
        self.drafts = {mode: "" for mode in MODES}
        self.active_tx_mode = self.config["terminal"]["tx_mode"]
        self.themes = self._load_themes()
        self.colors = self.themes.get(self.config["view"]["theme"], next(iter(self.themes.values())))
        self.formatters = {}
        self.transport = SerialTransport(self)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(350)
        self.save_timer.timeout.connect(self._save)
        self.setWindowTitle("USB UART · Terminal serie")
        self.setMinimumSize(860, 560)
        self.resize(self.config["window"]["width"], self.config["window"]["height"])
        self._build_ui()
        self._build_menus()
        self._apply_theme()
        self._apply_font()
        self._set_input_mode()
        self._reset_formatters()
        self._refresh_ports()
        self._connection_changed(False)
        self.transport.received.connect(lambda payload: self._record("RX", payload))
        self.transport.sent.connect(lambda payload: self._record("TX", payload))
        self.transport.failed.connect(self._report_error)
        self.transport.connected.connect(self._connection_changed)
        # Actualiza la lista sin interrumpir el puerto abierto ni elegir otro al desconectar.
        self.port_timer = QTimer(self)
        self.port_timer.setInterval(2000)
        self.port_timer.timeout.connect(self._refresh_ports)
        self.port_timer.start()
        if self.config["window"]["maximized"]:
            self.setWindowState(self.windowState() | Qt.WindowMaximized)
        if self.settings.error:
            QTimer.singleShot(0, lambda: self._report_error(self.settings.error))

    def _load_themes(self) -> dict:
        """Solo carga paletas JSON completas; evita inyectar estilos arbitrarios."""
        result = {}
        required = ("background", "panel", "input", "text", "muted", "border", "accent", "accent_text", "rx", "tx", "error")
        for path in sorted((self.root / "themes").glob("*.json")):
            try:
                theme = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(theme, dict) and isinstance(theme.get("name"), str) and all(
                    isinstance(theme.get(key), str) and QColor.isValidColorName(theme[key]) for key in required
                ):
                    result[theme["name"]] = theme
            except (OSError, ValueError):
                continue
        if not result:
            raise RuntimeError("No hay paletas válidas en la carpeta themes.")
        return result

    def _combo(self, values, selected) -> QComboBox:
        """Construye selectores no editables para parámetros de dominio cerrado."""
        combo = QComboBox()
        combo.addItems([str(value) for value in values])
        combo.setCurrentText(str(selected))
        return combo

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)
        header = QHBoxLayout()
        title = QLabel("USB UART")
        title.setObjectName("title")
        header.addWidget(title)
        header.addWidget(QLabel("TERMINAL SERIE"))
        header.addStretch()
        self.connection_label = QLabel("Desconectado")
        header.addWidget(self.connection_label)
        layout.addLayout(header)

        self.connection_panel = QFrame()
        self.connection_panel.setObjectName("panel")
        grid = QGridLayout(self.connection_panel)
        grid.setContentsMargins(14, 12, 14, 12)
        serial = self.config["serial"]
        terminal = self.config["terminal"]
        self.ports = QComboBox()
        self.ports.setMinimumWidth(210)
        self.ports.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.refresh_button = QToolButton()
        self.refresh_button.setToolTip("Actualizar puertos serie")
        self.refresh_button.setAccessibleName("Actualizar puertos")
        self.refresh_button.clicked.connect(self._refresh_ports)
        port_row = QHBoxLayout()
        port_row.addWidget(self.ports, 1)
        port_row.addWidget(self.refresh_button)
        grid.addWidget(QLabel("Puerto USB / COM"), 0, 0)
        grid.addLayout(port_row, 1, 0)
        self.baudrate = QComboBox()
        self.baudrate.setEditable(True)
        self.baudrate.addItems([str(value) for value in (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)])
        self.baudrate.setCurrentText(str(serial["baudrate"]))
        self.baudrate.setMinimumWidth(100)
        self.data_bits = self._combo((5, 6, 7, 8), serial["data_bits"])
        self.parity = self._combo(("Ninguna", "Par", "Impar", "Marca", "Espacio"), serial["parity"])
        self.stop_bits = self._combo(("1", "1.5", "2"), serial["stop_bits"])
        self.flow_control = self._combo(("Ninguno", "RTS/CTS", "XON/XOFF"), serial["flow_control"])
        for column, (label, widget) in enumerate((
            ("Baudios", self.baudrate), ("Bits", self.data_bits), ("Paridad", self.parity),
            ("Parada", self.stop_bits), ("Flujo", self.flow_control),
        ), 1):
            grid.addWidget(QLabel(label), 0, column)
            grid.addWidget(widget, 1, column)
            widget.currentTextChanged.connect(self._controls_changed)
        self.connect_button = QPushButton("Conectar")
        self.connect_button.setObjectName("primary")
        self.connect_button.clicked.connect(self._toggle_connection)
        grid.addWidget(self.connect_button, 1, 6)
        self.ports.currentIndexChanged.connect(self._controls_changed)
        self.rx_mode = self._combo(MODES, terminal["rx_mode"])
        self.timestamp = self._combo(("Ninguna", "RX", "TX", "Ambos"), terminal["timestamp"])
        self.line_ending = self._combo(ENDINGS, terminal["line_ending"])
        self.rx_mode.setToolTip("Representación de los bytes recibidos; también actualiza el historial")
        self.line_ending.setToolTip("Bytes añadidos al final de cada envío, en cualquier formato")
        options = QHBoxLayout()
        for label, widget in (("Recibir como", self.rx_mode), ("Marca de tiempo", self.timestamp), ("Final de envío", self.line_ending)):
            options.addWidget(QLabel(label))
            options.addWidget(widget)
            widget.currentTextChanged.connect(self._display_changed)
            options.addSpacing(12)
        options.addStretch()
        grid.addLayout(options, 2, 0, 1, 7)
        grid.setColumnStretch(0, 1)
        layout.addWidget(self.connection_panel)

        history_panel = QFrame()
        history_panel.setObjectName("panel")
        history_layout = QVBoxLayout(history_panel)
        history_layout.setContentsMargins(12, 8, 12, 12)
        history_header = QHBoxLayout()
        history_header.addWidget(QLabel("Historial"))
        legend = QLabel("RX  Recibido     TX  Enviado")
        legend.setObjectName("muted")
        history_header.addWidget(legend)
        history_header.addStretch()
        self.clear_button = QToolButton()
        self.clear_button.setToolTip("Eliminar historial y reiniciar contadores")
        self.clear_button.setAccessibleName("Eliminar historial")
        self.clear_button.clicked.connect(self._clear_history)
        history_header.addWidget(self.clear_button)
        history_layout.addLayout(history_header)
        self.history = QPlainTextEdit()
        self.history.setReadOnly(True)
        self.history.setUndoRedoEnabled(False)
        self.history.setPlaceholderText("Conecte un puerto para recibir datos. Los envíos y las recepciones aparecerán aquí.")
        self.history.document().setMaximumBlockCount(self.records.maxlen)
        history_layout.addWidget(self.history, 1)
        layout.addWidget(history_panel, 1)

        composer = QFrame()
        composer.setObjectName("panel")
        compose_layout = QVBoxLayout(composer)
        compose_layout.setContentsMargins(12, 10, 12, 10)
        row = QHBoxLayout()
        self.input = QLineEdit()
        # El límite contempla los separadores añadidos al agrupar los bytes.
        self.input.setMaxLength(32768)
        self.input.setClearButtonEnabled(False)
        self.input.setAccessibleName("Datos para enviar")
        self.input.textEdited.connect(self._group_input)
        self.input.returnPressed.connect(self._send)
        row.addWidget(self.input, 1)
        self.send_button = QPushButton("Enviar")
        self.send_button.setObjectName("primary")
        self.send_button.clicked.connect(self._send)
        row.addWidget(self.send_button)
        self.tx_mode = self._combo(MODES, terminal["tx_mode"])
        self.tx_mode.setToolTip("Formato de los bytes a enviar y de las filas TX")
        self.tx_mode.currentTextChanged.connect(self._set_input_mode)
        row.addWidget(self.tx_mode)
        compose_layout.addLayout(row)
        self.input_hint = QLabel()
        self.input_hint.setObjectName("muted")
        compose_layout.addWidget(self.input_hint)
        layout.addWidget(composer)
        self.message = QLabel("Listo. Seleccione el puerto y sus parámetros.")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.PlainText)
        self.statusBar().addWidget(self.message, 1)
        self.counters = QLabel("RX 0 B   ·   TX 0 B")
        self.statusBar().addPermanentWidget(self.counters)

    def _action(self, menu, text, callback, shortcut=None, checked=None):
        """Centraliza las acciones para conservar atajos y estado de los menús."""
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(shortcut)
        if checked is not None:
            action.setCheckable(True)
            action.setChecked(checked)
        action.triggered.connect(callback)
        menu.addAction(action)
        return action

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("Archivo")
        self._action(file_menu, "Guardar historial…", self._export_history, "Ctrl+S")
        self._action(file_menu, "Copiar selección del historial", self.history.copy, "Ctrl+Shift+C")
        self._action(file_menu, "Seleccionar historial completo", self.history.selectAll, "Ctrl+Shift+A")
        file_menu.addSeparator()
        self._action(file_menu, "Salir", self.close, "Ctrl+Q")
        settings_menu = self.menuBar().addMenu("Configuración")
        self._action(settings_menu, "Actualizar puertos", self._refresh_ports, "F5")
        self._action(settings_menu, "Límite del historial…", self._history_limit)
        self.console_action = self._action(settings_menu, "Mostrar consola al iniciar", self._console_changed, checked=self.config["show_console"])
        self._action(settings_menu, "Guardar configuración", self._save)
        view_menu = self.menuBar().addMenu("Vista")
        self._action(view_menu, "Fuente, estilo y tamaño…", self._choose_font, "Ctrl+Shift+F")
        self._action(view_menu, "Aumentar tamaño", lambda: self._zoom(1), "Ctrl++")
        self._action(view_menu, "Reducir tamaño", lambda: self._zoom(-1), "Ctrl+-")
        view_menu.addSeparator()
        self.wrap_action = self._action(view_menu, "Ajuste de línea", self._view_changed, checked=self.config["view"]["word_wrap"])
        self.scroll_action = self._action(view_menu, "Desplazamiento automático", self._view_changed, checked=self.config["view"]["autoscroll"])
        theme_menu = view_menu.addMenu("Tema de colores")
        self.theme_group = QActionGroup(self)
        self.theme_group.setExclusive(True)
        selected = self.config["view"]["theme"]
        if selected not in self.themes:
            selected = next(iter(self.themes))
            self.config["view"]["theme"] = selected
        for name in self.themes:
            action = self._action(theme_menu, name, lambda checked=False, value=name: self._change_theme(value), checked=name == selected)
            self.theme_group.addAction(action)
        view_menu.addSeparator()
        self._action(view_menu, "Eliminar historial", self._clear_history, "Ctrl+L")

    def _icon(self, name: str, primary: bool = False) -> QIcon:
        """Colorea SVG locales según la paleta; no usa iconos de sistema ni mapas de bits."""
        svg = (self.root / "icons" / f"{name}.svg").read_text(encoding="utf-8")
        color = self.colors["accent_text"] if primary else self.colors["text"]
        renderer = QSvgRenderer(QByteArray(svg.replace("currentColor", color).encode("utf-8")))
        pixmap = QPixmap(40, 40)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        return QIcon(pixmap)

    def _apply_theme(self) -> None:
        c = self.colors
        arrow = (self.root / "icons" / "chevron-down.svg").as_posix()
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background: {c['background']}; color: {c['text']}; }}
            QFrame#panel {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 9px; }}
            QFrame#panel QLabel {{ background: transparent; }}
            QLabel#title {{ font-size: 20pt; font-weight: 700; }}
            QLabel#muted {{ color: {c['muted']}; font-size: 9pt; }}
            QComboBox, QLineEdit, QSpinBox {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 5px; padding: 7px; selection-background-color: {c['accent']}; selection-color: {c['accent_text']}; }}
            QComboBox:focus, QLineEdit:focus {{ border-color: {c['accent']}; }}
            QComboBox {{ padding-right: 24px; }}
            QComboBox::drop-down {{ border: none; width: 22px; }}
            QComboBox::down-arrow {{ image: url("{arrow}"); width: 12px; height: 12px; }}
            QComboBox QAbstractItemView {{ background: {c['input']}; selection-background-color: {c['accent']}; selection-color: {c['accent_text']}; }}
            QPushButton, QToolButton {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 5px; padding: 7px 12px; }}
            QPushButton#primary {{ background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']}; font-weight: 600; }}
            QPushButton:hover, QToolButton:hover {{ border-color: {c['accent']}; }}
            QPushButton:disabled, QComboBox:disabled {{ color: {c['muted']}; background: {c['background']}; border-color: {c['border']}; }}
            QPushButton#primary:disabled {{ color: {c['muted']}; background: {c['background']}; border-color: {c['border']}; }}
            QPlainTextEdit {{ background: {c['input']}; border: none; padding: 8px; selection-background-color: {c['accent']}; selection-color: {c['accent_text']}; }}
            QMenuBar, QMenu {{ background: {c['panel']}; }}
            QMenuBar::item:selected, QMenu::item:selected {{ background: {c['accent']}; color: {c['accent_text']}; }}
            QMenu::item {{ padding: 6px 24px; }}
            QStatusBar {{ border-top: 1px solid {c['border']}; }}
            QStatusBar::item {{ border: none; }}
            QScrollBar {{ background: {c['panel']}; }}
            QScrollBar::handle {{ background: {c['border']}; border-radius: 4px; min-height: 24px; min-width: 24px; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
        """)
        for button, icon in ((self.refresh_button, "refresh"), (self.clear_button, "trash"), (self.send_button, "send"), (self.connect_button, "plug")):
            button.setIcon(self._icon(icon, button in (self.send_button, self.connect_button)))
            button.setIconSize(QSize(18, 18))
        self.setWindowIcon(self._icon("plug"))
        self._render_history()

    def _apply_font(self) -> None:
        view = self.config["view"]
        font = QFont(view["font_family"], view["font_size"])
        font.setBold(view["bold"])
        font.setItalic(view["italic"])
        self.history.setFont(font)
        self.input.setFont(font)
        self.history.setLineWrapMode(QPlainTextEdit.WidgetWidth if view["word_wrap"] else QPlainTextEdit.NoWrap)

    def _refresh_ports(self) -> None:
        if self.transport.port.isOpen():
            return
        selected = self.ports.currentData() or self.config["serial"]["port"]
        ports = sorted(QSerialPortInfo.availablePorts(), key=lambda port: port.portName())
        signature = [(port.portName(), port.description()) for port in ports]
        if getattr(self, "port_signature", None) == signature:
            return
        self.port_signature = signature
        self.ports.blockSignals(True)
        self.ports.clear()
        self.ports.addItem("Seleccione un puerto" if ports else "Sin puertos serie disponibles", "")
        for port in ports:
            self.ports.addItem(f"{port.portName()} · {port.description() or 'Puerto serie'}", port.portName())
            tooltip = f"{port.systemLocation()}\n{port.manufacturer()}\nSerie: {port.serialNumber() or '—'}"
            if port.hasVendorIdentifier() and port.hasProductIdentifier():
                tooltip += f"\nVID:PID {port.vendorIdentifier():04X}:{port.productIdentifier():04X}"
            self.ports.setItemData(self.ports.count() - 1, tooltip, Qt.ToolTipRole)
        index = self.ports.findData(selected)
        self.ports.setCurrentIndex(max(0, index))
        self.ports.blockSignals(False)
        self.connect_button.setEnabled(bool(self.ports.currentData()))

    def _controls_changed(self, *args) -> None:
        """No guarda un baudrate incompleto mientras el usuario lo está escribiendo."""
        serial = self.config["serial"]
        if self.ports.currentData():
            serial["port"] = self.ports.currentData()
        try:
            baudrate = int(self.baudrate.currentText())
            if 50 <= baudrate <= 4000000:
                serial["baudrate"] = baudrate
        except ValueError:
            pass
        serial.update(data_bits=int(self.data_bits.currentText()), parity=self.parity.currentText(), stop_bits=self.stop_bits.currentText(), flow_control=self.flow_control.currentText())
        self.connect_button.setEnabled(self.transport.port.isOpen() or bool(self.ports.currentData()))
        self.save_timer.start()

    def _toggle_connection(self) -> None:
        if self.transport.port.isOpen():
            self.transport.close()
            return
        if not self.ports.currentData():
            self._report_error("Seleccione un puerto serie disponible.")
            return
        try:
            baudrate = int(self.baudrate.currentText())
            if not 50 <= baudrate <= 4000000:
                raise ValueError
        except ValueError:
            self._report_error("Baudios debe ser un entero entre 50 y 4000000.")
            return
        self._controls_changed()
        self.transport.open(self.config["serial"])

    def _connection_changed(self, connected: bool) -> None:
        for widget in (self.ports, self.refresh_button, self.baudrate, self.data_bits, self.parity, self.stop_bits, self.flow_control):
            widget.setEnabled(not connected)
        self.connect_button.setText("Desconectar" if connected else "Conectar")
        self.send_button.setEnabled(connected)
        self.connection_label.setText(f"Conectado · {self.config['serial']['port']}" if connected else "Desconectado")
        self.connection_label.setStyleSheet(f"color: {self.colors['rx'] if connected else self.colors['muted']};")
        if connected:
            self._system(f"Conectado a {self.config['serial']['port']} · {self.config['serial']['baudrate']} baudios")
            self._set_message("Conectado. Enter o Enviar transmite los datos.")
            self.input.setFocus()
        elif getattr(self, "was_connected", False):
            self._system("Puerto desconectado")
            self._set_message("Puerto desconectado.")
        self.was_connected = connected

    def _set_input_mode(self, *args) -> None:
        mode = self.tx_mode.currentText()
        self.drafts[self.active_tx_mode] = self.input.text()
        self.active_tx_mode = mode
        self.input.setValidator(None)
        self.input.setText(self.drafts[mode])
        self.validator = ByteValidator(mode, self.input)
        self.input.setValidator(self.validator)
        hints = {
            "UTF-8": "Texto UTF-8 · Enter envía · Los finales de línea se configuran arriba.",
            "ASCII": "ASCII de 7 bits · Para tildes o ñ seleccione UTF-8 · Enter envía.",
            "Binario": "Solo 0 y 1 · Un byte por grupo de 8 bits · Ejemplo: 01001000 01101001",
            "Hexadecimal": "Solo 0–9 y A–F · Un byte por par · Ejemplo: 48 69 FF",
        }
        self.input_hint.setText(hints[mode])
        self.input.setPlaceholderText({"UTF-8": "Escriba un mensaje…", "ASCII": "Texto ASCII…", "Binario": "01001000 01101001", "Hexadecimal": "48 69 FF"}[mode])
        self.config["terminal"]["tx_mode"] = mode
        self._render_history()
        self.save_timer.start()

    def _group_input(self, text: str) -> None:
        mode = self.tx_mode.currentText()
        if mode not in ("Binario", "Hexadecimal"):
            return
        position = self.input.cursorPosition()
        before = sum(not character.isspace() for character in text[:position])
        grouped = grouped_input(text, mode)
        if grouped != text:
            self.input.setText(grouped)
            # Conserva el cursor al editar o pegar en mitad de la secuencia.
            width = 8 if mode == "Binario" else 2
            self.input.setCursorPosition(min(len(grouped), before + max(0, before - 1) // width))

    def _send(self) -> None:
        if not self.input.text():
            return
        try:
            payload = encode_payload(self.input.text(), self.tx_mode.currentText()) + ENDINGS[self.line_ending.currentText()]
        except ValueError as error:
            self._report_error(str(error))
            return
        if self.transport.send(payload):
            self.input.clear()
            self.drafts[self.active_tx_mode] = ""
            self._set_message(f"En cola TX: {len(payload)} bytes.")

    def _display_changed(self, *args) -> None:
        self.config["terminal"].update(rx_mode=self.rx_mode.currentText(), timestamp=self.timestamp.currentText(), line_ending=self.line_ending.currentText())
        self._render_history()
        self.save_timer.start()

    def _reset_formatters(self) -> None:
        self.formatters = {"RX": StreamFormatter(self.rx_mode.currentText()), "TX": StreamFormatter(self.tx_mode.currentText())}

    def _record(self, direction: str, payload: bytes) -> None:
        if direction == "RX":
            self.rx_bytes += len(payload)
        else:
            self.tx_bytes += len(payload)
        # Acota también cada registro: pegar texto largo no crea una fila ilimitada.
        for offset in range(0, len(payload), 4096):
            entry = Entry(direction, datetime.now().astimezone(), payload[offset:offset + 4096])
            self.records.append(entry)
            self._append_entry(entry)
        self.counters.setText(f"RX {self.rx_bytes:,} B   ·   TX {self.tx_bytes:,} B")

    def _system(self, message: str) -> None:
        entry = Entry("SYS", datetime.now().astimezone(), b"", message)
        self.records.append(entry)
        self._append_entry(entry)

    def _append_entry(self, entry: Entry) -> None:
        """Inserta texto plano con color; los datos UART nunca se interpretan como HTML."""
        if entry.direction == "SYS":
            # Una nueva conexión no puede completar el UTF-8 pendiente de la anterior.
            self._reset_formatters()
            content = entry.message
        else:
            content = self.formatters[entry.direction].format(entry.payload)
            if not content:
                content = "[secuencia UTF-8 pendiente]"
        stamp = self.timestamp.currentText()
        prefix = f"[{entry.timestamp:%H:%M:%S}.{entry.timestamp.microsecond // 1000:03d}] " if stamp == "Ambos" or stamp == entry.direction else ""
        line = f"{prefix}{entry.direction}  {content}"
        scrollbar = self.history.verticalScrollBar()
        previous_scroll = scrollbar.value()
        follow = self.config["view"]["autoscroll"] and previous_scroll >= scrollbar.maximum() - 2
        cursor = QTextCursor(self.history.document())
        cursor.movePosition(QTextCursor.End)
        if not self.history.document().isEmpty():
            cursor.insertBlock()
        char_format = QTextCharFormat()
        char_format.setForeground(QColor(self.colors.get(entry.direction.lower(), self.colors["muted"])))
        cursor.insertText(line, char_format)
        scrollbar.setValue(scrollbar.maximum() if follow else previous_scroll)

    def _render_history(self) -> None:
        if not hasattr(self, "history"):
            return
        scroll = self.history.verticalScrollBar()
        previous = scroll.value()
        at_end = previous >= scroll.maximum() - 2
        self.history.clear()
        self._reset_formatters()
        for entry in self.records:
            self._append_entry(entry)
        scroll.setValue(scroll.maximum() if at_end and self.config["view"]["autoscroll"] else previous)

    def _clear_history(self) -> None:
        self.records.clear()
        self.history.clear()
        self.rx_bytes = self.tx_bytes = 0
        self.counters.setText("RX 0 B   ·   TX 0 B")
        self._reset_formatters()
        self._set_message("Historial eliminado.")

    def _set_message(self, message: str, error: bool = False) -> None:
        self.message.setText(message)
        self.message.setStyleSheet(f"color: {self.colors['error'] if error else self.colors['muted']};")

    def _report_error(self, message: str) -> None:
        self._set_message(message, error=True)

    def _choose_font(self) -> None:
        accepted, font = QFontDialog.getFont(self.history.font(), self, "Fuente del historial y del cuadro de envío")
        if accepted:
            self.config["view"].update(font_family=font.family(), font_size=max(6, min(72, font.pointSize())), bold=font.bold(), italic=font.italic())
            self._apply_font()
            self.save_timer.start()

    def _zoom(self, delta: int) -> None:
        self.config["view"]["font_size"] = max(6, min(72, self.config["view"]["font_size"] + delta))
        self._apply_font()
        self.save_timer.start()

    def _view_changed(self) -> None:
        self.config["view"].update(word_wrap=self.wrap_action.isChecked(), autoscroll=self.scroll_action.isChecked())
        self._apply_font()
        self.save_timer.start()

    def _change_theme(self, name: str) -> None:
        self.colors = self.themes[name]
        self.config["view"]["theme"] = name
        self._apply_theme()
        self.connection_label.setStyleSheet(f"color: {self.colors['rx'] if self.transport.port.isOpen() else self.colors['muted']};")
        self.save_timer.start()

    def _console_changed(self) -> None:
        self.config["show_console"] = self.console_action.isChecked()
        self.save_timer.start()

    def _history_limit(self) -> None:
        value, accepted = QInputDialog.getInt(self, "Historial", "Máximo de registros (se descartan los más antiguos):", self.records.maxlen, 100, 10000)
        if accepted:
            self.config["terminal"]["history_limit"] = value
            self.records = deque(self.records, maxlen=value)
            self.history.document().setMaximumBlockCount(value)
            self._render_history()
            self.save_timer.start()

    def _export_history(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Guardar historial visible", "historial_uart.txt", "Texto UTF-8 (*.txt)")
        if path:
            try:
                Path(path).write_text(self.history.toPlainText() + "\n", encoding="utf-8")
                self._set_message(f"Historial guardado: {path}")
            except OSError as error:
                self._report_error(f"No se pudo guardar el historial: {error}")

    def _save(self) -> None:
        try:
            self.settings.save()
        except OSError as error:
            self._report_error(f"No se pudo guardar la configuración: {error}")

    def closeEvent(self, event) -> None:
        """Libera el puerto y guarda la geometría normal incluso si estaba maximizada."""
        self.port_timer.stop()
        self.save_timer.stop()
        self.transport.close()
        geometry = self.normalGeometry()
        self.config["window"].update(width=geometry.width(), height=geometry.height(), maximized=self.isMaximized())
        self._save()
        event.accept()
