from langchain.agents.middleware import AgentMiddleware, dynamic_prompt, ModelRequest
from src.agent_escrituras_skilled.models import SkillsState
from src.models import AppContext
from typing import Any
from langchain_core.messages import AIMessage

@dynamic_prompt
def inject_skill_content(request: ModelRequest) -> str:
    """
    Dynamic prompt middleware that injects loaded skill content into the system prompt.
    """
    # Access state attributes directly using getattr
    current_skill = getattr(request.state, 'current_skill', None)
    skill_content = getattr(request.state, 'skill_content', None)
    
    print(f"[inject_skill_content] Middleware called")
    print(f"[inject_skill_content] current_skill: {current_skill}")
    print(f"[inject_skill_content] skill_content exists: {bool(skill_content)}")
    
    if current_skill and skill_content:
        # Skill is loaded - inject its content
        print(f"[inject_skill_content] ✅ Injecting skill content for: {current_skill}")
        return f"""

# Habilidad cargada: {current_skill}
{skill_content}

---

Recuerda seguir el flujo de trabajo, restricciones y pautas definidas en la habilidad anterior.
"""
    
    # No skill loaded - return empty string (system prompt handles this)
    print(f"[inject_skill_content] ⚠️ No skill loaded - returning empty string")
    return ""