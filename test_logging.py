import asyncio
import logging
from pydantic_ai import Agent

logging.basicConfig(level=logging.DEBUG)
logging.getLogger("pydantic_ai").setLevel(logging.DEBUG)

def test_tool(ctx, arg: str) -> str:
    print(f"--- TOOL CALLED: {arg} ---")
    return "ok"

agent = Agent("test", tools=[test_tool])

async def main():
    try:
        await agent.run("call test_tool with arg 'hello'")
    except Exception as e:
        print(e)

asyncio.run(main())
