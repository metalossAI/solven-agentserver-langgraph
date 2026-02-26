import os
from dotenv import load_dotenv

from langchain_groq import ChatGroq

from langchain_cloudflare.chat_models import ChatCloudflareWorkersAI
from langchain_ibm.chat_models import ChatWatsonx
from langchain_openai.chat_models import ChatOpenAI
from langchain_openrouter.chat_models import ChatOpenRouter

load_dotenv()

granite_llm = ChatWatsonx(
    model_id="ibm/granite-4-h-small",
    url="https://eu-de.ml.cloud.ibm.com",
    project_id="1f803e27-a263-42e6-a21b-db988a4f9b40",
)

xai_grok_4_fast = ChatOpenRouter(
    model="x-ai/grok-4.1-fast",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

xai_grok_4_fast_SO =ChatOpenRouter(
    model="x-ai/grok-4.1-fast",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

xai_grok_code_fast_1 = ChatOpenRouter(
    model="x-ai/grok-code-fast-1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

minimax_m2_5 = ChatOpenRouter(
    model="minimax/minimax-m2.5",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

mistralai_ministral_3b_2512 = ChatOpenRouter(
    model="mistralai/ministral-3b-2512",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

mistralai_devs_mistral_2512 = ChatOpenRouter(
    model="mistralai/codestral-2508",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

openai_oss_120b = ChatOpenRouter(
    model="openai/gpt-oss-120b",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

groq_oss_120b = ChatGroq(
    model="openai/gpt-oss-120b",
    api_key=os.getenv("GROQ_API_KEY"),
    reasoning_effort="low",
)

google_gemini = ChatOpenRouter(
    model="google/gemini-3-flash-preview",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

claude_sonnet = ChatOpenRouter(
    model="anthropic/claude-sonnet-4.5",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

LLM_SKILL_MATCHING = xai_grok_4_fast

LLM_SO = xai_grok_4_fast_SO

CODING_LLM = minimax_m2_5 #ai_grok_code_fast_1

LLM = xai_grok_4_fast  # Default model for agent


__all__ = ["LLM"]