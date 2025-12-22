from langchain_core.tools import tool
from langgraph.graph.state import Command
from src.workflow_skills_create.models import SkillMD

@tool
async def escribir_skill_md(skill: SkillMD) -> Command:
    """
    Escribe el skill en markdown

    Args:
        skill: SkillMD
    """
    try:
        skills_md = skill.to_markdown()
        return Command(update={"skills_md": skills_md})
    except Exception as e:
        return Command(
            goto="__end__"
        )