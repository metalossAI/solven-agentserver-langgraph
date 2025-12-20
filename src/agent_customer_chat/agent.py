from langchain.agents import create_agent
from src.llm import LLM
from src.agent_customer_chat.prompt import main_prompt


agent = create_agent(
    model=LLM,
    system_prompt=main_prompt.format(),
    tools=[]
)