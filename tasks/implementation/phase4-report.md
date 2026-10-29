# Отчёт: фаза 4 — downloads, ZIP и Web UI

Статус: **Завершена**. Основание — [план фазы 4](../phase4-downloads-zip-and-webui.md),
[SPEC](../../SPEC.md) §§8–12, 14; прочитан [отчёт фазы 3](phase3-report.md).

Пользователь прямо разрешил переход до полного DoD фазы 3. Отложенная
самостоятельная приёмка Lampa/Num и остальные проверки фазы 3 не отменены.
Ни готовность клиентов, ни готовность всего продукта здесь не объявляются.

## Реализованное поведение

- Авторизованные RPC `create_download_ticket` и `get_download_info` (API 1).
  Списки содержат external id, native index, путь, размер, native readiness и
  playback URL при доступной публикации; страницы по 128 файлов. Настройки — admin,
  policy/publication — owner/admin; shared даёт чтение, не изменение policy.
- Криптографические capabilities, не сохраняемые в SQLite: 128 tickets,
  `max_pending_download_tickets` reservations, до 16 последовательных GET и один
  активный GET на ticket. Назначение file/ZIP, пользователь и экземпляр фиксированы.
  Reservation предшествует preflight; при выдаче TTL lease и ticket стартуют вместе.
  HEAD срок не продлевает. Активный GET переживает start TTL, но имеет idle timeout.
  Ticket → file/ZIP admission использует policy lock без разрыва FIFO-защиты.
- Готовность определяется native have_piece, не размером/sparse/wanted progress.
  Файл не требует готовности соседних файлов. ZIP проверяет все payload, включая
  skipped; pads исключаются, пустые файлы включаются. Downloads не создают deadlines,
  playback или скрытую догрузку; verified данные читаются при pause/skip.
- Direct core HTTP GET/HEAD, single Range файла, Content-Disposition с UTF-8;
  ZIP64 STORE с incremental CRC, descriptors и central directory. ZIP Range
  игнорируется (200, Accept-Ranges: none), полный архив не сохраняется.
- Metadata paths/collisions проверяются до выдачи; symlink components и изменение
  файлов — worker fingerprints до/после native reads и перед финалом ZIP. Ошибка
  до тела даёт безопасный JSON; после начала ZIP соединение обрывается, без
  успешного завершения повреждённого архива. Причина сохраняется в diagnostics/log.
- Auth/доступ и native instance перепроверяются, включая отзыв shared/admin/account
  и delete/re-add. Pause не отменяется; recheck/move/remove прерывают передачу.
  Native alerts дополняют periodic guard: быстрый recheck нельзя пропустить между
  тиками. Matching handle не переносит старое событие на новый экземпляр.
- Payload использует общий budget reader с дополнительным HTTP framing reserve.
  Отдельно ограничены 4 preflight jobs (timeout не больше 60 с), 100000 native entries,
  2 MiB путей, 32 MiB плана ZIP имён, max_zip_entries с каталогами и суммарный
  консервативный metadata budget tickets 64 MiB. Это не лимит RSS Deluge.
- Preferences → Stream, панель Details → Stream, torrent menu с batch-результатами
  и подтверждением временности, download в штатной Files и копирование playback URL.
  Наблюдаемые playback/preload/file/ZIP и причины защиты не заменяют native state.
  RPC-ошибки и bind_failed показаны безопасными сообщениями. Медленное получение
  списка файлов не блокирует секундный опрос состояния. Binary не проходит через
  JSON/Base64/Blob всего файла или файловую систему deluge-web.

## Изменённые файлы

| Файлы | Назначение |
| --- | --- |
| `deluge_stream/downloads.py` | Tickets, preflight/readiness, fingerprints, caps и metadata accounting |
| `deluge_stream/download_http.py`, `archive.py` | HTTP producer, backpressure/error/cancel, incremental ZIP64 |
| `deluge_stream/core.py`, `torrserver.py` | RPC и маршрутизация приватных downloads независимо от TorrServer owner/publication |
| `deluge_stream/streaming.py`, `stream_adapter.py`, `transfers.py` | Admission reservation, отсутствие download QoS, invalidation alerts, однократный старт TTL |
| `deluge_stream/policy_adapter.py` | Текущие auth-права и native moving_storage вместо исторического destination |
| `deluge_stream/data/stream.js`, `webui.py` | Настройки, действия, меню, панель и безопасные ошибки |
| `tests/unit/test_archive.py`, `test_download_tickets.py`, `test_streaming.py` | Формат/пути, TTL/quota/cancel, native error/invalidation и no-download-QoS |
| `tests/runtime/downloads.py`, `web_browser.py`, `split_node.py` | Установленный artifact, native HTTP/FIFO/fault и настоящий Chromium |
| `tools/zip_probe.py`, `split_web_check.py` | Независимая проверка большого product ZIP writer; два контейнера без общего payload FS |
| `Makefile`, `.github/workflows/checks.yml` | test-downloads/test-web в check, отдельный split UI шаг в CI |
| `requirements-dev.txt`, `constraints.txt`, `tools/runtime.Dockerfile` | Playwright 1.56.0, закреплённые зависимости, системный Chromium |
| `README.md`, `docs/architecture.md`, файлы фаз/индексов и этот отчёт | Действия пользователя, ограничения, согласованный порядок фаз |

## Definition of Done

- [x] Готовый отдельный файл скачивается с корректными байтами, именем и Range; неготовый отклоняется.
  Native closed/suffix Range, HEAD, пустые файлы, Chromium Unicode download;
  A17 проверяет готовый файл рядом с неготовым skipped/sparse файлом.
- [x] Готовность ZIP учитывает все payload-файлы, включая skipped, и не вызывает догрузку.
  Native v1/v2/hybrid и mixed fixture с 100% wanted, но неготовым skipped файлом.
- [x] ZIP64 проверен по составу/CRC, большим размерам и числу записей без полного промежуточного архива.
  Native HTTP ZIP и независимый zipfile; product writer с виртуальным sink для
  >4 GiB и 65537 записей. Это не HTTP-прогон настоящего четырёхгигабайтного файла.
- [x] Tickets, reservations, отмена и защита от cleanup работают с настоящими file/ZIP-передачами.
  Native slow sockets, TTL/reuse, pending reservation → два GET, release одного,
  pin и восстановление FIFO-порядка, native cleanup только после release обоих.
- [x] Web UI позволяет управлять настройками, постоянством и публикацией.
  Chromium: settings/save/error/bind conflict/retry, публикация и обе смены retention.
- [x] «Стриминг», preload и downloads отображаются раздельно и своевременно обновляются.
  Native playback/preload counters в общей регрессии; Chromium видит одновременно
  активные file/ZIP, их независимое завершение и отсутствие ложного «Стриминг».
- [x] Web UI работает отдельно от daemon без общего диска; права и доступность URL проверены.
  Два контейнера с internal network, общий только read-only artifact/test code;
  payload существует лишь в core. Chromium скачивает file/ZIP с core URL.
- [x] make test-web/test-downloads включены в make check и CI; проверки проходят.
  ARM64 и x86_64 полные check Passed. Hosted CI не запускался.
- [x] README содержит проверенные пользовательские сценарии и ограничения downloads.
  Описаны actual UI действия, URL/core доступность, готовность, tickets и отсутствие ZIP resume.
- [x] Отчёт и индекс фаз обновлены; A17–A24 подтверждены с указанием среды.
  Проверенные части и ограничения перечислены ниже.

## Среда и точные проверки

Debian 12 Docker, Python 3.11.2, Deluge 2.2.0, libtorrent 2.0.8.0, Twisted 24.11.0,
SQLite 3.40.1, setuptools 75.8.0; Chromium 156.0.8078.47, Playwright 1.56.0.
Host macOS ARM64, Linux x86_64 — эмуляция. Профили одноразовые; пользовательский
Deluge не менялся. Обычные runtime checks — `--network none`; split — собственная
internal Docker network, без публичного swarm. Сборки запускались последовательно:
обе архитектуры используют общий `dist/Stream-0.0.1-py3.11.egg`.

```sh
docker build --platform linux/arm64 -t deluge-stream-phase4:arm64 -f tools/runtime.Dockerfile .
docker build --platform linux/amd64 -t deluge-stream-phase4:amd64 -f tools/runtime.Dockerfile .
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase4:arm64 make check VENV=/opt/venv
make test-web-split CI_IMAGE=deluge-stream-phase4:arm64 CI_PLATFORM=linux/arm64
docker run --rm --platform linux/amd64 --network none -v "$PWD:/work" deluge-stream-phase4:amd64 make check VENV=/opt/venv
```

| Проверка | Результат |
| --- | --- |
| ARM64 полный финальный make check | Passed: 185 unit, 87 persistence, все прежние native suites, downloads, ZIP64, Chromium и core/web smoke |
| x86_64 полный make check | Passed: 185 unit, 87 persistence, все native suites, downloads, ZIP64, Chromium и core/web smoke; эмуляция на ARM64 host |
| ARM64 split Web UI | Passed на текущем artifact: Chromium file/ZIP через core, отсутствие payload FS в web контейнере |
| Большой product ZIP writer | Passed: payload 4294967419 байт, 65537 записей, виртуальный архив 4306610901 байт, retained headers 11643482, max write 1048576; CRC проверен независимым zipfile |
| Hosted GitHub Actions | Not run; подготовленный workflow не объявляется успешным hosted run |
| Lampa/Num нового backend | Отложены пользователем, относятся к незакрытой фазе 3 |

После финальной сверки документов выполнены
`docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase4:arm64 make check-docs lint format-check VENV=/opt/venv`
(Passed, 28 Markdown/82 Python-файла), `git diff --check` и
`node --check deluge_stream/data/stream.js` (Passed). Полная регрессия повторно
не запускалась после исключительно документальных изменений.

Значимые промежуточные сбои не скрыты: первоначальные browser tests обнаружили
гонку deferred clear панели, порядок lazily registered Files menu и native
Preferences.removePage с пустой selection. Исправлены адаптацией UI, без monkey patch.
Тесты также уточнены: default-daemon/dialog, видимый OK вместо ambiguous hidden
button и ожидаемый ConnectionResetError при оборванном ZIP.

Расширенный native тест обнаружил пропуск быстрого recheck между polling ticks:
добавлены matching state/path alerts. Затем обнаружено, что Deluge 2.2.0 не
обнуляет `moving_storage_dest_path` после переноса; рабочее состояние теперь берётся
из `status.moving_storage`. После исправлений native recheck/move/remove и полный
ARM64 check прошли. Ранние lint/format ошибки и пропущенный `VENV=/opt/venv` исправлены,
не превращались в успешные skips.

Один повторный split-run завершился ошибкой DNS имени `core`; корневая причина
не локализована. Добавлен вывод core logs при отказе, следующий запуск без изменений
продукта прошёл. Это не основание объявлять первый отказ успешным.

## Сценарии из SPEC

| ID | Подтверждённая часть | Граница проверки |
| --- | --- | --- |
| A17 | Готовый a.bin скачивается, ZIP с неготовым b.bin отклоняется; Range и точные bytes | Изолированный native fixture, не пользовательское видео |
| A18 | Полноразмерный sparse skipped b.bin не принимается за готовый при 100% wanted | Плагин не включает файл/не создаёт deadlines |
| A19 | Native v1/v2/hybrid ZIP: directories, empty, pads excluded, bytes/CRC; Unicode Chromium; product writer >4 GiB/65537 | Большие размеры/число записей — виртуальный sink, не большой native HTTP torrent |
| A20 | Настоящие не читающие sockets file/ZIP, budget, start TTL при активном GET, disconnect/zero reservations | Не длительный многодневный NAS load test |
| A21 | Native ready pause/skip, быстрый recheck, move, remove/re-add; fault disk_full до тела и после ZIP header | Disk full — управляемая fault injection, не заполненный физический диск |
| A22 | Worker symlink/replace и нормализованные path collisions/traversal; native ownership/shared-path regression прежних фаз | Полная adversarial гонка внешнего filesystem mutator не заявляется |
| A23 | Invalid/expired/stale ticket, unrelated user, shared read + revoke, account revoke; anonymous web RPC regression | Ticket — bearer capability, не дополнительная HTTP password-auth |
| A24 | Chromium + раздельные core/web процессы и два контейнера, direct URL, отсутствие общего payload FS | HTTPS proxy/реальный NAS и другие браузеры не проверены |
| A10/A13/A14 | Реальный oldest reservation/file/ZIP, pin, release одного/обоих и cleanup retry; stale same-hash ticket | Согласованные native гарантии удаления, не filesystem barrier |
| A26 | Read fault disk_full → 507 либо обрыв начатого ZIP; нового free-space cleanup trigger нет | Реальный ENOSPC файловой системы не воспроизводился |

## README, Makefile и CI/CD

`test-downloads` запускает installed-artifact native HTTP и product ZIP writer probe;
`test-web` — настоящий Chromium с installed core/web. Обе цели входят в `test/check`.
`test-web-split` — отдельная Docker-only проверка после `make ci-check` в checks.yml,
поскольку обычный `make check` выполняется внутри контейнера без Docker socket.
Логика тестов не дублируется в workflow. Runtime image включает Chromium; отсутствие
обязательной зависимости приводит к ошибке. README и архитектура приведены к
реализованному поведению, включая trusted LAN, HTTPS/mixed-content и ticket limits.

## Ограничения и следующий шаг

- DoD фазы 4 выполнен в указанном объёме проверок. Остаточные native piece
  priorities и отложенная Lampa/Num приёмка фазы 3 не переопределены.
- Tickets ограничены одним активным соединением и 16 GET, без возобновления ZIP;
  новые ограничения ресурсов документированы, а не скрыты успешным ответом.
- Lstat/fingerprints — проверки известных состояний, не filesystem isolation от
  внешнего привилегированного mutator. Hash verification и re-download остаются native.
- Реальный NAS/HTTPS proxy, большие HTTP-файлы и продолжительная нагрузка не
  объявляются проверенными по виртуальному ZIP или локальному Chromium.
- Следующий этап — фаза 5, только по отдельной команде пользователя; она не начата.
  Пользовательских установок, commit/push, публикаций и тегов не выполнялось.
