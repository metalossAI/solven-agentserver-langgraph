import os
import httpx
from typing import Optional
from langchain_core.tools import tool

@tool
async def obtener_numeros_via(
    provincia: str,
    municipio: str,
    tipo_via: str,
    nombre_via: str,
    numero: str
):
    """
    Obtiene los números (portales) con sus referencias catastrales de una vía específica.
    
    Args:
        provincia (str): Nombre de la provincia (ej: "Madrid")
        municipio (str): Nombre del municipio (ej: "Madrid")
        tipo_via (str): Tipo de vía (ej: "CL" para calle, "AV" para avenida, "PZ" para plaza)
        nombre_via (str): Nombre exacto de la vía (ej: "GRAN VÍA")
        numero (str): Número específico a consultar (ej: "81")
    
    Returns:
        dict: Información de números con referencias catastrales (pc1, pc2)
    """
    try:
        api_key = os.getenv("CATASTRO_API_KEY")
        if not api_key:
            return {"error": "CATASTRO_API_KEY no configurada"}
        
        url = "https://catastro-api.es/api/callejero/numeros"
        params = {
            "provincia": provincia,
            "municipio": municipio,
            "tipoVia": tipo_via,
            "nombreVia": nombre_via,
            "numero": numero
        }
        headers = {"X-API-Key": api_key}
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {"error": str(e)}

@tool
async def buscar_inmueble_localizacion(
    provincia: str,
    municipio: str,
    tipo_via: str,
    nombre_via: str,
    numero: str,
    bloque: Optional[str] = None,
    escalera: Optional[str] = None,
    planta: Optional[str] = None,
    puerta: Optional[str] = None
):
    """
    Busca información detallada de inmuebles (viviendas, locales, etc.) basada en la dirección.
    Devuelve datos completos incluyendo referencia catastral, superficie, año construcción, etc.
    
    Args:
        provincia (str): Nombre de la provincia (ej: "Madrid")
        municipio (str): Nombre del municipio (ej: "Madrid")
        tipo_via (str): Tipo de vía (ej: "CL" para calle, "AV" para avenida)
        nombre_via (str): Nombre exacto de la vía (ej: "GRAN VÍA")
        numero (str): Número de portal (ej: "1")
        bloque (str, optional): Bloque del inmueble
        escalera (str, optional): Escalera del inmueble
        planta (str, optional): Planta del inmueble
        puerta (str, optional): Puerta del inmueble
    
    Returns:
        dict: Información detallada del inmueble con referencia catastral, dirección, datos económicos
    """
    try:
        api_key = os.getenv("CATASTRO_API_KEY")
        if not api_key:
            return {"error": "CATASTRO_API_KEY no configurada"}
        
        url = "https://catastro-api.es/api/callejero/inmueble-localizacion"
        params = {
            "provincia": provincia,
            "municipio": municipio,
            "tipoVia": tipo_via,
            "nombreVia": nombre_via,
            "numero": numero
        }
        
        if bloque:
            params["bloque"] = bloque
        if escalera:
            params["escalera"] = escalera
        if planta:
            params["planta"] = planta
        if puerta:
            params["puerta"] = puerta
        
        headers = {"X-API-Key": api_key}
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {"error": str(e)}

@tool
async def buscar_inmueble_rc(rc: str):
    """
    Busca información detallada de un inmueble específico utilizando su Referencia Catastral (RC).
    La referencia catastral es un identificador único de 14-20 caracteres alfanuméricos.
    
    Args:
        rc (str): Referencia Catastral del inmueble (14-20 caracteres, ej: "9872023VH5797S0001WX")
    
    Returns:
        dict: Información completa del inmueble incluyendo dirección, superficie, uso, año construcción
    """
    try:
        api_key = os.getenv("CATASTRO_API_KEY")
        if not api_key:
            return {"error": "CATASTRO_API_KEY no configurada"}
        
        if len(rc) < 14 or len(rc) > 20:
            return {"error": "La referencia catastral debe tener entre 14 y 20 caracteres"}
        
        url = "https://catastro-api.es/api/callejero/inmueble-rc"
        params = {"rc": rc}
        headers = {"X-API-Key": api_key}
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {"error": str(e)}

@tool
async def obtener_provincias():
    """
    Obtiene el listado completo de provincias españolas con sus códigos oficiales del catastro.
    Útil para conocer qué provincias están disponibles antes de realizar búsquedas.
    
    Returns:
        dict: Listado de provincias con nombre y código INE
    """
    try:
        api_key = os.getenv("CATASTRO_API_KEY")
        if not api_key:
            return {"error": "CATASTRO_API_KEY no configurada"}
        
        url = "https://catastro-api.es/api/callejero/provincias"
        headers = {"X-API-Key": api_key}
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {"error": str(e)}

@tool
async def obtener_municipios(provincia: str, municipio: Optional[str] = None):
    """
    Obtiene los municipios de una provincia específica.
    Opcionalmente puede filtrar por nombre de municipio para búsqueda específica.
    
    Args:
        provincia (str): Nombre de la provincia (ej: "Madrid")
        municipio (str, optional): Nombre del municipio para búsqueda específica (ej: "Madrid")
    
    Returns:
        dict: Listado de municipios con códigos de delegación, provincia e INE
    """
    try:
        api_key = os.getenv("CATASTRO_API_KEY")
        if not api_key:
            return {"error": "CATASTRO_API_KEY no configurada"}
        
        url = "https://catastro-api.es/api/callejero/municipios"
        params = {"provincia": provincia}
        
        if municipio:
            params["municipio"] = municipio
        
        headers = {"X-API-Key": api_key}
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {"error": str(e)}

@tool
async def obtener_vias(
    provincia: str,
    municipio: str,
    tipo_via: Optional[str] = None,
    nombre_via: Optional[str] = None
):
    """
    Obtiene las vías (calles, avenidas, plazas, etc.) de un municipio.
    Se recomienda usar al menos 3 caracteres en nombre_via para búsquedas más rápidas.
    
    Tipos de vía comunes:
    - CL: CALLE
    - AV: AVENIDA
    - PZ: PLAZA
    - PS: PASEO
    - CR: CARRETERA
    - CM: CAMINO
    - TR: TRAVESIA
    - PJ: PASAJE
    
    Args:
        provincia (str): Nombre de la provincia (ej: "Madrid")
        municipio (str): Nombre del municipio (ej: "Madrid")
        tipo_via (str, optional): Tipo de vía (ej: "CL", "AV", "PZ")
        nombre_via (str, optional): Nombre de vía para búsqueda (ej: "GRAN", mínimo 3 caracteres recomendado)
    
    Returns:
        dict: Listado de vías con código, nombre y tipo
    """
    try:
        api_key = os.getenv("CATASTRO_API_KEY")
        if not api_key:
            return {"error": "CATASTRO_API_KEY no configurada"}
        
        url = "https://catastro-api.es/api/callejero/vias"
        params = {
            "provincia": provincia,
            "municipio": municipio
        }
        
        if tipo_via:
            params["tipoVia"] = tipo_via
        if nombre_via:
            params["nombreVia"] = nombre_via
        
        headers = {"X-API-Key": api_key}
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {"error": str(e)}