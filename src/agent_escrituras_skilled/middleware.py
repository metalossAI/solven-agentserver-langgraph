from langchain.agents.middleware import AgentMiddleware, ModelRequest
from src.agent_escrituras_skilled.models import SkillsState
from src.models import AppContext
from typing import Any
from langchain_core.messages import AIMessage

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
        
        print(f"[EnsureSkillLoadedMiddleware] Checking skill status...")
        print(f"[EnsureSkillLoadedMiddleware] current_skill: {current_skill}")
        
        # Check if user is asking for document generation
        messages = getattr(request.state, 'messages', [])
        if messages:
            last_message = messages[-1].content if hasattr(messages[-1], 'content') else ""
            
            # Keywords that indicate document generation request
            doc_keywords = ['documento', 'escritura', 'contrato', 'acta', 'redacta', 'genera', 'crea']
            is_doc_request = any(keyword in str(last_message).lower() for keyword in doc_keywords)
            
            # If it's a document request and no skill is loaded, force skill loading
            if is_doc_request and not current_skill:
                print(f"[EnsureSkillLoadedMiddleware] ❌ Document request without skill - forcing list_skills")
                return AIMessage(
                    content="Para generar documentos legales, primero debo cargar la habilidad apropiada. "
                    "Déjame ver qué habilidades están disponibles.",
                    tool_calls=[{
                        "name": "list_skills",
                        "args": {},
                        "id": "ensure_skill_loaded",
                    }]
                )
        
        print(f"[EnsureSkillLoadedMiddleware] ✅ Proceeding with model call")
        return await handler(request)