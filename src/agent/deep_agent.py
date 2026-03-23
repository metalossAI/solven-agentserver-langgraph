import datetime
import asyncio
import os

from deepagents.graph import FilesystemMiddleware, SubAgentMiddleware, TodoListMiddleware
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from dotenv import load_dotenv
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langchain_openrouter.chat_models import ChatOpenRouter

from src.common.tools import ask
from src.agent_email.agent import email_coordinator
load_dotenv()

from langchain.agents.middleware import AgentMiddleware, ModelFallbackMiddleware, ModelRequest, ModelResponse, wrap_model_call, after_agent, hook_config

from langchain_core.messages import SystemMessage, ToolMessage, AIMessage, HumanMessage
from langchain.agents import create_agent
from deepagents.middleware import FilesystemMiddleware, SubAgentMiddleware, SummarizationMiddleware
from langchain.agents.middleware import TodoListMiddleware

from src.utils.backend import get_backend

 
from langgraph.runtime import Runtime

from langgraph.config import get_config

from deepagents import CompiledSubAgent, MemoryMiddleware, create_deep_agent, SubAgent

from src.llm import LLM as llm, google_gemini
from src.llm import CODING_LLM as coding_llm
from src.models import AppContext, DeepAgentState, SolvenState

from src.agent_catastro.agent import subagent as catastro_subagent
from src.agent.middleware import ReflectionMiddleware, create_prompt_middleware
from src.utils.tickets import get_ticket
from src.common_tools.files import solicitar_archivo

from langchain.agents.middleware import before_agent, AgentState
from langgraph.runtime import Runtime
from typing import Callable, Awaitable

# Import email tools
from src.agent_email.gmail_tools import gmail_tools
from src.agent_email.outlook_tools import outlook_tools
from src.agent.middleware import SkillsMiddleware
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


# Max evaluation cycles to avoid infinite loops (e.g. model keeps replying without tool calls but we keep re-asking)
MAX_EVALUATION_CYCLES = 20

EVALUATION_PROMPT = (
	"Revisa cuidadosamente los resultados de las herramientas y evalúa si el trabajo está completo "
	"o si necesitas continuar con pasos adicionales. Responde con más llamadas a herramientas o con tu respuesta final."
)

# Metadata key used to mark our evaluation SystemMessages (avoids content-based detection)
EVALUATION_MSG_TYPE = "evaluation"


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
		from src.utils.config import get_thread_id
		thread_id = get_thread_id()
		if thread_id and getattr(runtime, "context", None) is not None:
			ctx = runtime.context
			if isinstance(ctx, dict):
				ctx["workspace_id"] = thread_id
			else:
				ctx.workspace_id = thread_id
		backend = get_backend(runtime)
		await asyncio.to_thread(backend.ensure_ready)
		if not backend.is_available():
			print("[initialize_sandbox] Sandbox not available after ensure_ready", flush=True)
	except Exception as e:
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

# Default middleware stack for nested subagents (documents + email coordinator)
gp_middleware: list[AgentMiddleware] = [
    TodoListMiddleware(),
    FilesystemMiddleware(backend=get_backend),
    SummarizationMiddleware(
            model=ChatOpenRouter(
                model="x-ai/grok-4.1-fast",
                api_key=os.getenv("OPENROUTER_API_KEY"),
            ),
            backend=get_backend,
            trigger=("fraction", 0.85),
            trim_tokens_to_summarize=None,
            truncate_args_settings=None,
    ),
    AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"),
    PatchToolCallsMiddleware(),
    OpenRouterContentMiddleware(),
]

oficial_notarial = SubAgent(
    name="oficial_notarial",
    description="asistente para trabajar en escrituras/documentos legales de todo tipo y formato.",
    system_prompt="",
    model=ChatOpenRouter(
        model="google/gemini-3-flash-preview",
        api_key=os.getenv("OPENROUTER_API_KEY"),
        model_kwargs={
            "parallel_tool_calls": False,
        }
    ),
    middleware=[
        official_notarial_prompt,
        SkillsMiddleware(
            backend=get_backend,
            sources=[USER_SKILLS_PATH],
            exclude_skills=["docx"],
        ),
    ],
)



graph = create_deep_agent(
    model=ChatOpenRouter(
        model="google/gemini-3-flash-preview",
        api_key=os.getenv("OPENROUTER_API_KEY"),
    ),
    system_prompt="",
    tools=[ask],
    backend=get_backend,
    subagents=[
        CompiledSubAgent(
            name="asistente_correo",
            description="",
            runnable=email_coordinator,
        ),
        catastro_subagent,
    ],
    middleware=[
       initialize_sandbox,
       main_prompt,
       ModelFallbackMiddleware(
            ChatOpenRouter(
                model="x-ai/grok-4.1-fast",
                api_key=os.getenv("OPENROUTER_API_KEY")
            ),
        ),
        SkillsMiddleware(
            backend=get_backend,
            sources=[USER_SKILLS_PATH],
        ),
        MemoryMiddleware(
            backend=get_backend,
            sources=[
                "/.solven/AGENTS.md"
            ],
        ),
        OpenRouterContentMiddleware(),
    ],
    context_schema=AppContext,
)

#agent = create_agent(
#    model=ChatOpenRouter(
#        model="google/gemini-3-flash-preview",
#        api_key=os.getenv("OPENROUTER_API_KEY"),
#        model_kwargs={
#            "parallel_tool_calls": False,
#        }
#    ),
#    tools=[ask],
#    system_prompt="",
#    middleware=[
#        initialize_sandbox,
#        main_prompt,
#        ReflectionMiddleware(
#            reflection_prompt=(
#                "evalua acciones previas y decide si continuar con las siguientes acciones "
#                "o si es necesario solicitar más información al usuario."
#            ),
#        ),
#        FilesystemMiddleware(
#            backend=get_backend,
#        ),
#        SkillsMiddleware(
#            backend=get_backend,
#            sources=[USER_SKILLS_PATH],
#        ),
#        ModelFallbackMiddleware(
#            ChatOpenRouter(model="x-ai/grok-4.1-fast",api_key=os.getenv("OPENROUTER_API_KEY"))
#        ),
#        SubAgentMiddleware(
#            backend=get_backend,
#            subagents=[
#                SubAgent(
#                    name="asistente_correo",
#                    description=(
#                        "Coordinador de correo electrónico: usa las cuentas Gmail y Outlook conectadas, "
#                        "trabaja en todas las bandejas de correo del usuario."
#                    ),
#                    system_prompt="",
#                    model=ChatOpenRouter(
#                        model="google/gemini-3-flash-preview",
#                        api_key=os.getenv("OPENROUTER_API_KEY"),
#                        model_kwargs={
#                            "parallel_tool_calls": False,
#                        }
#                    ),
#                    tools=[],
#                    interrupt_on={
#                        "GMAIL_SEND_EMAIL": {"allowed_decisions": ["approve", "edit", "reject"]},
#                        "OUTLOOK_SEND_EMAIL": {"allowed_decisions": ["approve", "edit", "reject"]}
#                    },
#                    middleware=[
#                        email_prompt,
#                        *gp_middleware,
#                        SubAgentMiddleware(
#                            backend=get_backend,
#                            subagents=[
#                                SubAgent(
#                                    name="asistente_gmail",
#                                    description="agente para gestionar correo de gmail - listar, leer y enviar correos electrónicos",
#                                    system_prompt="",
#                                    model=llm,
#                                    tools=gmail_tools,
#                                    interrupt_on={"GMAIL_SEND_EMAIL": {"allowed_decisions": ["approve", "edit", "reject"]}}
#                                ),
#                                SubAgent(
#                                    name="asistente_outlook",
#                                    description="agente para gestionar correo de outlook - listar, leer y enviar correos electrónicos",
#                                    system_prompt="",
#                                    model=llm,
#                                    tools=outlook_tools,
#                                    interrupt_on={"OUTLOOK_SEND_EMAIL": {"allowed_decisions": ["approve", "edit", "reject"]}}
#                                ),
#                            ],
#                        ),
#                    ],
#                ),
#                catastro_subagent,
#            ],
#        ),
#        SummarizationMiddleware(
#            model=ChatOpenRouter(
#                model="x-ai/grok-4.1-fast",
#                api_key=os.getenv("OPENROUTER_API_KEY"),
#            ),
#            backend=get_backend,
#            trigger=("fraction", 0.85),
#            trim_tokens_to_summarize=None,
#            truncate_args_settings=None,
#        ),
#        OpenRouterContentMiddleware(),
#        PatchToolCallsMiddleware(),
#    ],
#    context_schema=AppContext,
#    state_schema=DeepAgentState,
#).with_config({"recursion_limit": 1000})