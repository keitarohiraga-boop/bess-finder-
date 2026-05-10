"""
JEPXスポット市場価格データの取得・集計モジュール
"""
import csv
import io
import statistics
import urllib.request
from datetime import datetime

from app.area_mapping import AREA_NAMES, JEPX_COLUMN_MAP

JEPX_CSV_URL = (
    "https://www.jepx.jp/_download.php"
    "?directory=spot_summary&filename=spot_summary_{year}.csv"
)

# ピーク時間帯: 7:00〜22:00 = 時刻コード14〜44
PEAK_CODES = set(range(14, 45))


def fetch_csv(year: int) -> list[dict]:
    url = JEPX_CSV_URL.format(year=year)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    for enc in ("shift_jis", "utf-8-sig", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def _find_column(row: dict, keyword: str) -> str | None:
    for key in row:
        if keyword in key:
            return key
    return None


def compute_metrics(rows: list[dict], area: str) -> dict | None:
    area_col = None
    time_col = None

    if rows:
        area_col = _find_column(rows[0], JEPX_COLUMN_MAP[area])
        time_col = _find_column(rows[0], "時刻")

    if not area_col or not time_col:
        return None

    peak, offpeak, all_prices = [], [], []
    for row in rows:
        try:
            code = int(row[time_col])
            price = float(row[area_col])
            all_prices.append(price)
            (peak if code in PEAK_CODES else offpeak).append(price)
        except (ValueError, KeyError, TypeError):
            continue

    if not all_prices:
        return None

    avg = statistics.mean(all_prices)
    volatility = statistics.stdev(all_prices) if len(all_prices) > 1 else 0.0
    peak_avg = statistics.mean(peak) if peak else avg
    offpeak_avg = statistics.mean(offpeak) if offpeak else avg
    spread = peak_avg - offpeak_avg

    return {
        "avg_price": round(avg, 2),
        "peak_avg": round(peak_avg, 2),
        "offpeak_avg": round(offpeak_avg, 2),
        "spread": round(spread, 2),
        "volatility": round(volatility, 2),
    }


def _normalize(value: float, min_v: float, max_v: float) -> int:
    if max_v == min_v:
        return 50
    return round((value - min_v) / (max_v - min_v) * 100)


def update_jepx_metrics(year: int | None = None) -> dict:
    from app.database import SessionLocal
    from app import models

    if year is None:
        year = datetime.now().year - 1

    print(f"JEPX {year}年データを取得中...")
    rows = fetch_csv(year)
    print(f"{len(rows)} 行取得")

    raw: dict[str, dict] = {}
    for area in AREA_NAMES:
        m = compute_metrics(rows, area)
        if m:
            raw[area] = m

    if not raw:
        raise ValueError("JEPXデータの解析に失敗しました")

    spreads = [m["spread"] for m in raw.values()]
    vols = [m["volatility"] for m in raw.values()]
    min_s, max_s = min(spreads), max(spreads)
    min_v, max_v = min(vols), max(vols)

    db = SessionLocal()
    try:
        for area, m in raw.items():
            jepx_score = round(
                _normalize(m["spread"], min_s, max_s) * 0.6
                + _normalize(m["volatility"], min_v, max_v) * 0.4
            )
            rec = db.query(models.JepxAreaMetrics).filter_by(area=area).first()
            if rec:
                rec.avg_price = m["avg_price"]
                rec.peak_avg = m["peak_avg"]
                rec.offpeak_avg = m["offpeak_avg"]
                rec.spread = m["spread"]
                rec.volatility = m["volatility"]
                rec.jepx_score = jepx_score
                rec.data_year = year
            else:
                db.add(models.JepxAreaMetrics(
                    area=area, data_year=year, jepx_score=jepx_score,
                    **{k: m[k] for k in ("avg_price", "peak_avg", "offpeak_avg", "spread", "volatility")},
                ))
        db.commit()
        print("JEPXメトリクスを更新しました")
        return {a: raw[a] for a in raw}
    finally:
        db.close()
