"""
サンプルデータ投入スクリプト
実行: python -m app.seed
"""
from shapely.geometry import Point
from geoalchemy2.shape import from_shape
from app.database import SessionLocal, engine
from app import models

models.Base.metadata.create_all(bind=engine)

SAMPLE_SITES = [
    dict(name="福島県 郡山市 工業団地隣接地", address="福島県郡山市田村町",
         lat=37.41, lng=140.37, area=8500, landuse="industrial",
         landuse_label="工業地域", flood="none", flood_label="浸水リスクなし",
         slope=2.1, substation_dist=420, land_price=12800,
         farm_class=None, soil_risk="なし", road_width=6.5, score=92),
    dict(name="北海道 苫小牧市 工業専用地域", address="北海道苫小牧市勇払",
         lat=42.67, lng=141.93, area=15000, landuse="industrial",
         landuse_label="工業専用地域", flood="none", flood_label="浸水リスクなし",
         slope=0.8, substation_dist=680, land_price=6200,
         farm_class=None, soil_risk="なし", road_width=8.0, score=88),
    dict(name="愛知県 豊橋市 工業専用地域", address="愛知県豊橋市神野新田",
         lat=34.73, lng=137.38, area=6200, landuse="industrial",
         landuse_label="工業専用地域", flood="low", flood_label="浸水想定0.3m",
         slope=1.2, substation_dist=950, land_price=28500,
         farm_class=None, soil_risk="液状化リスク低", road_width=5.5, score=81),
    dict(name="岡山県 倉敷市 準工業地域", address="岡山県倉敷市玉島",
         lat=34.53, lng=133.67, area=4800, landuse="quasi-industrial",
         landuse_label="準工業地域", flood="low", flood_label="浸水想定0.2m",
         slope=3.5, substation_dist=1200, land_price=18000,
         farm_class=None, soil_risk="なし", road_width=4.5, score=74),
    dict(name="茨城県 鉾田市 農地転用候補", address="茨城県鉾田市大竹",
         lat=36.16, lng=140.52, area=12000, landuse="unzoned",
         landuse_label="用途地域外（農用地区域外）", flood="none", flood_label="浸水リスクなし",
         slope=1.8, substation_dist=1800, land_price=5400,
         farm_class="class3-farm", soil_risk="なし", road_width=5.0, score=69),
    dict(name="熊本県 菊池市 農地（第2種）", address="熊本県菊池市泗水町",
         lat=32.98, lng=130.88, area=9500, landuse="unzoned",
         landuse_label="用途地域外", flood="none", flood_label="浸水リスクなし",
         slope=4.2, substation_dist=2400, land_price=7800,
         farm_class="class2-farm", soil_risk="なし", road_width=4.0, score=63),
    dict(name="宮城県 石巻市 沿岸工業地", address="宮城県石巻市渡波",
         lat=38.43, lng=141.34, area=7200, landuse="industrial",
         landuse_label="工業地域", flood="mid", flood_label="浸水想定1.5m（津波リスク注意）",
         slope=1.1, substation_dist=750, land_price=9200,
         farm_class=None, soil_risk="液状化リスク中", road_width=7.0, score=55),
    dict(name="静岡県 焼津市 第1種農地", address="静岡県焼津市大覚寺",
         lat=34.87, lng=138.32, area=5500, landuse="unzoned",
         landuse_label="農振農用地（第1種農地）", flood="mid", flood_label="浸水想定2.0m",
         slope=0.5, substation_dist=2100, land_price=22000,
         farm_class="class1-farm", soil_risk="なし", road_width=3.5, score=38),
]


def seed():
    db = SessionLocal()
    try:
        if db.query(models.Site).count() > 0:
            print("既にデータが存在します。スキップします。")
            return

        for data in SAMPLE_SITES:
            lat, lng = data.pop("lat"), data.pop("lng")
            site = models.Site(
                **data,
                geom=from_shape(Point(lng, lat), srid=4326),
            )
            db.add(site)

        db.commit()
        print(f"{len(SAMPLE_SITES)} 件のサンプルデータを投入しました。")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
