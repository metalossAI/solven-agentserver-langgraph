from langsmith import Client
import os
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate

load_dotenv()

def generate_prompt_template(name : str, language : str, profile : str):
    client = Client()
    main_prompt : ChatPromptTemplate = client.pull_prompt("solven-main")
    formatted_prompt = main_prompt.format(
        name=name.capitalize(),
        language=language.lower(),
        profile=profile
    )
    return formatted_prompt