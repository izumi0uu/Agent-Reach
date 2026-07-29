"""Offline RSS execution tests over host-fetched bounded bytes."""

from __future__ import annotations

import builtins
import socket
import subprocess
import urllib.request
from io import BytesIO
from types import SimpleNamespace

import feedparser.http
import pytest

import agent_reach.execution.v1.rss as rss_execution
from agent_reach.execution.v1 import (
    PROTOCOL_VERSION,
    ExecutionContextV1,
    ExecutionFailureV1,
    ExecutionLimitsV1,
    ExecutionRequestV1,
    ExecutionSuccessV1,
    FetchedDocumentV1,
    execute,
)

FEED_URL = "https://example.com/feed.xml"
ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example feed</title><subtitle>Feed body</subtitle>
  <entry><id>entry-1</id><title>Entry title</title>
    <content type="html">&lt;p&gt;Preferred body&lt;/p&gt;</content>
    <summary>Fallback body</summary><author><name>Alice</name></author>
    <link href="/entry?tracking=yes" />
    <updated>2026-07-27T01:02:03Z</updated>
  </entry>
</feed>"""


def _context(
    body: bytes = ATOM,
    content_type: str = "application/atom+xml",
    *,
    checkpoint=lambda: None,
) -> ExecutionContextV1:
    return ExecutionContextV1(
        (FetchedDocumentV1(body, content_type, FEED_URL),),
        checkpoint=checkpoint,
    )


def test_read_and_browse_project_closed_source_native_schemas() -> None:
    read = execute(
        ExecutionRequestV1(PROTOCOL_VERSION, "rss", "read.feed"),
        _context(),
    )
    browse = execute(
        ExecutionRequestV1(
            PROTOCOL_VERSION,
            "rss",
            "browse.entries",
            {"max_entries": 2},
        ),
        _context(),
    )

    assert isinstance(read, ExecutionSuccessV1)
    assert read.backend_id == "feedparser"
    assert read.backend_version == "6.0.12"
    assert read.items[0].schema_id == "rss.feed.v1"
    assert read.items[0].fields == {
        "text": "Feed body",
        "title": "Example feed",
        "url": None,
    }
    assert isinstance(browse, ExecutionSuccessV1)
    assert browse.items[0].schema_id == "rss.entry.v1"
    assert browse.items[0].fields["text"] == "<p>Preferred body</p>"
    assert browse.items[0].fields["author"] == "Alice"
    assert browse.items[0].fields["url"] == "https://example.com/entry"


def test_feedparser_receives_bytesio_without_network_or_file_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[object] = []
    real_parse = __import__("feedparser").parse

    def parse(value: object, **kwargs: object) -> object:
        seen.append(value)
        assert type(value) is BytesIO
        assert kwargs == {
            "response_headers": {
                "content-location": FEED_URL,
                "content-type": "application/atom+xml",
            },
            "resolve_relative_uris": True,
            "sanitize_html": True,
        }
        return real_parse(value, **kwargs)

    def unexpected_io(*_: object, **__: object) -> object:
        raise AssertionError("RSS execution attempted ambient I/O")

    monkeypatch.setattr(builtins, "open", unexpected_io)
    monkeypatch.setattr(feedparser.http, "get", unexpected_io)
    monkeypatch.setattr(socket, "create_connection", unexpected_io)
    monkeypatch.setattr(urllib.request, "urlopen", unexpected_io)
    monkeypatch.setattr(subprocess, "Popen", unexpected_io)
    monkeypatch.setattr(subprocess, "run", unexpected_io)
    monkeypatch.setattr(
        rss_execution.importlib,
        "import_module",
        lambda name: (
            SimpleNamespace(__version__="6.0.12", parse=parse)
            if name == "feedparser"
            else unexpected_io(name)
        ),
    )

    result = execute(
        ExecutionRequestV1(PROTOCOL_VERSION, "rss", "read.feed"),
        _context(),
    )

    assert isinstance(result, ExecutionSuccessV1)
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("backend", "error_code"),
    [
        (ImportError("missing"), "backend_unavailable"),
        (SimpleNamespace(__version__="6.0.11", parse=lambda _: None), "backend_incompatible"),
        (SimpleNamespace(__version__="6.0.12", parse=None), "backend_incompatible"),
    ],
)
def test_backend_absence_and_drift_fail_with_closed_provenance(
    monkeypatch: pytest.MonkeyPatch,
    backend: object,
    error_code: str,
) -> None:
    def load(_: str) -> object:
        if isinstance(backend, BaseException):
            raise backend
        return backend

    monkeypatch.setattr(rss_execution.importlib, "import_module", load)

    result = execute(
        ExecutionRequestV1(PROTOCOL_VERSION, "rss", "read.feed"),
        _context(),
    )

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == error_code
    assert result.backend_id == "feedparser"
    assert result.backend_version == "6.0.12"
    assert not hasattr(result, "message")


def test_version_is_validated_before_parser_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def parse(*_: object, **__: object) -> object:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(
        rss_execution.importlib,
        "import_module",
        lambda _: SimpleNamespace(__version__="6.0.11", parse=parse),
    )

    result = execute(
        ExecutionRequestV1(PROTOCOL_VERSION, "rss", "read.feed"),
        _context(),
    )

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_incompatible"
    assert called is False


def test_bozo_recovery_is_partial_and_unusable_data_fails_closed() -> None:
    recovered = (
        b"<rss><channel><title>Feed</title><item><title>Recovered</title>"
        b"<description>Recovered body</description></item>"
    )
    partial = execute(
        ExecutionRequestV1(
            PROTOCOL_VERSION,
            "rss",
            "browse.entries",
            {"max_entries": 2},
        ),
        _context(recovered),
    )
    failed = execute(
        ExecutionRequestV1(
            PROTOCOL_VERSION,
            "rss",
            "browse.entries",
            {"max_entries": 2},
        ),
        _context(b"<not-feed"),
    )

    assert isinstance(partial, ExecutionSuccessV1)
    assert partial.partial_error_code == "permanent"
    assert partial.items[0].fields["title"] == "Recovered"
    assert isinstance(failed, ExecutionFailureV1)
    assert failed.error_code == "permanent"

    link_only = execute(
        ExecutionRequestV1(
            PROTOCOL_VERSION,
            "rss",
            "browse.entries",
            {"max_entries": 2},
        ),
        _context(
            b"<rss><channel><title>Feed</title><item>"
            b"<link>https://example.com/link-only</link>"
            b"</item></channel></rss>"
        ),
    )
    assert isinstance(link_only, ExecutionFailureV1)
    assert link_only.error_code == "permanent"


def test_rss_1_projection_preserves_source_order() -> None:
    body = b"""<?xml version="1.0" encoding="utf-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns="http://purl.org/rss/1.0/">
  <channel><title>RSS 1 feed</title><link>https://example.com/</link></channel>
  <item><title>First</title><description>First body</description></item>
  <item><title>Second</title><description>Second body</description></item>
</rdf:RDF>"""

    result = execute(
        ExecutionRequestV1(
            PROTOCOL_VERSION,
            "rss",
            "browse.entries",
            {"max_entries": 2},
        ),
        _context(body),
    )

    assert isinstance(result, ExecutionSuccessV1)
    assert [(item.fields["title"], item.fields["text"]) for item in result.items] == [
        ("First", "First body"),
        ("Second", "Second body"),
    ]


def test_browse_enforces_host_limit_and_reports_truncation() -> None:
    entries = b"".join(f"<item><title>{index}</title></item>".encode() for index in range(3))
    body = b"<rss><channel><title>Feed</title>" + entries + b"</channel></rss>"

    result = execute(
        ExecutionRequestV1(
            PROTOCOL_VERSION,
            "rss",
            "browse.entries",
            {"max_entries": 3},
        ),
        ExecutionContextV1(
            (FetchedDocumentV1(body, "application/rss+xml", FEED_URL),),
            limits=ExecutionLimitsV1(maximum_items=2),
        ),
    )

    assert isinstance(result, ExecutionSuccessV1)
    assert len(result.items) == 2
    assert result.truncated is True


def test_browse_accepts_the_twenty_first_overflow_sentinel() -> None:
    entries = b"".join(f"<item><title>{index}</title></item>".encode() for index in range(22))
    body = b"<rss><channel><title>Feed</title>" + entries + b"</channel></rss>"

    result = execute(
        ExecutionRequestV1(
            PROTOCOL_VERSION,
            "rss",
            "browse.entries",
            {"max_entries": 21},
        ),
        _context(body),
    )

    assert isinstance(result, ExecutionSuccessV1)
    assert len(result.items) == 21
    assert result.truncated is True


def test_empty_content_type_is_forwarded_without_inventing_a_header_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_parse = __import__("feedparser").parse

    def parse(value: object, **kwargs: object) -> object:
        assert kwargs["response_headers"] == {
            "content-location": FEED_URL,
            "content-type": "",
        }
        return real_parse(value, **kwargs)

    monkeypatch.setattr(
        rss_execution.importlib,
        "import_module",
        lambda _: SimpleNamespace(__version__="6.0.12", parse=parse),
    )

    result = execute(
        ExecutionRequestV1(PROTOCOL_VERSION, "rss", "read.feed"),
        _context(content_type=""),
    )

    assert isinstance(result, ExecutionSuccessV1)


def test_late_checkpoint_cancellation_propagates_before_parser_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HostCancelled(Exception):
        pass

    checks = 0
    parser_called = False

    def checkpoint() -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise HostCancelled("host cancellation")

    def parse(*_: object, **__: object) -> object:
        nonlocal parser_called
        parser_called = True
        return {}

    monkeypatch.setattr(
        rss_execution.importlib,
        "import_module",
        lambda _: SimpleNamespace(__version__="6.0.12", parse=parse),
    )

    with pytest.raises(HostCancelled):
        execute(
            ExecutionRequestV1(PROTOCOL_VERSION, "rss", "read.feed"),
            _context(checkpoint=checkpoint),
        )

    assert parser_called is False


def test_backend_exception_text_is_not_exposed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def parse(*_: object, **__: object) -> object:
        raise RuntimeError("Cookie=private /Users/private?token=signed")

    monkeypatch.setattr(
        rss_execution.importlib,
        "import_module",
        lambda _: SimpleNamespace(__version__="6.0.12", parse=parse),
    )

    result = execute(
        ExecutionRequestV1(PROTOCOL_VERSION, "rss", "read.feed"),
        _context(),
    )

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "permanent"
    assert not hasattr(result, "message")
