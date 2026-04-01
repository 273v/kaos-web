"""Middleware protocol and chain composition."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, Protocol

from kaos_web.models import WebRequest, WebResponse

# Type alias for the async handler function
Handler = Callable[[WebRequest], Coroutine[Any, Any, WebResponse]]


class Middleware(Protocol):
    """Protocol for request/response middleware."""

    async def process(self, request: WebRequest, next_handler: Handler) -> WebResponse:
        """Process a request, optionally delegating to next_handler."""
        ...


class MiddlewareChain:
    """Composable middleware chain.

    Wraps a final handler with a sequence of middleware. Each middleware
    can inspect/modify the request, delegate to the next handler, and
    inspect/modify the response.

    Execution order: first middleware added executes first (outermost).
    """

    def __init__(self, handler: Handler) -> None:
        self._handler = handler
        self._middleware: list[Middleware] = []

    def add(self, middleware: Middleware) -> MiddlewareChain:
        """Add middleware to the chain. Returns self for fluent API."""
        self._middleware.append(middleware)
        return self

    async def execute(self, request: WebRequest) -> WebResponse:
        """Execute the full middleware chain."""
        # Build from inside out: last middleware wraps the handler,
        # first middleware is outermost
        handler = self._handler
        for mw in reversed(self._middleware):
            handler = _wrap(mw, handler)
        return await handler(request)


def _wrap(middleware: Middleware, next_handler: Handler) -> Handler:
    """Wrap a handler with a middleware layer."""

    async def wrapped(request: WebRequest) -> WebResponse:
        return await middleware.process(request, next_handler)

    return wrapped
