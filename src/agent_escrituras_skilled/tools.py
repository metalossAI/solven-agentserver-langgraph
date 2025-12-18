from langchain_core.tools import tool
from langchain_core.messages import ToolMessage
from langchain.tools import ToolRuntime
from langgraph.types import Command
from src.models import AppContext
from src.backend import S3Backend

@tool
async def list_skills(runtime: ToolRuntime[AppContext]) -> str:
    """
    List available skills in the 'escrituras' domain.
    
    Returns:
        Formatted string with escrituras skills
    """
    backend : S3Backend = runtime.context.backend
    if not backend:
        return "Error: No backend available"
    
    # Filter to only show escrituras skills
    return await backend.load_all_skills_formatted(category='escrituras')

@tool
async def load_skill(runtime: ToolRuntime[AppContext], skill_path: str) -> Command:
    """
    Load a specific skill to use for the current task.
    This will inject the skill's instructions into the system prompt.
    
    Args:
        skill_path: Path to the skill in format 'category/skill_name' (e.g., 'escrituras/compraventa')
    
    Returns:
        Command object that updates state and returns message
    """
    print(f"[load_skill] Tool called with skill_path: {skill_path}")
    
    backend : S3Backend = runtime.context.backend
    if not backend:
        print(f"[load_skill] ❌ No backend available")
        return Command(
            update={
                "messages": [ToolMessage(
                    content="Error: No backend available",
                    tool_call_id=runtime.tool_call_id
                )]
            }
        )
    
    # Load the skill content using backend method
    print(f"[load_skill] Loading skill content from S3...")
    content = await backend.load_skill_content(skill_path)
    
    if not content:
        print(f"[load_skill] ❌ Could not load skill content")
        return Command(
            update={
                "messages": [ToolMessage(
                    content=f"Error: Could not load skill '{skill_path}'. Make sure it exists and has a SKILL.md file.",
                    tool_call_id=runtime.tool_call_id
                )]
            }
        )
    
    print(f"[load_skill] ✅ Skill content loaded ({len(content)} chars)")
    print(f"[load_skill] ✅ Returning skill instructions in ToolMessage")
    
    # Return Command to update state and inject skill instructions into conversation
    return Command(
        update={
            "messages": [ToolMessage(
                content=content,
                tool_call_id=runtime.tool_call_id
            )],
            "current_skill": skill_path
        }
    )