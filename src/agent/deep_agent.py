import datetime
import asyncio

from deepagents.graph import SkillsMiddleware, FilesystemMiddleware, SubAgentMiddleware, TodoListMiddleware
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
load_dotenv()

from langsmith import AsyncClient
from langchain.tools import ToolRuntime
from langchain.agents.middleware import AgentMiddleware, ModelRequest, before_model, dynamic_prompt, ModelResponse, wrap_model_call

from langchain_core.messages import SystemMessage
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
from src.models import Thread, AppContext, SolvenState, User

from src.agent_catastro.agent import subagent as catastro_subagent
from src.agent.tools import cargar_habilidad
from src.utils.tickets import get_ticket
from src.common_tools.files import solicitar_archivo

from langchain.agents.middleware import before_agent, AgentState
from langgraph.runtime import Runtime

# Import email tools
from src.agent_email.gmail_tools import gmail_tools, gmail_send_email
from src.agent_email.outlook_tools import outlook_tools


@before_agent
async def build_context(state: AgentState, runtime: Runtime):
	"""Build runtime context from config data sent by frontend"""
	# Get config from the current execution context
	config: RunnableConfig = get_config()
	
	# Extract user data from auth
	user_config = config["configurable"].get("langgraph_auth_user")
	user_data = user_config.get("user_data") if user_config else {}
	
	# Extract custom context sent from frontend (via config.metadata or config.configurable)
	metadata = config.get("metadata", {})
	
	# Thread ID comes from configurable (set by LangGraph SDK)
	thread_id = config["configurable"].get("thread_id")
	print("[build_context] Thread ID: ", thread_id)
	print("[build_context] Metadata: ", metadata)
	
	print("[build_context] Getting ticket: ", metadata.get("ticket_id"))
	runtime.context.ticket = await get_ticket(metadata.get("ticket_id"))
	print("[build_context] Ticket: ", runtime.context.ticket)
	
	# Populate user context
	runtime.context.user = User(
		id=user_data.get("id"),
		name=user_data.get("name"),
		email=user_data.get("email"),
		role=user_data.get("role"),
		company_id=user_data.get("company_id"),
	)
	runtime.context.company_id = user_data.get("company_id")
	
	# Populate thread context
	runtime.context.thread = Thread(
		id=thread_id,
		title=metadata.get("title"),
		description=metadata.get("description"),
	)

@dynamic_prompt
async def build_prompt(request: ModelRequest):
	# Reuse existing backend instead of creating a new one
	# Backend is already initialized in build_context
	runtime : Runtime[AppContext] = request.runtime
	ticket = runtime.context.ticket
	system_prompt : SystemMessage = request.system_message

	client = AsyncClient()
	base_prompt: ChatPromptTemplate = await client.pull_prompt("solven-main")
	initial_prompt = base_prompt.format(
		date=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
		name=runtime.context.user.name,
		language="español",
		role=runtime.context.user.role,
		ticket=ticket,
	)
	# SystemMessage: append BASE_AGENT_PROMPT to content_blocks
	new_content = [
		{"type": "text", "text": f"{initial_prompt}\n\n"},
		*system_prompt.content_blocks,
	]
	final_system_prompt = SystemMessage(content=new_content)
	return final_system_prompt

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

# docx_subagent = SubAgent(
# 	name="asistente_docx",
# 	description="agente encargado de redactar, editar y analizar documentos de word (.docx)",
# 	system_prompt="",
# 	model=coding_llm,
# 	tools=[
# 		cargar_habilidad,
# 	],
# 	middleware=[
# 		build_docx_prompt
# 	],
# 	state_schema=SolvenState,
# )

graph = create_deep_agent(
	model=llm,
	system_prompt="",
	backend=lambda rt: SandboxBackend(rt),
	subagents=[
		gmail_subagent,
		outlook_subagent,
		catastro_subagent,
	],
	middleware=[
		build_context,
		build_prompt,
	],
	skills=["/skills/"],
	context_schema=AppContext,
)