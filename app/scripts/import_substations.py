"""
OpenStreetMap Overpass API から日本全国の変電所データを取得してDBに格納する
実行: python -m app.scripts.import_substations
"""
import json
import sys
import urllib.request
import urllib.parse

from app.database import SessionLocal, engine
from app import models
from app.area_mapping import PREFECTURE_TO_AREA

models.Base.metadata.create_all(bind=engine)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# 日本のバウンディングボックス（南,西,北,東）
JAPAN_BBOX = "20,122,46,154"

OVERPASS_QUERY = f"""
[out:json][timeout:180][bbox:{JAPAN_BBOX}];
(
  node["power"="substation"];
  node["power"="sub_station"];
  way["power"="substation"];
  way["power"="sub_station"];
  relation["power"="substation"];
  relation["power"="sub_station"];
);
out center;
"""


def fetch_substations() -> list[dict]:
    print("OpenStreetMap から変電所データを取得中（node/way/relation、〜2分かかります）...")
    data = urllib.parse.urlencode({"data": OVERPASS_QUERY}).encode()
    req = urllib.request.Request(
        OVERPASS_URL,
        data=data,
        headers={"User-Agent": "BSRI-BESSFinder/1.0 (bess-site-finder; keitaro.hiraga@natural-born.jp)", "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=240) as resp:
        result = json.loads(resp.read())

    elements = result.get("elements", [])
    print(f"{len(elements)} 件取得（node/way/relation合計）")
    return elements


def prefecture_from_coords(lat: float, lng: float) -> str:
    """簡易的な都道府県判定（緯度経度の範囲で大まかに判定）"""
    if lat > 41.3:
        return "北海道"
    if lat > 40.0 and lng < 141.5:
        return "青森県"
    if lng > 141.0 and lat > 38.5:
        return "岩手県"
    if lat > 38.0 and lng < 141.0:
        return "秋田県"
    if lat > 38.2:
        return "宮城県"
    if lat > 37.5 and lng > 139.5:
        return "福島県"
    if lat > 37.5:
        return "山形県"
    if lat > 36.5 and lng > 139.8:
        return "茨城県"
    if lat > 36.5 and lng > 139.3:
        return "栃木県"
    if lat > 36.5 and lng < 139.3:
        return "群馬県"
    if lat > 36.0 and lng > 139.3:
        return "埼玉県"
    if lat > 35.5 and lng > 139.8:
        return "千葉県"
    if 35.5 < lat < 35.9 and 139.0 < lng < 139.9:
        return "東京都"
    if lat > 35.3 and lng < 139.4:
        return "山梨県"
    if 35.1 < lat < 35.7 and 139.0 < lng < 139.7:
        return "神奈川県"
    if lat > 36.0 and lng < 138.5:
        return "長野県"
    if lat > 35.5 and lng < 137.5:
        return "岐阜県"
    if lat > 35.0 and 136.5 < lng < 137.5:
        return "愛知県"
    if lat > 35.0 and 138.0 < lng < 139.0:
        return "静岡県"
    if lat > 35.0 and lng < 136.5:
        return "三重県"
    if 34.5 < lat < 35.5 and 135.0 < lng < 136.5:
        return "大阪府"
    if lat > 35.0 and 135.0 < lng < 136.0:
        return "京都府"
    if lat > 35.0 and lng < 135.0:
        return "兵庫県"
    if 34.0 < lat < 35.5 and 135.5 < lng < 136.5:
        return "奈良県"
    if 34.0 < lat < 34.5:
        return "和歌山県"
    if 34.5 < lat < 35.5 and 133.0 < lng < 134.5:
        return "鳥取県"
    if 34.5 < lat < 35.3 and 132.0 < lng < 133.5:
        return "島根県"
    if 34.0 < lat < 35.0 and 133.5 < lng < 134.5:
        return "岡山県"
    if 34.0 < lat < 35.0 and 132.0 < lng < 133.5:
        return "広島県"
    if 33.5 < lat < 34.5 and 130.5 < lng < 132.0:
        return "山口県"
    if 33.5 < lat < 34.5 and 134.0 < lng < 134.8:
        return "徳島県"
    if 33.5 < lat < 34.5 and 133.5 < lng < 134.5:
        return "香川県"
    if 33.0 < lat < 34.0 and 132.5 < lng < 133.7:
        return "愛媛県"
    if 33.0 < lat < 34.0 and 132.5 < lng < 134.0:
        return "高知県"
    if 33.0 < lat < 34.0 and 130.0 < lng < 131.5:
        return "福岡県"
    if 33.0 < lat < 34.0 and 129.5 < lng < 130.5:
        return "佐賀県"
    if 32.5 < lat < 33.5 and 129.0 < lng < 130.5:
        return "長崎県"
    if 32.0 < lat < 33.5 and 130.5 < lng < 131.5:
        return "熊本県"
    if 32.5 < lat < 33.5 and 131.0 < lng < 132.0:
        return "大分県"
    if 31.5 < lat < 32.5:
        return "宮崎県"
    if 31.0 < lat < 32.0:
        return "鹿児島県"
    if lat < 27.0:
        return "沖縄県"
    return "不明"


def run():
    db = SessionLocal()
    try:
        existing = db.query(models.Substation).count()
        if existing > 0:
            print(f"既に {existing} 件の変電所データが存在します。")
            ans = input("上書きしますか？ (y/N): ").strip().lower()
            if ans != "y":
                print("キャンセルしました。")
                return
            db.query(models.Substation).delete()
            db.commit()

        elements = fetch_substations()

        count = 0
        for elem in elements:
            # node は lat/lon 直接、way/relation は center キー内
            if elem.get("type") == "node":
                lat = elem.get("lat")
                lng = elem.get("lon")
            else:
                center = elem.get("center", {})
                lat = center.get("lat")
                lng = center.get("lon")
            if not lat or not lng:
                continue
            if not (20 <= lat <= 46 and 122 <= lng <= 154):
                continue

            tags = elem.get("tags", {})
            name = tags.get("name") or tags.get("name:ja") or "変電所"
            voltage = tags.get("voltage", "")
            try:
                v = int(str(voltage).split(";")[0].strip()) if voltage else 0
                voltage_class = "特別高圧" if v >= 66000 else "高圧" if v > 0 else "不明"
            except (ValueError, AttributeError):
                voltage_class = "不明"

            pref = prefecture_from_coords(lat, lng)

            db.add(models.Substation(
                name=name,
                prefecture=pref,
                lat=lat,
                lng=lng,
                voltage_class=voltage_class,
            ))
            count += 1

            if count % 500 == 0:
                db.commit()
                print(f"  {count} 件処理中...")

        db.commit()
        print(f"\n合計 {count} 件の変電所データを格納しました。")

    finally:
        db.close()


if __name__ == "__main__":
    run()
