from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langsmith import AsyncClient
load_dotenv()

from langchain.tools import ToolRuntime
from langchain.agents.middleware import AgentMiddleware, ModelRequest, before_model, dynamic_prompt, ModelResponse, wrap_model_call

from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from langgraph.graph.state import RunnableConfig
from langgraph.config import get_config

from deepagents import create_deep_agent, SubAgent

from src.llm import LLM as llm
from src.models import Thread, AppContext, SolvenState, Ticket, User

from src.agent_customer_chat.prompt import main_prompt

from src.agent_customer_chat.backend import S3Backend
from src.agent_customer_chat.tools import listar_solicitudes_cliente, crear_solicitud, actualizar_solicitud
from src.common_tools.files import solicitar_archivo

from langchain.agents.middleware import before_agent, AgentState
from langchain.messages import AIMessage
from langgraph.runtime import Runtime

@before_agent
async def build_context(state: AgentState, runtime: Runtime):
    
    config: RunnableConfig = get_config()
    user_config = config["configurable"].get("langgraph_auth_user")
    user_data = user_config.get("user_data")
    
    runtime.context.user = User(
        id=user_data.get("id"),
        name=user_data.get("name"),
        email=user_data.get("email"),
        role=user_data.get("role"),
        company_id=user_data.get("company_id"),
    )

    runtime.context.thread = Thread(
        id=config.get("metadata").get("thread_id"),
        title=config.get("metadata").get("title", ""),
        description=config.get("metadata").get("description", ""),
    )

@dynamic_prompt
async def build_prompt(state: AgentState, runtime: Runtime[AppContext]):

    client = AsyncClient()
    main_prompt : ChatPromptTemplate = await client.pull_prompt("solven-customer-chat")
    return main_prompt.format(
        name=runtime.context.user.name,
        email=runtime.context.user.email,
    )

graph = create_deep_agent(
    model=llm,
    backend=lambda rt: S3Backend(rt),
    tools=[
        listar_solicitudes_cliente,
        solicitar_archivo,
        actualizar_solicitud,
        crear_solicitud,
    ],
    middleware=[
        build_context,
    ],
    context_schema=AppContext,
)