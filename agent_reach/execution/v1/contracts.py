"""Closed, immutable contracts for the Agent Reach execution v1 API."""

from __future__ import annotations

import ipaddress
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal, TypeAlias, cast
from urllib.parse import parse_qs, quote_plus, urlsplit

PROTOCOL_VERSION: Final = "v1"
FETCHED_DOCUMENT_CAPABILITY: Final = "fetched_document.v1"
NETWORK_ACCESS_CAPABILITY: Final = "network_access.v1"
PRIVATE_WORKSPACE_CAPABILITY: Final = "private_workspace.v1"
MCPORTER_ARTIFACTS_CAPABILITY: Final = "mcporter_artifacts.v1"
OPENCLI_SESSION_CAPABILITY: Final = "opencli_session.v1"
LINKEDIN_MCP_CAPABILITY: Final = "linkedin_mcp.v1"
XUEQIU_SESSION_CAPABILITY: Final = "xueqiu_session.v1"

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
MAX_XUEQIU_SYMBOL_CHARACTERS: Final = 64
MAX_AUTHOR_CHARACTERS: Final = 2_048
MAX_PUBLISHED_CHARACTERS: Final = 512

_MAX_BILIBILI_OUTPUT_BYTES: Final = 512 * 1_024
_MAX_BILIBILI_AUTHOR_CHARACTERS: Final = 1_024
_MAX_BILIBILI_QUERY_CHARACTERS: Final = 4_096
_MAX_YOUTUBE_OUTPUT_BYTES: Final = 512 * 1_024
_MAX_YOUTUBE_TITLE_BYTES: Final = 1_024
_MAX_YOUTUBE_AUTHOR_BYTES: Final = 1_024
_MAX_YOUTUBE_AUTHOR_CHARACTERS: Final = 1_024
_MAX_YOUTUBE_LANGUAGE_CHARACTERS: Final = 32
_MAX_V2EX_IDENTIFIER_CHARACTERS: Final = 64
_MAX_EXA_OUTPUT_BYTES: Final = 512 * 1_024
_MAX_OPENCLI_OUTPUT_BYTES: Final = 512 * 1_024
_MAX_ARTIFACT_PATH_CHARACTERS: Final = 8_192

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
_YOUTUBE_LANGUAGE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}")
_YOUTUBE_SUBTITLE_MARKER: Final = "WEBVTT"
_POSITIVE_DECIMAL: Final = re.compile(r"[1-9][0-9]{0,31}")
_V2EX_IDENTIFIER: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_REDDIT_POST_ID: Final = re.compile(r"[a-z0-9]{1,32}")
_XIAOHONGSHU_NOTE_ID: Final = re.compile(r"[0-9a-f]{24}")
_MAINLAND_STOCK_SYMBOL: Final = re.compile(r"(SH|SZ|BJ)[0-9]{6}")
_QUALIFIED_STOCK_SYMBOL: Final = re.compile(r"([A-Z]{2,16}):[A-Z0-9]+(?:[.-][A-Z0-9]+)*")
_STOCK_EXCHANGE: Final = re.compile(r"[A-Z]{2,16}")
_SOCIAL_USERNAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_SUBREDDIT_IDENTIFIER: Final = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,20}")
_SHA256: Final = re.compile(r"[0-9a-f]{64}")
_COOKIE_NAME: Final = re.compile(r"[A-Za-z0-9_]{1,64}")
_LINKEDIN_ENDPOINT: Final = "http://127.0.0.1:8001/mcp"
_LINKEDIN_ALIAS_WHEEL_SHA256: Final = (
    "2173ead9777f6202fd581b4ec227d7a7212e9798f26f530b3174ff4683797558"
)
_LINKEDIN_BACKEND_WHEEL_SHA256: Final = (
    "62a889ac417e5e04d1635d5698df7178edc667a232dca42f417647e2ea25926d"
)
_LINKEDIN_RUNTIME_LOCK_SHA256: Final = (
    "9150a44d903ecfecdc48d115b87385bb78f3c69f4067951cf238e7fda6f09a17"
)
_LINKEDIN_SOURCE_COMMIT: Final = "7edbd32231afa6d40fabad207329591ad5a4feb0"
_LINKEDIN_SCHEMA_SHA256: Final = "2549d379d2306ba22c24f06015db67f448d109943fb96f2d656986d2d92f0699"
_LINKEDIN_TOOL_TIMEOUT_SECONDS: Final = 12
_LINKEDIN_REFERENCE_KINDS: Final = frozenset(
    {
        "person",
        "company",
        "company_urn",
        "job",
        "feed_post",
        "article",
        "newsletter",
        "school",
        "conversation",
        "external",
    }
)
_LINKEDIN_REFERENCE_CONTEXTS: Final = frozenset(
    {
        "about",
        "experience",
        "education",
        "interests",
        "honors",
        "languages",
        "contact info",
        "job posting",
        "inbox",
        "conversation",
        "job result",
        "search result",
        "post author",
        "company post",
        "post attachment",
        "featured",
        "top card",
    }
)
_LINKEDIN_REFERENCE_PREFIXES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "person": "/in/",
        "company": "/company/",
        "school": "/school/",
        "job": "/jobs/view/",
        "newsletter": "/newsletters/",
        "article": "/pulse/",
        "feed_post": "/feed/update/",
        "conversation": "/messaging/thread/",
    }
)
_LINKEDIN_COMPANY_URN_URL: Final = re.compile(
    r"/search/results/people/[?]currentCompany=%5B%22([1-9][0-9]{0,31})%22%5D"
)

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
            or len(set(self.required_host_capabilities)) != len(self.required_host_capabilities)
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
class PrivateWorkspaceV1:
    """Data-free approval to use only the host process's private cwd."""


@dataclass(frozen=True, slots=True)
class McporterArtifactsV1:
    """Closed identities for one operator-attested mcporter installation."""

    node_executable: str
    node_sha256: str
    mcporter_root: str
    mcporter_cli: str
    mcporter_tree_sha256: str
    config_path: str
    config_sha256: str

    def __post_init__(self) -> None:
        paths = (
            self.node_executable,
            self.mcporter_root,
            self.mcporter_cli,
            self.config_path,
        )
        digests = (
            self.node_sha256,
            self.mcporter_tree_sha256,
            self.config_sha256,
        )
        if (
            any(not _valid_closed_absolute_path(value) for value in paths)
            or any(type(value) is not str or _SHA256.fullmatch(value) is None for value in digests)
            or not Path(self.mcporter_cli).is_relative_to(Path(self.mcporter_root))
            or self.mcporter_cli == self.mcporter_root
        ):
            raise ValueError("invalid mcporter artifacts")


@dataclass(frozen=True, slots=True)
class OpenCliSessionV1:
    """Closed identities for an attested npm-prefix closure and browser session."""

    node_executable: str
    node_sha256: str
    opencli_root: str
    opencli_cli: str
    opencli_tree_sha256: str
    session_home: str

    def __post_init__(self) -> None:
        paths = (
            self.node_executable,
            self.opencli_root,
            self.opencli_cli,
            self.session_home,
        )
        digests = (self.node_sha256, self.opencli_tree_sha256)
        if (
            any(not _valid_closed_absolute_path(value) for value in paths)
            or any(type(value) is not str or _SHA256.fullmatch(value) is None for value in digests)
            or not Path(self.opencli_cli).is_relative_to(Path(self.opencli_root))
            or self.opencli_cli == self.opencli_root
        ):
            raise ValueError("invalid opencli session")


@dataclass(frozen=True, slots=True)
class LinkedInMcpV1:
    """Closed attestation for the reviewed loopback LinkedIn read service."""

    endpoint: str
    alias_wheel_sha256: str
    backend_wheel_sha256: str
    runtime_lock_sha256: str
    source_commit: str
    schema_sha256: str
    log_level: str
    tool_timeout_seconds: int

    def __post_init__(self) -> None:
        if (
            self.endpoint != _LINKEDIN_ENDPOINT
            or self.alias_wheel_sha256 != _LINKEDIN_ALIAS_WHEEL_SHA256
            or self.backend_wheel_sha256 != _LINKEDIN_BACKEND_WHEEL_SHA256
            or self.runtime_lock_sha256 != _LINKEDIN_RUNTIME_LOCK_SHA256
            or self.source_commit != _LINKEDIN_SOURCE_COMMIT
            or self.schema_sha256 != _LINKEDIN_SCHEMA_SHA256
            or self.log_level not in {"WARNING", "ERROR", "CRITICAL"}
            or type(self.tool_timeout_seconds) is not int
            or self.tool_timeout_seconds != _LINKEDIN_TOOL_TIMEOUT_SECONDS
        ):
            raise ValueError("invalid LinkedIn MCP attestation")


@dataclass(frozen=True, slots=True, repr=False)
class XueqiuSessionV1:
    """One-attempt Xueqiu Cookie header owned by a trusted host."""

    cookie_header: bytearray = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        value = self.cookie_header
        if type(value) is not bytearray or not _valid_xueqiu_cookie_header(value):
            if type(value) is bytearray:
                value[:] = b"\x00" * len(value)
            raise ValueError("invalid Xueqiu session")
        copied = bytearray(value)
        value[:] = b"\x00" * len(value)
        object.__setattr__(self, "cookie_header", copied)

    def close(self) -> None:
        self.cookie_header[:] = b"\x00" * len(self.cookie_header)

    def __repr__(self) -> str:
        return "XueqiuSessionV1(<redacted>)"


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


HostCapabilityV1: TypeAlias = (
    FetchedDocumentV1
    | NetworkAccessV1
    | PrivateWorkspaceV1
    | McporterArtifactsV1
    | OpenCliSessionV1
    | LinkedInMcpV1
    | XueqiuSessionV1
)


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
                type(capability)
                not in {
                    FetchedDocumentV1,
                    NetworkAccessV1,
                    PrivateWorkspaceV1,
                    McporterArtifactsV1,
                    OpenCliSessionV1,
                    LinkedInMcpV1,
                    XueqiuSessionV1,
                }
                for capability in capabilities
            )
            or len({type(capability) for capability in capabilities}) != len(capabilities)
        ):
            raise ValueError("invalid execution context")
        object.__setattr__(self, "host_capabilities", capabilities)


def _valid_xueqiu_cookie_header(value: bytearray) -> bool:
    if not 1 <= len(value) <= 8_192 or 0 in value:
        return False
    try:
        text = value.decode("ascii", errors="strict")
    except UnicodeError:
        return False
    if text != text.strip() or any(
        ord(character) < 32 or ord(character) == 127 for character in text
    ):
        return False
    names: set[str] = set()
    for raw_pair in text.split(";"):
        pair = raw_pair.strip()
        name, separator, cookie_value = pair.partition("=")
        if (
            separator != "="
            or _COOKIE_NAME.fullmatch(name) is None
            or not cookie_value
            or any(character in ";," or character.isspace() for character in cookie_value)
            or name in names
        ):
            return False
        names.add(name)
    return "xq_a_token" in names


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
        "youtube.subtitle.v1": MappingProxyType(
            {
                "text": _text_rule(MAX_TEXT_CHARACTERS, nullable=False),
                "native_id": _text_rule(MAX_NATIVE_ID_CHARACTERS, nullable=False),
                "title": _text_rule(MAX_TITLE_CHARACTERS, nullable=False),
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=False),
                "language": _text_rule(_MAX_YOUTUBE_LANGUAGE_CHARACTERS, nullable=False),
                "origin": _text_rule(16, nullable=False),
            }
        ),
        "v2ex.topic.v1": MappingProxyType(
            {
                "text": _text_rule(MAX_TEXT_CHARACTERS, nullable=True),
                "native_id": _text_rule(MAX_NATIVE_ID_CHARACTERS, nullable=False),
                "title": _text_rule(MAX_TITLE_CHARACTERS, nullable=False),
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=False),
                "author": _text_rule(MAX_AUTHOR_CHARACTERS, nullable=True),
                "published_at": _text_rule(MAX_PUBLISHED_CHARACTERS, nullable=True),
                "node": _text_rule(_MAX_V2EX_IDENTIFIER_CHARACTERS, nullable=False),
            }
        ),
        "v2ex.reply.v1": MappingProxyType(
            {
                "text": _text_rule(MAX_TEXT_CHARACTERS, nullable=False),
                "native_id": _text_rule(MAX_NATIVE_ID_CHARACTERS, nullable=False),
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=False),
                "author": _text_rule(MAX_AUTHOR_CHARACTERS, nullable=False),
                "published_at": _text_rule(MAX_PUBLISHED_CHARACTERS, nullable=True),
            }
        ),
        "v2ex.profile.v1": MappingProxyType(
            {
                "text": _text_rule(MAX_TEXT_CHARACTERS, nullable=True),
                "native_id": _text_rule(MAX_NATIVE_ID_CHARACTERS, nullable=False),
                "title": _text_rule(_MAX_V2EX_IDENTIFIER_CHARACTERS, nullable=False),
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=False),
                "published_at": _text_rule(MAX_PUBLISHED_CHARACTERS, nullable=True),
            }
        ),
        "exa.search.result.v1": MappingProxyType(
            {
                "text": _text_rule(MAX_TEXT_CHARACTERS, nullable=False),
                "title": _text_rule(MAX_TITLE_CHARACTERS, nullable=False),
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=False),
                "author": _text_rule(MAX_AUTHOR_CHARACTERS, nullable=True),
                "published_at": _text_rule(MAX_PUBLISHED_CHARACTERS, nullable=True),
            }
        ),
        "exa.code.result.v1": MappingProxyType(
            {
                "text": _text_rule(MAX_TEXT_CHARACTERS, nullable=False),
                "title": _text_rule(MAX_TITLE_CHARACTERS, nullable=False),
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=False),
            }
        ),
        "reddit.post.v1": MappingProxyType(
            {
                "text": _text_rule(MAX_TEXT_CHARACTERS, nullable=True),
                "native_id": _text_rule(MAX_NATIVE_ID_CHARACTERS, nullable=False),
                "title": _text_rule(MAX_TITLE_CHARACTERS, nullable=False),
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=False),
                "author": _text_rule(MAX_AUTHOR_CHARACTERS, nullable=True),
                "published_at": _text_rule(MAX_PUBLISHED_CHARACTERS, nullable=True),
                "score": _integer_rule(nullable=True),
                "comment_count": _integer_rule(nullable=True),
                "subreddit": _text_rule(64, nullable=True),
                "media_type": _text_rule(64, nullable=True),
            }
        ),
        "reddit.thread.item.v1": MappingProxyType(
            {
                "text": _text_rule(MAX_TEXT_CHARACTERS, nullable=False),
                "native_id": _text_rule(MAX_NATIVE_ID_CHARACTERS, nullable=True),
                "title": _text_rule(MAX_TITLE_CHARACTERS, nullable=True),
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=True),
                "author": _text_rule(MAX_AUTHOR_CHARACTERS, nullable=True),
                "score": _integer_rule(nullable=True),
                "kind": _text_rule(16, nullable=False),
                "media_type": _text_rule(64, nullable=True),
            }
        ),
        "reddit.subreddit.v1": MappingProxyType(
            {
                "text": _text_rule(MAX_TEXT_CHARACTERS, nullable=True),
                "native_id": _text_rule(64, nullable=False),
                "title": _text_rule(MAX_TITLE_CHARACTERS, nullable=False),
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=False),
                "published_at": _text_rule(MAX_PUBLISHED_CHARACTERS, nullable=True),
                "subscriber_count": _integer_rule(nullable=True),
                "active_count": _integer_rule(nullable=True),
                "nsfw": _integer_rule(),
                "subreddit_type": _text_rule(64, nullable=True),
            }
        ),
        "facebook.search.result.v1": MappingProxyType(
            {
                "text": _text_rule(MAX_TEXT_CHARACTERS, nullable=True),
                "native_id": _text_rule(MAX_NATIVE_ID_CHARACTERS, nullable=False),
                "title": _text_rule(MAX_TITLE_CHARACTERS, nullable=False),
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=False),
            }
        ),
        "facebook.profile.v1": MappingProxyType(
            {
                "text": _text_rule(MAX_TEXT_CHARACTERS, nullable=True),
                "native_id": _text_rule(MAX_NATIVE_ID_CHARACTERS, nullable=False),
                "title": _text_rule(MAX_TITLE_CHARACTERS, nullable=False),
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=False),
                "friend_count": _integer_rule(nullable=True),
                "follower_count": _integer_rule(nullable=True),
            }
        ),
        "facebook.post.v1": MappingProxyType(
            {
                "text": _text_rule(MAX_TEXT_CHARACTERS, nullable=False),
                "native_id": _text_rule(MAX_NATIVE_ID_CHARACTERS, nullable=False),
                "author": _text_rule(MAX_AUTHOR_CHARACTERS, nullable=True),
                "reaction_count": _integer_rule(nullable=True),
                "comment_count": _integer_rule(nullable=True),
                "share_count": _integer_rule(nullable=True),
            }
        ),
        "facebook.group.v1": MappingProxyType(
            {
                "text": _text_rule(MAX_TEXT_CHARACTERS, nullable=True),
                "native_id": _text_rule(MAX_NATIVE_ID_CHARACTERS, nullable=False),
                "title": _text_rule(MAX_TITLE_CHARACTERS, nullable=False),
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=False),
            }
        ),
        "instagram.user.v1": MappingProxyType(
            {
                "native_id": _text_rule(MAX_NATIVE_ID_CHARACTERS, nullable=False),
                "title": _text_rule(MAX_TITLE_CHARACTERS, nullable=False),
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=False),
                "verified": _integer_rule(),
                "private": _integer_rule(),
            }
        ),
        "instagram.profile.v1": MappingProxyType(
            {
                "text": _text_rule(MAX_TEXT_CHARACTERS, nullable=True),
                "native_id": _text_rule(MAX_NATIVE_ID_CHARACTERS, nullable=False),
                "title": _text_rule(MAX_TITLE_CHARACTERS, nullable=False),
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=False),
                "follower_count": _integer_rule(nullable=True),
                "following_count": _integer_rule(nullable=True),
                "post_count": _integer_rule(nullable=True),
                "verified": _integer_rule(),
            }
        ),
        "instagram.post.v1": MappingProxyType(
            {
                "text": _text_rule(MAX_TEXT_CHARACTERS, nullable=True),
                "native_id": _text_rule(MAX_NATIVE_ID_CHARACTERS, nullable=False),
                "author": _text_rule(MAX_AUTHOR_CHARACTERS, nullable=True),
                "published_at": _text_rule(MAX_PUBLISHED_CHARACTERS, nullable=True),
                "reaction_count": _integer_rule(nullable=True),
                "comment_count": _integer_rule(nullable=True),
                "media_type": _text_rule(64, nullable=True),
            }
        ),
        "twitter.post.v1": MappingProxyType(
            {
                "text": _text_rule(MAX_TEXT_CHARACTERS, nullable=True),
                "native_id": _text_rule(MAX_NATIVE_ID_CHARACTERS, nullable=False),
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=False),
                "author": _text_rule(MAX_AUTHOR_CHARACTERS, nullable=True),
                "published_at": _text_rule(MAX_PUBLISHED_CHARACTERS, nullable=True),
                "reaction_count": _integer_rule(nullable=True),
                "view_count": _integer_rule(nullable=True),
                "has_media": _integer_rule(),
            }
        ),
        "xiaohongshu.note.v1": MappingProxyType(
            {
                "text": _text_rule(MAX_TEXT_CHARACTERS, nullable=False),
                "native_id": _text_rule(MAX_NATIVE_ID_CHARACTERS, nullable=False),
                "title": _text_rule(MAX_TITLE_CHARACTERS, nullable=False),
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=False),
                "author": _text_rule(MAX_AUTHOR_CHARACTERS, nullable=True),
                "published_at": _text_rule(MAX_PUBLISHED_CHARACTERS, nullable=True),
                "reaction_count": _integer_rule(nullable=True),
            }
        ),
        "linkedin.people.search.document.v1": MappingProxyType(
            {
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=False),
                "sections": _text_rule(MAX_TEXT_CHARACTERS, nullable=False),
                "references": _text_rule(MAX_TEXT_CHARACTERS, nullable=True),
            }
        ),
        "linkedin.jobs.search.document.v1": MappingProxyType(
            {
                "url": _text_rule(MAX_URL_CHARACTERS, nullable=False),
                "sections": _text_rule(MAX_TEXT_CHARACTERS, nullable=False),
                "references": _text_rule(MAX_TEXT_CHARACTERS, nullable=True),
                "job_ids": _text_rule(MAX_TEXT_CHARACTERS, nullable=False),
            }
        ),
        "xueqiu.stock.v1": MappingProxyType(
            {
                "symbol": _text_rule(MAX_XUEQIU_SYMBOL_CHARACTERS, nullable=False),
                "name": _text_rule(MAX_TITLE_CHARACTERS, nullable=False),
                "exchange": _text_rule(64, nullable=False),
            }
        ),
    }
)

_ExpectedSuccessContract: TypeAlias = tuple[
    str,
    str,
    tuple[str, ...],
    int,
    int,
    frozenset[str],
    int,
]
_EXPECTED_SUCCESS_CONTRACT: Final[Mapping[tuple[str, str], _ExpectedSuccessContract]] = (
    MappingProxyType(
        {
            ("rss", "read.feed"): (
                "feedparser",
                "6.0.12",
                ("rss.feed.v1",),
                1,
                1,
                frozenset({"permanent"}),
                MAX_OUTPUT_BYTES,
            ),
            ("rss", "browse.entries"): (
                "feedparser",
                "6.0.12",
                ("rss.entry.v1",),
                0,
                21,
                frozenset({"permanent"}),
                MAX_OUTPUT_BYTES,
            ),
            ("bilibili", "search.videos"): (
                "bili-cli",
                "0.6.2",
                ("bilibili.video.v1",),
                0,
                50,
                frozenset(),
                _MAX_BILIBILI_OUTPUT_BYTES,
            ),
            ("bilibili", "read.video"): (
                "bili-cli",
                "0.6.2",
                ("bilibili.video.v1",),
                1,
                1,
                frozenset(),
                _MAX_BILIBILI_OUTPUT_BYTES,
            ),
            ("bilibili", "browse.hot"): (
                "bili-cli",
                "0.6.2",
                ("bilibili.video.v1",),
                0,
                50,
                frozenset(),
                _MAX_BILIBILI_OUTPUT_BYTES,
            ),
            ("bilibili", "browse.rank"): (
                "bili-cli",
                "0.6.2",
                ("bilibili.video.v1",),
                0,
                50,
                frozenset(),
                _MAX_BILIBILI_OUTPUT_BYTES,
            ),
            ("youtube", "read.video"): (
                "yt-dlp",
                "2026.7.4",
                ("youtube.video.v1",),
                1,
                1,
                frozenset(),
                _MAX_YOUTUBE_OUTPUT_BYTES,
            ),
            ("youtube", "search.videos"): (
                "yt-dlp",
                "2026.7.4",
                ("youtube.video.v1",),
                0,
                50,
                frozenset(),
                _MAX_YOUTUBE_OUTPUT_BYTES,
            ),
            ("youtube", "read.subtitles"): (
                "yt-dlp",
                "2026.7.4",
                ("youtube.subtitle.v1",),
                1,
                1,
                frozenset(),
                _MAX_YOUTUBE_OUTPUT_BYTES,
            ),
            ("v2ex", "browse.hot"): (
                "v2ex-public-api",
                "legacy-json-2026-07-31",
                ("v2ex.topic.v1",),
                0,
                50,
                frozenset(),
                MAX_OUTPUT_BYTES,
            ),
            ("v2ex", "browse.node_topics"): (
                "v2ex-public-api",
                "legacy-json-2026-07-31",
                ("v2ex.topic.v1",),
                0,
                50,
                frozenset(),
                MAX_OUTPUT_BYTES,
            ),
            ("v2ex", "read.topic"): (
                "v2ex-public-api",
                "legacy-json-2026-07-31",
                ("v2ex.topic.v1", "v2ex.reply.v1"),
                1,
                21,
                frozenset(
                    {
                        "not_found",
                        "authentication",
                        "authorization",
                        "rate_limit",
                        "transient",
                        "permanent",
                        "backend_contract_violation",
                    }
                ),
                MAX_OUTPUT_BYTES,
            ),
            ("v2ex", "read.user"): (
                "v2ex-public-api",
                "legacy-json-2026-07-31",
                ("v2ex.profile.v1",),
                1,
                1,
                frozenset(),
                MAX_OUTPUT_BYTES,
            ),
            ("exa", "search.web"): (
                "exa-mcporter",
                "0.12.3+exa-web.v1",
                ("exa.search.result.v1",),
                0,
                20,
                frozenset(),
                _MAX_EXA_OUTPUT_BYTES,
            ),
            ("exa", "search.code"): (
                "exa-mcporter",
                "0.12.3+exa-code.v1",
                ("exa.code.result.v1",),
                0,
                20,
                frozenset(),
                _MAX_EXA_OUTPUT_BYTES,
            ),
            ("reddit", "search.posts"): (
                "opencli",
                "1.8.6-hermes.1",
                ("reddit.post.v1",),
                0,
                50,
                frozenset(),
                _MAX_OPENCLI_OUTPUT_BYTES,
            ),
            ("reddit", "read.post"): (
                "opencli",
                "1.8.6-hermes.1",
                ("reddit.thread.item.v1",),
                1,
                14,
                frozenset(),
                _MAX_OPENCLI_OUTPUT_BYTES,
            ),
            ("reddit", "browse.subreddit"): (
                "opencli",
                "1.8.6-hermes.1",
                ("reddit.post.v1",),
                0,
                50,
                frozenset(),
                _MAX_OPENCLI_OUTPUT_BYTES,
            ),
            ("reddit", "browse.hot"): (
                "opencli",
                "1.8.6-hermes.1",
                ("reddit.post.v1",),
                0,
                50,
                frozenset(),
                _MAX_OPENCLI_OUTPUT_BYTES,
            ),
            ("reddit", "browse.popular"): (
                "opencli",
                "1.8.6-hermes.1",
                ("reddit.post.v1",),
                0,
                50,
                frozenset(),
                _MAX_OPENCLI_OUTPUT_BYTES,
            ),
            ("reddit", "browse.all"): (
                "opencli",
                "1.8.6-hermes.1",
                ("reddit.post.v1",),
                0,
                50,
                frozenset(),
                _MAX_OPENCLI_OUTPUT_BYTES,
            ),
            ("reddit", "read.subreddit"): (
                "opencli",
                "1.8.6-hermes.1",
                ("reddit.subreddit.v1",),
                1,
                1,
                frozenset(),
                _MAX_OPENCLI_OUTPUT_BYTES,
            ),
            ("facebook", "search"): (
                "opencli",
                "1.8.6-hermes.1",
                ("facebook.search.result.v1",),
                0,
                50,
                frozenset(),
                _MAX_OPENCLI_OUTPUT_BYTES,
            ),
            ("facebook", "read.profile"): (
                "opencli",
                "1.8.6-hermes.1",
                ("facebook.profile.v1",),
                1,
                1,
                frozenset(),
                _MAX_OPENCLI_OUTPUT_BYTES,
            ),
            ("facebook", "browse.feed"): (
                "opencli",
                "1.8.6-hermes.1",
                ("facebook.post.v1",),
                0,
                50,
                frozenset(),
                _MAX_OPENCLI_OUTPUT_BYTES,
            ),
            ("facebook", "browse.groups"): (
                "opencli",
                "1.8.6-hermes.1",
                ("facebook.group.v1",),
                0,
                50,
                frozenset(),
                _MAX_OPENCLI_OUTPUT_BYTES,
            ),
            ("instagram", "search.users"): (
                "opencli",
                "1.8.6-hermes.1",
                ("instagram.user.v1",),
                0,
                50,
                frozenset(),
                _MAX_OPENCLI_OUTPUT_BYTES,
            ),
            ("instagram", "read.profile"): (
                "opencli",
                "1.8.6-hermes.1",
                ("instagram.profile.v1",),
                1,
                1,
                frozenset(),
                _MAX_OPENCLI_OUTPUT_BYTES,
            ),
            ("instagram", "browse.user_posts"): (
                "opencli",
                "1.8.6-hermes.1",
                ("instagram.post.v1",),
                0,
                50,
                frozenset(),
                _MAX_OPENCLI_OUTPUT_BYTES,
            ),
            ("instagram", "browse.explore"): (
                "opencli",
                "1.8.6-hermes.1",
                ("instagram.post.v1",),
                0,
                50,
                frozenset(),
                _MAX_OPENCLI_OUTPUT_BYTES,
            ),
            ("twitter", "search.posts"): (
                "opencli",
                "1.8.6-hermes.1",
                ("twitter.post.v1",),
                0,
                50,
                frozenset(),
                _MAX_OPENCLI_OUTPUT_BYTES,
            ),
            ("xiaohongshu", "search.notes"): (
                "opencli",
                "1.8.6-hermes.1",
                ("xiaohongshu.note.v1",),
                0,
                50,
                frozenset(),
                _MAX_OPENCLI_OUTPUT_BYTES,
            ),
            ("linkedin", "search.people"): (
                "linkedin-scraper-mcp",
                "4.14.0",
                ("linkedin.people.search.document.v1",),
                1,
                1,
                frozenset(),
                _MAX_EXA_OUTPUT_BYTES,
            ),
            ("linkedin", "search.jobs"): (
                "linkedin-scraper-mcp",
                "4.14.0",
                ("linkedin.jobs.search.document.v1",),
                1,
                1,
                frozenset(),
                _MAX_EXA_OUTPUT_BYTES,
            ),
            ("xueqiu", "search.stocks"): (
                "xueqiu-api",
                "1.5.0+search.v1",
                ("xueqiu.stock.v1",),
                0,
                50,
                frozenset(),
                MAX_OUTPUT_BYTES,
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
            schema_ids,
            minimum,
            maximum,
            partial_error_codes,
            maximum_output_bytes,
        ) = expected_contract
        if (
            self.protocol_version != PROTOCOL_VERSION
            or self.backend_id != backend_id
            or self.backend_version != backend_version
            or type(self.truncated) is not bool
            or (
                self.partial_error_code is not None
                and self.partial_error_code not in partial_error_codes
            )
            or any(type(item) is not ExecutionItemV1 for item in items)
        ):
            raise ValueError("invalid execution success")
        if (
            not minimum <= len(items) <= maximum
            or not _valid_result_schema_sequence(
                (self.source, self.operation),
                items,
                schema_ids,
            )
            or (
                self.partial_error_code is not None
                and (self.source, self.operation) == ("v2ex", "read.topic")
                and len(items) != 1
            )
            or (self.source == "bilibili" and any(not _valid_bilibili_item(item) for item in items))
            or (self.source == "youtube" and any(not _valid_youtube_item(item) for item in items))
            or (self.source == "v2ex" and any(not _valid_v2ex_item(item) for item in items))
            or (self.source == "exa" and any(not _valid_exa_item(item) for item in items))
            or (
                self.source in {"reddit", "facebook", "instagram", "twitter", "xiaohongshu"}
                and not _valid_opencli_social_result((self.source, self.operation), items)
            )
            or (self.source == "linkedin" and any(not _valid_linkedin_item(item) for item in items))
            or (self.source == "xueqiu" and not _valid_xueqiu_items(items))
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


def _valid_closed_absolute_path(value: object) -> bool:
    if (
        type(value) is not str
        or not 0 < len(value) <= _MAX_ARTIFACT_PATH_CHARACTERS
        or _contains_control(value)
        or _contains_invalid_scalar(value)
    ):
        return False
    path = Path(value)
    return bool(
        path.is_absolute() and path.name and str(path) == value and os.path.normpath(value) == value
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
    if item.schema_id == "youtube.subtitle.v1":
        return _valid_youtube_subtitle_item(item)
    if item.schema_id != "youtube.video.v1":
        return False
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


def _valid_youtube_subtitle_item(item: ExecutionItemV1) -> bool:
    native_id = item.fields.get("native_id")
    title = item.fields.get("title")
    language = item.fields.get("language")
    text = item.fields.get("text")
    return bool(
        type(native_id) is str
        and _YOUTUBE_VIDEO_ID.fullmatch(native_id)
        and item.fields.get("url") == f"https://www.youtube.com/watch?v={native_id}"
        and type(title) is str
        and _utf8_within(title, _MAX_YOUTUBE_TITLE_BYTES)
        and type(language) is str
        and _YOUTUBE_LANGUAGE.fullmatch(language)
        and item.fields.get("origin") in {"manual", "automatic"}
        and type(text) is str
        and text.lstrip("\ufeff\r\n ").startswith(_YOUTUBE_SUBTITLE_MARKER)
    )


def _valid_result_schema_sequence(
    key: tuple[str, str],
    items: tuple[ExecutionItemV1, ...],
    schema_ids: tuple[str, ...],
) -> bool:
    if key == ("v2ex", "read.topic"):
        if not items or items[0].schema_id != "v2ex.topic.v1":
            return False
        topic_id = items[0].fields.get("native_id")
        replies = items[1:]
        reply_ids = tuple(reply.fields.get("native_id") for reply in replies)
        return bool(
            schema_ids == ("v2ex.topic.v1", "v2ex.reply.v1")
            and type(topic_id) is str
            and all(reply.schema_id == schema_ids[1] for reply in replies)
            and len(set(reply_ids)) == len(reply_ids)
            and all(
                reply.fields.get("url")
                == f"https://www.v2ex.com/t/{topic_id}#reply{reply.fields.get('native_id')}"
                for reply in replies
            )
        )
    return len(schema_ids) == 1 and all(item.schema_id == schema_ids[0] for item in items)


def _valid_v2ex_item(item: ExecutionItemV1) -> bool:
    native_id = item.fields.get("native_id")
    published_at = item.fields.get("published_at")
    if (
        type(native_id) is not str
        or _POSITIVE_DECIMAL.fullmatch(native_id) is None
        or int(native_id) > _MAX_RESULT_INTEGER
        or not _valid_v2ex_timestamp(published_at)
    ):
        return False
    if item.schema_id == "v2ex.topic.v1":
        node = item.fields.get("node")
        author = item.fields.get("author")
        return bool(
            item.fields.get("url") == f"https://www.v2ex.com/t/{native_id}"
            and type(node) is str
            and _V2EX_IDENTIFIER.fullmatch(node)
            and (author is None or (type(author) is str and _V2EX_IDENTIFIER.fullmatch(author)))
        )
    if item.schema_id == "v2ex.reply.v1":
        url = item.fields.get("url")
        author = item.fields.get("author")
        return bool(
            type(url) is str
            and re.fullmatch(
                rf"https://www[.]v2ex[.]com/t/[1-9][0-9]{{0,31}}#reply{re.escape(native_id)}",
                url,
            )
            and type(author) is str
            and _V2EX_IDENTIFIER.fullmatch(author)
        )
    if item.schema_id == "v2ex.profile.v1":
        username = item.fields.get("title")
        return bool(
            type(username) is str
            and _V2EX_IDENTIFIER.fullmatch(username)
            and item.fields.get("url") == f"https://www.v2ex.com/member/{username}"
        )
    return False


def _valid_v2ex_timestamp(value: object) -> bool:
    if value is None:
        return True
    if type(value) is not str or not value.isascii():
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return bool(
        parsed.tzinfo is not None
        and parsed.utcoffset() == timezone.utc.utcoffset(parsed)
        and parsed.year >= 1970
        and parsed.isoformat() == value
    )


def _valid_opencli_social_result(
    key: tuple[str, str],
    items: tuple[ExecutionItemV1, ...],
) -> bool:
    if key == ("reddit", "read.post"):
        if not items or items[0].fields.get("kind") != "post":
            return False
        first = items[0]
        native_id = first.fields.get("native_id")
        return bool(
            type(native_id) is str
            and _REDDIT_POST_ID.fullmatch(native_id)
            and _reddit_post_id_from_url(first.fields.get("url")) == native_id
            and all(
                item.fields.get("kind") == "comment"
                and item.fields.get("native_id") is None
                and item.fields.get("title") is None
                and item.fields.get("url") is None
                for item in items[1:]
            )
        )
    if key == ("reddit", "read.subreddit"):
        if len(items) != 1:
            return False
        item = items[0]
        native_id = item.fields.get("native_id")
        return bool(
            type(native_id) is str
            and _SUBREDDIT_IDENTIFIER.fullmatch(native_id)
            and item.fields.get("url") == f"https://www.reddit.com/r/{native_id}/"
            and item.fields.get("nsfw") in {0, 1}
        )
    if key[0] == "reddit":
        return all(
            type(item.fields.get("native_id")) is str
            and _REDDIT_POST_ID.fullmatch(cast(str, item.fields["native_id"]))
            and _reddit_post_id_from_url(item.fields.get("url")) == item.fields.get("native_id")
            for item in items
        )
    if key == ("facebook", "read.profile"):
        return len(items) == 1 and _valid_social_profile_item(items[0], "facebook.com")
    if key in {("facebook", "search"), ("facebook", "browse.groups")}:
        return all(
            _valid_hosted_public_url(item.fields.get("url"), "facebook.com") for item in items
        )
    if key == ("instagram", "read.profile"):
        return bool(
            len(items) == 1
            and _valid_social_profile_item(items[0], "instagram.com")
            and items[0].fields.get("verified") in {0, 1}
        )
    if key == ("instagram", "search.users"):
        return all(
            _valid_social_profile_item(item, "instagram.com")
            and item.fields.get("verified") in {0, 1}
            and item.fields.get("private") in {0, 1}
            for item in items
        )
    if key == ("twitter", "search.posts"):
        return _unique_native_ids(items) and all(
            type(item.fields.get("native_id")) is str
            and _POSITIVE_DECIMAL.fullmatch(cast(str, item.fields["native_id"]))
            and item.fields.get("url") == f"https://x.com/i/status/{item.fields['native_id']}"
            and item.fields.get("has_media") in {0, 1}
            for item in items
        )
    if key == ("xiaohongshu", "search.notes"):
        return _unique_native_ids(items) and all(
            type(item.fields.get("native_id")) is str
            and _XIAOHONGSHU_NOTE_ID.fullmatch(cast(str, item.fields["native_id"]))
            and item.fields.get("url")
            == f"https://www.xiaohongshu.com/explore/{item.fields['native_id']}"
            for item in items
        )
    return key in {
        ("facebook", "browse.feed"),
        ("instagram", "browse.user_posts"),
        ("instagram", "browse.explore"),
    }


def _valid_social_profile_item(item: ExecutionItemV1, host: str) -> bool:
    native_id = item.fields.get("native_id")
    return bool(
        type(native_id) is str
        and _SOCIAL_USERNAME.fullmatch(native_id)
        and _valid_hosted_public_url(item.fields.get("url"), host)
    )


def _valid_hosted_public_url(value: object, suffix: str) -> bool:
    if not _valid_public_result_url(value):
        return False
    try:
        host = urlsplit(cast(str, value)).hostname
    except (UnicodeError, ValueError):
        return False
    return bool(host == suffix or (type(host) is str and host.endswith(f".{suffix}")))


def _reddit_post_identity_from_url(value: object) -> tuple[str, str] | None:
    if type(value) is not str or not value.isascii() or len(value) > 512:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (UnicodeError, ValueError):
        return None
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or type(host) is not str
        or host not in {"reddit.com", "www.reddit.com"}
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    normalized_path = parsed.path[:-1] if parsed.path.endswith("/") else parsed.path
    parts = normalized_path.split("/")
    if (
        len(parts) not in {5, 6}
        or parts[0] != ""
        or parts[1] != "r"
        or parts[3] != "comments"
        or re.fullmatch(r"[A-Za-z0-9_]{1,32}", parts[2]) is None
        or _REDDIT_POST_ID.fullmatch(parts[4].lower()) is None
        or (len(parts) == 6 and re.fullmatch(r"[A-Za-z0-9_-]{1,256}", parts[5]) is None)
    ):
        return None
    return parts[2], parts[4].lower()


def _reddit_post_id_from_url(value: object) -> str | None:
    identity = _reddit_post_identity_from_url(value)
    return None if identity is None else identity[1]


def _valid_exa_item(item: ExecutionItemV1) -> bool:
    if item.schema_id not in {"exa.search.result.v1", "exa.code.result.v1"}:
        return False
    return _valid_public_result_url(item.fields.get("url"))


def _valid_linkedin_item(item: ExecutionItemV1) -> bool:
    url = item.fields.get("url")
    if not _valid_public_result_url(url) or type(url) is not str:
        return False
    parsed = urlsplit(url)
    expected_path = {
        "linkedin.people.search.document.v1": "/search/results/people/",
        "linkedin.jobs.search.document.v1": "/jobs/search/",
    }.get(item.schema_id)
    if (
        expected_path is None
        or parsed.scheme != "https"
        or parsed.hostname != "www.linkedin.com"
        or parsed.port not in {None, 443}
        or parsed.path != expected_path
        or parsed.fragment
    ):
        return False
    try:
        query = parse_qs(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=1,
        )
    except ValueError:
        return False
    if set(query) != {"keywords"} or len(query["keywords"]) != 1:
        return False
    keywords = query["keywords"][0]
    if (
        not keywords
        or keywords != keywords.strip()
        or len(keywords) > _MAX_BILIBILI_QUERY_CHARACTERS
        or _contains_invalid_scalar(keywords)
        or parsed.query != f"keywords={quote_plus(keywords)}"
    ):
        return False
    sections = _closed_json_text(item.fields.get("sections"))
    if not isinstance(sections, dict):
        return False
    if sections and (
        set(sections) != {"search_results"}
        or type(sections["search_results"]) is not str
        or not sections["search_results"]
        or _contains_invalid_scalar(sections["search_results"])
    ):
        return False
    references = item.fields.get("references")
    if references is not None and not _valid_linkedin_references(_closed_json_text(references)):
        return False
    if item.schema_id == "linkedin.jobs.search.document.v1":
        job_ids = item.fields.get("job_ids")
        if not _valid_linkedin_job_ids(_closed_json_text(job_ids)):
            return False
    return True


def _closed_json_text(value: object) -> object:
    if type(value) is not str:
        return None

    def object_from_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        selected: dict[str, object] = {}
        for key, child in pairs:
            if key in selected:
                raise ValueError("duplicate JSON key")
            selected[key] = child
        return selected

    try:
        decoded = json.loads(
            value,
            object_pairs_hook=object_from_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    try:
        canonical = json.dumps(
            decoded,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        return None
    return decoded if canonical == value else None


def _valid_linkedin_references(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"search_results"}:
        return False
    references = value["search_results"]
    if not isinstance(references, list) or len(references) > 15:
        return False
    urls: set[str] = set()
    for reference in references:
        selected = _linkedin_reference_fields(reference)
        if selected is None or selected["url"] in urls:
            return False
        urls.add(selected["url"])
    return True


def _linkedin_reference_fields(value: object) -> dict[str, str] | None:
    if type(value) is not dict or any(type(key) is not str for key in value):
        return None
    reference = cast(dict[str, object], value)
    if not {"kind", "url"}.issubset(reference) or not set(reference).issubset(
        {"kind", "url", "text", "context", "value"}
    ):
        return None
    kind = reference["kind"]
    url = reference["url"]
    if (
        type(kind) is not str
        or kind not in _LINKEDIN_REFERENCE_KINDS
        or type(url) is not str
        or not _valid_linkedin_reference_url(kind, url, reference.get("value"))
    ):
        return None
    selected = {"kind": kind, "url": url}
    text = reference.get("text")
    if text is not None:
        if (
            kind == "company_urn"
            or type(text) is not str
            or text != text.strip()
            or not 2 <= len(text) <= 80
            or _contains_invalid_scalar(text)
        ):
            return None
        selected["text"] = text
    context = reference.get("context")
    if context is not None:
        if type(context) is not str or context not in _LINKEDIN_REFERENCE_CONTEXTS:
            return None
        selected["context"] = context
    urn_value = reference.get("value")
    if kind == "company_urn":
        if type(urn_value) is not str:
            return None
        selected["value"] = urn_value
    elif urn_value is not None:
        return None
    return selected


def _valid_linkedin_reference_url(kind: str, value: str, urn_value: object) -> bool:
    if (
        not value
        or value != value.strip()
        or len(value) > MAX_URL_CHARACTERS
        or _contains_invalid_scalar(value)
    ):
        return False
    if kind == "external":
        if urn_value is not None or not _valid_public_result_url(value):
            return False
        parsed = urlsplit(value)
        return not parsed.query and not parsed.fragment
    if not value.isascii():
        return False
    if kind == "company_urn":
        match = _LINKEDIN_COMPANY_URN_URL.fullmatch(value)
        return bool(match is not None and type(urn_value) is str and urn_value == match.group(1))
    prefix = _LINKEDIN_REFERENCE_PREFIXES.get(kind)
    if prefix is None or not value.startswith(prefix) or not value.endswith("/"):
        return False
    segment = value[len(prefix) : -1]
    if kind == "job":
        return _POSITIVE_DECIMAL.fullmatch(segment) is not None
    return _valid_linkedin_reference_segment(segment)


def _valid_linkedin_reference_segment(value: str) -> bool:
    if not 1 <= len(value) <= 512 or not value[0].isalnum():
        return False
    index = 0
    while index < len(value):
        character = value[index]
        if character == "%":
            if index + 2 >= len(value) or any(
                child not in "0123456789ABCDEF" for child in value[index + 1 : index + 3]
            ):
                return False
            index += 3
            continue
        if not (character.isalnum() or character in "-._~:"):
            return False
        index += 1
    return True


def _valid_linkedin_job_ids(value: object) -> bool:
    if not isinstance(value, list) or len(value) > 50:
        return False
    seen: set[str] = set()
    for job_id in value:
        if type(job_id) is not str or _POSITIVE_DECIMAL.fullmatch(job_id) is None or job_id in seen:
            return False
        seen.add(job_id)
    return True


def _unique_native_ids(items: tuple[ExecutionItemV1, ...]) -> bool:
    values = tuple(item.fields.get("native_id") for item in items)
    return len(values) == len(set(values))


def _valid_xueqiu_items(items: tuple[ExecutionItemV1, ...]) -> bool:
    symbols: set[str] = set()
    for item in items:
        symbol = item.fields.get("symbol")
        exchange = item.fields.get("exchange")
        if (
            item.schema_id != "xueqiu.stock.v1"
            or type(symbol) is not str
            or symbol in symbols
            or type(exchange) is not str
            or not _valid_xueqiu_stock_identity(symbol, exchange)
        ):
            return False
        symbols.add(symbol)
    return True


def _valid_xueqiu_stock_identity(symbol: object, exchange: object) -> bool:
    if (
        type(symbol) is not str
        or not symbol.isascii()
        or symbol != symbol.strip()
        or not 1 <= len(symbol) <= MAX_XUEQIU_SYMBOL_CHARACTERS
        or _contains_invalid_scalar(symbol)
        or type(exchange) is not str
        or not exchange.isascii()
        or exchange != exchange.strip()
        or _STOCK_EXCHANGE.fullmatch(exchange) is None
        or _contains_invalid_scalar(exchange)
    ):
        return False
    mainland = _MAINLAND_STOCK_SYMBOL.fullmatch(symbol)
    if mainland is not None:
        prefix = mainland.group(1)
        return exchange in {prefix, f"{prefix}A"}
    qualified = _QUALIFIED_STOCK_SYMBOL.fullmatch(symbol)
    return qualified is not None and qualified.group(1) == exchange


def _valid_public_result_url(value: object) -> bool:
    if (
        type(value) is not str
        or not value.isascii()
        or not 0 < len(value) <= MAX_URL_CHARACTERS
        or value != value.strip()
        or _contains_control(value)
        or any(character.isspace() for character in value)
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
            return bool(
                len(labels) >= 2
                and all(
                    0 < len(label) <= 63
                    and label[0].isalnum()
                    and label[-1].isalnum()
                    and all(character.isalnum() or character == "-" for character in label)
                    for label in labels
                )
            )
        return _is_global_address(address)
    except (UnicodeError, ValueError):
        return False


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
