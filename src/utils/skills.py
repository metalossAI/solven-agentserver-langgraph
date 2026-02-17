"""
Utility functions for skill matching and management.
"""
from typing import List, Literal
from pydantic import BaseModel, Field, field_validator
from langchain_core.prompts import ChatPromptTemplate

from src.models import Skill
from src.backend import S3Backend
from src.llm import LLM_SKILL_MATCHING


def create_skill_match_result_model(available_skill_names: List[str]):
	"""
	Create a SkillMatchResult model with Literal constraints based on available skills.
	This ensures the LLM can only return valid skill names.
	"""
	if not available_skill_names:
		SkillNameLiteral = Literal[""]
	else:
		SkillNameLiteral = Literal[tuple(available_skill_names)]
	
	class SkillMatchResult(BaseModel):
		"""Salida estructurada para la coincidencia de habilidades."""
		matched_skill_names: List[SkillNameLiteral] = Field(
			description=f"Lista de nombres de habilidades que coinciden con el contexto de la conversación. Máximo 3 habilidades, ordenadas por relevancia. Habilidades disponibles: {', '.join(available_skill_names)}"
		)
		
		@field_validator('matched_skill_names')
		@classmethod
		def validate_skill_names(cls, v: List[str]) -> List[str]:
			"""Validate that all skill names are in the available list."""
			valid_names = []
			for name in v:
				if name in available_skill_names:
					valid_names.append(name)
			# Limit to 3 skills
			return valid_names[:3]
	
	return SkillMatchResult


async def match_skills_with_conversation(skills: List[Skill], conversation_text: str) -> List[Skill]:
	"""
	Match skills with conversation context using an LLM agent with structured output.
	The agent analyzes the conversation history and selects relevant skills.
	
	Args:
		skills: List of available skills to match against
		conversation_text: Combined text from recent conversation messages
		
	Returns:
		List of matched skills sorted by relevance (max 3)
	"""
	if not skills:
		return []
	
	try:
		# Extract skill names for Literal type constraint
		available_skill_names = [s.name for s in skills]
		
		# Create dynamic model with Literal constraints
		SkillMatchResult = create_skill_match_result_model(available_skill_names)
		
		# Build formatted list of available skills
		skills_list = "\n".join([
			f"{i+1}. **{s.name}**: {s.description}"
			for i, s in enumerate(skills)
		])
		
		# Create prompt for the agent
		prompt = ChatPromptTemplate.from_messages([
			("system", """Eres un agente especializado en analizar conversaciones y seleccionar las habilidades más relevantes para el contexto.

Tu tarea es analizar el historial de conversación y determinar qué habilidades deberían cargarse para ayudar al usuario.

Habilidades disponibles:
{skills_list}

IMPORTANTE: 
- Solo puedes seleccionar nombres de habilidades que aparezcan EXACTAMENTE en la lista anterior
- Los nombres disponibles son: {skill_names}
- Analiza el contexto completo de la conversación, no solo el último mensaje
- Considera la intención general y el flujo de la conversación

Instrucciones:
- Selecciona hasta 3 habilidades que mejor se alineen con el contexto de la conversación
- Ordénalas por relevancia (la más relevante primero)
- Solo selecciona habilidades que sean claramente relevantes para el contexto actual
- Si ninguna habilidad es relevante, devuelve una lista vacía
- Usa EXACTAMENTE los nombres de las habilidades tal como aparecen en la lista"""),
			("human", """Contexto de la conversación (últimos mensajes):

{conversation_context}

¿Qué habilidades deberían cargarse basándose en este contexto de conversación?""")
		])
		
		# Create structured output chain with constrained model
		structured_llm = LLM_SKILL_MATCHING.with_structured_output(SkillMatchResult)
		chain = prompt | structured_llm
		
		# Get LLM response
		result: SkillMatchResult = await chain.ainvoke({
			"skills_list": skills_list,
			"skill_names": ", ".join(available_skill_names),
			"conversation_context": conversation_text
		})
		
		# Map skill names back to Skill objects (validation already done in model)
		skill_map = {s.name: s for s in skills}
		matched_skills = []
		
		for skill_name in result.matched_skill_names:
			if skill_name in skill_map:
				matched_skills.append(skill_map[skill_name])
		
		return matched_skills
		
	except Exception:
		# Fallback to keyword-based matching
		return match_skills_with_keywords(skills, conversation_text)


def match_skills_with_keywords(skills: List[Skill], message: str) -> List[Skill]:
	"""
	Fallback keyword-based skill matching when LLM fails.
	Matches skills based on keyword presence in skill name/description.
	"""
	message_lower = message.lower()
	matched = []
	
	# Extract keywords from message (simple approach)
	# Look for common skill-related keywords
	for skill in skills:
		# Extract key terms from skill name (split by hyphens)
		skill_keywords = skill.name.replace('-', ' ').split()
		# Also use description keywords
		desc_keywords = skill.description.lower().split()
		
		# Check if any keywords match
		score = 0
		for keyword in skill_keywords + desc_keywords:
			if len(keyword) > 3 and keyword.lower() in message_lower:
				score += 1
		
		if score > 0:
			matched.append((score, skill))
	
	# Sort by score and return top matches
	matched.sort(key=lambda x: x[0], reverse=True)
	result = [skill for score, skill in matched[:3]]
	
	
	return result

