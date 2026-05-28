import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from vector_ops import qdrant
from routes import auth, index_route, search_route, files

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    for attempt in range(12):
        try:
            await qdrant.get_collections()
            break
        except Exception:
            if attempt == 11:
                raise RuntimeError("Qdrant not reachable after 60s — giving up")
            await asyncio.sleep(5)
    yield


app = FastAPI(title="*search", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(auth.router)
app.include_router(index_route.router)
app.include_router(search_route.router)
app.include_router(files.router)


@app.get("/", response_class=HTMLResponse)
def ui():
    return (STATIC_DIR / "ui.html").read_text()
