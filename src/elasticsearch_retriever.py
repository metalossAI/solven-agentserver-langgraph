import os
from elasticsearch import Elasticsearch
from typing import List, Dict, Any
from dotenv import load_dotenv
from langchain.tools import tool, ToolRuntime

load_dotenv()

class ElasticsearchRetriever:
    """
    Elasticsearch retriever for document search.
    Uses native Elasticsearch client instead of langchain-elasticsearch.
    """
    
    def __init__(self, user_id: str, tenant_id: str):
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.es_url = os.getenv("ELASTICSEARCH_ENDPOINT", "http://localhost:9200")
        self.es_api_key = os.getenv("ELASTICSEARCH_API_KEY")
        self.index_name = os.getenv("ELASTICSEARCH_INDEX", "documents")
        
        # Initialize Elasticsearch client
        if self.es_api_key:
            self.client = Elasticsearch(
                self.es_url,
                api_key=self.es_api_key
            )
        else:
            self.client = Elasticsearch(self.es_url)
    
    async def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Search documents in Elasticsearch.
        
        Args:
            query: Search query string
            top_k: Number of results to return
            
        Returns:
            List of documents with content and metadata
        """
        try:
            response = self.client.search(
                index=self.index_name,
                body={
                    "query": {
                        "bool": {
                            "must": [
                                {
                                    "multi_match": {
                                        "query": query,
                                        "fields": ["content"],  # Boost title matches
                                        "type": "best_fields"
                                    }
                                }
                            ],
                            "filter": [
                                {"term": {"tenant_id": self.tenant_id}}  # Tenant isolation
                            ]
                        }
                    },
                    "size": top_k,
                    "_source": ["content", "title", "file_path", "metadata"]
                }
            )
            
            # Format results
            results = []
            for hit in response["hits"]["hits"]:
                source = hit["_source"]
                results.append({
                    "content": source.get("content", ""),
                    "title": source.get("title", ""),
                    "file_path": source.get("file_path", ""),
                    "metadata": source.get("metadata", {}),
                    "score": hit["_score"]
                })
            
            return results
            
        except Exception as e:
            print(f"Error searching Elasticsearch: {e}")
            return []
    
    def format_results_for_context(self, results: List[Dict[str, Any]]) -> str:
        """
        Format search results as context string for LLM.
        
        Args:
            results: List of search results
            
        Returns:
            Formatted string with all results
        """
        if not results:
            return "No relevant documents found."
        
        context_parts = []
        for i, result in enumerate(results, 1):
            title = result.get("title", "Untitled")
            content = result.get("content", "")
            file_path = result.get("file_path", "")
            
            context_parts.append(
                f"Document {i}: {title}\n"
                f"Path: {file_path}\n"
                f"Content:\n{content}\n"
                f"---"
            )
        
        return "\n\n".join(context_parts)

@tool
async def buscar_documentos(query: str, runtime: ToolRuntime):
    user_id = runtime.config.get("user_id")
    tenant_id = runtime.config.get("tenant_id")
    retriever = ElasticsearchRetriever(user_id, tenant_id)

    
    

    
async def create_elasticsearch_tool(user_id: str, tenant_id: str):
    """
    Create an Elasticsearch retriever tool for the agent.
    
    Args:
        user_id: User ID for tenant isolation
        
    Returns:
        Retriever instance
    """
    return ElasticsearchRetriever(user_id, tenant_id)
