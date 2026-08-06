import asyncio

from agents.basic_agent import ask_kimi_stream


async def main():

    async for token in ask_kimi_stream(
        "帮我规划今天的一天"
    ):
        print(token, end="", flush=True)

if __name__ == "__main__":
    asyncio.run(main())