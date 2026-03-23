from langgraph.types import interrupt
from typing import Any

async def ask(
    question: str,
    options: list[str],
    allow_other: bool = True,
) -> Any:
    """
    Ask the user for additional context.
    """
    interrupt_payload = {
        "action": "ask",
        "question": question,
        "options": options,
        "allow_other": allow_other,
    }
    return interrupt(interrupt_payload)