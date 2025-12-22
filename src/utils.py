
from typing import Dict, Any
import re
import yaml

from langgraph.graph.state import RunnableConfig
from src.models import AppContext

def parse_skillmd_frontmatter(skillmd: str) -> str:
    """
    Parse and extract the frontmatter from a skillmd file.
    
    Extracts YAML frontmatter from the beginning of a file in the format:
    ---
    name: compraventa-escrituras
    description: Redacta escrituras de compraventa...
    ---
    
    Args:
        skillmd: The content of the skillmd file as a string
        
    Returns:
        The frontmatter string (content between --- delimiters), or empty string if not found
    """
    # Match YAML frontmatter between --- delimiters at the start of the file
    frontmatter_pattern = r'^---\s*\n(.*?)\n---\s*\n'
    match = re.match(frontmatter_pattern, skillmd, re.DOTALL)
    
    if not match:
        return ""
    
    # Return the frontmatter content (group 1 is the content between the --- delimiters)
    return match.group(1)

