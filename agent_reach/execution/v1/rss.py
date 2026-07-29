"""Fork-owned RSS and Atom execution over a host-fetched byte document."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from io import BytesIO
from typing import Final, cast
from urllib.parse import urlsplit, urlunsplit

from .contracts import (
    MAX_AUTHOR_CHARACTERS,
    MAX_NATIVE_ID_CHARACTERS,
    MAX_PUBLISHED_CHARACTERS,
    MAX_TITLE_CHARACTERS,
    MAX_URL_CHARACTERS,
    PROTOCOL_VERSION,
    ExecutionContextV1,
    ExecutionErrorCodeV1,
    ExecutionFailureV1,
    ExecutionItemV1,
    ExecutionRequestV1,
    ExecutionResultV1,
    ExecutionSuccessV1,
    FetchedDocumentV1,
    _contains_invalid_scalar,
)

_BACKEND_ID: Final = "feedparser"
_BACKEND_VERSION: Final = "6.0.12"


def execute_rss(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
    document: FetchedDocumentV1,
) -> ExecutionResultV1:
    """Execute one already-validated RSS registry request."""

    backend = _load_backend()
    if isinstance(backend, str):
        return _failure(request, backend)

    context.checkpoint()
    try:
        with BytesIO(document.body) as body:
            parsed_value = backend(
                body,
                response_headers={
                    "content-location": document.content_location,
                    "content-type": document.content_type,
                },
                resolve_relative_uris=True,
                sanitize_html=True,
            )
    except Exception:
        return _failure(request, "permanent")
    context.checkpoint()

    try:
        parsed = _as_mapping(parsed_value)
        if parsed is None:
            return _failure(request, "backend_contract_violation")
        entries = parsed.get("entries")
        if not isinstance(entries, list):
            return _failure(request, "backend_contract_violation")
        bozo_value = parsed.get("bozo", False)
        if type(bozo_value) not in {bool, int} or bozo_value not in {False, True, 0, 1}:
            return _failure(request, "backend_contract_violation")
        bozo = bool(bozo_value)
    except Exception:
        return _failure(request, "backend_contract_violation")

    if request.operation == "read.feed":
        return _read_feed(parsed, bozo, context)
    return _browse_entries(parsed, entries, bozo, request, context)


def _load_backend() -> Callable[..., object] | ExecutionErrorCodeV1:
    try:
        backend = importlib.import_module("feedparser")
    except ImportError:
        return "backend_unavailable"
    except Exception:
        return "backend_unavailable"
    if getattr(backend, "__version__", None) != _BACKEND_VERSION:
        return "backend_incompatible"
    parser = getattr(backend, "parse", None)
    if not callable(parser):
        return "backend_incompatible"
    return cast(Callable[..., object], parser)


def _read_feed(
    parsed: Mapping[str, object],
    bozo: bool,
    context: ExecutionContextV1,
) -> ExecutionResultV1:
    context.checkpoint()
    try:
        feed = _as_mapping(parsed.get("feed"))
        if feed is None:
            return _failure_for("read.feed", "permanent")
        fields = _project_feed(feed, context.limits.maximum_text_characters)
        if fields["text"] is None and fields["title"] is None:
            return _failure_for("read.feed", "permanent")
        item = ExecutionItemV1("rss.feed.v1", fields)
        return ExecutionSuccessV1(
            PROTOCOL_VERSION,
            "rss",
            "read.feed",
            _BACKEND_ID,
            _BACKEND_VERSION,
            (item,),
            partial_error_code="permanent" if bozo else None,
        )
    except Exception:
        return _failure_for("read.feed", "backend_contract_violation")


def _browse_entries(
    parsed: Mapping[str, object],
    entries: list[object],
    bozo: bool,
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
) -> ExecutionResultV1:
    del parsed
    requested = cast(int, request.arguments["max_entries"])
    maximum = min(requested, context.limits.maximum_items)
    selected: list[ExecutionItemV1] = []
    dropped = False
    for value in entries[:maximum]:
        context.checkpoint()
        try:
            entry = _as_mapping(value)
            if entry is None:
                return _failure(request, "backend_contract_violation")
            fields = _project_entry(entry, context.limits.maximum_text_characters)
            if fields["text"] is None and fields["title"] is None:
                dropped = True
                continue
            selected.append(ExecutionItemV1("rss.entry.v1", fields))
        except Exception:
            return _failure(request, "backend_contract_violation")

    if not selected and (bozo or entries):
        return _failure(request, "permanent")
    context.checkpoint()
    try:
        return ExecutionSuccessV1(
            PROTOCOL_VERSION,
            "rss",
            "browse.entries",
            _BACKEND_ID,
            _BACKEND_VERSION,
            tuple(selected),
            truncated=len(entries) > maximum,
            partial_error_code="permanent" if bozo or dropped else None,
        )
    except ValueError:
        return _failure(request, "backend_contract_violation")


def _failure_for(operation: str, error_code: ExecutionErrorCodeV1) -> ExecutionFailureV1:
    return ExecutionFailureV1(
        protocol_version=PROTOCOL_VERSION,
        source="rss",
        operation=operation,
        backend_id=_BACKEND_ID,
        backend_version=_BACKEND_VERSION,
        error_code=error_code,
    )


def _failure(request: ExecutionRequestV1, error_code: ExecutionErrorCodeV1) -> ExecutionFailureV1:
    return _failure_for(request.operation, error_code)


def _project_feed(
    feed: Mapping[str, object], maximum_text_characters: int
) -> dict[str, str | None]:
    title = _source_string(feed.get("title"), MAX_TITLE_CHARACTERS)
    return {
        "text": _first_string(
            feed,
            ("subtitle", "description", "title"),
            maximum_text_characters,
        ),
        "title": title,
        "url": _source_url(feed.get("link"), MAX_URL_CHARACTERS),
    }


def _project_entry(
    entry: Mapping[str, object], maximum_text_characters: int
) -> dict[str, str | None]:
    title = _source_string(entry.get("title"), MAX_TITLE_CHARACTERS)
    text = _content_value(entry, maximum_text_characters)
    if text is None:
        text = _first_string(
            entry,
            ("summary", "description", "title"),
            maximum_text_characters,
        )
    author = _source_string(entry.get("author"), MAX_AUTHOR_CHARACTERS)
    if author is None:
        detail = _as_mapping(entry.get("author_detail"))
        if detail is not None:
            author = _source_string(detail.get("name"), MAX_AUTHOR_CHARACTERS)
    return {
        "text": text,
        "native_id": _source_identifier(
            _first_string(entry, ("id", "guid"), MAX_NATIVE_ID_CHARACTERS)
        ),
        "title": title,
        "url": _source_url(entry.get("link"), MAX_URL_CHARACTERS),
        "author": author,
        "published_at": _first_string(
            entry,
            ("published", "updated"),
            MAX_PUBLISHED_CHARACTERS,
        ),
    }


def _content_value(entry: Mapping[str, object], maximum_text_characters: int) -> str | None:
    content = entry.get("content")
    if not isinstance(content, list):
        return None
    for value in content:
        mapping = _as_mapping(value)
        if mapping is None:
            continue
        selected = _source_string(mapping.get("value"), maximum_text_characters)
        if selected is not None:
            return selected
    return None


def _first_string(value: Mapping[str, object], names: tuple[str, ...], maximum: int) -> str | None:
    for name in names:
        selected = _source_string(value.get(name), maximum)
        if selected is not None:
            return selected
    return None


def _source_string(value: object, maximum: int) -> str | None:
    if type(value) is not str or not value.strip() or _contains_invalid_scalar(value):
        return None
    return value[:maximum]


def _source_identifier(value: str | None) -> str | None:
    if value is None or "://" not in value:
        return value
    return _source_url(value, MAX_NATIVE_ID_CHARACTERS)


def _source_url(value: object, maximum: int) -> str | None:
    selected = _source_string(value, maximum)
    if selected is None or "\\" in selected:
        return None
    try:
        parsed = urlsplit(selected)
        if parsed.scheme and parsed.scheme.lower() not in {"http", "https"}:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        if parsed.netloc and parsed.hostname is None:
            return None
        return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path, "", "")) or None
    except (UnicodeError, ValueError):
        return None


def _as_mapping(value: object) -> Mapping[str, object] | None:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else None
