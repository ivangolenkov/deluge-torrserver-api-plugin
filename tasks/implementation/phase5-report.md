# Отчёт: фаза 5 — поставка и финальная приёмка

Статус: **В работе**, 30 октября 2026 года.
Основание — [план фазы 5](../phase5-packaging-and-release.md), SPEC §§13–16;
прочитан [отчёт фазы 4](phase4-report.md).

Пользователь отложил создание LICENSE. Агент не назначает лицензию; локальная
разработка продолжается. Лицензия остаётся условием выпуска, не блокером этой
разработки. GitHub/NAS/клиентские условия полного DoD остаются открытыми.

## Реализовано

- Версия egg и runtime вынесена в один `_version.py`, описание пакета актуализировано,
  Python requirement ограничен подтверждённым 3.11. Make/runtime/split runner
  используют ту же версию, не независимые hardcoded имена.
- `candidate-check`: installed-artifact smoke, проверка ZIP/entrypoints/web resource,
  комплект egg + compatibility.json + SHA256SUMS + RELEASE-NOTES.md. LICENSE
  копируется только если непустой; отсутствующая лицензия не выдумывается.
  Manifest фиксирует commit/dirty/source fingerprint, runtime-контракт и checksum,
  но прямо отказывается от объявления приёмки.
- `release-check`: candidate + LICENSE/clean source/tag gates без публикации;
  `release-verify`: проверяет существующий artifact и smoke **без пересборки**.
  `test-packaging` сравнивает две clean-directory сборки и отдельно содержимое
  entries/полные hashes. ZIP timestamps не нормализуются, bit reproducibility
  не обещана; clean-directory текущего snapshot не называется clean Git checkout.
- Product Dockerfile, core/web Compose с раздельными профилями и payload только
  у core. Первоначальные профили создаются через Deluge Config; settings плагина
  задаются штатным UI/RPC и сохраняются в SQLite, не фиктивном stream.conf.
  Numeric UID/GID, gosu, приватный RPC secret, initial empty-volume ownership без
  recursive chown, writable egg cache, штатный SIGTERM и 60s grace period.
- `docker-build/up/down`, `test-docker`: случайный тестовый Compose project,
  временный secret, собственные volumes; down без -v для проверки recreation,
  удаляются только тестовые volumes в finally. Рабочий docker-down volumes не удаляет.
- Checks workflow расширен native x86_64/ARM64 runners, candidate и product Compose.
  Release workflow повторно использует checks commit, скачивает проверенный комплект,
  smoke/strict verify без rebuild; manual run не публикует. Write scope только
  publish job, environment release и отдельное подтверждение acceptance владельцем.
- Publisher создаёт draft, отказывается перезаписывать/менять content и чужие assets,
  сравнивает existing bytes, дополняет только draft и скачивает весь комплект для
  сверки перед finalize. Это код и unit-guards, **не реальный GitHub run**.

## Изменённые файлы

| Файлы | Назначение |
| --- | --- |
| `deluge_stream/_version.py`, `__init__.py`, `setup.py`, `tools/version.py` | Единая версия/точный Python contract |
| `tools/release_bundle.py`, `packaging_check.py`, `publish_release.py` | Комплект, clean-directory сравнение и guarded publisher |
| `tools/runtime_check.py`, `split_web_check.py`, `runtime.Dockerfile`, `tests/runtime/split_node.py` | Текущая версия artifact, Git для provenance |
| `Dockerfile`, `.dockerignore`, `compose.yml`, `docker/entrypoint.py`, `.gitignore` | Product candidate, persistent profiles, secret/UID/GID |
| `tools/docker_check.py`, `Makefile` | Реальный Compose recreation и локальные/CI цели |
| `.github/workflows/checks.yml`, `release.yml`, `tests/unit/test_ci.py` | Native matrix, checked-byte выпуск и статические guards |
| `tests/unit/test_release_bundle.py`, `test_publish_release.py` | Tampering/tag/license/asset-plan failures |
| `docs/delivery.md`, `compatibility.md`, README/SPEC/индексы/план и этот отчёт | Честные инструкции и открытые gates |

## Исходный Definition of Done

- [x] Поддерживаемая runtime-матрица подтверждена на Linux x86_64/ARM64; пакет устанавливается штатно.
  Полный check с установленным egg на обеих архитектурах; x86_64 локально эмулируется.
  Это не подтверждение native hosted runner или NAS.
- [x] Docker Compose и make docker-* работают; постоянные данные переживают пересоздание контейнеров.
  Product recreation прошёл на ARM64 и эмулируемом x86_64; команды/данные ниже.
- [ ] make check/release-check проходят; комплект содержит artifacts, checksums и manifest.
  Строгий release-check не может пройти без отложенного LICENSE; candidate проверяется отдельно.
- [ ] checks.yml автоматически проверяет согласованную матрицу, результаты реальных runs сохранены.
  Workflow подготовлен, hosted run отсутствует.
- [ ] release.yml собирает и проверяет commit тега, публикует plugin artifacts и поддерживает dry run.
  Подготовлен код; настоящий hosted dry run ещё не выполнялся.
- [ ] Успех и отказ публикационного сценария проверены; статус тестовой/публичной публикации указан отдельно.
  Unit asset-plan guards не подменяют remote draft-сценарий. Публикаций не было.
- [ ] Все A01–A26 и обязательные ресурсные проверки подтверждены; измерения NAS сохранены.
- [ ] README позволяет установить, настроить, собрать и протестировать проект по проверенным командам.
  Инструкция обновлена; полноценный clean checkout + новый пользователь/NAS пока не проверены.
- [ ] Release notes и инструкции обновления/восстановления соответствуют фактическому продукту.
  Candidate notes есть; offline backup/restore подтверждён дополнением ниже,
  обновление между двумя опубликованными версиями пока невозможно (релизов нет).
- [ ] Итоговый отчёт, индекс фаз и общий DoD в ТЗ обновлены без фиктивных отметок.
  Этот промежуточный отчёт не является итоговым.

## Среда и проверки

Host macOS ARM64, Docker Desktop Linux VM. Debian 12, Python 3.11.2, Deluge 2.2.0,
libtorrent 2.0.8.0, Twisted 24.11.0, SQLite 3.40.1, setuptools 75.8.0/wheel 0.45.1.
Тестовый образ с Git получен заново из APT; Chromium обновился до 156.0.8078.73.
x86_64 здесь эмулируется, не native hardware и не hosted runner.

```sh
docker build --platform linux/arm64 -t deluge-stream-phase5-check:arm64 -f tools/runtime.Dockerfile .
docker build --platform linux/amd64 -t deluge-stream-phase5-check:amd64 -f tools/runtime.Dockerfile .
docker build --platform linux/arm64 -t deluge-stream-phase5:arm64 .
docker build --platform linux/amd64 -t deluge-stream-phase5:amd64 .
make test-docker PRODUCT_IMAGE=deluge-stream-phase5:arm64 CI_PLATFORM=linux/arm64
make test-docker PRODUCT_IMAGE=deluge-stream-phase5:amd64 CI_PLATFORM=linux/amd64
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase5-check:arm64 make check candidate-check VENV=/opt/venv
docker run --rm --platform linux/amd64 --network none -v "$PWD:/work" deluge-stream-phase5-check:amd64 make check VENV=/opt/venv
```

| Проверка | Результат |
| --- | --- |
| Unit + installed smoke первого candidate | 196 unit Passed, daemon/core/web/20 cycles Passed; затем manifest отказал из-за отсутствия Git в прежнем test image |
| Новый ARM64 test image с Git | Build Passed |
| Product images ARM64/x86_64 | Build Passed; сборка не называется установочной приёмкой |
| ARM64 Compose recreation | Passed: temporary/publication/record_id/settings и точные download bytes после down/up, payload FS не монтируется web |
| x86_64 Compose recreation | Passed: те же проверки; эмуляция, не native x86_64 hardware |
| ARM64 полный check + candidate | Passed: 211 unit/87 persistence, все native suites, downloads/ZIP/Chromium 156.0.8078.73, installed core/web smoke, packaging и bundle |
| x86_64 полный check | Passed: 211 unit/87 persistence, native suites, downloads/ZIP/Chromium, installed smoke, packaging и candidate; эмуляция |
| Две ARM64 clean-directory сборки | Содержимое entries одинаково, полные hashes различны: 3ca29f7d3d9475cd78d88e0e470a426539875d92f719c617733b193d55e79dde / 784935c6b21044f5eb4abb988d0afa0280939dc6e7066f301e6b930912e99933; bit reproducibility не заявлена |
| Две x86_64 clean-directory сборки | Содержимое entries одинаково, hashes 631985939b743043212b5eb0fe1139675d6f487999255c8e8a8032af4c512a43 / b9226afebceaded11111a0df59c415ee82347c06e242072aee1626695c529339 различны |
| ARM64 split core/web после версии из metadata | Passed: `make test-web-split CI_IMAGE=deluge-stream-phase5-check:arm64 CI_PLATFORM=linux/arm64` |
| Строгая проверка без LICENSE | Ожидаемый отказ `BUNDLE FAILED: LICENSE is required for release readiness`; отказ проверен, не объявлен успешным release-check |
| Hosted checks/release dry run | Not run: remote отсутствует, конкретный GitHub доступ/разрешение не предоставлены |
| NAS/Lampa/Num | Not run: NAS не выбран; клиентскую приёмку пользователь ранее отложил на себя |

Промежуточные отказы: исправлены две E501 ошибки. Первый candidate дошёл до
smoke, но не создал manifest из-за отсутствующего Git: добавлена обязательная
зависимость test image, не успешный skip. Первый Compose обнаружил egg cache
`/.cache` без доступа для numeric UID; задан `/config/.python-eggs`, повтор прошёл.
Первая сборка нового x86_64 test image отказала на PyPI read timeout; повтор той же
команды прошёл. Это сетевой отказ, не успешный skip. Общая проверка сначала
остановилась на дополнительной E501 в docker_check; строка разбита, повтор ARM64
полностью прошёл.

Workflow syntax/expressions отдельно проверены actionlint 1.7.12 (macOS ARM64):
`/private/var/folders/zg/l0438jl160s4wr89pw16vh4c0000gn/T/opencode/actionlint-1.7.12 -shellcheck= .github/workflows/checks.yml .github/workflows/release.yml` — Passed.
Скачанный binary архив проверен SHA-256
`aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f` по public API.
Shellcheck отдельно не запускался; actionlint не равен hosted run.

Отрицательный LICENSE-сценарий выполнен через `subprocess.run` в ARM64 test image:
`/opt/venv/bin/python tools/release_bundle.py --dist dist --strict --tag v0.0.1`;
внешний проверочный процесс подтвердил ненулевой exit и точную причину. `make
release-check` целиком отдельно не повторялся: smoke/candidate уже прошли, а
строгая стадия заведомо отказывает по отложенному пользователем LICENSE.

SHA pins проверены public GitHub API: checkout v7.0.1
`3d3c42e5aac5ba805825da76410c181273ba90b1`, upload-artifact v7.0.2
`cf430e030ddbb5b0abf93d22962f4752f3646cd9`, download-artifact v8.0.2
`9000827ccba6bdab643e8b6fd33ac0654aef8333`. Документация GitHub перечисляет
ubuntu-24.04-arm; доступность именно для этого репозитория не проверена.

## A01–A26 и границы

Новое подтверждение A11/A12/A24: штатное пересоздание product core/web сохраняет
record_id/temporary/publication/settings/payload; прямой file ticket работает,
общего payload-диска у web нет. Остальные предыдущие результаты — в отчётах 0–4,
не новая полная приёмка фазы 5. На первом этапе ещё не были выполнены 15 минут
двух playback и 1000 GET/seek/cancel; результаты продолжения приведены ниже.
Финальные p95/полная память/целевой HDD, Lampa/Num и hosted release остаются открытыми.

## Makefile/CI/README и следующий шаг

Makefile дополнен реальными packaging/candidate/release/docker целями. Основной
check включает clean-directory packaging и candidate manifest/checksums. Docker-only split и Compose — отдельные
шаги CI снаружи контейнера без Docker socket. README ссылается на подробную
поставку; release URL и badges успешных runs не выдумываются.

Локальные packaging/Compose/regression завершены в описанном объёме. Пользователь
уточнил целевую установку: ПК с хранилищем на HDD, а не выбранная модель готового NAS.
Для нагрузочной приёмки (§14.2 SPEC) ещё нужны ОС/Docker, CPU/архитектура, RAM,
параметры HDD и способ запуска проверок на этом ПК. HDD-измерения будут отмечены
отдельно от предложенного эталонного SSD-стенда; подтверждение ARM64 не выводится
из неизвестной архитектуры ПК. Скорости и функциональные критерии не ослаблены.
GitHub-репозиторий пользователь создаст позже: hosted/draft проверки отложены,
не объявлены выполненными и не блокируют независимую локальную работу.
Итоговая нагрузочная приёмка, межверсионное update опубликованных версий, полный
аудит licenses/notices и клиентская приёмка **ещё не завершены**. LICENSE пользователь
создаст позже; он не объявлен новым препятствием локальной работе. Поддержаны PEP 440 теги
vX.Y.Z[aN/bN/rcN] с prerelease flag; они проверены unit-format guards, не remote run.
Коммиты, push, внешние репозитории, теги и публикации не выполнялись.

## Продолжение после пользовательского commit dc3da30

На повторной команде продолжить фазу дерево было чистым: пользователь сохранил
предыдущий результат в `dc3da30` (`feat: step 5`). Старые результаты выше —
история первого этапа; текущие дополнительные прогоны отделены ниже.

### Новые проверки и файлы

- `tools/volume_snapshot.py`, `tests/unit/test_volume_snapshot.py`: внутренний
  offline snapshot harness для одноразовых Compose volumes. Проверяются canonical
  relative paths, запрет links/special files/duplicates и только пустые restore targets.
  Это не API восстановления пользовательских volumes.
- `tools/docker_check.py`: после recreation выполняет остановку обоих процессов,
  backup core/web полных профилей и downloads, удаление только своих случайно
  названных test volumes, restore в новые volumes с numeric ownership, затем
  проверяет record_id/FIFO/temporary/publication/settings и точные download bytes.
  Secret сохраняется отдельно в том же одноразовом комплекте.
- `tests/runtime/delivery_lifecycle.py`, `tools/historical_delivery.py` и опция
  parser в `policy_smoke.py`: установленный core egg заменяется при остановленном
  daemon; проверяется миграция подготовленного schema-5 fixture в schema 6,
  viewed/settings/policy/native options, disable/reset/re-enable/uninstall без
  удаления native torrent и foreign/payload файлов. Исторический commit строится
  в temp через git archive, не меняет рабочее дерево. Это не опубликованная версия.
- `tests/runtime/performance.py`: настоящая installed native сессия и HTTP clients,
  два rate-limited reader по 50 Mbit/s, проверки bytes/Range cancel, timings,
  payload/RSS/FD и raw `disk.disk_blocks_in_use`. Длительность/piece size/циклы
  настраиваются. JSON сохраняется даже перед отказом критерия throughput.
- `deluge_stream/torrserver.py`, `tests/unit/test_http_lifetime.py` и расширение
  `tests/runtime/streaming.py`: исправлена утечка HTTP admission при синхронном
  producer/admission failure. notifyFinish теперь регистрируется до dispatch;
  при завершении до возврата pending Deferred он отменяется. Добавлены 40
  последовательных синхронных отказов в unit и native HTTP, без изменения
  Deluge/libtorrent очереди alerts. Ранее исчерпывалось 32 active_requests.
- `Makefile`: test-delivery включён в обязательный check и тем самым CI;
  test-delivery-historical и длительный test-performance — отдельные цели.
  Compose backup автоматически расширяет существующий CI test-docker, YAML не
  дублирует эту логику. README/delivery/compatibility обновлены, добавлена
  [сводка A01–A26](../../docs/acceptance.md), которая не объявляет частичное покрытие полным.

### Выполненные дополнительные команды

```sh
make test-docker PRODUCT_IMAGE=deluge-stream-phase5:arm64 CI_PLATFORM=linux/arm64
make test-docker PRODUCT_IMAGE=deluge-stream-phase5:amd64 CI_PLATFORM=linux/amd64
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase5-check:arm64 make lint test-unit VENV=/opt/venv
docker run --rm --cpus 4 --memory 4g --network none -v "$PWD:/work" deluge-stream-phase5-check:arm64 make lint test-performance test-delivery VENV=/opt/venv LOAD_SECONDS=10 LOAD_CYCLES=1000 LOAD_PIECE_KIB=1024 LOAD_REPORT=build/performance-arm64-1m-quick.json
docker run --rm --cpus 2 --memory 4g --network none -v "$PWD:/work" deluge-stream-phase5-check:arm64 make test-delivery-historical VENV=/opt/venv PREVIOUS_REF=037931f
docker run --rm --cpus 2 --memory 4g --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase5-check:arm64 make check VENV=/opt/venv
docker run --rm --cpus 2 --memory 4g --platform linux/amd64 --network none -v "$PWD:/work" deluge-stream-phase5-check:amd64 make check test-delivery-historical VENV=/opt/venv PREVIOUS_REF=037931f
docker run --rm --cpus 4 --memory 4g --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase5-check:arm64 /opt/venv/bin/python -I tests/runtime/performance.py --egg build/load-artifact-1wzuenwc/Stream-0.0.1-py3.11.egg --seconds 30 --cycles 1000 --piece-kib 64 --output build/performance-arm64-64k-30s.json
docker run --rm --cpus 4 --memory 4g --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase5-check:arm64 /opt/venv/bin/python -I tests/runtime/performance.py --egg build/load-artifact-1wzuenwc/Stream-0.0.1-py3.11.egg --seconds 900 --cycles 1000 --piece-kib 1024 --output build/performance-arm64-900s.json
```

Длинный load использует отдельную копию egg с SHA-256
`f94fd3dc5110db4815f9ce108fb2a6097906b0ff8611efa7e8a2d1c897122526`, чтобы
последующие сборки общего dist не меняли проверяемый artifact.
Docker VM сообщает 12 CPU/25162465280 bytes RAM; load ограничен cgroup 4 CPU/4 GiB,
regression — 2 CPU/4 GiB. Сеть loopback, ready fixture 64 MiB, диск/caches Docker VM.
Во время длинного прогона выполнялись и другие локальные тесты: это диагностическое
измерение, не выделенный физический стенд, не uncached HDD и не target acceptance.

| Проверка | Фактический результат |
| --- | --- |
| ARM64 и эмулируемый x86_64 offline Compose backup/новые volumes restore | Passed: оба полных профиля, payload, ownership, policy/FIFO/settings/download bytes |
| Дополнительные unit snapshot guards | 222 unit Passed; временный 223-й тест относился к отменённому эксперименту, не итоговый count |
| Короткий ARM64 1 MiB / 10 с / 1000 cancels, 4 CPU/4 GiB | Passed: 49.9865/49.9864 Mbit/s, ready-first-byte p95 4.353 мс, reactor p95 2.125 мс, payload peak 4456448 bytes, FD 19→19; это не 15 минут |
| Тот же запуск test-delivery | Первый отдельный запуск Passed: schema fixture, replacement/disable/uninstall |
| Исторический core artifact 037931f2da0cd19f92264c14311252f5d4dc04fe → текущий | Первый запуск Passed: old SHA c4aefc2ba7b4eccd27e819f3d53be8da624f15249cec8b1d9761094b7404d6ff, new SHA 5af00d75a8729042c01459251ebe7d7a0627351bd7e5222a614882a628aa2c7e; оба version 0.0.1, не два релиза |
| Дополнительный полный ARM64 make check | Старые suites, 222 unit/87 persistence, native/Chromium/smoke/packaging Passed; новый delivery test обнаружил race/native seed queue marker, весь check пока Failed |
| ARM64 64 KiB / 30 с / 1000 cancels, 4 CPU/4 GiB | Failed throughput: 24.0185/23.9832 Mbit/s; p95 first-byte 5.465 мс, reactor 5.284 мс, payload peak 786432 bytes, FD 19→19; код продукта исходный |
| ARM64 1 MiB / 900 с / 1000 cancels | Passed для staged artifact: 49.999905/49.999826 Mbit/s, ready-first-byte p95 17.644 мс, reactor p95 5.008 мс (45251 samples), payload peak 4456448 bytes, FD 19→19; sessions/jobs/deadlines/reserved = 0 в конце |
| ARM64 финальный полный make check после HTTP fix | Passed: 224 unit/87 persistence, все native suites (включая 40 admission conflicts), Chromium, installed smoke, delivery/migration/uninstall, packaging и candidate |
| x86_64 финальный check + историческая замена | Passed: 224 unit/87 persistence, native/Chromium/smoke/delivery/packaging/candidate; historical old SHA 9604249bd6c8b5d1f09fe6ab1f723dff45b3f91559438a12a5a7a9d0853ac0dc → new d7d18b73399a6f5e71f5b382c3633005d6123dcd69efae08b36a1c160f0801f7; x86_64 эмулируется |

### Диагностика и ограничения

Первый restore отказал: целевой Python 3.11.2 не поддерживает tarfile `filter`.
Добавлена проверка доступного API; архив всё равно предварительно проверяется
на paths/types. Повторы обеих архитектур прошли. Исправлены E501 и один неверный
ручной запуск lint без VENV=/opt/venv; это не успешные skips.

Первые 64-KiB load прогоны выявили недобор скорости. Пробный QoS fast path
полностью готового seed не помог (один прогон ~15.4/15.5 Mbit/s), поэтому **отменён**;
production reader/QoS и его исходные unit tests в итоговом дереве не изменены.
Первый baseline ~43.6/43.5 Mbit/s также не достиг цели. В Deluge AlertManager
прочитан штатный 50-мс wait при delayed handler callbacks; последовательные
native reads мелких pieces чувствительны к нему. Это вероятный фактор, не
доказанная единственная причина. Собственная очередь alerts/monkey patch не добавлялись.
Для устранения ограничения рассматривается bounded native read-ahead с тем же
budget/instance/cancellation/admission контрактом. Критерий скорости не снижен,
зелёный 1-MiB тест не закрывает отказ 64-KiB теста.

Повтор delivery под нагрузкой обнаружил готовность native catalogue после
подключения RPC: добавлено ожидание появления/согласования native options.
У готового paused seed queue marker после рестарта менялся -1→0, поэтому этот
marker теперь выводится как диагностика, не сравнивается с пользовательским
порядком incomplete queue (последний проверяют прежние native suites).
Кроме того, RPC readiness опережала native resume-data checking, из-за чего
ранний play давал operation_conflict. Тест сначала подтверждает сохранённый pause,
затем явно моделирует **пользовательский resume**, ждёт только transient conflict
и проверяет bytes. Плагин не возобновляет торрент автоматически и не считает
историческую готовность доказательством have_piece. Этот путь помог обнаружить
HTTP admission leak, описанный выше. После исправления полные ARM64 и x86_64 check прошли.

Полный сырой 900-секундный JSON находится в `build/performance-arm64-900s.json`
(SHA-256 `4bf2d75c69d520aa9ceb1dab69d6d3367552653a99d2f7827d25008640f72c6c`),
краткие измерения сохранены в [summary](performance-arm64-summary.json).
RSS peak (включая in-process harness/client workers/sample arrays): до 137800 KiB,
в середине 155120, в конце 157540; worker threads 17→18, FD до/после 19→19
(во время передачи до 23). Это не доказательство полного native heap plateau.
Staged egg был собран **до** последующего HTTP admission fix; этот прогон не
объявляется финальной нагрузочной приёмкой нового release artifact. Reader/QoS
в итоговом коде не менялись. Отказ 64-KiB и пользовательский HDD-стенд остаются открытыми.

A01/A11/A12/A16/A24/A25 получили дополнительную проверку backup/delivery в
описанных границах. A02, целевой ПК/HDD, настоящий ENOSPC, full native heap/ZIP
memory, лицензии/notices и hosted release остаются открытыми. Raw disk-block
counter не объявляется полной памятью libtorrent, RSS peak не называется
доказательством отсутствия любого роста RSS. Финальный DoD не закрыт.

После HTTP fix product images пересобраны теми же командами `docker build`
из первого блока: ARM64 image id
`7a37f2ec45f50f86f5b8f00cbaabf1047bf5a6edbb42edb0ec2d9afa5b4da95f`, x86_64
`b287abcb30ad7c12563c71065883542b2c70b7d94b07dc425c7a622d6e0b9f4e`.
Повторные `make test-docker PRODUCT_IMAGE=deluge-stream-phase5:arm64 CI_PLATFORM=linux/arm64`
и аналогичная amd64 команда **Passed** с offline backup/restore уже на этих образах.

Следующий технический шаг — устранить/локализовать недобор throughput мелких
pieces, сохраняя adapter/alerts/quotas/cancellation и пользовательские native options.
Для возможного read-ahead потребуются bounded несколько pending reads, порядок
выдачи и освобождение всех chunks/orphan reservations при seek/stop; ни второй
движок, ни обход AlertManager не допускаются. До этого нагрузочный DoD не закрывается.
Внешние gates по-прежнему отложены, не подменяются локальными результатами.
