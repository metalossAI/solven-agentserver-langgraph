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
from src.models import Thread, AppContext, SolvenState, User

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
	user_config = config["configurable"].get("langgraph_auth_user")
	user_data = user_config.get("user_data")

	# Load ticket if ticket_id exists in metadata
	ticket_id = config.get("metadata", {}).get("ticket_id")
	if ticket_id:
		runtime.context.ticket = await get_ticket(ticket_id)
	else:
		runtime.context.ticket = None

	runtime.context.user = User(
		id=user_data.get("id"),
		name=user_data.get("name"),
		email=user_data.get("email"),
		role=user_data.get("role"),
		company_id=user_data.get("company_id"),
	)
	runtime.context.company_id = user_data.get("company_id")
	runtime.context.thread = Thread(
		id=config.get("metadata").get("thread_id"),
		title=config.get("metadata").get("title"),
		description=config.get("metadata").get("description"),
	)

	runtime.context.backend = SandboxBackend(runtime)

	return Command(
		goto="run_agent",
	)

async def run_agent(
	state : SolvenState,
	config : RunnableConfig,
	runtime :  Runtime[AppContext],
	store : BaseStore
):
	
	# Load skills frontmatter directly from backend
	backend: SandboxBackend = runtime.context.backend
	skills_frontmatter = await backend.load_skills_frontmatter()
	
	main_prompt = await generate_prompt_template(
		name=runtime.context.user.name,
		profile=f"email: {runtime.context.user.email} | role: {runtime.context.user.role}",
		language="español",
		context_title=runtime.context.thread.title or "Conversación general",
		context_description=runtime.context.thread.description or "Conversación general",
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
		backend=runtime.context.backend,
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