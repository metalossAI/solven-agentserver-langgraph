import os
from dotenv import load_dotenv

load_dotenv()

from langchain.tools import tool, ToolRuntime
from langchain_elasticsearch import ElasticsearchStore, BM25Strategy, DenseVectorStrategy
from src.embeddings import embeddings

from src.models import AppContext
from langchain_core.documents import Document
from enum import Enum

elasticBMSearch = ElasticsearchStore(
    es_url=os.getenv("ELASTICSEARCH_ENDPOINT"),
    es_api_key=os.getenv("ELASTICSEARCH_API_KEY"),
    index_name="solven",
    embedding=embeddings,
    strategy=BM25Strategy()
)

elasticDVSearch = ElasticsearchStore(
    es_url=os.getenv("ELASTICSEARCH_ENDPOINT"),
    es_api_key=os.getenv("ELASTICSEARCH_API_KEY"),
    index_name="solven",
    embedding=embeddings,
    strategy=DenseVectorStrategy(hybrid=True)
)

class SearchMode(Enum):
    keyword = "keyword"
    similarity = "similarity"

class SearchScope(Enum):
    tenant = "tenant"
    user = "user"

@tool
async def buscar_documentos(
    runtime: ToolRuntime[AppContext],
    query: str,
    strategy : SearchMode = SearchMode.similarity,
    k : int = 5,
    scope : SearchScope = SearchScope.tenant
):
    """
    Busca documentos en la base de datos de documentos.

    Args:
        query (str): La consulta a buscar. Palabra clave o texto.
        strategy (SearchMode: "keyword" | "similarity"): estrategia de busqueda por palabra clave o similitud (similarity).
        k (int): Número de resultados a devolver. No mas de 100.
        scope (SearchScope: "tenant" | "user"): scope de busqueda. Limitada a archivos del usuario o de la empresa.
    """
    top_k = k if k < 100 else 100
    user_id = runtime.context.user_id
    company_id = runtime.context.company_id

    filter_query = {
        "term": {
            "metadata.company_id.keyword": company_id
        }
    }
    
    if scope == SearchScope.user:
        filter_query = {
            "term": {
                "metadata.uploaded-by.keyword": user_id
            }
        }
    
    search_results = []
    # rank_window_size must be >= k (size) for RRF hybrid search
    rank_window_size = max(top_k, 10)
    
    if strategy == SearchMode.keyword:
        search_results = elasticBMSearch.similarity_search(
            query,
            k=top_k,
            filter=filter_query
        )
    else:
        search_results = elasticDVSearch.similarity_search(
            query,
            k=top_k,
            filter=filter_query,
            rank_window_size=rank_window_size
        )

    if search_results:
        print("ES Search results: ", search_results)
        return search_results
    
    return []    