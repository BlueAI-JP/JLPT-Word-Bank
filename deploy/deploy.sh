#!/bin/bash
# =============================================================================
# JLPT 單字王 — 部署腳本（由 GitHub Actions SSH 觸發）
# =============================================================================
set -euo pipefail

APP_DIR="$HOME/jlpt-word-bank"

echo "[deploy] 拉取最新程式碼..."
cd "$APP_DIR"
git fetch origin
git reset --hard origin/master

echo "[deploy] 更新 Python 依賴..."
"$APP_DIR/.venv/bin/pip" install --quiet \
    "fastapi[standard]" openpyxl aiosqlite uvicorn

echo "[deploy] 重啟服務..."
sudo systemctl restart jlpt

echo "[deploy] 確認服務狀態..."
sleep 2
sudo systemctl is-active jlpt && echo "[deploy] 服務正常運行 ✓" || {
    echo "[deploy] 服務啟動失敗，查看 log:"
    sudo journalctl -u jlpt -n 30 --no-pager
    exit 1
}
