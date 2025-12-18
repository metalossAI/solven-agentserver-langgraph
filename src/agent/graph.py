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

from src.models import Thread, AppContext, SolvenState, User
from src.agent.prompt import generate_prompt_template
from src.agent_elasticsearch.agent import doc_search_agent
from src.agent_email.agent import generate_gmail_subagent, generate_outlook_subagent
from src.agent_email.tools import get_composio_outlook_tools
from src.agent_catastro.agent import subagent as catastro_subagent
from src.agent_escrituras_skilled.agent import generate_escrituras_agent
from src.agent_escrituras_skilled.tools import list_skills, load_skill

async def build_context(
	state : SolvenState,
	config : RunnableConfig,
	runtime :  Runtime[AppContext],
	store : BaseStore,
):
	user_config = config["configurable"].get("langgraph_auth_user")
	user_data = user_config.get("user_data")

	runtime.context.backend = await get_user_s3_backend(
		user_data.get("id"),
		config.get("metadata").get("thread_id")
	)
	runtime.context.user = User(
		id=user_data.get("id"),
		name=user_data.get("name"),
		email=user_data.get("email"),
		role=user_data.get("role"),
		company_id=user_data.get("company_id"),
	)
	runtime.context.tenant_id = user_data.get("company_id")
	runtime.context.thread = Thread(
		id=config.get("metadata").get("thread_id"),
		title=config.get("metadata").get("title"),
		description=config.get("metadata").get("description"),
	)

	return Command(
		goto="run_agent",
	)

async def run_agent(
	state : SolvenState,
	config : RunnableConfig,
	runtime :  Runtime[AppContext],
	store : BaseStore
):

	main_prompt = generate_prompt_template(
		name=runtime.context.user.name,
		profile=f"email: {runtime.context.user.email} | role: {runtime.context.user.role}",
		language="español",
		context_title=runtime.context.thread.title,
		context_description=runtime.context.thread.description,
	)

	gmail_agent = await generate_gmail_subagent(runtime)
	outlook_agent = await generate_outlook_subagent(runtime)
	escrituras_agent = await generate_escrituras_agent(runtime)

	main_agent = create_deep_agent(
		model=llm,
		system_prompt=main_prompt,
		subagents=[
			doc_search_agent,
			gmail_agent,
			outlook_agent,
			catastro_subagent,
			escrituras_agent
		],
		store=store,
		backend=runtime.context.backend,
		context_schema=AppContext,
	)

	response = await main_agent.ainvoke(
		state,
		config=config,
		context=runtime.context,
	)

	return response

workflow = StateGraph(
	state_schema=SolvenState,
	context_schema=AppContext,
)

workflow.add_node("build_context", build_context)
workflow.set_entry_point("build_context")
workflow.add_node("run_agent", run_agent)
workflow.add_edge("build_context", "run_agent")
workflow.add_edge("run_agent", "__end__")


graph = workflow.compile(
	name="solven",
)