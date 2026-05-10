from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app import models

router = APIRouter(prefix="/fit-solar", tags=["fit-solar"])

# 出典: 資源エネルギー庁 FIT/FIP情報公表ウェブサイト（2024年度末）概算値
# https://www.fit-portal.go.jp/publicinfosummary
# 電力エリア別の太陽光FIT導入容量（MW）
FIT_SOLAR_DATA = [
    ("北海道",  3500,  5.0,  27),
    ("東北",    8500, 12.2,  65),
    ("東京",   13000, 18.7, 100),
    ("中部",    6000,  8.6,  46),
    ("北陸",    1200,  1.7,   9),
    ("関西",    6500,  9.4,  50),
    ("中国",    3500,  5.0,  27),
    ("四国",    2000,  2.9,  15),
    ("九州",    9000, 12.9,  69),
]

TOTAL_MW = sum(d[1] for d in FIT_SOLAR_DATA)


@router.post("/seed", summary="FIT太陽光データを初期投入")
def seed_fit_solar(db: Session = Depends(get_db)):
    for area, mw, share, score in FIT_SOLAR_DATA:
        rec = db.query(models.FitSolarData).filter_by(area=area).first()
        if rec:
            rec.capacity_mw = mw
            rec.share_pct   = share
            rec.fit_score   = score
        else:
            db.add(models.FitSolarData(
                area=area, capacity_mw=mw, share_pct=share, fit_score=score,
                data_source="資源エネルギー庁 FIT/FIP情報公表ウェブサイト（2024年度末・概算）",
            ))
    db.commit()
    return {"message": "FIT太陽光データを投入しました", "count": len(FIT_SOLAR_DATA)}


@router.get("/ranking", summary="エリア別FIT太陽光導入量ランキング")
def get_ranking(db: Session = Depends(get_db)):
    return db.query(models.FitSolarData).order_by(
        models.FitSolarData.capacity_mw.desc()
    ).all()
