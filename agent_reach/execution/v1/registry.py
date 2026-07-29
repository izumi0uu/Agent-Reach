"""Static capability discovery and closed execution dispatch for v1."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from .contracts import (
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
    PROTOCOL_VERSION,
    ExecutionContextV1,
    ExecutionErrorCodeV1,
    ExecutionFailureV1,
    ExecutionRequestV1,
    ExecutionResultV1,
    FetchedDocumentV1,
    OperationCapabilityV1,
)


def _rss_capability(
    *,
    source: str,
    operation: str,
    argument_schema_id: str,
    result_schema_ids: tuple[str, ...],
    maximum_items: int,
) -> OperationCapabilityV1:
    return OperationCapabilityV1(
        protocol_version=PROTOCOL_VERSION,
        source=source,
        operation=operation,
        argument_schema_id=argument_schema_id,
        result_schema_ids=result_schema_ids,
        backend_id="feedparser",
        backend_version="6.0.12",
        required_host_capabilities=(FETCHED_DOCUMENT_CAPABILITY,),
        maximum_items=maximum_items,
        maximum_document_bytes=MAX_DOCUMENT_BYTES,
        maximum_metadata_bytes=MAX_METADATA_BYTES,
        maximum_output_bytes=MAX_OUTPUT_BYTES,
        maximum_content_type_characters=MAX_CONTENT_TYPE_CHARACTERS,
        maximum_content_location_characters=MAX_CONTENT_LOCATION_CHARACTERS,
        maximum_text_characters=MAX_TEXT_CHARACTERS,
        maximum_title_characters=MAX_TITLE_CHARACTERS,
        maximum_url_characters=MAX_URL_CHARACTERS,
        maximum_native_id_characters=MAX_NATIVE_ID_CHARACTERS,
        maximum_author_characters=MAX_AUTHOR_CHARACTERS,
        maximum_published_characters=MAX_PUBLISHED_CHARACTERS,
    )


_CAPABILITIES: Final = (
    _rss_capability(
        source="rss",
        operation="read.feed",
        argument_schema_id="rss.read.feed.arguments.v1",
        result_schema_ids=("rss.feed.v1",),
        maximum_items=1,
    ),
    _rss_capability(
        source="rss",
        operation="browse.entries",
        argument_schema_id="rss.browse.entries.arguments.v1",
        result_schema_ids=("rss.entry.v1",),
        maximum_items=21,
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
    if len(host_capabilities) != 1 or type(host_capabilities[0]) is not FetchedDocumentV1:
        return _failure(request, "invalid_request")

    context.checkpoint()

    # Keep discovery and all rejected request paths independent of feedparser.
    from .rss import execute_rss

    return execute_rss(
        request,
        context,
        host_capabilities[0],
    )


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
    if request.operation == "read.feed":
        return not arguments
    if request.operation == "browse.entries" and set(arguments) == {"max_entries"}:
        maximum = arguments["max_entries"]
        return type(maximum) is int and 1 <= maximum <= capability.maximum_items
    return False
