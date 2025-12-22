from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langgraph.graph.state import Command

from src.llm import LLM_SO
from src.workflow_skills_create.models import SkillMD
from src.workflow_skills_create.tools import escribir_skill_md
from src.workflow_skills_create.prompt import prompt

agent = create_agent(
    model=LLM_SO,
    tools=[escribir_skill_md],
    system_prompt=prompt.format(),
    response_format=SkillMD
)