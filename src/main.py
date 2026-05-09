
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from src.db import (
    fetch_jokes,
    get_conn,
    create_user,
    verify_user
)
from psycopg2.errors import UniqueViolation
from psycopg2 import IntegrityError


app = FastAPI()
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
    sort: str = "top"
):
    limit = 20
    offset = (page - 1) * limit

    jokes = fetch_jokes(limit, offset, date, tag, search, sort)

    user = request.session.get("user")

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
            "user": user
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
    password: str = Form(...)
):
    try:
        create_user(username, password)

        return RedirectResponse(
            url="/login",
            status_code=303
        )

    except IntegrityError:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
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
    password: str = Form(...)
):
    user = verify_user(username, password)

    if not user:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": "Неверный логин или пароль"
            }
        )

    request.session["user"] = user

    return RedirectResponse(
        url="/",
        status_code=303
    )

@app.get("/logout")
def logout(request: Request):
    request.session.clear()

    return RedirectResponse(
        url="/",
        status_code=303
    )

@app.get("/api/jokes")
def api_jokes(page: int = 1, date: str = None, tag: str = None, search: str = None, sort: str = "top"):
    limit = 20
    offset = (page - 1) * limit

    jokes = fetch_jokes(limit + 1, offset, date, tag, search, sort)

    has_next = len(jokes) > limit
    jokes = jokes[:limit]

    return {
        "jokes": jokes,
        "has_next": has_next
    }

@app.get("/random")
def random_joke():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT j.text
        FROM jokes j
        ORDER BY random()
        LIMIT 1
    """)

    joke = cur.fetchone()

    cur.close()
    conn.close()

    return {"text": joke[0] if joke else ""}