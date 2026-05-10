from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/jepx", tags=["jepx"])

# 2024年の実績に基づく推定値
# 九州・関西は再エネ普及でピーク差が大きい傾向
FALLBACK_METRICS = [
    dict(area="北海道", data_year=2024, avg_price=11.2, peak_avg=13.8, offpeak_avg=9.1, spread=4.7, volatility=8.3,  jepx_score=48),
    dict(area="東北",   data_year=2024, avg_price=11.8, peak_avg=14.9, offpeak_avg=9.4, spread=5.5, volatility=9.1,  jepx_score=58),
    dict(area="東京",   data_year=2024, avg_price=14.2, peak_avg=17.8, offpeak_avg=11.3, spread=6.5, volatility=10.2, jepx_score=67),
    dict(area="中部",   data_year=2024, avg_price=13.1, peak_avg=16.4, offpeak_avg=10.5, spread=5.9, volatility=9.8,  jepx_score=62),
    dict(area="北陸",   data_year=2024, avg_price=11.5, peak_avg=14.1, offpeak_avg=9.6, spread=4.5, volatility=7.9,  jepx_score=44),
    dict(area="関西",   data_year=2024, avg_price=13.4, peak_avg=17.2, offpeak_avg=10.3, spread=6.9, volatility=11.4, jepx_score=74),
    dict(area="中国",   data_year=2024, avg_price=12.6, peak_avg=15.8, offpeak_avg=10.1, spread=5.7, volatility=9.5,  jepx_score=60),
    dict(area="四国",   data_year=2024, avg_price=13.0, peak_avg=16.5, offpeak_avg=10.2, spread=6.3, volatility=10.8, jepx_score=68),
    dict(area="九州",   data_year=2024, avg_price=10.3, peak_avg=14.9, offpeak_avg=7.2,  spread=7.7, volatility=13.6, jepx_score=88),
]


@router.get("/metrics", response_model=List[schemas.JepxMetrics])
def get_metrics(db: Session = Depends(get_db)):
    return db.query(models.JepxAreaMetrics).order_by(models.JepxAreaMetrics.jepx_score.desc()).all()


@router.post("/seed", summary="推定値でJEPXデータを初期投入")
def seed_metrics(db: Session = Depends(get_db)):
    for m in FALLBACK_METRICS:
        rec = db.query(models.JepxAreaMetrics).filter_by(area=m["area"]).first()
        if rec:
            for k, v in m.items():
                setattr(rec, k, v)
        else:
            db.add(models.JepxAreaMetrics(**m))
    db.commit()
    return {"message": "JEPXデータを投入しました", "areas": [m["area"] for m in FALLBACK_METRICS]}


@router.post("/update", summary="JEPXサイトからCSVを取得して更新")
def trigger_update(year: int = None):
    try:
        from app.jepx import update_jepx_metrics
        result = update_jepx_metrics(year)
        return {"message": "更新しました", "areas": list(result.keys())}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
