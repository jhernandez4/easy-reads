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

USER_ROLE = "user"
AI_ROLE = "model"
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    system_instruction= '''You are an AI study assistant. You will help explain 
    excerpts of text chosen by the user that they are confused about. Provide
    accurate explanations, focusing on helping the user resolve confusion.
    ''' 
)

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

class PromptRequest(BaseModel):
    text: str

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

async def generate_title(prompt: str):
    # Use smaller model for title generation
    mini_model = genai.GenerativeModel("gemini-1.5-flash-8b")
    response = await mini_model.generate_content_async(
        f'Generate, in a few words, an appropriate title (that does NOT use Markdown) for the following text: {prompt}'
    )

    return response.text

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

@app.post("/textbooks/{textbook_id}/chapters/{chapter_id}/conversations")
async def create_conversation(
    prompt: PromptRequest,
    chapter_id: int,
    session: SessionDep
):
    title = await generate_title(prompt.text)

    new_conversation = Conversation(
        title=title,
        chapter_id=chapter_id,
    )
    
    session.add(new_conversation)
    session.commit()
    session.refresh(new_conversation)


    chat = model.start_chat(
        history=[{"role": USER_ROLE, "parts": prompt.text}]
    )

    ai_response = await model.generate_content_async(prompt.text)

    session.add_all([
        Response(conversation_id=new_conversation.id, role=USER_ROLE, content=prompt.text),
        Response(conversation_id=new_conversation.id, role=AI_ROLE, content=ai_response.text)
    ])

    session.commit()

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "message": "Conversation started successfully",
            "conversation": {
                "id": new_conversation.id,
                "title": new_conversation.title
            },
            "responses": [
                {"role": USER_ROLE, "content": prompt.text},
                {"role": AI_ROLE, "content": ai_response.text}
            ]
        }
    )

@app.post("/textbooks/{textbook_id}/chapters/{chapter_id}/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: int,
    prompt: PromptRequest,
    session: SessionDep
):
    # Fetch conversation
    conversation = session.exec(
        select(Conversation).where(Conversation.id == conversation_id)
    ).first()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    # Retrieve chat history from the database
    history = session.exec(
        select(Response)
        .where(Response.conversation_id == conversation_id)
        .order_by(Response.timestamp)
    ).all()

    # Convert history to Gemini-compatible format
    chat_history = [{"role": resp.role, "parts": resp.content} for resp in history]

    # Reinitialize the Gemini chat
    chat = model.start_chat(history=chat_history)

    # Send the user's message to Gemini
    ai_response = chat.send_message(prompt.text)

    # Save the new user and AI responses to the database
    session.add_all([
        Response(conversation_id=conversation_id, role=USER_ROLE, content=prompt.text),
        Response(conversation_id=conversation_id, role=AI_ROLE, content=ai_response.text)
    ])
    session.commit()

    # Retrieve updated chat history
    updated_history = session.exec(
        select(Response)
        .where(Response.conversation_id == conversation_id)
        .order_by(Response.timestamp)
    ).all()

    chat_history = [{"role": resp.role, "content": resp.content} for resp in updated_history]

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "message": "Message sent sucessfully",
            "history": chat_history
        }
    )

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
    chapters = session.exec(
        select(Chapter).where(Chapter.textbook_id == textbook.id)
    ).all()

    for chapter in chapters:
        session.delete(chapter)
    
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

# Delete a conversation and all messages within it
@app.delete("/textbooks/{textbook_id}/chapters/{chapter_id}/conversations/{conversation_id}")
async def delete_conversation(
    textbook_id: int,
    chapter_id: int,
    conversation_id: int,
    session: SessionDep,
    current_user: UserDep
):
    # Check if textbook belongs to the current user
    textbook = await validate_user_owns_textbook(textbook_id, current_user, session)
    
    # Check if chapter belongs to the textbook
    chapter = await validate_chapter_ownership(chapter_id, textbook, session)

    # Get convo
    conversation = session.exec(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.chapter_id == chapter.id)
    ).first()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    # Delete all related responses
    responses = session.exec(
        select(Response).where(Response.conversation_id == conversation.id)
    ).all()

    for response in responses:
        session.delete(response)

    # Delete convo itself
    session.delete(conversation)
    session.commit()

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Conversation and all related messages has been deleted successfully."}
    )

# View all conversations
@app.get("/textbooks/{textbook_id}/chapters/{chapter_id}/conversations/")
async def get_all_conversations(
    textbook_id: int,
    chapter_id: int,
    session: SessionDep,
    current_user: UserDep,
    offset: int = 0,
    limit: Annotated[int, Query(le=100)] = 100,
):
    # Check if textbook belongs to current user
    textbook = await validate_user_owns_textbook(textbook_id, current_user, session)

    # Check if chapter belongs to textbook
    chapter = await validate_chapter_ownership(chapter_id, textbook, session)

    # Get convo related with chapter
    conversations = session.exec(
        select(Conversation)
        .where(Conversation.chapter_id == chapter.id)
        .offset(offset)
        .limit(limit)
    ).all()

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"conversations": [
            {
                "id": conversation.id,
                "title": conversation.title,
                "start_time": conversation.start_time,
                "end_time": conversation.end_time
            } for conversation in conversations
        ]}
    )