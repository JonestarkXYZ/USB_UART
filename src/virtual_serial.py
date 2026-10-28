"""Puerto TEST interno con la interfaz de E/S utilizada de QSerialPort."""

from __future__ import annotations

import math

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtSerialPort import QSerialPort


class VirtualSerialPort(QObject):
    """Simula un dispositivo de eco; nunca registra un puerto COM en el sistema."""

    readyRead = Signal()
    bytesWritten = Signal(int)
    errorOccurred = Signal(QSerialPort.SerialPortError)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._opened = False
        self._session = 0
        self._name = "TEST"
        self._error = "Sin errores"
        self._baudrate = 115200
        self._data_bits = 8
        self._parity = QSerialPort.NoParity
        self._stop_bits = 1.0
        self._flow = QSerialPort.NoFlowControl
        self._read_limit = 262144
        self._tx = bytearray()
        self._rx = bytearray()
        self._scheduled = 0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._transfer)

    def setPortName(self, name: str) -> None:
        self._name = name

    def setReadBufferSize(self, size: int) -> None:
        self._read_limit = max(0, size)

    def setBaudRate(self, value: int) -> bool:
        if value <= 0:
            return False
        self._baudrate = value
        return True

    def setDataBits(self, value: QSerialPort.DataBits) -> bool:
        if value.value not in (5, 6, 7, 8):
            return False
        self._data_bits = value.value
        return True

    def setParity(self, value: QSerialPort.Parity) -> bool:
        if value not in (QSerialPort.NoParity, QSerialPort.EvenParity, QSerialPort.OddParity, QSerialPort.MarkParity, QSerialPort.SpaceParity):
            return False
        self._parity = value
        return True

    def setStopBits(self, value: QSerialPort.StopBits) -> bool:
        values = {QSerialPort.OneStop: 1.0, QSerialPort.OneAndHalfStop: 1.5, QSerialPort.TwoStop: 2.0}
        if value not in values:
            return False
        self._stop_bits = values[value]
        return True

    def setFlowControl(self, value: QSerialPort.FlowControl) -> bool:
        if value not in (QSerialPort.NoFlowControl, QSerialPort.HardwareControl, QSerialPort.SoftwareControl):
            return False
        self._flow = value
        return True

    def open(self, mode) -> bool:
        """Reinicia los buffers como una nueva conexión y conserva los parámetros UART."""
        self.close()
        self._opened = True
        self._error = "Sin errores"
        return True

    def close(self) -> None:
        self.timer.stop()
        self._session += 1
        self._opened = False
        self._tx.clear()
        self._rx.clear()

    def isOpen(self) -> bool:
        return self._opened

    def errorString(self) -> str:
        return self._error

    def write(self, payload: bytes) -> int:
        """Solo acepta en la cola: confirmar y recibir ocurren después por señales."""
        if not self._opened:
            self._error = "TEST está desconectado"
            self.errorOccurred.emit(QSerialPort.NotOpenError)
            return -1
        self._tx.extend(payload)
        if self._tx and not self.timer.isActive():
            self._schedule()
        return len(payload)

    def bytesAvailable(self) -> int:
        return len(self._rx)

    def read(self, maximum: int) -> bytes:
        payload = bytes(self._rx[:maximum])
        del self._rx[:maximum]
        return payload

    def _schedule(self) -> None:
        """Calcula tramas con inicio, datos, paridad y parada; bloques de hasta 64 bytes."""
        frame_bits = 1 + self._data_bits + (self._parity != QSerialPort.NoParity) + self._stop_bits
        # Cede a la UI al menos cada ~20 ms; Qt aproxima los retardos a milisegundos.
        count = min(64, len(self._tx), max(1, int(self._baudrate * 0.020 / frame_bits)))
        self._scheduled = count
        self.timer.start(max(1, math.ceil(count * frame_bits * 1000 / self._baudrate)))

    def _transfer(self) -> None:
        """Notifica escrituras parciales y ofrece el eco mediante readyRead/read."""
        if not self._opened or not self._tx:
            return
        count = self._scheduled
        if self._read_limit:
            count = min(count, self._read_limit - len(self._rx))
        if count > 0:
            session = self._session
            payload = self._tx[:count]
            del self._tx[:count]
            # Una trama de 5/6/7 bits no puede transportar los bits superiores del byte.
            if self._data_bits < 8:
                mask = (1 << self._data_bits) - 1
                payload = bytes(value & mask for value in payload)
            self._rx.extend(payload)
            self.bytesWritten.emit(count)
            if session != self._session or not self._opened:
                return
            self.readyRead.emit()
            if session != self._session or not self._opened:
                return
        if self._tx:
            self._schedule()
