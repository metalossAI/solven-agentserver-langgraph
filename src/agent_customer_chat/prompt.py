from langsmith import Client
import os
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from datetime import datetime
load_dotenv()

client = Client()
main_prompt : ChatPromptTemplate = client.pull_prompt("solven-customer-chat")