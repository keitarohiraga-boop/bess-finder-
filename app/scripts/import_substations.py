"""
国土数値情報 P03（発電施設）から変電所データを取得してDBに格納する
実行: python -m app.scripts.import_substations
オプション: --pref 01,02,03  （都道府県コードを指定。省略時は全国）
"""
import io
import math
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

from app.database import SessionLocal, engine
from app import models

models.Base.metadata.create_all(bind=engine)

# 都道府県コード → 都道府県名
PREFECTURES = {
    "01": "北海道", "02": "青森県", "03": "岩手県", "04": "宮城県",
    "05": "秋田県", "06": "山形県", "07": "福島県", "08": "茨城県",
    "09": "栃木県", "10": "群馬県", "11": "埼玉県", "12": "千葉県",
    "13": "東京都", "14": "神奈川県", "15": "新潟県", "16": "富山県",
    "17": "石川県", "18": "福井県", "19": "山梨県", "20": "長野県",
    "21": "岐阜県", "22": "静岡県", "23": "愛知県", "24": "三重県",
    "25": "滋賀県", "26": "京都府", "27": "大阪府", "28": "兵庫県",
    "29": "奈良県", "30": "和歌山県", "31": "鳥取県", "32": "島根県",
    "33": "岡山県", "34": "広島県", "35": "山口県", "36": "徳島県",
    "37": "香川県", "38": "愛媛県", "39": "高知県", "40": "福岡県",
    "41": "佐賀県", "42": "長崎県", "43": "熊本県", "44": "大分県",
    "45": "宮崎県", "46": "鹿児島県", "47": "沖縄県",
}

# 試行するデータ年度（新しい順）
YEARS = ["2022", "2021", "2019"]

BASE_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/P03/P03-{year}/P03-{yy}_{code}_GML.zip"


def download_zip(pref_code: str) -> bytes | None:
    for year in YEARS:
        yy = year[2:]
        url = BASE_URL.format(year=year, yy=yy, code=pref_code)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            if len(data) > 1000:  # HTMLエラーページでないことを確認
                return data
        except Exception:
            continue
    return None


def parse_gml(gml_text: str, pref_name: str) -> list[dict]:
    """GMLを解析して変電所（class=2）を抽出"""
    substations = []
    try:
        root = ET.fromstring(gml_text)
    except ET.ParseError:
        return []

    # 名前空間を動的に検出
    ns_map = {}
    for match in re.finditer(r'xmlns:?(\w*)=["\']([^"\']+)["\']', gml_text[:2000]):
        prefix, uri = match.group(1), match.group(2)
        ns_map[prefix or "default"] = uri

    # 変電所クラスコード（2 または 02）
    SUBSTATION_CLASSES = {"2", "02"}

    # 全要素を走査してclass=2の施設を抽出
    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

        if tag not in ("PowerGenerate", "PowerStation"):
            continue

        # クラス判定
        cls_val = None
        for child in elem:
            child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if child_tag in ("class", "type", "powerGenerateType"):
                cls_val = (child.text or "").strip()
                break

        if cls_val not in SUBSTATION_CLASSES and cls_val != "変電所":
            continue

        # 座標取得
        lat, lng, name = None, None, ""
        for child in elem:
            child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if child_tag == "name":
                name = (child.text or "").strip()
            elif child_tag in ("position", "representativePoint"):
                for pos_elem in child.iter():
                    pos_tag = pos_elem.tag.split("}")[-1] if "}" in pos_elem.tag else pos_elem.tag
                    if pos_tag == "pos" and pos_elem.text:
                        coords = pos_elem.text.strip().split()
                        if len(coords) >= 2:
                            try:
                                lat, lng = float(coords[0]), float(coords[1])
                            except ValueError:
                                pass

        if lat and lng and 20 <= lat <= 50 and 120 <= lng <= 150:
            substations.append({
                "name": name or f"{pref_name}変電所",
                "prefecture": pref_name,
                "lat": lat,
                "lng": lng,
            })

    return substations


def import_prefecture(pref_code: str, db) -> int:
    pref_name = PREFECTURES[pref_code]
    print(f"  {pref_name} を取得中...", end=" ", flush=True)

    zip_data = download_zip(pref_code)
    if not zip_data:
        print("スキップ（取得失敗）")
        return 0

    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            gml_files = [n for n in zf.namelist() if n.endswith(".xml") or n.endswith(".gml")]
            if not gml_files:
                print("スキップ（GMLなし）")
                return 0

            count = 0
            for gml_name in gml_files:
                raw = zf.read(gml_name)
                for enc in ("utf-8", "shift_jis", "utf-8-sig"):
                    try:
                        gml_text = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    continue

                for s in parse_gml(gml_text, pref_name):
                    db.add(models.Substation(**s))
                    count += 1

            db.commit()
            print(f"{count}件")
            return count

    except zipfile.BadZipFile:
        print("スキップ（ZIPエラー）")
        return 0


def run(pref_codes: list[str] | None = None):
    codes = pref_codes or list(PREFECTURES.keys())
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

        print(f"国土数値情報 P03（変電所）を取得します（{len(codes)}都道府県）")
        total = 0
        for code in codes:
            total += import_prefecture(code, db)

        print(f"\n合計 {total} 件の変電所データを格納しました。")
    finally:
        db.close()


if __name__ == "__main__":
    pref_codes = None
    if "--pref" in sys.argv:
        idx = sys.argv.index("--pref")
        pref_codes = sys.argv[idx + 1].split(",")
    run(pref_codes)
