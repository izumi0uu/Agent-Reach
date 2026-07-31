"""Hermetic security and contract tests for Exa Web execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

import pytest

from agent_reach.execution.v1 import (
    PROTOCOL_VERSION,
    ExecutionContextV1,
    ExecutionFailureV1,
    ExecutionLimitsV1,
    ExecutionRequestV1,
    ExecutionSuccessV1,
    McporterArtifactsV1,
    NetworkAccessV1,
    exa,
    execute,
)

QUERY_CANARY = "秘密 QUERY_CANARY"
STERILE_CONFIG = b'{"imports":[],"mcpServers":{}}'
VALID_RESULT_TEXT = """Title: Result One
URL: https://example.com/articles/one?from=exa
Published: 2026-07-31
Author: Researcher
Highlights:
First highlight
Second highlight"""

VALID_CLI = r'''import json
import os
from pathlib import Path
import sys

arguments = sys.argv[1:]
assert arguments[0] == "--config"
assert Path(arguments[1]).read_bytes() == b'{"imports":[],"mcpServers":{}}'
assert arguments[2:] == [
    "--log-level", "error", "call",
    "--http-url", "https://mcp.exa.ai/mcp",
    "--name", "exa", "--tool", "web_search_exa",
    "--args", "-", "--output", "json",
    "--timeout", "14000", "--no-oauth",
]
payload = json.load(sys.stdin)
assert set(payload) == {"query", "numResults"}
assert payload["numResults"] == 20
query = payload["query"]
assert query not in "\0".join(sys.argv)
assert query not in os.getcwd()
assert all(query not in value for value in os.environ.values())
assert os.getcwd() == os.environ["HOME"]
assert Path(os.environ["HOME"]).is_dir()
assert Path(os.environ["XDG_CONFIG_HOME"]).is_dir()
assert Path(os.environ["TMPDIR"]).is_dir()
assert os.environ["LANG"] == os.environ["LC_ALL"] == "C.UTF-8"
assert os.environ["TZ"] == "UTC"
assert os.environ["NO_COLOR"] == "1"
assert os.environ["MCPORTER_LOG_LEVEL"] == "error"
text = """Title: Result One
URL: https://example.com/articles/one?from=exa
Published: 2026-07-31
Author: Researcher
Highlights:
First highlight
Second highlight"""
json.dump({
    "content": [{
        "type": "text",
        "text": text,
        "_meta": {"searchTime": 0.42},
    }],
    "isError": False,
}, sys.stdout, separators=(",", ":"))
'''


@dataclass(frozen=True)
class _ArtifactFixture:
    root: Path
    cli: Path
    config: Path
    node: Path

    def capability(self) -> McporterArtifactsV1:
        return McporterArtifactsV1(
            node_executable=str(self.node),
            node_sha256=exa._file_sha256(
                self.node,
                maximum_bytes=exa._MAX_NODE_BYTES,
                executable=True,
            ),
            mcporter_root=str(self.root),
            mcporter_cli=str(self.cli),
            mcporter_tree_sha256=exa._mcporter_tree_digest(self.root),
            config_path=str(self.config),
            config_sha256=hashlib.sha256(self.config.read_bytes()).hexdigest(),
        )


@pytest.fixture
def artifact_fixture(tmp_path: Path) -> _ArtifactFixture:
    base = tmp_path.resolve()
    root = base / "mcporter-closure"
    cli = root / "dist" / "cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text(VALID_CLI, encoding="utf-8")
    (root / "package.json").write_text(
        json.dumps(
            {"name": "mcporter", "version": "0.12.3"},
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    config = base / "sterile-config.json"
    config.write_bytes(STERILE_CONFIG)
    node = base / "node"
    source_executable = Path(sys.executable).resolve(strict=True)
    shutil.copyfile(source_executable, node)
    node.chmod(0o755)
    base_executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve(strict=True)
    (base / "pyvenv.cfg").write_text(
        "\n".join(
            (
                f"home = {base_executable.parent}",
                "include-system-site-packages = false",
                f"version = {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                f"executable = {base_executable}",
                "",
            )
        ),
        encoding="utf-8",
    )
    return _ArtifactFixture(
        root=root,
        cli=cli,
        config=config,
        node=node,
    )


def _request(*, limit: int = 50, query: str = QUERY_CANARY) -> ExecutionRequestV1:
    return ExecutionRequestV1(
        PROTOCOL_VERSION,
        "exa",
        "search.web",
        {"query": query, "limit": limit},
    )


def _context(
    artifacts: McporterArtifactsV1,
    *,
    checkpoint: object | None = None,
    maximum_items: int = 20,
    maximum_text: int = 16_000,
) -> ExecutionContextV1:
    callback = (lambda: None) if checkpoint is None else checkpoint
    return ExecutionContextV1(
        (NetworkAccessV1(), artifacts),
        checkpoint=callback,  # type: ignore[arg-type]
        limits=ExecutionLimitsV1(
            maximum_items=maximum_items,
            maximum_text_characters=maximum_text,
        ),
    )


def _assert_failure(
    result: object,
    code: str,
    *,
    backend_identity: bool = True,
) -> ExecutionFailureV1:
    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == code
    if backend_identity:
        assert result.backend_id == "exa-mcporter"
        assert result.backend_version == "0.12.3+exa-web.v1"
    else:
        assert result.backend_id is None
        assert result.backend_version is None
    assert QUERY_CANARY not in repr(result)
    return result


def test_artifact_fixture_uses_a_safe_independent_node_copy(
    artifact_fixture: _ArtifactFixture,
) -> None:
    source = Path(sys.executable).resolve(strict=True)
    details = artifact_fixture.node.lstat()

    assert artifact_fixture.node != source
    assert stat.S_ISREG(details.st_mode)
    assert details.st_nlink == 1
    assert stat.S_IMODE(details.st_mode) == 0o755
    assert artifact_fixture.node.read_bytes() == source.read_bytes()
    artifact_fixture.capability()


def _write_cli(fixture: _ArtifactFixture, body: str) -> McporterArtifactsV1:
    fixture.cli.write_text(body, encoding="utf-8")
    return fixture.capability()


def test_web_search_uses_one_fixed_stdin_process_and_closed_projection(
    artifact_fixture: _ArtifactFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_popen = subprocess.Popen
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def spawn(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        calls.append((args, kwargs))
        return cast("subprocess.Popen[bytes]", real_popen(*args, **kwargs))

    monkeypatch.setattr(exa.subprocess, "Popen", spawn)
    monkeypatch.chdir(artifact_fixture.root.parent)

    result = execute(_request(), _context(artifact_fixture.capability()))

    assert isinstance(result, ExecutionSuccessV1)
    assert result.source == "exa"
    assert result.operation == "search.web"
    assert result.backend_id == "exa-mcporter"
    assert result.backend_version == "0.12.3+exa-web.v1"
    assert result.truncated is True
    assert len(result.items) == 1
    assert result.items[0].schema_id == "exa.search.result.v1"
    assert dict(result.items[0].fields) == {
        "text": "First highlight Second highlight",
        "title": "Result One",
        "url": "https://example.com/articles/one?from=exa",
        "author": "Researcher",
        "published_at": "2026-07-31",
    }
    assert len(calls) == 1
    positional, keyword = calls[0]
    assert positional == (
        (
            str(artifact_fixture.node),
            str(artifact_fixture.cli),
            "--config",
            str(artifact_fixture.config),
            "--log-level",
            "error",
            "call",
            "--http-url",
            "https://mcp.exa.ai/mcp",
            "--name",
            "exa",
            "--tool",
            "web_search_exa",
            "--args",
            "-",
            "--output",
            "json",
            "--timeout",
            "14000",
            "--no-oauth",
        ),
    )
    assert keyword["shell"] is False
    assert keyword["close_fds"] is True
    assert keyword["start_new_session"] is True
    assert QUERY_CANARY not in repr(positional)
    assert QUERY_CANARY not in repr(keyword["env"])
    assert QUERY_CANARY not in str(keyword["cwd"])
    environment = cast(dict[str, str], keyword["env"])
    assert set(environment) == {
        "HOME",
        "XDG_CONFIG_HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "TZ",
        "NO_COLOR",
        "MCPORTER_LOG_LEVEL",
    }
    assert "PATH" not in environment
    assert "NODE_OPTIONS" not in environment
    assert "EXA_API_KEY" not in environment
    assert "HTTPS_PROXY" not in environment
    assert not any(
        path.name.startswith(".agent-reach-exa-") for path in artifact_fixture.root.parent.iterdir()
    )


def test_no_results_is_a_closed_zero_item_success(
    artifact_fixture: _ArtifactFixture,
) -> None:
    text = "No search results found. Please try a different query."
    script = f"""import json, sys
json.dump({{"content":[{{"type":"text","text":{text!r}}}]}}, sys.stdout)
"""
    artifacts = _write_cli(artifact_fixture, script)

    result = execute(_request(), _context(artifacts))

    assert isinstance(result, ExecutionSuccessV1)
    assert result.items == ()


def test_context_narrows_provider_count_and_text(
    artifact_fixture: _ArtifactFixture,
) -> None:
    script = """import json, sys
payload = json.load(sys.stdin)
assert payload["numResults"] == 3
text = "Title: T\\nURL: https://example.com/x\\nPublished: N/A\\nAuthor: N/A\\nText: 123456789"
json.dump({"content":[{"type":"text","text":text}]}, sys.stdout)
"""
    artifacts = _write_cli(artifact_fixture, script)

    result = execute(
        _request(limit=9),
        _context(artifacts, maximum_items=3, maximum_text=5),
    )

    assert isinstance(result, ExecutionSuccessV1)
    assert result.truncated is True
    assert result.items[0].fields["text"] == "12345"
    assert result.items[0].fields["author"] is None
    assert result.items[0].fields["published_at"] is None


@pytest.mark.parametrize(
    "raw",
    [
        b"[]",
        b'{"content":[]}',
        b'{"content":[{"type":"image","data":"x"}]}',
        b'{"content":[{"type":"text","text":"x"}],"isError":true}',
        b'{"content":[{"type":"text","text":"x"}],"structuredContent":{}}',
        b'{"content":[{"type":"text","text":"x","resource":{}}]}',
        b'{"content":[{"type":"text","text":"x"},{"type":"text","text":"y"}]}',
        b'{"content":[{"type":"text","text":"x"}],"content":[]}',
        b'{"content":[{"type":"text","text":"x"}]}{}',
        b"\xff",
    ],
)
def test_mcp_envelope_rejects_flattening_errors_extra_blocks_and_trailing_json(
    raw: bytes,
) -> None:
    with pytest.raises(exa._BackendContractError):
        exa._mcp_text(raw)


def test_mcp_envelope_accepts_only_reviewed_text_metadata() -> None:
    raw = json.dumps(
        {
            "content": [
                {
                    "type": "text",
                    "text": VALID_RESULT_TEXT,
                    "_meta": {"searchTime": 1.25},
                }
            ]
        },
        separators=(",", ":"),
    ).encode()

    assert exa._mcp_text(raw) == VALID_RESULT_TEXT
    for metadata in (
        {"searchTime": -1},
        {"searchTime": "1"},
        {"searchTime": 3_601},
        {"query": QUERY_CANARY},
    ):
        invalid = json.dumps(
            {"content": [{"type": "text", "text": VALID_RESULT_TEXT, "_meta": metadata}]},
            separators=(",", ":"),
        ).encode()
        with pytest.raises(exa._BackendContractError):
            exa._mcp_text(invalid)


@pytest.mark.parametrize(
    "text",
    [
        "provider error: unavailable",
        "No search results found",
        "Title: T\nURL: https://example.com\nPublished: N/A\nAuthor: N/A",
        "Title: T\nURL: https://example.com\nAuthor: A\nPublished: N/A\nText: body",
        "Title: T\nURL: https://example.com\nPublished: N/A\nAuthor: N/A\nCode: body",
        "Title: T\nURL: https://example.com\nPublished: N/A\nAuthor: N/A\nHighlights:",
        "Title: T\nURL: https://example.com\nPublished: N/A\nAuthor: N/A\nText: ",
        f"Title: {QUERY_CANARY}\r\nURL: https://example.com\nPublished: N/A\nAuthor: N/A\nText: body",
    ],
)
def test_web_text_grammar_fails_closed(text: str) -> None:
    with pytest.raises(exa._BackendContractError):
        exa._project_web_results(text, maximum_items=20, maximum_text=16_000)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/result",
        "http://10.0.0.1/result",
        "http://[::1]/result",
        "https://localhost/result",
        "https://service.local/result",
        "https://user@example.com/result",
        "https://example.com:8443/result",
        "file:///tmp/result",
        "https://singlelabel/result",
        "https://example.com/back\\slash",
        "https://example.com/white space",
    ],
)
def test_web_projection_rejects_non_public_or_ambiguous_urls(url: str) -> None:
    text = f"Title: T\nURL: {url}\nPublished: N/A\nAuthor: N/A\nText: body"

    with pytest.raises(exa._BackendContractError):
        exa._project_web_results(text, maximum_items=20, maximum_text=16_000)


def test_web_projection_rejects_more_results_than_requested() -> None:
    text = f"{VALID_RESULT_TEXT}\n\n---\n\n{VALID_RESULT_TEXT}"

    with pytest.raises(exa._BackendContractError):
        exa._project_web_results(text, maximum_items=1, maximum_text=16_000)


@pytest.mark.parametrize("drift", ["node", "tree", "config", "version", "mode"])
def test_artifact_drift_fails_before_spawn(
    drift: str,
    artifact_fixture: _ArtifactFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = artifact_fixture.capability()
    if drift == "node":
        capability = replace(capability, node_sha256="0" * 64)
    elif drift == "tree":
        artifact_fixture.cli.write_text(VALID_CLI + "\n# drift\n", encoding="utf-8")
    elif drift == "config":
        artifact_fixture.config.write_bytes(b'{"imports":["ambient"],"mcpServers":{}}')
    elif drift == "version":
        (artifact_fixture.root / "package.json").write_text(
            '{"name":"mcporter","version":"0.12.4"}',
            encoding="utf-8",
        )
        capability = artifact_fixture.capability()
    else:
        artifact_fixture.cli.chmod(0o620)

    def unexpected_spawn(*_: object, **__: object) -> object:
        raise AssertionError("artifact drift reached process creation")

    monkeypatch.setattr(exa.subprocess, "Popen", unexpected_spawn)

    _assert_failure(execute(_request(), _context(capability)), "backend_incompatible")


def test_missing_artifact_is_unavailable_before_spawn(
    artifact_fixture: _ArtifactFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = artifact_fixture.capability()
    missing = (artifact_fixture.root.parent / "missing-node").resolve(strict=False)
    capability = replace(capability, node_executable=str(missing))

    def unexpected_spawn(*_: object, **__: object) -> object:
        raise AssertionError("missing artifact reached process creation")

    monkeypatch.setattr(exa.subprocess, "Popen", unexpected_spawn)

    _assert_failure(execute(_request(), _context(capability)), "backend_unavailable")


def test_artifacts_are_revalidated_after_initial_checkpoint(
    artifact_fixture: _ArtifactFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = artifact_fixture.capability()
    calls = 0

    def mutate_before_spawn() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            artifact_fixture.config.write_bytes(b"{}")

    def unexpected_spawn(*_: object, **__: object) -> object:
        raise AssertionError("post-checkpoint drift reached process creation")

    monkeypatch.setattr(exa.subprocess, "Popen", unexpected_spawn)

    _assert_failure(
        execute(_request(), _context(capability, checkpoint=mutate_before_spawn)),
        "backend_incompatible",
    )
    assert calls == 2


def test_cancellation_after_artifact_revalidation_prevents_spawn(
    artifact_fixture: _ArtifactFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Cancelled(BaseException):
        pass

    checkpoints = 0
    spawns = 0

    def checkpoint() -> None:
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints == 3:
            raise Cancelled()

    def unexpected_spawn(*_: object, **__: object) -> object:
        nonlocal spawns
        spawns += 1
        raise AssertionError("cancelled artifact validation reached provider spawn")

    monkeypatch.setattr(exa.subprocess, "Popen", unexpected_spawn)

    with pytest.raises(Cancelled):
        execute(
            _request(),
            _context(artifact_fixture.capability(), checkpoint=checkpoint),
        )

    assert checkpoints == 3
    assert spawns == 0


@pytest.mark.parametrize("error_type", [TimeoutError, asyncio.CancelledError])
def test_pre_spawn_checkpoint_exception_is_preserved_unchanged(
    error_type: type[BaseException],
    artifact_fixture: _ArtifactFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = error_type("CHECKPOINT_CANARY")
    checkpoints = 0
    spawns = 0

    def checkpoint() -> None:
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints == 3:
            raise expected

    def unexpected_spawn(*_: object, **__: object) -> object:
        nonlocal spawns
        spawns += 1
        raise AssertionError("checkpoint failure reached provider spawn")

    monkeypatch.setattr(exa.subprocess, "Popen", unexpected_spawn)

    with pytest.raises(error_type) as raised:
        execute(
            _request(),
            _context(artifact_fixture.capability(), checkpoint=checkpoint),
        )

    assert raised.value is expected
    assert checkpoints == 3
    assert spawns == 0


def test_cancellation_activated_during_validation_cannot_submit_query(
    artifact_fixture: _ArtifactFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Cancelled(BaseException):
        pass

    real_validate = exa._validate_artifacts
    cancellation_active = False
    checkpoints = 0
    provider_inputs: list[bytes] = []

    def validate(artifacts: McporterArtifactsV1) -> tuple[Path, Path, Path]:
        nonlocal cancellation_active
        validated = real_validate(artifacts)
        cancellation_active = True
        return validated

    def checkpoint() -> None:
        nonlocal checkpoints
        checkpoints += 1
        if cancellation_active:
            raise Cancelled()

    def unexpected_spawn(*_: object, **__: object) -> object:
        provider_inputs.append(QUERY_CANARY.encode())
        raise AssertionError("cancelled validation reached provider input")

    monkeypatch.setattr(exa, "_validate_artifacts", validate)
    monkeypatch.setattr(exa.subprocess, "Popen", unexpected_spawn)

    with pytest.raises(Cancelled):
        execute(
            _request(),
            _context(artifact_fixture.capability(), checkpoint=checkpoint),
        )

    assert checkpoints == 3
    assert provider_inputs == []


def test_cancellation_activated_while_popen_blocks_cannot_submit_query(
    artifact_fixture: _ArtifactFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Cancelled(BaseException):
        pass

    class MemoryStream:
        def __init__(self, writes: list[bytes] | None = None) -> None:
            self._writes = writes

        def write(self, value: bytes | bytearray) -> int:
            if self._writes is not None:
                self._writes.append(bytes(value))
            return len(value)

        def flush(self) -> None:
            return None

        def close(self) -> None:
            return None

        def read(self, _size: int = -1) -> bytes:
            return b""

    class Process:
        def __init__(self, provider_inputs: list[bytes]) -> None:
            self.stdin = MemoryStream(provider_inputs)
            self.stdout = MemoryStream()
            self.stderr = MemoryStream()
            self.pid = 424_242
            self.returncode: int | None = None
            self.wait_calls = 0

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, *, timeout: float) -> int:
            del timeout
            self.wait_calls += 1
            self.returncode = -int(signal.SIGKILL)
            return self.returncode

        def kill(self) -> None:
            self.returncode = -int(signal.SIGKILL)

    cancellation_active = False
    checkpoints = 0
    provider_inputs: list[bytes] = []
    process = Process(provider_inputs)
    started_threads: list[str] = []
    kills: list[tuple[int, signal.Signals]] = []

    def checkpoint() -> None:
        nonlocal checkpoints
        checkpoints += 1
        if cancellation_active:
            raise Cancelled()

    def spawn(*_: object, **__: object) -> object:
        nonlocal cancellation_active
        cancellation_active = True
        return process

    def start_inline(thread: object) -> None:
        assert isinstance(thread, exa.threading.Thread)
        started_threads.append(thread.name)
        thread.run()

    def killpg(pid: int, requested_signal: signal.Signals) -> None:
        kills.append((pid, requested_signal))

    monkeypatch.setattr(exa.subprocess, "Popen", spawn)
    monkeypatch.setattr(exa.threading.Thread, "start", start_inline)
    monkeypatch.setattr(exa.os, "killpg", killpg)

    with pytest.raises(Cancelled):
        execute(
            _request(),
            _context(artifact_fixture.capability(), checkpoint=checkpoint),
        )

    assert checkpoints == 4
    assert started_threads == []
    assert provider_inputs == []
    assert kills == [(process.pid, signal.SIGKILL)]
    assert process.wait_calls == 1


@pytest.mark.parametrize("error_type", [TimeoutError, asyncio.CancelledError])
def test_checkpoint_exception_survives_temporary_directory_cleanup_error(
    error_type: type[BaseException],
    artifact_fixture: _ArtifactFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    expected = error_type("CHECKPOINT_CANARY")
    private_root = tmp_path / "private"
    private_root.mkdir()

    class FailingTemporaryDirectory:
        name = str(private_root)

        def cleanup(self) -> None:
            raise OSError("CLEANUP_CANARY")

    def temporary_directory(**_: object) -> FailingTemporaryDirectory:
        return FailingTemporaryDirectory()

    def checkpoint_failure(*_: object, **__: object) -> bytes:
        raise exa._CheckpointRaised(expected)

    monkeypatch.setattr(exa.tempfile, "TemporaryDirectory", temporary_directory)
    monkeypatch.setattr(exa.subprocess, "Popen", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(exa, "_exchange_process", checkpoint_failure)

    with pytest.raises(error_type) as raised:
        execute(_request(), _context(artifact_fixture.capability()))

    assert raised.value is expected


@pytest.mark.parametrize(
    ("script", "expected_code"),
    [
        ("import sys\nsys.stderr.write('STDERR_CANARY')\n", "backend_contract_violation"),
        ("import sys\nsys.stdout.write('{bad json')\n", "backend_contract_violation"),
        ("raise SystemExit(7)\n", "transient"),
        (
            "import sys, time\nsys.stdout.write('X' * 70000)\nsys.stdout.flush()\ntime.sleep(5)\n",
            "backend_contract_violation",
        ),
        ("import time\ntime.sleep(5)\n", "deadline_exceeded"),
    ],
)
def test_process_failures_are_redacted_and_kill_the_original_group(
    script: str,
    expected_code: str,
    artifact_fixture: _ArtifactFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _write_cli(artifact_fixture, script)
    real_killpg = os.killpg
    kills: list[tuple[int, signal.Signals]] = []

    def killpg(pid: int, requested_signal: signal.Signals) -> None:
        kills.append((pid, requested_signal))
        real_killpg(pid, requested_signal)

    monkeypatch.setattr(exa.os, "killpg", killpg)
    monkeypatch.setattr(
        exa,
        "_PROCESS_TIMEOUT_SECONDS",
        0.05 if expected_code == "deadline_exceeded" else 1.0,
    )

    result = execute(_request(), _context(artifacts))

    _assert_failure(result, expected_code)
    assert kills
    assert all(requested_signal is signal.SIGKILL for _, requested_signal in kills)
    assert "STDERR_CANARY" not in repr(result)
    assert "bad json" not in repr(result)


def test_checkpoint_cancellation_kills_and_reaps_before_propagation(
    artifact_fixture: _ArtifactFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Cancelled(BaseException):
        pass

    artifacts = _write_cli(artifact_fixture, "import time\ntime.sleep(5)\n")
    real_killpg = os.killpg
    kills: list[int] = []
    checkpoints = 0

    def checkpoint() -> None:
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints == 4:
            raise Cancelled()

    def killpg(pid: int, requested_signal: signal.Signals) -> None:
        assert requested_signal is signal.SIGKILL
        kills.append(pid)
        real_killpg(pid, requested_signal)

    monkeypatch.setattr(exa.os, "killpg", killpg)

    with pytest.raises(Cancelled):
        execute(_request(), _context(artifacts, checkpoint=checkpoint))

    assert checkpoints == 4
    assert kills


def test_thread_start_failure_kills_and_reaps_without_raw_exception(
    artifact_fixture: _ArtifactFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _write_cli(artifact_fixture, "import time\ntime.sleep(5)\n")
    real_start = exa.threading.Thread.start
    real_killpg = os.killpg
    started: list[str] = []
    kills: list[int] = []

    joined: list[str] = []

    def start(thread: object) -> None:
        assert isinstance(thread, exa.threading.Thread)
        started.append(thread.name)
        if thread.name == "exa-mcporter-stdout":
            raise RuntimeError("THREAD_START_CANARY")
        real_start(thread)

    real_join = exa.threading.Thread.join

    def join(thread: object, timeout: float | None = None) -> None:
        assert isinstance(thread, exa.threading.Thread)
        assert thread.ident is not None
        joined.append(thread.name)
        real_join(thread, timeout=timeout)

    def killpg(pid: int, requested_signal: signal.Signals) -> None:
        assert requested_signal is signal.SIGKILL
        kills.append(pid)
        real_killpg(pid, requested_signal)

    monkeypatch.setattr(exa.threading.Thread, "start", start)
    monkeypatch.setattr(exa.threading.Thread, "join", join)
    monkeypatch.setattr(exa.os, "killpg", killpg)

    result = execute(_request(), _context(artifacts))

    _assert_failure(result, "transient")
    assert started == ["exa-mcporter-stdin", "exa-mcporter-stdout"]
    assert joined == ["exa-mcporter-stdin"]
    assert kills
    assert "THREAD_START_CANARY" not in repr(result)


def test_thread_construction_failure_closes_process_before_redacted_failure(
    artifact_fixture: _ArtifactFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _write_cli(artifact_fixture, "import time\ntime.sleep(5)\n")
    real_thread = exa.threading.Thread
    real_killpg = os.killpg
    constructed: list[str] = []
    kills: list[int] = []

    def construct(*args: object, **kwargs: object) -> exa.threading.Thread:
        name = cast(str, kwargs["name"])
        constructed.append(name)
        if name == "exa-mcporter-stdout":
            raise RuntimeError("THREAD_CONSTRUCTION_CANARY")
        return real_thread(*args, **kwargs)

    def killpg(pid: int, requested_signal: signal.Signals) -> None:
        assert requested_signal is signal.SIGKILL
        kills.append(pid)
        real_killpg(pid, requested_signal)

    monkeypatch.setattr(exa.threading, "Thread", construct)
    monkeypatch.setattr(exa.os, "killpg", killpg)

    result = execute(_request(), _context(artifacts))

    _assert_failure(result, "transient")
    assert constructed == ["exa-mcporter-stdin", "exa-mcporter-stdout"]
    assert kills
    assert "THREAD_CONSTRUCTION_CANARY" not in repr(result)


def test_process_group_cleanup_falls_back_to_direct_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        pid = 424242
        returncode: int | None = None
        kills = 0
        waits = 0

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            self.kills += 1
            self.returncode = -9

        def wait(self, *, timeout: float) -> int:
            assert timeout == exa._CLEANUP_WAIT_SECONDS
            self.waits += 1
            assert self.returncode is not None
            return self.returncode

    process = Process()

    def fail_killpg(*_: object) -> None:
        raise OSError("KILLPG_CANARY")

    monkeypatch.setattr(exa.os, "killpg", fail_killpg)

    exa._kill_and_reap(cast(subprocess.Popen[bytes], process))

    assert process.kills == 1
    assert process.waits == 1


@pytest.mark.parametrize("missing_attribute", ["killpg", "SIGKILL"])
def test_process_cleanup_uses_direct_kill_when_group_kill_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    missing_attribute: str,
) -> None:
    events: list[tuple[object, ...]] = []
    group_calls: list[tuple[int, object]] = []

    class Process:
        pid = 424242
        returncode: int | None = None

        def poll(self) -> int | None:
            events.append(("poll",))
            return self.returncode

        def kill(self) -> None:
            events.append(("kill",))
            self.returncode = -9

        def wait(self, *, timeout: float) -> int:
            events.append(("wait", timeout))
            assert self.returncode is not None
            return self.returncode

    def killpg(pid: int, requested_signal: object) -> None:
        group_calls.append((pid, requested_signal))

    monkeypatch.setattr(exa.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(exa.signal, "SIGKILL", object(), raising=False)
    if missing_attribute == "killpg":
        monkeypatch.delattr(exa.os, "killpg", raising=False)
    else:
        monkeypatch.delattr(exa.signal, "SIGKILL", raising=False)

    exa._kill_and_reap(cast(subprocess.Popen[bytes], Process()))

    assert group_calls == []
    assert events == [
        ("poll",),
        ("kill",),
        ("wait", exa._CLEANUP_WAIT_SECONDS),
    ]


def test_search_code_has_no_executable_route(
    artifact_fixture: _ArtifactFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def unexpected_spawn(*_: object, **__: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("search.code reached process creation")

    monkeypatch.setattr(exa.subprocess, "Popen", unexpected_spawn)
    request = ExecutionRequestV1(
        PROTOCOL_VERSION,
        "exa",
        "search.code",
        {"query": QUERY_CANARY, "limit": 5},
    )

    result = execute(request, _context(artifact_fixture.capability()))

    _assert_failure(result, "unsupported_operation", backend_identity=False)
    assert calls == 0
