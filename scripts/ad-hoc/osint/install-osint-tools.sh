#!/usr/bin/env bash
# Установка OSINT-набора в облачный контейнер Claude Code (Ubuntu 24.04, root).
# Контейнер одноразовый: после перезапуска сессии запустите скрипт снова
# или вставьте его вызов в «Setup script» облачного окружения.
#
#   bash scripts/ad-hoc/osint/install-osint-tools.sh                # базовый набор (PyPI + apt)
#   bash scripts/ad-hoc/osint/install-osint-tools.sh --with-github  # + инструменты с GitHub
#
# Инструкция: scripts/ad-hoc/osint/README.md
set -uo pipefail

export DEBIAN_FRONTEND=noninteractive
export PIPX_HOME=/opt/pipx
export PIPX_BIN_DIR=/usr/local/bin

WITH_GITHUB=0
[[ "${1:-}" == "--with-github" ]] && WITH_GITHUB=1

ok=()
failed=()
step() {
  local name=$1
  shift
  printf '… %s\n' "$name"
  if "$@" >/dev/null 2>&1; then ok+=("$name"); else failed+=("$name"); fi
}

# 1. Системные утилиты: whois, dig, exiftool (метаданные файлов), jq, nmap, tor
step "apt: обновление списков" apt-get update -qq
step "apt: whois dnsutils exiftool jq nmap tor" \
  apt-get install -y -qq whois dnsutils libimage-exiftool-perl jq nmap tor torsocks

# 2. pipx — каждый инструмент в своём изолированном окружении
if ! command -v pipx >/dev/null 2>&1; then
  step "pipx" pip3 install -q --ignore-installed pipx
fi

PYPI_TOOLS=(
  sherlock-project # поиск аккаунтов по нику (400+ сайтов)
  maigret          # то же, 3000+ сайтов, хорошо покрывает рунет
  holehe           # на каких сервисах зарегистрирован email
  ignorant         # на каких сервисах зарегистрирован телефон
  "dnstwist[full]" # домены-двойники бренда (контрафакт, фишинг)
  waybackpy        # история сайта в Wayback Machine
  shodan           # поиск по серверам и устройствам (нужен API-ключ)
  trafilatura      # вытащить чистый текст со страницы
  yt-dlp           # скачать видео (YouTube, VK, Rutube) для анализа
)
for tool in "${PYPI_TOOLS[@]}"; do
  step "pipx: $tool" pipx install -q "$tool"
done
# shodan 1.31 импортирует pkg_resources, которого нет в новых setuptools
step "pipx: shodan (фикс setuptools)" pipx inject -q shodan "setuptools<81"

# 3. Библиотеки для своих скриптов: Telegram API и проверка компаний по ИНН
step "venv /opt/osint-py: telethon dadata requests" bash -c \
  'python3 -m venv /opt/osint-py && /opt/osint-py/bin/pip install -q telethon dadata requests'

# 4. Инструменты разведки по организациям с GitHub (по умолчанию выключено).
#    theHarvester требует Python 3.14, SpiderFoot — старые зависимости,
#    поэтому интерпретаторы берёт uv.
if ((WITH_GITHUB)); then
  export PATH="/root/.local/bin:$PATH"
  export UV_TOOL_DIR=/opt/uv-tools
  export UV_TOOL_BIN_DIR=/usr/local/bin
  command -v uv >/dev/null 2>&1 || step "uv" pip3 install -q uv

  step "theHarvester (почты, поддомены, хосты домена)" \
    uv tool install -q --python 3.14 "git+https://github.com/laramies/theHarvester.git"
  # theHarvester не читает HTTPS_PROXY: прокси сессии прописывается в его конфиг,
  # запуск с флагом -p.
  if [[ -n "${HTTPS_PROXY:-}" ]]; then
    mkdir -p ~/.theHarvester
    printf 'http:\n    - %s\n' "${HTTPS_PROXY#http://}" >~/.theHarvester/proxies.yaml
  fi

  step "spiderfoot (230 модулей, CLI и веб-интерфейс)" bash -c '
    rm -rf /opt/spiderfoot &&
    git clone -q --depth 1 https://github.com/smicallef/spiderfoot.git /opt/spiderfoot &&
    uv venv -q --python 3.11 /opt/spiderfoot/venv &&
    uv pip install -q --python /opt/spiderfoot/venv/bin/python -r /opt/spiderfoot/requirements.txt'
  cat >/usr/local/bin/spiderfoot <<'EOF'
#!/bin/sh
cd /opt/spiderfoot && exec ./venv/bin/python sf.py "$@"
EOF
  chmod 755 /usr/local/bin/spiderfoot
fi

echo
echo "Готово: ${#ok[@]}, ошибок: ${#failed[@]}"
for name in "${failed[@]}"; do echo "  ✗ $name"; done
((${#failed[@]} == 0))
