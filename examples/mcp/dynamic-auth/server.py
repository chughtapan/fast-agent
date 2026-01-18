#!/usr/bin/env python3
"""
Authentication MCP Server - Dynamic Tool Demo

Demonstrates dynamic tool availability:
- Initially: only 'authenticate' tool
- After auth: 'authenticate' removed, protected tools added
- After logout: back to initial state

Credentials: admin/admin123 (admin) or user/user123 (user)
"""

from mcp.server.fastmcp import FastMCP

# Create server
app = FastMCP(name="Auth Demo Server")

# State
authenticated = False
user_data = {"username": None, "role": None}

# Valid credentials  
CREDENTIALS = {
    "admin": {"password": "admin123", "role": "admin"},
    "user": {"password": "user123", "role": "user"}
}


@app.tool(name="authenticate", description="Login with username and password")
async def authenticate(username: str, password: str) -> str:
    """Authenticate and enable protected tools"""
    global authenticated, user_data
    
    # Check credentials
    if username not in CREDENTIALS or CREDENTIALS[username]["password"] != password:
        return f"❌ Authentication failed for {username}"
    
    # Update state
    authenticated = True
    user_data["username"] = username
    user_data["role"] = CREDENTIALS[username]["role"]
    
    # Remove authenticate tool
    if "authenticate" in app._tool_manager._tools:
        del app._tool_manager._tools["authenticate"]
    
    # Add protected tools based on role
    if user_data["role"] == "admin":
        app.add_tool(list_files, name="list_files", description="List files (admin)")
        app.add_tool(read_file, name="read_file", description="Read files (admin)")
        app.add_tool(get_user_info, name="get_user_info", description="Get user info")
        app.add_tool(logout, name="logout", description="Logout")
    else:
        app.add_tool(list_files, name="list_files", description="List files (user)")
        app.add_tool(get_user_info, name="get_user_info", description="Get user info")
        app.add_tool(logout, name="logout", description="Logout")
    
    # Notify client
    context = app.get_context()
    await context.session.send_tool_list_changed()
    
    return f"✅ Welcome {username} ({user_data['role']})! New tools available."


async def list_files() -> str:
    """List available files"""
    files = ["document1.txt", "document2.txt", "secret.pdf"]
    
    # Filter for regular users
    if user_data["role"] != "admin":
        files = [f for f in files if "secret" not in f]
    
    file_list = "\n".join(f"📄 {f}" for f in files)
    return f"Files for {user_data['username']} ({user_data['role']}):\n{file_list}"


async def read_file(filename: str) -> str:
    """Read file (admin only)"""
    if user_data["role"] != "admin":
        return "❌ Admin access required"
    
    files = ["document1.txt", "document2.txt", "secret.pdf"]
    if filename not in files:
        return f"❌ File '{filename}' not found"
    
    return f"📖 Contents of {filename}:\n[File content would be here]"


async def get_user_info() -> str:
    """Get current user info"""
    return f"👤 User: {user_data['username']}\n🏷️  Role: {user_data['role']}"


async def logout() -> str:
    """Logout and return to auth state"""
    global authenticated, user_data
    
    username = user_data["username"]
    
    # Reset state
    authenticated = False
    user_data = {"username": None, "role": None}
    
    # Remove protected tools
    protected_tools = ["list_files", "read_file", "get_user_info", "logout"]
    for tool_name in protected_tools:
        if tool_name in app._tool_manager._tools:
            del app._tool_manager._tools[tool_name]
    
    # Re-add authenticate
    app.add_tool(authenticate, name="authenticate", description="Login with username and password")
    
    # Notify client
    context = app.get_context()
    await context.session.send_tool_list_changed()
    
    return f"👋 Goodbye {username}! You can authenticate again."


if __name__ == "__main__":
    app.run()