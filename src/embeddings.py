import os
from dotenv import load_dotenv
load_dotenv()

from langchain_cloudflare.embeddings import (
    CloudflareWorkersAIEmbeddings,
)

embeddings = CloudflareWorkersAIEmbeddings(
    account_id=os.getenv("CF_ACCOUNT_ID"),
    api_token=os.getenv("CF_AI_API_TOKEN"),
    model_name="@cf/baai/bge-m3",
)