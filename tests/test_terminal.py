"""Pruebas de protocolo, configuración y UI sin abrir puertos físicos."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Fuerza Qt sin pantalla únicamente en el proceso de pruebas.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QValidator
from PySide6.QtSerialPort import QSerialPort
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from protocol import StreamFormatter, encode_payload, grouped_input
from settings import Settings
from transport import SerialTransport
from window import ByteValidator, MainWindow


class ProtocolTests(unittest.TestCase):
    """Verifica los bytes reales, incluidas entradas inválidas y UTF-8 fragmentado."""

    def test_text_encodings(self):
        self.assertEqual(encode_payload("Hola ñ", "UTF-8"), b"Hola \xc3\xb1")
        self.assertEqual(encode_payload("Hola", "ASCII"), b"Hola")
        with self.assertRaises(ValueError):
            encode_payload("ñ", "ASCII")

    def test_binary_and_hex_are_bytes(self):
        self.assertEqual(encode_payload("01001000 01101001", "Binario"), b"Hi")
        self.assertEqual(encode_payload("4869ff", "Hexadecimal"), b"Hi\xff")
        self.assertEqual(grouped_input("aabbcc", "Hexadecimal"), "AA BB CC")
        self.assertEqual(grouped_input("0100100001101001", "Binario"), "01001000 01101001")

    def test_reject_incomplete_and_invalid_bytes(self):
        for text, mode in (("1", "Binario"), ("01000002", "Binario"), ("01 000000", "Binario"), ("F", "Hexadecimal"), ("GG", "Hexadecimal"), ("A B", "Hexadecimal")):
            with self.subTest(text=text), self.assertRaises(ValueError):
                encode_payload(text, mode)

    def test_utf8_split_and_invalid_bytes_are_visible(self):
        formatter = StreamFormatter("UTF-8")
        self.assertEqual(formatter.format(b"\xc3"), "")
        self.assertEqual(formatter.format(b"\xb1\r\n\x00\xff"), "ñ\\r\\n\\x00\\xff")

    def test_representations(self):
        self.assertEqual(StreamFormatter("Binario").format(b"\x00\xff"), "00000000 11111111")
        self.assertEqual(StreamFormatter("Hexadecimal").format(b"\x00\xff"), "00 FF")

    def test_unicode_separators_do_not_create_history_lines(self):
        self.assertEqual(StreamFormatter("UTF-8").format("a\u2028b\u2029c".encode()), "a\\u2028b\\u2029c")


class SettingsTests(unittest.TestCase):
    """Un JSON inválido no se destruye; las claves ajenas sobreviven al guardado."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "config.json"

    def tearDown(self):
        self.temporary.cleanup()

    def test_roundtrip_and_unknown_keys(self):
        self.path.write_text(json.dumps({"show_console": True, "custom": 42, "serial": {"baudrate": 9600, "custom": "x"}}), encoding="utf-8")
        settings = Settings(self.path)
        settings.data["view"]["font_size"] = 14
        settings.save()
        restored = Settings(self.path)
        self.assertEqual(restored.data["serial"]["baudrate"], 9600)
        self.assertEqual(restored.data["view"]["font_size"], 14)
        self.assertEqual(restored.data["custom"], 42)
        self.assertEqual(restored.data["serial"]["custom"], "x")
        self.assertTrue(restored.data["show_console"])

    def test_corrupt_file_is_preserved(self):
        self.path.write_text("{incorrecto", encoding="utf-8")
        settings = Settings(self.path)
        with self.assertRaises(OSError):
            settings.save()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{incorrecto")

    def test_validation(self):
        self.path.write_text(json.dumps({"show_console": "false", "serial": {"baudrate": -1, "parity": []}, "view": {"font_size": 100000}, "terminal": {"rx_mode": None}}), encoding="utf-8")
        settings = Settings(self.path)
        self.assertFalse(settings.data["show_console"])
        self.assertEqual(settings.data["serial"]["baudrate"], 115200)
        self.assertEqual(settings.data["serial"]["parity"], "Ninguna")
        self.assertEqual(settings.data["view"]["font_size"], 11)


class FakePort(QObject):
    """Doble de controlador: permite confirmar escrituras parciales sin hardware."""

    bytesWritten = Signal(int)
    errorOccurred = Signal(QSerialPort.SerialPortError)

    def __init__(self):
        super().__init__()
        self.opened = True
        self.output = []

    def isOpen(self):
        return self.opened

    def write(self, payload):
        self.output.append(payload)
        return len(payload)

    def close(self):
        self.opened = False

    def errorString(self):
        return "USB retirado"


class TransportTests(unittest.TestCase):
    """TX exige confirmación; la desconexión invalida lo pendiente."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.transport = SerialTransport()
        self.fake = FakePort()
        self.transport.port = self.fake
        self.fake.bytesWritten.connect(self.transport._written)
        self.fake.errorOccurred.connect(self.transport._error)
        self.sent = []
        self.errors = []
        self.transport.sent.connect(self.sent.append)
        self.transport.failed.connect(self.errors.append)

    def test_partial_confirmations_and_fifo(self):
        self.assertTrue(self.transport.send(b"abc"))
        self.assertTrue(self.transport.send(b"de"))
        self.assertEqual(self.sent, [])
        self.fake.bytesWritten.emit(2)
        self.assertEqual(self.sent, [])
        self.fake.bytesWritten.emit(2)
        self.assertEqual(self.sent, [b"abc"])
        self.fake.bytesWritten.emit(1)
        self.assertEqual(self.sent, [b"abc", b"de"])
        self.assertEqual(self.transport.pending_bytes, 0)

    def test_disconnect_does_not_confirm_pending(self):
        self.transport.send(b"abc")
        self.fake.errorOccurred.emit(QSerialPort.ResourceError)
        self.assertFalse(self.fake.isOpen())
        self.assertEqual(self.transport.pending_bytes, 0)
        self.assertEqual(self.sent, [])
        self.assertTrue(any("sin confirmar" in message for message in self.errors))

    def test_queue_limit_and_disconnected_send(self):
        self.assertFalse(self.transport.send(b"x" * (SerialTransport.MAX_PENDING + 1)))
        self.fake.close()
        self.assertFalse(self.transport.send(b"x"))
        self.assertEqual(self.fake.output, [])

    def test_open_missing_device_fails_cleanly(self):
        # Usa QSerialPort real contra un nombre inexistente, sin tocar un COM válido.
        transport = SerialTransport()
        settings = {"port": "__usb_uart_missing_test_port__", "baudrate": 115200, "data_bits": 8, "parity": "Ninguna", "stop_bits": "1", "flow_control": "Ninguno"}
        errors = []
        transport.failed.connect(errors.append)
        self.assertFalse(transport.open(settings))
        self.assertFalse(transport.port.isOpen())
        self.assertEqual(len(errors), 1)


class WindowTests(unittest.TestCase):
    """Ejercita teclado, historial y persistencia en una copia aislada de recursos."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for folder in ("icons", "themes"):
            shutil.copytree(ROOT / folder, self.root / folder)
        self.window = MainWindow(self.root)
        self.window.port_timer.stop()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()
        self.temporary.cleanup()

    def test_binary_validation_and_keyboard_grouping(self):
        self.window.tx_mode.setCurrentText("Binario")
        self.window.input.setFocus()
        QTest.keyClicks(self.window.input, "0100100001101001")
        self.assertEqual(self.window.input.text(), "01001000 01101001")
        QTest.keyClicks(self.window.input, "29z")
        self.assertEqual(self.window.input.text(), "01001000 01101001")

    def test_hex_uppercase_and_enter_send(self):
        self.window.tx_mode.setCurrentText("Hexadecimal")
        QTest.keyClicks(self.window.input, "aaff01")
        self.assertEqual(self.window.input.text(), "AA FF 01")
        self.window.line_ending.setCurrentText("CRLF")
        with patch.object(self.window.transport, "send", return_value=True) as send:
            QTest.keyClick(self.window.input, Qt.Key_Return)
            send.assert_called_once_with(b"\xaa\xff\x01\r\n")
        self.assertEqual(self.window.input.text(), "")

    def test_incomplete_byte_does_not_send(self):
        self.window.tx_mode.setCurrentText("Binario")
        self.window.input.setText("101")
        with patch.object(self.window.transport, "send") as send:
            self.window._send()
            send.assert_not_called()
        self.assertIn("Complete", self.window.message.text())

    def test_view_changes_reinterpret_history_and_clear(self):
        self.window._record("RX", b"Hi\x00")
        self.assertIn("Hi\\x00", self.window.history.toPlainText())
        self.window.rx_mode.setCurrentText("Hexadecimal")
        self.assertIn("48 69 00", self.window.history.toPlainText())
        self.window.timestamp.setCurrentText("Ninguna")
        self.assertTrue(self.window.history.toPlainText().startswith("RX  "))
        self.window.clear_button.click()
        self.assertEqual(self.window.history.toPlainText(), "")
        self.assertEqual(self.window.rx_bytes, 0)

    def test_bounded_history(self):
        for _ in range(1003):
            self.window._record("RX", b"x")
        self.assertEqual(len(self.window.records), 1000)
        self.assertEqual(self.window.history.document().blockCount(), 1000)

    def test_fragmented_utf8_and_plain_text(self):
        self.window._record("RX", b"\xc3")
        self.window._record("RX", b"\xb1<b>texto</b>")
        self.assertIn("ñ<b>texto</b>", self.window.history.toPlainText())

    def test_font_theme_and_persistence(self):
        self.window._zoom(3)
        self.window._change_theme("Claro")
        self.assertEqual(self.window.history.font().pointSize(), 14)
        self.window._save()
        data = Settings(self.root / "config.json").data
        self.assertEqual(data["view"]["font_size"], 14)
        self.assertEqual(data["view"]["theme"], "Claro")

    def test_validator_rejects_invalid_paste(self):
        self.assertEqual(ByteValidator("Hexadecimal").validate("AA GG", 5)[0], QValidator.Invalid)
        self.assertEqual(ByteValidator("Binario").validate("01012", 5)[0], QValidator.Invalid)

    def test_paste_and_input_limit(self):
        self.window.tx_mode.setCurrentText("Hexadecimal")
        self.app.clipboard().setText("aabbccdd")
        self.window.input.paste()
        self.assertEqual(self.window.input.text(), "AA BB CC DD")
        self.app.clipboard().setText("GG")
        self.window.input.paste()
        self.assertEqual(self.window.input.text(), "AA BB CC DD")
        self.assertEqual(ByteValidator("Hexadecimal").validate("F" * 8193, 8193)[0], QValidator.Invalid)

    def test_send_history_arrows_and_draft(self):
        # Las flechas recorren en orden, se detienen en los extremos y no transmiten.
        with patch.object(self.window.transport, "send", return_value=True) as send:
            for text in ("primero", "segundo", "tercero"):
                self.window.input.setText(text)
                QTest.keyClick(self.window.input, Qt.Key_Return)
            self.window.input.setText("borrador")
            for expected in ("tercero", "segundo", "primero", "primero"):
                QTest.keyClick(self.window.input, Qt.Key_Up)
                self.assertEqual(self.window.input.text(), expected)
            for expected in ("segundo", "tercero", "borrador", "borrador"):
                QTest.keyClick(self.window.input, Qt.Key_Down)
                self.assertEqual(self.window.input.text(), expected)
            self.assertEqual(send.call_count, 3)

    def test_recalled_entry_can_be_edited_and_sent(self):
        with patch.object(self.window.transport, "send", return_value=True) as send:
            self.window.input.setText("LED")
            self.window._send()
            QTest.keyClick(self.window.input, Qt.Key_Up)
            QTest.keyClicks(self.window.input, " ON")
            QTest.keyClick(self.window.input, Qt.Key_Return)
            self.assertEqual(send.call_args.args[0], b"LED ON")
            QTest.keyClick(self.window.input, Qt.Key_Up)
            self.assertEqual(self.window.input.text(), "LED ON")
            QTest.keyClick(self.window.input, Qt.Key_Up)
            self.assertEqual(self.window.input.text(), "LED")

    def test_send_history_is_per_mode_and_survives_log_clear(self):
        with patch.object(self.window.transport, "send", return_value=True):
            self.window.input.setText("mensaje")
            self.window._send()
            self.window.tx_mode.setCurrentText("Hexadecimal")
            self.window.input.setText("AA FF")
            self.window._send()
        self.window._clear_history()
        QTest.keyClick(self.window.input, Qt.Key_Up)
        self.assertEqual(self.window.input.text(), "AA FF")
        self.window.tx_mode.setCurrentText("UTF-8")
        QTest.keyClick(self.window.input, Qt.Key_Up)
        self.assertEqual(self.window.input.text(), "mensaje")

    def test_send_history_skips_failed_send_and_is_bounded(self):
        self.window.input.setText("sin conexión")
        with patch.object(self.window.transport, "send", return_value=False):
            self.window._send()
        QTest.keyClick(self.window.input, Qt.Key_Up)
        self.assertEqual(self.window.input.text(), "sin conexión")
        self.assertFalse(self.window.send_history["UTF-8"])
        with patch.object(self.window.transport, "send", return_value=True):
            for index in range(103):
                self.window.input.setText(str(index))
                self.window._send()
        self.assertEqual(len(self.window.send_history["UTF-8"]), 100)
        self.assertEqual(self.window.send_history["UTF-8"][0], "3")


if __name__ == "__main__":
    unittest.main()
