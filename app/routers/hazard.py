import json
import urllib.request
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/hazard", tags=["hazard"])

JSHIS_GROUND_URL = (
    "https://www.j-shis.bosai.go.jp/map/api/sstrct/V2/meshinfo.geojson"
    "?position={lng},{lat}&epsg=4326"
)

# 微地形分類 → リスクレベル（低いほど安全）
TERRAIN_RISK = {
    "山地":     1, "丘陵":     1, "台地":     1, "段丘":     2,
    "扇状地":   2, "自然堤防": 3, "砂州・砂礫州": 3, "砂丘":   3,
    "氾濫平野": 3, "旧河道":   4, "谷底平野": 4, "沖積低地": 4,
    "後背湿地": 5, "三角州":   5, "干拓地":   5, "埋立地":   5,
}

def arv_to_score(arv: float | None, terrain: str) -> int:
    """表層地盤増幅率と微地形分類から地震リスクスコア（100=安全）を算出"""
    if arv is None:
        return 50

    # ARVベーススコア
    if arv <= 1.5:
        score = 92
    elif arv <= 2.5:
        score = 78
    elif arv <= 3.5:
        score = 58
    elif arv <= 5.0:
        score = 38
    else:
        score = 18

    # 微地形分類でペナルティ補正
    risk = TERRAIN_RISK.get(terrain, 3)
    penalty = (risk - 3) * 5
    return max(5, min(100, score - penalty))


@router.get("/earthquake", summary="J-SHIS 地震ハザード・表層地盤情報を取得")
def get_earthquake_hazard(
    lat: float = Query(...),
    lng: float = Query(...),
):
    url = JSHIS_GROUND_URL.format(lat=lat, lng=lng)
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "BESS-Site-Finder/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"J-SHIS API エラー: {e}")

    features = data.get("features", [])
    if not features:
        raise HTTPException(status_code=404, detail="この地点のデータが見つかりません")

    props = features[0].get("properties", {})
    arv     = props.get("ARV")
    avs     = props.get("AVS")
    terrain = props.get("JNAME", "不明")

    score = arv_to_score(arv, terrain)
    risk_label = (
        "低リスク" if score >= 75 else
        "中程度"   if score >= 50 else
        "高リスク" if score >= 30 else
        "要注意"
    )

    return {
        "arv":         arv,
        "avs":         avs,
        "terrain":     terrain,
        "score":       score,
        "risk_label":  risk_label,
        "data_source": "防災科学技術研究所 J-SHIS 表層地盤情報 V2",
    }
