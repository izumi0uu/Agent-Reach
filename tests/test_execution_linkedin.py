"""Offline execution tests for the closed LinkedIn MCP search runtime."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

import agent_reach.execution.v1.exa as exa_execution
import agent_reach.execution.v1.linkedin as linkedin_execution
from agent_reach.execution.v1 import (
    PROTOCOL_VERSION,
    ExecutionContextV1,
    ExecutionFailureV1,
    ExecutionItemV1,
    ExecutionLimitsV1,
    ExecutionRequestV1,
    ExecutionSuccessV1,
    LinkedInMcpV1,
    McporterArtifactsV1,
    execute,
)

_ALIAS_DIGEST = "2173ead9777f6202fd581b4ec227d7a7212e9798f26f530b3174ff4683797558"
_BACKEND_DIGEST = "62a889ac417e5e04d1635d5698df7178edc667a232dca42f417647e2ea25926d"
_RUNTIME_LOCK_DIGEST = "9150a44d903ecfecdc48d115b87385bb78f3c69f4067951cf238e7fda6f09a17"
_SOURCE_COMMIT = "7edbd32231afa6d40fabad207329591ad5a4feb0"
_SCHEMA_DIGEST = "2549d379d2306ba22c24f06015db67f448d109943fb96f2d656986d2d92f0699"
_QUERY_CANARY = "secret LINKEDIN_QUERY_CANARY"


def _expected_mcporter_config(tool: str) -> bytes:
    return json.dumps(
        {
            "imports": [],
            "mcpServers": {
                "linkedin": {
                    "allowedTools": [tool],
                    "baseUrl": "http://127.0.0.1:8001/mcp",
                }
            },
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


@dataclass(frozen=True)
class _LinkedInArtifactFixture:
    root: Path
    cli: Path
    config: Path
    node: Path

    def capability(self) -> McporterArtifactsV1:
        return McporterArtifactsV1(
            node_executable=str(self.node),
            node_sha256=exa_execution._file_sha256(
                self.node,
                maximum_bytes=exa_execution._MAX_NODE_BYTES,
                executable=True,
            ),
            mcporter_root=str(self.root),
            mcporter_cli=str(self.cli),
            mcporter_tree_sha256=exa_execution._mcporter_tree_digest(self.root),
            config_path=str(self.config),
            config_sha256=hashlib.sha256(self.config.read_bytes()).hexdigest(),
        )


def _create_artifact_fixture(
    tmp_path: Path,
    *,
    config: bytes,
    cli_body: str,
) -> _LinkedInArtifactFixture:
    base = tmp_path.resolve()
    root = base / "mcporter-closure"
    cli = root / "dist" / "cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text(cli_body, encoding="utf-8")
    (root / "package.json").write_text(
        json.dumps(
            {"name": "mcporter", "version": "0.12.3"},
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    config_path = base / "linkedin-config.json"
    config_path.write_bytes(config)
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
    return _LinkedInArtifactFixture(root, cli, config_path, node)


def _artifact_context(
    artifacts: McporterArtifactsV1,
    *,
    checkpoint: Callable[[], None] = lambda: None,
) -> ExecutionContextV1:
    return ExecutionContextV1(
        (artifacts, _service()),
        checkpoint=checkpoint,
    )


def _valid_cli_body(
    *,
    config: bytes,
    tool: str,
    payload: dict[str, object],
    output: dict[str, object],
) -> str:
    return f"""import json
import os
from pathlib import Path
import sys

arguments = sys.argv[1:]
assert arguments[0] == "--config"
config_bytes = Path(arguments[1]).read_bytes()
assert config_bytes == {config!r}
config = json.loads(config_bytes)
assert config == {{
    "imports": [],
    "mcpServers": {{
        "linkedin": {{
            "allowedTools": [{tool!r}],
            "baseUrl": "http://127.0.0.1:8001/mcp",
        }}
    }},
}}
assert arguments[2:] == [
    "--log-level", "error", "call", "--server", "linkedin",
    "--tool", {tool!r}, "--args", "-", "--output", "json",
    "--timeout", "14000", "--no-oauth",
]
request = json.load(sys.stdin)
assert request == {payload!r}
assert request["keywords"] not in "\\0".join(sys.argv)
assert all(request["keywords"] not in value for value in os.environ.values())
json.dump({output!r}, sys.stdout, separators=(",", ":"))
"""


def _request(
    operation: str,
    *,
    query: str = "AI engineer",
    limit: int = 10,
) -> ExecutionRequestV1:
    return ExecutionRequestV1(
        PROTOCOL_VERSION,
        "linkedin",
        operation,
        {"query": query, "limit": limit},
    )


def _artifacts(root: Path) -> McporterArtifactsV1:
    return McporterArtifactsV1(
        node_executable=str(root / "node"),
        node_sha256="a" * 64,
        mcporter_root=str(root / "mcporter"),
        mcporter_cli=str(root / "mcporter" / "dist" / "cli.js"),
        mcporter_tree_sha256="b" * 64,
        config_path=str(root / "config.json"),
        config_sha256="c" * 64,
    )


def _service() -> LinkedInMcpV1:
    return LinkedInMcpV1(
        endpoint="http://127.0.0.1:8001/mcp",
        alias_wheel_sha256=_ALIAS_DIGEST,
        backend_wheel_sha256=_BACKEND_DIGEST,
        runtime_lock_sha256=_RUNTIME_LOCK_DIGEST,
        source_commit=_SOURCE_COMMIT,
        schema_sha256=_SCHEMA_DIGEST,
        log_level="WARNING",
        tool_timeout_seconds=12,
    )


def _context(
    root: Path,
    *,
    maximum_text_characters: int = 16_000,
    checkpoint: Callable[[], None] = lambda: None,
) -> ExecutionContextV1:
    return ExecutionContextV1(
        (_artifacts(root), _service()),
        checkpoint=checkpoint,
        limits=ExecutionLimitsV1(maximum_text_characters=maximum_text_characters),
    )


def _people_document(query: str = "AI engineer") -> dict[str, object]:
    return {
        "url": (
            f"https://www.linkedin.com/search/results/people/?keywords={query.replace(' ', '+')}"
        ),
        "sections": {"search_results": "Jane Doe\nAI Engineer at Acme"},
        "references": {
            "search_results": [
                {
                    "kind": "person",
                    "url": "/in/jane-doe/",
                    "text": "Jane Doe",
                    "context": "search result",
                }
            ]
        },
    }


def _jobs_document(query: str = "python") -> dict[str, object]:
    return {
        "url": f"https://www.linkedin.com/jobs/search/?keywords={query}",
        "sections": {"search_results": "Software Engineer\nExample Corp"},
        "references": {
            "search_results": [
                {"kind": "job", "url": "/jobs/view/123/", "text": "Software Engineer"}
            ]
        },
        "job_ids": ["123", "456"],
    }


def test_people_and_jobs_preserve_one_closed_native_search_document(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    responses = iter([_people_document(), _jobs_document()])
    monkeypatch.setattr(
        linkedin_execution,
        "_invoke_mcporter",
        lambda _request, _context, _artifacts: next(responses),
    )

    people = execute(_request("search.people"), _context(tmp_path))
    jobs = execute(_request("search.jobs", query="python"), _context(tmp_path))

    assert isinstance(people, ExecutionSuccessV1)
    assert people.backend_id == "linkedin-scraper-mcp"
    assert people.backend_version == "4.14.0"
    assert len(people.items) == 1
    assert people.items[0].schema_id == "linkedin.people.search.document.v1"
    assert people.items[0].fields == {
        "url": "https://www.linkedin.com/search/results/people/?keywords=AI+engineer",
        "sections": '{"search_results":"Jane Doe\\nAI Engineer at Acme"}',
        "references": (
            '{"search_results":[{"context":"search result","kind":"person",'
            '"text":"Jane Doe","url":"/in/jane-doe/"}]}'
        ),
    }

    assert isinstance(jobs, ExecutionSuccessV1)
    assert jobs.items[0].schema_id == "linkedin.jobs.search.document.v1"
    assert jobs.items[0].fields == {
        "url": "https://www.linkedin.com/jobs/search/?keywords=python",
        "sections": '{"search_results":"Software Engineer\\nExample Corp"}',
        "references": (
            '{"search_results":[{"kind":"job","text":"Software Engineer","url":"/jobs/view/123/"}]}'
        ),
        "job_ids": '["123","456"]',
    }


def test_limit_is_public_only_and_jobs_pages_are_fixed_to_one() -> None:
    people = _request("search.people", limit=50)
    jobs = _request("search.jobs", limit=50)

    assert linkedin_execution._backend_arguments(people) == {"keywords": "AI engineer"}
    assert linkedin_execution._backend_arguments(jobs) == {
        "keywords": "AI engineer",
        "max_pages": 1,
    }
    assert "limit" not in linkedin_execution._backend_arguments(people)
    assert "limit" not in linkedin_execution._backend_arguments(jobs)


def test_public_limit_truncates_references_and_job_ids_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    people_document = _people_document()
    people_document["references"] = {
        "search_results": [
            {"kind": "person", "url": f"/in/person-{index}/", "text": f"Person {index}"}
            for index in range(3)
        ]
    }
    jobs_document = _jobs_document()
    jobs_document["references"] = {
        "search_results": [
            {"kind": "job", "url": f"/jobs/view/{index + 1}/", "text": f"Job {index}"}
            for index in range(3)
        ]
    }
    jobs_document["job_ids"] = ["1", "2", "3"]
    responses = iter([people_document, jobs_document])
    monkeypatch.setattr(
        linkedin_execution,
        "_invoke_mcporter",
        lambda _request, _context, _artifacts: next(responses),
    )

    people = execute(_request("search.people", limit=1), _context(tmp_path))
    jobs = execute(_request("search.jobs", query="python", limit=1), _context(tmp_path))

    assert isinstance(people, ExecutionSuccessV1)
    assert json.loads(cast(str, people.items[0].fields["references"]))["search_results"] == [
        {"kind": "person", "text": "Person 0", "url": "/in/person-0/"}
    ]
    assert people.truncated is True
    assert isinstance(jobs, ExecutionSuccessV1)
    assert json.loads(cast(str, jobs.items[0].fields["references"]))["search_results"] == [
        {"kind": "job", "text": "Job 0", "url": "/jobs/view/1/"}
    ]
    assert jobs.items[0].fields["job_ids"] == '["1"]'
    assert jobs.truncated is True


def test_empty_native_documents_remain_successful(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    people_document = _people_document()
    people_document["sections"] = {}
    people_document.pop("references")
    jobs_document = _jobs_document()
    jobs_document["sections"] = {}
    jobs_document.pop("references")
    jobs_document["job_ids"] = []
    responses = iter([people_document, jobs_document])
    monkeypatch.setattr(
        linkedin_execution,
        "_invoke_mcporter",
        lambda _request, _context, _artifacts: next(responses),
    )

    people = execute(_request("search.people"), _context(tmp_path))
    jobs = execute(_request("search.jobs", query="python"), _context(tmp_path))

    assert isinstance(people, ExecutionSuccessV1)
    assert people.items[0].fields["sections"] == "{}"
    assert people.items[0].fields["references"] is None
    assert isinstance(jobs, ExecutionSuccessV1)
    assert jobs.items[0].fields["sections"] == "{}"
    assert jobs.items[0].fields["references"] is None
    assert jobs.items[0].fields["job_ids"] == "[]"


def test_operation_specific_configs_expose_only_one_read_tool() -> None:
    expected = {
        "search.people": (
            "search_people",
            "bde84482cda676b21d6a2c10ceef2ad8ea76106a35b73fbf05dbd79c168a70a5",
        ),
        "search.jobs": (
            "search_jobs",
            "917b75d814de1e44021c21966b1887aca5dc7281069a67ef60b26b700be1a36b",
        ),
    }

    for operation, (tool, digest) in expected.items():
        config = linkedin_execution._mcporter_config(operation)
        assert config == _expected_mcporter_config(tool)
        assert hashlib.sha256(config).hexdigest() == digest
        assert b"send_message" not in config
        assert b"connect_with_person" not in config


@pytest.mark.parametrize(
    ("operation", "tool", "query", "payload", "document"),
    [
        (
            "search.people",
            "search_people",
            "AI engineer",
            {"keywords": "AI engineer"},
            _people_document(),
        ),
        (
            "search.jobs",
            "search_jobs",
            "python",
            {"keywords": "python", "max_pages": 1},
            _jobs_document(),
        ),
    ],
)
def test_real_artifact_validation_and_process_use_the_one_tool_config(
    operation: str,
    tool: str,
    query: str,
    payload: dict[str, object],
    document: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _expected_mcporter_config(tool)
    fixture = _create_artifact_fixture(
        tmp_path,
        config=config,
        cli_body=_valid_cli_body(
            config=config,
            tool=tool,
            payload=payload,
            output=document,
        ),
    )
    artifacts = fixture.capability()
    real_popen = subprocess.Popen
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def spawn(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        calls.append((args, kwargs))
        return cast("subprocess.Popen[bytes]", real_popen(*args, **kwargs))

    monkeypatch.setattr(linkedin_execution.subprocess, "Popen", spawn)
    monkeypatch.chdir(tmp_path)

    result = execute(
        _request(operation, query=query),
        _artifact_context(artifacts),
    )

    assert isinstance(result, ExecutionSuccessV1)
    assert result.operation == operation
    assert len(calls) == 1
    positional, keyword = calls[0]
    assert positional[0][0] == str(fixture.node)
    assert positional[0][1] == str(fixture.cli)
    assert keyword["shell"] is False
    assert keyword["close_fds"] is True
    assert keyword["start_new_session"] is True
    assert fixture.config.read_bytes() == config
    assert artifacts.config_sha256 == hashlib.sha256(config).hexdigest()
    assert not any(path.name.startswith(".agent-reach-linkedin-") for path in tmp_path.iterdir())


@pytest.mark.parametrize(
    ("operation", "configured_tool"),
    [
        ("search.people", "search_jobs"),
        ("search.jobs", "search_people"),
    ],
)
def test_wrong_operation_allowlist_is_rejected_before_spawn(
    operation: str,
    configured_tool: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = _create_artifact_fixture(
        tmp_path,
        config=_expected_mcporter_config(configured_tool),
        cli_body="raise SystemExit('unexpected spawn')\n",
    )
    spawned = 0

    def unexpected_spawn(*_: object, **__: object) -> object:
        nonlocal spawned
        spawned += 1
        raise AssertionError("operation-config mismatch spawned mcporter")

    monkeypatch.setattr(linkedin_execution.subprocess, "Popen", unexpected_spawn)
    monkeypatch.chdir(tmp_path)

    result = execute(
        _request(operation, query="python"),
        _artifact_context(fixture.capability()),
    )

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_incompatible"
    assert spawned == 0


def test_fixed_mcporter_argv_has_no_generic_or_write_authority(tmp_path: Path) -> None:
    node = tmp_path / "node"
    cli = tmp_path / "mcporter" / "dist" / "cli.js"
    config = tmp_path / "config.json"

    people = linkedin_execution._mcporter_argv(
        node,
        cli,
        config,
        operation="search.people",
    )
    jobs = linkedin_execution._mcporter_argv(
        node,
        cli,
        config,
        operation="search.jobs",
    )

    expected_prefix = (
        str(node),
        str(cli),
        "--config",
        str(config),
        "--log-level",
        "error",
        "call",
        "--server",
        "linkedin",
        "--tool",
    )
    assert people == (
        *expected_prefix,
        "search_people",
        "--args",
        "-",
        "--output",
        "json",
        "--timeout",
        "14000",
        "--no-oauth",
    )
    assert jobs == (
        *expected_prefix,
        "search_jobs",
        "--args",
        "-",
        "--output",
        "json",
        "--timeout",
        "14000",
        "--no-oauth",
    )
    joined = " ".join(people + jobs)
    for forbidden in ("send_message", "connect_with_person", "post", "keyword", "limit"):
        assert forbidden not in joined


def test_invocation_sends_only_keywords_and_fixed_page_count_on_stdin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured_argv: list[tuple[str, ...]] = []
    captured_payloads: list[bytes] = []
    response = _jobs_document()
    output = json.dumps(response).encode()

    expected_configs: list[bytes] = []

    def validate_artifacts(
        _artifacts: McporterArtifactsV1,
        *,
        expected_config: bytes,
    ) -> tuple[Path, Path, Path]:
        expected_configs.append(expected_config)
        return (
            tmp_path / "node",
            tmp_path / "mcporter" / "dist" / "cli.js",
            tmp_path / "config.json",
        )

    monkeypatch.setattr(linkedin_execution, "_validate_artifacts", validate_artifacts)

    class FakeProcess:
        pass

    def popen(argv: tuple[str, ...], **_: object) -> FakeProcess:
        captured_argv.append(argv)
        return FakeProcess()

    def exchange(
        _process: object,
        payload: bytearray,
        checkpoint: Callable[[], None],
    ) -> bytes:
        checkpoint()
        captured_payloads.append(bytes(payload))
        return output

    monkeypatch.setattr(linkedin_execution.subprocess, "Popen", popen)
    monkeypatch.setattr(linkedin_execution, "_exchange_process", exchange)

    value = linkedin_execution._invoke_mcporter(
        _request("search.jobs", query="python", limit=50),
        _context(tmp_path),
        _artifacts(tmp_path),
    )

    assert value == response
    assert captured_payloads == [b'{"keywords":"python","max_pages":1}']
    assert expected_configs == [linkedin_execution._JOBS_CONFIG]
    assert "python" not in captured_argv[0]
    assert "50" not in captured_argv[0]


@pytest.mark.parametrize(
    ("mutate", "error_code"),
    [
        (
            lambda value: value.update(url="http://www.linkedin.com/messaging"),
            "backend_contract_violation",
        ),
        (
            lambda value: value.update(url="https://www.linkedin.com/jobs/search/?keywords=wrong"),
            "backend_contract_violation",
        ),
        (lambda value: value.update(sections={"other": "text"}), "backend_contract_violation"),
        (
            lambda value: value.update(references={"search_results": "bad"}),
            "backend_contract_violation",
        ),
        (lambda value: value.update(job_ids=["abc"]), "backend_contract_violation"),
        (lambda value: value.update(job_ids=["123", "123"]), "backend_contract_violation"),
        (
            lambda value: value.update(section_errors={"search_results": {"message": "private"}}),
            "backend_unavailable",
        ),
    ],
)
def test_complete_document_identity_and_native_fields_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutate: Callable[[dict[str, object]], object],
    error_code: str,
) -> None:
    document = _jobs_document()
    mutate(document)
    monkeypatch.setattr(
        linkedin_execution,
        "_invoke_mcporter",
        lambda _request, _context, _artifacts: document,
    )

    result = execute(_request("search.jobs", query="python"), _context(tmp_path))

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == error_code


def test_plain_structured_document_is_required(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    document = _people_document()
    assert linkedin_execution._mcp_document(json.dumps(document).encode()) == document

    backend_text = "SECRET query and browser path"
    envelope = {
        "content": [{"type": "text", "text": backend_text}],
        "isError": True,
    }
    monkeypatch.setattr(
        linkedin_execution,
        "_invoke_mcporter",
        lambda _request, _context, _artifacts: envelope,
    )
    result = execute(_request("search.people"), _context(tmp_path))
    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == "backend_contract_violation"
    assert backend_text not in repr(result)


def test_text_bound_reports_truncation_and_cancellation_propagates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    document = _people_document()
    document["sections"] = {"search_results": "abcdef"}
    monkeypatch.setattr(
        linkedin_execution,
        "_invoke_mcporter",
        lambda _request, _context, _artifacts: document,
    )

    result = execute(
        _request("search.people"),
        _context(tmp_path, maximum_text_characters=3),
    )
    assert isinstance(result, ExecutionSuccessV1)
    assert result.items[0].fields["sections"] == '{"search_results":"abc"}'
    assert result.truncated is True

    cancelled = _context(
        tmp_path,
        checkpoint=lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        execute(_request("search.people"), cancelled)


def test_json_escaping_is_included_in_the_projected_text_bound(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    document = _people_document()
    document["sections"] = {"search_results": ('"\\' * 8_000)}
    document.pop("references")
    monkeypatch.setattr(
        linkedin_execution,
        "_invoke_mcporter",
        lambda _request, _context, _artifacts: document,
    )

    result = execute(_request("search.people"), _context(tmp_path))

    assert isinstance(result, ExecutionSuccessV1)
    sections = cast(str, result.items[0].fields["sections"])
    assert len(sections) <= 16_000
    assert json.loads(sections)["search_results"]
    assert result.truncated is True


@pytest.mark.parametrize(
    "references",
    [
        [{"kind": "send_message", "url": "/messaging/thread/2-write/"}],
        [{"kind": "external", "url": "javascript:alert(1)"}],
        [{"kind": "job", "url": "/in/not-a-job/"}],
        [
            {
                "kind": "company_urn",
                "url": "/search/results/people/?currentCompany=%5B%221115%22%5D",
                "value": "2222",
            }
        ],
        [{"kind": "person", "url": "/in/example/", "text": 123}],
        [{"kind": "person", "url": "/in/example/", "text": "x" * 81}],
        [{"kind": "person", "url": "/in/example/", "context": "write action"}],
        [
            {"kind": "person", "url": "/in/duplicate/", "text": "First Person"},
            {"kind": "person", "url": "/in/duplicate/", "text": "Second Person"},
        ],
    ],
)
def test_reference_authority_is_rejected_by_runtime_and_success_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    references: list[dict[str, object]],
) -> None:
    document = _people_document()
    document["references"] = {"search_results": references}
    monkeypatch.setattr(
        linkedin_execution,
        "_invoke_mcporter",
        lambda _request, _context, _artifacts: document,
    )

    runtime_result = execute(_request("search.people"), _context(tmp_path))
    assert isinstance(runtime_result, ExecutionFailureV1)
    assert runtime_result.error_code == "backend_contract_violation"

    item = ExecutionItemV1(
        "linkedin.people.search.document.v1",
        {
            "url": "https://www.linkedin.com/search/results/people/?keywords=AI+engineer",
            "sections": '{"search_results":"result"}',
            "references": json.dumps(
                {"search_results": references},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        },
    )
    with pytest.raises(ValueError):
        ExecutionSuccessV1(
            PROTOCOL_VERSION,
            "linkedin",
            "search.people",
            "linkedin-scraper-mcp",
            "4.14.0",
            (item,),
        )


def test_company_urn_reference_has_one_canonical_query_and_correlated_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    document = _people_document()
    document["references"] = {
        "search_results": [
            {
                "kind": "company_urn",
                "url": "/search/results/people/?currentCompany=%5B%221115%22%5D",
                "value": "1115",
                "context": "top card",
            }
        ]
    }
    monkeypatch.setattr(
        linkedin_execution,
        "_invoke_mcporter",
        lambda _request, _context, _artifacts: document,
    )

    result = execute(_request("search.people"), _context(tmp_path))

    assert isinstance(result, ExecutionSuccessV1)
    assert json.loads(cast(str, result.items[0].fields["references"])) == document["references"]


@pytest.mark.parametrize(
    "sections",
    [
        '{"search_results": "result"}',
        '{"search_results":"first","search_results":"second"}',
        '{"search_results":NaN}',
        '{"search_results":"result"} trailing',
    ],
)
def test_success_contract_requires_one_canonical_strict_json_document(sections: str) -> None:
    item = ExecutionItemV1(
        "linkedin.people.search.document.v1",
        {
            "url": "https://www.linkedin.com/search/results/people/?keywords=engineer",
            "sections": sections,
            "references": None,
        },
    )

    with pytest.raises(ValueError):
        ExecutionSuccessV1(
            PROTOCOL_VERSION,
            "linkedin",
            "search.people",
            "linkedin-scraper-mcp",
            "4.14.0",
            (item,),
        )


def test_linkedin_attestation_pins_both_distribution_wheels() -> None:
    service = _service()
    assert service.alias_wheel_sha256 == _ALIAS_DIGEST
    assert service.backend_wheel_sha256 == _BACKEND_DIGEST
    assert service.runtime_lock_sha256 == _RUNTIME_LOCK_DIGEST
    assert "7edbd322" in service.source_commit
    assert "d45536" not in repr(service)

    values = {
        "endpoint": service.endpoint,
        "alias_wheel_sha256": service.alias_wheel_sha256,
        "backend_wheel_sha256": service.backend_wheel_sha256,
        "runtime_lock_sha256": service.runtime_lock_sha256,
        "source_commit": service.source_commit,
        "schema_sha256": service.schema_sha256,
        "log_level": service.log_level,
        "tool_timeout_seconds": service.tool_timeout_seconds,
    }
    for field_name in values:
        tampered = dict(values)
        tampered[field_name] = (
            13 if field_name == "tool_timeout_seconds" else cast(str, values[field_name]) + "x"
        )
        with pytest.raises(ValueError):
            LinkedInMcpV1(**tampered)

    for log_level in ("WARNING", "ERROR", "CRITICAL"):
        accepted = dict(values)
        accepted["log_level"] = log_level
        assert LinkedInMcpV1(**accepted).log_level == log_level
    assert service.tool_timeout_seconds == 12


def test_schema_digest_is_derived_from_canonical_tools_list_fixture() -> None:
    fixture = Path(__file__).parent / "fixtures" / "linkedin_v4_14_0_read_tools.json"
    value = json.loads(fixture.read_text(encoding="ascii"))
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")

    assert len(payload) == 3_282
    assert hashlib.sha256(payload).hexdigest() == _SCHEMA_DIGEST


@pytest.mark.parametrize("exchange_fails", [False, True])
def test_backend_query_buffer_is_cleared_on_success_and_failure(
    exchange_fails: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: list[bytearray] = []
    submitted: list[bytes] = []

    def validate_artifacts(
        _artifacts: McporterArtifactsV1,
        *,
        expected_config: bytes,
    ) -> tuple[Path, Path, Path]:
        assert expected_config == _expected_mcporter_config("search_people")
        return (
            tmp_path / "node",
            tmp_path / "mcporter" / "dist" / "cli.js",
            tmp_path / "config.json",
        )

    def exchange(
        _process: object,
        payload: bytearray,
        _checkpoint: Callable[[], None],
    ) -> bytes:
        captured.append(payload)
        submitted.append(bytes(payload))
        if exchange_fails:
            raise linkedin_execution._BackendContractError("failure canary")
        return json.dumps(_people_document(_QUERY_CANARY)).encode()

    monkeypatch.setattr(linkedin_execution, "_validate_artifacts", validate_artifacts)
    monkeypatch.setattr(linkedin_execution.subprocess, "Popen", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(linkedin_execution, "_exchange_process", exchange)

    if exchange_fails:
        with pytest.raises(linkedin_execution._BackendContractError):
            linkedin_execution._invoke_mcporter(
                _request("search.people", query=_QUERY_CANARY),
                _context(tmp_path),
                _artifacts(tmp_path),
            )
    else:
        result = linkedin_execution._invoke_mcporter(
            _request("search.people", query=_QUERY_CANARY),
            _context(tmp_path),
            _artifacts(tmp_path),
        )
        assert result == _people_document(_QUERY_CANARY)

    assert submitted == [json.dumps({"keywords": _QUERY_CANARY}, separators=(",", ":")).encode()]
    assert len(captured) == 1
    assert captured[0] == bytearray(len(submitted[0]))
    assert _QUERY_CANARY.encode() not in captured[0]


@pytest.mark.parametrize(
    ("script", "expected_code", "timeout"),
    [
        (
            "import sys,time\n"
            "sys.stdout.buffer.write(b'X' * 131072)\n"
            "sys.stdout.flush()\n"
            "time.sleep(5)\n",
            "backend_contract_violation",
            2.0,
        ),
        (
            "import sys,time\n"
            "sys.stderr.buffer.write(b'STDERR_OVERFLOW_CANARY' * 2000)\n"
            "sys.stderr.flush()\n"
            "time.sleep(5)\n",
            "backend_contract_violation",
            2.0,
        ),
        (
            "import time\ntime.sleep(5)\n",
            "deadline_exceeded",
            0.05,
        ),
    ],
)
def test_process_overflow_and_deadline_kill_and_reap_the_process_group(
    script: str,
    expected_code: str,
    timeout: float,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = _create_artifact_fixture(
        tmp_path,
        config=_expected_mcporter_config("search_people"),
        cli_body=script,
    )
    artifacts = fixture.capability()
    real_popen = subprocess.Popen
    real_killpg = os.killpg
    processes: list[subprocess.Popen[bytes]] = []
    kills: list[tuple[int, signal.Signals]] = []

    def spawn(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = cast("subprocess.Popen[bytes]", real_popen(*args, **kwargs))
        processes.append(process)
        return process

    def killpg(pid: int, requested_signal: signal.Signals) -> None:
        kills.append((pid, requested_signal))
        real_killpg(pid, requested_signal)

    monkeypatch.setattr(linkedin_execution.subprocess, "Popen", spawn)
    monkeypatch.setattr(exa_execution.os, "killpg", killpg)
    monkeypatch.setattr(exa_execution, "_PROCESS_TIMEOUT_SECONDS", timeout)
    monkeypatch.chdir(tmp_path)

    result = execute(
        _request("search.people", query=_QUERY_CANARY),
        _artifact_context(artifacts),
    )

    assert isinstance(result, ExecutionFailureV1)
    assert result.error_code == expected_code
    assert _QUERY_CANARY not in repr(result)
    assert "STDERR_OVERFLOW_CANARY" not in repr(result)
    assert processes
    assert all(process.poll() is not None for process in processes)
    assert kills
    assert all(requested_signal is signal.SIGKILL for _, requested_signal in kills)
    assert {pid for pid, _ in kills}.issubset({process.pid for process in processes})
    assert not any(path.name.startswith(".agent-reach-linkedin-") for path in tmp_path.iterdir())


def test_checkpoint_cancellation_after_spawn_kills_and_reaps_before_propagation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Cancelled(BaseException):
        pass

    fixture = _create_artifact_fixture(
        tmp_path,
        config=_expected_mcporter_config("search_people"),
        cli_body="import time\ntime.sleep(5)\n",
    )
    artifacts = fixture.capability()
    real_popen = subprocess.Popen
    real_killpg = os.killpg
    processes: list[subprocess.Popen[bytes]] = []
    kills: list[tuple[int, signal.Signals]] = []
    checkpoints = 0

    def checkpoint() -> None:
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints == 4:
            raise Cancelled()

    def spawn(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = cast("subprocess.Popen[bytes]", real_popen(*args, **kwargs))
        processes.append(process)
        return process

    def killpg(pid: int, requested_signal: signal.Signals) -> None:
        kills.append((pid, requested_signal))
        real_killpg(pid, requested_signal)

    monkeypatch.setattr(linkedin_execution.subprocess, "Popen", spawn)
    monkeypatch.setattr(exa_execution.os, "killpg", killpg)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(Cancelled):
        execute(
            _request("search.people", query=_QUERY_CANARY),
            _artifact_context(artifacts, checkpoint=checkpoint),
        )

    assert checkpoints == 4
    assert processes
    assert all(process.poll() is not None for process in processes)
    assert kills
    assert all(requested_signal is signal.SIGKILL for _, requested_signal in kills)
    assert {pid for pid, _ in kills}.issubset({process.pid for process in processes})
    assert not any(path.name.startswith(".agent-reach-linkedin-") for path in tmp_path.iterdir())
