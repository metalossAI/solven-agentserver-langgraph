from pydantic import BaseModel

class CalendarEvent(BaseModel):
    id: str
    company_id: str
    created_by: str
    title: str
    description: Optional[str] = None
    start_date: str
    end_date: str
    location: Optional[str] = None
    attendees: List[str] = []
    color: str = '#3b82f6'
    all_day: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class CalendarEventCreate(BaseModel):
    title: str
    description: Optional[str] = None
    start_date: str
    end_date: str
    location: Optional[str] = None
    attendees: List[str] = []
    color: str = '#3b82f6'
    all_day: bool = False