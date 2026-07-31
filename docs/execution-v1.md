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
    NetworkAccessV1,
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

bilibili_request = ExecutionRequestV1(
    PROTOCOL_VERSION,
    "bilibili",
    "search.videos",
    {"query": "agent runtime", "limit": 5},
)
bilibili_result = execute(
    bilibili_request,
    ExecutionContextV1((NetworkAccessV1(),)),
)

youtube_request = ExecutionRequestV1(
    PROTOCOL_VERSION,
    "youtube",
    "read.video",
    {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
)
youtube_result = execute(
    youtube_request,
    ExecutionContextV1((NetworkAccessV1(),)),
)
```

The v1 registry contains seven operations: `rss:read.feed`,
`rss:browse.entries`, `bilibili:search.videos`, `bilibili:read.video`,
`bilibili:browse.hot`, `bilibili:browse.rank`, and
`youtube:read.video`. Requests have closed operation-specific arguments. They
cannot choose a backend, command, executable, argv, transport, endpoint,
credential, browser profile, Cookie, output path, plugin, remote component, or
fallback.

The host must fetch and validate the document before execution. RSS receives
one non-empty `FetchedDocumentV1` of at most 1 MiB. Its public HTTP(S)
`content_location` has no userinfo, query, fragment, or non-default port. The
executor never opens that location: it passes only `BytesIO(body)` to exact
`feedparser==6.0.12` and returns schema-tagged bounded fields.

Bilibili execution requires one data-free `NetworkAccessV1` and a host-installed
`bilibili-cli==0.6.2`. The executor maps each registered operation to fixed
Click arguments, validates the exact console entry point and bounded JSON
envelope, and returns only the closed `bilibili.video.v1` projection. The marker
is explicit host authority, not an OS sandbox: the host remains responsible for
private HOME/XDG/TMP state, proxy and credential isolation, hard timeout and
process cleanup, output framing, and independent result validation.
Because Click output capture temporarily replaces process-global stdout, the
executor permits only one active Bilibili backend invocation per process. A
call that overlaps that invocation fails closed as `transient`; calls may
otherwise validate and project results concurrently. Hosts must still prevent
unrelated concurrent stdout writers, so a dedicated one-request worker remains
the recommended containment boundary.

YouTube read-video execution requires the same data-free network marker and the
exact host closure `yt-dlp==2026.7.4`, `yt-dlp-ejs==0.8.0`, and
`deno==2.8.3`. Deno must be the regular, executable, single-link binary next
to the active Python interpreter. The URL must already be the literal canonical
`https://www.youtube.com/watch?v=<11-character-id>` form; encoded IDs, alternate
hosts, extra query fields, fragments, and user information are rejected. The
executor disables plugins, configuration and cache use, proxies, Cookies,
browser state, netrc, credentials, downloads, postprocessors, playlists, and
remote components. It invokes only
`YoutubeDL(params).extract_info(canonical_url, download=False,
ie_key="Youtube")` and returns one identity-correlated
`youtube.video.v1` item. The host remains responsible for a private process
environment, a hard timeout and cancellation boundary, process-group cleanup,
bounded framing, and independent result validation.

YouTube text is whitespace-normalized before the existing source projections:
title and author are bounded to 1 KiB of UTF-8 and description to 64 KiB. These
legacy field projections are silent. The result `text` is then bounded by the
execution context to at most 16,000 Unicode code points; only clipping at this
context boundary sets `ExecutionSuccessV1.truncated`. The complete typed item
and encoded result remain subject to the closed schema and output-byte limits.

Success and failure are immutable discriminated variants. A success identifies
the selected backend and version. A failure contains only protocol,
source-operation correlation, optional selected-backend identity, and a closed
error code. It contains no exception text, headers, path, raw body, provider
message, or remediation. Host cancellation raised by a checkpoint propagates
to the host instead of being converted into a backend result.

## Discovery and compatibility

`list_capabilities()` is static. It does not import `feedparser`, `bili_cli`,
`yt_dlp`, `yt_dlp_ejs`, or `deno`,
inspect configuration, read credentials, access the network or filesystem, or
start a process. Hosts should validate the exact protocol, descriptors,
schemas, limits, backend identity, dependency commit, and installed backend
version before enabling an operation. A newly published capability is not
authority for a host to enable it automatically.

## Fork update discipline

The fork's `main` remains a fast-forward mirror of the official repository.
Execution changes live on a separate branch that is rebased onto official
`main`; official history is never merged into that branch. After each rebase,
run the complete Agent Reach suite and every consuming host's compatibility
audit. Preserve each consumed revision with an immutable integration tag and
pin consumers to the exact reviewed commit. Only the rebased development
branch may be updated with force-with-lease.

The PEP 517 build backend is also an exact project pin. Keep that pin reviewed
and run the clean-wheel gate after every update so installing the same VCS
commit cannot silently select a different Hatchling release later.
