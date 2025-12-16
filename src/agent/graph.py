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
from langgraph.store.base import BaseStore
from langgraph.graph.ui import push_ui_message

from langgraph.graph.state import RunnableConfig

from collections.abc import Callable, Sequence
from typing import Any, Optional, List, TypedDict

from langchain.agents import create_agent
from langchain.agents.middleware.context_editing import ContextEditingMiddleware, ClearToolUsesEdit
from langchain.agents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware import InterruptOnConfig, TodoListMiddleware

from deepagents import create_deep_agent
from deepagents.middleware import FilesystemMiddleware
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.subagents import SubAgent, SubAgentMiddleware
from src.backend import get_user_s3_backend

from src.llm import LLM as llm
from src.embeddings import embeddings

from src.models import AppContext, SolvenState
from src.agent.prompt import generate_prompt_template
from src.agent_elasticsearch.agent import doc_search_agent
from src.agent_email.agent import generate_gmail_subagent, generate_outlook_subagent
from src.agent_email.tools import get_composio_outlook_tools
from src.agent_catastro.agent import subagent as catastro_subagent


async def run_agent(
	state : SolvenState,
	config : RunnableConfig,
	runtime :  Runtime[AppContext],
	store : BaseStore
):
	
	# Get context from config
	user_config = config["configurable"].get("langgraph_auth_user")
	user_data = user_config.get("user_data")
	user_id = user_config.get("user_data").get("id")
	tenant_id = user_config.get("user_data").get("company_id")
	conversation_id = config.get("metadata").get("thread_id")
	thread_title = config.get("metadata").get("title")
	thread_description = config.get("metadata").get("description")

	s3_backend = await get_user_s3_backend(user_id, conversation_id)

	main_prompt = generate_prompt_template(
		name=user_data.get("name"),
		profile=f"email: {user_data.get('email')} | role: {user_data.get('role')} | company: {user_data.get('company_name')}",
		language="español",
		context_title=thread_title,
		context_description=thread_description,
	)
	
	gmail_agent = await generate_gmail_subagent(s3_backend, user_id, conversation_id)
	outlook_agent = await generate_outlook_subagent(s3_backend, user_id, conversation_id)

	main_agent = create_deep_agent(
		model=llm,
		system_prompt=main_prompt,
		subagents=[
			doc_search_agent,
			gmail_agent,
			outlook_agent,
			catastro_subagent,
		],
		store=store,
		backend=s3_backend,
		context_schema=AppContext,
	)
	
	# Create context for the agent
	agent_context = AppContext(
		user_id=user_id,
		tenant_id=tenant_id,
		thread_id=conversation_id
	)

	response = await main_agent.ainvoke(
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