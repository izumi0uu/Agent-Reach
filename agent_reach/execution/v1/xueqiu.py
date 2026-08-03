"""Fork-owned execution for the fixed Xueqiu stock-search endpoint."""

from __future__ import annotations

import http.client
import ipaddress
import json
import math
import socket
import ssl
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Protocol, TypeVar, cast
from urllib.parse import urlencode

from .contracts import (
    MAX_TITLE_CHARACTERS,
    PROTOCOL_VERSION,
    ExecutionContextV1,
    ExecutionErrorCodeV1,
    ExecutionFailureV1,
    ExecutionItemV1,
    ExecutionRequestV1,
    ExecutionResultV1,
    ExecutionSuccessV1,
    XueqiuSessionV1,
    _contains_invalid_scalar,
    _valid_xueqiu_stock_identity,
)

_BACKEND_ID: Final = "xueqiu-api"
_BACKEND_VERSION: Final = "1.5.0+search.v1"
_ORIGIN_HOST: Final = "xueqiu.com"
_ORIGIN_PORT: Final = 443
_SEARCH_PATH: Final = "/stock/search.json"
_USER_AGENT: Final = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_MAX_QUERY_CHARACTERS: Final = 4_096
_MAX_ITEMS: Final = 50
_MAX_RESPONSE_BYTES: Final = 512 * 1_024
_MAX_DECODED_CHARACTERS: Final = 125_000
_MAX_HEADER_COUNT: Final = 128
_MAX_HEADER_BYTES: Final = 32_768
_MAX_JSON_DEPTH: Final = 12
_MAX_JSON_NODES: Final = 10_000
_MAX_JSON_STRINGS: Final = 5_000
_MAX_JSON_SCALARS: Final = 10_000
_MAX_JSON_STRING_CHARACTERS: Final = 125_000
_MAX_JSON_INTEGER: Final = (1 << 53) - 1
_ATTEMPT_TIMEOUT_SECONDS: Final = 15.0
_CONNECT_TIMEOUT_SECONDS: Final = 5.0
_READ_TIMEOUT_SECONDS: Final = 8.0
_CANCELLATION_POLL_SECONDS: Final = 0.05
_READ_CHUNK_BYTES: Final = 64 * 1_024

_BlockingValue = TypeVar("_BlockingValue")


class _Resolver(Protocol):
    def __call__(self, host: str, port: int) -> tuple[str, ...]: ...


class _Exchange(Protocol):
    def __call__(
        self,
        pinned_ip: str,
        request_bytes: bytearray,
        attempt: _AttemptControl,
    ) -> tuple[int, tuple[tuple[str, str], ...], bytes]: ...


class XueqiuTransportFailure(Exception):
    """A closed transport failure carrying no query, Cookie, or response text."""

    def __init__(self, error_code: ExecutionErrorCodeV1) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class _AttemptControl:
    """One monotonic attempt budget with cooperative resource cancellation."""

    __slots__ = (
        "_cancelled",
        "_checkpoint",
        "_clock",
        "_closeables",
        "_deadline",
        "_lock",
    )

    def __init__(self, checkpoint: Callable[[], None], clock: Callable[[], float]) -> None:
        self._checkpoint = checkpoint
        self._clock = clock
        self._deadline = clock() + _ATTEMPT_TIMEOUT_SECONDS
        self._cancelled = False
        self._closeables: list[object] = []
        self._lock = threading.Lock()

    def __call__(self) -> None:
        self._raise_if_expired()

    def checkpoint(self) -> None:
        self._checkpoint()
        self._raise_if_expired()

    def remaining_timeout(self, maximum: float) -> float:
        self._raise_if_expired()
        remaining = self._deadline - self._clock()
        if remaining <= 0:
            raise XueqiuTransportFailure("deadline_exceeded")
        return min(maximum, remaining)

    def wait_timeout(self) -> float:
        return self.remaining_timeout(_CANCELLATION_POLL_SECONDS)

    def register(self, closeable: object) -> None:
        if not callable(getattr(closeable, "close", None)):
            raise XueqiuTransportFailure("backend_contract_violation")
        close_now = False
        with self._lock:
            if self._cancelled:
                close_now = True
            elif all(existing is not closeable for existing in self._closeables):
                self._closeables.append(closeable)
        if close_now:
            _close_network_resource(closeable)
            raise XueqiuTransportFailure("deadline_exceeded")

    def unregister(self, closeable: object) -> None:
        with self._lock:
            self._closeables = [
                existing for existing in self._closeables if existing is not closeable
            ]

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            closeables = tuple(reversed(self._closeables))
            self._closeables.clear()
        for closeable in closeables:
            _close_network_resource(closeable)

    def _raise_if_expired(self) -> None:
        with self._lock:
            cancelled = self._cancelled
        if cancelled or self._clock() >= self._deadline:
            raise XueqiuTransportFailure("deadline_exceeded")


def _run_blocking(
    operation: Callable[[], _BlockingValue],
    attempt: _AttemptControl,
) -> _BlockingValue:
    """Run one blocking phase while the calling thread polls its host checkpoint."""

    done = threading.Event()
    values: list[_BlockingValue] = []
    failures: list[BaseException] = []

    def run() -> None:
        try:
            values.append(operation())
        except BaseException as error:
            failures.append(error)
        finally:
            done.set()

    worker = threading.Thread(
        target=run,
        name="agent-reach-xueqiu-attempt",
        daemon=True,
    )
    worker.start()
    try:
        while not done.is_set():
            attempt.checkpoint()
            done.wait(attempt.wait_timeout())
        attempt.checkpoint()
    except BaseException:
        attempt.cancel()
        raise

    if failures:
        raise failures[0]
    if len(values) != 1:
        raise XueqiuTransportFailure("backend_contract_violation")
    return values[0]


def _close_network_resource(resource: object) -> None:
    shutdown = getattr(resource, "shutdown", None)
    if callable(shutdown):
        try:
            shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
    close = getattr(resource, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


@dataclass(frozen=True, slots=True)
class XueqiuTransport:
    """One fixed HTTPS stock-search transport with no ambient session state."""

    _resolver: _Resolver = field(default_factory=lambda: _resolve_public_addresses)
    _exchange: _Exchange = field(default_factory=lambda: _exchange_tls)
    _clock: Callable[[], float] = field(default_factory=lambda: time.monotonic)

    def search(
        self,
        query: str,
        limit: int,
        cookie_header: bytearray,
        checkpoint: Callable[[], None],
    ) -> object:
        if (
            type(query) is not str
            or query != query.strip()
            or not 1 <= len(query) <= _MAX_QUERY_CHARACTERS
            or _contains_invalid_scalar(query)
            or type(limit) is not int
            or not 1 <= limit <= _MAX_ITEMS
            or type(cookie_header) is not bytearray
            or not cookie_header
        ):
            raise XueqiuTransportFailure("backend_contract_violation")

        attempt = _AttemptControl(checkpoint, self._clock)
        try:
            attempt.checkpoint()
            try:
                addresses = _run_blocking(
                    lambda: self._resolver(_ORIGIN_HOST, _ORIGIN_PORT),
                    attempt,
                )
            except XueqiuTransportFailure:
                raise
            except socket.gaierror as error:
                missing = error.errno == getattr(socket, "EAI_NONAME", None)
                raise XueqiuTransportFailure("not_found" if missing else "transient") from None
            except Exception:
                raise XueqiuTransportFailure("transient") from None
            pinned_ip = _select_public_address(addresses)
            attempt.checkpoint()

            target = f"{_SEARCH_PATH}?{urlencode((('code', query), ('size', str(limit))))}"
            request_bytes = _request_bytes(target, cookie_header)
            try:
                try:
                    status, headers, body = _run_blocking(
                        lambda: self._exchange(pinned_ip, request_bytes, attempt),
                        attempt,
                    )
                except XueqiuTransportFailure:
                    raise
                except (TimeoutError, socket.timeout, OSError, ssl.SSLError):
                    raise XueqiuTransportFailure("transient") from None
                except Exception:
                    raise XueqiuTransportFailure("backend_contract_violation") from None
            finally:
                request_bytes[:] = b"\x00" * len(request_bytes)

            attempt.checkpoint()
            _raise_for_status(status)
            declared_length = _validate_response_headers(headers)
            if declared_length is not None and declared_length != len(body):
                raise XueqiuTransportFailure("backend_contract_violation")
            decoded = _decode_json(body)
            attempt.checkpoint()
            return decoded
        finally:
            attempt.cancel()


def execute_xueqiu(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
) -> ExecutionResultV1:
    """Execute one registry-validated Xueqiu stock search."""

    session = _session_from_context(context)
    if not _valid_request(request) or session is None:
        if session is not None:
            session.close()
        return _failure(request, "backend_contract_violation")

    try:
        _checkpoint(context)
        requested_limit = cast(int, request.arguments["limit"])
        effective_limit = min(requested_limit, context.limits.maximum_items, _MAX_ITEMS)
        raw = XueqiuTransport().search(
            cast(str, request.arguments["query"]),
            effective_limit,
            session.cookie_header,
            lambda: _checkpoint(context),
        )
        items = _project_stocks(raw, maximum_items=effective_limit)
        _checkpoint(context)
        return ExecutionSuccessV1(
            protocol_version=PROTOCOL_VERSION,
            source="xueqiu",
            operation="search.stocks",
            backend_id=_BACKEND_ID,
            backend_version=_BACKEND_VERSION,
            items=items,
            truncated=requested_limit > effective_limit,
            partial_error_code=None,
        )
    except _CheckpointRaised as raised:
        raise raised.original
    except XueqiuTransportFailure as error:
        return _failure(request, error.error_code)
    except (_XueqiuDataError, TypeError, ValueError):
        return _failure(request, "backend_contract_violation")
    except Exception:
        return _failure(request, "transient")
    finally:
        session.close()


def _valid_request(request: ExecutionRequestV1) -> bool:
    if (
        type(request) is not ExecutionRequestV1
        or request.protocol_version != PROTOCOL_VERSION
        or request.source != "xueqiu"
        or request.operation != "search.stocks"
        or set(request.arguments) != {"query", "limit"}
    ):
        return False
    query = request.arguments["query"]
    limit = request.arguments["limit"]
    return bool(
        type(query) is str
        and query == query.strip()
        and 1 <= len(query) <= _MAX_QUERY_CHARACTERS
        and not _contains_invalid_scalar(query)
        and type(limit) is int
        and 1 <= limit <= _MAX_ITEMS
    )


def _session_from_context(context: ExecutionContextV1) -> XueqiuSessionV1 | None:
    if type(context) is not ExecutionContextV1 or len(context.host_capabilities) != 1:
        return None
    session = context.host_capabilities[0]
    return session if type(session) is XueqiuSessionV1 else None


class _XueqiuDataError(Exception):
    pass


def _project_stocks(value: object, *, maximum_items: int) -> tuple[ExecutionItemV1, ...]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise _XueqiuDataError("response invalid")
    stocks = value.get("stocks")
    if not isinstance(stocks, list) or len(stocks) > maximum_items:
        raise _XueqiuDataError("stocks invalid")

    projected: list[ExecutionItemV1] = []
    seen: set[str] = set()
    for raw in stocks:
        if not isinstance(raw, Mapping) or any(type(key) is not str for key in raw):
            raise _XueqiuDataError("stock invalid")
        symbol, exchange = _stock_identity(raw.get("code"), raw.get("exchange"))
        name = _stock_name(raw.get("name"))
        if symbol in seen:
            raise _XueqiuDataError("duplicate stock")
        seen.add(symbol)
        projected.append(
            ExecutionItemV1(
                "xueqiu.stock.v1",
                {"symbol": symbol, "name": name, "exchange": exchange},
            )
        )
    return tuple(projected)


def _stock_identity(code: object, exchange: object) -> tuple[str, str]:
    if not _valid_xueqiu_stock_identity(code, exchange):
        raise _XueqiuDataError("stock identity invalid")
    return cast(str, code), cast(str, exchange)


def _stock_name(value: object) -> str:
    if (
        type(value) is not str
        or value != value.strip()
        or not 1 <= len(value) <= MAX_TITLE_CHARACTERS
        or _contains_invalid_scalar(value)
    ):
        raise _XueqiuDataError("stock name invalid")
    return value


def _request_bytes(target: str, cookie_header: bytearray) -> bytearray:
    try:
        target_bytes = target.encode("ascii", errors="strict")
    except UnicodeError:
        raise XueqiuTransportFailure("backend_contract_violation") from None
    request = bytearray()
    request.extend(b"GET ")
    request.extend(target_bytes)
    request.extend(b" HTTP/1.1\r\nHost: ")
    request.extend(_ORIGIN_HOST.encode("ascii"))
    request.extend(b"\r\nAccept: application/json\r\n")
    request.extend(b"Accept-Encoding: identity\r\nConnection: close\r\nUser-Agent: ")
    request.extend(_USER_AGENT.encode("ascii"))
    request.extend(b"\r\nReferer: https://xueqiu.com/\r\nCookie: ")
    request.extend(cookie_header)
    request.extend(b"\r\n\r\n")
    if len(request) > 16_384 or b"\r" in cookie_header or b"\n" in cookie_header:
        request[:] = b"\x00" * len(request)
        raise XueqiuTransportFailure("backend_contract_violation")
    return request


def _exchange_tls(
    pinned_ip: str,
    request_bytes: bytearray,
    attempt: _AttemptControl,
) -> tuple[int, tuple[tuple[str, str], ...], bytes]:
    raw_socket: socket.socket | None = None
    tls_socket: ssl.SSLSocket | None = None
    response: http.client.HTTPResponse | None = None
    try:
        try:
            parsed_address = ipaddress.ip_address(pinned_ip)
        except ValueError:
            raise XueqiuTransportFailure("permanent") from None
        if not _is_public_address(parsed_address):
            raise XueqiuTransportFailure("permanent")
        family = socket.AF_INET6 if parsed_address.version == 6 else socket.AF_INET
        raw_socket = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        attempt.register(raw_socket)
        raw_socket.settimeout(attempt.remaining_timeout(_CONNECT_TIMEOUT_SECONDS))
        raw_socket.connect(
            (pinned_ip, _ORIGIN_PORT, 0, 0)
            if family == socket.AF_INET6
            else (pinned_ip, _ORIGIN_PORT)
        )
        raw_socket.settimeout(attempt.remaining_timeout(_READ_TIMEOUT_SECONDS))
        tls_socket = ssl.create_default_context().wrap_socket(
            raw_socket,
            server_hostname=_ORIGIN_HOST,
            do_handshake_on_connect=False,
        )
        attempt.register(tls_socket)
        attempt.unregister(raw_socket)
        raw_socket = None
        tls_socket.settimeout(attempt.remaining_timeout(_READ_TIMEOUT_SECONDS))
        tls_socket.do_handshake()
        tls_socket.settimeout(attempt.remaining_timeout(_READ_TIMEOUT_SECONDS))
        tls_socket.sendall(request_bytes)
        tls_socket.settimeout(attempt.remaining_timeout(_READ_TIMEOUT_SECONDS))
        response = http.client.HTTPResponse(tls_socket)
        attempt.register(response)
        response.begin()
        attempt()
        status = response.status
        headers = tuple(response.getheaders())
        if type(status) is not int or not 100 <= status <= 599:
            raise XueqiuTransportFailure("backend_contract_violation")
        body = bytearray()
        while True:
            tls_socket.settimeout(attempt.remaining_timeout(_READ_TIMEOUT_SECONDS))
            chunk = response.read(_READ_CHUNK_BYTES)
            attempt()
            if type(chunk) is not bytes:
                raise XueqiuTransportFailure("backend_contract_violation")
            if not chunk:
                break
            if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                raise XueqiuTransportFailure("permanent")
            body.extend(chunk)
        return status, headers, bytes(body)
    finally:
        if response is not None:
            attempt.unregister(response)
            _close_network_resource(response)
        if tls_socket is not None:
            attempt.unregister(tls_socket)
            _close_network_resource(tls_socket)
        if raw_socket is not None:
            attempt.unregister(raw_socket)
            _close_network_resource(raw_socket)


def _resolve_public_addresses(host: str, port: int) -> tuple[str, ...]:
    if host != _ORIGIN_HOST or port != _ORIGIN_PORT:
        raise XueqiuTransportFailure("permanent")
    records = socket.getaddrinfo(
        host,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    addresses: list[str] = []
    for record in records:
        try:
            address = record[4][0]
        except (IndexError, TypeError):
            raise XueqiuTransportFailure("permanent") from None
        if type(address) is not str:
            raise XueqiuTransportFailure("permanent")
        addresses.append(address)
    return tuple(addresses)


def _select_public_address(addresses: tuple[str, ...]) -> str:
    if type(addresses) is not tuple or not addresses:
        raise XueqiuTransportFailure("not_found")
    parsed: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for value in addresses:
        if type(value) is not str:
            raise XueqiuTransportFailure("permanent")
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            raise XueqiuTransportFailure("permanent") from None
        if not _is_public_address(address):
            raise XueqiuTransportFailure("permanent")
        parsed.append(address)
    return str(sorted(parsed, key=lambda item: (item.version, item.packed))[0])


def _is_public_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return _is_public_address(address.ipv4_mapped)
    return bool(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_reserved
        and not address.is_multicast
        and not address.is_unspecified
    )


def _raise_for_status(status: int) -> None:
    if status == 200:
        return
    if status in {404, 410}:
        raise XueqiuTransportFailure("not_found")
    if status in {408, 425} or 500 <= status <= 599:
        raise XueqiuTransportFailure("transient")
    if status == 401:
        raise XueqiuTransportFailure("authentication")
    if status == 403:
        raise XueqiuTransportFailure("authorization")
    if status == 429:
        raise XueqiuTransportFailure("rate_limit")
    if 300 <= status <= 399:
        raise XueqiuTransportFailure("backend_contract_violation")
    raise XueqiuTransportFailure("permanent")


def _validate_response_headers(headers: Sequence[tuple[str, str]]) -> int | None:
    if len(headers) > _MAX_HEADER_COUNT:
        raise XueqiuTransportFailure("backend_contract_violation")
    selected: dict[str, str] = {}
    total = 0
    allowed = {"content-length", "content-encoding", "content-type"}
    for row in headers:
        if not isinstance(row, tuple) or len(row) != 2:
            raise XueqiuTransportFailure("backend_contract_violation")
        name, value = row
        if type(name) is not str or type(value) is not str:
            raise XueqiuTransportFailure("backend_contract_violation")
        try:
            total += len(name.encode("ascii")) + len(value.encode("ascii"))
        except UnicodeError:
            raise XueqiuTransportFailure("backend_contract_violation") from None
        if total > _MAX_HEADER_BYTES:
            raise XueqiuTransportFailure("backend_contract_violation")
        key = name.lower()
        if key in allowed:
            if key in selected:
                raise XueqiuTransportFailure("backend_contract_violation")
            selected[key] = value

    encoding = selected.get("content-encoding", "identity").strip().lower()
    if encoding not in {"", "identity"}:
        raise XueqiuTransportFailure("permanent")
    raw_content_type = selected.get("content-type")
    if raw_content_type is None:
        raise XueqiuTransportFailure("backend_contract_violation")
    parts = tuple(part.strip() for part in raw_content_type.split(";"))
    if not parts or parts[0].lower() != "application/json":
        raise XueqiuTransportFailure("permanent")
    if len(parts) > 2:
        raise XueqiuTransportFailure("permanent")
    if len(parts) == 2:
        name, separator, raw_value = parts[1].partition("=")
        value = raw_value.strip()
        if value.startswith('"') or value.endswith('"'):
            if len(value) < 2 or not (value.startswith('"') and value.endswith('"')):
                raise XueqiuTransportFailure("permanent")
            value = value[1:-1]
        if separator != "=" or name.strip().lower() != "charset" or value.lower() != "utf-8":
            raise XueqiuTransportFailure("permanent")

    length = selected.get("content-length")
    if length is None:
        return None
    stripped = length.strip()
    if not stripped or not stripped.isascii() or not stripped.isdigit():
        raise XueqiuTransportFailure("backend_contract_violation")
    parsed = int(stripped)
    if parsed > _MAX_RESPONSE_BYTES:
        raise XueqiuTransportFailure("permanent")
    return parsed


def _decode_json(body: bytes) -> object:
    if type(body) is not bytes or not body:
        raise XueqiuTransportFailure("backend_contract_violation")
    if len(body) > _MAX_RESPONSE_BYTES:
        raise XueqiuTransportFailure("permanent")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeError:
        raise XueqiuTransportFailure("backend_contract_violation") from None
    if len(text) > _MAX_DECODED_CHARACTERS:
        raise XueqiuTransportFailure("permanent")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_from_pairs,
            parse_constant=_reject_json_constant,
            parse_int=_parse_json_integer,
            parse_float=_parse_json_float,
        )
    except (json.JSONDecodeError, UnicodeError, ValueError, TypeError):
        raise XueqiuTransportFailure("backend_contract_violation") from None
    _validate_json_tree(value)
    return value


def _object_from_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    del value
    raise ValueError("non-finite JSON number")


def _parse_json_integer(value: str) -> int:
    parsed = int(value)
    if abs(parsed) > _MAX_JSON_INTEGER:
        raise ValueError("JSON integer out of range")
    return parsed


def _parse_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or abs(parsed) > _MAX_JSON_INTEGER:
        raise ValueError("JSON number out of range")
    return parsed


def _validate_json_tree(value: object) -> None:
    nodes = 0
    strings = 0
    scalars = 0
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
            raise XueqiuTransportFailure("backend_contract_violation")
        if isinstance(current, dict):
            for key, child in current.items():
                strings += 1
                scalars += 1
                _validate_json_string(key)
                stack.append((child, depth + 1))
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)
        elif type(current) is str:
            strings += 1
            scalars += 1
            _validate_json_string(current)
        elif current is None or type(current) is bool:
            scalars += 1
        elif type(current) is int:
            scalars += 1
            if abs(current) > _MAX_JSON_INTEGER:
                raise XueqiuTransportFailure("backend_contract_violation")
        elif type(current) is float:
            scalars += 1
            if not math.isfinite(current) or abs(current) > _MAX_JSON_INTEGER:
                raise XueqiuTransportFailure("backend_contract_violation")
        else:
            raise XueqiuTransportFailure("backend_contract_violation")
        if strings > _MAX_JSON_STRINGS or scalars > _MAX_JSON_SCALARS:
            raise XueqiuTransportFailure("backend_contract_violation")


def _validate_json_string(value: str) -> None:
    if len(value) > _MAX_JSON_STRING_CHARACTERS or _contains_invalid_scalar(value):
        raise XueqiuTransportFailure("backend_contract_violation")


class _CheckpointRaised(BaseException):
    def __init__(self, original: BaseException) -> None:
        self.original = original


def _checkpoint(context: ExecutionContextV1) -> None:
    try:
        context.checkpoint()
    except BaseException as error:
        raise _CheckpointRaised(error) from None


def _failure(
    request: ExecutionRequestV1,
    error_code: ExecutionErrorCodeV1,
) -> ExecutionFailureV1:
    return ExecutionFailureV1(
        protocol_version=PROTOCOL_VERSION,
        source="xueqiu",
        operation=request.operation,
        backend_id=_BACKEND_ID,
        backend_version=_BACKEND_VERSION,
        error_code=error_code,
    )
