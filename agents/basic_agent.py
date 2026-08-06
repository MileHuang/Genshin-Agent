from openai import AsyncOpenAI

from config.settings import (
    MOONSHOT_API_KEY,
    KIMI_MODEL,
    KIMI_BASE_URL,
    KIMI_REASONING_EFFORT,
    AGENT_SYSTEM_PROMPT
)


client = AsyncOpenAI(
    api_key=MOONSHOT_API_KEY,
    base_url=KIMI_BASE_URL
)


async def ask_kimi_stream(prompt: str):

    response = await client.chat.completions.create(
        model=KIMI_MODEL,

        reasoning_effort=KIMI_REASONING_EFFORT,

        stream=True,

        messages=[
            {
                "role": "system",
                "content": AGENT_SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    )


    async for chunk in response:

        content = chunk.choices[0].delta.content

        if content:
            yield content