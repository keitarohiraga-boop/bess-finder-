from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/jepx", tags=["jepx"])


@router.get("/metrics", response_model=List[schemas.JepxMetrics])
def get_metrics(db: Session = Depends(get_db)):
    return db.query(models.JepxAreaMetrics).order_by(models.JepxAreaMetrics.jepx_score.desc()).all()


@router.post("/update")
def trigger_update(year: int = None):
    try:
        from app.jepx import update_jepx_metrics
        result = update_jepx_metrics(year)
        return {"message": "更新しました", "areas": list(result.keys())}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
