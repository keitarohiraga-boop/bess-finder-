"""
国土数値情報 土地利用メッシュ ルーター
変電所近くの農地・荒地密度から「仲介業者に打診すべきエリア」を抽出する。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app import models
from app.utils import haversine

router = APIRouter(prefix="/mesh", tags=["mesh"])

# 仲介打診候補に含める最低bess_score（農地・荒地が総セルの○%以上）
DEFAULT_MIN_BESS_SCORE = 20


@router.get("/status", summary="土地利用メッシュDBのインポート状況")
def mesh_status(db: Session = Depends(get_db)):
    total = db.query(models.LandUseMesh).count()
    if total == 0:
        return {
            "total_cells": 0,
            "prefectures": [],
            "message": "未インポート — import_land_use_mesh.py を実行してください",
        }

    rows = db.query(
        models.LandUseMesh.prefecture,
        func.count(models.LandUseMesh.id).label("cells"),
        func.avg(models.LandUseMesh.bess_score).label("avg_score"),
    ).group_by(models.LandUseMesh.prefecture).all()

    return {
        "total_cells": total,
        "prefectures": [
            {
                "prefecture": r.prefecture,
                "cells": r.cells,
                "avg_bess_score": round(r.avg_score or 0, 1),
            }
            for r in sorted(rows, key=lambda x: x.cells, reverse=True)
        ],
    }


@router.get("/check", summary="指定座標周辺の農地・荒地密度を確認")
def check_mesh(
    lat: float,
    lng: float,
    radius_km: float = Query(default=5.0, le=20.0),
    db: Session = Depends(get_db),
):
    """
    座標周辺の土地利用メッシュ集計を返す。
    bess_scoreが高いエリアほど農地・荒地が多く、仲介打診の優先度が高い。
    """
    deg = radius_km / 111.0
    cells = db.query(models.LandUseMesh).filter(
        models.LandUseMesh.lat.between(lat - deg, lat + deg),
        models.LandUseMesh.lng.between(lng - deg, lng + deg),
    ).all()

    # 実際の距離でフィルタ
    cells = [c for c in cells if haversine(lat, lng, c.lat, c.lng) <= radius_km * 1000]

    if not cells:
        return {
            "lat": lat, "lng": lng, "radius_km": radius_km,
            "total_cells": 0,
            "message": "このエリアのメッシュデータ未インポート",
        }

    paddy  = sum(c.paddy_count for c in cells)
    agri   = sum(c.agri_count  for c in cells)
    waste  = sum(c.waste_count for c in cells)
    other  = sum(c.other_count for c in cells)
    golf   = sum(c.golf_count  for c in cells)
    total  = sum(c.total_count for c in cells)

    bess_relevant = paddy + agri + waste + other + golf
    density_pct = round(bess_relevant / total * 100, 1) if total > 0 else 0

    top_cells = sorted(cells, key=lambda c: c.bess_score, reverse=True)[:5]

    return {
        "lat": lat, "lng": lng, "radius_km": radius_km,
        "total_cells": len(cells),
        "land_use_summary": {
            "paddy_cells":  paddy,   # 田
            "agri_cells":   agri,    # 畑・樹園地等
            "waste_cells":  waste,   # 荒地
            "other_cells":  other,   # 未利用地等
            "golf_cells":   golf,    # ゴルフ場
        },
        "bess_density_pct": density_pct,
        "broker_approach_priority": (
            "高" if density_pct >= 40 else
            "中" if density_pct >= 20 else
            "低"
        ),
        "top_spots": [
            {"lat": c.lat, "lng": c.lng, "bess_score": c.bess_score,
             "paddy": c.paddy_count, "agri": c.agri_count,
             "waste": c.waste_count, "other": c.other_count}
            for c in top_cells
        ],
    }


@router.get("/hotspots", summary="変電所近くの農地・荒地密度が高いエリア一覧")
def get_hotspots(
    prefecture: str,
    min_bess_score: int = Query(default=DEFAULT_MIN_BESS_SCORE, le=100),
    subst_radius_km: float = Query(default=5.0, le=20.0),
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_db),
):
    """
    指定都道府県内で、変電所から subst_radius_km 以内かつ農地密度が高い
    1kmセルを返す。これが「仲介業者に打診するエリア候補」になる。
    """
    # 該当都道府県の土地利用メッシュ取得
    cells = db.query(models.LandUseMesh).filter(
        models.LandUseMesh.prefecture == prefecture,
        models.LandUseMesh.bess_score >= min_bess_score,
    ).all()

    if not cells:
        return {
            "prefecture": prefecture,
            "hotspots": [],
            "message": f"{prefecture}のメッシュデータ未インポート or 対象セルなし",
        }

    # 変電所との距離チェック
    substations = db.query(models.Substation).filter(
        models.Substation.prefecture == prefecture
    ).all()

    deg = subst_radius_km / 111.0
    hotspots = []
    for cell in cells:
        # 近くに変電所があるか（バウンディングボックス高速フィルタ後にHaversine精査）
        nearby = [
            s for s in substations
            if abs(s.lat - cell.lat) <= deg and abs(s.lng - cell.lng) <= deg
            and haversine(cell.lat, cell.lng, s.lat, s.lng) <= subst_radius_km * 1000
        ]
        if not nearby:
            continue

        nearest = min(nearby, key=lambda s: haversine(cell.lat, cell.lng, s.lat, s.lng))
        dist_km = round(haversine(cell.lat, cell.lng, nearest.lat, nearest.lng) / 1000, 2)

        hotspots.append({
            "lat": cell.lat,
            "lng": cell.lng,
            "bess_score": cell.bess_score,
            "paddy_cells": cell.paddy_count,
            "agri_cells":  cell.agri_count,
            "waste_cells": cell.waste_count,
            "nearest_substation": nearest.name,
            "subst_dist_km": dist_km,
            "broker_priority": (
                "高" if cell.bess_score >= 50 and dist_km <= 2 else
                "中" if cell.bess_score >= 30 else
                "低"
            ),
        })

    hotspots.sort(key=lambda x: (x["bess_score"] * -1, x["subst_dist_km"]))
    hotspots = hotspots[:limit]

    return {
        "prefecture": prefecture,
        "total_candidates": len(hotspots),
        "hotspots": hotspots,
    }
