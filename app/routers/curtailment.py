from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app import models

router = APIRouter(prefix="/curtailment", tags=["curtailment"])

# 出典: 資源エネルギー庁・OCCTO公表データ
CURTAILMENT_DATA = [
    ("北海道", 0.02, 0.5,   1),
    ("東北",   0.75, 1.8,   8),
    ("東京",   0.00, 0.0,   0),
    ("中部",   0.22, 0.3,   2),
    ("北陸",   0.53, 0.8,   6),
    ("関西",   0.07, 0.1,   1),
    ("中国",   3.40, 4.2,  38),
    ("四国",   1.57, 2.5,  18),
    ("九州",   8.88, 6.5, 100),
]


@router.post("/seed", summary="出力制御データを初期投入")
def seed_curtailment(db: Session = Depends(get_db)):
    for area, rate23, rate24, score in CURTAILMENT_DATA:
        rec = db.query(models.CurtailmentData).filter_by(area=area).first()
        if rec:
            rec.rate_2023 = rate23
            rec.rate_2024 = rate24
            rec.curtailment_score = score
        else:
            db.add(models.CurtailmentData(
                area=area,
                rate_2023=rate23,
                rate_2024=rate24,
                curtailment_score=score,
                data_source="資源エネルギー庁・OCCTO 2023年度実績 / 2024年度見通し",
            ))
    db.commit()
    return {"message": "出力制御データを投入しました", "count": len(CURTAILMENT_DATA)}


@router.get("/ranking", summary="エリア別出力制御率ランキング")
def get_ranking(db: Session = Depends(get_db)):
    return db.query(models.CurtailmentData).order_by(
        models.CurtailmentData.curtailment_score.desc()
    ).all()
