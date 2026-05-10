"""
OpenStreetMapから日本国内の系統用蓄電池施設データを取得してDBに格納する
実行: python -m app.scripts.import_bess_facilities
"""
import json
import urllib.request
import urllib.parse

from app.database import SessionLocal, engine
from app import models

models.Base.metadata.create_all(bind=engine)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

OVERPASS_QUERY = """
[out:json][timeout:60][bbox:20,122,46,154];
(
  node["plant:source"="battery"]["power"="plant"];
  way["plant:source"="battery"]["power"="plant"];
  node["generator:source"="battery"]["power"="generator"];
  way["generator:source"="battery"]["power"="generator"];
  node["power"="battery"];
  way["power"="battery"];
);
out center;
"""


def run():
    print("OpenStreetMapから系統用蓄電池施設データを取得中...")
    data = urllib.parse.urlencode({"data": OVERPASS_QUERY}).encode()
    req = urllib.request.Request(
        OVERPASS_URL, data=data,
        headers={"User-Agent": "BESS-Site-Finder/1.0",
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        result = json.loads(resp.read())

    elements = result.get("elements", [])
    print(f"{len(elements)} 件取得")

    db = SessionLocal()
    try:
        existing = db.query(models.BessFacility).count()
        if existing > 0:
            print(f"既に {existing} 件存在します。上書きしますか？ (y/N): ", end="")
            if input().strip().lower() != "y":
                return
            db.query(models.BessFacility).delete()
            db.commit()

        count = 0
        for elem in elements:
            tags = elem.get("tags", {})
            if elem["type"] == "node":
                lat, lng = elem.get("lat"), elem.get("lon")
            else:
                center = elem.get("center", {})
                lat, lng = center.get("lat"), center.get("lon")

            if not lat or not lng:
                continue
            if not (20 <= lat <= 46 and 122 <= lng <= 154):
                continue

            name = tags.get("name") or tags.get("name:ja") or "系統用蓄電池"
            capacity = None
            power = None

            storage = tags.get("plant:storage") or tags.get("storage")
            if storage:
                try:
                    capacity = float(storage.split()[0])
                except (ValueError, IndexError):
                    pass

            output = tags.get("plant:output:electricity") or tags.get("generator:output:electricity")
            if output:
                try:
                    power = float(output.split()[0])
                except (ValueError, IndexError):
                    pass

            db.add(models.BessFacility(
                name=name, lat=lat, lng=lng,
                capacity_mwh=capacity, power_mw=power,
                osm_id=str(elem.get("id")),
            ))
            count += 1

        db.commit()
        print(f"{count} 件の蓄電池施設データを格納しました。")
    finally:
        db.close()


if __name__ == "__main__":
    run()
