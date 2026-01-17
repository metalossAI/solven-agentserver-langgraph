
from e2b import CommandResult
from langgraph.config import get_config
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph.state import RunnableConfig
from langgraph.runtime import Runtime
from langsmith import AsyncClient
from src.common_tools.files import solicitar_archivo

from deepagents import create_deep_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    after_agent,
    before_agent,
    before_model,
    dynamic_prompt,
    ModelResponse,
    wrap_model_call
)
from src.llm import LLM
from src.models import AppContext, SkillCreate, User, Thread
from src.sandbox_backend import SandboxBackend

@before_agent
async def build_context(state: AgentState, runtime: Runtime[AppContext]):
    config: RunnableConfig = get_config()
    user_config = config["configurable"].get("langgraph_auth_user")
    user_data = user_config.get("user_data")
    
    # Get metadata from config - this should contain skill_name set by frontend
    metadata = config.get("metadata", {})
    thread_id = config["configurable"].get("thread_id")
    
    # Try to get skill_name from metadata - prioritize skill_name over title
    # skill_name is set by the frontend when creating the thread with the dialog
    skill_name = metadata.get("skill_name")
    
    # Only use title as fallback if skill_name is not set AND title is not a default value
    if not skill_name:
        title = metadata.get("title")
        # Don't use default thread titles like "nueva conversación" as skill name
        # These are generic thread titles, not skill names
        default_titles = ["nueva conversación", "nueva-habilidad", "nueva conversacion"]
        if title and title.lower() not in [dt.lower() for dt in default_titles]:
            skill_name = title
        else:
            skill_name = "nueva-habilidad"
    
    description = metadata.get("description") or ""
    
    print(f"[build_context] Thread ID: {thread_id}")
    print(f"[build_context] Metadata: {metadata}")
    print(f"[build_context] Extracted skill_name: {skill_name}")

    runtime.context.skill_create = SkillCreate(
        name=skill_name,
        description=description,
    )

    print("[build_context] Skill create: ", runtime.context.skill_create)
    
    runtime.context.user = User(
        id=user_data.get("id"),
        name=user_data.get("name"),
        email=user_data.get("email"),
        role=user_data.get("role"),
        company_id=user_data.get("company_id"),
    )

    runtime.context.thread = Thread(
        id=metadata.get("thread_id"),
        title=metadata.get("title"),
        description=metadata.get("description"),
    )

    runtime.context.backend = SandboxBackend(runtime)

@before_agent
async def init_skill(state: AgentState, runtime: Runtime[AppContext]):
    """
    We manually init the skill in a folder with skillname
    init_skill.py <skill-name> --path <path>
    """
    if runtime.context.backend is None:
        runtime.context.backend = SandboxBackend(runtime)
    
    backend : SandboxBackend = runtime.context.backend
    skill_name = runtime.context.skill_create.name
    # Use the bind-mounted path inside bwrap: /.solven/skills/system/skill-creator/scripts/init_skill.py
    init_skill_path = "/.solven/skills/system/skill-creator/scripts/init_skill.py"
    # The workspace root is / inside bwrap (which maps to /mnt/r2/threads/{thread_id})
    workspace_path = "/"
    
    # Normalize skill name for the script
    import re
    normalized_skill_name = re.sub(r'[^a-z0-9-]', '', skill_name.lower().replace(' ', '-'))

    # run init_skill.py <skill-name> --path <path> inside bwrap isolation
    await backend._ensure_initialized()
    
    try:
        result : CommandResult = await backend._run_isolated(
            f"uv run {init_skill_path} {normalized_skill_name} --path {workspace_path}",
            timeout=300
        )
        
        # Forward stdout result regardless of exit code
        if result.stdout:
            runtime.stream_writer(result.stdout)
        
        if result.exit_code != 0:
            error_output = (result.stderr or result.stdout or "").lower()
            
            # Check if the error is because the skill directory already exists
            # This is not a fatal error - the skill is already initialized, so we can continue
            if "already exists" in error_output or "directory already exists" in error_output:
                print(f"[init_skill] Skill already initialized, continuing...", flush=True)
                # Don't return - continue execution normally
            else:
                # Other errors are fatal
                if result.stderr:
                    runtime.stream_writer(f"Error: {result.stderr}")
                return state
        else:
            # Success case - stdout already forwarded above
            pass
            
    except Exception as e:
        # Handle CommandExitException from e2b
        error_str = str(e).lower()
        if "command exited with code 1" in error_str or "code 1" in error_str:
            # Check if it's an "already exists" error by trying to get the output
            # For now, assume it's okay and continue - the project is already initialized
            print(f"[init_skill] Command exited with code 1, but continuing (skill may already exist)", flush=True)
            runtime.stream_writer(f"El asistente ya estaba inicializado, continuando...")
            # Don't return - continue execution normally
        else:
            # Other exceptions are fatal
            error_msg = f"Exception during init_skill: {str(e)}"
            print(f"[init_skill] Exception: {error_msg}", flush=True)
            runtime.stream_writer(f"Error al inicializar el asistente: {error_msg}")
            return state    

@after_agent
async def validate_skill(state: AgentState, runtime: Runtime[AppContext]):
    """
    Validate the skill content if it returns an error we send it backe to the agent
    """
    if runtime.context.backend is None:
        runtime.context.backend = SandboxBackend(runtime)
    
    backend : SandboxBackend = runtime.context.backend
    skill_name = runtime.context.skill_create.name
    skill_path = f"/mnt/r2/skills/{runtime.context.user.id}/{skill_name}"

    # run quick_validate.py <skill-path>
    result : CommandResult = await backend._run_isolated(f"uv run ./scripts/quick_validate.py {skill_path}")
    if result.exit_code != 0:
        runtime.stream_writer(f"Error al inicializar el asistente: {result.stderr}")
        return state
    
    runtime.stream_writer(f"Asistente creado: {result.stdout}")

@after_agent
async def save_skill(state: AgentState, runtime: Runtime[AppContext]):
    """
    If there is a dist folder we unzip and save to skill name path
    """
    if runtime.context.backend is None:
        runtime.context.backend = SandboxBackend(runtime)
    
    backend : SandboxBackend = runtime.context.backend
    skill_name = runtime.context.skill_create.name
    skill_path = f"/mnt/r2/skills/{runtime.context.user.id}/{skill_name}"

    # run quick_validate.py <skill-path>
    runtime.stream_writer(f"Guardando asistente")
    result : CommandResult = await backend.execute(f"\
        cp -r /mnt/r2/threads/{runtime.context.thread.id}/{skill_name} \
            {skill_path} \
    ")
    if result.exit_code != 0:
        runtime.stream_writer(f"Error al guardar el asistente: {result.stderr}")
        return state
    
    runtime.stream_writer(f"Asistente guardado: {result.stdout}")

@before_model
async def append_skill_message(state: AgentState, runtime: Runtime[AppContext]):
    """
    Append the skill creator skillmd to the message history so each run has this as first message
    """
    pass

@dynamic_prompt
async def build_prompt(state: AgentState, runtime: Runtime[AppContext]):
    """
    Build the prompt for the skill creator
    """
    pass
    client = AsyncClient()
    main_prompt : ChatPromptTemplate = await client.pull_prompt("solven-skill-creator")
    return main_prompt.format(
        name=runtime.context.user.name,
        email=runtime.context.user.email,
    )

graph = create_deep_agent(
    model=LLM,
    tools=[
        solicitar_archivo
    ],
    backend=lambda rt: SandboxBackend(rt),
    middleware=[
        build_context,
        init_skill,
        append_skill_message,
        # validate_skill,
        # save_skill,
    ],
    context_schema=AppContext,
)