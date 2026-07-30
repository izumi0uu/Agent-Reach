"""Agent Reach's additive structured execution protocol v1."""

from .contracts import (
    EXECUTION_ERROR_CODES,
    FETCHED_DOCUMENT_CAPABILITY,
    NETWORK_ACCESS_CAPABILITY,
    PROTOCOL_VERSION,
    ExecutionContextV1,
    ExecutionErrorCodeV1,
    ExecutionFailureV1,
    ExecutionItemV1,
    ExecutionLimitsV1,
    ExecutionRequestV1,
    ExecutionResultV1,
    ExecutionSuccessV1,
    FetchedDocumentV1,
    NetworkAccessV1,
    OperationCapabilityV1,
)
from .registry import execute, list_capabilities

__all__ = [
    "EXECUTION_ERROR_CODES",
    "FETCHED_DOCUMENT_CAPABILITY",
    "NETWORK_ACCESS_CAPABILITY",
    "PROTOCOL_VERSION",
    "ExecutionContextV1",
    "ExecutionErrorCodeV1",
    "ExecutionFailureV1",
    "ExecutionItemV1",
    "ExecutionLimitsV1",
    "ExecutionRequestV1",
    "ExecutionResultV1",
    "ExecutionSuccessV1",
    "FetchedDocumentV1",
    "NetworkAccessV1",
    "OperationCapabilityV1",
    "execute",
    "list_capabilities",
]
