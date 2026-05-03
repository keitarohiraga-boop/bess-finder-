from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, or_
from typing import Optional, List

from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/sites", tags=["sites"])


@router.get("", response_model=List[schemas.SiteOut])
def list_sites(
    db: Session = Depends(get_db),
    landuse: Optional[List[str]] = Query(default=None),
    flood: Optional[List[str]] = Query(default=None),
    substation_max: int = Query(default=5000, ge=0),
    area_min: float = Query(default=0, ge=0),
    slope_max: float = Query(default=15, ge=0),
):
    stmt = select(models.Site)

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

    return db.execute(stmt).scalars().all()


@router.get("/{site_id}", response_model=schemas.SiteOut)
def get_site(site_id: int, db: Session = Depends(get_db)):
    site = db.get(models.Site, site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return site
