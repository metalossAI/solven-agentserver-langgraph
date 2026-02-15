import os
from typing import Optional
from dotenv import load_dotenv

from supabase import create_async_client
from langchain_postgres import PGVectorStore, PGEngine
from sqlalchemy.ext.asyncio import create_async_engine

from src.embeddings import embeddings

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SECRET_KEY")

def _get_database_url() -> str:
    """Get DATABASE_URL from environment and ensure it uses asyncpg driver."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is required")
    
    # Ensure DATABASE_URL uses asyncpg driver
    if not database_url.startswith("postgresql+asyncpg://"):
        # Convert postgresql:// to postgresql+asyncpg:// if needed
        if database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    
    return database_url

# Global PGEngine instance (connection pool)
_pg_engine: Optional[PGEngine] = None

async def get_pg_engine() -> PGEngine:
    """Get or create PGEngine instance with connection pool.
    
    Following LangChain PGVectorStore documentation:
    https://docs.langchain.com/oss/python/integrations/vectorstores/pgvectorstore
    """
    global _pg_engine
    if _pg_engine is None:
        # Get DATABASE_URL from environment (read fresh each time)
        database_url = _get_database_url()
        
        # Create async engine
        engine = create_async_engine(database_url)
        
        # Create PGEngine from engine
        _pg_engine = PGEngine.from_engine(engine=engine)
        
        # Note: We don't initialize the table here because it already exists
        # with our custom schema (id, content, metadata, embedding)
    
    return _pg_engine

async def search(
    query: str,
    company_id: str,
    k: int = 5
) -> str:
    """
    Search for similar tickets using vector search with fallback to text search.
    
    Uses PGVectorStore following LangChain documentation:
    https://docs.langchain.com/oss/python/integrations/vectorstores/pgvectorstore
    
    Args:
        query: Search query text
        company_id: Company ID to filter tickets
        k: Number of results to return (default: 5)
        
    Returns:
        Formatted string with search results, or error message
    """
    if not company_id:
        return "Error: No se encontró el ID de la compañía"
    
    try:
        supabase_async = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        
        # Try vector search first using PGVectorStore
        try:
            # Get PGEngine instance
            pg_engine = await get_pg_engine()
            
            # Create PGVectorStore instance using existing table schema
            # Our table has: id, content, metadata, embedding (not langchain_id/langchain_metadata)
            # Following LangChain docs for custom table schema:
            # https://docs.langchain.com/oss/python/integrations/vectorstores/pgvectorstore#create-a-vector-store-using-existing-table
            vector_store = await PGVectorStore.create(
                engine=pg_engine,
                table_name="documents",
                embedding_service=embeddings,
                # Map to existing column names
                id_column="id",  # Our table uses "id" not "langchain_id"
                content_column="content",
                embedding_column="embedding",
                metadata_json_column="metadata",  # Our table uses "metadata" not "langchain_metadata"
            )
            
            # Perform similarity search with relevance scores (async, following docs)
            # This matches: docs = await store.asimilarity_search_with_relevance_scores(query)
            results = await vector_store.asimilarity_search_with_relevance_scores(
                query=query,
                k=k * 2,  # Get more results to filter by metadata
            )
            
            # Filter results by company_id and type in metadata
            filtered_results = []
            for doc, score in results:
                metadata = doc.metadata or {}
                if (metadata.get("company_id") == company_id and 
                    metadata.get("type") == "ticket_description"):
                    filtered_results.append((doc, score))
                    if len(filtered_results) >= k:
                        break
            
            if filtered_results:
                response_lines = [f"Se encontraron {len(filtered_results)} tickets relacionados:\n"]
                
                for doc, score in filtered_results:
                    ticket_id = doc.metadata.get("ticket_id")
                    title = doc.metadata.get("title", "Sin título")
                    customer_email = doc.metadata.get("customer_email", "Desconocido")
                    priority = doc.metadata.get("priority", "medium")
                    
                    # Get ticket status from tickets table
                    ticket_info = await supabase_async.table("tickets").select("status").eq("id", ticket_id).execute()
                    status = ticket_info.data[0]["status"] if ticket_info.data else "unknown"
                    
                    response_lines.append(
                        f"- ID: {ticket_id}\n"
                        f"  Título: {title}\n"
                        f"  Cliente: {customer_email}\n"
                        f"  Estado: {status}\n"
                        f"  Prioridad: {priority}\n"
                        f"  Relevancia: {score:.3f}\n"
                        f"  Resumen: {doc.page_content[:200]}...\n"
                    )
                
                return "\n".join(response_lines)
        except Exception as vector_error:
            import traceback
            print(f"[DEBUG] Vector search failed in utils: {type(vector_error).__name__}: {str(vector_error)}", flush=True)
            print(f"[DEBUG] Vector search traceback:", flush=True)
            traceback.print_exc()
            # Fallback to text search
            docs_response = await supabase_async.table("documents").select("id, content, metadata").ilike("content", f"%{query}%").execute()
            
            if docs_response.data:
                filtered_docs = [
                    doc for doc in docs_response.data
                    if doc.get("metadata", {}).get("company_id") == company_id
                    and doc.get("metadata", {}).get("type") == "ticket_description"
                ]
                
                if filtered_docs:
                    response_lines = [f"Se encontraron {min(len(filtered_docs), k)} tickets relacionados (búsqueda por texto):\n"]
                    
                    for doc in filtered_docs[:k]:
                        metadata = doc.get("metadata", {})
                        ticket_id = metadata.get("ticket_id")
                        title = metadata.get("title", "Sin título")
                        customer_email = metadata.get("customer_email", "Desconocido")
                        priority = metadata.get("priority", "medium")
                        
                        ticket_info = await supabase_async.table("tickets").select("status").eq("id", ticket_id).execute()
                        status = ticket_info.data[0]["status"] if ticket_info.data else "unknown"
                        
                        content = doc.get("content", "")
                        response_lines.append(
                            f"- ID: {ticket_id}\n"
                            f"  Título: {title}\n"
                            f"  Cliente: {customer_email}\n"
                            f"  Estado: {status}\n"
                            f"  Prioridad: {priority}\n"
                            f"  Resumen: {content[:200]}...\n"
                        )
                    
                    return "\n".join(response_lines)
        
        return "No se encontraron tickets relacionados con la búsqueda"
    except Exception as e:
        print(f"[ERROR] Error searching tickets: {str(e)}", flush=True)
        return f"Error al buscar tickets: {str(e)}"
