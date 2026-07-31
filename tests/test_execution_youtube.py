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
    execute,
)

VIDEO_ID = "dQw4w9WgXcQ"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
VERSIONS = {"yt-dlp": "2026.7.4", "yt-dlp-ejs": "0.8.0", "deno": "2.8.3"}


def _request(url: str = VIDEO_URL) -> ExecutionRequestV1:
    return ExecutionRequestV1(
        PROTOCOL_VERSION,
        "youtube",
        "read.video",
        {"url": url},
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
    response: object = _video()
    raised: BaseException | None = None
    close_raised: Exception | None = None

    def __init__(self, params: dict[str, object]) -> None:
        self.params = params
        self.calls: list[tuple[str, bool, str]] = []
        self.closed = False
        self.close_calls = 0
        self.instances.append(self)

    def extract_info(self, target: str, *, download: bool, ie_key: str) -> object:
        self.calls.append((target, download, ie_key))
        if self.raised is not None:
            raise self.raised
        return self.response

    def close(self) -> None:
        self.closed = True
        self.close_calls += 1
        if self.close_raised is not None:
            raise self.close_raised


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
    yield
    FakeDownloader.instances.clear()
    FakeDownloader.response = _video()
    FakeDownloader.raised = None
    FakeDownloader.close_raised = None


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
