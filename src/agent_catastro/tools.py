import os
import yaml
from langchain_community.agent_toolkits.openapi.spec import reduce_openapi_spec
from langchain_community.agent_toolkits import OpenAPIToolkit
from langchain_community.utilities.requests import RequestsWrapper
from langchain_community.agent_toolkits.openapi import planner
from src.llm import LLM
from langchain_core.tools import tool

ALLOW_DANGEROUS_REQUEST = True

class CatastroOpenAPIAgent:

    def __init__(self):
        self.file_dir = os.path.dirname(os.path.abspath(__file__))
        self.spec = self.load_spec(os.path.join(self.file_dir, "openapi.yaml"))
        self.headers = {
            "X-API-Key" : os.getenv("CATASTRO_API_KEY")
        }
        self.requests = RequestsWrapper(headers=self.headers)
        self.openapi_agent = planner.create_openapi_agent(
            self.spec,
            self.requests,
            LLM,
            allow_dangerous_requests=ALLOW_DANGEROUS_REQUEST,
        )

    @staticmethod
    def load_spec(path):
        with open(path) as f:
            raw_api_spec = yaml.load(f, Loader=yaml.Loader)
            api_spec = reduce_openapi_spec(raw_api_spec)
        return api_spec

    @property
    def agent(self):
        return self.openapi_agent

openapi = CatastroOpenAPIAgent()

@tool
async def busqueda_catastro(query : str):
    """
    Herramienta para la busqueda de informacion en la API del catastro.
    Proporciona información de inmuebles consultando codigos, direcciones o coordenadas.

    Args:
        query (str): Query de búsqueda para realizar en el catastro

    Returns:
        str: Resultado de la busqueda
    """
    try:
        result = await openapi.agent.ainvoke(query)
        return result
    except Exception as e:
        return str(e)