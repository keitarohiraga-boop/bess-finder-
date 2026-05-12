from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app import models
from app.utils import haversine

router = APIRouter(prefix="/substations", tags=["substations"])


@router.get("/geojson", summary="変電所一覧をGeoJSONで返す")
def get_substations_geojson(
    db: Session = Depends(get_db),
    prefecture: Optional[str] = Query(default=None),
    limit: int = Query(default=2000, le=5000),
):
    q = db.query(models.Substation)
    if prefecture:
        q = q.filter(models.Substation.prefecture == prefecture)
    substations = q.limit(limit).all()

    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [s.lng, s.lat]},
                "properties": {"id": s.id, "name": s.name, "prefecture": s.prefecture},
            }
            for s in substations
        ],
    }


@router.get("/nearest", summary="指定地点から最寄り変電所を返す")
def get_nearest(
    lat: float = Query(...),
    lng: float = Query(...),
    n: int = Query(default=3, le=10),
    db: Session = Depends(get_db),
):
    # 対象を絞るため±0.5度の範囲に限定
    candidates = db.query(models.Substation).filter(
        models.Substation.lat.between(lat - 0.5, lat + 0.5),
        models.Substation.lng.between(lng - 0.5, lng + 0.5),
    ).all()

    if not candidates:
        candidates = db.query(models.Substation).all()

    ranked = sorted(candidates, key=lambda s: haversine(lat, lng, s.lat, s.lng))[:n]
    return [
        {
            "id": s.id,
            "name": s.name,
            "prefecture": s.prefecture,
            "lat": s.lat,
            "lng": s.lng,
            "distance_m": round(haversine(lat, lng, s.lat, s.lng)),
        }
        for s in ranked
    ]


@router.get("/count", summary="格納済み変電所数を返す")
def get_count(db: Session = Depends(get_db)):
    return {"count": db.query(models.Substation).count()}
