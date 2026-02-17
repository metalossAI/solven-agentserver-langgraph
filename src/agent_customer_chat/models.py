from langgraph.graph import MessagesState
from typing import Optional
from src.models import Ticket

class CustomerChatState(MessagesState):
    ticket : Optional[Ticket] = None # the upstandig ticket context which will serve as link wiht for customer communications