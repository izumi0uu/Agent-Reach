"""Offline execution and transport tests for the closed V2EX runtime."""

from __future__ import annotations

import json
import socket
from collections.abc import Callable, Iterator
from importlib.metadata import PackageNotFoundError
from types import SimpleNamespace
from typing import cast

import httpcore
import pytest

import agent_reach.execution.v1._v2ex_transport as v2ex_transport
import agent_reach.execution.v1.v2ex as v2ex_execution
from agent_reach.execution.v1 import (
    PROTOCOL_VERSION,
    ExecutionContextV1,
    ExecutionFailureV1,
    ExecutionLimitsV1,
    ExecutionRequestV1,
    ExecutionSuccessV1,
    NetworkAccessV1,
    execute,
)


def _request(operation: str, **arguments: object) -> ExecutionRequestV1:
    return ExecutionRequestV1(PROTOCOL_VERSION, "v2ex", operation, arguments)


def _context(
    *,
    maximum_items: int = 50,
    maximum_text_characters: int = 16_000,
    checkpoint: Callable[[], None] = lambda: None,
) -> ExecutionContextV1:
    return ExecutionContextV1(
        (NetworkAccessV1(),),
        checkpoint=checkpoint,
        limits=ExecutionLimitsV1(
            maximum_items=maximum_items,
            maximum_text_characters=maximum_text_characters,
        ),
    )


def _topic(
    topic_id: object = 42,
    *,
    node: object = "python",
    title: object = "Topic title",
    content: object = "Topic body",
    username: object = "alice",
    created: object = 1_700_000_000,
) -> dict[str, object]:
    return {
        "id": topic_id,
        "title": title,
        "content": content,
        "url": "https://attacker.invalid/topic",
        "node": {"name": node, "title": "Python"},
        "member": {"username": username},
        "created": created,
        "ignored": {"bounded": True},
    }


def _reply(
    reply_id: object = 7,
    *,
    content: object = "Reply body",
    username: object = "bob",
    created: object = 1_700_000_001,
) -> dict[str, object]:
    return {
        "id": reply_id,
        "content": content,
        "member": {"username": username},
        "created": created,
    }


def _profile(
    username: object = "Alice",
    *,
    member_id: object = 9,
    status: object = "found",
) -> dict[str, object]:
    return {
        "status": status,
        "id": member_id,
        "username": username,
        "url": "https://attacker.invalid/member",
        "bio": "  Builds   things ",
        "location": "Shanghai",
        "website": "https://example.com",
        "github": "alice",
        "created": 1_700_000_000,
    }


class FixtureTransport:
    def __init__(self, **responses: object) -> None:
        self.responses = responses
        self.calls: list[tuple[object, ...]] = []

    def _response(self, name: str, *arguments: object) -> object:
        self.calls.append((name, *arguments))
        response = self.responses[name]
        if isinstance(response, BaseException):
            raise response
        return response

    def fetch_hot_topics(self, checkpoint: Callable[[], None]) -> object:
        checkpoint()
        return self._response("hot")

    def fetch_node_topics(
        self,
        node: str,
        page: int,
        checkpoint: Callable[[], None],
    ) -> object:
        checkpoint()
        return self._response("node", node, page)

    def fetch_topic(self, topic_id: str, checkpoint: Callable[[], None]) -> object:
        checkpoint()
        return self._response("topic", topic_id)

    def fetch_replies(self, topic_id: str, checkpoint: Callable[[], None]) -> object:
        checkpoint()
        return self._response("replies", topic_id)

    def fetch_user(self, username: str, checkpoint: Callable[[], None]) -> object:
        checkpoint()
        return self._response("user", username)


def _install_fixture_transport(
    monkeypatch: pytest.MonkeyPatch,
    transport: FixtureTransport,
) -> None:
    monkeypatch.setattr(v2ex_execution, "load_v2ex_transport", lambda: transport)


def test_all_four_operations_project_closed_canonical_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FixtureTransport(
        hot=[
            _topic(2, title="  Second   topic  ", content="  second\nbody "),
            _topic(1, title="First topic", content=None, username=None),
        ],
        node=[_topic(3, node="Python")],
        topic=[_topic(42)],
        replies=[_reply(7)],
        user=_profile("Alice"),
    )
    _install_fixture_transport(monkeypatch, transport)

    hot = execute(_request("browse.hot", limit=2), _context())
    node = execute(
        _request("browse.node_topics", node="python", page=3, limit=5),
        _context(),
    )
    topic = execute(_request("read.topic", topic_id="00042"), _context())
    user = execute(_request("read.user", username="alice"), _context())

    assert isinstance(hot, ExecutionSuccessV1)
    assert [item.fields["native_id"] for item in hot.items] == ["2", "1"]
    assert hot.items[0].fields == {
        "text": "second body",
        "native_id": "2",
        "title": "Second topic",
        "url": "https://www.v2ex.com/t/2",
        "author": "alice",
        "published_at": "2023-11-14T22:13:20+00:00",
        "node": "python",
    }
    assert hot.items[1].fields["text"] is None
    assert hot.items[1].fields["author"] is None

    assert isinstance(node, ExecutionSuccessV1)
    assert node.items[0].fields["node"] == "Python"
    assert isinstance(topic, ExecutionSuccessV1)
    assert [item.schema_id for item in topic.items] == [
        "v2ex.topic.v1",
        "v2ex.reply.v1",
    ]
    assert topic.items[0].fields["native_id"] == "42"
    assert topic.items[1].fields == {
        "text": "Reply body",
        "native_id": "7",
        "url": "https://www.v2ex.com/t/42#reply7",
        "author": "bob",
        "published_at": "2023-11-14T22:13:21+00:00",
    }

    assert isinstance(user, ExecutionSuccessV1)
    assert user.items[0].fields == {
        "text": "Builds things Shanghai https://example.com alice",
        "native_id": "9",
        "title": "Alice",
        "url": "https://www.v2ex.com/member/Alice",
        "published_at": "2023-11-14T22:13:20+00:00",
    }
    assert transport.calls == [
        ("hot",),
        ("node", "python", 3),
        ("topic", "42"),
        ("replies", "42"),
        ("user", "alice"),
    ]


def test_browse_limits_and_text_bounds_report_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FixtureTransport(
        hot=[
            _topic(1, content="abcdef"),
            _topic(2, content="second"),
            _topic(3, content="third"),
        ]
    )
    _install_fixture_transport(monkeypatch, transport)

    result = execute(
        _request("browse.hot", limit=3),
        _context(maximum_items=2, maximum_text_characters=4),
    )

    assert isinstance(result, ExecutionSuccessV1)
    assert len(result.items) == 2
    assert result.items[0].fields["text"] == "abcd"
    assert result.truncated is True


def test_profile_component_truncation_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile("alice")
    profile["bio"] = "x" * 16_001
    transport = FixtureTransport(user=profile)
    _install_fixture_transport(monkeypatch, transport)

    result = execute(_request("read.user", username="alice"), _context())

    assert isinstance(result, ExecutionSuccessV1)
    assert result.truncated is True
    assert len(cast(str, result.items[0].fields["text"])) == 16_000


def test_node_topics_validate_every_returned_node_before_slicing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FixtureTransport(node=[_topic(1, node="python"), _topic(2, node="jobs")])
    _install_fixture_transport(monkeypatch, transport)

    result = execute(
        _request("browse.node_topics", node="python", page=2, limit=1),
        _context(),
    )

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_contract_violation"


@pytest.mark.parametrize(
    "error_code",
    [
        "not_found",
        "authentication",
        "authorization",
        "rate_limit",
        "transient",
        "permanent",
        "backend_contract_violation",
    ],
)
def test_reply_transport_failures_are_truthful_topic_only_partials(
    monkeypatch: pytest.MonkeyPatch,
    error_code: str,
) -> None:
    transport = FixtureTransport(
        topic=[_topic(42)],
        replies=v2ex_transport.V2exTransportFailure(
            cast(v2ex_execution.ExecutionErrorCodeV1, error_code)
        ),
    )
    _install_fixture_transport(monkeypatch, transport)

    result = execute(_request("read.topic", topic_id="42"), _context())

    assert isinstance(result, ExecutionSuccessV1)
    assert len(result.items) == 1
    assert result.items[0].schema_id == "v2ex.topic.v1"
    assert result.partial_error_code == error_code
    assert transport.calls == [("topic", "42"), ("replies", "42")]


@pytest.mark.parametrize(
    "replies",
    [
        {"not": "a list"},
        [_reply(1), _reply(1)],
        [_reply(True)],
        [_reply(1, username="bad username")],
        [_reply(1, content="   ")],
    ],
)
def test_reply_shape_is_validated_atomically_as_topic_only_partial(
    monkeypatch: pytest.MonkeyPatch,
    replies: object,
) -> None:
    transport = FixtureTransport(topic=[_topic(42)], replies=replies)
    _install_fixture_transport(monkeypatch, transport)

    result = execute(_request("read.topic", topic_id="42"), _context())

    assert isinstance(result, ExecutionSuccessV1)
    assert len(result.items) == 1
    assert result.partial_error_code == "backend_contract_violation"


def test_reply_page_is_capped_after_complete_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies = [_reply(index, content=f"reply {index}") for index in range(1, 22)]
    transport = FixtureTransport(topic=[_topic(42)], replies=replies)
    _install_fixture_transport(monkeypatch, transport)

    result = execute(_request("read.topic", topic_id="42"), _context())

    assert isinstance(result, ExecutionSuccessV1)
    assert len(result.items) == 21
    assert result.truncated is True
    assert result.partial_error_code is None

    replies.append(_reply(1))
    invalid = execute(_request("read.topic", topic_id="42"), _context())
    assert isinstance(invalid, ExecutionSuccessV1)
    assert invalid.partial_error_code == "backend_contract_violation"
    assert len(invalid.items) == 1


def test_read_topic_respects_host_item_narrowing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FixtureTransport(
        topic=[_topic(42)],
        replies=[_reply(1), _reply(2), _reply(3)],
    )
    _install_fixture_transport(monkeypatch, transport)

    result = execute(
        _request("read.topic", topic_id="42"),
        _context(maximum_items=2),
    )

    assert isinstance(result, ExecutionSuccessV1)
    assert [item.fields["native_id"] for item in result.items] == ["42", "1"]
    assert result.truncated is True


@pytest.mark.parametrize(
    ("operation", "arguments", "response_name", "response", "error_code"),
    [
        ("read.topic", {"topic_id": "42"}, "topic", [], "not_found"),
        (
            "read.topic",
            {"topic_id": "42"},
            "topic",
            [_topic(43)],
            "backend_contract_violation",
        ),
        (
            "read.user",
            {"username": "alice"},
            "user",
            {"status": "notfound"},
            "not_found",
        ),
        (
            "read.user",
            {"username": "alice"},
            "user",
            _profile("mallory"),
            "backend_contract_violation",
        ),
        (
            "browse.hot",
            {"limit": 1},
            "hot",
            [_topic(True)],
            "backend_contract_violation",
        ),
        (
            "browse.hot",
            {"limit": 1},
            "hot",
            [_topic(1, created=(1 << 53))],
            "backend_contract_violation",
        ),
    ],
)
def test_identity_not_found_and_scalar_failures_are_closed(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    arguments: dict[str, object],
    response_name: str,
    response: object,
    error_code: str,
) -> None:
    transport = FixtureTransport(**{response_name: response})
    _install_fixture_transport(monkeypatch, transport)

    result = execute(_request(operation, **arguments), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == error_code
    assert not hasattr(result, "message")


@pytest.mark.parametrize("error_code", ["backend_unavailable", "backend_incompatible"])
def test_backend_gate_failures_have_closed_provenance(
    monkeypatch: pytest.MonkeyPatch,
    error_code: str,
) -> None:
    monkeypatch.setattr(v2ex_execution, "load_v2ex_transport", lambda: error_code)

    result = execute(_request("browse.hot", limit=1), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == error_code
    assert result.backend_id == "v2ex-public-api"
    assert result.backend_version == "legacy-json-2026-07-31"


def test_invalid_direct_request_does_not_load_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_load() -> object:
        raise AssertionError("invalid request loaded the backend")

    monkeypatch.setattr(v2ex_execution, "load_v2ex_transport", unexpected_load)

    result = v2ex_execution.execute_v2ex(
        _request("browse.hot", limit=True),
        _context(),
    )

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_contract_violation"

    missing_capability = v2ex_execution.execute_v2ex(
        _request("browse.hot", limit=1),
        ExecutionContextV1(),
    )
    assert isinstance(missing_capability, ExecutionFailureV1)
    assert missing_capability.error_code == "backend_contract_violation"


def test_checkpoint_exception_propagates_without_becoming_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StopExecution(Exception):
        pass

    checkpoints = 0

    def checkpoint() -> None:
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints == 4:
            raise StopExecution("stop")

    transport = FixtureTransport(topic=[_topic(42)], replies=[_reply(1)])
    _install_fixture_transport(monkeypatch, transport)

    with pytest.raises(StopExecution, match="stop"):
        execute(
            _request("read.topic", topic_id="42"),
            _context(checkpoint=checkpoint),
        )


class _FakeTimeout(Exception):
    pass


class _FakeNetworkError(Exception):
    pass


class _FakeProtocolError(Exception):
    pass


class _FakeUnderlyingBackend:
    def __init__(self, calls: list[tuple[object, ...]]) -> None:
        self.calls = calls

    def connect_tcp(self, host: str, port: int, **kwargs: object) -> object:
        self.calls.append(("connect", host, port, kwargs))
        return object()

    def sleep(self, seconds: float) -> None:
        self.calls.append(("sleep", seconds))


class _FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        headers: list[tuple[bytes, bytes]] | None = None,
        stream_error: Exception | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or [(b"content-type", b"application/json; charset=utf-8")]
        self.body = body
        self.stream_error = stream_error
        self.closed = False

    def iter_stream(self) -> Iterator[bytes]:
        midpoint = len(self.body) // 2
        yield self.body[:midpoint]
        if self.stream_error is not None:
            raise self.stream_error
        yield self.body[midpoint:]


class _FakeResponseManager:
    def __init__(
        self,
        response: _FakeResponse,
        exit_error: BaseException | None = None,
    ) -> None:
        self.response = response
        self.exit_error = exit_error

    def __enter__(self) -> _FakeResponse:
        return self.response

    def __exit__(self, *_: object) -> None:
        self.response.closed = True
        if self.exit_error is not None:
            raise self.exit_error


class _TransportHarness:
    def __init__(
        self,
        response: _FakeResponse,
        *,
        response_exit_error: BaseException | None = None,
    ) -> None:
        self.response = response
        self.response_exit_error = response_exit_error
        self.pool_arguments: list[dict[str, object]] = []
        self.requests: list[dict[str, object]] = []
        self.backend_calls: list[tuple[object, ...]] = []
        self.pool_closed = False

    def backend_factory(self) -> object:
        return _FakeUnderlyingBackend(self.backend_calls)

    def pool_factory(self, **kwargs: object) -> object:
        self.pool_arguments.append(kwargs)
        harness = self

        class Pool:
            def stream(
                self,
                method: str,
                url: object,
                **request_kwargs: object,
            ) -> object:
                backend = cast(
                    v2ex_transport._PinnedNetworkBackend,
                    kwargs["network_backend"],
                )
                backend.connect_tcp("www.v2ex.com", 443, timeout=5.0)
                harness.requests.append({"method": method, "url": url, **request_kwargs})
                return _FakeResponseManager(
                    harness.response,
                    harness.response_exit_error,
                )

            def close(self) -> None:
                harness.pool_closed = True

        return Pool()

    def url_factory(self, **kwargs: object) -> object:
        return SimpleNamespace(**kwargs)

    def transport(
        self,
        resolver: Callable[[str, int], tuple[str, ...]] | None = None,
    ) -> v2ex_transport.V2exTransport:
        api = v2ex_transport._HttpcoreApi(
            pool_factory=self.pool_factory,
            url_factory=self.url_factory,
            backend_factory=self.backend_factory,
            timeout_errors=(_FakeTimeout,),
            network_errors=(_FakeNetworkError,),
            protocol_errors=(_FakeProtocolError,),
        )
        return v2ex_transport.V2exTransport(
            api,
            cast(
                v2ex_transport._Resolver,
                resolver or (lambda _host, _port: ("2606:4700:4700::1111", "8.8.8.8")),
            ),
        )


def test_transport_fixes_route_origin_headers_tls_identity_and_pool_policy() -> None:
    harness = _TransportHarness(_FakeResponse(b"[]"))
    transport = harness.transport()

    assert transport.fetch_hot_topics(lambda: None) == []
    assert transport.fetch_node_topics("python", 3, lambda: None) == []
    assert transport.fetch_topic("42", lambda: None) == []
    assert transport.fetch_replies("42", lambda: None) == []
    assert transport.fetch_user("Alice", lambda: None) == []

    assert [request["method"] for request in harness.requests] == ["GET"] * 5
    assert [request["url"].target for request in harness.requests] == [
        "/api/topics/hot.json",
        "/api/topics/show.json?node_name=python&page=3",
        "/api/topics/show.json?id=42",
        "/api/replies/show.json?topic_id=42&page=1",
        "/api/members/show.json?username=Alice",
    ]
    assert all(request["url"].scheme == "https" for request in harness.requests)
    assert all(request["url"].host == "www.v2ex.com" for request in harness.requests)
    assert all(request["url"].port == 443 for request in harness.requests)
    assert all(request["headers"] == v2ex_transport._HEADERS for request in harness.requests)
    assert all(
        request["extensions"]
        == {
            "timeout": {
                "pool": 5.0,
                "connect": 5.0,
                "read": 5.0,
                "write": 5.0,
            }
        }
        for request in harness.requests
    )
    assert all(arguments["proxy"] is None for arguments in harness.pool_arguments)
    assert all(arguments["http1"] is True for arguments in harness.pool_arguments)
    assert all(arguments["http2"] is False for arguments in harness.pool_arguments)
    assert all(arguments["retries"] == 0 for arguments in harness.pool_arguments)
    assert [call[1] for call in harness.backend_calls] == ["8.8.8.8"] * 5
    assert harness.response.closed is True
    assert harness.pool_closed is True


class _WireStream:
    def __init__(self, response: bytes, events: list[tuple[object, ...]]) -> None:
        self._response = bytearray(response)
        self.events = events

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        self.events.append(("read", max_bytes, timeout))
        chunk = bytes(self._response[:max_bytes])
        del self._response[:max_bytes]
        return chunk

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.events.append(("write", buffer, timeout))

    def close(self) -> None:
        self.events.append(("close",))

    def start_tls(
        self,
        ssl_context: object,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> _WireStream:
        self.events.append(("tls", server_hostname, timeout, ssl_context))
        return self

    def get_extra_info(self, info: str) -> object:
        if info == "is_readable":
            return False
        return None


class _WireBackend:
    def __init__(self, stream: _WireStream, events: list[tuple[object, ...]]) -> None:
        self.stream = stream
        self.events = events

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: object = None,
    ) -> _WireStream:
        self.events.append(("connect", host, port, timeout, local_address, socket_options))
        return self.stream

    def connect_unix_socket(self, *_: object, **__: object) -> object:
        raise AssertionError("unexpected Unix socket")

    def sleep(self, seconds: float) -> None:
        raise AssertionError(f"unexpected retry sleep {seconds}")


def test_exact_httpcore_uses_pinned_tcp_with_origin_host_and_tls_sni() -> None:
    events: list[tuple[object, ...]] = []
    response = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/json; charset=utf-8\r\n"
        b"Content-Length: 2\r\n"
        b"Connection: close\r\n\r\n[]"
    )
    stream = _WireStream(response, events)
    backend = _WireBackend(stream, events)
    api = v2ex_transport._HttpcoreApi(
        pool_factory=httpcore.ConnectionPool,
        url_factory=httpcore.URL,
        backend_factory=lambda: backend,
        timeout_errors=(httpcore.TimeoutException,),
        network_errors=(httpcore.NetworkError,),
        protocol_errors=(httpcore.ProtocolError,),
    )
    transport = v2ex_transport.V2exTransport(
        api,
        cast(v2ex_transport._Resolver, lambda _host, _port: ("8.8.8.8",)),
    )

    assert transport.fetch_node_topics("python", 3, lambda: None) == []
    assert events[0][:5] == ("connect", "8.8.8.8", 443, 5.0, None)
    tls = next(event for event in events if event[0] == "tls")
    assert tls[1:3] == ("www.v2ex.com", 5.0)
    request_bytes = b"".join(cast(bytes, event[1]) for event in events if event[0] == "write")
    assert request_bytes.startswith(
        b"GET /api/topics/show.json?node_name=python&page=3 HTTP/1.1\r\n"
    )
    assert b"Host: www.v2ex.com\r\n" in request_bytes


def test_transport_rejects_any_unsafe_dns_answer_before_pool_creation() -> None:
    harness = _TransportHarness(_FakeResponse(b"[]"))
    transport = harness.transport(lambda _host, _port: ("8.8.8.8", "127.0.0.1"))

    with pytest.raises(v2ex_transport.V2exTransportFailure) as raised:
        transport.fetch_hot_topics(lambda: None)

    assert raised.value.error_code == "permanent"
    assert harness.pool_arguments == []
    assert harness.backend_calls == []


def test_pinned_backend_denies_origin_change_and_unix_sockets() -> None:
    backend = v2ex_transport._PinnedNetworkBackend(
        "8.8.8.8",
        _FakeUnderlyingBackend([]),
    )

    with pytest.raises(v2ex_transport.V2exTransportFailure):
        backend.connect_tcp("attacker.invalid", 443)
    with pytest.raises(v2ex_transport.V2exTransportFailure):
        backend.connect_tcp("www.v2ex.com", 80)
    with pytest.raises(v2ex_transport.V2exTransportFailure):
        backend.connect_unix_socket("/tmp/socket")


@pytest.mark.parametrize(
    ("status", "error_code"),
    [
        (301, "backend_contract_violation"),
        (404, "not_found"),
        (408, "transient"),
        (500, "transient"),
        (401, "authentication"),
        (403, "authorization"),
        (429, "rate_limit"),
        (400, "permanent"),
    ],
)
def test_transport_maps_status_without_following_redirects(
    status: int,
    error_code: str,
) -> None:
    harness = _TransportHarness(_FakeResponse(b"[]", status=status))

    with pytest.raises(v2ex_transport.V2exTransportFailure) as raised:
        harness.transport().fetch_hot_topics(lambda: None)

    assert raised.value.error_code == error_code
    assert harness.response.closed is True
    assert harness.pool_closed is True


@pytest.mark.parametrize(
    ("headers", "body", "error_code"),
    [
        ([(b"content-encoding", b"gzip")], b"[]", "permanent"),
        ([(b"content-type", b"text/html")], b"[]", "permanent"),
        (
            [(b"content-type", b"application/json; charset=latin-1")],
            b"[]",
            "permanent",
        ),
        (
            [(b"content-length", b"1048577")],
            b"[]",
            "permanent",
        ),
        (
            [(b"content-length", b"not-a-number")],
            b"[]",
            "backend_contract_violation",
        ),
        (
            [(b"content-length", b"3")],
            b"[]",
            "backend_contract_violation",
        ),
        (
            [(b"content-type", b"application/json"), (b"Content-Type", b"text/json")],
            b"[]",
            "backend_contract_violation",
        ),
        ([(b"content-type", b"application/json")], b"\xff", "backend_contract_violation"),
    ],
)
def test_transport_rejects_unsupported_or_malformed_responses(
    headers: list[tuple[bytes, bytes]],
    body: bytes,
    error_code: str,
) -> None:
    harness = _TransportHarness(_FakeResponse(body, headers=headers))

    with pytest.raises(v2ex_transport.V2exTransportFailure) as raised:
        harness.transport().fetch_hot_topics(lambda: None)

    assert raised.value.error_code == error_code


def test_transport_enforces_streamed_byte_limit() -> None:
    body = b"[" + (b" " * 1_048_575) + b"]"
    harness = _TransportHarness(_FakeResponse(body))

    with pytest.raises(v2ex_transport.V2exTransportFailure) as raised:
        harness.transport().fetch_hot_topics(lambda: None)

    assert raised.value.error_code == "permanent"
    assert harness.response.closed is True
    assert harness.pool_closed is True


def test_strict_json_rejects_oversize_direct_input() -> None:
    with pytest.raises(v2ex_transport.V2exTransportFailure) as raised:
        v2ex_transport._decode_json(b" " * 1_048_577)

    assert raised.value.error_code == "permanent"


@pytest.mark.parametrize(
    "body",
    [
        b'{"duplicate":1,"duplicate":2}',
        b'{"number":NaN}',
        b'{"number":Infinity}',
        b'{"number":9007199254740992}',
        b'{"text":"\\ud800"}',
        json.dumps([[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]).encode(),
    ],
)
def test_strict_json_rejects_duplicate_nonfinite_unsafe_or_deep_values(
    body: bytes,
) -> None:
    with pytest.raises(v2ex_transport.V2exTransportFailure) as raised:
        v2ex_transport._decode_json(body)

    assert raised.value.error_code == "backend_contract_violation"


def test_strict_json_validates_ignored_fields_before_projection() -> None:
    body = json.dumps(
        [_topic(1) | {"ignored_integer": 1 << 53}],
        separators=(",", ":"),
    ).encode()

    with pytest.raises(v2ex_transport.V2exTransportFailure) as raised:
        v2ex_transport._decode_json(body)

    assert raised.value.error_code == "backend_contract_violation"


def test_transport_timeout_is_closed_and_always_closes() -> None:
    harness = _TransportHarness(_FakeResponse(b"[]", stream_error=_FakeTimeout("private target")))

    with pytest.raises(v2ex_transport.V2exTransportFailure) as raised:
        harness.transport().fetch_hot_topics(lambda: None)

    assert raised.value.error_code == "transient"
    assert "private target" not in str(raised.value)
    assert harness.response.closed is True
    assert harness.pool_closed is True


class _Cancellation(BaseException):
    pass


@pytest.mark.parametrize("checkpoint_error", [TimeoutError("timeout"), _Cancellation()])
def test_response_cleanup_cannot_mask_checkpoint_exception(
    monkeypatch: pytest.MonkeyPatch,
    checkpoint_error: BaseException,
) -> None:
    harness = _TransportHarness(
        _FakeResponse(b"[]"),
        response_exit_error=OSError("cleanup failed"),
    )
    monkeypatch.setattr(
        v2ex_execution,
        "load_v2ex_transport",
        harness.transport,
    )

    def checkpoint() -> None:
        if harness.requests:
            raise checkpoint_error

    with pytest.raises(BaseException) as raised:
        execute(
            _request("browse.hot", limit=1),
            _context(checkpoint=checkpoint),
        )

    assert raised.value is checkpoint_error
    assert harness.response.closed is True
    assert harness.pool_closed is True


def test_transport_loader_gates_exact_metadata_before_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported: list[str] = []
    monkeypatch.setattr(v2ex_transport, "version", lambda _name: "1.0.8")
    monkeypatch.setattr(
        v2ex_transport,
        "import_module",
        lambda name: imported.append(name),
    )

    assert v2ex_transport.load_v2ex_transport() == "backend_incompatible"
    assert imported == []

    def missing(_name: str) -> str:
        raise PackageNotFoundError("httpcore")

    monkeypatch.setattr(v2ex_transport, "version", missing)
    assert v2ex_transport.load_v2ex_transport() == "backend_unavailable"
    assert imported == []


def test_transport_loader_rejects_module_version_or_api_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(v2ex_transport, "version", lambda _name: "1.0.9")
    monkeypatch.setattr(
        v2ex_transport,
        "import_module",
        lambda _name: SimpleNamespace(__version__="1.0.8"),
    )
    assert v2ex_transport.load_v2ex_transport() == "backend_incompatible"

    monkeypatch.setattr(
        v2ex_transport,
        "import_module",
        lambda _name: SimpleNamespace(__version__="1.0.9"),
    )
    assert v2ex_transport.load_v2ex_transport() == "backend_incompatible"


def test_resolver_uses_only_fixed_dns_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []

    def getaddrinfo(*args: object, **kwargs: object) -> list[tuple[object, ...]]:
        calls.append((*args, kwargs))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]

    monkeypatch.setattr(v2ex_transport.socket, "getaddrinfo", getaddrinfo)

    assert v2ex_transport._resolve_public_addresses("www.v2ex.com", 443) == ("8.8.8.8",)
    assert calls == [
        (
            "www.v2ex.com",
            443,
            {
                "family": socket.AF_UNSPEC,
                "type": socket.SOCK_STREAM,
                "proto": socket.IPPROTO_TCP,
            },
        )
    ]
    with pytest.raises(v2ex_transport.V2exTransportFailure):
        v2ex_transport._resolve_public_addresses("attacker.invalid", 443)
