from fastapi import FastAPI, Depends, HTTPException, Query
from typing import Annotated
from sqlmodel import Session, select
from database import (
    get_session, create_db_and_tables, engine, Textbook, Chapter, 
    Conversation, Response, Quiz, Question
)

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