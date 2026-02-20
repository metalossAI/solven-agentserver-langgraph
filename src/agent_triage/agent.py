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
from src.agent_triage.tools import crear_ticket, patch_ticket, buscar_tickets, leer_ticket, leer_acciones, merge_tickets, descartar_evento, gestionar_acciones
from src.utils.vector_store import search
from src.utils.tickets import get_ticket

from src.agent_email.gmail_tools import gmail_tools
from src.agent_email.outlook_tools import outlook_tools

class ForceToolCallMiddleware(AgentMiddleware):
	"""
	Middleware to encourage tool usage without preventing graph termination.
	
	Strategy:
	- Forces tool calls only when there are NO tool messages yet (first turn)
	- Once tools have been called, allows natural model behavior
	- This lets tools that return Command(goto="__end__") properly terminate the graph
	"""
	async def awrap_model_call(
		self,
		request: ModelRequest, 
		handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
	) -> ModelResponse:
		messages = request.messages
		
		# Check if any tools have been called yet
		has_tool_messages = any(isinstance(msg, ToolMessage) for msg in messages)
		
		# Only force tool calls on the first turn (when no tools have been called yet)
		# This ensures the agent doesn't just chat, but allows proper termination later
		if not has_tool_messages:
			forced_request = request.override(tool_choice="required")
			return await handler(forced_request)
		
		# After the first tool call, let the model decide naturally
		# This allows Command(goto="__end__") to work properly
		return await handler(request)

@dynamic_prompt
async def build_prompt(request: ModelRequest):
	from src.utils.config import get_company_id_from_config, get_event_message_from_config
	
	# Get event_message from config
	event_message = get_event_message_from_config() or ""
	
	# Search for similar tickets using the utility function
	company_id = get_company_id_from_config()
	
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
	tools=[buscar_tickets, leer_ticket, leer_acciones, crear_ticket, patch_ticket, merge_tickets, descartar_evento, gestionar_acciones],
)

graph = create_deep_agent(
	model=llm,
	tools=[
		buscar_tickets,
		leer_ticket,
		leer_acciones,
		crear_ticket,
		patch_ticket,
		merge_tickets,
		descartar_evento,
		gestionar_acciones,
	],
	subagents=[
		gmail_subagent,
		outlook_subagent,
	],
	middleware=[
		ToolCallLimitMiddleware(run_limit=15, exit_behavior="end"),
		ForceToolCallMiddleware(),  # Forces tool calls but respects Command returns
		build_prompt
	],
	system_prompt="",  # Prompt is built dynamically via @dynamic_prompt middleware
	context_schema=TriageContext,
)