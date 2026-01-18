#!/usr/bin/env python3
"""
Authentication MCP Server Example

Demonstrates dynamic tool availability based on authentication state:
1. Initially only 'authenticate' tool is available
2. After successful authentication:
   - 'authenticate' tool is removed
   - Protected tools become available
3. Sends ToolListChangedNotification to update clients

Usage:
    python auth_server.py
"""

import logging

from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create the FastMCP server
app = FastMCP(name="Authentication Demo Server", instructions="This server demonstrates dynamic authentication")

# Authentication state
authenticated = False
user_data = {
    "username": None,
    "role": None,
    "files": ["document1.txt", "document2.txt", "secret_report.pdf"],
    "api_endpoints": ["/users", "/data", "/analytics"]
}

# Valid credentials for demo
VALID_CREDENTIALS = {
    "admin": {"password": "admin123", "role": "administrator"},
    "user": {"password": "user123", "role": "user"}
}


@app.tool(
    name="authenticate",
    description="Authenticate with username and password to access protected resources"
)
async def authenticate(username: str, password: str) -> str:
    """Authenticate user and enable protected tools"""
    global authenticated, user_data
    
    context = app.get_context()
    
    # Check credentials
    if username in VALID_CREDENTIALS and VALID_CREDENTIALS[username]["password"] == password:
        authenticated = True
        user_data["username"] = username
        user_data["role"] = VALID_CREDENTIALS[username]["role"]
        
        logger.info(f"User {username} authenticated successfully with role {user_data['role']}")
        
        # Remove the authenticate tool
        if "authenticate" in app._tool_manager._tools:
            del app._tool_manager._tools["authenticate"]
            logger.info("Removed 'authenticate' tool from available tools")
        
        # Add protected tools based on role
        if user_data["role"] == "administrator":
            # Admin gets all tools
            app.add_tool(
                list_files,
                name="list_files",
                description="List available files (admin access)"
            )
            app.add_tool(
                read_file,
                name="read_file", 
                description="Read file contents (admin access)"
            )
            app.add_tool(
                get_user_info,
                name="get_user_info",
                description="Get current user information"
            )
            app.add_tool(
                call_api,
                name="call_api",
                description="Call API endpoints (admin access)"
            )
            app.add_tool(
                logout,
                name="logout",
                description="Logout and return to authentication state"
            )
        else:
            # Regular user gets limited tools
            app.add_tool(
                list_files,
                name="list_files",
                description="List available files (user access)"
            )
            app.add_tool(
                get_user_info,
                name="get_user_info",
                description="Get current user information"
            )
            app.add_tool(
                logout,
                name="logout",
                description="Logout and return to authentication state"
            )
        
        # Notify client that tools have changed
        await context.session.send_tool_list_changed()
        logger.info("Sent ToolListChangedNotification to client")
        
        return f"Authentication successful! Welcome {username} ({user_data['role']}). New tools are now available."
    else:
        logger.warning(f"Authentication failed for username: {username}")
        return "Authentication failed. Invalid username or password."


async def list_files() -> str:
    """List available files"""
    if not authenticated:
        return "Error: Not authenticated"
    
    files = user_data["files"]
    if user_data["role"] == "user":
        # Users can only see non-secret files
        files = [f for f in files if "secret" not in f.lower()]
    
    return f"Available files for {user_data['username']} ({user_data['role']}):\n" + "\n".join(f"- {f}" for f in files)


async def read_file(filename: str) -> str:
    """Read file contents (admin only)"""
    if not authenticated:
        return "Error: Not authenticated"
    
    if user_data["role"] != "administrator":
        return "Error: Administrator access required"
    
    if filename not in user_data["files"]:
        return f"Error: File '{filename}' not found"
    
    # Simulate file reading
    return f"Contents of {filename}:\n[Simulated file content for demonstration]"


async def get_user_info() -> str:
    """Get current user information"""
    if not authenticated:
        return "Error: Not authenticated"
    
    return f"User: {user_data['username']}\nRole: {user_data['role']}\nAuthenticated: {authenticated}"


async def call_api(endpoint: str) -> str:
    """Call API endpoints (admin only)"""
    if not authenticated:
        return "Error: Not authenticated"
    
    if user_data["role"] != "administrator":
        return "Error: Administrator access required"
    
    if endpoint not in user_data["api_endpoints"]:
        return f"Error: Endpoint '{endpoint}' not available"
    
    # Simulate API call
    return f"API call to {endpoint}:\n[Simulated API response for demonstration]"


async def logout() -> str:
    """Logout and return to authentication state"""
    global authenticated, user_data
    
    context = app.get_context()
    
    if not authenticated:
        return "Already logged out"
    
    username = user_data["username"]
    logger.info(f"User {username} logging out")
    
    # Reset authentication state
    authenticated = False
    user_data["username"] = None
    user_data["role"] = None
    
    # Remove all protected tools
    protected_tools = ["list_files", "read_file", "get_user_info", "call_api", "logout"]
    for tool_name in protected_tools:
        if tool_name in app._tool_manager._tools:
            del app._tool_manager._tools[tool_name]
    
    logger.info("Removed all protected tools")
    
    # Re-add the authenticate tool
    app.add_tool(
        authenticate,
        name="authenticate",
        description="Authenticate with username and password to access protected resources"
    )
    
    # Notify client that tools have changed
    await context.session.send_tool_list_changed()
    logger.info("Sent ToolListChangedNotification after logout")
    
    return f"Logout successful for {username}. You can authenticate again to access protected resources."


if __name__ == "__main__":
    print("Starting Authentication Demo Server...")
    print("Available credentials:")
    print("  admin/admin123 (administrator)")
    print("  user/user123 (user)")
    print()
    app.run(transport="stdio")