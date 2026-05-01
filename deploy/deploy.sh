#!/bin/bash
# =============================================================================
# JLPT 單字王 — 部署腳本（由 GitHub Actions 或手動呼叫）
# 在 VPS 上以 jlpt 使用者或 root 執行
# =============================================================================
set -euo pipefail

APP_DIR="/var/www/jlpt-word-bank"
APP_USER="jlpt"

echo "[deploy] 拉取最新程式碼..."
cd "$APP_DIR"
git fetch origin
git reset --hard origin/master

echo "[deploy] 更新 Python 依賴..."
"$APP_DIR/.venv/bin/pip" install --quiet \
    fastapi[standard] openpyxl aiosqlite uvicorn

echo "[deploy] 重啟服務..."
systemctl restart jlpt

echo "[deploy] 確認服務狀態..."
sleep 2
systemctl is-active jlpt && echo "[deploy] 服務正常運行 ✓" || {
    echo "[deploy] 服務啟動失敗，查看 log:"
    journalctl -u jlpt -n 30 --no-pager
    exit 1
}
