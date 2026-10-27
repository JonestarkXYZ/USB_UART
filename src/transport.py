"""Transporte UART asíncrono de Qt, sin hilos ni esperas bloqueantes."""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import QIODevice, QObject, QTimer, Signal
from PySide6.QtSerialPort import QSerialPort


class SerialTransport(QObject):
    """Notifica TX al confirmar el controlador los bytes, no al pulsar Enviar."""

    received = Signal(bytes)
    sent = Signal(bytes)
    failed = Signal(str)
    connected = Signal(bool)
    MAX_PENDING = 262144

    def __init__(self, parent=None):
        super().__init__(parent)
        self.port = QSerialPort(self)
        self.port.setReadBufferSize(262144)
        self.pending = deque()
        self.pending_bytes = 0
        self.opening = False
        self.port.readyRead.connect(self._read)
        self.port.bytesWritten.connect(self._written)
        self.port.errorOccurred.connect(self._error)

    def open(self, settings: dict) -> bool:
        """Verifica todos los parámetros; el controlador puede rechazar combinaciones."""
        self.close()
        self.opening = True
        self.port.setPortName(settings["port"])
        parity = {"Ninguna": QSerialPort.NoParity, "Par": QSerialPort.EvenParity, "Impar": QSerialPort.OddParity, "Marca": QSerialPort.MarkParity, "Espacio": QSerialPort.SpaceParity}
        stop = {"1": QSerialPort.OneStop, "1.5": QSerialPort.OneAndHalfStop, "2": QSerialPort.TwoStop}
        flow = {"Ninguno": QSerialPort.NoFlowControl, "RTS/CTS": QSerialPort.HardwareControl, "XON/XOFF": QSerialPort.SoftwareControl}
        valid = all((
            self.port.setBaudRate(settings["baudrate"]),
            self.port.setDataBits(QSerialPort.DataBits(settings["data_bits"])),
            self.port.setParity(parity[settings["parity"]]),
            self.port.setStopBits(stop[settings["stop_bits"]]),
            self.port.setFlowControl(flow[settings["flow_control"]]),
        ))
        opened = valid and self.port.open(QIODevice.ReadWrite)
        self.opening = False
        if not opened:
            self.failed.emit(f"No se pudo abrir {settings['port']}: {self.port.errorString()}")
        self.connected.emit(bool(opened))
        return bool(opened)

    def close(self) -> None:
        """No reconecta automáticamente ni presenta bytes pendientes como enviados."""
        discarded = self.pending_bytes
        self.pending.clear()
        self.pending_bytes = 0
        if self.port.isOpen():
            self.port.close()
        self.connected.emit(False)
        if discarded:
            self.failed.emit(f"Conexión cerrada con {discarded} bytes TX sin confirmar; podrían haberse enviado parcialmente.")

    def send(self, payload: bytes) -> bool:
        """Limita la cola para impedir crecimiento de memoria con control de flujo."""
        if not payload:
            self.failed.emit("El envío debe contener al menos un byte.")
            return False
        if not self.port.isOpen():
            self.failed.emit("Conecte un puerto antes de enviar.")
            return False
        if self.pending_bytes + len(payload) > self.MAX_PENDING:
            self.failed.emit("Cola TX llena (256 KiB). Espere a que termine el envío.")
            return False
        self.pending.append([payload, 0])
        self.pending_bytes += len(payload)
        accepted = self.port.write(payload)
        if accepted != len(payload):
            self.close()
            self.failed.emit("El controlador no aceptó el envío completo. Revise la conexión.")
            return False
        return True

    def _written(self, count: int) -> None:
        """Una señal puede confirmar parte de un envío o varios envíos juntos."""
        while count > 0 and self.pending:
            entry = self.pending[0]
            confirmed = min(count, len(entry[0]) - entry[1])
            entry[1] += confirmed
            self.pending_bytes -= confirmed
            count -= confirmed
            if entry[1] == len(entry[0]):
                self.pending.popleft()
                self.sent.emit(entry[0])

    def _read(self) -> None:
        """Cede al bucle de eventos entre bloques para mantener fluida la UI."""
        if not self.port.isOpen():
            return
        payload = bytes(self.port.read(4096))
        if payload:
            self.received.emit(payload)
        if self.port.bytesAvailable():
            QTimer.singleShot(0, self._read)

    def _error(self, error: QSerialPort.SerialPortError) -> None:
        """Cierra al perder el USB o ante fallos de E/S; la reapertura es manual."""
        if self.opening or not self.port.isOpen() or error == QSerialPort.NoError:
            return
        if error in (QSerialPort.ResourceError, QSerialPort.DeviceNotFoundError, QSerialPort.PermissionError, QSerialPort.ReadError, QSerialPort.WriteError):
            message = self.port.errorString()
            if self.pending_bytes:
                message += f"; {self.pending_bytes} bytes TX sin confirmar, con posible envío parcial"
            self.close()
            self.failed.emit(f"Error UART: {message}")
