from fastapi import FastAPI, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jwt.exceptions import InvalidTokenError
from passlib.context import CryptContext
from pydantic import BaseModel
from typing import Annotated
from sqlmodel import Session, select
import google.generativeai as genai
from database import (
    get_session, create_db_and_tables, engine, Textbook, Chapter, 
    Conversation, Response, Quiz, Question
)
from dotenv import load_dotenv
import os

load_dotenv()

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# Dependency for session management
SessionDep = Annotated[Session, Depends(get_session)]

app = FastAPI()

# Initialize database tables on startup
@app.on_event("startup")
def on_startup():
    create_db_and_tables()


@app.get("/")
async def root():
    return {"message": "Hello World"}