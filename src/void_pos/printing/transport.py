from __future__ import annotations

import socket
from typing import Protocol

from ..errors import ServiceUnavailable


class PrinterUnavailable(ServiceUnavailable):
    code = "printer_unavailable"


class Printer(Protocol):
    def send(self, data: bytes) -> None: ...


class NetworkPrinter:
    """Raw ESC/POS over TCP — the common path for networked kitchen printers (port 9100)."""

    def __init__(self, host: str, port: int = 9100, timeout: float = 5.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    def send(self, data: bytes) -> None:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.sendall(data)
        except OSError as exc:  # refused, unreachable, timed out
            raise PrinterUnavailable(
                f"the kitchen printer at {self.host}:{self.port} can't be reached"
            ) from exc
