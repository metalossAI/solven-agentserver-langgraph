"""
Modelos de datos para el servicio de Catastro
"""

import re
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, validator
from datetime import datetime


class ReferenciaCatastral(BaseModel):
    """Modelo para validar referencias catastrales españolas"""
    
    referencia: str = Field(
        ..., 
        description="Referencia catastral de 20 caracteres",
        min_length=20,
        max_length=20
    )
    
    @validator('referencia')
    def validar_formato(cls, v):
        """Valida únicamente el formato básico de la referencia catastral"""
        if not cls.validar_formato_estatico(v):
            raise ValueError('Formato de referencia catastral inválido')
        return v.upper()
    
    @staticmethod
    def validar_formato_estatico(referencia: str) -> bool:
        """Valida el formato básico de una referencia catastral (20 caracteres alfanuméricos)"""
        # Simplificamos: solo verificamos que tenga 20 caracteres alfanuméricos
        # Primero limpiamos espacios y convertimos a mayúsculas
        ref_limpia = referencia.replace(' ', '').upper()
        
        # Verificar longitud exacta
        if len(ref_limpia) != 20:
            return False
            
        # Verificar que solo contenga números y letras mayúsculas
        patron = r'^[0-9A-Z]{20}$'
        return bool(re.match(patron, ref_limpia))

    @staticmethod
    def analizar_referencia_detallado(referencia: str) -> dict:
        """
        Análisis detallado de una referencia catastral, incluyendo referencias parciales
        
        Returns:
            dict con información detallada sobre la referencia
        """
        ref_limpia = referencia.replace(' ', '').upper() if referencia else ""
        longitud = len(ref_limpia)
        
        # Patrón para caracteres válidos
        patron_valido = r'^[0-9A-Z]+$'
        caracteres_validos = bool(re.match(patron_valido, ref_limpia)) if ref_limpia else False
        
        analisis = {
            "referencia_original": referencia,
            "referencia_limpia": ref_limpia,
            "longitud": longitud,
            "caracteres_validos": caracteres_validos,
            "es_referencia_completa": longitud == 20 and caracteres_validos,
        }
        
        # Análisis específico según longitud
        if longitud == 0:
            analisis.update({
                "tipo": "vacia",
                "estado": "error",
                "mensaje": "Referencia vacía"
            })
        elif longitud == 14:
            analisis.update({
                "tipo": "parcial_14_caracteres", 
                "estado": "incompleta",
                "mensaje": "Referencia parcial (14 caracteres). Puede ser código de parcela o polígono.",
                "explicacion": f"""
La referencia '{ref_limpia}' tiene 14 caracteres, que corresponde a:
• Código de parcela catastral (sin dígitos de control ni subparcela)
• Formato: PROVINCIA(2) + MUNICIPIO(3) + SECTOR(2) + MANZANA(3) + PARCELA(4)

Para obtener la referencia catastral completa (20 caracteres):
1. Visita https://sede.catastro.gob.es
2. Busca por dirección o usa este código parcial
3. Copia la referencia completa que incluye:
   - Los 14 caracteres que tienes: {ref_limpia}
   - + 4 caracteres de subparcela (ej: 0001)  
   - + 2 dígitos de control (ej: AB)
   
Formato completo esperado: {ref_limpia}XXXXXX (donde X son los 6 caracteres faltantes)
                """.strip()
            })
        elif longitud < 20:
            analisis.update({
                "tipo": f"incompleta_{longitud}_caracteres",
                "estado": "incompleta", 
                "mensaje": f"Referencia incompleta ({longitud}/20 caracteres)",
                "explicacion": f"""
La referencia '{ref_limpia}' tiene {longitud} caracteres pero necesita exactamente 20.

Faltan {20 - longitud} caracteres para completar la referencia catastral.

Estructura completa de referencia catastral (20 caracteres):
• Posiciones 1-14: Código de parcela (lo que tienes parcialmente)
• Posiciones 15-18: Código de subparcela (ej: 0001)  
• Posiciones 19-20: Dígitos de control (ej: AB)

Para obtener la referencia completa:
1. Visita https://sede.catastro.gob.es
2. Busca usando esta referencia parcial o por dirección
3. Copia la referencia completa de 20 caracteres
                """.strip()
            })
        elif longitud > 20:
            analisis.update({
                "tipo": "demasiado_larga",
                "estado": "error",
                "mensaje": f"Referencia demasiado larga ({longitud} caracteres, máximo 20)",
                "explicacion": f"Las referencias catastrales españolas tienen exactamente 20 caracteres. Verifica que no haya caracteres extra."
            })
        elif longitud == 20:
            if caracteres_validos:
                analisis.update({
                    "tipo": "completa_valida",
                    "estado": "valida",
                    "mensaje": "Referencia catastral válida (20 caracteres alfanuméricos)"
                })
            else:
                caracteres_invalidos = [c for c in ref_limpia if not c.isalnum()]
                analisis.update({
                    "tipo": "completa_invalida",
                    "estado": "error",
                    "mensaje": "Referencia de 20 caracteres pero con caracteres inválidos",
                    "caracteres_invalidos": caracteres_invalidos,
                    "explicacion": f"Solo se permiten números (0-9) y letras mayúsculas (A-Z). Caracteres problemáticos: {', '.join(caracteres_invalidos)}"
                })
        
        return analisis


class Coordenadas(BaseModel):
    """Coordenadas geográficas"""
    latitud: float = Field(..., ge=35.0, le=44.0, description="Latitud en grados decimales")
    longitud: float = Field(..., ge=-10.0, le=5.0, description="Longitud en grados decimales")
    sistema: str = Field(default="WGS84", description="Sistema de coordenadas")


class DatosBasicosInmueble(BaseModel):
    """Datos básicos de un inmueble catastral"""
    uso: Optional[str] = Field(None, description="Uso del inmueble")
    superficie_construida: Optional[float] = Field(None, description="Superficie construida en m²")
    superficie_suelo: Optional[float] = Field(None, description="Superficie de suelo en m²")
    antiguedad: Optional[int] = Field(None, description="Año de construcción")
    plantas: Optional[int] = Field(None, description="Número de plantas")


class DireccionCatastral(BaseModel):
    """Dirección catastral del inmueble"""
    via: Optional[str] = Field(None, description="Tipo y nombre de vía")
    numero: Optional[str] = Field(None, description="Número")
    planta: Optional[str] = Field(None, description="Planta")
    puerta: Optional[str] = Field(None, description="Puerta")
    codigo_postal: Optional[str] = Field(None, description="Código postal")
    municipio: Optional[str] = Field(None, description="Municipio")
    provincia: Optional[str] = Field(None, description="Provincia")


class ValorCatastral(BaseModel):
    """Valores catastrales del inmueble"""
    valor_catastral: Optional[float] = Field(None, description="Valor catastral total")
    valor_suelo: Optional[float] = Field(None, description="Valor catastral del suelo")
    valor_construccion: Optional[float] = Field(None, description="Valor catastral de la construcción")
    año_valor: Optional[int] = Field(None, description="Año de los valores")


class CatastroResponse(BaseModel):
    """Respuesta completa de una consulta catastral"""
    referencia_catastral: str
    datos_basicos: Optional[DatosBasicosInmueble] = None
    direccion: Optional[DireccionCatastral] = None
    valores: Optional[ValorCatastral] = None
    coordenadas: Optional[Coordenadas] = None
    fecha_consulta: datetime = Field(default_factory=datetime.now)
    estado_consulta: str = Field(default="exitosa")
    mensaje_error: Optional[str] = None
    datos_raw: Optional[Dict[str, Any]] = Field(None, description="Datos originales de la API")


class ConsultaPorCoordenadas(BaseModel):
    """Modelo para consultas por coordenadas"""
    latitud: float = Field(..., ge=35.0, le=44.0)
    longitud: float = Field(..., ge=-10.0, le=5.0)
    radio_busqueda: Optional[int] = Field(default=100, description="Radio de búsqueda en metros")


class ResumenIA(BaseModel):
    """Modelo para el resumen generado por IA"""
    referencia_catastral: str
    resumen: str
    idioma: str = Field(default="es")
    modelo_usado: str = Field(default="simulado")
    fecha_generacion: datetime = Field(default_factory=datetime.now)
    puntos_clave: Optional[List[str]] = None
    calidad_datos: Optional[str] = None


class ErrorCatastral(BaseModel):
    """Modelo para errores del servicio catastral"""
    codigo_error: str
    mensaje: str
    detalles: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)


class EstadisticasConsulta(BaseModel):
    """Estadísticas de consultas realizadas"""
    total_consultas: int = 0
    consultas_exitosas: int = 0
    consultas_fallidas: int = 0
    tiempo_promedio_respuesta: float = 0.0
    fecha_inicio: datetime = Field(default_factory=datetime.now)
    ultima_consulta: Optional[datetime] = None