"""
国土数値情報 土地利用細分メッシュ（L03-b）インポートスクリプト
100mセルを1kmセルに集計してDBに格納する。WAGRI不要で農地・荒地の密度を把握できる。

ダウンロード先:
  https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-L03-b.html
  → 対象メッシュコードのZIPをダウンロード（GML＋Shapefile入り）

メッシュコード対応表（1次メッシュ / 約100km×100km）:
  福島県: 5339, 5439, 5440, 5539, 5540, 5541, 5639, 5640, 5641
  宮城県: 5640, 5740, 5741
  茨城県: 5239, 5240, 5339, 5340

URL例:
  https://nlftp.mlit.go.jp/ksj/gml/data/L03-b/L03-b-16/L03-b-16_5540-jgd_GML.zip

使用方法:
  python -m app.scripts.import_land_use_mesh --pref 福島県 --mesh 5540 5640 5440
  python -m app.scripts.import_land_use_mesh --pref 福島県 --file /path/to/L03-b-16_5540.shp

  # 自動ダウンロード（インターネット接続必要）
  python -m app.scripts.import_land_use_mesh --pref 福島県 --mesh 5540 5640 --download
"""
import argparse
import os
import sys
import urllib.request
import zipfile
import tempfile
import collections
from pathlib import Path

import shapefile  # pyshp

from app.database import SessionLocal, engine
from app import models
from app.area_mapping import PREFECTURE_TO_AREA

models.Base.metadata.create_all(bind=engine)

# 土地利用区分コード
_AGRI_CODES = {
    "0100": "paddy",    # 田
    "0200": "agri",     # その他農用地（畑・樹園地・牧草地）
    "0400": "waste",    # 荒地（耕作放棄地含む）
    "0800": "other",    # その他用地（未利用地含む）
    "1200": "golf",     # ゴルフ場（大面積・平坦）
}
_ALL_TARGET_CODES = set(_AGRI_CODES.keys())

BASE_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/L03-b/L03-b-16/"


def download_mesh(mesh_code: str, dest_dir: str) -> str | None:
    """1次メッシュZIPをダウンロードして展開、Shapefileパスを返す"""
    zip_name = f"L03-b-16_{mesh_code}-jgd_GML.zip"
    url = BASE_URL + zip_name
    zip_path = os.path.join(dest_dir, zip_name)

    print(f"  ダウンロード中: {url}")
    try:
        urllib.request.urlretrieve(url, zip_path)
    except Exception as e:
        print(f"  ダウンロード失敗: {e}")
        return None

    # 展開
    extract_dir = os.path.join(dest_dir, f"mesh_{mesh_code}")
    with zipfile.ZipFile(zip_path, 'r') as z:
        z.extractall(extract_dir)

    # .shp ファイルを探す
    shp_files = list(Path(extract_dir).glob("*.shp"))
    if not shp_files:
        print(f"  Shapefileが見つかりません")
        return None
    return str(shp_files[0])


def _mesh8_centroid(mesh8: str) -> tuple[float, float]:
    """
    8桁の3次メッシュコードから重心(lat, lng)を計算。
    XX YY p q r s → XX=lat*1.5の整数部 / YY=lng-100 / pq=2次細分 / rs=3次細分
    """
    try:
        p = int(mesh8[0:2])   # lat * 1.5 (floor)
        u = int(mesh8[2:4])   # lng - 100 (floor)
        q = int(mesh8[4])     # 2次: 0-7 (lat方向)
        r = int(mesh8[5])     # 2次: 0-7 (lng方向)
        s = int(mesh8[6])     # 3次: 0-9 (lat方向)
        t = int(mesh8[7])     # 3次: 0-9 (lng方向)

        lat_base = p / 1.5
        lng_base = u + 100.0

        # 2次: 1.5°/8 lat, 1°/8 lng
        lat_2 = lat_base + q * (1.5 / 8)
        lng_2 = lng_base + r * (1.0 / 8)

        # 3次: (1.5/8)/10 lat, (1/8)/10 lng → 約0.01875° × 0.0125°
        cell_lat = 1.5 / 8 / 10
        cell_lng = 1.0 / 8 / 10

        lat = lat_2 + (s + 0.5) * cell_lat
        lng = lng_2 + (t + 0.5) * cell_lng
        return round(lat, 6), round(lng, 6)
    except Exception:
        return 0.0, 0.0


def import_shapefile(pref: str, shp_path: str) -> int:
    """ShapefileをLandUseMeshに1km集計でインポート"""
    print(f"  読み込み中: {shp_path}")

    sf = shapefile.Reader(shp_path, encoding='cp932')
    fields = [f[0] for f in sf.fields[1:]]

    # フィールドインデックス（エンコード問題があるので位置で取得）
    # フィールド順: メッシュ, 土地利用区分, 更新年月日
    mesh_idx = 0
    code_idx = 1

    print(f"  レコード数: {sf.numRecords:,}")

    # 1km（3次メッシュ=8桁）でグループ集計
    groups: dict[str, dict] = {}

    for i, rec in enumerate(sf.iterRecords()):
        mesh10 = str(rec[mesh_idx]).strip()
        code4  = str(rec[code_idx]).strip().zfill(4)

        if len(mesh10) < 8:
            continue

        mesh8 = mesh10[:8]
        if mesh8 not in groups:
            groups[mesh8] = {
                "paddy": 0, "agri": 0, "waste": 0,
                "other": 0, "golf": 0, "total": 0,
            }

        groups[mesh8]["total"] += 1
        col = _AGRI_CODES.get(code4)
        if col:
            groups[mesh8][col] += 1

        if i % 100000 == 0 and i > 0:
            print(f"    {i:,} 件処理中...")

    print(f"  1kmセル数: {len(groups):,}")

    db = SessionLocal()
    try:
        # 既存データを削除
        existing = db.query(models.LandUseMesh).filter(
            models.LandUseMesh.prefecture == pref,
        ).count()
        if existing > 0:
            # メッシュコード範囲で特定できないので全件対象
            print(f"  既存データ {existing:,} 件（同一都道府県）はスキップ。--overwriteで上書き可能")

        count = 0
        for mesh8, g in groups.items():
            if g["total"] == 0:
                continue

            bess_score = round(
                (g["paddy"] + g["agri"] + g["waste"] + g["other"] + g["golf"])
                / g["total"] * 100
            )
            if bess_score == 0:
                continue  # 対象外セルのみの1kmブロックはスキップ

            lat, lng = _mesh8_centroid(mesh8)
            if lat == 0.0 and lng == 0.0:
                continue

            db.add(models.LandUseMesh(
                mesh_code_1km=mesh8,
                lat=lat, lng=lng,
                prefecture=pref,
                paddy_count=g["paddy"],
                agri_count=g["agri"],
                waste_count=g["waste"],
                other_count=g["other"],
                golf_count=g["golf"],
                total_count=g["total"],
                bess_score=bess_score,
            ))
            count += 1

            if count % 5000 == 0:
                db.commit()
                print(f"    {count:,} 件インポート中...")

        db.commit()
        print(f"  完了: {count:,} 件の1kmセルをインポート")
        return count

    finally:
        db.close()


def run():
    parser = argparse.ArgumentParser(description="国土数値情報 土地利用メッシュ インポーター")
    parser.add_argument("--pref", required=True, help="都道府県名（例: 福島県）")
    parser.add_argument("--mesh", nargs="+", help="1次メッシュコード（例: 5540 5640）")
    parser.add_argument("--file", nargs="+", help="Shapefileパス（直接指定）")
    parser.add_argument("--download", action="store_true",
                        help="--meshと併用: 自動ダウンロード＆インポート")
    args = parser.parse_args()

    total = 0

    if args.file:
        for fp in args.file:
            print(f"\n=== {fp} ===")
            n = import_shapefile(args.pref, fp)
            total += n

    elif args.mesh and args.download:
        with tempfile.TemporaryDirectory() as tmpdir:
            for mesh in args.mesh:
                print(f"\n=== メッシュ {mesh} のダウンロード ===")
                shp_path = download_mesh(mesh, tmpdir)
                if shp_path:
                    n = import_shapefile(args.pref, shp_path)
                    total += n

    elif args.mesh:
        print("メッシュコードが指定されました。--download を付けると自動取得します。")
        print("手動手順:")
        for m in args.mesh:
            print(f"  curl -L '{BASE_URL}L03-b-16_{m}-jgd_GML.zip' -o /tmp/{m}.zip")
            print(f"  unzip /tmp/{m}.zip -d /tmp/mesh_{m}")
            print(f"  python -m app.scripts.import_land_use_mesh --pref {args.pref} --file /tmp/mesh_{m}/L03-b-16_{m}.shp")
        return

    else:
        parser.print_help()
        return

    print(f"\n合計 {total:,} 件の1kmセルをインポートしました")


if __name__ == "__main__":
    run()
