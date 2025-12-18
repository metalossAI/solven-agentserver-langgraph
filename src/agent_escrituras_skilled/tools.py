from langchain.tools import tool, ToolRuntime
from src.models import AppContext
from typing import Optional
from src.backend import S3Backend

@tool
async def list_skills(runtime: ToolRuntime[AppContext]) -> str:
    """
    List all available skills organized by categories and subcategories.
    
    Returns:
        Formatted string with all skills organized by category
    """
    backend : S3Backend = runtime.context.backend
    if not backend:
        return "Error: No backend available"
    
    return await backend.load_all_skills_formatted()

@tool
async def load_skill(runtime: ToolRuntime[AppContext], skill_path: str) -> str:
    """
    Load a specific skill to use for the current task.
    This will inject the skill's instructions into the system prompt.
    
    Args:
        skill_path: Path to the skill in format 'category/skill_name' (e.g., 'escrituras/compraventa')
    
    Returns:
        Success message with skill name
    """
    print(f"[load_skill] Tool called with skill_path: {skill_path}")
    
    backend : S3Backend = runtime.context.backend
    if not backend:
        print(f"[load_skill] ❌ No backend available")
        return "Error: No backend available"
    
    # Load the skill content using backend method
    print(f"[load_skill] Loading skill content from S3...")
    content = await backend.load_skill_content(skill_path)
    
    if not content:
        print(f"[load_skill] ❌ Could not load skill content")
        return f"Error: Could not load skill '{skill_path}'. Make sure it exists and has a SKILL.md file."
    
    print(f"[load_skill] ✅ Skill content loaded ({len(content)} chars)")
    
    # Update state with loaded skill
    runtime.state.current_skill = skill_path
    runtime.state.skill_content = content
    
    print(f"[load_skill] ✅ State updated - current_skill: {runtime.state.current_skill}")
    
    return f"✅ Skill '{skill_path}' loaded successfully. Now follow the skill's instructions to complete the task."