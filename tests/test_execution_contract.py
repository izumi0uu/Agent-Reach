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
    NETWORK_ACCESS_CAPABILITY,
    PROTOCOL_VERSION,
    ExecutionContextV1,
    ExecutionFailureV1,
    ExecutionItemV1,
    ExecutionLimitsV1,
    ExecutionRequestV1,
    ExecutionSuccessV1,
    FetchedDocumentV1,
    NetworkAccessV1,
    execute,
    list_capabilities,
)

FEED_URL = "https://example.com/feed.xml"
ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Feed</title></feed>"""


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
    ]
    assert all(item.protocol_version == PROTOCOL_VERSION for item in capabilities)
    assert [item.maximum_items for item in capabilities] == [1, 21, 50, 1, 50, 50]
    assert all(item.maximum_document_bytes == 1_048_576 for item in capabilities)
    assert all(item.maximum_metadata_bytes == 16_384 for item in capabilities)
    assert [item.maximum_output_bytes for item in capabilities] == [
        1_048_576,
        1_048_576,
        524_288,
        524_288,
        524_288,
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
        1_024,
        1_024,
        1_024,
        1_024,
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
assert len(list_capabilities()) == 6
assert 'feedparser' not in sys.modules
assert not any(name == 'bili_cli' or name.startswith('bili_cli.') for name in sys.modules)
assert 'agent_reach.execution.v1.rss' not in sys.modules
assert 'agent_reach.execution.v1.bilibili' not in sys.modules
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
        if level == 1 and name in {"bilibili", "rss"}:
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
    )
    for request, host_capabilities in cases:
        context = ExecutionContextV1(host_capabilities, checkpoint=cancelled)
        with pytest.raises(asyncio.CancelledError):
            execute(request, context)


def test_top_level_agent_reach_export_remains_compatible() -> None:
    from agent_reach import AgentReach
    from agent_reach.core import AgentReach as CoreAgentReach

    assert AgentReach is CoreAgentReach
