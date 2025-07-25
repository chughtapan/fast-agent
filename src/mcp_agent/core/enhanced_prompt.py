"""
Enhanced prompt functionality with advanced prompt_toolkit features.
"""

import asyncio
import os
import shlex
import subprocess
import tempfile
from importlib.metadata import version
from typing import List, Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, WordCompleter
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich import print as rich_print

from mcp_agent.core.agent_types import AgentType
from mcp_agent.core.exceptions import PromptExitError

# Get the application version
try:
    app_version = version("fast-agent-mcp")
except:  # noqa: E722
    app_version = "unknown"

# Map of agent names to their history
agent_histories = {}

# Store available agents for auto-completion
available_agents = set()

# Keep track of multi-line mode state
in_multiline_mode = False

# Track whether help text has been shown globally
help_message_shown = False

# Track which agents have shown their info
_agent_info_shown = set()


async def _display_agent_info_helper(agent_name: str, agent_provider: object) -> None:
    """Helper function to display agent information."""
    # Only show once per agent
    if agent_name in _agent_info_shown:
        return

    try:
        # Get agent info
        if hasattr(agent_provider, "_agent"):
            # This is an AgentApp - get the specific agent
            agent = agent_provider._agent(agent_name)
        else:
            # This is a single agent
            agent = agent_provider

        # Get counts
        servers = await agent.list_servers()
        server_count = len(servers) if servers else 0

        tools_result = await agent.list_tools()
        tool_count = (
            len(tools_result.tools) if tools_result and hasattr(tools_result, "tools") else 0
        )

        resources_dict = await agent.list_resources()
        resource_count = (
            sum(len(resources) for resources in resources_dict.values()) if resources_dict else 0
        )

        prompts_dict = await agent.list_prompts()
        prompt_count = sum(len(prompts) for prompts in prompts_dict.values()) if prompts_dict else 0

        # Handle different agent types
        if agent.agent_type == AgentType.PARALLEL:
            # Count child agents for parallel agents
            child_count = 0
            if hasattr(agent, "fan_out_agents") and agent.fan_out_agents:
                child_count += len(agent.fan_out_agents)
            if hasattr(agent, "fan_in_agent") and agent.fan_in_agent:
                child_count += 1

            if child_count > 0:
                child_word = "child agent" if child_count == 1 else "child agents"
                rich_print(
                    f"[dim]Agent [/dim][blue]{agent_name}[/blue][dim]:[/dim] {child_count:,}[dim] {child_word}[/dim]"
                )
        elif agent.agent_type == AgentType.ROUTER:
            # Count child agents for router agents
            child_count = 0
            if hasattr(agent, "routing_agents") and agent.routing_agents:
                child_count = len(agent.routing_agents)
            elif hasattr(agent, "agents") and agent.agents:
                child_count = len(agent.agents)

            if child_count > 0:
                child_word = "child agent" if child_count == 1 else "child agents"
                rich_print(
                    f"[dim]Agent [/dim][blue]{agent_name}[/blue][dim]:[/dim] {child_count:,}[dim] {child_word}[/dim]"
                )
        else:
            # For regular agents, only display if they have MCP servers attached
            if server_count > 0:
                # Build display parts in order: tools, prompts, resources (omit if count is 0)
                display_parts = []

                if tool_count > 0:
                    tool_word = "tool" if tool_count == 1 else "tools"
                    display_parts.append(f"{tool_count:,}[dim] {tool_word}[/dim]")

                if prompt_count > 0:
                    prompt_word = "prompt" if prompt_count == 1 else "prompts"
                    display_parts.append(f"{prompt_count:,}[dim] {prompt_word}[/dim]")

                if resource_count > 0:
                    resource_word = "resource" if resource_count == 1 else "resources"
                    display_parts.append(f"{resource_count:,}[dim] {resource_word}[/dim]")

                # Always show server count
                server_word = "Server" if server_count == 1 else "Servers"
                server_text = f"{server_count:,}[dim] MCP {server_word}[/dim]"

                if display_parts:
                    content = (
                        f"{server_text}[dim], [/dim]"
                        + "[dim], [/dim]".join(display_parts)
                        + "[dim] available[/dim]"
                    )
                else:
                    content = f"{server_text}[dim] available[/dim]"

                rich_print(f"[dim]Agent [/dim][blue]{agent_name}[/blue][dim]:[/dim] {content}")

        # Mark as shown
        _agent_info_shown.add(agent_name)

    except Exception:
        # Silently ignore errors to not disrupt the user experience
        pass


async def _display_all_agents_with_hierarchy(available_agents: List[str], agent_provider) -> None:
    """Display all agents with tree structure for workflow agents."""
    # Track which agents are children to avoid displaying them twice
    child_agents = set()

    # First pass: identify all child agents
    for agent_name in available_agents:
        try:
            if hasattr(agent_provider, "_agent"):
                agent = agent_provider._agent(agent_name)
            else:
                agent = agent_provider

            if agent.agent_type == AgentType.PARALLEL:
                if hasattr(agent, "fan_out_agents") and agent.fan_out_agents:
                    for child_agent in agent.fan_out_agents:
                        child_agents.add(child_agent.name)
                if hasattr(agent, "fan_in_agent") and agent.fan_in_agent:
                    child_agents.add(agent.fan_in_agent.name)
            elif agent.agent_type == AgentType.ROUTER:
                if hasattr(agent, "routing_agents") and agent.routing_agents:
                    for child_agent in agent.routing_agents:
                        child_agents.add(child_agent.name)
                elif hasattr(agent, "agents") and agent.agents:
                    for child_agent in agent.agents:
                        child_agents.add(child_agent.name)
        except Exception:
            continue

    # Second pass: display agents (parents with children, standalone agents without children)
    for agent_name in sorted(available_agents):
        # Skip if this agent is a child of another agent
        if agent_name in child_agents:
            continue

        try:
            if hasattr(agent_provider, "_agent"):
                agent = agent_provider._agent(agent_name)
            else:
                agent = agent_provider

            # Display parent agent
            await _display_agent_info_helper(agent_name, agent_provider)

            # If it's a workflow agent, display its children
            if agent.agent_type == AgentType.PARALLEL:
                await _display_parallel_children(agent, agent_provider)
            elif agent.agent_type == AgentType.ROUTER:
                await _display_router_children(agent, agent_provider)

        except Exception:
            continue


async def _display_parallel_children(parallel_agent, agent_provider) -> None:
    """Display child agents of a parallel agent in tree format."""
    children = []

    # Collect fan-out agents
    if hasattr(parallel_agent, "fan_out_agents") and parallel_agent.fan_out_agents:
        for child_agent in parallel_agent.fan_out_agents:
            children.append(child_agent)

    # Collect fan-in agent
    if hasattr(parallel_agent, "fan_in_agent") and parallel_agent.fan_in_agent:
        children.append(parallel_agent.fan_in_agent)

    # Display children with tree formatting
    for i, child_agent in enumerate(children):
        is_last = i == len(children) - 1
        prefix = "└─" if is_last else "├─"
        await _display_child_agent_info(child_agent, prefix, agent_provider)


async def _display_router_children(router_agent, agent_provider) -> None:
    """Display child agents of a router agent in tree format."""
    children = []

    # Collect routing agents
    if hasattr(router_agent, "routing_agents") and router_agent.routing_agents:
        children = router_agent.routing_agents
    elif hasattr(router_agent, "agents") and router_agent.agents:
        children = router_agent.agents

    # Display children with tree formatting
    for i, child_agent in enumerate(children):
        is_last = i == len(children) - 1
        prefix = "└─" if is_last else "├─"
        await _display_child_agent_info(child_agent, prefix, agent_provider)


async def _display_child_agent_info(child_agent, prefix: str, agent_provider) -> None:
    """Display info for a child agent with tree prefix."""
    try:
        # Get counts for child agent
        servers = await child_agent.list_servers()
        server_count = len(servers) if servers else 0

        tools_result = await child_agent.list_tools()
        tool_count = (
            len(tools_result.tools) if tools_result and hasattr(tools_result, "tools") else 0
        )

        resources_dict = await child_agent.list_resources()
        resource_count = (
            sum(len(resources) for resources in resources_dict.values()) if resources_dict else 0
        )

        prompts_dict = await child_agent.list_prompts()
        prompt_count = sum(len(prompts) for prompts in prompts_dict.values()) if prompts_dict else 0

        # Only display if child has MCP servers
        if server_count > 0:
            # Pluralization helpers
            server_word = "Server" if server_count == 1 else "Servers"
            tool_word = "tool" if tool_count == 1 else "tools"
            resource_word = "resource" if resource_count == 1 else "resources"
            prompt_word = "prompt" if prompt_count == 1 else "prompts"

            rich_print(
                f"[dim]  {prefix} [/dim][blue]{child_agent.name}[/blue][dim]:[/dim] {server_count:,}[dim] MCP {server_word}, [/dim]{tool_count:,}[dim] {tool_word}, [/dim]{resource_count:,}[dim] {resource_word}, [/dim]{prompt_count:,}[dim] {prompt_word} available[/dim]"
            )
        else:
            # Show child even without MCP servers for context
            rich_print(
                f"[dim]  {prefix} [/dim][blue]{child_agent.name}[/blue][dim]: No MCP Servers[/dim]"
            )

    except Exception:
        # Fallback: just show the name
        rich_print(f"[dim]  {prefix} [/dim][blue]{child_agent.name}[/blue]")


class AgentCompleter(Completer):
    """Provide completion for agent names and common commands."""

    def __init__(
        self,
        agents: List[str],
        commands: List[str] = None,
        agent_types: dict = None,
        is_human_input: bool = False,
    ) -> None:
        self.agents = agents
        # Map commands to their descriptions for better completion hints
        self.commands = {
            "tools": "List and call MCP tools",
            "prompt": "List and select MCP prompts, or apply specific prompt (/prompt <name>)",
            "agents": "List available agents",
            "usage": "Show current usage statistics",
            "markdown": "Show last assistant message without markdown formatting",
            "help": "Show available commands",
            "clear": "Clear the screen",
            "STOP": "Stop this prompting session and move to next workflow step",
            "EXIT": "Exit fast-agent, terminating any running workflows",
            **(commands or {}),  # Allow custom commands to be passed in
        }
        if is_human_input:
            self.commands.pop("agents")
            self.commands.pop("prompt", None)  # Remove prompt command in human input mode
            self.commands.pop("tools", None)  # Remove tools command in human input mode
            self.commands.pop("usage", None)  # Remove usage command in human input mode
        self.agent_types = agent_types or {}

    def get_completions(self, document, complete_event):
        """Synchronous completions method - this is what prompt_toolkit expects by default"""
        text = document.text_before_cursor.lower()

        # Complete commands
        if text.startswith("/"):
            cmd = text[1:]
            # Simple command completion - match beginning of command
            for command, description in self.commands.items():
                if command.lower().startswith(cmd):
                    yield Completion(
                        command,
                        start_position=-len(cmd),
                        display=command,
                        display_meta=description,
                    )

        # Complete agent names for agent-related commands
        elif text.startswith("@"):
            agent_name = text[1:]
            for agent in self.agents:
                if agent.lower().startswith(agent_name.lower()):
                    # Get agent type or default to "Agent"
                    agent_type = self.agent_types.get(agent, AgentType.BASIC).value
                    yield Completion(
                        agent,
                        start_position=-len(agent_name),
                        display=agent,
                        display_meta=agent_type,
                    )


# Helper function to open text in an external editor
def get_text_from_editor(initial_text: str = "") -> str:
    """
    Opens the user\'s configured editor ($VISUAL or $EDITOR) to edit the initial_text.
    Falls back to \'nano\' (Unix) or \'notepad\' (Windows) if neither is set.
    Returns the edited text, or the original text if an error occurs.
    """
    editor_cmd_str = os.environ.get("VISUAL") or os.environ.get("EDITOR")

    if not editor_cmd_str:
        if os.name == "nt":  # Windows
            editor_cmd_str = "notepad"
        else:  # Unix-like (Linux, macOS)
            editor_cmd_str = "nano"  # A common, usually available, simple editor

    # Use shlex.split to handle editors with arguments (e.g., "code --wait")
    try:
        editor_cmd_list = shlex.split(editor_cmd_str)
        if not editor_cmd_list:  # Handle empty string from shlex.split
            raise ValueError("Editor command string is empty or invalid.")
    except ValueError as e:
        rich_print(f"[red]Error: Invalid editor command string ('{editor_cmd_str}'): {e}[/red]")
        return initial_text

    # Create a temporary file for the editor to use.
    # Using a suffix can help some editors with syntax highlighting or mode.
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+", delete=False, suffix=".txt", encoding="utf-8"
        ) as tmp_file:
            if initial_text:
                tmp_file.write(initial_text)
                tmp_file.flush()  # Ensure content is written to disk before editor opens it
            temp_file_path = tmp_file.name
    except Exception as e:
        rich_print(f"[red]Error: Could not create temporary file for editor: {e}[/red]")
        return initial_text

    try:
        # Construct the full command: editor_parts + [temp_file_path]
        # e.g., [\'vim\', \'/tmp/somefile.txt\'] or [\'code\', \'--wait\', \'/tmp/somefile.txt\']
        full_cmd = editor_cmd_list + [temp_file_path]

        # Run the editor. This is a blocking call.
        subprocess.run(full_cmd, check=True)

        # Read the content back from the temporary file.
        with open(temp_file_path, "r", encoding="utf-8") as f:
            edited_text = f.read()

    except FileNotFoundError:
        rich_print(
            f"[red]Error: Editor command '{editor_cmd_list[0]}' not found. "
            f"Please set $VISUAL or $EDITOR correctly, or install '{editor_cmd_list[0]}'.[/red]"
        )
        return initial_text
    except subprocess.CalledProcessError as e:
        rich_print(
            f"[red]Error: Editor '{editor_cmd_list[0]}' closed with an error (code {e.returncode}).[/red]"
        )
        return initial_text
    except Exception as e:
        rich_print(
            f"[red]An unexpected error occurred while launching or using the editor: {e}[/red]"
        )
        return initial_text
    finally:
        # Always attempt to clean up the temporary file.
        if "temp_file_path" in locals() and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception as e:
                rich_print(
                    f"[yellow]Warning: Could not remove temporary file {temp_file_path}: {e}[/yellow]"
                )

    return edited_text.strip()  # Added strip() to remove trailing newlines often added by editors


def create_keybindings(on_toggle_multiline=None, app=None, agent_provider=None, agent_name=None):
    """Create custom key bindings."""
    kb = KeyBindings()

    @kb.add("c-m", filter=Condition(lambda: not in_multiline_mode))
    def _(event) -> None:
        """Enter: accept input when not in multiline mode."""
        event.current_buffer.validate_and_handle()

    @kb.add("c-m", filter=Condition(lambda: in_multiline_mode))
    def _(event) -> None:
        """Enter: insert newline when in multiline mode."""
        event.current_buffer.insert_text("\n")

    # Use c-j (Ctrl+J) as an alternative to represent Ctrl+Enter in multiline mode
    @kb.add("c-j", filter=Condition(lambda: in_multiline_mode))
    def _(event) -> None:
        """Ctrl+J (equivalent to Ctrl+Enter): Submit in multiline mode."""
        event.current_buffer.validate_and_handle()

    @kb.add("c-t")
    def _(event) -> None:
        """Ctrl+T: Toggle multiline mode."""
        global in_multiline_mode
        in_multiline_mode = not in_multiline_mode

        # Force redraw the app to update toolbar
        if event.app:
            event.app.invalidate()
        elif app:
            app.invalidate()

        # Call the toggle callback if provided
        if on_toggle_multiline:
            on_toggle_multiline(in_multiline_mode)

        # Instead of printing, we'll just update the toolbar
        # The toolbar will show the current mode

    @kb.add("c-l")
    def _(event) -> None:
        """Ctrl+L: Clear the input buffer."""
        event.current_buffer.text = ""

    @kb.add("c-e")
    async def _(event) -> None:
        """Ctrl+E: Edit current buffer in $EDITOR."""
        current_text = event.app.current_buffer.text
        try:
            # Run the synchronous editor function in a thread
            edited_text = await event.app.loop.run_in_executor(
                None, get_text_from_editor, current_text
            )
            event.app.current_buffer.text = edited_text
            # Optionally, move cursor to the end of the edited text
            event.app.current_buffer.cursor_position = len(edited_text)
        except asyncio.CancelledError:
            rich_print("[yellow]Editor interaction cancelled.[/yellow]")
        except Exception as e:
            rich_print(f"[red]Error during editor interaction: {e}[/red]")
        finally:
            # Ensure the UI is updated
            if event.app:
                event.app.invalidate()

    # Store reference to agent provider and agent name for clipboard functionality
    kb.agent_provider = agent_provider
    kb.current_agent_name = agent_name

    @kb.add("c-y")
    async def _(event) -> None:
        """Ctrl+Y: Copy last assistant response to clipboard."""
        if kb.agent_provider and kb.current_agent_name:
            try:
                # Get the agent
                if hasattr(kb.agent_provider, "_agent"):
                    agent = kb.agent_provider._agent(kb.current_agent_name)
                else:
                    agent = kb.agent_provider

                # Get message history
                if hasattr(agent, "_llm") and agent._llm and agent._llm.message_history:
                    # Find last assistant message
                    for msg in reversed(agent._llm.message_history):
                        if msg.role == "assistant":
                            content = msg.last_text()
                            import pyperclip

                            pyperclip.copy(content)
                            rich_print("\n[green]✓ Copied to clipboard[/green]")
                            return

                else:
                    pass
            except Exception:
                pass
        else:
            pass

    return kb


async def get_enhanced_input(
    agent_name: str,
    default: str = "",
    show_default: bool = False,
    show_stop_hint: bool = False,
    multiline: bool = False,
    available_agent_names: List[str] = None,
    agent_types: dict[str, AgentType] = None,
    is_human_input: bool = False,
    toolbar_color: str = "ansiblue",
    agent_provider: object = None,
) -> str:
    """
    Enhanced input with advanced prompt_toolkit features.

    Args:
        agent_name: Name of the agent (used for prompt and history)
        default: Default value if user presses enter
        show_default: Whether to show the default value in the prompt
        show_stop_hint: Whether to show the STOP hint
        multiline: Start in multiline mode
        available_agent_names: List of agent names for auto-completion
        agent_types: Dictionary mapping agent names to their types for display
        is_human_input: Whether this is a human input request (disables agent selection features)
        toolbar_color: Color to use for the agent name in the toolbar (default: "ansiblue")
        agent_provider: Optional agent provider for displaying agent info

    Returns:
        User input string
    """
    global in_multiline_mode, available_agents, help_message_shown

    # Update global state
    in_multiline_mode = multiline
    if available_agent_names:
        available_agents = set(available_agent_names)

    # Get or create history object for this agent
    if agent_name not in agent_histories:
        agent_histories[agent_name] = InMemoryHistory()

    # Define callback for multiline toggle
    def on_multiline_toggle(enabled) -> None:
        nonlocal session
        if hasattr(session, "app") and session.app:
            session.app.invalidate()

    # Define toolbar function that will update dynamically
    def get_toolbar():
        if in_multiline_mode:
            mode_style = "ansired"  # More noticeable for multiline mode
            mode_text = "MULTILINE"
            toggle_text = "Normal"
        else:
            mode_style = "ansigreen"
            mode_text = "NORMAL"
            toggle_text = "Multiline"

        shortcuts = [
            ("Ctrl+T", toggle_text),
            ("Ctrl+E", "External"),
            ("Ctrl+Y", "Copy"),
            ("Ctrl+L", "Clear"),
            ("↑/↓", "History"),
        ]

        newline = "Ctrl+&lt;Enter&gt;:Submit" if in_multiline_mode else "&lt;Enter&gt;:Submit"

        # Only show relevant shortcuts based on mode
        shortcuts = [(k, v) for k, v in shortcuts if v]

        shortcut_text = " | ".join(f"{key}:{action}" for key, action in shortcuts)

        return HTML(
            f" <style fg='{toolbar_color}' bg='ansiblack'> {agent_name} </style> Mode: <style fg='{mode_style}' bg='ansiblack'> {mode_text} </style> {newline} | {shortcut_text} | v{app_version}"
        )

    # A more terminal-agnostic style that should work across themes
    custom_style = Style.from_dict(
        {
            "completion-menu.completion": "bg:#ansiblack #ansigreen",
            "completion-menu.completion.current": "bg:#ansiblack bold #ansigreen",
            "completion-menu.meta.completion": "bg:#ansiblack #ansiblue",
            "completion-menu.meta.completion.current": "bg:#ansibrightblack #ansiblue",
            "bottom-toolbar": "#ansiblack bg:#ansigray",
        }
    )
    # Create session with history and completions
    session = PromptSession(
        history=agent_histories[agent_name],
        completer=AgentCompleter(
            agents=list(available_agents) if available_agents else [],
            agent_types=agent_types or {},
            is_human_input=is_human_input,
        ),
        complete_while_typing=True,
        multiline=Condition(lambda: in_multiline_mode),
        complete_in_thread=True,
        mouse_support=False,
        bottom_toolbar=get_toolbar,
        style=custom_style,
    )

    # Create key bindings with a reference to the app
    bindings = create_keybindings(
        on_toggle_multiline=on_multiline_toggle,
        app=session.app,
        agent_provider=agent_provider,
        agent_name=agent_name,
    )
    session.app.key_bindings = bindings

    # Create formatted prompt text
    prompt_text = f"<ansibrightblue>{agent_name}</ansibrightblue> > "

    # Add default value display if requested
    if show_default and default and default != "STOP":
        prompt_text = f"{prompt_text} [<ansigreen>{default}</ansigreen>] "

    # Only show hints at startup if requested
    if show_stop_hint:
        if default == "STOP":
            rich_print("Enter a prompt, [red]STOP[/red] to finish")
            if default:
                rich_print(f"Press <ENTER> to use the default prompt:\n[cyan]{default}[/cyan]")

    # Mention available features but only on first usage globally
    if not help_message_shown:
        if is_human_input:
            rich_print("[dim]Type /help for commands. Ctrl+T toggles multiline mode.[/dim]")
        else:
            rich_print(
                "[dim]Type '/' for commands, '@' to switch agent. Ctrl+T multiline, CTRL+E external editor.[/dim]\n"
            )

            # Display agent info right after help text if agent_provider is available
            if agent_provider and not is_human_input:
                # Display info for all available agents with tree structure for workflows
                await _display_all_agents_with_hierarchy(available_agents, agent_provider)

        rich_print()
        help_message_shown = True

    # Process special commands

    def pre_process_input(text):
        # Command processing
        if text and text.startswith("/"):
            cmd_parts = text[1:].strip().split(maxsplit=1)
            cmd = cmd_parts[0].lower()

            if cmd == "help":
                return "HELP"
            elif cmd == "clear":
                return "CLEAR"
            elif cmd == "agents":
                return "LIST_AGENTS"
            elif cmd == "usage":
                return "SHOW_USAGE"
            elif cmd == "markdown":
                return "MARKDOWN"
            elif cmd == "prompt":
                # Handle /prompt with no arguments as interactive mode
                if len(cmd_parts) > 1:
                    # Direct prompt selection with name or number
                    prompt_arg = cmd_parts[1].strip()
                    # Check if it's a number (use as index) or a name (use directly)
                    if prompt_arg.isdigit():
                        return {"select_prompt": True, "prompt_index": int(prompt_arg)}
                    else:
                        return f"SELECT_PROMPT:{prompt_arg}"
                else:
                    # If /prompt is used without arguments, show interactive selection
                    return {"select_prompt": True, "prompt_name": None}
            elif cmd == "tools":
                # Return a dictionary with list_tools action
                return {"list_tools": True}
            elif cmd == "exit":
                return "EXIT"
            elif cmd.lower() == "stop":
                return "STOP"

        # Agent switching
        if text and text.startswith("@"):
            return f"SWITCH:{text[1:].strip()}"

        # Remove the # command handling completely

        return text

    # Get the input - using async version
    try:
        result = await session.prompt_async(HTML(prompt_text), default=default)
        return pre_process_input(result)
    except KeyboardInterrupt:
        # Handle Ctrl+C gracefully
        return "STOP"
    except EOFError:
        # Handle Ctrl+D gracefully
        return "STOP"
    except Exception as e:
        # Log and gracefully handle other exceptions
        print(f"\nInput error: {type(e).__name__}: {e}")
        return "STOP"
    finally:
        # Ensure the prompt session is properly cleaned up
        # This is especially important on Windows to prevent resource leaks
        if session.app.is_running:
            session.app.exit()


async def get_selection_input(
    prompt_text: str,
    options: List[str] = None,
    default: str = None,
    allow_cancel: bool = True,
    complete_options: bool = True,
) -> Optional[str]:
    """
    Display a selection prompt and return the user's selection.

    Args:
        prompt_text: Text to display as the prompt
        options: List of valid options (for auto-completion)
        default: Default value if user presses enter
        allow_cancel: Whether to allow cancellation with empty input
        complete_options: Whether to use the options for auto-completion

    Returns:
        Selected value, or None if cancelled
    """
    try:
        # Initialize completer if options provided and completion requested
        completer = WordCompleter(options) if options and complete_options else None

        # Create prompt session
        prompt_session = PromptSession(completer=completer)

        try:
            # Get user input
            selection = await prompt_session.prompt_async(prompt_text, default=default or "")

            # Handle cancellation
            if allow_cancel and not selection.strip():
                return None

            return selection
        finally:
            # Ensure prompt session cleanup
            if prompt_session.app.is_running:
                prompt_session.app.exit()
    except (KeyboardInterrupt, EOFError):
        return None
    except Exception as e:
        rich_print(f"\n[red]Error getting selection: {e}[/red]")
        return None


async def get_argument_input(
    arg_name: str,
    description: str = None,
    required: bool = True,
) -> Optional[str]:
    """
    Prompt for an argument value with formatting and help text.

    Args:
        arg_name: Name of the argument
        description: Optional description of the argument
        required: Whether this argument is required

    Returns:
        Input value, or None if cancelled/skipped
    """
    # Format the prompt differently based on whether it's required
    required_text = "(required)" if required else "(optional, press Enter to skip)"

    # Show description if available
    if description:
        rich_print(f"  [dim]{arg_name}: {description}[/dim]")

    prompt_text = HTML(
        f"Enter value for <ansibrightcyan>{arg_name}</ansibrightcyan> {required_text}: "
    )

    # Create prompt session
    prompt_session = PromptSession()

    try:
        # Get user input
        arg_value = await prompt_session.prompt_async(prompt_text)

        # For optional arguments, empty input means skip
        if not required and not arg_value:
            return None

        return arg_value
    except (KeyboardInterrupt, EOFError):
        return None
    except Exception as e:
        rich_print(f"\n[red]Error getting input: {e}[/red]")
        return None
    finally:
        # Ensure prompt session cleanup
        if prompt_session.app.is_running:
            prompt_session.app.exit()


async def handle_special_commands(command, agent_app=None):
    """
    Handle special input commands.

    Args:
        command: The command to handle, can be string or dictionary
        agent_app: Optional agent app reference

    Returns:
        True if command was handled, False if not, or a dict with action info
    """
    # Quick guard for empty or None commands
    if not command:
        return False

    # If command is already a dictionary, it has been pre-processed
    # Just return it directly (like when /prompts converts to select_prompt dict)
    if isinstance(command, dict):
        return command

    # Check for special string commands
    if command == "HELP":
        rich_print("\n[bold]Available Commands:[/bold]")
        rich_print("  /help          - Show this help")
        rich_print("  /clear         - Clear screen")
        rich_print("  /agents        - List available agents")
        rich_print("  /prompt <name> - Apply a specific prompt by name")
        rich_print("  /usage         - Show current usage statistics")
        rich_print("  /markdown      - Show last assistant message without markdown formatting")
        rich_print("  @agent_name    - Switch to agent")
        rich_print("  STOP           - Return control back to the workflow")
        rich_print("  EXIT           - Exit fast-agent, terminating any running workflows")
        rich_print("\n[bold]Keyboard Shortcuts:[/bold]")
        rich_print("  Enter          - Submit (normal mode) / New line (multiline mode)")
        rich_print("  Ctrl+Enter     - Always submit (in any mode)")
        rich_print("  Ctrl+T         - Toggle multiline mode")
        rich_print("  Ctrl+E         - Edit in external editor")
        rich_print("  Ctrl+Y         - Copy last assistant response to clipboard")
        rich_print("  Ctrl+L         - Clear input")
        rich_print("  Up/Down        - Navigate history")
        return True

    elif command == "CLEAR":
        # Clear screen (ANSI escape sequence)
        print("\033c", end="")
        return True

    elif isinstance(command, str) and command.upper() == "EXIT":
        raise PromptExitError("User requested to exit fast-agent session")

    elif command == "LIST_AGENTS":
        if available_agents:
            rich_print("\n[bold]Available Agents:[/bold]")
            for agent in sorted(available_agents):
                rich_print(f"  @{agent}")
        else:
            rich_print("[yellow]No agents available[/yellow]")
        return True

    elif command == "SHOW_USAGE":
        # Return a dictionary to signal that usage should be shown
        return {"show_usage": True}

    elif command == "MARKDOWN":
        # Return a dictionary to signal that markdown display should be shown
        return {"show_markdown": True}

    elif command == "SELECT_PROMPT" or (
        isinstance(command, str) and command.startswith("SELECT_PROMPT:")
    ):
        # Handle prompt selection UI
        if agent_app:
            # If it's a specific prompt, extract the name
            prompt_name = None
            if isinstance(command, str) and command.startswith("SELECT_PROMPT:"):
                prompt_name = command.split(":", 1)[1].strip()

            # Return a dictionary with a select_prompt action to be handled by the caller
            return {"select_prompt": True, "prompt_name": prompt_name}
        else:
            rich_print(
                "[yellow]Prompt selection is not available outside of an agent context[/yellow]"
            )
            return True

    elif isinstance(command, str) and command.startswith("SWITCH:"):
        agent_name = command.split(":", 1)[1]
        if agent_name in available_agents:
            if agent_app:
                # The parameter can be the actual agent_app or just True to enable switching
                return {"switch_agent": agent_name}
            else:
                rich_print("[yellow]Agent switching not available in this context[/yellow]")
        else:
            rich_print(f"[red]Unknown agent: {agent_name}[/red]")
        return True

    return False
