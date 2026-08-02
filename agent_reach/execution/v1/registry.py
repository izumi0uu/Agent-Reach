"""Static capability discovery and closed execution dispatch for v1."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from .contracts import (
    _MAX_BILIBILI_AUTHOR_CHARACTERS,
    _MAX_BILIBILI_OUTPUT_BYTES,
    _MAX_BILIBILI_QUERY_CHARACTERS,
    _MAX_EXA_OUTPUT_BYTES,
    _MAX_OPENCLI_OUTPUT_BYTES,
    _MAX_YOUTUBE_AUTHOR_CHARACTERS,
    _MAX_YOUTUBE_OUTPUT_BYTES,
    _SOCIAL_USERNAME,
    _SUBREDDIT_IDENTIFIER,
    _YOUTUBE_SUBTITLE_MARKER,
    FETCHED_DOCUMENT_CAPABILITY,
    LINKEDIN_MCP_CAPABILITY,
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
    MCPORTER_ARTIFACTS_CAPABILITY,
    NETWORK_ACCESS_CAPABILITY,
    OPENCLI_SESSION_CAPABILITY,
    PRIVATE_WORKSPACE_CAPABILITY,
    PROTOCOL_VERSION,
    XUEQIU_SESSION_CAPABILITY,
    ExecutionContextV1,
    ExecutionErrorCodeV1,
    ExecutionFailureV1,
    ExecutionRequestV1,
    ExecutionResultV1,
    FetchedDocumentV1,
    HostCapabilityV1,
    LinkedInMcpV1,
    McporterArtifactsV1,
    NetworkAccessV1,
    OpenCliSessionV1,
    OperationCapabilityV1,
    PrivateWorkspaceV1,
    XueqiuSessionV1,
    _reddit_post_id_from_url,
    _valid_bilibili_video_url,
    _valid_youtube_video_url,
)


def _capability(
    *,
    source: str,
    operation: str,
    argument_schema_id: str,
    result_schema_ids: tuple[str, ...],
    backend_id: str,
    backend_version: str,
    required_host_capabilities: tuple[str, ...],
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
        required_host_capabilities=required_host_capabilities,
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


def _opencli_capability(
    *,
    source: str,
    operation: str,
    argument_schema_id: str,
    result_schema_id: str,
    maximum_items: int,
) -> OperationCapabilityV1:
    return _capability(
        source=source,
        operation=operation,
        argument_schema_id=argument_schema_id,
        result_schema_ids=(result_schema_id,),
        backend_id="opencli",
        backend_version="1.8.6-hermes.1",
        required_host_capabilities=(OPENCLI_SESSION_CAPABILITY,),
        maximum_items=maximum_items,
        maximum_output_bytes=_MAX_OPENCLI_OUTPUT_BYTES,
    )


_CAPABILITIES: Final = (
    _capability(
        source="rss",
        operation="read.feed",
        argument_schema_id="rss.read.feed.arguments.v1",
        result_schema_ids=("rss.feed.v1",),
        backend_id="feedparser",
        backend_version="6.0.12",
        required_host_capabilities=(FETCHED_DOCUMENT_CAPABILITY,),
        maximum_items=1,
    ),
    _capability(
        source="rss",
        operation="browse.entries",
        argument_schema_id="rss.browse.entries.arguments.v1",
        result_schema_ids=("rss.entry.v1",),
        backend_id="feedparser",
        backend_version="6.0.12",
        required_host_capabilities=(FETCHED_DOCUMENT_CAPABILITY,),
        maximum_items=21,
    ),
    _capability(
        source="bilibili",
        operation="search.videos",
        argument_schema_id="bilibili.search.videos.arguments.v1",
        result_schema_ids=("bilibili.video.v1",),
        backend_id="bili-cli",
        backend_version="0.6.2",
        required_host_capabilities=(NETWORK_ACCESS_CAPABILITY,),
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
        required_host_capabilities=(NETWORK_ACCESS_CAPABILITY,),
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
        required_host_capabilities=(NETWORK_ACCESS_CAPABILITY,),
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
        required_host_capabilities=(NETWORK_ACCESS_CAPABILITY,),
        maximum_items=50,
        maximum_output_bytes=_MAX_BILIBILI_OUTPUT_BYTES,
        maximum_author_characters=_MAX_BILIBILI_AUTHOR_CHARACTERS,
    ),
    _capability(
        source="youtube",
        operation="read.video",
        argument_schema_id="youtube.read.video.arguments.v1",
        result_schema_ids=("youtube.video.v1",),
        backend_id="yt-dlp",
        backend_version="2026.7.4",
        required_host_capabilities=(NETWORK_ACCESS_CAPABILITY,),
        maximum_items=1,
        maximum_output_bytes=_MAX_YOUTUBE_OUTPUT_BYTES,
        maximum_author_characters=_MAX_YOUTUBE_AUTHOR_CHARACTERS,
    ),
    _capability(
        source="youtube",
        operation="search.videos",
        argument_schema_id="youtube.search.videos.arguments.v1",
        result_schema_ids=("youtube.video.v1",),
        backend_id="yt-dlp",
        backend_version="2026.7.4",
        required_host_capabilities=(NETWORK_ACCESS_CAPABILITY,),
        maximum_items=50,
        maximum_output_bytes=_MAX_YOUTUBE_OUTPUT_BYTES,
        maximum_author_characters=_MAX_YOUTUBE_AUTHOR_CHARACTERS,
    ),
    _capability(
        source="youtube",
        operation="read.subtitles",
        argument_schema_id="youtube.read.subtitles.arguments.v1",
        result_schema_ids=("youtube.subtitle.v1",),
        backend_id="yt-dlp",
        backend_version="2026.7.4",
        required_host_capabilities=(
            NETWORK_ACCESS_CAPABILITY,
            PRIVATE_WORKSPACE_CAPABILITY,
        ),
        maximum_items=1,
        maximum_output_bytes=_MAX_YOUTUBE_OUTPUT_BYTES,
        maximum_author_characters=_MAX_YOUTUBE_AUTHOR_CHARACTERS,
    ),
    _capability(
        source="v2ex",
        operation="browse.hot",
        argument_schema_id="v2ex.browse.hot.arguments.v1",
        result_schema_ids=("v2ex.topic.v1",),
        backend_id="v2ex-public-api",
        backend_version="legacy-json-2026-07-31",
        required_host_capabilities=(NETWORK_ACCESS_CAPABILITY,),
        maximum_items=50,
    ),
    _capability(
        source="v2ex",
        operation="browse.node_topics",
        argument_schema_id="v2ex.browse.node_topics.arguments.v1",
        result_schema_ids=("v2ex.topic.v1",),
        backend_id="v2ex-public-api",
        backend_version="legacy-json-2026-07-31",
        required_host_capabilities=(NETWORK_ACCESS_CAPABILITY,),
        maximum_items=50,
    ),
    _capability(
        source="v2ex",
        operation="read.topic",
        argument_schema_id="v2ex.read.topic.arguments.v1",
        result_schema_ids=("v2ex.topic.v1", "v2ex.reply.v1"),
        backend_id="v2ex-public-api",
        backend_version="legacy-json-2026-07-31",
        required_host_capabilities=(NETWORK_ACCESS_CAPABILITY,),
        maximum_items=21,
    ),
    _capability(
        source="v2ex",
        operation="read.user",
        argument_schema_id="v2ex.read.user.arguments.v1",
        result_schema_ids=("v2ex.profile.v1",),
        backend_id="v2ex-public-api",
        backend_version="legacy-json-2026-07-31",
        required_host_capabilities=(NETWORK_ACCESS_CAPABILITY,),
        maximum_items=1,
    ),
    _capability(
        source="exa",
        operation="search.web",
        argument_schema_id="exa.search.web.arguments.v1",
        result_schema_ids=("exa.search.result.v1",),
        backend_id="exa-mcporter",
        backend_version="0.12.3+exa-web.v1",
        required_host_capabilities=(
            NETWORK_ACCESS_CAPABILITY,
            MCPORTER_ARTIFACTS_CAPABILITY,
        ),
        maximum_items=20,
        maximum_output_bytes=_MAX_EXA_OUTPUT_BYTES,
    ),
    _opencli_capability(
        source="reddit",
        operation="search.posts",
        argument_schema_id="reddit.search.posts.arguments.v1",
        result_schema_id="reddit.post.v1",
        maximum_items=50,
    ),
    _opencli_capability(
        source="reddit",
        operation="read.post",
        argument_schema_id="reddit.read.post.arguments.v1",
        result_schema_id="reddit.thread.item.v1",
        maximum_items=14,
    ),
    _opencli_capability(
        source="reddit",
        operation="browse.subreddit",
        argument_schema_id="reddit.browse.subreddit.arguments.v1",
        result_schema_id="reddit.post.v1",
        maximum_items=50,
    ),
    _opencli_capability(
        source="reddit",
        operation="browse.hot",
        argument_schema_id="reddit.browse.hot.arguments.v1",
        result_schema_id="reddit.post.v1",
        maximum_items=50,
    ),
    _opencli_capability(
        source="reddit",
        operation="browse.popular",
        argument_schema_id="reddit.browse.popular.arguments.v1",
        result_schema_id="reddit.post.v1",
        maximum_items=50,
    ),
    _opencli_capability(
        source="reddit",
        operation="browse.all",
        argument_schema_id="reddit.browse.all.arguments.v1",
        result_schema_id="reddit.post.v1",
        maximum_items=50,
    ),
    _opencli_capability(
        source="reddit",
        operation="read.subreddit",
        argument_schema_id="reddit.read.subreddit.arguments.v1",
        result_schema_id="reddit.subreddit.v1",
        maximum_items=1,
    ),
    _opencli_capability(
        source="facebook",
        operation="search",
        argument_schema_id="facebook.search.arguments.v1",
        result_schema_id="facebook.search.result.v1",
        maximum_items=50,
    ),
    _opencli_capability(
        source="facebook",
        operation="read.profile",
        argument_schema_id="facebook.read.profile.arguments.v1",
        result_schema_id="facebook.profile.v1",
        maximum_items=1,
    ),
    _opencli_capability(
        source="facebook",
        operation="browse.feed",
        argument_schema_id="facebook.browse.feed.arguments.v1",
        result_schema_id="facebook.post.v1",
        maximum_items=50,
    ),
    _opencli_capability(
        source="facebook",
        operation="browse.groups",
        argument_schema_id="facebook.browse.groups.arguments.v1",
        result_schema_id="facebook.group.v1",
        maximum_items=50,
    ),
    _opencli_capability(
        source="instagram",
        operation="search.users",
        argument_schema_id="instagram.search.users.arguments.v1",
        result_schema_id="instagram.user.v1",
        maximum_items=50,
    ),
    _opencli_capability(
        source="instagram",
        operation="read.profile",
        argument_schema_id="instagram.read.profile.arguments.v1",
        result_schema_id="instagram.profile.v1",
        maximum_items=1,
    ),
    _opencli_capability(
        source="instagram",
        operation="browse.user_posts",
        argument_schema_id="instagram.browse.user_posts.arguments.v1",
        result_schema_id="instagram.post.v1",
        maximum_items=50,
    ),
    _opencli_capability(
        source="instagram",
        operation="browse.explore",
        argument_schema_id="instagram.browse.explore.arguments.v1",
        result_schema_id="instagram.post.v1",
        maximum_items=50,
    ),
    _opencli_capability(
        source="twitter",
        operation="search.posts",
        argument_schema_id="twitter.search.posts.arguments.v1",
        result_schema_id="twitter.post.v1",
        maximum_items=50,
    ),
    _opencli_capability(
        source="xiaohongshu",
        operation="search.notes",
        argument_schema_id="xiaohongshu.search.notes.arguments.v1",
        result_schema_id="xiaohongshu.note.v1",
        maximum_items=50,
    ),
    _capability(
        source="linkedin",
        operation="search.people",
        argument_schema_id="linkedin.search.people.arguments.v1",
        result_schema_ids=("linkedin.people.search.document.v1",),
        backend_id="linkedin-scraper-mcp",
        backend_version="4.14.0",
        required_host_capabilities=(
            MCPORTER_ARTIFACTS_CAPABILITY,
            LINKEDIN_MCP_CAPABILITY,
        ),
        maximum_items=1,
        maximum_output_bytes=_MAX_EXA_OUTPUT_BYTES,
    ),
    _capability(
        source="linkedin",
        operation="search.jobs",
        argument_schema_id="linkedin.search.jobs.arguments.v1",
        result_schema_ids=("linkedin.jobs.search.document.v1",),
        backend_id="linkedin-scraper-mcp",
        backend_version="4.14.0",
        required_host_capabilities=(
            MCPORTER_ARTIFACTS_CAPABILITY,
            LINKEDIN_MCP_CAPABILITY,
        ),
        maximum_items=1,
        maximum_output_bytes=_MAX_EXA_OUTPUT_BYTES,
    ),
    _capability(
        source="xueqiu",
        operation="search.stocks",
        argument_schema_id="xueqiu.search.stocks.arguments.v1",
        result_schema_ids=("xueqiu.stock.v1",),
        backend_id="xueqiu-api",
        backend_version="1.5.0+search.v1",
        required_host_capabilities=(XUEQIU_SESSION_CAPABILITY,),
        maximum_items=50,
    ),
    _capability(
        source="exa",
        operation="search.code",
        argument_schema_id="exa.search.code.arguments.v1",
        result_schema_ids=("exa.code.result.v1",),
        backend_id="exa-mcporter",
        backend_version="0.12.3+exa-code.v1",
        required_host_capabilities=(
            NETWORK_ACCESS_CAPABILITY,
            MCPORTER_ARTIFACTS_CAPABILITY,
        ),
        maximum_items=20,
        maximum_output_bytes=_MAX_EXA_OUTPUT_BYTES,
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

    host_capabilities = context.host_capabilities if type(context) is ExecutionContextV1 else ()
    try:
        return _execute(request, context)
    finally:
        _close_consumable_capabilities(host_capabilities)


def _execute(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
) -> ExecutionResultV1:
    """Execute after installing the top-level consumable-capability cleanup."""

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
    if (request.source, request.operation) == (
        "youtube",
        "read.subtitles",
    ) and context.limits.maximum_text_characters < len(_YOUTUBE_SUBTITLE_MARKER):
        return _failure(request, "invalid_input")

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
    if request.source == "youtube":
        from .youtube import execute_youtube

        return execute_youtube(request, context)
    if request.source == "v2ex":
        from .v2ex import execute_v2ex

        return execute_v2ex(request, context)
    if request.source == "exa":
        from .exa import execute_exa

        return execute_exa(request, context)
    if request.source in {
        "reddit",
        "facebook",
        "instagram",
        "twitter",
        "xiaohongshu",
    }:
        from .opencli_social import execute_opencli_social

        return execute_opencli_social(request, context)
    if request.source == "linkedin":
        from .linkedin import execute_linkedin

        return execute_linkedin(request, context)
    if request.source == "xueqiu":
        from .xueqiu import execute_xueqiu

        return execute_xueqiu(request, context)
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
    if key == ("youtube", "read.video") and set(arguments) == {"url"}:
        return _valid_youtube_video_url(arguments["url"])
    if key == ("youtube", "search.videos") and set(arguments) == {"query", "limit"}:
        return _valid_query_and_limit(arguments, maximum_limit=capability.maximum_items)
    if key == ("youtube", "read.subtitles") and set(arguments) == {"url", "language"}:
        language = arguments["language"]
        return bool(
            _valid_youtube_video_url(arguments["url"])
            and (
                language is None
                or (
                    type(language) is str
                    and 1 <= len(language) <= 32
                    and language[0].isalnum()
                    and language.isascii()
                    and all(character.isalnum() or character in "_-" for character in language)
                )
            )
        )
    if key in {("bilibili", "browse.hot"), ("bilibili", "browse.rank")} and set(arguments) == {
        "limit"
    }:
        limit = arguments["limit"]
        return type(limit) is int and 1 <= limit <= capability.maximum_items
    if key == ("v2ex", "browse.hot") and set(arguments) == {"limit"}:
        limit = arguments["limit"]
        return type(limit) is int and 1 <= limit <= 50
    if key == ("v2ex", "browse.node_topics") and set(arguments) == {
        "node",
        "page",
        "limit",
    }:
        node = arguments["node"]
        page = arguments["page"]
        limit = arguments["limit"]
        return bool(
            _valid_v2ex_identifier(node)
            and type(page) is int
            and 1 <= page <= 100
            and type(limit) is int
            and 1 <= limit <= 50
        )
    if key == ("v2ex", "read.topic") and set(arguments) == {"topic_id"}:
        topic_id = arguments["topic_id"]
        return bool(
            type(topic_id) is str
            and topic_id.isascii()
            and topic_id.isdigit()
            and 1 <= len(topic_id) <= 32
            and int(topic_id) > 0
        )
    if key == ("v2ex", "read.user") and set(arguments) == {"username"}:
        return _valid_v2ex_identifier(arguments["username"])
    if key in {("exa", "search.web"), ("exa", "search.code")} and set(arguments) == {
        "query",
        "limit",
    }:
        return _valid_query_and_limit(arguments, maximum_limit=50)
    if key in {
        ("reddit", "search.posts"),
        ("facebook", "search"),
        ("instagram", "search.users"),
        ("twitter", "search.posts"),
        ("xiaohongshu", "search.notes"),
    } and set(arguments) == {"query", "limit"}:
        return _valid_query_and_limit(arguments, maximum_limit=capability.maximum_items)
    if key in {
        ("linkedin", "search.people"),
        ("linkedin", "search.jobs"),
    } and set(arguments) == {"query", "limit"}:
        return _valid_query_and_limit(arguments, maximum_limit=50)
    if key == ("xueqiu", "search.stocks") and set(arguments) == {"query", "limit"}:
        return _valid_query_and_limit(arguments, maximum_limit=capability.maximum_items)
    if key == ("reddit", "read.post") and set(arguments) == {"url"}:
        return _reddit_post_id_from_url(arguments["url"]) is not None
    if key == ("reddit", "browse.subreddit") and set(arguments) == {
        "subreddit",
        "limit",
    }:
        return bool(
            _valid_subreddit(arguments["subreddit"])
            and _valid_limit(arguments["limit"], capability.maximum_items)
        )
    if key == ("reddit", "read.subreddit") and set(arguments) == {"subreddit"}:
        return _valid_subreddit(arguments["subreddit"])
    if key in {
        ("reddit", "browse.hot"),
        ("reddit", "browse.popular"),
        ("reddit", "browse.all"),
        ("facebook", "browse.feed"),
        ("facebook", "browse.groups"),
        ("instagram", "browse.explore"),
    } and set(arguments) == {"limit"}:
        return _valid_limit(arguments["limit"], capability.maximum_items)
    if key in {
        ("facebook", "read.profile"),
        ("instagram", "read.profile"),
    } and set(arguments) == {"username"}:
        return _valid_social_identifier(arguments["username"])
    if key == ("instagram", "browse.user_posts") and set(arguments) == {
        "username",
        "limit",
    }:
        return bool(
            _valid_social_identifier(arguments["username"])
            and _valid_limit(arguments["limit"], capability.maximum_items)
        )
    return False


def _valid_query_and_limit(
    arguments: Mapping[str, object],
    *,
    maximum_limit: int,
) -> bool:
    query = arguments["query"]
    limit = arguments["limit"]
    return bool(
        type(query) is str
        and query == query.strip()
        and 1 <= len(query) <= _MAX_BILIBILI_QUERY_CHARACTERS
        and type(limit) is int
        and 1 <= limit <= maximum_limit
    )


def _valid_v2ex_identifier(value: object) -> bool:
    return bool(
        type(value) is str
        and value.isascii()
        and 1 <= len(value) <= 64
        and value[0].isalnum()
        and all(character.isalnum() or character in "_-" for character in value)
    )


def _valid_limit(value: object, maximum: int) -> bool:
    return type(value) is int and 1 <= value <= maximum


def _valid_subreddit(value: object) -> bool:
    return bool(type(value) is str and value.isascii() and _SUBREDDIT_IDENTIFIER.fullmatch(value))


def _valid_social_identifier(value: object) -> bool:
    return bool(type(value) is str and value.isascii() and _SOCIAL_USERNAME.fullmatch(value))


def _valid_host_capabilities(
    capability: OperationCapabilityV1,
    host_capabilities: tuple[HostCapabilityV1, ...],
) -> bool:
    expected = capability.required_host_capabilities
    actual = tuple(_host_capability_id(capability) for capability in host_capabilities)
    return actual == expected


def _host_capability_id(capability: HostCapabilityV1) -> str:
    if type(capability) is FetchedDocumentV1:
        return FETCHED_DOCUMENT_CAPABILITY
    if type(capability) is NetworkAccessV1:
        return NETWORK_ACCESS_CAPABILITY
    if type(capability) is PrivateWorkspaceV1:
        return PRIVATE_WORKSPACE_CAPABILITY
    if type(capability) is McporterArtifactsV1:
        return MCPORTER_ARTIFACTS_CAPABILITY
    if type(capability) is OpenCliSessionV1:
        return OPENCLI_SESSION_CAPABILITY
    if type(capability) is LinkedInMcpV1:
        return LINKEDIN_MCP_CAPABILITY
    if type(capability) is XueqiuSessionV1:
        return XUEQIU_SESSION_CAPABILITY
    raise AssertionError("unreachable host capability")


def _close_consumable_capabilities(
    host_capabilities: tuple[HostCapabilityV1, ...],
) -> None:
    for capability in host_capabilities:
        if type(capability) is XueqiuSessionV1:
            capability.close()
