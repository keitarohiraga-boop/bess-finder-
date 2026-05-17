"""
全国土地スキャンルーター
市区町村コード単位でWAGRI農地ナビAPIを呼び出し、
BESSに適した転用可能農地を自動発見・候補地登録する。
"""
import json
import math
import ssl
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
from app.routers.wagri import _get_token, _determine_farm_class, SEARCH_URL

router = APIRouter(prefix="/scan", tags=["scan"])

# 国交省 Webland API で市区町村コードを取得
WEBLAND_CITY_URL = "https://www.land.mlit.go.jp/webland/api/CitySearch?area={pref_code}"

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


def _get_city_codes(pref_code: int) -> list[dict]:
    """国交省WebLand APIから市区町村コード一覧を取得（SSL対応）"""
    url = WEBLAND_CITY_URL.format(pref_code=f"{pref_code:02d}")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read())
        cities = data.get("data", [])
        if cities:
            return cities
    except Exception:
        pass
    # フォールバック：ハードコード済み市区町村コード
    return _FALLBACK_CITIES.get(pref_code, [])


# フォールバック用 主要都道府県の市区町村コード
_FALLBACK_CITIES: dict[int, list[dict]] = {
    43: [  # 熊本県
        {"id":"43101","name":"熊本市"},{"id":"43202","name":"八代市"},
        {"id":"43203","name":"人吉市"},{"id":"43204","name":"荒尾市"},
        {"id":"43205","name":"水俣市"},{"id":"43206","name":"玉名市"},
        {"id":"43208","name":"山鹿市"},{"id":"43210","name":"菊池市"},
        {"id":"43211","name":"宇土市"},{"id":"43212","name":"上天草市"},
        {"id":"43213","name":"宇城市"},{"id":"43214","name":"阿蘇市"},
        {"id":"43215","name":"天草市"},{"id":"43216","name":"合志市"},
        {"id":"43348","name":"美里町"},{"id":"43364","name":"玉東町"},
        {"id":"43367","name":"南関町"},{"id":"43368","name":"長洲町"},
        {"id":"43369","name":"和水町"},{"id":"43404","name":"大津町"},
        {"id":"43405","name":"菊陽町"},{"id":"43423","name":"南小国町"},
        {"id":"43424","name":"小国町"},{"id":"43425","name":"産山村"},
        {"id":"43428","name":"高森町"},{"id":"43432","name":"西原村"},
        {"id":"43433","name":"南阿蘇村"},{"id":"43441","name":"御船町"},
        {"id":"43442","name":"嘉島町"},{"id":"43443","name":"益城町"},
        {"id":"43444","name":"甲佐町"},{"id":"43447","name":"山都町"},
        {"id":"43468","name":"氷川町"},{"id":"43482","name":"芦北町"},
        {"id":"43484","name":"津奈木町"},{"id":"43501","name":"錦町"},
        {"id":"43505","name":"多良木町"},{"id":"43506","name":"湯前町"},
        {"id":"43507","name":"水上村"},{"id":"43510","name":"相良村"},
        {"id":"43511","name":"五木村"},{"id":"43512","name":"山江村"},
        {"id":"43513","name":"球磨村"},{"id":"43514","name":"あさぎり町"},
        {"id":"43531","name":"苓北町"},
    ],
    40: [  # 福岡県（代表的な市のみ）
        {"id":"40100","name":"福岡市"},{"id":"40202","name":"大牟田市"},
        {"id":"40203","name":"久留米市"},{"id":"40205","name":"飯塚市"},
        {"id":"40207","name":"田川市"},{"id":"40208","name":"柳川市"},
        {"id":"40211","name":"八女市"},{"id":"40212","name":"筑後市"},
        {"id":"40213","name":"大川市"},{"id":"40214","name":"行橋市"},
        {"id":"40215","name":"豊前市"},{"id":"40216","name":"中間市"},
        {"id":"40217","name":"小郡市"},{"id":"40218","name":"筑紫野市"},
        {"id":"40219","name":"春日市"},{"id":"40220","name":"大野城市"},
        {"id":"40221","name":"宗像市"},{"id":"40222","name":"太宰府市"},
        {"id":"40224","name":"古賀市"},{"id":"40225","name":"福津市"},
        {"id":"40226","name":"うきは市"},{"id":"40227","name":"宮若市"},
        {"id":"40228","name":"嘉麻市"},{"id":"40229","name":"朝倉市"},
        {"id":"40230","name":"みやま市"},{"id":"40231","name":"糸島市"},
    ],
}


def _add_check_digit(code5: str) -> str:
    """5桁市区町村コード → 6桁（チェックディジット付き）に変換"""
    digits = [int(c) for c in code5]
    weights = [6, 5, 4, 3, 2]
    total = sum(d * w for d, w in zip(digits, weights))
    check = 11 - (total % 11)
    if check >= 10:
        check = 0
    return code5 + str(check)


def _wagri_by_city(city_code: str) -> list[dict]:
    """WAGRIのSearchByCityCodeで農地ピン情報を取得"""
    # 5桁コードの場合は6桁に変換
    if len(city_code) == 5:
        city_code = _add_check_digit(city_code)
    token = _get_token()
    url = f"https://api.wagri2.net/basic/farmland/AgriculturalLand/SearchByCityCode?CityCode={city_code}"
    req = urllib.request.Request(
        url,
        headers={"X-Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        if isinstance(data, list):
            return data
        return data.get("value", data.get("features", []))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise
    except Exception:
        return []


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

    # 出力制御スコア
    curtailment = db.get(models.CurtailmentData, jepx_area)
    ctrl_score = curtailment.curtailment_score if curtailment else 30

    # 立地スコア
    grid_s = max(0, 100 - subst_dist / 50)
    area_s = min(100, area / 100)
    location_score = round(grid_s * 0.6 + area_s * 0.4)

    # 収益スコア
    revenue_score = round(jepx_score * 0.6 + ctrl_score * 0.4)

    # 農地は第3種＝規制リスク低
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
    }


def _sse(event: str, data: dict) -> str:
    return f"data: {json.dumps({'event': event, **data}, ensure_ascii=False)}\n\n"


async def _run_scan(prefecture: str, min_score: int, max_register: int, db: Session) -> AsyncGenerator[str, None]:
    pref_code = PREF_CODE_MAP.get(prefecture)
    if not pref_code:
        yield _sse("error", {"message": f"都道府県名が不正: {prefecture}"})
        return

    yield _sse("start", {"message": f"{prefecture}のスキャンを開始します"})

    # 市区町村一覧を取得
    cities = _get_city_codes(pref_code)
    if not cities:
        yield _sse("error", {"message": "市区町村コードの取得に失敗しました"})
        return

    yield _sse("progress", {"message": f"{len(cities)}市区町村を発見。農地データ取得を開始します", "total": len(cities)})

    candidates = []
    for i, city in enumerate(cities):
        city_code = city.get("id", "")
        city_name = city.get("name", "")
        if not city_code:
            continue

        yield _sse("progress", {
            "message": f"[{i+1}/{len(cities)}] {city_name} をスキャン中...",
            "current": i + 1, "total": len(cities)
        })

        try:
            features = _wagri_by_city(city_code)
        except Exception as e:
            yield _sse("progress", {"message": f"{city_name}: スキップ ({str(e)[:30]})"})
            continue

        # 第3種農地のみ抽出
        class3 = [
            f for f in features
            if _determine_farm_class([f]) == "class3-farm"
            and f.get("Area", 0) >= 3000  # 3,000㎡以上
        ]

        for feat in class3:
            lat = feat.get("Latitude")
            lng = feat.get("Longitude")
            area = feat.get("Area", 5000)
            address = feat.get("Address", f"{prefecture}{city_name}")
            if not lat or not lng:
                continue
            scores = _score_candidate(lat, lng, area, prefecture, db)
            candidates.append({
                "lat": lat, "lng": lng, "area": area,
                "address": address, "prefecture": prefecture,
                "city_name": city_name, "scores": scores,
            })

        time.sleep(0.3)  # APIレート制限対策

    yield _sse("progress", {"message": f"スキャン完了。{len(candidates)}件の第3種農地を発見。スコア上位を登録中..."})

    # スコア降順でソートし上位を登録
    candidates.sort(key=lambda c: c["scores"]["overall"], reverse=True)
    registered = 0

    for cand in candidates:
        if registered >= max_register:
            break
        if cand["scores"]["overall"] < min_score:
            continue

        # 重複チェック（既存サイトから500m以内は除外）
        existing = db.query(models.Site).filter(
            models.Site.lat.between(cand["lat"] - 0.01, cand["lat"] + 0.01),
            models.Site.lng.between(cand["lng"] - 0.01, cand["lng"] + 0.01),
        ).all()
        if any(haversine(cand["lat"], cand["lng"], s.lat, s.lng) < 500 for s in existing):
            continue

        s = cand["scores"]
        site = models.Site(
            name=f"【自動発見】{cand['city_name']} 農地転用候補",
            address=cand["address"],
            prefecture=cand["prefecture"],
            area=cand["area"],
            landuse="unzoned",
            landuse_label="用途地域外（農地転用候補）",
            flood="none",
            flood_label="未確認",
            slope=2.0,
            substation_dist=s["subst_dist"],
            land_price=None,
            farm_class="class3-farm",
            soil_risk="未確認",
            road_width=4.0,
            score=s["overall"],
            lat=cand["lat"],
            lng=cand["lng"],
        )
        db.add(site)
        registered += 1

    db.commit()
    yield _sse("done", {
        "message": f"完了！{registered}件の候補地を登録しました（スコア{min_score}点以上）",
        "registered": registered,
        "scanned": len(candidates),
    })


# ===== エンドポイント =====

@router.get("/prefectures", summary="スキャン対象都道府県一覧")
def list_prefectures():
    return {"prefectures": list(PREF_CODE_MAP.keys())}


@router.post("/prefecture", summary="都道府県単位で農地候補地をスキャン・登録（SSEストリーミング）")
def scan_prefecture(
    prefecture: str,
    min_score: int = 50,
    max_register: int = 100,
    db: Session = Depends(get_db),
):
    if prefecture not in PREF_CODE_MAP:
        raise HTTPException(status_code=400, detail=f"都道府県名が不正です: {prefecture}")

    async def generate():
        async for chunk in _run_scan(prefecture, min_score, max_register, db):
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
