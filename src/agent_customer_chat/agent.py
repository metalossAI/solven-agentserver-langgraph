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

from src.models import AppContext, SolvenState
from src.agent_customer_chat.tools import listar_solicitudes_cliente, crear_solicitud, actualizar_solicitud, solicitar_archivo

async def build_context(
	state : SolvenState,
	config : RunnableConfig,
	runtime :  Runtime[AppContext],
	store : BaseStore,
):
	from src.utils.config import get_user_id_from_config, get_thread_id_from_config
	user_id = get_user_id_from_config()
	thread_id = get_thread_id_from_config()
	
	runtime.context.backend = await get_user_s3_backend(
		user_id,
		thread_id
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
			solicitar_archivo,
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