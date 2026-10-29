# Отчёт: фаза 3 — TorrServer и streaming

Статус: **В работе**, 29 октября 2026 года. Предпосылка: [фаза 2](phase2-report.md).
Основание: [план](../phase3-torrserver-and-streaming.md), [SPEC](../../SPEC.md).
Обязательная клиентская приёмка пока недоступна; фаза не завершена.

## Что сделано

- Встроенный HTTP listener: TorrServer add/list/get/set/rem, upload, file list,
  playlist, play/preload/stat/cache/settings/viewed. Numeric DTO проверяются
  схемами fixtures; preload возвращает пустой 200. Settings set/def и drop/wipe
  безопасные no-op, rem сохраняет данные и учитывает активную защиту.
- Проверяются configured owner, native access и effective publication. После
  crash прежняя публикация закрыта до admin confirmation; обычный restart
  сохраняет её. Existing add не дублирует экземпляр и не понижает постоянство.
- HTTP(S) источники с ограниченными redirects, текстовые magnets, v1 hex/base32,
  v1/v2/hybrid torrent metadata, UUID download directories. Metadata-pending
  magnet имеет явное состояние ожидания. SQLite schema 6 атомарно фиксирует
  policy/metadata/source intent; native add и SQLite не являются одной транзакцией.
  Незавершённые intents после crash помечаются abandoned, а не переигрываются.
- Async native reader, агрегированные moving deadlines, shared native read для
  нескольких клиентов, независимые chunks/reservations, timeout/cancellation.
  Один длинный GET двигает окно без новых Range. Готовность определяет native,
  а не размер/sparse-файл. Проверяются размер read и актуальный экземпляр.
- Готовые данные читаются при torrent/session pause и skip. Для отсутствующих
  данных возвращается blocked error, без resume/изменения пользовательского выбора.
  Preload disk-backed, coalesced, с абсолютным timeout и отдельной lease.
- Ограничены sessions, native jobs, deadlines, буферы, входные тела, source fetches
  и add jobs. HTTP backpressure останавливает producer. Отменённый уже выданный
  read сохраняет слот и reservation до matching alert; orphan payload не копируется.
  Close идемпотентен и очищает runtime accounting.
- Listener reconfiguration/quiescing, bind diagnostics, RPC/status fields и
  whitelisted web diagnostics. HEAD/stat не создают playback/debounce.
  Research probe сохранён отдельно; одновременная payload-работа с product reader
  запрещена, чтобы не обходить общий бюджет.

Прежний QoS-блокер снят пользователем. [История](phase3-history.md) сохраняет
native probes, но требование собственного recovery journal больше не актуально.
Свои deadlines агрегируются/снимаются; residual native piece priorities допустимы.
File priorities/skip/first-last/pause/queue не меняются; payload/recheck остаются Deluge.

## Изменённые файлы

| Файлы | Назначение |
| --- | --- |
| `deluge_stream/streaming.py`, `stream_adapter.py` | Reader, окна, native alerts, preload, budget/cancellation |
| `deluge_stream/torrserver.py`, `torrents.py`, `sources.py` | HTTP/producer, DTO/access, source acquisition/add |
| `deluge_stream/core.py`, `lifecycle.py`, `policy_adapter.py`, `adapter.py` | Startup/quiescing, policy admission, diagnostics, exclusion с probe |
| `deluge_stream/repository.py` | Schema 6 и source-add intents |
| `deluge_stream/webui.py`, `tests/runtime/web_contract.py` | Whitelisted streaming diagnostics |
| `tests/unit/test_streaming.py` | Shared reads, окна, blocked readiness, cancellation/timeout/budgets |
| `tests/persistence/test_source_adds.py` | Реальная SQLite: atomic commit, rollback, restart/abandonment, caps |
| `tests/runtime/streaming.py`, `streaming_lifecycle.py` | Установленный egg, HTTP/native seed, actual daemon restart/SIGKILL |
| `tools/runtime_check.py`, `Makefile` | Product suites в обязательном make check; актуальный help |
| `README.md`, `docs/architecture.md`, `docs/client-acceptance.md` | Настройка кандидата, архитектура, незакрытая клиентская приёмка |
| `SPEC.md`, `tasks/README.md`, `tasks/phase3-torrserver-and-streaming.md`, `tasks/implementation/README.md` | Согласованный QoS-контракт и фактический статус |

Phase-2 retention/deletion/transfers и их тесты также остаются в незакоммиченном
дереве; они описаны в предыдущем отчёте, не объявлены новой работой фазы 3.

## Статус Definition of Done

- [ ] Требуемый TorrServer-контракт реализован и проверен fixtures и реальными Lampa/Num.
  HTTP/schema проверки есть; полного реального Lampa/Num playback ещё нет.
- [ ] Незавершённый файл воспроизводится; Range/HEAD/seek возвращают корректные статусы, заголовки и байты.
  Binary fixture читается побайтово до полной загрузки; декодирование видео не проверено.
- [x] Окно pieces продвигается в одном длинном GET; отмена прекращает ненужную работу.
  Native suite проверяет движение окна; unit-тесты отмены сохраняют quota
  неотменяемого native read до alert, новые reads не создаются бесконтрольно.
- [x] Несколько playback/preload-сессий согласованно управляют QoS и защитой от cleanup.
  Shared-read/deadline union unit tests, два incomplete HTTP reader, coalesced
  native preload и общий lease registry/FIFO regression.
- [x] Пользовательские priorities/pause/queue сохраняются, включая disable и crash/resume.
  Native suites и actual-daemon live GET + fsynced fastresume + restart/SIGKILL;
  сравниваются file priorities/first-last/auto-managed, не numeric piece vector.
- [ ] Backpressure, лимиты буферов и неблокирующее выполнение подтверждены.
  Есть slow-socket/native и deterministic quota tests, bounded worker paths;
  полная нагрузочная матрица, большие pieces/файлы и fault injection ещё не закрыты.
- [x] Наблюдаемое состояние и диагностика доступны web-части через RPC/status fields.
  Runtime assertions и web reply whitelist; полный UI относится к фазе 4.
- [x] make check проходит; новые автоматические проверки включены в CI.
  ARM64 и x86_64 полные локальные check зелёные после quota-правки;
  suites вызываются существующим CI через make ci-check. Hosted run не выполнялся.
- [ ] README описывает проверенное подключение и ограничения streaming.
  Настройки кандидата и ограничения описаны; фактическое подключение клиентов не проверено.
- [x] Отчёт и индекс фаз обновлены; частичные и ручные проверки явно обозначены.

## Проверки

Среда: Docker Debian 12, Python 3.11.2, Deluge 2.2.0, libtorrent 2.0.8.0,
Twisted 24.11.0, SQLite 3.40.1, setuptools 75.8.0. Host macOS ARM64;
Linux x86_64 запускается через эмуляцию. Контейнеры с `--network none`,
seed и HTTP sources локальные, профили одноразовые; пользовательский Deluge не менялся.

Точные команды из корня проекта:

```sh
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make check VENV=/opt/venv
docker run --rm --platform linux/amd64 --network none -v "$PWD:/work" deluge-stream-phase0:amd64 make check VENV=/opt/venv
```

| Проверка | Результат |
| --- | --- |
| ARM64 полный check до последней quota-правки | Passed: 164 unit, 87 persistence, native/runtime, build, ZIP64, core/web smoke |
| ARM64 первый check после quota-правки | Failed: 167 tests passed, teardown повторно вызвал unregister; исправлено идемпотентным close |
| ARM64 финальный check | Passed: 167 unit, 87 persistence, все native/runtime suites, build, ZIP64 и core/web smoke |
| x86_64 финальный check | Passed: 167 unit, 87 persistence, все native/runtime suites, build, ZIP64 и core/web smoke; эмуляция на ARM64 host |
| Проверка документации после обновления отчёта | `docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make check-docs VENV=/opt/venv`: Passed, 27 Markdown-файлов |
| Lampa/Num нового backend | Not run: `adb devices -l` без устройств; emulator отсутствует в PATH; доступного стенда нет |
| Hosted GitHub Actions | Not run; локальный Docker check не объявляется hosted run |

Промежуточные native ошибки теста lifecycle (RPC возвращает tuple file priorities,
а не list) исправлены нормализацией assertion. Последующие отдельный и общий
ARM64 прогоны этого сценария прошли. Автоматические payload fixtures — binary,
не видео; HTTP 206 не доказывает совместимость decoder/UI.

## Приёмочные сценарии из ТЗ

| ID | Проверенная часть | Остаётся |
| --- | --- | --- |
| A02 | HTTP add/list/preload/play, DTO, URL redirect/text magnet, upload v1/v2/hybrid, pending metadata | Реальные версии Lampa/Num, декодирование видео, полный replay client fixtures |
| A03 | Parser unit tests, native closed/open/suffix Range, 416, HEAD без playback, точные payload bytes | Полная комбинационная HTTP-матрица заголовков/ошибок |
| A04 | Незавершённый локальный payload, движение окна в одном GET | Реальное видео и большие медиа |
| A05 | Разные HTTP offsets, moving deadlines и unit union/reset | Клиентский seek далеко вперёд/назад с видео |
| A06 | Два incomplete reader с разными offsets, независимое release/shared read | Два реальных плеера и длительная нагрузка |
| A07 | V1 общий граничный piece; v2/hybrid padding и полные точные байты файла | Расширенная гонка одновременного seek/close на границе |
| A08 | Native first-last change, file priority/skip, background download и сохранённый global limit | Широкая матрица concurrent user actions |
| A10 | Playback/preload используют проверенный FIFO/lease registry; активный rem отклонён | Дополнительные end-to-end cleanup races с новым HTTP; file/ZIP — фаза 4 |
| A16 | Live GET, фактический fastresume, stop/start и SIGKILL; state обнулён, viewed/options сохранены, publication требует confirmation | Расширенная матрица crash cuts product reader |
| A20 | Slow native socket/disconnect, unit cancel до/после issued read, caps/timeouts | Длительная нагрузка; file/ZIP HTTP — фаза 4 |
| A21 | Ready pause/session pause/skip; missing blocked; instance checks; research move/recheck/remove regression | Полный product fault injection disk/hash error/move/recheck/remove |
| A26 | Нет нового free-space/size/TTL cleanup trigger; error path source/SQLite fail-closed | Реальный ENOSPC при streaming/source add не проверен |

## README, Makefile и CI/CD

README описывает candidate listener/public_base_url, owner/publication, unsafe-LAN
границу, HTTP маршруты, limits, native QoS side effects и диагностику. Чеклист
клиентов вынесен в [docs/client-acceptance.md](../../docs/client-acceptance.md).
`tools/runtime_check.py` включает product suites в `make test-integration/check`.
`.github/workflows/checks.yml` уже вызывает `make ci-check`, который использует
тот же check; дублировать suites в YAML не нужно. Отдельного hosted run нет.
Сборка/проверки устанавливают egg только в изолированные тестовые профили.

## Отклонения, ограничения и следующий шаг

- По указанию пользователя residual native piece priorities допустимы: journal,
  нормализация vector и восстановление snapshot не нужны. A16 и планы приведены
  в соответствие этому решению; file priorities/pause/queue по-прежнему защищены.
- V2-only btmh magnet не принимается native Deluge 2.2.0; v2/hybrid torrent files
  поддерживаются. Второй движок/обход native validation не добавлен.
- Source add не является распределённой транзакцией: после crash native torrent
  может остаться постоянным/неопубликованным; пустой UUID-каталог не удаляется
  автоматически. Recovery intents не повторяют опасную mutation.
- Не выполнены приёмка Lampa/Num, полная fault/нагрузочная матрица и ENOSPC.
  Не заявляется готовность фазы 3 или всего продукта. Впоследствии пользователь
  прямо разрешил начать фазу 4 до полного DoD фазы 3; проверки не отменены.
- Пользователь подтвердил, что выполнит приёмку Lampa/Num самостоятельно позже.
  Это отложенная проверка, не отмена клиентского gate и не успешный результат.
- Следующий шаг: закрыть оставшиеся fault/нагрузочные проверки фазы 3;
  результаты пользовательской приёмки по чеклисту ожидаются отдельно.
  Ничего не отправлено в GitHub и не опубликовано; release/tag не создавались.
