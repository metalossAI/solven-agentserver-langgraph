import os
import json
import asyncio
from datetime import datetime
from deepagents import create_deep_agent
from dotenv import load_dotenv
from langchain_openrouter.chat_models import ChatOpenRouter
from numpy import tri

from src.common.tools import ask
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


from deepagents.middleware.subagents import SubAgent

from src.llm import LLM as llm
from src.agent_email.gmail_tools import gmail_tools
from src.agent_email.outlook_tools import outlook_tools
from src.utils.backend import get_backend

gmail_subagent = SubAgent(
    name="asistente_gmail",
    description="agente para gestionar correo de gmail - listar, leer y enviar correos electrónicos",
    system_prompt="",
    model=llm,
    tools=gmail_tools,
    interrupt_on={"GMAIL_SEND_EMAIL": {"allowed_decisions": ["approve", "edit", "reject"]}}
)

outlook_subagent = SubAgent(
    name="asistente_outlook",
    description="agente para gestionar correo de outlook - listar, leer y enviar correos electrónicos",
    system_prompt="",
    model=llm,
    tools=outlook_tools,
    interrupt_on={"OUTLOOK_SEND_EMAIL": {"allowed_decisions": ["approve", "edit", "reject"]}}
)

email_coordinator = create_deep_agent(
    name="asistente_correo",
    system_prompt="",
    tools=[ask],
    model=ChatOpenRouter(
        model="x-ai/grok-4.1-fast",
        api_key=os.getenv("OPENROUTER_API_KEY"),
    ),
    backend=get_backend,
    subagents=[gmail_subagent, outlook_subagent],
)