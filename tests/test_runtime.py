from selma.tracing import setup

setup()

import asyncio

from selma.runtime import RuntimeEnv, agent_command
from selma.tracing import tracer


@tracer.agent(name="test_runtime")
async def main():

    print("Test: What files are in the current directory?")

    # "What files are in the current directory?"

    result = await agent_command(
        "Why is the sky blue?",
        session_key="agent:main:main",
        runtime=RuntimeEnv(cwd="."),
    )
    print(result.payloads[0].text)


if __name__ == "__main__":
    asyncio.run(main())
