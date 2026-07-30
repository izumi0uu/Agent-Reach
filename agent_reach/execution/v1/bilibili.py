"""Fork-owned Bilibili execution through the fixed bili-cli entry point."""

from __future__ import annotations

import io
import json
import re
from collections.abc import Callable, Mapping
from contextlib import redirect_stdout
from importlib import import_module
from importlib.metadata import PackageNotFoundError, distribution
from types import MappingProxyType
from typing import Final, cast

from .contracts import (
    _MAX_BILIBILI_AUTHOR_CHARACTERS,
    _MAX_BILIBILI_OUTPUT_BYTES,
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
    _contains_invalid_scalar,
)

_BACKEND_DISTRIBUTION: Final = "bilibili-cli"
_BACKEND_ID: Final = "bili-cli"
_BACKEND_VERSION: Final = "0.6.2"
_BACKEND_ENTRY_POINT: Final = "bili_cli.cli:cli"
_BACKEND_MODULE: Final = "bili_cli.cli"
_BVID: Final = re.compile(r"BV[A-Za-z0-9]{10}")
_MAX_OUTPUT_BYTES: Final = _MAX_BILIBILI_OUTPUT_BYTES
_MAX_JSON_DEPTH: Final = 12
_MAX_JSON_ITEMS: Final = 64
_MAX_STRING_BYTES: Final = 64 * 1024
_MAX_RESULT_INTEGER: Final = (1 << 53) - 1

_BACKEND_ERROR_CODES: Final[Mapping[str, ExecutionErrorCodeV1]] = MappingProxyType(
    {
        "invalid_input": "invalid_input",
        "not_found": "not_found",
        "not_authenticated": "authentication",
        "permission_denied": "authorization",
        "rate_limited": "rate_limit",
        "network_error": "transient",
        "upstream_error": "permanent",
        "internal_error": "permanent",
    }
)


class _BackendContractError(Exception):
    pass


class _BackendInvocationError(Exception):
    pass


class _BoundedTextSink(io.StringIO):
    def __init__(self, maximum_bytes: int = _MAX_OUTPUT_BYTES) -> None:
        super().__init__()
        self._maximum_bytes = maximum_bytes
        self._size = 0

    def write(self, value: str) -> int:
        if type(value) is not str:
            raise TypeError("backend output invalid")
        try:
            size = len(value.encode("utf-8", errors="strict"))
        except UnicodeError:
            raise _BackendContractError("backend output invalid") from None
        if self._size + size > self._maximum_bytes:
            raise _BackendContractError("backend output invalid")
        self._size += size
        return super().write(value)


def execute_bilibili(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
) -> ExecutionResultV1:
    """Execute one registry-validated Bilibili request."""

    backend = _load_backend()
    if isinstance(backend, str):
        return _failure(request, backend)

    context.checkpoint()
    try:
        envelope = _invoke_backend(request, context, backend)
    except _BackendContractError:
        return _failure(request, "backend_contract_violation")
    except _BackendInvocationError:
        return _failure(request, "transient")
    context.checkpoint()

    if envelope["ok"] is False:
        error = cast(Mapping[str, object], envelope["error"])
        code = cast(str, error["code"])
        return _failure(request, _BACKEND_ERROR_CODES.get(code, "permanent"))

    try:
        result = _project_success(request, context, envelope["data"])
    except (TypeError, ValueError, _BackendContractError):
        return _failure(request, "backend_contract_violation")
    context.checkpoint()
    return result


def _load_backend() -> Callable[..., object] | ExecutionErrorCodeV1:
    try:
        installed = distribution(_BACKEND_DISTRIBUTION)
    except PackageNotFoundError:
        return "backend_unavailable"
    except Exception:
        return "backend_unavailable"
    try:
        if installed.version != _BACKEND_VERSION:
            return "backend_incompatible"
        entry_points = [
            entry
            for entry in installed.entry_points
            if entry.group == "console_scripts" and entry.name == "bili"
        ]
        if len(entry_points) != 1 or entry_points[0].value != _BACKEND_ENTRY_POINT:
            return "backend_incompatible"
    except Exception:
        return "backend_incompatible"
    try:
        module = import_module(_BACKEND_MODULE)
        cli = getattr(module, "cli", None)
        main = getattr(cli, "main", None)
    except Exception:
        return "backend_incompatible"
    if not callable(main):
        return "backend_incompatible"
    return cast(Callable[..., object], main)


def _invoke_backend(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
    main: Callable[..., object],
) -> Mapping[str, object]:
    sink = _BoundedTextSink()
    exited = False
    try:
        with redirect_stdout(sink):
            try:
                main(
                    args=list(_argv(request, context)),
                    prog_name="bili",
                    standalone_mode=False,
                )
            except SystemExit as error:
                if type(error.code) is not int or error.code != 1:
                    raise _BackendContractError("backend exit invalid") from None
                exited = True
    except _BackendContractError:
        raise
    except Exception:
        raise _BackendInvocationError("backend invocation failed") from None

    envelope = _validated_backend_envelope(
        _load_json(sink.getvalue().encode("utf-8", errors="strict"))
    )
    if (envelope["ok"] is False) != exited:
        raise _BackendContractError("backend exit invalid")
    return envelope


def _argv(request: ExecutionRequestV1, context: ExecutionContextV1) -> tuple[str, ...]:
    arguments = request.arguments
    if request.operation == "search.videos":
        query = arguments["query"]
        if type(query) is not str:
            raise _BackendContractError("request invalid")
        return (
            "search",
            "--type",
            "video",
            "--max",
            str(_effective_limit(request, context)),
            "--json",
            "--",
            query,
        )
    if request.operation == "read.video":
        url = arguments["url"]
        if type(url) is not str:
            raise _BackendContractError("request invalid")
        return ("video", url, "--json")
    if request.operation == "browse.hot":
        return ("hot", "--max", str(_effective_limit(request, context)), "--json")
    if request.operation == "browse.rank":
        return ("rank", "--max", str(_effective_limit(request, context)), "--json")
    raise _BackendContractError("request invalid")


def _project_success(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
    data: object,
) -> ExecutionSuccessV1:
    maximum_text = min(context.limits.maximum_text_characters, MAX_TEXT_CHARACTERS)
    text_truncated = False
    if request.operation == "search.videos":
        limit = _effective_limit(request, context)
        items, text_truncated = _project_search_items(data, limit, maximum_text)
    elif request.operation == "read.video":
        url = request.arguments["url"]
        if type(url) is not str:
            raise _BackendContractError("request invalid")
        item, text_truncated = _project_video_command(data, url, maximum_text)
        items = (item,)
    elif request.operation == "browse.hot":
        limit = _effective_limit(request, context)
        items, text_truncated = _project_listing(
            data,
            limit,
            maximum_text,
            page=1,
        )
    elif request.operation == "browse.rank":
        limit = _effective_limit(request, context)
        items, text_truncated = _project_listing(
            data,
            limit,
            maximum_text,
            day=3,
        )
    else:
        raise _BackendContractError("request invalid")
    return ExecutionSuccessV1(
        PROTOCOL_VERSION,
        "bilibili",
        request.operation,
        _BACKEND_ID,
        _BACKEND_VERSION,
        items,
        truncated=_request_was_narrowed(request, context) or text_truncated,
    )


def _project_search_items(
    value: object,
    limit: int,
    maximum_text: int,
) -> tuple[tuple[ExecutionItemV1, ...], bool]:
    if not isinstance(value, list) or len(value) > limit:
        raise _BackendContractError("backend data invalid")
    projected = tuple(_project_search_item(item, maximum_text) for item in value)
    return tuple(item for item, _ in projected), any(truncated for _, truncated in projected)


def _project_search_item(value: object, maximum_text: int) -> tuple[ExecutionItemV1, bool]:
    item = _closed_mapping(value, {"id", "bvid", "title", "author", "play", "duration"})
    bvid = _bvid(item.get("bvid"))
    if item.get("id") != bvid:
        raise _BackendContractError("backend item invalid")
    title = _required_text(item.get("title"), MAX_TITLE_CHARACTERS)
    return (
        _video_item(
            text=title[:maximum_text],
            bvid=bvid,
            title=title,
            author=_optional_text(item.get("author"), _MAX_BILIBILI_AUTHOR_CHARACTERS),
            duration_seconds=_duration_seconds(item.get("duration")),
            view_count=_non_negative_integer(item.get("play")),
        ),
        len(title) > maximum_text,
    )


def _project_video_command(
    value: object,
    requested_url: str,
    maximum_text: int,
) -> tuple[ExecutionItemV1, bool]:
    data = _closed_mapping(
        value,
        {"video", "subtitle", "ai_summary", "comments", "related", "warnings"},
    )
    subtitle = _closed_mapping(
        data.get("subtitle"),
        {"available", "format", "text", "items"},
    )
    if (
        subtitle.get("available") is not False
        or type(subtitle.get("format")) is not str
        or subtitle.get("format") != "plain"
        or type(subtitle.get("text")) is not str
        or subtitle.get("text") != ""
        or type(subtitle.get("items")) is not list
        or bool(subtitle.get("items"))
        or type(data.get("ai_summary")) is not str
        or data.get("ai_summary") != ""
        or type(data.get("comments")) is not list
        or bool(data.get("comments"))
        or type(data.get("related")) is not list
        or bool(data.get("related"))
        or type(data.get("warnings")) is not list
        or bool(data.get("warnings"))
    ):
        raise _BackendContractError("backend optional path invalid")
    item, truncated = _project_video_summary(data.get("video"), maximum_text)
    if item.fields["url"] != requested_url:
        raise _BackendContractError("backend item invalid")
    return item, truncated


def _project_listing(
    value: object,
    limit: int,
    maximum_text: int,
    *,
    page: int | None = None,
    day: int | None = None,
) -> tuple[tuple[ExecutionItemV1, ...], bool]:
    discriminator = "page" if page is not None else "day"
    expected = page if page is not None else day
    data = _closed_mapping(value, {"items", discriminator, "count"})
    values = data.get("items")
    discriminator_value = data.get(discriminator)
    count = data.get("count")
    if (
        type(discriminator_value) is not int
        or discriminator_value != expected
        or type(count) is not int
        or count != limit
        or not isinstance(values, list)
        or len(values) > limit
    ):
        raise _BackendContractError("backend listing invalid")
    projected = tuple(_project_video_summary(item, maximum_text) for item in values)
    return tuple(item for item, _ in projected), any(truncated for _, truncated in projected)


def _project_video_summary(
    value: object,
    maximum_text: int,
) -> tuple[ExecutionItemV1, bool]:
    item = _closed_mapping(
        value,
        {
            "id",
            "bvid",
            "aid",
            "title",
            "description",
            "duration_seconds",
            "duration",
            "url",
            "owner",
            "stats",
        },
    )
    bvid = _bvid(item.get("bvid"))
    if item.get("id") != bvid or item.get("url") != _video_url(bvid):
        raise _BackendContractError("backend item invalid")
    _non_negative_integer(item.get("aid"))
    title = _required_text(item.get("title"), MAX_TITLE_CHARACTERS)
    description = _optional_text(item.get("description"), _MAX_STRING_BYTES)
    duration = _non_negative_integer(item.get("duration_seconds"))
    if _duration_seconds(item.get("duration")) != duration:
        raise _BackendContractError("backend item invalid")
    owner = _closed_mapping(item.get("owner"), {"id", "name"})
    _optional_text(owner.get("id"), 64)
    author = _optional_text(owner.get("name"), _MAX_BILIBILI_AUTHOR_CHARACTERS)
    stats = _closed_mapping(
        item.get("stats"),
        {"view", "danmaku", "like", "coin", "favorite", "share"},
    )
    view_count = _non_negative_integer(stats.get("view"))
    for name in ("danmaku", "like", "coin", "favorite", "share"):
        _non_negative_integer(stats.get(name))
    text = description or title
    truncated = len(text) > maximum_text
    return (
        _video_item(
            text=text[:maximum_text],
            bvid=bvid,
            title=title,
            author=author,
            duration_seconds=duration,
            view_count=view_count,
        ),
        truncated,
    )


def _video_item(
    *,
    text: str,
    bvid: str,
    title: str,
    author: str | None,
    duration_seconds: int,
    view_count: int,
) -> ExecutionItemV1:
    return ExecutionItemV1(
        "bilibili.video.v1",
        {
            "text": text,
            "native_id": bvid,
            "title": title,
            "url": _video_url(bvid),
            "author": author,
            "duration_seconds": duration_seconds,
            "view_count": view_count,
        },
    )


def _effective_limit(request: ExecutionRequestV1, context: ExecutionContextV1) -> int:
    if request.operation == "read.video":
        return 1
    value = request.arguments.get("limit")
    if type(value) is not int:
        raise _BackendContractError("request invalid")
    return min(value, context.limits.maximum_items)


def _request_was_narrowed(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
) -> bool:
    if request.operation == "read.video":
        return False
    value = request.arguments.get("limit")
    return type(value) is int and value > context.limits.maximum_items


def _validated_backend_envelope(value: object) -> Mapping[str, object]:
    if not _json_within_bounds(value) or not isinstance(value, dict):
        raise _BackendContractError("backend envelope invalid")
    if value.get("schema_version") != "1" or type(value.get("ok")) is not bool:
        raise _BackendContractError("backend envelope invalid")
    if value["ok"] is True:
        if set(value) != {"ok", "schema_version", "data"}:
            raise _BackendContractError("backend envelope invalid")
    else:
        if set(value) != {"ok", "schema_version", "error"}:
            raise _BackendContractError("backend envelope invalid")
        error = value.get("error")
        if (
            not isinstance(error, dict)
            or not {"code", "message"} <= set(error) <= {"code", "message", "details"}
            or type(error.get("code")) is not str
            or type(error.get("message")) is not str
        ):
            raise _BackendContractError("backend envelope invalid")
    return cast(Mapping[str, object], value)


def _load_json(value: bytes) -> object:
    if not 0 < len(value) <= _MAX_OUTPUT_BYTES:
        raise _BackendContractError("backend output invalid")
    try:
        return json.loads(
            value.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_int=_parse_integer,
        )
    except (
        _BackendContractError,
        UnicodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ):
        raise _BackendContractError("backend output invalid") from None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _BackendContractError("backend output invalid")
        result[key] = value
    return result


def _reject_constant(_: str) -> object:
    raise _BackendContractError("backend output invalid")


def _parse_integer(value: str) -> int:
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > len(str(_MAX_RESULT_INTEGER)):
        raise _BackendContractError("backend output invalid")
    parsed = int(value)
    if not -_MAX_RESULT_INTEGER <= parsed <= _MAX_RESULT_INTEGER:
        raise _BackendContractError("backend output invalid")
    return parsed


def _json_within_bounds(value: object, depth: int = 0) -> bool:
    if depth > _MAX_JSON_DEPTH:
        return False
    if value is None or type(value) in {bool, int}:
        return type(value) is not int or -_MAX_RESULT_INTEGER <= value <= _MAX_RESULT_INTEGER
    if type(value) is str:
        return _valid_json_string(value, _MAX_STRING_BYTES)
    if isinstance(value, list):
        return len(value) <= _MAX_JSON_ITEMS and all(
            _json_within_bounds(item, depth + 1) for item in value
        )
    if isinstance(value, dict):
        return len(value) <= _MAX_JSON_ITEMS and all(
            type(key) is str
            and _valid_json_string(key, 64)
            and _json_within_bounds(item, depth + 1)
            for key, item in value.items()
        )
    return False


def _valid_json_string(value: str, maximum_bytes: int) -> bool:
    if _contains_invalid_scalar(value):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= maximum_bytes
    except UnicodeError:
        return False


def _closed_mapping(value: object, fields: set[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise _BackendContractError("backend data invalid")
    if not all(type(key) is str for key in value):
        raise _BackendContractError("backend data invalid")
    return cast(Mapping[str, object], value)


def _required_text(value: object, maximum: int) -> str:
    text = _optional_text(value, maximum)
    if text is None:
        raise _BackendContractError("backend text invalid")
    return text


def _optional_text(value: object, maximum: int) -> str | None:
    if type(value) is not str or len(value) > maximum:
        raise _BackendContractError("backend text invalid")
    normalized = " ".join(value.split())
    return normalized or None


def _bvid(value: object) -> str:
    if type(value) is not str or _BVID.fullmatch(value) is None:
        raise _BackendContractError("backend bvid invalid")
    return value


def _non_negative_integer(value: object) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_RESULT_INTEGER:
        raise _BackendContractError("backend integer invalid")
    return value


def _duration_seconds(value: object) -> int:
    if type(value) is not str or not 1 <= len(value) <= 16:
        raise _BackendContractError("backend duration invalid")
    parts = value.split(":")
    if len(parts) not in {2, 3} or any(not part.isascii() or not part.isdigit() for part in parts):
        raise _BackendContractError("backend duration invalid")
    numbers = [int(part) for part in parts]
    if any(number < 0 for number in numbers) or any(number >= 60 for number in numbers[-2:]):
        raise _BackendContractError("backend duration invalid")
    seconds = numbers[-1] + numbers[-2] * 60
    if len(numbers) == 3:
        seconds += numbers[0] * 3600
    return _non_negative_integer(seconds)


def _video_url(bvid: str) -> str:
    return f"https://www.bilibili.com/video/{bvid}"


def _failure(
    request: ExecutionRequestV1,
    error_code: ExecutionErrorCodeV1,
) -> ExecutionFailureV1:
    return ExecutionFailureV1(
        protocol_version=PROTOCOL_VERSION,
        source="bilibili",
        operation=request.operation,
        backend_id=_BACKEND_ID,
        backend_version=_BACKEND_VERSION,
        error_code=error_code,
    )
