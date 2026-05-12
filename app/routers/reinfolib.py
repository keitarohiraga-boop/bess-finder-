"""
不動産情報ライブラリ API 連携
- 洪水浸水想定区域（XKT026）
- 土砂災害警戒区域（XKT029）
"""
import json
import math
import os
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from shapely.geometry import Point, shape
from app.database import get_db

router = APIRouter(prefix="/reinfolib", tags=["reinfolib"])

API_KEY = os.getenv("REINFOLIB_API_KEY", "")

# 簡易的な都道府県コード推定（緯度経度から）
_PREF_BOUNDS = [
    ("01", 41.3, 45.6, 139.3, 145.9),  # 北海道
    ("02", 40.2, 41.6, 139.8, 141.7),  # 青森
    ("03", 38.7, 40.5, 140.5, 142.1),  # 岩手
    ("04", 37.7, 39.0, 140.2, 141.7),  # 宮城
    ("05", 38.9, 40.5, 139.5, 140.8),  # 秋田
    ("06", 37.7, 39.1, 139.6, 140.7),  # 山形
    ("07", 36.7, 37.9, 138.9, 141.1),  # 福島
    ("08", 35.7, 36.8, 139.7, 140.9),  # 茨城
    ("09", 36.2, 37.2, 139.3, 140.3),  # 栃木
    ("10", 36.1, 37.0, 138.4, 139.4),  # 群馬
    ("11", 35.7, 36.3, 138.7, 139.9),  # 埼玉
    ("12", 34.9, 36.1, 139.7, 140.9),  # 千葉
    ("13", 35.5, 35.9, 138.9, 139.9),  # 東京
    ("14", 35.1, 35.7, 138.9, 139.8),  # 神奈川
    ("15", 36.8, 38.6, 137.7, 139.6),  # 新潟
    ("16", 36.4, 36.9, 136.7, 137.7),  # 富山
    ("17", 36.1, 37.0, 136.2, 137.4),  # 石川
    ("18", 35.4, 36.2, 135.9, 136.9),  # 福井
    ("19", 35.2, 35.9, 138.3, 139.2),  # 山梨
    ("20", 35.2, 37.0, 136.9, 138.6),  # 長野
    ("21", 35.1, 36.4, 136.2, 137.7),  # 岐阜
    ("22", 34.5, 35.7, 137.5, 139.2),  # 静岡
    ("23", 34.5, 35.5, 136.6, 137.7),  # 愛知
    ("24", 33.9, 35.3, 135.8, 136.9),  # 三重
    ("25", 34.7, 35.6, 135.8, 136.5),  # 滋賀
    ("26", 34.7, 35.8, 135.0, 136.0),  # 京都
    ("27", 34.3, 35.1, 135.0, 135.8),  # 大阪
    ("28", 34.1, 35.7, 134.3, 135.5),  # 兵庫
    ("29", 34.1, 34.8, 135.5, 136.3),  # 奈良
    ("30", 33.4, 34.3, 135.0, 136.1),  # 和歌山
    ("31", 35.0, 35.6, 133.2, 134.3),  # 鳥取
    ("32", 34.5, 35.8, 131.7, 133.4),  # 島根
    ("33", 34.5, 35.3, 133.2, 134.5),  # 岡山
    ("34", 33.9, 35.1, 131.9, 133.5),  # 広島
    ("35", 33.7, 34.8, 130.8, 132.2),  # 山口
    ("36", 33.5, 34.4, 133.8, 134.8),  # 徳島
    ("37", 34.0, 34.5, 133.4, 134.4),  # 香川
    ("38", 32.8, 34.0, 132.0, 133.7),  # 愛媛
    ("39", 32.7, 33.9, 132.5, 134.3),  # 高知
    ("40", 33.0, 34.2, 129.9, 131.4),  # 福岡
    ("41", 33.0, 33.7, 129.7, 130.7),  # 佐賀
    ("42", 32.5, 34.4, 128.6, 130.5),  # 長崎
    ("43", 32.1, 33.5, 130.0, 131.5),  # 熊本
    ("44", 32.7, 33.9, 130.8, 132.1),  # 大分
    ("45", 31.3, 33.0, 130.7, 131.9),  # 宮崎
    ("46", 30.0, 32.5, 129.3, 131.4),  # 鹿児島
    ("47", 24.0, 28.0, 122.9, 131.4),  # 沖縄
]

def _guess_pref_code(lat: float, lng: float) -> str:
    for code, lat_min, lat_max, lng_min, lng_max in _PREF_BOUNDS:
        if lat_min <= lat <= lat_max and lng_min <= lng <= lng_max:
            return code
    return "13"  # デフォルト: 東京
BASE_URL = "https://www.reinfolib.mlit.go.jp/ex-api/external"


def latlon_to_tile(lat: float, lng: float, z: int = 14) -> tuple[int, int]:
    x = int((lng + 180) / 360 * (2 ** z))
    y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * (2 ** z))
    return x, y


def fetch_tile(endpoint: str, z: int, x: int, y: int) -> list[dict]:
    url = f"{BASE_URL}/{endpoint}?response_format=geojson&z={z}&x={x}&y={y}"
    req = urllib.request.Request(
        url, headers={"Ocp-Apim-Subscription-Key": API_KEY}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return data.get("features", [])


def point_in_features(lat: float, lng: float, features: list[dict]) -> list[dict]:
    """指定座標が含まれるフィーチャーを返す"""
    pt = Point(lng, lat)
    matches = []
    for feat in features:
        try:
            geom = shape(feat["geometry"])
            if geom.contains(pt):
                matches.append(feat["properties"])
        except Exception:
            continue
    return matches


@router.get("/hazard", summary="洪水・土砂災害リスクを取得")
def get_hazard(
    lat: float = Query(...),
    lng: float = Query(...),
):
    if not API_KEY:
        raise HTTPException(status_code=503, detail="REINFOLIB_API_KEY が設定されていません")

    z = 14
    x, y = latlon_to_tile(lat, lng, z)

    result = {
        "flood": {"risk": "none", "risk_label": "浸水リスクなし", "rank": 0, "rivers": []},
        "landslide": {"risk": "none", "risk_label": "土砂災害リスクなし", "zones": []},
    }

    # 洪水浸水想定区域
    try:
        flood_features = fetch_tile("XKT026", z, x, y)
        matches = point_in_features(lat, lng, flood_features)
        if matches:
            max_rank = max((int(m.get("A31a_205", 0)) for m in matches), default=0)
            rivers = list({m.get("A31a_202", "") for m in matches if m.get("A31a_202")})
            risk = "low" if max_rank <= 2 else "mid" if max_rank <= 3 else "high"
            risk_label = {
                "low": "浸水想定あり（0.5m未満）",
                "mid": "浸水想定あり（0.5〜3m）",
                "high": "浸水想定あり（3m以上）",
            }[risk]
            result["flood"] = {
                "risk": risk, "risk_label": risk_label,
                "rank": max_rank, "rivers": rivers,
            }
    except Exception as e:
        result["flood"]["error"] = str(e)

    # 土砂災害警戒区域
    try:
        slide_features = fetch_tile("XKT029", z, x, y)
        matches = point_in_features(lat, lng, slide_features)
        if matches:
            zone_types = list({m.get("A33_003", "") for m in matches if m.get("A33_003")})
            result["landslide"] = {
                "risk": "high",
                "risk_label": "土砂災害警戒区域内",
                "zones": zone_types,
            }
    except Exception as e:
        result["landslide"]["error"] = str(e)

    return result


@router.get("/landprice", summary="地価公示データをDBキャッシュから取得")
def get_landprice(
    lat: float = Query(...),
    lng: float = Query(...),
    db: Session = Depends(get_db),
):
    from app import models as m

    # ±0.3度以内の地価公示点を検索
    candidates = db.query(m.LandPricePoint).filter(
        m.LandPricePoint.lat.between(lat - 0.3, lat + 0.3),
        m.LandPricePoint.lng.between(lng - 0.3, lng + 0.3),
    ).all()

    if not candidates:
        return {"count": 0, "nearest": None}

    nearest = min(candidates, key=lambda p: haversine(lat, lng, p.lat, p.lng))
    return {
        "count": len(candidates),
        "nearest": {
            "price_per_m2": nearest.price_per_m2,
            "address": nearest.address,
            "year": nearest.data_year,
            "use_type": nearest.use_type,
        }
    }
