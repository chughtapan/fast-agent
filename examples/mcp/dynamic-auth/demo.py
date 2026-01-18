import asyncio

from mcp_agent.core.fastagent import FastAgent

# Create the application
fast = FastAgent("Auth Demo")

@fast.agent(instruction="Test the authentication system", servers=["auth_demo"])
async def main():
    async with fast.run() as agent:
        print("🔐 Authentication Demo\n")
        
        # Show initial tools
        await agent.send("What tools do you have?")
        
        # Try authentication
        await agent.send('Authenticate with username "admin" and password "admin123"')
        
        # Show tools after auth
        await agent.send("What tools are available now?")
        
        # Try protected functionality  
        await agent.send("Use list_files to show what files I can access")
        
        # Try logout
        await agent.send("Use the logout tool")
        
        # Show we're back to initial state
        await agent.send("What tools do you have now?")

if __name__ == "__main__":
    asyncio.run(main())