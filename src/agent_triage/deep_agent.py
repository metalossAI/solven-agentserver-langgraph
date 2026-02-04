from dotenv import load_dotenv
load_dotenv()

from langchain.tools import ToolRuntime
from langchain.agents.middleware import AgentMiddleware, ModelRequest, before_model, dynamic_prompt, ModelResponse, wrap_model_call


from src.sandbox_backend import SandboxBackend

 
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from langgraph.graph.state import RunnableConfig
from langgraph.config import get_config

from deepagents import create_deep_agent, SubAgent
from src.backend import get_user_s3_backend, S3Backend

from src.llm import LLM as llm
from src.models import Thread, AppContext, SolvenState, Ticket, User

from src.agent.prompt import generate_prompt_template

from src.agent_catastro.agent import subagent as catastro_subagent
from src.agent.tools import cargar_habilidad
from src.utils.tickets import get_ticket
from src.common_tools.files import solicitar_archivo

from langchain.agents.middleware import before_agent, AgentState, after_agent
from langchain.messages import AIMessage
from langgraph.runtime import Runtime

# Import email tools
from src.agent_email.gmail_tools import gmail_tools
from src.agent_email.outlook_tools import outlook_tools

from src.agent_triage.models import TriageState

# first identify existing ticket or new ticket.


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
    
    # Load ticket if ticket_id exists in metadata
    ticket_id = metadata.get("ticket_id")
    if ticket_id:
        runtime.context.ticket = await get_ticket(ticket_id)
    else:
        runtime.context.ticket = None
    
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

@after_agent
async def update_ticket(state: AgentState, runtime: Runtime):
    """
    Actualiza el ticket en la base de datos
    """
    pass

async def enforce_ticket_selection(state: TriageState, runtime: Runtime):
    """
    Forzar la selección de un ticket
    """
    pass

gmail_subagent = SubAgent(
    name="asistente_gmail",
    description="agente para gestionar correo de gmail - listar, leer y enviar correos electrónicos",
    system_prompt="",
    model=llm,
    tools=gmail_tools,
    state_schema=TriageState,
)

outlook_subagent = SubAgent(
    name="asistente_outlook",
    description="agente para gestionar correo de outlook - listar, leer y enviar correos electrónicos",
    system_prompt="",
    model=llm,
    tools=outlook_tools,
    state_schema=TriageState,
)

graph = create_deep_agent(
    model=llm,
    backend=lambda rt: SandboxBackend(rt),
    tools=[
        # seleccionar_ticket
        # crear_ticket,
        # patch_ticket,
        # listar_tickets,
    ],
    subagents=[
        gmail_subagent,
        outlook_subagent,
    ],
    middleware=[
        build_context,
        build_prompt,
    ],
    context_schema=AppContext,
)