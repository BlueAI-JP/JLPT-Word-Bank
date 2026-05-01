# JLPT 單字王

JLPT 日文單字卡學習系統，支援 N1～N4 各級別，具備 ANKI SRS 學習邏輯。

## 功能

- **學習模式**：每次隨機 20 個單字，使用 Again / Hard / Good / Easy 間隔重複
- **測驗模式**：隨機 50 個單字 Yes / No 測驗，計算成績
- **已學會單字管理**：Easy 標記後永久排除，可查看完整列表
- **多使用者記錄**：各使用者獨立的學習進度與歷史
- **鍵盤快捷鍵**：Space 翻面、Enter 播音、1-4 SRS、Y/N 測驗
- **響應式設計**：支援 Windows / Mac / iPhone / iPad
- **VPS 部署支援**：音檔與單字資料透過 API 傳輸，防盜取

## 環境需求

- Python 3.11+
- 依賴套件（見 `pyproject.toml`）

## 安裝與啟動

```bash
# 安裝依賴
pip install fastapi[standard] openpyxl aiosqlite uvicorn

# 啟動伺服器
python main.py
```

開啟瀏覽器前往 `http://localhost:8000`

## 資料檔案放置

音檔與單字資料需手動放入（不含於 git）：

```
WordBank/
  JLPTWordBank_N4_ALL.xlsx
  JLPTWordBank_N3_ALL.xlsx
  JLPTWordBank_N2_ALL.xlsx   ← N2 開放時放入
  JLPTWordBank_N1_ALL.xlsx   ← N1 開放時放入

JLPT_N4/   0001.mp3 ~ XXXX.mp3
JLPT_N3/   0001.mp3 ~ XXXX.mp3
JLPT_N2/   ← N2 開放時放入
JLPT_N1/   ← N1 開放時放入
```

## VPS 部署

建議使用 `nginx` 反向代理 + `systemd` 管理程序，或使用 Docker。

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
}
```

## 專案結構

```
main.py          FastAPI 主程式（所有 API 路由）
database.py      SQLite 使用者進度管理
data_loader.py   Excel 單字資料載入
static/
  index.html     前端單頁應用（SPA）
WordBank/        單字 Excel 檔案
data/            使用者進度資料庫（自動建立）
```
