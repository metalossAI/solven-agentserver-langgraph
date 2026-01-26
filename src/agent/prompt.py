from datetime import datetime
from dotenv import load_dotenv
from langsmith import AsyncClient, Client
from langchain_core.prompts import ChatPromptTemplate

load_dotenv()

client = Client()

def get_prompt_variables(name: str, profile: str, language: str = "español", context_title: str = "", context_description: str = "") -> dict:
    """
    Genera las variables que serán usadas por el middleware para construir el prompt completo.
    
    El prompt base será cargado por el middleware desde LangSmith y formateado con estas variables.
    El middleware también inyectará dinámicamente la sección SKILL con:
    - Lista de skills disponibles (cuando no hay skill cargado)
    - Contenido completo del SKILL.md (cuando hay un skill cargado)
    
    Args:
        name: Nombre del usuario
        profile: Perfil del usuario (rol, email, etc.)
        language: Idioma preferido del usuario
        context_title: Título del thread actual
        context_description: Descripción del thread actual
    
    Returns:
        Diccionario con las variables para el prompt
    """
    return {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "name": name.capitalize(),
        "language": language.lower(),
        "profile": profile,
        "initial_context_title": context_title or "Sin contexto específico",
        "initial_context_description": context_description or "Conversación general",
    }

async def generate_prompt_template(
	name: str, 
	profile: str, 
	language: str = "español", 
	context_title: str = "", 
	context_description: str = "",
	skills: list[str] = []
) -> str:
	"""
	Generate the system prompt by loading the template from LangSmith and formatting it
	with user variables and skills frontmatter.
	
	Args:
		name: Nombre del usuario
		profile: Perfil del usuario (rol, email, etc.)
		language: Idioma preferido del usuario
		context_title: Título del thread actual
		context_description: Descripción del thread actual
		skills_frontmatter: Raw concatenated YAML frontmatter blocks string from backend
	
	Returns:
		Formatted system prompt string
	"""
	# Load prompt template from LangSmith
	client = AsyncClient()
	base_prompt: ChatPromptTemplate = await client.pull_prompt("solven-main-skills")
	
	base_prompt = base_prompt.format(
        date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        name=name,
        profile=profile,  
        language=language,
        context_title=context_title,
        context_description=context_description,
        skills=skills,
    )
	return base_prompt
