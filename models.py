from pydantic import BaseModel
from typing import Optional

class UserCreate(BaseModel):
    username: str
    email: str
    password: str

class TextbookCreate(BaseModel):
    title: str
    author: Optional[str] = None

class ChapterCreate(BaseModel):
    name: str

class PromptRequest(BaseModel):
    text: str

class TextbookUpdate(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None

class ChapterUpdate(BaseModel):
    name: Optional[str] = None