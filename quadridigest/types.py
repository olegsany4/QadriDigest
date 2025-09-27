
from typing import Literal, List, Optional
from pydantic import BaseModel, Field

Topic = Literal["politics", "trading", "pf", "infosec"]

class CleanItem(BaseModel):
    text: str
    lang: str = "ru"

class LLMAnnotation(BaseModel):
    version: str = "V2"
    topic: Topic
    headline: str = Field(..., max_length=120)
    summary: str = Field(..., max_length=320)
    fingerprint: str = Field(..., min_length=8, max_length=40)
    evidence: List[str] = []

class FullText(BaseModel):
    full_text: str

class PostRecord(BaseModel):
    topic: Topic
    fingerprint: str
    headline: str
    summary: str
    full_text: str
    channel: str
    message_id: Optional[int] = None
    expanded: bool = False
    prompt_version: str = "V2"
