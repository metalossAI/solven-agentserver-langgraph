import datetime
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
load_dotenv()

from langsmith import AsyncClient
from langchain.tools import ToolRuntime
from langchain.agents.middleware import AgentMiddleware, ModelRequest, before_model, dynamic_prompt, ModelResponse, wrap_model_call


from src.sandbox_backend import SandboxBackend

 
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from langgraph.graph.state import RunnableConfig
from langgraph.config import get_config

from deepagents import create_deep_agent, SubAgent

from src.llm import LLM as llm
from src.models import Thread, AppContext, SolvenState, User

from src.agent_catastro.agent import subagent as catastro_subagent
from src.agent.tools import cargar_habilidad
from src.utils.tickets import get_ticket
from src.common_tools.files import solicitar_archivo

from langchain.agents.middleware import before_agent, AgentState
from langgraph.runtime import Runtime

# Import email tools
from src.agent_email.gmail_tools import gmail_tools
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
	
	# Initialize backend
	if not runtime.context.backend:
		runtime.context.backend = SandboxBackend(runtime)


@dynamic_prompt
async def build_prompt(request: ModelRequest):
	# Reuse existing backend instead of creating a new one
	# Backend is already initialized in build_context
	runtime : Runtime[AppContext] = request.runtime
	if not runtime.context.backend:
		runtime.context.backend = SandboxBackend(runtime)
	
	backend : SandboxBackend = runtime.context.backend
	skills_frontmatter = await backend.load_skills_frontmatter()
	
	# Safely access ticket attributes, providing defaults if ticket is None
	ticket = runtime.context.ticket

	client = AsyncClient()
	base_prompt: ChatPromptTemplate = await client.pull_prompt("solven-main-skills")
	prompt = base_prompt.format(
		date=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
		name=runtime.context.user.name,
		language="español",
		role=runtime.context.user.role,
		ticket=ticket,
		skills=skills_frontmatter,
	)
	return prompt


gmail_subagent = SubAgent(
	name="asistente_gmail",
	description="agente para gestionar correo de gmail - listar, leer y enviar correos electrónicos",
	system_prompt="",
	model=llm,
	tools=gmail_tools,
	state_schema=SolvenState,
)

outlook_subagent = SubAgent(
	name="asistente_outlook",
	description="agente para gestionar correo de outlook - listar, leer y enviar correos electrónicos",
	system_prompt="",
	model=llm,
	tools=outlook_tools,
	state_schema=SolvenState,
)

graph = create_deep_agent(
	model=llm,
	backend=lambda rt: SandboxBackend(rt),
	tools=[
		cargar_habilidad,
		solicitar_archivo,
	],
	subagents=[
		gmail_subagent,
		outlook_subagent,
		catastro_subagent,
	],
	middleware=[
		build_context,
		build_prompt,
	],
	context_schema=AppContext,
)