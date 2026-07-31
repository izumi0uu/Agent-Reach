"""Fork-owned YouTube execution through fixed yt-dlp APIs."""

from __future__ import annotations

import math
import os
import stat
import sys
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Final, cast

from .contracts import (
    _YOUTUBE_LANGUAGE,
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
_MAX_SEARCH_DESCRIPTION_BYTES: Final = 4 * 1_024
_MAX_AUTHOR_BYTES: Final = 1_024
_MAX_SUBTITLE_FILE_BYTES: Final = 512 * 1_024
_MAX_SUBTITLE_TEXT_BYTES: Final = 256 * 1_024
_MAX_RESULT_INTEGER: Final = (1 << 53) - 1
_DEFAULT_SUBTITLE_LANGUAGES: Final = ("zh-Hans", "zh", "en")
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


class _BackendNotFoundError(Exception):
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
    """Execute one registry-validated YouTube request."""

    checkpoint_failure: BaseException | None = None

    def backend_checkpoint() -> None:
        nonlocal checkpoint_failure
        try:
            context.checkpoint()
        except BaseException as error:
            checkpoint_failure = error
            raise

    error_code, backend = _load_backend()
    if error_code is not None or backend is None:
        return _failure(request, error_code or "backend_unavailable")

    context.checkpoint()
    try:
        raw = _invoke_backend(
            backend,
            request,
            context,
            checkpoint=backend_checkpoint,
        )
    except Exception as error:
        if error is checkpoint_failure:
            raise
        if isinstance(error, _BackendIncompatibleError):
            return _failure(request, "backend_incompatible")
        if isinstance(error, _BackendNotFoundError):
            return _failure(request, "not_found")
        if isinstance(error, _BackendContractError):
            return _failure(request, "backend_contract_violation")
        return _failure(request, _backend_error_code(error))
    context.checkpoint()

    try:
        result = (
            raw
            if type(raw) is ExecutionSuccessV1 and request.operation == "read.subtitles"
            else _project_success(request, context, raw)
        )
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


def _invoke_backend(
    backend: _LoadedBackend,
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
    *,
    checkpoint: Callable[[], None],
) -> object:
    private_root = _private_workspace_root() if request.operation == "read.subtitles" else None
    language = request.arguments.get("language")
    subtitle_languages: tuple[str, ...] | None = None
    if private_root is not None:
        if language is not None and type(language) is not str:
            raise _BackendContractError("request invalid")
        subtitle_languages = (language,) if language is not None else _DEFAULT_SUBTITLE_LANGUAGES
    existing_entries = _workspace_entry_names(private_root) if private_root is not None else None
    try:
        return _invoke_downloader(
            backend,
            request,
            context,
            private_root=private_root,
            subtitle_languages=subtitle_languages,
            checkpoint=checkpoint,
        )
    finally:
        if private_root is not None and existing_entries is not None:
            _cleanup_new_workspace_files(private_root, existing_entries)


def _invoke_downloader(
    backend: _LoadedBackend,
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
    *,
    private_root: Path | None,
    subtitle_languages: tuple[str, ...] | None,
    checkpoint: Callable[[], None],
) -> object:
    downloader = backend.factory(
        _fixed_options(
            backend.deno,
            private_root=private_root,
            subtitle_languages=subtitle_languages,
        )
    )
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
        target, download, ie_key = _backend_call(request, context)
        result = cast(Callable[..., object], extract_info)(
            target,
            download=download,
            ie_key=ie_key,
        )
        if request.operation == "read.subtitles":
            if private_root is None:
                raise _BackendContractError("private workspace missing")
            result = _project_subtitle_success(
                request,
                context,
                result,
                private_root,
                checkpoint,
            )
    except BaseException:
        _close_after_failure(cast(Callable[[], object], close))
        raise
    _close_after_success(cast(Callable[[], object], close))
    return result


def _workspace_entry_names(root: Path) -> frozenset[str]:
    try:
        with os.scandir(root) as entries:
            return frozenset(entry.name for entry in entries)
    except OSError:
        raise _BackendContractError("private workspace invalid") from None


def _cleanup_new_workspace_files(root: Path, existing_entries: frozenset[str]) -> None:
    try:
        with os.scandir(root) as entries:
            candidates = tuple(
                entry.path for entry in entries if entry.name not in existing_entries
            )
    except OSError:
        return
    for candidate in candidates:
        try:
            details = os.lstat(candidate)
            if not stat.S_ISDIR(details.st_mode):
                os.unlink(candidate)
        except OSError:
            pass


def _close_after_failure(close: Callable[[], object]) -> None:
    try:
        close()
    except Exception:
        pass


def _close_after_success(close: Callable[[], object]) -> None:
    close()


def _fixed_options(
    deno: Path,
    *,
    private_root: Path | None = None,
    subtitle_languages: tuple[str, ...] | None = None,
) -> dict[str, object]:
    options: dict[str, object] = {
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
    if private_root is not None and subtitle_languages is not None:
        options.update(
            {
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": list(subtitle_languages),
                "subtitlesformat": "vtt",
                "skip_download": True,
                "paths": {
                    "home": str(private_root),
                    "temp": str(private_root),
                    "subtitle": str(private_root),
                },
                "outtmpl": {
                    "default": str(private_root / "%(id)s.%(ext)s"),
                    "subtitle": str(private_root / "%(id)s.%(ext)s"),
                },
                "overwrites": True,
                "nopart": True,
                "max_filesize": _MAX_SUBTITLE_FILE_BYTES,
                "buffersize": 16 * 1_024,
                "http_chunk_size": 16 * 1_024,
                "noresizebuffer": True,
                "progress_hooks": [_bounded_progress],
            }
        )
    return options


def _private_workspace_root() -> Path:
    try:
        root = Path.cwd()
        details = root.lstat()
        resolved = root.resolve(strict=True)
    except OSError:
        raise _BackendContractError("private workspace invalid") from None
    if not stat.S_ISDIR(details.st_mode):
        raise _BackendContractError("private workspace invalid")
    return resolved


def _backend_call(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
) -> tuple[str, bool, str]:
    if request.operation == "search.videos":
        query = request.arguments.get("query")
        if type(query) is not str:
            raise _BackendContractError("request invalid")
        return (
            f"ytsearch{_effective_limit(request, context)}:{query}",
            False,
            "YoutubeSearch",
        )
    url = request.arguments.get("url")
    if not _valid_youtube_video_url(url):
        raise _BackendContractError("request invalid")
    if request.operation == "read.video":
        return cast(str, url), False, "Youtube"
    if request.operation == "read.subtitles":
        return cast(str, url), True, "Youtube"
    raise _BackendContractError("request invalid")


def _project_success(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
    value: object,
) -> ExecutionSuccessV1:
    if request.operation == "search.videos":
        return _project_search_success(request, context, value)
    if request.operation != "read.video":
        raise _BackendContractError("request invalid")
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


def _project_search_success(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
    value: object,
) -> ExecutionSuccessV1:
    info = _mapping(value)
    entries = info.get("entries")
    limit = _effective_limit(request, context)
    if type(entries) is not list or len(entries) > limit:
        raise _BackendContractError("backend search invalid")
    maximum_text = min(context.limits.maximum_text_characters, MAX_TEXT_CHARACTERS)
    projected = tuple(
        _project_video_item(entry, maximum_text=maximum_text, search_result=True)
        for entry in entries
    )
    items = tuple(item for item, _ in projected)
    native_ids = tuple(item.fields["native_id"] for item in items)
    if len(set(native_ids)) != len(native_ids):
        raise _BackendContractError("backend search invalid")
    return ExecutionSuccessV1(
        PROTOCOL_VERSION,
        "youtube",
        "search.videos",
        _BACKEND_ID,
        _BACKEND_VERSION,
        items,
        truncated=_request_was_narrowed(request, context)
        or any(truncated for _, truncated in projected),
    )


def _project_video_item(
    value: object,
    *,
    maximum_text: int,
    search_result: bool,
) -> tuple[ExecutionItemV1, bool]:
    info = _mapping(value)
    video_id = _video_id(info.get("id"))
    title = _required_projected_text(info.get("title"), _MAX_TITLE_BYTES)
    description = _optional_projected_text(
        info.get("description"),
        _MAX_SEARCH_DESCRIPTION_BYTES if search_result else _MAX_DESCRIPTION_BYTES,
    )
    uploader = info.get("uploader")
    if uploader is None:
        uploader = info.get("channel")
    text = description or title
    return (
        ExecutionItemV1(
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
        ),
        len(text) > maximum_text,
    )


def _project_subtitle_success(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
    value: object,
    root: Path,
    checkpoint: Callable[[], None],
) -> ExecutionSuccessV1:
    info = _mapping(value)
    video_id = _video_id(info.get("id"))
    requested_url = request.arguments.get("url")
    if type(requested_url) is not str or requested_url != _video_url(video_id):
        raise _BackendContractError("backend identity invalid")
    requested_value = info.get("requested_subtitles")
    if not isinstance(requested_value, Mapping) or not requested_value:
        raise _BackendNotFoundError("backend subtitle missing")
    requested = _mapping(requested_value)
    language = request.arguments.get("language")
    if language is not None and (
        type(language) is not str or _YOUTUBE_LANGUAGE.fullmatch(language) is None
    ):
        raise _BackendContractError("request invalid")
    selected_language = _select_language(requested, language)
    selected = _mapping(requested.get(selected_language))
    if selected.get("ext") != "vtt":
        raise _BackendContractError("backend subtitle format invalid")
    filepath = selected.get("filepath")
    if type(filepath) is not str:
        raise _BackendContractError("backend subtitle path invalid")
    manual = info.get("subtitles")
    automatic = info.get("automatic_captions")
    if isinstance(manual, Mapping) and selected_language in manual:
        origin = "manual"
    elif isinstance(automatic, Mapping) and selected_language in automatic:
        origin = "automatic"
    else:
        raise _BackendContractError("backend subtitle origin invalid")
    text, source_truncated = _read_subtitle_file(
        Path(filepath),
        root,
        checkpoint,
    )
    maximum_text = min(context.limits.maximum_text_characters, MAX_TEXT_CHARACTERS)
    item = ExecutionItemV1(
        "youtube.subtitle.v1",
        {
            "text": text[:maximum_text],
            "native_id": video_id,
            "title": _required_projected_text(info.get("title"), _MAX_TITLE_BYTES),
            "url": _video_url(video_id),
            "language": selected_language,
            "origin": origin,
        },
    )
    return ExecutionSuccessV1(
        PROTOCOL_VERSION,
        "youtube",
        "read.subtitles",
        _BACKEND_ID,
        _BACKEND_VERSION,
        (item,),
        truncated=source_truncated or len(text) > maximum_text,
    )


def _select_language(requested: Mapping[str, object], language: str | None) -> str:
    candidates = (language,) if language is not None else _DEFAULT_SUBTITLE_LANGUAGES
    for candidate in candidates:
        if candidate != "live_chat" and candidate in requested:
            return candidate
    raise _BackendNotFoundError("backend subtitle missing")


def _read_subtitle_file(
    path: Path,
    root: Path,
    checkpoint: Callable[[], None],
) -> tuple[str, bool]:
    candidate = path.absolute()
    try:
        resolved_root = root.resolve(strict=True)
        details = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise _BackendContractError("backend subtitle path invalid") from None
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_nlink != 1
        or not resolved.is_relative_to(resolved_root)
    ):
        raise _BackendContractError("backend subtitle path invalid")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = -1
    checkpoint_failure: BaseException | None = None
    try:
        if details.st_size > _MAX_SUBTITLE_FILE_BYTES:
            raise _BackendContractError("backend subtitle too large")
        descriptor = os.open(resolved, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino, opened.st_size)
            != (details.st_dev, details.st_ino, details.st_size)
        ):
            raise _BackendContractError("backend subtitle path invalid")
        output = bytearray()
        while len(output) <= _MAX_SUBTITLE_FILE_BYTES:
            try:
                checkpoint()
            except BaseException as error:
                checkpoint_failure = error
                raise
            chunk = os.read(
                descriptor,
                min(64 * 1_024, _MAX_SUBTITLE_FILE_BYTES + 1 - len(output)),
            )
            if not chunk:
                break
            output.extend(chunk)
        final_details = os.fstat(descriptor)
        if (
            final_details.st_size != opened.st_size
            or final_details.st_mtime_ns != opened.st_mtime_ns
        ):
            raise _BackendContractError("backend subtitle changed")
        data = bytes(output)
    except OSError as error:
        if error is checkpoint_failure:
            raise
        raise _BackendContractError("backend subtitle read invalid") from None
    finally:
        active_error = sys.exc_info()[1]
        close_failed = False
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                close_failed = active_error is None
        _unlink_same_file(resolved, details)
        if close_failed:
            raise _BackendContractError("backend subtitle close invalid") from None
    if len(data) > _MAX_SUBTITLE_FILE_BYTES:
        raise _BackendContractError("backend subtitle too large")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError:
        raise _BackendContractError("backend subtitle encoding invalid") from None
    if not text.lstrip("\ufeff\r\n ").startswith("WEBVTT"):
        raise _BackendContractError("backend subtitle format invalid")
    return _truncate_utf8_with_flag(text, _MAX_SUBTITLE_TEXT_BYTES)


def _unlink_same_file(path: Path, expected: os.stat_result) -> None:
    try:
        current = path.lstat()
        if (
            stat.S_ISREG(current.st_mode)
            and current.st_nlink == 1
            and (current.st_dev, current.st_ino) == (expected.st_dev, expected.st_ino)
        ):
            path.unlink()
    except OSError:
        pass


def _bounded_progress(status_value: object) -> None:
    if not isinstance(status_value, Mapping):
        raise _BackendContractError("backend progress invalid")
    downloaded = status_value.get("downloaded_bytes")
    if downloaded is not None and (
        type(downloaded) is not int or downloaded < 0 or downloaded > _MAX_SUBTITLE_FILE_BYTES
    ):
        raise _BackendContractError("backend subtitle too large")


def _effective_limit(request: ExecutionRequestV1, context: ExecutionContextV1) -> int:
    limit = request.arguments.get("limit")
    if type(limit) is not int:
        raise _BackendContractError("request invalid")
    return min(limit, context.limits.maximum_items)


def _request_was_narrowed(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
) -> bool:
    limit = request.arguments.get("limit")
    return type(limit) is int and limit > context.limits.maximum_items


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
    return _truncate_utf8_with_flag(value, maximum_bytes)[0]


def _truncate_utf8_with_flag(value: str, maximum_bytes: int) -> tuple[str, bool]:
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise _BackendContractError("backend text invalid") from None
    if len(encoded) <= maximum_bytes:
        return value, False
    end = maximum_bytes
    while end > 0:
        try:
            return encoded[:end].decode("utf-8", errors="strict").rstrip(" "), True
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
    pending: deque[BaseException] = deque((error,))
    seen: set[int] = set()
    result: list[BaseException] = []
    while pending and len(result) < 16:
        current = pending.popleft()
        if id(current) in seen:
            continue
        seen.add(id(current))
        result.append(current)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        exc_info = getattr(current, "exc_info", None)
        if (
            isinstance(exc_info, tuple)
            and len(exc_info) == 3
            and isinstance(exc_info[1], BaseException)
        ):
            pending.append(exc_info[1])
        if current.__context__ is not None:
            pending.append(current.__context__)
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
