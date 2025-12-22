from langsmith import Client
import os
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from datetime import datetime
load_dotenv()

def generate_prompt_template(name : str,  profile : str, language : str = "español", context_title : str = "", context_description : str = ""):
    client = Client()
    main_prompt : ChatPromptTemplate = client.pull_prompt("solven-main")
    formatted_prompt = main_prompt.format(
        date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        name=name.capitalize(),
        language=language.lower(),
        profile=profile,
        initial_context_title=context_title,
        initial_context_description=context_description,
    )
    return formatted_prompt