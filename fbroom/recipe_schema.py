from pydantic import BaseModel
from typing import List, Optional


class CleaningStep(BaseModel):
    action: str
    column: Optional[str]
    params: Optional[dict] = {}


class JoinSpec(BaseModel):
    left: str
    right: str
    keys: Optional[List[str]] = []


class Recipe(BaseModel):
    sources: List[dict]
    cleaning_steps: List[CleaningStep] = []
    joins: Optional[List[JoinSpec]] = []
    outputs: List[dict] = []
