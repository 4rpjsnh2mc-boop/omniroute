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

# 4. Код и бинарники с GitHub. По умолчанию выключено: в сессиях Claude
#    загрузку чужого кода с GitHub нужно разрешить явно. Этот блок
#    в сессии 2026-11-05 не запускался и не проверялся.
if ((WITH_GITHUB)); then
  step "theHarvester (почты и поддомены компании)" \
    pipx install -q --python 3.14 --fetch-python=missing \
    "git+https://github.com/laramies/theHarvester.git"
  step "telepathy (OSINT по Telegram-каналам)" \
    pipx install -q --python 3.10 --fetch-python=missing telepathy
  step "phoneinfoga (информация по номеру телефона)" bash -c '
    d=$(mktemp -d) &&
    curl -fsSL -o "$d/pi.tgz" \
      https://github.com/sundowndev/phoneinfoga/releases/latest/download/phoneinfoga_Linux_x86_64.tar.gz &&
    tar -xzf "$d/pi.tgz" -C "$d" &&
    install -m755 "$d/phoneinfoga" /usr/local/bin/phoneinfoga &&
    rm -rf "$d"'
  step "spiderfoot (веб-интерфейс, 200+ источников)" bash -c '
    rm -rf /opt/spiderfoot &&
    git clone -q --depth 1 https://github.com/smicallef/spiderfoot.git /opt/spiderfoot &&
    python3 -m venv /opt/spiderfoot/venv &&
    /opt/spiderfoot/venv/bin/pip install -q -r /opt/spiderfoot/requirements.txt'
fi

echo
echo "Готово: ${#ok[@]}, ошибок: ${#failed[@]}"
for name in "${failed[@]}"; do echo "  ✗ $name"; done
((${#failed[@]} == 0))
