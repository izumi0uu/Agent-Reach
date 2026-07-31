"""Closed Bilibili execution tests for the additive execution v1 API."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Mapping
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from typing import cast

import pytest

import agent_reach.execution.v1.bilibili as bilibili_execution
from agent_reach.execution.v1 import (
    PROTOCOL_VERSION,
    ExecutionContextV1,
    ExecutionFailureV1,
    ExecutionLimitsV1,
    ExecutionRequestV1,
    ExecutionSuccessV1,
    FetchedDocumentV1,
    NetworkAccessV1,
    execute,
    list_capabilities,
)

VIDEO_ID = "BV1xx411c7mD"
OTHER_VIDEO_ID = "BV1Q5411W7xQ"
VIDEO_URL = f"https://www.bilibili.com/video/{VIDEO_ID}"
OTHER_VIDEO_URL = f"https://www.bilibili.com/video/{OTHER_VIDEO_ID}"


def _request(
    operation: str,
    *,
    query: str = "query",
    url: str = VIDEO_URL,
    limit: int = 2,
) -> ExecutionRequestV1:
    if operation == "search.videos":
        arguments: Mapping[str, object] = {"query": query, "limit": limit}
    elif operation == "read.video":
        arguments = {"url": url}
    else:
        arguments = {"limit": limit}
    return ExecutionRequestV1(PROTOCOL_VERSION, "bilibili", operation, arguments)


def _context(
    *,
    checkpoint: Callable[[], None] = lambda: None,
    maximum_items: int = 50,
    maximum_text_characters: int = 16_000,
) -> ExecutionContextV1:
    return ExecutionContextV1(
        (NetworkAccessV1(),),
        checkpoint=checkpoint,
        limits=ExecutionLimitsV1(
            maximum_items=maximum_items,
            maximum_text_characters=maximum_text_characters,
        ),
    )


def _success(data: object) -> dict[str, object]:
    return {"ok": True, "schema_version": "1", "data": data}


def _error(code: str = "not_found") -> dict[str, object]:
    return {
        "ok": False,
        "schema_version": "1",
        "error": {
            "code": code,
            "message": "Cookie=private /Users/private?token=signed",
            "details": {"credential": "hidden"},
        },
    }


def _search_item(**overrides: object) -> dict[str, object]:
    return {
        "id": VIDEO_ID,
        "bvid": VIDEO_ID,
        "title": " Search\n title ",
        "author": " Search\t author ",
        "play": 42,
        "duration": "01:30",
        **overrides,
    }


def _summary(
    bvid: str = VIDEO_ID,
    *,
    description: str = " Description\n body ",
    **overrides: object,
) -> dict[str, object]:
    return {
        "id": bvid,
        "bvid": bvid,
        "aid": 123,
        "title": " Video\t title ",
        "description": description,
        "duration_seconds": 125,
        "duration": "02:05",
        "url": f"https://www.bilibili.com/video/{bvid}",
        "owner": {"id": "7", "name": " Video\n author "},
        "stats": {
            "view": 99,
            "danmaku": 8,
            "like": 7,
            "coin": 6,
            "favorite": 5,
            "share": 4,
        },
        **overrides,
    }


def _video_command(
    summary: object | None = None,
    **overrides: object,
) -> dict[str, object]:
    return {
        "video": _summary() if summary is None else summary,
        "subtitle": {"available": False, "format": "plain", "text": "", "items": []},
        "ai_summary": "",
        "comments": [],
        "related": [],
        "warnings": [],
        **overrides,
    }


def _entry_point(
    *,
    group: str = "console_scripts",
    name: str = "bili",
    value: str = "bili_cli.cli:cli",
) -> SimpleNamespace:
    return SimpleNamespace(group=group, name=name, value=value)


def _install_fake_backend(
    monkeypatch: pytest.MonkeyPatch,
    output: object,
    *,
    raw_output: bool = False,
    exit_code: object | None = None,
    raised: BaseException | None = None,
) -> tuple[list[dict[str, object]], list[str], list[str]]:
    calls: list[dict[str, object]] = []
    distribution_calls: list[str] = []
    import_calls: list[str] = []
    stdout = (
        cast(str, output) if raw_output else json.dumps(output, ensure_ascii=False, allow_nan=False)
    )

    def metadata(name: str) -> SimpleNamespace:
        distribution_calls.append(name)
        return SimpleNamespace(
            version="0.6.2",
            entry_points=(
                _entry_point(group="other", name="ignored", value="ignored:main"),
                _entry_point(),
            ),
        )

    def main(**kwargs: object) -> None:
        calls.append(kwargs)
        if raised is not None:
            raise raised
        sys.stdout.write(stdout)
        if exit_code is not None:
            raise SystemExit(exit_code)

    def load(name: str) -> object:
        import_calls.append(name)
        return SimpleNamespace(cli=SimpleNamespace(main=main))

    monkeypatch.setattr(bilibili_execution, "distribution", metadata)
    monkeypatch.setattr(bilibili_execution, "import_module", load)
    return calls, distribution_calls, import_calls


def _execute_fake(
    monkeypatch: pytest.MonkeyPatch,
    request: ExecutionRequestV1,
    output: object,
    *,
    context: ExecutionContextV1 | None = None,
    raw_output: bool = False,
    exit_code: object | None = None,
    raised: BaseException | None = None,
) -> tuple[
    ExecutionSuccessV1 | ExecutionFailureV1,
    list[dict[str, object]],
    list[str],
    list[str],
]:
    calls, distribution_calls, import_calls = _install_fake_backend(
        monkeypatch,
        output,
        raw_output=raw_output,
        exit_code=exit_code,
        raised=raised,
    )
    result = execute(request, _context() if context is None else context)
    return result, calls, distribution_calls, import_calls


@pytest.mark.parametrize(
    ("execution_request", "data", "expected_argv", "expected_fields"),
    [
        (
            _request("search.videos", query="-danger", limit=1),
            [_search_item()],
            ("search", "--type", "video", "--max", "1", "--json", "--", "-danger"),
            {
                "text": "Search title",
                "native_id": VIDEO_ID,
                "title": "Search title",
                "url": VIDEO_URL,
                "author": "Search author",
                "duration_seconds": 90,
                "view_count": 42,
            },
        ),
        (
            _request("read.video"),
            _video_command(),
            ("video", VIDEO_URL, "--json"),
            {
                "text": "Description body",
                "native_id": VIDEO_ID,
                "title": "Video title",
                "url": VIDEO_URL,
                "author": "Video author",
                "duration_seconds": 125,
                "view_count": 99,
            },
        ),
        (
            _request("browse.hot", limit=2),
            {"items": [_summary()], "page": 1, "count": 2},
            ("hot", "--max", "2", "--json"),
            {
                "text": "Description body",
                "native_id": VIDEO_ID,
                "title": "Video title",
                "url": VIDEO_URL,
                "author": "Video author",
                "duration_seconds": 125,
                "view_count": 99,
            },
        ),
        (
            _request("browse.rank", limit=3),
            {"items": [_summary(description="")], "day": 3, "count": 3},
            ("rank", "--max", "3", "--json"),
            {
                "text": "Video title",
                "native_id": VIDEO_ID,
                "title": "Video title",
                "url": VIDEO_URL,
                "author": "Video author",
                "duration_seconds": 125,
                "view_count": 99,
            },
        ),
    ],
)
def test_all_operations_use_exact_click_argv_and_project_closed_items(
    monkeypatch: pytest.MonkeyPatch,
    execution_request: ExecutionRequestV1,
    data: object,
    expected_argv: tuple[str, ...],
    expected_fields: dict[str, object],
) -> None:
    result, calls, distribution_calls, import_calls = _execute_fake(
        monkeypatch,
        execution_request,
        _success(data),
    )

    assert isinstance(result, ExecutionSuccessV1)
    assert result.backend_id == "bili-cli"
    assert result.backend_version == "0.6.2"
    assert len(result.items) == 1
    assert result.items[0].schema_id == "bilibili.video.v1"
    assert result.items[0].fields == expected_fields
    assert result.truncated is False
    assert calls == [
        {
            "args": list(expected_argv),
            "prog_name": "bili",
            "standalone_mode": False,
        }
    ]
    assert distribution_calls == ["bilibili-cli"]
    assert import_calls == ["bili_cli.cli"]


def test_real_click_parser_keeps_leading_hyphen_query_positional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bili_cli.client

    calls: list[tuple[str, int]] = []

    async def search_video(keyword: str, page: int = 1) -> list[dict[str, object]]:
        calls.append((keyword, page))
        return [
            {
                "bvid": VIDEO_ID,
                "title": "Result",
                "author": "Author",
                "play": 1,
                "duration": "00:10",
            }
        ]

    monkeypatch.setattr(bili_cli.client, "search_video", search_video)

    result = execute(_request("search.videos", query="-danger", limit=1), _context())

    assert isinstance(result, ExecutionSuccessV1)
    assert result.items[0].fields["native_id"] == VIDEO_ID
    assert calls == [("-danger", 1)]


def test_overlapping_backend_invocation_fails_closed_and_releases_stdout_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_entered = Event()
    release_first = Event()
    second_finished = Event()
    calls: list[str] = []
    results: dict[str, ExecutionSuccessV1 | ExecutionFailureV1] = {}
    original_stdout = sys.stdout

    def metadata(_: str) -> SimpleNamespace:
        return SimpleNamespace(version="0.6.2", entry_points=(_entry_point(),))

    def main(**kwargs: object) -> None:
        args = kwargs.get("args")
        if not isinstance(args, list) or not args:
            raise AssertionError("missing backend arguments")
        query = args[-1]
        if not isinstance(query, str):
            raise AssertionError("invalid backend query")
        calls.append(query)
        if query == "first":
            first_entered.set()
            if not release_first.wait(2):
                raise AssertionError("first backend invocation was not released")
            bvid = VIDEO_ID
        elif query == "second":
            bvid = OTHER_VIDEO_ID
        else:
            raise AssertionError("unexpected backend query")
        sys.stdout.write(
            json.dumps(
                _success([_search_item(id=bvid, bvid=bvid, title=query)]),
                ensure_ascii=False,
            )
        )

    def load(_: str) -> object:
        return SimpleNamespace(cli=SimpleNamespace(main=main))

    def run(query: str, *, finished: Event | None = None) -> None:
        results[query] = execute(_request("search.videos", query=query, limit=1), _context())
        if finished is not None:
            finished.set()

    monkeypatch.setattr(bilibili_execution, "distribution", metadata)
    monkeypatch.setattr(bilibili_execution, "import_module", load)

    first = Thread(target=run, args=("first",), daemon=True)
    second = Thread(
        target=run,
        args=("second",),
        kwargs={"finished": second_finished},
        daemon=True,
    )
    first.start()
    try:
        assert first_entered.wait(1)
        second.start()
        assert second_finished.wait(1)
    finally:
        release_first.set()
        first.join(2)
        if second.ident is not None:
            second.join(2)

    assert not first.is_alive()
    assert not second.is_alive()
    first_result = results["first"]
    competing_result = results["second"]
    assert isinstance(first_result, ExecutionSuccessV1)
    assert isinstance(competing_result, ExecutionFailureV1)
    assert competing_result.error_code == "transient"
    assert first_result.items[0].fields["native_id"] == VIDEO_ID
    assert calls == ["first"]
    assert sys.stdout is original_stdout

    recovered = execute(_request("search.videos", query="second", limit=1), _context())

    assert isinstance(recovered, ExecutionSuccessV1)
    assert recovered.items[0].fields["native_id"] == OTHER_VIDEO_ID
    assert calls == ["first", "second"]
    assert sys.stdout is original_stdout


def test_clean_process_discovery_and_rejections_do_not_import_bilibili_runtime() -> None:
    repository = Path(__file__).resolve().parents[1]
    script = f"""
import sys
sys.path.insert(0, {str(repository)!r})
from agent_reach.execution.v1 import (
    PROTOCOL_VERSION,
    ExecutionContextV1,
    ExecutionFailureV1,
    ExecutionRequestV1,
    NetworkAccessV1,
    execute,
    list_capabilities,
)
assert len(list_capabilities()) == 7
assert 'agent_reach.execution.v1.bilibili' not in sys.modules
assert not any(name == 'bili_cli' or name.startswith('bili_cli.') for name in sys.modules)
failure = execute(
    ExecutionRequestV1(
        PROTOCOL_VERSION,
        'bilibili',
        'search.videos',
        {{'query': 'query', 'limit': 1, 'command': 'login'}},
    ),
    ExecutionContextV1((NetworkAccessV1(),)),
)
assert isinstance(failure, ExecutionFailureV1)
assert failure.error_code == 'invalid_request'
missing = execute(
    ExecutionRequestV1(
        PROTOCOL_VERSION,
        'bilibili',
        'browse.hot',
        {{'limit': 1}},
    ),
    ExecutionContextV1(),
)
assert missing.error_code == 'host_capability_missing'
assert 'agent_reach.execution.v1.bilibili' not in sys.modules
assert not any(name == 'bili_cli' or name.startswith('bili_cli.') for name in sys.modules)
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


@pytest.mark.parametrize(
    ("execution_request", "context", "error_code"),
    [
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "bilibili",
                "search.videos",
                {"query": "query", "limit": 1, "command": "login"},
            ),
            _context(),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "bilibili",
                "search.videos",
                {"query": " query", "limit": 1},
            ),
            _context(),
            "invalid_request",
        ),
        (
            ExecutionRequestV1(
                PROTOCOL_VERSION,
                "bilibili",
                "search.videos",
                {"query": "query", "limit": True},
            ),
            _context(),
            "invalid_request",
        ),
        (_request("browse.hot", limit=0), _context(), "invalid_request"),
        (_request("browse.rank", limit=51), _context(), "invalid_request"),
        (
            _request("read.video", url=f"http://www.bilibili.com/video/{VIDEO_ID}"),
            _context(),
            "invalid_request",
        ),
        (
            _request("read.video", url=f"https://www.bilibili.com/video/{VIDEO_ID}?x=1"),
            _context(),
            "invalid_request",
        ),
        (_request("browse.hot", limit=1), ExecutionContextV1(), "host_capability_missing"),
        (
            _request("browse.hot", limit=1),
            ExecutionContextV1(
                (
                    FetchedDocumentV1(
                        b"<rss/>",
                        "application/rss+xml",
                        "https://example.com/feed.xml",
                    ),
                )
            ),
            "invalid_request",
        ),
        (
            _request("browse.hot", limit=1),
            ExecutionContextV1(
                (
                    NetworkAccessV1(),
                    FetchedDocumentV1(
                        b"<rss/>",
                        "application/rss+xml",
                        "https://example.com/feed.xml",
                    ),
                )
            ),
            "invalid_request",
        ),
    ],
)
def test_invalid_requests_and_network_authority_fail_before_backend_loading(
    monkeypatch: pytest.MonkeyPatch,
    execution_request: ExecutionRequestV1,
    context: ExecutionContextV1,
    error_code: str,
) -> None:
    def unexpected_backend(*_: object, **__: object) -> object:
        raise AssertionError("rejected request loaded the backend")

    monkeypatch.setattr(bilibili_execution, "distribution", unexpected_backend)
    monkeypatch.setattr(bilibili_execution, "import_module", unexpected_backend)

    result = execute(execution_request, context)

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == error_code
    assert result.backend_id is None


def test_query_boundaries_are_closed_before_backend_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_backend(*_: object, **__: object) -> object:
        raise AssertionError("invalid query loaded the backend")

    monkeypatch.setattr(bilibili_execution, "distribution", unexpected_backend)

    with pytest.raises(ValueError):
        ExecutionRequestV1(
            PROTOCOL_VERSION,
            "bilibili",
            "search.videos",
            {"query": "x" * 4_097, "limit": 1},
        )

    accepted, _, _, _ = _execute_fake(
        monkeypatch,
        _request("search.videos", query="x" * 4_096, limit=1),
        _success([]),
    )
    assert isinstance(accepted, ExecutionSuccessV1)


@pytest.mark.parametrize(
    ("metadata", "expected_error"),
    [
        (PackageNotFoundError("bilibili-cli"), "backend_unavailable"),
        (OSError("private metadata path"), "backend_unavailable"),
        (
            SimpleNamespace(version="0.6.1", entry_points=(_entry_point(),)),
            "backend_incompatible",
        ),
        (SimpleNamespace(version="0.6.2", entry_points=()), "backend_incompatible"),
        (
            SimpleNamespace(
                version="0.6.2",
                entry_points=(_entry_point(), _entry_point()),
            ),
            "backend_incompatible",
        ),
        (
            SimpleNamespace(
                version="0.6.2",
                entry_points=(_entry_point(value="bili_cli.cli:other"),),
            ),
            "backend_incompatible",
        ),
        (
            SimpleNamespace(
                version="0.6.2",
                entry_points=(_entry_point(group="other"),),
            ),
            "backend_incompatible",
        ),
        (
            SimpleNamespace(
                version="0.6.2",
                entry_points=(_entry_point(name="other"),),
            ),
            "backend_incompatible",
        ),
    ],
)
def test_distribution_version_and_entry_point_drift_fail_before_import(
    monkeypatch: pytest.MonkeyPatch,
    metadata: object,
    expected_error: str,
) -> None:
    distribution_calls: list[str] = []

    def read_distribution(name: str) -> object:
        distribution_calls.append(name)
        if isinstance(metadata, BaseException):
            raise metadata
        return metadata

    def unexpected_import(name: str) -> object:
        raise AssertionError(f"metadata drift imported {name}")

    monkeypatch.setattr(bilibili_execution, "distribution", read_distribution)
    monkeypatch.setattr(bilibili_execution, "import_module", unexpected_import)

    result = execute(_request("browse.hot", limit=1), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == expected_error
    assert result.backend_id == "bili-cli"
    assert result.backend_version == "0.6.2"
    assert distribution_calls == ["bilibili-cli"]
    assert "private" not in repr(result)


def test_malformed_distribution_metadata_is_closed_as_incompatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenMetadata:
        @property
        def version(self) -> str:
            raise RuntimeError("/private/metadata/path")

    def unexpected_import(name: str) -> object:
        raise AssertionError(f"malformed metadata imported {name}")

    monkeypatch.setattr(bilibili_execution, "distribution", lambda _: BrokenMetadata())
    monkeypatch.setattr(bilibili_execution, "import_module", unexpected_import)

    result = execute(_request("browse.hot", limit=1), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_incompatible"
    assert "private" not in repr(result)


@pytest.mark.parametrize(
    "loaded",
    [
        ImportError("private import path"),
        SimpleNamespace(),
        SimpleNamespace(cli=object()),
        SimpleNamespace(cli=SimpleNamespace(main=None)),
    ],
)
def test_entry_module_or_callable_drift_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
    loaded: object,
) -> None:
    monkeypatch.setattr(
        bilibili_execution,
        "distribution",
        lambda _: SimpleNamespace(version="0.6.2", entry_points=(_entry_point(),)),
    )

    def load(_: str) -> object:
        if isinstance(loaded, BaseException):
            raise loaded
        return loaded

    monkeypatch.setattr(bilibili_execution, "import_module", load)

    result = execute(_request("browse.hot", limit=1), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_incompatible"
    assert result.backend_id == "bili-cli"
    assert "private" not in repr(result)


@pytest.mark.parametrize(
    ("raw_output", "exit_code"),
    [
        ("", None),
        ("not-json", None),
        ('{"ok":true,"ok":false,"schema_version":"1","data":[]}', None),
        ('{"ok":true,"schema_version":"1","data":[NaN]}', None),
        ('{"ok":true,"schema_version":"1","data":[],"raw":"secret"}', None),
        ('{"ok":true,"schema_version":1,"data":[]}', None),
        ('{"ok":false,"schema_version":"1","error":{"code":"not_found"}}', 1),
        (json.dumps(_error()), None),
        (json.dumps(_success([])), 1),
        (json.dumps(_success([])), 2),
        (json.dumps(_error()), True),
        (json.dumps(_error()), 1.0),
        ("[]", None),
    ],
)
def test_json_and_command_envelopes_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    raw_output: str,
    exit_code: object | None,
) -> None:
    result, _, _, _ = _execute_fake(
        monkeypatch,
        _request("search.videos", limit=1),
        raw_output,
        raw_output=True,
        exit_code=exit_code,
    )

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_contract_violation"
    assert result.backend_id == "bili-cli"
    assert "secret" not in repr(result)


def _deep_value() -> object:
    value: object = "leaf"
    for _ in range(14):
        value = [value]
    return value


@pytest.mark.parametrize(
    "details",
    [
        [None] * 65,
        _deep_value(),
        "x" * (64 * 1024 + 1),
        (1 << 53),
        1.5,
    ],
)
def test_error_details_cannot_bypass_json_bounds(
    monkeypatch: pytest.MonkeyPatch,
    details: object,
) -> None:
    envelope = _error("future_error")
    cast(dict[str, object], envelope["error"])["details"] = details
    result, _, _, _ = _execute_fake(
        monkeypatch,
        _request("browse.hot", limit=1),
        envelope,
        exit_code=1,
    )

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_contract_violation"


def test_boolean_json_values_remain_distinct_from_integers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = _error("not_found")
    cast(dict[str, object], envelope["error"])["details"] = {"retryable": True}
    result, _, _, _ = _execute_fake(
        monkeypatch,
        _request("browse.hot", limit=1),
        envelope,
        exit_code=1,
    )

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "not_found"


def test_stdout_is_bounded_in_utf8_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _, _, _ = _execute_fake(
        monkeypatch,
        _request("browse.hot", limit=1),
        "x" * (512 * 1024 + 1),
        raw_output=True,
    )

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_contract_violation"


def test_projected_result_cannot_expand_past_the_capability_output_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = [_search_item(title='"' * 4_096) for _ in range(50)]
    envelope = _success(items)
    raw_backend_bytes = json.dumps(envelope, ensure_ascii=False).encode("utf-8")

    assert len(raw_backend_bytes) < 512 * 1024

    result, _, _, _ = _execute_fake(
        monkeypatch,
        _request("search.videos", limit=50),
        envelope,
    )

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_contract_violation"


def test_invalid_unicode_output_is_a_contract_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _, _, _ = _execute_fake(
        monkeypatch,
        _request("browse.hot", limit=1),
        "\ud800",
        raw_output=True,
    )

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_contract_violation"


def test_escaped_lone_surrogate_json_is_a_closed_contract_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _, _, _ = _execute_fake(
        monkeypatch,
        _request("browse.hot", limit=1),
        ('{"ok":false,"schema_version":"1","error":{"code":"not_found","message":"\\ud800"}}'),
        raw_output=True,
        exit_code=1,
    )

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_contract_violation"


def test_huge_json_integer_is_a_closed_contract_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_output = (
        '{"ok":false,"schema_version":"1","error":'
        '{"code":"not_found","message":"redacted","details":' + "9" * 5_000 + "}}"
    )

    result, _, _, _ = _execute_fake(
        monkeypatch,
        _request("browse.hot", limit=1),
        raw_output,
        raw_output=True,
        exit_code=1,
    )

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_contract_violation"


@pytest.mark.parametrize(
    ("backend_code", "expected_error"),
    [
        ("invalid_input", "invalid_input"),
        ("not_found", "not_found"),
        ("not_authenticated", "authentication"),
        ("permission_denied", "authorization"),
        ("rate_limited", "rate_limit"),
        ("network_error", "transient"),
        ("upstream_error", "permanent"),
        ("internal_error", "permanent"),
        ("future_error", "permanent"),
    ],
)
def test_backend_errors_use_only_the_closed_redacted_mapping(
    monkeypatch: pytest.MonkeyPatch,
    backend_code: str,
    expected_error: str,
) -> None:
    result, _, _, _ = _execute_fake(
        monkeypatch,
        _request("browse.hot", limit=1),
        _error(backend_code),
        exit_code=1,
    )

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == expected_error
    assert result.backend_id == "bili-cli"
    assert result.backend_version == "0.6.2"
    assert not hasattr(result, "message")
    assert "private" not in repr(result)
    assert "credential" not in repr(result)


def test_backend_exception_text_is_redacted_as_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _, _, _ = _execute_fake(
        monkeypatch,
        _request("browse.hot", limit=1),
        "",
        raw_output=True,
        raised=RuntimeError("Cookie=private /Users/private?token=signed"),
    )

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "transient"
    assert "private" not in repr(result)


@pytest.mark.parametrize(
    ("execution_request", "data"),
    [
        (
            _request("search.videos", limit=1),
            [_search_item(raw="backend")],
        ),
        (
            _request("search.videos", limit=1),
            [_search_item(id=OTHER_VIDEO_ID)],
        ),
        (
            _request("search.videos", limit=1),
            [_search_item(duration="01:99")],
        ),
        (
            _request("search.videos", limit=1),
            [_search_item(play=True)],
        ),
        (
            _request("search.videos", limit=1),
            [_search_item(), _search_item()],
        ),
        (
            _request("read.video"),
            _video_command(comments=[{"message": "unexpected"}]),
        ),
        (
            _request("read.video"),
            _video_command(subtitle={"available": 0, "format": "plain", "text": "", "items": []}),
        ),
        (
            _request("read.video"),
            _video_command(_summary(url="https://evil.example/video")),
        ),
        (
            _request("read.video"),
            _video_command(_summary(duration_seconds=124)),
        ),
        (
            _request("read.video"),
            _video_command(
                _summary(
                    stats={
                        "view": -1,
                        "danmaku": 8,
                        "like": 7,
                        "coin": 6,
                        "favorite": 5,
                        "share": 4,
                    }
                )
            ),
        ),
        (
            _request("browse.hot", limit=1),
            {"items": [], "page": True, "count": 1},
        ),
        (
            _request("browse.hot", limit=1),
            {"items": [], "page": 1, "count": True},
        ),
        (
            _request("browse.rank", limit=1),
            {"items": [], "day": 1, "count": 1},
        ),
        (
            _request("browse.rank", limit=1),
            {"items": [], "day": 3, "count": 2},
        ),
    ],
)
def test_projection_rejects_raw_schema_identity_and_numeric_drift(
    monkeypatch: pytest.MonkeyPatch,
    execution_request: ExecutionRequestV1,
    data: object,
) -> None:
    result, _, _, _ = _execute_fake(monkeypatch, execution_request, _success(data))

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_contract_violation"


def test_read_result_must_correlate_to_the_requested_canonical_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _, _, _ = _execute_fake(
        monkeypatch,
        _request("read.video", url=VIDEO_URL),
        _success(_video_command(_summary(OTHER_VIDEO_ID))),
    )

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_contract_violation"


def test_host_item_limit_narrows_argv_and_marks_the_result_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, calls, _, _ = _execute_fake(
        monkeypatch,
        _request("search.videos", limit=5),
        _success([_search_item(), _search_item()]),
        context=_context(maximum_items=2),
    )

    assert isinstance(result, ExecutionSuccessV1)
    assert len(result.items) == 2
    assert result.truncated is True
    assert calls[0]["args"] == [
        "search",
        "--type",
        "video",
        "--max",
        "2",
        "--json",
        "--",
        "query",
    ]


@pytest.mark.parametrize(
    ("execution_request", "data"),
    [
        (
            _request("search.videos", limit=1),
            [_search_item(title="abcdefgh")],
        ),
        (
            _request("read.video"),
            _video_command(_summary(description="abcdefgh")),
        ),
        (
            _request("browse.hot", limit=1),
            {"items": [_summary(description="abcdefgh")], "page": 1, "count": 1},
        ),
        (
            _request("browse.rank", limit=1),
            {"items": [_summary(description="abcdefgh")], "day": 3, "count": 1},
        ),
    ],
)
def test_host_text_limit_applies_to_every_operation_and_marks_truncation(
    monkeypatch: pytest.MonkeyPatch,
    execution_request: ExecutionRequestV1,
    data: object,
) -> None:
    result, _, _, _ = _execute_fake(
        monkeypatch,
        execution_request,
        _success(data),
        context=_context(maximum_text_characters=5),
    )

    assert isinstance(result, ExecutionSuccessV1)
    assert result.items[0].fields["text"] == "abcde"
    assert result.truncated is True


def test_checkpoints_stop_before_backend_loading_invocation_and_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HostCancelled(Exception):
        pass

    def unexpected_backend(*_: object, **__: object) -> object:
        raise AssertionError("early cancellation loaded the backend")

    monkeypatch.setattr(bilibili_execution, "distribution", unexpected_backend)
    with pytest.raises(HostCancelled):
        execute(
            _request("browse.hot", limit=1),
            _context(checkpoint=lambda: (_ for _ in ()).throw(HostCancelled())),
        )

    checks = 0

    def cancel_before_invocation() -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise HostCancelled

    calls, _, _ = _install_fake_backend(
        monkeypatch,
        _success({"items": [], "page": 1, "count": 1}),
    )
    with pytest.raises(HostCancelled):
        execute(
            _request("browse.hot", limit=1),
            _context(checkpoint=cancel_before_invocation),
        )
    assert calls == []

    checks = 0

    def cancel_after_invocation() -> None:
        nonlocal checks
        checks += 1
        if checks == 3:
            raise HostCancelled

    calls, _, _ = _install_fake_backend(
        monkeypatch,
        _success({"items": [], "page": 1, "count": 1}),
    )
    with pytest.raises(HostCancelled):
        execute(
            _request("browse.hot", limit=1),
            _context(checkpoint=cancel_after_invocation),
        )
    assert len(calls) == 1


def test_bilibili_runtime_bound_is_reflected_by_static_capabilities() -> None:
    capabilities = [item for item in list_capabilities() if item.source == "bilibili"]

    assert len(capabilities) == 4
    assert all(item.maximum_output_bytes == 512 * 1024 for item in capabilities)


def test_backend_error_mapping_is_exact_and_immutable() -> None:
    assert bilibili_execution._BACKEND_ERROR_CODES == {
        "invalid_input": "invalid_input",
        "not_found": "not_found",
        "not_authenticated": "authentication",
        "permission_denied": "authorization",
        "rate_limited": "rate_limit",
        "network_error": "transient",
        "upstream_error": "permanent",
        "internal_error": "permanent",
    }
    with pytest.raises(TypeError):
        bilibili_execution._BACKEND_ERROR_CODES["not_found"] = "permanent"  # type: ignore[index]
