"""
Configuración del servicio MCP Catastro
"""

import os
from typing import Optional


class Settings:
    """Configuración de la aplicación"""
    
    def __init__(self):
        # Configuración del servicio
        self.app_name = os.getenv("CATASTRO_APP_NAME", "MCP Catastro España")
        self.app_version = os.getenv("CATASTRO_APP_VERSION", "1.0.0")
        self.debug = os.getenv("CATASTRO_DEBUG", "false").lower() == "true"
        
        # API del Catastro
        self.catastro_base_url = os.getenv("CATASTRO_CATASTRO_BASE_URL", "https://ovc.catastro.meh.es")
        self.catastro_timeout = int(os.getenv("CATASTRO_CATASTRO_TIMEOUT", "30"))
        self.catastro_max_retries = int(os.getenv("CATASTRO_CATASTRO_MAX_RETRIES", "3"))
        self.catastro_retry_delay = float(os.getenv("CATASTRO_CATASTRO_RETRY_DELAY", "1.0"))
        
        # Rate limiting
        self.max_requests_per_minute = int(os.getenv("CATASTRO_MAX_REQUESTS_PER_MINUTE", "60"))
        self.max_requests_per_hour = int(os.getenv("CATASTRO_MAX_REQUESTS_PER_HOUR", "1000"))
        
        # OpenAI (opcional)
        self.openai_api_key = os.getenv("CATASTRO_OPENAI_API_KEY")
        self.openai_model = os.getenv("CATASTRO_OPENAI_MODEL", "gpt-4")
        self.openai_max_tokens = int(os.getenv("CATASTRO_OPENAI_MAX_TOKENS", "500"))
        self.openai_temperature = float(os.getenv("CATASTRO_OPENAI_TEMPERATURE", "0.3"))
        
        # Logging
        self.log_level = os.getenv("CATASTRO_LOG_LEVEL", "INFO")
        self.log_format = os.getenv("CATASTRO_LOG_FORMAT", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        
        # Cache (opcional)
        self.enable_cache = os.getenv("CATASTRO_ENABLE_CACHE", "true").lower() == "true"
        self.cache_ttl_seconds = int(os.getenv("CATASTRO_CACHE_TTL_SECONDS", "3600"))
        self.cache_max_size = int(os.getenv("CATASTRO_CACHE_MAX_SIZE", "1000"))
        
        # Métricas
        self.enable_metrics = os.getenv("CATASTRO_ENABLE_METRICS", "true").lower() == "true"
        self.metrics_port = int(os.getenv("CATASTRO_METRICS_PORT", "9090"))
        
        # Paths de documentación
        self.docs_path = os.getenv("CATASTRO_DOCS_PATH", "/docs")
        self.redoc_path = os.getenv("CATASTRO_REDOC_PATH", "/redoc")


# Configuración específica de endpoints del Catastro
class CatastroEndpoints:
    """URLs de los servicios web del Catastro - API WCF Oficial"""
    
    # Base paths para servicios WCF
    CALLEJERO_BASE = "/OVCServWeb/OVCWcfCallejero/COVCCallejero.svc"
    CALLEJERO_CODIGOS_BASE = "/OVCServWeb/OVCWcfCallejero/COVCCallejeroCodigos.svc"
    COORDENADAS_BASE = "/OVCServWeb/OVCWcfCallejero/COVCCoordenadas.svc"
    
    # Consultas por denominación (REST JSON)
    CONSULTA_PROVINCIA = f"{CALLEJERO_BASE}/json/ConsultaProvincia"
    CONSULTA_MUNICIPIO = f"{CALLEJERO_BASE}/json/ConsultaMunicipio"
    CONSULTA_VIA = f"{CALLEJERO_BASE}/json/ConsultaVia"
    CONSULTA_NUMERO = f"{CALLEJERO_BASE}/json/ConsultaNumero"
    CONSULTA_DNPLOC = f"{CALLEJERO_BASE}/json/Consulta_DNPLOC"
    CONSULTA_DNPRC = f"{CALLEJERO_BASE}/json/Consulta_DNPRC"
    CONSULTA_DNPPP = f"{CALLEJERO_BASE}/json/Consulta_DNPPP"
    
    # Consultas por códigos (REST JSON)
    CONSULTA_MUNICIPIO_CODIGOS = f"{CALLEJERO_CODIGOS_BASE}/json/ConsultaMunicipioCodigos"
    CONSULTA_VIA_CODIGOS = f"{CALLEJERO_CODIGOS_BASE}/json/ConsultaViaCodigos"
    CONSULTA_NUMERO_CODIGOS = f"{CALLEJERO_CODIGOS_BASE}/json/ConsultaNumeroCodigos"
    CONSULTA_DNPLOC_CODIGOS = f"{CALLEJERO_CODIGOS_BASE}/json/Consulta_DNPLOC_Codigos"
    CONSULTA_DNPRC_CODIGOS = f"{CALLEJERO_CODIGOS_BASE}/json/Consulta_DNPRC_Codigos"
    CONSULTA_DNPPP_CODIGOS = f"{CALLEJERO_CODIGOS_BASE}/json/Consulta_DNPPP_Codigos"
    
    # Servicios de coordenadas (REST JSON)
    CONSULTA_RCCOOR = f"{COORDENADAS_BASE}/json/Consulta_RCCOOR"
    CONSULTA_RCCOOR_DISTANCIA = f"{COORDENADAS_BASE}/json/Consulta_RCCOOR_Distancia"
    CONSULTA_CPMRC = f"{COORDENADAS_BASE}/json/Consulta_CPMRC"
    
    # Consulta por referencia catastral (principal)
    CONSULTA_DNP = CONSULTA_DNPRC
    
    # Aliases para compatibilidad
    CONSULTA_COORDENADAS = CONSULTA_RCCOOR  # Por coordenadas
    CONSULTA_MUNICIPIOS = CONSULTA_MUNICIPIO  # Municipios
    CONSULTA_PROVINCIAS = CONSULTA_PROVINCIA  # Provincias
    
    # URLs de ayuda para desarrollo
    HELP_CALLEJERO = f"{CALLEJERO_BASE}/json/help"
    HELP_CODIGOS = f"{CALLEJERO_CODIGOS_BASE}/json/help"
    HELP_COORDENADAS = f"{COORDENADAS_BASE}/json/help"


# Configuración de logging
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        },
        "detailed": {
            "format": "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d: %(message)s"
        }
    },
    "handlers": {
        "default": {
            "level": "INFO",
            "formatter": "standard",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout"
        },
        "file": {
            "level": "DEBUG",
            "formatter": "detailed",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": "logs/catastro_mcp.log",
            "maxBytes": 10485760,  # 10MB
            "backupCount": 5
        }
    },
    "loggers": {
        "": {
            "handlers": ["default", "file"],
            "level": "INFO",
            "propagate": False
        },
        "uvicorn": {
            "handlers": ["default"],
            "level": "INFO",
            "propagate": False
        }
    }
}


def get_settings() -> Settings:
    """Obtiene la configuración de la aplicación"""
    return Settings()


# Configuración de validación
VALIDATION_CONFIG = {
    "referencia_catastral": {
        "pattern": r"^[0-9]{7}[A-Z]{2}[0-9]{4}[A-Z]{1}[0-9]{4}[A-Z]{2}$",
        "length": 20
    },
    "coordenadas": {
        "latitud_min": 35.0,
        "latitud_max": 44.0,
        "longitud_min": -10.0,
        "longitud_max": 5.0
    }
}


# Mensajes de error estándar
ERROR_MESSAGES = {
    "REFERENCIA_INVALIDA": "El formato de la referencia catastral no es válido",
    "COORDENADAS_INVALIDAS": "Las coordenadas están fuera del rango válido para España",
    "SERVICIO_NO_DISPONIBLE": "El servicio del Catastro no está disponible temporalmente",
    "LIMITE_VELOCIDAD": "Se ha excedido el límite de consultas por minuto",
    "DATOS_NO_ENCONTRADOS": "No se encontraron datos para la consulta realizada",
    "ERROR_CONEXION": "Error de conexión con el servicio del Catastro",
    "TIMEOUT": "La consulta ha excedido el tiempo límite",
    "API_KEY_INVALIDA": "La clave de API de OpenAI no es válida o ha caducado"
}