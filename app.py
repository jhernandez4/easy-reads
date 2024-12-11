import os
import json
from datetime import timedelta, datetime
from typing import Annotated

from fastapi import FastAPI, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import JSONResponse
from sqlmodel import Session, select
import google.generativeai as genai
from dotenv import load_dotenv

from sqlmodel import SQLModel
from database import engine

from database import (
    get_session, create_db_and_tables, 
    Textbook, Chapter, Conversation, 
    Response, Quiz, Question, User
)
from auth import (
    authenticate_user, create_access_token, 
    get_password_hash, TokenData, Token, 
    ACCESS_TOKEN_EXPIRE_MINUTES, get_current_user
)
from cache import (
    init_cache_service, CacheType, RedisConfig, 
    CacheTTLConfig, CacheService, AIResponseCache
)
from models import (
    UserCreate, TextbookCreate, ChapterCreate,
    PromptRequest, TextbookUpdate, ChapterUpdate
)
import json

load_dotenv()


app = FastAPI()

redis_config = RedisConfig(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", 6379)),
)

ttl_config = CacheTTLConfig(
    textbook=int(os.getenv("CACHE_TTL_TEXTBOOK", 3600)),
    chapter=int(os.getenv("CACHE_TTL_CHAPTER", 3600)),
    conversation=int(os.getenv("CACHE_TTL_CONVERSATION", 1800)),
    ai_response=int(os.getenv("CACHE_TTL_AI_RESPONSE", 86400)),
    quiz=int(os.getenv("CACHE_TTL_QUIZ", 7200))
)

cache_service = init_cache_service(redis_config, ttl_config)
ai_response_cache = AIResponseCache(cache_service)

USER_ROLE = "user"
AI_ROLE = "model"
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    system_instruction='You are an AI study assistant. You will help explain excerpts of text chosen by the user that they are confused about. Provide accurate explanations, focusing on helping the user resolve confusion.'
)

async def get_cache_service() -> CacheService:
    return cache_service

CacheServiceDep = Annotated[CacheService, Depends(get_cache_service)]
SessionDep = Annotated[Session, Depends(get_session)]
UserDep = Annotated[User, Depends(get_current_user)]

async def validate_user_owns_textbook(
    textbook_id: int, 
    current_user: UserDep, 
    session: SessionDep
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

ChapterDep = Annotated[Chapter, Depends(validate_chapter_ownership)]

async def generate_title(prompt: str):
    # Use smaller model for title generation
    mini_model = genai.GenerativeModel("gemini-1.5-flash-8b")
    response = await mini_model.generate_content_async(
        f'Generate, in a few words, an appropriate title (that does NOT use Markdown) for the following text: {prompt}'
    )

    return response.text

async def generate_quiz_questions(prompt: str):
    quiz_model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction="""
            You are an AI study assistant designed to help learners review material. You will be given a list of messages 
            between an AI model and a user. Each message will be a dictionary with the following structure:
            - "role": The role of the speaker, either "user" or "model".
            - "parts": The text of the message.

            Based on the conversation, your task is to generate quiz questions that assess key concepts or information discussed 
            during the chat. These questions should be focused on helping the learner review and test their understanding of 
            the material. And all these messages are from a single chapter of a textbook.

            The quiz questions should be formatted as a list of dictionaries, where each dictionary represents a question 
            and its correct answer. Each dictionary should contain the following fields:
            - "content": A clear, concise question based on the conversation.
            - "correct_answer": The correct answer to the question, derived from the conversation.
            - "question_type": A type identifier,"open-ended", of the question. Choose the question type based on the content
            of the conversation.

            Output the quiz questions as plain string, not in a Markdown code block. Here is an example of how the output should be structured:

            [
                {"content": "What is a derivative?", "correct_answer": "The rate of change of a function", "question_type": "open-ended"},
                {"content": "Explain concurrency.", "correct_answer": "Concurrency allows tasks to make progress without running simultaneously.", "question_type": "open-ended"}
            ]

        """
    ) 

    response = await quiz_model.generate_content_async(prompt)

    return response.text

# Initialize database tables on startup
@app.on_event("startup")
def on_startup() -> None:
    create_db_and_tables()

# For testing purposes, we can drop all tables on shutdown
@app.on_event("shutdown")
async def shutdown_event():
    SQLModel.metadata.drop_all(engine)

@app.get("/")
async def root() -> dict:
    return {"message": "Hello World"}

@app.post("/login")
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: SessionDep
) -> Token:
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
    session: SessionDep
) -> Token:
    existing_user = session.exec(
        select(User).where(User.username == user_create.username)
    ).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
    
    existing_email = session.exec(
        select(User).where(User.email == user_create.email)
    ).first()
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    hashed_password = get_password_hash(user_create.password)
    new_user = User(
        username=user_create.username,
        email=user_create.email,
        hashed_password=hashed_password
    )

    session.add(new_user)
    session.commit()
    session.refresh(new_user)

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": new_user.username}, expires_delta=access_token_expires
    )
    return Token(access_token=access_token, token_type="bearer")

@app.post("/textbooks")
async def create_textbook(
    textbook: TextbookCreate,
    session: SessionDep,
    current_user: UserDep,
    cache: CacheServiceDep
) -> JSONResponse:
    new_textbook = Textbook(
        title=textbook.title,
        author=textbook.author,
        user_id=current_user.id
    )

    session.add(new_textbook)
    session.commit()
    session.refresh(new_textbook)

    await cache.invalidate_pattern(f"{CacheType.TEXTBOOK.value}:user:{current_user.id}:*")

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

@app.get("/textbooks")
@cache_service.cache_decorator(CacheType.TEXTBOOK)
async def get_all_textbooks(
    session: SessionDep,
    current_user: UserDep,
    offset: int = 0,
    limit: Annotated[int, Query(le=100)] = 100,
) -> dict:
    textbooks = session.exec(
        select(Textbook)
        .where(Textbook.user_id == current_user.id)
        .offset(offset)
        .limit(limit)
    ).all()
    
    return {
        "textbooks": [
            {
                "id": textbook.id,
                "title": textbook.title,
                "author": textbook.author,
                "user_id": textbook.user_id
            } for textbook in textbooks
        ]
    }

@app.put("/textbooks/{textbook_id}")
async def update_textbook(
    textbook: TextbookDep,
    textbook_update: TextbookUpdate,
    session: SessionDep,
    cache: CacheServiceDep,
) -> JSONResponse:
    if textbook_update.title is not None:
        textbook.title = textbook_update.title
    if textbook_update.author is not None:
        textbook.author = textbook_update.author

    session.add(textbook)
    session.commit()
    session.refresh(textbook)

    await cache.invalidate_pattern(f"{CacheType.TEXTBOOK.value}:*")

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

@app.delete("/textbooks/{textbook_id}")
async def delete_textbook(
    textbook: TextbookDep,
    session: SessionDep,
    cache: CacheServiceDep,
) -> JSONResponse:
    chapters = session.exec(
        select(Chapter).where(Chapter.textbook_id == textbook.id)
    ).all()

    for chapter in chapters:
        session.delete(chapter)
    
    session.delete(textbook)
    session.commit()

    await cache.invalidate_pattern(f"{CacheType.TEXTBOOK.value}:*")
    await cache.invalidate_pattern(f"{CacheType.CHAPTER.value}:*")
    await cache.invalidate_pattern(f"{CacheType.CONVERSATION.value}:*")

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Textbook and its chapters deleted successfully"}
    )

@app.post("/textbooks/{textbook_id}/chapters")
async def create_chapter(
    textbook: TextbookDep,
    chapter: ChapterCreate,
    session: SessionDep,
    cache: CacheServiceDep
) -> JSONResponse:
    new_chapter = Chapter(
        name=chapter.name,
        textbook_id=textbook.id
    )

    session.add(new_chapter)
    session.commit()
    session.refresh(new_chapter)

    await cache.invalidate_pattern(f"{CacheType.CHAPTER.value}:*")

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "message": "Chapter created successfully",
            "chapter": {
                "id": new_chapter.id,
                "name": new_chapter.name,
                "textbook_id": new_chapter.textbook_id
            }
        }
    )

@app.get("/textbooks/{textbook_id}/chapters")
@cache_service.cache_decorator(CacheType.CHAPTER)
async def get_all_chapters(
    textbook: TextbookDep,
    chapter: ChapterCreate,
    session: SessionDep,
    offset: int = 0,
    limit: Annotated[int, Query(le=100)] = 100,
) -> dict:
    chapters = session.exec(
        select(Chapter)
        .where(Chapter.textbook_id == textbook.id)
        .offset(offset)
        .limit(limit)
    ).all()
    
    return {
        "chapters": [
            {
                "id": chapter.id,
                "name": chapter.name,
                "textbook_id": chapter.textbook_id
            } for chapter in chapters
        ]
    }

@app.put("/textbooks/{textbook_id}/chapters/{chapter_id}")
async def update_chapter(
    chapter_id: int,
    textbook: TextbookDep,
    chapter_update: ChapterUpdate,
    session: SessionDep,
    cache: CacheServiceDep,
) -> JSONResponse:
    chapter = await validate_chapter_ownership(chapter_id, textbook, session)

    if chapter_update.name is not None:
        chapter.name = chapter_update.name

    session.add(chapter)
    session.commit()
    session.refresh(chapter)

    await cache.invalidate_pattern(f"{CacheType.CHAPTER.value}:*")

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

@app.delete("/textbooks/{textbook_id}/chapters/{chapter_id}")
async def delete_chapter(
    chapter_id: int,
    textbook: TextbookDep,
    session: SessionDep,
    cache: CacheServiceDep,
) -> JSONResponse:
    chapter = await validate_chapter_ownership(chapter_id, textbook, session)
    
    session.delete(chapter)
    session.commit()

    await cache.invalidate_pattern(f"{CacheType.CHAPTER.value}:*")
    await cache.invalidate_pattern(f"{CacheType.CONVERSATION.value}:*")

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Chapter deleted successfully"}
    )

@app.post("/textbooks/{textbook_id}/chapters/{chapter_id}/conversations")
async def create_conversation(
    prompt: PromptRequest,
    chapter_id: int,
    session: SessionDep,
    cache: CacheServiceDep
) -> JSONResponse:
    cached_response = await ai_response_cache.get_response(prompt.text)
    
    if cached_response:
        ai_response_text = cached_response
    else:
        chat = model.start_chat(
            history=[{"role": USER_ROLE, "parts": prompt.text}]
        )
        ai_response = await model.generate_content_async(prompt.text)
        ai_response_text = ai_response.text
        await ai_response_cache.cache_response(prompt.text, ai_response_text)

    title = await generate_title(prompt.text)
    new_conversation = Conversation(
        title=title,
        chapter_id=chapter_id,
    )
    
    session.add(new_conversation)
    session.commit()
    session.refresh(new_conversation)

    session.add_all([
        Response(conversation_id=new_conversation.id, role=USER_ROLE, content=prompt.text),
        Response(conversation_id=new_conversation.id, role=AI_ROLE, content=ai_response_text)
    ])
    session.commit()

    await cache.invalidate_pattern(f"{CacheType.CONVERSATION.value}:*")

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
                {"role": AI_ROLE, "content": ai_response_text}
            ]
        }
    )

@app.post("/textbooks/{textbook_id}/chapters/{chapter_id}/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: int,
    prompt: PromptRequest,
    session: SessionDep,
    cache: CacheServiceDep
) -> JSONResponse:
    conversation = session.exec(
        select(Conversation).where(Conversation.id == conversation_id)
    ).first()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    history = session.exec(
        select(Response)
        .where(Response.conversation_id == conversation_id)
        .order_by(Response.timestamp)
    ).all()

    chat_history = [{"role": resp.role, "parts": resp.content} for resp in history]
    chat = model.start_chat(history=chat_history)
    ai_response = chat.send_message(prompt.text)

    session.add_all([
        Response(conversation_id=conversation_id, role=USER_ROLE, content=prompt.text),
        Response(conversation_id=conversation_id, role=AI_ROLE, content=ai_response.text)
    ])
    session.commit()

    await cache.invalidate_pattern(f"{CacheType.CONVERSATION.value}:*")

    updated_history = session.exec(
        select(Response)
        .where(Response.conversation_id == conversation_id)
        .order_by(Response.timestamp)
    ).all()

    chat_history = [{"role": resp.role, "content": resp.content} for resp in updated_history]

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "message": "Message sent successfully",
            "history": chat_history
        }
    )

@app.delete("/textbooks/{textbook_id}/chapters/{chapter_id}/conversations/{conversation_id}")
async def delete_conversation(
    textbook_id: int,
    chapter_id: int,
    conversation_id: int,
    session: SessionDep,
    current_user: UserDep,
    cache: CacheServiceDep
) -> JSONResponse:
    textbook = await validate_user_owns_textbook(textbook_id, current_user, session)
    chapter = await validate_chapter_ownership(chapter_id, textbook, session)
    
    conversation = session.exec(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.chapter_id == chapter.id)
    ).first()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    responses = session.exec(
        select(Response).where(Response.conversation_id == conversation.id)
    ).all()

    for response in responses:
        session.delete(response)

    session.delete(conversation)
    session.commit()
    
    await cache.invalidate_pattern(f"{CacheType.CONVERSATION.value}:*")

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Conversation and all related messages deleted successfully"}
    )

@app.get("/textbooks/{textbook_id}/chapters/{chapter_id}/conversations")
@cache_service.cache_decorator(CacheType.CONVERSATION)
async def get_all_conversations(
    textbook_id: int,
    chapter_id: int,
    session: SessionDep,
    current_user: UserDep,
    offset: int = 0,
    limit: Annotated[int, Query(le=100)] = 100,
) -> dict:
    textbook = await validate_user_owns_textbook(textbook_id, current_user, session)
    chapter = await validate_chapter_ownership(chapter_id, textbook, session)

    conversations = session.exec(
        select(Conversation)
        .where(Conversation.chapter_id == chapter.id)
        .offset(offset)
        .limit(limit)
    ).all()

    return {
        "conversations": [
            {
                "id": conversation.id,
                "title": conversation.title,
                "start_time": conversation.start_time.isoformat() if conversation.start_time else None,
                "end_time": conversation.end_time.isoformat() if conversation.end_time else None
            } for conversation in conversations
        ]
    }

@app.post("/textbooks/{textbook_id}/chapters/{chapter_id}/quizzes")
async def generate_quiz(
    chapter: ChapterDep,
    session: SessionDep,
) -> JSONResponse:
    conversations = session.exec(
        select(Conversation).where(Conversation.chapter_id == chapter.id)
    ).all()

    if not conversations:
        raise HTTPException(status_code=404, detail="No conversations found for this chapter")
    
    chapter_content = " ".join(
        f"{response.role}: {response.content}"
        for conversation in conversations
        for response in conversation.responses
    )

    quiz_data = await generate_quiz_questions(chapter_content)
    try:
        quiz_list = json.loads(quiz_data)
        new_quiz = Quiz(
            title=f"Quiz for {chapter.name}",
            chapter_id=chapter.id,
            created_at=datetime.utcnow()
        )
        session.add(new_quiz)
        session.flush()

        questions = []
        for question in quiz_list:
            new_question = Question(
                quiz_id=new_quiz.id,
                content=question["content"],
                correct_answer=question["correct_answer"],
                question_type=question.get("question_type", "open-ended")
            )
            session.add(new_question)
            questions.append({
                "content": question["content"],
                "correct_answer": question["correct_answer"],
                "question_type": question.get("question_type", "open-ended")
            })

        session.commit()
        return {
            "id": new_quiz.id,
            "title": new_quiz.title,
            "chapter_id": chapter.id,
            "questions": questions
        }

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Failed to create a quiz for this chapter")