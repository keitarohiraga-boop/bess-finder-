from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from app.database import engine
from app import models
from app.routers import sites, jepx, substations, solar, curtailment, hazard

models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="BESS Site Finder API",
    description="蓄電池施設 建設候補地探索システム",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sites.router, prefix="/api/v1")
app.include_router(jepx.router, prefix="/api/v1")
app.include_router(substations.router, prefix="/api/v1")
app.include_router(solar.router, prefix="/api/v1")
app.include_router(curtailment.router, prefix="/api/v1")
app.include_router(hazard.router, prefix="/api/v1")

# フロントエンドの静的ファイルを配信
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")

if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def serve_frontend():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
else:
    @app.get("/")
    def root():
        return {"message": "BESS Site Finder API", "docs": "/docs"}
