from langchain.agents.middleware import (
	AgentState,
    ModelRequest,
    ModelResponse,
)
from typing import Any, Dict
from src.models import SolvenState, AppContext
from langgraph.runtime import Runtime
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph.ui import push_ui_message
from langchain.agents.middleware import TodoListMiddleware
from langchain.agents.middleware.todo import PlanningState

class MessagesToUIMiddleware(TodoListMiddleware):
	def after_model(self, state: PlanningState, runtime: Runtime) -> ModelResponse:
		tool_messages = [msg for msg in state["messages"] if msg.type == "tool"]
		ai_messages = [msg for msg in state["messages"] if msg.type == "ai"]

		for ai_message in ai_messages:
			push_ui_message("ai_message", message=ai_message)

		for tool_message in tool_messages:
			match tool_message.name:
				case "write_todos":
					push_ui_message("write_todos", message=tool_message)
				
