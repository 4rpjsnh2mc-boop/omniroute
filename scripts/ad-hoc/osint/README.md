# OSINT-набор для работы и Telegram-канала

Набор для разведки по открытым источникам под задачи продаж ЭКСПЕРТ ОЙЛ / TEBOIL: проверка
контрагентов, мониторинг конкурентов, защита бренда от двойников и аналитика Telegram-канала.

```bash
bash scripts/ad-hoc/osint/install-osint-tools.sh                # PyPI + apt
bash scripts/ad-hoc/osint/install-osint-tools.sh --with-github  # + theHarvester, SpiderFoot
```

Повторный запуск безопасен: установленное пропускается. При ошибке скрипт показывает её причину
и завершается с кодом 1, полный лог — `/var/log/osint-install.log`.

### Автоустановка в каждой новой сессии

Контейнер облачной сессии одноразовый. Чтобы набор ставился сам, вставьте в «Setup script»
окружения (меню окружения → «Изменить облачную среду»):

```bash
f=omniroute/scripts/ad-hoc/osint/install-osint-tools.sh
if [ -f "$f" ]; then bash "$f" --with-github || true; fi
```

- Setup script запускается в папке, где лежат репозитории, поэтому путь начинается с `omniroute/`.
- `|| true` обязателен: упавший setup script не даёт сессии запуститься.
- Скрипт лежит в ветке `claude/dazzling-mendel-e6u574`. Пока ветка не влита в основную, в сессиях
  на основной ветке файла нет, и проверка `[ -f ]` просто пропустит установку.

## Что установлено (проверено 2026-11-05)

| Инструмент                  | Для чего в работе                                                  |
| --------------------------- | ------------------------------------------------------------------ |
| `theHarvester`              | Почты, поддомены и хосты домена компании                           |
| `spiderfoot`                | 230 модулей разведки по домену/IP, CLI и веб-интерфейс             |
| `dnstwist`                  | Домены-двойники бренда (контрафакт, фишинг)                        |
| `maigret`, `sherlock`       | Где ещё встречается название бренда, дилера, конкурента            |
| `trafilatura`               | Чистый текст со страницы: новости, прайсы, описания продуктов      |
| `exiftool`                  | Метаданные PDF/фото: автор документа, программа, дата              |
| `waybackpy`                 | История сайта в Wayback Machine (из облака сейчас не работает)     |
| `holehe`, `ignorant`        | К каким сервисам привязан email / телефон (аудит своих контактов)  |
| `shodan`                    | Поиск по серверам (нужен ключ с shodan.io)                         |
| `yt-dlp`                    | Скачать видеообзор (YouTube, VK, Rutube) для разбора               |
| `whois`, `dig`, `nmap`      | Работают только при прямом выходе в сеть, в облаке — нет           |
| `/opt/osint-py`: `telethon` | Telegram API: статистика своего канала                             |
| `/opt/osint-py`: `dadata`   | Проверка компании по ИНН/ОГРН (ключ на dadata.ru, есть бесплатный) |

## Сеть в облачной сессии

Нужен уровень доступа **Full** (меню окружения → «Изменить облачную среду» → Network access).
Наружу выходит только HTTPS через прокси сессии (`$HTTPS_PROXY`), поэтому:

- `whois`, `dig` и DNS-резолв в `dnstwist` не работают — домены проверяются через DNS-over-HTTPS
  (пример ниже).
- `trafilatura` не умеет HTTP-прокси — страницу скачивает `curl`, текст достаёт trafilatura.
- `maigret` нужен `--proxy "$HTTPS_PROXY" -n 8`; часть сайтов всё равно отвечает 403/429.
- `theHarvester` сам не читает `$HTTPS_PROXY`, а порт прокси меняется даже посреди сессии.
  Установщик ставит обёртку, которая при каждом запуске прописывает текущий прокси и флаг `-p`.
- `web.archive.org` обрывает соединения из облака — архив сайтов смотрит Claude через веб-поиск.
- `spiderfoot` запускается, но модули, которым нужен DNS, в облаке ничего не находят; пробный
  скан teboil.ru вернул только сам домен. Полноценно он работает на своём компьютере.

## Примеры

```bash
# Почты и поддомены компании
theHarvester -d konkurent.ru -b hackertarget,rapiddns,duckduckgo -l 200

# Пассивная разведка по домену, отчёт в CSV
spiderfoot -s konkurent.ru -u passive -o csv > konkurent.csv

# Зарегистрированные двойники домена бренда (через DNS-over-HTTPS)
dnstwist --format list teboil.ru | grep -vx teboil.ru | xargs -P 6 -I{} sh -c \
  'a=$(curl -s "https://dns.google/resolve?name={}&type=A" | jq -r "[.Answer[]?.data]|join(\" \")"); [ -n "$a" ] && echo "{} $a"'

# Текст страницы конкурента
curl -sL https://konkurent.ru/prices | trafilatura

# Где используется название
maigret expertoil --top-sites 500 --proxy "$HTTPS_PROXY" -n 8

# Кто и когда сделал документ
exiftool prais-konkurenta.pdf
```

Проверка контрагента по ИНН:

```python
# /opt/osint-py/bin/python
from dadata import Dadata

d = Dadata("КЛЮЧ_С_DADATA_RU")
company = d.find_by_id("party", "7707083893")[0]
print(company["value"], company["data"]["state"]["status"], company["data"]["address"]["value"])
```

Просмотры последних постов своего канала (`api_id`/`api_hash` выдаются на my.telegram.org):

```python
# /opt/osint-py/bin/python
from telethon.sync import TelegramClient

API_ID = 1234567  # ваш api_id
API_HASH = "ваш_api_hash"

with TelegramClient("session", API_ID, API_HASH) as client:
    for msg in client.iter_messages("имя_канала", limit=20):
        print(msg.date.strftime("%d.%m"), msg.views, (msg.text or "")[:60])
```

## Навыки Claude для OSINT (уже включены)

- `dossier`: досье на компанию перед встречей и проверка контрагента.
- `competitive-intel`, `competitor-profiling`, `competitive-ads-extractor`: конкуренты, их
  реклама, battlecards.
- `lead-research-assistant`, `prospecting`: поиск и квалификация клиентов (автопарки, СТО,
  дилеры спецтехники).
- `deep-research`: многошаговое исследование рынка с источниками.
- `collecting-open-source-intelligence`, `conducting-external-reconnaissance-with-osint`:
  методика разведки по организации.
- `analyzing-typosquatting-domains-with-dnstwist`,
  `analyzing-certificate-transparency-for-phishing`: поиск сайтов-подделок под бренд.
- `social-media-analyzer`, `social-media-manager`: аналитика и ведение канала.

## Закон

Данные о физлицах (телефоны, email, аккаунты) подпадают под 152-ФЗ «О персональных данных», а
сбор сведений о частной жизни без согласия — под ст. 137 УК РФ. Набор предназначен для работы с
компаниями, доменами и брендами.
