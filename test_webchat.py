import asyncio
import json

import httpx

WEBCHAT_STREAM_URL = "http://localhost:8000/webchat/stream"


async def main():

    payload = {
        "user_id": "test-user",
        "text": "What files are in the current directory?",
        "user_name": "Test User",
    }

    print(f"You: {payload['text']}\n")
    print("Selma: ", end="", flush=True)

    async with httpx.AsyncClient() as client:
        async with client.stream("POST", WEBCHAT_STREAM_URL, json=payload, timeout=120.0) as response:
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                match event.get("type"):
                    case "tool":
                        print(f"\n🔧 {event.get('name', 'tool')}…", flush=True)
                    case "chunk":
                        print(event["text"], end="", flush=True)
                    case "done":
                        print(f"\n\nSession: {event.get('session_key', '—')}")
                    case "error":
                        print(f"\nError: {event['message']}")


if __name__ == "__main__":
    asyncio.run(main())
