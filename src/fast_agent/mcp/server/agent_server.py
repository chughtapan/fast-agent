"""Agent MCP server."""

import asyncio
import logging
import os
import time
from importlib.metadata import version as get_version
from typing import Any, Awaitable, Callable, Literal, cast

from fastmcp import Context as MCPContext
from fastmcp import FastMCP
from fastmcp.prompts import Message
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.dependencies import get_access_token
from pydantic import AnyHttpUrl
from starlette.middleware import Middleware

import fast_agent.core.prompt
from fast_agent.core.fastagent import AgentInstance
from fast_agent.core.logging.logger import get_logger
from fast_agent.llm.request_params import (
    ResponseMode,
    ToolResultMode,
    response_mode_to_tool_result_mode,
    tool_result_mode_allows_response_mode,
)
from fast_agent.mcp.auth.middleware import HFAuthHeaderMiddleware
from fast_agent.mcp.prompts.prompt_server import convert_to_fastmcp_messages
from fast_agent.mcp.tool_progress import MCPToolProgressManager
from fast_agent.types import PromptMessageExtended, RequestParams
from fast_agent.utils.async_utils import run_sync

logger = get_logger(__name__)


def _get_request_bearer_token() -> str | None:
    """Return the authenticated bearer token for the current MCP request."""
    access_token = get_access_token()
    if access_token is None:
        return None
    return access_token.token


def _get_fast_agent_version() -> str | None:
    for package_name in ("fast-agent-mcp", "fast-agent"):
        try:
            return get_version(package_name)
        except Exception:
            continue
    return None


def _get_oauth_config() -> tuple[str | None, list[str], str]:
    """
    Read OAuth configuration from environment variables.

    Returns:
        Tuple of (provider, scopes, resource_url).
        provider is None if OAuth is not enabled.
    """
    oauth_provider = os.environ.get("FAST_AGENT_SERVE_OAUTH", "").lower()
    if oauth_provider in ("hf", "huggingface"):
        oauth_provider = "huggingface"
    elif not oauth_provider:
        oauth_provider = None

    oauth_scopes_str = os.environ.get("FAST_AGENT_OAUTH_SCOPES", "")
    oauth_scopes = [scope.strip() for scope in oauth_scopes_str.split(",") if scope.strip()] or [
        "access"
    ]
    resource_url = os.environ.get("FAST_AGENT_OAUTH_RESOURCE_URL", "http://localhost:8000")
    return oauth_provider, oauth_scopes, resource_url


def _history_to_fastmcp_messages(
    message_history: list[PromptMessageExtended],
) -> list[Message]:
    """Convert stored agent history into FastMCP prompt messages."""
    prompt_messages = fast_agent.core.prompt.Prompt.from_multipart(message_history)
    return convert_to_fastmcp_messages(prompt_messages)


TransportMode = Literal["http", "stdio"]


class AgentMCPServer:
    """Exposes FastAgent agents as MCP tools through an MCP server."""

    def __init__(
        self,
        primary_instance: AgentInstance,
        create_instance: Callable[[], Awaitable[AgentInstance]],
        dispose_instance: Callable[[AgentInstance], Awaitable[None]],
        instance_scope: str,
        server_name: str = "FastAgent-MCP-Server",
        server_description: str | None = None,
        tool_description: str | None = None,
        host: str = "0.0.0.0",
        get_registry_version: Callable[[], int] | None = None,
        reload_callback: Callable[[], Awaitable[bool]] | None = None,
        tool_name_template: str | None = None,
    ) -> None:
        self.primary_instance = primary_instance
        self._create_instance_task = create_instance
        self._dispose_instance_task = dispose_instance
        self._instance_scope = instance_scope
        self._default_host = host
        self._get_registry_version = get_registry_version
        self._reload_callback = reload_callback
        self._primary_registry_version = getattr(primary_instance, "registry_version", 0)
        self._shared_instance_lock = asyncio.Lock()
        self._shared_active_requests = 0
        self._shared_instance_active = True
        self._stale_instances: list[AgentInstance] = []
        self._tool_description = tool_description
        self._tool_name_template = tool_name_template or "{agent}"
        if "{agent}" not in self._tool_name_template:
            raise ValueError("tool_name_template must include '{agent}'.")

        oauth_provider, oauth_scopes, resource_url = _get_oauth_config()
        auth_provider = None
        if oauth_provider == "huggingface":
            from fast_agent.mcp.auth.presence import PresenceTokenVerifier

            token_verifier = PresenceTokenVerifier(
                provider="huggingface",
                scopes=oauth_scopes,
                base_url=resource_url,
            )
            auth_provider = RemoteAuthProvider(
                token_verifier=token_verifier,
                authorization_servers=[AnyHttpUrl("https://huggingface.co")],
                base_url=AnyHttpUrl(resource_url),
                scopes_supported=oauth_scopes,
                resource_name=server_name,
            )
            logger.info(
                f"OAuth enabled for provider '{oauth_provider}'",
                name="oauth_enabled",
                provider=oauth_provider,
                scopes=oauth_scopes,
                resource_url=resource_url,
            )

        self.mcp_server = FastMCP(
            name=server_name,
            instructions=self._build_instructions(server_description),
            version=_get_fast_agent_version(),
            auth=auth_provider,
        )

        @self.mcp_server.custom_route("/", methods=["GET"])
        async def root_info(request):
            del request
            from starlette.responses import PlainTextResponse

            version = _get_fast_agent_version() or "unknown"
            return PlainTextResponse(
                f"fast-agent mcp server (v{version}) - see https://fast-agent.ai for more information."
            )

        self._registered_agents: set[str] = set(primary_instance.agents.keys())
        self.std_logger = logging.getLogger("fast_agent.server")
        self._connection_instances: dict[int, AgentInstance] = {}
        self._connection_cleanup_tasks: dict[int, Callable[[], Awaitable[None]]] = {}
        self._connection_lock = asyncio.Lock()

        self.setup_tools()

        logger.info(
            f"AgentMCPServer initialized with {len(primary_instance.agents)} agents",
            name="mcp_server_initialized",
            agent_count=len(primary_instance.agents),
            instance_scope=instance_scope,
        )

    def setup_tools(self) -> None:
        """Register all agents as MCP tools."""
        for agent_name in self.primary_instance.agents.keys():
            self.register_agent_tools(agent_name)
        if self._reload_callback is not None:
            self._register_reload_tool()

    @staticmethod
    def _agent_tool_result_mode(agent: Any | None) -> ToolResultMode:
        config = getattr(agent, "config", None)
        request_params = (
            getattr(config, "default_request_params", None) if config is not None else None
        )
        if request_params is None:
            return "postprocess"
        return request_params.tool_result_mode

    def register_agent_tools(self, agent_name: str) -> None:
        """Register tools for a specific agent."""
        self._registered_agents.add(agent_name)

        tool_description = (
            self._tool_description.format(agent=agent_name)
            if self._tool_description and "{agent}" in self._tool_description
            else self._tool_description
        )

        agent = self.primary_instance.agents.get(agent_name)
        agent_description = None
        if agent is not None:
            config = getattr(agent, "config", None)
            agent_description = getattr(config, "description", None)

        response_mode_enabled = tool_result_mode_allows_response_mode(
            self._agent_tool_result_mode(agent)
        )
        tool_name = self._tool_name_template.format(agent=agent_name)
        tool_description_value = (
            tool_description or agent_description or f"Send a message to the {agent_name} agent"
        )

        async def _send_message(
            message: str,
            ctx: MCPContext,
            response_mode: ResponseMode | None = None,
        ) -> str:
            from fast_agent.mcp.auth.context import request_bearer_token

            saved_token = request_bearer_token.set(_get_request_bearer_token())
            request_param_overrides: dict[str, Any] = {
                "tool_execution_handler": MCPToolProgressManager(self._build_progress_reporter(ctx)),
                "emit_loop_progress": True,
            }
            if response_mode is not None:
                tool_result_mode = response_mode_to_tool_result_mode(response_mode)
                if tool_result_mode is not None:
                    request_param_overrides["tool_result_mode"] = tool_result_mode

            request_params = RequestParams(**request_param_overrides)
            try:
                instance = await self._acquire_instance(ctx)
                agent_instance = instance.app[agent_name]
                agent_context = getattr(agent_instance, "context", None)

                async def execute_send() -> str:
                    start = time.perf_counter()
                    logger.info(
                        f"MCP request received for agent '{agent_name}'",
                        name="mcp_request_start",
                        agent=agent_name,
                        session=self._session_identifier(ctx),
                    )
                    self.std_logger.info(
                        "MCP request received for agent '%s' (scope=%s)",
                        agent_name,
                        self._instance_scope,
                    )

                    response = await agent_instance.send(message, request_params=request_params)
                    duration = time.perf_counter() - start

                    logger.info(
                        f"Agent '{agent_name}' completed MCP request",
                        name="mcp_request_complete",
                        agent=agent_name,
                        duration=duration,
                        session=self._session_identifier(ctx),
                    )
                    self.std_logger.info(
                        "Agent '%s' completed MCP request in %.2fs (scope=%s)",
                        agent_name,
                        duration,
                        self._instance_scope,
                    )
                    return response

                try:
                    if agent_context is not None:
                        return await self.with_bridged_context(agent_context, ctx, execute_send)
                    return await execute_send()
                finally:
                    await self._release_instance(ctx, instance)
            finally:
                request_bearer_token.reset(saved_token)

        if response_mode_enabled:

            @self.mcp_server.tool(
                name=tool_name,
                description=tool_description_value,
                output_schema=None,
            )
            async def send_message(
                message: str,
                ctx: MCPContext,
                response_mode: Literal["inherit", "postprocess", "passthrough"] = "inherit",
            ) -> str:
                return await _send_message(message, ctx, response_mode)

        else:

            @self.mcp_server.tool(
                name=tool_name,
                description=tool_description_value,
                output_schema=None,
            )
            async def send_message(message: str, ctx: MCPContext) -> str:
                return await _send_message(message, ctx)

        if self._instance_scope == "request":
            return

        @self.mcp_server.prompt(
            name=f"{agent_name}_history",
            description=f"Conversation history for the {agent_name} agent",
        )
        async def get_history_prompt(ctx: MCPContext) -> list[Message]:
            instance = await self._acquire_instance(ctx)
            agent_instance = instance.app[agent_name]
            try:
                multipart_history = agent_instance.message_history
                if not multipart_history:
                    return []

                return _history_to_fastmcp_messages(multipart_history)
            finally:
                await self._release_instance(ctx, instance, reuse_connection=True)

    def _register_missing_agents(self, instance: AgentInstance) -> None:
        new_agents = set(instance.agents.keys())
        for agent_name in sorted(new_agents - self._registered_agents):
            self.register_agent_tools(agent_name)

    def _register_reload_tool(self) -> None:
        @self.mcp_server.tool(
            name="reload_agent_cards",
            description="Reload AgentCards",
            output_schema=None,
        )
        async def reload_agent_cards(ctx: MCPContext) -> str:
            if not self._reload_callback:
                return "Reload not available."

            changed = await self._reload_callback()
            if not changed:
                return "No AgentCard changes detected."

            if self._instance_scope == "shared":
                await self._maybe_refresh_shared_instance()
                return "Reloaded AgentCards."

            if self._instance_scope == "connection":
                session_key = self._connection_key(ctx)
                new_instance = await self._create_instance_task()
                async with self._connection_lock:
                    old_instance = self._connection_instances.get(session_key)
                    self._connection_instances[session_key] = new_instance
                self._register_missing_agents(new_instance)
                if old_instance is not None:
                    await self._dispose_instance_task(old_instance)
                return "Reloaded AgentCards."

            new_instance = await self._create_instance_task()
            try:
                self._register_missing_agents(new_instance)
            finally:
                await self._dispose_instance_task(new_instance)
            return "Reloaded AgentCards."

    def _build_instructions(self, server_description: str | None) -> str:
        agent_count = len(self.primary_instance.agents)
        base = server_description or f"This server provides access to {agent_count} agents."
        scope_info = (
            "do NOT retain history between your requests"
            if self._instance_scope == "request"
            else "retain history between tool calls."
        )
        return (
            f"{base} Use the `{self._name_for_send_tool()}` tools to send messages to agents. "
            f"Instance mode is {self._instance_scope}. Agents ({scope_info})"
        )

    def _name_for_send_tool(self) -> str:
        return self._tool_name_template.format(agent="<agent>")

    def _build_progress_reporter(
        self, ctx: MCPContext
    ) -> Callable[[float, float | None, str | None], Awaitable[None]]:
        async def report_progress(
            progress: float,
            total: float | None = None,
            message: str | None = None,
        ) -> None:
            try:
                await ctx.report_progress(progress, total, message)
            except Exception:
                pass

        return report_progress

    async def _acquire_instance(self, ctx: MCPContext | None) -> AgentInstance:
        if self._instance_scope == "shared":
            await self._maybe_refresh_shared_instance()
            self._shared_active_requests += 1
            return self.primary_instance

        if self._instance_scope == "request":
            return await self._create_instance_task()

        assert ctx is not None, "Context is required for connection-scoped instances"
        session_key = self._connection_key(ctx)
        async with self._connection_lock:
            instance = self._connection_instances.get(session_key)
            if instance is None:
                instance = await self._create_instance_task()
                self._connection_instances[session_key] = instance
                self._register_session_cleanup(ctx, session_key)
            return instance

    async def _release_instance(
        self,
        ctx: MCPContext | None,
        instance: AgentInstance,
        *,
        reuse_connection: bool = False,
    ) -> None:
        del ctx, reuse_connection
        if self._instance_scope == "shared":
            if self._shared_active_requests > 0:
                self._shared_active_requests -= 1
            await self._dispose_stale_instances_if_idle()
            return
        if self._instance_scope == "request":
            await self._dispose_instance_task(instance)

    def _connection_key(self, ctx: MCPContext) -> int:
        return id(ctx.session)

    def _register_session_cleanup(self, ctx: MCPContext, session_key: int) -> None:
        async def cleanup() -> None:
            instance = self._connection_instances.pop(session_key, None)
            if instance is not None:
                await self._dispose_instance_task(instance)

        exit_stack = getattr(ctx.session, "_exit_stack", None)
        if exit_stack is not None:
            exit_stack.push_async_callback(cleanup)
            return
        self._connection_cleanup_tasks[session_key] = cleanup

    def _session_identifier(self, ctx: MCPContext | None) -> str | None:
        if ctx is None or ctx.request_context is None:
            return None
        request = getattr(ctx.request_context, "request", None)
        if request is None:
            return None
        headers = getattr(request, "headers", None)
        return headers.get("mcp-session-id") if headers is not None else None

    async def _maybe_refresh_shared_instance(self) -> None:
        if not self._get_registry_version:
            return
        latest_version = self._get_registry_version()
        if latest_version <= self._primary_registry_version:
            return

        async with self._shared_instance_lock:
            latest_version = self._get_registry_version()
            if latest_version <= self._primary_registry_version:
                return

            new_instance = await self._create_instance_task()
            old_instance = self.primary_instance
            self.primary_instance = new_instance
            self._primary_registry_version = getattr(new_instance, "registry_version", latest_version)
            self._stale_instances.append(old_instance)
            self._register_missing_agents(new_instance)

    async def _dispose_stale_instances_if_idle(self) -> None:
        if self._shared_active_requests or not self._stale_instances:
            return
        stale = list(self._stale_instances)
        self._stale_instances.clear()
        for instance in stale:
            await self._dispose_instance_safely(instance, phase="shared stale instance cleanup")

    async def _dispose_primary_instance(self) -> None:
        if not self._shared_instance_active:
            return
        try:
            await self._dispose_instance_safely(
                self.primary_instance,
                phase="primary instance cleanup",
            )
        finally:
            self._shared_instance_active = False

    async def _dispose_all_stale_instances(self) -> None:
        if not self._stale_instances:
            return
        stale = list(self._stale_instances)
        self._stale_instances.clear()
        for instance in stale:
            await self._dispose_instance_safely(
                instance,
                phase="stale instance cleanup",
            )

    async def _dispose_all_connection_instances(self) -> None:
        pending_cleanups = list(self._connection_cleanup_tasks.values())
        self._connection_cleanup_tasks.clear()
        for cleanup in pending_cleanups:
            try:
                await cleanup()
            except Exception:
                logger.exception("Connection cleanup callback failed during shutdown")

        async with self._connection_lock:
            instances = list(self._connection_instances.values())
            self._connection_instances.clear()

        for instance in instances:
            await self._dispose_instance_safely(instance, phase="connection instance cleanup")

    async def _dispose_instance_safely(self, instance: AgentInstance, *, phase: str) -> None:
        try:
            await self._dispose_instance_task(instance)
        except Exception:
            logger.exception("Agent instance disposal failed during %s", phase)

    def _http_middleware(self) -> list[Middleware] | None:
        oauth_provider = os.environ.get("FAST_AGENT_SERVE_OAUTH", "").lower()
        if oauth_provider not in {"hf", "huggingface"}:
            return None
        return [Middleware(cast("Any", HFAuthHeaderMiddleware))]

    def http_app(self):
        """Return a FastMCP HTTP ASGI app configured for the current instance scope."""
        return self.mcp_server.http_app(
            transport="http",
            middleware=self._http_middleware(),
            stateless_http=self._instance_scope == "request",
        )

    def run(
        self,
        transport: TransportMode = "http",
        host: str = "0.0.0.0",
        port: int = 8000,
    ) -> None:
        """Run the MCP server synchronously."""
        try:
            if transport == "http":
                self.mcp_server.run(
                    transport="http",
                    host=host,
                    port=port,
                    middleware=self._http_middleware(),
                    stateless_http=self._instance_scope == "request",
                )
                return
            if transport == "stdio":
                self.mcp_server.run(transport="stdio")
                return
            raise ValueError(f"Unsupported MCP server transport: {transport}")
        except KeyboardInterrupt:
            print("\nServer stopped by user (CTRL+C)")
        finally:
            run_sync(self.shutdown)

    async def run_async(
        self,
        transport: TransportMode = "http",
        host: str = "0.0.0.0",
        port: int = 8000,
    ) -> None:
        """Run the MCP server asynchronously."""
        try:
            if transport == "http":
                await self.mcp_server.run_http_async(
                    transport="http",
                    host=host,
                    port=port,
                    middleware=self._http_middleware(),
                    stateless_http=self._instance_scope == "request",
                )
                return
            if transport == "stdio":
                await self.mcp_server.run_stdio_async()
                return
            raise ValueError(f"Unsupported MCP server transport: {transport}")
        finally:
            await self.shutdown()

    async def with_bridged_context(
        self,
        agent_context: Any,
        mcp_context: MCPContext,
        func: Callable[..., Awaitable[str]],
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """Execute a function with bridged context between MCP and agent."""
        original_progress_reporter = getattr(agent_context, "progress_reporter", None)
        agent_context.mcp_context = mcp_context

        async def bridged_progress(
            progress: float,
            total: float | None = None,
            message: str | None = None,
        ) -> None:
            await mcp_context.report_progress(progress, total, message)
            if original_progress_reporter is None:
                return
            try:
                await original_progress_reporter(progress, total, message)
            except TypeError:
                await original_progress_reporter(progress, total)

        if hasattr(agent_context, "progress_reporter"):
            agent_context.progress_reporter = bridged_progress

        try:
            return await func(*args, **kwargs)
        finally:
            if hasattr(agent_context, "progress_reporter"):
                agent_context.progress_reporter = original_progress_reporter
            if hasattr(agent_context, "mcp_context"):
                delattr(agent_context, "mcp_context")

    async def shutdown(self) -> None:
        """Dispose all managed agent instances."""
        await self._dispose_all_connection_instances()
        await self._dispose_primary_instance()
        await self._dispose_all_stale_instances()
