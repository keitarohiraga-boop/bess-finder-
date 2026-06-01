"""
農林水産省 筆ポリゴン（農地ナビ）インポートスクリプト
WAGRIのAPIリクエストを完全に置き換える農地ローカルDBを構築する。

ダウンロード先:
  https://www.maff.go.jp/j/tokei/porigon/hudepoligon.html
  → 都道府県ごとのZIPを展開してGeoJSON（または.shp）を用意

使用方法:
  # GeoJSONファイルを指定してインポート
  python -m app.scripts.import_fude_polygons --pref 福島県 --file fude_R05_07.geojson

  # 複数ファイルを一度に
  python -m app.scripts.import_fude_polygons --pref 福島県 --file fude_07.geojson --pref 宮城県 --file fude_04.geojson

注意事項:
  - 面積3000m²未満 / 農用地区域（class1）はインポートしない（DBサイズ削減）
  - city_code（都市計画法区分）は国土数値情報で後補完予定（現在はNone）
"""
import argparse
import json
import math
import sys

from app.database import SessionLocal, engine
from app import models

models.Base.metadata.create_all(bind=engine)

# 農振区分フィールド名候補（年度・バージョンで変わる）
_AGRI_CODE_FIELDS = [
    "農振区分コード", "VIBCODE", "agriVibCode",
    "農振区分", "振興区分", "kccode", "KCCODE",
]
_AGRI_LABEL_FIELDS = [
    "農振区分", "AgriculturalVibrationMethodClassification",
    "振興区分名", "vibLabel",
]
_AREA_FIELDS = [
    "面積(a)", "面積（a）", "MENSEKI", "area", "Area", "AREA", "面積",
]
_LAND_TYPE_FIELDS = [
    "地目", "KCCODE", "landType", "地目コード", "jimoku",
]

# 農振区分の文字列 → コード変換
_AGRI_STR_TO_CODE = {
    "農用地区域": "1", "農振農用地区域": "1", "農用地": "1",
    "農業振興地域内・農用地区域内": "1", "農業振興地域内農用地区域": "1",
    "農振内白地": "2", "白地": "2", "農業振興地域内・農用地区域外": "2",
    "農業振興地域内農用地区域外": "2",
    "農振外": "3", "農業振興地域外": "3",
}

_AGRI_CODE_TO_LABEL = {
    "1": "農用地区域（転用不可）",
    "2": "農振内白地（要協議）",
    "3": "農振外（転用可能性高）",
}


def _get_field(props: dict, candidates: list[str], default=None):
    """候補フィールド名リストから最初に見つかった値を返す"""
    for key in candidates:
        if key in props:
            return props[key]
    return default


def _polygon_centroid(coordinates) -> tuple[float, float] | None:
    """GeoJSON座標リストから重心を計算（MultiPolygon/Polygon両対応）"""
    try:
        # Polygonの場合: coordinates = [[[lng,lat], ...]]
        # MultiPolygonの場合: coordinates = [[[[lng,lat], ...]]]
        ring = coordinates
        while isinstance(ring[0][0], list):
            ring = ring[0]
        # ring = [[lng, lat], ...]
        lats = [c[1] for c in ring]
        lngs = [c[0] for c in ring]
        return sum(lats) / len(lats), sum(lngs) / len(lngs)
    except Exception:
        return None


def _area_from_props(props: dict) -> float:
    """プロパティから面積(m²)を取得。単位がa（アール）の場合は×100"""
    raw = _get_field(props, _AREA_FIELDS)
    if raw is None:
        return 0.0
    try:
        v = float(raw)
        # 農水省の面積はアール単位（1a = 100m²）が多い
        # ただし大きな値（>10000）はすでにm²の場合もある
        if "面積" in str(_AREA_FIELDS) and v < 10000:
            return v * 100  # a → m²
        return v
    except (ValueError, TypeError):
        return 0.0


def _parse_agri_code(props: dict) -> tuple[str, str]:
    """農振区分コードとラベルを取得"""
    # まずコードフィールドを探す
    code = _get_field(props, _AGRI_CODE_FIELDS)
    if code is not None:
        code = str(code).strip()
        # 文字列で来た場合
        if code in _AGRI_STR_TO_CODE:
            code = _AGRI_STR_TO_CODE[code]
        # すでに数字コード
        if code in ("1", "2", "3"):
            label = _AGRI_CODE_TO_LABEL.get(code, code)
            return code, label

    # コードが取れなかったらラベルから逆引き
    label_raw = _get_field(props, _AGRI_LABEL_FIELDS, "")
    label_raw = str(label_raw).strip()
    for key, c in _AGRI_STR_TO_CODE.items():
        if key in label_raw:
            return c, _AGRI_CODE_TO_LABEL.get(c, label_raw)

    return "2", "不明（白地扱い）"  # デフォルトは保守的に白地


def _parse_land_type(props: dict) -> str:
    raw = _get_field(props, _LAND_TYPE_FIELDS, "")
    return str(raw).strip() or "農地"


def import_geojson(pref: str, filepath: str, min_area_m2: float = 3000.0) -> int:
    """GeoJSONファイルを読み込んでFudeFieldにインポート"""
    print(f"読み込み中: {filepath}")
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)

    features = data.get("features", [])
    print(f"  フィーチャー数: {len(features):,}")

    db = SessionLocal()
    try:
        # 既存の当該都道府県データを削除
        existing = db.query(models.FudeField).filter(
            models.FudeField.prefecture == pref
        ).count()
        if existing > 0:
            print(f"  既存データ {existing:,} 件を削除中...")
            db.query(models.FudeField).filter(
                models.FudeField.prefecture == pref
            ).delete()
            db.commit()

        count = 0
        skipped_area = 0
        skipped_class1 = 0

        for i, feat in enumerate(features):
            props = feat.get("properties", {}) or {}
            geom = feat.get("geometry", {})
            if not geom:
                continue

            # 重心計算
            coords = geom.get("coordinates")
            if not coords:
                continue
            centroid = _polygon_centroid(coords)
            if centroid is None:
                continue
            lat, lng = centroid

            # 面積
            area = _area_from_props(props)
            if area < min_area_m2:
                skipped_area += 1
                continue

            # 農振区分
            agri_code, agri_label = _parse_agri_code(props)

            # class1（農用地区域）はスキップ — 転用不可のためBESS候補として不適
            if agri_code == "1":
                skipped_class1 += 1
                continue

            land_type = _parse_land_type(props)

            db.add(models.FudeField(
                prefecture=pref,
                lat=lat, lng=lng,
                area_m2=area,
                agri_code=agri_code,
                agri_label=agri_label,
                land_type=land_type,
                city_code=None,
            ))
            count += 1

            if count % 5000 == 0:
                db.commit()
                print(f"  {count:,} 件インポート中...")

        db.commit()
        print(f"完了: {count:,} 件インポート")
        print(f"  スキップ（面積不足）: {skipped_area:,} 件")
        print(f"  スキップ（class1・転用不可）: {skipped_class1:,} 件")
        return count

    finally:
        db.close()


def run():
    parser = argparse.ArgumentParser(description="農水省 筆ポリゴン インポーター")
    parser.add_argument("--pref",  required=True, action="append", help="都道府県名（例: 福島県）")
    parser.add_argument("--file",  required=True, action="append", help="GeoJSONファイルパス")
    parser.add_argument("--min-area", type=float, default=3000.0,
                        help="最小面積m²（デフォルト: 3000）")
    args = parser.parse_args()

    if len(args.pref) != len(args.file):
        print("エラー: --pref と --file の数が一致しません")
        sys.exit(1)

    total = 0
    for pref, filepath in zip(args.pref, args.file):
        print(f"\n=== {pref} ===")
        n = import_geojson(pref, filepath, args.min_area)
        total += n

    print(f"\n合計 {total:,} 件をインポートしました")


if __name__ == "__main__":
    run()
