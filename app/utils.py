"""
共通ユーティリティ関数
"""
import math


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """2点間の距離をメートルで返す（Haversine公式）"""
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))
