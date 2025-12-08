# Add a tool

from langchain.tools import tool
from src.catastro.catastro import CatastroService
from src.catastro.models import CatastroResponse

catastro_service = CatastroService()

@tool
async def consultar_por_referencia(referencia_catastral: str) -> CatastroResponse:
    """
    Consulta datos catastrales por referencia catastral

    Args:
        referencia_catastral: Referencia catastral de 20 caracteres
    """
    result = await catastro_service.consultar_por_referencia(referencia_catastral)
    return result

@tool
async def consultar_por_coordenadas(latitud: float, longitud: float) -> CatastroResponse:
    """
    Consulta datos catastrales por coordenadas GPS

    Args:
        latitud: Latitud en grados decimales
        longitud: Longitud en grados decimales
    """
    result = await catastro_service.consultar_por_coordenadas(latitud, longitud)
    return result

@tool
async def consultar_por_direccion(codigo: str) -> CatastroResponse:
    """
    Consulta datos catastrales por código de parcela (14 caracteres)

    Args:
        codigo: Código de parcela de 14 caracteres
    """
    result = await catastro_service.consultar_parcela_por_codigo(codigo)
    return result