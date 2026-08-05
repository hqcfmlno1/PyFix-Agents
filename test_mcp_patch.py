from pydantic_ai.mcp import MCPToolset
import asyncio

original_call_tool = MCPToolset.call_tool
async def patched_call_tool(self, name: str, arguments: dict, *args, **kwargs):
    print(f"HOOKED: {name} {arguments}")
    return await original_call_tool(self, name, arguments, *args, **kwargs)

MCPToolset.call_tool = patched_call_tool
print("Patched successfully")
