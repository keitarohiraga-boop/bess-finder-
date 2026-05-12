"""
地価公示データを都道府県別にDBにキャッシュするスクリプト
実行: python -m app.scripts.cache_landprice
"""
import gzip, io, json, math, os, urllib.request
from app.database import SessionLocal, engine
from app import models

models.Base.metadata.create_all(bind=engine)

API_KEY = os.getenv("REINFOLIB_API_KEY", "")
BASE_URL = "https://www.reinfolib.mlit.go.jp/ex-api/external/XCT001"

PREFS = {
    "01":"北海道","02":"青森県","03":"岩手県","04":"宮城県","05":"秋田県",
    "06":"山形県","07":"福島県","08":"茨城県","09":"栃木県","10":"群馬県",
    "11":"埼玉県","12":"千葉県","13":"東京都","14":"神奈川県","15":"新潟県",
    "16":"富山県","17":"石川県","18":"福井県","19":"山梨県","20":"長野県",
    "21":"岐阜県","22":"静岡県","23":"愛知県","24":"三重県","25":"滋賀県",
    "26":"京都府","27":"大阪府","28":"兵庫県","29":"奈良県","30":"和歌山県",
    "31":"鳥取県","32":"島根県","33":"岡山県","34":"広島県","35":"山口県",
    "36":"徳島県","37":"香川県","38":"愛媛県","39":"高知県","40":"福岡県",
    "41":"佐賀県","42":"長崎県","43":"熊本県","44":"大分県","45":"宮崎県",
    "46":"鹿児島県","47":"沖縄県",
}

def fetch_pref(area_code, year=2025):
    records = []
    for division in ["00", "05"]:
        url = f"{BASE_URL}?year={year}&area={area_code}&division={division}"
        req = urllib.request.Request(url, headers={"Ocp-Apim-Subscription-Key": API_KEY})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
                text = gz.read().decode("utf-8", errors="replace")
            data = json.loads(text)
            records.extend(data.get("data", []))
        except Exception as e:
            print(f"  {area_code}-{division}: skip ({e})")
    return records

def haversine(lat1, lng1, lat2, lng2):
    R = 6371000
    dlat = math.radians(lat2-lat1)
    dlng = math.radians(lng2-lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlng/2)**2
    return R*2*math.asin(math.sqrt(a))

def run():
    if not API_KEY:
        print("REINFOLIB_API_KEY が設定されていません")
        return

    db = SessionLocal()
    total = 0
    try:
        for code, pref in PREFS.items():
            print(f"取得中: {pref}...", end=" ", flush=True)
            records = fetch_pref(code)
            if not records:
                print("0件")
                continue

            LAT_KEY = next((k for k in records[0] if "緯度" in k), None)
            LNG_KEY = next((k for k in records[0] if "経度" in k), None)
            PRICE_KEY = next((k for k in records[0] if "㎡" in k or "公示価格" in k), None)
            ADDR_KEY = next((k for k in records[0] if "住居表示" in k), None)
            USE_KEY = next((k for k in records[0] if "用途区分" in k), None)

            if not LAT_KEY or not LNG_KEY or not PRICE_KEY:
                print(f"キー不明: skip")
                continue

            count = 0
            for r in records:
                try:
                    lat = float(r.get(LAT_KEY) or 0)
                    lng = float(r.get(LNG_KEY) or 0)
                    price = int(r.get(PRICE_KEY) or 0)
                    if not lat or not lng or not price:
                        continue
                    db.add(models.LandPricePoint(
                        prefecture=pref,
                        lat=lat, lng=lng,
                        price_per_m2=price,
                        use_type=r.get(USE_KEY, ""),
                        address=r.get(ADDR_KEY, ""),
                        data_year=2025,
                    ))
                    count += 1
                except Exception:
                    continue

            db.commit()
            total += count
            print(f"{count}件")

        print(f"\n合計 {total} 件の地価公示データをキャッシュしました。")
    finally:
        db.close()

if __name__ == "__main__":
    run()
