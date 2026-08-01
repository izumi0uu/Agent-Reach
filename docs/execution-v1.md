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
    OpenCliSessionV1,
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

social_session = OpenCliSessionV1(
    node_executable=operator_selected_node,
    node_sha256=attested_node_sha256,
    opencli_root=dedicated_opencli_root,
    opencli_cli=dedicated_opencli_entrypoint,
    opencli_tree_sha256=attested_opencli_tree_sha256,
    session_home=trusted_session_home,
)
reddit_request = ExecutionRequestV1(
    PROTOCOL_VERSION,
    "reddit",
    "search.posts",
    {"query": "agent runtime", "limit": 5},
)
reddit_result = execute(
    reddit_request,
    ExecutionContextV1((social_session,)),
)
```

The v1 registry contains twenty-nine operations:

- `rss:read.feed` and `rss:browse.entries`
- `bilibili:search.videos`, `bilibili:read.video`, `bilibili:browse.hot`, and
  `bilibili:browse.rank`
- `youtube:read.video`, `youtube:search.videos`, and
  `youtube:read.subtitles`
- `v2ex:browse.hot`, `v2ex:browse.node_topics`, `v2ex:read.topic`, and
  `v2ex:read.user`
- `exa:search.web`
- `reddit:search.posts`, `reddit:read.post`, `reddit:browse.subreddit`,
  `reddit:browse.hot`, `reddit:browse.popular`, `reddit:browse.all`, and
  `reddit:read.subreddit`
- `facebook:search`, `facebook:read.profile`, `facebook:browse.feed`, and
  `facebook:browse.groups`
- `instagram:search.users`, `instagram:read.profile`,
  `instagram:browse.user_posts`, and `instagram:browse.explore`

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

### OpenCLI social execution

The fifteen Reddit, Facebook, and Instagram operations require exactly one
`OpenCliSessionV1` and use only `@jackwener/opencli@1.8.6-hermes.1`. The descriptor,
not the request or host, selects the command. Every command also appends the
fixed `--format yaml` output selector:

| Operation | Fixed OpenCLI arguments before `--format yaml` |
| --- | --- |
| `reddit:search.posts` | `reddit search QUERY --limit N` |
| `reddit:read.post` | `reddit read POST_ID --sort best --limit 3 --depth 2 --replies 2 --max-length 800` |
| `reddit:browse.subreddit` | `reddit subreddit NAME --sort hot --time all --limit N` |
| `reddit:browse.hot` | `reddit hot --limit N` |
| `reddit:browse.popular` | `reddit popular --limit N` |
| `reddit:browse.all` | `reddit frontpage --limit N` |
| `reddit:read.subreddit` | `reddit subreddit-info NAME` |
| `facebook:search` | `facebook search QUERY --limit N` |
| `facebook:read.profile` | `facebook profile USERNAME` |
| `facebook:browse.feed` | `facebook feed --limit N` |
| `facebook:browse.groups` | `facebook groups --limit N` |
| `instagram:search.users` | `instagram search QUERY --limit N` |
| `instagram:read.profile` | `instagram profile USERNAME` |
| `instagram:browse.user_posts` | `instagram user USERNAME --limit N` |
| `instagram:browse.explore` | `instagram explore --limit N` |

`OpenCliSessionV1` is an immutable host authority, not request data. It binds
an absolute Node executable and SHA-256, an absolute dedicated npm install
prefix and canonical entrypoint, the approved full prefix-tree SHA-256, and an
absolute trusted session home. The fixed entrypoint is
`node_modules/@jackwener/opencli/dist/src/main.js`; the attested prefix includes
OpenCLI and every production dependency that Node can resolve. Installations
with symlinks, hard-linked files, or group/other-writable entries are rejected.

The reviewed package is built from official base
`399c0de2a76eb979aee3a3836cf2d24fd247780f`, owner-fork commit
`9b0ec22faeff186d53836c14f39cbf5cdddfca55`, and Git tree
`fc3e59294a5b06e7e236fb21c8c4a80b7749ed50`. Its tarball SHA-256 is
`eebe99d2e848927edaa8b10d6edbfaec088a9b4bdd06436be7556601fb1be2a4`
and SHA-512 is
`hhVlYQ9LUtxoP1Y7IfnZxTjOxfGf0IbdJrHYpBmtxfwophmlAJ7xXPFYax+FFL5ZmBCfSppPfZ061aL1Cb2shg==`.
The Git tree identifies reviewed source; it is not the 64-character installed
prefix digest carried by `OpenCliSessionV1`.

Build the dedicated prefix from that reviewed tarball with lifecycle scripts
and npm bin links disabled, for example with
`npm install --ignore-scripts --no-bin-links --omit=dev`. Do not point this
capability at a general global npm tree: unrelated packages and mutable bin
links would become part of the execution authority.

Before every attempt the runtime copies Node, the complete npm prefix, and the
fixed lifecycle guard into a new private directory. It validates the copied
bytes, path ownership and permissions, fixed entrypoint, tree digest, and exact
package name/version/bin contract, then executes only the private copies. A
source-path replacement after validation cannot change the launched Node,
entrypoint, dependency, or guard. Runtime authority comes from the current
operator-approved installed-prefix digest, not a registry lookup or PATH
discovery, and callers cannot select another executable.

Each attempt receives a new private `HOME`, `USERPROFILE`, `XDG_CONFIG_HOME`,
temporary directory, and working directory. Only
`OPENCLI_CONFIG_DIR=<session_home>/.opencli` points back to the operator-approved
session so OpenCLI can reach its existing local browser bridge configuration.
The trusted session home is not used as the child HOME; this prevents ambient
user OpenCLI adapters and plugins under the normal HOME from replacing the
reviewed built-in social commands. PATH, proxy, credential, Cookie, and other
ambient environment variables are not inherited. The session directory is
owner-private and canonicalized, but its live browser/daemon state is
deliberately not included in the install-tree digest. It remains a mutable,
trusted-device capability and may fail closed when disconnected, logged out,
challenged, or incompatible.

This capability grants use of an already configured browser session; it does
not log in, export cookies, copy a Chrome profile, select a browser/profile, or
grant arbitrary browser navigation. The Node/OpenCLI process still runs with
the local user's filesystem authority, so the private environment is a
containment boundary, not a kernel sandbox. Hosts should keep the session on a
trusted device, narrow authorization per operation, and never send capability
paths or session material to an untrusted remote host.

The runtime starts Node directly without a shell, closes stdin, bounds stdout
and stderr concurrently, and kills and reaps the process group on deadline,
cancellation, output overflow, or exchange failure. Success YAML rejects
duplicate keys, aliases, custom tags, unexpected columns, non-scalar values,
and depth/node/byte overflow before source-specific projection. Failures use
only an allowlisted OpenCLI error code; provider messages, queries, usernames,
post IDs, paths, session details, and raw output are never copied into an
execution failure.

Success and failure are immutable discriminated variants. A success identifies
the selected backend and version. A failure contains only protocol,
source-operation correlation, optional selected-backend identity, and a closed
error code. It contains no exception text, headers, path, raw body, provider
message, or remediation. Host cancellation raised by a checkpoint propagates
to the host instead of being converted into a backend result.

## Discovery and compatibility

`list_capabilities()` is static. It does not import `feedparser`, `bili_cli`,
`yt_dlp`, `yt_dlp_ejs`, `deno`, `httpcore`, or an MCP client; inspect
configuration or artifacts; load the OpenCLI social runtime; read credentials;
access the network or filesystem; or start a process. Hosts should validate
the exact protocol, descriptors, schemas, limits, backend identity, dependency
commit, and installed backend version before enabling an operation. A newly
published capability is not authority for a host to enable it automatically.

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
