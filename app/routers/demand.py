"""
周辺大口需要家・防災施設をOpenStreetMapから取得して評価するエンドポイント
"""
import json
import urllib.request
import urllib.parse

from fastapi import APIRouter, HTTPException, Query
from app.utils import haversine

router = APIRouter(prefix="/demand", tags=["demand"])

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# 施設タイプ定義
FACILITY_TYPES = {
    # 大口需要家
    "hospital":    {"label": "病院・医療機関", "weight": 5, "category": "demand",   "color": "#f87171"},
    "industrial":  {"label": "工場・工業施設", "weight": 4, "category": "demand",   "color": "#fb923c"},
    "datacenter":  {"label": "データセンター", "weight": 5, "category": "demand",   "color": "#a78bfa"},
    "commercial":  {"label": "大型商業施設",   "weight": 3, "category": "demand",   "color": "#fbbf24"},
    # 防災・レジリエンス
    "fire_station":{"label": "消防署",         "weight": 3, "category": "resilience","color": "#f97316"},
    "shelter":     {"label": "避難所",         "weight": 2, "category": "resilience","color": "#38bdf8"},
    "university":  {"label": "大学・高専",     "weight": 2, "category": "demand",   "color": "#4ade80"},
}

OVERPASS_QUERY = """
[out:json][timeout:30][bbox:{s},{w},{n},{e}];
(
  node["amenity"="hospital"];
  way["amenity"="hospital"];
  node["healthcare"="hospital"];
  node["landuse"="industrial"];
  way["landuse"="industrial"];
  node["building"="industrial"];
  way["building"="industrial"];
  node["building"="data_center"];
  way["building"="data_center"];
  node["shop"="mall"];
  way["shop"="mall"];
  node["building"="retail"]["shop"];
  way["shop"="supermarket"];
  node["amenity"="fire_station"];
  way["amenity"="fire_station"];
  node["amenity"="shelter"];
  way["amenity"="college"];
  way["amenity"="university"];
);
out center;
"""



def classify(tags: dict) -> str | None:
    amenity  = tags.get("amenity", "")
    building = tags.get("building", "")
    landuse  = tags.get("landuse", "")
    shop     = tags.get("shop", "")
    healthcare = tags.get("healthcare", "")

    if amenity in ("hospital", "clinic") or healthcare == "hospital":
        return "hospital"
    if amenity == "fire_station":
        return "fire_station"
    if amenity == "shelter":
        return "shelter"
    if amenity in ("university", "college"):
        return "university"
    if landuse == "industrial" or building in ("industrial", "factory", "warehouse"):
        return "industrial"
    if building == "data_center" or tags.get("facility") == "data_center":
        return "datacenter"
    if shop in ("mall", "supermarket", "department_store") or building == "retail":
        return "commercial"
    return None


@router.get("", summary="周辺大口需要家・防災施設を取得")
def get_nearby(
    lat:    float = Query(...),
    lng:    float = Query(...),
    radius: int   = Query(default=5000, ge=500, le=10000),
):
    deg = radius / 111000
    bbox = f"{lat-deg},{lng-deg},{lat+deg},{lng+deg}"
    query = OVERPASS_QUERY.format(s=lat-deg, w=lng-deg, n=lat+deg, e=lng+deg)

    try:
        data = urllib.parse.urlencode({"data": query}).encode()
        req  = urllib.request.Request(
            OVERPASS_URL, data=data,
            headers={"User-Agent": "BESS-Site-Finder/1.0",
                     "Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=35) as resp:
            result = json.loads(resp.read())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Overpass API エラー: {e}")

    facilities = []
    seen = set()

    for elem in result.get("elements", []):
        tags = elem.get("tags", {})
        kind = classify(tags)
        if not kind:
            continue

        # 座標取得
        if elem["type"] == "node":
            elat, elng = elem.get("lat"), elem.get("lon")
        else:
            center = elem.get("center", {})
            elat, elng = center.get("lat"), center.get("lon")

        if not elat or not elng:
            continue

        dist = haversine(lat, lng, elat, elng)
        if dist > radius:
            continue

        name = tags.get("name") or tags.get("name:ja") or FACILITY_TYPES[kind]["label"]

        # 重複排除（同名・同距離）
        key = (kind, round(dist, -2))
        if key in seen:
            continue
        seen.add(key)

        facilities.append({
            "kind":     kind,
            "label":    FACILITY_TYPES[kind]["label"],
            "category": FACILITY_TYPES[kind]["category"],
            "weight":   FACILITY_TYPES[kind]["weight"],
            "color":    FACILITY_TYPES[kind]["color"],
            "name":     name,
            "dist_m":   round(dist),
            "lat":      elat,
            "lng":      elng,
        })

    facilities.sort(key=lambda x: x["dist_m"])

    # スコア計算（距離減衰 × 重みの合計を正規化）
    demand_score = 0
    resilience_score = 0
    for f in facilities:
        decay = max(0, 1 - f["dist_m"] / radius)
        weighted = f["weight"] * decay * 10
        if f["category"] == "demand":
            demand_score += weighted
        else:
            resilience_score += weighted

    demand_score     = min(100, round(demand_score))
    resilience_score = min(100, round(resilience_score))

    # カテゴリ別集計
    counts = {}
    for f in facilities:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1

    return {
        "radius_m":        radius,
        "total":           len(facilities),
        "demand_score":    demand_score,
        "resilience_score": resilience_score,
        "counts":          counts,
        "facilities":      facilities[:30],  # 上位30件
    }
