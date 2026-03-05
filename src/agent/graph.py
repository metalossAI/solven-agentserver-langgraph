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
from src.middleware.tool_call_ids import UniqueToolCallIdsMiddleware
from src.utils.tickets import get_ticket
from src.common_tools.files import solicitar_archivo
from src.common_tools.files import solicitar_archivo

async def build_context(
	state : SolvenState,
	config : RunnableConfig,
	runtime :  ToolRuntime[AppContext],
	store : BaseStore,
):
	# Load ticket using thread_id (which is the ticket ID)
	thread_id = config.get("configurable", {}).get("thread_id")
	print(f"[build_context] Building context for thread_id: {thread_id}", flush=True)
	if thread_id:
		print(f"[build_context] Loading ticket for thread_id: {thread_id}", flush=True)
		runtime.context.ticket = await get_ticket(thread_id)
		if runtime.context.ticket:
			print(f"[build_context] Ticket loaded successfully: {runtime.context.ticket.id} - {runtime.context.ticket.title}", flush=True)
		else:
			print(f"[build_context] No ticket found for thread_id: {thread_id}", flush=True)
	else:
		print(f"[build_context] No thread_id provided, ticket context will be None", flush=True)
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
	from src.utils.config import get_user, get_thread_id
	user = get_user()
	metadata = config.get("metadata", {})
	thread_id = get_thread_id() or config["configurable"].get("thread_id")

	# Ensure model_name is set in runtime context from metadata (in case it wasn't set in build_context)
	model_name = metadata.get("model_name")
	if model_name:
		runtime.context.model_name = model_name

	# Load skills frontmatter directly from backend
	backend: SandboxBackend = SandboxBackend(runtime)
	skills_frontmatter = await backend.load_skills_frontmatter()

	main_prompt = await generate_prompt_template(
		name=user.name or "Usuario",
		profile=f"email: {user.email} | role: {user.role}",
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
		middleware=[UniqueToolCallIdsMiddleware()],
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