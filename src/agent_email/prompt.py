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
	composio = Composio(provider=LangchainProvider())

	all_configs = {
		"gmail": os.getenv("COMPOSIO_AUTH_CONFIG_GMAIL"),
		"outlook": os.getenv("COMPOSIO_AUTH_CONFIG_OUTLOOK")
	}

	accounts = composio.connected_accounts.list(
		user_ids=[user_id]
	)

	for config in accounts.items:
		if config.toolkit.slug == "gmail" and config.auth_config.id == all_configs["gmail"]:
			gmail_account = config.toolkit.slug
			gmail_status = config.status
		elif config.toolkit.slug == "outlook" and config.auth_config.id == all_configs["outlook"]:
			outlook_account = config.toolkit.slug
			outlook_status = config.status
	main_prompt : ChatPromptTemplate = client.pull_prompt("solven-subagent-email")
	formatted_prompt = main_prompt.format(
		gmail_account=gmail_account,
		gmail_status=gmail_status,
		outlook_account=outlook_account,
		outlook_status=outlook_status
	)
	print(formatted_prompt)
	return formatted_prompt
