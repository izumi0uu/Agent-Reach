"""Fork-owned YouTube video metadata execution through fixed yt-dlp APIs."""

from __future__ import annotations

import math
import os
import stat
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Final, cast

from .contracts import (
    MAX_TEXT_CHARACTERS,
    PROTOCOL_VERSION,
    ExecutionContextV1,
    ExecutionErrorCodeV1,
    ExecutionFailureV1,
    ExecutionItemV1,
    ExecutionRequestV1,
    ExecutionResultV1,
    ExecutionSuccessV1,
    _valid_youtube_video_url,
)

_BACKEND_DISTRIBUTION: Final = "yt-dlp"
_BACKEND_ID: Final = "yt-dlp"
_BACKEND_VERSION: Final = "2026.7.4"
_EJS_DISTRIBUTION: Final = "yt-dlp-ejs"
_EJS_VERSION: Final = "0.8.0"
_DENO_DISTRIBUTION: Final = "deno"
_DENO_VERSION: Final = "2.8.3"
_MAX_TITLE_BYTES: Final = 1_024
_MAX_DESCRIPTION_BYTES: Final = 64 * 1_024
_MAX_AUTHOR_BYTES: Final = 1_024
_MAX_RESULT_INTEGER: Final = (1 << 53) - 1
_VIDEO_ID_CHARACTERS: Final = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
)
_FIXED_USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


class _BackendContractError(Exception):
    pass


class _BackendUnavailableError(Exception):
    pass


class _BackendIncompatibleError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _LoadedBackend:
    factory: Callable[[dict[str, object]], object]
    deno: Path


class _NullLogger:
    def debug(self, _message: object) -> None:
        return None

    def warning(self, _message: object) -> None:
        return None

    def error(self, _message: object) -> None:
        return None


def execute_youtube(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
) -> ExecutionResultV1:
    """Execute one registry-validated YouTube read-video request."""

    error_code, backend = _load_backend()
    if error_code is not None or backend is None:
        return _failure(request, error_code or "backend_unavailable")

    context.checkpoint()
    url = request.arguments.get("url")
    if not _valid_youtube_video_url(url):
        return _failure(request, "backend_contract_violation")
    try:
        raw = _invoke_backend(backend, cast(str, url))
    except _BackendIncompatibleError:
        return _failure(request, "backend_incompatible")
    except _BackendContractError:
        return _failure(request, "backend_contract_violation")
    except Exception as error:
        return _failure(request, _backend_error_code(error))
    context.checkpoint()

    try:
        result = _project_success(request, context, raw)
    except Exception:
        return _failure(request, "backend_contract_violation")
    context.checkpoint()
    return result


def _load_backend(
    *,
    executable: str | None = None,
) -> tuple[ExecutionErrorCodeV1 | None, _LoadedBackend | None]:
    expected = {
        _BACKEND_DISTRIBUTION: _BACKEND_VERSION,
        _EJS_DISTRIBUTION: _EJS_VERSION,
        _DENO_DISTRIBUTION: _DENO_VERSION,
    }
    try:
        installed = {name: version(name) for name in expected}
    except (PackageNotFoundError, ValueError):
        return "backend_unavailable", None
    except Exception:
        return "backend_unavailable", None
    if installed != expected:
        return "backend_incompatible", None

    try:
        deno = _deno_executable(sys.executable if executable is None else executable)
    except _BackendUnavailableError:
        return "backend_unavailable", None
    except _BackendIncompatibleError:
        return "backend_incompatible", None

    os.environ["YTDLP_NO_PLUGINS"] = "1"
    try:
        yt_dlp = import_module("yt_dlp")
        yt_dlp_ejs = import_module("yt_dlp_ejs")
        globals_module = import_module("yt_dlp.globals")
    except Exception:
        return "backend_incompatible", None
    if getattr(yt_dlp_ejs, "version", None) != _EJS_VERSION:
        return "backend_incompatible", None
    plugin_dirs = getattr(globals_module, "plugin_dirs", None)
    if plugin_dirs is None or not hasattr(plugin_dirs, "value"):
        return "backend_incompatible", None
    try:
        plugin_dirs.value = []
    except Exception:
        return "backend_incompatible", None
    youtube_dl = getattr(yt_dlp, "YoutubeDL", None)
    if not callable(youtube_dl):
        return "backend_incompatible", None
    return (
        None,
        _LoadedBackend(
            cast(Callable[[dict[str, object]], object], youtube_dl),
            deno,
        ),
    )


def _deno_executable(executable: str) -> Path:
    scripts = Path(executable).absolute().parent
    candidate = scripts / ("deno.exe" if os.name == "nt" else "deno")
    try:
        details = candidate.lstat()
    except OSError:
        raise _BackendUnavailableError("backend deno missing") from None
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_nlink != 1
        or not os.access(candidate, os.X_OK)
    ):
        raise _BackendIncompatibleError("backend deno invalid")
    return candidate


def _invoke_backend(backend: _LoadedBackend, url: str) -> object:
    downloader = backend.factory(_fixed_options(backend.deno))
    try:
        close = getattr(downloader, "close", None)
    except Exception:
        raise _BackendIncompatibleError("backend close entry point invalid") from None
    if not callable(close):
        raise _BackendIncompatibleError("backend close entry point invalid")

    try:
        extract_info = getattr(downloader, "extract_info", None)
        if not callable(extract_info):
            raise _BackendIncompatibleError("backend entry point invalid")
        result = cast(Callable[..., object], extract_info)(
            url,
            download=False,
            ie_key="Youtube",
        )
    except BaseException:
        _close_after_failure(cast(Callable[[], object], close))
        raise
    _close_after_success(cast(Callable[[], object], close))
    return result


def _close_after_failure(close: Callable[[], object]) -> None:
    try:
        close()
    except Exception:
        pass


def _close_after_success(close: Callable[[], object]) -> None:
    close()


def _fixed_options(deno: Path) -> dict[str, object]:
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _NullLogger(),
        "ignoreerrors": False,
        "cachedir": False,
        "proxy": "",
        "cookiefile": None,
        "cookiesfrombrowser": None,
        "usenetrc": False,
        "netrc_cmd": None,
        "username": None,
        "password": None,
        "http_headers": {"User-Agent": _FIXED_USER_AGENT},
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


def _project_success(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
    value: object,
) -> ExecutionSuccessV1:
    info = _mapping(value)
    video_id = _video_id(info.get("id"))
    requested_url = request.arguments.get("url")
    if type(requested_url) is not str or requested_url != _video_url(video_id):
        raise _BackendContractError("backend identity invalid")

    title = _required_projected_text(info.get("title"), _MAX_TITLE_BYTES)
    description = _optional_projected_text(
        info.get("description"),
        _MAX_DESCRIPTION_BYTES,
    )
    uploader = info.get("uploader")
    if uploader is None:
        uploader = info.get("channel")
    text = description or title
    maximum_text = min(
        context.limits.maximum_text_characters,
        MAX_TEXT_CHARACTERS,
    )
    truncated = len(text) > maximum_text
    item = ExecutionItemV1(
        "youtube.video.v1",
        {
            "text": text[:maximum_text],
            "native_id": video_id,
            "title": title,
            "url": _video_url(video_id),
            "author": _optional_projected_text(uploader, _MAX_AUTHOR_BYTES),
            "published_at": _upload_date(info.get("upload_date")),
            "duration_seconds": _optional_duration_seconds(info.get("duration")),
            "view_count": _optional_integer(info.get("view_count")),
            "comment_count": _optional_integer(info.get("comment_count")),
        },
    )
    return ExecutionSuccessV1(
        PROTOCOL_VERSION,
        "youtube",
        "read.video",
        _BACKEND_ID,
        _BACKEND_VERSION,
        (item,),
        truncated=truncated,
    )


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(type(key) is str for key in value):
        raise _BackendContractError("backend data invalid")
    return cast(Mapping[str, object], value)


def _required_projected_text(value: object, maximum_bytes: int) -> str:
    text = _optional_projected_text(value, maximum_bytes)
    if text is None:
        raise _BackendContractError("backend text invalid")
    return text


def _optional_projected_text(value: object, maximum_bytes: int) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise _BackendContractError("backend text invalid")
    normalized = " ".join(value.split())
    if not normalized:
        return None
    return _truncate_utf8(normalized, maximum_bytes)


def _truncate_utf8(value: str, maximum_bytes: int) -> str:
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise _BackendContractError("backend text invalid") from None
    if len(encoded) <= maximum_bytes:
        return value
    end = maximum_bytes
    while end > 0:
        try:
            return encoded[:end].decode("utf-8", errors="strict").rstrip(" ")
        except UnicodeDecodeError:
            end -= 1
    raise _BackendContractError("backend text invalid")


def _optional_integer(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= _MAX_RESULT_INTEGER:
        raise _BackendContractError("backend integer invalid")
    return value


def _optional_duration_seconds(value: object) -> int | None:
    if type(value) is float:
        if not math.isfinite(value) or not value.is_integer():
            raise _BackendContractError("backend integer invalid")
        value = int(value)
    return _optional_integer(value)


def _upload_date(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str or len(value) != 8 or not value.isascii() or not value.isdigit():
        raise _BackendContractError("backend date invalid")
    year, month, day = int(value[:4]), int(value[4:6]), int(value[6:])
    try:
        parsed = date(year, month, day)
    except ValueError:
        raise _BackendContractError("backend date invalid") from None
    if parsed.year < 1970:
        raise _BackendContractError("backend date invalid")
    return parsed.isoformat()


def _video_id(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 11
        or any(character not in _VIDEO_ID_CHARACTERS for character in value)
    ):
        raise _BackendContractError("backend identity invalid")
    return value


def _video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _backend_error_code(error: Exception) -> ExecutionErrorCodeV1:
    try:
        for current in _exception_chain(error):
            name = type(current).__name__
            status_code = getattr(current, "status", None)
            if name == "HTTPError" and type(status_code) is int:
                if status_code == 404:
                    return "not_found"
                if status_code == 429:
                    return "rate_limit"
                if status_code == 401:
                    return "authentication"
                if status_code == 403:
                    return "authorization"
            if name == "GeoRestrictedError":
                return "authorization"
            if name in {
                "TransportError",
                "IncompleteRead",
                "TimeoutError",
                "ConnectionError",
                "SSLError",
                "SocketError",
            } or isinstance(current, OSError):
                return "transient"
    except Exception:
        return "permanent"
    return "permanent"


def _exception_chain(error: Exception) -> tuple[BaseException, ...]:
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    result: list[BaseException] = []
    while pending and len(result) < 16:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        result.append(current)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
        exc_info = getattr(current, "exc_info", None)
        if (
            isinstance(exc_info, tuple)
            and len(exc_info) == 3
            and isinstance(exc_info[1], BaseException)
        ):
            pending.append(exc_info[1])
    return tuple(result)


def _failure(
    request: ExecutionRequestV1,
    error_code: ExecutionErrorCodeV1,
) -> ExecutionFailureV1:
    return ExecutionFailureV1(
        protocol_version=PROTOCOL_VERSION,
        source="youtube",
        operation=request.operation,
        backend_id=_BACKEND_ID,
        backend_version=_BACKEND_VERSION,
        error_code=error_code,
    )
