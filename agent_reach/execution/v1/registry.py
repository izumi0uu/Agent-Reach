"""Static capability discovery and closed execution dispatch for v1."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from .contracts import (
    _MAX_BILIBILI_AUTHOR_CHARACTERS,
    _MAX_BILIBILI_OUTPUT_BYTES,
    _MAX_BILIBILI_QUERY_CHARACTERS,
    FETCHED_DOCUMENT_CAPABILITY,
    MAX_AUTHOR_CHARACTERS,
    MAX_CONTENT_LOCATION_CHARACTERS,
    MAX_CONTENT_TYPE_CHARACTERS,
    MAX_DOCUMENT_BYTES,
    MAX_METADATA_BYTES,
    MAX_NATIVE_ID_CHARACTERS,
    MAX_OUTPUT_BYTES,
    MAX_PUBLISHED_CHARACTERS,
    MAX_TEXT_CHARACTERS,
    MAX_TITLE_CHARACTERS,
    MAX_URL_CHARACTERS,
    NETWORK_ACCESS_CAPABILITY,
    PROTOCOL_VERSION,
    ExecutionContextV1,
    ExecutionErrorCodeV1,
    ExecutionFailureV1,
    ExecutionRequestV1,
    ExecutionResultV1,
    FetchedDocumentV1,
    NetworkAccessV1,
    OperationCapabilityV1,
    _valid_bilibili_video_url,
)


def _capability(
    *,
    source: str,
    operation: str,
    argument_schema_id: str,
    result_schema_ids: tuple[str, ...],
    backend_id: str,
    backend_version: str,
    required_host_capability: str,
    maximum_items: int,
    maximum_output_bytes: int = MAX_OUTPUT_BYTES,
    maximum_author_characters: int = MAX_AUTHOR_CHARACTERS,
) -> OperationCapabilityV1:
    return OperationCapabilityV1(
        protocol_version=PROTOCOL_VERSION,
        source=source,
        operation=operation,
        argument_schema_id=argument_schema_id,
        result_schema_ids=result_schema_ids,
        backend_id=backend_id,
        backend_version=backend_version,
        required_host_capabilities=(required_host_capability,),
        maximum_items=maximum_items,
        maximum_document_bytes=MAX_DOCUMENT_BYTES,
        maximum_metadata_bytes=MAX_METADATA_BYTES,
        maximum_output_bytes=maximum_output_bytes,
        maximum_content_type_characters=MAX_CONTENT_TYPE_CHARACTERS,
        maximum_content_location_characters=MAX_CONTENT_LOCATION_CHARACTERS,
        maximum_text_characters=MAX_TEXT_CHARACTERS,
        maximum_title_characters=MAX_TITLE_CHARACTERS,
        maximum_url_characters=MAX_URL_CHARACTERS,
        maximum_native_id_characters=MAX_NATIVE_ID_CHARACTERS,
        maximum_author_characters=maximum_author_characters,
        maximum_published_characters=MAX_PUBLISHED_CHARACTERS,
    )


_CAPABILITIES: Final = (
    _capability(
        source="rss",
        operation="read.feed",
        argument_schema_id="rss.read.feed.arguments.v1",
        result_schema_ids=("rss.feed.v1",),
        backend_id="feedparser",
        backend_version="6.0.12",
        required_host_capability=FETCHED_DOCUMENT_CAPABILITY,
        maximum_items=1,
    ),
    _capability(
        source="rss",
        operation="browse.entries",
        argument_schema_id="rss.browse.entries.arguments.v1",
        result_schema_ids=("rss.entry.v1",),
        backend_id="feedparser",
        backend_version="6.0.12",
        required_host_capability=FETCHED_DOCUMENT_CAPABILITY,
        maximum_items=21,
    ),
    _capability(
        source="bilibili",
        operation="search.videos",
        argument_schema_id="bilibili.search.videos.arguments.v1",
        result_schema_ids=("bilibili.video.v1",),
        backend_id="bili-cli",
        backend_version="0.6.2",
        required_host_capability=NETWORK_ACCESS_CAPABILITY,
        maximum_items=50,
        maximum_output_bytes=_MAX_BILIBILI_OUTPUT_BYTES,
        maximum_author_characters=_MAX_BILIBILI_AUTHOR_CHARACTERS,
    ),
    _capability(
        source="bilibili",
        operation="read.video",
        argument_schema_id="bilibili.read.video.arguments.v1",
        result_schema_ids=("bilibili.video.v1",),
        backend_id="bili-cli",
        backend_version="0.6.2",
        required_host_capability=NETWORK_ACCESS_CAPABILITY,
        maximum_items=1,
        maximum_output_bytes=_MAX_BILIBILI_OUTPUT_BYTES,
        maximum_author_characters=_MAX_BILIBILI_AUTHOR_CHARACTERS,
    ),
    _capability(
        source="bilibili",
        operation="browse.hot",
        argument_schema_id="bilibili.browse.hot.arguments.v1",
        result_schema_ids=("bilibili.video.v1",),
        backend_id="bili-cli",
        backend_version="0.6.2",
        required_host_capability=NETWORK_ACCESS_CAPABILITY,
        maximum_items=50,
        maximum_output_bytes=_MAX_BILIBILI_OUTPUT_BYTES,
        maximum_author_characters=_MAX_BILIBILI_AUTHOR_CHARACTERS,
    ),
    _capability(
        source="bilibili",
        operation="browse.rank",
        argument_schema_id="bilibili.browse.rank.arguments.v1",
        result_schema_ids=("bilibili.video.v1",),
        backend_id="bili-cli",
        backend_version="0.6.2",
        required_host_capability=NETWORK_ACCESS_CAPABILITY,
        maximum_items=50,
        maximum_output_bytes=_MAX_BILIBILI_OUTPUT_BYTES,
        maximum_author_characters=_MAX_BILIBILI_AUTHOR_CHARACTERS,
    ),
)
_CAPABILITY_BY_OPERATION: Final = MappingProxyType(
    {(capability.source, capability.operation): capability for capability in _CAPABILITIES}
)
_SOURCES: Final = frozenset(capability.source for capability in _CAPABILITIES)


def list_capabilities() -> tuple[OperationCapabilityV1, ...]:
    """Return the static v1 registry without importing or probing a backend."""

    return _CAPABILITIES


def execute(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
) -> ExecutionResultV1:
    """Validate a closed request and invoke its fixed registered executor."""

    if type(request) is not ExecutionRequestV1:
        return _failure(None, "invalid_request")
    if request.protocol_version != PROTOCOL_VERSION:
        return _failure(request, "unsupported_protocol_version")
    if request.source not in _SOURCES:
        return _failure(request, "unsupported_source")

    capability = _CAPABILITY_BY_OPERATION.get((request.source, request.operation))
    if capability is None:
        return _failure(request, "unsupported_operation")
    if not _valid_arguments(request, capability):
        return _failure(request, "invalid_request")
    if type(context) is not ExecutionContextV1:
        return _failure(request, "invalid_request")

    host_capabilities = context.host_capabilities
    if not host_capabilities:
        return _failure(request, "host_capability_missing")
    if not _valid_host_capabilities(capability, host_capabilities):
        return _failure(request, "invalid_request")

    context.checkpoint()

    # Keep discovery and every rejected request independent of runtime backends.
    if request.source == "rss":
        from .rss import execute_rss

        document = host_capabilities[0]
        if type(document) is not FetchedDocumentV1:
            return _failure(request, "invalid_request")
        return execute_rss(request, context, document)
    if request.source == "bilibili":
        from .bilibili import execute_bilibili

        return execute_bilibili(request, context)
    return _failure(request, "unsupported_source")


def _failure(
    request: ExecutionRequestV1 | None,
    error_code: ExecutionErrorCodeV1,
) -> ExecutionFailureV1:
    return ExecutionFailureV1(
        protocol_version=PROTOCOL_VERSION,
        source=None if request is None else request.source,
        operation=None if request is None else request.operation,
        backend_id=None,
        backend_version=None,
        error_code=error_code,
    )


def _valid_arguments(
    request: ExecutionRequestV1,
    capability: OperationCapabilityV1,
) -> bool:
    arguments = request.arguments
    key = (request.source, request.operation)
    if key == ("rss", "read.feed"):
        return not arguments
    if key == ("rss", "browse.entries") and set(arguments) == {"max_entries"}:
        maximum = arguments["max_entries"]
        return type(maximum) is int and 1 <= maximum <= capability.maximum_items
    if key == ("bilibili", "search.videos") and set(arguments) == {"query", "limit"}:
        query = arguments["query"]
        limit = arguments["limit"]
        return bool(
            type(query) is str
            and query == query.strip()
            and 1 <= len(query) <= _MAX_BILIBILI_QUERY_CHARACTERS
            and type(limit) is int
            and 1 <= limit <= capability.maximum_items
        )
    if key == ("bilibili", "read.video") and set(arguments) == {"url"}:
        return _valid_bilibili_video_url(arguments["url"])
    if key in {("bilibili", "browse.hot"), ("bilibili", "browse.rank")} and set(arguments) == {
        "limit"
    }:
        limit = arguments["limit"]
        return type(limit) is int and 1 <= limit <= capability.maximum_items
    return False


def _valid_host_capabilities(
    capability: OperationCapabilityV1,
    host_capabilities: tuple[FetchedDocumentV1 | NetworkAccessV1, ...],
) -> bool:
    expected = capability.required_host_capabilities
    if expected == (FETCHED_DOCUMENT_CAPABILITY,):
        return len(host_capabilities) == 1 and type(host_capabilities[0]) is FetchedDocumentV1
    if expected == (NETWORK_ACCESS_CAPABILITY,):
        return len(host_capabilities) == 1 and type(host_capabilities[0]) is NetworkAccessV1
    return False
