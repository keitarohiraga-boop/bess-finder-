"""
エネルギーソース情報ルーター
風力発電所（OSM）・FIT認定情報（市町村別）の検索・スコアリング支援。
"""
import time
import json
import urllib.request
import urllib.parse
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app import models
from app.utils import haversine

router = APIRouter(prefix="/energy-sources", tags=["energy-sources"])

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


# ===== 風力発電所 =====

def _fetch_wind_farms(lat: float, lng: float, radius_m: int = 10000) -> list[dict]:
    """OSM Overpassから指定座標周辺の風力発電所を取得"""
    query = f"""
[out:json][timeout:30];
(
  node["power"="generator"]["generator:source"="wind"](around:{radius_m},{lat},{lng});
  way["power"="generator"]["generator:source"="wind"](around:{radius_m},{lat},{lng});
  node["generator:source"="wind"](around:{radius_m},{lat},{lng});
);
out center;
"""
    body = urllib.parse.urlencode({"data": query}).encode("utf-8")
    req = urllib.request.Request(
        OVERPASS_URL, data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "BSRI-BESSFinder/1.0 (bess-site-finder; keitaro.hiraga@natural-born.jp)",
        },
    )
    with urllib.request.urlopen(req, timeout=35) as resp:
        result = json.loads(resp.read())

    farms = []
    for elem in result.get("elements", []):
        if elem["type"] == "node":
            flat, flng = elem["lat"], elem["lon"]
        else:
            center = elem.get("center", {})
            flat, flng = center.get("lat", 0), center.get("lon", 0)
        if not flat or not flng:
            continue
        tags = elem.get("tags", {})
        farms.append({
            "lat": flat, "lng": flng,
            "name": tags.get("name", "風力発電機"),
            "output_kw": tags.get("generator:output:electricity", "不明"),
            "operator": tags.get("operator", ""),
            "distance_m": round(haversine(lat, lng, flat, flng)),
        })
    return sorted(farms, key=lambda x: x["distance_m"])


@router.get("/wind-farms/nearby", summary="指定座標周辺の風力発電所をOSMから取得")
def get_nearby_wind_farms(
    lat: float,
    lng: float,
    radius_km: float = Query(default=10.0, le=50.0),
):
    """
    風力発電所への近接性はBESSの余剰電力引き取りビジネスの立地指標となる。
    radius_km 内の風力発電設備をOSM Overpassから取得する。
    """
    try:
        farms = _fetch_wind_farms(lat, lng, int(radius_km * 1000))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OSM Overpass エラー: {str(e)[:80]}")

    wind_score = 0
    if farms:
        nearest_km = farms[0]["distance_m"] / 1000
        if nearest_km <= 2:
            wind_score = 80
        elif nearest_km <= 5:
            wind_score = 50
        elif nearest_km <= 10:
            wind_score = 25
        else:
            wind_score = 10

    return {
        "lat": lat, "lng": lng, "radius_km": radius_km,
        "wind_farm_count": len(farms),
        "nearest_km": round(farms[0]["distance_m"] / 1000, 2) if farms else None,
        "wind_proximity_score": wind_score,
        "farms": farms[:10],
    }


# ===== FIT認定情報 =====

@router.get("/fit-solar/status", summary="FIT認定データのインポート状況")
def fit_status(db: Session = Depends(get_db)):
    total = db.query(models.FitMunicipalitySolar).count()
    if total == 0:
        return {
            "total": 0,
            "message": "未インポート — import_fit_solar.py を実行してください",
            "download_url": "https://www.fit-portal.go.jp/publicinfosummary",
        }
    rows = db.query(
        models.FitMunicipalitySolar.prefecture,
        func.sum(models.FitMunicipalitySolar.certified_kw).label("total_kw"),
        func.count(models.FitMunicipalitySolar.id).label("muni_count"),
    ).group_by(models.FitMunicipalitySolar.prefecture).order_by(
        func.sum(models.FitMunicipalitySolar.certified_kw).desc()
    ).all()
    return {
        "total_municipalities": total,
        "by_prefecture": [
            {"prefecture": r.prefecture, "total_kw": r.total_kw, "municipalities": r.muni_count}
            for r in rows[:15]
        ],
    }


@router.get("/fit-solar/hotspots", summary="FIT認定容量が多い市町村（太陽光連携BESS候補エリア）")
def fit_hotspots(
    prefecture: Optional[str] = None,
    min_kw: float = Query(default=10000, description="最低認定容量kW"),
    limit: int = Query(default=30, le=100),
    db: Session = Depends(get_db),
):
    """
    FIT認定容量が大きい市町村 = FIT失効後に余剰電力が発生するエリア。
    BESS設置・太陽光電力引き取りビジネスの打診先として優先度が高い。
    """
    q = db.query(models.FitMunicipalitySolar).filter(
        models.FitMunicipalitySolar.certified_kw >= min_kw
    )
    if prefecture:
        q = q.filter(models.FitMunicipalitySolar.prefecture == prefecture)

    rows = q.order_by(models.FitMunicipalitySolar.certified_kw.desc()).limit(limit).all()

    if not rows:
        return {"message": "データなし。import_fit_solar.py --demo を実行して動作確認してください", "hotspots": []}

    return {
        "total_found": len(rows),
        "hotspots": [
            {
                "prefecture": r.prefecture,
                "municipality": r.municipality,
                "certified_kw": r.certified_kw,
                "certified_count": r.certified_count,
                "bess_colocation_priority": (
                    "高" if r.certified_kw >= 50000 else
                    "中" if r.certified_kw >= 20000 else "低"
                ),
            }
            for r in rows
        ],
    }
