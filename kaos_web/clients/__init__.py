"""Web client implementations."""

from kaos_web.clients.http import HttpClient
from kaos_web.clients.protocol import WebClientProtocol

__all__ = [
    "HttpClient",
    "WebClientProtocol",
]
