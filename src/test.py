from langgraph_sdk import get_sync_client

client = get_sync_client(
    url="https://solven-production-68c142f4e56b5bb9abc3d93eaabfc616.eu.langgraph.app",
    api_key=""
)

for chunk in client.runs.stream(
    None,  # Threadless run
    "solven", # Name of assistant. Defined in langgraph.json.
    input={
        "messages": [{
            "role": "human",
            "content": "What is LangGraph?",
        }],
    },
    stream_mode="updates",
):
    print(f"Receiving new event of type: {chunk.event}...")
    print(chunk.data)
    print("\n\n")