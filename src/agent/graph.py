import os
import json
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from numpy import tri
load_dotenv()
 
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command, interrupt
from langgraph.graph import MessagesState
from langgraph.graph import StateGraph
from langgraph.runtime import Runtime
from langgraph.graph.ui import push_ui_message

from copilotkit.langgraph import RunnableConfig, CopilotContextItem

from collections.abc import Callable, Sequence
from typing import Any, Optional, List, TypedDict

from langchain.chat_models import init_chat_model
from langchain.agents import create_agent
from langchain.agents.middleware import FilesystemFileSearchMiddleware
from langchain.agents.middleware.context_editing import ContextEditingMiddleware, ClearToolUsesEdit
from langchain.agents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware import InterruptOnConfig, TodoListMiddleware

from deepagents.middleware import FilesystemMiddleware
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.subagents import SubAgent, SubAgentMiddleware
from src.backend import get_user_s3_backend

from src.llm import LLM as llm
from src.models import SolvenContext, SolvenState
from src.agent.prompt import generate_prompt_template
from src.agent_elasticsearch.agent import doc_search_agent
from src.agent_email.agent import generate_email_subagent
from src.agent_email.tools import get_composio_outlook_tools
from src.catastro.tools import busqueda_catastro


async def run_agent(state: SolvenState, config: RunnableConfig, runtime: Runtime[SolvenContext]):
	
	# Get context from config
	user_config = config["configurable"].get("langgraph_auth_user")
	user_data = user_config.get("user_data")
	user_id = user_config.get("user_data").get("id")
	tenant_id = user_config.get("user_data").get("company_id")
	conversation_id = config.get("metadata").get("thread_id")

	s3_backend = await get_user_s3_backend(user_id, conversation_id)


	main_prompt = generate_prompt_template(
		name=user_data.get("name"),
		profile=f"email: {user_data.get('email')} | role: {user_data.get('role')} | company: {user_data.get('company_name')}",
		language="español",
	)
	
	email_agent = await generate_email_subagent(user_id, conversation_id)

	scriba_deep_agent = create_agent(
		name="scriba",
		model=llm,
		system_prompt=main_prompt,
		middleware=[
			PatchToolCallsMiddleware(),
			SubAgentMiddleware(
				general_purpose_agent=True,
				default_model=llm,
				subagents=[
					doc_search_agent,
					email_agent,
					SubAgent(
						name="asistente_busqueda_catastro",
						description="agente para gestionar busquedas en el catastro",
						system_prompt="Eres un asistente de busqueda de datos del catastro de España.",
						model=llm,
						tools=[busqueda_catastro],
						state_schema=SolvenState,
					),
					SubAgent(
						name="asistente_redactor",
						description="asistente para gestionar, leer, y redactar documentos genericos.",
						system_prompt="Eres un asistente de documentos. Puedes listar documentos, leer su contenido completo y redactar documentos.",
						model=llm,
						state_schema=SolvenState,
						middleware=[
							FilesystemMiddleware(
								system_prompt="Espacio de trabajo para crear, editar y gestionar documentos.",
								backend=s3_backend
							),
							SummarizationMiddleware(
								model=llm,
								trigger=("tokens", 30000),
								max_tokens_before_summary=10000,
								messages_to_keep=5,
							),
							ContextEditingMiddleware(
								edits=[
									ClearToolUsesEdit(
										trigger=100000,
										keep=3,
									),
								],
							),
						],
					),
				]
			),
			SummarizationMiddleware(
				model=llm,
				trigger=("tokens", 30000),
				max_tokens_before_summary=50000,
				messages_to_keep=5,
			),
			ContextEditingMiddleware(
				edits=[
					ClearToolUsesEdit(
						trigger=50000,
						keep=3,
					),
				],
			),
			TodoListMiddleware(
				tool_description="Herramienta para gestionar tareas pendientes y completadas.",
				system_prompt="Apunta siempre tareas pendientes y tacha las que esten completadas."
			),
		],
		state_schema=SolvenState,
		context_schema=SolvenContext,
	)
	
	# Create context for the agent
	agent_context = SolvenContext(
		user_id=user_id,
		tenant_id=tenant_id,
		thread_id=conversation_id
	)
	
	response = await scriba_deep_agent.ainvoke(
		state,
		config=config,
		context=agent_context,
	)

	return response

workflow = StateGraph(
	state_schema=SolvenState,
)

workflow.add_node("run_agent", run_agent)
workflow.set_entry_point("run_agent")
workflow.add_edge("run_agent", "__end__")


graph = workflow.compile(
	name="scriba",
)