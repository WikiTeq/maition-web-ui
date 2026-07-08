import gzip
from typing import Callable, MutableMapping, Optional, cast

import brotli
import zstandard
from asgiref.typing import (
    ASGI3Application,
    ASGIReceiveCallable,
    ASGISendCallable,
    ASGISendEvent,
    Scope as ASGIScope,
)
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request

# Response bodies larger than this are left uncompressed to bound
# in-memory buffering of the full response.
MAX_COMPRESSIBLE_BODY_SIZE = 10 * 1024 * 1024

COMPRESSIBLE_CONTENT_TYPES = {
    "application/json",
    "application/javascript",
    "application/xml",
    "text/css",
    "text/html",
    "text/javascript",
    "text/plain",
    "text/xml",
    "image/svg+xml",
}


def _compress_gzip(body: bytes) -> bytes:
    return gzip.compress(body)


def _compress_brotli(body: bytes) -> bytes:
    return brotli.compress(body)


def _compress_zstd(body: bytes) -> bytes:
    return zstandard.ZstdCompressor().compress(body)


ENCODERS: dict[str, Callable[[bytes], bytes]] = {
    "zstd": _compress_zstd,
    "br": _compress_brotli,
    "gzip": _compress_gzip,
}

# Server preference order, most preferred first. Used to break ties when the
# client's Accept-Encoding assigns equal q-values to multiple encodings.
SERVER_PREFERENCE = ("zstd", "br", "gzip")


def _parse_accept_encoding(header_value: str) -> dict[str, float]:
    """Parse an Accept-Encoding header into a mapping of encoding -> q-value."""
    weights: dict[str, float] = {}
    for part in header_value.split(","):
        part = part.strip()
        if not part:
            continue

        if ";" in part:
            encoding, _, params = part.partition(";")
            encoding = encoding.strip().lower()
            q = 1.0
            for param in params.split(";"):
                name, _, value = param.partition("=")
                if name.strip().lower() == "q":
                    try:
                        q = float(value.strip())
                    except ValueError:
                        q = 0.0
                    else:
                        q = max(0.0, min(1.0, q))
        else:
            encoding = part.lower()
            q = 1.0

        weights[encoding] = q

    return weights


def choose_encoding(accept_encoding: Optional[str]) -> Optional[str]:
    """Pick the best encoding to use for the given Accept-Encoding header value.

    Respects explicit q=0 refusals and the `*` wildcard, and otherwise prefers
    the highest-q supported encoding, breaking ties via SERVER_PREFERENCE.
    """
    if not accept_encoding:
        return None

    weights = _parse_accept_encoding(accept_encoding)
    wildcard_q = weights.get("*")

    best_encoding = None
    best_q = 0.0
    for encoding in SERVER_PREFERENCE:
        if encoding in weights:
            q = weights[encoding]
        elif wildcard_q is not None:
            q = wildcard_q
        else:
            continue

        if q > best_q:
            best_q = q
            best_encoding = encoding

    if best_q <= 0.0:
        return None

    return best_encoding


class ResponseCompressionMiddleware:
    """ASGI middleware that compresses HTTP responses via gzip, brotli, or zstd,
    negotiated from the request's Accept-Encoding header.

    Streaming content types (e.g. text/event-stream) are always passed through
    untouched so SSE responses are never buffered or delayed.
    """

    def __init__(
        self,
        app: ASGI3Application,
        *,
        minimum_size: int = 500,
        max_body_size: int = MAX_COMPRESSIBLE_BODY_SIZE,
    ) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.max_body_size = max_body_size

    async def __call__(
        self,
        scope: ASGIScope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        request = Request(scope=cast(MutableMapping, scope))
        encoding = choose_encoding(request.headers.get("accept-encoding"))
        if encoding is None:
            return await self.app(scope, receive, send)

        response_start: Optional[ASGISendEvent] = None
        body = bytearray()
        should_compress = False
        passthrough = False

        async def flush_passthrough(chunk: bytes, more_body: bool) -> None:
            """Give up on buffering: emit response_start and whatever body has
            been accumulated so far uncompressed, followed by the chunk that
            tipped us over the cap, then stream any further chunks straight
            through."""
            nonlocal passthrough, response_start
            passthrough = True
            assert response_start is not None
            await send(response_start)
            response_start = None
            if body:
                await send(
                    {
                        "type": "http.response.body",
                        "body": bytes(body),
                        "more_body": True,
                    }
                )
                body.clear()
            await send(
                {
                    "type": "http.response.body",
                    "body": chunk,
                    "more_body": more_body,
                }
            )

        async def send_wrapper(message: ASGISendEvent) -> None:
            nonlocal response_start, should_compress

            if message["type"] == "http.response.start":
                headers = Headers(raw=message.get("headers", []))
                content_type = headers.get("content-type", "").split(";")[0].strip()
                should_compress = (
                    message.get("status", 200) != 206
                    and "content-range" not in headers
                    and "content-encoding" not in headers
                    and content_type in COMPRESSIBLE_CONTENT_TYPES
                )
                if not should_compress:
                    return await send(message)
                response_start = message
                return

            if message["type"] != "http.response.body":
                return await send(message)

            if not should_compress:
                return await send(message)

            if passthrough:
                return await send(message)

            chunk = message.get("body", b"")
            more_body = message.get("more_body", False)
            if len(body) + len(chunk) > self.max_body_size:
                return await flush_passthrough(chunk, more_body)

            body.extend(chunk)

            if more_body:
                return

            assert response_start is not None
            if len(body) < self.minimum_size:
                await send(response_start)
                await send(
                    {
                        "type": "http.response.body",
                        "body": bytes(body),
                        "more_body": False,
                    }
                )
                return

            compressed_body = ENCODERS[encoding](bytes(body))

            headers = MutableHeaders(raw=list(response_start.get("headers", [])))
            headers["content-encoding"] = encoding
            headers["content-length"] = str(len(compressed_body))
            headers.add_vary_header("Accept-Encoding")
            response_start["headers"] = headers.raw

            await send(response_start)
            await send(
                {
                    "type": "http.response.body",
                    "body": compressed_body,
                    "more_body": False,
                }
            )

        await self.app(scope, receive, send_wrapper)
