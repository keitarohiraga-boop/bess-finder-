from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, or_
from typing import Optional, List

from app.database import get_db
from app import models, schemas
from app.area_mapping import PREFECTURE_TO_AREA

router = APIRouter(prefix="/sites", tags=["sites"])


def _build_lookup_maps(db: Session) -> dict:
    """エリア別マスターデータを一括取得してキャッシュ（N+1対策）"""
    return {
        "jepx":        {r.area: r for r in db.query(models.JepxAreaMetrics).all()},
        "solar":       {r.prefecture: r for r in db.query(models.SolarPotential).all()},
        "curtailment": {r.area: r for r in db.query(models.CurtailmentData).all()},
        "fit_solar":   {r.area: r for r in db.query(models.FitSolarData).all()},
        "outage":      {r.area: r for r in db.query(models.OutageRiskData).all()},
        "ev":          {r.prefecture: r for r in db.query(models.EVAdoptionData).all()},
    }


def _attach_extras(site: models.Site, maps: dict) -> schemas.SiteOut:
    """ルックアップマップからエリアデータを付加（DBアクセスなし）"""
    d = schemas.SiteOut.model_validate(site)

    if not site.prefecture:
        return d

    area = PREFECTURE_TO_AREA.get(site.prefecture)

    if area:
        if jepx := maps["jepx"].get(area):
            d.jepx = schemas.JepxMetrics.model_validate(jepx)
        if curtailment := maps["curtailment"].get(area):
            d.curtailment = schemas.CurtailmentOut.model_validate(curtailment)
        if fit_solar := maps["fit_solar"].get(area):
            d.fit_solar = schemas.FitSolarOut.model_validate(fit_solar)
        if outage := maps["outage"].get(area):
            d.outage = schemas.OutageRiskOut.model_validate(outage)

    if solar := maps["solar"].get(site.prefecture):
        d.solar = schemas.SolarOut.model_validate(solar)
    if ev := maps["ev"].get(site.prefecture):
        d.ev = schemas.EVAdoptionOut.model_validate(ev)

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

    # マスターデータを一括取得（クエリ数を固定: 6本）
    maps = _build_lookup_maps(db)
    sites = db.execute(stmt).scalars().all()
    return [_attach_extras(s, maps) for s in sites]


@router.get("/{site_id}", response_model=schemas.SiteOut)
def get_site(site_id: int, db: Session = Depends(get_db)):
    site = db.get(models.Site, site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    maps = _build_lookup_maps(db)
    return _attach_extras(site, maps)
