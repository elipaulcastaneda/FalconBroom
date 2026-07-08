from pydantic import BaseModel
from typing import List, Optional, Union


class CleaningStep(BaseModel):
    action: str
    # allow a single column or a list of columns to apply the same step
    column: Optional[Union[str, List[str]]]
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
