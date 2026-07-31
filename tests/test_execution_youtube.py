"""Closed YouTube read-video execution tests for execution v1."""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Callable, Iterator, Mapping
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import agent_reach.execution.v1.youtube as youtube_execution
from agent_reach.execution.v1 import (
    PROTOCOL_VERSION,
    ExecutionContextV1,
    ExecutionFailureV1,
    ExecutionLimitsV1,
    ExecutionRequestV1,
    ExecutionSuccessV1,
    NetworkAccessV1,
    PrivateWorkspaceV1,
    execute,
)

VIDEO_ID = "dQw4w9WgXcQ"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
VERSIONS = {"yt-dlp": "2026.7.4", "yt-dlp-ejs": "0.8.0", "deno": "2.8.3"}


class SubtitleCheckpointError(Exception):
    pass


def _request(url: str = VIDEO_URL) -> ExecutionRequestV1:
    return ExecutionRequestV1(
        PROTOCOL_VERSION,
        "youtube",
        "read.video",
        {"url": url},
    )


def _search_request(query: str = "private query", limit: int = 2) -> ExecutionRequestV1:
    return ExecutionRequestV1(
        PROTOCOL_VERSION,
        "youtube",
        "search.videos",
        {"query": query, "limit": limit},
    )


def _subtitle_request(language: str | None = "en") -> ExecutionRequestV1:
    return ExecutionRequestV1(
        PROTOCOL_VERSION,
        "youtube",
        "read.subtitles",
        {"url": VIDEO_URL, "language": language},
    )


def _context(
    *,
    checkpoint: Callable[[], None] = lambda: None,
    maximum_text_characters: int = 16_000,
) -> ExecutionContextV1:
    return ExecutionContextV1(
        (NetworkAccessV1(),),
        checkpoint=checkpoint,
        limits=ExecutionLimitsV1(
            maximum_items=1,
            maximum_text_characters=maximum_text_characters,
        ),
    )


def _search_context(
    *,
    maximum_items: int = 50,
    maximum_text_characters: int = 16_000,
) -> ExecutionContextV1:
    return ExecutionContextV1(
        (NetworkAccessV1(),),
        limits=ExecutionLimitsV1(
            maximum_items=maximum_items,
            maximum_text_characters=maximum_text_characters,
        ),
    )


def _subtitle_context(
    *,
    checkpoint: Callable[[], None] = lambda: None,
    maximum_text_characters: int = 16_000,
) -> ExecutionContextV1:
    return ExecutionContextV1(
        (NetworkAccessV1(), PrivateWorkspaceV1()),
        checkpoint=checkpoint,
        limits=ExecutionLimitsV1(
            maximum_items=1,
            maximum_text_characters=maximum_text_characters,
        ),
    )


def _video(**overrides: object) -> dict[str, object]:
    return {
        "id": VIDEO_ID,
        "title": " Video   title ",
        "description": " Description\nbody ",
        "uploader": " Channel\tname ",
        "channel": "Fallback channel",
        "duration": 213,
        "view_count": 42,
        "comment_count": 7,
        "upload_date": "20091025",
        "formats": [{"url": "https://signed.invalid/private?token=secret"}],
        "http_headers": {"Cookie": "private"},
        **overrides,
    }


class FakeDownloader:
    instances: list[FakeDownloader] = []
    response: object | Callable[[FakeDownloader], object] = _video()
    raised: BaseException | None = None
    close_raised: Exception | None = None
    on_extract: Callable[[FakeDownloader, str], None] | None = None

    def __init__(self, params: dict[str, object]) -> None:
        self.params = params
        self.calls: list[tuple[str, bool, str]] = []
        self.closed = False
        self.close_calls = 0
        self.instances.append(self)

    def extract_info(self, target: str, *, download: bool, ie_key: str) -> object:
        self.calls.append((target, download, ie_key))
        on_extract = type(self).on_extract
        if on_extract is not None:
            on_extract(self, target)
        if self.raised is not None:
            raise self.raised
        response = type(self).response
        return response(self) if callable(response) else response

    def close(self) -> None:
        self.closed = True
        self.close_calls += 1
        if self.close_raised is not None:
            raise self.close_raised


def _subtitle_output_root(downloader: FakeDownloader) -> Path:
    paths = downloader.params.get("paths")
    assert isinstance(paths, dict)
    root = paths.get("subtitle")
    assert isinstance(root, str)
    return Path(root)


def _subtitle_backend_response(
    downloader: FakeDownloader,
    body: str = "WEBVTT\n",
    *,
    language: str = "en",
    origin: str = "manual",
) -> dict[str, object]:
    root = _subtitle_output_root(downloader)
    subtitle = root / f"{VIDEO_ID}.{language}.vtt"
    subtitle.write_text(body, encoding="utf-8")
    return {
        **_video(),
        "requested_subtitles": {
            language: {"ext": "vtt", "filepath": str(subtitle)},
        },
        "subtitles": {language: [{"ext": "vtt"}]} if origin == "manual" else {},
        "automatic_captions": ({language: [{"ext": "vtt"}]} if origin == "automatic" else {}),
    }


class BadMapping(Mapping[str, object]):
    def __getitem__(self, _key: str) -> object:
        raise RuntimeError("private mapping detail")

    def __iter__(self) -> Iterator[str]:
        raise RuntimeError("private mapping detail")

    def __len__(self) -> int:
        return 1


@pytest.fixture(autouse=True)
def _reset_fake_downloader() -> Iterator[None]:
    FakeDownloader.instances.clear()
    FakeDownloader.response = _video()
    FakeDownloader.raised = None
    FakeDownloader.close_raised = None
    FakeDownloader.on_extract = None
    yield
    FakeDownloader.instances.clear()
    FakeDownloader.response = _video()
    FakeDownloader.raised = None
    FakeDownloader.close_raised = None
    FakeDownloader.on_extract = None


def _adjacent_runtime(tmp_path: Path) -> tuple[Path, Path]:
    scripts = tmp_path / "bin"
    scripts.mkdir()
    executable = scripts / "python"
    executable.write_bytes(b"")
    deno = scripts / ("deno.exe" if os.name == "nt" else "deno")
    deno.write_bytes(b"deno")
    deno.chmod(0o700)
    return executable, deno


def _install_fake_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    ejs_version: str = "0.8.0",
    plugin_dirs: object | None = None,
    youtube_dl: object = FakeDownloader,
) -> tuple[SimpleNamespace, Path]:
    executable, deno = _adjacent_runtime(tmp_path)
    plugins = (
        cast(SimpleNamespace, plugin_dirs)
        if plugin_dirs is not None
        else SimpleNamespace(value=["ambient"])
    )

    def load(name: str) -> object:
        if name == "yt_dlp":
            return SimpleNamespace(YoutubeDL=youtube_dl)
        if name == "yt_dlp_ejs":
            return SimpleNamespace(version=ejs_version)
        if name == "yt_dlp.globals":
            return SimpleNamespace(plugin_dirs=plugins)
        raise AssertionError(name)

    monkeypatch.setattr(youtube_execution, "version", lambda name: VERSIONS[name])
    monkeypatch.setattr(youtube_execution, "import_module", load)
    monkeypatch.setattr(youtube_execution.sys, "executable", str(executable))
    monkeypatch.setenv("YTDLP_NO_PLUGINS", "ambient")
    return plugins, deno


def test_search_uses_exact_backend_call_and_projects_closed_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    FakeDownloader.response = {
        "entries": [
            _video(),
            _video(
                id="aaaaaaaaaaa",
                title="Second video",
                description=None,
                uploader=None,
                channel="Second channel",
            ),
        ]
    }

    result = execute(_search_request(), _search_context())

    assert isinstance(result, ExecutionSuccessV1)
    assert result.source == "youtube"
    assert result.operation == "search.videos"
    assert result.backend_id == "yt-dlp"
    assert result.backend_version == "2026.7.4"
    assert result.truncated is False
    assert [item.fields["native_id"] for item in result.items] == [VIDEO_ID, "aaaaaaaaaaa"]
    assert result.items[1].fields == {
        "text": "Second video",
        "native_id": "aaaaaaaaaaa",
        "title": "Second video",
        "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        "author": "Second channel",
        "published_at": "2009-10-25",
        "duration_seconds": 213,
        "view_count": 42,
        "comment_count": 7,
    }
    instance = FakeDownloader.instances[0]
    assert instance.calls == [("ytsearch2:private query", False, "YoutubeSearch")]
    assert instance.closed is True
    assert "writesubtitles" not in instance.params
    assert "paths" not in instance.params


def test_search_empty_results_and_context_narrowing_are_explicit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    FakeDownloader.response = {"entries": []}
    empty = execute(_search_request(limit=1), _search_context())
    FakeDownloader.response = {
        "entries": [_video(), _video(id="aaaaaaaaaaa")],
    }
    narrowed = execute(_search_request(limit=5), _search_context(maximum_items=2))

    assert isinstance(empty, ExecutionSuccessV1)
    assert empty.items == ()
    assert empty.truncated is False
    assert isinstance(narrowed, ExecutionSuccessV1)
    assert len(narrowed.items) == 2
    assert narrowed.truncated is True
    assert FakeDownloader.instances[0].calls == [
        ("ytsearch1:private query", False, "YoutubeSearch")
    ]
    assert FakeDownloader.instances[1].calls == [
        ("ytsearch2:private query", False, "YoutubeSearch")
    ]


@pytest.mark.parametrize(
    "response",
    [
        {"entries": (_video(),)},
        {"entries": [_video(), _video()]},
        {"entries": [_video(), _video(id="aaaaaaaaaaa")]},
        {"entries": [BadMapping()]},
    ],
)
def test_search_rejects_invalid_entry_collections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    response: object,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    FakeDownloader.response = response
    context = _search_context(maximum_items=1) if isinstance(response, dict) else _search_context()

    result = execute(_search_request(limit=2), context)

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_contract_violation"
    assert FakeDownloader.instances[0].close_calls == 1


def test_search_source_caps_are_silent_but_context_text_narrowing_is_reported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    FakeDownloader.response = {"entries": [_video(description="a" * 4_097)]}
    source_capped = execute(_search_request(limit=1), _search_context())
    FakeDownloader.response = {"entries": [_video(description="abcdefghijk")]}
    context_capped = execute(
        _search_request(limit=1),
        _search_context(maximum_text_characters=10),
    )

    assert isinstance(source_capped, ExecutionSuccessV1)
    assert source_capped.items[0].fields["text"] == "a" * 4_096
    assert source_capped.truncated is False
    assert isinstance(context_capped, ExecutionSuccessV1)
    assert context_capped.items[0].fields["text"] == "abcdefghij"
    assert context_capped.truncated is True


def test_subtitle_text_limit_below_webvtt_marker_fails_before_backend_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_loads = 0
    checkpoints = 0

    def load_backend() -> object:
        nonlocal backend_loads
        backend_loads += 1
        raise AssertionError("narrow subtitle limit reached backend loading")

    def checkpoint() -> None:
        nonlocal checkpoints
        checkpoints += 1

    monkeypatch.setattr(youtube_execution, "_load_backend", load_backend)

    result = execute(
        _subtitle_request(),
        _subtitle_context(
            checkpoint=checkpoint,
            maximum_text_characters=len("WEBVTT") - 1,
        ),
    )

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "invalid_input"
    assert result.source == "youtube"
    assert result.operation == "read.subtitles"
    assert result.backend_id is None
    assert result.backend_version is None
    assert backend_loads == 0
    assert checkpoints == 0


def test_subtitles_use_explicit_language_manual_precedence_and_private_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    sibling = tmp_path / "created-during-subtitle-call.txt"

    def response(downloader: FakeDownloader) -> object:
        sibling.write_text("unrelated", encoding="utf-8")
        value = _subtitle_backend_response(
            downloader,
            "WEBVTT\n\n00:00.000 --> 00:01.000\nHello",
        )
        value["automatic_captions"] = {"en": [{"ext": "vtt", "url": "private"}]}
        return value

    FakeDownloader.response = response

    result = execute(_subtitle_request(), _subtitle_context())

    assert isinstance(result, ExecutionSuccessV1)
    assert result.operation == "read.subtitles"
    assert result.truncated is False
    assert result.items[0].schema_id == "youtube.subtitle.v1"
    assert result.items[0].fields == {
        "text": "WEBVTT\n\n00:00.000 --> 00:01.000\nHello",
        "native_id": VIDEO_ID,
        "title": "Video title",
        "url": VIDEO_URL,
        "language": "en",
        "origin": "manual",
    }
    instance = FakeDownloader.instances[0]
    assert instance.calls == [(VIDEO_URL, True, "Youtube")]
    assert instance.params["subtitleslangs"] == ["en"]
    assert instance.params["writesubtitles"] is True
    assert instance.params["writeautomaticsub"] is True
    assert instance.params["subtitlesformat"] == "vtt"
    assert instance.params["skip_download"] is True
    subtitle_root = _subtitle_output_root(instance)
    assert instance.params["paths"] == {
        "home": str(subtitle_root),
        "temp": str(subtitle_root),
        "subtitle": str(subtitle_root),
    }
    assert instance.params["outtmpl"] == {
        "default": str(subtitle_root / "%(id)s.%(ext)s"),
        "subtitle": str(subtitle_root / "%(id)s.%(ext)s"),
    }
    assert subtitle_root.parent == tmp_path
    assert subtitle_root.name.startswith(".agent-reach-youtube-")
    assert subtitle_root.exists() is False
    assert sibling.read_text(encoding="utf-8") == "unrelated"
    assert (tmp_path / "bin").is_dir()


def test_subtitle_calls_use_distinct_temporary_output_directories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    roots: list[Path] = []

    def response(downloader: FakeDownloader) -> object:
        roots.append(_subtitle_output_root(downloader))
        return _subtitle_backend_response(downloader)

    FakeDownloader.response = response

    results = (
        execute(_subtitle_request(), _subtitle_context()),
        execute(_subtitle_request(), _subtitle_context()),
    )

    assert all(isinstance(result, ExecutionSuccessV1) for result in results)
    assert len(set(roots)) == 2
    assert all(root.parent == tmp_path for root in roots)
    assert all(not root.exists() for root in roots)


@pytest.mark.parametrize(
    ("body", "maximum_text", "expected_prefix"),
    [
        ("WEBVTT\n" + ("a" * (256 * 1_024)), 16_000, "WEBVTT\n" + ("a" * 10)),
        ("WEBVTT\nabcdefghijk", 10, "WEBVTT\nabc"),
        ("WEBVTT\nabcdefghijk", len("WEBVTT"), "WEBVTT"),
    ],
)
def test_subtitle_source_and_context_truncation_are_ored(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    body: str,
    maximum_text: int,
    expected_prefix: str,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    FakeDownloader.response = lambda downloader: _subtitle_backend_response(
        downloader,
        body,
        language="zh-Hans",
        origin="automatic",
    )

    result = execute(
        _subtitle_request(None),
        _subtitle_context(maximum_text_characters=maximum_text),
    )

    assert isinstance(result, ExecutionSuccessV1)
    assert result.items[0].fields["language"] == "zh-Hans"
    assert result.items[0].fields["origin"] == "automatic"
    assert cast(str, result.items[0].fields["text"]).startswith(expected_prefix)
    assert result.truncated is True
    assert FakeDownloader.instances[0].params["subtitleslangs"] == ["zh-Hans", "zh", "en"]
    assert _subtitle_output_root(FakeDownloader.instances[0]).exists() is False


@pytest.mark.parametrize(
    "requested_subtitles",
    [{}, {"zh": {"ext": "vtt", "filepath": "/private/zh.vtt"}}, {"live_chat": {}}],
)
def test_subtitle_absence_or_language_mismatch_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    requested_subtitles: object,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    FakeDownloader.response = {
        **_video(),
        "requested_subtitles": requested_subtitles,
        "subtitles": {},
        "automatic_captions": {},
    }

    result = execute(_subtitle_request(), _subtitle_context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "not_found"
    assert FakeDownloader.instances[0].close_calls == 1


@pytest.mark.parametrize("failure", ["format", "origin", "path"])
def test_subtitle_projection_failure_cleans_call_directory_without_touching_siblings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    sibling = tmp_path / "unrelated-concurrent-output.txt"

    def response(downloader: FakeDownloader) -> object:
        sibling.write_text("preserve", encoding="utf-8")
        nested = _subtitle_output_root(downloader) / "nested"
        nested.mkdir()
        (nested / "backend.tmp").write_text("cleanup", encoding="utf-8")
        value = _subtitle_backend_response(downloader)
        requested = cast(dict[str, dict[str, object]], value["requested_subtitles"])
        requested["en"]["ext"] = "srt" if failure == "format" else "vtt"
        if failure == "path":
            requested["en"]["filepath"] = 1
        if failure == "origin":
            value["subtitles"] = {}
        return value

    FakeDownloader.response = response

    result = execute(_subtitle_request(), _subtitle_context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_contract_violation"
    instance = FakeDownloader.instances[0]
    assert _subtitle_output_root(instance).exists() is False
    assert sibling.read_text(encoding="utf-8") == "preserve"
    assert instance.close_calls == 1


def test_subtitle_path_escape_is_rejected_without_removing_external_file(tmp_path: Path) -> None:
    root = tmp_path / "private"
    root.mkdir()
    outside = tmp_path / "outside.vtt"
    outside.write_text("WEBVTT\n", encoding="utf-8")

    with pytest.raises(youtube_execution._BackendContractError):
        youtube_execution._read_subtitle_file(outside, root, lambda: None)

    assert outside.exists() is True


@pytest.mark.skipif(os.name == "nt", reason="symlink aliases require POSIX semantics")
def test_subtitle_path_accepts_a_resolved_private_root_alias(tmp_path: Path) -> None:
    root = tmp_path / "private"
    root.mkdir()
    alias = tmp_path / "private-alias"
    alias.symlink_to(root, target_is_directory=True)
    subtitle = root / f"{VIDEO_ID}.en.vtt"
    subtitle.write_text("WEBVTT\n\nHello", encoding="utf-8")

    text, truncated = youtube_execution._read_subtitle_file(
        alias / subtitle.name,
        alias,
        lambda: None,
    )

    assert text.endswith("Hello")
    assert truncated is False
    assert subtitle.exists() is False


@pytest.mark.skipif(os.name == "nt", reason="link shape checks require POSIX semantics")
@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_subtitle_execution_rejects_links_and_cleans_only_the_new_link(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "bin" / "target.vtt"
    target.write_text("WEBVTT\n", encoding="utf-8")

    def response(downloader: FakeDownloader) -> object:
        subtitle = _subtitle_output_root(downloader) / f"{VIDEO_ID}.en.vtt"
        if kind == "symlink":
            subtitle.symlink_to(target)
        else:
            os.link(target, subtitle)
        return {
            **_video(),
            "requested_subtitles": {"en": {"ext": "vtt", "filepath": str(subtitle)}},
            "subtitles": {"en": [{"ext": "vtt"}]},
            "automatic_captions": {},
        }

    FakeDownloader.response = response

    result = execute(_subtitle_request(), _subtitle_context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_contract_violation"
    subtitle_root = _subtitle_output_root(FakeDownloader.instances[0])
    assert subtitle_root.exists() is False
    assert target.exists() is True


@pytest.mark.parametrize(
    "body",
    [b"WEBVTT\n" + (b"a" * (512 * 1_024)), b"WEBVTT\n\xff", b"plain text"],
)
def test_subtitle_file_rejects_oversize_invalid_utf8_and_non_vtt(
    tmp_path: Path,
    body: bytes,
) -> None:
    subtitle = tmp_path / "subtitle.vtt"
    subtitle.write_bytes(body)

    with pytest.raises(youtube_execution._BackendContractError):
        youtube_execution._read_subtitle_file(subtitle, tmp_path, lambda: None)

    assert subtitle.exists() is False


def test_subtitle_file_rejects_inode_swap_and_detects_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    subtitle = tmp_path / "subtitle.vtt"
    subtitle.write_text("WEBVTT\n", encoding="utf-8")
    replacement = tmp_path / "replacement.vtt"
    replacement.write_text("WEBVTT\nreplacement", encoding="utf-8")
    real_fstat = os.fstat
    monkeypatch.setattr(youtube_execution.os, "fstat", lambda _descriptor: replacement.stat())

    with pytest.raises(youtube_execution._BackendContractError):
        youtube_execution._read_subtitle_file(subtitle, tmp_path, lambda: None)

    assert subtitle.exists() is False
    assert replacement.exists() is True

    subtitle.write_text("WEBVTT\n", encoding="utf-8")
    calls = 0

    def changed_after_read(descriptor: int) -> object:
        nonlocal calls
        calls += 1
        details = real_fstat(descriptor)
        if calls == 1:
            return details
        return SimpleNamespace(st_size=details.st_size, st_mtime_ns=details.st_mtime_ns + 1)

    monkeypatch.setattr(youtube_execution.os, "fstat", changed_after_read)
    with pytest.raises(youtube_execution._BackendContractError):
        youtube_execution._read_subtitle_file(subtitle, tmp_path, lambda: None)
    assert subtitle.exists() is False


def test_subtitle_cancellation_closes_backend_and_cleans_new_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)

    def create_output(downloader: FakeDownloader, _target: str) -> None:
        nested = _subtitle_output_root(downloader) / "nested"
        nested.mkdir()
        (nested / "partial.vtt").write_text("WEBVTT\n", encoding="utf-8")

    FakeDownloader.on_extract = create_output
    FakeDownloader.raised = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        execute(_subtitle_request(), _subtitle_context())

    instance = FakeDownloader.instances[0]
    assert _subtitle_output_root(instance).exists() is False
    assert instance.close_calls == 1


@pytest.mark.parametrize(
    "failure_type",
    [asyncio.CancelledError, TimeoutError, SubtitleCheckpointError],
    ids=["cancelled", "timeout", "custom"],
)
@pytest.mark.parametrize("close_fails", [False, True], ids=["close-ok", "close-error"])
def test_subtitle_read_checkpoint_failure_propagates_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_type: type[BaseException],
    close_fails: bool,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    FakeDownloader.response = _subtitle_backend_response
    checkpoints = 0
    close_calls = 0
    subtitle_descriptor = -1
    subtitle_closed = False
    failure = failure_type("host checkpoint detail")
    real_open = os.open
    real_close = os.close

    def fail_during_read() -> None:
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints == 3:
            raise failure

    def open_file(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal subtitle_descriptor
        descriptor = real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]
        if Path(path).suffix == ".vtt":  # type: ignore[arg-type]
            subtitle_descriptor = descriptor
        return descriptor

    def close(descriptor: int) -> None:
        nonlocal close_calls, subtitle_closed
        closing_subtitle = descriptor == subtitle_descriptor and not subtitle_closed
        real_close(descriptor)
        if closing_subtitle:
            subtitle_closed = True
            close_calls += 1
        if closing_subtitle and close_fails:
            raise OSError("cleanup detail")

    monkeypatch.setattr(youtube_execution.os, "open", open_file)
    monkeypatch.setattr(youtube_execution.os, "close", close)

    with pytest.raises(failure_type) as raised:
        execute(
            _subtitle_request(),
            _subtitle_context(checkpoint=fail_during_read),
        )

    assert raised.value is failure
    assert checkpoints == 3
    assert close_calls == 1
    instance = FakeDownloader.instances[0]
    assert _subtitle_output_root(instance).exists() is False
    assert instance.close_calls == 1


def test_subtitle_os_read_failure_remains_a_closed_contract_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    FakeDownloader.response = _subtitle_backend_response

    def fail_read(_descriptor: int, _length: int) -> bytes:
        raise OSError("private read detail")

    monkeypatch.setattr(youtube_execution.os, "read", fail_read)

    result = execute(_subtitle_request(), _subtitle_context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_contract_violation"
    assert "private" not in repr(result)
    instance = FakeDownloader.instances[0]
    assert _subtitle_output_root(instance).exists() is False
    assert instance.close_calls == 1


def test_read_video_uses_exact_backend_call_and_closed_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plugins, deno = _install_fake_backend(monkeypatch, tmp_path)
    FakeDownloader.response = _video(uploader=None)

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionSuccessV1)
    assert result.source == "youtube"
    assert result.operation == "read.video"
    assert result.backend_id == "yt-dlp"
    assert result.backend_version == "2026.7.4"
    assert result.truncated is False
    assert result.partial_error_code is None
    assert len(result.items) == 1
    assert result.items[0].schema_id == "youtube.video.v1"
    assert result.items[0].fields == {
        "text": "Description body",
        "native_id": VIDEO_ID,
        "title": "Video title",
        "url": VIDEO_URL,
        "author": "Fallback channel",
        "published_at": "2009-10-25",
        "duration_seconds": 213,
        "view_count": 42,
        "comment_count": 7,
    }
    assert "secret" not in repr(result)
    instance = FakeDownloader.instances[0]
    assert instance.calls == [(VIDEO_URL, False, "Youtube")]
    assert instance.closed is True
    logger = instance.params["logger"]
    assert type(logger).__name__ == "_NullLogger"
    assert instance.params == {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": logger,
        "ignoreerrors": False,
        "cachedir": False,
        "proxy": "",
        "cookiefile": None,
        "cookiesfrombrowser": None,
        "usenetrc": False,
        "netrc_cmd": None,
        "username": None,
        "password": None,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            )
        },
        "mark_watched": False,
        "noplaylist": True,
        "postprocessors": [],
        "js_runtimes": {"deno": {"path": str(deno)}},
        "remote_components": [],
        "retries": 1,
        "fragment_retries": 1,
        "extractor_retries": 1,
        "socket_timeout": 10,
        "concurrent_fragment_downloads": 1,
    }
    assert plugins.value == []
    assert os.environ["YTDLP_NO_PLUGINS"] == "1"


@pytest.mark.parametrize("text", ["abcde", "中文字符五", "😀😀😀😀😀"])
def test_text_at_context_code_point_limit_is_not_truncated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    text: str,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    FakeDownloader.response = _video(description=text)

    result = execute(_request(), _context(maximum_text_characters=5))

    assert isinstance(result, ExecutionSuccessV1)
    assert result.items[0].fields["text"] == text
    assert result.truncated is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("abcdef", "abcde"),
        (" 中文字符六个 ", "中文字符六"),
        ("😀😀😀😀😀😀", "😀😀😀😀😀"),
    ],
)
def test_text_one_over_context_code_point_limit_is_normalized_and_truncated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    text: str,
    expected: str,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    FakeDownloader.response = _video(description=text)

    result = execute(_request(), _context(maximum_text_characters=5))

    assert isinstance(result, ExecutionSuccessV1)
    assert result.items[0].fields["text"] == expected
    assert result.truncated is True


def test_utf8_field_caps_preserve_valid_prefixes_and_existing_source_semantics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    FakeDownloader.response = _video(
        title=("😀" * 256) + "x",
        description=("😀" * 16_384) + "x",
        uploader=("中" * 341) + "文",
    )

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionSuccessV1)
    assert result.items[0].fields["title"] == "😀" * 256
    assert result.items[0].fields["text"] == "😀" * 16_000
    assert result.items[0].fields["author"] == "中" * 341
    assert result.truncated is True


@pytest.mark.parametrize("field", ["title", "uploader"])
@pytest.mark.parametrize("character", ["a", "中", "😀"])
def test_title_and_author_source_caps_are_silent_legacy_projection_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    character: str,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    count = 1_024 // len(character.encode("utf-8"))
    exact = character * count
    FakeDownloader.response = _video(**{field: exact})

    exact_result = execute(_request(), _context())
    FakeDownloader.response = _video(**{field: exact + character})
    one_over_result = execute(_request(), _context())

    assert isinstance(exact_result, ExecutionSuccessV1)
    assert isinstance(one_over_result, ExecutionSuccessV1)
    result_field = "title" if field == "title" else "author"
    assert exact_result.items[0].fields[result_field] == exact
    assert one_over_result.items[0].fields[result_field] == exact
    assert exact_result.truncated is False
    assert one_over_result.truncated is False


@pytest.mark.parametrize("character", ["a", "中", "😀"])
def test_description_source_cap_has_exact_and_one_character_over_boundaries(
    character: str,
) -> None:
    maximum = 64 * 1_024
    count = maximum // len(character.encode("utf-8"))
    exact = character * count

    assert youtube_execution._optional_projected_text(exact, maximum) == exact
    assert youtube_execution._optional_projected_text(exact + character, maximum) == exact


def test_source_truncation_does_not_leave_denormalized_trailing_space() -> None:
    value = ("a" * 1_023) + " b"

    assert youtube_execution._optional_projected_text(value, 1_024) == "a" * 1_023


def test_description_falls_back_to_title_and_blank_uploader_does_not_fall_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    FakeDownloader.response = _video(description=" \n ", uploader=" \t ")

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionSuccessV1)
    assert result.items[0].fields["text"] == "Video title"
    assert result.items[0].fields["author"] is None


@pytest.mark.parametrize(
    ("duration", "expected"),
    [(0, 0), (-0.0, 0), (300.0, 300), ((1 << 53) - 1, (1 << 53) - 1)],
)
def test_duration_accepts_only_bounded_integral_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    duration: object,
    expected: int,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    FakeDownloader.response = _video(duration=duration)

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionSuccessV1)
    assert result.items[0].fields["duration_seconds"] == expected


@pytest.mark.parametrize(
    "duration",
    [True, -1, 300.5, math.nan, math.inf, -math.inf, 1 << 53, "300"],
)
def test_duration_rejects_non_projectable_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    duration: object,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    FakeDownloader.response = _video(duration=duration)

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_contract_violation"


@pytest.mark.parametrize("field", ["view_count", "comment_count"])
@pytest.mark.parametrize("value", [None, 0, (1 << 53) - 1])
def test_counter_fields_accept_nullable_bounded_integers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    value: int | None,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    FakeDownloader.response = _video(**{field: value})

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionSuccessV1)
    assert result.items[0].fields[field] == value


@pytest.mark.parametrize("field", ["view_count", "comment_count"])
@pytest.mark.parametrize("value", [True, -1, 0.0, 1 << 53, "1"])
def test_counter_fields_reject_non_projectable_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    FakeDownloader.response = _video(**{field: value})

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_contract_violation"


@pytest.mark.parametrize(
    ("upload_date", "expected"),
    [
        ("19700101", "1970-01-01"),
        ("20240229", "2024-02-29"),
        (None, None),
    ],
)
def test_dates_are_normalized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    upload_date: object,
    expected: str | None,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    FakeDownloader.response = _video(upload_date=upload_date)

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionSuccessV1)
    assert result.items[0].fields["published_at"] == expected


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": "aaaaaaaaaaa"},
        {"title": None},
        {"description": 1},
        {"view_count": True},
        {"comment_count": -1},
        {"upload_date": "19691231"},
        {"upload_date": "20230229"},
        {"upload_date": "２０２４０２２９"},
        {"title": "value\x00private"},
    ],
)
def test_projection_drift_returns_contract_violation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    overrides: Mapping[str, object],
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    FakeDownloader.response = _video(**dict(overrides))

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_contract_violation"
    assert "private" not in repr(result)


def test_hostile_mapping_is_redacted_as_contract_violation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    FakeDownloader.response = BadMapping()

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_contract_violation"
    assert "private" not in repr(result)
    assert FakeDownloader.instances[0].close_calls == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com:443/watch?v=dQw4w9WgXcQ",
        "https://user@www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=private",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ#fragment",
        "https://www.youtube.com/watch?v=%64Qw4w9WgXcQ",
        "https://www.youtube.com/watch?v=invalid",
    ],
)
def test_noncanonical_urls_are_rejected_before_runtime_import(url: str) -> None:
    result = execute(_request(url), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "invalid_request"
    assert result.backend_id is None


def test_dependency_absence_and_version_drift_are_closed_setup_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)

    def missing(_: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(youtube_execution, "version", missing)
    unavailable = execute(_request(), _context())
    monkeypatch.setattr(
        youtube_execution,
        "version",
        lambda name: "0" if name == "yt-dlp-ejs" else VERSIONS[name],
    )
    incompatible = execute(_request(), _context())

    assert isinstance(unavailable, ExecutionFailureV1)
    assert unavailable.error_code == "backend_unavailable"
    assert isinstance(incompatible, ExecutionFailureV1)
    assert incompatible.error_code == "backend_incompatible"
    assert FakeDownloader.instances == []


@pytest.mark.parametrize("kind", ["ejs", "plugins", "entry_point"])
def test_backend_entry_closure_drift_is_incompatible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
) -> None:
    plugins: object = SimpleNamespace(value=[])
    ejs_version = "0.8.0"
    entry_point: object = FakeDownloader
    if kind == "ejs":
        ejs_version = "0"
    elif kind == "plugins":
        plugins = object()
    else:
        entry_point = object()
    _install_fake_backend(
        monkeypatch,
        tmp_path,
        ejs_version=ejs_version,
        plugin_dirs=plugins,
        youtube_dl=entry_point,
    )

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_incompatible"
    assert FakeDownloader.instances == []


def test_constructed_downloader_without_extract_info_is_incompatible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    instances: list[DownloaderWithoutEntryPoint] = []

    class DownloaderWithoutEntryPoint:
        def __init__(self, _params: dict[str, object]) -> None:
            self.closed = False
            instances.append(self)

        def close(self) -> None:
            self.closed = True

    _install_fake_backend(
        monkeypatch,
        tmp_path,
        youtube_dl=DownloaderWithoutEntryPoint,
    )

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_incompatible"
    assert instances[0].closed is True


def test_missing_extract_info_remains_incompatible_when_close_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    instances: list[DownloaderWithoutEntryPoint] = []

    class DownloaderWithoutEntryPoint:
        def __init__(self, _params: dict[str, object]) -> None:
            self.close_calls = 0
            instances.append(self)

        def close(self) -> None:
            self.close_calls += 1
            raise RuntimeError("private close detail")

    _install_fake_backend(
        monkeypatch,
        tmp_path,
        youtube_dl=DownloaderWithoutEntryPoint,
    )

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_incompatible"
    assert instances[0].close_calls == 1
    assert "private" not in repr(result)


@pytest.mark.parametrize("kind", ["missing", "raising_property"])
def test_constructed_downloader_without_stable_close_is_incompatible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
) -> None:
    calls = 0

    class DownloaderWithoutClose:
        def __init__(self, _params: dict[str, object]) -> None:
            return None

        def extract_info(self, _target: str, *, download: bool, ie_key: str) -> object:
            nonlocal calls
            calls += 1
            return _video()

    class DownloaderWithRaisingClose(DownloaderWithoutClose):
        @property
        def close(self) -> object:
            raise RuntimeError("private close property")

    backend = DownloaderWithoutClose if kind == "missing" else DownloaderWithRaisingClose
    _install_fake_backend(monkeypatch, tmp_path, youtube_dl=backend)

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_incompatible"
    assert calls == 0
    assert "private" not in repr(result)


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable shape checks")
@pytest.mark.parametrize("kind", ["missing", "directory", "symlink", "hardlink", "not_executable"])
def test_adjacent_deno_gate_rejects_unexpected_shapes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
) -> None:
    scripts = tmp_path / "bin"
    scripts.mkdir()
    executable = scripts / "python"
    executable.write_bytes(b"")
    deno = scripts / "deno"
    if kind == "directory":
        deno.mkdir()
    elif kind == "symlink":
        target = tmp_path / "real-deno"
        target.write_bytes(b"deno")
        target.chmod(0o700)
        deno.symlink_to(target)
    elif kind == "hardlink":
        target = tmp_path / "real-deno"
        target.write_bytes(b"deno")
        target.chmod(0o700)
        os.link(target, deno)
    elif kind == "not_executable":
        deno.write_bytes(b"deno")
        deno.chmod(0o600)

    import_calls: list[str] = []

    def record_import(name: str) -> object:
        import_calls.append(name)
        raise AssertionError(f"unexpected backend import: {name}")

    monkeypatch.setattr(youtube_execution, "version", lambda name: VERSIONS[name])
    monkeypatch.setattr(youtube_execution, "import_module", record_import)
    code, _ = youtube_execution._load_backend(executable=str(executable))

    expected = "backend_unavailable" if kind == "missing" else "backend_incompatible"
    assert code == expected
    assert import_calls == []


@pytest.mark.parametrize(
    ("error_name", "status", "expected"),
    [
        ("HTTPError", 404, "not_found"),
        ("HTTPError", 429, "rate_limit"),
        ("HTTPError", 401, "authentication"),
        ("HTTPError", 403, "authorization"),
        ("HTTPError", 500, "permanent"),
        ("GeoRestrictedError", None, "authorization"),
        ("TransportError", None, "transient"),
        ("IncompleteRead", None, "transient"),
        ("TimeoutError", None, "transient"),
        ("ConnectionError", None, "transient"),
        ("SSLError", None, "transient"),
        ("SocketError", None, "transient"),
        ("UnknownBackendError", None, "permanent"),
    ],
)
def test_backend_errors_are_allowlisted_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    error_name: str,
    status: int | None,
    expected: str,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    error_type = type(error_name, (Exception,), {})
    error = error_type("Cookie=private /Users/private?token=secret")
    if status is not None:
        error.status = status  # type: ignore[attr-defined]
    FakeDownloader.raised = error

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == expected
    assert "private" not in repr(result)
    assert FakeDownloader.instances[0].closed is True


def test_os_error_is_transient_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    FakeDownloader.raised = OSError("private filesystem or socket detail")

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "transient"
    assert "private" not in repr(result)


def test_yt_dlp_exc_info_wrapper_is_classified_from_nested_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    inner_type = type("SocketError", (Exception,), {})
    inner = inner_type("inner private")
    wrapper = RuntimeError("outer private")
    wrapper.exc_info = (inner_type, inner, None)  # type: ignore[attr-defined]
    FakeDownloader.raised = wrapper

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "transient"
    assert "private" not in repr(result)


def test_explicit_http_cause_precedes_unrelated_os_error_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    http_error_type = type("HTTPError", (Exception,), {})
    http_error = http_error_type("cause Cookie=private")
    http_error.status = 404  # type: ignore[attr-defined]
    try:
        raise OSError("context /Users/private?token=secret")
    except OSError:
        try:
            raise RuntimeError("wrapper private") from http_error
        except RuntimeError as error:
            FakeDownloader.raised = error

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "not_found"
    assert "private" not in repr(result)


def test_exc_info_http_error_precedes_unrelated_os_error_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    http_error_type = type("HTTPError", (Exception,), {})
    http_error = http_error_type("exc_info Cookie=private")
    http_error.status = 404  # type: ignore[attr-defined]
    wrapper = RuntimeError("wrapper private")
    wrapper.exc_info = (http_error_type, http_error, None)  # type: ignore[attr-defined]
    wrapper.__context__ = OSError("context /Users/private?token=secret")
    FakeDownloader.raised = wrapper

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "not_found"
    assert "private" not in repr(result)


@pytest.mark.parametrize("attribute", ["status", "exc_info"])
def test_exception_classifier_is_total_for_raising_properties(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    attribute: str,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)

    def raise_private(_error: BaseException) -> object:
        raise RuntimeError("private classifier detail")

    name = "HTTPError" if attribute == "status" else "UnknownBackendError"
    error_type = type(name, (Exception,), {attribute: property(raise_private)})
    FakeDownloader.raised = error_type("private backend detail")

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "permanent"
    assert "private" not in repr(result)


def test_nested_backend_error_is_classified_and_downloader_is_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    transport_type = type("ConnectionError", (Exception,), {})
    wrapper = RuntimeError("outer private")
    wrapper.__cause__ = transport_type("inner private")
    FakeDownloader.raised = wrapper

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "transient"
    assert FakeDownloader.instances[0].closed is True


def test_successful_close_failure_is_redacted_permanent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    FakeDownloader.close_raised = RuntimeError("private close detail")

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "permanent"
    assert "private" not in repr(result)


def test_failure_path_close_failure_preserves_extraction_classification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    transport_type = type("TransportError", (Exception,), {})
    FakeDownloader.raised = transport_type("private transport detail")
    FakeDownloader.close_raised = RuntimeError("private close detail")

    result = execute(_request(), _context())

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "transient"
    assert "private" not in repr(result)
    assert FakeDownloader.instances[0].close_calls == 1


def test_backend_cancellation_survives_close_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    FakeDownloader.raised = asyncio.CancelledError()
    FakeDownloader.close_raised = RuntimeError("private close detail")

    with pytest.raises(asyncio.CancelledError):
        execute(_request(), _context())

    assert FakeDownloader.instances[0].close_calls == 1


@pytest.mark.parametrize("cancel_at", [1, 2, 3, 4])
def test_checkpoint_cancellation_propagates_at_every_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cancel_at: int,
) -> None:
    _install_fake_backend(monkeypatch, tmp_path)
    checkpoints = 0

    def cancelled() -> None:
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints == cancel_at:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        execute(_request(), _context(checkpoint=cancelled))

    assert checkpoints == cancel_at
    if cancel_at <= 2:
        assert FakeDownloader.instances == []
    else:
        assert FakeDownloader.instances[0].close_calls == 1
