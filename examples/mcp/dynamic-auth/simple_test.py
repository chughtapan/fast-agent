#!/usr/bin/env python3
"""Simple test to verify authentication workflow"""

import asyncio

from mcp_agent.core.fastagent import FastAgent

fast = FastAgent("Auth Test")

@fast.agent(
    instruction="Test authentication workflow",
    servers=["auth_demo"]
)
async def main():
    async with fast.run() as agent:
        print("🔍 Testing Authentication Workflow\n")
        
        # Step 1: Check initial tools  
        print("Step 1: Listing available tools...")
        try:
            # Try to get available tools
            result = await agent.send("What tools do you have available?")
            print(f"Tools response: {result}")
        except Exception as e:
            print(f"Error: {e}")
        
        print("\nStep 2: Testing authentication...")
        result = await agent.send('Use the authenticate tool with username "admin" and password "admin123"')
        print(f"Auth result: {result}")
        
        print("\nStep 3: Checking tools after auth...")
        result = await agent.send("What tools do you have available now?")
        print(f"Post-auth tools: {result}")

if __name__ == "__main__":
    asyncio.run(main())