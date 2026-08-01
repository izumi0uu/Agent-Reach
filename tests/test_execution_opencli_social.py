"""Hermetic security and contract tests for OpenCLI social execution."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

import pytest
import yaml

import agent_reach.execution.v1.opencli_social as opencli
from agent_reach.execution.v1 import (
    PROTOCOL_VERSION,
    ExecutionContextV1,
    ExecutionFailureV1,
    ExecutionLimitsV1,
    ExecutionRequestV1,
    ExecutionSuccessV1,
    NetworkAccessV1,
    OpenCliSessionV1,
    execute,
)

QUERY_CANARY = "secret-query-canary"
PATH_CANARY = "/private/user/session/canary"
POST_URL = "https://www.reddit.com/r/Python/comments/abc123/example/"


class _AttestationCancelled(BaseException):
    pass


class _AttestationDeadline(BaseException):
    pass


@dataclass(frozen=True)
class _Closure:
    root: Path
    cli: Path
    node: Path
    session_home: Path

    @property
    def package_root(self) -> Path:
        return self.root / opencli._PACKAGE_ROOT

    def capability(self) -> OpenCliSessionV1:
        return OpenCliSessionV1(
            node_executable=str(self.node),
            node_sha256=opencli._file_sha256(
                self.node,
                maximum_bytes=opencli._MAX_NODE_BYTES,
                executable=True,
            ),
            opencli_root=str(self.root),
            opencli_cli=str(self.cli),
            opencli_tree_sha256=opencli._tree_sha256(self.root),
            session_home=str(self.session_home),
        )


@pytest.fixture
def closure(tmp_path: Path) -> _Closure:
    base = tmp_path.resolve()
    root = base / "opencli-closure"
    package_root = root / opencli._PACKAGE_ROOT
    cli = root / opencli._PACKAGE_CLI
    cli.parent.mkdir(parents=True)
    cli.write_text("raise SystemExit('fixture process should be intercepted')\n", encoding="utf-8")
    (package_root / "package.json").write_text(
        json.dumps(
            {
                "name": "@jackwener/opencli",
                "version": "1.8.6-hermes.1",
                "bin": {"opencli": "dist/src/main.js"},
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    node = base / "node"
    source_executable = Path(sys.executable).resolve(strict=True)
    _write_executable_launcher(node, source_executable)
    session_home = base / "trusted-session"
    session_home.mkdir()
    return _Closure(root=root, cli=cli, node=node, session_home=session_home)


def _write_executable_launcher(path: Path, executable: Path) -> None:
    path.write_text(
        f'#!/bin/sh\nexec {shlex.quote(str(executable))} "$@"\n',
        encoding="utf-8",
    )
    path.chmod(0o755)


def _context(
    session: OpenCliSessionV1,
    *,
    checkpoint: Callable[[], None] = lambda: None,
    maximum_items: int = 50,
    maximum_text: int = 16_000,
) -> ExecutionContextV1:
    return ExecutionContextV1(
        (session,),
        checkpoint=checkpoint,
        limits=ExecutionLimitsV1(
            maximum_items=maximum_items,
            maximum_text_characters=maximum_text,
        ),
    )


def _request(source: str, operation: str, arguments: Mapping[str, object]) -> ExecutionRequestV1:
    return ExecutionRequestV1(
        PROTOCOL_VERSION,
        source,
        operation,
        cast(Mapping[str, str | int | bool | None], arguments),
    )


def _yaml(value: object) -> bytes:
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False).encode("utf-8")


def _assert_failure(result: object, code: str) -> ExecutionFailureV1:
    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == code
    assert result.backend_id == "opencli"
    assert result.backend_version == "1.8.6-hermes.1"
    assert QUERY_CANARY not in repr(result)
    assert PATH_CANARY not in repr(result)
    return result


def test_final_opencli_owner_fork_provenance_and_install_layout_are_frozen() -> None:
    assert opencli._BACKEND_VERSION == "1.8.6-hermes.1"
    assert opencli._PACKAGE_ROOT == Path("node_modules/@jackwener/opencli")
    assert opencli._PACKAGE_CLI == Path("node_modules/@jackwener/opencli/dist/src/main.js")
    assert opencli._OPENCLI_OFFICIAL_BASE_COMMIT == ("399c0de2a76eb979aee3a3836cf2d24fd247780f")
    assert opencli._OPENCLI_SOURCE_COMMIT == "9b0ec22faeff186d53836c14f39cbf5cdddfca55"
    assert opencli._OPENCLI_SOURCE_TREE == "fc3e59294a5b06e7e236fb21c8c4a80b7749ed50"
    assert opencli._OPENCLI_TARBALL_SHA256 == (
        "eebe99d2e848927edaa8b10d6edbfaec088a9b4bdd06436be7556601fb1be2a4"
    )
    assert opencli._OPENCLI_TARBALL_SHA512 == (
        "hhVlYQ9LUtxoP1Y7IfnZxTjOxfGf0IbdJrHYpBmtxfwophmlAJ7xXPFYax+FFL5ZmBCfSppPfZ061aL1Cb2shg=="
    )


def _media_fields() -> dict[str, object]:
    return {
        "post_hint": "self",
        "url_overridden_by_dest": None,
        "preview_image_url": "",
        "gallery_urls": [],
    }


@dataclass(frozen=True)
class _OperationCase:
    source: str
    operation: str
    arguments: Mapping[str, object]
    argv: tuple[str, ...]
    rows: tuple[Mapping[str, object], ...]
    schema_ids: tuple[str, ...]
    fields: tuple[Mapping[str, object], ...]


def _operation_cases() -> tuple[_OperationCase, ...]:
    reddit_post = {
        "id": "abc123",
        "title": "  Post   title ",
        "subreddit": "r/Python",
        "author": " alice ",
        "score": 42,
        "comments": "7",
        "url": POST_URL,
        "created_utc": 1_700_000_000,
        "selftext": " Body\n text ",
        **_media_fields(),
    }
    reddit_post_fields = {
        "text": "Body text",
        "native_id": "abc123",
        "title": "Post title",
        "url": POST_URL,
        "author": "alice",
        "published_at": "2023-11-14T22:13:20+00:00",
        "score": 42,
        "comment_count": 7,
        "subreddit": "Python",
        "media_type": "self",
    }
    thread_rows = (
        {
            "type": "POST",
            "author": "alice",
            "score": 12,
            "text": " Thread   title ",
            **_media_fields(),
        },
        {
            "type": "L1",
            "author": "bob",
            "score": "3",
            "text": " First\n reply ",
            **_media_fields(),
        },
    )
    subreddit_rows = tuple(
        {"field": field, "value": value}
        for field, value in (
            ("Name", "r/Python"),
            ("Title", " Python Community "),
            ("Subscribers", "12,345"),
            ("Active Now", 321),
            ("NSFW", "No"),
            ("Type", "public"),
            ("Description", " Learn\n Python "),
            ("Created", "2008-01-01"),
            ("URL", "https://www.reddit.com/r/Python/"),
        )
    )
    return (
        _OperationCase(
            "reddit",
            "search.posts",
            {"query": QUERY_CANARY, "limit": 3},
            ("reddit", "search", QUERY_CANARY, "--limit", "3"),
            (reddit_post,),
            ("reddit.post.v1",),
            (reddit_post_fields,),
        ),
        _OperationCase(
            "reddit",
            "read.post",
            {"url": POST_URL},
            (
                "reddit",
                "read",
                "abc123",
                "--sort",
                "best",
                "--limit",
                "3",
                "--depth",
                "2",
                "--replies",
                "2",
                "--max-length",
                "800",
            ),
            thread_rows,
            ("reddit.thread.item.v1", "reddit.thread.item.v1"),
            (
                {
                    "text": "Thread title",
                    "native_id": "abc123",
                    "title": "Thread title",
                    "url": POST_URL,
                    "author": "alice",
                    "score": 12,
                    "kind": "post",
                    "media_type": "self",
                },
                {
                    "text": "First reply",
                    "native_id": None,
                    "title": None,
                    "url": None,
                    "author": "bob",
                    "score": 3,
                    "kind": "comment",
                    "media_type": None,
                },
            ),
        ),
        _OperationCase(
            "reddit",
            "browse.subreddit",
            {"subreddit": "Python", "limit": 3},
            (
                "reddit",
                "subreddit",
                "Python",
                "--sort",
                "hot",
                "--time",
                "all",
                "--limit",
                "3",
            ),
            (
                {key: value for key, value in reddit_post.items() if key != "score"}
                | {"upvotes": 42},
            ),
            ("reddit.post.v1",),
            (reddit_post_fields,),
        ),
        _OperationCase(
            "reddit",
            "browse.hot",
            {"limit": 3},
            ("reddit", "hot", "--limit", "3"),
            (
                {
                    "rank": 1,
                    "title": "Post title",
                    "subreddit": "Python",
                    "score": 42,
                    "comments": 7,
                    "postId": "abc123",
                    "author": "alice",
                    "url": POST_URL,
                    **_media_fields(),
                },
            ),
            ("reddit.post.v1",),
            ({**reddit_post_fields, "text": None, "published_at": None},),
        ),
        _OperationCase(
            "reddit",
            "browse.popular",
            {"limit": 3},
            ("reddit", "popular", "--limit", "3"),
            ({**reddit_post, "rank": 1},),
            ("reddit.post.v1",),
            (reddit_post_fields,),
        ),
        _OperationCase(
            "reddit",
            "browse.all",
            {"limit": 3},
            ("reddit", "frontpage", "--limit", "3"),
            (
                {
                    "title": "Post title",
                    "subreddit": "Python",
                    "author": "alice",
                    "upvotes": 42,
                    "comments": 7,
                    "url": POST_URL,
                    **_media_fields(),
                },
            ),
            ("reddit.post.v1",),
            ({**reddit_post_fields, "text": None, "published_at": None},),
        ),
        _OperationCase(
            "reddit",
            "read.subreddit",
            {"subreddit": "Python"},
            ("reddit", "subreddit-info", "Python"),
            subreddit_rows,
            ("reddit.subreddit.v1",),
            (
                {
                    "text": "Learn Python",
                    "native_id": "Python",
                    "title": "Python Community",
                    "url": "https://www.reddit.com/r/Python/",
                    "published_at": "2008-01-01",
                    "subscriber_count": 12_345,
                    "active_count": 321,
                    "nsfw": 0,
                    "subreddit_type": "public",
                },
            ),
        ),
        _OperationCase(
            "facebook",
            "search",
            {"query": QUERY_CANARY, "limit": 3},
            ("facebook", "search", QUERY_CANARY, "--limit", "3"),
            (
                {
                    "index": 1,
                    "title": " Result   title ",
                    "text": " Result\n text ",
                    "url": "https://www.facebook.com/result.one",
                },
            ),
            ("facebook.search.result.v1",),
            (
                {
                    "text": "Result text",
                    "native_id": "1",
                    "title": "Result title",
                    "url": "https://www.facebook.com/result.one",
                },
            ),
        ),
        _OperationCase(
            "facebook",
            "read.profile",
            {"username": "alice"},
            ("facebook", "profile", "alice"),
            (
                {
                    "name": " Alice Example ",
                    "username": "Alice",
                    "friends": "1.2K friends",
                    "followers": "2M followers",
                    "url": "https://www.facebook.com/alice",
                },
            ),
            ("facebook.profile.v1",),
            (
                {
                    "text": None,
                    "native_id": "Alice",
                    "title": "Alice Example",
                    "url": "https://www.facebook.com/alice",
                    "friend_count": 1_200,
                    "follower_count": 2_000_000,
                },
            ),
        ),
        _OperationCase(
            "facebook",
            "browse.feed",
            {"limit": 3},
            ("facebook", "feed", "--limit", "3"),
            (
                {
                    "index": 1,
                    "author": " Alice ",
                    "content": " Feed\n content ",
                    "likes": "1.5K",
                    "comments": 2,
                    "shares": "3",
                },
            ),
            ("facebook.post.v1",),
            (
                {
                    "text": "Feed content",
                    "native_id": "1",
                    "author": "Alice",
                    "reaction_count": 1_500,
                    "comment_count": 2,
                    "share_count": 3,
                },
            ),
        ),
        _OperationCase(
            "facebook",
            "browse.groups",
            {"limit": 3},
            ("facebook", "groups", "--limit", "3"),
            (
                {
                    "index": 1,
                    "name": " Python Group ",
                    "last_post": " Last\n post ",
                    "url": "https://www.facebook.com/groups/python",
                },
            ),
            ("facebook.group.v1",),
            (
                {
                    "text": "Last post",
                    "native_id": "1",
                    "title": "Python Group",
                    "url": "https://www.facebook.com/groups/python",
                },
            ),
        ),
        _OperationCase(
            "instagram",
            "search.users",
            {"query": QUERY_CANARY, "limit": 3},
            ("instagram", "search", QUERY_CANARY, "--limit", "3"),
            (
                {
                    "rank": 1,
                    "username": "alice.dev",
                    "name": " Alice Dev ",
                    "verified": "Yes",
                    "private": False,
                    "url": "https://www.instagram.com/alice.dev/",
                },
            ),
            ("instagram.user.v1",),
            (
                {
                    "native_id": "alice.dev",
                    "title": "Alice Dev",
                    "url": "https://www.instagram.com/alice.dev/",
                    "verified": 1,
                    "private": 0,
                },
            ),
        ),
        _OperationCase(
            "instagram",
            "read.profile",
            {"username": "alice.dev"},
            ("instagram", "profile", "alice.dev"),
            (
                {
                    "username": "Alice.Dev",
                    "name": " Alice Dev ",
                    "followers": "12,345",
                    "following": 120,
                    "posts": 88,
                    "verified": True,
                    "bio": " Builder\n bio ",
                },
            ),
            ("instagram.profile.v1",),
            (
                {
                    "text": "Builder bio",
                    "native_id": "Alice.Dev",
                    "title": "Alice Dev",
                    "url": "https://www.instagram.com/Alice.Dev/",
                    "follower_count": 12_345,
                    "following_count": 120,
                    "post_count": 88,
                    "verified": 1,
                },
            ),
        ),
        _OperationCase(
            "instagram",
            "browse.user_posts",
            {"username": "alice.dev", "limit": 3},
            ("instagram", "user", "alice.dev", "--limit", "3"),
            (
                {
                    "index": 1,
                    "caption": " Post\n caption ",
                    "likes": 10,
                    "comments": "2",
                    "type": "image",
                    "date": "2026-08-01",
                },
            ),
            ("instagram.post.v1",),
            (
                {
                    "text": "Post caption",
                    "native_id": "1",
                    "author": "alice.dev",
                    "published_at": "2026-08-01",
                    "reaction_count": 10,
                    "comment_count": 2,
                    "media_type": "image",
                },
            ),
        ),
        _OperationCase(
            "instagram",
            "browse.explore",
            {"limit": 3},
            ("instagram", "explore", "--limit", "3"),
            (
                {
                    "rank": 1,
                    "user": "alice.dev",
                    "caption": " Explore\n caption ",
                    "likes": "10",
                    "comments": 2,
                    "type": "reel",
                },
            ),
            ("instagram.post.v1",),
            (
                {
                    "text": "Explore caption",
                    "native_id": "1",
                    "author": "alice.dev",
                    "published_at": None,
                    "reaction_count": 10,
                    "comment_count": 2,
                    "media_type": "reel",
                },
            ),
        ),
    )


@pytest.mark.parametrize(
    "case",
    _operation_cases(),
    ids=lambda case: f"{case.source}-{case.operation}",
)
def test_all_fifteen_operations_use_fixed_argv_and_closed_projection(
    case: _OperationCase,
    closure: _Closure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], Mapping[str, str], Path]] = []

    def run(
        argv: tuple[str, ...],
        environment: Mapping[str, str],
        cwd: Path,
        context: ExecutionContextV1,
        _deadline: float,
    ) -> tuple[int, bytes, bytes]:
        context.checkpoint()
        assert cwd == Path(environment["HOME"])
        assert set(environment) == {
            "HOME",
            "USERPROFILE",
            "XDG_CONFIG_HOME",
            "TMPDIR",
            "OPENCLI_CONFIG_DIR",
            "NODE_OPTIONS",
            "CI",
            "LANG",
            "LC_ALL",
            "TZ",
            "NO_COLOR",
        }
        assert environment["OPENCLI_CONFIG_DIR"] == str(closure.session_home / ".opencli")
        snapshot_root = Path(argv[0]).parent
        assert argv[0] != str(closure.node)
        assert argv[1] != str(closure.cli)
        assert Path(argv[0]).read_bytes() == closure.node.read_bytes()
        assert Path(argv[1]).relative_to(snapshot_root) == (Path("opencli") / opencli._PACKAGE_CLI)
        assert environment["NODE_OPTIONS"] == (
            f"--import={(snapshot_root / 'opencli-no-lifecycle.mjs').as_uri()}"
        )
        calls.append((argv, environment, cwd))
        return 0, _yaml(case.rows), b""

    monkeypatch.setattr(opencli, "_run_process", run)
    monkeypatch.chdir(closure.root.parent)

    result = execute(
        _request(case.source, case.operation, case.arguments),
        _context(closure.capability()),
    )

    assert isinstance(result, ExecutionSuccessV1)
    assert result.source == case.source
    assert result.operation == case.operation
    assert result.backend_id == "opencli"
    assert result.backend_version == "1.8.6-hermes.1"
    assert tuple(item.schema_id for item in result.items) == case.schema_ids
    assert tuple(dict(item.fields) for item in result.items) == case.fields
    assert result.truncated is False
    assert len(calls) == 1
    argv, environment, _ = calls[0]
    snapshot_root = Path(argv[0]).parent
    assert argv[0] != str(closure.node)
    assert argv[1] != str(closure.cli)
    assert Path(argv[1]).relative_to(snapshot_root) == Path("opencli") / opencli._PACKAGE_CLI
    assert argv[2:] == (*case.argv, "--format", "yaml")
    assert environment["NODE_OPTIONS"] == (
        f"--import={(snapshot_root / 'opencli-no-lifecycle.mjs').as_uri()}"
    )


@pytest.mark.parametrize(
    ("source", "operation", "arguments"),
    [
        ("reddit", "search.posts", {"query": QUERY_CANARY}),
        ("reddit", "read.post", {"url": "https://evil.test/comments/abc123"}),
        ("reddit", "browse.subreddit", {"subreddit": "r/Python", "limit": 1}),
        ("reddit", "browse.hot", {"limit": 0}),
        ("reddit", "browse.popular", {"limit": True}),
        ("reddit", "browse.all", {"limit": 1, "backend": "opencli"}),
        ("reddit", "read.subreddit", {"subreddit": "ab"}),
        ("facebook", "search", {"query": " padded ", "limit": 1}),
        ("facebook", "read.profile", {"username": "@alice"}),
        ("facebook", "browse.feed", {"limit": 51}),
        ("facebook", "browse.groups", {"limit": "2"}),
        ("instagram", "search.users", {"query": "", "limit": 1}),
        ("instagram", "read.profile", {"username": "alice dev"}),
        ("instagram", "browse.user_posts", {"username": "@alice", "limit": 1}),
        ("instagram", "browse.explore", {"limit": 1, "scope": "account_visible"}),
    ],
)
def test_each_operation_rejects_non_closed_arguments_before_backend_import(
    source: str,
    operation: str,
    arguments: Mapping[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawns = 0

    def unexpected_spawn(*_: object, **__: object) -> object:
        nonlocal spawns
        spawns += 1
        raise AssertionError("invalid request reached backend process")

    monkeypatch.setattr(opencli.subprocess, "Popen", unexpected_spawn)
    result = execute(
        _request(source, operation, arguments),
        ExecutionContextV1((NetworkAccessV1(),)),
    )

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "invalid_request"
    assert result.backend_id is None
    assert spawns == 0


def test_opencli_capability_is_exact_and_caller_limits_only_narrow(
    closure: _Closure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request("facebook", "browse.feed", {"limit": 10})
    no_capability = execute(request, ExecutionContextV1())
    wrong_capability = execute(request, ExecutionContextV1((NetworkAccessV1(),)))
    extra_capability = execute(
        request,
        ExecutionContextV1((closure.capability(), NetworkAccessV1())),
    )
    observed_argv: tuple[str, ...] | None = None

    def run(
        argv: tuple[str, ...],
        _environment: Mapping[str, str],
        _cwd: Path,
        _context: ExecutionContextV1,
        _deadline: float,
    ) -> tuple[int, bytes, bytes]:
        nonlocal observed_argv
        observed_argv = argv
        rows = [
            {
                "index": index,
                "author": "alice",
                "content": f"post {index}",
                "likes": 1,
                "comments": 2,
                "shares": 3,
            }
            for index in range(1, 4)
        ]
        return 0, _yaml(rows), b""

    monkeypatch.setattr(opencli, "_run_process", run)
    monkeypatch.chdir(closure.root.parent)
    narrowed = execute(
        request,
        _context(closure.capability(), maximum_items=2),
    )

    assert isinstance(no_capability, ExecutionFailureV1)
    assert no_capability.error_code == "host_capability_missing"
    assert isinstance(wrong_capability, ExecutionFailureV1)
    assert wrong_capability.error_code == "invalid_request"
    assert isinstance(extra_capability, ExecutionFailureV1)
    assert extra_capability.error_code == "invalid_request"
    assert isinstance(narrowed, ExecutionSuccessV1)
    assert len(narrowed.items) == 2
    assert narrowed.truncated is True
    assert observed_argv is not None
    assert observed_argv[-4:] == ("--limit", "2", "--format", "yaml")


@pytest.mark.parametrize(
    ("source", "operation", "arguments", "rows"),
    [
        (
            "reddit",
            "search.posts",
            {"query": QUERY_CANARY, "limit": 1},
            (
                {
                    "id": "different",
                    "title": "title",
                    "subreddit": "Python",
                    "author": "alice",
                    "score": 1,
                    "comments": 1,
                    "url": POST_URL,
                    "created_utc": 1_700_000_000,
                    "selftext": "body",
                    **_media_fields(),
                },
            ),
        ),
        (
            "reddit",
            "read.subreddit",
            {"subreddit": "Python"},
            tuple(
                {"field": field, "value": value}
                for field, value in (
                    ("Name", "r/Rust"),
                    ("Title", "Rust"),
                    ("Subscribers", 1),
                    ("Active Now", 1),
                    ("NSFW", "No"),
                    ("Type", "public"),
                    ("Description", "body"),
                    ("Created", "2008"),
                    ("URL", "https://www.reddit.com/r/Rust/"),
                )
            ),
        ),
        (
            "reddit",
            "browse.subreddit",
            {"subreddit": "Python", "limit": 1},
            (
                {
                    "id": "abc123",
                    "title": "title",
                    "subreddit": "r/Rust",
                    "author": "alice",
                    "upvotes": 1,
                    "comments": 1,
                    "url": "https://www.reddit.com/r/Rust/comments/abc123/example/",
                    "created_utc": 1_700_000_000,
                    "selftext": "body",
                    **_media_fields(),
                },
            ),
        ),
        (
            "facebook",
            "read.profile",
            {"username": "alice"},
            (
                {
                    "name": "Mallory",
                    "username": "mallory",
                    "friends": 1,
                    "followers": 1,
                    "url": "https://www.facebook.com/mallory",
                },
            ),
        ),
        (
            "instagram",
            "read.profile",
            {"username": "alice"},
            (
                {
                    "username": "mallory",
                    "name": "Mallory",
                    "followers": 1,
                    "following": 1,
                    "posts": 1,
                    "verified": False,
                    "bio": "bio",
                },
            ),
        ),
    ],
)
def test_request_and_result_identity_must_correlate(
    source: str,
    operation: str,
    arguments: Mapping[str, object],
    rows: tuple[Mapping[str, object], ...],
    closure: _Closure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        opencli,
        "_run_process",
        lambda *_args, **_kwargs: (0, _yaml(rows), b""),
    )
    monkeypatch.chdir(closure.root.parent)

    result = execute(
        _request(source, operation, arguments),
        _context(closure.capability()),
    )

    _assert_failure(result, "backend_contract_violation")


@pytest.mark.parametrize(
    ("source", "operation", "ordinal_field"),
    [
        ("reddit", "browse.hot", "rank"),
        ("reddit", "browse.popular", "rank"),
        ("facebook", "search", "index"),
        ("facebook", "browse.feed", "index"),
        ("facebook", "browse.groups", "index"),
        ("instagram", "search.users", "rank"),
        ("instagram", "browse.user_posts", "index"),
        ("instagram", "browse.explore", "rank"),
    ],
)
def test_ranked_and_indexed_results_require_the_complete_one_based_sequence(
    source: str,
    operation: str,
    ordinal_field: str,
    closure: _Closure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = next(
        candidate
        for candidate in _operation_cases()
        if (candidate.source, candidate.operation) == (source, operation)
    )
    row = dict(case.rows[0])
    row[ordinal_field] = 2
    monkeypatch.setattr(
        opencli,
        "_run_process",
        lambda *_args, **_kwargs: (0, _yaml((row,)), b""),
    )
    monkeypatch.chdir(closure.root.parent)

    result = execute(
        _request(source, operation, case.arguments),
        _context(closure.capability()),
    )

    _assert_failure(result, "backend_contract_violation")


@pytest.mark.parametrize(
    ("source", "operation", "ordinal_field"),
    [
        ("facebook", "search", "index"),
        ("instagram", "browse.user_posts", "index"),
    ],
)
def test_duplicate_list_identifiers_fail_closed(
    source: str,
    operation: str,
    ordinal_field: str,
    closure: _Closure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = next(
        candidate
        for candidate in _operation_cases()
        if (candidate.source, candidate.operation) == (source, operation)
    )
    first = dict(case.rows[0])
    second = dict(first)
    first[ordinal_field] = 1
    second[ordinal_field] = 1
    if source == "facebook":
        second["title"] = "different"
        second["url"] = "https://www.facebook.com/different"
    monkeypatch.setattr(
        opencli,
        "_run_process",
        lambda *_args, **_kwargs: (0, _yaml((first, second)), b""),
    )
    monkeypatch.chdir(closure.root.parent)

    result = execute(
        _request(source, operation, case.arguments),
        _context(closure.capability()),
    )

    _assert_failure(result, "backend_contract_violation")


def _deep_value() -> object:
    value: object = "leaf"
    for _ in range(opencli._MAX_YAML_DEPTH + 4):
        value = [value]
    return value


@pytest.mark.parametrize(
    "raw",
    [
        b"- index: 1\n  index: 2\n  title: title\n  text: text\n  url: https://www.facebook.com/a\n",
        b"- &row\n  index: 1\n  title: title\n  text: text\n  url: https://www.facebook.com/a\n- *row\n",
        b"- index: 1\n  title: !secret title\n  text: text\n  url: https://www.facebook.com/a\n",
        json.dumps(
            [
                {
                    "index": 1,
                    "title": "title",
                    "text": _deep_value(),
                    "url": "https://www.facebook.com/a",
                }
            ]
        ).encode(),
        json.dumps(
            [
                {
                    "index": 1,
                    "title": "title",
                    "text": ["node"] * (opencli._MAX_YAML_NODES + 1),
                    "url": "https://www.facebook.com/a",
                }
            ]
        ).encode(),
        b"- index: 1\n  title: [unterminated\n",
        b"- index: 1\n  title: title\n  text: .nan\n  url: https://www.facebook.com/a\n",
        b"\xff\xfe",
    ],
    ids=(
        "duplicate-key",
        "alias",
        "custom-tag",
        "depth-overflow",
        "node-overflow",
        "malformed",
        "non-finite",
        "invalid-utf8",
    ),
)
def test_success_yaml_is_strict_and_bounded(
    raw: bytes,
    closure: _Closure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(opencli, "_run_process", lambda *_args, **_kwargs: (0, raw, b""))
    monkeypatch.chdir(closure.root.parent)

    result = execute(
        _request("facebook", "search", {"query": QUERY_CANARY, "limit": 1}),
        _context(closure.capability()),
    )

    _assert_failure(result, "backend_contract_violation")


@pytest.mark.parametrize(
    ("return_code", "backend_code", "expected"),
    [
        (2, "ARGUMENT", "invalid_input"),
        (66, "EMPTY_RESULT", "not_found"),
        (69, "BROWSER_CONNECT", "backend_unavailable"),
        (69, "ADAPTER_LOAD", "backend_unavailable"),
        (75, "TIMEOUT", "deadline_exceeded"),
        (75, "RATE_LIMIT", "rate_limit"),
        (77, "AUTH_REQUIRED", "authentication"),
        (77, "LOGIN_WALL", "authentication"),
        (78, "CONFIG", "backend_incompatible"),
        (1, "SELECTOR", "backend_contract_violation"),
        (1, "COMMAND_EXEC", "transient"),
        (1, "UNKNOWN", "backend_contract_violation"),
        (1, "PLUGIN", "transient"),
        (130, "COMMAND_EXEC", "transient"),
    ],
)
def test_error_codes_have_one_closed_redacted_mapping(
    return_code: int,
    backend_code: str,
    expected: str,
    closure: _Closure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stderr = _yaml(
        {
            "ok": False,
            "error": {
                "code": backend_code,
                "exitCode": return_code,
                "message": f"{QUERY_CANARY} {PATH_CANARY}",
                "details": {"cookie": "secret-cookie-canary"},
            },
        }
    )
    monkeypatch.setattr(
        opencli,
        "_run_process",
        lambda *_args, **_kwargs: (return_code, b"", stderr),
    )
    monkeypatch.chdir(closure.root.parent)

    result = execute(
        _request("facebook", "search", {"query": QUERY_CANARY, "limit": 1}),
        _context(closure.capability()),
    )

    failure = _assert_failure(result, expected)
    assert "secret-cookie-canary" not in repr(failure)
    assert backend_code not in repr(failure)


@pytest.mark.parametrize(
    ("return_code", "stdout", "stderr"),
    [
        (
            69,
            b"unexpected",
            _yaml({"ok": False, "error": {"code": "BROWSER_CONNECT", "exitCode": 69}}),
        ),
        (69, b"", b""),
        (69, b"", _yaml({"ok": False, "error": {"code": "BROWSER_CONNECT", "exitCode": 77}})),
        (429, b"", _yaml({"ok": False, "error": {"code": "RATE_LIMIT", "exitCode": 429}})),
        (0, b"[]\n", b"stderr-canary"),
    ],
    ids=(
        "stdout-on-error",
        "missing-frame",
        "exit-mismatch",
        "unproven-rate-limit",
        "stderr-on-success",
    ),
)
def test_malformed_or_unproven_error_evidence_fails_closed(
    return_code: int,
    stdout: bytes,
    stderr: bytes,
    closure: _Closure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        opencli,
        "_run_process",
        lambda *_args, **_kwargs: (return_code, stdout, stderr),
    )
    monkeypatch.chdir(closure.root.parent)

    result = execute(
        _request("facebook", "search", {"query": QUERY_CANARY, "limit": 1}),
        _context(closure.capability()),
    )

    _assert_failure(result, "backend_contract_violation")
    assert "stderr-canary" not in repr(result)


def _valid_search_rows() -> bytes:
    return _yaml(
        [
            {
                "index": 1,
                "title": "title",
                "text": "text",
                "url": "https://www.facebook.com/alice",
            }
        ]
    )


def test_node_and_tree_are_revalidated_before_every_execution(
    closure: _Closure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def run(*_args: object, **_kwargs: object) -> tuple[int, bytes, bytes]:
        nonlocal calls
        calls += 1
        return 0, _valid_search_rows(), b""

    monkeypatch.setattr(opencli, "_run_process", run)
    monkeypatch.chdir(closure.root.parent)
    request = _request("facebook", "search", {"query": QUERY_CANARY, "limit": 1})
    node_session = closure.capability()

    assert isinstance(execute(request, _context(node_session)), ExecutionSuccessV1)
    closure.node.write_bytes(closure.node.read_bytes() + b"node-mutation-canary")
    closure.node.chmod(0o755)
    node_failure = execute(request, _context(node_session))

    _assert_failure(node_failure, "backend_incompatible")
    assert calls == 1

    closure.node.write_bytes(closure.node.read_bytes()[: -len(b"node-mutation-canary")])
    closure.node.chmod(0o755)
    tree_session = closure.capability()
    assert isinstance(execute(request, _context(tree_session)), ExecutionSuccessV1)
    (closure.root / "injected-plugin.js").write_text("plugin-mutation-canary", encoding="utf-8")
    tree_failure = execute(request, _context(tree_session))

    _assert_failure(tree_failure, "backend_incompatible")
    assert "plugin-mutation-canary" not in repr(tree_failure)
    assert calls == 2


def test_execution_uses_private_verified_bytes_after_original_paths_are_replaced(
    closure: _Closure,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_node = closure.node.read_bytes()
    original_cli = closure.cli.read_bytes()
    original_guard = opencli._lifecycle_guard_path().read_bytes()
    package = tmp_path.resolve() / "agent_reach" / "execution" / "v1"
    package.mkdir(parents=True)
    fake_runtime = package / "opencli_social.py"
    fake_runtime.write_text("runtime-placeholder", encoding="utf-8")
    fake_guard = package / "_opencli_no_lifecycle.mjs"
    fake_guard.write_bytes(original_guard)
    monkeypatch.setattr(opencli, "__file__", str(fake_runtime))
    session = closure.capability()

    def run(
        argv: tuple[str, ...],
        environment: Mapping[str, str],
        *_args: object,
    ) -> tuple[int, bytes, bytes]:
        closure.node.write_bytes(b"replacement-node-canary")
        closure.cli.write_bytes(b"replacement-cli-canary")
        fake_guard.write_bytes(b"replacement-guard-canary")

        snapshot_node = Path(argv[0])
        snapshot_cli = Path(argv[1])
        snapshot_guard = Path(environment["NODE_OPTIONS"].removeprefix("--import=")[7:])
        assert snapshot_node.read_bytes() == original_node
        assert snapshot_cli.read_bytes() == original_cli
        assert snapshot_guard.read_bytes() == original_guard
        assert "replacement" not in repr((argv, environment))
        return 0, _valid_search_rows(), b""

    monkeypatch.setattr(opencli, "_run_process", run)
    result = execute(
        _request("facebook", "search", {"query": QUERY_CANARY, "limit": 1}),
        _context(session),
    )

    assert isinstance(result, ExecutionSuccessV1)


def test_lifecycle_guard_has_a_frozen_identity_and_rejects_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = opencli._lifecycle_guard_path()
    assert hashlib.sha256(guard.read_bytes()).hexdigest() == opencli._LIFECYCLE_GUARD_SHA256

    package = tmp_path.resolve() / "agent_reach" / "execution" / "v1"
    package.mkdir(parents=True)
    fake_runtime = package / "opencli_social.py"
    fake_runtime.write_text("runtime-placeholder", encoding="utf-8")
    fake_guard = package / "_opencli_no_lifecycle.mjs"
    fake_guard.write_bytes(guard.read_bytes() + b"\n// mutation-canary\n")
    monkeypatch.setattr(opencli, "__file__", str(fake_runtime))

    with pytest.raises(opencli._ArtifactIncompatibleError):
        opencli._lifecycle_guard_path()


def test_streaming_tree_entry_limit_stops_before_late_directory_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path.resolve() / "bounded-tree"
    root.mkdir()
    for index in range(8):
        (root / f"a-{index}.txt").write_text("bounded", encoding="utf-8")
    poison = root / "zz-poison"
    poison.mkdir()
    (poison / "must-not-be-enumerated.txt").write_text("late", encoding="utf-8")
    real_scandir = os.scandir
    poison_scans = 0
    checkpoints = 0

    def guarded_scandir(path: object) -> object:
        nonlocal poison_scans
        if Path(cast(str | os.PathLike[str], path)) == poison:
            poison_scans += 1
            raise AssertionError("entry overflow traversed a late directory")
        return real_scandir(cast(str | os.PathLike[str], path))

    def checkpoint() -> None:
        nonlocal checkpoints
        checkpoints += 1

    monkeypatch.setattr(opencli, "_MAX_TREE_ENTRIES", 4)
    monkeypatch.setattr(opencli.os, "scandir", guarded_scandir)

    with pytest.raises(opencli._ArtifactIncompatibleError):
        opencli._tree_sha256(root, checkpoint=checkpoint)

    assert poison_scans == 0
    assert checkpoints <= opencli._MAX_TREE_ENTRIES + 3


def test_tree_digest_preserves_legacy_global_path_order_without_rglob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path.resolve() / "ordered-tree"
    nested = root / "a-directory"
    nested.mkdir(parents=True)
    (root / "!before-root.txt").write_bytes(b"first")
    (nested / "payload.txt").write_bytes(b"nested")

    legacy = hashlib.sha256(b"agent-reach-opencli-tree-v1\0")
    entries = [root, *root.rglob("*")]
    entries.sort(key=lambda path: path.relative_to(root).as_posix())
    for path in entries:
        relative = path.relative_to(root)
        name = "." if relative == Path(".") else relative.as_posix()
        encoded = name.encode("utf-8")
        metadata = path.lstat()
        legacy.update(b"D" if path.is_dir() else b"F")
        legacy.update(len(encoded).to_bytes(4, "big"))
        legacy.update(encoded)
        legacy.update((metadata.st_mode & 0o7777).to_bytes(2, "big"))
        if path.is_file():
            payload = path.read_bytes()
            legacy.update(len(payload).to_bytes(8, "big"))
            legacy.update(payload)

    def forbidden_rglob(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("tree attestation used eager Path.rglob")

    monkeypatch.setattr(Path, "rglob", forbidden_rglob)

    assert opencli._tree_sha256(root) == legacy.hexdigest()


def test_tree_sorting_has_a_cancellation_checkpoint(tmp_path: Path) -> None:
    root = tmp_path.resolve() / "sorting-tree"
    root.mkdir()
    (root / "b.txt").write_text("b", encoding="utf-8")
    (root / "a.txt").write_text("a", encoding="utf-8")
    checkpoints = 0

    def checkpoint() -> None:
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints == 3:
            raise _AttestationCancelled()

    with pytest.raises(_AttestationCancelled):
        opencli._tree_sha256(root, checkpoint=checkpoint)

    assert checkpoints == 3


@pytest.mark.parametrize("stop", [_AttestationCancelled, _AttestationDeadline])
def test_tree_enumeration_checkpoint_stops_without_mutating_the_tree(
    stop: type[BaseException],
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve() / "enumeration-tree"
    root.mkdir()
    for index in range(12):
        (root / f"directory-{index:02d}").mkdir()
    before = tuple(path.relative_to(root).as_posix() for path in sorted(root.rglob("*")))
    checkpoints = 0

    def checkpoint() -> None:
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints == 4:
            raise stop()

    with pytest.raises(stop) as raised:
        opencli._tree_sha256(root, checkpoint=checkpoint)

    after = tuple(path.relative_to(root).as_posix() for path in sorted(root.rglob("*")))
    assert checkpoints == 4
    assert raised.value.args == ()
    assert before == after
    assert str(root) not in repr(raised.value)


@pytest.mark.parametrize("stop", [_AttestationCancelled, _AttestationDeadline])
def test_tree_large_file_hashing_checkpoints_between_chunks(
    stop: type[BaseException],
    tmp_path: Path,
) -> None:
    small_root = tmp_path.resolve() / "small-tree"
    small_root.mkdir()
    (small_root / "payload.bin").write_bytes(b"x")
    baseline_checkpoints = 0

    def baseline_checkpoint() -> None:
        nonlocal baseline_checkpoints
        baseline_checkpoints += 1

    opencli._tree_sha256(small_root, checkpoint=baseline_checkpoint)

    large_root = tmp_path.resolve() / "large-tree"
    large_root.mkdir()
    large_file = large_root / "payload.bin"
    large_file.write_bytes(b"x" * (4 * 64 * 1_024))
    before = large_file.stat()
    checkpoints = 0

    def checkpoint() -> None:
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints == baseline_checkpoints + 1:
            raise stop()

    with pytest.raises(stop) as raised:
        opencli._tree_sha256(large_root, checkpoint=checkpoint)

    after = large_file.stat()
    assert checkpoints == baseline_checkpoints + 1
    assert raised.value.args == ()
    assert (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    assert str(large_file) not in repr(raised.value)


@pytest.mark.parametrize(
    "package",
    [
        {
            "name": "attacker/opencli",
            "version": "1.8.6-hermes.1",
            "bin": {"opencli": "dist/src/main.js"},
        },
        {
            "name": "@jackwener/opencli",
            "version": "1.8.6",
            "bin": {"opencli": "dist/src/main.js"},
        },
        {
            "name": "@jackwener/opencli",
            "version": "1.8.6-hermes.1",
            "bin": {"opencli": "evil.js"},
        },
    ],
    ids=("name", "version", "entrypoint"),
)
def test_package_identity_is_independent_of_an_operator_supplied_tree_digest(
    package: Mapping[str, object],
    closure: _Closure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (closure.package_root / "package.json").write_text(
        json.dumps(package, separators=(",", ":")),
        encoding="utf-8",
    )
    session = replace(
        closure.capability(),
        opencli_tree_sha256=opencli._tree_sha256(closure.root),
    )
    spawns = 0

    def unexpected_spawn(*_: object, **__: object) -> object:
        nonlocal spawns
        spawns += 1
        raise AssertionError("invalid package reached process creation")

    monkeypatch.setattr(opencli.subprocess, "Popen", unexpected_spawn)
    monkeypatch.chdir(closure.root.parent)

    result = execute(
        _request("facebook", "search", {"query": QUERY_CANARY, "limit": 1}),
        _context(session),
    )

    _assert_failure(result, "backend_incompatible")
    assert spawns == 0


def test_oversized_package_manifest_fails_before_opening_or_reading_it(
    closure: _Closure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_json = closure.package_root / "package.json"
    package_json.write_bytes(b"x" * (opencli._MAX_PACKAGE_JSON_BYTES + 1))
    real_open = Path.open
    opens = 0

    def guarded_open(path: Path, *args: object, **kwargs: object) -> object:
        nonlocal opens
        if path == package_json:
            opens += 1
            raise AssertionError("oversized package.json was opened")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    with pytest.raises(opencli._ArtifactIncompatibleError):
        opencli._validate_package_identity(closure.root, closure.cli)

    assert opens == 0


def test_missing_or_replaced_session_home_fails_before_spawn(
    closure: _Closure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request("facebook", "search", {"query": QUERY_CANARY, "limit": 1})
    missing_session = closure.capability()
    closure.session_home.rmdir()
    missing = execute(request, _context(missing_session))

    _assert_failure(missing, "backend_unavailable")

    replacement = closure.root.parent / "replacement-session"
    replacement.mkdir()
    closure.session_home.symlink_to(replacement, target_is_directory=True)
    replaced_session = replace(missing_session, session_home=str(closure.session_home))
    replaced = execute(request, _context(replaced_session))

    _assert_failure(replaced, "backend_incompatible")
    assert str(replacement) not in repr(replaced)


@pytest.mark.parametrize(
    ("source", "operation", "arguments", "rows"),
    [
        (
            "reddit",
            "search.posts",
            {"query": QUERY_CANARY, "limit": 1},
            (
                {
                    "id": "abc123",
                    "title": "title",
                    "subreddit": "Python",
                    "author": "alice",
                    "score": 1,
                    "comments": 1,
                    "url": POST_URL,
                    "created_utc": 10**400,
                    "selftext": "body",
                    **_media_fields(),
                },
            ),
        ),
        (
            "facebook",
            "browse.feed",
            {"limit": 1},
            (
                {
                    "index": 1,
                    "author": "alice",
                    "content": "content",
                    "likes": f"{'9' * 400}K",
                    "comments": 1,
                    "shares": 1,
                },
            ),
        ),
    ],
    ids=("reddit-timestamp-overflow", "facebook-human-count-overflow"),
)
def test_large_backend_numbers_fail_closed_without_python_exceptions(
    source: str,
    operation: str,
    arguments: Mapping[str, object],
    rows: tuple[Mapping[str, object], ...],
    closure: _Closure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        opencli,
        "_run_process",
        lambda *_args, **_kwargs: (0, _yaml(rows), b""),
    )
    monkeypatch.chdir(closure.root.parent)

    result = execute(
        _request(source, operation, arguments),
        _context(closure.capability()),
    )

    _assert_failure(result, "backend_contract_violation")


@pytest.mark.parametrize(
    ("parser", "value"),
    [
        (opencli._optional_integer, "1,,2"),
        (opencli._optional_integer, "12,34"),
        (opencli._human_count, "1,,2"),
        (opencli._human_count, "12,34"),
        (opencli._human_count, "junk 1.2K friends trailing"),
        (opencli._human_count, "0.0001K"),
    ],
)
def test_backend_numbers_require_closed_full_string_syntax(
    parser: Callable[[object], int | None],
    value: str,
) -> None:
    with pytest.raises(opencli._BackendContractError):
        parser(value)


def test_human_counts_use_exact_decimal_scaling() -> None:
    assert opencli._human_count("1.2K followers") == 1_200
    assert opencli._human_count("0.001K") == 1


@pytest.mark.parametrize(
    "raw",
    [
        (
            b"- index: "
            + (b"9" * 5_000)
            + b"\n  title: title\n  text: text\n  url: https://www.facebook.com/alice\n"
        ),
        b"- index: 1\n  title: title\n  text: 2026-99-99\n  url: https://www.facebook.com/alice\n",
    ],
    ids=("python-integer-digit-limit", "invalid-yaml-timestamp"),
)
def test_yaml_scalar_constructor_failures_are_redacted_contract_failures(
    raw: bytes,
    closure: _Closure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(opencli, "_run_process", lambda *_args, **_kwargs: (0, raw, b""))
    monkeypatch.chdir(closure.root.parent)

    result = execute(
        _request("facebook", "search", {"query": QUERY_CANARY, "limit": 1}),
        _context(closure.capability()),
    )

    _assert_failure(result, "backend_contract_violation")


@pytest.mark.parametrize("error", [ValueError("value-canary"), OverflowError("overflow-canary")])
def test_yaml_loader_constructor_value_and_overflow_errors_never_escape(
    error: Exception,
    closure: _Closure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_constructor_error(_loader: object) -> object:
        raise error

    monkeypatch.setattr(opencli._ClosedYamlLoader, "get_single_data", raise_constructor_error)
    monkeypatch.setattr(
        opencli,
        "_run_process",
        lambda *_args, **_kwargs: (0, b"[]\n", b""),
    )
    monkeypatch.chdir(closure.root.parent)

    result = execute(
        _request("facebook", "search", {"query": QUERY_CANARY, "limit": 1}),
        _context(closure.capability()),
    )

    _assert_failure(result, "backend_contract_violation")
    assert str(error) not in repr(result)


@pytest.mark.parametrize("host", ["old.reddit.com", "attacker.reddit.com"])
def test_reddit_request_rejects_unapproved_subdomains_before_backend_import(
    host: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawns = 0

    def unexpected_spawn(*_: object, **__: object) -> object:
        nonlocal spawns
        spawns += 1
        raise AssertionError("noncanonical Reddit request reached process creation")

    monkeypatch.setattr(opencli.subprocess, "Popen", unexpected_spawn)
    request = _request(
        "reddit",
        "read.post",
        {"url": f"https://{host}/r/Python/comments/abc123/example/"},
    )

    result = execute(request, ExecutionContextV1((NetworkAccessV1(),)))

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "invalid_request"
    assert result.backend_id is None
    assert host not in repr(result)
    assert spawns == 0


@pytest.mark.parametrize(
    "url",
    [
        "https://old.reddit.com/r/Python/comments/abc123/example/",
        "https://www.reddit.com/comments/abc123/",
        "https://www.reddit.com/r/Rust/comments/abc123/example/",
        f"{POST_URL}?utm_source=backend-canary",
    ],
    ids=("subdomain", "noncanonical-path", "subreddit-mismatch", "query-component"),
)
def test_reddit_result_requires_one_canonical_correlated_post_url(
    url: str,
    closure: _Closure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = (
        {
            "id": "abc123",
            "title": "title",
            "subreddit": "Python",
            "author": "alice",
            "score": 1,
            "comments": 1,
            "url": url,
            "created_utc": 1_700_000_000,
            "selftext": "body",
            **_media_fields(),
        },
    )
    monkeypatch.setattr(
        opencli,
        "_run_process",
        lambda *_args, **_kwargs: (0, _yaml(rows), b""),
    )
    monkeypatch.chdir(closure.root.parent)

    result = execute(
        _request("reddit", "search.posts", {"query": QUERY_CANARY, "limit": 1}),
        _context(closure.capability()),
    )

    _assert_failure(result, "backend_contract_violation")
    assert "backend-canary" not in repr(result)


def _write_cli(closure: _Closure, source: str) -> OpenCliSessionV1:
    closure.cli.write_text(source, encoding="utf-8")
    return closure.capability()


def _javascript_guard_closure(
    tmp_path: Path,
    scenario: str,
) -> tuple[_Closure, Path]:
    node_source = shutil.which("node")
    if node_source is None:
        pytest.skip("Node is required to exercise the reviewed OpenCLI preload guard")
    base = tmp_path.resolve()
    root = base / f"opencli-{scenario}"
    package_root = root / opencli._PACKAGE_ROOT
    browser = package_root / "dist" / "src" / "browser"
    browser.mkdir(parents=True)
    session_home = base / f"session-{scenario}"
    config = session_home / ".opencli"
    config.mkdir(parents=True)
    persistent_state = config / "daemon-state.txt"
    persistent_state.write_text("ready-state", encoding="utf-8")
    marker = json.dumps(str(persistent_state))
    (package_root / "package.json").write_text(
        json.dumps(
            {
                "name": "@jackwener/opencli",
                "version": "1.8.6-hermes.1",
                "type": "module",
                "bin": {"opencli": "dist/src/main.js"},
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    (package_root / "dist" / "src" / "errors.js").write_text(
        """export class BrowserConnectError extends Error {
  constructor(message, hint, code) {
    super(message);
    this.hint = hint;
    this.code = code;
  }
}
""",
        encoding="utf-8",
    )
    (browser / "daemon-lifecycle.js").write_text(
        f"""import {{spawn}} from "node:child_process";
import fs from "node:fs";

const statePath = {marker};
export function markLifecycleMutation(name) {{
  fs.appendFileSync(statePath, `\\n${{name}}`);
}}
export function spawnDetachedDirectly() {{
  const child = spawn(process.execPath, ["--version"], {{
    detached: true,
    stdio: "ignore",
  }});
  child.unref();
  markLifecycleMutation("direct-spawn");
}}
export const daemonLifecycleHooks = {{
  requestDaemonShutdown: async () => {{
    markLifecycleMutation("shutdown");
    return true;
  }},
  waitForDaemonStop: async () => {{
    markLifecycleMutation("wait");
    return false;
  }},
  spawnDaemonProcess: () => {{
    markLifecycleMutation("spawn");
    return {{unref: () => markLifecycleMutation("unref")}};
  }},
}};
""",
        encoding="utf-8",
    )
    cli = root / opencli._PACKAGE_CLI
    cli.write_text(
        f"""import childProcess, {{spawn as namedSpawn}} from "node:child_process";
import {{BrowserConnectError}} from "./errors.js";
import {{
  daemonLifecycleHooks,
  markLifecycleMutation,
  spawnDetachedDirectly,
}} from "./browser/daemon-lifecycle.js";

const scenario = {json.dumps(scenario)};
const expectDenied = async (action) => {{
  try {{
    await action();
  }} catch (error) {{
    if (
      error instanceof BrowserConnectError &&
      error.code === "daemon-not-running"
    ) {{
      return;
    }}
    throw error;
  }}
  throw new Error("lifecycle mutation was not denied");
}};
try {{
  if (scenario === "stopped") {{
    const child = daemonLifecycleHooks.spawnDaemonProcess();
    child?.unref();
  }} else if (scenario === "stale") {{
    const shutdown = await daemonLifecycleHooks.requestDaemonShutdown();
    const stopped = shutdown && await daemonLifecycleHooks.waitForDaemonStop();
    if (!stopped) {{
      markLifecycleMutation("kill");
    }}
  }} else if (scenario === "primitives") {{
    const capturedFetch = globalThis.fetch.bind(globalThis);
    let installedFetchCalls = 0;
    const fetchWithNodeNetwork = async (input, init) => {{
      installedFetchCalls += 1;
      const target = new URL(input instanceof Request ? input.url : String(input));
      if (target.origin === "http://127.0.0.1:19825") {{
        markLifecycleMutation("shutdown-fetch-reached");
        return new Response("unsafe");
      }}
      return capturedFetch(input, init);
    }};

    globalThis.fetch = ((input, init) => fetchWithNodeNetwork(input, init));
    const safeResponse = await globalThis.fetch("data:text/plain,ready");
    if ((await safeResponse.text()) !== "ready" || installedFetchCalls !== 1) {{
      throw new Error("OpenCLI fetch replacement failed");
    }}
    await expectDenied(() => globalThis.fetch(
      "http://127.0.0.1:19825/shutdown",
      {{method: "POST"}},
    ));
    if (installedFetchCalls !== 1) {{
      throw new Error("shutdown reached the installed fetch");
    }}
    await expectDenied(() => {{
      globalThis.fetch = (() => Promise.resolve(new Response("unsafe")));
    }});

    process.kill(process.pid, 0);
    await expectDenied(() => process.kill(2147483647, "SIGKILL"));
    await expectDenied(() => childProcess.spawn(
      process.execPath,
      ["--version"],
      {{detached: true, stdio: "ignore"}},
    ));
    await expectDenied(() => namedSpawn(
      process.execPath,
      ["--version"],
      {{detached: true, stdio: "ignore"}},
    ));
    await expectDenied(() => spawnDetachedDirectly());
    await expectDenied(() => daemonLifecycleHooks.requestDaemonShutdown());
    await expectDenied(() => daemonLifecycleHooks.waitForDaemonStop());
    await expectDenied(() => daemonLifecycleHooks.spawnDaemonProcess());
  }}
  process.stdout.write(JSON.stringify([
    {{index: 1, title: "title", text: "text", url: "https://www.facebook.com/alice"}},
  ]));
}} catch (error) {{
  process.stderr.write(JSON.stringify({{
    ok: false,
    error: {{
      code: "BROWSER_CONNECT",
      exitCode: 69,
      message: "lifecycle mutation denied",
    }},
  }}));
  process.exitCode = 69;
}}
""",
        encoding="utf-8",
    )
    node = base / f"node-{scenario}"
    _write_executable_launcher(node, Path(node_source).resolve(strict=True))
    return _Closure(root=root, cli=cli, node=node, session_home=session_home), persistent_state


@pytest.mark.parametrize(
    ("scenario", "expected_type", "expected_code"),
    [
        ("ready", ExecutionSuccessV1, None),
        ("primitives", ExecutionSuccessV1, None),
        ("stopped", ExecutionFailureV1, "backend_unavailable"),
        ("stale", ExecutionFailureV1, "backend_unavailable"),
    ],
)
def test_node_preload_allows_ready_daemon_and_denies_all_lifecycle_mutation(
    scenario: str,
    expected_type: type[ExecutionSuccessV1] | type[ExecutionFailureV1],
    expected_code: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closure, persistent_state = _javascript_guard_closure(tmp_path, scenario)
    session = closure.capability()
    before = persistent_state.read_bytes()
    monkeypatch.chdir(closure.root.parent)

    result = execute(
        _request("facebook", "search", {"query": QUERY_CANARY, "limit": 1}),
        _context(session),
    )

    assert isinstance(result, expected_type)
    if expected_code is None:
        assert isinstance(result, ExecutionSuccessV1)
        assert result.items[0].fields["url"] == "https://www.facebook.com/alice"
    else:
        _assert_failure(result, expected_code)
    assert persistent_state.read_bytes() == before
    assert b"shutdown" not in persistent_state.read_bytes()
    assert b"wait" not in persistent_state.read_bytes()
    assert b"kill" not in persistent_state.read_bytes()
    assert b"spawn" not in persistent_state.read_bytes()
    assert b"unref" not in persistent_state.read_bytes()


def test_real_process_uses_private_home_and_isolates_ambient_user_plugins(
    closure: _Closure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = closure.session_home / ".opencli"
    config.mkdir(parents=True)
    (config / "daemon.json").write_text("trusted-daemon-config", encoding="utf-8")
    ambient_home = closure.root.parent / "ambient-user"
    (ambient_home / ".opencli" / "clis").mkdir(parents=True)
    (ambient_home / ".opencli" / "plugins").mkdir()
    (ambient_home / ".opencli" / "clis" / "facebook.js").write_text(
        "adapter-canary",
        encoding="utf-8",
    )
    (ambient_home / ".opencli" / "plugins" / "override.js").write_text(
        "plugin-canary",
        encoding="utf-8",
    )
    source = f'''import json
import os
from pathlib import Path
import sys

assert sys.argv[1:] == ["facebook", "search", "{QUERY_CANARY}", "--limit", "1", "--format", "yaml"]
home = Path(os.environ["HOME"])
config = Path(os.environ["OPENCLI_CONFIG_DIR"])
assert Path.cwd() == home
assert os.environ["USERPROFILE"] == str(home)
assert config == Path({str(config)!r})
assert config.joinpath("daemon.json").read_text() == "trusted-daemon-config"
assert not home.joinpath(".opencli", "clis").exists()
assert not home.joinpath(".opencli", "plugins").exists()
assert all({str(ambient_home)!r} not in value for value in os.environ.values())
assert "PATH" not in os.environ
assert "OPENCLI_PLUGIN_PATH" not in os.environ
assert "SECRET_TOKEN" not in os.environ
json.dump([{{"index": 1, "title": "title", "text": "text", "url": "https://www.facebook.com/alice"}}], sys.stdout)
'''
    session = _write_cli(closure, source)
    monkeypatch.setenv("HOME", str(ambient_home))
    monkeypatch.setenv("USERPROFILE", str(ambient_home))
    monkeypatch.setenv("PATH", PATH_CANARY)
    monkeypatch.setenv("OPENCLI_PLUGIN_PATH", PATH_CANARY)
    monkeypatch.setenv("SECRET_TOKEN", "secret-environment-canary")
    monkeypatch.chdir(closure.root.parent)

    result = execute(
        _request("facebook", "search", {"query": QUERY_CANARY, "limit": 1}),
        _context(session),
    )

    assert isinstance(result, ExecutionSuccessV1)
    assert result.items[0].fields["title"] == "title"
    assert PATH_CANARY not in repr(result)
    assert "secret-environment-canary" not in repr(result)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_output_byte_overflow_kills_and_reaps_the_process_group(
    stream: str,
    closure: _Closure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = "sys.stdout.buffer" if stream == "stdout" else "sys.stderr.buffer"
    session = _write_cli(
        closure,
        f"""import sys
import time
{target}.write(b"x" * {opencli._MAX_STDOUT_BYTES + 1})
{target}.flush()
time.sleep(5)
""",
    )
    real_killpg = os.killpg
    real_cleanup = opencli._kill_and_reap
    group_kills: list[int] = []
    reaped: list[int] = []

    def killpg(pid: int, requested_signal: signal.Signals) -> None:
        assert requested_signal is signal.SIGKILL
        group_kills.append(pid)
        real_killpg(pid, requested_signal)

    def kill_and_reap(process: subprocess.Popen[bytes]) -> None:
        real_cleanup(process)
        assert process.poll() is not None
        reaped.append(process.pid)

    monkeypatch.setattr(opencli.os, "killpg", killpg)
    monkeypatch.setattr(opencli, "_kill_and_reap", kill_and_reap)
    monkeypatch.chdir(closure.root.parent)

    result = execute(
        _request("facebook", "search", {"query": QUERY_CANARY, "limit": 1}),
        _context(session),
    )

    _assert_failure(result, "backend_contract_violation")
    assert group_kills
    assert reaped == group_kills


def test_process_timeout_kills_and_reaps_before_redacted_failure(
    closure: _Closure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _write_cli(closure, "import time\ntime.sleep(5)\n")
    real_cleanup = opencli._kill_and_reap
    reaped: list[int] = []

    def kill_and_reap(process: subprocess.Popen[bytes]) -> None:
        real_cleanup(process)
        assert process.poll() is not None
        reaped.append(process.pid)

    monkeypatch.setattr(opencli, "_kill_and_reap", kill_and_reap)
    monkeypatch.setattr(opencli, "_PROCESS_TIMEOUT_SECONDS", 0.05)
    monkeypatch.chdir(closure.root.parent)

    result = execute(
        _request("facebook", "search", {"query": QUERY_CANARY, "limit": 1}),
        _context(session),
    )

    _assert_failure(result, "deadline_exceeded")
    assert reaped


def test_checkpoint_cancellation_kills_and_reaps_before_propagation(
    closure: _Closure,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Cancelled(BaseException):
        pass

    session = _write_cli(closure, "import time\ntime.sleep(5)\n")
    real_cleanup = opencli._kill_and_reap
    real_popen = cast(
        "Callable[..., subprocess.Popen[bytes]]",
        opencli.subprocess.Popen,
    )
    reaped: list[int] = []
    checkpoints = 0
    spawned = False

    def checkpoint() -> None:
        nonlocal checkpoints
        checkpoints += 1
        if spawned:
            raise Cancelled()

    def spawn(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        nonlocal spawned
        process = real_popen(*args, **kwargs)
        spawned = True
        return process

    def kill_and_reap(process: subprocess.Popen[bytes]) -> None:
        real_cleanup(process)
        assert process.poll() is not None
        reaped.append(process.pid)

    monkeypatch.setattr(opencli, "_kill_and_reap", kill_and_reap)
    monkeypatch.setattr(opencli.subprocess, "Popen", spawn)
    monkeypatch.chdir(closure.root.parent)

    with pytest.raises(Cancelled):
        execute(
            _request("facebook", "search", {"query": QUERY_CANARY, "limit": 1}),
            _context(session, checkpoint=checkpoint),
        )

    assert spawned is True
    assert checkpoints > 4
    assert reaped


def test_process_group_cleanup_falls_back_to_direct_kill_and_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[object, ...]] = []

    class Process:
        pid = 424_242
        return_code: int | None = None

        def poll(self) -> int | None:
            events.append(("poll",))
            return self.return_code

        def kill(self) -> None:
            events.append(("kill",))
            self.return_code = -9

        def wait(self, *, timeout: float) -> int:
            events.append(("wait", timeout))
            assert self.return_code is not None
            return self.return_code

    monkeypatch.setattr(
        opencli.os,
        "killpg",
        lambda *_args: (_ for _ in ()).throw(OSError("group unavailable")),
    )

    opencli._kill_and_reap(cast(subprocess.Popen[bytes], Process()))

    assert events == [
        ("poll",),
        ("kill",),
        ("wait", opencli._CLEANUP_WAIT_SECONDS),
    ]
