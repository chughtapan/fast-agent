#!/usr/bin/env python3
"""Test the full auth + logout cycle"""

import asyncio

from mcp_agent.core.fastagent import FastAgent

fast = FastAgent("Logout Test")

@fast.agent(
    instruction="Test full authentication cycle",
    servers=["auth_demo"]
)
async def main():
    async with fast.run() as agent:
        print("🔐 Testing Full Auth Cycle\n")
        
        print("1. Initial tools:")
        result = await agent.send("What tools do you have?")
        print(f"   {result}\n")
        
        print("2. Authenticating as admin...")
        result = await agent.send('Use authenticate with username "admin" and password "admin123"')
        print(f"   Auth: {result.split('New tools')[0]}...\n")
        
        print("3. Tools after auth:")
        result = await agent.send("List your available tools")
        print(f"   {result}\n")
        
        print("4. Testing admin function...")
        result = await agent.send("Use list_files to show available files")
        print(f"   Files: {result}\n")
        
        print("5. Logging out...")
        result = await agent.send("Use the logout tool")
        print(f"   Logout: {result}\n")
        
        print("6. Tools after logout:")
        result = await agent.send("What tools do you have now?")
        print(f"   {result}")

if __name__ == "__main__":
    asyncio.run(main())