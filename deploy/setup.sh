#!/bin/bash
# =============================================================================
# JLPT 單字王 — VPS 一次性初始化腳本
# Ubuntu 22.04 / 24.04，以具有 sudo 權限的使用者執行
# 用法: sudo bash setup.sh <網域名稱>
# 範例: sudo bash setup.sh vividuck.com
# =============================================================================
set -euo pipefail

DOMAIN="${1:?請傳入網域名稱，例如: sudo bash setup.sh vividuck.com}"
APP_USER="${SUDO_USER:-blue}"          # 以 sudo 執行時自動抓登入使用者名稱
APP_DIR="/home/$APP_USER/jlpt-word-bank"
REPO="https://github.com/BlueAI-JP/JLPT-Word-Bank.git"

echo ">>> 設定資訊"
echo "    使用者: $APP_USER"
echo "    目錄:   $APP_DIR"
echo "    網域:   $DOMAIN"
echo ""

echo ">>> [1/8] 更新系統套件"
apt-get update -y && apt-get upgrade -y

echo ">>> [2/8] 安裝必要套件"
apt-get install -y python3-venv python3-pip \
    nginx certbot python3-certbot-nginx git curl ufw

echo ">>> [3/8] Clone 程式碼"
if [ -d "$APP_DIR/.git" ]; then
    echo "    已有 git repo，略過 clone"
else
    sudo -u "$APP_USER" git clone "$REPO" "$APP_DIR"
fi

echo ">>> [4/8] 建立 Python 虛擬環境與安裝依賴"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip --quiet
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install \
    "fastapi[standard]" openpyxl aiosqlite uvicorn --quiet
echo "    依賴安裝完成"

echo ">>> [5/8] 建立資料目錄"
sudo -u "$APP_USER" mkdir -p "$APP_DIR/data"

echo ">>> [6/8] 安裝 systemd 服務"
cp "$APP_DIR/deploy/jlpt.service" /etc/systemd/system/jlpt.service
sed -i "s|APP_DIR_PLACEHOLDER|$APP_DIR|g"   /etc/systemd/system/jlpt.service
sed -i "s|APP_USER_PLACEHOLDER|$APP_USER|g" /etc/systemd/system/jlpt.service
systemctl daemon-reload
systemctl enable jlpt
systemctl start jlpt
sleep 2
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
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

# 允許 APP_USER 免密碼重啟服務（給 CI/CD 用）
echo "$APP_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart jlpt, /bin/systemctl status jlpt" \
    > /etc/sudoers.d/jlpt-deploy
chmod 440 /etc/sudoers.d/jlpt-deploy
echo "    已設定免密碼 sudo 重啟服務"

echo ""
echo "======================================================"
echo " 初始化完成！"
echo " 網址: https://$DOMAIN"
echo ""
echo " 下一步：用 WinSCP 上傳音檔"
echo "   本機 JLPT_N4/ → 遠端 $APP_DIR/JLPT_N4/"
echo "   本機 JLPT_N3/ → 遠端 $APP_DIR/JLPT_N3/"
echo "======================================================"
