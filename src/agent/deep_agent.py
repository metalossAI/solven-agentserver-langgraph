import datetime
import asyncio
import os

from deepagents.graph import SkillsMiddleware, FilesystemMiddleware, SubAgentMiddleware, TodoListMiddleware
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
load_dotenv()

from langchain_openai.chat_models import ChatOpenAI
from langsmith import AsyncClient
from langchain.tools import ToolRuntime
from langchain.agents.middleware import AgentMiddleware, ModelRequest, before_model, dynamic_prompt, ModelResponse, wrap_model_call

from langchain_core.messages import SystemMessage
from langchain.agents import create_agent
from deepagents.middleware import FilesystemMiddleware, SubAgentMiddleware
from langchain.agents.middleware import TodoListMiddleware

from src.sandbox_backend import SandboxBackend

 
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from langgraph.graph.state import RunnableConfig
from langgraph.config import get_config

from deepagents import create_deep_agent, SubAgent

from src.llm import LLM as llm
from src.llm import CODING_LLM as coding_llm
from src.models import AppContext, SolvenState

from src.agent_catastro.agent import subagent as catastro_subagent
from src.agent.tools import cargar_habilidad
from src.utils.tickets import get_ticket
from src.common_tools.files import solicitar_archivo

from langchain.agents.middleware import before_agent, AgentState
from langgraph.runtime import Runtime

# Import email tools
from src.agent_email.gmail_tools import gmail_tools, gmail_send_email
from src.agent_email.outlook_tools import outlook_tools


@dynamic_prompt
async def build_prompt(request: ModelRequest):
    # Reuse existing backend instead of creating a new one
    # Backend is already initialized in build_context
    runtime : Runtime[AppContext] = request.runtime
    ticket = runtime.context.ticket
    system_prompt : SystemMessage = request.system_message

    # Extract user data from config
    config: RunnableConfig = get_config()
    user_config = config["configurable"].get("langgraph_auth_user", {})
    user_data = user_config.get("user_data", {})
    user_name = user_data.get("name", "Usuario")
    user_role = user_data.get("role", "usuario")

    client = AsyncClient()
    base_prompt: ChatPromptTemplate = await client.pull_prompt("solven-main")
    initial_prompt = base_prompt.format(
        date=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        name=user_name,
        language="español",
        role=user_role,
        ticket=ticket,
    )
    # SystemMessage: append BASE_AGENT_PROMPT to content_blocks
    new_content = [
        {"type": "text", "text": f"{initial_prompt}\n\n"},
        *system_prompt.content_blocks,
    ]
    final_system_prompt = SystemMessage(content=new_content)
    return final_system_prompt

@wrap_model_call
async def dynamic_model_router(request: ModelRequest, handler):
    """
    Middleware to dynamically route to different models based on context.
    This wraps the model call and replaces the model instance before invocation.
    """
    try:
        # Get model name from runtime context (AppContext)
        model_name = request.runtime.context.model_name
        
        if model_name:
            print(f"[dynamic_model_router] Switching to model: {model_name}")
            
            # Create dynamic ChatOpenAI instance for the selected model
            dynamic_llm = ChatOpenAI(
                model=model_name,
                base_url="https://openrouter.ai/api/v1",
                api_key=os.getenv("OPENROUTER_API_KEY"),
                streaming=True,
                model_kwargs={
                    "parallel_tool_calls": False
                }
            )
            
            # Override the model in the request
            modified_request = request.override(model=dynamic_llm)
            return await handler(modified_request)
        else:
            print("[dynamic_model_router] No model specified in context, using default")
            return await handler(request)
            
    except Exception as e:
        print(f"[dynamic_model_router] Error: {e}, using default model")
        return await handler(request)

gmail_subagent = SubAgent(
    name="asistente_gmail",
    description="agente para gestionar correo de gmail - listar, leer y enviar correos electrónicos",
    system_prompt="",
    model=llm,
    tools=gmail_tools,
)

outlook_subagent = SubAgent(
    name="asistente_outlook",
    description="agente para gestionar correo de outlook - listar, leer y enviar correos electrónicos",
    system_prompt="",
    model=llm,
    tools=outlook_tools,
)

# docx_subagent = SubAgent(
#   name="asistente_docx",
#   description="agente encargado de redactar, editar y analizar documentos de word (.docx)",
#   system_prompt="",
#   model=coding_llm,
#   tools=[
#       cargar_habilidad,
#   ],
#   middleware=[
#       build_docx_prompt
#   ],
#   state_schema=SolvenState,
# )

graph = create_deep_agent(
    model=llm,  # Default model - will be dynamically swapped by middleware
    system_prompt="",
    backend=lambda rt: SandboxBackend(rt),
    subagents=[
        gmail_subagent,
        outlook_subagent,
        catastro_subagent,
    ],
    middleware=[
        build_prompt,
        dynamic_model_router,  # Dynamically route to selected model
    ],
    skills=["/skills/"],
    context_schema=AppContext,
)