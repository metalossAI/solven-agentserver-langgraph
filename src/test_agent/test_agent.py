from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openrouter.chat_models import ChatOpenRouter
import os
from dotenv import load_dotenv
import time
load_dotenv()

@tool(description="Test tool that returns the number passed to it. Use for testing tool-call functionality.")
def test_tool_call(tool_number: int) -> str:
    """Return the number passed to the tool for testing."""
    return f"test {tool_number}"

agent = create_agent(
    model=ChatOpenRouter(
        model="x-ai/grok-4.1-fast",
        api_key=os.getenv("OPENROUTER_API_KEY"),
    ),
    tools=[test_tool_call],
    system_prompt="You are a helpful assistant. You will be given a number and you will need to call the test_tool_call tool with the number.",
)