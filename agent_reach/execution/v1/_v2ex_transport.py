"""Fixed, bounded transport for the public V2EX legacy JSON API."""

from __future__ import annotations

import ipaddress
import json
import math
import re
import socket
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import Final, Protocol, cast
from urllib.parse import urlencode

from .contracts import CheckpointV1, ExecutionErrorCodeV1

_BACKEND_DISTRIBUTION: Final = "httpcore"
_BACKEND_VERSION: Final = "1.0.9"
_ORIGIN_HOST: Final = "www.v2ex.com"
_ORIGIN_PORT: Final = 443
_HOT_PATH: Final = "/api/topics/hot.json"
_TOPICS_PATH: Final = "/api/topics/show.json"
_REPLIES_PATH: Final = "/api/replies/show.json"
_MEMBER_PATH: Final = "/api/members/show.json"

_MAX_RESPONSE_BYTES: Final = 1_048_576
_MAX_DECODED_CHARACTERS: Final = 250_000
_MAX_HEADER_COUNT: Final = 128
_MAX_HEADER_BYTES: Final = 32_768
_MAX_JSON_DEPTH: Final = 16
_MAX_JSON_NODES: Final = 20_000
_MAX_JSON_STRINGS: Final = 10_000
_MAX_JSON_SCALARS: Final = 20_000
_MAX_JSON_STRING_CHARACTERS: Final = 250_000
_MAX_JSON_INTEGER: Final = (1 << 53) - 1
_CONNECT_TIMEOUT_SECONDS: Final = 5.0
_READ_TIMEOUT_SECONDS: Final = 5.0
_IDENTIFIER: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_CANONICAL_DECIMAL: Final = re.compile(r"[1-9][0-9]{0,31}")
_HEADERS: Final = (
    (b"Accept", b"application/json"),
    (b"User-Agent", b"agent-reach/1.0"),
    (b"Accept-Encoding", b"identity"),
    (b"Connection", b"close"),
)


class _Resolver(Protocol):
    def __call__(self, host: str, port: int) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class _HttpcoreApi:
    pool_factory: Callable[..., object]
    url_factory: Callable[..., object]
    backend_factory: Callable[[], object]
    timeout_errors: tuple[type[Exception], ...]
    network_errors: tuple[type[Exception], ...]
    protocol_errors: tuple[type[Exception], ...]


class V2exTransportFailure(Exception):
    """A closed transport error carrying no request or response content."""

    def __init__(self, error_code: ExecutionErrorCodeV1) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class _PinnedNetworkBackend:
    """Pin TCP to a validated address while preserving Host and TLS SNI."""

    def __init__(self, pinned_ip: str, backend: object) -> None:
        self._pinned_ip = pinned_ip
        self._backend = backend

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[object] | None = None,
    ) -> object:
        if host != _ORIGIN_HOST or port != _ORIGIN_PORT or local_address is not None:
            raise V2exTransportFailure("permanent")
        connect = getattr(self._backend, "connect_tcp", None)
        if not callable(connect):
            raise V2exTransportFailure("backend_incompatible")
        return cast(Callable[..., object], connect)(
            self._pinned_ip,
            _ORIGIN_PORT,
            timeout=timeout,
            local_address=None,
            socket_options=socket_options,
        )

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[object] | None = None,
    ) -> object:
        del path, timeout, socket_options
        raise V2exTransportFailure("permanent")

    def sleep(self, seconds: float) -> None:
        sleep = getattr(self._backend, "sleep", None)
        if not callable(sleep):
            raise V2exTransportFailure("backend_incompatible")
        cast(Callable[[float], None], sleep)(seconds)


@dataclass(frozen=True, slots=True)
class V2exTransport:
    """One loaded exact backend with only the five fixed public reads."""

    _api: _HttpcoreApi
    _resolver: _Resolver = field(default_factory=lambda: _resolve_public_addresses)

    def fetch_hot_topics(self, checkpoint: CheckpointV1) -> object:
        return self._request_json(_HOT_PATH, checkpoint)

    def fetch_node_topics(
        self,
        node: str,
        page: int,
        checkpoint: CheckpointV1,
    ) -> object:
        if type(node) is not str or _IDENTIFIER.fullmatch(node) is None:
            raise V2exTransportFailure("backend_contract_violation")
        if type(page) is not int or not 1 <= page <= 100:
            raise V2exTransportFailure("backend_contract_violation")
        query = urlencode((("node_name", node), ("page", str(page))))
        return self._request_json(f"{_TOPICS_PATH}?{query}", checkpoint)

    def fetch_topic(self, topic_id: str, checkpoint: CheckpointV1) -> object:
        if type(topic_id) is not str or _CANONICAL_DECIMAL.fullmatch(topic_id) is None:
            raise V2exTransportFailure("backend_contract_violation")
        query = urlencode((("id", topic_id),))
        return self._request_json(f"{_TOPICS_PATH}?{query}", checkpoint)

    def fetch_replies(self, topic_id: str, checkpoint: CheckpointV1) -> object:
        if type(topic_id) is not str or _CANONICAL_DECIMAL.fullmatch(topic_id) is None:
            raise V2exTransportFailure("backend_contract_violation")
        query = urlencode((("topic_id", topic_id), ("page", "1")))
        return self._request_json(f"{_REPLIES_PATH}?{query}", checkpoint)

    def fetch_user(self, username: str, checkpoint: CheckpointV1) -> object:
        if type(username) is not str or _IDENTIFIER.fullmatch(username) is None:
            raise V2exTransportFailure("backend_contract_violation")
        query = urlencode((("username", username),))
        return self._request_json(f"{_MEMBER_PATH}?{query}", checkpoint)

    def _request_json(self, target: str, checkpoint: CheckpointV1) -> object:
        checkpoint()
        try:
            addresses = self._resolver(_ORIGIN_HOST, _ORIGIN_PORT)
        except V2exTransportFailure:
            raise
        except socket.gaierror as error:
            missing = error.errno == getattr(socket, "EAI_NONAME", None)
            raise V2exTransportFailure("not_found" if missing else "transient") from None
        except Exception:
            raise V2exTransportFailure("transient") from None
        pinned_ip = _select_public_address(addresses)
        checkpoint()

        try:
            backend = _PinnedNetworkBackend(pinned_ip, self._api.backend_factory())
            pool = self._api.pool_factory(
                ssl_context=None,
                proxy=None,
                max_connections=1,
                max_keepalive_connections=0,
                keepalive_expiry=0.0,
                http1=True,
                http2=False,
                retries=0,
                network_backend=backend,
            )
        except V2exTransportFailure:
            raise
        except Exception:
            raise V2exTransportFailure("backend_incompatible") from None

        result: object | None = None
        pending: BaseException | None = None
        try:
            result = self._exchange(pool, target, checkpoint)
        except BaseException as error:
            pending = error
        try:
            close = getattr(pool, "close", None)
            if not callable(close):
                raise TypeError("pool close unavailable")
            cast(Callable[[], None], close)()
        except Exception:
            if pending is None:
                pending = V2exTransportFailure("transient")
        if pending is not None:
            raise pending
        return result

    def _exchange(
        self,
        pool: object,
        target: str,
        checkpoint: CheckpointV1,
    ) -> object:
        timeout = {
            "pool": _CONNECT_TIMEOUT_SECONDS,
            "connect": _CONNECT_TIMEOUT_SECONDS,
            "read": _READ_TIMEOUT_SECONDS,
            "write": _READ_TIMEOUT_SECONDS,
        }
        try:
            url = self._api.url_factory(
                scheme="https",
                host=_ORIGIN_HOST,
                port=_ORIGIN_PORT,
                target=target,
            )
            stream = getattr(pool, "stream", None)
            if not callable(stream):
                raise TypeError("pool stream unavailable")
            manager = cast(Callable[..., object], stream)(
                "GET",
                url,
                headers=_HEADERS,
                extensions={"timeout": timeout},
            )
            response_manager = cast(_ResponseManager, manager)
            response = response_manager.__enter__()
            result: object | None = None
            pending: BaseException | None = None
            try:
                checkpoint()
                status = getattr(response, "status", None)
                if type(status) is not int or not 100 <= status <= 599:
                    raise V2exTransportFailure("backend_contract_violation")
                headers = _validated_headers(getattr(response, "headers", None))
                _raise_for_status(status)
                declared_length = _validate_content_headers(headers)
                iterator = getattr(response, "iter_stream", None)
                if not callable(iterator):
                    raise V2exTransportFailure("backend_incompatible")
                body = bytearray()
                for chunk in cast(Callable[[], Iterator[bytes]], iterator)():
                    checkpoint()
                    if type(chunk) is not bytes:
                        raise V2exTransportFailure("backend_contract_violation")
                    if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                        raise V2exTransportFailure("permanent")
                    body.extend(chunk)
                checkpoint()
                if declared_length is not None and len(body) != declared_length:
                    raise V2exTransportFailure("backend_contract_violation")
                result = _decode_json(bytes(body))
            except BaseException as error:
                pending = error
            try:
                response_manager.__exit__(
                    type(pending) if pending is not None else None,
                    pending,
                    pending.__traceback__ if pending is not None else None,
                )
            except BaseException as error:
                if pending is None:
                    pending = error
            if pending is not None:
                raise pending
            return result
        except V2exTransportFailure:
            raise
        except self._api.timeout_errors:
            raise V2exTransportFailure("transient") from None
        except self._api.network_errors:
            raise V2exTransportFailure("transient") from None
        except self._api.protocol_errors:
            raise V2exTransportFailure("transient") from None
        except OSError:
            raise V2exTransportFailure("transient") from None
        except Exception:
            raise V2exTransportFailure("backend_contract_violation") from None


class _ResponseManager(Protocol):
    def __enter__(self) -> object: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> object: ...


def load_v2ex_transport() -> V2exTransport | ExecutionErrorCodeV1:
    """Load the exact backend only after a closed request reaches execution."""

    try:
        installed = version(_BACKEND_DISTRIBUTION)
    except PackageNotFoundError:
        return "backend_unavailable"
    except Exception:
        return "backend_unavailable"
    if installed != _BACKEND_VERSION:
        return "backend_incompatible"

    try:
        module = import_module("httpcore")
    except Exception:
        return "backend_incompatible"
    if getattr(module, "__version__", None) != _BACKEND_VERSION:
        return "backend_incompatible"
    try:
        api = _HttpcoreApi(
            pool_factory=_callable_attribute(module, "ConnectionPool"),
            url_factory=_callable_attribute(module, "URL"),
            backend_factory=_callable_attribute(module, "SyncBackend"),
            timeout_errors=_exception_attributes(module, ("TimeoutException",)),
            network_errors=_exception_attributes(module, ("NetworkError",)),
            protocol_errors=_exception_attributes(module, ("ProtocolError",)),
        )
    except (TypeError, ValueError):
        return "backend_incompatible"
    return V2exTransport(api)


def _callable_attribute(module: object, name: str) -> Callable[..., object]:
    value = getattr(module, name, None)
    if not callable(value):
        raise TypeError("backend API invalid")
    return cast(Callable[..., object], value)


def _exception_attributes(
    module: object,
    names: tuple[str, ...],
) -> tuple[type[Exception], ...]:
    selected: list[type[Exception]] = []
    for name in names:
        value = getattr(module, name, None)
        if not isinstance(value, type) or not issubclass(value, Exception):
            raise TypeError("backend API invalid")
        selected.append(value)
    return tuple(selected)


def _resolve_public_addresses(host: str, port: int) -> tuple[str, ...]:
    if host != _ORIGIN_HOST or port != _ORIGIN_PORT:
        raise V2exTransportFailure("permanent")
    try:
        records = socket.getaddrinfo(
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror:
        raise
    except OSError:
        raise V2exTransportFailure("transient") from None
    addresses: list[str] = []
    for record in records:
        try:
            address = record[4][0]
        except (IndexError, TypeError):
            raise V2exTransportFailure("permanent") from None
        if type(address) is not str:
            raise V2exTransportFailure("permanent")
        addresses.append(address)
    return tuple(addresses)


def _select_public_address(addresses: tuple[str, ...]) -> str:
    if type(addresses) is not tuple or not addresses:
        raise V2exTransportFailure("not_found")
    parsed: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for value in addresses:
        if type(value) is not str:
            raise V2exTransportFailure("permanent")
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            raise V2exTransportFailure("permanent") from None
        if not _is_public_address(address):
            raise V2exTransportFailure("permanent")
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


def _validated_headers(value: object) -> dict[bytes, bytes]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise V2exTransportFailure("backend_contract_violation")
    if len(value) > _MAX_HEADER_COUNT:
        raise V2exTransportFailure("backend_contract_violation")
    selected: dict[bytes, bytes] = {}
    total = 0
    allowed = {b"content-length", b"content-encoding", b"content-type"}
    for row in value:
        if (
            not isinstance(row, tuple)
            or len(row) != 2
            or type(row[0]) is not bytes
            or type(row[1]) is not bytes
        ):
            raise V2exTransportFailure("backend_contract_violation")
        name, header_value = row
        total += len(name) + len(header_value)
        if total > _MAX_HEADER_BYTES:
            raise V2exTransportFailure("backend_contract_violation")
        key = name.lower()
        if key in allowed:
            if key in selected:
                raise V2exTransportFailure("backend_contract_violation")
            selected[key] = header_value
    return selected


def _validate_content_headers(headers: dict[bytes, bytes]) -> int | None:
    encoding = headers.get(b"content-encoding", b"identity").strip().lower()
    if encoding not in {b"", b"identity"}:
        raise V2exTransportFailure("permanent")

    content_length = headers.get(b"content-length")
    declared_length: int | None = None
    if content_length is not None:
        stripped = content_length.strip()
        if not stripped or not stripped.isascii() or not stripped.isdigit():
            raise V2exTransportFailure("backend_contract_violation")
        declared_length = int(stripped)
        if declared_length > _MAX_RESPONSE_BYTES:
            raise V2exTransportFailure("permanent")

    content_type = headers.get(b"content-type", b"")
    try:
        decoded = content_type.decode("ascii", errors="strict").lower()
    except UnicodeError:
        raise V2exTransportFailure("backend_contract_violation") from None
    parts = tuple(part.strip() for part in decoded.split(";"))
    if parts[0] not in {"", "application/json", "text/json"}:
        raise V2exTransportFailure("permanent")
    for parameter in parts[1:]:
        if parameter.startswith("charset="):
            charset = parameter.split("=", 1)[1].strip('"')
            if charset not in {"utf-8", "utf8"}:
                raise V2exTransportFailure("permanent")
    return declared_length


def _raise_for_status(status: int) -> None:
    if status == 200:
        return
    if status in {404, 410}:
        raise V2exTransportFailure("not_found")
    if status in {408, 425} or 500 <= status <= 599:
        raise V2exTransportFailure("transient")
    if status == 401:
        raise V2exTransportFailure("authentication")
    if status == 403:
        raise V2exTransportFailure("authorization")
    if status == 429:
        raise V2exTransportFailure("rate_limit")
    if 300 <= status <= 399:
        raise V2exTransportFailure("backend_contract_violation")
    raise V2exTransportFailure("permanent")


def _decode_json(body: bytes) -> object:
    if type(body) is not bytes or not body:
        raise V2exTransportFailure("backend_contract_violation")
    if len(body) > _MAX_RESPONSE_BYTES:
        raise V2exTransportFailure("permanent")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeError:
        raise V2exTransportFailure("backend_contract_violation") from None
    if len(text) > _MAX_DECODED_CHARACTERS:
        raise V2exTransportFailure("permanent")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_from_pairs,
            parse_constant=_reject_json_constant,
            parse_int=_parse_json_integer,
            parse_float=_parse_json_float,
        )
    except (json.JSONDecodeError, UnicodeError, ValueError, TypeError):
        raise V2exTransportFailure("backend_contract_violation") from None
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
            raise V2exTransportFailure("backend_contract_violation")
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
                raise V2exTransportFailure("backend_contract_violation")
        elif type(current) is float:
            scalars += 1
            if not math.isfinite(current) or abs(current) > _MAX_JSON_INTEGER:
                raise V2exTransportFailure("backend_contract_violation")
        else:
            raise V2exTransportFailure("backend_contract_violation")
        if strings > _MAX_JSON_STRINGS or scalars > _MAX_JSON_SCALARS:
            raise V2exTransportFailure("backend_contract_violation")


def _validate_json_string(value: str) -> None:
    if len(value) > _MAX_JSON_STRING_CHARACTERS or any(
        character == "\x00" or 0xD800 <= ord(character) <= 0xDFFF for character in value
    ):
        raise V2exTransportFailure("backend_contract_violation")
