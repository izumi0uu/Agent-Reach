"""Fork-owned LinkedIn searches through one fixed loopback MCP service."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Final, cast
from urllib.parse import quote_plus

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
    LinkedInMcpV1,
    McporterArtifactsV1,
    _contains_invalid_scalar,
    _linkedin_reference_fields,
    _valid_public_result_url,
)
from .exa import (
    _ArtifactIncompatibleError,
    _ArtifactUnavailableError,
    _BackendContractError,
    _BackendDeadlineError,
    _BackendTransientError,
    _exchange_process,
    _kill_and_reap,
    _load_json,
    _private_process_environment,
    _validate_artifacts,
)

_BACKEND_ID: Final = "linkedin-scraper-mcp"
_BACKEND_VERSION: Final = "4.14.0"
_ENDPOINT: Final = "http://127.0.0.1:8001/mcp"
_PEOPLE_CONFIG: Final = (
    b'{"imports":[],"mcpServers":{"linkedin":{"allowedTools":["search_people"],'
    b'"baseUrl":"http://127.0.0.1:8001/mcp"}}}'
)
_JOBS_CONFIG: Final = (
    b'{"imports":[],"mcpServers":{"linkedin":{"allowedTools":["search_jobs"],'
    b'"baseUrl":"http://127.0.0.1:8001/mcp"}}}'
)
_MAX_QUERY_CHARACTERS: Final = 4_096
_MAX_NATIVE_DOCUMENT_BYTES: Final = 64 * 1_024
_MAX_REFERENCES: Final = 15
_MAX_JOB_IDS: Final = 50


class _LinkedInDataError(Exception):
    pass


class _LinkedInUnavailableError(_LinkedInDataError):
    pass


class _CheckpointRaised(BaseException):
    def __init__(self, original: BaseException) -> None:
        self.original = original


def execute_linkedin(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
) -> ExecutionResultV1:
    """Execute one registry-validated LinkedIn search document request."""

    capabilities = _capabilities_from_context(context)
    if not _valid_request(request) or capabilities is None:
        return _failure(request, "backend_contract_violation")
    artifacts, _service = capabilities

    try:
        _checkpoint(context)
        raw = _invoke_mcporter(request, context, artifacts)
        item, truncated = _project_document(raw, request, context)
        _checkpoint(context)
        return ExecutionSuccessV1(
            protocol_version=PROTOCOL_VERSION,
            source="linkedin",
            operation=request.operation,
            backend_id=_BACKEND_ID,
            backend_version=_BACKEND_VERSION,
            items=(item,),
            truncated=truncated,
            partial_error_code=None,
        )
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
    except _LinkedInUnavailableError:
        return _failure(request, "backend_unavailable")
    except (_BackendContractError, _LinkedInDataError, TypeError, ValueError):
        return _failure(request, "backend_contract_violation")
    except Exception:
        return _failure(request, "transient")


def _valid_request(request: ExecutionRequestV1) -> bool:
    if (
        type(request) is not ExecutionRequestV1
        or request.protocol_version != PROTOCOL_VERSION
        or request.source != "linkedin"
        or request.operation not in {"search.people", "search.jobs"}
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


def _capabilities_from_context(
    context: ExecutionContextV1,
) -> tuple[McporterArtifactsV1, LinkedInMcpV1] | None:
    if type(context) is not ExecutionContextV1 or len(context.host_capabilities) != 2:
        return None
    artifacts, service = context.host_capabilities
    if type(artifacts) is not McporterArtifactsV1 or type(service) is not LinkedInMcpV1:
        return None
    return artifacts, service


def _invoke_mcporter(
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
    artifacts: McporterArtifactsV1,
) -> object:
    payload = bytearray(
        json.dumps(
            _backend_arguments(request),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    )
    try:
        try:
            temporary = tempfile.TemporaryDirectory(
                prefix=".agent-reach-linkedin-",
                dir=str(Path.cwd()),
            )
            try:
                private_root = Path(temporary.name).resolve(strict=True)
                environment, home = _private_process_environment(private_root)
                node, cli, config = _validate_artifacts(
                    artifacts,
                    expected_config=_mcporter_config(request.operation),
                )
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
                    return _mcp_document(stdout)
                except (_BackendContractError, TypeError, ValueError):
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


def _backend_arguments(request: ExecutionRequestV1) -> dict[str, object]:
    query = cast(str, request.arguments["query"])
    if request.operation == "search.people":
        return {"keywords": query}
    if request.operation == "search.jobs":
        return {"keywords": query, "max_pages": 1}
    raise _BackendContractError("request invalid")


def _mcporter_config(operation: str) -> bytes:
    if operation == "search.people":
        return _PEOPLE_CONFIG
    if operation == "search.jobs":
        return _JOBS_CONFIG
    raise _BackendContractError("request invalid")


def _mcporter_argv(
    node: Path,
    cli: Path,
    config: Path,
    *,
    operation: str,
) -> tuple[str, ...]:
    if operation == "search.people":
        tool = "search_people"
    elif operation == "search.jobs":
        tool = "search_jobs"
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
        "--server",
        "linkedin",
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


def _mcp_document(raw: bytes) -> object:
    # mcporter JSON output selects CallResult.structuredContent and prints the
    # plain document. The outer MCP content envelope is not present here.
    document = _load_json(raw)
    if not isinstance(document, Mapping):
        raise _BackendContractError("backend document invalid")
    return document


def _project_document(
    value: object,
    request: ExecutionRequestV1,
    context: ExecutionContextV1,
) -> tuple[ExecutionItemV1, bool]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise _LinkedInDataError("document invalid")
    document = cast(Mapping[str, object], value)
    required_fields = {"url", "sections"}
    schema_id = "linkedin.people.search.document.v1"
    if request.operation == "search.jobs":
        required_fields.add("job_ids")
        schema_id = "linkedin.jobs.search.document.v1"
    if not required_fields.issubset(document) or not set(document).issubset(
        required_fields | {"references", "section_errors"}
    ):
        raise _LinkedInDataError("document invalid")
    if "section_errors" in document:
        _validate_section_errors(document["section_errors"])
        raise _LinkedInUnavailableError("search document unavailable")
    url = _validated_search_url(document["url"], request)
    sections, text_truncated = _project_sections(document["sections"], context)
    limit = cast(int, request.arguments["limit"])
    references, references_truncated = _project_references(
        document.get("references"),
        limit=limit,
    )
    fields: dict[str, str | int | None] = {
        "url": url,
        "sections": sections,
        "references": references,
    }
    job_ids_truncated = False
    if request.operation == "search.jobs":
        fields["job_ids"], job_ids_truncated = _project_job_ids(
            document["job_ids"],
            limit=limit,
        )
    return (
        ExecutionItemV1(schema_id, fields),
        text_truncated or references_truncated or job_ids_truncated,
    )


def _validated_search_url(value: object, request: ExecutionRequestV1) -> str:
    if type(value) is not str or not _valid_public_result_url(value):
        raise _LinkedInDataError("search URL invalid")
    query = quote_plus(cast(str, request.arguments["query"]))
    expected = (
        f"https://www.linkedin.com/search/results/people/?keywords={query}"
        if request.operation == "search.people"
        else f"https://www.linkedin.com/jobs/search/?keywords={query}"
    )
    if value != expected:
        raise _LinkedInDataError("search URL invalid")
    return value


def _project_sections(
    value: object,
    context: ExecutionContextV1,
) -> tuple[str, bool]:
    if not isinstance(value, Mapping):
        raise _LinkedInDataError("sections invalid")
    if not value:
        return "{}", False
    if set(value) != {"search_results"}:
        raise _LinkedInDataError("sections invalid")
    text = value["search_results"]
    if (
        type(text) is not str
        or not text
        or _contains_invalid_scalar(text)
        or len(text.encode("utf-8", errors="strict")) > _MAX_NATIVE_DOCUMENT_BYTES
    ):
        raise _LinkedInDataError("section invalid")
    maximum = min(context.limits.maximum_text_characters, MAX_TEXT_CHARACTERS)
    selected = text[:maximum]
    encoded = _encode_canonical_json({"search_results": selected})
    if len(encoded) > MAX_TEXT_CHARACTERS:
        lower = 0
        upper = len(selected)
        while lower < upper:
            midpoint = (lower + upper + 1) // 2
            candidate = _encode_canonical_json({"search_results": selected[:midpoint]})
            if len(candidate) <= MAX_TEXT_CHARACTERS:
                lower = midpoint
            else:
                upper = midpoint - 1
        selected = selected[:lower]
        encoded = _encode_canonical_json({"search_results": selected})
    return encoded, len(selected) < len(text)


def _project_references(value: object, *, limit: int) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    if not isinstance(value, Mapping) or set(value) != {"search_results"}:
        raise _LinkedInDataError("references invalid")
    raw_references = value["search_results"]
    if not isinstance(raw_references, list) or len(raw_references) > _MAX_REFERENCES:
        raise _LinkedInDataError("references invalid")
    validated: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for item in raw_references:
        reference = _linkedin_reference_fields(item)
        if reference is None or reference["url"] in seen_urls:
            raise _LinkedInDataError("reference invalid")
        seen_urls.add(reference["url"])
        validated.append(reference)
    selected: list[dict[str, str]] = []
    truncated = len(validated) > limit
    for reference in validated[:limit]:
        candidate = [*selected, reference]
        if len(_encode_canonical_json({"search_results": candidate})) > MAX_TEXT_CHARACTERS:
            truncated = True
            break
        selected.append(reference)
    return _canonical_json({"search_results": selected}), truncated


def _project_job_ids(value: object, *, limit: int) -> tuple[str, bool]:
    if not isinstance(value, list) or len(value) > _MAX_JOB_IDS:
        raise _LinkedInDataError("job IDs invalid")
    selected: list[str] = []
    seen: set[str] = set()
    for job_id in value:
        if (
            type(job_id) is not str
            or not job_id.isascii()
            or not job_id.isdigit()
            or not 1 <= len(job_id) <= 32
            or job_id.startswith("0")
            or job_id in seen
        ):
            raise _LinkedInDataError("job ID invalid")
        seen.add(job_id)
        selected.append(job_id)
    return _canonical_json(selected[:limit]), len(selected) > limit


def _validate_section_errors(value: object) -> None:
    if (
        type(value) is not dict
        or set(value) != {"search_results"}
        or type(value["search_results"]) is not dict
    ):
        raise _LinkedInDataError("section errors invalid")


def _canonical_json(value: object) -> str:
    encoded = _encode_canonical_json(value)
    if len(encoded) > MAX_TEXT_CHARACTERS or _contains_invalid_scalar(encoded):
        raise _LinkedInDataError("projected document invalid")
    return encoded


def _encode_canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _checkpoint(context: ExecutionContextV1) -> None:
    try:
        context.checkpoint()
    except BaseException as error:
        raise _CheckpointRaised(error) from None


def _failure(
    request: ExecutionRequestV1,
    error_code: ExecutionErrorCodeV1,
) -> ExecutionFailureV1:
    return ExecutionFailureV1(
        protocol_version=PROTOCOL_VERSION,
        source="linkedin",
        operation=request.operation,
        backend_id=_BACKEND_ID,
        backend_version=_BACKEND_VERSION,
        error_code=error_code,
    )
