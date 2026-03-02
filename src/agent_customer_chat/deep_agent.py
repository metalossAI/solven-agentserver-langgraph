import os
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langsmith import AsyncClient
load_dotenv()

from langchain.agents.middleware import ModelRequest, dynamic_prompt, AgentState
from deepagents import create_deep_agent

from src.llm import LLM as llm
from src.models import AppContext
from src.agent_customer_chat.tools import (
    listar_solicitudes_cliente,
    crear_solicitud,
    leer_solicitud,
    actualizar_solicitud,
    solicitar_archivo,
)


@dynamic_prompt
async def build_prompt(state: AgentState):
    from src.utils.config import get_user, get_user_info_by_id
    user = get_user()
    user_info = await get_user_info_by_id(user.id, user.company_id) if user.id else {}
    client = AsyncClient()
    main_prompt: ChatPromptTemplate = await client.pull_prompt("solven-customer-chat")
    return main_prompt.format(
        company_name=user_info.get("company_name") or user.name,
        client_name=user_info.get("full_name") or user.name,
        client_email=user_info.get("email") or user.email,
        phone_number=user_info.get("phone", ""),
    )


graph = create_deep_agent(
    model=llm,
    tools=[
        listar_solicitudes_cliente,
        crear_solicitud,
        leer_solicitud,
        actualizar_solicitud,
        solicitar_archivo,
    ],
    middleware=[build_prompt],
    system_prompt="",
    context_schema=AppContext,
)
