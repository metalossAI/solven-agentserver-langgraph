import os
import json
from datetime import datetime
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langchain_openrouter.chat_models import ChatOpenRouter
from langsmith import AsyncClient

from src.agent_email.gmail_tools import gmail_tools
from src.agent_email.outlook_tools import outlook_tools
from src.utils.backend import get_backend

load_dotenv()
 
from langchain.agents.middleware import ModelRequest, dynamic_prompt, AgentMiddleware
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import SystemMessage, ToolMessage
from typing import Callable, Awaitable


from deepagents import SubAgentMiddleware, create_deep_agent, SubAgent
from langchain.agents.middleware.tool_call_limit import ToolCallLimitMiddleware

from src.llm import LLM as llm

from src.agent_triage.models import TriageContext
from src.agent_triage.tools import (
    gmail_tools_triage,
    outlook_tools_triage,
    seleccionar_ticket,
    crear_ticket,
    patch_ticket,
    buscar_tickets,
    leer_ticket,
    leer_acciones,
    merge_tickets,
    descartar_evento,
    gestionar_acciones,
)

from src.utils.vector_store import search
from src.backend import SolvenS3Backend


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
async def build_prompt(request: ModelRequest) -> SystemMessage:
	from src.utils.config import get_user, get_event_message_from_config

	event_message = get_event_message_from_config() or ""

	user = get_user()
	company_id = user.company_id
	
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
	triage_prompt = main_prompt.format(
		similar_tickets=similar_tickets,
	)
	# Prepend triage instructions; keep content already merged into system_message by other middleware.
	system_prompt = request.system_message
	prior_blocks = list(system_prompt.content_blocks) if system_prompt is not None else []
	new_content = [
		{"type": "text", "text": f"{triage_prompt}\n\n"},
		*prior_blocks,
	]
	return SystemMessage(content=new_content)

ticket_triage_subagent = SubAgent(
	name="asistente_ticket",
	description=(
		"Gestión de tickets de soporte: buscar, leer, crear, actualizar, fusionar tickets, "
		"gestionar acciones, descartar eventos y fijar el workspace del ticket (seleccionar_ticket)."
	),
	system_prompt="",
	model=ChatOpenRouter(
		model="x-ai/grok-4.1-fast",
		api_key=os.getenv("OPENROUTER_API_KEY"),
	),
	tools=[
		seleccionar_ticket,
		buscar_tickets,
		leer_ticket,
		leer_acciones,
		crear_ticket,
		patch_ticket,
		merge_tickets,
		descartar_evento,
		gestionar_acciones,
	],
)

email_coordinator_subagent = SubAgent(
    name="asistente_correo",
    description=(
        "Coordinador de correo electrónico: usa las cuentas Gmail y Outlook conectadas, "
        "trabaja en todas las bandejas de correo del usuario."
    ),
    system_prompt="",
    model=ChatOpenRouter(
        model="google/gemini-3-flash-preview",
        api_key=os.getenv("OPENROUTER_API_KEY"),
        model_kwargs={
            "parallel_tool_calls": False,
        }
    ),
    middleware=[
        SubAgentMiddleware(
            backend=get_backend,
            subagents=[
                SubAgent(
                    name="asistente_gmail",
                    description="agente para gestionar correo de gmail - listar, buscar, leer, descargar adjuntos, etc. de correos electrónicos",
                    system_prompt="",
                    model=llm,
                    tools=gmail_tools_triage,
                ),
                SubAgent(
                    name="asistente_outlook",
                    description="agente para gestionar correo de outlook - listar, buscar, leer, descargar adjuntos, etc. de correos electrónicos",
                    system_prompt="",
                    model=llm,
                    tools=outlook_tools_triage,
                ),
            ],
        ),
    ],
)

# Filesystem tools (read_file, ls, …) use SolvenS3Backend: thread workspace at
# {company_id}/threads/{workspace_id}. PDF/DOCX/etc. are converted via Modal/Docling
# (see src/utils/document_conversion.py and _BaseS3Backend.read).
graph = create_deep_agent(
	model=ChatOpenRouter(
		model="x-ai/grok-4.1-fast",
		api_key=os.getenv("OPENROUTER_API_KEY"),
	),
	backend=SolvenS3Backend,
	tools=[
		descartar_evento,
	],
	subagents=[
		email_coordinator_subagent,
		ticket_triage_subagent,
	],
	middleware=[
		build_prompt,
		ToolCallLimitMiddleware(run_limit=15, exit_behavior="end"),
		ForceToolCallMiddleware(),  # Forces tool calls but respects Command returns
	],
	system_prompt="",
	context_schema=TriageContext,
)