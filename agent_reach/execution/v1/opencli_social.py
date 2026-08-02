"""Fork-owned social-platform execution through OpenCLI."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import re
import signal
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import IO, Final, cast
from urllib.parse import urlsplit

import yaml
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, Node

from .contracts import (
    MAX_AUTHOR_CHARACTERS,
    MAX_NATIVE_ID_CHARACTERS,
    MAX_PUBLISHED_CHARACTERS,
    MAX_TEXT_CHARACTERS,
    MAX_TITLE_CHARACTERS,
    PROTOCOL_VERSION,
    ExecutionContextV1,
    ExecutionErrorCodeV1,
    ExecutionFailureV1,
    ExecutionItemV1,
    ExecutionRequestV1,
    ExecutionResultV1,
    ExecutionSuccessV1,
    OpenCliSessionV1,
    _contains_invalid_scalar,
    _valid_public_result_url,
)
from .contracts import (
    _reddit_post_id_from_url as _contract_reddit_post_id_from_url,
)
from .contracts import (
    _reddit_post_identity_from_url as _contract_reddit_post_identity_from_url,
)

_BACKEND_ID: Final = "opencli"
_BACKEND_VERSION: Final = "1.8.6-hermes.1"
_PACKAGE_NAME: Final = "@jackwener/opencli"
_PACKAGE_ROOT: Final = Path("node_modules/@jackwener/opencli")
_PACKAGE_CLI: Final = _PACKAGE_ROOT / "dist/src/main.js"
_OPENCLI_OFFICIAL_BASE_COMMIT: Final = "399c0de2a76eb979aee3a3836cf2d24fd247780f"
_OPENCLI_SOURCE_COMMIT: Final = "594b21498680f6372279f178aa9b3aaed2c71e35"
_OPENCLI_SOURCE_TREE: Final = "be64296855727d00478cc857f2b26eb7d3790057"
_OPENCLI_TARBALL_SHA256: Final = "dac98c69802621d55d8e3a5ae7032f47ab22b3785331a69499a907456f9dfb73"
_OPENCLI_TARBALL_SHA512: Final = (
    "kiYpXZ4jrwr6q6yVHCclL0wv3alO0JN1+GOjeY9S9q+73EKkZ3ZQE0nIiE7WoK5MC1ttK918PkOhYO3uKQPgyQ=="
)
_SOCIAL_SOURCES: Final = frozenset({"reddit", "facebook", "instagram", "twitter", "xiaohongshu"})
_REDDIT_POST_ID: Final = re.compile(r"[a-z0-9]{1,32}")
_SUBREDDIT: Final = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,20}")
_USERNAME: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_COMMENT_LEVEL: Final = re.compile(r"L[0-9]+")
_TWITTER_POST_ID: Final = re.compile(r"[1-9][0-9]{0,31}")
_XIAOHONGSHU_NOTE_ID: Final = re.compile(r"[0-9a-f]{24}")
_INTEGER_TEXT: Final = re.compile(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)")
_HUMAN_COUNT_TEXT: Final = re.compile(
    r"(?P<number>(?:[0-9]+(?:\.[0-9]+)?|[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?))"
    r"\s*(?P<suffix>[KkMmBb万亿]?)"
    r"(?:\s+(?:friends?|followers?|likes?|reactions?|comments?|shares?|members?|posts?))?",
    re.IGNORECASE,
)

_MAX_STDOUT_BYTES: Final = 512 * 1_024
_MAX_STDERR_BYTES: Final = 64 * 1_024
_MAX_NODE_BYTES: Final = 256 * 1_024 * 1_024
_MAX_TREE_BYTES: Final = 768 * 1_024 * 1_024
_MAX_TREE_ENTRIES: Final = 30_000
_MAX_TREE_DEPTH: Final = 96
_MAX_PATH_BYTES: Final = 4_096
_TREE_SORT_CHUNK_ENTRIES: Final = 256
_MAX_PACKAGE_JSON_BYTES: Final = 64 * 1_024
_MAX_LIFECYCLE_GUARD_BYTES: Final = 16 * 1_024
_MAX_YAML_NODES: Final = 8_192
_MAX_YAML_DEPTH: Final = 32
_MAX_ERROR_NODES: Final = 128
_FORBIDDEN_TREE_MODE_BITS: Final = 0o7022
_PROCESS_TIMEOUT_SECONDS: Final = 20.0
_POLL_SECONDS: Final = 0.01
_CLEANUP_WAIT_SECONDS: Final = 1.0
_LIFECYCLE_GUARD_SHA256: Final = "9c9cd9bf8163fb3fba863f94a71e9ea09ea3323b84f89a06b55e3d1f19515213"
_SINGLE_ROW_OPERATIONS: Final = frozenset(
    {
        ("facebook", "read.profile"),
        ("instagram", "read.profile"),
    }
)

_ROW_FIELDS: Final[Mapping[tuple[str, str], frozenset[str]]] = {
    ("reddit", "search.posts"): frozenset(
        {
            "id",
            "title",
            "subreddit",
            "author",
            "score",
            "comments",
            "url",
            "created_utc",
            "selftext",
            "post_hint",
            "url_overridden_by_dest",
            "preview_image_url",
            "gallery_urls",
        }
    ),
    ("reddit", "read.post"): frozenset(
        {
            "type",
            "author",
            "score",
            "text",
            "post_hint",
            "url_overridden_by_dest",
            "preview_image_url",
            "gallery_urls",
        }
    ),
    ("reddit", "browse.subreddit"): frozenset(
        {
            "id",
            "title",
            "subreddit",
            "author",
            "upvotes",
            "comments",
            "url",
            "created_utc",
            "selftext",
            "post_hint",
            "url_overridden_by_dest",
            "preview_image_url",
            "gallery_urls",
        }
    ),
    ("reddit", "browse.hot"): frozenset(
        {
            "rank",
            "title",
            "subreddit",
            "score",
            "comments",
            "postId",
            "author",
            "url",
            "post_hint",
            "url_overridden_by_dest",
            "preview_image_url",
            "gallery_urls",
        }
    ),
    ("reddit", "browse.popular"): frozenset(
        {
            "rank",
            "id",
            "title",
            "subreddit",
            "score",
            "comments",
            "author",
            "url",
            "created_utc",
            "selftext",
            "post_hint",
            "url_overridden_by_dest",
            "preview_image_url",
            "gallery_urls",
        }
    ),
    ("reddit", "browse.all"): frozenset(
        {
            "title",
            "subreddit",
            "author",
            "upvotes",
            "comments",
            "url",
            "post_hint",
            "url_overridden_by_dest",
            "preview_image_url",
            "gallery_urls",
        }
    ),
    ("reddit", "read.subreddit"): frozenset({"field", "value"}),
    ("facebook", "search"): frozenset({"index", "title", "text", "url"}),
    ("facebook", "read.profile"): frozenset({"name", "username", "friends", "followers", "url"}),
    ("facebook", "browse.feed"): frozenset(
        {"index", "author", "content", "likes", "comments", "shares"}
    ),
    ("facebook", "browse.groups"): frozenset({"index", "name", "last_post", "url"}),
    ("instagram", "search.users"): frozenset(
        {"rank", "username", "name", "verified", "private", "url"}
    ),
    ("instagram", "read.profile"): frozenset(
        {"username", "name", "followers", "following", "posts", "verified", "bio"}
    ),
    ("instagram", "browse.user_posts"): frozenset(
        {"index", "caption", "likes", "comments", "type", "date"}
    ),
    ("instagram", "browse.explore"): frozenset(
        {"rank", "user", "caption", "likes", "comments", "type"}
    ),
    ("twitter", "search.posts"): frozenset(
        {
            "id",
            "author",
            "bio",
            "text",
            "created_at",
            "likes",
            "views",
            "url",
            "has_media",
            "media_urls",
            "media_posters",
            "card",
            "quoted_tweet",
        }
    ),
    ("xiaohongshu", "search.notes"): frozenset(
        {"rank", "title", "author", "likes", "published_at", "url", "author_url"}
    ),
}


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


class _CheckpointRaised(BaseException):
    def __init__(self, original: BaseException) -> None:
        self.original = original


class _DuplicateYamlKeyError(yaml.YAMLError):
    pass


class _ClosedYamlLoader(yaml.SafeLoader):
    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._closed_nodes = 0
        self._closed_depth = 0

    def compose_node(self, parent: Node | None, index: int) -> Node | None:
        check_event = cast(Callable[[type[AliasEvent]], bool], self.check_event)
        if check_event(AliasEvent):
            raise yaml.YAMLError("yaml aliases are not allowed")
        self._closed_nodes += 1
        self._closed_depth += 1
        if self._closed_nodes > _MAX_YAML_NODES or self._closed_depth > _MAX_YAML_DEPTH:
            raise yaml.YAMLError("yaml bounds exceeded")
        try:
            return super().compose_node(parent, index)
        finally:
            self._closed_depth -= 1

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[object, object]:
        if not isinstance(node, MappingNode):
            raise yaml.YAMLError("yaml mapping invalid")
        mapping: dict[object, object] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError:
                raise yaml.YAMLError("yaml mapping key invalid") from None
            if duplicate:
                raise _DuplicateYamlKeyError("duplicate yaml key")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


@dataclass(slots=True)
class _ProjectionState:
    truncated: bool = False


def execute_opencli_social(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
) -> ExecutionResultV1:
    """Execute one registry-validated social operation."""

    if not _valid_request(request) or type(context) is not ExecutionContextV1:
        return _failure(request, "backend_contract_violation")
    session = _session_from_context(context)
    if session is None:
        return _failure(request, "backend_contract_violation")
    deadline = time.monotonic() + _PROCESS_TIMEOUT_SECONDS
    try:
        _execution_checkpoint(context, deadline)
        result = _invoke_opencli(request, context, session, deadline)
        _execution_checkpoint(context, deadline)
        return result
    except _CheckpointRaised as raised:
        raise raised.original from None
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


def _failure(request: ExecutionRequestV1, code: ExecutionErrorCodeV1) -> ExecutionFailureV1:
    return ExecutionFailureV1(
        PROTOCOL_VERSION,
        request.source,
        request.operation,
        _BACKEND_ID,
        _BACKEND_VERSION,
        code,
    )


def _valid_request(request: ExecutionRequestV1) -> bool:
    return bool(
        type(request) is ExecutionRequestV1
        and request.protocol_version == PROTOCOL_VERSION
        and request.source in _SOCIAL_SOURCES
        and (request.source, request.operation) in _ROW_FIELDS
    )


def _session_from_context(context: ExecutionContextV1) -> OpenCliSessionV1 | None:
    if type(context) is not ExecutionContextV1 or len(context.host_capabilities) != 1:
        return None
    session = context.host_capabilities[0]
    return session if type(session) is OpenCliSessionV1 else None


def _validate_session(
    session: OpenCliSessionV1,
    context: ExecutionContextV1,
    deadline: float,
    snapshot_root: Path,
) -> tuple[Path, Path, Path, Path]:
    def attestation_checkpoint() -> None:
        _execution_checkpoint(context, deadline)

    attestation_checkpoint()
    node = _canonical_path(session.node_executable, kind="file")
    attestation_checkpoint()
    root = _canonical_path(session.opencli_root, kind="directory")
    attestation_checkpoint()
    cli = _canonical_path(session.opencli_cli, kind="file")
    attestation_checkpoint()
    home = _canonical_path(session.session_home, kind="directory")
    lifecycle_guard = _lifecycle_guard_path(checkpoint=attestation_checkpoint)
    if cli != root / _PACKAGE_CLI:
        raise _ArtifactIncompatibleError("opencli entrypoint invalid")
    snapshot_node = snapshot_root / "node"
    snapshot_opencli_root = snapshot_root / "opencli"
    snapshot_guard = snapshot_root / "opencli-no-lifecycle.mjs"
    node_digest, _ = _copy_regular_file(
        node,
        snapshot_node,
        maximum_bytes=_MAX_NODE_BYTES,
        executable=True,
        destination_mode=0o500,
        checkpoint=attestation_checkpoint,
    )
    if node_digest != session.node_sha256:
        raise _ArtifactIncompatibleError("node identity invalid")
    _copy_tree_snapshot(
        root,
        snapshot_opencli_root,
        expected_sha256=session.opencli_tree_sha256,
        checkpoint=attestation_checkpoint,
    )
    guard_digest, _ = _copy_regular_file(
        lifecycle_guard,
        snapshot_guard,
        maximum_bytes=_MAX_LIFECYCLE_GUARD_BYTES,
        executable=False,
        destination_mode=0o400,
        allow_multiple_links=True,
        checkpoint=attestation_checkpoint,
    )
    if guard_digest != _LIFECYCLE_GUARD_SHA256:
        raise _ArtifactIncompatibleError("lifecycle guard invalid")
    snapshot_cli = snapshot_opencli_root / _PACKAGE_CLI
    _validate_package_identity(
        snapshot_opencli_root,
        snapshot_cli,
        checkpoint=attestation_checkpoint,
    )
    attestation_checkpoint()
    return snapshot_node, snapshot_cli, home, snapshot_guard


def _lifecycle_guard_path(*, checkpoint: Callable[[], None] | None = None) -> Path:
    try:
        path = Path(__file__).with_name("_opencli_no_lifecycle.mjs").resolve(strict=True)
        metadata = path.lstat()
    except (FileNotFoundError, OSError):
        raise _ArtifactUnavailableError("lifecycle guard unavailable") from None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o022:
        raise _ArtifactIncompatibleError("lifecycle guard invalid")
    if (
        _file_sha256(
            path,
            maximum_bytes=_MAX_LIFECYCLE_GUARD_BYTES,
            allow_multiple_links=True,
            checkpoint=checkpoint,
        )
        != _LIFECYCLE_GUARD_SHA256
    ):
        raise _ArtifactIncompatibleError("lifecycle guard invalid")
    return path


def _canonical_path(value: str, *, kind: str) -> Path:
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
        metadata = path.lstat()
    except FileNotFoundError:
        raise _ArtifactUnavailableError("artifact unavailable") from None
    except OSError:
        raise _ArtifactUnavailableError("artifact unavailable") from None
    if resolved != path or metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
        raise _ArtifactIncompatibleError("artifact path invalid")
    if kind == "file" and not stat.S_ISREG(metadata.st_mode):
        raise _ArtifactIncompatibleError("artifact path invalid")
    if kind == "directory" and not stat.S_ISDIR(metadata.st_mode):
        raise _ArtifactIncompatibleError("artifact path invalid")
    return path


def _file_sha256(
    path: Path,
    *,
    maximum_bytes: int,
    executable: bool = False,
    allow_multiple_links: bool = False,
    checkpoint: Callable[[], None] | None = None,
) -> str:
    try:
        if checkpoint is not None:
            checkpoint()
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or (not allow_multiple_links and metadata.st_nlink != 1)
            or metadata.st_size <= 0
            or metadata.st_size > maximum_bytes
            or metadata.st_mode & 0o022
            or (executable and not metadata.st_mode & stat.S_IXUSR)
        ):
            raise _ArtifactIncompatibleError("artifact file invalid")
        digest = hashlib.sha256()
        total = 0
        with path.open("rb") as stream:
            observed = os.fstat(stream.fileno())
            if (observed.st_dev, observed.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise _ArtifactIncompatibleError("artifact file invalid")
            while True:
                if checkpoint is not None:
                    checkpoint()
                chunk = stream.read(64 * 1_024)
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum_bytes:
                    raise _ArtifactIncompatibleError("artifact file invalid")
                digest.update(chunk)
        if total != metadata.st_size:
            raise _ArtifactIncompatibleError("artifact file invalid")
        return digest.hexdigest()
    except _ArtifactIncompatibleError:
        raise
    except FileNotFoundError:
        raise _ArtifactUnavailableError("artifact unavailable") from None
    except OSError:
        raise _ArtifactUnavailableError("artifact unavailable") from None


def _copy_regular_file(
    source: Path,
    destination: Path,
    *,
    maximum_bytes: int,
    executable: bool,
    destination_mode: int,
    allow_empty: bool = False,
    allow_multiple_links: bool = False,
    checkpoint: Callable[[], None] | None = None,
) -> tuple[str, int]:
    try:
        if checkpoint is not None:
            checkpoint()
        metadata = source.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or (not allow_multiple_links and metadata.st_nlink != 1)
            or (not allow_empty and metadata.st_size <= 0)
            or metadata.st_size > maximum_bytes
            or metadata.st_mode & 0o022
            or (executable and not metadata.st_mode & stat.S_IXUSR)
        ):
            raise _ArtifactIncompatibleError("artifact file invalid")
        digest = hashlib.sha256()
        with source.open("rb") as input_stream, destination.open("xb") as output_stream:
            observed = os.fstat(input_stream.fileno())
            if (
                (observed.st_dev, observed.st_ino) != (metadata.st_dev, metadata.st_ino)
                or observed.st_mode != metadata.st_mode
                or observed.st_uid != metadata.st_uid
                or observed.st_nlink != metadata.st_nlink
                or observed.st_size != metadata.st_size
            ):
                raise _ArtifactIncompatibleError("artifact file invalid")
            remaining = metadata.st_size
            while remaining:
                if checkpoint is not None:
                    checkpoint()
                chunk = input_stream.read(min(64 * 1_024, remaining))
                if not chunk:
                    raise _ArtifactIncompatibleError("artifact file invalid")
                output_stream.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
            if input_stream.read(1):
                raise _ArtifactIncompatibleError("artifact file invalid")
            output_stream.flush()
            os.fchmod(output_stream.fileno(), destination_mode)
            final = os.fstat(input_stream.fileno())
            if (
                final.st_dev,
                final.st_ino,
                final.st_mode,
                final.st_uid,
                final.st_nlink,
                final.st_size,
                final.st_mtime_ns,
                final.st_ctime_ns,
            ) != (
                observed.st_dev,
                observed.st_ino,
                observed.st_mode,
                observed.st_uid,
                observed.st_nlink,
                observed.st_size,
                observed.st_mtime_ns,
                observed.st_ctime_ns,
            ):
                raise _ArtifactIncompatibleError("artifact file invalid")
        return digest.hexdigest(), metadata.st_size
    except _ArtifactIncompatibleError:
        raise
    except FileExistsError:
        raise _ArtifactIncompatibleError("artifact snapshot invalid") from None
    except FileNotFoundError:
        raise _ArtifactUnavailableError("artifact unavailable") from None
    except OSError:
        raise _ArtifactUnavailableError("artifact unavailable") from None


def _copy_tree_snapshot(
    source_root: Path,
    destination_root: Path,
    *,
    expected_sha256: str,
    checkpoint: Callable[[], None] | None = None,
) -> None:
    directory_modes: list[tuple[Path, int]] = []
    total_bytes = 0
    try:
        destination_root.mkdir(mode=0o700)
        for name, source in _sorted_tree_entries(source_root, checkpoint=checkpoint):
            if checkpoint is not None:
                checkpoint()
            metadata = source.lstat()
            if metadata.st_uid != os.getuid() or metadata.st_mode & _FORBIDDEN_TREE_MODE_BITS:
                raise _ArtifactIncompatibleError("artifact tree invalid")
            destination = destination_root if name == "." else destination_root / name
            if stat.S_ISDIR(metadata.st_mode):
                if name != ".":
                    destination.mkdir(mode=0o700)
                directory_modes.append((destination, metadata.st_mode & 0o777))
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise _ArtifactIncompatibleError("artifact tree invalid")
            remaining_budget = _MAX_TREE_BYTES - total_bytes
            _, copied = _copy_regular_file(
                source,
                destination,
                maximum_bytes=remaining_budget,
                executable=False,
                destination_mode=metadata.st_mode & 0o777,
                allow_empty=True,
                checkpoint=checkpoint,
            )
            total_bytes += copied
        for directory, mode in reversed(directory_modes):
            if checkpoint is not None:
                checkpoint()
            directory.chmod(mode)
    except _ArtifactIncompatibleError:
        raise
    except FileExistsError:
        raise _ArtifactIncompatibleError("artifact snapshot invalid") from None
    except FileNotFoundError:
        raise _ArtifactUnavailableError("artifact unavailable") from None
    except OSError:
        raise _ArtifactUnavailableError("artifact unavailable") from None
    if _tree_sha256(destination_root, checkpoint=checkpoint) != expected_sha256:
        raise _ArtifactIncompatibleError("opencli identity invalid")


def _tree_entry(root: Path, path: Path) -> tuple[str, Path]:
    try:
        relative = path.relative_to(root)
        name = "." if relative == Path(".") else relative.as_posix()
        encoded = name.encode("utf-8", errors="strict")
    except (UnicodeError, ValueError):
        raise _ArtifactIncompatibleError("artifact tree invalid") from None
    if not encoded or len(encoded) > _MAX_PATH_BYTES or len(relative.parts) > _MAX_TREE_DEPTH:
        raise _ArtifactIncompatibleError("artifact tree invalid")
    return name, path


def _sorted_tree_entries(
    root: Path,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> Iterator[tuple[str, Path]]:
    chunks: list[list[tuple[str, Path]]] = []
    chunk = [_tree_entry(root, root)]
    directories = [root]
    entry_count = 1

    try:
        while directories:
            directory = directories.pop()
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    if checkpoint is not None:
                        checkpoint()
                    if entry_count >= _MAX_TREE_ENTRIES:
                        raise _ArtifactIncompatibleError("artifact tree invalid")
                    path = Path(entry.path)
                    chunk.append(_tree_entry(root, path))
                    entry_count += 1
                    if entry.is_dir(follow_symlinks=False):
                        directories.append(path)
                    if len(chunk) >= _TREE_SORT_CHUNK_ENTRIES:
                        if checkpoint is not None:
                            checkpoint()
                        chunk.sort(key=lambda value: value[0])
                        if checkpoint is not None:
                            checkpoint()
                        chunks.append(chunk)
                        chunk = []
        if chunk:
            if checkpoint is not None:
                checkpoint()
            chunk.sort(key=lambda value: value[0])
            if checkpoint is not None:
                checkpoint()
            chunks.append(chunk)
    except _ArtifactIncompatibleError:
        raise
    except FileNotFoundError:
        raise _ArtifactUnavailableError("artifact unavailable") from None
    except OSError:
        raise _ArtifactUnavailableError("artifact unavailable") from None

    ordered = heapq.merge(*chunks, key=lambda value: value[0])
    for index, value in enumerate(ordered):
        if checkpoint is not None and index % _TREE_SORT_CHUNK_ENTRIES == 0:
            checkpoint()
        yield value


def _tree_sha256(root: Path, *, checkpoint: Callable[[], None] | None = None) -> str:
    digest = hashlib.sha256(b"agent-reach-opencli-tree-v1\0")
    total_bytes = 0
    for name, path in _sorted_tree_entries(root, checkpoint=checkpoint):
        if checkpoint is not None:
            checkpoint()
        try:
            encoded = name.encode("utf-8", errors="strict")
            metadata = path.lstat()
        except UnicodeError:
            raise _ArtifactIncompatibleError("artifact tree invalid") from None
        except FileNotFoundError:
            raise _ArtifactUnavailableError("artifact unavailable") from None
        except OSError:
            raise _ArtifactUnavailableError("artifact unavailable") from None
        if metadata.st_uid != os.getuid() or metadata.st_mode & _FORBIDDEN_TREE_MODE_BITS:
            raise _ArtifactIncompatibleError("artifact tree invalid")
        digest.update(b"D" if stat.S_ISDIR(metadata.st_mode) else b"F")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update((metadata.st_mode & 0o777).to_bytes(2, "big"))
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise _ArtifactIncompatibleError("artifact tree invalid")
        total_bytes += metadata.st_size
        if total_bytes > _MAX_TREE_BYTES:
            raise _ArtifactIncompatibleError("artifact tree invalid")
        digest.update(metadata.st_size.to_bytes(8, "big"))
        try:
            with path.open("rb") as stream:
                observed = os.fstat(stream.fileno())
                if (observed.st_dev, observed.st_ino) != (metadata.st_dev, metadata.st_ino):
                    raise _ArtifactIncompatibleError("artifact tree invalid")
                remaining = metadata.st_size
                while remaining:
                    if checkpoint is not None:
                        checkpoint()
                    chunk = stream.read(min(64 * 1_024, remaining))
                    if not chunk:
                        raise _ArtifactIncompatibleError("artifact tree invalid")
                    digest.update(chunk)
                    remaining -= len(chunk)
                if stream.read(1):
                    raise _ArtifactIncompatibleError("artifact tree invalid")
        except _ArtifactIncompatibleError:
            raise
        except FileNotFoundError:
            raise _ArtifactUnavailableError("artifact unavailable") from None
        except OSError:
            raise _ArtifactUnavailableError("artifact unavailable") from None
    return digest.hexdigest()


def _validate_package_identity(
    root: Path,
    cli: Path,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> None:
    package_json = root / _PACKAGE_ROOT / "package.json"
    try:
        if checkpoint is not None:
            checkpoint()
        metadata = package_json.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or metadata.st_size <= 0
            or metadata.st_size > _MAX_PACKAGE_JSON_BYTES
            or metadata.st_mode & 0o022
        ):
            raise _ArtifactIncompatibleError("package identity invalid")
        raw_buffer = bytearray()
        with package_json.open("rb") as stream:
            observed = os.fstat(stream.fileno())
            if (
                (observed.st_dev, observed.st_ino) != (metadata.st_dev, metadata.st_ino)
                or not stat.S_ISREG(observed.st_mode)
                or observed.st_uid != metadata.st_uid
                or observed.st_nlink != metadata.st_nlink
                or observed.st_size != metadata.st_size
                or observed.st_mode != metadata.st_mode
            ):
                raise _ArtifactIncompatibleError("package identity invalid")
            remaining = metadata.st_size
            while remaining:
                if checkpoint is not None:
                    checkpoint()
                chunk = stream.read(min(64 * 1_024, remaining))
                if not chunk:
                    raise _ArtifactIncompatibleError("package identity invalid")
                raw_buffer.extend(chunk)
                remaining -= len(chunk)
            if checkpoint is not None:
                checkpoint()
            if stream.read(1):
                raise _ArtifactIncompatibleError("package identity invalid")
            final = os.fstat(stream.fileno())
            if (
                (
                    final.st_dev,
                    final.st_ino,
                    final.st_size,
                    final.st_mode,
                    final.st_uid,
                    final.st_nlink,
                )
                != (
                    observed.st_dev,
                    observed.st_ino,
                    observed.st_size,
                    observed.st_mode,
                    observed.st_uid,
                    observed.st_nlink,
                )
                or final.st_mtime_ns != observed.st_mtime_ns
                or final.st_ctime_ns != observed.st_ctime_ns
            ):
                raise _ArtifactIncompatibleError("package identity invalid")
        raw = bytes(raw_buffer)
    except _ArtifactIncompatibleError:
        raise
    except FileNotFoundError:
        raise _ArtifactUnavailableError("package unavailable") from None
    except OSError:
        raise _ArtifactUnavailableError("package unavailable") from None
    if checkpoint is not None:
        checkpoint()
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_json_pairs)
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise _ArtifactIncompatibleError("package identity invalid") from None
    if not isinstance(value, Mapping):
        raise _ArtifactIncompatibleError("package identity invalid")
    package = cast(Mapping[str, object], value)
    binary = package.get("bin")
    if (
        package.get("name") != _PACKAGE_NAME
        or package.get("version") != _BACKEND_VERSION
        or not isinstance(binary, Mapping)
        or set(binary) != {"opencli"}
        or binary.get("opencli") != "dist/src/main.js"
        or cli != root / _PACKAGE_CLI
    ):
        raise _ArtifactIncompatibleError("package identity invalid")


def _reject_duplicate_json_pairs(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate json key")
        result[key] = value
    return result


def _invoke_opencli(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
    session: OpenCliSessionV1,
    deadline: float,
) -> ExecutionResultV1:
    state = _ProjectionState()
    limit = _effective_limit(request, context)
    try:
        with tempfile.TemporaryDirectory(prefix=".agent-reach-opencli-") as name:
            private_root = Path(name).resolve(strict=True)
            node, cli, session_home, lifecycle_guard = _validate_session(
                session,
                context,
                deadline,
                private_root,
            )
            argv = (
                str(node),
                str(cli),
                *_command_argv(request, limit),
                "--format",
                "yaml",
            )
            environment, cwd = _private_environment(
                private_root,
                session_home,
                lifecycle_guard,
            )
            _execution_checkpoint(context, deadline)
            return_code, stdout, stderr = _run_process(
                argv,
                environment,
                cwd,
                context,
                deadline,
            )
    except _CheckpointRaised:
        raise
    except (
        _ArtifactUnavailableError,
        _ArtifactIncompatibleError,
        _BackendContractError,
        _BackendDeadlineError,
        _BackendTransientError,
    ):
        raise
    except OSError:
        raise _ArtifactUnavailableError("backend unavailable") from None
    if return_code != 0:
        code = _error_code(return_code, stdout, stderr)
        return _failure(request, code)
    if stderr:
        raise _BackendContractError("unexpected backend stderr")
    rows = _success_rows(stdout, request)
    items = _project_rows(request, rows, limit, context, state)
    requested_limit = request.arguments.get("limit")
    narrowed = type(requested_limit) is int and requested_limit > limit
    return ExecutionSuccessV1(
        PROTOCOL_VERSION,
        request.source,
        request.operation,
        _BACKEND_ID,
        _BACKEND_VERSION,
        items,
        truncated=narrowed or state.truncated,
    )


def _effective_limit(request: ExecutionRequestV1, context: ExecutionContextV1) -> int:
    requested = request.arguments.get("limit")
    if type(requested) is int:
        return min(requested, context.limits.maximum_items, 50)
    if (request.source, request.operation) == ("reddit", "read.post"):
        return min(14, context.limits.maximum_items)
    return 1


def _command_argv(request: ExecutionRequestV1, limit: int) -> tuple[str, ...]:
    arguments = request.arguments
    key = (request.source, request.operation)
    if key == ("reddit", "search.posts"):
        return ("reddit", "search", cast(str, arguments["query"]), "--limit", str(limit))
    if key == ("twitter", "search.posts"):
        return ("twitter", "search", cast(str, arguments["query"]), "--limit", str(limit))
    if key == ("xiaohongshu", "search.notes"):
        return (
            "xiaohongshu",
            "search",
            cast(str, arguments["query"]),
            "--limit",
            str(limit),
        )
    if key == ("reddit", "read.post"):
        post_id = _contract_reddit_post_id_from_url(arguments["url"])
        if post_id is None:
            raise _BackendContractError("request invalid")
        return (
            "reddit",
            "read",
            post_id,
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
        )
    if key == ("reddit", "browse.subreddit"):
        return (
            "reddit",
            "subreddit",
            cast(str, arguments["subreddit"]),
            "--sort",
            "hot",
            "--time",
            "all",
            "--limit",
            str(limit),
        )
    if key == ("reddit", "browse.hot"):
        return ("reddit", "hot", "--limit", str(limit))
    if key == ("reddit", "browse.popular"):
        return ("reddit", "popular", "--limit", str(limit))
    if key == ("reddit", "browse.all"):
        return ("reddit", "frontpage", "--limit", str(limit))
    if key == ("reddit", "read.subreddit"):
        return ("reddit", "subreddit-info", cast(str, arguments["subreddit"]))
    if key == ("facebook", "search"):
        return ("facebook", "search", cast(str, arguments["query"]), "--limit", str(limit))
    if key == ("facebook", "read.profile"):
        return ("facebook", "profile", cast(str, arguments["username"]))
    if key == ("facebook", "browse.feed"):
        return ("facebook", "feed", "--limit", str(limit))
    if key == ("facebook", "browse.groups"):
        return ("facebook", "groups", "--limit", str(limit))
    if key == ("instagram", "search.users"):
        return ("instagram", "search", cast(str, arguments["query"]), "--limit", str(limit))
    if key == ("instagram", "read.profile"):
        return ("instagram", "profile", cast(str, arguments["username"]))
    if key == ("instagram", "browse.user_posts"):
        return ("instagram", "user", cast(str, arguments["username"]), "--limit", str(limit))
    if key == ("instagram", "browse.explore"):
        return ("instagram", "explore", "--limit", str(limit))
    raise _BackendContractError("request invalid")


def _private_environment(
    root: Path,
    session_home: Path,
    lifecycle_guard: Path,
) -> tuple[dict[str, str], Path]:
    home = root / "home"
    xdg = root / "xdg"
    temporary = root / "tmp"
    for directory in (home, xdg, temporary):
        directory.mkdir(mode=0o700)
    return (
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "XDG_CONFIG_HOME": str(xdg),
            "TMPDIR": str(temporary),
            "OPENCLI_CONFIG_DIR": str(session_home / ".opencli"),
            "NODE_OPTIONS": f"--import={lifecycle_guard.as_uri()}",
            "CI": "1",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "NO_COLOR": "1",
        },
        home,
    )


def _run_process(
    argv: tuple[str, ...],
    environment: Mapping[str, str],
    cwd: Path,
    context: ExecutionContextV1,
    deadline: float,
) -> tuple[int, bytes, bytes]:
    _execution_checkpoint(context, deadline)
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=dict(environment),
            shell=False,
            close_fds=True,
            start_new_session=True,
        )
    except OSError:
        raise _ArtifactUnavailableError("backend unavailable") from None
    threads: tuple[threading.Thread, ...] = ()
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    read_failed = threading.Event()
    output_overflow = threading.Event()
    try:
        stdout = process.stdout
        stderr = process.stderr
        if stdout is None or stderr is None:
            raise _BackendTransientError("backend pipes unavailable")

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
                read_failed.set()

        threads = (
            threading.Thread(
                target=read_bounded,
                args=(stdout, stdout_buffer, _MAX_STDOUT_BYTES),
                name="opencli-social-stdout",
                daemon=True,
            ),
            threading.Thread(
                target=read_bounded,
                args=(stderr, stderr_buffer, _MAX_STDERR_BYTES),
                name="opencli-social-stderr",
                daemon=True,
            ),
        )
        for thread in threads:
            thread.start()
        while True:
            _execution_checkpoint(context, deadline)
            if output_overflow.is_set():
                raise _BackendContractError("backend output invalid")
            if read_failed.is_set():
                raise _BackendTransientError("backend exchange failed")
            return_code = process.poll()
            if return_code is not None and all(not thread.is_alive() for thread in threads):
                return return_code, bytes(stdout_buffer), bytes(stderr_buffer)
            if time.monotonic() >= deadline:
                raise _BackendDeadlineError("backend deadline exceeded")
            time.sleep(_POLL_SECONDS)
    except (
        _BackendContractError,
        _BackendDeadlineError,
        _BackendTransientError,
        _CheckpointRaised,
    ):
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
        if process.poll() is None:
            _kill_and_reap(process)
        stdout_buffer[:] = b"\x00" * len(stdout_buffer)
        stderr_buffer[:] = b"\x00" * len(stderr_buffer)


def _kill_and_reap(process: subprocess.Popen[bytes]) -> None:
    group_signalled = False
    killpg = getattr(os, "killpg", None)
    sigkill = getattr(signal, "SIGKILL", None)
    if callable(killpg) and sigkill is not None:
        try:
            killpg(process.pid, sigkill)
            group_signalled = True
        except (ProcessLookupError, PermissionError, OSError):
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
    for stream in (process.stdout, process.stderr):
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


def _execution_checkpoint(context: ExecutionContextV1, deadline: float) -> None:
    _checkpoint(context)
    if time.monotonic() >= deadline:
        raise _BackendDeadlineError("backend deadline exceeded")


def _error_code(return_code: int, stdout: bytes, stderr: bytes) -> ExecutionErrorCodeV1:
    if stdout or not stderr:
        raise _BackendContractError("backend error frame invalid")
    value = _load_yaml(stderr)
    if not isinstance(value, Mapping) or not _bounded_yaml_tree(value, _MAX_ERROR_NODES):
        raise _BackendContractError("backend error frame invalid")
    envelope = cast(Mapping[str, object], value)
    if envelope.get("ok") is not False or not isinstance(envelope.get("error"), Mapping):
        raise _BackendContractError("backend error frame invalid")
    error = cast(Mapping[str, object], envelope["error"])
    code = error.get("code")
    framed_exit = error.get("exitCode")
    if type(code) is not str or type(framed_exit) is not int or framed_exit != return_code:
        raise _BackendContractError("backend error frame invalid")
    if code == "ARGUMENT" and return_code == 2:
        return "invalid_input"
    if code == "EMPTY_RESULT" and return_code == 66:
        return "not_found"
    if code in {"BROWSER_CONNECT", "ADAPTER_LOAD"} and return_code == 69:
        return "backend_unavailable"
    if code == "TIMEOUT" and return_code == 75:
        return "deadline_exceeded"
    if code == "RATE_LIMIT" and return_code == 75:
        return "rate_limit"
    if code in {"AUTH_REQUIRED", "LOGIN_WALL"} and return_code == 77:
        return "authentication"
    if code == "CONFIG" and return_code == 78:
        return "backend_incompatible"
    if code == "SELECTOR":
        return "backend_contract_violation"
    if code == "UNKNOWN":
        return "backend_contract_violation"
    if return_code in {1, 130} and code in {"COMMAND_EXEC", "PLUGIN"}:
        return "transient"
    raise _BackendContractError("backend error frame invalid")


def _load_yaml(raw: bytes) -> object:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        raise _BackendContractError("backend yaml invalid") from None
    if not text or _contains_invalid_scalar(text):
        raise _BackendContractError("backend yaml invalid")
    loader = _ClosedYamlLoader(text)
    try:
        return loader.get_single_data()
    except (RecursionError, ValueError, OverflowError, yaml.YAMLError):
        raise _BackendContractError("backend yaml invalid") from None
    finally:
        cast(Callable[[], None], loader.dispose)()


def _bounded_yaml_tree(value: object, maximum_nodes: int) -> bool:
    remaining = maximum_nodes
    stack = [value]
    while stack:
        current = stack.pop()
        remaining -= 1
        if remaining < 0:
            return False
        if current is None or type(current) in {bool, int}:
            continue
        if type(current) is float:
            if math.isfinite(current):
                continue
            return False
        if type(current) is str:
            if len(current) <= MAX_TEXT_CHARACTERS and not _contains_invalid_scalar(current):
                continue
            return False
        if isinstance(current, list):
            stack.extend(current)
            continue
        if isinstance(current, Mapping):
            if any(type(key) is not str for key in current):
                return False
            stack.extend(current.keys())
            stack.extend(current.values())
            continue
        return False
    return True


def _success_rows(raw: bytes, request: ExecutionRequestV1) -> tuple[Mapping[str, object], ...]:
    value = _load_yaml(raw)
    if not isinstance(value, list) or not _bounded_yaml_tree(value, _MAX_YAML_NODES):
        raise _BackendContractError("backend result invalid")
    expected = _ROW_FIELDS[(request.source, request.operation)]
    rows: list[Mapping[str, object]] = []
    for row in value:
        if not isinstance(row, Mapping) or set(row) != expected:
            raise _BackendContractError("backend result invalid")
        rows.append(cast(Mapping[str, object], row))
    key = (request.source, request.operation)
    maximum = (
        1
        if key in _SINGLE_ROW_OPERATIONS
        else (
            9
            if key == ("reddit", "read.subreddit")
            else (14 if key == ("reddit", "read.post") else 50)
        )
    )
    minimum = (
        1
        if key
        in {
            ("reddit", "read.post"),
            ("reddit", "read.subreddit"),
            ("facebook", "read.profile"),
            ("instagram", "read.profile"),
        }
        else 0
    )
    if not minimum <= len(rows) <= maximum:
        raise _BackendContractError("backend result invalid")
    return tuple(rows)


def _project_rows(
    request: ExecutionRequestV1,
    rows: tuple[Mapping[str, object], ...],
    limit: int,
    context: ExecutionContextV1,
    state: _ProjectionState,
) -> tuple[ExecutionItemV1, ...]:
    key = (request.source, request.operation)
    maximum_text = min(context.limits.maximum_text_characters, MAX_TEXT_CHARACTERS)
    if key == ("reddit", "read.post"):
        items = _project_reddit_thread(request, rows, maximum_text, state)
    elif key == ("reddit", "read.subreddit"):
        items = _project_subreddit(request, rows, maximum_text, state)
    elif request.source == "reddit":
        items = _project_reddit_posts(request, rows, maximum_text, state)
    elif key == ("facebook", "search"):
        items = _project_facebook_search(rows, maximum_text, state)
    elif key == ("facebook", "read.profile"):
        items = _project_facebook_profile(request, rows, maximum_text, state)
    elif key == ("facebook", "browse.feed"):
        items = _project_facebook_feed(rows, maximum_text, state)
    elif key == ("facebook", "browse.groups"):
        items = _project_facebook_groups(rows, maximum_text, state)
    elif key == ("instagram", "search.users"):
        items = _project_instagram_users(rows, state)
    elif key == ("instagram", "read.profile"):
        items = _project_instagram_profile(request, rows, maximum_text, state)
    elif key in {("instagram", "browse.user_posts"), ("instagram", "browse.explore")}:
        items = _project_instagram_posts(request, rows, maximum_text, state)
    elif key == ("twitter", "search.posts"):
        items = _project_twitter_posts(rows, maximum_text, state)
    elif key == ("xiaohongshu", "search.notes"):
        items = _project_xiaohongshu_notes(rows, maximum_text, state)
    else:
        raise _BackendContractError("backend result invalid")
    if len(items) > limit:
        state.truncated = True
        items = items[:limit]
    return tuple(items)


def _project_reddit_posts(
    request: ExecutionRequestV1,
    rows: tuple[Mapping[str, object], ...],
    maximum_text: int,
    state: _ProjectionState,
) -> list[ExecutionItemV1]:
    key = (request.source, request.operation)
    items: list[ExecutionItemV1] = []
    identities: set[str] = set()
    for position, row in enumerate(rows, start=1):
        if key in {("reddit", "browse.hot"), ("reddit", "browse.popular")}:
            _ordered_row_identifier(row["rank"], position)
        url = _required_url(row["url"])
        subreddit = _subreddit_value(row["subreddit"], state)
        if key == ("reddit", "browse.subreddit"):
            requested = cast(str, request.arguments["subreddit"])
            if subreddit.casefold() != requested.casefold():
                raise _BackendContractError("reddit identity invalid")
        url_identity = _contract_reddit_post_identity_from_url(url)
        url_id = None
        if url_identity is not None and url_identity[0].casefold() == subreddit.casefold():
            url_id = url_identity[1]
        field = "postId" if key == ("reddit", "browse.hot") else "id"
        native_id = (
            url_id
            if key == ("reddit", "browse.all")
            else _required_text(row[field], MAX_NATIVE_ID_CHARACTERS, state)
        )
        if (
            native_id is None
            or url_id != native_id.lower()
            or _REDDIT_POST_ID.fullmatch(native_id.lower()) is None
            or native_id.lower() in identities
        ):
            raise _BackendContractError("reddit identity invalid")
        native_id = native_id.lower()
        identities.add(native_id)
        score_field = "upvotes" if "upvotes" in row else "score"
        text_value = row.get("selftext")
        items.append(
            ExecutionItemV1(
                "reddit.post.v1",
                {
                    "text": _optional_text(text_value, maximum_text, state),
                    "native_id": native_id,
                    "title": _required_text(row["title"], MAX_TITLE_CHARACTERS, state),
                    "url": url,
                    "author": _optional_text(row["author"], MAX_AUTHOR_CHARACTERS, state),
                    "published_at": _reddit_timestamp(row.get("created_utc")),
                    "score": _optional_integer(row[score_field]),
                    "comment_count": _optional_integer(row["comments"]),
                    "subreddit": subreddit,
                    "media_type": _optional_text(row["post_hint"], 64, state),
                },
            )
        )
        _validate_media_fields(row)
    return items


def _project_reddit_thread(
    request: ExecutionRequestV1,
    rows: tuple[Mapping[str, object], ...],
    maximum_text: int,
    state: _ProjectionState,
) -> list[ExecutionItemV1]:
    url = cast(str, request.arguments["url"])
    native_id = _contract_reddit_post_id_from_url(url)
    if native_id is None:
        raise _BackendContractError("reddit identity invalid")
    items: list[ExecutionItemV1] = []
    for index, row in enumerate(rows):
        row_type = _required_or_empty_text(row["type"], 16, state)
        text = _required_text(row["text"], maximum_text, state)
        if index == 0:
            if row_type != "POST":
                raise _BackendContractError("reddit thread invalid")
            title = text.split("\n", maxsplit=1)[0][:MAX_TITLE_CHARACTERS]
            fields = {
                "text": text,
                "native_id": native_id,
                "title": title or "Reddit post",
                "url": url,
                "author": _optional_text(row["author"], MAX_AUTHOR_CHARACTERS, state),
                "score": _optional_integer(row["score"]),
                "kind": "post",
                "media_type": _optional_text(row["post_hint"], 64, state),
            }
        else:
            if row_type and _COMMENT_LEVEL.fullmatch(row_type) is None:
                raise _BackendContractError("reddit thread invalid")
            fields = {
                "text": text,
                "native_id": None,
                "title": None,
                "url": None,
                "author": _optional_text(row["author"], MAX_AUTHOR_CHARACTERS, state),
                "score": _optional_integer(row["score"]),
                "kind": "comment",
                "media_type": None,
            }
        _validate_media_fields(row)
        items.append(ExecutionItemV1("reddit.thread.item.v1", fields))
    return items


def _project_subreddit(
    request: ExecutionRequestV1,
    rows: tuple[Mapping[str, object], ...],
    maximum_text: int,
    state: _ProjectionState,
) -> list[ExecutionItemV1]:
    values: dict[str, object] = {}
    for row in rows:
        field = _required_text(row["field"], 64, state)
        if field in values:
            raise _BackendContractError("subreddit result invalid")
        values[field] = row["value"]
    expected = {
        "Name",
        "Title",
        "Subscribers",
        "Active Now",
        "NSFW",
        "Type",
        "Description",
        "Created",
        "URL",
    }
    if set(values) != expected:
        raise _BackendContractError("subreddit result invalid")
    requested = cast(str, request.arguments["subreddit"])
    name = _required_text(values["Name"], 64, state)
    if name.lower() != f"r/{requested}".lower():
        raise _BackendContractError("subreddit identity invalid")
    url = _required_url(values["URL"])
    canonical_url = f"https://www.reddit.com/r/{requested}/"
    parsed = urlsplit(url)
    if (
        parsed.hostname not in {"reddit.com", "www.reddit.com"}
        or parsed.path.rstrip("/").lower() != f"/r/{requested}".lower()
    ):
        raise _BackendContractError("subreddit identity invalid")
    nsfw = _required_text(values["NSFW"], 8, state).lower()
    if nsfw not in {"yes", "no"}:
        raise _BackendContractError("subreddit result invalid")
    title = _optional_text(values["Title"], MAX_TITLE_CHARACTERS, state) or name
    return [
        ExecutionItemV1(
            "reddit.subreddit.v1",
            {
                "text": _optional_text(values["Description"], maximum_text, state),
                "native_id": requested,
                "title": title,
                "url": canonical_url,
                "published_at": _optional_text(values["Created"], MAX_PUBLISHED_CHARACTERS, state),
                "subscriber_count": _optional_integer(values["Subscribers"]),
                "active_count": _optional_integer(values["Active Now"]),
                "nsfw": 1 if nsfw == "yes" else 0,
                "subreddit_type": _optional_text(values["Type"], 64, state),
            },
        )
    ]


def _project_facebook_search(
    rows: tuple[Mapping[str, object], ...], maximum_text: int, state: _ProjectionState
) -> list[ExecutionItemV1]:
    items: list[ExecutionItemV1] = []
    for position, row in enumerate(rows, start=1):
        items.append(
            ExecutionItemV1(
                "facebook.search.result.v1",
                {
                    "text": _optional_text(row["text"], maximum_text, state),
                    "native_id": _ordered_row_identifier(row["index"], position),
                    "title": _required_text(row["title"], MAX_TITLE_CHARACTERS, state),
                    "url": _hosted_url(row["url"], "facebook.com"),
                },
            )
        )
    return items


def _project_facebook_profile(
    request: ExecutionRequestV1,
    rows: tuple[Mapping[str, object], ...],
    maximum_text: int,
    state: _ProjectionState,
) -> list[ExecutionItemV1]:
    row = rows[0]
    username = _required_text(row["username"], MAX_NATIVE_ID_CHARACTERS, state)
    requested = cast(str, request.arguments["username"])
    if username.casefold() != requested.casefold() or _USERNAME.fullmatch(username) is None:
        raise _BackendContractError("facebook identity invalid")
    name = _optional_text(row["name"], MAX_TITLE_CHARACTERS, state) or username
    return [
        ExecutionItemV1(
            "facebook.profile.v1",
            {
                "text": None,
                "native_id": username,
                "title": name,
                "url": _hosted_url(row["url"], "facebook.com"),
                "friend_count": _human_count(row["friends"]),
                "follower_count": _human_count(row["followers"]),
            },
        )
    ]


def _project_facebook_feed(
    rows: tuple[Mapping[str, object], ...], maximum_text: int, state: _ProjectionState
) -> list[ExecutionItemV1]:
    items: list[ExecutionItemV1] = []
    for position, row in enumerate(rows, start=1):
        items.append(
            ExecutionItemV1(
                "facebook.post.v1",
                {
                    "text": _required_text(row["content"], maximum_text, state),
                    "native_id": _ordered_row_identifier(row["index"], position),
                    "author": _optional_text(row["author"], MAX_AUTHOR_CHARACTERS, state),
                    "reaction_count": _human_count(row["likes"]),
                    "comment_count": _human_count(row["comments"]),
                    "share_count": _human_count(row["shares"]),
                },
            )
        )
    return items


def _project_facebook_groups(
    rows: tuple[Mapping[str, object], ...], maximum_text: int, state: _ProjectionState
) -> list[ExecutionItemV1]:
    items: list[ExecutionItemV1] = []
    for position, row in enumerate(rows, start=1):
        items.append(
            ExecutionItemV1(
                "facebook.group.v1",
                {
                    "text": _optional_text(row["last_post"], maximum_text, state),
                    "native_id": _ordered_row_identifier(row["index"], position),
                    "title": _required_text(row["name"], MAX_TITLE_CHARACTERS, state),
                    "url": _hosted_url(row["url"], "facebook.com"),
                },
            )
        )
    return items


def _project_instagram_users(
    rows: tuple[Mapping[str, object], ...], state: _ProjectionState
) -> list[ExecutionItemV1]:
    items: list[ExecutionItemV1] = []
    seen: set[str] = set()
    for position, row in enumerate(rows, start=1):
        _ordered_row_identifier(row["rank"], position)
        username = _required_text(row["username"], MAX_NATIVE_ID_CHARACTERS, state)
        if _USERNAME.fullmatch(username) is None or username.casefold() in seen:
            raise _BackendContractError("instagram identity invalid")
        seen.add(username.casefold())
        url = _hosted_url(row["url"], "instagram.com")
        if urlsplit(url).path.rstrip("/").casefold() != f"/{username}".casefold():
            raise _BackendContractError("instagram identity invalid")
        items.append(
            ExecutionItemV1(
                "instagram.user.v1",
                {
                    "native_id": username,
                    "title": _optional_text(row["name"], MAX_TITLE_CHARACTERS, state) or username,
                    "url": url,
                    "verified": _yes_no(row["verified"]),
                    "private": _yes_no(row["private"]),
                },
            )
        )
    return items


def _project_instagram_profile(
    request: ExecutionRequestV1,
    rows: tuple[Mapping[str, object], ...],
    maximum_text: int,
    state: _ProjectionState,
) -> list[ExecutionItemV1]:
    row = rows[0]
    username = _required_text(row["username"], MAX_NATIVE_ID_CHARACTERS, state)
    requested = cast(str, request.arguments["username"])
    if username.casefold() != requested.casefold() or _USERNAME.fullmatch(username) is None:
        raise _BackendContractError("instagram identity invalid")
    return [
        ExecutionItemV1(
            "instagram.profile.v1",
            {
                "text": _optional_text(row["bio"], maximum_text, state),
                "native_id": username,
                "title": _optional_text(row["name"], MAX_TITLE_CHARACTERS, state) or username,
                "url": f"https://www.instagram.com/{username}/",
                "follower_count": _optional_integer(row["followers"]),
                "following_count": _optional_integer(row["following"]),
                "post_count": _optional_integer(row["posts"]),
                "verified": _yes_no(row["verified"]),
            },
        )
    ]


def _project_instagram_posts(
    request: ExecutionRequestV1,
    rows: tuple[Mapping[str, object], ...],
    maximum_text: int,
    state: _ProjectionState,
) -> list[ExecutionItemV1]:
    explore = request.operation == "browse.explore"
    items: list[ExecutionItemV1] = []
    for position, row in enumerate(rows, start=1):
        native_id = _ordered_row_identifier(row["rank" if explore else "index"], position)
        author = (
            _optional_text(row["user"], MAX_AUTHOR_CHARACTERS, state)
            if explore
            else cast(str, request.arguments["username"])
        )
        if author is not None and _USERNAME.fullmatch(author) is None:
            raise _BackendContractError("instagram identity invalid")
        items.append(
            ExecutionItemV1(
                "instagram.post.v1",
                {
                    "text": _optional_text(row["caption"], maximum_text, state),
                    "native_id": native_id,
                    "author": author,
                    "published_at": (
                        None
                        if explore
                        else _optional_text(row["date"], MAX_PUBLISHED_CHARACTERS, state)
                    ),
                    "reaction_count": _optional_integer(row["likes"]),
                    "comment_count": _optional_integer(row["comments"]),
                    "media_type": _optional_text(row["type"], 64, state),
                },
            )
        )
    return items


def _project_twitter_posts(
    rows: tuple[Mapping[str, object], ...],
    maximum_text: int,
    state: _ProjectionState,
) -> list[ExecutionItemV1]:
    items: list[ExecutionItemV1] = []
    identities: set[str] = set()
    for row in rows:
        native_id = _required_text(row["id"], MAX_NATIVE_ID_CHARACTERS, state)
        if _TWITTER_POST_ID.fullmatch(native_id) is None or native_id in identities:
            raise _BackendContractError("twitter identity invalid")
        identities.add(native_id)
        if _twitter_post_id(row["url"]) != native_id:
            raise _BackendContractError("twitter identity invalid")
        _optional_text(row["bio"], maximum_text, state)
        _validate_twitter_native_media(row)
        has_media = row["has_media"]
        if type(has_media) is not bool:
            raise _BackendContractError("twitter media invalid")
        items.append(
            ExecutionItemV1(
                "twitter.post.v1",
                {
                    "text": _optional_text(row["text"], maximum_text, state),
                    "native_id": native_id,
                    "url": f"https://x.com/i/status/{native_id}",
                    "author": _optional_text(row["author"], MAX_AUTHOR_CHARACTERS, state),
                    "published_at": _optional_text(
                        row["created_at"], MAX_PUBLISHED_CHARACTERS, state
                    ),
                    "reaction_count": _optional_integer(row["likes"]),
                    "view_count": _optional_integer(row["views"]),
                    "has_media": 1 if has_media else 0,
                },
            )
        )
    return items


def _project_xiaohongshu_notes(
    rows: tuple[Mapping[str, object], ...],
    maximum_text: int,
    state: _ProjectionState,
) -> list[ExecutionItemV1]:
    items: list[ExecutionItemV1] = []
    identities: set[str] = set()
    for position, row in enumerate(rows, start=1):
        _ordered_row_identifier(row["rank"], position)
        native_id = _xiaohongshu_note_id(row["url"])
        if native_id is None or native_id in identities:
            raise _BackendContractError("xiaohongshu identity invalid")
        identities.add(native_id)
        _hosted_url(row["author_url"], "xiaohongshu.com")
        title = _required_text(row["title"], MAX_TITLE_CHARACTERS, state)
        items.append(
            ExecutionItemV1(
                "xiaohongshu.note.v1",
                {
                    "text": title[:maximum_text],
                    "native_id": native_id,
                    "title": title,
                    "url": f"https://www.xiaohongshu.com/explore/{native_id}",
                    "author": _optional_text(row["author"], MAX_AUTHOR_CHARACTERS, state),
                    "published_at": _optional_text(
                        row["published_at"], MAX_PUBLISHED_CHARACTERS, state
                    ),
                    "reaction_count": _human_count(row["likes"]),
                },
            )
        )
        if len(title) > maximum_text:
            state.truncated = True
    return items


def _twitter_post_id(value: object) -> str | None:
    if type(value) is not str or not _valid_public_result_url(value):
        return None
    parsed = urlsplit(value)
    host = parsed.hostname
    if (
        host not in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    parts = parsed.path.rstrip("/").split("/")
    if len(parts) != 4 or parts[0] != "" or parts[2] != "status":
        return None
    post_id = parts[3]
    return post_id if _TWITTER_POST_ID.fullmatch(post_id) is not None else None


def _xiaohongshu_note_id(value: object) -> str | None:
    if type(value) is not str or not _valid_public_result_url(value):
        return None
    parsed = urlsplit(value)
    if parsed.hostname not in {"xiaohongshu.com", "www.xiaohongshu.com"}:
        return None
    parts = parsed.path.rstrip("/").split("/")
    candidates = (
        parts[2] if len(parts) == 3 and parts[1] == "explore" else None,
        parts[3] if len(parts) == 4 and parts[1:3] == ["discovery", "item"] else None,
    )
    return next(
        (
            candidate
            for candidate in candidates
            if type(candidate) is str and _XIAOHONGSHU_NOTE_ID.fullmatch(candidate)
        ),
        None,
    )


def _validate_twitter_native_media(row: Mapping[str, object]) -> None:
    for field in ("media_urls", "media_posters"):
        value = row[field]
        if not isinstance(value, list) or len(value) > 16:
            raise _BackendContractError("twitter media invalid")
        if any(type(item) is not str or not _valid_public_result_url(item) for item in value):
            raise _BackendContractError("twitter media invalid")
    for field in ("card", "quoted_tweet"):
        if not _bounded_native_value(row[field], maximum_nodes=128):
            raise _BackendContractError("twitter media invalid")


def _bounded_native_value(value: object, *, maximum_nodes: int) -> bool:
    pending = [value]
    remaining = maximum_nodes
    while pending:
        current = pending.pop()
        remaining -= 1
        if remaining < 0:
            return False
        if current is None or type(current) in {bool, int}:
            continue
        if type(current) is str:
            if len(current) > MAX_TEXT_CHARACTERS or _contains_invalid_scalar(current):
                return False
            continue
        if isinstance(current, list):
            if len(current) > 32:
                return False
            pending.extend(current)
            continue
        if isinstance(current, Mapping):
            if len(current) > 32 or any(type(key) is not str for key in current):
                return False
            pending.extend(current.keys())
            pending.extend(current.values())
            continue
        return False
    return True


def _required_url(value: object) -> str:
    if type(value) is not str or not _valid_public_result_url(value):
        raise _BackendContractError("result url invalid")
    return value


def _hosted_url(value: object, suffix: str) -> str:
    url = _required_url(value)
    host = urlsplit(url).hostname
    if host != suffix and not (type(host) is str and host.endswith(f".{suffix}")):
        raise _BackendContractError("result url invalid")
    return url


def _required_text(value: object, maximum: int, state: _ProjectionState) -> str:
    result = _optional_text(value, maximum, state)
    if result is None:
        raise _BackendContractError("result text invalid")
    return result


def _required_or_empty_text(value: object, maximum: int, state: _ProjectionState) -> str:
    if type(value) is not str or _contains_invalid_scalar(value):
        raise _BackendContractError("result text invalid")
    normalized = " ".join(value.split())
    if len(normalized) > maximum:
        state.truncated = True
        normalized = normalized[:maximum]
    return normalized


def _optional_text(value: object, maximum: int, state: _ProjectionState) -> str | None:
    if value is None:
        return None
    if type(value) is not str or _contains_invalid_scalar(value):
        raise _BackendContractError("result text invalid")
    normalized = " ".join(value.split())
    if not normalized or normalized == "-":
        return None
    if len(normalized) > maximum:
        state.truncated = True
        normalized = normalized[:maximum]
    return normalized


def _optional_integer(value: object) -> int | None:
    if value is None or value == "" or value == "-":
        return None
    if type(value) is int:
        result = value
    elif type(value) is float and math.isfinite(value) and value.is_integer():
        result = int(value)
    elif type(value) is str:
        if _INTEGER_TEXT.fullmatch(value) is None:
            raise _BackendContractError("result integer invalid")
        digits = value.replace(",", "")
        if len(digits) > 32:
            raise _BackendContractError("result integer invalid")
        result = int(digits)
    else:
        raise _BackendContractError("result integer invalid")
    if not 0 <= result <= (1 << 53) - 1:
        raise _BackendContractError("result integer invalid")
    return result


def _human_count(value: object) -> int | None:
    if value is None or value == "" or value == "-":
        return None
    if type(value) in {int, float}:
        return _optional_integer(value)
    if type(value) is not str or len(value) > 128 or _contains_invalid_scalar(value):
        raise _BackendContractError("result count invalid")
    match = _HUMAN_COUNT_TEXT.fullmatch(value)
    if match is None:
        raise _BackendContractError("result count invalid")
    numeric = match.group("number").replace(",", "")
    if len(numeric) > 32:
        raise _BackendContractError("result count invalid")
    try:
        number = Decimal(numeric)
    except InvalidOperation:
        raise _BackendContractError("result count invalid") from None
    multiplier = {
        "": 1,
        "k": 1_000,
        "m": 1_000_000,
        "b": 1_000_000_000,
        "万": 10_000,
        "亿": 100_000_000,
    }[match.group("suffix").lower()]
    scaled = number * multiplier
    if scaled != scaled.to_integral_value():
        raise _BackendContractError("result count invalid")
    result = int(scaled)
    if result < 0 or result > (1 << 53) - 1:
        raise _BackendContractError("result count invalid")
    return result


def _yes_no(value: object) -> int:
    if type(value) is bool:
        return int(value)
    if type(value) is str:
        normalized = value.strip().lower()
        if normalized in {"yes", "true"}:
            return 1
        if normalized in {"no", "false"}:
            return 0
    raise _BackendContractError("result boolean invalid")


def _ordered_row_identifier(value: object, expected: int) -> str:
    result = _optional_integer(value)
    if result != expected:
        raise _BackendContractError("result identity invalid")
    return str(result)


def _subreddit_value(value: object, state: _ProjectionState) -> str:
    result = _required_text(value, 64, state)
    if result.lower().startswith("r/"):
        result = result[2:]
    if _SUBREDDIT.fullmatch(result) is None:
        raise _BackendContractError("subreddit identity invalid")
    return result


def _reddit_timestamp(value: object) -> str | None:
    if value is None or value == "":
        return None
    if type(value) is str:
        try:
            number = float(value)
        except ValueError:
            raise _BackendContractError("reddit timestamp invalid") from None
    elif type(value) in {int, float}:
        try:
            number = float(cast(int | float, value))
        except OverflowError:
            raise _BackendContractError("reddit timestamp invalid") from None
    else:
        raise _BackendContractError("reddit timestamp invalid")
    if not math.isfinite(number) or number < 0:
        raise _BackendContractError("reddit timestamp invalid")
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        raise _BackendContractError("reddit timestamp invalid") from None


def _validate_media_fields(row: Mapping[str, object]) -> None:
    for name in ("url_overridden_by_dest", "preview_image_url"):
        value = row[name]
        if value not in {"", None} and (
            type(value) is not str or not _valid_public_result_url(value)
        ):
            raise _BackendContractError("reddit media invalid")
    gallery = row["gallery_urls"]
    if not isinstance(gallery, list) or len(gallery) > 20:
        raise _BackendContractError("reddit media invalid")
    if any(type(url) is not str or not _valid_public_result_url(url) for url in gallery):
        raise _BackendContractError("reddit media invalid")
