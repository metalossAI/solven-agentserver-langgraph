import os
from dotenv import load_dotenv
load_dotenv()

from langchain_cloudflare.embeddings import (
    CloudflareWorkersAIEmbeddings,
)

embeddings = CloudflareWorkersAIEmbeddings(
    account_id=os.getenv("CLOUDFLARE_ACCOUNT_ID"),
    api_token=os.getenv("CLOUDFLARE_AI_API_KEY"),
    model_name="@cf/qwen/qwen3-embedding-0.6b",
)