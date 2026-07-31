"""Closed, immutable contracts for the Agent Reach execution v1 API."""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Final, Literal, TypeAlias
from urllib.parse import urlsplit

PROTOCOL_VERSION: Final = "v1"
FETCHED_DOCUMENT_CAPABILITY: Final = "fetched_document.v1"
NETWORK_ACCESS_CAPABILITY: Final = "network_access.v1"

MAX_DOCUMENT_BYTES: Final = 1_048_576
MAX_METADATA_BYTES: Final = 16_384
MAX_OUTPUT_BYTES: Final = 1_048_576
MAX_ITEMS: Final = 50
MAX_CONTENT_TYPE_CHARACTERS: Final = 512
MAX_CONTENT_LOCATION_CHARACTERS: Final = 8_192
MAX_TEXT_CHARACTERS: Final = 16_000
MAX_TITLE_CHARACTERS: Final = 4_096
MAX_URL_CHARACTERS: Final = 8_192
MAX_NATIVE_ID_CHARACTERS: Final = 512
MAX_AUTHOR_CHARACTERS: Final = 2_048
MAX_PUBLISHED_CHARACTERS: Final = 512

_MAX_BILIBILI_OUTPUT_BYTES: Final = 512 * 1_024
_MAX_BILIBILI_AUTHOR_CHARACTERS: Final = 1_024
_MAX_BILIBILI_QUERY_CHARACTERS: Final = 4_096
_MAX_YOUTUBE_OUTPUT_BYTES: Final = 512 * 1_024
_MAX_YOUTUBE_TITLE_BYTES: Final = 1_024
_MAX_YOUTUBE_AUTHOR_BYTES: Final = 1_024
_MAX_YOUTUBE_AUTHOR_CHARACTERS: Final = 1_024

_MAX_ARGUMENTS: Final = 8
_MAX_ARGUMENT_STRING_CHARACTERS: Final = _MAX_BILIBILI_QUERY_CHARACTERS
_MAX_ARGUMENT_INTEGER: Final = 1_000_000_000
_MAX_HOST_CAPABILITIES: Final = 8
_MAX_RESULT_INTEGER: Final = (1 << 53) - 1
_IDENTIFIER: Final = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
_BACKEND_IDENTIFIER: Final = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_ARGUMENT_NAME: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_BVID: Final = re.compile(r"BV[A-Za-z0-9]{10}")
_YOUTUBE_VIDEO_ID: Final = re.compile(r"[A-Za-z0-9_-]{11}")
_YOUTUBE_VIDEO_URL: Final = re.compile(r"https://www[.]youtube[.]com/watch[?]v=([A-Za-z0-9_-]{11})")

ExecutionErrorCodeV1 = Literal[
    "unsupported_protocol_version",
    "invalid_request",
    "unsupported_source",
    "unsupported_operation",
    "host_capability_missing",
    "backend_unavailable",
    "backend_incompatible",
    "deadline_exceeded",
    "cancelled",
    "invalid_input",
    "not_found",
    "authentication",
    "authorization",
    "rate_limit",
    "transient",
    "permanent",
    "backend_contract_violation",
]
EXECUTION_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        "unsupported_protocol_version",
        "invalid_request",
        "unsupported_source",
        "unsupported_operation",
        "host_capability_missing",
        "backend_unavailable",
        "backend_incompatible",
        "deadline_exceeded",
        "cancelled",
        "invalid_input",
        "not_found",
        "authentication",
        "authorization",
        "rate_limit",
        "transient",
        "permanent",
        "backend_contract_violation",
    }
)

ArgumentScalarV1: TypeAlias = str | int | bool | None
ResultScalarV1: TypeAlias = str | int | None
CheckpointV1: TypeAlias = Callable[[], None]


def _noop_checkpoint() -> None:
    return None


def _empty_arguments() -> Mapping[str, ArgumentScalarV1]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class OperationCapabilityV1:
    """One statically registered operation and its complete hard limits."""

    protocol_version: str
    source: str
    operation: str
    argument_schema_id: str
    result_schema_ids: tuple[str, ...]
    backend_id: str
    backend_version: str
    required_host_capabilities: tuple[str, ...]
    maximum_items: int
    maximum_document_bytes: int
    maximum_metadata_bytes: int
    maximum_output_bytes: int
    maximum_content_type_characters: int
    maximum_content_location_characters: int
    maximum_text_characters: int
    maximum_title_characters: int
    maximum_url_characters: int
    maximum_native_id_characters: int
    maximum_author_characters: int
    maximum_published_characters: int

    def __post_init__(self) -> None:
        if (
            self.protocol_version != PROTOCOL_VERSION
            or not _valid_identifier(self.source)
            or not _valid_identifier(self.operation)
            or not _valid_identifier(self.argument_schema_id)
            or not self.result_schema_ids
            or any(not _valid_identifier(value) for value in self.result_schema_ids)
            or not _valid_backend_identifier(self.backend_id)
            or not _valid_version(self.backend_version)
            or not self.required_host_capabilities
            or any(not _valid_identifier(value) for value in self.required_host_capabilities)
        ):
            raise ValueError("invalid execution capability")
        numeric_limits = (
            self.maximum_items,
            self.maximum_document_bytes,
            self.maximum_metadata_bytes,
            self.maximum_output_bytes,
            self.maximum_content_type_characters,
            self.maximum_content_location_characters,
            self.maximum_text_characters,
            self.maximum_title_characters,
            self.maximum_url_characters,
            self.maximum_native_id_characters,
            self.maximum_author_characters,
            self.maximum_published_characters,
        )
        if any(type(value) is not int or value <= 0 for value in numeric_limits):
            raise ValueError("invalid execution capability")
        object.__setattr__(self, "result_schema_ids", tuple(self.result_schema_ids))
        object.__setattr__(
            self,
            "required_host_capabilities",
            tuple(self.required_host_capabilities),
        )


@dataclass(frozen=True, slots=True)
class ExecutionRequestV1:
    """A caller request with no backend, transport, path, or credential fields."""

    protocol_version: str
    source: str
    operation: str
    arguments: Mapping[str, ArgumentScalarV1] = field(default_factory=_empty_arguments)

    def __post_init__(self) -> None:
        if (
            type(self.protocol_version) is not str
            or not 0 < len(self.protocol_version) <= 16
            or _contains_invalid_scalar(self.protocol_version)
            or not _valid_identifier(self.source)
            or not _valid_identifier(self.operation)
            or not isinstance(self.arguments, Mapping)
            or len(self.arguments) > _MAX_ARGUMENTS
        ):
            raise ValueError("invalid execution request")
        frozen: dict[str, ArgumentScalarV1] = {}
        for name, value in self.arguments.items():
            if type(name) is not str or _ARGUMENT_NAME.fullmatch(name) is None:
                raise ValueError("invalid execution request")
            if not _valid_argument_scalar(value):
                raise ValueError("invalid execution request")
            frozen[name] = value
        object.__setattr__(self, "arguments", MappingProxyType(frozen))


@dataclass(frozen=True, slots=True)
class FetchedDocumentV1:
    """A validated, already-fetched public document supplied by the host."""

    body: bytes
    content_type: str
    content_location: str

    def __post_init__(self) -> None:
        if (
            type(self.body) is not bytes
            or not 0 < len(self.body) <= MAX_DOCUMENT_BYTES
            or type(self.content_type) is not str
            or len(self.content_type) > MAX_CONTENT_TYPE_CHARACTERS
            or not self.content_type.isascii()
            or self.content_type != self.content_type.strip()
            or _contains_control(self.content_type)
            or not _valid_public_location(self.content_location)
        ):
            raise ValueError("invalid fetched document")


@dataclass(frozen=True, slots=True)
class NetworkAccessV1:
    """Data-free host approval to invoke a registered network backend."""


@dataclass(frozen=True, slots=True)
class ExecutionLimitsV1:
    """Host-selected limits that may only narrow descriptor hard limits."""

    maximum_items: int = MAX_ITEMS
    maximum_text_characters: int = MAX_TEXT_CHARACTERS

    def __post_init__(self) -> None:
        if (
            type(self.maximum_items) is not int
            or not 1 <= self.maximum_items <= MAX_ITEMS
            or type(self.maximum_text_characters) is not int
            or not 1 <= self.maximum_text_characters <= MAX_TEXT_CHARACTERS
        ):
            raise ValueError("invalid execution limits")


HostCapabilityV1: TypeAlias = FetchedDocumentV1 | NetworkAccessV1


@dataclass(frozen=True, slots=True)
class ExecutionContextV1:
    """Closed host authority and cooperative cancellation/deadline checkpoint."""

    host_capabilities: tuple[HostCapabilityV1, ...] = ()
    checkpoint: CheckpointV1 = _noop_checkpoint
    limits: ExecutionLimitsV1 = field(default_factory=ExecutionLimitsV1)

    def __post_init__(self) -> None:
        try:
            capabilities = tuple(self.host_capabilities)
        except TypeError:
            raise ValueError("invalid execution context") from None
        if (
            len(capabilities) > _MAX_HOST_CAPABILITIES
            or not callable(self.checkpoint)
            or type(self.limits) is not ExecutionLimitsV1
            or any(
                type(capability) not in {FetchedDocumentV1, NetworkAccessV1}
                for capability in capabilities
            )
            or len({type(capability) for capability in capabilities}) != len(capabilities)
        ):
            raise ValueError("invalid execution context")
        object.__setattr__(self, "host_capabilities", capabilities)


_ResultFieldKind: TypeAlias = Literal["text", "integer"]
_ResultFieldRule: TypeAlias = tuple[_ResultFieldKind, int, bool]


def _text_rule(maximum: int, *, nullable: bool) -> _ResultFieldRule:
    return ("text", maximum, nullable)


def _integer_rule(*, nullable: bool = False) -> _ResultFieldRule:
    return ("integer", _MAX_RESULT_INTEGER, nullable)


_RESULT_SCHEMA_FIELDS: Final[Mapping[str, Mapping[str, _ResultFieldRule]]] = MappingProxyType(
    {
        "rss.feed.v1": MappingProxyType(
            {
                "text": _text_rule(MAX_TEXT_CHARACTERS, nullable=True),
                "title": _text_rule(MAX_TITLE_CHARACTERS, nullable=True),
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=True),
            }
        ),
        "rss.entry.v1": MappingProxyType(
            {
                "text": _text_rule(MAX_TEXT_CHARACTERS, nullable=True),
                "native_id": _text_rule(MAX_NATIVE_ID_CHARACTERS, nullable=True),
                "title": _text_rule(MAX_TITLE_CHARACTERS, nullable=True),
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=True),
                "author": _text_rule(MAX_AUTHOR_CHARACTERS, nullable=True),
                "published_at": _text_rule(MAX_PUBLISHED_CHARACTERS, nullable=True),
            }
        ),
        "bilibili.video.v1": MappingProxyType(
            {
                "text": _text_rule(MAX_TEXT_CHARACTERS, nullable=False),
                "native_id": _text_rule(MAX_NATIVE_ID_CHARACTERS, nullable=False),
                "title": _text_rule(MAX_TITLE_CHARACTERS, nullable=False),
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=False),
                "author": _text_rule(_MAX_BILIBILI_AUTHOR_CHARACTERS, nullable=True),
                "duration_seconds": _integer_rule(),
                "view_count": _integer_rule(),
            }
        ),
        "youtube.video.v1": MappingProxyType(
            {
                "text": _text_rule(MAX_TEXT_CHARACTERS, nullable=False),
                "native_id": _text_rule(MAX_NATIVE_ID_CHARACTERS, nullable=False),
                "title": _text_rule(MAX_TITLE_CHARACTERS, nullable=False),
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=False),
                "author": _text_rule(_MAX_YOUTUBE_AUTHOR_CHARACTERS, nullable=True),
                "published_at": _text_rule(MAX_PUBLISHED_CHARACTERS, nullable=True),
                "duration_seconds": _integer_rule(nullable=True),
                "view_count": _integer_rule(nullable=True),
                "comment_count": _integer_rule(nullable=True),
            }
        ),
    }
)

_ExpectedSuccessContract: TypeAlias = tuple[str, str, str, int, int, bool, int]
_EXPECTED_SUCCESS_CONTRACT: Final[Mapping[tuple[str, str], _ExpectedSuccessContract]] = (
    MappingProxyType(
        {
            ("rss", "read.feed"): (
                "feedparser",
                "6.0.12",
                "rss.feed.v1",
                1,
                1,
                True,
                MAX_OUTPUT_BYTES,
            ),
            ("rss", "browse.entries"): (
                "feedparser",
                "6.0.12",
                "rss.entry.v1",
                0,
                21,
                True,
                MAX_OUTPUT_BYTES,
            ),
            ("bilibili", "search.videos"): (
                "bili-cli",
                "0.6.2",
                "bilibili.video.v1",
                0,
                50,
                False,
                _MAX_BILIBILI_OUTPUT_BYTES,
            ),
            ("bilibili", "read.video"): (
                "bili-cli",
                "0.6.2",
                "bilibili.video.v1",
                1,
                1,
                False,
                _MAX_BILIBILI_OUTPUT_BYTES,
            ),
            ("bilibili", "browse.hot"): (
                "bili-cli",
                "0.6.2",
                "bilibili.video.v1",
                0,
                50,
                False,
                _MAX_BILIBILI_OUTPUT_BYTES,
            ),
            ("bilibili", "browse.rank"): (
                "bili-cli",
                "0.6.2",
                "bilibili.video.v1",
                0,
                50,
                False,
                _MAX_BILIBILI_OUTPUT_BYTES,
            ),
            ("youtube", "read.video"): (
                "yt-dlp",
                "2026.7.4",
                "youtube.video.v1",
                1,
                1,
                False,
                _MAX_YOUTUBE_OUTPUT_BYTES,
            ),
        }
    )
)


@dataclass(frozen=True, slots=True)
class ExecutionItemV1:
    """One schema-tagged result item with an exact scalar field set."""

    schema_id: str
    fields: Mapping[str, ResultScalarV1]

    def __post_init__(self) -> None:
        expected = _RESULT_SCHEMA_FIELDS.get(self.schema_id)
        if expected is None or not isinstance(self.fields, Mapping):
            raise ValueError("invalid execution item")
        try:
            names = set(self.fields)
        except (TypeError, ValueError):
            raise ValueError("invalid execution item") from None
        if names != set(expected):
            raise ValueError("invalid execution item")
        frozen: dict[str, ResultScalarV1] = {}
        for name, rule in expected.items():
            value = self.fields[name]
            if not _valid_result_scalar(value, rule):
                raise ValueError("invalid execution item")
            frozen[name] = value
        object.__setattr__(self, "fields", MappingProxyType(frozen))


@dataclass(frozen=True, slots=True)
class ExecutionSuccessV1:
    """Closed successful execution with exact backend provenance."""

    protocol_version: str
    source: str
    operation: str
    backend_id: str
    backend_version: str
    items: tuple[ExecutionItemV1, ...]
    truncated: bool = False
    partial_error_code: ExecutionErrorCodeV1 | None = None

    def __post_init__(self) -> None:
        try:
            items = tuple(self.items)
        except TypeError:
            raise ValueError("invalid execution success") from None
        expected_contract = _EXPECTED_SUCCESS_CONTRACT.get((self.source, self.operation))
        if expected_contract is None:
            raise ValueError("invalid execution success")
        (
            backend_id,
            backend_version,
            schema_id,
            minimum,
            maximum,
            allows_partial,
            maximum_output_bytes,
        ) = expected_contract
        if (
            self.protocol_version != PROTOCOL_VERSION
            or self.backend_id != backend_id
            or self.backend_version != backend_version
            or type(self.truncated) is not bool
            or (
                self.partial_error_code is not None
                and (not allows_partial or self.partial_error_code != "permanent")
            )
            or any(type(item) is not ExecutionItemV1 for item in items)
        ):
            raise ValueError("invalid execution success")
        if (
            not minimum <= len(items) <= maximum
            or any(item.schema_id != schema_id for item in items)
            or (self.source == "bilibili" and any(not _valid_bilibili_item(item) for item in items))
            or (self.source == "youtube" and any(not _valid_youtube_item(item) for item in items))
            or _result_payload_size(items) > maximum_output_bytes
        ):
            raise ValueError("invalid execution success")
        object.__setattr__(self, "items", items)


@dataclass(frozen=True, slots=True)
class ExecutionFailureV1:
    """A correlated failure that exposes no backend message or details."""

    protocol_version: str
    source: str | None
    operation: str | None
    backend_id: str | None
    backend_version: str | None
    error_code: ExecutionErrorCodeV1

    def __post_init__(self) -> None:
        identity_present = self.source is not None or self.operation is not None
        backend_present = self.backend_id is not None or self.backend_version is not None
        expected_backend = _backend_for_operation(self.source, self.operation)
        if (
            self.protocol_version != PROTOCOL_VERSION
            or (
                identity_present
                and not (_valid_identifier(self.source) and _valid_identifier(self.operation))
            )
            or (
                backend_present
                and (
                    expected_backend is None
                    or (self.backend_id, self.backend_version) != expected_backend
                    or not identity_present
                )
            )
            or self.error_code not in EXECUTION_ERROR_CODES
        ):
            raise ValueError("invalid execution failure")


ExecutionResultV1: TypeAlias = ExecutionSuccessV1 | ExecutionFailureV1


def _valid_identifier(value: object) -> bool:
    return type(value) is str and _IDENTIFIER.fullmatch(value) is not None


def _valid_backend_identifier(value: object) -> bool:
    return type(value) is str and _BACKEND_IDENTIFIER.fullmatch(value) is not None


def _valid_version(value: object) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= 64
        and value.isascii()
        and not _contains_control(value)
    )


def _valid_argument_scalar(value: object) -> bool:
    if value is None or type(value) is bool:
        return True
    if type(value) is int:
        return -_MAX_ARGUMENT_INTEGER <= value <= _MAX_ARGUMENT_INTEGER
    return bool(
        type(value) is str
        and len(value) <= _MAX_ARGUMENT_STRING_CHARACTERS
        and not _contains_invalid_scalar(value)
    )


def _valid_result_scalar(value: object, rule: _ResultFieldRule) -> bool:
    kind, maximum, nullable = rule
    if value is None:
        return nullable
    if kind == "integer":
        return type(value) is int and 0 <= value <= maximum
    return bool(
        type(value) is str and 0 < len(value) <= maximum and not _contains_invalid_scalar(value)
    )


def _backend_for_operation(
    source: object,
    operation: object,
) -> tuple[str, str] | None:
    if type(source) is not str or type(operation) is not str:
        return None
    contract = _EXPECTED_SUCCESS_CONTRACT.get((source, operation))
    if contract is None:
        return None
    return contract[0], contract[1]


def _valid_bilibili_video_url(value: object) -> bool:
    if type(value) is not str or not value.isascii() or len(value) > 128:
        return False
    try:
        parsed = urlsplit(value)
    except (UnicodeError, ValueError):
        return False
    parts = parsed.path.split("/")
    return bool(
        parsed.scheme == "https"
        and parsed.netloc == "www.bilibili.com"
        and not parsed.query
        and not parsed.fragment
        and len(parts) == 3
        and parts[1] == "video"
        and _BVID.fullmatch(parts[2])
    )


def _valid_bilibili_item(item: ExecutionItemV1) -> bool:
    native_id = item.fields.get("native_id")
    return bool(
        type(native_id) is str
        and _BVID.fullmatch(native_id)
        and item.fields.get("url") == f"https://www.bilibili.com/video/{native_id}"
    )


def _valid_youtube_video_url(value: object) -> bool:
    return bool(
        type(value) is str
        and value.isascii()
        and len(value) <= 128
        and _YOUTUBE_VIDEO_URL.fullmatch(value)
    )


def _valid_youtube_item(item: ExecutionItemV1) -> bool:
    native_id = item.fields.get("native_id")
    title = item.fields.get("title")
    author = item.fields.get("author")
    published_at = item.fields.get("published_at")
    if (
        type(native_id) is not str
        or _YOUTUBE_VIDEO_ID.fullmatch(native_id) is None
        or item.fields.get("url") != f"https://www.youtube.com/watch?v={native_id}"
        or type(title) is not str
        or not _utf8_within(title, _MAX_YOUTUBE_TITLE_BYTES)
        or (
            author is not None
            and (type(author) is not str or not _utf8_within(author, _MAX_YOUTUBE_AUTHOR_BYTES))
        )
    ):
        return False
    if published_at is None:
        return True
    if type(published_at) is not str or len(published_at) != 10 or not published_at.isascii():
        return False
    try:
        parsed = date.fromisoformat(published_at)
    except ValueError:
        return False
    return parsed.year >= 1970 and parsed.isoformat() == published_at


def _utf8_within(value: str, maximum_bytes: int) -> bool:
    try:
        return len(value.encode("utf-8", errors="strict")) <= maximum_bytes
    except UnicodeError:
        return False


def _valid_public_location(value: object) -> bool:
    if (
        type(value) is not str
        or not 0 < len(value) <= MAX_CONTENT_LOCATION_CHARACTERS
        or not value.isascii()
        or value != value.strip()
        or _contains_control(value)
        or "\\" in value
    ):
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or parsed.username is not None
            or parsed.password is not None
            or hostname is None
            or parsed.query
            or parsed.fragment
        ):
            return False
        expected_port = 443 if parsed.scheme.lower() == "https" else 80
        if (parsed.port or expected_port) != expected_port:
            return False
        host = hostname.rstrip(".").lower()
        if not host or host == "localhost" or host.endswith((".localhost", ".local")):
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            labels = host.split(".")
            return all(
                0 < len(label) <= 63
                and label[0].isalnum()
                and label[-1].isalnum()
                and all(character.isalnum() or character == "-" for character in label)
                for label in labels
            )
        return _is_global_address(address)
    except (UnicodeError, ValueError):
        return False


def _is_global_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return _is_global_address(address.ipv4_mapped)
    return bool(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_reserved
        and not address.is_multicast
        and not address.is_unspecified
    )


def _result_payload_size(items: tuple[ExecutionItemV1, ...]) -> int:
    size = 1_024
    for item in items:
        size += 256 + _json_scalar_size(item.schema_id)
        for name, value in item.fields.items():
            size += _json_scalar_size(name) + 16 + _json_scalar_size(value)
    return size


def _json_scalar_size(value: str | int | None) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    )


def _contains_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _contains_invalid_scalar(value: str) -> bool:
    return any(character == "\x00" or 0xD800 <= ord(character) <= 0xDFFF for character in value)
