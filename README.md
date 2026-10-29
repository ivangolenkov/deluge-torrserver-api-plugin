# Deluge Stream Plugin

Проект плагина для просмотра торрентов в Lampa и Num через Deluge. Обычная загрузка и раздача остаются в Deluge; целевой продукт добавит совместимость с TorrServer, приоритет нужных частей файла и управление временными торрентами.

**Состояние проекта:** фазы 0–2 и 4 завершены; фаза 3 в работе. Реализованы core/web,
SQLite, FIFO и встроенные TorrServer HTTP/Range/preload с несколькими читателями.
Полная клиентская приёмка Lampa/Num ещё не выполнена и отложена пользователем.
По его разрешению фаза 4 выполнена раньше: настройки и действия Web UI, tickets,
готовые file/ZIP downloads. Полные ARM64/x86_64 check, Chromium и раздельные core/web
контейнеры проверены. [Отчёт и ограничения](tasks/implementation/phase4-report.md).
Hosted run и релиза нет.

Фаза 5 в работе: локальная упаковка и автоматизация продолжаются, LICENSE
пользователь создаст позже. Для выпуска также нужны GitHub runs и NAS-приёмка.
[Текущий отчёт](tasks/implementation/phase5-report.md).

Локальный candidate, manifest/checksums, Docker Compose, volumes и release gates:
[инструкция поставки](docs/delivery.md). Это ещё не завершённая фаза 5 и не релиз.

При enable Core открывает `<config deluged>/stream.sqlite`: WAL, последовательные
миграции, политика временности/публикации, монотонный FIFO-ключ, hash aliases,
metadata/viewed и настройки. SQLite и проверки файловых путей выполняются
ограниченным однопоточным worker вне reactor. Смена политики доступна владельцу
или администратору; shared-доступ не даёт права изменения. Обычные торренты
регистрируются постоянными и неопубликованными. **Перевод во временный разрешает
автоматическое удаление торрента вместе с данными при превышении лимита.**
Подробности — в [отчёте фазы 1](tasks/implementation/phase1-report.md).

В фазе 2 согласованы штатные гарантии удаления Deluge/libtorrent, без собственного
unlink и замораживания внешних rename/move. [Отчёт](tasks/implementation/phase2-report.md).

В фазе 3 согласовано: плагин управляет своими deadlines, но не восстанавливает
числовые native piece priorities и не ведёт для этого recovery journal.
Остаточные изменения после просмотра/crash допустимы. File priorities, skip,
first/last, pause и очередь плагин не меняет; payload/recheck остаются Deluge.
**Готовые проверенные данные доступны на паузе и у skipped-файлов.** Если нужных
данных нет, плагин не снимает паузу и не включает файл. [Отчёт](tasks/implementation/phase3-report.md).

Один `.egg` теперь содержит core и web entry points и браузерный script. Web-часть
выполняет авторизованную проверку версии core, добавляет настройки и панель Stream,
действия политики/публикации и прямого скачивания с core listener.
Процессы deluged и deluge-web не требуют общего профиля или каталога downloads.

Отдельный исследовательский reader фазы 0 сохранён на loopback-порту с эфемерной
авторизацией. Это не публичный download endpoint; он не работает одновременно
с новым streaming-controller, чтобы не обходить общий бюджет. Исследовательские
QoS/witness/ledger остаются тестами и не подключены к продуктовой recovery.
[Архитектура](docs/architecture.md), [история фазы 0](tasks/implementation/phase0-report.md).

Зафиксирован [HTTP-контракт эмуляции TorrServer из torrent-stream-hub](docs/torrserver-contract.md):
маршруты, schemas и тестовые примеры без переноса движка или Web GUI. Отдельно
получены [35 реальных HTTP-записей TorrServer и модуля Lampa](tests/fixtures/torrserver-reference/README.md).
Это частичная проверка модуля клиента в Chromium, не воспроизведение видео,
не тест Num и не приёмка нового API плагина.
По согласованию с пользователем этого достаточно для клиентской части фазы 0.
Num и полноценные UI/playback-сценарии отложены до последующей приёмки;
при необходимости пользователь выполнит их позднее.

## Что будет уметь плагин

- Воспроизводить незавершённые торренты в Lampa/Num через TorrServer-совместимый HTTP API, включая перемотку.
- Повышать срочность загрузки pieces около текущей позиции и учитывать несколько одновременных просмотров.
- Разделять торренты на временные и постоянные. При превышении количества удалять самые старые временные вместе с данными, сохраняя активные передачи.
- Показывать в стандартном Web UI Deluge временность, публикацию, состояние стриминга и отдельные HTTP-скачивания.
- Давать скачать готовый файл или весь готовый торрент ZIP64-архивом без создания второго полного архива на диске.

Вся серверная часть работает внутри deluged и использует его libtorrent session. Отдельный Go-сервис не нужен. Для управления используется расширение стандартного deluge-web.

## Платформы и ограничения

Целевые платформы — Linux x86_64 и ARM64, включая NAS и Docker. Прототип проверяется на Debian 12, Python 3.11.2, Deluge 2.2.0, libtorrent 2.0.8.0 и Twisted 24.11.0. ARM64 запускается нативно в Linux VM; x86_64 — под эмуляцией. Это не подтверждение всех Deluge 2.x или реального NAS. [Точная матрица и зависимости](docs/compatibility.md).

В первой версии не планируются транскодирование, отдельный веб-плеер и поддержка Windows/macOS. FIFO-очистка срабатывает только при превышении количества временных торрентов, а не по свободному месту или общему размеру данных.

Скачивание из Web UI доступно только для готовых проверенных данных. ZIP недоступен,
если хотя бы один payload-файл не скачан, включая skipped, даже при 100% wanted
progress. Возобновление динамического ZIP по Range не поддерживается.

## Как будет устанавливаться

После появления релиза порядок установки будет следующим:

1. Проверить поддерживаемые версии Deluge, Python и libtorrent в таблице релиза.
2. Скачать совместимый plugin artifact из GitHub Releases этого репозитория и проверить опубликованную checksum.
3. Установить пакет в окружение, где работает deluged, и включить core-плагин штатным механизмом Deluge.
4. Если deluge-web работает отдельно, установить web-часть того же пакета также в его окружение. Установка только UI-части не добавляет серверные функции.
5. Настроить каталог загрузок, владельца TorrServer-контекста, HTTP-порт и доступный клиентам public_base_url.
6. Указать полученный адрес сервера в Lampa/Num. Торренты, добавленные обычным способом в Deluge, публикуются для TorrServer явно.

Этот порядок получения пакета из Releases описывает **будущий выпуск**, которого
ещё нет. Сейчас есть сборка кандидата и установка в одноразовые тестовые профили.
Core/web и настройки реализованы, но установка в рабочий Deluge не рекомендуется
до завершения приёмки. Подключение Lampa/Num нового backend ещё не проверено.

## Сборка и тестирование

Рекомендуемый проверяемый путь — Docker с Linux containers. Из корня чистой копии:

```sh
make help
docker build --platform linux/arm64 -f tools/runtime.Dockerfile -t deluge-stream-phase0:arm64 .
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make check VENV=/opt/venv
```

Для x86_64 замените platform на `linux/amd64`, tag на `deluge-stream-phase0:amd64`
и добавьте `BUILD_DIR=build/amd64 DIST_DIR=dist/amd64` к make check.
На ненативной архитектуре нужна поддержка эмуляции Docker.

Artifact ARM64-проверки: `dist/Stream-0.0.1-py3.11.egg`. Это pure-Python egg,
но runtime libtorrent является системной зависимостью. Не переименовывайте egg
для другой версии Python. Сборка `.egg` через setuptools пока необходима для
проверенного PluginManager Deluge 2.2.0; setuptools сообщает deprecation warning.

`make smoke` автоматически копирует именно этот artifact в новый временный
`profile/plugins`, запускает настоящий deluged и выполняет 20 enable/disable
через авторизованный RPC. Рабочий профиль пользователя не используется.
`make test-integration` проверяет реальные компоненты Deluge, локальный seed,
HTTP read/cancel, pause/recheck/move/delete-readd, metadata v1/v2/hybrid,
resume-data/SIGKILL-recovery, identity/worker-result guards, очередь/паузу сессии
и отдельный ZIP64 writer-эксперимент. Retained ownership отдельно проверяется
на временной SQLite и при crash/policy change во время настоящего commit.

Основные команды уже реализованы:

| Команда | Назначение | Статус |
| --- | --- | --- |
| make help | Команды и подготовка окружения | Реализовано |
| make bootstrap | Venv и закреплённые dev-зависимости | Реализовано |
| make lint / make format-check | Проверка кода и форматирования | Реализовано |
| make check-docs | Markdown parser и локальные ссылки | Реализовано |
| make test-unit | Range, layout/file policy, identity/worker guards, контрактные artifacts, ссылки и clean | Реализовано |
| make test-persistence | Настоящая временная SQLite: policy/FIFO/aliases/viewed, миграции, worker/cancel/lock и исследовательский recovery ledger | Реализовано |
| make test-integration | Deluge/libtorrent, seed, HTTP, policy/FIFO/restart/crash | Реализовано |
| make test | Unit + integration + downloads + браузер | Реализовано |
| make build | Исследовательский egg в dist/ | Реализовано |
| make smoke | Установка/enable/disable artifact в чистом профиле | Реализовано |
| make check | Документация, lint, format, тесты, build и smoke | Реализовано |
| make test-web / make test-downloads | Настоящий Chromium + core/web; native file/ZIP/tickets и ZIP64 | Реализовано |
| make test-web-split | Два контейнера без общего payload-диска; нужен Docker и собранный egg/runtime image | Реализовано; отдельный CI-шаг |
| make docker-build / make docker-up / make docker-down | Сборка и запуск тестового Docker-окружения | Фаза 5 |
| make release-check | Локальная подготовка и проверка релизного комплекта без публикации | Фаза 5 |
| make clean | Только стандартные build/dist и tool caches проекта | Реализовано |
| make ci-check | Docker build закреплённого runtime и make check; логи в build/ci | Реализовано; по умолчанию linux/amd64 |

Строки фазы 5 остаются планом, соответствующих целей пока нет.
Успех make check подтверждает текущие эксперименты, но не полную продуктовую приёмку.

Для `test-web/check` дополнительно нужны Chromium (`/usr/bin/chromium`) и dev
Playwright 1.56.0. Docker runtime устанавливает их при сборке; запуск тестов
не скачивает браузер и не использует публичный swarm. Отсутствие браузера —
ошибка с подсказкой, не успешный skip.
При отсутствии обязательного runtime тесты завершаются ошибкой, а не skip.

Переменные: `PYTHON` — интерпретатор для bootstrap (по умолчанию python3),
`VENV` — окружение инструментов (`.venv`; в тестовом образе `/opt/venv`),
`BUILD_DIR`/`DIST_DIR` — генерируемые каталоги (`build`/`dist`),
`TEST_PROFILE_ROOT` — необязательный существующий родитель одноразовых профилей
(по умолчанию системный temp контейнера). Тест никогда не открывает готовый
профиль по этому пути: создаёт собственный подкаталог. `make clean` не следует
переопределённым путям и удаляет только стандартные выходы сборки/кэши.

Подготовка локального Linux без Docker, версии, лицензии и ограничения
воспроизводимости описаны в [docs/compatibility.md](docs/compatibility.md).

Сборка и тесты не должны менять пользовательский профиль Deluge, скачанные файлы или автоматически публиковать релиз.

## Автоматизация и релизы

Добавлен [.github/workflows/checks.yml](.github/workflows/checks.yml): PR,
push в `main`, ручной `workflow_dispatch` и reusable `workflow_call`. Job
`Linux x86_64 / Python 3.11` вызывает **make ci-check**: собирает закреплённый
Linux runtime и выполняет внутри него make check без внешней сети. Smoke
устанавливает egg в одноразовые профили и запускает настоящие deluged/deluge-web
в разных процессах, проверяя авторизацию и 20 enable/disable.

В GitHub Actions → Checks можно вручную выбрать Run workflow. После run тестовый
artifact `stream-linux-x86_64` содержит egg (если сборка дошла до него) и логи
`build/ci/image.log`, `build/ci/check.log`; срок хранения — 14 дней. Загрузка логов
выполняется и при неуспехе. Имена обязательных branch checks настраиваются в
репозитории, workflow сам не меняет branch protection. Hosted run пока **не
выполнялся**: файл workflow и локальные проверки не заменяют GitHub run.

Локальный эквивалент требует Docker и make, но не локального Deluge:

```sh
make ci-check
# На ARM64-хосте без эмуляции:
make ci-check CI_PLATFORM=linux/arm64 CI_IMAGE=deluge-stream-check:arm64
```

Сеть нужна при подготовке Docker-образа, но не при выполнении тестов. Hosted CI
пока автоматизирует только Linux x86_64. ARM64 проверяется локально; hosted
ARM64-матрица и поставка образов остаются задачей фазы 5. Actions закреплены SHA,
permissions только contents:read, публикационных secrets нет. Это тестовые
artifacts, не GitHub Releases.

В фазе 5 появится отдельный release workflow. Версионный тег будет запускать обязательные проверки, сборку и автоматическую публикацию проверенных plugin artifacts в GitHub Releases с checksum и таблицей совместимости. Обычный PR не публикует релиз; ручная проверка pipeline должна поддерживать режим без публикации.

Docker Compose и инструкции работы с постоянными volumes также запланированы на фазу 5. `tools/runtime.Dockerfile` — только изолированный стенд фазы 0, не продуктовый Docker-образ. Публичных релизных образов и artifacts пока нет.

## Тестовая установка core/web

Для подтверждённого Python 3.11 после `make build` используется
`dist/Stream-0.0.1-py3.11.egg`. Установочный artifact проверяется через `make smoke`,
а не импорт исходников. Установка вне тестов — явное действие пользователя:

1. Добавьте egg через штатную страницу Plugins Deluge или, остановив процессы,
   положите его в `plugins/` каталога конфигурации **deluged**.
2. Если deluge-web использует отдельный профиль/машину, положите **тот же egg** в
   его собственный `plugins/`. Общий диск с daemon не требуется.
3. Запустите процессы, включите Stream в Plugins на daemon и подключите Web UI
   к нему. В Preferences появится страница Stream с настройками и диагностикой core. При
   обновлении web-файлов перезагрузите страницу браузера.
4. Обе части должны иметь одинаковую версию plugin и API. Диагностика не
   подтверждает клиентскую совместимость: HTTP API реализован, но полная
   приёмка Lampa/Num ещё не выполнена.
5. Для отключения снимите Stream в Plugins daemon; web-часть снимается штатным
   событием. **Явное отключение сбрасывает временность, публикацию и экземплярные
   metadata/viewed.** Торренты и их файлы не удаляются, настройки сохраняются.
   Для удаления egg остановите
   соответствующие процессы и удалите только этот artifact из plugins/.

Автоматически проверены одноразовые Linux-профили, настоящий Chromium с Web UI
и раздельные контейнеры без общего payload-диска. Установка в существующий
NAS-профиль не принята. Смена временности/публикации, права и restart проверены
на настоящем daemon; UI-действия дополнительно проверены браузером. Backup/restore целого профиля
остаётся непроверенной процедурой; нельзя независимо восстанавливать старую
SQLite поверх другого native-каталога Deluge и считать это доказанной сверкой.

## HTTP и streaming: тестовый кандидат фазы 3

Listener по умолчанию `0.0.0.0:8090`, но запросы данных закрыты до выбора
действительного `torrent_owner`. Он предназначен **только для доверенной LAN**:
CORS `*` и отсутствие пароля TorrServer не делают порт безопасным для Интернета.
Контекст видит только доступные ему опубликованные торренты; обычные native
торренты не публикуются автоматически. Управляющие RPC по-прежнему авторизованы
Deluge; HTTP `/settings set/def` — compatibility no-op, а не обход RPC.

В авторизованном RPC-клиенте администратора, например для локального стенда:

```python
client.stream.set_config(1, {
    "torrent_owner": "localclient",
    "bind_host": "127.0.0.1",
    "bind_port": 8090,
    "public_base_url": "http://127.0.0.1:8090",
})
client.stream.get_diagnostics(1)
```

`localclient` — стандартная учётная запись Deluge; для другого владельца выберите
реальное имя из его auth-контекста. Для внешнего плеера замените bind на доступный
LAN-интерфейс и `public_base_url` на **адрес daemon, доступный плееру**, не localhost
deluge-web. За reverse proxy укажите внешний URL с учётом prefix. M3U строится
только из `public_base_url`, не из непроверенного Host. Без него прямой stream
работает, но playlist возвращает `public_url_required`. Подключение в Lampa/Num
должно использовать корень этого HTTP listener, а не Web UI/RPC порт Deluge.
Это порядок настройки кандидата, не заявление о пройденной приёмке клиентов.

Работают `/echo`, `/torrents add/get/list/set/rem/drop/wipe`, multipart
`/torrent/upload`, `/playlist`, `/stream[/name]`, `/play/{hash}/{id}`, `/cache`,
`/settings` и `/viewed set/list/rem`. `echo` эмулирует MatriX.147.1; настоящий
номер плагина доступен через RPC. `stat` числовой, preload отвечает пустым 200,
viewed сохраняет timecode и возвращает JSON; пустой viewed list — `[]`, не `null`.
File id **1-based**, без pad-файлов, включая пустые файлы. Для metadata-pending
magnet add возвращает явное ожидание и пустой file list, не выдуманные пути.

Новые add без `save_to_db` временные и опубликованные; upload по умолчанию
постоянный. Каждый новый экземпляр получает UUID-подкаталог download_root
(default: `deluge-stream` внутри native download location). Existing add не
перемещает данные, не дублирует native торрент и не понижает постоянство.
`rem` сохраняет payload и отклоняется при защите передачи/debounce. `drop/wipe`
ничего не удаляют. Пересечения SQLite и native add не являются одной транзакцией:
незавершённый add intent после crash не переигрывается; при неполном commit native
торрент может остаться постоянным/неопубликованным, пустой UUID-каталог — остаться.
Уже принятые изменения не откатываются отключением HTTP-клиента.

Источники: v1 magnet/hex/base32 infohash, HTTP(S) `.torrent`, ограниченные redirects
и текстовый magnet. V1/v2/hybrid `.torrent` поддержаны. **V2-only btmh magnet
не поддержан Deluge 2.2.0**, возвращается явная ошибка, второй движок не запускается.
HTTP sources могут обращаться к LAN; поэтому не давайте доступ к порту недоверенным
клиентам. Собственные HTTP access logs не пишут query/источники/токены; native
DEBUG-логи Deluge следует отдельно проверять и редактировать перед публикацией.

Reader ждёт native `have_piece`, читает через `read_piece_alert` и проверяет размер
и экземпляр. Один native read обслуживает несколько ожидающих клиентов. Смена
позиции внутри одного GET передвигает агрегированное окно; остановка клиента не
снимает deadlines другого. Preload — disk-backed задача с абсолютным timeout,
дубликаты того же файла объединяются; чтение payload в RAM для preload не нужно.
HEAD/stat/cache/settings/playlist не создают playback и не продлевают debounce.
«Стриминг» — наблюдаемые GET-запросы, не точное число людей и не фоновая загрузка.

Default окно и preload — 20 MiB; playback cap 8, preload cap 2, payload budget
64 MiB. Есть дополнительные hard caps: 128 sessions, 16 native read jobs,
4096 агрегированных missing-piece deadlines, 10000 файлов и 2 MiB имён metadata,
32 HTTP connections/requests, 16 MiB входной `.torrent`, 32 MiB общей входной
памяти, 8 multipart parts, 8 MiB control response, 2 HTTP source fetches и 4 add.
Слишком большой piece/window отклоняется с `buffer_limit`/`deadline_limit`;
увеличение логического окна не означает выделение RAM под весь фильм. Бюджет
пессимистично учитывает alert-copy, удержанный chunk и ограниченную HTTP-очередь;
он не ограничивает RSS всего Deluge, native cache, metadata или kernel buffers.

`get_diagnostics(1)` показывает `http_error` (включая `bind_failed`) и streaming
counters, reservations, ожидания, deadlines, last error и последнюю измеренную
reactor lag. Web JSON отдаёт проверенное подмножество этих полей. `get_status`
и status fields содержат activity/protection/waiting pieces. UI фазы 4 описан ниже.
Смена bind/порта/контекста закрывает старые соединения; ошибка bind не ломает
daemon. Повторный set_config позволяет повторить bind после устранения конфликта.

Тесты подтверждают HTTP-байты локального fixture, не декодирование настоящего видео.
Клиентские сценарии — [чеклист](docs/client-acceptance.md).

## Web UI и скачивание готовых данных

Установите один и тот же egg в core и отдельный deluge-web тестового стенда,
включите Stream и подключитесь к daemon. В **Preferences → Stream** администратор
может изменить все настройки из `config.py`. Неверные значения не применяются;
RPC-ошибка показывается безопасным сообщением. При несовместимых частях или
недоступном core функции Stream недоступны, штатный Deluge продолжает работать.

1. Выберите торрент и вкладку **Stream** рядом с Files. Она показывает временность,
   FIFO-ключ, публикацию, защиту и причину отсрочки cleanup. Счётчики «Стриминг»
   (HTTP playback), «Предзагрузка», «Скачивание файла» и «Скачивание ZIP» раздельны;
   статусы опрашиваются раз в секунду. Это не точное число зрителей.
2. В контекстном меню торрента откройте **Stream**: сделать постоянным/временным,
   открыть/закрыть TorrServer или скачать ZIP. Перевод во временный требует
   подтверждения, поскольку разрешает удаление данных. Для нескольких выбранных
   торрентов (до 128) результат выводится отдельно по каждому, включая ошибки.
3. Во вкладке Stream выберите готовый файл и **Скачать файл**. Либо выберите один
   файл в штатной **Files**, откройте контекстное меню и **Скачать файл через Stream**.
   Пункт **Приоритеты Deluge** сохраняет доступ к штатному меню выбора файлов.
   Списки Stream постраничные, по 128 файлов; стрелки переключают страницы.
4. **Скачать ZIP** доступно только после готовности всех payload-файлов, в том числе
   «Не скачивать». Pad/служебные файлы не включаются, пустые файлы включаются.
   Действие не снимает паузу/skip и не инициирует догрузку.
5. Для опубликованного файла **Ссылка воспроизведения** показывает URL для
   копирования. Это playback URL, не приватный download ticket.

Downloads не требуют публикации в TorrServer. Доступ проверяется в авторизованном
RPC, затем снова при HTTP GET и чтении: account/права, record_id, native readiness,
пути и состояние экземпляра. Shared-доступ разрешает чтение, но не изменение policy.
Готовые файлы можно скачать на паузе и со skip. Файловая система deluge-web не нужна:
браузер получает файл/ZIP **напрямую с core**, без Base64/Blob всего файла/JSON proxy.
Этот путь проверен Chromium 156.0.8078.47, в том числе в двух контейнерах без общего
payload-диска; это не проверка всех браузеров или конкретного NAS.

Нужны `http_enabled=true` и `public_base_url`, **доступный с устройства браузера**.
Если Web UI открыт по HTTPS, используйте доступный HTTPS reverse proxy для core,
иначе браузер может заблокировать mixed-content download. Адрес deluge-web не
подменяет адрес core. При ошибке ссылки проверьте DNS/порт/firewall/proxy, bind и
`stream.get_diagnostics(1).http_error`; исправьте URL и нажмите скачать заново.
Не публикуйте ticket URL или DEBUG-логи с ним. Собственный HTTP access log отключён.

Ticket по умолчанию действует 60 секунд до начала/повторного GET, не сохраняется
в SQLite или после restart. HEAD не продлевает срок и не превращает reservation
в активную передачу. Один ticket допускает до 16 последовательных GET; одновременный
GET тем же ticket отклоняется. File single Range поддерживает возобновление в срок
ticket; после истечения получите новую ссылку. Уже активный GET не прерывается
из-за истечения start TTL, но ограничен idle timeout и правами текущего экземпляра.

ZIP64 STORE выдаётся потоково, без второго полного архива на диске. **ZIP resume
отсутствует:** Range игнорируется, ответ 200 с `Accept-Ranges: none`. При ошибке,
move/recheck/remove или внешнем изменении исходного файла передача прерывается;
частичный ZIP не следует считать готовым архивом. Начните загрузку заново после
устранения причины. Нулевые файлы без физических данных не создаются ради ZIP.

Кроме общего payload budget есть hard limits: 128 tickets, 4 preflight jobs,
100000 native entries и 2 MiB путей, до `max_zip_entries` (не больше 100000)
ZIP-записей вместе с каталогами; 32 MiB плана имён и отдельный суммарный
консервативный metadata budget 64 MiB. Слишком глубокие/длинные пути, symlinks,
traversal и неоднозначные нормализованные имена отклоняются до начала ZIP.
Эти пределы не являются ограничением RSS всего Deluge. `get_diagnostics(1)`
показывает число tickets/preflight и `metadata_bytes_reserved` отдельно от payload.

## FIFO, передачи и незавершённое удаление

`max_temporary_torrents` по умолчанию **3**. При превышении выбирается только
самый старый временный торрент по сохраняемому FIFO-ключу. Постоянные в счётчик
не входят. Повторный временный статус не двигает FIFO; постоянный → временный
получает новую позицию. Смена статуса сама по себе не перемещает файлы и не
меняет pause/priorities.

Playback, preload, file, ZIP, ticket reservation и debounce защищают старейшего:
очередь ждёт, а не удаляет более новый торрент вместо него. Допускается временное
превышение лимита. Фоновая загрузка, сидирование, HEAD и stats защитой не являются.
HTTP playback/preload и file/ZIP download handlers используют этот API. Счётчик
`active_stream_requests` — число playback-запросов, не людей.

После освобождения защиты очистка повторяется. Ошибки preflight повторяются
не более трёх попыток за цикл, с интервалом `cleanup_retry_seconds` (не меньше 5 с);
новое событие политики/каталога/освобождения может начать новый цикл. Нехватка
места, размер файлов и возраст сами по себе не вызывают удаления.

Удаление выполняет Deluge с данными. `True`/TorrentRemovedEvent не равно
завершению: плагин ждёт matching native deletion alert (до 30 с).
Известные shared paths, symlinks и traversal блокируют опасную операцию, но
плагин не даёт дополнительных гарантий против параллельных native rename/move,
повторного add и внешних изменений диска сверх гарантий самого Deluge.
Не изменяйте размещение и не используйте общие payload-пути во время удаления.

Ошибка/timeout оставляет journal в `manual` и останавливает дальнейшую очередь.
После restart `prepared` отменяется; `submitting/submitted` проверяется только
на отсутствие native торрента и сохранённых файлов. Неоднозначность остаётся
ручной: плагин **никогда не удаляет остатки по сохранённым путям самостоятельно**.
Администратор может вызвать RPC:

```python
client.stream.get_cleanup(1)
client.stream.resolve_removal(1, operation_id, "verify")
client.stream.resolve_removal(1, operation_id, "acknowledge")
```

`verify` закрывает журнал только если native торрент и плановые файлы отсутствуют.
`acknowledge` явно признаёт ручное разрешение и снимает блокировку журнала,
не удаляя файлов; администратор сам решает судьбу остатков. Если старый торрент
ещё существует и остаётся временным, очистка после снятия блокировки может
выбрать его снова. Это отдельное действие от подтверждения каталога после crash.
Ручное удаление через обычный Deluge не подчиняется защите leases плагина.

## Политика, перезапуск и подтверждение после аварии

| Действие | Результат |
| --- | --- |
| Обычный stop/start deluged | Сохраняются record_id, временность, FIFO, публикация и metadata/viewed |
| Явно отключить Stream в Plugins | Сбрасываются экземплярные связи; при включении торренты постоянные и неопубликованные, новые record_id |
| Crash/SIGKILL | Временность/FIFO сохраняются, но прежняя публикация и автоочистка заблокированы до подтверждения администратором |
| Remove/re-add при работающем плагине | Новый record_id, без наследования прежней временности и публикации |

После crash страница Stream показывает необходимость подтверждения. Даже если
затем штатно перезапустить daemon, это требование не пропадёт. Сначала проверьте
текущий каталог: администратор подтверждает применение сохранённой политики
**к нынешним торрентам**, а не автоматическое доказательство их идентичности.
Ненужную временность можно предварительно снять. Hardlinks на служебные файлы
или payload для политики не требуются. [Обоснование решения](docs/instance-identity-decision.md).

Помимо кнопок Web UI доступны RPC namespace `stream`, версия API **1**:

| Метод | Параметры после api_version | Доступ |
| --- | --- | --- |
| get_config / set_config | — / changes | Администратор |
| get_status | Список до 128 Deluge torrent id | Владелец/admin, shared — чтение |
| set_retention | torrent_id, record_id, boolean temporary | Владелец/admin; проверка безопасных путей |
| set_published | torrent_id, record_id, boolean published | Владелец/admin; true недоступен при recovery_required |
| publish_existing | hash_alias, boolean save_to_db | Владелец/admin; публикует существующий torrent, не создаёт новый и не понижает постоянство |
| get_download_info | torrent_id, record_id, offset=0, native_index=null | Доступный торрент; страницы по 128 payload-файлов, ready и общий zip_ready |
| create_download_ticket | torrent_id, record_id, kind=file/zip, file_id=1 | Доступный готовый файл/ZIP; краткоживущая capability, не binary data |
| get_diagnostics | — | Авторизованный пользователь; recovery_token только admin |
| confirm_recovery | Актуальный recovery_token | Администратор, повторная проверка каталога и путей |

Через консоль разработчика **уже авторизованного штатного Web UI**, после
подключения к daemon, можно запросить настройки и изменить лимит:

```js
deluge.client.stream.get_config(1, {success: console.log, failure: console.error});
deluge.client.stream.set_config(1, {max_temporary_torrents: 3},
    {success: console.log, failure: console.error});
```

Для работы с торрентом сначала get_status(1, [torrent_id]), затем используйте
возвращённый record_id в set_retention/set_published. Устаревший record_id
отклоняется. В ответе `published_requested` — сохранённое желание публикации,
а `published` — эффективное разрешение с учётом recovery gate.

После crash администратор получает get_diagnostics(1) и, **проверив каталог**,
явно вызывает confirm_recovery(1, recovery_token). Между чтением token и
подтверждением каталог не должен измениться; иначе вернётся operation_conflict.
RPC не передаёт payload и не требует пароля Deluge в URL. Backend-вызовы проверены
реальными daemon/Web JSON тестами; вставка примеров в пользовательский браузер
отдельно не автоматизирована.

Полная схема настроек — [deluge_stream/config.py](deluge_stream/config.py).
Применяются policy-, HTTP- и streaming-настройки. `torrent_owner` проверяется по
аккаунтам daemon; пока он пуст, TorrServer-запросы данных отклоняются без контекста
владельца. Download/ZIP с авторизованными tickets не требуют этого контекста или
публикации торрента. Исследовательский
loopback-probe отдельный, с эфемерной admin-авторизацией.

Ошибочная настройка отклоняется целиком. Неизвестная/повреждённая SQLite выключает
функции плагина, но не заменяется пустой БД и не останавливает Deluge. Проверка
временности консервативна: symlinks, hardlinks payload и пересекающиеся пути
отклоняются. Текущие предохранительные пределы — 4096 торрентов каталога, 10000
native file entries в проверке путей, 32 DB-работы/управляющих операции; превышение
не маскируется успехом. Если metadata ещё не готовы, временность не включается.

## Как устроена разработка

- [Техническое задание](SPEC.md) — поведение и приёмочные сценарии A01–A26.
- [Правила работы агентов](AGENTS.md) — последовательные фазы, проверки и отчётность.
- [План и статус фаз](tasks/README.md) — этапы 0–5 и обязательный Makefile/CI/CD.
- [Отчёты](tasks/implementation/README.md) — что выполнено и чем проверено.

Завершены [фаза 0](tasks/phase0-feasibility-and-tooling.md), [фаза 1](tasks/phase1-core-persistence-and-ci.md), [фаза 2](tasks/phase2-retention-and-transfer-lifecycle.md) и [фаза 4](tasks/phase4-downloads-zip-and-webui.md). [Фаза 3](tasks/phase3-torrserver-and-streaming.md) в работе: HTTP/streaming реализованы, приёмка Lampa/Num не выполнена. Такой порядок прямо разрешён пользователем. [Фаза 5](tasks/phase5-packaging-and-release.md) в работе; LICENSE отложен пользователем.
