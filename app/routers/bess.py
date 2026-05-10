from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app import models

router = APIRouter(prefix="/bess-facilities", tags=["bess-facilities"])


@router.get("/geojson", summary="既存BESS施設をGeoJSONで返す")
def get_bess_geojson(db: Session = Depends(get_db)):
    facilities = db.query(models.BessFacility).all()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [f.lng, f.lat]},
                "properties": {
                    "id": f.id,
                    "name": f.name,
                    "capacity_mwh": f.capacity_mwh,
                    "power_mw": f.power_mw,
                },
            }
            for f in facilities
        ],
    }


@router.get("/count", summary="格納済み件数")
def get_count(db: Session = Depends(get_db)):
    return {"count": db.query(models.BessFacility).count()}
