import os
import json
import asyncio
from dotenv import load_dotenv
from numpy import tri
load_dotenv()
 
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command, interrupt
from langgraph.graph import StateGraph
from langgraph.runtime import Runtime

from copilotkit import CopilotKitContext, LangGraphAGUIAgent, CopilotKitState
from copilotkit.langchain import copilotkit_customize_config
from copilotkit.langgraph import RunnableConfig, CopilotContextItem

from collections.abc import Callable, Sequence
from typing import Any, Optional, List, TypedDict

from langchain.chat_models import init_chat_model
from langchain.agents import create_agent
from langchain.agents.middleware import FilesystemFileSearchMiddleware
from langchain.agents.middleware.context_editing import ContextEditingMiddleware, ClearToolUsesEdit
from langchain.agents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware import InterruptOnConfig, TodoListMiddleware
from langchain_mcp_adapters.client import MultiServerMCPClient

from deepagents.middleware import FilesystemMiddleware
from deepagents.middleware.subagents import SubAgent, SubAgentMiddleware
from src.backend import get_user_s3_backend

from src.llm import LLM as llm
from src.models import AppContext
from src.agent.tools import get_composio_gmail_tools, get_composio_outlook_tools
from src.catastro.tools import consultar_por_referencia, consultar_por_coordenadas, consultar_por_direccion

class ScribaState(CopilotKitState):
    """
    The state of the agent.
    """
    # Steps Update
    steps: Optional[List[str]] = None
    # Documento
    document: Optional[str] = None
    # Contenido
    content: Optional[str] = None

async def get_context_item(context_items, item_name):
    for item in context_items:
        # Handle both dict and object formats
        if isinstance(item, dict):
            if item.get('description') == item_name:
                return item.get('value')
        elif hasattr(item, 'description') and hasattr(item, 'value'):
            if item.description == item_name:
                return item.value
    return None

async def build_runtime_context(
    state: ScribaState,
    config: RunnableConfig,
    runtime: Runtime
):
    copilotkit_context = state.get("copilotkit", {}).get("context", [])
    user_json = await get_context_item(copilotkit_context, "user")
    user = json.loads(user_json) if user_json else None
    user_id = user.get("id") if user else None
    tenant_id = await get_context_item(copilotkit_context, "tenant")

    # Set context as dictionary
    runtime.context["user_id"] = user_id
    runtime.context["tenant_id"] = tenant_id
    runtime.context["thread_id"] = config.get("configurable", {}).get("thread_id")
    
    return state
    

async def run_agent(state: ScribaState, config: RunnableConfig, runtime: Runtime):
    
    # Get context from runtime
    user_id = runtime.context.get("user_id")
    conversation_id = runtime.context.get("thread_id")

    s3_backend = await get_user_s3_backend(user_id, conversation_id)
    
    gmail_tools = get_composio_gmail_tools(user_id)
    outlook_tools = get_composio_outlook_tools(user_id)
    
    scriba_deep_agent = create_agent(
        name="scriba",
        model=llm,
        system_prompt='''
        Eres un agente que se encarga orquestrar tareas para completar tareas que te encargue el usuario.
        Reglas:
        - Dirige el flujo de trabajo.
        - Evita emoticonos, emojis y simbolos.
        - Responde en el idioma en que se dirija el usuario.
        - Si el usuario no especifica un idioma, responde en español.
        Tarea:
        - Lanzar subagentes especializados para completar los TO-DOs.
        - Dirigir el flujo de trabajo
        Objetivo: Ayudar a procesar tareas al usuario.
        Subagentes:
        - correo_electronico: Agente encargado de recabar informacion y realizar acciones en el correo.
        ''',
        middleware=[
            FilesystemMiddleware(
                system_prompt="Espacio de trabajo para crear, editar y gestionar documentos.",
                backend=s3_backend
            ),
            SubAgentMiddleware(
                default_model=llm,
                subagents=[
                    SubAgent(
                        name="asistente_busqueda_catastro",
                        description="agente para gestionar busquedas en el catastro",
                        system_prompt="Eres un asistente de busqueda de datos del catastro de España.",
                        model=llm,
                        tools=[consultar_por_referencia, consultar_por_coordenadas, consultar_por_direccion],
                        state_schema=ScribaState,
                    ),
                    SubAgent(
                        name="asistente_correo_electronico",
                        description="agente para gestionar correo electronico - listar, leer y enviar correos electrónicos",
                        system_prompt="Eres un asistente de email. Puedes listar emails, leer su contenido completo y enviar nuevos emails. Cuando leas emails, proporciona resúmenes claros. Cuando envíes emails, asegúrate de que sean profesionales y bien formateados.",
                        model=llm,
                        tools=gmail_tools + outlook_tools,
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
                                        trigger=30000,
                                        keep=3,
                                    ),
                                ],
                            ),
                        ],
                        state_schema=ScribaState,
                    ),
                    SubAgent(
                        name="documento",
                        description="agente para gestionar documentos - listar, leer y enviar correos electrónicos",
                        system_prompt="Eres un asistente de email. Puedes listar emails, leer su contenido completo y enviar nuevos emails. Cuando leas emails, proporciona resúmenes claros. Cuando envíes emails, asegúrate de que sean profesionales y bien formateados.",
                        model=llm,
                        state_schema=ScribaState,
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
        state_schema=ScribaState,
        context_schema=CopilotKitContext,
    )
    
    response = await scriba_deep_agent.ainvoke(
        state,
        config=config,
        parallel_tool_calls=False
    )

    return response

workflow = StateGraph(
    ScribaState,   
)

workflow.add_node("build_runtime_context", build_runtime_context)
workflow.set_entry_point("build_runtime_context")
workflow.add_node("run_agent", run_agent)
workflow.add_edge("build_runtime_context", "run_agent")
workflow.add_edge("run_agent", "__end__")


graph = workflow.compile(
    name="scriba",
)