from langchain.agents.middleware import AgentMiddleware, ModelRequest
from src.models import AppContext, SolvenState
from src.sandbox_backend import SandboxBackend

class LoadSkillsMiddleware(AgentMiddleware[AppContext]):
    """
    Middleware that links skills from state into the backend workspace.
    
    Two-leg system:
    1. Tool leg: Passes the wanted skill.md at runtime (handled by cargar_habilidad tool)
    2. Skill linking: Takes skills in state [system + user] and links them into the workspace
    
    System skills are always loaded at initialization, this middleware ensures
    user-selected skills from state are also linked into the workspace.
    Maximum: All system skills + one user skill.
    """
    state_schema = SolvenState
    context_schema = AppContext
    
    async def awrap_model_call(self, request: ModelRequest, handler):
        """Link skills from state into backend workspace before model execution"""
        # Create backend directly from runtime
        runtime = getattr(request, 'runtime', None)
        if not runtime:
            return await handler(request)
        
        backend = SandboxBackend(runtime)
        
        if not isinstance(backend, SandboxBackend):
            return await handler(request)
        
        # Ensure backend is initialized
        if not getattr(backend, '_initialized', False):
            print(f"[LoadSkillsMiddleware] Backend not initialized, skipping skill linking", flush=True)
            return await handler(request)
        
        # Get skills from state
        skills_from_state = getattr(request.state, 'skills', []) or []
        
        if not skills_from_state:
            return await handler(request)
        
        try:
            # Link all skills from state to the workspace
            # This includes both system skills (already linked at init) and user skills
            # The load_skills method will handle symlinking appropriately
            await backend.load_skills(skills_from_state)
            print(f"[LoadSkillsMiddleware] Linked skills from state to workspace: {skills_from_state}", flush=True)
        except Exception as e:
            import traceback
            print(f"[LoadSkillsMiddleware] Warning: Failed to link skills from state: {e}", flush=True)
            print(f"[LoadSkillsMiddleware] Traceback: {traceback.format_exc()}", flush=True)
            # Don't fail the request if skill linking fails - continue with model call
        
        return await handler(request)

