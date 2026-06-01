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
    landuse: Optional[List[str]] = Query(default=None),  # 用途地域フィルター
    farm:    Optional[List[str]] = Query(default=None),   # 農地法フィルター（独立）
    flood:   Optional[List[str]] = Query(default=None),
    substation_max: int = Query(default=5000, ge=0),
    area_min: float = Query(default=0, ge=0),
    slope_max: float = Query(default=15, ge=0),
):
    stmt = select(models.Site)

    # 用途地域フィルター（site.landuse に対して適用）
    if landuse:
        stmt = stmt.where(models.Site.landuse.in_(landuse))

    # 農地法フィルター（site.farm_class に対して適用、non-farm は farm_class IS NULL を意味する）
    if farm:
        non_farm_selected = "non-farm" in farm
        other_farm = [f for f in farm if f != "non-farm"]
        from sqlalchemy import or_, null
        conditions = []
        if non_farm_selected:
            conditions.append(models.Site.farm_class == None)   # noqa: E711
        if other_farm:
            conditions.append(models.Site.farm_class.in_(other_farm))
        if conditions:
            stmt = stmt.where(or_(*conditions))
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


@router.delete("/{site_id}", summary="候補地を1件削除")
def delete_site(site_id: int, db: Session = Depends(get_db)):
    site = db.get(models.Site, site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    db.delete(site)
    db.commit()
    return {"deleted": site_id}


@router.delete("/admin/clear-samples", summary="サンプル・旧データを一括削除（OSMスキャン結果は残す）")
def clear_sample_sites(db: Session = Depends(get_db)):
    """
    OSMスキャン以外で登録されたサンプル・手動データを削除する。
    landuse='osm' でないサイトを全削除。本番OSMデータは保持される。
    """
    sample_sites = db.query(models.Site).filter(
        models.Site.landuse != "osm"
    ).all()
    count = len(sample_sites)
    for s in sample_sites:
        db.delete(s)
    db.commit()
    return {"deleted_count": count, "message": f"{count}件のサンプルデータを削除しました"}
