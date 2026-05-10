"""
住所・座標から土地のBESS適地ポテンシャルを総合評価するエンドポイント
"""
import math
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app import models
from app.area_mapping import PREFECTURE_TO_AREA

router = APIRouter(prefix="/evaluate", tags=["evaluate"])

PREFECTURES = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]


def detect_prefecture(address: str) -> str:
    for pref in PREFECTURES:
        if pref in address:
            return pref
    return "東京都"  # フォールバック


def haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def nearest_substations(lat, lng, db, n=3):
    candidates = db.query(models.Substation).filter(
        models.Substation.lat.between(lat - 0.5, lat + 0.5),
        models.Substation.lng.between(lng - 0.5, lng + 0.5),
    ).all()
    if not candidates:
        candidates = db.query(models.Substation).all()
    ranked = sorted(candidates, key=lambda s: haversine(lat, lng, s.lat, s.lng))[:n]
    return [
        {"id": s.id, "name": s.name, "distance_m": round(haversine(lat, lng, s.lat, s.lng))}
        for s in ranked
    ]


def calc_score(substation_dist, area_m2, jepx, curtailment) -> int:
    grid   = max(0, 100 - substation_dist / 50)
    jepx_s = jepx.jepx_score if jepx else 50
    ctrl_s = curtailment.curtailment_score if curtailment else 10
    area_s = min(100, area_m2 / 100)
    return round(grid * 0.4 + jepx_s * 0.3 + ctrl_s * 0.2 + area_s * 0.1)


@router.get("", summary="住所・座標からBESSポテンシャルを評価")
def evaluate(
    lat:      float = Query(...),
    lng:      float = Query(...),
    address:  str   = Query(default=""),
    area_m2:  float = Query(default=5000, ge=0),
    db: Session = Depends(get_db),
):
    prefecture = detect_prefecture(address)
    jepx_area  = PREFECTURE_TO_AREA.get(prefecture, "東京")

    substations  = nearest_substations(lat, lng, db)
    subst_dist   = substations[0]["distance_m"] if substations else 99999

    jepx        = db.get(models.JepxAreaMetrics, jepx_area)
    curtailment = db.get(models.CurtailmentData, jepx_area)
    fit_solar   = db.get(models.FitSolarData,    jepx_area)
    solar       = db.get(models.SolarPotential,  prefecture)

    score = calc_score(subst_dist, area_m2, jepx, curtailment)

    def to_dict(obj):
        if obj is None:
            return None
        return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}

    return {
        "lat":             lat,
        "lng":             lng,
        "address":         address,
        "prefecture":      prefecture,
        "jepx_area":       jepx_area,
        "substation_dist": subst_dist,
        "nearest_substations": substations,
        "score":           score,
        "jepx":            to_dict(jepx),
        "curtailment":     to_dict(curtailment),
        "fit_solar":       to_dict(fit_solar),
        "solar":           to_dict(solar),
    }
