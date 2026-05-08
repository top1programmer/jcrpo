from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from db import fetch_jokes, get_conn
from fastapi.staticfiles import StaticFiles


app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# @app.get("/", response_class=HTMLResponse)
# def index(request: Request):
#     return templates.TemplateResponse(
#         request=request,
#         name="index.html",
#         context={}
#     )
@app.get("/")
def index(request: Request, page: int = 1, date: str = None, tag: str = None, search: str = None, sort: str = "top"):
    limit = 20
    offset = (page - 1) * limit

    jokes = fetch_jokes(limit, offset, date, tag, search, sort)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "jokes": jokes,
            "page": page,
            "date": date,
            "tag": tag,
            "search": search,
            "sort": sort
        }
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