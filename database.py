from sqlmodel import SQLModel, Field, Session, create_engine, select
from typing import Optional, Annotated
import os
from dotenv import load_dotenv

load_dotenv()

class Textbook(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(index=True)
    author: str


class Chapter(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    textbook_id: int = Field(foreign_key="textbook.id")
    name: str


class Quiz(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    chapter_id: int = Field(foreign_key="chapter.id")
    questions: str  # JSON or a serialized string of quiz questions


class QuerySession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    chapter_id: int = Field(foreign_key="chapter.id")
    confusing_text: str


class QueryResponse(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="querysession.id")
    response_text: str

# Database Initialization
DATABASE_URL = os.getenv('MYSQL_URI')

engine = create_engine(DATABASE_URL)

# Function to create all tables
def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session