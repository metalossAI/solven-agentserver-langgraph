import os
from dotenv import load_dotenv

from langchain_groq import ChatGroq

from langchain_cloudflare.chat_models import ChatCloudflareWorkersAI
from langchain_ibm.chat_models import ChatWatsonx
from langchain_openai.chat_models import ChatOpenAI

load_dotenv()

granite_llm = ChatWatsonx(
	model_id="ibm/granite-4-h-small",
    url="https://eu-de.ml.cloud.ibm.com",
    project_id="1f803e27-a263-42e6-a21b-db988a4f9b40",
)

xai_grok_4_fast = ChatOpenAI(
    model="x-ai/grok-4.1-fast",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    model_kwargs={
        "parallel_tool_calls" : False,
    }
)

xai_grok_4_fast_SO =ChatOpenAI(
    model="x-ai/grok-4.1-fast",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    streaming=False,
)

xai_grok_code_fast_1 = ChatOpenAI(
    model="x-ai/grok-code-fast-1",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    streaming=False,
)

mistralai_ministral_3b_2512 = ChatOpenAI(
    model="mistralai/ministral-3b-2512",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

mistralai_devs_mistral_2512 = ChatOpenAI(
    model="mistralai/codestral-2508",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

openai_oss_120b = ChatOpenAI(
    model="openai/gpt-oss-120b",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

groq_oss_120b = ChatGroq(
	model="openai/gpt-oss-120b",
    api_key=os.getenv("GROQ_API_KEY"),
    reasoning_effort="low",
)

google_gemini = ChatOpenAI(
    model="google/gemini-3-flash-preview",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

claude_sonnet = ChatOpenAI(
    model="anthropic/claude-sonnet-4.5",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

LLM_SKILL_MATCHING = xai_grok_4_fast

LLM_SO = xai_grok_4_fast_SO

CODING_LLM = xai_grok_code_fast_1

LLM = google_gemini

__all__ = ["LLM"]