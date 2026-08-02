"""Fork-owned Exa Web and Code search through fixed mcporter invocations."""

from __future__ import annotations

import hashlib
import json
import math
import os
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import IO, Final, Protocol, cast

from .contracts import (
    MAX_AUTHOR_CHARACTERS,
    MAX_PUBLISHED_CHARACTERS,
    MAX_TEXT_CHARACTERS,
    MAX_TITLE_CHARACTERS,
    MAX_URL_CHARACTERS,
    PROTOCOL_VERSION,
    ExecutionContextV1,
    ExecutionErrorCodeV1,
    ExecutionFailureV1,
    ExecutionItemV1,
    ExecutionRequestV1,
    ExecutionResultV1,
    ExecutionSuccessV1,
    McporterArtifactsV1,
    _contains_invalid_scalar,
    _valid_public_result_url,
)

_BACKEND_ID: Final = "exa-mcporter"
_MCPORTER_VERSION: Final = "0.12.3"
_WEB_BACKEND_VERSION: Final = "0.12.3+exa-web.v1"
_CODE_BACKEND_VERSION: Final = "0.12.3+exa-code.v1"
_WEB_ENDPOINT: Final = "https://mcp.exa.ai/mcp"
_CODE_ENDPOINT: Final = "https://mcp.exa.ai/mcp?tools=get_code_context_exa"
_WEB_TOOL: Final = "web_search_exa"
_CODE_TOOL: Final = "get_code_context_exa"
_STERILE_CONFIG: Final = b'{"imports":[],"mcpServers":{}}'
_NO_RESULTS: Final = "No search results found. Please try a different query."
_BLOCK_SEPARATOR: Final = "\n\n---\n\n"

_MAX_PROVIDER_RESULTS: Final = 20
_MAX_QUERY_CHARACTERS: Final = 4_096
_MAX_STDOUT_BYTES: Final = 65_536
_MAX_STDERR_BYTES: Final = 8_192
_MAX_NODE_BYTES: Final = 256 * 1_024 * 1_024
_MAX_ARTIFACT_FILE_BYTES: Final = 64 * 1_024 * 1_024
_MAX_ARTIFACT_TREE_BYTES: Final = 512 * 1_024 * 1_024
_MAX_ARTIFACT_ENTRIES: Final = 20_000
_MAX_ARTIFACT_DEPTH: Final = 64
_MAX_ARTIFACT_PATH_BYTES: Final = 4_096
_MAX_PACKAGE_JSON_BYTES: Final = 64 * 1_024
_PROCESS_TIMEOUT_SECONDS: Final = 14.5
_POLL_SECONDS: Final = 0.01
_CLEANUP_WAIT_SECONDS: Final = 1.0


class _ArtifactUnavailableError(Exception):
    pass


class _ArtifactIncompatibleError(Exception):
    pass


class _BackendContractError(Exception):
    pass


class _BackendTransientError(Exception):
    pass


class _BackendDeadlineError(Exception):
    pass


class _DuplicateJsonKeyError(Exception):
    pass


class _CheckpointRaised(BaseException):
    def __init__(self, original: BaseException) -> None:
        self.original = original


class _Hasher(Protocol):
    def update(self, data: bytes) -> None: ...


def execute_exa(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
) -> ExecutionResultV1:
    """Execute one registry-validated Exa search request."""

    if not _valid_request(request):
        return _failure(request, "backend_contract_violation")
    artifacts = _artifacts_from_context(context)
    if artifacts is None:
        return _failure(request, "backend_contract_violation")

    try:
        _checkpoint(context)
        result = _invoke_mcporter(request, context, artifacts)
        _checkpoint(context)
        return result
    except _CheckpointRaised as raised:
        raise raised.original
    except _ArtifactUnavailableError:
        return _failure(request, "backend_unavailable")
    except _ArtifactIncompatibleError:
        return _failure(request, "backend_incompatible")
    except _BackendDeadlineError:
        return _failure(request, "deadline_exceeded")
    except _BackendTransientError:
        return _failure(request, "transient")
    except _BackendContractError:
        return _failure(request, "backend_contract_violation")


def _valid_request(request: ExecutionRequestV1) -> bool:
    if (
        type(request) is not ExecutionRequestV1
        or request.protocol_version != PROTOCOL_VERSION
        or request.source != "exa"
        or request.operation not in {"search.web", "search.code"}
        or set(request.arguments) != {"query", "limit"}
    ):
        return False
    query = request.arguments["query"]
    limit = request.arguments["limit"]
    return bool(
        type(query) is str
        and query == query.strip()
        and 1 <= len(query) <= _MAX_QUERY_CHARACTERS
        and not _contains_invalid_scalar(query)
        and type(limit) is int
        and 1 <= limit <= 50
    )


def _artifacts_from_context(context: ExecutionContextV1) -> McporterArtifactsV1 | None:
    if type(context) is not ExecutionContextV1 or len(context.host_capabilities) != 2:
        return None
    artifacts = context.host_capabilities[1]
    if type(artifacts) is not McporterArtifactsV1:
        return None
    return artifacts


def _validate_artifacts(
    artifacts: McporterArtifactsV1,
    *,
    expected_config: bytes = _STERILE_CONFIG,
) -> tuple[Path, Path, Path]:
    node = _canonical_existing_path(artifacts.node_executable, kind="file")
    root = _canonical_existing_path(artifacts.mcporter_root, kind="directory")
    cli = _canonical_existing_path(artifacts.mcporter_cli, kind="file")
    config = _canonical_existing_path(artifacts.config_path, kind="file")

    _require_digest(artifacts.node_sha256)
    _require_digest(artifacts.mcporter_tree_sha256)
    _require_digest(artifacts.config_sha256)

    if _file_sha256(node, maximum_bytes=_MAX_NODE_BYTES, executable=True) != artifacts.node_sha256:
        raise _ArtifactIncompatibleError("artifact identity invalid")
    _validate_tree_root(root)
    _validate_cli_location(root, cli)
    _file_sha256(cli, maximum_bytes=_MAX_ARTIFACT_FILE_BYTES)
    if _mcporter_tree_digest(root) != artifacts.mcporter_tree_sha256:
        raise _ArtifactIncompatibleError("artifact identity invalid")
    _validate_package_identity(cli)
    config_bytes = _read_file(config, maximum_bytes=len(expected_config))
    if (
        config_bytes != expected_config
        or hashlib.sha256(config_bytes).hexdigest() != artifacts.config_sha256
    ):
        raise _ArtifactIncompatibleError("artifact identity invalid")
    return node, cli, config


def _canonical_existing_path(value: str, *, kind: str) -> Path:
    if (
        type(value) is not str
        or not value
        or _contains_invalid_scalar(value)
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise _ArtifactIncompatibleError("artifact path invalid")
    path = Path(value)
    if not path.is_absolute():
        raise _ArtifactIncompatibleError("artifact path invalid")
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        raise _ArtifactUnavailableError("artifact unavailable") from None
    except OSError:
        raise _ArtifactUnavailableError("artifact unavailable") from None
    if str(path) != str(resolved):
        raise _ArtifactIncompatibleError("artifact path invalid")
    try:
        metadata = path.lstat()
    except OSError:
        raise _ArtifactUnavailableError("artifact unavailable") from None
    if kind == "file" and not stat.S_ISREG(metadata.st_mode):
        raise _ArtifactIncompatibleError("artifact path invalid")
    if kind == "directory" and not stat.S_ISDIR(metadata.st_mode):
        raise _ArtifactIncompatibleError("artifact path invalid")
    return path


def _require_digest(value: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise _ArtifactIncompatibleError("artifact digest invalid")


def _file_sha256(path: Path, *, maximum_bytes: int, executable: bool = False) -> str:
    return hashlib.sha256(
        _read_file(path, maximum_bytes=maximum_bytes, executable=executable)
    ).hexdigest()


def _read_file(path: Path, *, maximum_bytes: int, executable: bool = False) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        _validate_file_metadata(before, maximum_bytes=maximum_bytes, executable=executable)
        body = bytearray()
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise _ArtifactIncompatibleError("artifact content invalid")
            body.extend(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise _ArtifactIncompatibleError("artifact content invalid")
        after = os.fstat(descriptor)
        if _stable_metadata(before) != _stable_metadata(after):
            raise _ArtifactIncompatibleError("artifact changed")
        return bytes(body)
    except _ArtifactIncompatibleError:
        raise
    except FileNotFoundError:
        raise _ArtifactUnavailableError("artifact unavailable") from None
    except OSError:
        raise _ArtifactUnavailableError("artifact unavailable") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_file_metadata(
    metadata: os.stat_result,
    *,
    maximum_bytes: int,
    executable: bool,
) -> None:
    mode = metadata.st_mode
    if (
        not stat.S_ISREG(mode)
        or metadata.st_nlink != 1
        or not _safe_owner(metadata)
        or bool(mode & (stat.S_IWGRP | stat.S_IWOTH))
        or not 0 < metadata.st_size <= maximum_bytes
        or (executable and not _current_process_can_execute(metadata))
    ):
        raise _ArtifactIncompatibleError("artifact metadata invalid")


def _safe_owner(metadata: os.stat_result) -> bool:
    getuid = getattr(os, "geteuid", None)
    if getuid is None:
        return True
    return metadata.st_uid in {0, cast(int, getuid())}


def _current_process_can_execute(metadata: os.stat_result) -> bool:
    mode = metadata.st_mode
    getuid = getattr(os, "geteuid", None)
    getgid = getattr(os, "getegid", None)
    getgroups = getattr(os, "getgroups", None)
    if getuid is None or getgid is None or getgroups is None:
        return bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    user = cast(int, getuid())
    if user == 0:
        return bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    if metadata.st_uid == user:
        return bool(mode & stat.S_IXUSR)
    groups = {cast(int, getgid()), *cast(list[int], getgroups())}
    if metadata.st_gid in groups:
        return bool(mode & stat.S_IXGRP)
    return bool(mode & stat.S_IXOTH)


def _stable_metadata(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _validate_tree_root(root: Path) -> None:
    try:
        metadata = root.lstat()
    except OSError:
        raise _ArtifactUnavailableError("artifact unavailable") from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or not _safe_owner(metadata)
        or bool(metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
    ):
        raise _ArtifactIncompatibleError("artifact tree invalid")


def _validate_cli_location(root: Path, cli: Path) -> None:
    package_root = cli.parent.parent
    try:
        cli.relative_to(root)
        package_root.relative_to(root)
    except ValueError:
        raise _ArtifactIncompatibleError("artifact cli invalid") from None
    if cli.name != "cli.js" or cli.parent.name != "dist":
        raise _ArtifactIncompatibleError("artifact cli invalid")


def _mcporter_tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    entries = 0
    total_bytes = 0
    try:
        walker = os.walk(root, topdown=True, followlinks=False)
        for current_value, directory_names, file_names in walker:
            current = Path(current_value)
            directory_names.sort()
            file_names.sort()
            relative_directory = current.relative_to(root)
            _tree_digest_record(digest, b"D", relative_directory, current.lstat().st_mode)
            entries += 1
            for name in directory_names:
                path = current / name
                metadata = path.lstat()
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or not _safe_owner(metadata)
                    or bool(metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
                ):
                    raise _ArtifactIncompatibleError("artifact tree invalid")
            for name in file_names:
                path = current / name
                relative = path.relative_to(root)
                metadata = path.lstat()
                _validate_file_metadata(
                    metadata,
                    maximum_bytes=_MAX_ARTIFACT_FILE_BYTES,
                    executable=False,
                )
                body = _read_file(path, maximum_bytes=_MAX_ARTIFACT_FILE_BYTES)
                total_bytes += len(body)
                entries += 1
                if total_bytes > _MAX_ARTIFACT_TREE_BYTES:
                    raise _ArtifactIncompatibleError("artifact tree invalid")
                _tree_digest_record(digest, b"F", relative, metadata.st_mode)
                digest.update(len(body).to_bytes(8, "big"))
                digest.update(hashlib.sha256(body).digest())
            if entries > _MAX_ARTIFACT_ENTRIES:
                raise _ArtifactIncompatibleError("artifact tree invalid")
    except _ArtifactIncompatibleError:
        raise
    except FileNotFoundError:
        raise _ArtifactUnavailableError("artifact unavailable") from None
    except OSError:
        raise _ArtifactUnavailableError("artifact unavailable") from None
    return digest.hexdigest()


def _tree_digest_record(
    digest: _Hasher,
    kind: bytes,
    relative: Path,
    mode: int,
) -> None:
    value = "." if relative == Path(".") else relative.as_posix()
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise _ArtifactIncompatibleError("artifact tree invalid") from None
    if (
        not encoded
        or len(encoded) > _MAX_ARTIFACT_PATH_BYTES
        or len(relative.parts) > _MAX_ARTIFACT_DEPTH
        or _contains_invalid_scalar(value)
    ):
        raise _ArtifactIncompatibleError("artifact tree invalid")
    digest.update(kind)
    digest.update(len(encoded).to_bytes(4, "big"))
    digest.update(encoded)
    digest.update((mode & 0o7777).to_bytes(2, "big"))


def _validate_package_identity(cli: Path) -> None:
    package_json = cli.parent.parent / "package.json"
    raw = _read_file(package_json, maximum_bytes=_MAX_PACKAGE_JSON_BYTES)
    try:
        value = _load_json(raw)
    except _BackendContractError:
        raise _ArtifactIncompatibleError("artifact package invalid") from None
    if not isinstance(value, Mapping):
        raise _ArtifactIncompatibleError("artifact package invalid")
    package = cast(Mapping[str, object], value)
    if package.get("name") != "mcporter" or package.get("version") != _MCPORTER_VERSION:
        raise _ArtifactIncompatibleError("artifact package invalid")


def _provider_limit(request: ExecutionRequestV1, context: ExecutionContextV1) -> int:
    requested = request.arguments["limit"]
    if type(requested) is not int:
        raise _BackendContractError("request invalid")
    return min(requested, _MAX_PROVIDER_RESULTS, context.limits.maximum_items)


def _request_was_narrowed(request: ExecutionRequestV1, provider_limit: int) -> bool:
    requested = request.arguments["limit"]
    return type(requested) is int and requested > provider_limit


def _invoke_mcporter(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
    artifacts: McporterArtifactsV1,
) -> ExecutionSuccessV1:
    query = cast(str, request.arguments["query"])
    limit = _provider_limit(request, context)
    payload = bytearray(
        json.dumps(
            {"query": query, "numResults": limit},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    )
    try:
        try:
            temporary = tempfile.TemporaryDirectory(
                prefix=".agent-reach-exa-",
                dir=str(Path.cwd()),
            )
            try:
                private_root = Path(temporary.name).resolve(strict=True)
                environment, home = _private_process_environment(private_root)

                # Revalidate the complete operator-attested closure immediately
                # before composing the sole fixed provider process.
                node, cli, config = _validate_artifacts(artifacts)
                argv = _mcporter_argv(node, cli, config, operation=request.operation)
                _checkpoint(context)
                try:
                    process = subprocess.Popen(
                        argv,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=home,
                        env=environment,
                        shell=False,
                        close_fds=True,
                        start_new_session=True,
                    )
                except OSError:
                    raise _ArtifactUnavailableError("backend unavailable") from None
                stdout = _exchange_process(
                    process,
                    payload,
                    lambda: _checkpoint(context),
                )
                try:
                    text = _mcp_text(stdout)
                    maximum_text = min(
                        context.limits.maximum_text_characters,
                        MAX_TEXT_CHARACTERS,
                    )
                    if request.operation == "search.web":
                        items, text_truncated = _project_web_results(
                            text,
                            maximum_items=limit,
                            maximum_text=maximum_text,
                        )
                    else:
                        items, text_truncated = _project_code_results(
                            text,
                            maximum_items=limit,
                            maximum_text=maximum_text,
                        )
                    return ExecutionSuccessV1(
                        PROTOCOL_VERSION,
                        "exa",
                        request.operation,
                        _BACKEND_ID,
                        _backend_version(request.operation),
                        items,
                        truncated=_request_was_narrowed(request, limit) or text_truncated,
                    )
                except (TypeError, ValueError, _BackendContractError):
                    _kill_and_reap(process)
                    raise _BackendContractError("backend output invalid") from None
            finally:
                active_error = sys.exc_info()[1]
                try:
                    temporary.cleanup()
                except OSError:
                    if active_error is None:
                        raise
        except OSError:
            raise _ArtifactUnavailableError("backend unavailable") from None
    finally:
        payload[:] = b"\x00" * len(payload)


def _mcporter_argv(
    node: Path,
    cli: Path,
    config: Path,
    *,
    operation: str,
) -> tuple[str, ...]:
    if operation == "search.web":
        endpoint = _WEB_ENDPOINT
        tool = _WEB_TOOL
    elif operation == "search.code":
        endpoint = _CODE_ENDPOINT
        tool = _CODE_TOOL
    else:
        raise _BackendContractError("request invalid")
    return (
        str(node),
        str(cli),
        "--config",
        str(config),
        "--log-level",
        "error",
        "call",
        "--http-url",
        endpoint,
        "--name",
        "exa",
        "--tool",
        tool,
        "--args",
        "-",
        "--output",
        "json",
        "--timeout",
        "14000",
        "--no-oauth",
    )


def _private_process_environment(root: Path) -> tuple[dict[str, str], Path]:
    home = root / "home"
    config = root / "xdg"
    temporary = root / "tmp"
    for directory in (home, config, temporary):
        directory.mkdir(mode=0o700)
    return (
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(config),
            "TMPDIR": str(temporary),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "NO_COLOR": "1",
            "MCPORTER_LOG_LEVEL": "error",
        },
        home,
    )


def _exchange_process(
    process: subprocess.Popen[bytes],
    payload: bytearray,
    checkpoint: Callable[[], None],
) -> bytes:
    threads: tuple[threading.Thread, ...] = ()
    stdout_buffer: bytearray | None = None
    stderr_buffer: bytearray | None = None
    try:
        stdin = process.stdin
        stdout = process.stdout
        stderr = process.stderr
        if stdin is None or stdout is None or stderr is None:
            raise _BackendTransientError("backend pipes unavailable")

        stdout_buffer = bytearray()
        stderr_buffer = bytearray()
        input_failed = threading.Event()
        output_overflow = threading.Event()

        # Popen may block long enough for host cancellation to become active.
        # Recheck before any thread can submit the provider-visible query.
        checkpoint()

        def write_input() -> None:
            try:
                stdin.write(payload)
                stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                input_failed.set()
            finally:
                try:
                    stdin.close()
                except OSError:
                    pass

        def read_bounded(stream: IO[bytes], target: bytearray, maximum: int) -> None:
            try:
                while True:
                    read1 = getattr(stream, "read1", None)
                    chunk = read1(8_192) if callable(read1) else stream.read(8_192)
                    if not chunk:
                        return
                    if type(chunk) is not bytes or len(target) + len(chunk) > maximum:
                        target[:] = b"\x00" * len(target)
                        output_overflow.set()
                        return
                    target.extend(chunk)
            except (OSError, ValueError):
                input_failed.set()

        threads = (
            threading.Thread(target=write_input, name="exa-mcporter-stdin", daemon=True),
            threading.Thread(
                target=read_bounded,
                args=(stdout, stdout_buffer, _MAX_STDOUT_BYTES),
                name="exa-mcporter-stdout",
                daemon=True,
            ),
            threading.Thread(
                target=read_bounded,
                args=(stderr, stderr_buffer, _MAX_STDERR_BYTES),
                name="exa-mcporter-stderr",
                daemon=True,
            ),
        )
        deadline = time.monotonic() + _PROCESS_TIMEOUT_SECONDS
        for thread in threads:
            thread.start()
        while True:
            checkpoint()
            if output_overflow.is_set():
                raise _BackendContractError("backend output invalid")
            if input_failed.is_set():
                raise _BackendTransientError("backend exchange failed")
            return_code = process.poll()
            if return_code is not None and all(not thread.is_alive() for thread in threads):
                break
            if time.monotonic() >= deadline:
                raise _BackendDeadlineError("backend deadline exceeded")
            time.sleep(_POLL_SECONDS)
        if return_code != 0:
            raise _BackendTransientError("backend process failed")
        if stderr_buffer:
            raise _BackendContractError("backend stderr invalid")
        return bytes(stdout_buffer)
    except (_BackendContractError, _BackendDeadlineError, _BackendTransientError):
        _kill_and_reap(process)
        raise
    except BaseException as error:
        _kill_and_reap(process)
        if isinstance(error, Exception):
            raise _BackendTransientError("backend exchange failed") from None
        raise
    finally:
        _close_process_streams(process)
        for thread in threads:
            try:
                if thread.ident is not None:
                    thread.join(timeout=_CLEANUP_WAIT_SECONDS)
            except RuntimeError:
                pass
        payload[:] = b"\x00" * len(payload)
        if process.poll() is None:
            _kill_and_reap(process)
        if stdout_buffer is not None:
            stdout_buffer[:] = b"\x00" * len(stdout_buffer)
        if stderr_buffer is not None:
            stderr_buffer[:] = b"\x00" * len(stderr_buffer)


def _kill_and_reap(process: subprocess.Popen[bytes]) -> None:
    group_signalled = False
    killpg = getattr(os, "killpg", None)
    sigkill = getattr(signal, "SIGKILL", None)
    if callable(killpg) and sigkill is not None:
        try:
            killpg(process.pid, sigkill)
            group_signalled = True
        except (ProcessLookupError, PermissionError):
            pass
        except OSError:
            pass
    if not group_signalled and process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=_CLEANUP_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=_CLEANUP_WAIT_SECONDS)
        except (ChildProcessError, OSError, subprocess.TimeoutExpired):
            pass
    except (ChildProcessError, OSError):
        pass


def _close_process_streams(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:
            pass


def _checkpoint(context: ExecutionContextV1) -> None:
    try:
        context.checkpoint()
    except BaseException as error:
        raise _CheckpointRaised(error) from None


def _mcp_text(raw: bytes) -> str:
    value = _load_json(raw)
    if not isinstance(value, Mapping):
        raise _BackendContractError("backend envelope invalid")
    envelope = cast(Mapping[str, object], value)
    if set(envelope) not in ({"content"}, {"content", "isError"}):
        raise _BackendContractError("backend envelope invalid")
    if "isError" in envelope and envelope["isError"] is not False:
        raise _BackendContractError("backend envelope invalid")
    content = envelope.get("content")
    if not isinstance(content, list) or len(content) != 1:
        raise _BackendContractError("backend envelope invalid")
    block_value = content[0]
    if not isinstance(block_value, Mapping):
        raise _BackendContractError("backend envelope invalid")
    block = cast(Mapping[str, object], block_value)
    if set(block) not in ({"type", "text"}, {"type", "text", "_meta"}):
        raise _BackendContractError("backend envelope invalid")
    if block.get("type") != "text":
        raise _BackendContractError("backend envelope invalid")
    text = block.get("text")
    if (
        type(text) is not str
        or not text
        or len(text.encode("utf-8", errors="strict")) > _MAX_STDOUT_BYTES
        or _contains_invalid_scalar(text)
    ):
        raise _BackendContractError("backend envelope invalid")
    if "_meta" in block:
        _validate_block_metadata(block["_meta"])
    return text


def _validate_block_metadata(value: object) -> None:
    if not isinstance(value, Mapping) or set(value) not in (set(), {"searchTime"}):
        raise _BackendContractError("backend envelope invalid")
    if "searchTime" not in value:
        return
    search_time = value["searchTime"]
    if (
        type(search_time) not in {int, float}
        or not math.isfinite(search_time)
        or search_time < 0
        or search_time > 3_600
    ):
        raise _BackendContractError("backend envelope invalid")


def _load_json(raw: bytes) -> object:
    if not raw or len(raw) > _MAX_STDOUT_BYTES:
        raise _BackendContractError("backend json invalid")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        raise _BackendContractError("backend json invalid") from None
    if text.startswith("\ufeff"):
        raise _BackendContractError("backend json invalid")
    leading = 0
    while leading < len(text) and text[leading] in " \t\r\n":
        leading += 1
    try:
        decoder = json.JSONDecoder(
            object_pairs_hook=_closed_json_object,
            parse_constant=_reject_json_constant,
        )
        value, end = decoder.raw_decode(text, leading)
    except (json.JSONDecodeError, ValueError, _DuplicateJsonKeyError):
        raise _BackendContractError("backend json invalid") from None
    if any(character not in " \t\r\n" for character in text[end:]):
        raise _BackendContractError("backend json invalid")
    _validate_json_shape(value)
    return value


def _closed_json_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError("duplicate json key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    raise ValueError("invalid json constant")


def _validate_json_shape(value: object) -> None:
    pending: list[tuple[object, int]] = [(value, 1)]
    nodes = 0
    while pending:
        current, depth = pending.pop()
        nodes += 1
        if nodes > 512 or depth > 12:
            raise _BackendContractError("backend json invalid")
        if current is None or type(current) in {bool, int}:
            continue
        if type(current) is float:
            if not math.isfinite(current):
                raise _BackendContractError("backend json invalid")
            continue
        if type(current) is str:
            if _contains_invalid_scalar(current):
                raise _BackendContractError("backend json invalid")
            continue
        if isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
            continue
        if isinstance(current, Mapping):
            pending.extend((item, depth + 1) for item in current.values())
            continue
        raise _BackendContractError("backend json invalid")


def _project_web_results(
    text: str,
    *,
    maximum_items: int,
    maximum_text: int,
) -> tuple[tuple[ExecutionItemV1, ...], bool]:
    if text == _NO_RESULTS:
        return (), False
    if "\r" in text or text != text.strip():
        raise _BackendContractError("backend text invalid")
    blocks = text.split(_BLOCK_SEPARATOR)
    if not blocks or len(blocks) > maximum_items:
        raise _BackendContractError("backend text invalid")
    projected = tuple(_project_web_block(block, maximum_text) for block in blocks)
    return tuple(item for item, _ in projected), any(truncated for _, truncated in projected)


def _project_web_block(
    block: str,
    maximum_text: int,
) -> tuple[ExecutionItemV1, bool]:
    lines = block.split("\n")
    if len(lines) < 5:
        raise _BackendContractError("backend text invalid")
    title = _label_value(lines[0], "Title: ", MAX_TITLE_CHARACTERS)
    url = _label_value(lines[1], "URL: ", MAX_URL_CHARACTERS)
    published = _label_value(lines[2], "Published: ", MAX_PUBLISHED_CHARACTERS)
    author = _label_value(lines[3], "Author: ", MAX_AUTHOR_CHARACTERS)
    if not _valid_public_result_url(url):
        raise _BackendContractError("backend url invalid")

    if lines[4] == "Highlights:":
        if len(lines) < 6:
            raise _BackendContractError("backend text invalid")
        body = "\n".join(lines[5:])
    elif lines[4].startswith("Text: "):
        body = "\n".join((lines[4][len("Text: ") :], *lines[5:]))
    else:
        raise _BackendContractError("backend text invalid")
    normalized = " ".join(body.split())
    if not normalized or _contains_invalid_scalar(normalized):
        raise _BackendContractError("backend text invalid")
    truncated = len(normalized) > maximum_text
    item = ExecutionItemV1(
        "exa.search.result.v1",
        {
            "text": normalized[:maximum_text],
            "title": title,
            "url": url,
            "author": None if author == "N/A" else author,
            "published_at": None if published == "N/A" else published,
        },
    )
    return item, truncated


def _project_code_results(
    text: str,
    *,
    maximum_items: int,
    maximum_text: int,
) -> tuple[tuple[ExecutionItemV1, ...], bool]:
    if text == _NO_RESULTS:
        return (), False
    if "\r" in text or text != text.strip():
        raise _BackendContractError("backend text invalid")
    blocks = text.split(_BLOCK_SEPARATOR)
    if not blocks or len(blocks) > maximum_items:
        raise _BackendContractError("backend text invalid")
    projected = tuple(_project_code_block(block, maximum_text) for block in blocks)
    return tuple(item for item, _ in projected), any(truncated for _, truncated in projected)


def _project_code_block(
    block: str,
    maximum_text: int,
) -> tuple[ExecutionItemV1, bool]:
    lines = block.split("\n")
    if len(lines) < 3:
        raise _BackendContractError("backend text invalid")
    title = _label_value(lines[0], "Title: ", MAX_TITLE_CHARACTERS)
    url = _label_value(lines[1], "URL: ", MAX_URL_CHARACTERS)
    if not _valid_public_result_url(url):
        raise _BackendContractError("backend url invalid")

    if lines[2] == "Code/Highlights:":
        if len(lines) < 4:
            raise _BackendContractError("backend text invalid")
        body = "\n".join(lines[3:])
    elif lines[2].startswith("Text: "):
        body = "\n".join((lines[2][len("Text: ") :], *lines[3:]))
    else:
        raise _BackendContractError("backend text invalid")
    normalized = " ".join(body.split())
    if not normalized or _contains_invalid_scalar(normalized):
        raise _BackendContractError("backend text invalid")
    truncated = len(normalized) > maximum_text
    return (
        ExecutionItemV1(
            "exa.code.result.v1",
            {
                "text": normalized[:maximum_text],
                "title": title,
                "url": url,
            },
        ),
        truncated,
    )


def _label_value(line: str, prefix: str, maximum: int) -> str:
    if not line.startswith(prefix):
        raise _BackendContractError("backend text invalid")
    value = line[len(prefix) :]
    if (
        not value
        or value != value.strip()
        or len(value) > maximum
        or _contains_invalid_scalar(value)
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise _BackendContractError("backend text invalid")
    return value


def _failure(
    request: ExecutionRequestV1,
    error_code: ExecutionErrorCodeV1,
) -> ExecutionFailureV1:
    return ExecutionFailureV1(
        protocol_version=PROTOCOL_VERSION,
        source="exa",
        operation=(
            request.operation
            if request.operation in {"search.web", "search.code"}
            else "search.web"
        ),
        backend_id=_BACKEND_ID,
        backend_version=_backend_version(request.operation),
        error_code=error_code,
    )


def _backend_version(operation: str) -> str:
    return _CODE_BACKEND_VERSION if operation == "search.code" else _WEB_BACKEND_VERSION


__all__ = ["execute_exa"]
