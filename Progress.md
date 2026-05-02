# JLPT 單字王 — 開發進度記錄

**專案**：JLPT 單字王（jlptking.vividuck.com）  
**GitHub**：BlueAI-JP/JLPT-Word-Bank  
**技術棧**：FastAPI + aiosqlite + Vanilla JS SPA  
**目前版本**：V1.20 · 20260502 · © vividuck.com  

---

## 已完成功能

### 基礎架構
- [x] FastAPI 後端，aiosqlite 資料庫
- [x] Vanilla JS SPA（無框架，單頁 index.html）
- [x] VPS 部署（RackNerd）+ GitHub Actions CI/CD
- [x] Nginx 反向代理 + Let's Encrypt HTTPS
- [x] Excel 單字庫載入（N4、N3 已上線）

### 登入 / 身份驗證
- [x] Google OAuth 2.0 SSO 登入
- [x] 匿名試用模式（每日次數限制：學習 2 次 / 測驗 1 次）
- [x] Cookie-based session（httponly, samesite=strict）
- [x] 帳號封禁機制（封禁者無法登入，跳轉 `?auth_error=banned`）

### 學習功能
- [x] 學習模式（SRS 間隔重複，Again / Hard / Good / Easy）
- [x] 測驗模式（會 / 不會，50 題，自動計分）
- [x] 測驗檢討模式（錯誤單字庫，Easy 可移除記錄）
- [x] 已學會單字列表（點擊查看詳情）
- [x] 學習記錄頁（各等級統計、重置功能）
- [x] 音訊預載 cache（背景預載接下來 3 個單字）

### 全書閱讀模式（VIP 專屬）
- [x] 依單字編號排序全級別單字依序呈現（背面卡）
- [x] 自動朗讀開關（進入時詢問）
- [x] 「再讀」→ 移至佇列末尾；「已讀」→ 從佇列移除
- [x] 進度持久化（DB 儲存，離開自動 debounce 儲存）
- [x] 重置功能（標頭按鈕 / 完成畫面均可）
- [x] 全部讀完顯示完成畫面
- [x] 鍵盤快捷鍵：`→`/`D`=已讀、`←`/`A`=再讀、`Enter`=播音

### 管理者功能
- [x] 管理者帳號清單（新增 / 移除，預設 bluejp.lin@gmail.com 不可刪）
- [x] 使用者列表（登入次數、練習/測驗次數、學會單字數、最高分、最近登入、IP）
- [x] 封禁 / 解封使用者
- [x] 刪除使用者（連帶刪除所有學習記錄，二次確認）
- [x] VIP 指定（管理者帳號自動享有 VIP）
- [x] 新使用者登入時 Email 通知所有管理者（Gmail SMTP）
- [x] 管理者面板「發送測試 Email」按鈕

### 響應式設計
- [x] iPhone（<768px）：原始排版
- [x] iPad（768px+）：4 個模式卡片改為 2×2 Grid，元素隨 dvh 縮放
- [x] Desktop（1200px+）：進一步放大

### UI / UX
- [x] 日式漆器風格設計（金色主題、青海波紋背景、金繼裂紋）
- [x] 飄落櫻花瓣動畫
- [x] Toast 通知（debounce，不重複閃爍）
- [x] 鍵盤快捷鍵（Space / Enter / 1234 / Y / N）
- [x] 版本標示 + 版權聲明（登入頁 & 等級選單頁底部）
- [x] 使用者手冊（手風琴展開，9 個章節，app 內建）

### 安全 / 爬蟲防護
- [x] 所有單字 / 音訊 API 需有效 session
- [x] Rate limit middleware（/api/audio/ 與 /api/words/ 每 IP 每分鐘上限 300 次）
- [x] robots.txt（Disallow: /api/, /auth/）
- [x] .gitignore：WordBank/*.xlsx、**/*.mp3 不上傳 GitHub

---

## Commit 歷史（功能相關）

| Commit | 說明 |
|--------|------|
| `a02f73e` | feat: 新增使用者手冊（手風琴展開，9 章節）|
| `93edfb8` | fix: 修正新使用者 Email 通知無效（BackgroundTasks + logging）|
| `f234c0d` | feat: 管理者可刪除使用者帳號 |
| `8698a7c` | fix: 全書閱讀單字卡頂部間距（編號與內容重疊）|
| `e22d01b` | fix: 管理者帳號自動享有 VIP 權限 |
| `409083f` | feat(V1.20): VIP全書閱讀、Email通知、爬蟲防護 |
| `f1a7f19` | feat: 響應式排版 + V1.10 版本標示 |
| `5179f0a` | feat: 管理者介面（使用者管理、封禁）|
| `590dbe6` | feat: 音訊預載 cache |
| `bb4b9ca` | fix: 等級按鈕無反應（立即切換畫面）|
| `afb1eb5` | fix: 無限遞迴 & Toast 疊加問題 |
| `51effd7` | fix: 網路間歇錯誤 & httpx 部署依賴 |
| `fdd610c` | feat: Google SSO + 匿名試用 |

---

## VPS 環境設定

**服務**：systemd `jlpt.service`  
**設定檔**：`/etc/systemd/system/jlpt.service`  
**環境變數**：直接寫在 service 檔的 `Environment=` 行  

| 環境變數 | 說明 |
|----------|------|
| `PRODUCTION=true` | 啟用 secure cookie |
| `PORT=8000` | 服務埠 |
| `GOOGLE_CLIENT_ID` | Google OAuth Client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth Client Secret |
| `GOOGLE_REDIRECT_URI` | OAuth 回調 URL |
| `GMAIL_APP_PASSWORD` | Gmail 應用程式密碼（16碼，無空格）|

**Gmail App Password 申請**：https://myaccount.google.com/apppasswords  
（需先開啟兩步驟驗證）

---

## 資料庫 Schema（SQLite）

| 資料表 | 說明 |
|--------|------|
| `users` | 使用者帳號（含 is_anonymous, is_vip, login_count 等）|
| `mastered_words` | 已學會單字（user_id, level, word_id）|
| `study_sessions` | 學習記錄 |
| `quiz_sessions` | 測驗記錄（含分數）|
| `quiz_wrong_words` | 錯誤單字庫 |
| `anonymous_usage` | 匿名使用次數（IP + 日期）|
| `admin_emails` | 管理者 Email 清單 |
| `banned_users` | 封禁使用者清單 |
| `book_read_progress` | 全書閱讀進度（佇列 JSON）|

---

## 待辦 / 未來規劃

- [ ] N2、N1 單字庫上線
- [ ] 更細緻的 SRS 間隔演算法（目前為簡化版）
- [ ] 單字搜尋功能
- [ ] 社群功能（排行榜）
