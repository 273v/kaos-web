"""Web client implementations."""

from kaos_web.clients.config import BrowserClientConfig, HttpClientConfig
from kaos_web.clients.http import HttpClient
from kaos_web.clients.protocol import WebClientProtocol

__all__ = [
    "BrowserClientConfig",
    "HttpClient",
    "HttpClientConfig",
    "WebClientProtocol",
]

# BrowserClient is lazily importable (requires [browser] extra)
# from kaos_web.clients.browser import BrowserClient
