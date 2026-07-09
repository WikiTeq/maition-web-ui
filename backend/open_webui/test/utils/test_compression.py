import asyncio

from open_webui.utils.compression import (
    ResponseCompressionMiddleware,
    choose_encoding,
)


# --- choose_encoding ---


def test_no_accept_encoding_header_returns_none():
    assert choose_encoding(None) is None
    assert choose_encoding("") is None


def test_single_supported_encoding_is_chosen():
    assert choose_encoding("gzip") == "gzip"
    assert choose_encoding("br") == "br"
    assert choose_encoding("zstd") == "zstd"


def test_higher_q_value_wins():
    assert choose_encoding("gzip;q=0.2, br;q=0.8") == "br"


def test_tie_breaks_via_server_preference_order():
    # zstd > br > gzip when q-values are equal
    assert choose_encoding("gzip;q=0.5, br;q=0.5, zstd;q=0.5") == "zstd"
    assert choose_encoding("gzip;q=0.5, br;q=0.5") == "br"


def test_q_zero_refuses_encoding():
    assert choose_encoding("gzip;q=0") is None
    assert choose_encoding("gzip;q=0, br;q=0.5") == "br"


def test_wildcard_is_used_for_unlisted_encodings():
    assert choose_encoding("*") == "zstd"
    assert choose_encoding("*;q=0.3") == "zstd"


def test_unsupported_encoding_without_wildcard_returns_none():
    assert choose_encoding("identity") is None


def test_malformed_q_value_treated_as_zero():
    assert choose_encoding("gzip;q=not-a-number") is None


# --- ResponseCompressionMiddleware ---


def _run(coro):
    return asyncio.run(coro)


async def _call_middleware(middleware, headers, body_chunks, response_headers=None):
    """Drive the ASGI middleware through a request/response cycle and
    collect what it sends downstream."""
    scope = {
        "type": "http",
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in headers.items()
        ],
    }
    sent = []

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (k.lower().encode(), v.encode())
                    for k, v in (response_headers or {}).items()
                ],
            }
        )
        for i, chunk in enumerate(body_chunks):
            await send(
                {
                    "type": "http.response.body",
                    "body": chunk,
                    "more_body": i < len(body_chunks) - 1,
                }
            )

    middleware = ResponseCompressionMiddleware(app, **middleware)
    await middleware(scope, receive, send)
    return sent


def _headers_dict(message):
    return {k.decode().lower(): v.decode() for k, v in message.get("headers", [])}


def test_compresses_body_above_minimum_size():
    body = b"x" * 1000
    sent = _run(
        _call_middleware(
            {"minimum_size": 500},
            {"accept-encoding": "gzip"},
            [body],
            {"content-type": "application/json"},
        )
    )
    start, body_msg = sent
    headers = _headers_dict(start)
    assert headers["content-encoding"] == "gzip"
    assert body_msg["body"] != body
    assert int(headers["content-length"]) == len(body_msg["body"])
    assert headers["vary"] == "Accept-Encoding"


def test_body_below_minimum_size_is_not_compressed():
    body = b"tiny"
    sent = _run(
        _call_middleware(
            {"minimum_size": 500},
            {"accept-encoding": "gzip"},
            [body],
            {"content-type": "application/json"},
        )
    )
    start, body_msg = sent
    headers = _headers_dict(start)
    assert "content-encoding" not in headers
    assert body_msg["body"] == body


def test_no_acceptable_encoding_passes_through_untouched():
    body = b"x" * 1000
    sent = _run(
        _call_middleware(
            {"minimum_size": 500},
            {"accept-encoding": "identity"},
            [body],
            {"content-type": "application/json"},
        )
    )
    start, body_msg = sent
    headers = _headers_dict(start)
    assert "content-encoding" not in headers
    assert body_msg["body"] == body


def test_non_compressible_content_type_passes_through():
    body = b"x" * 1000
    sent = _run(
        _call_middleware(
            {"minimum_size": 500},
            {"accept-encoding": "gzip"},
            [body],
            {"content-type": "image/png"},
        )
    )
    start, body_msg = sent
    headers = _headers_dict(start)
    assert "content-encoding" not in headers
    assert body_msg["body"] == body


def test_font_and_wasm_content_types_are_compressed():
    body = b"x" * 1000
    for content_type in (
        "font/ttf",
        "font/otf",
        "font/woff",
        "font/woff2",
        "application/x-font-ttf",
        "application/wasm",
        "application/manifest+json",
    ):
        sent = _run(
            _call_middleware(
                {"minimum_size": 500},
                {"accept-encoding": "gzip"},
                [body],
                {"content-type": content_type},
            )
        )
        start, body_msg = sent
        headers = _headers_dict(start)
        assert headers["content-encoding"] == "gzip", content_type
        assert body_msg["body"] != body, content_type


def test_content_type_matching_is_case_insensitive():
    body = b"x" * 1000
    sent = _run(
        _call_middleware(
            {"minimum_size": 500},
            {"accept-encoding": "gzip"},
            [body],
            {"content-type": "Application/JSON"},
        )
    )
    start, body_msg = sent
    headers = _headers_dict(start)
    assert headers["content-encoding"] == "gzip"
    assert body_msg["body"] != body


def test_streaming_content_type_is_never_buffered_or_compressed():
    chunks = [b"data: one\n\n", b"data: two\n\n"]
    sent = _run(
        _call_middleware(
            {"minimum_size": 1},
            {"accept-encoding": "gzip"},
            chunks,
            {"content-type": "text/event-stream"},
        )
    )
    start, *body_msgs = sent
    headers = _headers_dict(start)
    assert "content-encoding" not in headers
    assert [m["body"] for m in body_msgs] == chunks


def test_already_encoded_response_is_left_alone():
    body = b"x" * 1000
    sent = _run(
        _call_middleware(
            {"minimum_size": 500},
            {"accept-encoding": "gzip"},
            [body],
            {"content-type": "application/json", "content-encoding": "identity"},
        )
    )
    start, body_msg = sent
    headers = _headers_dict(start)
    assert headers["content-encoding"] == "identity"
    assert body_msg["body"] == body


def test_body_exceeding_max_size_falls_back_to_passthrough():
    chunk = b"x" * 100
    sent = _run(
        _call_middleware(
            {"minimum_size": 1, "max_body_size": 150},
            {"accept-encoding": "gzip"},
            [chunk, chunk],
            {"content-type": "application/json"},
        )
    )
    start, *body_msgs = sent
    headers = _headers_dict(start)
    assert "content-encoding" not in headers
    assert b"".join(m["body"] for m in body_msgs) == chunk + chunk


def test_partial_content_response_is_not_compressed():
    body = b"x" * 1000
    scope = {
        "type": "http",
        "headers": [(b"accept-encoding", b"gzip")],
    }
    sent = []

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 206,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})

    middleware = ResponseCompressionMiddleware(app, minimum_size=1)
    _run(middleware(scope, receive, send))

    start, body_msg = sent
    headers = _headers_dict(start)
    assert "content-encoding" not in headers
    assert body_msg["body"] == body


def test_non_http_scope_is_passed_through_untouched():
    sent = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    async def app(scope, receive, send):
        await send({"type": "websocket.accept"})

    middleware = ResponseCompressionMiddleware(app)
    _run(middleware({"type": "websocket", "headers": []}, receive, send))

    assert sent == [{"type": "websocket.accept"}]
