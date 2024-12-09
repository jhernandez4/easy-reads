from datetime import timedelta
from fastapi import FastAPI, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Annotated
from sqlmodel import Session, select
import google.generativeai as genai
from database import (
    get_session, create_db_and_tables, Textbook, Chapter, 
    Conversation, Response, Quiz, Question, User
)
from dotenv import load_dotenv
import os
from auth import (
    authenticate_user, create_access_token, get_password_hash,
    TokenData, Token, ACCESS_TOKEN_EXPIRE_MINUTES,
    get_current_user
)

load_dotenv()

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# Pydantic models for the request body
class UserCreate(BaseModel):
    username: str
    email: str
    password: str

class TextbookCreate(BaseModel):
    title: str
    author: str | None = None

class ChapterCreate(BaseModel):
    name: str

class TextbookUpdate(BaseModel):
    title: str | None = None
    author: str | None = None

class ChapterUpdate(BaseModel):
    name: str | None = None

# Dependency for session management
SessionDep = Annotated[Session, Depends(get_session)]
# Dependency that retrieves the current authenticated user
UserDep = Annotated[User, Depends(get_current_user)]

# Helper function that returns textbook from given textbook id
async def validate_user_owns_textbook(
    textbook_id: int, current_user: UserDep, session: SessionDep
) -> Textbook:
    textbook = session.exec(
        select(Textbook)
        .where(Textbook.id == textbook_id, Textbook.user_id == current_user.id)
    ).first()

    if not textbook:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Textbook not found or does not belong to the current user."
        )
    return textbook

TextbookDep = Annotated[Textbook, Depends(validate_user_owns_textbook)]

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

@app.post("/textbooks/")
async def create_textbook(
    textbook: TextbookCreate,
    session: SessionDep,
    current_user: UserDep 
):
    # Create and save the textbook associated with the current user
    new_textbook = Textbook(
        title=textbook.title,
        author=textbook.author,
        user_id=current_user.id
    )

    session.add(new_textbook)
    session.commit()
    session.refresh(new_textbook)

    # Return a detailed HTTP response
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "message": "Textbook created successfully",
            "textbook": {
                "id": new_textbook.id,
                "title": new_textbook.title,
                "author": new_textbook.author,
                "user_id": new_textbook.user_id
            }
        }
    )

@app.get("/textbooks/")
async def get_all_textbooks(
    session: SessionDep,
    current_user: UserDep,
    offset: int = 0,
    limit: Annotated[int, Query(le=100)] = 100,
) -> list[Textbook]:

    textbooks = session.exec(
        select(Textbook)
        .where(Textbook.user_id == current_user.id)
        .offset(offset)
        .limit(limit)).all()

    return textbooks

@app.post("/textbooks/{textbook_id}/chapters/")
async def create_chapter(
    textbook: TextbookDep,
    chapter: ChapterCreate,
    session: SessionDep,
):
    new_chapter = Chapter(
        name = chapter.name,
        textbook_id = textbook.id
    )

    # Add the chapter to the database and commit
    session.add(new_chapter)
    session.commit()
    
    # Return the response as JSONResponse
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
        "message": "Chapter created successfully",
        "chapter": {
            "id": new_chapter.id,
            "name": new_chapter.name,
            "textbook_id": new_chapter.textbook_id,
            }
        }
    )

@app.get("/textbooks/{textbook_id}/chapters/")
async def get_all_chapters(
    textbook: TextbookDep,
    session: SessionDep,
    offset: int = 0,
    limit: Annotated[int, Query(le=100)] = 100,
) -> list[Chapter]:

    chapters = session.exec(
        select(Chapter)
        .where(Chapter.textbook_id == textbook.id)
        .offset(offset)
        .limit(limit)).all()

    return chapters

# Update textbook's title and/or author
@app.put("/textbooks/{textbook_id}")
async def update_textbook(
    textbook: TextbookDep,
    textbook_update: TextbookUpdate,
    session: SessionDep,
):
    if textbook_update.title is not None:
        textbook.title = textbook_update.title
    if textbook_update.author is not None:
        textbook.author = textbook_update.author

    session.add(textbook)
    session.commit()
    session.refresh(textbook)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "message": "Textbook updated successfully",
            "textbook": {
                "id": textbook.id,
                "title": textbook.title,
                "author": textbook.author,
                "user_id": textbook.user_id
            }
        }
    )

# Delete textbook and its chapters
@app.delete("/textbooks/{textbook_id}")
async def delete_textbook(
    textbook: TextbookDep,
    session: SessionDep,
):
    session.exec(
        select(Chapter).where(Chapter.textbook_id == textbook.id)
    ).delete()
    
    session.delete(textbook)
    session.commit()

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "message": "Textbook and its chapters deleted successfully"
        }
    )

# Validate chapter belongs to textbook
async def validate_chapter_ownership(
    chapter_id: int,
    textbook: TextbookDep,
    session: SessionDep,
) -> Chapter:
    chapter = session.exec(
        select(Chapter)
        .where(Chapter.id == chapter_id, Chapter.textbook_id == textbook.id)
    ).first()

    if not chapter:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chapter not found or does not belong to the specified textbook."
        )
    return chapter

# Update chapter name
@app.put("/textbooks/{textbook_id}/chapters/{chapter_id}")
async def update_chapter(
    chapter_id: int,
    textbook: TextbookDep,
    chapter_update: ChapterUpdate,
    session: SessionDep,
):
    chapter = await validate_chapter_ownership(chapter_id, textbook, session)

    if chapter_update.name is not None:
        chapter.name = chapter_update.name

    session.add(chapter)
    session.commit()
    session.refresh(chapter)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "message": "Chapter updated successfully",
            "chapter": {
                "id": chapter.id,
                "name": chapter.name,
                "textbook_id": chapter.textbook_id
            }
        }
    )

# Delete specific chapter
@app.delete("/textbooks/{textbook_id}/chapters/{chapter_id}")
async def delete_chapter(
    chapter_id: int,
    textbook: TextbookDep,
    session: SessionDep,
):
    chapter = await validate_chapter_ownership(chapter_id, textbook, session)
    
    session.delete(chapter)
    session.commit()

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "message": "Chapter deleted successfully"
        }
    )