"""Fork-owned execution for the fixed public V2EX legacy JSON API."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Final, cast

from ._v2ex_transport import (
    V2exTransport,
    V2exTransportFailure,
    load_v2ex_transport,
)
from .contracts import (
    MAX_TEXT_CHARACTERS,
    MAX_TITLE_CHARACTERS,
    PROTOCOL_VERSION,
    ExecutionContextV1,
    ExecutionErrorCodeV1,
    ExecutionFailureV1,
    ExecutionItemV1,
    ExecutionRequestV1,
    ExecutionResultV1,
    ExecutionSuccessV1,
    NetworkAccessV1,
    _contains_invalid_scalar,
)

_BACKEND_ID: Final = "v2ex-public-api"
_BACKEND_VERSION: Final = "legacy-json-2026-07-31"
_BASE_URL: Final = "https://www.v2ex.com"
_MAX_BROWSE_ITEMS: Final = 50
_MAX_REPLIES: Final = 20
_MAX_IDENTIFIER_CHARACTERS: Final = 64
_MAX_TOPIC_ID_CHARACTERS: Final = 32
_MAX_RESULT_INTEGER: Final = (1 << 53) - 1
_IDENTIFIER: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_PARTIAL_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        "not_found",
        "authentication",
        "authorization",
        "rate_limit",
        "transient",
        "permanent",
        "backend_contract_violation",
    }
)


class _V2exDataError(Exception):
    pass


class _V2exNotFoundError(_V2exDataError):
    pass


class _CheckpointRaised(BaseException):
    def __init__(self, original: BaseException) -> None:
        self.original = original


def execute_v2ex(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
) -> ExecutionResultV1:
    """Execute one registry-validated V2EX operation."""

    if not _valid_request(request) or not _valid_context(context):
        return _failure(request, "backend_contract_violation")

    transport = load_v2ex_transport()
    if isinstance(transport, str):
        return _failure(request, transport)

    try:
        _checkpoint(context)
        result = _execute(request, context, transport)
        _checkpoint(context)
        return result
    except _CheckpointRaised as raised:
        raise raised.original
    except _V2exNotFoundError:
        return _failure(request, "not_found")
    except V2exTransportFailure as error:
        return _failure(request, error.error_code)
    except (_V2exDataError, TypeError, ValueError):
        return _failure(request, "backend_contract_violation")
    except Exception:
        return _failure(request, "transient")


def _execute(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
    transport: V2exTransport,
) -> ExecutionResultV1:
    if request.operation == "browse.hot":
        raw = transport.fetch_hot_topics(lambda: _checkpoint(context))
        projected, text_truncated = _project_topics(raw, context)
        limit = _effective_browse_limit(request, context)
        requested = cast(int, request.arguments["limit"])
        return _success(
            request,
            tuple(projected[:limit]),
            truncated=(len(projected) > limit or requested > limit or text_truncated),
        )

    if request.operation == "browse.node_topics":
        node = cast(str, request.arguments["node"])
        page = cast(int, request.arguments["page"])
        raw = transport.fetch_node_topics(node, page, lambda: _checkpoint(context))
        projected, text_truncated = _project_topics(raw, context, expected_node=node)
        limit = _effective_browse_limit(request, context)
        requested = cast(int, request.arguments["limit"])
        return _success(
            request,
            tuple(projected[:limit]),
            truncated=(len(projected) > limit or requested > limit or text_truncated),
        )

    if request.operation == "read.topic":
        topic_id = _canonical_topic_id(cast(str, request.arguments["topic_id"]))
        raw_topic = transport.fetch_topic(topic_id, lambda: _checkpoint(context))
        topic, topic_truncated = _project_single_topic(raw_topic, context, topic_id)
        try:
            raw_replies = transport.fetch_replies(
                topic_id,
                lambda: _checkpoint(context),
            )
            replies, replies_truncated = _project_replies(raw_replies, context, topic_id)
        except _CheckpointRaised:
            raise
        except V2exTransportFailure as error:
            return _partial_topic(request, topic, topic_truncated, error.error_code)
        except (_V2exDataError, TypeError, ValueError):
            return _partial_topic(
                request,
                topic,
                topic_truncated,
                "backend_contract_violation",
            )

        reply_limit = min(
            _MAX_REPLIES,
            max(context.limits.maximum_items - 1, 0),
        )
        selected = tuple(replies[:reply_limit])
        return _success(
            request,
            (topic, *selected),
            truncated=(topic_truncated or replies_truncated or len(replies) > reply_limit),
        )

    if request.operation == "read.user":
        username = cast(str, request.arguments["username"])
        raw = transport.fetch_user(username, lambda: _checkpoint(context))
        profile, text_truncated = _project_profile(raw, context, username)
        return _success(request, (profile,), truncated=text_truncated)

    raise _V2exDataError("operation invalid")


def _valid_request(request: ExecutionRequestV1) -> bool:
    if (
        type(request) is not ExecutionRequestV1
        or request.protocol_version != PROTOCOL_VERSION
        or request.source != "v2ex"
    ):
        return False
    arguments = request.arguments
    if request.operation == "browse.hot" and set(arguments) == {"limit"}:
        limit = arguments["limit"]
        return type(limit) is int and 1 <= limit <= _MAX_BROWSE_ITEMS
    if request.operation == "browse.node_topics" and set(arguments) == {
        "node",
        "page",
        "limit",
    }:
        node = arguments["node"]
        page = arguments["page"]
        limit = arguments["limit"]
        return bool(
            type(node) is str
            and len(node) <= _MAX_IDENTIFIER_CHARACTERS
            and _IDENTIFIER.fullmatch(node)
            and type(page) is int
            and 1 <= page <= 100
            and type(limit) is int
            and 1 <= limit <= _MAX_BROWSE_ITEMS
        )
    if request.operation == "read.topic" and set(arguments) == {"topic_id"}:
        topic_id = arguments["topic_id"]
        return bool(
            type(topic_id) is str
            and 1 <= len(topic_id) <= _MAX_TOPIC_ID_CHARACTERS
            and topic_id.isascii()
            and topic_id.isdigit()
            and int(topic_id) > 0
        )
    if request.operation == "read.user" and set(arguments) == {"username"}:
        username = arguments["username"]
        return bool(
            type(username) is str
            and len(username) <= _MAX_IDENTIFIER_CHARACTERS
            and _IDENTIFIER.fullmatch(username)
        )
    return False


def _valid_context(context: ExecutionContextV1) -> bool:
    return bool(
        type(context) is ExecutionContextV1
        and len(context.host_capabilities) == 1
        and type(context.host_capabilities[0]) is NetworkAccessV1
    )


def _effective_browse_limit(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
) -> int:
    return min(
        cast(int, request.arguments["limit"]),
        context.limits.maximum_items,
        _MAX_BROWSE_ITEMS,
    )


def _project_topics(
    value: object,
    context: ExecutionContextV1,
    *,
    expected_node: str | None = None,
) -> tuple[list[ExecutionItemV1], bool]:
    values = _list(value)
    projected: list[ExecutionItemV1] = []
    truncated = False
    seen: set[int] = set()
    for raw in values:
        _checkpoint(context)
        item, item_truncated, topic_id = _project_topic(
            raw,
            context,
            expected_node=expected_node,
        )
        if topic_id in seen:
            raise _V2exDataError("duplicate topic")
        seen.add(topic_id)
        projected.append(item)
        truncated = truncated or item_truncated
    return projected, truncated


def _project_single_topic(
    value: object,
    context: ExecutionContextV1,
    requested_topic_id: str,
) -> tuple[ExecutionItemV1, bool]:
    values = _list(value)
    if not values:
        raise _V2exNotFoundError("topic not found")
    if len(values) != 1:
        raise _V2exDataError("topic result invalid")
    topic, truncated, topic_id = _project_topic(values[0], context)
    if str(topic_id) != requested_topic_id:
        raise _V2exDataError("topic identity invalid")
    return topic, truncated


def _project_topic(
    value: object,
    context: ExecutionContextV1,
    *,
    expected_node: str | None = None,
) -> tuple[ExecutionItemV1, bool, int]:
    raw = _mapping(value)
    topic_id = _positive_integer(raw, "id")
    title, title_truncated = _projected_text(
        raw.get("title"),
        MAX_TITLE_CHARACTERS,
        required=True,
    )
    text, text_truncated = _projected_text(
        raw.get("content"),
        min(context.limits.maximum_text_characters, MAX_TEXT_CHARACTERS),
        required=False,
    )
    node = _mapping(raw.get("node"))
    node_name = _identifier(node.get("name"), required=True)
    if expected_node is not None and cast(str, node_name).casefold() != expected_node.casefold():
        raise _V2exDataError("topic node identity invalid")

    member_value = raw.get("member")
    author: str | None
    if member_value is None:
        author = None
    else:
        member = _mapping(member_value)
        author = _identifier(member.get("username"), required=False)

    item = ExecutionItemV1(
        "v2ex.topic.v1",
        {
            "text": text,
            "native_id": str(topic_id),
            "title": cast(str, title),
            "url": f"{_BASE_URL}/t/{topic_id}",
            "author": author,
            "published_at": _timestamp(_optional_integer(raw, "created")),
            "node": node_name,
        },
    )
    return item, title_truncated or text_truncated, topic_id


def _project_replies(
    value: object,
    context: ExecutionContextV1,
    topic_id: str,
) -> tuple[list[ExecutionItemV1], bool]:
    values = _list(value)
    projected: list[ExecutionItemV1] = []
    truncated = False
    seen: set[int] = set()
    for value in values:
        _checkpoint(context)
        raw = _mapping(value)
        reply_id = _positive_integer(raw, "id")
        if reply_id in seen:
            raise _V2exDataError("duplicate reply")
        seen.add(reply_id)
        text, text_truncated = _projected_text(
            raw.get("content"),
            min(context.limits.maximum_text_characters, MAX_TEXT_CHARACTERS),
            required=True,
        )
        member = _mapping(raw.get("member"))
        author = _identifier(member.get("username"), required=True)
        projected.append(
            ExecutionItemV1(
                "v2ex.reply.v1",
                {
                    "text": cast(str, text),
                    "native_id": str(reply_id),
                    "url": f"{_BASE_URL}/t/{topic_id}#reply{reply_id}",
                    "author": cast(str, author),
                    "published_at": _timestamp(_optional_integer(raw, "created")),
                },
            )
        )
        truncated = truncated or text_truncated
    return projected, truncated


def _project_profile(
    value: object,
    context: ExecutionContextV1,
    requested_username: str,
) -> tuple[ExecutionItemV1, bool]:
    raw = _mapping(value)
    status = raw.get("status")
    if status == "notfound":
        raise _V2exNotFoundError("profile not found")
    if status not in {None, "found"}:
        raise _V2exDataError("profile status invalid")

    username = _identifier(raw.get("username"), required=True)
    if cast(str, username).casefold() != requested_username.casefold():
        raise _V2exDataError("profile identity invalid")
    member_id = _positive_integer(raw, "id")

    parts: list[str] = []
    source_truncated = False
    for name in ("bio", "location", "website", "github"):
        part, part_truncated = _projected_text(
            raw.get(name),
            MAX_TEXT_CHARACTERS,
            required=False,
        )
        if part is not None:
            parts.append(part)
        source_truncated = source_truncated or part_truncated
    combined = " ".join(parts)
    maximum = min(context.limits.maximum_text_characters, MAX_TEXT_CHARACTERS)
    truncated = source_truncated or len(combined) > maximum
    text = combined[:maximum] or None
    item = ExecutionItemV1(
        "v2ex.profile.v1",
        {
            "text": text,
            "native_id": str(member_id),
            "title": username,
            "url": f"{_BASE_URL}/member/{username}",
            "published_at": _timestamp(_optional_integer(raw, "created")),
        },
    )
    return item, truncated


def _mapping(value: object) -> Mapping[str, object]:
    if type(value) is not dict or not all(type(key) is str for key in value):
        raise _V2exDataError("JSON object required")
    return cast(Mapping[str, object], value)


def _list(value: object) -> list[object]:
    if type(value) is not list:
        raise _V2exDataError("JSON list required")
    return cast(list[object], value)


def _projected_text(
    value: object,
    maximum: int,
    *,
    required: bool,
) -> tuple[str | None, bool]:
    if value is None:
        if required:
            raise _V2exDataError("text required")
        return None, False
    if type(value) is not str or _contains_invalid_scalar(value):
        raise _V2exDataError("text invalid")
    normalized = " ".join(value.split())
    if not normalized:
        if required:
            raise _V2exDataError("text required")
        return None, False
    return normalized[:maximum], len(normalized) > maximum


def _identifier(value: object, *, required: bool) -> str | None:
    if value is None and not required:
        return None
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise _V2exDataError("identifier invalid")
    return value


def _positive_integer(value: Mapping[str, object], name: str) -> int:
    raw = value.get(name)
    if type(raw) is not int or not 0 < raw <= _MAX_RESULT_INTEGER:
        raise _V2exDataError("positive integer required")
    return raw


def _optional_integer(value: Mapping[str, object], name: str) -> int | None:
    raw = value.get(name)
    if raw is None:
        return None
    if type(raw) is not int or not 0 <= raw <= _MAX_RESULT_INTEGER:
        raise _V2exDataError("integer invalid")
    return raw


def _timestamp(value: int | None) -> str | None:
    if value is None:
        return None
    try:
        timestamp = datetime.fromtimestamp(value, timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise _V2exDataError("timestamp invalid") from None
    if timestamp.year < 1970:
        raise _V2exDataError("timestamp invalid")
    return timestamp.isoformat()


def _canonical_topic_id(value: str) -> str:
    return str(int(value))


def _partial_topic(
    request: ExecutionRequestV1,
    topic: ExecutionItemV1,
    truncated: bool,
    error_code: ExecutionErrorCodeV1,
) -> ExecutionSuccessV1:
    selected_error: ExecutionErrorCodeV1 = (
        error_code if error_code in _PARTIAL_ERROR_CODES else "permanent"
    )
    return _success(
        request,
        (topic,),
        truncated=truncated,
        partial_error_code=selected_error,
    )


def _success(
    request: ExecutionRequestV1,
    items: tuple[ExecutionItemV1, ...],
    *,
    truncated: bool,
    partial_error_code: ExecutionErrorCodeV1 | None = None,
) -> ExecutionSuccessV1:
    return ExecutionSuccessV1(
        protocol_version=PROTOCOL_VERSION,
        source="v2ex",
        operation=request.operation,
        backend_id=_BACKEND_ID,
        backend_version=_BACKEND_VERSION,
        items=items,
        truncated=truncated,
        partial_error_code=partial_error_code,
    )


def _failure(
    request: ExecutionRequestV1,
    error_code: ExecutionErrorCodeV1,
) -> ExecutionFailureV1:
    return ExecutionFailureV1(
        protocol_version=PROTOCOL_VERSION,
        source="v2ex",
        operation=request.operation,
        backend_id=_BACKEND_ID,
        backend_version=_BACKEND_VERSION,
        error_code=error_code,
    )


def _checkpoint(context: ExecutionContextV1) -> None:
    try:
        context.checkpoint()
    except BaseException as error:
        raise _CheckpointRaised(error) from None
