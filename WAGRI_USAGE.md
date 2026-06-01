# WAGRI APIリクエスト使用量ログ

試用版月間上限: **100リクエスト/月**（毎月1日JST 00:00リセット）

## 確認方法（常に最新）

```
GET https://bess-finder-production.up.railway.app/api/v1/wagri/usage
```

→ `monthly_requests`（今月消費数）と `remaining`（残数）が返る。
→ セッション開始時に必ずここを確認してからスキャンを実施する。

---

## 手動記録（セッション終了時に更新）

| 日付 | 消費リクエスト数 | 累計（月間） | 主な作業内容 |
|---|---|---|---|
| 2026-06-02 | 約55件 | 約55件 | WAGRIテスト・class3探索・変電所確認 |

---

## 注意事項

- 1リクエスト = `_search_farmland` 1呼び出し = WAGRI SearchByDistance 1回
- `wagri/check`, `spot-check`, `scan/prefecture` いずれも1呼び出し = 1リクエスト
- `scan/prefecture?max_requests=N` → N件消費
- スキャン前に残量確認必須。残30件以下は慎重に運用する

## 変更履歴

- 2026-06-02: WAGRIリクエストログ機能を実装（DB記録 + `/api/v1/wagri/usage` エンドポイント）
- 2026-06-02: `$top` 非対応確認 → Python側切り詰めに変更
