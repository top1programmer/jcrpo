import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from src.bot import start_bot
from src.db import (
    create_joke,
    create_user,
    delete_joke_db,
    fetch_jokes,
    fetch_duplicate_groups,
    get_db,
    get_joke,
    get_random_joke,
    resolve_duplicate_group,
    update_joke,
    verify_user,
)
from src.settings import BOT_TOKEN, ENABLE_BOT, SECRET_KEY, STATIC_DIR, TEMPLATES_DIR


bot_task = None


def is_moderator(user):
    return bool(user and user.get("role") == "moderator")


def serialize_joke(joke):
    return {
        "id": joke.id,
        "text": joke.text,
        "source_date": joke.source_date.isoformat() if joke.source_date else None,
        "final_rating": joke.final_rating,
        "tags": [{"name": tag.name} for tag in joke.tags],
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_task

    if ENABLE_BOT and BOT_TOKEN:
        bot_task = asyncio.create_task(start_bot())

    yield

    if bot_task:
        bot_task.cancel()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY
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
    offset = (max(page, 1) - 1) * limit

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
            context={
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
            context={
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
    offset = (max(page, 1) - 1) * limit

    jokes = fetch_jokes(db, limit + 1, offset, date, tag, search, sort)

    has_next = len(jokes) > limit
    jokes = jokes[:limit]

    return {
        "jokes": [serialize_joke(joke) for joke in jokes],
        "has_next": has_next
    }


@app.get("/random")
def random_joke(db: Session = Depends(get_db)):
    joke = get_random_joke(db)
    return {"text": joke.text if joke else ""}


@app.get("/duplicates")
def duplicates_page(
    request: Request,
    db: Session = Depends(get_db)
):
    user = request.session.get("user")

    if not is_moderator(user):
        return RedirectResponse("/", status_code=303)

    groups = fetch_duplicate_groups(db)

    return templates.TemplateResponse(
        request=request,
        name="duplicates.html",
        context={
            "request": request,
            "groups": groups,
            "user": user
        }
    )


@app.post("/duplicates/{cluster_id}/resolve")
def resolve_duplicates_action(
    request: Request,
    cluster_id: int,
    keep_joke_id: int = Form(...),
    db: Session = Depends(get_db)
):
    user = request.session.get("user")

    if not is_moderator(user):
        return RedirectResponse("/", status_code=303)

    resolve_duplicate_group(db, cluster_id, keep_joke_id)
    return RedirectResponse("/duplicates", status_code=303)


@app.get("/jokes/create")
def create_joke_page(request: Request):
    if not request.session.get("user"):
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="create_joke.html",
        context={"request": request}
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

    if not text.strip():
        return RedirectResponse("/jokes/create", status_code=303)

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
        context={
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
