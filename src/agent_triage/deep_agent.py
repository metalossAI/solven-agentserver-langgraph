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
from src.models import AppContext, SolvenState, Ticket

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
    """Load ticket if ticket_id exists in metadata"""
    config: RunnableConfig = get_config()
    metadata = config.get("metadata", {})
    ticket_id = metadata.get("ticket_id")
    if ticket_id:
        runtime.context.ticket = await get_ticket(ticket_id)
    else:
        runtime.context.ticket = None

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