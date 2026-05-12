"""
不動産情報ライブラリ API 連携
- 洪水浸水想定区域（XKT026）
- 土砂災害警戒区域（XKT029）
"""
import json
import math
import os
import urllib.request

from fastapi import APIRouter, HTTPException, Query
from shapely.geometry import Point, shape

router = APIRouter(prefix="/reinfolib", tags=["reinfolib"])

API_KEY = os.getenv("REINFOLIB_API_KEY", "")


@router.get("/debug-key")
def debug_key():
    import os
    key = os.getenv("REINFOLIB_API_KEY", "")
    return {
        "key_set": bool(key),
        "key_length": len(key),
        "key_first4": key[:4] if key else "",
        "all_env_keys": [k for k in os.environ if "REINFOLIB" in k or "API" in k]
    }
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


@router.get("/landprice", summary="地価公示データを取得")
def get_landprice(
    lat: float = Query(...),
    lng: float = Query(...),
    year: int = Query(default=2024),
):
    if not API_KEY:
        raise HTTPException(status_code=503, detail="REINFOLIB_API_KEY が設定されていません")

    z = 15
    x, y = latlon_to_tile(lat, lng, z)

    try:
        url = f"{BASE_URL}/XCT001?response_format=geojson&z={z}&x={x}&y={y}&year={year}"
        req = urllib.request.Request(
            url, headers={"Ocp-Apim-Subscription-Key": API_KEY}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        features = data.get("features", [])
        if not features:
            return {"count": 0, "nearest": None}

        # 最寄りの地価公示点を返す
        pt = Point(lng, lat)
        nearest = min(
            features,
            key=lambda f: pt.distance(Point(f["geometry"]["coordinates"]))
        )
        props = nearest["properties"]
        return {
            "count": len(features),
            "nearest": {
                "price_per_m2": props.get("L01_006"),
                "address": props.get("L01_025", ""),
                "year": props.get("L01_001"),
                "use_type": props.get("L01_027", ""),
            }
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
