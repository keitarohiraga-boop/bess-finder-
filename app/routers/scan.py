"""
全国土地スキャンルーター
OSM Overpass API・土地利用メッシュ・筆ポリゴンを使ってBESS候補地を自動発見・登録する。
"""
import json
import math
import time
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app import models
from app.utils import haversine
from app.area_mapping import PREFECTURE_TO_AREA
router = APIRouter(prefix="/scan", tags=["scan"])

# 都道府県名 → 2桁コード
PREF_CODE_MAP = {
    "北海道":1,"青森県":2,"岩手県":3,"宮城県":4,"秋田県":5,"山形県":6,"福島県":7,
    "茨城県":8,"栃木県":9,"群馬県":10,"埼玉県":11,"千葉県":12,"東京都":13,"神奈川県":14,
    "新潟県":15,"富山県":16,"石川県":17,"福井県":18,"山梨県":19,"長野県":20,
    "岐阜県":21,"静岡県":22,"愛知県":23,"三重県":24,"滋賀県":25,"京都府":26,
    "大阪府":27,"兵庫県":28,"奈良県":29,"和歌山県":30,"鳥取県":31,"島根県":32,
    "岡山県":33,"広島県":34,"山口県":35,"徳島県":36,"香川県":37,"愛媛県":38,
    "高知県":39,"福岡県":40,"佐賀県":41,"長崎県":42,"熊本県":43,"大分県":44,
    "宮崎県":45,"鹿児島県":46,"沖縄県":47,
}



def _score_candidate(lat: float, lng: float, area: float, prefecture: str, db: Session) -> dict:
    """簡易スコア計算（シミュレーター不使用・高速版）"""
    # 最寄り変電所距離
    substations = db.query(models.Substation).filter(
        models.Substation.lat.between(lat - 0.3, lat + 0.3),
        models.Substation.lng.between(lng - 0.3, lng + 0.3),
    ).all()
    if substations:
        nearest = min(substations, key=lambda s: haversine(lat, lng, s.lat, s.lng))
        subst_dist = round(haversine(lat, lng, nearest.lat, nearest.lng))
        subst_name = nearest.name
    else:
        subst_dist = 99999
        subst_name = "不明"

    # JEPXエリアスコア
    jepx_area = PREFECTURE_TO_AREA.get(prefecture, "東京")
    jepx = db.get(models.JepxAreaMetrics, jepx_area)
    jepx_score = jepx.jepx_score if jepx else 50

    # 出力制御スコア（太陽光・風力別スコアを持つ場合は最大値を採用）
    curtailment = db.get(models.CurtailmentData, jepx_area)
    if curtailment:
        solar_ctrl = curtailment.solar_score or curtailment.curtailment_score or 30
        wind_ctrl  = curtailment.wind_score  or 0
        ctrl_score = max(solar_ctrl, wind_ctrl)
    else:
        ctrl_score = 30

    # 立地スコア
    grid_s = max(0, 100 - subst_dist / 50)
    area_s = min(100, area / 100)
    location_score = round(grid_s * 0.6 + area_s * 0.4)

    # 収益スコア
    revenue_score = round(jepx_score * 0.6 + ctrl_score * 0.4)

    # リスクスコア（デフォルト: 中程度）
    risk_score = 75

    # 需要スコア（都道府県別日射量）
    solar = db.get(models.SolarPotential, prefecture)
    solar_score = solar.solar_score if solar else 50
    demand_score = round(solar_score * 0.4 + 50 * 0.6)

    overall = round(location_score * 0.30 + revenue_score * 0.40 + demand_score * 0.15 + risk_score * 0.15)

    return {
        "overall": overall,
        "location": location_score,
        "revenue": revenue_score,
        "demand": demand_score,
        "risk": risk_score,
        "subst_dist": subst_dist,
        "subst_name": subst_name,
        "jepx_area": jepx_area,
        "solar_ctrl_score": solar_ctrl if curtailment else None,
        "wind_ctrl_score": wind_ctrl if curtailment else None,
    }


def _sse(event: str, data: dict) -> str:
    return f"data: {json.dumps({'event': event, **data}, ensure_ascii=False)}\n\n"


# 都道府県バウンディングボックス（lat_min, lat_max, lng_min, lng_max）
_PREF_BOUNDS: dict[str, tuple] = {
    "北海道":(41.3,45.6,139.3,145.9),"青森県":(40.2,41.6,139.8,141.7),
    "岩手県":(38.7,40.5,140.5,142.1),"宮城県":(37.7,39.0,140.2,141.7),
    "秋田県":(38.9,40.5,139.5,140.8),"山形県":(37.7,39.1,139.6,140.7),
    "福島県":(36.7,37.9,138.9,141.1),"茨城県":(35.7,36.8,139.7,140.9),
    "栃木県":(36.2,37.2,139.3,140.3),"群馬県":(36.1,37.0,138.4,139.4),
    "埼玉県":(35.7,36.3,138.7,139.9),"千葉県":(34.9,36.1,139.7,140.9),
    "東京都":(35.5,35.9,138.9,139.9),"神奈川県":(35.1,35.7,138.9,139.8),
    "新潟県":(36.8,38.6,137.7,139.6),"富山県":(36.4,36.9,136.7,137.7),
    "石川県":(36.1,37.0,136.2,137.4),"福井県":(35.4,36.2,135.9,136.9),
    "山梨県":(35.2,35.9,138.3,139.2),"長野県":(35.2,37.0,136.9,138.6),
    "岐阜県":(35.1,36.4,136.2,137.7),"静岡県":(34.5,35.7,137.5,139.2),
    "愛知県":(34.5,35.5,136.6,137.7),"三重県":(33.9,35.3,135.8,136.9),
    "滋賀県":(34.7,35.6,135.8,136.5),"京都府":(34.7,35.8,135.0,136.0),
    "大阪府":(34.3,35.1,135.0,135.8),"兵庫県":(34.1,35.7,134.3,135.5),
    "奈良県":(34.1,34.8,135.5,136.3),"和歌山県":(33.4,34.3,135.0,136.1),
    "鳥取県":(35.0,35.6,133.2,134.3),"島根県":(34.5,35.8,131.7,133.4),
    "岡山県":(34.5,35.3,133.2,134.5),"広島県":(33.9,35.1,131.9,133.5),
    "山口県":(33.7,34.8,130.8,132.2),"徳島県":(33.5,34.4,133.8,134.8),
    "香川県":(34.0,34.5,133.4,134.4),"愛媛県":(32.8,34.0,132.0,133.7),
    "高知県":(32.7,33.9,132.5,134.3),"福岡県":(33.0,34.2,129.9,131.4),
    "佐賀県":(33.0,33.7,129.7,130.7),"長崎県":(32.5,34.4,128.6,130.5),
    "熊本県":(32.1,33.5,130.0,131.5),"大分県":(32.7,33.9,130.8,132.1),
    "宮崎県":(31.3,33.0,130.7,131.9),"鹿児島県":(30.0,32.5,129.3,131.4),
    "沖縄県":(24.0,28.0,122.9,131.4),
}

KM_PER_LAT = 111.0
def _make_grid(pref: str, grid_km: float = 15.0) -> list[tuple]:
    """都道府県のバウンディングボックス内に格子点を生成"""
    bounds = _PREF_BOUNDS.get(pref)
    if not bounds:
        return []
    lat_min, lat_max, lng_min, lng_max = bounds
    lat_step = grid_km / KM_PER_LAT
    center_lat = (lat_min + lat_max) / 2
    lng_step = grid_km / (KM_PER_LAT * math.cos(math.radians(center_lat)))
    points = []
    lat = lat_min
    while lat <= lat_max:
        lng = lng_min
        while lng <= lng_max:
            points.append((round(lat, 4), round(lng, 4)))
            lng += lng_step
        lat += lat_step
    return points


# ===== OSM (Overpass API) スキャン =====

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# 低圧BESS（49.9kW）候補として抽出するOSMタグ
_OSM_TAGS = [
    ("amenity", "parking"),       # 大規模駐車場
    ("landuse",  "brownfield"),   # 廃工場・遊休工業地
    ("landuse",  "vacant"),       # 空き地
    ("landuse",  "industrial"),   # 工業用地（現役・低利用）
    ("landuse",  "meadow"),       # 草地・牧草地
    ("landuse",  "landfill"),     # 廃棄物処分場跡地
    ("natural",  "scrub"),        # 藪地・未整備地
    ("landuse",  "farmland"),     # 農地（OSMベース・筆ポリゴン移行前の広域カバー）
    ("landuse",  "grass"),        # 草地（大面積のもの）
]

_OSM_LABEL = {
    "parking":    "大規模駐車場",
    "brownfield": "遊休地（元工業）",
    "vacant":     "空き地",
    "industrial": "工業用地",
    "meadow":     "草地・牧草地",
    "landfill":   "処分場跡地",
    "scrub":      "藪地・未整備地",
    "farmland":   "農地（OSM）",
    "grass":      "草地",
}


def _polygon_area_m2(coords: list[tuple]) -> float:
    """緯度経度ポリゴンの面積をm²で返す（Shoelace + 球面補正）"""
    n = len(coords)
    if n < 3:
        return 0.0
    avg_lat = sum(c[0] for c in coords) / n
    lat_m = 111320.0
    lng_m = 111320.0 * math.cos(math.radians(avg_lat))
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        xi, yi = coords[i][1] * lng_m, coords[i][0] * lat_m
        xj, yj = coords[j][1] * lng_m, coords[j][0] * lat_m
        area += xi * yj - xj * yi
    return abs(area) / 2.0


def _score_low_voltage_candidate(lat: float, lng: float, area: float, land_type: str, prefecture: str, db: Session) -> dict:
    """低圧BESS（49.9kW）専用スコアリング。変電所距離は使わない。"""

    # 面積スコア（20m²=最低限、300m²以上=ほぼ満点）
    if area < 20:
        area_score = 0
    elif area < 50:
        area_score = 50 + (area - 20) / 30 * 20   # 50〜70
    elif area < 100:
        area_score = 70 + (area - 50) / 50 * 15   # 70〜85
    elif area < 300:
        area_score = 85 + (area - 100) / 200 * 10 # 85〜95
    else:
        area_score = 95

    # 需給調整市場エリアスコア（JEPXスコアを流用）
    jepx_area = PREFECTURE_TO_AREA.get(prefecture, "東京")
    jepx = db.get(models.JepxAreaMetrics, jepx_area)
    market_score = jepx.jepx_score if jepx else 50

    # 土地種別スコア（設置しやすさ）
    zone_score = {
        "parking":    85,  # アスファルト済み・権利が明確
        "vacant":     78,  # 空き地・整地が必要な場合あり
        "meadow":     75,  # 草地・平坦で整地コスト低
        "farmland":   73,  # 農地（OSM）・農地転用手続き要
        "scrub":      70,  # 藪地・整地コストあり
        "brownfield": 68,  # 元工業地・土壌汚染調査要
        "grass":      65,  # 草地・維持管理用途の場合あり
        "industrial": 63,  # 現役工業地・交渉が必要
        "landfill":   55,  # 処分場跡地・土壌調査必須
    }.get(land_type, 60)

    # 洪水リスク（固定値70 → 将来的にハザードAPIと連携）
    flood_score = 70

    overall = round(
        area_score   * 0.30 +
        market_score * 0.30 +
        zone_score   * 0.25 +
        flood_score  * 0.15
    )

    return {
        "overall": overall,
        "area_score": round(area_score),
        "market_score": round(market_score),
        "zone_score": round(zone_score),
        "flood_score": round(flood_score),
        "jepx_area": jepx_area,
        "scoring_model": "low_voltage_49kw",
    }


def _query_osm_candidates(lat: float, lng: float, radius_m: int = 500) -> list[dict]:
    """Overpass APIで候補地（駐車場・遊休地等）を取得。認証不要・無料。"""
    tag_lines = "\n  ".join(
        f'way["{k}"="{v}"](around:{radius_m},{lat},{lng});'
        for k, v in _OSM_TAGS
    )
    query = f"[out:json][timeout:30];\n(\n  {tag_lines}\n);\nout body;\n>;\nout skel qt;\n"
    try:
        import urllib.parse
        body = urllib.parse.urlencode({"data": query}).encode("utf-8")
        req = urllib.request.Request(
            OVERPASS_URL,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "BSRI-BESSFinder/1.0 (bess-site-finder; contact@bsri.jp)",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=35) as resp:
            result = json.loads(resp.read())
    except Exception as e:
        raise RuntimeError(f"Overpass API: {str(e)[:80]}")

    nodes = {
        e["id"]: (e["lat"], e["lon"])
        for e in result.get("elements", [])
        if e["type"] == "node"
    }

    candidates, seen = [], set()
    for elem in result.get("elements", []):
        if elem["type"] != "way":
            continue
        tags = elem.get("tags", {})
        coords = [nodes[nid] for nid in elem.get("nodes", []) if nid in nodes]
        if len(coords) < 3:
            continue
        area = _polygon_area_m2(coords)
        if area < 15:  # 15m²未満は無視
            continue
        clat = round(sum(c[0] for c in coords) / len(coords), 5)
        clng = round(sum(c[1] for c in coords) / len(coords), 5)
        key = (clat, clng)
        if key in seen:
            continue
        seen.add(key)
        land_type = tags.get("amenity") or tags.get("landuse") or tags.get("natural", "unknown")
        candidates.append({
            "lat": clat, "lng": clng,
            "area": round(area),
            "land_type": land_type,
            "land_label": _OSM_LABEL.get(land_type, land_type),
            "name": tags.get("name", ""),
            "osm_id": elem["id"],
        })
    return candidates


async def _run_osm_scan(
    prefecture: str,
    min_score: int,
    max_register: int,
    db: Session,
    radius_m: int = 500,
    min_area_m2: int = 20,
    max_substations: int = 50,
) -> AsyncGenerator[str, None]:
    """変電所を起点にOverpass APIで低圧BESS候補地をスキャン"""
    # 対象変電所を都道府県でフィルタ（DBから）
    substations = db.query(models.Substation).filter(
        models.Substation.prefecture == prefecture
    ).limit(max_substations).all()

    if not substations:
        yield _sse("error", {"message": f"{prefecture}の変電所データが見つかりません"})
        return

    yield _sse("start", {
        "message": (
            f"{prefecture}のOSMスキャンを開始します "
            f"（変電所{len(substations)}箇所 × 半径{radius_m}m）"
        )
    })

    seen_coords: set = set()
    candidates = []

    for i, sub in enumerate(substations):
        yield _sse("progress", {
            "message": f"[{i+1}/{len(substations)}] {sub.name}（{sub.lat:.3f},{sub.lng:.3f}）周辺をスキャン中...",
            "current": i + 1, "total": len(substations),
        })
        try:
            osm_hits = _query_osm_candidates(sub.lat, sub.lng, radius_m)
        except Exception as e:
            yield _sse("progress", {"message": f"  → スキップ: {str(e)[:50]}"})
            time.sleep(1.0)
            continue

        new_hits = 0
        for hit in osm_hits:
            if hit["area"] < min_area_m2:
                continue
            key = (hit["lat"], hit["lng"])
            if key in seen_coords:
                continue
            seen_coords.add(key)

            pref = prefecture
            scores = _score_candidate(hit["lat"], hit["lng"], hit["area"], pref, db)
            candidates.append({**hit, "prefecture": pref, "scores": scores})
            new_hits += 1

        yield _sse("progress", {"message": f"  → {len(osm_hits)}件取得、新規{new_hits}件"})
        time.sleep(0.5)  # Overpassへの礼儀

    yield _sse("progress", {
        "message": f"スキャン完了。{len(candidates)}件の候補地を発見。スコア上位を登録中..."
    })

    candidates.sort(key=lambda c: c["scores"]["overall"], reverse=True)
    registered = 0

    for cand in candidates:
        if registered >= max_register:
            break
        s = cand["scores"]
        if s["overall"] < min_score:
            continue

        existing = db.query(models.Site).filter(
            models.Site.lat.between(cand["lat"] - 0.005, cand["lat"] + 0.005),
            models.Site.lng.between(cand["lng"] - 0.005, cand["lng"] + 0.005),
        ).all()
        if any(haversine(cand["lat"], cand["lng"], sx.lat, sx.lng) < 300 for sx in existing):
            continue

        label = cand["land_label"]
        name_hint = f"「{cand['name']}」" if cand["name"] else ""
        db.add(models.Site(
            name=f"【OSM発見】{label}{name_hint} {cand['prefecture']}",
            address=cand["prefecture"],
            prefecture=cand["prefecture"],
            area=cand["area"],
            landuse="osm",
            landuse_label=label,
            flood="none", flood_label="未確認",
            slope=2.0,
            substation_dist=s["subst_dist"],
            land_price=None,
            farm_class=None,
            soil_risk="未確認",
            road_width=4.0,
            score=s["overall"],
            lat=cand["lat"], lng=cand["lng"],
        ))
        registered += 1

    db.commit()
    yield _sse("done", {
        "message": f"完了！{registered}件の低圧BESS候補地を登録しました",
        "registered": registered,
        "found": len(candidates),
        "source": "OSM",
    })



# ===== OSMエンドポイント =====

@router.get("/osm-spot-check", summary="OSM Overpass APIの動作確認（1座標・無料）")
def osm_spot_check(
    lat: float = 35.68,
    lng: float = 139.69,
    radius_m: int = 500,
    db: Session = Depends(get_db),
):
    """
    指定座標周辺のOSM候補地（駐車場・遊休地等）を取得して返す。
    WAGRI不要・無料・認証なし。低圧BESS（49.9kW）候補地の動作確認用。
    """
    try:
        hits = _query_osm_candidates(lat, lng, radius_m)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    pref = "東京都"
    for p, (lat_min, lat_max, lng_min, lng_max) in _PREF_BOUNDS.items():
        if lat_min <= lat <= lat_max and lng_min <= lng <= lng_max:
            pref = p
            break

    results = []
    for h in hits:
        scores = _score_candidate(h["lat"], h["lng"], h["area"], pref, db)
        results.append({**h, "scores": scores, "prefecture": pref})

    return {
        "total_found": len(hits),
        "radius_m": radius_m,
        "source": "OSM Overpass API",
        "candidates": sorted(results, key=lambda x: x["scores"]["overall"], reverse=True)[:20],
    }


@router.post("/osm-prefecture", summary="都道府県グリッド方式でOSM BESS候補地をスキャン（SSE）")
def scan_osm_prefecture(
    prefecture: str,
    min_score: int = 40,
    max_register: int = 200,
    radius_m: int = 500,
    min_area_m2: int = 20,
    grid_km: float = 10.0,
    max_points: int = 30,
    bess_type: str = "low",   # "low"=49.9kW配電線接続 / "high"=500kW+変電所接続
    db: Session = Depends(get_db),
):
    """
    都道府県をグリッド分割してOverpass APIでBESS候補地をスキャン。

    - bess_type="low"  : 低圧49.9kW。面積・需要・土地種別でスコアリング。変電所距離は不使用
    - bess_type="high" : 高圧500kW+。変電所距離・大面積を重視したスコアリング
    - radius_m: 各グリッド点からの検索半径（デフォルト500m）
    - grid_km: グリッド間隔km（デフォルト10km）
    - max_points: 最大グリッド点数（デフォルト30点）
    """
    if prefecture not in PREF_CODE_MAP:
        raise HTTPException(status_code=400, detail=f"都道府県名が不正です: {prefecture}")

    radius_m = min(radius_m, 2000)
    max_points = min(max_points, 100)

    async def generate():
        if prefecture not in _PREF_BOUNDS:
            yield _sse("error", {"message": f"バウンディングボックスが未定義: {prefecture}"})
            return

        grid = _make_grid(prefecture, grid_km=grid_km)
        actual_points = min(len(grid), max_points)

        yield _sse("start", {
            "message": f"{prefecture}のOSMスキャンを開始します（グリッド{grid_km}km / {actual_points}点 / 半径{radius_m}m）"
        })

        seen_coords: set = set()
        candidates = []

        for i, (lat, lng) in enumerate(grid[:actual_points]):
            yield _sse("progress", {
                "message": f"[{i+1}/{actual_points}] ({lat:.3f},{lng:.3f}) をスキャン中...",
                "current": i + 1, "total": actual_points,
            })
            try:
                hits = _query_osm_candidates(lat, lng, radius_m)
            except Exception as e:
                yield _sse("progress", {"message": f"  → スキップ: {str(e)[:50]}"})
                time.sleep(0.5)
                continue

            new_hits = 0
            for h in hits:
                if h["area"] < min_area_m2:
                    continue
                key = (h["lat"], h["lng"])
                if key in seen_coords:
                    continue
                seen_coords.add(key)
                if bess_type == "low":
                    scores = _score_low_voltage_candidate(
                        h["lat"], h["lng"], h["area"], h["land_type"], prefecture, db
                    )
                else:
                    scores = _score_candidate(h["lat"], h["lng"], h["area"], prefecture, db)
                candidates.append({**h, "prefecture": prefecture, "scores": scores})
                new_hits += 1

            yield _sse("progress", {"message": f"  → {len(hits)}件取得、新規{new_hits}件"})
            time.sleep(1.5)  # Overpass 429対策

        type_label = "低圧49.9kW" if bess_type == "low" else "高圧500kW+"
        yield _sse("progress", {
            "message": f"スキャン完了。{len(candidates)}件の候補地を発見。スコア上位を登録中..."
        })

        candidates.sort(key=lambda c: c["scores"]["overall"], reverse=True)
        registered = 0

        for cand in candidates:
            if registered >= max_register:
                break
            s = cand["scores"]
            if s["overall"] < min_score:
                continue
            existing = db.query(models.Site).filter(
                models.Site.lat.between(cand["lat"] - 0.005, cand["lat"] + 0.005),
                models.Site.lng.between(cand["lng"] - 0.005, cand["lng"] + 0.005),
            ).all()
            if any(haversine(cand["lat"], cand["lng"], sx.lat, sx.lng) < 300 for sx in existing):
                continue
            label = cand["land_label"]
            name_hint = f"「{cand['name']}」" if cand["name"] else ""
            # 低圧は変電所距離が不要なので0（配電線接続・距離不問）、高圧は実距離を使う
            subst_dist = 0 if bess_type == "low" else s.get("subst_dist", 99999)
            db.add(models.Site(
                name=f"【OSM{type_label}】{label}{name_hint} {cand['prefecture']}",
                address=cand["prefecture"],
                prefecture=cand["prefecture"],
                area=cand["area"],
                landuse="osm",
                landuse_label=label,
                flood="none", flood_label="未確認",
                slope=2.0,
                substation_dist=subst_dist,
                land_price=None,
                farm_class=None,
                soil_risk="未確認",
                road_width=4.0,
                score=s["overall"],
                lat=cand["lat"], lng=cand["lng"],
            ))
            registered += 1

        db.commit()
        yield _sse("done", {
            "message": f"完了！{registered}件の{type_label}BESS候補地を登録しました",
            "registered": registered,
            "found": len(candidates),
            "source": "OSM",
            "bess_type": bess_type,
        })

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
