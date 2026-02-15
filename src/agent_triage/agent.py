import os
import json
from datetime import datetime
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langsmith import AsyncClient
load_dotenv()
 
from langchain.agents.middleware import ModelRequest, dynamic_prompt, AgentMiddleware, before_agent, AgentState
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from typing import Callable, Awaitable, Any, Optional, List, TypedDict
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command, interrupt
from langgraph.graph import MessagesState
from langgraph.graph import StateGraph
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from langgraph.graph.ui import push_ui_message

from langgraph.graph.state import RunnableConfig
from langgraph.config import get_config

from collections.abc import Sequence

from deepagents import create_deep_agent, SubAgent
from langchain.agents.middleware.tool_call_limit import ToolCallLimitMiddleware

from src.llm import LLM as llm

from src.agent_triage.models import InputTriageState, OutputTriageState, TriageState, TriageContext
from src.agent_triage.tools import crear_ticket, patch_ticket, buscar_tickets, leer_ticket, merge_tickets, descartar_evento
from src.utils.vector_store import search
from src.models import User, Thread
from src.utils.tickets import get_ticket

from src.agent_email.gmail_tools import gmail_tools
from src.agent_email.outlook_tools import outlook_tools

@before_agent
async def build_context(state: AgentState, runtime: Runtime):
    """Build runtime context from config data sent by frontend"""
    # Get config from the current execution context
    config: RunnableConfig = get_config()
    
    print(f"[DEBUG build_context] Full config keys: {config.keys()}")
    
    # Extract data from configurable (LangGraph SDK puts context params here)
    configurable = config.get("configurable", {})
    metadata = config.get("metadata", {})
    
    print(f"[DEBUG build_context] configurable keys: {configurable.keys()}")
    print(f"[DEBUG build_context] metadata keys: {metadata.keys()}")
    
    # Get user_data from configurable (this is where our context parameter ends up)
    user_data = configurable.get("user_data", {})
    
    print(f"[DEBUG build_context] user_data extracted: {user_data}")
    
    # Thread ID comes from configurable (set by LangGraph SDK)
    thread_id = configurable.get("thread_id")
    
    # Get ticket if ticket_id is provided
    ticket_id = metadata.get("ticket_id")
    if ticket_id:
        runtime.context.ticket = await get_ticket(ticket_id)
    
    # Populate user context - ensure all required fields are present
    if user_data and user_data.get("id"):
        runtime.context.user = User(
            id=user_data.get("id"),
            name=user_data.get("name", "Unknown"),
            email=user_data.get("email", ""),
            role=user_data.get("role", "user"),
            company_id=user_data.get("company_id", ""),
        )
        runtime.context.company_id = user_data.get("company_id", "")
        print(f"[DEBUG build_context] ✅ User created: {runtime.context.user}")
    else:
        # Fallback: try to get from configurable or metadata directly
        user_id = configurable.get("user_id") or metadata.get("user_id")
        company_id = configurable.get("company_id") or metadata.get("company_id")
        print(f"[DEBUG build_context] Fallback - user_id: {user_id}, company_id: {company_id}")
        if user_id and company_id:
            runtime.context.user = User(
                id=user_id,
                name="Unknown",
                email="",
                role="user",
                company_id=company_id,
            )
            runtime.context.company_id = company_id
            print(f"[DEBUG build_context] ✅ User created (fallback): {runtime.context.user}")
        else:
            print(f"[ERROR build_context] ❌ No user data found!")
    
    # Get event_message from configurable or metadata (for triage agent)
    event_message = configurable.get("event_message") or metadata.get("event_message", "")
    # Set event_message in context (TriageContext has this field)
    runtime.context.event_message = event_message
    
    # Populate thread context
    runtime.context.thread = Thread(
        id=thread_id,
        title=metadata.get("title"),
        description=metadata.get("description"),
    )
    
    print(f"[DEBUG build_context] Final runtime.context.user: {runtime.context.user}")
    print(f"[DEBUG build_context] Final runtime.context.company_id: {runtime.context.company_id}")

class ForceToolCallMiddleware(AgentMiddleware):
    """Middleware to force the model to always call at least one tool.
    This ensures the agent always uses tools instead of generating direct responses.
    """
    async def awrap_model_call(
        self,
        request: ModelRequest, 
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        forced_request = request.override(tool_choice="any")
        return await handler(forced_request)

@dynamic_prompt
async def build_prompt(request: ModelRequest):
    runtime = request.runtime
    context: TriageContext = runtime.context
    
    # Get event_message from context (set in build_context)
    event_message = context.event_message or ""
    
    # Search for similar tickets using the utility function
    company_id = context.company_id
    if not company_id:
        # Fallback: try to get from user
        company_id = context.user.company_id if context.user else None
    
    if company_id and event_message:
        similar_tickets = await search(
            query=event_message,
            company_id=company_id,
            k=5
        )
    else:
        similar_tickets = "No se encontró el ID de la compañía o el mensaje del evento"
    
    # Pull prompt from LangSmith
    client = AsyncClient()
    main_prompt: ChatPromptTemplate = await client.pull_prompt("solven-triage-solicitudes")
    
    # Format prompt with similar_tickets parameter
    prompt = main_prompt.format(
        similar_tickets=similar_tickets,
    )
    return prompt

gmail_subagent = SubAgent(
    name="asistente_gmail",
    description="agente para gestionar correo de gmail - listar, leer y enviar correos electrónicos",
    system_prompt="",
    model=llm,
    tools=gmail_tools,
)

outlook_subagent = SubAgent(
    name="asistente_outlook",
    description="agente para gestionar correo de outlook - listar, leer y enviar correos electrónicos",
    system_prompt="",
    model=llm,
    tools=outlook_tools,
)

ticket_triage_subagent = SubAgent(
    name="asistente_ticket",
    description="Agente para gestión de tickets. Puede buscar, leer, crear y actualizar tickets.",
    system_prompt="",
    model=llm,
    tools=[buscar_tickets, leer_ticket, crear_ticket, patch_ticket, merge_tickets, descartar_evento],
)

graph = create_deep_agent(
    model=llm,
    tools=[
        buscar_tickets,
        leer_ticket,
        crear_ticket,
        patch_ticket,
        merge_tickets,
        descartar_evento,
    ],
    subagents=[
        gmail_subagent,
        outlook_subagent,
    ],
    middleware=[
        build_context,  # Build context from config (must be first)
        ToolCallLimitMiddleware(run_limit=15, exit_behavior="end"),
        ForceToolCallMiddleware(),
        build_prompt
    ],
    system_prompt="",  # Prompt is built dynamically via @dynamic_prompt middleware
    context_schema=TriageContext,
)