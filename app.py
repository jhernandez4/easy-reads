import jwt
from datetime import datetime, timedelta, timezone
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
    Conversation, Response, Quiz, Question, User
)
from dotenv import load_dotenv
import os
from auth import (
    authenticate_user, create_access_token, get_password_hash,
    TokenData, Token, ACCESS_TOKEN_EXPIRE_MINUTES
)

load_dotenv()

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# Pydantic model for the request body
class UserCreate(BaseModel):
    username: str
    email: str
    password: str

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

@app.post("/login")
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: SessionDep) -> Token:
    user = authenticate_user(form_data.username, form_data.password, session)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return Token(access_token=access_token, token_type="bearer")

@app.post("/signup", response_model=Token)
async def sign_up(
    user_create: UserCreate,
    session: SessionDep) -> Token:
    # Check if the username or email already exists
    existing_user = session.exec(select(User).where(User.username == user_create.username)).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
    
    existing_email = session.exec(select(User).where(User.email == user_create.email)).first()

    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Hash the password
    hashed_password = get_password_hash(user_create.password)

    # Create a new user instance
    new_user = User(
        username=user_create.username,
        email=user_create.email,
        hashed_password=hashed_password
    )

    # Save the new user to the database
    session.add(new_user)
    session.commit()
    session.refresh(new_user) # Ensure the user instance is up to data after commit

    # Generate the JWT token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": new_user.username}, expires_delta=access_token_expires
    )

    return Token(access_token=access_token, token_type="bearer")