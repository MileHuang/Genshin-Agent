import asyncio

from agents.basic_agent import ask_openai_stream


DEMO_PROMPT = """
Plan my day from 9:00 AM to 10:00 PM. Include two focused work sessions, a
45-minute workout, grocery shopping, meals, short breaks, and one hour of free
time. Keep the schedule realistic and briefly explain any assumptions.
""".strip()


async def main():
    async for token in ask_openai_stream(DEMO_PROMPT):
        print(token, end="", flush=True)
    print()


if __name__ == "__main__":
    asyncio.run(main())
