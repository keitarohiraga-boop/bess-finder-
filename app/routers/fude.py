"""
筆ポリゴン（農地ナビ）ローカルDB ルーター
農林水産省データをローカルDBに取り込み、農地区画の位置・面積情報を提供する。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.utils import haversine


def _classify_by_wagri_codes(agri_code: str, city_code: str) -> str:
    """農振区分コードと都市計画法区分コードから農地3種区分を判定"""
    if agri_code == "1":
        return "class1-farm"
    if city_code == "1":
        return "class3-farm"
    if agri_code == "2":
        return "class2-farm"
    if agri_code == "3":
        return "class2-farm" if city_code == "2" else "class3-farm"
    return "class2-farm"


def _determine_farm_class(features: list[dict]) -> Optional[str]:
    """フィーチャーリストから最も規制の強い農地クラスを返す"""
    if not features:
        return None
    priority = {"class1-farm": 3, "class2-farm": 2, "class3-farm": 1}
    detected = None
    for feat in features:
        props = feat.get("properties", feat) if isinstance(feat, dict) else {}
        agri = str(props.get("AgriculturalVibrationMethodClassificationCode", "")).strip()
        city = str(props.get("CityPlanningActClassificationCode", "")).strip()
        fc = _classify_by_wagri_codes(agri, city)
        if detected is None or priority.get(fc, 0) > priority.get(detected, 0):
            detected = fc
    return detected or "class3-farm"

router = APIRouter(prefix="/fude", tags=["fude"])


def _fude_to_feature(field: models.FudeField) -> dict:
    """FudeFieldを農地クラス判定用フォーマットに変換"""
    return {
        "Latitude":  field.lat,
        "Longitude": field.lng,
        "Area":      field.area_m2,
        "AgriculturalVibrationMethodClassificationCode": field.agri_code,
        "AgriculturalVibrationMethodClassification":     field.agri_label,
        "CityPlanningActClassificationCode":             field.city_code or "9",
        "CityPlanningActClassification":                 "不明（国土数値情報で後補完予定）",
        "LandCategory": field.land_type,
        "source": "fude_polygon",
    }


def search_by_distance(lat: float, lng: float, distance_m: int, db: Session,
                       max_features: int = 50) -> list[dict]:
    """
    WAGRIの_search_farmlandと同等のローカルDB版。
    scan.py等から直接呼び出し可能。
    """
    deg = distance_m / 111320.0
    candidates = db.query(models.FudeField).filter(
        models.FudeField.lat.between(lat - deg, lat + deg),
        models.FudeField.lng.between(lng - deg, lng + deg),
    ).all()

    # 正確な距離でフィルタ → WAGRIフォーマットに変換
    features = [
        _fude_to_feature(f)
        for f in candidates
        if haversine(lat, lng, f.lat, f.lng) <= distance_m
    ]
    return features[:max_features]


@router.get("/status", summary="筆ポリゴンDBのインポート状況")
def fude_status(db: Session = Depends(get_db)):
    total = db.query(models.FudeField).count()
    if total == 0:
        return {"total": 0, "prefectures": [], "message": "未インポート — import_fude_polygons.py を実行してください"}

    from sqlalchemy import func
    rows = db.query(
        models.FudeField.prefecture,
        func.count(models.FudeField.id).label("count"),
        func.sum(models.FudeField.area_m2).label("total_area"),
    ).group_by(models.FudeField.prefecture).all()

    return {
        "total": total,
        "prefectures": [
            {
                "prefecture": r.prefecture,
                "count": r.count,
                "total_area_ha": round((r.total_area or 0) / 10000, 1),
            }
            for r in sorted(rows, key=lambda x: x.count, reverse=True)
        ],
    }


@router.get("/check", summary="座標から農地クラスを判定（WAGRIの代替・ローカルDB版）")
def check_fude(
    lat: float,
    lng: float,
    distance_m: int = Query(default=500, le=5000),
    db: Session = Depends(get_db),
):
    """
    ローカルDBから農地クラスを判定。APIリクエスト消費なし。
    """
    features = search_by_distance(lat, lng, distance_m, db)

    if not features:
        return {
            "lat": lat, "lng": lng,
            "farm_class": None,
            "farm_class_label": "農地データなし（このエリア未インポート）",
            "features_count": 0,
            "source": "fude_polygon_local",
        }

    farm_class = _determine_farm_class(features)
    label_map = {
        "class1-farm": "第1種農地（転用不可）",
        "class2-farm": "第2種農地（要協議）",
        "class3-farm": "第3種農地（転用可）",
        None: "農地なし",
    }

    return {
        "lat": lat, "lng": lng,
        "farm_class": farm_class,
        "farm_class_label": label_map.get(farm_class, farm_class),
        "features_count": len(features),
        "raw_sample": features[0] if features else None,
        "source": "fude_polygon_local",
    }


@router.get("/scan-preview", summary="指定エリアの農地候補をプレビュー（登録なし）")
def scan_preview(
    lat: float,
    lng: float,
    distance_m: int = Query(default=2000, le=10000),
    db: Session = Depends(get_db),
):
    """指定座標周辺の筆ポリゴン農地候補をクラス別に集計して返す（WAGRIリクエスト消費なし）"""
    features = search_by_distance(lat, lng, distance_m, db, max_features=500)

    class_counts = {"class1-farm": 0, "class2-farm": 0, "class3-farm": 0, None: 0}
    class3_fields = []

    for f in features:
        fc = _classify_by_wagri_codes(
            f["AgriculturalVibrationMethodClassificationCode"],
            f["CityPlanningActClassificationCode"],
        )
        class_counts[fc] = class_counts.get(fc, 0) + 1
        if fc == "class3-farm":
            class3_fields.append({
                "lat": f["Latitude"], "lng": f["Longitude"],
                "area_m2": f["Area"], "land_type": f["LandCategory"],
                "agri_label": f["AgriculturalVibrationMethodClassification"],
            })

    return {
        "center": {"lat": lat, "lng": lng},
        "distance_m": distance_m,
        "total_fields": len(features),
        "class_breakdown": class_counts,
        "class3_fields": sorted(class3_fields, key=lambda x: x["area_m2"], reverse=True)[:10],
        "source": "fude_polygon_local",
    }
