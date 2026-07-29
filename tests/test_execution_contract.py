"""Offline contract tests for the additive execution v1 API."""

from __future__ import annotations

import asyncio
import builtins
import socket
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import MappingProxyType

import pytest

from agent_reach.execution.v1 import (
    FETCHED_DOCUMENT_CAPABILITY,
    PROTOCOL_VERSION,
    ExecutionContextV1,
    ExecutionFailureV1,
    ExecutionItemV1,
    ExecutionLimitsV1,
    ExecutionRequestV1,
    ExecutionSuccessV1,
    FetchedDocumentV1,
    execute,
    list_capabilities,
)

FEED_URL = "https://example.com/feed.xml"
ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Feed</title></feed>"""


def _document() -> FetchedDocumentV1:
    return FetchedDocumentV1(ATOM, "application/atom+xml", FEED_URL)


def test_capability_discovery_is_static_closed_and_io_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_io(*_: object, **__: object) -> object:
        raise AssertionError("capability discovery attempted I/O")

    monkeypatch.setattr(builtins, "open", unexpected_io)
    monkeypatch.setattr(socket, "create_connection", unexpected_io)
    monkeypatch.setattr(subprocess, "run", unexpected_io)

    capabilities = list_capabilities()

    assert [(item.source, item.operation) for item in capabilities] == [
        ("rss", "read.feed"),
        ("rss", "browse.entries"),
    ]
    assert all(item.protocol_version == PROTOCOL_VERSION for item in capabilities)
    assert all(item.backend_id == "feedparser" for item in capabilities)
    assert all(item.backend_version == "6.0.12" for item in capabilities)
    assert all(
        item.required_host_capabilities == (FETCHED_DOCUMENT_CAPABILITY,) for item in capabilities
    )
    assert [item.maximum_items for item in capabilities] == [1, 21]
    assert all(item.maximum_document_bytes == 1_048_576 for item in capabilities)
    assert all(item.maximum_output_bytes == 1_048_576 for item in capabilities)


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
assert len(list_capabilities()) == 2
assert 'feedparser' not in sys.modules
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
    with pytest.raises(ValueError):
        ExecutionLimitsV1(maximum_items=22)

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
    ],
)
def test_dispatch_rejects_unknown_authority_before_backend_import(
    monkeypatch: pytest.MonkeyPatch,
    execution_request: ExecutionRequestV1,
    context: ExecutionContextV1,
    error_code: str,
) -> None:
    def unexpected_import(name: str, *_: object, **__: object) -> object:
        raise AssertionError(f"rejected request imported {name}")

    monkeypatch.setattr("importlib.import_module", unexpected_import)

    result = execute(execution_request, context)

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == error_code
    assert result.backend_id is None


def test_host_cancellation_propagates_without_backend_execution() -> None:
    def cancelled() -> None:
        raise asyncio.CancelledError

    request = ExecutionRequestV1(PROTOCOL_VERSION, "rss", "read.feed")
    context = ExecutionContextV1((_document(),), checkpoint=cancelled)

    with pytest.raises(asyncio.CancelledError):
        execute(request, context)


def test_top_level_agent_reach_export_remains_compatible() -> None:
    from agent_reach import AgentReach
    from agent_reach.core import AgentReach as CoreAgentReach

    assert AgentReach is CoreAgentReach
