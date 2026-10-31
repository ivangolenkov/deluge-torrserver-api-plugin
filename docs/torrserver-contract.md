# Контракт эмуляции TorrServer: источник и границы

Это **анализ HTTP-эмуляции**, а не готовый API плагина и не запись общения
Lampa/Num с эталонным TorrServer. Источник — указанный пользователем репозиторий
`torrent-stream-hub`, commit `043e26a2bd2479350dbc1d1486b32e85e7bacb7a`.
Движок, management API и Web GUI этого проекта не переносятся.

В [fixtures](../tests/fixtures/torrserver-emulation/manifest.json) записаны SHA256
семи исходных файлов, 13 регистраций маршрутов и ограничения источника.
[JSON Schema](../tests/fixtures/torrserver-emulation/schema.json) фиксирует
наблюдаемые типы, а [examples](../tests/fixtures/torrserver-emulation/examples.json)
содержит синтетические обезличенные примеры, выведенные из кода и тестов.
Примеры не запускались против Go-сервера и не являются captures клиентов.

Позднее отдельно получены [реальные captures эталона и модуля Lampa](../tests/fixtures/torrserver-reference/README.md).
Они не заменяют этот исторический snapshot: эталон имеет **числовой stat**,
preload отвечает пустым 200, viewed использует **set/list/rem** с persistence.
Окончательная совместимость Num и плагина ещё не подтверждена.

Дополнительная проверка локальной Lampa 3.1.8 (`http://localhost:3000/`):
настройки TorrServer проверяют **GET к корню сервера**, не `/echo`, и помечают
404 как ошибку. Плагин поэтому отвечает на GET/HEAD `/` тем же коротким
`MatriX.147.1`, что и на `/echo`, с CORS. Это compatibility health response,
не копия Web GUI TorrServer и не выдача списка торрентов. Неизвестные пути
по-прежнему возвращают 404. Проверка индикатора доступности в настоящем браузере
не равна проверке воспроизведения, перемотки или совместимости всех версий Lampa.

В той же Lampa file-list polling (`Torserver.files` / `Torrent.files`) проверяет
`if (json.file_stats)` и использует HTTP timeout 2000 ms. Поэтому pending DTO
плагина **не содержит file_stats** (поле необязательно по schema), а не возвращает
истинный в JS пустой массив. `/torrents get` допускает short-poll до 1 секунды;
если metadata не появилась, возвращает stat 1 / metadata_pending и клиент
продолжает опрос. Add/list/stat остаются неблокирующими. File-dependent маршруты
ожидают metadata до metadata_timeout_seconds и отменяются с HTTP-клиентом.
На изолированном установленном плагине metadata намеренно удерживалась около
33 секунд: настоящая Lampa продолжала polling, а после local-peer metadata
exchange открыла список двух файлов. Это не проверка декодирования fixture-видео.

## Наблюдаемый HTTP-интерфейс

| Маршрут | Поведение источника |
| --- | --- |
| GET /echo | 200, text/plain, строка `1.2.133` |
| POST /torrents | `add`, `get`, `list`, `set`, `rem`, `drop`, `wipe` |
| POST /torrent/upload | Multipart `.torrent`, новый upload постоянный |
| POST /settings | `get` возвращает параметры; `set`/`def` — no-op |
| POST /cache | Состояние окна; пустой объект при отсутствии данных/неизвестном action |
| POST /viewed | Безусловный `{"status":"ok"}` — заглушка, не persistence |
| GET /playlist | M3U для hash |
| GET, HEAD /stream и /stream/{name} | `link`, `index`; флаги `m3u`, `preload`, `stat`, затем поток |
| GET, HEAD /play/{hash}/{id} | Алиас потока |

Проверяется наличие флагов, а не только значение `true`; приоритет обработки:
`m3u` → `preload` → `stat` → поток. `play` не меняет этот порядок.
JSON body для `/torrents` и `/cache` принимается даже с
`application/x-www-form-urlencoded; charset=UTF-8`: это не URL-encoded форма.
Настоящая форма применяется к multipart upload. CORS источника разрешает `*`,
GET/HEAD/POST/OPTIONS, заголовки Range и DLNA; это не авторизация.
Для upload используется файловая часть `file`, необязательные текстовые поля
`poster` и `folder_name`. Обработчик сохраняет новый upload постоянным независимо
от `save_to_db` JSON-действия add; переносить внутреннюю обработку путей не нужно.

## DTO и действия, которые используем как ориентир

- `data` — непрозрачная **строка** с JSON-текстом, не вложенный JSON object.
  `title`, `poster`, `category` также строки; `save_to_db` — boolean.
- `set` меняет дополнительные метаданные, не путь payload или настройки Deluge.
  `drop`/`wipe` безопасны и ничего не удаляют; `rem` сохраняет payload.
  Допуск удаления при активной передаче определяется нашим ТЗ, не этим примером.
- Ответ содержит `hash`, `title`, `name`, `timestamp`, размеры/скорости/peers,
  preload-поля и `file_stats` с `id`, `path`, `length`, `size`.
  Размеры и timestamp — JSON integers, `progress` — number.
- `stat` и `stat_string` в snapshot — **строки внутренних состояний**.
  Это намеренно зафиксировано как наблюдение, а не утверждение о правильном
  типе в эталонном TorrServer. Тип эталона уже подтверждён отдельной записью:
  integer. Строка остаётся наблюдением над TSH, не целевым stat плагина.
- `/cache` использует регистр `Hash`, `Capacity`, `Filled`, `PiecesLength`,
  `PiecesCount`, `Torrent`, `Pieces`, `Readers`. Последние поля snapshot
  частично заглушены: `0`, `0`, `{}`, `[]`; это не готовый контракт наблюдаемости.
- Ошибки обычно имеют `{"error":"..."}`, безопасный no-op —
  `{"status":"ok"}`. Для потока окончательный Range-ответ формирует Go
  `ServeContent`; парсер `stream_range.go` сам по себе не описывает HTTP-ответ.

## Что нельзя переносить без изменений

| Особенность snapshot | Решение по SPEC.md |
| --- | --- |
| `link`/`play` — 1-based, но `hash`-shortcut — 0-based; nonpositive id сводится к первому файлу | Явный единый 1-based payload mapping, без скрытого fallback; pad/служебные записи не публиковать |
| file id = внутренний index + 1; возможны пропуски | Не считать индексом Deluge внешний id; пользоваться таблицей mapping |
| HEAD preload может запланировать warmup | HEAD не читает payload и не запускает загрузку/QoS |
| Playlist строится с Host запроса | Использовать настроенный public_base_url |
| `/viewed` всегда отвечает ok | У эталона подтверждены set/list/rem и persistence; хранить метки в SQLite, не копировать заглушку |
| Числа settings/cache частично константные | Capacity — логическое disk-backed окно, Filled — готовые байты, не общий progress и не RAM |
| Go ServeContent определяет Range | Соблюдать SPEC §6.1 и существующие unit-тесты Range, не копировать отличия Go неявно |

Неизвестное действие должно иметь определённую ошибку. Наблюдаемое молчаливое
`{}` для неизвестного cache action — свойство источника, не разрешение менять ТЗ.
HTTP 200 для `/viewed`, cache или echo не подтверждает совместимость плеера.

## Проверки и следующий шаг

`make test-unit` проверяет валидность schemas, типы примеров, маршруты, полноту
torrents actions и честную маркировку происхождения. Он **не** тестирует ещё
не реализованные handlers плагина. `jsonschema` — dev-зависимость, не runtime.

Необязательная read-only сверка с исходным checkout (без Go и сети):

```sh
python3 tools/verify_contract_source.py --source-root /Users/ivangolenkov/Documents/my/my/torrent-stream-hub
```

Она проверяет commit, семь SHA256, 13 маршрутов и четыре DTO; не исполняет Go.
Обычные build/check не зависят от соседнего репозитория. По указанию пользователя
от 29 октября 2026 года полученных module-level записей Lampa/TorrServer достаточно
для клиентского DoD фазы 0. Num и оставшиеся [реальные клиентские сценарии](../tests/fixtures/README.md),
особенно полноценные playback/seek и закрытие страницы, перенесены в последующую
приёмку, при необходимости пользователем; они не объявлены проверенными.
Исходный Go-код не включается в plugin artifact;
сведения о происхождении не заменяют лицензионный аудит перед выпуском.
