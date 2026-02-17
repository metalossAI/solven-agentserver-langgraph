from dotenv import load_dotenv
from langchain.tools import ToolRuntime

from src.sandbox_backend import SandboxBackend
load_dotenv()
 
from langgraph.types import Command
from langgraph.graph import StateGraph
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from langgraph.graph.state import RunnableConfig

from deepagents import create_deep_agent
from src.backend import get_user_s3_backend, S3Backend

from src.llm import LLM as llm
from src.models import AppContext, SolvenState

from src.agent.prompt import generate_prompt_template

from src.agent_email.agent import create_gmail_subagent, create_outlook_subagent
from src.agent_catastro.agent import subagent as catastro_subagent
from src.agent.tools import cargar_habilidad
from src.utils.tickets import get_ticket
from src.common_tools.files import solicitar_archivo

async def build_context(
	state : SolvenState,
	config : RunnableConfig,
	runtime :  ToolRuntime[AppContext],
	store : BaseStore,
):
	# Load ticket if ticket_id exists in metadata
	ticket_id = config.get("metadata", {}).get("ticket_id")
	if ticket_id:
		runtime.context.ticket = await get_ticket(ticket_id)
	else:
		runtime.context.ticket = None
	
	# Extract model_name from metadata and set it in runtime context
	metadata = config.get("metadata", {})
	model_name = metadata.get("model_name")
	
	if model_name:
		runtime.context.model_name = model_name

	return Command(
		goto="run_agent",
	)

async def run_agent(
	state : SolvenState,
	config : RunnableConfig,
	runtime :  Runtime[AppContext],
	store : BaseStore
):
	# Get user data and thread data from config
	user_config = config["configurable"].get("langgraph_auth_user", {})
	user_data = user_config.get("user_data", {})
	metadata = config.get("metadata", {})
	thread_id = config["configurable"].get("thread_id")
	
	# Ensure model_name is set in runtime context from metadata (in case it wasn't set in build_context)
	model_name = metadata.get("model_name")
	if model_name:
		runtime.context.model_name = model_name
	
	# Load skills frontmatter directly from backend
	backend: SandboxBackend = SandboxBackend(runtime)
	skills_frontmatter = await backend.load_skills_frontmatter()
	
	main_prompt = await generate_prompt_template(
		name=user_data.get("name", "Usuario"),
		profile=f"email: {user_data.get('email', '')} | role: {user_data.get('role', 'usuario')}",
		language="español",
		context_title=metadata.get("title") or "Conversación general",
		context_description=metadata.get("description") or "Conversación general",
		skills=skills_frontmatter,
	)

	gmail_agent = create_gmail_subagent(runtime)
	outlook_agent = create_outlook_subagent(runtime)
	
	main_agent = create_deep_agent(
		model=llm,
		system_prompt=main_prompt,
		tools=[
			cargar_habilidad,
			solicitar_archivo
		],
		subagents=[
			gmail_agent,
			outlook_agent,
			catastro_subagent,
		],
		store=store,
		backend=backend,
		context_schema=AppContext,
	)

	response = await main_agent.ainvoke(
		state,
		config=config,
		context=runtime.context,
	)

	return response

workflow = StateGraph(
	state_schema=SolvenState,
	context_schema=AppContext,
)

workflow.add_node("build_context", build_context)
workflow.set_entry_point("build_context")
workflow.add_node("run_agent", run_agent)
workflow.add_edge("build_context", "run_agent")
workflow.add_edge("run_agent", "__end__")


graph = workflow.compile(
	name="solven",
)