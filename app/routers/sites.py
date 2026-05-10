from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, or_
from typing import Optional, List

from app.database import get_db
from app import models, schemas
from app.area_mapping import PREFECTURE_TO_AREA

router = APIRouter(prefix="/sites", tags=["sites"])


def _attach_extras(site: models.Site, db: Session) -> schemas.SiteOut:
    d = schemas.SiteOut.model_validate(site)

    if site.prefecture:
        # JEPXメトリクス
        area = PREFECTURE_TO_AREA.get(site.prefecture)
        if area:
            jepx = db.get(models.JepxAreaMetrics, area)
            if jepx:
                d.jepx = schemas.JepxMetrics.model_validate(jepx)

        # 太陽光ポテンシャル
        solar = db.get(models.SolarPotential, site.prefecture)
        if solar:
            d.solar = schemas.SolarOut.model_validate(solar)

        # 出力制御データ
        curtailment = db.get(models.CurtailmentData, area) if area else None
        if curtailment:
            d.curtailment = schemas.CurtailmentOut.model_validate(curtailment)

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

    sites = db.execute(stmt).scalars().all()
    return [_attach_extras(s, db) for s in sites]


@router.get("/{site_id}", response_model=schemas.SiteOut)
def get_site(site_id: int, db: Session = Depends(get_db)):
    site = db.get(models.Site, site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return _attach_extras(site, db)
