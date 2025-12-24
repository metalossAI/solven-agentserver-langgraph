from dotenv import load_dotenv
load_dotenv()
 
from langgraph.types import Command
from langgraph.graph import StateGraph
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from langgraph.graph.state import RunnableConfig

from langchain.agents import create_agent

from src.backend import get_user_s3_backend

from src.llm import LLM
from src.embeddings import embeddings

from src.models import Thread, AppContext, SolvenState, User
from src.agent_customer_chat.tools import listar_solicitudes_cliente, crear_solicitud, actualizar_solicitud

async def build_context(
	state : SolvenState,
	config : RunnableConfig,
	runtime :  Runtime[AppContext],
	store : BaseStore,
):
	user_config = config["configurable"].get("langgraph_auth_user")
	user_data = user_config.get("user_data")

	runtime.context.backend = await get_user_s3_backend(
		user_data.get("id"),
		config.get("metadata").get("thread_id")
	)

	runtime.context.user = User(
		id=user_data.get("id"),
		name=user_data.get("name"),
		email=user_data.get("email"),
		role=user_data.get("role"),
		company_id=user_data.get("company_id"),
	)

	runtime.context.thread = Thread(
		id=config.get("metadata").get("thread_id"),
		title=config.get("metadata").get("title"),
		description=config.get("metadata").get("description"),
	)

	return Command(
		goto="run_agent",
	)

async def run_agent(
	state : SolvenState,
	config : RunnableConfig,
	runtime :  Runtime[AppContext],
	store : BaseStore
):

	agent = create_agent(
        model=LLM,
		tools=[
			listar_solicitudes_cliente,
			crear_solicitud,
			actualizar_solicitud,
		],
        middleware=[
        ]
    )

	response = await agent.ainvoke(
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
	name="solven-customer-chat",
)