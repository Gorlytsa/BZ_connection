"""MVP-приложение платформы PowerStation Exchange."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from .config import settings
from .database import Base, engine
from .routers import (admin, applications, auth, documents_api, exchange,
                      finance_api, locations, organizations, stations)
from .seed import seed


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    seed()
    yield


app = FastAPI(title=settings.app_name + " — MVP",
              description="Биржа франшизы зарядных станций. См. DESCRIPTION.md.")

for r in (auth.router, organizations.router, locations.router, applications.router,
          exchange.router, documents_api.router, stations.router, finance_api.router,
          admin.router):
    app.include_router(r)


@app.exception_handler(Exception)
async def unhandled_exc(request: Request, exc: Exception):
    """Детальные сообщения об ошибках — только разработчику (31.4)."""
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка платформы"})


@app.get("/")
def index():
    return FileResponse("web/index.html")


@app.get("/health")
def health():
    return {"status": "ok"}
