import datetime
import asyncio
import os

from deepagents.graph import FilesystemMiddleware, SubAgentMiddleware, TodoListMiddleware
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from dotenv import load_dotenv
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langchain_openrouter.chat_models import ChatOpenRouter
from langgraph.types import Command
load_dotenv()

from langchain_openai.chat_models import ChatOpenAI
from langchain.tools import ToolRuntime
from langchain.agents.middleware import AgentMiddleware, ModelFallbackMiddleware, ModelRequest, before_model, ModelResponse, wrap_model_call, after_agent, hook_config

from langchain_core.messages import SystemMessage, ToolMessage, AIMessage, HumanMessage
from langchain.agents import create_agent
from deepagents.middleware import FilesystemMiddleware, SubAgentMiddleware, SummarizationMiddleware
from langchain.agents.middleware import TodoListMiddleware

from src.sandbox_backend import SandboxBackend

 
from langgraph.runtime import Runtime

from langgraph.config import get_config

from deepagents import create_deep_agent, SubAgent

from src.llm import LLM as llm, google_gemini
from src.llm import CODING_LLM as coding_llm
from src.models import AppContext, SolvenState

from src.agent_catastro.agent import subagent as catastro_subagent
from src.agent.tools import load_skill
from src.agent.middleware import create_prompt_middleware
from src.middleware.tool_call_ids import UniqueToolCallIdsMiddleware
from src.utils.tickets import get_ticket
from src.common_tools.files import solicitar_archivo

from langchain.agents.middleware import before_agent, AgentState
from langgraph.runtime import Runtime
from typing import Callable, Awaitable

# Import email tools
from src.agent_email.gmail_tools import gmail_tools, gmail_send_email
from src.agent_email.outlook_tools import outlook_tools
from src.agent.custom_skills_middleware import SkillsMiddleware
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


@after_agent
@hook_config(can_jump_to=["model"])
def continuation_evaluation_middleware(state: AgentState, runtime: Runtime[AppContext]) -> dict | None:
	"""
	Middleware so the model always evaluates tool results before ending.

	- After tools run, we inject a single evaluation request and jump back to the model.
	- The model then reads all tool results and either calls more tools or ends with a final reply.
	- We only inject when there are tool results that have not yet been "answered" by a model turn
	  (so we never re-send evaluation after the model has already replied).
	- Capped by MAX_EVALUATION_CYCLES to avoid infinite loops.
	"""
	messages = state.get("messages", [])

	# 1) If the last message is an AIMessage with tool_calls, tools have not run yet — do nothing.
	if messages:
		last = messages[-1]
		if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
			return None

	def is_evaluation_msg(msg) -> bool:
		if not isinstance(msg, SystemMessage):
			return False
		meta = getattr(msg, "additional_kwargs", None) or {}
		return meta.get("type") == EVALUATION_MSG_TYPE

	# 2) Count existing evaluation prompts so we can cap cycles.
	evaluation_count = sum(1 for m in messages if is_evaluation_msg(m))
	if evaluation_count >= MAX_EVALUATION_CYCLES:
		return None

	# 3) Find the last evaluation message (if any).
	last_eval_i = -1
	for i in range(len(messages) - 1, -1, -1):
		if is_evaluation_msg(messages[i]):
			last_eval_i = i
			break

	# 4) "Unanswered" tool results = ToolMessages after the last evaluation.
	#    If the model already replied after that evaluation, the last message is AIMessage (no tool_calls),
	#    and there are no ToolMessages after the evaluation — so we won't inject again.
	after_last_eval = messages[last_eval_i + 1:] if last_eval_i >= 0 else messages
	has_unanswered_tool_results = any(isinstance(m, ToolMessage) for m in after_last_eval)

	if not has_unanswered_tool_results:
		return None

	evaluation_message = SystemMessage(
		content=EVALUATION_PROMPT,
		additional_kwargs={"type": EVALUATION_MSG_TYPE},
	)
	return {
		"messages": [evaluation_message],
		"jump_to": "model",
	}


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
		backend = SandboxBackend(runtime)
		await asyncio.to_thread(backend._ensure_initialized)
		
	except Exception as e:
		print(f"[initialize_sandbox] ✗ Error initializing sandbox: {e}", flush=True)
		import traceback
		print(f"[initialize_sandbox] Traceback:\n{traceback.format_exc()}", flush=True)
		# Don't fail the entire agent if sandbox init fails
		# The agent can still try to work, and _ensure_initialized will be called again later
	
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

# Middleware created by factory; pass at runtime to create_agent / create_deep_agent
main_prompt = create_prompt_middleware("solven-main", _get_solven_main_variables)
official_notarial_prompt = create_prompt_middleware("solven-subagent-oficial", _get_official_notarial_variables)

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

gmail_subagent = SubAgent(
                    name="asistente_gmail",
                    description="agente para gestionar correo de gmail - listar, leer y enviar correos electrónicos",
                    system_prompt="",
                    model=llm,
                    tools=gmail_tools,
                    #interrupt_on={"GMAIL_SEND_EMAIL": {"allowed_decisions": ["approve", "edit", "reject"]}}
                )

outlook_subagent = SubAgent(
    name="asistente_outlook",
    description="agente para gestionar correo de outlook - listar, leer y enviar correos electrónicos",
    system_prompt="",
    model=llm,
    tools=outlook_tools,
    #interrupt_on={"OUTLOOK_SEND_EMAIL": {"allowed_decisions": ["approve", "edit", "reject"]}}
)

# Unified skills directory: user skills + Anthropic skills installed via npx (bind mount at /workspace/.solven/skills)
USER_SKILLS_PATH = "/.solven/skills/"

oficial_notarial = SubAgent(
    name="oficial_notarial",
    description="asistente para trabajar en escrituras/documentos legales de todo tipo y formato.",
    system_prompt="",
    model=ChatOpenRouter(
        model="openai/gpt-oss-120b:nitro",
        api_key=os.getenv("OPENROUTER_API_KEY"),
        model_kwargs={
            "parallel_tool_calls": False,
        }
    ),
    middleware=[
        official_notarial_prompt,
        SkillsMiddleware(
            backend=SandboxBackend,
            sources=[USER_SKILLS_PATH],
        ),
    ],
)

graph = create_deep_agent(
    model=ChatOpenRouter(
        model="x-ai/grok-4.1-fast",
        api_key=os.getenv("OPENROUTER_API_KEY"),
    ),
    system_prompt="",
    tools=[load_skill],
    backend=lambda rt: SandboxBackend(rt),
    subagents=[
        oficial_notarial,
        gmail_subagent,
        outlook_subagent,
        catastro_subagent,
    ],
    memory=[
        "/.solven/AGENTS.md"
    ],
    middleware=[
        initialize_sandbox,
        main_prompt,
        OpenRouterContentMiddleware(),
    ],
    skills=[
        USER_SKILLS_PATH,
    ],
    context_schema=AppContext,
)

# Build general-purpose subagent with default middleware stack
gp_middleware: list[AgentMiddleware] = [
    initialize_sandbox,
    OpenRouterContentMiddleware(),
    TodoListMiddleware(),
    FilesystemMiddleware(backend=SandboxBackend),
    SummarizationMiddleware(
            model=ChatOpenRouter(
                model="x-ai/grok-4.1-fast",
                api_key=os.getenv("OPENROUTER_API_KEY"),
            ),
            backend=SandboxBackend,
            trigger=("fraction", 0.85),
            trim_tokens_to_summarize=None,
            truncate_args_settings=None,
    ),
    AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"),
    PatchToolCallsMiddleware(),
    SkillsMiddleware(backend=SandboxBackend, sources=[USER_SKILLS_PATH]),
]

agent = create_agent(
    model=ChatOpenRouter(
        model="google/gemini-3-flash-preview",
        api_key=os.getenv("OPENROUTER_API_KEY"),
    ),
    tools=[load_skill],
    system_prompt="",
    middleware=[
        initialize_sandbox,
        main_prompt,
        OpenRouterContentMiddleware(),
        ToolEnforcementMiddleware(),
        FilesystemMiddleware(
            backend=SandboxBackend,
        ),
        SkillsMiddleware(
            backend=SandboxBackend,
            sources=[USER_SKILLS_PATH],
        ),
        ModelFallbackMiddleware(
            ChatOpenRouter(model="x-ai/grok-4.1-fast",api_key=os.getenv("OPENROUTER_API_KEY")),

        ),
        SubAgentMiddleware(
            backend=SandboxBackend,
            subagents=[
                SubAgent(
                    name="oficial_notarial",
                    description="asistente para trabajar en escrituras notariales",
                    system_prompt="",
                    model=ChatOpenRouter(
                        model="openai/gpt-oss-120b:nitro",
                        api_key=os.getenv("OPENROUTER_API_KEY"),
                    ),
                    tools=[load_skill],
                    middleware=[
                        official_notarial_prompt,
                        SkillsMiddleware(
                            backend=SandboxBackend,
                            sources=[USER_SKILLS_PATH],
                            exclude_skills=["docx"],
                        ),
                    ],
                ),
                SubAgent(
                    name="asistente_gmail",
                    description="agente para gestionar correo de gmail - listar, leer y enviar correos electrónicos",
                    system_prompt="",
                    model=llm,
                    tools=gmail_tools,
                    #interrupt_on={"GMAIL_SEND_EMAIL": {"allowed_decisions": ["approve", "edit", "reject"]}}
                ),
                SubAgent(
                    name="asistente_outlook",
                    description="agente para gestionar correo de outlook - listar, leer y enviar correos electrónicos",
                    system_prompt="",
                    model=llm,
                    tools=outlook_tools,
                    #interrupt_on={"OUTLOOK_SEND_EMAIL": {"allowed_decisions": ["approve", "edit", "reject"]}}
                ),
                catastro_subagent,
            ],
        ),
        SummarizationMiddleware(
            model=ChatOpenRouter(
                model="x-ai/grok-4.1-fast",
                api_key=os.getenv("OPENROUTER_API_KEY"),
            ),
            backend=SandboxBackend,
            trigger=("fraction", 0.85),
            trim_tokens_to_summarize=None,
            truncate_args_settings=None,
        ),
        PatchToolCallsMiddleware(),
    ],
)