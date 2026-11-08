#!/usr/bin/env bash
# Установка и обновление тендерного калькулятора на Ubuntu 22.04 / 24.04.
#
#   curl -fsSL https://raw.githubusercontent.com/4rpjsnh2mc-boop/omniroute/refs/heads/claude/ecstatic-lovelace-5myzzh/install.sh | sudo bash
#
# Повторный запуск той же команды обновляет программу. Данные (тендеры, документы,
# пользователи) хранятся отдельно в /var/lib/tender и при обновлении не трогаются.
set -euo pipefail

TARBALL="https://codeload.github.com/4rpjsnh2mc-boop/omniroute/tar.gz/refs/heads/claude/ecstatic-lovelace-5myzzh"
APP_DIR="/opt/tender"
DATA_DIR="/var/lib/tender"
ENV_FILE="/etc/tender.env"
SVC="tender"
PORT_PUBLIC="8090"

say() { printf '\n\033[1;33m%s\033[0m\n' "$*"; }
ok() { printf '\033[1;32m%s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mОшибка: %s\033[0m\n' "$*" >&2; exit 1; }
ask() { local reply=""; read -r -p "$1" reply < /dev/tty || true; printf '%s' "$reply"; }
ask_secret() { local reply=""; read -r -s -p "$1" reply < /dev/tty || true; printf '\n' > /dev/tty; printf '%s' "$reply"; }

[ "$(id -u)" -eq 0 ] || die "запустите от имени root (через sudo)."
command -v apt-get >/dev/null || die "нужен Ubuntu или Debian."

say "1/6. Проверяю Python"
if ! command -v python3 >/dev/null; then apt-get update -y && apt-get install -y python3; fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "нужен Python 3.10 или новее."
command -v curl >/dev/null || apt-get install -y curl
ok "Python $(python3 -c 'import platform; print(platform.python_version())')"

say "2/6. Скачиваю программу"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT
curl -fsSL "$TARBALL" -o "$TMP/app.tgz" || die "не удалось скачать программу с GitHub."
tar -xzf "$TMP/app.tgz" -C "$TMP"
SRC="$(find "$TMP" -mindepth 2 -maxdepth 2 -type d -name app | head -n 1)"
[ -n "$SRC" ] && [ -f "$SRC/server.py" ] || die "в архиве нет программы."
mkdir -p "$APP_DIR"
rm -rf -- "$APP_DIR/app.new"
cp -r "$SRC" "$APP_DIR/app.new"
if [ -d "$APP_DIR/app" ]; then rm -rf -- "$APP_DIR/app.old"; mv "$APP_DIR/app" "$APP_DIR/app.old"; fi
mv "$APP_DIR/app.new" "$APP_DIR/app"
rm -rf -- "$APP_DIR/app.old"
ok "Программа в $APP_DIR/app"

say "3/6. Пользователь и папка для данных"
id -u tender >/dev/null 2>&1 || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin tender
mkdir -p "$DATA_DIR/files" "$DATA_DIR/backups"
chown -R tender:tender "$DATA_DIR"
chmod 750 "$DATA_DIR"
ok "Данные хранятся в $DATA_DIR"

FIRST_RUN=0
DOMAIN=""
if [ ! -f "$ENV_FILE" ]; then
  FIRST_RUN=1
  say "4/6. Как открывать калькулятор"
  echo "Если у вас есть поддомен для калькулятора (например, tender.vash-sait.ru),"
  echo "сначала в настройках DNS направьте его на IP этого сервера, затем впишите его ниже."
  echo "Если поддомена нет, просто нажмите Enter: калькулятор откроется по адресу http://IP-сервера:${PORT_PUBLIC}"
  DOMAIN="$(ask 'Поддомен (или Enter): ' | tr -d '[:space:]' | tr 'A-Z' 'a-z')"
  if [ -n "$DOMAIN" ] && ! printf '%s' "$DOMAIN" | grep -Eq '^[a-z0-9.-]+\.[a-z]{2,}$'; then
    die "поддомен «$DOMAIN» записан неверно."
  fi
  if [ -n "$DOMAIN" ] && ! command -v nginx >/dev/null; then
    echo "На сервере нет nginx. Ставлю его (существующий сайт на Apache это не затронет, если nginx не займёт порт 80)."
    if ss -ltn 2>/dev/null | grep -Eq ':(80|443)\s'; then
      echo "Порт 80 уже занят другим веб-сервером. Калькулятор будет работать на порту ${PORT_PUBLIC} без поддомена."
      DOMAIN=""
    else
      apt-get update -y && apt-get install -y nginx
    fi
  fi
  if [ -n "$DOMAIN" ]; then
    printf 'TENDER_DATA=%s\nTENDER_HOST=127.0.0.1\nTENDER_PORT=8090\nTENDER_DOMAIN=%s\n' "$DATA_DIR" "$DOMAIN" > "$ENV_FILE"
  else
    printf 'TENDER_DATA=%s\nTENDER_HOST=0.0.0.0\nTENDER_PORT=%s\nTENDER_DOMAIN=\n' "$DATA_DIR" "$PORT_PUBLIC" > "$ENV_FILE"
  fi
  chmod 644 "$ENV_FILE"
else
  say "4/6. Настройки уже есть ($ENV_FILE), оставляю как есть"
  DOMAIN="$(grep -E '^TENDER_DOMAIN=' "$ENV_FILE" | cut -d= -f2- || true)"
fi

say "5/6. Запускаю службу"
cat > /etc/systemd/system/${SVC}.service <<UNIT
[Unit]
Description=Tender calculator
After=network-online.target
Wants=network-online.target

[Service]
User=tender
Group=tender
EnvironmentFile=${ENV_FILE}
WorkingDirectory=${APP_DIR}/app
ExecStart=/usr/bin/python3 ${APP_DIR}/app/server.py serve
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true
ReadWritePaths=${DATA_DIR}

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable "$SVC" >/dev/null 2>&1
systemctl restart "$SVC"
sleep 2
systemctl is-active --quiet "$SVC" || { journalctl -u "$SVC" -n 30 --no-pager; die "служба не запустилась."; }
ok "Служба $SVC работает"

if [ -n "$DOMAIN" ]; then
  if [ ! -f /etc/nginx/conf.d/tender.conf ]; then
    cat > /etc/nginx/conf.d/tender.conf <<NGINX
server {
    listen 80;
    server_name ${DOMAIN};
    client_max_body_size 64m;
    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
    }
}
NGINX
    if nginx -t >/dev/null 2>&1; then
      systemctl reload nginx
      ok "nginx настроен для $DOMAIN"
      if ! command -v certbot >/dev/null; then apt-get install -y certbot python3-certbot-nginx >/dev/null || true; fi
      EMAIL="$(ask 'E-mail для сертификата HTTPS (или Enter): ' | tr -d '[:space:]')"
      if [ -n "$EMAIL" ]; then CB_MAIL=(-m "$EMAIL"); else CB_MAIL=(--register-unsafely-without-email); fi
      if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --redirect "${CB_MAIL[@]}"; then
        ok "HTTPS включён"
      else
        echo "HTTPS не включился: проверьте, что поддомен $DOMAIN указывает на IP этого сервера, и запустите установку ещё раз."
      fi
    else
      rm -f /etc/nginx/conf.d/tender.conf
      echo "nginx не принял настройку, калькулятор доступен только на порту 8090 с самого сервера."
    fi
  fi
else
  if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow "${PORT_PUBLIC}/tcp" >/dev/null && ok "Порт ${PORT_PUBLIC} открыт в ufw"
  fi
fi

# Ежедневная резервная копия базы (храним 14 дней)
cat > /etc/cron.daily/tender-backup <<'CRON'
#!/bin/sh
cd /var/lib/tender || exit 0
python3 - <<'PY'
import sqlite3, time
src = sqlite3.connect("/var/lib/tender/tender.db")
dst = sqlite3.connect("/var/lib/tender/backups/tender-%s.db" % time.strftime("%Y-%m-%d"))
src.backup(dst)
dst.close(); src.close()
PY
find /var/lib/tender/backups -name 'tender-*.db' -mtime +14 -delete
chown -R tender:tender /var/lib/tender/backups
CRON
chmod 755 /etc/cron.daily/tender-backup

say "6/6. Администратор"
USERS="$(sudo -u tender env TENDER_DATA="$DATA_DIR" python3 "$APP_DIR/app/server.py" count-users 2>/dev/null || echo 0)"
if [ "$USERS" = "0" ]; then
  LOGIN="$(ask 'Логин администратора [admin]: ' | tr -d '[:space:]' | tr 'A-Z' 'a-z')"
  LOGIN="${LOGIN:-admin}"
  while true; do
    P1="$(ask_secret 'Пароль (не меньше 8 символов, не отображается): ')"
    P2="$(ask_secret 'Повторите пароль: ')"
    [ "$P1" = "$P2" ] && [ "${#P1}" -ge 8 ] && break
    echo "Пароли не совпадают или короче 8 символов. Ещё раз."
  done
  sudo -u tender env TENDER_DATA="$DATA_DIR" TENDER_ADMIN_PASSWORD="$P1" python3 "$APP_DIR/app/server.py" create-admin "$LOGIN"
  unset P1 P2
else
  ok "Пользователи уже есть: $USERS"
fi

IP="$(curl -fsS --max-time 5 https://ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')"
echo
echo "=============================================================="
if [ -n "$DOMAIN" ]; then
  ok "Готово. Откройте: https://${DOMAIN}  (если HTTPS не включился: http://${DOMAIN})"
else
  ok "Готово. Откройте: http://${IP}:${PORT_PUBLIC}"
  echo "Если страница не открывается, разрешите входящий порт ${PORT_PUBLIC} в панели Рег.облака (вкладка «Сеть»)."
fi
echo "Обновить программу: запустите эту же команду ещё раз."
echo "Данные и документы: ${DATA_DIR}  (копии базы: ${DATA_DIR}/backups)"
echo "=============================================================="
