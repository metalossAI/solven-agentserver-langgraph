from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain.agents.middleware import dynamic_prompt, ModelRequest
from backend import S3Backend
from src.agent_escrituras_skilled.models import SkillsState
from src.models import AppContext
from typing import Any

from langchain_core.messages import AIMessage

@dynamic_prompt
async  def SkillsMiddleware(request: ModelRequest) -> str:
    """
    Middleware that loads skills frontmatter
    """

    from src.agent_customer_chat.backend import S3Backend
    current_skill = getattr(request.state, 'current_skill', None)
    backend : S3Backend = S3Backend(request.runtime)
    if current_skill:
        return backend.load_skill_content(request.state.get("current_skill"))

    frontmatters = await backend.load_skills_frontmatter(category="atención_al_cliente")

    return frontmatters

class EnsureSkillLoadedMiddleware(AgentMiddleware[AppContext]):
    """
    Middleware that ensures a skill is loaded before allowing the model to generate responses.
    If no skill is loaded, it forces the agent to call list_skills first.
    """
    state_schema = SkillsState
    context_schema = AppContext
    
    async def awrap_model_call(self, request: ModelRequest, handler):
        """Check if skill is loaded before model execution"""
        current_skill = getattr(request.state, 'current_skill', None)
        
        # Check if user is asking for document generation
        messages = getattr(request.state, 'messages', [])
        if messages:
            last_message = messages[-1].content if hasattr(messages[-1], 'content') else ""            
            if not current_skill:
                return AIMessage(
                    content="Para atender a un cliente primero debo cargar la habilidad apropiada. "
                    "Déjame ver qué información está disponible.",
                    tool_calls=[{
                        "name": "list_skills",
                        "args": {},
                        "id": "ensure_skill_loaded",
                    }]
                )
        
        return await handler(request)