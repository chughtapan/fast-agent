#!/usr/bin/env python3
"""
Test the authentication flow to verify dynamic tool changes work correctly.

This script demonstrates:
1. Initial state: only 'authenticate' tool available
2. After authentication: 'authenticate' removed, protected tools added
3. After logout: back to initial state with only 'authenticate'

Usage:
    cd examples/mcp/dynamic-auth
    python test_auth_flow.py
"""

import asyncio

from mcp_agent.core.fastagent import FastAgent


async def test_auth_workflow():
    """Test the complete authentication workflow"""
    fast = FastAgent("Auth Test")
    
    @fast.agent(
        instruction="Test authentication and dynamic tool availability",
        servers=["auth_demo"]
    )
    async def test_agent():
        async with fast.run() as agent:
            print("🔍 Testing Authentication Workflow\n")
            
            # Step 1: Check initial tools
            print("Step 1: Checking initial tool availability...")
            tools_dict = await agent.list_mcp_tools()
            auth_tools = tools_dict.get("auth_demo", [])
            tool_names = [tool.name for tool in auth_tools]
            print(f"Available tools: {tool_names}")
            
            assert "authenticate" in tool_names, "authenticate tool should be available initially"
            assert len(tool_names) == 1, f"Expected only 1 tool initially, got {len(tool_names)}"
            print("✅ Initial state correct: only 'authenticate' tool available\n")
            
            # Step 2: Test failed authentication
            print("Step 2: Testing failed authentication...")
            result = await agent.send(
                '***CALL_TOOL authenticate {"username": "invalid", "password": "wrong"}'
            )
            print(f"Failed auth response: {result}")
            assert "failed" in result.lower(), "Should indicate authentication failure"
            print("✅ Failed authentication handled correctly\n")
            
            # Step 3: Test successful authentication as admin
            print("Step 3: Testing successful admin authentication...")
            result = await agent.send(
                '***CALL_TOOL authenticate {"username": "admin", "password": "admin123"}'
            )
            print(f"Auth response: {result}")
            assert "successful" in result.lower(), "Should indicate successful authentication"
            
            # Wait for tool list to update
            await asyncio.sleep(0.5)
            
            # Check updated tools
            tools_dict = await agent.list_mcp_tools()
            auth_tools = tools_dict.get("auth_demo", [])
            tool_names = [tool.name for tool in auth_tools]
            print(f"Available tools after admin auth: {tool_names}")
            
            expected_admin_tools = {"list_files", "read_file", "get_user_info", "call_api", "logout"}
            actual_tools = set(tool_names)
            
            assert "authenticate" not in actual_tools, "authenticate tool should be removed after auth"
            assert expected_admin_tools.issubset(actual_tools), f"Missing admin tools. Expected: {expected_admin_tools}, Got: {actual_tools}"
            print("✅ Admin authentication successful: 'authenticate' removed, admin tools added\n")
            
            # Step 4: Test admin-only functionality
            print("Step 4: Testing admin-only functionality...")
            
            # Test file listing
            result = await agent.send('***CALL_TOOL list_files {}')
            print(f"File list: {result}")
            assert "secret_report.pdf" in result, "Admin should see all files including secrets"
            
            # Test file reading (admin only)
            result = await agent.send(
                '***CALL_TOOL read_file {"filename": "secret_report.pdf"}'
            )
            print(f"File read: {result}")
            assert "administrator access required" not in result.lower(), "Admin should be able to read files"
            
            # Test API call (admin only)
            result = await agent.send(
                '***CALL_TOOL call_api {"endpoint": "/analytics"}'
            )
            print(f"API call: {result}")
            assert "administrator access required" not in result.lower(), "Admin should be able to call APIs"
            print("✅ Admin functionality working correctly\n")
            
            # Step 5: Test logout
            print("Step 5: Testing logout...")
            result = await agent.send('***CALL_TOOL logout {}')
            print(f"Logout response: {result}")
            assert "successful" in result.lower(), "Should indicate successful logout"
            
            # Wait for tool list to update
            await asyncio.sleep(0.5)
            
            # Check tools after logout
            tools_dict = await agent.list_mcp_tools()
            auth_tools = tools_dict.get("auth_demo", [])
            tool_names = [tool.name for tool in auth_tools]
            print(f"Available tools after logout: {tool_names}")
            
            assert "authenticate" in tool_names, "authenticate tool should be restored after logout"
            assert len(tool_names) == 1, f"Expected only 1 tool after logout, got {len(tool_names)}"
            print("✅ Logout successful: back to initial state with only 'authenticate' tool\n")
            
            # Step 6: Test regular user authentication
            print("Step 6: Testing regular user authentication...")
            result = await agent.send(
                '***CALL_TOOL authenticate {"username": "user", "password": "user123"}'
            )
            print(f"User auth response: {result}")
            assert "successful" in result.lower(), "Should indicate successful authentication"
            
            # Wait for tool list to update
            await asyncio.sleep(0.5)
            
            # Check user tools
            tools_dict = await agent.list_mcp_tools()
            auth_tools = tools_dict.get("auth_demo", [])
            tool_names = [tool.name for tool in auth_tools]
            print(f"Available tools for regular user: {tool_names}")
            
            expected_user_tools = {"list_files", "get_user_info", "logout"}
            actual_tools = set(tool_names)
            
            assert "authenticate" not in actual_tools, "authenticate tool should be removed after user auth"
            assert expected_user_tools.issubset(actual_tools), f"Missing user tools. Expected: {expected_user_tools}, Got: {actual_tools}"
            assert "read_file" not in actual_tools, "Regular users should not have read_file access"
            assert "call_api" not in actual_tools, "Regular users should not have call_api access"
            print("✅ Regular user authentication successful: limited tools available\n")
            
            # Step 7: Test user limitations
            print("Step 7: Testing user access limitations...")
            
            # Test file listing (should filter secrets)
            result = await agent.send('***CALL_TOOL list_files {}')
            print(f"User file list: {result}")
            assert "secret_report.pdf" not in result, "Regular user should not see secret files"
            assert "document1.txt" in result, "Regular user should see regular files"
            
            print("✅ User access limitations working correctly\n")
            
            print("🎉 All authentication workflow tests passed!")
            print("\nSummary:")
            print("- ✅ Initial state: only 'authenticate' tool")
            print("- ✅ Failed authentication handled correctly")
            print("- ✅ Admin authentication: 'authenticate' removed, admin tools added")
            print("- ✅ Admin functionality works (file read, API calls)")
            print("- ✅ Logout: returns to initial state")
            print("- ✅ Regular user authentication: limited tools added")
            print("- ✅ User access restrictions enforced")
    
    await test_agent()


if __name__ == "__main__":
    asyncio.run(test_auth_workflow())