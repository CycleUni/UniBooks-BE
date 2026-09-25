# ADR 0001：權杖與限流的儲存位置

- 狀態：已採納
- 日期：2026-09-25
- 取代：同編號的前一版（以遷移至 Cloudflare Workers 為前提；該遷移已撤回，保留於 `feat/cloudflare-workers` 分支）

## 背景

後端以 Upstash Redis 作為 `CACHES["default"]`，並在同一個快取中存放性質不同的資料：

| 類別 | 程式位置 | 一致性需求 |
|---|---|---|
| 內容快取（刊登、書目、首頁、地區、搜尋、統計）與版本號 | `listings/`、`catalog/`、`accounts/views/home.py`、`core/cache.py` 等 | 可容忍過期、可遺失 |
| Refresh token 白名單 `jwt:rt:*`、`jwt:user:*` | `accounts/services.py` | 強一致、不可遺失 |
| 一次性連結（驗證信、重設密碼、更換 email） | `accounts/views/auth.py` | 強一致、不可遺失 |
| DRF 限流計數 | `ScopedRateThrottle` | 需原子遞增 |

後三類放在快取中有以下問題：

1. **並發寫入互相覆蓋**：Django cache 介面沒有 `SADD`、`INCR` 以外的原子操作。`jwt:user:{id}` 是先讀後寫的清單，兩台裝置同時登入時會遺失其中一筆，「登出所有裝置」與重設密碼便漏撤該 session。DRF 限流同樣以先讀後寫的歷史清單計數，並發的暴力嘗試會被少算。
2. **淘汰即遺失**：記憶體壓力下 Redis 可淘汰任何 key。遺失白名單會登出使用者；遺失一次性連結會讓剛寄出的信失效。
3. **無法稽核**：資安事件時無法查詢某帳號有哪些有效 session、何時撤銷。

## 決策

| 用途 | 儲存位置 |
|---|---|
| 內容快取、版本號 | Redis（不變） |
| Refresh token 白名單 | Postgres：`accounts.RefreshTokenRecord`，每個 jti 一列 |
| 一次性連結 | Postgres：`accounts.OneTimeToken` |
| 限流計數 | Postgres：`core.ThrottleCounter`，固定視窗，以單一 `INSERT ... ON CONFLICT DO UPDATE ... RETURNING` 原子遞增 |

Access token 仍為 15 分鐘效期的無狀態 JWT，驗證時不查白名單；白名單僅在登入、換發（每位活躍使用者約每 15 分鐘一次）、登出時查詢，資料庫負擔小。`djangorestframework-simplejwt` 內建的 `token_blacklist` 亦採資料庫。

## 抽象層

- `accounts.token_store.TokenStore`：`record`、`lookup`、`mark_rotated`、`forget`、`revoke`、`revoke_all`、`purge`。`accounts/services.py` 的公開函式與錯誤語意（儲存失敗回 503 `TokenStoreUnavailable`，撤銷路徑失敗則降級）不變。
- `accounts.one_time_tokens`：`issue`、`read`、`consume`，以及更換 email 的 `issue_email_change`、`pending_email_change`、`cancel_email_change`。
- `core.throttling.ScopedThrottle`：`ScopedRateThrottle` 的直接替代，scope 與速率設定不變，計數交給 `ThrottleStore`。固定視窗在邊界處最多可放行兩倍額度，對目前的速率可接受。

## 遷移

舊資料皆有期限，以唯讀備援過渡：

| 資料 | 最長存活 | 過渡方式 |
|---|---|---|
| 限流計數 | 1 小時 | 直接切換 |
| 一次性連結 | 24 小時 | 查無時回讀快取中的舊連結，使用後一併刪除 |
| Refresh token 白名單 | 14 天 | 查無時回讀快取中的舊權杖並搬入 Postgres；撤銷同時作用於兩邊 |

備援由 `AUTH_LEGACY_CACHE_FALLBACK` 控制（預設開啟），期間只讀取與刪除快取中的舊資料，不寫入。部署 14 天後關閉，再刪除 `LegacyCacheTokenStore`、`MigratingTokenStore` 與 `one_time_tokens` 的舊資料讀取。

過期資料由 `cron/cleanup/` 每日清理：權杖列到期即刪，輪替紀錄中的權杖內容於寬限期後清空（避免資料庫長期保存可用的 bearer token），一次性連結到期即刪，限流計數保留一天。

## 後果

- 登入、換發權杖，以及每個受限流的請求各多一次資料庫寫入。
- Redis 只剩內容快取；Redis 故障或清空時只影響效能，不影響登入狀態與限流正確性。
- 測試以 `conftest.py` 固定 `core.throttling._now`，避免跨分鐘或整點時限流計數歸零。

## 重新評估條件

- 限流寫入量在資料庫監控中明顯可見：改為 Redis `INCR` 的 `ThrottleStore`（原子，且不佔資料庫）。
- 受攻擊時資料庫負載因限流上升：先加 WAF／反向代理的限流規則。
