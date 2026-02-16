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
from src.models import AppContext, SolvenState, Ticket

from src.agent_customer_chat.prompt import main_prompt

from src.agent_customer_chat.backend import S3Backend
from src.agent_customer_chat.tools import listar_solicitudes_cliente, crear_solicitud, actualizar_solicitud
from src.common_tools.files import solicitar_archivo

from langchain.agents.middleware import before_agent, AgentState
from langchain.messages import AIMessage
from langgraph.runtime import Runtime


@dynamic_prompt
async def build_prompt(state: AgentState, runtime: Runtime[AppContext]):

    # Get user data from config
    config: RunnableConfig = get_config()
    user_config = config["configurable"].get("langgraph_auth_user", {})
    user_data = user_config.get("user_data", {})
    
    client = AsyncClient()
    main_prompt : ChatPromptTemplate = await client.pull_prompt("solven-customer-chat")
    return main_prompt.format(
        name=user_data.get("name", "Usuario"),
        email=user_data.get("email", ""),
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
    middleware=[],
    context_schema=AppContext,
)