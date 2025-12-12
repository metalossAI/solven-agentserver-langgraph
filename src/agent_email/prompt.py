from langsmith import Client
import os
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from composio import Composio
from composio_langchain import LangchainProvider

load_dotenv()


def generate_email_prompt_template(
	user_id : str,
):
	client = Client()
	main_prompt : ChatPromptTemplate = client.pull_prompt("solven-subagent-email")
	formatted_prompt = main_prompt.format()
	print(formatted_prompt)
	return formatted_prompt
