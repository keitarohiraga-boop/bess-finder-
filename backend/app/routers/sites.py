from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, or_
from geoalchemy2.functions import ST_X, ST_Y
from typing import Optional, List

from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/sites", tags=["sites"])


def _row_to_dict(site: models.Site, lng: float, lat: float) -> dict:
    d = {c.name: getattr(site, c.name) for c in site.__table__.columns if c.name != "geom"}
    d["lat"] = float(lat)
    d["lng"] = float(lng)
    return d


@router.get("", response_model=List[schemas.SiteOut])
def list_sites(
    db: Session = Depends(get_db),
    landuse: Optional[List[str]] = Query(default=None),
    flood: Optional[List[str]] = Query(default=None),
    substation_max: int = Query(default=5000, ge=0),
    area_min: float = Query(default=0, ge=0),
    slope_max: float = Query(default=15, ge=0),
):
    stmt = select(
        models.Site,
        ST_X(models.Site.geom).label("lng"),
        ST_Y(models.Site.geom).label("lat"),
    )

    if landuse:
        stmt = stmt.where(
            or_(
                models.Site.landuse.in_(landuse),
                models.Site.farm_class.in_(landuse),
            )
        )
    if flood:
        stmt = stmt.where(models.Site.flood.in_(flood))

    stmt = stmt.where(
        models.Site.substation_dist <= substation_max,
        models.Site.area >= area_min,
        models.Site.slope <= slope_max,
    ).order_by(models.Site.score.desc())

    rows = db.execute(stmt).all()
    return [_row_to_dict(site, lng, lat) for site, lng, lat in rows]


@router.get("/{site_id}", response_model=schemas.SiteOut)
def get_site(site_id: int, db: Session = Depends(get_db)):
    stmt = select(
        models.Site,
        ST_X(models.Site.geom).label("lng"),
        ST_Y(models.Site.geom).label("lat"),
    ).where(models.Site.id == site_id)

    row = db.execute(stmt).first()
    if not row:
        raise HTTPException(status_code=404, detail="Site not found")

    site, lng, lat = row
    return _row_to_dict(site, lng, lat)
