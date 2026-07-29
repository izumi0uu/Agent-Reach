# Structured execution API v1

`agent_reach.execution.v1` is an additive library boundary for hosts that own
their own authorization, network and process containment, normalization,
audit, and rollback controls. It does not replace the existing CLI, doctor,
installer, skill, or channel registry.

## API

```python
from agent_reach.execution.v1 import (
    PROTOCOL_VERSION,
    ExecutionContextV1,
    ExecutionRequestV1,
    FetchedDocumentV1,
    execute,
    list_capabilities,
)

capabilities = list_capabilities()  # static: no backend import or I/O
request = ExecutionRequestV1(PROTOCOL_VERSION, "rss", "read.feed")
context = ExecutionContextV1(
    (
        FetchedDocumentV1(
            body=bounded_feed_bytes,
            content_type="application/rss+xml",
            content_location="https://example.com/feed.xml",
        ),
    )
)
result = execute(request, context)
```

The v1 registry contains only `rss:read.feed` and `rss:browse.entries`.
Requests have closed operation-specific arguments. They cannot choose a
backend, command, executable, argv, transport, endpoint, credential, browser
profile, Cookie, or fallback.

The host must fetch and validate the document before execution. RSS receives
one non-empty `FetchedDocumentV1` of at most 1 MiB. Its public HTTP(S)
`content_location` has no userinfo, query, fragment, or non-default port. The
executor never opens that location: it passes only `BytesIO(body)` to exact
`feedparser==6.0.12` and returns schema-tagged bounded fields.

Success and failure are immutable discriminated variants. A success identifies
the selected backend and version. A failure contains only protocol,
source-operation correlation, optional selected-backend identity, and a closed
error code. It contains no exception text, headers, path, raw body, provider
message, or remediation. Host cancellation raised by a checkpoint propagates
to the host instead of being converted into a backend result.

## Discovery and compatibility

`list_capabilities()` is static. It does not import `feedparser`, inspect
configuration, read credentials, access the network or filesystem, or start a
process. Hosts should validate the exact protocol, descriptors, schemas,
limits, backend identity, dependency commit, and installed backend version
before enabling an operation. A newly published capability is not authority
for a host to enable it automatically.

## Fork update discipline

The fork's `main` remains a fast-forward mirror of the official repository.
Execution changes live on a separate branch that is rebased onto official
`main`; official history is never merged into that branch. After each rebase,
run the complete Agent Reach suite and every consuming host's compatibility
audit. Preserve each consumed revision with an immutable integration tag and
pin consumers to the exact reviewed commit. Only the rebased development
branch may be updated with force-with-lease.
