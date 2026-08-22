from selma.tracing import setup

setup()

import asyncio

from selma.runtime import RuntimeEnv, agent_command
from selma.tracing import tracer


@tracer.agent(name="test_skills")
async def main():
    print("Test: summarize skill")

    result = await agent_command(
        "Fass diese Seite zusammen: https://docs.openclaw.ai/pi",
        session_key="agent:main:main",
        runtime=RuntimeEnv(cwd="."),
    )
    print(result.payloads[0].text)


if __name__ == "__main__":
    asyncio.run(main())
