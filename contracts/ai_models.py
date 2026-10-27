"""Typed generation contracts. Application grounding checks live in the gateway."""
from typing import Literal
from pydantic import BaseModel, ConfigDict

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class TopicProposal(StrictModel):
    title: str
    hypothesis: str
    rationale: str
    source_ids: list[str]
    limitations: str

class TopicBatch(StrictModel):
    proposals: list[TopicProposal]
    grounding_note: str

class AdviceItem(StrictModel):
    category: Literal["clarity", "method", "argument", "references", "structure"]
    unit_id: str
    evidence: str
    suggestion: str
    rationale: str
    uncertainty: str
    source_ids: list[str]

class AdviceBatch(StrictModel):
    suggestions: list[AdviceItem]
    limitations: str

