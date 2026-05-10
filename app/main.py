from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine
from app import models
from app.routers import sites, jepx, substations, solar, curtailment

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


@app.get("/")
def root():
    return {"message": "BESS Site Finder API", "docs": "/docs"}
