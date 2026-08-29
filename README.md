# UniBooks — 後端（UniBooks-BE）

台灣大專院校二手教科書搜尋與媒合平台之後端服務。

- 技術棧：Python 3.14 / Django 6 / Django REST Framework / django-environ
- 架構依據：`docs/backend-ssd.md`（於 UniBooks 工作根目錄；§8.1 組態政策為紅線）

## 專案結構

單一 Django project（modular monolith），依領域切八個 app（backend-ssd §4）：

| App | 職責 |
|---|---|
| `accounts` | 註冊、`.edu.tw` 驗證、JWT 發放與白名單 |
| `catalog` | 書目、Google Books 整合、預建書單、sitemap |
| `listings` | 刊登 CRUD、書況標籤、課程關聯、圖片上傳 |
| `search` | 搜尋端點、篩選、排序 |
| `subscriptions` | 到貨通知訂閱與扇出 |
| `messaging` | 站內私訊 |
| `moderation` | 檢舉與下架處理 |
| `core` | 共用抽象介面、健康檢查、組態載入（`core/conf.py`） |

## 環境建置

需求：Python 3.14（系統最新版）。

```bash
# 1. 建立虛擬環境
python3.14 -m venv .venv
source .venv/bin/activate

# 2. 安裝依賴（版本固定）
pip install -r requirements-dev.txt   # 開發（含測試工具）

# 或僅執行期依賴：pip install -r requirements.txt

# 3. 準備環境變數
cp .env.example .env
# 編輯 .env 填入實際值；.env 不得進入版控
```

### 組態政策（backend-ssd §8.1，硬性規範）

- **必填、無預設**：`SECRET_KEY`、`JWT_SIGNING_KEY`、`EMAIL_API_KEY`、`S3_BUCKET`、
  `GOOGLE_BOOKS_API_KEY` —— 缺失或空值即拒絕啟動
  （`ImproperlyConfigured`）；禁止預設值、寫死值、自動生成。
- **開發回退（僅 `DEBUG=True`）**：未設 `POSTGRES_DATABASE` 等資料庫變數 → SQLite；未設 `REDIS_URL` →
  LocMemCache。兩者均輸出警告。
- **防呆閘**：任一回退被觸發且 `DEBUG=False` → 啟動失敗。正式環境不得運行於
  SQLite 或單機快取之上。

## 啟動（本地開發）

```bash
source .venv/bin/activate
DEBUG=True python manage.py runserver
```

未設資料庫變數／`REDIS_URL` 時將回退 SQLite／LocMemCache 並輸出警告（僅限開發）。

## 測試與程式碼品質

```bash
source .venv/bin/activate

# 單元測試＋覆蓋率（門檻 80%，低於即失敗；含 §8.1 組態冒煙測試）
pytest

# Lint（設定見 pyproject.toml）
ruff check .
```

- 測試環境變數由根目錄 `conftest.py` 注入（一律虛構假值），並設
  `DJANGO_READ_DOT_ENV_FILE=0` 隔離本機 `.env`。
- 組態冒煙測試位於 `tests/test_config.py`：驗證「缺必填→拒啟動」、
  「DEBUG=True 回退＋警告」、「DEBUG=False 回退＝啟動失敗」三類情境。

## 開發流程

- 單人開發仍一律走 PR：feature branch → PR（留存審查結論）→ 合併 `main`。
- 觸及認證／組態／檔案上傳／個資／對外整合／新增依賴 → 必經 security‑manager 核可。
- 依賴新增或升級後，同步更新 `requirements*.txt`（版本固定）。

---

## 📁 更細部的專案結構說明

### 1️⃣ Settings 分層

```
unibooks/
├─ settings/
│  ├─ __init__.py          # from .base import *
│  ├─ base.py               # 通用設定（INSTALLED_APPS、MIDDLEWARE 等）
│  ├─ dev.py                # DEBUG=True、SQLite、LocMemCache
│  ├─ prod.py               # DEBUG=False、PostgreSQL、Redis
│  └─ secrets.py            # 只做 env.require()，確保必填變數在啟動時檢查
```

- `base.py` 包含所有 **非環境特定** 的設定。
- `dev.py` 與 `prod.py` 僅在 `DJANGO_SETTINGS_MODULE=unibooks.settings.dev|prod` 時被載入。
- `secrets.py` 放置 `conf.require()` 呼叫，所有機密（`SECRET_KEY`、`JWT_SIGNING_KEY`、`EMAIL_API_KEY`…）僅在此處驗證，避免在程式碼其他位置硬編碼。

### 2️⃣ API 設計（ViewSet + Router）

各 domain 皆改以 **DRF ViewSet** + **DefaultRouter** 提供 CRUD 與自訂動作，減少重複的 `APIView` 實作。

```python
# messaging/urls.py
router = DefaultRouter()
router.register(r'conversations', ConversationViewSet, basename='conversation')
router.register(r'conversations/(?P<conversation_pk>\d+)/messages', MessageViewSet, basename='message')
urlpatterns = router.urls
```

- `ConversationViewSet` 只負責取得、建立會話，權限檢查寫在 `get_queryset()` 中。
- `MessageViewSet` 只處理訊息的列表與建立，同樣在 `get_queryset()` 中做權限驗證。
- 這樣的結構讓 **單元測試** 能直接對 `ViewSet` 內的 `list`、`create` 方法做 mock，測試更聚焦。

### 3️⃣ 共用服務與快取

```
core/
├─ services/
│  ├─ jwt_utils.py           # JWT 簽發、驗證
│  ├─ email_utils.py         # 寄送驗證信、模板渲染
│  ├─ pagination.py         # 統一 PageNumberPagination
│  └─ google_books.py        # Google Books API 包裝、快取邏輯
```

- `google_books.py` 從 `catalog/services.py` 抽出，所有檔案皆使用同一個快取鍵命名規則，避免重複快取。
- `pagination.py` 只定義 `DefaultPageNumberPagination`，所有 viewset 只需 `pagination_class = DefaultPageNumberPagination`。

### 4️⃣ 測試策略

- **pytest** + **pytest‑django**，測試資料夾依 app 分層：`tests/accounts/…`、`tests/catalog/…`。
- 主要測試項目：
  - 環境變數驗證（`tests/test_config.py`）
  - JWT 旋轉、撤銷流程
  - Google Books fallback（使用 `requests-mock`）
  - 權限保護（未授權存取 401/403）
- CI 中會跑 **coverage**，門檻 80%（包含組態冒煙測試）。

### 5️⃣ 部署說明

| 平台 | 步驟 |
|------|------|
| **Docker** | `docker build -t unibooks-be .` → `docker run -p 8000:8000 -e DJANGO_SETTINGS_MODULE=unibooks.settings.prod unibooks-be` |
| **Heroku / Render** | 設定所有必填環境變數 → 自動載入 `settings/prod.py` |
| **Cloudflare Workers (SSR)** | 後端仍以傳統 API 部署（Fly.io、Railway…），前端 SSR 透過 Workers 代理，兩者皆遵循相同的安全組態。

---

以上說明提供了 **設定分層、API 結構、共用服務、測試與部署** 的完整藍圖，方便新進開發者快速上手與未來擴充。

### 6️⃣ Cloudflare R2 CORS 設定 (圖片上傳)

由於平台採用 Presigned URL 直接從前端（瀏覽器）上傳圖片至 Cloudflare R2（不經過後端），因此必須在 R2 Bucket 設定 CORS 規則。否則上傳時會遭遇 `403 Forbidden` 與 CORS 錯誤。

請至 Cloudflare Dashboard -> R2 -> Bucket -> **Settings** -> **CORS Policy** 中新增以下設定：

```json
[
  {
    "AllowedOrigins": [
      "http://localhost:4200",
      "http://127.0.0.1:4200",
      "https://你的正式網域.com"
    ],
    "AllowedMethods": [
      "PUT",
      "GET",
      "HEAD"
    ],
    "AllowedHeaders": [
      "*"
    ],
    "MaxAgeSeconds": 3600
  }
]
```

**⚠️ 注意事項：**
- **`AllowedMethods`** 必須包含 `PUT`，因為系統使用的是 `generate_presigned_url('put_object')`，R2 不支援 S3 的 `POST` 政策上傳。
- **`AllowedHeaders`** 強烈建議設為 `["*"]`，以避免瀏覽器預設附加的 Header 導致簽名不匹配 (SignatureMismatch) 而上傳失敗。
- **`AllowedOrigins`** 在正式環境務必替換為實際的正式網域。