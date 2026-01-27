from pydantic import BaseModel, Field
from typing import List, Optional, Literal

Resolution = Literal["1K", "2K", "4K"]
AspectRatio = Literal["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9", "auto"]

class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    imageUrls: List[str] = Field(default_factory=list)
    aspectRatio: AspectRatio = "1:1"
    resolution: Resolution = "1K"

class GenerateResponse(BaseModel):
    taskId: str

class TaskResponse(BaseModel):
    taskId: str
    successFlag: int
    response: Optional[dict] = None
    errorMessage: Optional[str] = None
