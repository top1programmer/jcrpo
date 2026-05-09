#main.py
from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from src.db import *
import asyncio
from contextlib import asynccontextmanager
from src.bot import start_bot


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(start_bot())
    yield

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="src/static"), name="static")
templates = Jinja2Templates(directory="src/templates")

app.add_middleware(
    SessionMiddleware,
    secret_key="super-secret-key"
)

@app.get("/")
def index(
    request: Request,
    page: int = 1,
    date: str = None,
    tag: str = None,
    search: str = None,
    sort: str = "top",
    db: Session = Depends(get_db)
):
    limit = 20
    offset = (page - 1) * limit

    jokes = fetch_jokes(db, limit, offset, date, tag, search, sort)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "jokes": jokes,
            "page": page,
            "date": date,
            "tag": tag,
            "search": search,
            "sort": sort,
            "user": request.session.get("user")
        }
    )

@app.get("/register")
def register_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={"error": None}
    )


@app.post("/register")
def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    try:
        create_user(db, username, password)

        return RedirectResponse("/login", status_code=303)

    except Exception:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context=            
            {
                "request": request,
                "error": "Пользователь уже существует"
            }
        )

@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": None}
    )


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = verify_user(db, username, password)

    if not user:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context=            
            {
                "request": request,
                "error": "Неверный логин или пароль"
            }
        )

    request.session["user"] = user

    return RedirectResponse("/", status_code=303)

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)

@app.get("/api/jokes")
def api_jokes(
    page: int = 1,
    date: str = None,
    tag: str = None,
    search: str = None,
    sort: str = "top",
    db: Session = Depends(get_db)
):
    limit = 20
    offset = (page - 1) * limit

    jokes = fetch_jokes(db, limit + 1, offset, date, tag, search, sort)

    has_next = len(jokes) > limit
    jokes = jokes[:limit]

    return {
        "jokes": jokes,
        "has_next": has_next
    }

@app.get("/random")
def random_joke(db: Session = Depends(get_db)):
    joke = db.query(Joke).order_by(func.random()).first()

    return {"text": joke.text if joke else ""}

@app.get("/jokes/create")
def create_joke_page(request: Request):
    if not request.session.get("user"):
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="create_joke.html",
        context=             
        {"request": request}
    )

@app.post("/jokes/create")
def create_joke_action(
    request: Request,
    text: str = Form(...),
    db: Session = Depends(get_db)
):
    user = request.session.get("user")

    if not user:
        return RedirectResponse("/login", status_code=303)

    create_joke(db, text, user["id"])

    return RedirectResponse("/", status_code=303)

@app.get("/jokes/{joke_id}/edit")
def edit_joke_page(
    request: Request,
    joke_id: int,
    db: Session = Depends(get_db)
):
    user = request.session.get("user")

    if not user or user["role"] != "moderator":
        return RedirectResponse("/", status_code=303)

    joke = get_joke(db, joke_id)

    return templates.TemplateResponse(
        request=request,
        name="edit_joke.html",
        context=                 
        {
            "request": request,
            "joke": joke
        }
    )

@app.post("/jokes/{joke_id}/edit")
def edit_joke_action(
    request: Request,
    joke_id: int,
    text: str = Form(...),
    db: Session = Depends(get_db)
):
    user = request.session.get("user")

    if not user or user["role"] != "moderator":
        return RedirectResponse("/", status_code=303)

    update_joke(db, joke_id, text)

    return RedirectResponse("/", status_code=303)

@app.post("/jokes/{joke_id}/delete")
def delete_joke(
    request: Request,
    joke_id: int,
    db: Session = Depends(get_db)
):
    user = request.session.get("user")

    if not user or user["role"] != "moderator":
        return RedirectResponse("/", status_code=303)

    delete_joke_db(db, joke_id)

    return RedirectResponse("/", status_code=303)