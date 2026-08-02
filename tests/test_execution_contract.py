"""Offline contract tests for the additive execution v1 API."""

from __future__ import annotations

import asyncio
import builtins
import importlib.metadata
import socket
import subprocess
import sys
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from types import MappingProxyType

import pytest

from agent_reach.execution.v1 import (
    EXECUTION_ERROR_CODES,
    FETCHED_DOCUMENT_CAPABILITY,
    LINKEDIN_MCP_CAPABILITY,
    MCPORTER_ARTIFACTS_CAPABILITY,
    NETWORK_ACCESS_CAPABILITY,
    OPENCLI_SESSION_CAPABILITY,
    PRIVATE_WORKSPACE_CAPABILITY,
    PROTOCOL_VERSION,
    XUEQIU_SESSION_CAPABILITY,
    ExecutionContextV1,
    ExecutionFailureV1,
    ExecutionItemV1,
    ExecutionLimitsV1,
    ExecutionRequestV1,
    ExecutionSuccessV1,
    FetchedDocumentV1,
    LinkedInMcpV1,
    McporterArtifactsV1,
    NetworkAccessV1,
    OpenCliSessionV1,
    PrivateWorkspaceV1,
    XueqiuSessionV1,
    execute,
    list_capabilities,
)
from agent_reach.execution.v1.contracts import MAX_XUEQIU_SYMBOL_CHARACTERS

FEED_URL = "https://example.com/feed.xml"
ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Feed</title></feed>"""


def test_sdist_excludes_worktree_git_pointer() -> None:
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")

    assert '[tool.hatch.build.targets.sdist]\nexclude = ["/.git"]' in pyproject


def _document() -> FetchedDocumentV1:
    return FetchedDocumentV1(ATOM, "application/atom+xml", FEED_URL)


def _bilibili_item(
    *,
    native_id: str = "BV0000000000",
    url: str | None = None,
    text: object = "description",
    title: object = "title",
    author: object = None,
    duration_seconds: object = 0,
    view_count: object = 0,
) -> ExecutionItemV1:
    return ExecutionItemV1(
        "bilibili.video.v1",
        {
            "text": text,
            "native_id": native_id,
            "title": title,
            "url": url or f"https://www.bilibili.com/video/{native_id}",
            "author": author,
            "duration_seconds": duration_seconds,
            "view_count": view_count,
        },  # type: ignore[arg-type]
    )


def _youtube_item(
    *,
    native_id: str = "dQw4w9WgXcQ",
    url: str | None = None,
    text: object = "description",
    title: object = "title",
    author: object = None,
    published_at: object = None,
    duration_seconds: object = None,
    view_count: object = None,
    comment_count: object = None,
) -> ExecutionItemV1:
    return ExecutionItemV1(
        "youtube.video.v1",
        {
            "text": text,
            "native_id": native_id,
            "title": title,
            "url": url or f"https://www.youtube.com/watch?v={native_id}",
            "author": author,
            "published_at": published_at,
            "duration_seconds": duration_seconds,
            "view_count": view_count,
            "comment_count": comment_count,
        },  # type: ignore[arg-type]
    )


def _subtitle_item(
    *,
    native_id: str = "dQw4w9WgXcQ",
    text: object = "WEBVTT\n",
    language: object = "en",
    origin: object = "manual",
) -> ExecutionItemV1:
    return ExecutionItemV1(
        "youtube.subtitle.v1",
        {
            "text": text,
            "native_id": native_id,
            "title": "title",
            "url": f"https://www.youtube.com/watch?v={native_id}",
            "language": language,
            "origin": origin,
        },  # type: ignore[arg-type]
    )


def _v2ex_topic_item(
    *,
    native_id: str = "42",
    url: str | None = None,
    author: object = "alice",
    published_at: object = "2026-07-31T00:00:00+00:00",
) -> ExecutionItemV1:
    return ExecutionItemV1(
        "v2ex.topic.v1",
        {
            "text": "topic body",
            "native_id": native_id,
            "title": "Topic title",
            "url": url or f"https://www.v2ex.com/t/{native_id}",
            "author": author,
            "published_at": published_at,
            "node": "python",
        },  # type: ignore[arg-type]
    )


def _v2ex_reply_item(
    *,
    topic_id: str = "42",
    native_id: str = "7",
    author: object = "bob",
    published_at: object = None,
) -> ExecutionItemV1:
    return ExecutionItemV1(
        "v2ex.reply.v1",
        {
            "text": "reply body",
            "native_id": native_id,
            "url": f"https://www.v2ex.com/t/{topic_id}#reply{native_id}",
            "author": author,
            "published_at": published_at,
        },  # type: ignore[arg-type]
    )


def _v2ex_profile_item(
    *,
    member_id: str = "9",
    username: str = "alice",
    text: object = "about",
    published_at: object = None,
) -> ExecutionItemV1:
    return ExecutionItemV1(
        "v2ex.profile.v1",
        {
            "text": text,
            "native_id": member_id,
            "title": username,
            "url": f"https://www.v2ex.com/member/{username}",
            "published_at": published_at,
        },  # type: ignore[arg-type]
    )


def _exa_item(*, url: str = "https://example.com/result") -> ExecutionItemV1:
    return ExecutionItemV1(
        "exa.search.result.v1",
        {
            "text": "result body",
            "title": "result title",
            "url": url,
            "author": None,
            "published_at": None,
        },
    )


def _exa_code_item() -> ExecutionItemV1:
    return ExecutionItemV1(
        "exa.code.result.v1",
        {"text": "code", "title": "example", "url": "https://example.com/code"},
    )


def _twitter_item() -> ExecutionItemV1:
    return ExecutionItemV1(
        "twitter.post.v1",
        {
            "text": "post",
            "native_id": "123",
            "url": "https://x.com/i/status/123",
            "author": "alice",
            "published_at": None,
            "reaction_count": 0,
            "view_count": 0,
            "has_media": 0,
        },
    )


def _xiaohongshu_item() -> ExecutionItemV1:
    return ExecutionItemV1(
        "xiaohongshu.note.v1",
        {
            "text": "note",
            "native_id": "0123456789abcdef01234567",
            "title": "title",
            "url": "https://www.xiaohongshu.com/explore/0123456789abcdef01234567",
            "author": "alice",
            "published_at": None,
            "reaction_count": 0,
        },
    )


def _linkedin_item(schema_id: str) -> ExecutionItemV1:
    fields: dict[str, object] = {
        "url": "https://www.linkedin.com/search/results/people/?keywords=engineer",
        "sections": '{"search_results":"result"}',
        "references": None,
    }
    if schema_id == "linkedin.jobs.search.document.v1":
        fields["url"] = "https://www.linkedin.com/jobs/search/?keywords=engineer"
        fields["job_ids"] = "[]"
    return ExecutionItemV1(schema_id, fields)  # type: ignore[arg-type]


def _xueqiu_item(
    *,
    symbol: str = "SH600519",
    exchange: str = "SH",
) -> ExecutionItemV1:
    return ExecutionItemV1(
        "xueqiu.stock.v1",
        {"symbol": symbol, "name": "Kweichow Moutai", "exchange": exchange},
    )


def _mcporter_artifacts(root: Path) -> McporterArtifactsV1:
    return McporterArtifactsV1(
        node_executable=str(root / "node"),
        node_sha256="a" * 64,
        mcporter_root=str(root / "mcporter"),
        mcporter_cli=str(root / "mcporter" / "dist" / "cli.js"),
        mcporter_tree_sha256="b" * 64,
        config_path=str(root / "config.json"),
        config_sha256="c" * 64,
    )


def _opencli_session(root: Path) -> OpenCliSessionV1:
    return OpenCliSessionV1(
        node_executable=str(root / "node"),
        node_sha256="a" * 64,
        opencli_root=str(root / "opencli"),
        opencli_cli=str(root / "opencli" / "dist" / "src" / "main.js"),
        opencli_tree_sha256="b" * 64,
        session_home=str(root / "session"),
    )


def _linkedin_service() -> LinkedInMcpV1:
    return LinkedInMcpV1(
        endpoint="http://127.0.0.1:8001/mcp",
        alias_wheel_sha256="2173ead9777f6202fd581b4ec227d7a7212e9798f26f530b3174ff4683797558",
        backend_wheel_sha256="62a889ac417e5e04d1635d5698df7178edc667a232dca42f417647e2ea25926d",
        runtime_lock_sha256="9150a44d903ecfecdc48d115b87385bb78f3c69f4067951cf238e7fda6f09a17",
        source_commit="7edbd32231afa6d40fabad207329591ad5a4feb0",
        schema_sha256="2549d379d2306ba22c24f06015db67f448d109943fb96f2d656986d2d92f0699",
        log_level="WARNING",
        tool_timeout_seconds=12,
    )


def test_capability_discovery_is_static_closed_and_io_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_io(*_: object, **__: object) -> object:
        raise AssertionError("capability discovery attempted I/O")

    monkeypatch.setattr(builtins, "open", unexpected_io)
    monkeypatch.setattr(importlib.metadata, "distribution", unexpected_io)
    monkeypatch.setattr(importlib.metadata, "version", unexpected_io)
    monkeypatch.setattr(socket, "create_connection", unexpected_io)
    monkeypatch.setattr(subprocess, "run", unexpected_io)

    capabilities = list_capabilities()

    assert [
        (
            item.source,
            item.operation,
            item.argument_schema_id,
            item.result_schema_ids,
            item.backend_id,
            item.backend_version,
            item.required_host_capabilities,
        )
        for item in capabilities
    ] == [
        (
            "rss",
            "read.feed",
            "rss.read.feed.arguments.v1",
            ("rss.feed.v1",),
            "feedparser",
            "6.0.12",
            (FETCHED_DOCUMENT_CAPABILITY,),
        ),
        (
            "rss",
            "browse.entries",
            "rss.browse.entries.arguments.v1",
            ("rss.entry.v1",),
            "feedparser",
            "6.0.12",
            (FETCHED_DOCUMENT_CAPABILITY,),
        ),
        (
            "bilibili",
            "search.videos",
            "bilibili.search.videos.arguments.v1",
            ("bilibili.video.v1",),
            "bili-cli",
            "0.6.2",
            (NETWORK_ACCESS_CAPABILITY,),
        ),
        (
            "bilibili",
            "read.video",
            "bilibili.read.video.arguments.v1",
            ("bilibili.video.v1",),
            "bili-cli",
            "0.6.2",
            (NETWORK_ACCESS_CAPABILITY,),
        ),
        (
            "bilibili",
            "browse.hot",
            "bilibili.browse.hot.arguments.v1",
            ("bilibili.video.v1",),
            "bili-cli",
            "0.6.2",
            (NETWORK_ACCESS_CAPABILITY,),
        ),
        (
            "bilibili",
            "browse.rank",
            "bilibili.browse.rank.arguments.v1",
            ("bilibili.video.v1",),
            "bili-cli",
            "0.6.2",
            (NETWORK_ACCESS_CAPABILITY,),
        ),
        (
            "youtube",
            "read.video",
            "youtube.read.video.arguments.v1",
            ("youtube.video.v1",),
            "yt-dlp",
            "2026.7.4",
            (NETWORK_ACCESS_CAPABILITY,),
        ),
        (
            "youtube",
            "search.videos",
            "youtube.search.videos.arguments.v1",
            ("youtube.video.v1",),
            "yt-dlp",
            "2026.7.4",
            (NETWORK_ACCESS_CAPABILITY,),
        ),
        (
            "youtube",
            "read.subtitles",
            "youtube.read.subtitles.arguments.v1",
            ("youtube.subtitle.v1",),
            "yt-dlp",
            "2026.7.4",
            (NETWORK_ACCESS_CAPABILITY, PRIVATE_WORKSPACE_CAPABILITY),
        ),
        (
            "v2ex",
            "browse.hot",
            "v2ex.browse.hot.arguments.v1",
            ("v2ex.topic.v1",),
            "v2ex-public-api",
            "legacy-json-2026-07-31",
            (NETWORK_ACCESS_CAPABILITY,),
        ),
        (
            "v2ex",
            "browse.node_topics",
            "v2ex.browse.node_topics.arguments.v1",
            ("v2ex.topic.v1",),
            "v2ex-public-api",
            "legacy-json-2026-07-31",
            (NETWORK_ACCESS_CAPABILITY,),
        ),
        (
            "v2ex",
            "read.topic",
            "v2ex.read.topic.arguments.v1",
            ("v2ex.topic.v1", "v2ex.reply.v1"),
            "v2ex-public-api",
            "legacy-json-2026-07-31",
            (NETWORK_ACCESS_CAPABILITY,),
        ),
        (
            "v2ex",
            "read.user",
            "v2ex.read.user.arguments.v1",
            ("v2ex.profile.v1",),
            "v2ex-public-api",
            "legacy-json-2026-07-31",
            (NETWORK_ACCESS_CAPABILITY,),
        ),
        (
            "exa",
            "search.web",
            "exa.search.web.arguments.v1",
            ("exa.search.result.v1",),
            "exa-mcporter",
            "0.12.3+exa-web.v1",
            (NETWORK_ACCESS_CAPABILITY, MCPORTER_ARTIFACTS_CAPABILITY),
        ),
        *[
            (
                source,
                operation,
                f"{source}.{operation}.arguments.v1",
                (schema,),
                "opencli",
                "1.8.6-hermes.1",
                (OPENCLI_SESSION_CAPABILITY,),
            )
            for source, operation, schema in (
                ("reddit", "search.posts", "reddit.post.v1"),
                ("reddit", "read.post", "reddit.thread.item.v1"),
                ("reddit", "browse.subreddit", "reddit.post.v1"),
                ("reddit", "browse.hot", "reddit.post.v1"),
                ("reddit", "browse.popular", "reddit.post.v1"),
                ("reddit", "browse.all", "reddit.post.v1"),
                ("reddit", "read.subreddit", "reddit.subreddit.v1"),
                ("facebook", "search", "facebook.search.result.v1"),
                ("facebook", "read.profile", "facebook.profile.v1"),
                ("facebook", "browse.feed", "facebook.post.v1"),
                ("facebook", "browse.groups", "facebook.group.v1"),
                ("instagram", "search.users", "instagram.user.v1"),
                ("instagram", "read.profile", "instagram.profile.v1"),
                ("instagram", "browse.user_posts", "instagram.post.v1"),
                ("instagram", "browse.explore", "instagram.post.v1"),
                ("twitter", "search.posts", "twitter.post.v1"),
                ("xiaohongshu", "search.notes", "xiaohongshu.note.v1"),
            )
        ],
        (
            "linkedin",
            "search.people",
            "linkedin.search.people.arguments.v1",
            ("linkedin.people.search.document.v1",),
            "linkedin-scraper-mcp",
            "4.14.0",
            (MCPORTER_ARTIFACTS_CAPABILITY, LINKEDIN_MCP_CAPABILITY),
        ),
        (
            "linkedin",
            "search.jobs",
            "linkedin.search.jobs.arguments.v1",
            ("linkedin.jobs.search.document.v1",),
            "linkedin-scraper-mcp",
            "4.14.0",
            (MCPORTER_ARTIFACTS_CAPABILITY, LINKEDIN_MCP_CAPABILITY),
        ),
        (
            "xueqiu",
            "search.stocks",
            "xueqiu.search.stocks.arguments.v1",
            ("xueqiu.stock.v1",),
            "xueqiu-api",
            "1.5.0+search.v1",
            (XUEQIU_SESSION_CAPABILITY,),
        ),
        (
            "exa",
            "search.code",
            "exa.search.code.arguments.v1",
            ("exa.code.result.v1",),
            "exa-mcporter",
            "0.12.3+exa-code.v1",
            (NETWORK_ACCESS_CAPABILITY, MCPORTER_ARTIFACTS_CAPABILITY),
        ),
    ]
    assert all(item.protocol_version == PROTOCOL_VERSION for item in capabilities)
    assert [item.maximum_items for item in capabilities] == [
        1,
        21,
        50,
        1,
        50,
        50,
        1,
        50,
        1,
        50,
        50,
        21,
        1,
        20,
        50,
        14,
        50,
        50,
        50,
        50,
        1,
        50,
        1,
        50,
        50,
        50,
        1,
        50,
        50,
        50,
        50,
        1,
        1,
        50,
        20,
    ]
    assert all(item.maximum_document_bytes == 1_048_576 for item in capabilities)
    assert all(item.maximum_metadata_bytes == 16_384 for item in capabilities)
    assert [item.maximum_output_bytes for item in capabilities] == [
        1_048_576,
        1_048_576,
        *([524_288] * 7),
        *([1_048_576] * 4),
        *([524_288] * 20),
        1_048_576,
        524_288,
    ]
    assert all(item.maximum_content_type_characters == 512 for item in capabilities)
    assert all(item.maximum_content_location_characters == 8_192 for item in capabilities)
    assert all(item.maximum_text_characters == 16_000 for item in capabilities)
    assert all(item.maximum_title_characters == 4_096 for item in capabilities)
    assert all(item.maximum_url_characters == 8_192 for item in capabilities)
    assert all(item.maximum_native_id_characters == 512 for item in capabilities)
    assert [item.maximum_author_characters for item in capabilities] == [
        2_048,
        2_048,
        *([1_024] * 7),
        *([2_048] * 5),
        *([2_048] * 21),
    ]
    assert all(item.maximum_published_characters == 512 for item in capabilities)
    with pytest.raises(FrozenInstanceError):
        capabilities[2].maximum_items = 51  # type: ignore[misc]


def test_clean_process_discovery_does_not_import_backend_or_host_config() -> None:
    repository = Path(__file__).resolve().parents[1]
    script = f"""
import pathlib
import sys
sys.path.insert(0, {str(repository)!r})
def denied_home(cls):
    raise AssertionError('ambient home access')
pathlib.Path.home = classmethod(denied_home)
from agent_reach.execution.v1 import list_capabilities
assert len(list_capabilities()) == 35
assert 'feedparser' not in sys.modules
assert not any(name == 'bili_cli' or name.startswith('bili_cli.') for name in sys.modules)
assert not any(name == 'yt_dlp' or name.startswith('yt_dlp.') for name in sys.modules)
assert 'yt_dlp_ejs' not in sys.modules
assert 'deno' not in sys.modules
assert 'agent_reach.execution.v1.rss' not in sys.modules
assert 'agent_reach.execution.v1.bilibili' not in sys.modules
assert 'agent_reach.execution.v1.youtube' not in sys.modules
assert 'agent_reach.execution.v1.v2ex' not in sys.modules
assert 'agent_reach.execution.v1._v2ex_transport' not in sys.modules
assert 'agent_reach.execution.v1.exa' not in sys.modules
assert 'agent_reach.execution.v1.opencli_social' not in sys.modules
assert 'agent_reach.execution.v1.linkedin' not in sys.modules
assert 'agent_reach.execution.v1.xueqiu' not in sys.modules
assert 'httpcore' not in sys.modules
assert 'agent_reach.config' not in sys.modules
"""

    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd="/",
        env={},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_request_context_and_items_are_immutable_and_closed() -> None:
    arguments = {"max_entries": 2}
    request = ExecutionRequestV1(
        PROTOCOL_VERSION,
        "rss",
        "browse.entries",
        arguments,
    )
    arguments["max_entries"] = 3

    assert request.arguments == MappingProxyType({"max_entries": 2})
    with pytest.raises(TypeError):
        request.arguments["max_entries"] = 4  # type: ignore[index]
    forbidden = ExecutionRequestV1(
        PROTOCOL_VERSION,
        "rss",
        "browse.entries",
        {"backend": "feedparser"},
    )
    rejected = execute(forbidden, ExecutionContextV1((_document(),)))
    assert isinstance(rejected, ExecutionFailureV1)
    assert rejected.error_code == "invalid_request"
    with pytest.raises(ValueError):
        FetchedDocumentV1(ATOM, "application/atom+xml", "file:///private/feed")
    assert ExecutionLimitsV1(maximum_items=50).maximum_items == 50
    with pytest.raises(ValueError):
        ExecutionLimitsV1(maximum_items=51)

    fields = {"text": "body", "title": "title", "url": None}
    item = ExecutionItemV1("rss.feed.v1", fields)
    fields["text"] = "changed"
    result = ExecutionSuccessV1(
        PROTOCOL_VERSION,
        "rss",
        "read.feed",
        "feedparser",
        "6.0.12",
        (item,),
    )
    assert result.items[0].fields["text"] == "body"
    with pytest.raises(TypeError):
        result.items[0].fields["text"] = "changed"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        result.truncated = True  # type: ignore[misc]
    with pytest.raises(ValueError):
        ExecutionItemV1(
            "rss.feed.v1",
            {"text": "body", "title": "title", "url": None, "raw": "backend"},
        )


def test_network_access_marker_is_data_free_immutable_and_context_is_closed() -> None:
    marker = NetworkAccessV1()

    assert fields(marker) == ()
    assert not hasattr(marker, "__dict__")
    with pytest.raises(TypeError):
        NetworkAccessV1("https://www.bilibili.com")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        NetworkAccessV1(endpoint="https://www.bilibili.com")  # type: ignore[call-arg]
    with pytest.raises((FrozenInstanceError, TypeError)):
        marker.endpoint = "https://www.bilibili.com"  # type: ignore[attr-defined]

    supplied = [marker]
    context = ExecutionContextV1(supplied)  # type: ignore[arg-type]
    supplied.clear()
    assert context.host_capabilities == (marker,)
    with pytest.raises(ValueError):
        ExecutionContextV1((marker, NetworkAccessV1()))
    with pytest.raises(ValueError):
        ExecutionContextV1((object(),))  # type: ignore[arg-type]


def test_private_workspace_and_mcporter_capabilities_are_closed_and_immutable(
    tmp_path: Path,
) -> None:
    workspace = PrivateWorkspaceV1()
    artifacts = _mcporter_artifacts(tmp_path)

    assert fields(workspace) == ()
    assert not hasattr(workspace, "__dict__")
    with pytest.raises(TypeError):
        PrivateWorkspaceV1(path=str(tmp_path))  # type: ignore[call-arg]
    with pytest.raises((FrozenInstanceError, TypeError)):
        workspace.path = str(tmp_path)  # type: ignore[attr-defined]
    assert tuple(field.name for field in fields(artifacts)) == (
        "node_executable",
        "node_sha256",
        "mcporter_root",
        "mcporter_cli",
        "mcporter_tree_sha256",
        "config_path",
        "config_sha256",
    )
    assert not hasattr(artifacts, "__dict__")
    with pytest.raises(FrozenInstanceError):
        artifacts.node_sha256 = "d" * 64  # type: ignore[misc]

    supplied = [NetworkAccessV1(), artifacts]
    context = ExecutionContextV1(supplied)  # type: ignore[arg-type]
    supplied.clear()
    assert context.host_capabilities == (NetworkAccessV1(), artifacts)


def test_opencli_session_capability_is_closed_and_immutable(tmp_path: Path) -> None:
    session = _opencli_session(tmp_path)

    assert tuple(field.name for field in fields(session)) == (
        "node_executable",
        "node_sha256",
        "opencli_root",
        "opencli_cli",
        "opencli_tree_sha256",
        "session_home",
    )
    assert not hasattr(session, "__dict__")
    with pytest.raises(FrozenInstanceError):
        session.node_sha256 = "c" * 64  # type: ignore[misc]
    assert ExecutionContextV1((session,)).host_capabilities == (session,)
    with pytest.raises(ValueError):
        OpenCliSessionV1(
            node_executable=str(tmp_path / "node"),
            node_sha256="a" * 64,
            opencli_root=str(tmp_path / "opencli"),
            opencli_cli=str(tmp_path / "outside.js"),
            opencli_tree_sha256="b" * 64,
            session_home=str(tmp_path / "session"),
        )


def test_mcporter_artifacts_reject_noncanonical_paths_and_digest_drift(tmp_path: Path) -> None:
    valid = _mcporter_artifacts(tmp_path)
    values: dict[str, object] = {field.name: getattr(valid, field.name) for field in fields(valid)}
    invalid = (
        ("node_executable", "node"),
        ("node_executable", f"{tmp_path}/nested/../node"),
        ("config_path", f"{tmp_path}/config.json/"),
        ("mcporter_cli", str(tmp_path / "outside" / "cli.js")),
        ("mcporter_cli", str(tmp_path / "mcporter")),
        ("node_sha256", "A" * 64),
        ("mcporter_tree_sha256", "b" * 63),
        ("config_sha256", "g" * 64),
    )
    for field_name, value in invalid:
        candidate = {**values, field_name: value}
        with pytest.raises(ValueError):
            McporterArtifactsV1(**candidate)  # type: ignore[arg-type]


def test_bilibili_result_schema_accepts_bounded_integers_but_not_booleans() -> None:
    item = _bilibili_item(
        duration_seconds=0,
        view_count=(1 << 53) - 1,
    )
    result = ExecutionSuccessV1(
        PROTOCOL_VERSION,
        "bilibili",
        "read.video",
        "bili-cli",
        "0.6.2",
        (item,),
    )

    assert result.items[0].fields["duration_seconds"] == 0
    assert result.items[0].fields["view_count"] == (1 << 53) - 1
    for field_name in ("duration_seconds", "view_count"):
        for invalid in (False, True, -1, 1 << 53, None, "1"):
            values = {field_name: invalid}
            with pytest.raises(ValueError):
                _bilibili_item(**values)  # type: ignore[arg-type]


def test_bilibili_success_requires_correlated_identity_and_closed_partial_state() -> None:
    mismatched = _bilibili_item(url="https://www.bilibili.com/video/BV1111111111")

    with pytest.raises(ValueError):
        ExecutionSuccessV1(
            PROTOCOL_VERSION,
            "bilibili",
            "read.video",
            "bili-cli",
            "0.6.2",
            (mismatched,),
        )
    with pytest.raises(ValueError):
        ExecutionSuccessV1(
            PROTOCOL_VERSION,
            "bilibili",
            "read.video",
            "bili-cli",
            "0.6.2",
            (_bilibili_item(),),
            partial_error_code="permanent",
        )


def test_bilibili_result_payload_is_capped_to_the_worker_frame_limit() -> None:
    maximum_size_item = _bilibili_item(
        text="x" * 16_000,
        title="t" * 4_096,
        author="a" * 1_024,
    )

    with pytest.raises(ValueError):
        ExecutionSuccessV1(
            PROTOCOL_VERSION,
            "bilibili",
            "search.videos",
            "bili-cli",
            "0.6.2",
            (maximum_size_item,) * 25,
        )
    with pytest.raises(ValueError):
        _bilibili_item(author="a" * 1_025)


def test_youtube_result_schema_is_closed_nullable_and_identity_correlated() -> None:
    item = _youtube_item(
        author="channel",
        published_at="2009-10-25",
        duration_seconds=0,
        view_count=(1 << 53) - 1,
        comment_count=None,
    )
    success = ExecutionSuccessV1(
        PROTOCOL_VERSION,
        "youtube",
        "read.video",
        "yt-dlp",
        "2026.7.4",
        (item,),
    )

    assert success.items == (item,)
    for field_name in ("duration_seconds", "view_count", "comment_count"):
        for invalid in (False, True, -1, 1 << 53, 1.5, "1"):
            with pytest.raises(ValueError):
                _youtube_item(**{field_name: invalid})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ExecutionItemV1(
            "youtube.video.v1",
            {**dict(item.fields), "raw": "private"},
        )
    with pytest.raises(ValueError):
        ExecutionSuccessV1(
            PROTOCOL_VERSION,
            "youtube",
            "read.video",
            "yt-dlp",
            "2026.7.4",
            (_youtube_item(url="https://www.youtube.com/watch?v=aaaaaaaaaaa"),),
        )
    for published_at in ("1969-12-31", "2026-02-31"):
        with pytest.raises(ValueError):
            ExecutionSuccessV1(
                PROTOCOL_VERSION,
                "youtube",
                "read.video",
                "yt-dlp",
                "2026.7.4",
                (_youtube_item(published_at=published_at),),
            )
    with pytest.raises(ValueError):
        _youtube_item(text="value\x00hidden")


def test_youtube_subtitle_schema_is_closed_and_correlated() -> None:
    item = _subtitle_item()
    success = ExecutionSuccessV1(
        PROTOCOL_VERSION,
        "youtube",
        "read.subtitles",
        "yt-dlp",
        "2026.7.4",
        (item,),
    )

    assert success.items == (item,)
    for invalid in (
        _subtitle_item(text="plain text"),
        _subtitle_item(native_id="invalid"),
        _subtitle_item(language="bad language"),
        _subtitle_item(origin="provider"),
    ):
        with pytest.raises(ValueError):
            ExecutionSuccessV1(
                PROTOCOL_VERSION,
                "youtube",
                "read.subtitles",
                "yt-dlp",
                "2026.7.4",
                (invalid,),
            )
    with pytest.raises(ValueError):
        ExecutionSuccessV1(
            PROTOCOL_VERSION,
            "youtube",
            "read.subtitles",
            "yt-dlp",
            "2026.7.4",
            (item,),
            partial_error_code="permanent",
        )


def test_v2ex_result_schemas_freeze_order_identity_timestamps_and_partial_state() -> None:
    topic = _v2ex_topic_item()
    replies = (_v2ex_reply_item(native_id="7"), _v2ex_reply_item(native_id="8"))
    complete = ExecutionSuccessV1(
        PROTOCOL_VERSION,
        "v2ex",
        "read.topic",
        "v2ex-public-api",
        "legacy-json-2026-07-31",
        (topic, *replies),
    )
    profile = ExecutionSuccessV1(
        PROTOCOL_VERSION,
        "v2ex",
        "read.user",
        "v2ex-public-api",
        "legacy-json-2026-07-31",
        (_v2ex_profile_item(),),
    )

    assert complete.items == (topic, *replies)
    assert profile.items[0].fields["title"] == "alice"
    for code in (
        "not_found",
        "authentication",
        "authorization",
        "rate_limit",
        "transient",
        "permanent",
        "backend_contract_violation",
    ):
        partial = ExecutionSuccessV1(
            PROTOCOL_VERSION,
            "v2ex",
            "read.topic",
            "v2ex-public-api",
            "legacy-json-2026-07-31",
            (topic,),
            partial_error_code=code,  # type: ignore[arg-type]
        )
        assert partial.partial_error_code == code

    invalid_sequences = (
        (replies[0], topic),
        (topic, _v2ex_reply_item(topic_id="43")),
        (topic, replies[0], replies[0]),
    )
    for items in invalid_sequences:
        with pytest.raises(ValueError):
            ExecutionSuccessV1(
                PROTOCOL_VERSION,
                "v2ex",
                "read.topic",
                "v2ex-public-api",
                "legacy-json-2026-07-31",
                items,
            )
    with pytest.raises(ValueError):
        ExecutionSuccessV1(
            PROTOCOL_VERSION,
            "v2ex",
            "read.topic",
            "v2ex-public-api",
            "legacy-json-2026-07-31",
            (topic, replies[0]),
            partial_error_code="transient",
        )
    with pytest.raises(ValueError):
        ExecutionSuccessV1(
            PROTOCOL_VERSION,
            "v2ex",
            "read.topic",
            "v2ex-public-api",
            "legacy-json-2026-07-31",
            (topic,),
            partial_error_code="invalid_input",
        )


def test_v2ex_result_rejects_unsafe_numeric_ids_and_authors() -> None:
    unsafe_id = str(1 << 53)
    invalid_results = (
        ("browse.hot", (_v2ex_topic_item(native_id=unsafe_id),)),
        (
            "read.topic",
            (_v2ex_topic_item(), _v2ex_reply_item(native_id=unsafe_id)),
        ),
        ("read.user", (_v2ex_profile_item(member_id=unsafe_id),)),
        ("browse.hot", (_v2ex_topic_item(author="bad author"),)),
        (
            "read.topic",
            (_v2ex_topic_item(), _v2ex_reply_item(author="bad author")),
        ),
        ("read.user", (_v2ex_profile_item(username="bad author"),)),
    )

    for operation, items in invalid_results:
        with pytest.raises(ValueError):
            ExecutionSuccessV1(
                PROTOCOL_VERSION,
                "v2ex",
                operation,
                "v2ex-public-api",
                "legacy-json-2026-07-31",
                items,
            )

    nullable_author = ExecutionSuccessV1(
        PROTOCOL_VERSION,
        "v2ex",
        "browse.hot",
        "v2ex-public-api",
        "legacy-json-2026-07-31",
        (_v2ex_topic_item(author=None),),
    )
    assert nullable_author.items[0].fields["author"] is None


@pytest.mark.parametrize(
    "published_at",
    ["1969-12-31T23:59:59+00:00", "2026-07-31T08:00:00+08:00", "2026-02-31T00:00:00+00:00"],
)
def test_v2ex_result_rejects_noncanonical_utc_timestamps(published_at: str) -> None:
    with pytest.raises(ValueError):
        ExecutionSuccessV1(
            PROTOCOL_VERSION,
            "v2ex",
            "browse.hot",
            "v2ex-public-api",
            "legacy-json-2026-07-31",
            (_v2ex_topic_item(published_at=published_at),),
        )


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/result",
        "https://localhost/result",
        "https://service.local/result",
        "http://127.0.0.1/result",
        "https://[::1]/result",
        "https://user@example.com/result",
        "https://example.com:8443/result",
        "https://example.com\\private",
        "https://例子.测试/result",
    ],
)
def test_exa_result_schema_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(ValueError):
        ExecutionSuccessV1(
            PROTOCOL_VERSION,
            "exa",
            "search.web",
            "exa-mcporter",
            "0.12.3+exa-web.v1",
            (_exa_item(url=url),),
        )


@pytest.mark.parametrize(
    ("symbol", "exchange"),
    [
        ("SH600519", "SH"),
        ("SH600519", "SHA"),
        ("SHA:600519", "SHA"),
        ("SZA:300750", "SZA"),
        ("BJA:430047", "BJA"),
        ("US:" + "A" * (MAX_XUEQIU_SYMBOL_CHARACTERS - 3), "US"),
    ],
)
def test_xueqiu_result_contract_accepts_runtime_symbol_grammar(
    symbol: str,
    exchange: str,
) -> None:
    result = ExecutionSuccessV1(
        PROTOCOL_VERSION,
        "xueqiu",
        "search.stocks",
        "xueqiu-api",
        "1.5.0+search.v1",
        (_xueqiu_item(symbol=symbol, exchange=exchange),),
    )

    assert result.items[0].fields["symbol"] == symbol


@pytest.mark.parametrize(
    ("symbol", "exchange"),
    [
        ("SH60051", "SH"),
        ("SH600519", "SZ"),
        ("SHA600519", "SHA"),
        ("SHA:", "SHA"),
        ("SHA:600519", "SH"),
        ("sha:600519", "SHA"),
        ("US:" + "A" * (MAX_XUEQIU_SYMBOL_CHARACTERS - 2), "US"),
    ],
)
def test_xueqiu_result_contract_rejects_runtime_invalid_symbol_grammar(
    symbol: str,
    exchange: str,
) -> None:
    with pytest.raises(ValueError):
        ExecutionSuccessV1(
            PROTOCOL_VERSION,
            "xueqiu",
            "search.stocks",
            "xueqiu-api",
            "1.5.0+search.v1",
            (_xueqiu_item(symbol=symbol, exchange=exchange),),
        )


@pytest.mark.parametrize(
    ("source", "operation", "backend_id", "backend_version", "substituted_item"),
    [
        ("twitter", "search.posts", "opencli", "1.8.6-hermes.1", _xiaohongshu_item()),
        ("xiaohongshu", "search.notes", "opencli", "1.8.6-hermes.1", _twitter_item()),
        (
            "linkedin",
            "search.people",
            "linkedin-scraper-mcp",
            "4.14.0",
            _linkedin_item("linkedin.jobs.search.document.v1"),
        ),
        (
            "linkedin",
            "search.jobs",
            "linkedin-scraper-mcp",
            "4.14.0",
            _linkedin_item("linkedin.people.search.document.v1"),
        ),
        ("xueqiu", "search.stocks", "xueqiu-api", "1.5.0+search.v1", _exa_code_item()),
        ("exa", "search.code", "exa-mcporter", "0.12.3+exa-code.v1", _exa_item()),
    ],
)
def test_new_operation_success_contracts_reject_cross_operation_result_substitution(
    source: str,
    operation: str,
    backend_id: str,
    backend_version: str,
    substituted_item: ExecutionItemV1,
) -> None:
    with pytest.raises(ValueError):
        ExecutionSuccessV1(
            PROTOCOL_VERSION,
            source,
            operation,
            backend_id,
            backend_version,
            (substituted_item,),
        )


def test_error_taxonomy_is_expanded_but_remains_closed_with_exact_provenance() -> None:
    assert EXECUTION_ERROR_CODES == frozenset(
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
    for error_code in (
        "invalid_input",
        "not_found",
        "authentication",
        "authorization",
        "rate_limit",
        "transient",
    ):
        failure = ExecutionFailureV1(
            PROTOCOL_VERSION,
            "bilibili",
            "read.video",
            "bili-cli",
            "0.6.2",
            error_code,  # type: ignore[arg-type]
        )
        assert failure.error_code == error_code

    with pytest.raises(ValueError):
        ExecutionFailureV1(
            PROTOCOL_VERSION,
            "bilibili",
            "read.video",
            "bili-cli",
            "0.6.3",
            "transient",
        )
    with pytest.raises(ValueError):
        ExecutionFailureV1(
            PROTOCOL_VERSION,
            "bilibili",
            "read.video",
            "bili-cli",
            "0.6.2",
            "future_error",  # type: ignore[arg-type]
        )


def test_rss_result_bounds_nullable_fields_partial_state_and_provenance_are_unchanged() -> None:
    feed = ExecutionItemV1("rss.feed.v1", {"text": None, "title": None, "url": None})
    success = ExecutionSuccessV1(
        PROTOCOL_VERSION,
        "rss",
        "read.feed",
        "feedparser",
        "6.0.12",
        (feed,),
        partial_error_code="permanent",
    )
    assert success.partial_error_code == "permanent"

    entry = ExecutionItemV1(
        "rss.entry.v1",
        {
            "text": None,
            "native_id": None,
            "title": None,
            "url": None,
            "author": None,
            "published_at": None,
        },
    )
    with pytest.raises(ValueError):
        ExecutionSuccessV1(
            PROTOCOL_VERSION,
            "rss",
            "browse.entries",
            "feedparser",
            "6.0.12",
            (entry,) * 22,
        )
    with pytest.raises(ValueError):
        ExecutionSuccessV1(
            PROTOCOL_VERSION,
            "rss",
            "read.feed",
            "bili-cli",
            "0.6.2",
            (feed,),
        )
    with pytest.raises(ValueError):
        ExecutionItemV1("rss.feed.v1", {"text": 1, "title": None, "url": None})


def test_fetched_document_accepts_an_omitted_content_type() -> None:
    document = FetchedDocumentV1(ATOM, "", FEED_URL)

    assert document.content_type == ""


@pytest.mark.parametrize(
    ("content_type", "content_location"),
    [
        ("application/rss+xml\nCookie: private", FEED_URL),
        ("application/rss+xml", "https://user:pass@example.com/feed.xml"),
        ("application/rss+xml", "https://example.com/feed.xml?token=private"),
        ("application/rss+xml", "http://127.0.0.1/feed.xml"),
        ("application/rss+xml", "https://example.com:8443/feed.xml"),
    ],
)
def test_fetched_document_rejects_unsafe_metadata(
    content_type: str,
    content_location: str,
) -> None:
    with pytest.raises(ValueError):
        FetchedDocumentV1(ATOM, content_type, content_location)


@pytest.mark.parametrize(
    ("execution_request", "context", "error_code"),
    [
        (
            ExecutionRequestV1("v2", "rss", "read.feed"),
            ExecutionContextV1((_document(),)),
            "unsupported_protocol_version",
        ),
        (
            ExecutionRequestV1(PROTOCOL_VERSION, "unknown", "read.feed"),
            ExecutionContextV1((_document(),)),
            "unsupported_source",
        ),
        (
            ExecutionRequestV1(PROTOCOL_VERSION, "rss", "unknown"),
            ExecutionContextV1((_document(),)),
            "unsupported_operation",
        ),
        (
            ExecutionRequestV1(PROTOCOL_VERSION, "rss", "read.feed"),
            ExecutionContextV1(),
            "host_capability_missing",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "rss",
                "browse.entries",
                {"max_entries": 2, "argv": "forbidden"},
            ),
            ExecutionContextV1((_document(),)),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "youtube",
                "read.video",
                {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            ),
            ExecutionContextV1(),
            "host_capability_missing",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "youtube",
                "read.video",
                {
                    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "proxy": "http://private",
                },
            ),
            ExecutionContextV1((NetworkAccessV1(),)),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "youtube",
                "read.video",
                {"url": "https://www.youtube.com/watch?v=%64Qw4w9WgXcQ"},
            ),
            ExecutionContextV1((NetworkAccessV1(),)),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "rss",
                "browse.entries",
                {"max_entries": 22},
            ),
            ExecutionContextV1((_document(),), limits=ExecutionLimitsV1(maximum_items=50)),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "bilibili",
                "read.video",
                {"url": "https://www.bilibili.com/video/BV0000000000"},
            ),
            ExecutionContextV1(),
            "host_capability_missing",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "bilibili",
                "search.videos",
                {"query": "query", "limit": 1},
            ),
            ExecutionContextV1((_document(),)),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(PROTOCOL_VERSION, "rss", "read.feed"),
            ExecutionContextV1((NetworkAccessV1(),)),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "bilibili",
                "browse.hot",
                {"limit": 1},
            ),
            ExecutionContextV1((_document(), NetworkAccessV1())),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "bilibili",
                "browse.rank",
                {"limit": 1, "backend": "bili-cli"},
            ),
            ExecutionContextV1((NetworkAccessV1(),)),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "youtube",
                "search.videos",
                {"query": "query", "limit": 1, "endpoint": "https://private"},
            ),
            ExecutionContextV1((NetworkAccessV1(),)),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "youtube",
                "read.subtitles",
                {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "language": None},
            ),
            ExecutionContextV1((NetworkAccessV1(),)),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "youtube",
                "read.subtitles",
                {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "language": "en"},
            ),
            ExecutionContextV1((PrivateWorkspaceV1(), NetworkAccessV1())),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "v2ex",
                "browse.node_topics",
                {"node": "python", "page": 0, "limit": 1},
            ),
            ExecutionContextV1((NetworkAccessV1(),)),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "v2ex",
                "read.topic",
                {"topic_id": "0"},
            ),
            ExecutionContextV1((NetworkAccessV1(),)),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "exa",
                "search.web",
                {"query": "private query", "limit": 20},
            ),
            ExecutionContextV1((NetworkAccessV1(),)),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "exa",
                "search.web",
                {"query": "private query", "limit": 20},
            ),
            ExecutionContextV1(
                (
                    _mcporter_artifacts(Path("/opt/agent-reach")),
                    NetworkAccessV1(),
                )
            ),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "exa",
                "search.web",
                {"query": " private query ", "limit": 20},
            ),
            ExecutionContextV1(
                (
                    NetworkAccessV1(),
                    _mcporter_artifacts(Path("/opt/agent-reach")),
                )
            ),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "twitter",
                "search.posts",
                {"query": "query", "limit": 1, "command": "send"},
            ),
            ExecutionContextV1((_opencli_session(Path("/opt/opencli")),)),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "xiaohongshu",
                "search.notes",
                {"query": "query", "limit": 1},
            ),
            ExecutionContextV1((NetworkAccessV1(),)),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "linkedin",
                "search.people",
                {"query": "query", "limit": 1},
            ),
            ExecutionContextV1(
                (
                    _linkedin_service(),
                    _mcporter_artifacts(Path("/opt/agent-reach")),
                )
            ),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "linkedin",
                "search.jobs",
                {"query": "query", "limit": 1, "method": "send_message"},
            ),
            ExecutionContextV1(
                (
                    _mcporter_artifacts(Path("/opt/agent-reach")),
                    _linkedin_service(),
                )
            ),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "xueqiu",
                "search.stocks",
                {"query": "query", "limit": 0},
            ),
            ExecutionContextV1((XueqiuSessionV1(bytearray(b"xq_a_token=secret")),)),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "exa",
                "search.code",
                {"query": "query", "limit": 1},
            ),
            ExecutionContextV1(
                (
                    _mcporter_artifacts(Path("/opt/agent-reach")),
                    NetworkAccessV1(),
                )
            ),
            "invalid_request",
        ),
    ],
)
def test_dispatch_rejects_unknown_authority_before_backend_import(
    monkeypatch: pytest.MonkeyPatch,
    execution_request: ExecutionRequestV1,
    context: ExecutionContextV1,
    error_code: str,
) -> None:
    original_import = builtins.__import__

    def guarded_import(
        name: str,
        globals_: dict[str, object] | None = None,
        locals_: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if level == 1 and name in {
            "bilibili",
            "exa",
            "linkedin",
            "opencli_social",
            "rss",
            "v2ex",
            "xueqiu",
            "youtube",
        }:
            raise AssertionError(f"rejected request imported {name}")
        return original_import(name, globals_, locals_, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    result = execute(execution_request, context)

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == error_code
    assert result.backend_id is None


def test_host_cancellation_propagates_without_backend_execution() -> None:
    def cancelled() -> None:
        raise asyncio.CancelledError

    cases = (
        (
            ExecutionRequestV1(PROTOCOL_VERSION, "rss", "read.feed"),
            (_document(),),
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "bilibili",
                "browse.hot",
                {"limit": 1},
            ),
            (NetworkAccessV1(),),
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "youtube",
                "read.video",
                {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            ),
            (NetworkAccessV1(),),
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "youtube",
                "read.subtitles",
                {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "language": None},
            ),
            (NetworkAccessV1(), PrivateWorkspaceV1()),
        ),
        (
            ExecutionRequestV1(PROTOCOL_VERSION, "v2ex", "browse.hot", {"limit": 1}),
            (NetworkAccessV1(),),
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "exa",
                "search.web",
                {"query": "query", "limit": 1},
            ),
            (NetworkAccessV1(), _mcporter_artifacts(Path("/opt/agent-reach"))),
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "twitter",
                "search.posts",
                {"query": "query", "limit": 1},
            ),
            (_opencli_session(Path("/opt/opencli-twitter")),),
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "xiaohongshu",
                "search.notes",
                {"query": "query", "limit": 1},
            ),
            (_opencli_session(Path("/opt/opencli-xiaohongshu")),),
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "linkedin",
                "search.people",
                {"query": "query", "limit": 1},
            ),
            (
                _mcporter_artifacts(Path("/opt/linkedin-people")),
                _linkedin_service(),
            ),
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "linkedin",
                "search.jobs",
                {"query": "query", "limit": 1},
            ),
            (
                _mcporter_artifacts(Path("/opt/linkedin-jobs")),
                _linkedin_service(),
            ),
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "xueqiu",
                "search.stocks",
                {"query": "query", "limit": 1},
            ),
            (XueqiuSessionV1(bytearray(b"xq_a_token=secret")),),
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "exa",
                "search.code",
                {"query": "query", "limit": 1},
            ),
            (NetworkAccessV1(), _mcporter_artifacts(Path("/opt/exa-code"))),
        ),
    )
    for request, host_capabilities in cases:
        context = ExecutionContextV1(host_capabilities, checkpoint=cancelled)
        with pytest.raises(asyncio.CancelledError):
            execute(request, context)


def test_top_level_agent_reach_export_remains_compatible() -> None:
    from agent_reach import AgentReach
    from agent_reach.core import AgentReach as CoreAgentReach

    assert AgentReach is CoreAgentReach
