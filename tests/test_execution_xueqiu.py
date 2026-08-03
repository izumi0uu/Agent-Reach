"""Offline execution tests for the closed Xueqiu stock-search runtime."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable

import pytest

import agent_reach.execution.v1.xueqiu as xueqiu_execution
from agent_reach.execution.v1 import (
    PROTOCOL_VERSION,
    ExecutionContextV1,
    ExecutionFailureV1,
    ExecutionLimitsV1,
    ExecutionRequestV1,
    ExecutionSuccessV1,
    XueqiuSessionV1,
    execute,
)


def _request(query: str = "600519", limit: int = 10) -> ExecutionRequestV1:
    return ExecutionRequestV1(
        PROTOCOL_VERSION,
        "xueqiu",
        "search.stocks",
        {"query": query, "limit": limit},
    )


def _context(
    cookie: bytearray,
    *,
    maximum_items: int = 50,
    checkpoint: Callable[[], None] = lambda: None,
) -> tuple[ExecutionContextV1, XueqiuSessionV1]:
    session = XueqiuSessionV1(cookie)
    return (
        ExecutionContextV1(
            (session,),
            checkpoint=checkpoint,
            limits=ExecutionLimitsV1(maximum_items=maximum_items),
        ),
        session,
    )


class FixtureTransport:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[tuple[str, int, bytes]] = []

    def search(
        self,
        query: str,
        limit: int,
        cookie_header: bytearray,
        checkpoint: Callable[[], None],
    ) -> object:
        checkpoint()
        self.calls.append((query, limit, bytes(cookie_header)))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class _FakeNetworkState:
    def __init__(
        self,
        *,
        blocking_phase: str | None = None,
        phase_advances: dict[str, float] | None = None,
        stream_forever: bool = False,
    ) -> None:
        self.blocking_phase = blocking_phase
        self.phase_advances = phase_advances or {}
        self.stream_forever = stream_forever
        self.clock_value = 0.0
        self.entered = threading.Event()
        self.released = threading.Event()
        self.block_exited = threading.Event()
        self.closed: set[str] = set()
        self.timeouts: list[tuple[str, float]] = []
        self.read_count = 0

    def clock(self) -> float:
        return self.clock_value

    def enter(self, phase: str) -> None:
        self.clock_value += self.phase_advances.get(phase, 0.0)
        if self.blocking_phase != phase:
            return
        self.entered.set()
        try:
            if not self.released.wait(1.0):
                raise AssertionError("blocking phase was not cancelled")
        finally:
            self.block_exited.set()
        raise OSError("closed by cancellation")

    def close(self, name: str) -> None:
        self.closed.add(name)
        self.released.set()


class _FakeRawSocket:
    def __init__(self, state: _FakeNetworkState) -> None:
        self.state = state

    def settimeout(self, timeout: float) -> None:
        self.state.timeouts.append(("raw", timeout))

    def connect(self, address: object) -> None:
        del address
        self.state.enter("connect")

    def shutdown(self, how: int) -> None:
        del how
        self.state.close("raw")

    def close(self) -> None:
        self.state.close("raw")


class _FakeTlsSocket:
    def __init__(self, state: _FakeNetworkState, raw_socket: _FakeRawSocket) -> None:
        self.state = state
        self.raw_socket = raw_socket

    def settimeout(self, timeout: float) -> None:
        self.state.timeouts.append(("tls", timeout))

    def do_handshake(self) -> None:
        self.state.enter("tls")

    def sendall(self, request: bytearray) -> None:
        del request
        self.state.enter("send")

    def shutdown(self, how: int) -> None:
        self.state.close("tls")
        self.raw_socket.shutdown(how)

    def close(self) -> None:
        self.state.close("tls")
        self.raw_socket.close()


class _FakeSslContext:
    def __init__(self, state: _FakeNetworkState, tls_socket: _FakeTlsSocket) -> None:
        self.state = state
        self.tls_socket = tls_socket

    def wrap_socket(
        self,
        raw_socket: object,
        *,
        server_hostname: str,
        do_handshake_on_connect: bool,
    ) -> _FakeTlsSocket:
        assert raw_socket is self.tls_socket.raw_socket
        assert server_hostname == "xueqiu.com"
        assert do_handshake_on_connect is False
        self.state.enter("wrap")
        return self.tls_socket


class _FakeHttpResponse:
    def __init__(self, state: _FakeNetworkState, tls_socket: _FakeTlsSocket) -> None:
        self.state = state
        self.tls_socket = tls_socket
        self.status = 200
        self._returned_body = False

    def begin(self) -> None:
        self.state.enter("headers")

    def getheaders(self) -> list[tuple[str, str]]:
        return [("Content-Type", "application/json")]

    def read(self, amount: int) -> bytes:
        assert amount == xueqiu_execution._READ_CHUNK_BYTES
        self.state.read_count += 1
        self.state.enter("body")
        if self.state.stream_forever:
            return b"x"
        if self._returned_body:
            return b""
        self._returned_body = True
        return b'{"stocks":[]}'

    def close(self) -> None:
        self.state.close("response")


def _install_fake_network(
    monkeypatch: pytest.MonkeyPatch,
    state: _FakeNetworkState,
) -> tuple[_FakeRawSocket, _FakeTlsSocket, _FakeHttpResponse]:
    raw_socket = _FakeRawSocket(state)
    tls_socket = _FakeTlsSocket(state, raw_socket)
    response = _FakeHttpResponse(state, tls_socket)
    ssl_context = _FakeSslContext(state, tls_socket)
    monkeypatch.setattr(xueqiu_execution.socket, "socket", lambda *_args: raw_socket)
    monkeypatch.setattr(xueqiu_execution.ssl, "create_default_context", lambda: ssl_context)
    monkeypatch.setattr(
        xueqiu_execution.http.client,
        "HTTPResponse",
        lambda selected_socket: (
            response if selected_socket is tls_socket else pytest.fail("unexpected response socket")
        ),
    )
    return raw_socket, tls_socket, response


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    transport: FixtureTransport,
) -> None:
    monkeypatch.setattr(xueqiu_execution, "XueqiuTransport", lambda: transport)


def test_search_projects_closed_stock_rows_and_clears_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FixtureTransport(
        {
            "stocks": [
                {
                    "code": "SH600519",
                    "name": "贵州茅台",
                    "exchange": "SH",
                    "ignored": "bounded upstream field",
                },
                {"code": "SZ000858", "name": "五粮液", "exchange": "SZ"},
            ],
            "ignored": True,
        }
    )
    _install_transport(monkeypatch, transport)
    source_cookie = bytearray(b"xq_a_token=secret; u=1")
    context, session = _context(source_cookie)

    result = execute(_request(limit=2), context)

    assert source_cookie == bytearray(b"\x00" * len(source_cookie))
    assert isinstance(result, ExecutionSuccessV1)
    assert result.backend_id == "xueqiu-api"
    assert result.backend_version == "1.5.0+search.v1"
    assert [dict(item.fields) for item in result.items] == [
        {"symbol": "SH600519", "name": "贵州茅台", "exchange": "SH"},
        {"symbol": "SZ000858", "name": "五粮液", "exchange": "SZ"},
    ]
    assert result.truncated is False
    assert transport.calls == [("600519", 2, b"xq_a_token=secret; u=1")]
    assert session.cookie_header == bytearray(b"\x00" * len(session.cookie_header))
    assert "secret" not in repr(session)


def test_context_limit_narrows_backend_size_and_reports_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FixtureTransport(
        {"stocks": [{"code": "SH600519", "name": "贵州茅台", "exchange": "SH"}]}
    )
    _install_transport(monkeypatch, transport)
    context, _ = _context(bytearray(b"xq_a_token=secret"), maximum_items=1)

    result = execute(_request(limit=10), context)

    assert isinstance(result, ExecutionSuccessV1)
    assert result.truncated is True
    assert transport.calls[0][:2] == ("600519", 1)


def test_search_preserves_native_order_and_accepts_correlated_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FixtureTransport(
        {
            "stocks": [
                {"code": "NASDAQ:AAPL", "name": "Apple", "exchange": "NASDAQ"},
                {"code": "SHA:600519", "name": "贵州茅台", "exchange": "SHA"},
                {"code": "SZA:300750", "name": "宁德时代", "exchange": "SZA"},
                {"code": "BJA:430047", "name": "诺思兰德", "exchange": "BJA"},
                {"code": "BJ430090", "name": "同辉信息", "exchange": "BJ"},
                {"code": "BJ430047", "name": "诺思兰德", "exchange": "BJA"},
                {"code": "SZ300750", "name": "宁德时代", "exchange": "SZA"},
                {"code": "SH600519", "name": "贵州茅台", "exchange": "SHA"},
            ]
        }
    )
    _install_transport(monkeypatch, transport)
    context, _ = _context(bytearray(b"xq_a_token=secret"))

    result = execute(_request(limit=8), context)

    assert isinstance(result, ExecutionSuccessV1)
    assert [dict(item.fields) for item in result.items] == [
        {"symbol": "NASDAQ:AAPL", "name": "Apple", "exchange": "NASDAQ"},
        {"symbol": "SHA:600519", "name": "贵州茅台", "exchange": "SHA"},
        {"symbol": "SZA:300750", "name": "宁德时代", "exchange": "SZA"},
        {"symbol": "BJA:430047", "name": "诺思兰德", "exchange": "BJA"},
        {"symbol": "BJ430090", "name": "同辉信息", "exchange": "BJ"},
        {"symbol": "BJ430047", "name": "诺思兰德", "exchange": "BJA"},
        {"symbol": "SZ300750", "name": "宁德时代", "exchange": "SZA"},
        {"symbol": "SH600519", "name": "贵州茅台", "exchange": "SHA"},
    ]


@pytest.mark.parametrize(
    ("symbol", "exchange"),
    [
        ("SH600519", "SH"),
        ("SH600519", "SHA"),
        ("SHA:600519", "SHA"),
        ("SZA:300750", "SZA"),
        ("BJA:430047", "BJA"),
        ("US:" + "A" * 61, "US"),
    ],
)
def test_runtime_stock_symbol_grammar_accepts_exact_contract_values(
    symbol: str,
    exchange: str,
) -> None:
    assert xueqiu_execution._stock_identity(symbol, exchange) == (symbol, exchange)


@pytest.mark.parametrize(
    ("symbol", "exchange"),
    [
        ("SH60051", "SH"),
        ("SH600519", "SZ"),
        ("SHA600519", "SHA"),
        ("SHA:", "SHA"),
        ("SHA:600519", "SH"),
        ("sha:600519", "SHA"),
        ("US:" + "A" * 62, "US"),
    ],
)
def test_runtime_stock_symbol_grammar_rejects_contract_invalid_values(
    symbol: str,
    exchange: str,
) -> None:
    with pytest.raises(xueqiu_execution._XueqiuDataError):
        xueqiu_execution._stock_identity(symbol, exchange)


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"stocks": "not-a-list"},
        {"stocks": [{"code": "", "name": "name", "exchange": "SH"}]},
        {"stocks": [{"code": "SH1", "name": "", "exchange": "SH"}]},
        {"stocks": [{"code": "SH1", "name": "name", "exchange": "S H"}]},
        {"stocks": [{"code": "600519", "name": "name", "exchange": "SH"}]},
        {"stocks": [{"code": "SH600519", "name": "name", "exchange": "SZ"}]},
        {"stocks": [{"code": "NASDAQ:AAPL", "name": "name", "exchange": "NYSE"}]},
        {"stocks": [{"code": "AAPL", "name": "name", "exchange": "NASDAQ"}]},
        {"stocks": [{"code": "nasdaq:AAPL", "name": "name", "exchange": "NASDAQ"}]},
        {
            "stocks": [
                {"code": "SH1", "name": "one", "exchange": "SH"},
                {"code": "SH1", "name": "two", "exchange": "SH"},
            ]
        },
    ],
)
def test_complete_native_response_is_validated_before_success(
    monkeypatch: pytest.MonkeyPatch,
    response: object,
) -> None:
    transport = FixtureTransport(response)
    _install_transport(monkeypatch, transport)
    context, session = _context(bytearray(b"xq_a_token=secret"))

    result = execute(_request(), context)

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_contract_violation"
    assert not any(session.cookie_header)


def test_backend_failure_and_cancellation_clear_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FixtureTransport(xueqiu_execution.XueqiuTransportFailure("rate_limit"))
    _install_transport(monkeypatch, transport)
    context, session = _context(bytearray(b"xq_a_token=secret"))

    result = execute(_request(), context)

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "rate_limit"
    assert not any(session.cookie_header)

    cancelled_context, cancelled_session = _context(
        bytearray(b"xq_a_token=cancel"),
        checkpoint=lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        execute(_request(), cancelled_context)
    assert not any(cancelled_session.cookie_header)


def test_predispatch_rejection_consumes_cookie_without_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_transport() -> object:
        raise AssertionError("rejected request constructed the transport")

    monkeypatch.setattr(xueqiu_execution, "XueqiuTransport", unexpected_transport)
    source_cookie = bytearray(b"xq_a_token=reject-canary")
    context, session = _context(source_cookie)
    request = ExecutionRequestV1(
        PROTOCOL_VERSION,
        "xueqiu",
        "search.stocks",
        {"query": "600519", "limit": 1, "cookie": "forbidden"},
    )

    result = execute(request, context)

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "invalid_request"
    assert not any(source_cookie)
    assert not any(session.cookie_header)


def test_transport_uses_only_fixed_origin_path_query_and_one_cookie_header() -> None:
    captured: list[tuple[str, bytes]] = []

    def exchange(
        address: str,
        request_bytes: bytearray,
        checkpoint: Callable[[], None],
    ) -> tuple[int, tuple[tuple[str, str], ...], bytes]:
        checkpoint()
        captured.append((address, bytes(request_bytes)))
        body = json.dumps({"stocks": []}).encode()
        return 200, (("Content-Type", "application/json"),), body

    transport = xueqiu_execution.XueqiuTransport(
        _resolver=lambda host, port: (
            "93.184.216.34"
            if (host, port) == ("xueqiu.com", 443)
            else pytest.fail("unexpected origin"),
        ),
        _exchange=exchange,
    )
    cookie = bytearray(b"xq_a_token=secret; u=1")

    result = transport.search("贵州 茅台", 7, cookie, lambda: None)

    assert result == {"stocks": []}
    assert captured[0][0] == "93.184.216.34"
    request = captured[0][1]
    assert request.startswith(
        b"GET /stock/search.json?code=%E8%B4%B5%E5%B7%9E+%E8%8C%85%E5%8F%B0&size=7 "
        b"HTTP/1.1\r\nHost: xueqiu.com\r\n"
    )
    assert request.count(b"\r\nCookie: xq_a_token=secret; u=1\r\n") == 1
    assert b"Proxy" not in request
    assert b"homepage" not in request.lower()


def test_request_host_header_follows_the_pinned_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(xueqiu_execution, "_ORIGIN_HOST", "stocks.example.com")
    cookie = bytearray(b"xq_a_token=secret")
    request = xueqiu_execution._request_bytes("/stock/search.json?code=600519&size=1", cookie)
    try:
        assert b"\r\nHost: stocks.example.com\r\n" in request
    finally:
        request[:] = b"\x00" * len(request)
        cookie[:] = b"\x00" * len(cookie)


def test_transport_cancellation_interrupts_blocking_dns_without_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    released = threading.Event()
    finished = threading.Event()
    exchange_calls = 0

    def resolver(_host: str, _port: int) -> tuple[str, ...]:
        entered.set()
        try:
            released.wait(1.0)
            return ("93.184.216.34",)
        finally:
            finished.set()

    def exchange(*_args: object) -> tuple[int, tuple[tuple[str, str], ...], bytes]:
        nonlocal exchange_calls
        exchange_calls += 1
        raise AssertionError("cancelled DNS reached exchange")

    def checkpoint() -> None:
        if entered.is_set():
            raise KeyboardInterrupt

    monkeypatch.setattr(xueqiu_execution, "_CANCELLATION_POLL_SECONDS", 0.001)
    transport = xueqiu_execution.XueqiuTransport(_resolver=resolver, _exchange=exchange)
    try:
        with pytest.raises(KeyboardInterrupt):
            transport.search("600519", 1, bytearray(b"xq_a_token=secret"), checkpoint)
    finally:
        released.set()

    assert finished.wait(1.0)
    assert exchange_calls == 0


def test_transport_absolute_deadline_interrupts_blocking_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _FakeNetworkState()
    released = threading.Event()
    finished = threading.Event()
    exchange_calls = 0

    def resolver(_host: str, _port: int) -> tuple[str, ...]:
        state.entered.set()
        state.clock_value = xueqiu_execution._ATTEMPT_TIMEOUT_SECONDS + 1.0
        try:
            released.wait(1.0)
            return ("93.184.216.34",)
        finally:
            finished.set()

    def exchange(*_args: object) -> tuple[int, tuple[tuple[str, str], ...], bytes]:
        nonlocal exchange_calls
        exchange_calls += 1
        raise AssertionError("expired DNS reached exchange")

    monkeypatch.setattr(xueqiu_execution, "_CANCELLATION_POLL_SECONDS", 0.001)
    transport = xueqiu_execution.XueqiuTransport(
        _resolver=resolver,
        _exchange=exchange,
        _clock=state.clock,
    )
    try:
        with pytest.raises(xueqiu_execution.XueqiuTransportFailure) as raised:
            transport.search("600519", 1, bytearray(b"xq_a_token=secret"), lambda: None)
    finally:
        released.set()

    assert raised.value.error_code == "deadline_exceeded"
    assert finished.wait(1.0)
    assert exchange_calls == 0


@pytest.mark.parametrize(
    ("phase", "expected_closed"),
    [
        ("connect", {"raw"}),
        ("tls", {"raw", "tls"}),
        ("headers", {"raw", "tls", "response"}),
        ("body", {"raw", "tls", "response"}),
    ],
)
def test_transport_cancellation_closes_every_blocking_network_phase(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    expected_closed: set[str],
) -> None:
    state = _FakeNetworkState(blocking_phase=phase)
    _install_fake_network(monkeypatch, state)
    monkeypatch.setattr(xueqiu_execution, "_CANCELLATION_POLL_SECONDS", 0.001)
    transport = xueqiu_execution.XueqiuTransport(
        _resolver=lambda _host, _port: ("93.184.216.34",),
    )

    def checkpoint() -> None:
        if state.entered.is_set():
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        transport.search("600519", 1, bytearray(b"xq_a_token=secret"), checkpoint)

    assert state.block_exited.wait(1.0)
    assert expected_closed <= state.closed


def test_exchange_uses_one_absolute_deadline_across_all_body_reads_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _FakeNetworkState(
        phase_advances={
            "connect": 2.0,
            "tls": 2.0,
            "send": 2.0,
            "headers": 2.0,
            "body": 3.0,
        },
        stream_forever=True,
    )
    _install_fake_network(monkeypatch, state)
    attempt = xueqiu_execution._AttemptControl(lambda: None, state.clock)

    with pytest.raises(xueqiu_execution.XueqiuTransportFailure) as raised:
        xueqiu_execution._exchange_tls(
            "93.184.216.34",
            bytearray(b"request"),
            attempt,
        )

    assert raised.value.error_code == "deadline_exceeded"
    assert state.read_count == 3
    assert [timeout for owner, timeout in state.timeouts if owner == "tls"][-3:] == [
        7.0,
        4.0,
        1.0,
    ]
    assert {"raw", "tls", "response"} <= state.closed


@pytest.mark.parametrize(
    ("addresses", "error_code"),
    [
        (("127.0.0.1",), "permanent"),
        (("10.0.0.1",), "permanent"),
        ((), "not_found"),
    ],
)
def test_transport_rejects_non_global_or_missing_dns_before_exchange(
    addresses: tuple[str, ...],
    error_code: str,
) -> None:
    calls = 0

    def exchange(*_: object) -> tuple[int, tuple[tuple[str, str], ...], bytes]:
        nonlocal calls
        calls += 1
        raise AssertionError("exchange called")

    transport = xueqiu_execution.XueqiuTransport(
        _resolver=lambda _host, _port: addresses,
        _exchange=exchange,
    )

    with pytest.raises(xueqiu_execution.XueqiuTransportFailure) as raised:
        transport.search("600519", 1, bytearray(b"xq_a_token=secret"), lambda: None)

    assert raised.value.error_code == error_code
    assert calls == 0


def test_redirect_duplicate_json_keys_and_compression_fail_closed() -> None:
    def transport_for(
        status: int,
        headers: tuple[tuple[str, str], ...],
        body: bytes,
    ) -> xueqiu_execution.XueqiuTransport:
        return xueqiu_execution.XueqiuTransport(
            _resolver=lambda _host, _port: ("93.184.216.34",),
            _exchange=lambda _address, _request, _checkpoint: (status, headers, body),
        )

    cases = (
        transport_for(302, (("Location", "https://xueqiu.com/"),), b"{}"),
        transport_for(200, (("Content-Type", "application/json"),), b'{"stocks":[],"stocks":[]}'),
        transport_for(
            200,
            (("Content-Encoding", "gzip"), ("Content-Type", "application/json")),
            b"compressed",
        ),
    )
    for transport in cases:
        with pytest.raises(xueqiu_execution.XueqiuTransportFailure):
            transport.search("600519", 1, bytearray(b"xq_a_token=secret"), lambda: None)


def test_transport_rejects_missing_content_type() -> None:
    body = json.dumps({"stocks": []}).encode()
    transport = xueqiu_execution.XueqiuTransport(
        _resolver=lambda _host, _port: ("93.184.216.34",),
        _exchange=lambda _address, _request, _checkpoint: (200, (), body),
    )

    with pytest.raises(xueqiu_execution.XueqiuTransportFailure) as raised:
        transport.search("600519", 1, bytearray(b"xq_a_token=secret"), lambda: None)

    assert raised.value.error_code == "backend_contract_violation"


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json",
        "APPLICATION/JSON",
        "application/json; charset=utf-8",
        ' application/json ; Charset = "UTF-8" ',
    ],
)
def test_transport_accepts_only_json_with_optional_single_utf8_charset(
    content_type: str,
) -> None:
    assert xueqiu_execution._validate_response_headers((("Content-Type", content_type),)) is None


@pytest.mark.parametrize(
    "content_type",
    [
        "text/json",
        "application/problem+json",
        "application/json; charset=utf8",
        "application/json; charset=latin-1",
        "application/json; boundary=value",
        "application/json; charset",
        "application/json; =utf-8",
        'application/json; charset="utf-8',
        'application/json; charset=utf-8"',
        "application/json; charset=utf-8=extra",
        "application/json; charset=utf-8; version=1",
        "application/json; charset=utf-8; charset=utf-8",
        "application/json;",
    ],
)
def test_transport_rejects_unknown_duplicate_malformed_or_extra_content_type_parameters(
    content_type: str,
) -> None:
    with pytest.raises(xueqiu_execution.XueqiuTransportFailure) as raised:
        xueqiu_execution._validate_response_headers((("Content-Type", content_type),))

    assert raised.value.error_code == "permanent"


def test_transport_rejects_duplicate_content_type_headers() -> None:
    with pytest.raises(xueqiu_execution.XueqiuTransportFailure) as raised:
        xueqiu_execution._validate_response_headers(
            (
                ("Content-Type", "application/json"),
                ("content-type", "application/json; charset=utf-8"),
            )
        )

    assert raised.value.error_code == "backend_contract_violation"
