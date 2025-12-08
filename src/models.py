from dataclasses import dataclass

@dataclass
class AppContext:
    thread_id: str
    user_id: str
    tenant_id: str