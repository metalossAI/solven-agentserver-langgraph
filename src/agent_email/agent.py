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

from src.llm import LLM as llm
from src.backend import get_user_s3_backend
from src.models import SolvenState, AppContext
from src.agent_email.prompt import generate_email_prompt_template
from src.agent_email.tools import get_composio_gmail_tools, get_composio_outlook_tools

def create_outlook_subagent(runtime: Runtime[AppContext]) -> SubAgent:
	"""Sync factory function that creates Outlook subagent with runtime context"""
	from src.utils.config import get_user_id_from_config, get_thread_id_from_config
	# Load tools synchronously using asyncio.run for the async function
	# This is acceptable in factory functions called during setup
	import asyncio
	user_id = get_user_id_from_config()
	thread_id = get_thread_id_from_config()
	try:
		loop = asyncio.get_event_loop()
		if loop.is_running():
			# We're in an async context, can't use asyncio.run
			# Create a temporary event loop in a thread
			import concurrent.futures
			with concurrent.futures.ThreadPoolExecutor() as executor:
				outlook_tools = executor.submit(
					lambda: asyncio.run(asyncio.to_thread(
						get_composio_outlook_tools,
						user_id,
						thread_id
					))
				).result()
		else:
			outlook_tools = asyncio.run(asyncio.to_thread(
				get_composio_outlook_tools,
				user_id,
				thread_id
			))
	except Exception as e:
		print(f"[Outlook Subagent] Error loading tools: {e}")
		outlook_tools = []
	
	outlook_subagent = SubAgent(
		name="asistente_outlook",
		description="agente para gestionar correo de outlook - listar, leer y enviar correos electrónicos",
		system_prompt="",
		model=llm,
		tools=outlook_tools,
		state_schema=SolvenState,
	)
	return outlook_subagent

def create_gmail_subagent(runtime: Runtime[AppContext]) -> SubAgent:
	"""Sync factory function that creates Gmail subagent with runtime context"""
	from src.utils.config import get_user_id_from_config, get_thread_id_from_config
	import asyncio
	user_id = get_user_id_from_config()
	thread_id = get_thread_id_from_config()
	try:
		loop = asyncio.get_event_loop()
		if loop.is_running():
			# We're in an async context, can't use asyncio.run
			# Create a temporary event loop in a thread
			import concurrent.futures
			with concurrent.futures.ThreadPoolExecutor() as executor:
				gmail_tools = executor.submit(
					lambda: asyncio.run(asyncio.to_thread(
						get_composio_gmail_tools, 
						user_id, 
						thread_id
					))
				).result()
		else:
			gmail_tools = asyncio.run(asyncio.to_thread(
				get_composio_gmail_tools, 
				user_id, 
				thread_id
			))
	except Exception as e:
		print(f"[Gmail Subagent] Error loading tools: {e}")
		gmail_tools = []

	gmail_agent = SubAgent(
		name="asistente_gmail",
		description="agente para gestionar correo de gmail - listar, leer y enviar correos electrónicos",
		system_prompt="",
		model=llm,
		tools=gmail_tools,
		state_schema=SolvenState,
	)
	
	return gmail_agent