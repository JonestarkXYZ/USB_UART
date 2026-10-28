"""Conversión explícita entre representaciones de texto y bytes UART."""

from __future__ import annotations

import codecs
import re


MODES = ("UTF-8", "ASCII", "Binario", "Hexadecimal")
ENDINGS = {"Ninguno": b"", "LF": b"\n", "CR": b"\r", "CRLF": b"\r\n"}


def encode_payload(text: str, mode: str) -> bytes:
    """Rechaza grupos incompletos; los espacios separan bytes y nunca se envían."""
    if mode in ("UTF-8", "ASCII"):
        try:
            return text.encode("utf-8" if mode == "UTF-8" else "ascii")
        except UnicodeEncodeError as error:
            raise ValueError("ASCII solo admite caracteres entre 0 y 127; use UTF-8.") from error
    width, alphabet, base = (8, "01", 2) if mode == "Binario" else (2, "0-9A-Fa-f", 16)
    if mode not in MODES:
        raise ValueError("Formato desconocido.")
    # Acepta pegado compacto o separado; cada grupo explícito debe estar completo.
    groups = text.split()
    if not groups or any(
        not re.fullmatch(f"[{alphabet}]+", group) or len(group) % width
        for group in groups
    ):
        raise ValueError(f"Complete cada byte: grupos de {width} dígitos para {mode}.")
    digits = "".join(groups)
    return bytes(int(digits[index:index + width], base) for index in range(0, len(digits), width))


def grouped_input(text: str, mode: str) -> str:
    """Agrupa la entrada validada sin completar ni inventar bits/dígitos."""
    if mode not in ("Binario", "Hexadecimal"):
        return text
    digits = "".join(text.split()).upper()
    width = 8 if mode == "Binario" else 2
    return " ".join(digits[index:index + width] for index in range(0, len(digits), width))


class StreamFormatter:
    """Mantiene UTF-8 incremental: una lectura UART puede cortar un carácter."""

    def __init__(self, mode: str, interpret_controls: bool = False):
        self.mode = mode
        self.interpret_controls = interpret_controls
        self.decoder = codecs.getincrementaldecoder("utf-8")("backslashreplace")

    def format(self, payload: bytes, final: bool = False) -> str:
        """Expone solo CR/LF/TAB/BS al visor cuando se solicita interpretarlos."""
        if self.mode == "Hexadecimal":
            return payload.hex(" ").upper()
        if self.mode == "Binario":
            return " ".join(f"{value:08b}" for value in payload)
        text = (
            self.decoder.decode(payload, final=final)
            if self.mode == "UTF-8"
            else payload.decode("ascii", errors="backslashreplace")
        )
        visible = []
        for character in text:
            codepoint = ord(character)
            if self.interpret_controls and character in "\r\n\t\b":
                visible.append(character)
            elif codepoint in (0x2028, 0x2029):
                # Los separadores Unicode también crean bloques en QTextDocument.
                visible.append(f"\\u{codepoint:04X}")
            elif codepoint < 32 or 127 <= codepoint < 160:
                visible.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}.get(character, f"\\x{codepoint:02X}"))
            else:
                visible.append(character)
        return "".join(visible)
