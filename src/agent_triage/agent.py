import os
import json
from datetime import datetime
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langchain_openrouter.chat_models import ChatOpenRouter
from langsmith import AsyncClient

from src.common.prompt import create_prompt_middleware
from src.agent_email.gmail_tools import gmail_download_attachments_list, gmail_tools
from src.agent_email.outlook_tools import outlook_download_attachments, outlook_tools
from src.utils.backend import get_backend
from src.utils.config import get_event_message_from_config, get_user

load_dotenv()
 
from langchain.agents.middleware import ModelRequest, dynamic_prompt, AgentMiddleware
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import SystemMessage, ToolMessage
from langchain.tools.tool_node import ToolCallRequest
from typing import Callable, Awaitable, Any


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


class RequireWorkspaceForTriageToolsMiddleware(AgentMiddleware):
	"""
	Blocks ticket-scoped triage tools when no workspace_id is selected.
	Allows only discovery/selection/create flow without workspace.
	"""

	ALLOWED_WITHOUT_WORKSPACE = {"crear_ticket", "seleccionar_ticket", "buscar_tickets"}

	def _get_tool_name(self, request: ToolCallRequest) -> str:
		tool_call = getattr(request, "tool_call", None)
		if isinstance(tool_call, dict):
			return str(tool_call.get("name") or "")
		return ""

	def _get_tool_call_id(self, request: ToolCallRequest) -> str:
		tool_call = getattr(request, "tool_call", None)
		if isinstance(tool_call, dict):
			return str(tool_call.get("id") or "")
		return ""

	def _workspace_selected(self, request: ToolCallRequest) -> bool:
		runtime = getattr(request, "runtime", None)
		context = getattr(runtime, "context", None) if runtime is not None else None
		if context is None:
			return False
		if isinstance(context, dict):
			return bool(context.get("workspace_id"))
		return bool(getattr(context, "workspace_id", None))

	def _guard_result(self, request: ToolCallRequest, tool_name: str) -> ToolMessage:
		return ToolMessage(
			content=(
				f"Error: No hay ticket seleccionado para ejecutar '{tool_name}'. "
				"Primero ejecuta seleccionar_ticket con el ticket_id correspondiente."
			),
			status="error",
			tool_call_id=self._get_tool_call_id(request),
			name=tool_name or "unknown_tool",
		)

	def wrap_tool_call(
		self,
		request: ToolCallRequest,
		handler: Callable[[ToolCallRequest], Any],
	) -> Any:
		tool_name = self._get_tool_name(request)
		if tool_name and tool_name not in self.ALLOWED_WITHOUT_WORKSPACE and not self._workspace_selected(request):
			return self._guard_result(request, tool_name)
		return handler(request)

	async def awrap_tool_call(
		self,
		request: ToolCallRequest,
		handler: Callable[[ToolCallRequest], Awaitable[Any]],
	) -> Any:
		tool_name = self._get_tool_name(request)
		if tool_name and tool_name not in self.ALLOWED_WITHOUT_WORKSPACE and not self._workspace_selected(request):
			return self._guard_result(request, tool_name)
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
	system_message = request.system_message
	prior_blocks = list(system_message.content_blocks) if system_message is not None else []
	new_content = [
		*prior_blocks,
		{"type": "text", "text": f"{triage_prompt}\n\n"},
	]
	return SystemMessage(content=new_content)

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
		seleccionar_ticket,
		gmail_download_attachments_list,
		outlook_download_attachments,
		buscar_tickets,
		leer_ticket,
		#leer_acciones,
		crear_ticket,
		patch_ticket,
		merge_tickets,
		descartar_evento,
		#gestionar_acciones,
	],
	subagents=[
		SubAgent(
			name="asistente_correo",
			description="Asistente para gestionar correo electrónico",
			system_prompt="",
			model=ChatOpenRouter(
				model="x-ai/grok-4.1-fast",
				api_key=os.getenv("OPENROUTER_API_KEY"),
			),
			tools=gmail_tools_triage + outlook_tools_triage,
			middleware=[
				SubAgentMiddleware(
					backend=get_backend,
					subagents=[
						SubAgent(
							name="asistente_gmail",
							description="Asistente para gestionar correo electrónico en gmail",
							system_prompt="",
							model=ChatOpenRouter(
								model="x-ai/grok-4.1-fast",
								api_key=os.getenv("OPENROUTER_API_KEY"),
							),
							tools=gmail_tools_triage,
						),
						SubAgent(
							name="asistente_outlook",
							description="Asistente para gestionar correo electrónico en outlook",
							system_prompt="",
							model=ChatOpenRouter(
								model="x-ai/grok-4.1-fast",
								api_key=os.getenv("OPENROUTER_API_KEY"),
							),
							tools=outlook_tools_triage,
						),
					],
				),
			],
		),
	],		
	middleware=[
		ForceToolCallMiddleware(),  # Forces tool calls but respects Command returns
		RequireWorkspaceForTriageToolsMiddleware(),
		build_prompt,
	],
	system_prompt="",
	context_schema=TriageContext,
)