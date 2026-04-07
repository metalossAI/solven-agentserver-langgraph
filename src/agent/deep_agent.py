import datetime
import asyncio
import os

from deepagents.graph import (
    BASE_AGENT_PROMPT,
    FilesystemMiddleware,
    SubAgentMiddleware,
    TodoListMiddleware,
)

from compact_middleware import CompactionMiddleware

from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from deepagents.middleware.summarization import create_summarization_middleware
from dotenv import load_dotenv
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langchain_openrouter.chat_models import ChatOpenRouter

from src.agent_email.outlook_tools import outlook_tools
from src.agent_email.gmail_tools import gmail_tools
from src.agent_catastro.tools import buscar_inmueble_localizacion, buscar_inmueble_rc, obtener_municipios, obtener_provincias, obtener_numeros_via, obtener_vias
from src.common.tools import ask
load_dotenv()

from langchain.agents.middleware import AgentMiddleware, ModelFallbackMiddleware, ModelRequest, ModelResponse, wrap_model_call

from langchain_core.messages import ToolMessage
from langchain.agents import create_agent

from src.utils.backend import get_backend

 
from langgraph.runtime import Runtime

from langgraph.config import get_config

from deepagents import CompiledSubAgent, MemoryMiddleware, SubAgent, create_deep_agent

from src.models import AppContext, DeepAgentState

from src.common.prompt import create_prompt_middleware
from src.utils.tickets import get_ticket

from langchain.agents.middleware import before_agent, AgentState
from typing import Callable, Awaitable

from src.agent.middleware import AutoevaluationMiddleware, SkillsMiddleware
from src.utils.openrouter import OpenRouterContentMiddleware


class ToolEnforcementMiddleware(AgentMiddleware):
	"""
	Middleware to enforce tool usage when no tools have been called yet.
	
	Strategy:
	- Forces tool calls only on the first turn (when there are NO tool messages)
	- Once tools have been called, allows natural model behavior
	- This prevents the agent from just chatting without taking action
	- Allows tools that return Command(goto="__end__") to properly terminate
	"""
	async def awrap_model_call(
		self,
		request: ModelRequest,
		handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
	) -> ModelResponse:
		messages = request.messages
		
		# Check if any tools have been called yet
		has_tool_messages = any(isinstance(msg, ToolMessage) for msg in messages)
		
		# Only force tool calls on the first turn
		if not has_tool_messages:
			return await handler(request.override(tool_choice="required"))
		
		# After the first tool call, let the model decide naturally
		return await handler(request)


@before_agent
async def initialize_sandbox(state: AgentState, runtime: Runtime[AppContext]):
	"""
	Initialize the sandbox before the agent starts working.
	This ensures the sandbox is fully set up with:
	- OverlayFS workspace at /workspace; user skills bind-mounted at /workspace/.solven/skills
	- Anthropic skills (docx/pdf/xlsx/pptx) installed via npx into /.solven/skills/
	- Local escrituras skills synced into /.solven/skills/
	
	Uses asyncio.to_thread to avoid blocking the async event loop.
	"""
	try:
		# Custom stream (streamMode includes "custom") — visible in the chat UI next to the loader.
		runtime.stream_writer("Inicializando entorno de trabajo…")
		from src.utils.config import get_thread_id
		thread_id = get_thread_id()
		if thread_id and getattr(runtime, "context", None) is not None:
			ctx = runtime.context
			if isinstance(ctx, dict):
				existing = ctx.get("workspace_id")
			else:
				existing = getattr(ctx, "workspace_id", None)
			if not existing:
				if isinstance(ctx, dict):
					ctx["workspace_id"] = thread_id
				else:
					ctx.workspace_id = thread_id
		runtime.stream_writer("Preparando espacio de archivos…")
		backend = get_backend(runtime)
		await asyncio.to_thread(backend.ensure_ready)
		if not backend.is_available():
			runtime.stream_writer("Sandbox no disponible; se usará el modo limitado.")
			print("[initialize_sandbox] Sandbox not available after ensure_ready", flush=True)
		else:
			runtime.stream_writer("Entorno listo.")
	except Exception as e:
		try:
			runtime.stream_writer(f"Error al inicializar el entorno: {e!s}")
		except Exception:
			pass
		print(f"[initialize_sandbox] ✗ Error initializing sandbox: {e}", flush=True)
		import traceback
		print(f"[initialize_sandbox] Traceback:\n{traceback.format_exc()}", flush=True)
		# Don't fail the entire agent if sandbox init fails
		# The agent can still try to work, and ensure_ready will be called again later by tools
	
	return state


async def _get_solven_main_variables(request: ModelRequest) -> dict:
    """Build format variables for the solven-main prompt from request/context."""
    from src.utils.config import get_user, get_thread_id
    user = get_user()
    user_name = user.name or "Usuario"
    user_role = user.role or "usuario"
    metadata = get_config().get("metadata") or {}
    ticket_id = metadata.get("ticket_id")
    thread_id = get_thread_id() or metadata.get("thread_id")
    id_for_ticket = ticket_id if ticket_id else thread_id
    ticket = await get_ticket(id_for_ticket)
    return {
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "name": user_name,
        "language": "español",
        "role": user_role,
        "ticket": ticket,
    }


async def _get_official_notarial_variables(request: ModelRequest) -> dict:
    """Build format variables for the official-notarial prompt from request/context."""
    return {
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "name": "Usuario",
        "language": "español",
    }

def _composio_toolkit_active(entry: dict | None) -> bool:
    if not entry or not isinstance(entry, dict):
        return False
    return str(entry.get("status") or "").upper() == "ACTIVE"


async def _get_email_variables(request: ModelRequest) -> dict:
    """Build format variables for the email prompt from request/context."""
    from src.utils.config import (
        get_user,
        get_composio_email_connections,
        get_email_preferences,
    )

    try:
        user = get_user()
        name = user.name or "Usuario"
        user_email = user.email or ""
    except RuntimeError:
        name = "Usuario"
        user_email = ""

    conn = get_composio_email_connections()
    gmail = conn.get("gmail") if isinstance(conn.get("gmail"), dict) else {}
    outlook = conn.get("outlook") if isinstance(conn.get("outlook"), dict) else {}
    gmail_connected = _composio_toolkit_active(gmail)
    outlook_connected = _composio_toolkit_active(outlook)

    def _line(label: str, entry: dict, active: bool) -> str:
        if not entry:
            return f"{label}: sin datos de conexión en esta sesión (pide al usuario conectar la cuenta)."
        st = entry.get("status", "?")
        hint = entry.get("name") or entry.get("connectedAccountId") or ""
        extra = f" — {hint}" if hint else ""
        state = "conectado (ACTIVE)" if active else f"no disponible (estado: {st})"
        return f"{label}: {state}{extra}"

    connected_accounts_summary = "\n".join(
        [
            _line("Gmail", gmail, gmail_connected),
            _line("Outlook", outlook, outlook_connected),
        ]
    )

    prefs = get_email_preferences()
    email_signature = str(prefs.get("signature") or "")
    email_sign_off = str(prefs.get("sign_off") or "")
    reply_language = str(prefs.get("default_reply_language") or "").strip() or "español"
    language = reply_language

    return {
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "name": name,
        "language": language,
        "user_email": user_email,
        "gmail_connected": "sí" if gmail_connected else "no",
        "outlook_connected": "sí" if outlook_connected else "no",
        "connected_accounts_summary": connected_accounts_summary,
        "email_signature": email_signature,
        "email_sign_off": email_sign_off,
        "reply_language": reply_language,
    }

# Middleware created by factory; pass at runtime to create_agent / create_deep_agent
main_prompt = create_prompt_middleware("solven-main", _get_solven_main_variables)
official_notarial_prompt = create_prompt_middleware("solven-subagent-oficial", _get_official_notarial_variables)
email_prompt = create_prompt_middleware("solven-subagent-email", _get_email_variables)

@wrap_model_call
async def dynamic_model_router(request: ModelRequest, handler):
    """
    Middleware to dynamically route to different models based on context.
    This wraps the model call and replaces the model instance before invocation.
    """
    try:
        # Get model name from runtime context (AppContext)
        runtime_context = request.runtime.context
        model_name = runtime_context.model_name
        
        # Fallback: Also check if model_name is in config metadata
        if not model_name:
            try:
                config = get_config()
                config_model_name = config.get("metadata", {}).get("model_name")
                if config_model_name:
                    model_name = config_model_name
                    # Also set it in the context for future use
                    runtime_context.model_name = config_model_name
            except Exception:
                pass
        
        if model_name:
            dynamic_llm = ChatOpenRouter(
                model=model_name,
                api_key=os.getenv("OPENROUTER_API_KEY"),
            )
            
            # Override the model in the request
            modified_request = request.override(model=dynamic_llm)
            return await handler(modified_request)
        else:
            return await handler(request)
            
    except Exception:
        return await handler(request)

# Unified skills directory: user skills + Anthropic skills installed via npx (bind mount at /workspace/.solven/skills)
USER_SKILLS_PATH = "/.solven/skills/"

# Main model (same instance for root summarization as in deepagents create_deep_agent)
_MAIN_MODEL = ChatOpenRouter(
    model="google/gemini-3-flash-preview",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    model_kwargs={"parallel_tool_calls": False},
)

# Mirrors deepagents.graph.create_deep_agent: TodoList, Filesystem, SubAgentMiddleware, summarization,
# Anthropic cache, Patch, then user middleware; BASE_AGENT_PROMPT on system message.
graph = create_agent(
    _MAIN_MODEL,
    tools=[ask],
    system_prompt="",
    middleware=[
        initialize_sandbox,
        main_prompt,
        CompactionMiddleware(
            ChatOpenRouter(
                model="google/gemini-3-flash-preview", api_key=os.getenv("OPENROUTER_API_KEY"),),
            backend=get_backend,
        ),
        AutoevaluationMiddleware(model=_MAIN_MODEL),
        TodoListMiddleware(),
        FilesystemMiddleware(backend=get_backend),
        SubAgentMiddleware(
            backend=get_backend,
            subagents=[
                {
                    **GENERAL_PURPOSE_SUBAGENT,
                    "model": _MAIN_MODEL,
                    "tools": [ask],
                    "middleware": [
                        TodoListMiddleware(),
                        FilesystemMiddleware(backend=get_backend),
                        create_summarization_middleware(_MAIN_MODEL, get_backend),
                        AutoevaluationMiddleware(model=_MAIN_MODEL),
                        AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"),
                        PatchToolCallsMiddleware(),
                        SkillsMiddleware(
                            backend=get_backend,
                            sources=[USER_SKILLS_PATH],
                        ),
                        OpenRouterContentMiddleware(),
                    ],
                },
                SubAgent(
                    name="oficial_notarial",
                    description="Asistente para trabajar en escrituras/documentos legales de todo tipo y formato.",
                    system_prompt="",
                    model=ChatOpenRouter(
                        model="google/gemini-3-flash-preview",
                        api_key=os.getenv("OPENROUTER_API_KEY"),
                        model_kwargs={
                            "parallel_tool_calls": False,
                        }
                    ),
                    tools=[ask],
                    middleware=[
                        official_notarial_prompt,
                        TodoListMiddleware(),
                        FilesystemMiddleware(backend=get_backend),
                        create_summarization_middleware(_MAIN_MODEL, get_backend),
                        AutoevaluationMiddleware(model=_MAIN_MODEL),
                        AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"),
                        PatchToolCallsMiddleware(),
                        SkillsMiddleware(
                            backend=get_backend,
                            sources=[USER_SKILLS_PATH],
                            exclude_skills=["docx"],
                        ),
                        OpenRouterContentMiddleware(),
                    ],
                ),
                CompiledSubAgent(
                    name="asistente_correo",
                    description="",
                    runnable=create_deep_agent(
                        name="asistente_correo",
                        system_prompt="",
                        tools=[ask],
                        model=ChatOpenRouter(
                            model="google/gemini-3-flash-preview",
                            api_key=os.getenv("OPENROUTER_API_KEY"),
                        ),
                        backend=get_backend,
                        middleware=[
                            email_prompt,
                            OpenRouterContentMiddleware(),
                        ],
                        subagents=[
                            SubAgent(
                                name="asistente_gmail",
                                description="agente para gestionar correo de gmail - listar, leer y enviar correos electrónicos",
                                system_prompt="",
                                model=ChatOpenRouter(
                                    model="google/gemini-3-flash-preview",
                                    api_key=os.getenv("OPENROUTER_API_KEY"),
                                ),
                                tools=gmail_tools,
                                interrupt_on={"GMAIL_SEND_EMAIL": {"allowed_decisions": ["approve", "edit", "reject"]}}
                            ),
                            SubAgent(
                                name="asistente_outlook",
                                description="agente para gestionar correo de outlook - listar, leer y enviar correos electrónicos",
                                system_prompt="",
                                model=ChatOpenRouter(
                                    model="google/gemini-3-flash-preview",
                                    api_key=os.getenv("OPENROUTER_API_KEY"),
                                ),
                                tools=outlook_tools,
                                interrupt_on={"OUTLOOK_SEND_EMAIL": {"allowed_decisions": ["approve", "edit", "reject"]}}
                            ),
                        ],
                    ),
                ),
                SubAgent(
                    name="asistente_busqueda_catastro",
                    description="agente para gestionar busquedas en el catastro",
                    system_prompt="Eres un asistente de busqueda de datos del catastro de España.",
                    model=_MAIN_MODEL,
                    tools=[
                        ask,
                        buscar_inmueble_localizacion,   
                        buscar_inmueble_rc,
                        obtener_municipios,
                        obtener_provincias,
                        obtener_numeros_via,
                        obtener_vias
                    ],
                )
            ],
        ),
        #create_summarization_middleware(_MAIN_MODEL, get_backend),
        AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"),
        PatchToolCallsMiddleware(),
        ModelFallbackMiddleware(
            ChatOpenRouter(
                model="x-ai/grok-4.1-fast",
                api_key=os.getenv("OPENROUTER_API_KEY"),
            ),
        ),
        SkillsMiddleware(
            backend=get_backend,
            sources=[USER_SKILLS_PATH],
        ),
        MemoryMiddleware(
            backend=get_backend,
            sources=["/.solven/AGENTS.md"],
        ),
        OpenRouterContentMiddleware(),
    ],
    context_schema=AppContext,
    state_schema=DeepAgentState,
).with_config({"recursion_limit": 1000})