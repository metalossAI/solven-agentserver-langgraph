from langchain_core.tools import tool

@tool
def get_item_from_store(item_id: str) -> str:
    return "Item " + item_id