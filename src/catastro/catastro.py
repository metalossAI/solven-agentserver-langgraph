"""
Servicio principal para consultas al Catastro de España
"""

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Optional, Dict, Any, List
from datetime import datetime
import httpx
from xml.dom import minidom

from src.catastro.models import (
    CatastroResponse, 
    DatosBasicosInmueble, 
    DireccionCatastral,
    ValorCatastral,
    Coordenadas,
    ReferenciaCatastral,
    ErrorCatastral
)

from src.catastro.settings import get_settings, CatastroEndpoints, ERROR_MESSAGES

logger = logging.getLogger(__name__)


class CatastroService:
    """Servicio para consultas al Catastro de España"""
    
    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.catastro_base_url
        self.timeout = self.settings.catastro_timeout
        self.max_retries = self.settings.catastro_max_retries
        self.retry_delay = self.settings.catastro_retry_delay
        
    async def consultar_por_referencia(self, referencia: str) -> CatastroResponse:
        """
        Consulta datos catastrales por referencia catastral usando la nueva API WCF
        
        Args:
            referencia: Referencia catastral de 20 caracteres
            
        Returns:
            CatastroResponse con los datos encontrados
        """
        try:
            # Validar formato de referencia
            ref_validada = ReferenciaCatastral(referencia=referencia)
            
            # Preparar parámetros para la consulta REST JSON
            params = {
                "Provincia": "",
                "Municipio": "",
                "RefCat": ref_validada.referencia
            }
            
            # Realizar consulta a la nueva API WCF
            url = f"{self.base_url}{CatastroEndpoints.CONSULTA_DNPRC}"
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # Configurar headers para JSON
                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded"
                }
                response = await self._realizar_consulta_con_reintentos(client, url, params, headers)
                
            # Parsear respuesta JSON (la nueva API devuelve JSON)
            datos_parseados = self._parsear_respuesta_json(response.text)
            
            # Construir respuesta estructurada
            return self._construir_respuesta_catastral(ref_validada.referencia, datos_parseados)
            
        except Exception as e:
            logger.error(f"Error consultando referencia {referencia}: {str(e)}")
            return CatastroResponse(
                referencia_catastral=referencia,
                estado_consulta="error",
                mensaje_error=str(e)
            )
    
    async def consultar_por_coordenadas(self, latitud: float, longitud: float) -> CatastroResponse:
        """
        Consulta datos catastrales por coordenadas geográficas usando la nueva API WCF
        
        Args:
            latitud: Latitud en grados decimales
            longitud: Longitud en grados decimales
            
        Returns:
            CatastroResponse con los datos encontrados
        """
        try:
            # Validar coordenadas
            coords = Coordenadas(latitud=latitud, longitud=longitud)
            
            # Preparar parámetros para la consulta de coordenadas
            params = {
                "SRS": "EPSG:4326",  # WGS84
                "Coordenada_X": str(coords.longitud), 
                "Coordenada_Y": str(coords.latitud)
            }
            
            # Realizar consulta a la nueva API de coordenadas
            url = f"{self.base_url}{CatastroEndpoints.CONSULTA_RCCOOR}"
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # Configurar headers para JSON
                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded"
                }
                response = await self._realizar_consulta_con_reintentos(client, url, params, headers)
                
            # Parsear respuesta JSON
            datos_parseados = self._parsear_respuesta_json(response.text)
            
            # Extraer referencia catastral de la respuesta
            referencia = self._extraer_referencia_de_coordenadas(datos_parseados)
            
            # Si encontramos referencia, hacer consulta completa de datos
            if referencia and referencia != "DESCONOCIDA":
                resultado_completo = await self.consultar_por_referencia(referencia)
                resultado_completo.coordenadas = coords
                return resultado_completo
            
            # Si no hay referencia, devolver respuesta básica
            return CatastroResponse(
                referencia_catastral="DESCONOCIDA",
                estado_consulta="sin_datos",
                mensaje_error="No se encontró inmueble en las coordenadas especificadas",
                coordenadas=coords,
                datos_raw=datos_parseados
            )
            
        except Exception as e:
            logger.error(f"Error consultando coordenadas {latitud}, {longitud}: {str(e)}")
            return CatastroResponse(
                referencia_catastral="DESCONOCIDA",
                estado_consulta="error",
                mensaje_error=str(e),
                coordenadas=Coordenadas(latitud=latitud, longitud=longitud)
            )
    
    async def _realizar_consulta_con_reintentos(self, client: httpx.AsyncClient, url: str, params: Dict[str, str], headers: Dict[str, str] = None) -> httpx.Response:
        """Realiza una consulta HTTP con reintentos automáticos"""
        
        for intento in range(self.max_retries + 1):
            try:
                logger.info(f"Realizando consulta (intento {intento + 1}): {url}")
                
                # Usar GET para la nueva API WCF JSON - funciona con parámetros en URL
                if headers:
                    response = await client.get(url, params=params, headers=headers)
                else:
                    response = await client.get(url, params=params)
                response.raise_for_status()
                
                # Verificar que la respuesta no esté vacía
                if not response.text.strip():
                    raise ValueError("Respuesta vacía del servidor")
                    
                return response
                
            except httpx.TimeoutException:
                if intento < self.max_retries:
                    logger.warning(f"Timeout en intento {intento + 1}, reintentando...")
                    await asyncio.sleep(self.retry_delay * (intento + 1))
                else:
                    raise ValueError(ERROR_MESSAGES["TIMEOUT"])
                    
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 503:
                    if intento < self.max_retries:
                        logger.warning(f"Servicio no disponible, reintentando...")
                        await asyncio.sleep(self.retry_delay * (intento + 1))
                    else:
                        raise ValueError(ERROR_MESSAGES["SERVICIO_NO_DISPONIBLE"])
                else:
                    raise ValueError(f"Error HTTP {e.response.status_code}")
                    
            except Exception as e:
                if intento < self.max_retries:
                    logger.warning(f"Error en intento {intento + 1}: {str(e)}")
                    await asyncio.sleep(self.retry_delay * (intento + 1))
                else:
                    raise
    
    def _parsear_respuesta_json(self, json_content: str) -> Dict[str, Any]:
        """Parsea la respuesta JSON de la nueva API WCF"""
        try:
            import json
            datos = json.loads(json_content)
            return datos
        except json.JSONDecodeError as e:
            logger.error(f"Error parseando JSON: {str(e)}")
            logger.debug(f"JSON recibido: {json_content[:500]}...")
            # Fallback a XML si JSON falla
            return self._parsear_respuesta_xml(json_content)
    
    def _parsear_respuesta_xml(self, xml_content: str) -> Dict[str, Any]:
        """Parsea la respuesta XML del Catastro (fallback)"""
        try:
            # Limpiar y parsear XML
            xml_limpio = xml_content.replace('<?xml version="1.0" encoding="UTF-8"?>', '')
            root = ET.fromstring(xml_limpio)
            
            # Convertir XML a diccionario
            return self._xml_a_dict(root)
            
        except ET.ParseError as e:
            logger.error(f"Error parseando XML: {str(e)}")
            logger.debug(f"XML recibido: {xml_content[:500]}...")
            raise ValueError("Error parseando respuesta del Catastro")
    
    def _xml_a_dict(self, element) -> Dict[str, Any]:
        """Convierte un elemento XML a diccionario"""
        result = {}
        
        # Atributos del elemento
        if element.attrib:
            result.update(element.attrib)
        
        # Texto del elemento
        if element.text and element.text.strip():
            if len(element) == 0:  # Es un elemento hoja
                return element.text.strip()
            else:
                result['_text'] = element.text.strip()
        
        # Elementos hijos
        for child in element:
            child_data = self._xml_a_dict(child)
            
            if child.tag in result:
                # Si ya existe, convertir a lista
                if not isinstance(result[child.tag], list):
                    result[child.tag] = [result[child.tag]]
                result[child.tag].append(child_data)
            else:
                result[child.tag] = child_data
        
        return result
    
    def _construir_respuesta_catastral(self, referencia: str, datos_raw: Dict[str, Any]) -> CatastroResponse:
        """Construye una respuesta estructurada a partir de los datos del Catastro usando estructura oficial"""
        
        try:
            consulta_result = datos_raw.get('consulta_dnprcResult', {})
            
            # Detectar el tipo de estructura según el contenido
            if 'lrcdnp' in consulta_result:
                # Estructura para consultas con 14 caracteres (múltiples inmuebles)
                return self._procesar_respuesta_multiple_inmuebles(referencia, consulta_result, datos_raw)
            elif 'bico' in consulta_result:
                # Estructura para consultas con 20 caracteres (inmueble específico)
                return self._procesar_respuesta_inmueble_individual(referencia, consulta_result, datos_raw)
            else:
                return CatastroResponse(
                    referencia_catastral=referencia,
                    estado_consulta="sin_datos",
                    mensaje_error="No se encontraron datos para esta referencia catastral",
                    datos_raw=datos_raw
                )
            
        except Exception as e:
            logger.error(f"Error construyendo respuesta: {str(e)}")
            return CatastroResponse(
                referencia_catastral=referencia,
                estado_consulta="error",
                mensaje_error=f"Error procesando datos: {str(e)}",
                datos_raw=datos_raw
            )
    
    def _procesar_respuesta_inmueble_individual(self, referencia: str, consulta_result: Dict[str, Any], datos_raw: Dict[str, Any]) -> CatastroResponse:
        """Procesa respuesta para inmueble individual (referencia de 20 caracteres)"""
        try:
            bico = consulta_result.get('bico', {})
            bi = bico.get('bi', {})
            
            if not bi:
                return CatastroResponse(
                    referencia_catastral=referencia,
                    estado_consulta="sin_datos",
                    mensaje_error="No se encontraron datos del inmueble",
                    datos_raw=datos_raw
                )
            
            # Extraer datos básicos
            debi = bi.get('debi', {})
            uso = debi.get('luso', debi.get('uso'))
            superficie = debi.get('sfc')
            antiguedad = debi.get('ant')
            
            # Convertir tipos
            superficie_num = None
            if superficie:
                try:
                    superficie_num = float(str(superficie).replace(',', '.'))
                except (ValueError, TypeError):
                    pass
            
            antiguedad_num = None
            if antiguedad:
                try:
                    antiguedad_num = int(antiguedad)
                except (ValueError, TypeError):
                    pass
            
            datos_basicos = DatosBasicosInmueble(
                uso=uso,
                superficie_construida=superficie_num,
                antiguedad=antiguedad_num
            )
            
            # Extraer dirección
            dt = bi.get('dt', {})
            provincia_nombre = dt.get('np', '')
            municipio_nombre = dt.get('nm', '')
            
            # Información de ubicación
            locs = dt.get('locs', {})
            lous = locs.get('lous', {})
            lourb = lous.get('lourb', {})
            
            # Vía
            dir_info = lourb.get('dir', {})
            tipo_via = dir_info.get('tv', '')
            nombre_via = dir_info.get('nv', '')
            
            # Información interior
            loint = lourb.get('loint', {})
            escalera = loint.get('es', '')
            planta = loint.get('pt', '')
            puerta = loint.get('pu', '')
            
            direccion = DireccionCatastral()
            if tipo_via and nombre_via:
                direccion.via = f"{tipo_via} {nombre_via}"
            direccion.planta = planta if planta else None
            direccion.puerta = puerta if puerta else None
            direccion.municipio = municipio_nombre
            direccion.provincia = provincia_nombre
            
            return CatastroResponse(
                referencia_catastral=referencia,
                datos_basicos=datos_basicos,
                direccion=direccion,
                valores=None,
                estado_consulta="exitosa",
                datos_raw=datos_raw
            )
            
        except Exception as e:
            logger.error(f"Error procesando inmueble individual: {str(e)}")
            return CatastroResponse(
                referencia_catastral=referencia,
                estado_consulta="error",
                mensaje_error=f"Error procesando datos del inmueble: {str(e)}",
                datos_raw=datos_raw
            )
    
    def _procesar_respuesta_multiple_inmuebles(self, referencia: str, consulta_result: Dict[str, Any], datos_raw: Dict[str, Any]) -> CatastroResponse:
        """Procesa respuesta para múltiples inmuebles (referencia de 14 caracteres)"""
        try:
            lrcdnp = consulta_result.get('lrcdnp', {})
            rcdnp_data = lrcdnp.get('rcdnp', {})
            
            if isinstance(rcdnp_data, list) and rcdnp_data:
                # Tomar el primer inmueble para la respuesta legacy
                inmueble_data = rcdnp_data[0]
            elif isinstance(rcdnp_data, dict):
                inmueble_data = rcdnp_data
            else:
                return CatastroResponse(
                    referencia_catastral=referencia,
                    estado_consulta="sin_datos",
                    mensaje_error="No se encontraron datos para esta referencia catastral",
                    datos_raw=datos_raw
                )
            
            # Procesar usando la función oficial
            inmueble_procesado = self._procesar_inmueble_catastro_oficial(inmueble_data)
            
            if not inmueble_procesado:
                return CatastroResponse(
                    referencia_catastral=referencia,
                    estado_consulta="error",
                    mensaje_error="Error procesando datos del inmueble",
                    datos_raw=datos_raw
                )
            
            # Convertir a estructura legacy
            datos_basicos = DatosBasicosInmueble(
                uso=inmueble_procesado.get('uso'),
                superficie_construida=float(inmueble_procesado.get('superficie', '0').replace(',', '.')) if inmueble_procesado.get('superficie', '').replace(',', '.').replace('.', '').isdigit() else None,
                antiguedad=int(inmueble_procesado.get('antiguedad', '0')) if inmueble_procesado.get('antiguedad', '').isdigit() else None
            )
            
            direccion = DireccionCatastral()
            direccion_texto = inmueble_procesado.get('direccion', '')
            if direccion_texto:
                partes = direccion_texto.split(', ')
                if partes:
                    direccion.via = partes[0]
                    direccion.planta = next((p.replace('Planta ', '') for p in partes if 'Planta' in p), None)
                    direccion.puerta = next((p.replace('Puerta ', '') for p in partes if 'Puerta' in p), None)
                    
                direccion.municipio = inmueble_procesado.get('municipio', '').split(' (')[0]
                direccion.provincia = inmueble_procesado.get('provincia', '').split(' (')[0]
            
            return CatastroResponse(
                referencia_catastral=referencia,
                datos_basicos=datos_basicos,
                direccion=direccion,
                valores=None,
                estado_consulta="exitosa",
                datos_raw=datos_raw
            )
            
        except Exception as e:
            logger.error(f"Error procesando múltiples inmuebles: {str(e)}")
            return CatastroResponse(
                referencia_catastral=referencia,
                estado_consulta="error",
                mensaje_error=f"Error procesando datos: {str(e)}",
                datos_raw=datos_raw
            )
    
    def _extraer_datos_basicos(self, datos: Dict[str, Any]) -> Optional[DatosBasicosInmueble]:
        """Extrae datos básicos del inmueble de la respuesta del Catastro"""
        try:
            # Buscar en diferentes estructuras posibles
            bico = datos.get('consulta_dnp', {}).get('bico', {})
            bi = bico.get('bi', {}) if isinstance(bico, dict) else {}
            debi = bi.get('debi', {}) if isinstance(bi, dict) else {}
            
            if not debi:
                return None
            
            # Extraer datos
            uso = debi.get('luso', debi.get('uso'))
            superficie = debi.get('sfc')
            antiguedad = debi.get('ant')
            
            # Convertir tipos
            superficie_num = None
            if superficie:
                try:
                    superficie_num = float(superficie)
                except (ValueError, TypeError):
                    pass
            
            antiguedad_num = None
            if antiguedad:
                try:
                    antiguedad_num = int(antiguedad)
                except (ValueError, TypeError):
                    pass
            
            return DatosBasicosInmueble(
                uso=uso,
                superficie_construida=superficie_num,
                antiguedad=antiguedad_num
            )
            
        except Exception as e:
            logger.warning(f"Error extrayendo datos básicos: {str(e)}")
            return None
    
    def _extraer_direccion(self, datos: Dict[str, Any]) -> Optional[DireccionCatastral]:
        """Extrae la dirección del inmueble de la respuesta del Catastro"""
        try:
            # Buscar datos de dirección en la estructura
            bico = datos.get('consulta_dnp', {}).get('bico', {})
            bi = bico.get('bi', {}) if isinstance(bico, dict) else {}
            
            if not bi:
                return None
            
            # Extraer datos de dirección
            via = bi.get('tv', '') + ' ' + bi.get('nv', '')
            numero = bi.get('num')
            
            return DireccionCatastral(
                via=via.strip() if via.strip() else None,
                numero=numero
            )
            
        except Exception as e:
            logger.warning(f"Error extrayendo dirección: {str(e)}")
            return None
    
    def _extraer_valores(self, datos: Dict[str, Any]) -> Optional[ValorCatastral]:
        """Extrae valores catastrales de la respuesta del Catastro"""
        try:
            # Los valores catastrales no siempre están disponibles en todas las consultas
            # Esto dependería de la estructura específica de la respuesta
            return None
            
        except Exception as e:
            logger.warning(f"Error extrayendo valores: {str(e)}")
            return None
    
    def _extraer_referencia_de_coordenadas(self, datos: Dict[str, Any]) -> str:
        """Extrae la referencia catastral de una consulta por coordenadas usando estructura oficial"""
        try:
            # Buscar en la estructura oficial de coordenadas
            # La respuesta de CONSULTA_RCCOOR puede tener una estructura diferente
            
            # Intentar estructura de coordenadas primero
            consulta_rccoor = datos.get('consulta_rccoorResult', {})
            if consulta_rccoor:
                # Extraer referencia directamente si está disponible
                if 'refcat' in consulta_rccoor:
                    return consulta_rccoor['refcat']
                if 'pc' in consulta_rccoor:
                    return consulta_rccoor['pc']
            
            # Intentar estructura estándar de DNPRC si la coordenada retorna datos completos
            consulta_result = datos.get('consulta_dnprcResult', {})
            if consulta_result:
                lrcdnp = consulta_result.get('lrcdnp', {})
                rcdnp_data = lrcdnp.get('rcdnp', {})
                
                if isinstance(rcdnp_data, list) and rcdnp_data:
                    rc_data = rcdnp_data[0].get('rc', {})
                elif isinstance(rcdnp_data, dict):
                    rc_data = rcdnp_data.get('rc', {})
                else:
                    return "DESCONOCIDA"
                
                # Construir referencia completa
                pc1 = rc_data.get('pc1', '')
                pc2 = rc_data.get('pc2', '')
                car = rc_data.get('car', '')
                cc1 = rc_data.get('cc1', '')
                cc2 = rc_data.get('cc2', '')
                
                if pc1 and pc2:
                    return f"{pc1}{pc2}{car}{cc1}{cc2}"
            
            return "DESCONOCIDA"
            
        except Exception as e:
            logger.warning(f"Error extrayendo referencia de coordenadas: {str(e)}")
            return "DESCONOCIDA"
    
    async def buscar_por_direccion(
        self, 
        provincia: str, 
        municipio: str, 
        tipo_via: str = "CALLE",
        nombre_via: str = "",
        numero: str = "",
        direccion_original: str = ""
    ) -> CatastroResponse:
        """
        Busca referencias catastrales por dirección postal
        
        NOTA: La API del Catastro tiene limitaciones significativas para búsquedas por dirección.
        Los endpoints JSON documentados no están disponibles actualmente.
        
        Args:
            provincia: Nombre de la provincia
            municipio: Nombre del municipio  
            tipo_via: Tipo de vía (CALLE, AVENIDA, etc.)
            nombre_via: Nombre de la vía
            numero: Número del inmueble (opcional)
            
        Returns:
            CatastroResponse indicando las alternativas disponibles
        """
        direccion_mostrar = direccion_original if direccion_original else f"{tipo_via} {nombre_via} {numero}, {municipio}, {provincia}"
        logger.info(f"Búsqueda por dirección solicitada: {direccion_mostrar}")
        
        # Verificar si todos los campos necesarios están presentes
        campos_faltantes = []
        if not provincia: campos_faltantes.append("PROVINCIA")
        if not municipio: campos_faltantes.append("MUNICIPIO") 
        if not nombre_via: campos_faltantes.append("NOMBRE_VIA")
        
        if campos_faltantes:
            mensaje_error = f"""
❌ FORMATO DE DIRECCIÓN INCORRECTO

Faltan los siguientes campos obligatorios: {', '.join(campos_faltantes)}

FORMATO CORRECTO REQUERIDO:
'TIPO_VIA NOMBRE_VIA NUMERO, CODIGO_POSTAL, MUNICIPIO, PROVINCIA'

EJEMPLOS VÁLIDOS:
• "CALLE REYES CATOLICOS 6, 18100, ARMILLA, GRANADA"
• "AVENIDA CONSTITUCION 25, 14011, CORDOBA, CORDOBA"
• "PLAZA MAYOR 1, 28012, MADRID, MADRID"

DIRECCIÓN RECIBIDA: {direccion_mostrar}

Por favor, reintente con el formato correcto.
            """.strip()
            
            return CatastroResponse(
                referencia_catastral="FORMATO_INCORRECTO",
                estado_consulta="error_formato",
                mensaje_error=mensaje_error,
                datos_raw={"direccion_original": direccion_original, "campos_faltantes": campos_faltantes}
            )
        
        # Información detallada sobre limitaciones y alternativas
        mensaje_alternativas = f"""
⚠️  BÚSQUEDA POR DIRECCIÓN NO DISPONIBLE

La API oficial del Catastro NO permite búsquedas directas por dirección postal.
Los endpoints JSON documentados no están operativos.

📍 DIRECCIÓN SOLICITADA:
{direccion_mostrar}

✅ ALTERNATIVAS FUNCIONALES:

1. 🌐 SEDE ELECTRÓNICA DEL CATASTRO (Más efectivo):
   → https://sede.catastro.gob.es
   → Ir a "Consulta tu catastro"
   → Buscar por dirección: {direccion_mostrar}
   → Copiar la referencia catastral (20 caracteres)
   → Usar aquí: consultar_catastro_por_referencia

2. 📍 BÚSQUEDA POR COORDENADAS GPS:
   → Abrir Google Maps: {direccion_mostrar}
   → Copiar coordenadas (clic derecho en el punto exacto)
   → Usar: consultar_catastro_por_coordenadas
   → Ejemplo coordenadas: 40.4168, -3.7038

3. 🔍 SI YA TIENES LA REFERENCIA CATASTRAL:
   → Usar: consultar_catastro_por_referencia
   → Formato: 20 caracteres alfanuméricos
   → Ejemplo: 4418928VG4141G0001IW

💡 RECOMENDACIÓN:
La opción MÁS RÁPIDA es buscar en sede.catastro.gob.es y luego usar 
la referencia catastral obtenida con nuestras herramientas MCP.
        """.strip()
        
        return CatastroResponse(
            referencia_catastral="BUSQUEDA_NO_DISPONIBLE",
            estado_consulta="informacion",
            mensaje_error=mensaje_alternativas,
            direccion=DireccionCatastral(
                via=f"{tipo_via} {nombre_via}",
                numero=numero,
                municipio=municipio,
                provincia=provincia
            ),
            datos_raw={
                "tipo_respuesta": "informacion_alternativas",
                "direccion_solicitada": f"{tipo_via} {nombre_via} {numero}, {municipio}, {provincia}",
                "alternativas": [
                    "sede.catastro.gob.es",
                    "busqueda_por_coordenadas", 
                    "consulta_por_referencia_catastral"
                ]
            }
        )
    
    async def consultar_parcela_por_codigo(self, codigo_parcela: str) -> CatastroResponse:
        """
        Consulta información de parcela usando código de 14 caracteres
        
        Args:
            codigo_parcela: Código de parcela de 14 caracteres (sin subparcela ni dígitos de control)
            
        Returns:
            CatastroResponse con información de la parcela y sus inmuebles
        """
        try:
            # Limpiar y validar el código de parcela
            codigo_limpio = codigo_parcela.replace(' ', '').upper()
            
            # Verificar longitud y formato
            if len(codigo_limpio) != 14:
                mensaje_error = f"""
CODIGO DE PARCELA INVALIDO

El codigo proporcionado '{codigo_parcela}' tiene {len(codigo_limpio)} caracteres.
Se requieren exactamente 14 caracteres.

ESTRUCTURA DEL CODIGO DE PARCELA (14 caracteres):
- Posiciones 1-2: PROVINCIA (ej: 23)
- Posiciones 3-5: MUNICIPIO (ej: 145)  
- Posiciones 6-7: SECTOR (ej: 01)
- Posiciones 8-10: MANZANA (ej: EG1)
- Posiciones 11-14: PARCELA (ej: 421S)

EJEMPLO VALIDO: 2314501EG1421S

PARA OBTENER EL CODIGO CORRECTO:
1. Visita https://sede.catastro.gob.es
2. Busca por direccion
3. Usa los primeros 14 caracteres de la referencia catastral
                """.strip()
                
                return CatastroResponse(
                    referencia_catastral=codigo_parcela,
                    estado_consulta="error_formato",
                    mensaje_error=mensaje_error
                )
            
            # Verificar caracteres válidos
            if not codigo_limpio.isalnum():
                return CatastroResponse(
                    referencia_catastral=codigo_parcela,
                    estado_consulta="error_formato", 
                    mensaje_error=f"El código de parcela solo puede contener números y letras. Código recibido: '{codigo_parcela}'"
                )
            
            logger.info(f"Consultando parcela con código: {codigo_limpio}")
            
            # Intentar buscar inmuebles en esta parcela usando la API de búsqueda por coordenadas
            # Como alternativa, podemos buscar usando los componentes del código
            resultado = await self._buscar_inmuebles_en_parcela(codigo_limpio)
            
            return resultado
            
        except Exception as e:
            logger.error(f"Error consultando parcela {codigo_parcela}: {str(e)}")
            return CatastroResponse(
                referencia_catastral=codigo_parcela,
                estado_consulta="error",
                mensaje_error=str(e)
            )
    
    async def _buscar_inmuebles_en_parcela(self, codigo_parcela: str) -> CatastroResponse:
        """
        Busca inmuebles dentro de una parcela usando el código de 14 caracteres
        SEGÚN LA DOCUMENTACIÓN OFICIAL: usar referencia de 14 chars devuelve TODOS los inmuebles automáticamente
        """
        try:
            # Extraer componentes del código para información
            provincia = codigo_parcela[:2]
            municipio = codigo_parcela[2:5] 
            sector = codigo_parcela[5:7]
            manzana = codigo_parcela[7:10]
            parcela = codigo_parcela[10:14]
            
            logger.info(f"Consultando parcela completa - Código: {codigo_parcela}")
            logger.info(f"Componentes - Provincia: {provincia}, Municipio: {municipio}, Sector: {sector}, Manzana: {manzana}, Parcela: {parcela}")
            
            # MÉTODO OFICIAL: Usar directamente Consulta_DNPRC con 14 caracteres
            # Según la documentación: "cuando introduces una referencia catastral de 14 caracteres, 
            # el sistema interpreta que quieres la unidad base o raíz de la finca, 
            # y devuelve todos los elementos asociados"
            
            params = {
                "Provincia": "",
                "Municipio": "",
                "RefCat": codigo_parcela  # Usar directamente el código de 14 caracteres
            }
            
            url = f"{self.base_url}{CatastroEndpoints.CONSULTA_DNPRC}"
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded"
                }
                response = await self._realizar_consulta_con_reintentos(client, url, params, headers)
            
            # Parsear respuesta JSON
            datos_parseados = self._parsear_respuesta_json(response.text)
            logger.info(f"Respuesta de la API: {datos_parseados}")
            
            # Analizar respuesta para múltiples inmuebles
            inmuebles_encontrados = self._extraer_inmuebles_division_horizontal(datos_parseados)
            
            # Construir respuesta
            if inmuebles_encontrados:
                mensaje_resultado = f"""
PARCELA CON DIVISION HORIZONTAL ENCONTRADA

Codigo de parcela: {codigo_parcela}
Inmuebles encontrados: {len(inmuebles_encontrados)}

DETALLES DE LA PARCELA:
- Provincia: {provincia}
- Municipio: {municipio}  
- Sector: {sector}
- Manzana: {manzana}
- Parcela: {parcela}

INMUEBLES EN LA PARCELA:
                """.strip()
                
                for i, inmueble in enumerate(inmuebles_encontrados, 1):
                    ref = inmueble.get("referencia_catastral", "No disponible")
                    subparcela = inmueble.get("subparcela", "N/A")
                    uso = inmueble.get("uso", "No especificado")
                    superficie = inmueble.get("superficie", "No especificada")
                    direccion = inmueble.get("direccion", "No especificada")
                    antiguedad = inmueble.get("antiguedad", "No especificada")
                    coeficiente = inmueble.get("coeficiente_participacion", "No especificado")
                    escalera = inmueble.get("escalera", "")
                    planta = inmueble.get("planta", "")
                    puerta = inmueble.get("puerta", "")
                    
                    mensaje_resultado += f"\n\n{i}. INMUEBLE {subparcela}:"
                    mensaje_resultado += f"\n   - Referencia completa: {ref}"
                    if direccion and direccion != "No especificada":
                        mensaje_resultado += f"\n   - Ubicacion: {direccion}"
                    if uso and uso != "No especificado":
                        mensaje_resultado += f"\n   - Uso: {uso}"
                    if superficie and superficie != "No especificada":
                        mensaje_resultado += f"\n   - Superficie: {superficie} m2"
                    if coeficiente and coeficiente != "No especificado":
                        mensaje_resultado += f"\n   - Coeficiente participacion: {coeficiente}%"
                    if antiguedad and antiguedad != "No especificada":
                        mensaje_resultado += f"\n   - Año construccion: {antiguedad}"
                
                mensaje_resultado += f"""

RECOMENDACIONES:
- Para consultar un inmueble especifico use: consultar_catastro_por_referencia
- Para mas inmuebles en esta parcela visite: https://sede.catastro.gob.es
- Busque por el codigo de parcela: {codigo_parcela}
                """.strip()
                
                return CatastroResponse(
                    referencia_catastral=codigo_parcela,
                    estado_consulta="exitosa",
                    mensaje_error=mensaje_resultado,
                    datos_raw={
                        "tipo_consulta": "parcela_con_division_horizontal",
                        "codigo_parcela": codigo_parcela,
                        "metodo_api": "Consulta_DNPRC con 14 caracteres (oficial)",
                        "componentes": {
                            "provincia": provincia,
                            "municipio": municipio,
                            "sector": sector,
                            "manzana": manzana,
                            "parcela": parcela
                        },
                        "inmuebles_encontrados": inmuebles_encontrados,
                        "total_inmuebles": len(inmuebles_encontrados),
                        "respuesta_api_original": datos_parseados
                    }
                )
            else:
                # No se encontraron inmuebles
                mensaje_info = f"""
PARCELA NO ENCONTRADA O SIN INMUEBLES

Codigo de parcela: {codigo_parcela}

Se intento buscar inmuebles en esta parcela pero no se encontraron resultados.

COMPONENTES ANALIZADOS:
- Provincia: {provincia}
- Municipio: {municipio}
- Sector: {sector}  
- Manzana: {manzana}
- Parcela: {parcela}

POSIBLES CAUSAS:
1. La parcela no existe en el catastro
2. La parcela no tiene inmuebles registrados
3. El codigo proporcionado es incorrecto
4. Los inmuebles usan subparcelas diferentes a las probadas

ALTERNATIVAS:
1. Verificar el codigo en https://sede.catastro.gob.es
2. Usar la referencia catastral completa (20 caracteres) si la tiene
3. Buscar por direccion en la sede electronica
4. Usar coordenadas GPS si conoce la ubicacion exacta
                """.strip()
                
                return CatastroResponse(
                    referencia_catastral=codigo_parcela,
                    estado_consulta="sin_datos",
                    mensaje_error=mensaje_info,
                    datos_raw={
                        "tipo_consulta": "parcela_sin_resultados",
                        "codigo_parcela": codigo_parcela,
                        "componentes": {
                            "provincia": provincia,
                            "municipio": municipio,
                            "sector": sector,
                            "manzana": manzana,
                            "parcela": parcela
                        }
                    }
                )
                
        except Exception as e:
            logger.error(f"Error buscando inmuebles en parcela {codigo_parcela}: {str(e)}")
            return CatastroResponse(
                referencia_catastral=codigo_parcela,
                estado_consulta="error",
                mensaje_error=f"Error interno buscando inmuebles en la parcela: {str(e)}"
            )
    
    def _extraer_inmuebles_division_horizontal(self, datos: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extrae información de múltiples inmuebles en una parcela con división horizontal
        Procesa la estructura real de la API: consulta_dnprcResult.lrcdnp.rcdnp[]
        """
        inmuebles = []
        
        try:
            # Estructura real de la API del Catastro para consultas con múltiples inmuebles
            consulta_result = datos.get('consulta_dnprcResult', {})
            lrcdnp = consulta_result.get('lrcdnp', {})
            rcdnp_array = lrcdnp.get('rcdnp', [])
            
            if isinstance(rcdnp_array, list):
                for inmueble_data in rcdnp_array:
                    inmueble_info = self._procesar_inmueble_catastro_oficial(inmueble_data)
                    if inmueble_info:
                        inmuebles.append(inmueble_info)
            elif isinstance(rcdnp_array, dict):
                # Un solo inmueble
                inmueble_info = self._procesar_inmueble_catastro_oficial(rcdnp_array)
                if inmueble_info:
                    inmuebles.append(inmueble_info)
            
            logger.info(f"Extraídos {len(inmuebles)} inmuebles de la respuesta oficial")
            return inmuebles
            
        except Exception as e:
            logger.warning(f"Error extrayendo inmuebles de división horizontal: {str(e)}")
            return []
    
    def _procesar_inmueble_individual(self, bi_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Procesa los datos de un inmueble individual
        """
        try:
            # Extraer referencia catastral
            referencia = ""
            if 'idbi' in bi_data:
                referencia = bi_data['idbi'].get('rc', '')
            
            # Extraer datos básicos
            debi = bi_data.get('debi', {})
            uso = debi.get('luso', debi.get('uso', 'No especificado'))
            superficie = debi.get('sfc', 'No especificada')
            antiguedad = debi.get('ant', 'No especificada')
            
            # Extraer dirección
            via = bi_data.get('tv', '') + ' ' + bi_data.get('nv', '')
            numero = bi_data.get('num', '')
            planta = bi_data.get('planta', '')
            puerta = bi_data.get('puerta', '')
            
            direccion_completa = f"{via.strip()} {numero}".strip()
            if planta:
                direccion_completa += f", Planta {planta}"
            if puerta:
                direccion_completa += f", Puerta {puerta}"
            
            return {
                "referencia_catastral": referencia,
                "uso": uso,
                "superficie": superficie,
                "antiguedad": antiguedad,
                "direccion": direccion_completa,
                "datos_raw": bi_data
            }
            
        except Exception as e:
            logger.warning(f"Error procesando inmueble individual: {str(e)}")
            return None
    
    def _procesar_inmueble_catastro_oficial(self, rcdnp_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Procesa los datos de un inmueble según la estructura oficial de la API
        Estructura: rcdnp{ rc{}, dt{}, debi{} }
        """
        try:
            # Extraer referencia catastral completa
            rc = rcdnp_data.get('rc', {})
            pc1 = rc.get('pc1', '')  # Primeros 7 caracteres
            pc2 = rc.get('pc2', '')  # Siguientes 7 caracteres  
            car = rc.get('car', '')  # Subparcela (4 caracteres)
            cc1 = rc.get('cc1', '')  # Control 1
            cc2 = rc.get('cc2', '')  # Control 2
            
            referencia_completa = f"{pc1}{pc2}{car}{cc1}{cc2}"
            
            # Extraer datos básicos del inmueble
            debi = rcdnp_data.get('debi', {})
            uso = debi.get('luso', 'No especificado')
            superficie = debi.get('sfc', 'No especificada')
            antiguedad = debi.get('ant', 'No especificada')
            coeficiente = debi.get('cpt', 'No especificado')
            
            # Extraer datos de ubicación
            dt = rcdnp_data.get('dt', {})
            
            # Provincia y municipio
            loine = dt.get('loine', {})
            provincia_codigo = loine.get('cp', '')
            municipio_codigo = loine.get('cm', '')
            provincia_nombre = dt.get('np', '')
            municipio_nombre = dt.get('nm', '')
            
            # Dirección
            locs = dt.get('locs', {})
            lous = locs.get('lous', {})
            lourb = lous.get('lourb', {})
            
            # Vía
            dir_info = lourb.get('dir', {})
            tipo_via = dir_info.get('tv', '')
            nombre_via = dir_info.get('nv', '')
            
            # Información interior
            loint = lourb.get('loint', {})
            escalera = loint.get('es', '')
            planta = loint.get('pt', '')
            puerta = loint.get('pu', '')
            
            # Construir dirección completa
            direccion_partes = []
            if tipo_via and nombre_via:
                direccion_partes.append(f"{tipo_via} {nombre_via}")
            if escalera:
                direccion_partes.append(f"Escalera {escalera}")
            if planta:
                direccion_partes.append(f"Planta {planta}")
            if puerta:
                direccion_partes.append(f"Puerta {puerta}")
            
            direccion_completa = ", ".join(direccion_partes)
            
            return {
                "referencia_catastral": referencia_completa,
                "subparcela": car,
                "uso": uso,
                "superficie": superficie,
                "antiguedad": antiguedad,
                "coeficiente_participacion": coeficiente,
                "direccion": direccion_completa,
                "provincia": f"{provincia_nombre} ({provincia_codigo})",
                "municipio": f"{municipio_nombre} ({municipio_codigo})",
                "escalera": escalera,
                "planta": planta,
                "puerta": puerta,
                "datos_raw": rcdnp_data
            }
            
        except Exception as e:
            logger.warning(f"Error procesando inmueble oficial: {str(e)}")
            return None