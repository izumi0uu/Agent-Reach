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
    McporterArtifactsV1,
    NetworkAccessV1,
    PrivateWorkspaceV1,
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

The v1 registry contains fourteen operations:

- `rss:read.feed` and `rss:browse.entries`
- `bilibili:search.videos`, `bilibili:read.video`, `bilibili:browse.hot`, and
  `bilibili:browse.rank`
- `youtube:read.video`, `youtube:search.videos`, and
  `youtube:read.subtitles`
- `v2ex:browse.hot`, `v2ex:browse.node_topics`, `v2ex:read.topic`, and
  `v2ex:read.user`
- `exa:search.web`

Requests have closed operation-specific arguments. They cannot choose a
backend, command, executable, argv, transport, endpoint, MCP method,
credential, browser profile, Cookie, output path, plugin, remote component, or
fallback. In particular, Exa code search is not registered because the
documented `tokensNum` contract does not match the live deprecated method's
`numResults` schema.

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

All three YouTube operations require the data-free network marker and the exact
host closure `yt-dlp==2026.7.4`, `yt-dlp-ejs==0.8.0`, and `deno==2.8.3`.
Deno must be the regular, executable, single-link binary next to the active
Python interpreter. Video and subtitle URLs must already be the literal
canonical `https://www.youtube.com/watch?v=<11-character-id>` form; encoded
IDs, alternate hosts, extra query fields, fragments, and user information are
rejected. The executor disables plugins, configuration and cache use, proxies,
Cookies, browser state, netrc, credentials, playlists, and remote components.
It invokes only the operation's fixed `YoutubeDL` call and returns closed
identity-correlated items.

Subtitle execution additionally requires the fieldless
`PrivateWorkspaceV1`. The host must set the process current directory to a new
private workspace for the attempt. The marker carries no path authority. Only
a bounded regular, single-link VTT file resolved beneath that workspace is
accepted; the executor checks its inode, reads it with byte and text limits,
and removes it on every terminal path. Manual subtitles win over automatic
subtitles for the selected language.

YouTube text is whitespace-normalized before the existing source projections:
title and author are bounded to 1 KiB of UTF-8 and description to 64 KiB. These
legacy field projections are silent. The result `text` is then bounded by the
execution context to at most 16,000 Unicode code points; only clipping at this
context boundary sets `ExecutionSuccessV1.truncated`. The complete typed item
and encoded result remain subject to the closed schema and output-byte limits.

V2EX operations require only `NetworkAccessV1`. The runtime fixes the public
origin to `www.v2ex.com:443`, validates every DNS answer as globally routable,
and connects to one pinned IP while retaining the original HTTP Host and TLS
SNI. It uses exact `httpcore==1.0.9`, no proxy, redirect, compression, or
caller-selected path. Bounded strict JSON is validated before projection.
Topic, reply, node, and user identities are correlated with the request and
canonical URLs are rebuilt from those identities. Once `read.topic` has a
valid topic, a reply failure returns only that topic with a closed partial error
instead of retrying or exposing raw provider data.

Exa Web search requires `NetworkAccessV1` plus one
`McporterArtifactsV1`. The artifact capability contains only operator-attested
absolute Node, mcporter, and sterile-config paths and their reviewed digests;
it contains no query, credential, endpoint, method, argv, or generic process
authority. The runtime revalidates the complete artifact closure immediately
before invoking one fixed mcporter command for `web_search_exa` at
`https://mcp.exa.ai/mcp`. The query is sent only as bounded canonical JSON on
stdin. The child receives a sterile environment, bounded concurrent pipes,
and process-group kill-and-reap cleanup. Exa receives the query and may retain
it; hosts must not describe this route as provider-private or no-query-log.

Success and failure are immutable discriminated variants. A success identifies
the selected backend and version. A failure contains only protocol,
source-operation correlation, optional selected-backend identity, and a closed
error code. It contains no exception text, headers, path, raw body, provider
message, or remediation. Host cancellation raised by a checkpoint propagates
to the host instead of being converted into a backend result.

## Discovery and compatibility

`list_capabilities()` is static. It does not import `feedparser`, `bili_cli`,
`yt_dlp`, `yt_dlp_ejs`, `deno`, `httpcore`, or an MCP client; inspect
configuration or artifacts; read credentials; access the network or
filesystem; or start a process. Hosts should validate the exact protocol,
descriptors, schemas, limits, backend identity, dependency commit, and
installed backend version before enabling an operation. A newly published
capability is not authority for a host to enable it automatically.

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
