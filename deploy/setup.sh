#!/bin/bash
# =============================================================================
# JLPT 單字王 — VPS 一次性初始化腳本
# 在全新的 Ubuntu 22.04 / 24.04 VPS 上以 root 執行一次
# 用法: bash setup.sh <your-domain.com>
# =============================================================================
set -euo pipefail

DOMAIN="${1:?請傳入網域名稱，例如: bash setup.sh jlpt.example.com}"
APP_DIR="/var/www/jlpt-word-bank"
APP_USER="jlpt"
REPO="https://github.com/BlueAI-JP/JLPT-Word-Bank.git"

echo ">>> [1/8] 更新系統套件"
apt-get update -y && apt-get upgrade -y

echo ">>> [2/8] 安裝必要套件"
apt-get install -y python3.11 python3.11-venv python3-pip \
    nginx certbot python3-certbot-nginx git curl ufw

echo ">>> [3/8] 建立應用程式使用者"
id -u "$APP_USER" &>/dev/null || useradd -m -s /bin/bash "$APP_USER"

echo ">>> [4/8] Clone 程式碼"
mkdir -p "$APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
    echo "    已有 git repo，略過 clone"
else
    git clone "$REPO" "$APP_DIR"
fi
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

echo ">>> [5/8] 建立 Python 虛擬環境與安裝依賴"
sudo -u "$APP_USER" python3.11 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install \
    fastapi[standard] openpyxl aiosqlite uvicorn

echo ">>> [6/8] 安裝 systemd 服務"
cp "$APP_DIR/deploy/jlpt.service" /etc/systemd/system/jlpt.service
sed -i "s|APP_DIR_PLACEHOLDER|$APP_DIR|g" /etc/systemd/system/jlpt.service
systemctl daemon-reload
systemctl enable jlpt
systemctl start jlpt
echo "    服務狀態: $(systemctl is-active jlpt)"

echo ">>> [7/8] 設定 Nginx"
cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/jlpt
sed -i "s|DOMAIN_PLACEHOLDER|$DOMAIN|g" /etc/nginx/sites-available/jlpt
ln -sf /etc/nginx/sites-available/jlpt /etc/nginx/sites-enabled/jlpt
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo ">>> [8/8] 申請 SSL 憑證 (Let's Encrypt)"
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
    --email "admin@$DOMAIN" --redirect
systemctl reload nginx

echo ">>> 設定防火牆"
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo ""
echo "======================================================"
echo " 初始化完成！"
echo " 網址: https://$DOMAIN"
echo ""
echo " 下一步：上傳音檔資料"
echo "   rsync -avz --progress ./JLPT_N4/ $APP_USER@$DOMAIN:$APP_DIR/JLPT_N4/"
echo "   rsync -avz --progress ./JLPT_N3/ $APP_USER@$DOMAIN:$APP_DIR/JLPT_N3/"
echo "======================================================"
