"""
WAGRI 農地ナビ API 連携
- OAuth 2.0 (client_credentials) でアクセストークン取得
- SearchByDistance で候補地座標から農地区分を判定
- Site.farm_class を実データで更新する
"""
import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app import models

router = APIRouter(prefix="/wagri", tags=["wagri"])

CLIENT_ID     = os.getenv("WAGRI_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("WAGRI_CLIENT_SECRET", "")

TOKEN_URL    = "https://api.wagri2.net/token"
SEARCH_URL   = "https://api.wagri2.net/basic/farmland/AgriculturalLand/SearchByDistance"

# トークンキャッシュ（プロセス内）
_token_cache: dict = {"token": None, "expires_at": 0}


# ===== 認証 =====

def _get_token() -> str:
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    if not CLIENT_ID or not CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="WAGRI_CLIENT_ID / WAGRI_CLIENT_SECRET が未設定です")

    body = urllib.parse.urlencode({
        "grant_type":    "client_credentials",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"WAGRIトークン取得失敗: {e.code}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"WAGRI接続エラー: {str(e)}")

    token = data.get("access_token")
    if not token:
        raise HTTPException(status_code=502, detail=f"WAGRIトークンが取得できません: {data}")

    _token_cache["token"] = token
    _token_cache["expires_at"] = time.time() + data.get("expires_in", 3600)
    return token


# ===== 農地データ取得 =====

def _search_farmland(lat: float, lng: float, distance_m: int = 100, max_features: int = 50) -> list[dict]:
    """指定座標から distance_m 以内の農地ピン情報を取得"""
    token = _get_token()
    params = urllib.parse.urlencode({
        "Latitude":  lat,
        "Longitude": lng,
        "Distance":  distance_m,
    })
    url = f"{SEARCH_URL}?{params}"
    req = urllib.request.Request(
        url,
        headers={"X-Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []  # 農地なし
        raise HTTPException(status_code=502, detail=f"WAGRI APIエラー: {e.code}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"WAGRI接続エラー: {str(e)}")

    # レスポンスがリストまたは features キーを持つ GeoJSON
    if isinstance(data, list):
        features = data
    elif isinstance(data, dict):
        features = data.get("features", data.get("value", []))
    else:
        features = []

    # WAGRIは$topを無視するためPython側で切り詰め
    total_received = len(features)
    truncated = features[:max_features]

    # リクエストをDBに記録（セッション横断で使用量追跡）
    try:
        log_db = SessionLocal()
        log_db.add(models.WagriRequestLog(
            called_at=datetime.now(timezone.utc).isoformat(),
            lat=lat, lng=lng, distance_m=distance_m,
            features_count=total_received,
        ))
        log_db.commit()
        log_db.close()
    except Exception:
        pass  # ログ失敗はサイレントに無視

    return truncated


def _determine_farm_class(features: list[dict]) -> Optional[str]:
    """
    WAGRIの農地ピンデータから農地クラスを判定。
    AgriculturalVibrationMethodClassificationCode（農振区分）と
    CityPlanningActClassificationCode（都市計画法区分）の組み合わせで判定。
    """
    if not features:
        return None  # 農地なし

    class_priority = {"class1-farm": 3, "class2-farm": 2, "class3-farm": 1}
    detected = None

    for feat in features:
        props = feat.get("properties", feat) if isinstance(feat, dict) else {}
        agri_code = str(props.get("AgriculturalVibrationMethodClassificationCode", "")).strip()
        city_code  = str(props.get("CityPlanningActClassificationCode", "")).strip()
        farm_class = _classify_by_wagri_codes(agri_code, city_code)

        if detected is None or class_priority.get(farm_class, 0) > class_priority.get(detected, 0):
            detected = farm_class

    return detected or "class3-farm"


def _classify_by_wagri_codes(agri_code: str, city_code: str) -> str:
    """
    農振区分コードと都市計画法区分コードから農地3種区分を判定。

    agri_code:
      1 = 農業振興地域内農用地区域（農振農用地） → 第1種農地
      2 = 農業振興地域内・農用地区域外（白地）
      3 = 農業振興地域外

    city_code:
      1 = 市街化区域 → 第3種農地（転用原則可）
      2 = 市街化調整区域 → 第1〜2種
      3 = 非線引き都市計画区域
      4 = 都市計画区域外
      9 = 調査中
    """
    # 農振農用地区域 → 第1種農地（転用不可）
    if agri_code == "1":
        return "class1-farm"

    # 市街化区域内 → 第3種農地（転用可）
    if city_code == "1":
        return "class3-farm"

    # 農振白地（農業振興地域内・農用地区域外）
    if agri_code == "2":
        # 市街化調整区域 → 第2種農地（要協議）
        if city_code in ("2", "3"):
            return "class2-farm"
        # 都市計画区域外・調査中 → 保守的に第2種
        return "class2-farm"

    # 農業振興地域外
    if agri_code == "3":
        if city_code in ("2",):
            return "class2-farm"
        return "class3-farm"

    # 不明な場合は保守的に第2種
    return "class2-farm"


# ===== エンドポイント =====

@router.get("/check", summary="座標から農地クラスを判定")
def check_farmland(
    lat: float,
    lng: float,
    distance_m: int = 100,
):
    features = _search_farmland(lat, lng, distance_m)
    farm_class = _determine_farm_class(features)
    return {
        "lat": lat, "lng": lng,
        "farm_class": farm_class,
        "farm_class_label": {
            "class1-farm": "第1種農地（転用不可）",
            "class2-farm": "第2種農地（要協議）",
            "class3-farm": "第3種農地（転用可）",
            None: "農地なし",
        }.get(farm_class),
        "features_count": len(features),
        "raw_sample": features[0] if features else None,  # デバッグ用（初回確認時に使用）
    }


@router.post("/update-site/{site_id}", summary="候補地の農地クラスをWAGRIで更新")
def update_site_farmclass(site_id: int, db: Session = Depends(get_db)):
    site = db.get(models.Site, site_id)
    if not site:
        raise HTTPException(status_code=404, detail="候補地が見つかりません")

    features = _search_farmland(site.lat, site.lng, distance_m=500)
    farm_class = _determine_farm_class(features)
    old_class = site.farm_class
    site.farm_class = farm_class
    db.commit()

    return {
        "site_id": site_id,
        "site_name": site.name,
        "farm_class_before": old_class,
        "farm_class_after": farm_class,
        "features_count": len(features),
    }


@router.post("/update-all", summary="全候補地の農地クラスをWAGRIで一括更新")
def update_all_farmclass(db: Session = Depends(get_db)):
    sites = db.query(models.Site).all()
    results = []
    errors = []

    for site in sites:
        try:
            # 工業・商業・住居系用途地域は農地になりえないのでスキップ
            if site.landuse in ("industrial", "quasi-industrial", "commercial", "residential"):
                results.append({
                    "site_id": site.id, "name": site.name,
                    "before": site.farm_class, "after": site.farm_class,
                    "skipped": "非農地用途地域のためスキップ",
                })
                continue

            features = _search_farmland(site.lat, site.lng, distance_m=500)
            farm_class = _determine_farm_class(features)
            old_class = site.farm_class
            site.farm_class = farm_class
            results.append({
                "site_id": site.id, "name": site.name,
                "before": old_class, "after": farm_class,
            })
        except Exception as e:
            errors.append({"site_id": site.id, "name": site.name, "error": str(e)})
            continue

    db.commit()
    return {
        "updated": len(results),
        "errors": len(errors),
        "results": results,
        "error_details": errors,
    }


@router.patch("/set-farmclass/{site_id}", summary="候補地のfarm_classを手動で設定（nullも可）")
def set_farmclass(
    site_id: int,
    farm_class: Optional[str] = None,
    db: Session = Depends(get_db),
):
    site = db.get(models.Site, site_id)
    if not site:
        raise HTTPException(status_code=404, detail="候補地が見つかりません")
    old = site.farm_class
    site.farm_class = farm_class if farm_class != "null" else None
    db.commit()
    return {"site_id": site_id, "name": site.name, "before": old, "after": site.farm_class}


@router.get("/status", summary="WAGRI API の設定状態を確認")
def status():
    return {
        "configured": bool(CLIENT_ID and CLIENT_SECRET),
        "client_id_set": bool(CLIENT_ID),
        "secret_set": bool(CLIENT_SECRET),
    }


@router.get("/usage", summary="WAGRI APIリクエスト使用量（今月・累計）")
def usage(db: Session = Depends(get_db)):
    """試用版の月100リクエスト上限に対する消費状況を返す。毎月1日にリセット。"""
    now = datetime.now(timezone.utc)
    jst = now + timedelta(hours=9)
    month_start = f"{jst.year}-{jst.month:02d}-01T00:00:00+09:00"

    # 今月分（JST月初以降）
    all_logs = db.query(models.WagriRequestLog).all()
    month_start_dt = datetime(jst.year, jst.month, 1, tzinfo=timezone(timedelta(hours=9)))
    monthly = [l for l in all_logs if datetime.fromisoformat(l.called_at) >= month_start_dt.astimezone(timezone.utc)]

    monthly_count = len(monthly)
    remaining = max(0, 100 - monthly_count)

    return {
        "month": f"{jst.year}-{jst.month:02d}",
        "monthly_requests": monthly_count,
        "monthly_limit": 100,
        "remaining": remaining,
        "usage_pct": round(monthly_count / 100 * 100, 1),
        "total_all_time": len(all_logs),
        "recent_10": [
            {
                "called_at": l.called_at,
                "lat": l.lat, "lng": l.lng,
                "distance_m": l.distance_m,
                "features_count": l.features_count,
            }
            for l in sorted(all_logs, key=lambda x: x.called_at, reverse=True)[:10]
        ],
    }
