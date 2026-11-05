# OSINT-набор для работы и Telegram-канала

Набор для разведки по открытым источникам под задачи продаж ЭКСПЕРТ ОЙЛ / TEBOIL: проверка
контрагентов, мониторинг конкурентов, защита бренда от двойников и аналитика Telegram-канала.

Установка: `bash scripts/ad-hoc/osint/install-osint-tools.sh`. Контейнер облачной сессии
одноразовый, поэтому в новой сессии скрипт нужно запустить заново или прописать в «Setup script»
окружения.

## Что установлено (проверено 2026-11-05)

| Инструмент                  | Для чего в работе                                                  |
| --------------------------- | ------------------------------------------------------------------ |
| `maigret`, `sherlock`       | Где ещё встречается ник/название: бренд, дилер, конкурент          |
| `dnstwist`                  | Зарегистрированные домены-двойники (контрафакт, фишинг под бренд)  |
| `waybackpy`                 | История сайта конкурента: старые прайсы, ассортимент, условия      |
| `trafilatura`               | Чистый текст со страницы: новости, прайсы, описания продуктов      |
| `exiftool`                  | Метаданные PDF/фото: автор документа, программа, дата, GPS         |
| `whois`, `dig`              | Владелец и дата регистрации домена, почтовые и DNS-записи          |
| `holehe`, `ignorant`        | К каким сервисам привязан email / телефон                          |
| `shodan`                    | Поиск по серверам (нужен ключ с shodan.io, нужен редко)            |
| `yt-dlp`                    | Скачать видеообзор (YouTube, VK, Rutube) для разбора               |
| `nmap`, `tor`, `jq`         | Служебные: проверка хоста, анонимный выход, разбор JSON            |
| `/opt/osint-py`: `telethon` | Telegram API: статистика своего канала, мониторинг чужих каналов   |
| `/opt/osint-py`: `dadata`   | Проверка компании по ИНН/ОГРН (ключ на dadata.ru, есть бесплатный) |

Не установлены, потому что их нужно качать с GitHub, а в сессии это требует явного разрешения:
`theHarvester` (почты и поддомены компании), `SpiderFoot` (веб-интерфейс, 200+ источников),
`PhoneInfoga` (номер телефона), `Telepathy` (OSINT по Telegram). После разрешения они ставятся
флагом `--with-github`. Этот блок не проверялся.

## Ограничение сети

Сейчас окружение пускает только к менеджерам пакетов и GitHub: сайты, DNS и whois из контейнера
недоступны, поэтому инструменты выше установлены, но искать не могут. Чтобы они заработали, в
настройках облачного окружения (меню окружения в заголовке сессии → Edit → Network access)
нужно выбрать более широкий уровень доступа или добавить нужные домены в Allowed domains.

Веб-поиск и чтение страниц у Claude работают отдельно от контейнера, так что исследование
компаний и конкурентов через поиск доступно уже сейчас.

## Примеры

```bash
dnstwist --registered teboil.ru              # двойники домена бренда
maigret expertoil --top-sites 500            # где используется название
waybackpy --url konkurent.ru --oldest        # самая старая копия сайта
trafilatura -u https://konkurent.ru/prices   # текст страницы
exiftool prais-konkurenta.pdf                # кто и когда сделал документ
whois konkurent.ru
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

- `dossier`: досье на компанию или человека перед встречей и проверка контрагента.
- `competitive-intel`, `competitor-profiling`, `competitive-ads-extractor`: конкуренты, их
  реклама, battlecards.
- `lead-research-assistant`, `prospecting`: поиск и квалификация клиентов (автопарки, СТО,
  дилеры спецтехники).
- `deep-research`: многошаговое исследование рынка с источниками.
- `collecting-open-source-intelligence`, `conducting-external-reconnaissance-with-osint`:
  общая методика OSINT.
- `analyzing-typosquatting-domains-with-dnstwist`,
  `analyzing-certificate-transparency-for-phishing`: поиск сайтов-подделок под бренд.
- `social-media-analyzer`, `social-media-manager`: аналитика и ведение канала.

## Закон

Данные о физлицах (телефоны, email, аккаунты) подпадают под 152-ФЗ «О персональных данных».
Собирать и хранить их можно только при законном основании. Для работы с компаниями, доменами и
брендами таких ограничений нет.
