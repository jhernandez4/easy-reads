from sqlmodel import SQLModel, Field, Session, create_engine, select, Relationship, Column
from sqlalchemy import Enum
from sqlalchemy.types import Text
from typing import Optional, Annotated, Literal
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# User Model
class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    email: str = Field(index=True, unique=True)
    hashed_password: str

    # Relationship: Users own multiple textbooks
    textbooks: list["Textbook"] = Relationship(back_populates="owner")

class Textbook(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")  # Track who owns the textbook
    title: str = Field(index=True)
    author: Optional[str] = Field(default=None, nullable=True)

    # Relationship to User (Many-to-one)
    owner: User = Relationship(back_populates="textbooks")

    # Relationship to Chapters (one-to-many)
    chapters: list["Chapter"] = Relationship(back_populates="textbook")


class Chapter(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    textbook_id: int = Field(foreign_key="textbook.id")
    name: str

    # Relationship to Conversations (one-to-many)
    conversations: list["Conversation"] = Relationship(back_populates="chapter")

    # Relationship to Quizzes (one-to-many)
    quizzes: list["Quiz"] = Relationship(back_populates="chapter")

    # Relationship to Textbook (many-to-one)
    textbook: Textbook = Relationship(back_populates="chapters")


class Conversation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(max_length=255)
    chapter_id: int = Field(foreign_key="chapter.id")
    start_time: datetime = Field(default_factory=datetime.utcnow)
    end_time: Optional[datetime] = None

    # Relationship to Responses (one-to-many)
    responses: list["Response"] = Relationship(back_populates="conversation")
    
    # Relationship to Chapter (many-to-one)
    chapter: Chapter = Relationship(back_populates="conversations")

class Response(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversation.id")
    role: str # 'user' or 'model' only
    content: str = Field(sa_column=Column(Text))
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Relationship to Conversation (many-to-one)
    conversation: Conversation = Relationship(back_populates="responses")

class Quiz(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    chapter_id: int = Field(foreign_key="chapter.id")
    content: str = Field(sa_column=Column(Text))
    created_at: datetime = Field(default_factory=datetime.utcnow)  # Timestamp for when the quiz was created

    # Relationship to Questions (one-to-many)
    questions: list["Question"] = Relationship(back_populates="quiz")

    # Relationship to Chapter (many-to-one)
    chapter: Chapter = Relationship(back_populates="quizzes")

class Question(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    quiz_id: int = Field(foreign_key="quiz.id")
    content: str = Field(sa_column=Column(Text))
    question_type: str  # E.g., 'multiple choice', 'true/false', 'open-ended'
    correct_answer: str  # The correct answer for the question

    # Relationship to Quiz (many-to-one)
    quiz: Quiz = Relationship(back_populates="questions")



# Database Initialization
DATABASE_URL = os.getenv('MYSQL_URI')

engine = create_engine(DATABASE_URL)

# Function to create all tables
def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session