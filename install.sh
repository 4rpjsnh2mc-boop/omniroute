#!/usr/bin/env bash
# Установка и обновление тендерного калькулятора на Ubuntu 22.04 / 24.04.
#
#   curl -fsSL https://raw.githubusercontent.com/4rpjsnh2mc-boop/omniroute/refs/heads/claude/ecstatic-lovelace-5myzzh/install.sh | sudo bash
#
# Повторный запуск той же команды обновляет программу. Данные (тендеры, документы,
# прайсы) хранятся отдельно в /var/lib/tender и при обновлении не трогаются.
# После установки:  tender-link          — показать ссылку-приглашение
#                   tender-link new      — выпустить новую (старая перестанет работать)
#                   tender-link users    — список рабочих мест
#                   tender-link user N   — новая личная ссылка для рабочего места N
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
set_env() { if grep -q "^$1=" "$ENV_FILE"; then sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"; else printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"; fi; }

[ "$(id -u)" -eq 0 ] || die "запустите от имени root (через sudo)."
command -v apt-get >/dev/null || die "нужен Ubuntu или Debian."

say "1/6. Проверяю Python"
if ! command -v python3 >/dev/null; then apt-get update -y && apt-get install -y python3; fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "нужен Python 3.10 или новее."
command -v curl >/dev/null || apt-get install -y curl
ok "Python $(python3 -c 'import platform; print(platform.python_version())')"
# распознавание прайсов: скриншоты и фото (tesseract), PDF (poppler), подготовка картинок (imagemagick)
if ! command -v tesseract >/dev/null || ! command -v pdftotext >/dev/null || ! command -v convert >/dev/null; then
  echo "Ставлю программы для чтения прайсов со скриншотов и из PDF..."
  if (apt-get update -y >/dev/null && apt-get install -y tesseract-ocr tesseract-ocr-rus poppler-utils imagemagick >/dev/null); then
    ok "Распознавание прайсов готово"
  else
    echo "Не получилось поставить распознавание текста. Excel, Word и текст работают и без него."
  fi
fi

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
else
  say "4/6. Настройки уже есть ($ENV_FILE), оставляю как есть"
  DOMAIN="$(grep -E '^TENDER_DOMAIN=' "$ENV_FILE" | cut -d= -f2- || true)"
fi
grep -q '^TENDER_INVITE=.\+' "$ENV_FILE" || set_env TENDER_INVITE "$(python3 -c 'import secrets; print(secrets.token_urlsafe(9))')"
if ! grep -q '^TENDER_TG_TOKEN=' "$ENV_FILE"; then
  echo
  echo "Напоминания в Telegram (необязательно, бесплатно):"
  echo "  1) в Telegram откройте @BotFather, отправьте /newbot и придумайте имя бота;"
  echo "  2) BotFather пришлёт токен вида 123456789:AA... Вставьте его ниже."
  echo "Чтобы пропустить, нажмите Enter (подключить можно позже: запустите установку ещё раз)."
  TG="$(ask 'Токен бота: ' | tr -d '[:space:]')"
  if [ -n "$TG" ] && ! printf '%s' "$TG" | grep -Eq '^[0-9]{5,15}:[A-Za-z0-9_-]{20,}$'; then
    echo "Токен не похож на настоящий, пропускаю. Запустите установку ещё раз, чтобы попробовать снова."
    TG=""
  fi
  [ -n "$TG" ] && set_env TENDER_TG_TOKEN "$TG"
fi
install_browser() {
  say "Ставлю встроенный браузер (несколько минут)"
  if apt-get install -y python3-venv >/dev/null \
     && python3 -m venv "$APP_DIR/venv" \
     && "$APP_DIR/venv/bin/pip" install -q --upgrade pip playwright \
     && PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/browsers" "$APP_DIR/venv/bin/playwright" install --with-deps chromium; then
    chmod -R a+rX "$APP_DIR/venv" "$APP_DIR/browsers"
    set_env TENDER_RENDER "$APP_DIR/venv/bin/python"
    set_env PLAYWRIGHT_BROWSERS_PATH "$APP_DIR/browsers"
    ok "Браузер установлен: Bidzaar и другие сайты на JavaScript открываются"
  else
    echo "Браузер поставить не удалось. Калькулятор работает и без него, кроме сайтов на JavaScript (Bidzaar)."
    echo "При следующем запуске установки я предложу ещё раз."
  fi
}
if ! grep -q '^TENDER_RENDER=' "$ENV_FILE"; then
  echo
  echo "Некоторые площадки (например, Bidzaar) показывают закупку только в браузере."
  echo "Чтобы калькулятор открывал их сам, нужен встроенный браузер Chromium (около 500 МБ на диске)."
  ANS="$(ask 'Поставить встроенный браузер? [Д/н]: ' | tr -d '[:space:]')"
  case "$ANS" in
    н*|Н*|n*|N*) set_env TENDER_RENDER "" ;;
    *) install_browser ;;
  esac
elif [ -x "$APP_DIR/venv/bin/python" ]; then
  "$APP_DIR/venv/bin/pip" install -q --upgrade playwright >/dev/null 2>&1 \
    && PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/browsers" "$APP_DIR/venv/bin/playwright" install chromium >/dev/null 2>&1 \
    && chmod -R a+rX "$APP_DIR/venv" "$APP_DIR/browsers" || true
fi
chown root:tender "$ENV_FILE"
chmod 640 "$ENV_FILE"

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

HTTPS_OK=0
[ -n "$DOMAIN" ] && [ -d "/etc/letsencrypt/live/$DOMAIN" ] && HTTPS_OK=1
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
        HTTPS_OK=1
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
src = sqlite3.connect("/var/lib/tender/app.db")
dst = sqlite3.connect("/var/lib/tender/backups/tender-%s.db" % time.strftime("%Y-%m-%d"))
src.backup(dst)
dst.close(); src.close()
PY
find /var/lib/tender/backups -name 'tender-*.db' -mtime +14 -delete
chown -R tender:tender /var/lib/tender/backups
CRON
chmod 755 /etc/cron.daily/tender-backup

say "6/6. Ссылка для работы"
IP="$(curl -fsS --max-time 5 https://ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')"
if [ -n "$DOMAIN" ]; then
  if [ "$HTTPS_OK" = "1" ]; then PUBLIC="https://${DOMAIN}"; else PUBLIC="http://${DOMAIN}"; fi
else
  PUBLIC="http://${IP}:${PORT_PUBLIC}"
fi
set_env TENDER_PUBLIC_URL "$PUBLIC"
systemctl restart "$SVC"

cat > /usr/local/bin/tender-link <<'LINK'
#!/bin/sh
# Ссылки тендерного калькулятора. Запускать от root.
set -e
ENV=/etc/tender.env
. "$ENV"
APP=/opt/tender/app/server.py
case "${1:-}" in
  new)
    CODE="$(python3 -c 'import secrets; print(secrets.token_urlsafe(9))')"
    sed -i "s|^TENDER_INVITE=.*|TENDER_INVITE=$CODE|" "$ENV"
    systemctl restart tender
    TENDER_INVITE="$CODE"
    echo "Старая ссылка-приглашение больше не работает (уже созданные рабочие места остаются). Новая:"
    ;;
  users)
    runuser -u tender -- env TENDER_DATA="$TENDER_DATA" python3 "$APP" users
    exit 0
    ;;
  user)
    KEY="$(runuser -u tender -- env TENDER_DATA="$TENDER_DATA" python3 "$APP" relink "$2")"
    echo "Новая личная ссылка для рабочего места $2 (старая перестала работать):"
    echo "${TENDER_PUBLIC_URL}/#/key/${KEY}"
    exit 0
    ;;
  *)
    echo "Ссылка-приглашение. Отправьте её тем, кто будет работать:"
    ;;
esac
echo "${TENDER_PUBLIC_URL}/#/join/${TENDER_INVITE}"
LINK
chmod 755 /usr/local/bin/tender-link
INVITE="$(grep -E '^TENDER_INVITE=' "$ENV_FILE" | cut -d= -f2-)"

echo
echo "=============================================================="
ok "Готово. Ссылка для работы (отправьте её тем, кому нужен калькулятор):"
echo
echo "    ${PUBLIC}/#/join/${INVITE}"
echo
if [ -z "$DOMAIN" ]; then
  echo "Если ссылка не открывается, разрешите входящий порт ${PORT_PUBLIC} в панели Рег.облака."
fi
if grep -q '^TENDER_TG_TOKEN=.\+' "$ENV_FILE"; then echo "Telegram-бот подключён: каждый включает напоминания в разделе «Настройки»."; fi
echo "Показать ссылку ещё раз:           tender-link"
echo "Выпустить новую (старая отключится): tender-link new"
echo "Обновить программу: запустите команду установки ещё раз."
echo "Данные и документы: ${DATA_DIR}  (копии базы: ${DATA_DIR}/backups)"
echo "=============================================================="
