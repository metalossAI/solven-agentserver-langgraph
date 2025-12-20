import os
import json
import asyncio
from datetime import datetime
from dotenv import load_dotenv
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
from langchain.agents.middleware.tool_call_limit import ToolCallLimitMiddleware

from src.llm import LLM as llm
from src.embeddings import embeddings
from src.utils import build_context_from_config

from src.agent_triage.models import InputTriageState, OutputTriageState, TriageState, TriageContext
from src.agent_triage.tools import crear_ticket, patch_ticket, listar_tickets    

from src.agent_triage.prompt import main_prompt
from src.agent_elasticsearch.agent import doc_search_agent

from src.agent_email.tools import get_composio_outlook_tools, get_composio_gmail_tools


async def run_agent(
	state : TriageState,
	config : RunnableConfig,
	context : TriageContext,
	store : BaseStore
):
	
	user_id = context.user_id
	tenant_id = context.tenant_id

	gmail_tools = get_composio_gmail_tools(user_id, "tickets")
	outlook_tools = get_composio_outlook_tools(user_id, "tickets")

	main_agent = create_agent(
		model=llm,
		tools=[gmail_tools, outlook_tools],
		state_schema=TriageState,
		context_schema=TriageContext,
	)
	
	# Create context for the agent
	agent_context = TriageContext(
		user_id=user_id,
	)

	response = await main_agent.ainvoke(
		state,
		config=config,
		context=agent_context,
	)

	return response

workflow = StateGraph(
	state_schema=TriageState,
)

workflow.add_node("run_agent", run_agent)
workflow.set_entry_point("run_agent")
workflow.add_edge("run_agent", "__end__")


graph = create_agent(
    model=llm,
    tools=[
        crear_ticket,
        patch_ticket,
        listar_tickets,
    ],
	middleware=[
		ToolCallLimitMiddleware(tool_name="crear_ticket", run_limit=1)
	],
    system_prompt=main_prompt.format(), 
    state_schema=TriageState,
    context_schema=TriageContext,
)

#workflow.compile(
#	name="triage",
#)