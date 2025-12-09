import os
import json
import asyncio
from dotenv import load_dotenv
from numpy import tri
load_dotenv()
 
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command, interrupt
from langgraph.graph import MessagesState
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

class SolvenState(MessagesState):
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
    

async def run_agent(state: SolvenState, config: RunnableConfig, runtime: Runtime):
    
    # Get context from runtime
    user_config = config["configurable"].get("langgraph_auth_user")
    print("User config:", user_config)
    user_id = user_config.get("user_data").get("id")
    conversation_id = config.get("metadata").get("thread_id")

    s3_backend = await get_user_s3_backend(user_id, conversation_id)

    gmail_tools = get_composio_gmail_tools(user_id, conversation_id)
    outlook_tools = get_composio_outlook_tools(user_id, conversation_id)
    
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
                        state_schema=SolvenState,
                    ),
					SubAgent(
                        name="asistente_correo",
                        description="Agente para gestionar los correos del usuario. Gestiona tant",
                        system_prompt="""
Eres un asistente maestro especializado en la gestión de correos electrónicos del usuario,
encargado de coordinar y supervisar a agentes subordinados responsables de Gmail y Outlook.

Tu objetivo es brindar una gestión unificada de todas las bandejas del usuario, incluyendo:
- Listar y organizar correos de Gmail y Outlook.
- Leer y resumir mensajes individuales de cualquiera de los servicios.
- Enviar correos en nombre del usuario, escogiendo el servicio correcto según corresponda.

REGLAS IMPORTANTES:
- Debes delegar el trabajo en el subagente apropiado según la cuenta y el servicio.
- Nunca ejecutes herramientas directamente. Solo tus subagentes pueden hacerlo.
- Debes combinar y unificar respuestas de distintas bandejas en una sola presentación coherente.
- Debes responder SIEMPRE tú al usuario. Los subagentes nunca deben responder al usuario.
- No reveles qué subagente utilizaste ni detalles internos de coordinación.

Tus resúmenes e interacciones deben ser:
- Claros, profesionales, confiables y concisos.
- Orientados a la acción cuando sea necesario.

Tu función es ser el gestor global que integra, resume y entrega el resultado final al usuario.
""",
                        model=llm,
                        middleware=[
                            SubAgentMiddleware(
								default_model=llm,
                                subagents=[
                                    SubAgent(
										name="asistente_gmail",
										description="agente para gestionar correo de gmail - listar, leer y enviar correos electrónicos",
										system_prompt="""
Eres un asistente especializado en Gmail. Tu función es:
- Listar emails asociados a la cuenta de Gmail del usuario.
- Leer emails y devolver su contenido con resúmenes claros y fiables.
- Enviar correos profesionales con el formato adecuado utilizando Gmail.

REGLAS IMPORTANTES:
- Solo debes ejecutar acciones relacionadas con Gmail.
- Nunca debes responder directamente al usuario final; siempre devuelves la información al agente maestro para que la presente.
- Entrega información objetiva y estructurada, evitando opiniones innecesarias.
- Cuando resumas, destaca información clave, remitente, propósito del correo y acciones requeridas (si las hay).
""",
										model=llm,
										tools=gmail_tools,
										middleware=[
											ContextEditingMiddleware(
												edits=[
													ClearToolUsesEdit(
														trigger=30000,
														keep=3,
													),
												],
											),
										],
										state_schema=SolvenState,
									),
                                    SubAgent(
										name="asistente_outlook",
										description="agente para gestionar correo de outlook - listar, leer y enviar correos electrónicos",
										system_prompt="""
Eres un asistente especializado en Outlook. Tu función es:
- Listar emails asociados a la cuenta de Outlook del usuario.
- Leer emails y devolver su contenido con resúmenes claros y fiables.
- Enviar correos profesionales con el formato adecuado utilizando Outlook.

REGLAS IMPORTANTES:
- Solo debes ejecutar acciones relacionadas con Outlook.
- Nunca debes responder directamente al usuario final; siempre devuelves la información al agente maestro para que la presente.
- Entrega información objetiva y estructurada, evitando opiniones innecesarias.
- Cuando resumas, destaca información clave, remitente, propósito del correo y acciones requeridas (si las hay).
""",
										model=llm,
										tools=outlook_tools,
										middleware=[
											ContextEditingMiddleware(
												edits=[
													ClearToolUsesEdit(
														trigger=30000,
														keep=3,
													),
												],
											),
										],
										state_schema=SolvenState,
									),
								]
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
                        state_schema=SolvenState,
                    ),
                    SubAgent(
                        name="documento",
                        description="agente para gestionar documentos - listar, leer y enviar documentos",
                        system_prompt="Eres un asistente de documentos. Puedes listar documentos, leer su contenido completo y enviar nuevos documentos. Cuando leas documentos, proporciona resúmenes claros. Cuando envíes documentos, asegúrate de que sean profesionales y bien formateados.",
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
        context_schema=CopilotKitContext,
    )
    
    response = await scriba_deep_agent.ainvoke(
        state,
        config=config,
        parallel_tool_calls=False
    )

    return response

workflow = StateGraph(
    SolvenState,   
)

workflow.set_entry_point("run_agent")
workflow.add_node("run_agent", run_agent)
workflow.add_edge("run_agent", "__end__")


graph = workflow.compile(
    name="scriba",
)