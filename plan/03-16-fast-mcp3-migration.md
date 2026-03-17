# 03-16 FastMCP 3 migration plan

## Status

Prepared after:
- installing `fastmcp==3.1.1` into the local repo venv for inspection
- cloning `PrefectHQ/fastmcp` at `v3.1.1` under `/tmp/fastmcp-src`
- reviewing these upstream docs/source areas:
  - `docs/getting-started/upgrading/from-mcp-sdk.mdx`
  - `docs/deployment/running-server.mdx`
  - `docs/servers/server.mdx`
  - `docs/servers/prompts.mdx`
  - `docs/servers/tools.mdx`
  - `docs/servers/auth/remote-oauth.mdx`
  - `src/fastmcp/server/server.py`
  - `src/fastmcp/prompts/function_prompt.py`
  - `src/fastmcp/tools/function_tool.py`

Updated after implementation progress:
- server-side SSE exposure removed from `fast-agent serve`, legacy `FastAgent` server args,
  `AgentMCPServer`, and `prompt-server`
- server-side SSE tests/examples/docs were pruned or converted to HTTP
- client-side SSE handling remains intentionally unchanged

## Decision summary

### 0. This is a clean migration, not a compatibility layer

Per project direction, this work is a **clean break** to FastMCP 3 patterns.

That means:
- no fast-agent-owned server code should continue importing `mcp.server.fastmcp`
- no deprecated fast-agent server transports or CLI affordances should be kept for transition
- no new shims should be introduced to preserve old FastMCP 1.x/SDK execution contracts
- internal abstractions should be rewritten around FastMCP 3 public concepts:
  - `FastMCP`
  - `FunctionTool`
  - `ToolResult`
  - `Prompt`
  - `Message`
  - `AuthProvider` / `TokenVerifier` / `RemoteAuthProvider`

If a local helper remains after migration, it should be a thin convenience wrapper over
public FastMCP 3 APIs, not a compatibility facade for old semantics.

### 1. Scope: migrate fast-agent's **server surface** to FastMCP 3

This plan is about fast-agent **providing** MCP server capabilities:
- `fast-agent serve`
- `fast-agent go --transport ...` server mode paths
- `prompt-server`
- bundled/server examples and server test fixtures

It does **not** include unrelated non-MCP SSE references elsewhere in the repo
(for example provider/model transport wording). It also does **not** remove
client-side SSE support for connecting to third-party MCP servers.

### 2. First task: remove fast-agent's own SSE server transport

Per project direction and FastMCP guidance, fast-agent's own exposed SSE server
transport should be removed before the FastMCP 3 refactor lands.

After that first task, the remaining exposed server transports in fast-agent will be:
- `http`
- `stdio`
- `acp` (separate path, not FastMCP)

We will remove SSE from:
- `fast-agent serve`
- `FastAgent` server-mode arg parsing/help
- `AgentMCPServer`
- `prompt-server`
- server-facing examples, resources, and tests that only exist to exercise SSE

This is a major simplifier because it lets us delete the current custom SSE
shutdown/connection-tracking code instead of porting any of it.

### 3. Follow FastMCP 3 best practice: use `run(..., transport="http")` and `http_app()`

Upstream guidance is clear:
- HTTP is the recommended network transport
- SSE is legacy-only
- transport config belongs on `run()` / `run_http_async()` / `http_app()`
- server definitions should not store deployment settings on the `FastMCP` instance

So the target shape is:
- server definition via `FastMCP(...)`
- runtime/deployment via:
  - `mcp.run(transport="http", host=..., port=...)`
  - `await mcp.run_http_async(...)`
  - `mcp.http_app(...)` when embedding in an ASGI app

### 4. Prefer public FastMCP 3 APIs only

We should stop depending on old/private MCP SDK internals for the server surface:
- no `mcp_server.settings`
- no `_sse_transport`
- no `_prompt_manager`
- no direct reliance on old tool internals like `fn_metadata` / `context_kwarg`
- no fast-agent-owned server/runtime imports from `mcp.server.fastmcp.*`

Corollary for the broader migration:
- prefer `fastmcp.tools.FunctionTool`, `fastmcp.tools.ToolResult`,
  `fastmcp.prompts.Prompt`, and `fastmcp.prompts.Message`
- move fast-agent internals to the FastMCP 3 public execution/result model instead of
  preserving the old local wrapper API

### 5. Structured-output suppression becomes a policy cleanup, not a transport blocker

The old code explicitly suppressed structured outputs on some FastMCP v1 tools because the old surface was too eager.

For FastMCP 3:
- plain text content is always present
- structured content behavior is better defined
- for server-facing send tools, we can likely remove the old suppression plumbing and rely on default behavior unless a concrete test/client regression says otherwise

Important nuance from upstream docs:
- object-like results (`dict`, Pydantic models, dataclasses) still auto-populate structured content
- primitive returns can also produce structured content when an output schema exists

So the implementation choice for the exposed send tools is:
- **default**: remove the old suppression switch and accept FastMCP 3 defaults
- **fallback**: if a regression appears, suppress by design (e.g. explicit `output_schema=None` or unannotated wrapper), but only where needed

That is a smaller concern than the transport/runtime migration.

## Current-state findings in fast-agent

## A. Core server runtime is tightly coupled to MCP SDK FastMCP 1.x

Primary file:
- `src/fast_agent/mcp/server/agent_server.py`

Main incompatibilities:
- imports `FastMCP` and `Context` from `mcp.server.fastmcp`
- passes `host=` to `FastMCP(...)`
- uses old auth wiring (`token_verifier=...`, `AuthSettings`)
- mutates `self.mcp_server.settings.*`
- uses removed methods:
  - `run_sse_async()`
  - `run_streamable_http_async()`
  - `streamable_http_app()`
- uses removed/private SSE internals for shutdown
- prompt function returns raw `{"role": ..., "content": ...}` dicts
- uses old `structured_output=False` decorator kwarg

## B. Prompt server uses old prompt internals

Primary files:
- `src/fast_agent/mcp/prompts/prompt_server.py`
- `src/fast_agent/mcp/prompts/prompt_load.py`

Main incompatibilities:
- imports from `mcp.server.fastmcp.prompts.base`
- uses removed `UserMessage` / `AssistantMessage`
- builds prompt objects with `Prompt(..., fn=...)`
- writes directly to `_prompt_manager`
- defines dynamic prompt handlers using `**kwargs`
  - FastMCP 3 explicitly rejects prompt functions with `**kwargs`
- still exposes SSE mode

## C. Local tool abstraction still assumes old FastMCP tool internals

Primary files:
- `src/fast_agent/tools/function_tool_loader.py`
- `src/fast_agent/agents/tool_agent.py`
- `src/fast_agent/tools/elicitation.py`

Main incompatibilities:
- imports old tool classes/exceptions
- local wrapper uses removed internal attributes
- local wrapper preserves old `run(arguments, context=..., convert_result=...)` API
- `ToolAgent.call_tool()` expects raw values from `.run(...)` instead of native `ToolResult`

Good news: FastMCP 3 already runs sync functions in a thread pool, so the main reason for the custom `FastMCPTool` override mostly disappears.

## D. Auth helper needs a small redesign

Primary file:
- `src/fast_agent/mcp/auth/presence.py`

Current code subclasses old MCP SDK `TokenVerifier`. We need the FastMCP 3 equivalent.

This should be manageable. The main design question is:
- plain `TokenVerifier` only, or
- `RemoteAuthProvider` wrapped around a verifier when we want protected-resource metadata discovery

Given the current server use is lightweight bearer-presence passthrough, a minimal verifier is probably enough to start unless tests/clients depend on the metadata endpoints.

## E. Examples/resources/tests need a mechanical sweep

This includes:
- `examples/...` MCP servers
- `src/fast_agent/resources/examples/...` duplicates
- server fixture files in `tests/...`

Many only need:
- import change
- constructor kwarg move to `run()`
- SSE removal or conversion to HTTP

## Migration target

### Target server runtime shape

For fast-agent's own server surface:
- `FastMCP` imported from `fastmcp`
- transports:
  - `stdio`
  - `http`
- no SSE-specific shutdown path
- no server-side imports from `mcp.server.fastmcp.*`
- FastMCP HTTP startup uses:
  - `run(..., transport="http", host=..., port=...)` for synchronous entry points
  - `await run_http_async(...)` or `http_app(...)` for async/embed cases
- prompt returns use `fastmcp.prompts.Message`
- prompt registration uses public `add_prompt()` / `Prompt.from_function(...)`
- any prompt with dynamic parameters uses a generated explicit signature, not `**kwargs`
- local tool execution uses native FastMCP 3 `FunctionTool.run(arguments) -> ToolResult`
- fast-agent converts `ToolResult` to MCP `CallToolResult` explicitly at its own boundaries

## Implementation plan

### Phase 1 — remove server-side SSE exposure

Status: **completed**

#### 1.1 Remove SSE from fast-agent's own server CLI/runtime surfaces first

Files:
- `src/fast_agent/cli/commands/serve.py`
- `src/fast_agent/core/fastagent.py`
- `src/fast_agent/cli/commands/README.md`
- `src/fast_agent/mcp/server/agent_server.py`
- `src/fast_agent/mcp/prompts/prompt_server.py`
- SSE-specific examples/resources/tests/docs

Plan:
- remove `sse` from server transport enums and help text
- remove SSE-specific server startup/shutdown logic
- remove prompt-server SSE mode
- convert or delete server-side SSE tests and examples

Note:
- client-side SSE handling remains as-is in this plan
- unrelated non-MCP SSE wording elsewhere in the repo is out of scope unless it blocks server cleanup

Completed:
- `ServeTransport` / legacy server CLI parsing no longer expose `sse`
- `AgentMCPServer` no longer has SSE startup/shutdown branches
- `prompt-server` now exposes `http|stdio` only
- targeted server-side tests/docs were updated accordingly

#### 1.2 Add FastMCP 3 as an explicit dependency

Files:
- `pyproject.toml`

Plan:
- add `fastmcp==3.1.1`
- keep explicit `mcp==1.26.0` for now because fast-agent still directly imports:
  - `mcp.types`
  - MCP client transports
  - other SDK types outside the server migration scope

Rule:
- after this change, any fast-agent **server** module that still imports
  `mcp.server.fastmcp...` is considered unfinished migration work

#### 1.3 Rework `AgentMCPServer` around HTTP + STDIO only

Files:
- `src/fast_agent/mcp/server/agent_server.py`

Plan:
- change imports to `fastmcp`
- narrow transport literals from `http|sse|stdio` to `http|stdio`
- delete SSE-specific shutdown/connection-tracking machinery
- stop mutating `self.mcp_server.settings`
- store bind/runtime info on `AgentMCPServer` itself where needed
- use FastMCP 3 public startup/app APIs

Target direction:
- `run_async(transport="http", host=..., port=...)` for HTTP
- `run_stdio_async()` for stdio
- `http_app(transport="http", path="/mcp", ...)` when embedding

#### 1.4 Migrate agent prompts to typed FastMCP prompt messages

Files:
- `src/fast_agent/mcp/server/agent_server.py`

Plan:
- replace raw dict prompt returns with `fastmcp.prompts.Message`
- keep history prompt availability logic as-is

#### 1.5 Auth adapter migration

Files:
- `src/fast_agent/mcp/auth/presence.py`
- `src/fast_agent/mcp/server/agent_server.py`

Plan:
- migrate `PresenceTokenVerifier` to FastMCP 3 auth base classes
- switch `_get_request_bearer_token()` to FastMCP 3 auth context helper
- choose the simplest viable server-side auth shape:
  - start with a FastMCP `TokenVerifier`-based solution
  - only use `RemoteAuthProvider` if protected-resource metadata is actually needed for current tests/flows

#### 1.6 Validation for Phase 1

Primary tests to update/run:
- `tests/unit/fast_agent/mcp/test_agent_server_tool_description.py`
- `tests/unit/fast_agent/mcp/test_agent_server_response_mode.py`
- `tests/unit/fast_agent/mcp/test_agent_server_auth_passthrough.py`
- `tests/integration/api/test_mcp_auth_passthrough.py`
- `tests/integration/api/test_cli_and_mcp_server.py`
- `tests/unit/fast_agent/commands/test_serve_command.py`

### Phase 2 — prompt server migration

Files:
- `src/fast_agent/mcp/prompts/prompt_server.py`
- `src/fast_agent/mcp/prompts/prompt_load.py`
- prompt-server tests/integration fixtures

Plan:
- switch imports to `fastmcp.prompts` / `fastmcp.resources`
- replace `UserMessage` / `AssistantMessage` with `Message(..., role=...)`
- replace `Prompt(..., fn=...)` + `_prompt_manager.add_prompt(...)` with public FastMCP 3 prompt registration
- keep prompt-server HTTP/stdio-only after the phase-1 SSE removal

#### 2.1 Dynamic prompt signature shim

This is the one real prompt-specific implementation detail.

Current code dynamically exposes template variables by:
- collecting vars from template metadata
- defining `async def handler(**kwargs)`
- manually building `PromptArgument` instances

FastMCP 3 rejects prompt functions with `**kwargs`, so we need a wrapper with an explicit runtime signature.

Planned approach:
- keep the current metadata/template loading logic
- build a wrapper function
- set `__signature__` to explicit string parameters matching the template vars
- set `__annotations__`
- register that wrapper with `Prompt.from_function(...)`

This keeps the current dynamic behavior while using FastMCP 3's public prompt system.

#### 2.2 Validation for Phase 2

Primary tests:
- prompt-server integration tests under `tests/integration/prompt-server/...`
- any prompt multipart/template tests that cover exposed prompt rendering

### Phase 3 — native tool migration

Files:
- `src/fast_agent/tools/function_tool_loader.py`
- `src/fast_agent/agents/tool_agent.py`
- `src/fast_agent/tools/elicitation.py`
- `examples/new-api/simple_llm_advanced.py`
- relevant unit tests

Plan:
- stop relying on old FastMCP tool internals
- remove the current `FastMCPTool` subclass/wrapper
- make the loader return native `fastmcp.tools.FunctionTool` instances
- update `ToolAgent` to store and execute native FastMCP 3 tools
- update `ToolAgent.call_tool()` to translate native `ToolResult` into MCP `CallToolResult`
- remove obsolete custom sync-thread execution logic because FastMCP 3 already handles that

Non-goal:
- do **not** preserve the old local wrapper contract just to minimize churn

#### 3.1 Structured output handling

Per the project simplification request:
- remove the old explicit suppression plumbing first
- rely on FastMCP 3 defaults unless tests show a concrete regression
- if needed later, suppress only on the specific exposed send tools, not globally

#### 3.2 Validation for Phase 3

Primary tests:
- `tests/unit/fast_agent/tools/test_function_tool_loader.py`
- tool-agent local tool tests
- any integration tests that call local FastMCP-backed tools through `ToolAgent`

### Phase 4 — example/resource/test sweep

Files:
- `examples/...`
- `src/fast_agent/resources/examples/...`
- MCP server fixture files in `tests/...`

Plan:
- mechanical import updates to `fastmcp`
- move old constructor kwargs to `run()` / `run_http_async()`
- convert or remove server-side SSE examples
- keep packaged resource examples in sync with `examples/`

Specific note:
- `examples/tensorzero/mcp_server/mcp_server.py` and its packaged duplicate should move from `sse_app()` / `settings.sse_path` to `http_app(...)` with the modern API, or be simplified to plain HTTP if the custom mount isn't needed

## Risks / edge cases

### 1. Prompt dynamic-registration shim

This is the least mechanical part after removing SSE. It is still contained and should not require upstream workarounds beyond explicit runtime signatures.

### 2. Auth behavior drift

If clients currently rely on OAuth discovery metadata from the server, a plain verifier may be insufficient and we should promote the server auth wiring to `RemoteAuthProvider`.

### 3. Test assumptions about old transport names

There are many tests and docs mentioning SSE, but most are about:
- client-side SSE
- provider/model SSE
- old prompt-server/server fixtures

We need to be careful to remove SSE from fast-agent's **server-providing** surfaces
without touching unrelated client/provider paths.

### 4. Tool wrapper migration scope

If we try to make tools fully native in the same PR as the server transport migration, the diff may get noisier than necessary.

Preferred sequencing:
- stabilize `serve`/server runtime first
- then complete the native tool migration without adding a compatibility layer

## Proposed delivery slices

### PR 1 — remove server-side SSE exposure

Status: **completed**

Scope:
- remove SSE from fast-agent's server-facing CLI/runtime surface
- update or delete SSE-specific server tests/docs/examples

Acceptance:
- no server transport enum or help text exposes `sse`
- no SSE code remains in `AgentMCPServer`
- prompt-server remains `http|stdio` only
- related tests pass

### PR 2 — FastMCP 3 foundation + `serve` runtime

Scope:
- `pyproject.toml`
- migrate `AgentMCPServer` to FastMCP 3 public APIs
- migrate server auth helper enough for current serve tests
- update core `serve` docs/tests

Acceptance:
- `fast-agent serve --transport=http` works
- `fast-agent serve --transport=stdio` works
- no SSE code remains in `AgentMCPServer`
- related tests pass

### PR 3 — prompt server migration

Scope:
- prompt-server runtime and prompt registration
- confirm prompt-server remains HTTP/stdio-only after PR 1
- update prompt-server tests

Acceptance:
- prompt-server works over `http` and `stdio`
- dynamic prompt vars still render correctly

### PR 4 — native tool migration + example sweep

Scope:
- native tool migration
- example/test fixture migration
- packaged resource example sync

Acceptance:
- local FastMCP-backed tools still work in agents through native FastMCP 3 types
- examples and duplicated packaged resources are aligned

## Validation checklist

After each implementation slice:
- `uv run scripts/lint.py`
- `uv run scripts/typecheck.py`

Likely targeted test groups during migration:
- SSE-removal regression tests across server CLI/runtime
- server runtime / serve command tests
- prompt-server integration tests
- tool wrapper/unit tests
- example/server fixture tests that are still in-scope after SSE removal

## Open questions to resolve while implementing

1. Server auth surface:
   - Is a plain FastMCP `TokenVerifier` enough for current fast-agent serve auth needs?
   - Or do we need `RemoteAuthProvider` to preserve expected MCP/OAuth discovery behavior?

2. Exposed send-tool structured content:
   - Accept FastMCP 3 defaults immediately?
   - Or suppress on those tools only if a concrete client/test regression appears?

3. TensorZero/example embedding:
   - keep a custom ASGI mount via `http_app(...)`
   - or simplify examples to plain `mcp.run(transport="http", ...)`

## Recommended next step

Start with **PR 2**:
- add `fastmcp==3.1.1`
- migrate `AgentMCPServer` imports/runtime/auth wiring to FastMCP 3 public APIs
- keep the diff focused on server runtime now that the transport matrix is smaller
