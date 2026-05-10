from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app import models

router = APIRouter(prefix="/solar", tags=["solar"])

# NEDO・JMA実測値に基づく都道府県別年間平均日射量（kWh/m²/day）
# 出典: NEDO日射量データベース MONSOLA-20 / 気象庁AMeDAS
SOLAR_DATA = [
    ("北海道", 3.7), ("青森県", 3.6), ("岩手県", 3.7), ("宮城県", 3.8),
    ("秋田県", 3.5), ("山形県", 3.7), ("福島県", 4.0), ("茨城県", 4.1),
    ("栃木県", 4.0), ("群馬県", 4.1), ("埼玉県", 4.0), ("千葉県", 4.1),
    ("東京都", 3.9), ("神奈川県", 4.0), ("新潟県", 3.5), ("富山県", 3.4),
    ("石川県", 3.5), ("福井県", 3.5), ("山梨県", 4.3), ("長野県", 4.1),
    ("岐阜県", 4.1), ("静岡県", 4.5), ("愛知県", 4.4), ("三重県", 4.3),
    ("滋賀県", 4.1), ("京都府", 4.1), ("大阪府", 4.2), ("兵庫県", 4.2),
    ("奈良県", 4.1), ("和歌山県", 4.3), ("鳥取県", 3.7), ("島根県", 3.7),
    ("岡山県", 4.4), ("広島県", 4.3), ("山口県", 4.2), ("徳島県", 4.2),
    ("香川県", 4.2), ("愛媛県", 4.1), ("高知県", 4.6), ("福岡県", 4.2),
    ("佐賀県", 4.3), ("長崎県", 4.3), ("熊本県", 4.5), ("大分県", 4.4),
    ("宮崎県", 4.8), ("鹿児島県", 4.7), ("沖縄県", 5.1),
]

GHI_MIN = 3.4
GHI_MAX = 5.1


def ghi_to_score(ghi: float) -> int:
    return round((ghi - GHI_MIN) / (GHI_MAX - GHI_MIN) * 100)


@router.post("/seed", summary="日射量データを初期投入")
def seed_solar(db: Session = Depends(get_db)):
    for pref, ghi in SOLAR_DATA:
        rec = db.query(models.SolarPotential).filter_by(prefecture=pref).first()
        score = ghi_to_score(ghi)
        if rec:
            rec.ghi = ghi
            rec.solar_score = score
        else:
            db.add(models.SolarPotential(
                prefecture=pref,
                ghi=ghi,
                solar_score=score,
                data_source="NEDO MONSOLA-20 / JMA AMeDAS",
            ))
    db.commit()
    return {"message": "日射量データを投入しました", "count": len(SOLAR_DATA)}


@router.get("/ranking", summary="都道府県別日射量ランキング")
def get_ranking(db: Session = Depends(get_db)):
    return db.query(models.SolarPotential).order_by(models.SolarPotential.ghi.desc()).all()
