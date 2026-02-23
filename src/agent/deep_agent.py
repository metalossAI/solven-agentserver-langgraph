import datetime
import asyncio
import os

from deepagents.graph import SkillsMiddleware, FilesystemMiddleware, SubAgentMiddleware, TodoListMiddleware
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langgraph.types import Command
load_dotenv()

from langchain_openai.chat_models import ChatOpenAI
from langsmith import AsyncClient
from langchain.tools import ToolRuntime
from langchain.agents.middleware import AgentMiddleware, ModelRequest, before_model, dynamic_prompt, ModelResponse, wrap_model_call, after_agent, hook_config

from langchain_core.messages import SystemMessage, ToolMessage, AIMessage, HumanMessage
from langchain.agents import create_agent
from deepagents.middleware import FilesystemMiddleware, SubAgentMiddleware
from langchain.agents.middleware import TodoListMiddleware

from src.sandbox_backend import SandboxBackend

 
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from langgraph.graph.state import RunnableConfig
from langgraph.config import get_config

from deepagents import create_deep_agent, SubAgent

from src.llm import LLM as llm
from src.llm import CODING_LLM as coding_llm
from src.models import AppContext, SolvenState

from src.agent_catastro.agent import subagent as catastro_subagent
from src.agent.tools import cargar_habilidad
from src.utils.tickets import get_ticket
from src.common_tools.files import solicitar_archivo

from langchain.agents.middleware import before_agent, AgentState
from langgraph.runtime import Runtime
from typing import Callable, Awaitable

# Import email tools
from src.agent_email.gmail_tools import gmail_tools, gmail_send_email
from src.agent_email.outlook_tools import outlook_tools


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


@after_agent
@hook_config(can_jump_to=["model"])
def continuation_evaluation_middleware(state: AgentState, runtime: Runtime[AppContext]) -> dict | None:
	"""
	Middleware to encourage agent to evaluate previous work and continue.
	
	Logic:
	- After tools execute and return results, sends an evaluation message
	- Agent can then decide to make more tool calls or finish
	- If agent makes more tool calls, those results will also be evaluated
	- Allows MULTIPLE evaluation cycles (after each set of tool results)
	- Only ends when agent responds without making new tool calls
	
	This enables iterative work where the agent can:
	1. Use tools → evaluate → continue with more tools → evaluate again → finish
	"""
	messages = state.get("messages", [])
	
	# Flag: Check if the last message has tool calls
	has_new_tool_calls = False
	if messages:
		last_message = messages[-1]
		if isinstance(last_message, AIMessage) and last_message.tool_calls:
			has_new_tool_calls = True
	
	# If agent made new tool calls, let them execute (don't evaluate yet)
	if has_new_tool_calls:
		return None
	
	# Find the last evaluation message index (if any)
	evaluation_content = "Revisa cuidadosamente los resultados y evalúa si el trabajo está completo o si necesitas continuar con pasos adicionales."
	last_evaluation_index = -1
	for i in range(len(messages) - 1, -1, -1):
		msg = messages[i]
		if isinstance(msg, SystemMessage) and evaluation_content in msg.content:
			last_evaluation_index = i
			break
	
	# Flag: Check if there are tool messages AFTER the last evaluation
	has_new_tool_results = False
	if last_evaluation_index >= 0:
		# Check for ToolMessages after the evaluation
		for msg in messages[last_evaluation_index + 1:]:
			if isinstance(msg, ToolMessage):
				has_new_tool_results = True
				break
	else:
		# No evaluation sent yet, check if there are any tool messages at all
		for msg in messages:
			if isinstance(msg, ToolMessage):
				has_new_tool_results = True
				break
	
	# Decision logic:
	# If there are new tool results AND no new tool calls → send evaluation
	if has_new_tool_results and not has_new_tool_calls:
		evaluation_message = SystemMessage(
			content=evaluation_content
		)
		return {
			"messages": [evaluation_message],
			"jump_to": "model"
		}
	
	# Otherwise, let it continue normally (will END if no more work)
	return None


@before_agent
async def initialize_sandbox(state: AgentState, runtime: Runtime[AppContext]):
	"""
	Initialize the sandbox before the agent starts working.
	This ensures the sandbox is fully set up with:
	- R2 mounts (workspace, skills, ticket)
	- System skills downloaded from Anthropic repo via SVN
	- Local skills synced
	
	Uses asyncio.to_thread to avoid blocking the async event loop.
	"""
	try:
		backend = SandboxBackend(runtime)
		await asyncio.to_thread(backend._ensure_initialized)
		
	except Exception as e:
		print(f"[initialize_sandbox] ✗ Error initializing sandbox: {e}", flush=True)
		import traceback
		print(f"[initialize_sandbox] Traceback:\n{traceback.format_exc()}", flush=True)
		# Don't fail the entire agent if sandbox init fails
		# The agent can still try to work, and _ensure_initialized will be called again later
	
	return state


@dynamic_prompt
async def build_prompt(request: ModelRequest):
    # Reuse existing backend instead of creating a new one
    # Backend is already initialized in build_context
    system_prompt : SystemMessage = request.system_message

    # Extract user data from config
    config: RunnableConfig = get_config()
    user_config = config["configurable"].get("langgraph_auth_user", {})
    user_data = user_config.get("user_data", {})
    user_name = user_data.get("name", "Usuario")
    user_role = user_data.get("role", "usuario")
    
    # Load ticket using thread_id (which is the ticket ID)
    thread_id = config.get("metadata", {}).get("thread_id")
    ticket = await get_ticket(thread_id)

    client = AsyncClient()
    base_prompt: ChatPromptTemplate = await client.pull_prompt("solven-main")
    initial_prompt = base_prompt.format(
        date=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        name=user_name,
        language="español",
        role=user_role,
        ticket=ticket,
    )
    # SystemMessage: append BASE_AGENT_PROMPT to content_blocks
    new_content = [
        {"type": "text", "text": f"{initial_prompt}\n\n"},
        *system_prompt.content_blocks,
    ]
    final_system_prompt = SystemMessage(content=new_content)
    return final_system_prompt

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
            dynamic_llm = ChatOpenAI(
                model=model_name,
                base_url="https://openrouter.ai/api/v1",
                api_key=os.getenv("OPENROUTER_API_KEY"),
                streaming=True,
                model_kwargs={
                    "parallel_tool_calls": False
                }
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
)

outlook_subagent = SubAgent(
    name="asistente_outlook",
    description="agente para gestionar correo de outlook - listar, leer y enviar correos electrónicos",
    system_prompt="",
    model=llm,
    tools=outlook_tools,
)

oficial_subagent = SubAgent(
    name="oficial_notarial",
    description="asistente para trabajar en escrituras notariales",
    system_prompt="",
    model=coding_llm,
    skills=[
        "/skills/",
    ],
)

graph = create_deep_agent(
    model=llm,  # Default model - will be dynamically swapped by middleware
    system_prompt="",
    backend=lambda rt: SandboxBackend(rt),
    subagents=[
        oficial_subagent,
        gmail_subagent,
        outlook_subagent,
        catastro_subagent,
    ],
    middleware=[
        initialize_sandbox,  # Initialize sandbox before agent starts (non-blocking)
        build_prompt,
        dynamic_model_router,  # Dynamically route to selected model
        ToolEnforcementMiddleware(),  # Ensure agent makes tool calls first
        #continuation_evaluation_middleware,  # Evaluate results and decide to continue (LAST)
    ],
	skills=[
        "/skills/",
    ],
    context_schema=AppContext,
)