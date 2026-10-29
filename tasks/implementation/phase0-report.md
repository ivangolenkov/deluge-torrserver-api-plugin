# Отчёт: фаза 0 — прототип чтения и инструменты

Статус: **Завершена**. Работа: 28–29 октября 2026 года.
Исходная задача: [фаза 0](../phase0-feasibility-and-tooling.md).
Предыдущей фазы нет; исходное дерево содержало только документацию и было чистым.

## Что сделано

Создан core-прототип `Stream` 0.0.1: штатный Deluge entry point и `.egg`,
loopback-only HTTP probe с admin RPC и случайным Bearer key. Чтение идёт из
существующей session через have_piece/read_piece и AlertManager. Один GET,
один piece до 4 MiB, timeout, backpressure, cancel и проверка экземпляра.
HEAD не читает payload; Range policy вынесена в чистую функцию.

В HTTP probe **нет QoS**. Отдельный реальный эксперимент обнаружил:
исходный piece priority 4 → deadline 7 → reset 1; оба overrides попадают в
resume data. Восстановление после настоящего crash проверено отдельным журналом;
дополнительно испытаны candidate identity и post-worker guards. Новый протокол
retained ownership закрывает потерю recovery coverage при completion commit.
Переносимость identity, безопасный retirement/GC и live controller ещё не готовы.
Требования не упрощены.

Есть реальный deluged subprocess smoke через authenticated RPC, отдельный
integration harness с настоящими компонентами Deluge и локальным seed,
а также ZIP64 writer experiment без полного архива на диске/RAM.
API Lampa/Num, FIFO, продуктовая SQLite, Web UI и downloads не реализованы.

Продолжение 29 октября: добавлены metadata matrix v1/v2/hybrid, чистая модель
актуальной file policy и семь процессных сценариев SQLite WAL/SIGKILL/restart.
SQLite пока существует **только в исследовательском harness**, не в core-плагине.
При продолжении сохранены все прежние незакоммиченные файлы и изменения.

Следующая часть 29 октября: добавлен witness на hardlink служебного `.torrent`
и fingerprint, отдельные record_id в research-журнале, deterministic worker-result
guards, очередь и pending-read pause/skip. В самом PieceAdapter исправлены
проверки session pause/queue и защита от replay старого removal callback.
В плагине по-прежнему нет продуктовой БД, QoS controller и hardlink-требования.

Следующая часть: выбран retained-ownership протокол вместо удаления marker.
Добавлен экспериментальный RecoveryLedger с epoch-CAS и bounded scan, 15 проверок
на настоящей временной SQLite и шесть процессных commit/crash сценариев.
Acknowledgement не означает отсутствие будущей потребности в сверке: startup
audit читает также pending=0. Никакая новая БД при enable Core не открывается.

По указанию пользователя отдельно исследована только HTTP-эмуляция TorrServer
из `torrent-stream-hub`, commit `043e26a2bd2479350dbc1d1486b32e85e7bacb7a`.
Сохранены source-derived schemas, manifest и 20 синтетических примеров;
движок, management API и Web GUI не перенесены. Это не клиентские captures.
Полное описание — в [контракте](../../docs/torrserver-contract.md).

Дальнейшее продолжение: запущен настоящий TorrServer MatriX.147.1 и оригинальный
модуль Lampa 3.4.4 в Chromium. Сохранены 35 HTTP-обменов: 22 scripted reference,
10 вызовов модуля клиента и три явно отмеченных browser fetch. Подтверждены
числовой stat, строковый data, реальные cache pieces, пустой preload 200 и
viewed set/list/rem с persistence. Это не UI/player end-to-end; Num не запускался.

### Согласованное изменение клиентского gate и завершение

29 октября 2026 года пользователь уточнил: сейчас достаточно Lampa;
полноценные проверки Num не нужны, при необходимости он выполнит проверки
сам позднее. Для завершения исследовательской фазы приняты уже полученные
module-level записи Lampa/TorrServer. Num и полноценные UI/playback/закрытие
страницы перенесены в последующую приёмку и не объявлены выполненными.

Исходный пункт DoD «Сохранены версии клиентов, fixtures и минимальный контракт
совместимости» ранее включал Lampa, Num и эталонный TorrServer с полной клиентской
записью. Теперь gate ограничен версиями Lampa/TorrServer, 35 реальными обменами,
наблюдёнными schemas и минимальным viewed-контрактом. Причина изменения — прямое
указание пользователя, а не подмена недоступной проверки заглушкой. Продуктовая
цель поддержки Num и сценарий A02 сохранены; они не засчитаны как пройденные.
SPEC §5.1/этап 0, план, индекс, README и fixture-документация согласованы.

Все пункты уточнённого DoD закрыты ранее выполненными проверками и этим
согласованием. Runtime, код, schemas, captures и их флаги num_verified=false /
full_ui_playback_verified=false при завершении не менялись. Последний полный
make check: ARM64 и эмулируемый x86_64, 110 unit, 15 persistence, runtime suites,
build, ZIP64 и daemon smoke — успешно. Для текущего документационного изменения
достаточны check-docs и git diff --check; повтор всего runtime-набора не нужен.
Makefile и CI не меняются: поведение и состав автоматических проверок прежние,
checks.yml остаётся задачей фазы 1. Фаза 1 в рамках этого уточнения не начата.

Проверки текущего уточнения:

```sh
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make check-docs VENV=/opt/venv
git diff --check
```

Обе успешны: Markdown parsed, local targets exist: 19 files; whitespace errors нет.

## Изменённые файлы

| Файлы | Назначение |
| --- | --- |
| deluge_stream/{__init__,core,adapter,ranges,layout}.py | Entry point, HTTP producer, внутренние API, Range и immutable metadata/file-policy модель |
| deluge_stream/{witness,recovery_guard}.py | Исследовательский filesystem witness и чистый допуск worker-result; не подключены к HTTP/QoS |
| deluge_stream/recovery_ledger.py | Исследовательский retained-ownership store: WAL/FULL, per-piece epoch, CAS acknowledgement без удаления coverage, bounded admission/scan |
| setup.py, pyproject.toml | Egg-сборка, lint/formatter/test config |
| requirements-dev.txt, requirements-runtime.txt, constraints.txt | Закреплённые Python-зависимости |
| Makefile, tools/{runtime_check,clean,check_docs,zip_probe}.py | Реальные команды, runtime preflight, очистка и эксперименты |
| tools/runtime.Dockerfile, .dockerignore, .gitignore | Linux-стенд и исключение генерируемых файлов |
| tests/unit/*.py | Детерминированные проверки Range, broken links и безопасной очистки |
| tests/runtime/{probe,daemon_smoke,research}.py | Seed/read/lifecycle, настоящий daemon/RPC smoke, metadata matrix и процессные crash/recovery эксперименты |
| tests/runtime/guards.py | Crash + identity, offline/replayed re-add, metadata unlink fault, policy barrier, native queue/session-pause/skip |
| tests/runtime/cleanup.py, tests/persistence/test_recovery_ledger.py | Настоящие SQLite commit/crash/policy/epoch/re-add сценарии и отдельные persistence-тесты |
| tests/fixtures/README.md | Происхождение локального fixture и план клиентских captures |
| tests/fixtures/torrserver-emulation/{schema,manifest,examples}.json | Source-derived DTO, 13 маршрутов, SHA256 семи исходников и 20 примеров; не runtime captures |
| tests/unit/test_emulation_contract.py, tools/verify_contract_source.py | Offline schema/type/example проверки и необязательная read-only сверка source checkout |
| docs/torrserver-contract.md | Границы использования HTTP-эмуляции и явные отличия от SPEC |
| tools/capture_reference.py, tools/reference_browser_bridge.py, tools/capture_lampa.js | Опциональный network-none стенд, loopback docker-exec bridge и Chromium API-module сценарий |
| tests/fixtures/torrserver-reference/*, tests/unit/test_reference_contract.py | Настоящая запись, наблюдённые schemas, происхождение/команды и offline regression |
| docs/{architecture,compatibility}.md | Решения, риск deadlines, версии, подготовка и лицензии |
| README.md, SPEC.md, tasks/README.md, файл фазы и этот отчёт | Фактический статус, инструкции, устранение ссылок на внешний checkout |

## Статус Definition of Done

- [x] Выбраны и записаны точные версии и подтверждён способ установки runtime на x86_64/ARM64. Docker build и make check прошли на ARM64 и эмулируемом x86_64; версии ниже и в docs/compatibility.md. Полный lock системных пакетов остаётся ограничением воспроизводимости.
- [x] Прототип собирается, устанавливается и включается без изменения исходников Deluge. Обе архитектуры: egg установлен в новый профиль, реальный daemon/RPC smoke 20 циклов прошёл.
- [x] Подтверждены отменяемое HTTP-чтение, deadlines, read_piece и lifetime alert-буферов. Обе архитектуры: точные 100000 байт незавершённого fixture, copied buffer после циклов alerts, cancel и отдельный deadline experiment. Это не полная нагрузочная приёмка.
- [x] Выбран проверяемый алгоритм снятия QoS и восстановления после save-resume/crash. Для подтверждённого runtime выбран retained ownership + epoch-CAS + witnessed-instance startup audit; шесть новых real-process сценариев закрывают потерю coverage при commit. Это выбор и проверка алгоритма фазы 0, не готовый live QoS controller: portable identity/backup/GC и продуктовая интеграция остаются ограничениями следующих фаз.
- [x] Зафиксированы mapping v1/v2/hybrid и основные конфликты с пользовательскими действиями. Mapping, aliases, pads, общие pieces, контрольные file priorities/first-last, pause/recheck/move/delete-readd, native queue и session pause/skip подтверждены. Это фаза-0 контрольные сценарии, не полная продуктовая приёмка A07/A08/A14/A21.
- [x] Проверен выбранный потоковый ZIP64 writer. zipfile с non-seekable sink: 4 GiB + 123 байта, 65537 entries, CRC стандартным reader и BrokenPipe. Это технический эксперимент; независимый архиватор и реальный HTTP ZIP cancel остаются для продуктовой проверки A19/A20.
- [x] Сохранены версии Lampa/TorrServer, fixtures и минимальный исследовательский контракт по уточнению пользователя от 29 октября 2026 года. TorrServer MatriX.147.1, Lampa 3.4.4, 35 реальных обменов, schemas и viewed set/list/rem подтверждены. Num и полноценные playback/UI/закрытие страницы отложены до последующей приёмки, не объявлены проверенными. Source-derived примеры TSH сохранены отдельно.
- [x] Makefile выполняет реальные базовые сценарии; make check и smoke проходят. Расширенный make check с research matrix пройден на обеих архитектурах; отдельно ранее проверены bootstrap, pip check, help и clean. Unit-тесты подтверждают сохранение downloads/профиля/SQLite и отказ от symlink.
- [x] README содержит воспроизведённые инструкции подготовки, сборки, установки прототипа и тестирования. Docker build и make check воспроизведены, включая установку собранного egg в изолированный deluged. Нативная установка вне контейнера явно обозначена как не проверенная отдельно.
- [x] Создан отчёт с фактами и ограничениями; индекс фаз обновлён.

## Проверки

Хост: macOS ARM64, Docker Desktop 28.0.4, Linux VM kernel 6.10.14-linuxkit,
12 CPU / 23.43 GiB RAM. ARM64 — нативное выполнение в VM; amd64 — эмуляция.
Runtime: Debian bookworm, Python 3.11.2, Deluge 2.2.0, libtorrent binding
2.0.8.0, Twisted 24.11.0, SQLite 3.40.1, setuptools 75.8.0, wheel 0.45.1.

Команды выполнялись из корня checkout:

```sh
docker build -f tools/runtime.Dockerfile -t deluge-stream-phase0:arm64 .
docker build --platform linux/amd64 -f tools/runtime.Dockerfile -t deluge-stream-phase0:amd64 .
docker run --rm -v "$PWD:/work" deluge-stream-phase0:arm64 make test-integration VENV=/opt/venv
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make smoke VENV=/opt/venv
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make check VENV=/opt/venv
docker run --rm --platform linux/amd64 --network none -v "$PWD:/work" deluge-stream-phase0:amd64 make check VENV=/opt/venv BUILD_DIR=build/amd64 DIST_DIR=dist/amd64
docker run --rm -v "$PWD:/work" deluge-stream-phase0:arm64 sh -c 'make bootstrap VENV=/tmp/deluge-stream-bootstrap && /tmp/deluge-stream-bootstrap/bin/python -m pip check && python -m ruff format . && make clean PYTHON=python && make help'
python3 tools/runtime_check.py --mode smoke --dist dist
```

| Проверка | Фактический результат |
| --- | --- |
| ruff check / format | Пройдены после форматирования |
| pytest tests/unit | 110 passed на каждой архитектуре: прежние 66 и 44 проверки реальных captures/recorder; offline replay schemas не приравнен к повторному запуску Lampa |
| pytest tests/persistence | 15 passed на каждой архитектуре: реальная SQLite, retained coverage, epochs, rollback, bounds, schema/corruption, WAL reader во время worker commit |
| ARM64 runtime integration | Пройден: progress 0 → 0.0625 в последнем запуске, 100000 точных байт, 11 reactor ticks за 0.2 s ожидания, cancel освободил reader; затем pause/recheck/move/remove/re-add |
| ARM64 smoke, deluged subprocess | Пройден: 20 циклов через настоящий authenticated RPC; listener закрыт, RPC удалён после каждого disable; shutdown exit 0 |
| Deadline/save-resume experiment | Подтверждён риск: priority 4 → 7 → 1; HTTP probe отключён от QoS |
| ZIP64 writer | Пройден: 4294967419 байт, 65537 entries, virtual ZIP 4303727309 байт, retained headers 8759890, chunk ≤ 1 MiB, CRC reader verified, cancel после 2 source reads |
| amd64 image build | Пройден; один повторный build прерван таймаутом скачивания, следующий успешно завершился |
| amd64 runtime integration и daemon smoke | Пройдены под эмуляцией: progress 0 → 0.03125 в последнем запуске, точные байты/cancel/resume/lifecycle, 20 enable/disable через RPC |
| Metadata matrix | На обеих архитектурах: v1/v2/hybrid загружены через Deluge; каждый piece/file intersection сверён с map_block; три file-policy/first-last состояния на variant |
| research.py SIGKILL/restart | На обеих архитектурах: шесть exit -SIGKILL и restart плюс штатный shutdown/restart; journal/resume/skipped/memory/restored/cleared/graceful, все recoveries exit 0 |
| guards.py | На обеих архитектурах: пять SIGKILL preparations, normal/offline/unlink-failed/race/policy, native queue/session pause/skip и synthetic stale removal replay; все ожидаемые отказы содержат 0 записей в handle |
| cleanup.py | На обеих архитектурах: before/after commit, rollback, live policy retry, epoch и re-add; 11 ожидаемых SIGKILL на подготовке/commit-границах, 6 успешных startup audits |
| Итоговый make check | Passed на ARM64 и эмулируемом x86_64: docs, lint, format, 38 unit, 15 persistence, build, HTTP/lifecycle, metadata/recovery/guards/cleanup, ZIP64 и daemon smoke; внешняя сеть отключена |
| bootstrap / pip check / help / clean | Passed в одноразовом ARM64-контейнере; финальный check выполнен после clean, то есть без старых build/dist |
| Runtime preflight на macOS, Python 3.13.3 | Ожидаемый exit 1: отсутствует libtorrent, выведена инструкция подготовки; успешного skip нет |

При разработке runtime-тесты реально выявляли ошибки, не замаскированные skip:
отсутствующий pad_file_at; truthiness error_code при успешном read; неверное
предположение о reset deadlines и достаточности повторного file priorities.
После исправлений HTTP-прототип не меняет приоритеты.
`check-docs` обнаружил три ссылки SPEC на соседний внешний checkout, недоступный
в standalone контейнере: заменены на текстовые указания внешних источников.

Новые эксперименты выполняются теми же двумя командами `make check`, отдельный
вход для диагностики (после `make build`):

```sh
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase0:arm64 python -I tests/runtime/research.py --egg /work/dist/Stream-0.0.1-py3.11.egg
docker run --rm --platform linux/amd64 --network none -v "$PWD:/work" deluge-stream-phase0:amd64 python -I tests/runtime/research.py --egg /work/dist/amd64/Stream-0.0.1-py3.11.egg
```

Parent harness создаёт временные профили и запускает процессы с настоящим
Deluge/установленным egg. SQLite используется только в worker thread, WAL,
busy_timeout=5000, synchronous=FULL, одно соединение на последовательную операцию.
После journal commit и сохранения Deluge state/resume worker вызывает SIGKILL
самому себе; parent проверяет реальный return code, затем запускает новый процесс.
В вариантах resume/skipped/memory live vector до recovery действительно
не совпадает с актуальной сохранённой политикой. После recovery совпадают live
и сериализованный resume, marker очищается только после сохранения чистого resume.
Неверный exit или timeout проваливают тест; полные логи worker выводятся при ошибке.
Соединение SQLite явно закрывается через contextlib.closing после транзакции.
После этого уточнения повторены полный ARM64 make check и отдельная полная
research matrix x86_64; оба прогона Passed, остальные код/зависимости не менялись.

### Продолжение: identity и очередь

Расширенный набор повторно пройден **полностью на обеих архитектурах** теми же
двумя make check из списка выше. Версии зависимостей и Dockerfile не менялись.
Профили и witnesses находятся в temp контейнера, filesystem — `overlayfs`:

```sh
docker run --rm --network none deluge-stream-phase0:arm64 stat -f -c %T /tmp
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase0:arm64 python -I tests/runtime/guards.py --egg /work/dist/Stream-0.0.1-py3.11.egg
```

| Сценарий guards.py | Фактический результат на ARM64 / эмулируемом x86_64 |
| --- | --- |
| normal | Witness пережил SIGKILL/restart; restored=1, marker очищен |
| offline | Плагин выключен перед remove/re-add того же hash/path; старый record identity_unproven с 0 writes, новый restored; один старый marker оставлен для диагностики |
| unlink-failed | Тестовый os.remove fault только для служебной metadata; Deluge продолжил remove/re-add и перезаписал inode на месте. Fingerprint старого record отклонён, новый восстановлен независимо |
| race | После worker identity-read, но до применения результата, настоящий Core remove/re-add; instance_changed, 0 writes; старый и новый markers не очищены, приоритет нового экземпляра остался 7 |
| policy | На том же барьере Core меняет file priorities на [2,0,3]; policy_changed и 0 writes; marker сохранён; отдельный retry восстанавливает свежую, а не старую политику |
| queue/session pause/skip | Test admin задаёт native active_downloads/active_limit=0; reader не force-start и не меняет лимиты. Пауза всей сессии и последующий user skip прекращают pending wait менее чем за 1 s, pending освобождён |
| stale removal replay | EventManager синхронен. Искусственный replay старого removal через него после re-add не отменяет новое чтение. До исправления старый egg провалил этот regression assertion, после пересборки новый прошёл |

`research.py` стал импортируемым helper для `guards.py`; CLI по-прежнему вызывает
те же реальные эксперименты и не подменяется заглушкой. Исследовательский
`guards.sqlite` изолирован от hash-only `research.sqlite`: это независимые тесты,
не две продуктовые БД. Witness создаёт только служебный metadata hardlink,
UUID и fingerprint фиксируются до overrides; ни payload, ни общий downloads
плагин не связывает/удаляет. SQLite/metadata I/O выполняются worker-операциями.

**Риск исходной ветки:** `recover_one` очищает marker только в контролируемой quiet-ветке.
Проверка policy stamp до worker commit не защищает от смены политики во время
самого commit. Вне этого тестового условия такой cleanup ещё не безопасен.
Эта ветка оставлена как исходный эксперимент; для выбранного следующего протокола
удаление coverage заменено сохраняемым ownership. Hardlinks/timestamps/backup/GC
ещё не подтверждены как переносимый продуктовый механизм; HTTP не включает QoS.

### Продолжение: completion commit без потери coverage

`RecoveryLedger` сохраняет одну строку на record_id/piece, а не историю каждого GET.
`arm` коммитит coverage и новый epoch **до** deadline. `acknowledge` использует CAS
по record_id/piece/epoch и меняет только pending. Записи ownership не удаляются;
startup audit обязан читать и pending=0. Это не playback/refcount, reservation
или причина защиты FIFO. Неподтверждённое retirement не удаляет payload и не
обходит resource budget: новые admissions отклоняются до native-изменений.

SQLite-кандидат имеет WAL, synchronous=FULL, busy_timeout=5000, FK и версию схемы.
Одна connection на вызов явно закрывается, I/O вызывается только через worker в
runtime harness. Исследовательские пределы: 128 generations, 1000000 ownership rows,
4096 pieces на arm batch и 128 rows на scan page. Новая схема не является
продуктовой миграцией persistence фазы 1; dispatcher в Core не добавлен.

Команды дополнительных проверок (они также включены в полный make check):

```sh
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make test-persistence VENV=/opt/venv
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase0:arm64 python -I tests/runtime/cleanup.py --egg /work/dist/Stream-0.0.1-py3.11.egg
```

| Сценарий cleanup.py | Фактический результат на обеих архитектурах |
| --- | --- |
| before | Policy first/last меняется в открытой SQLite writer-транзакции; SIGKILL до commit. После restart pending=2, coverage=2; audit исправляет один owned piece |
| after | SIGKILL после commit, пока результат ещё удерживается worker-барьером. После restart pending=0, coverage=2; owned piece 0 реально имеет старый 7 при актуальной политике 1 и исправляется audit |
| rollback | Worker fault до commit откатывает acknowledgement; после SIGKILL/restart pending=2 и recovery работает |
| live | Policy меняется во время commit без crash; после completion отдельный bounded retry использует актуальную policy и делает одну корректирующую запись; следующая startup-проверка не требует изменений |
| epoch | После cleanup сохранён новый admission epoch=2 и новый deadline. Старый ack epoch=1 возвращает пустой результат, не сбрасывает priority 7/новый pending. После restart восстанавливается текущая эпоха, coverage остаётся |
| readd | Core remove/re-add выполняется во время worker commit. Старый ack относится только к старому UUID; новая generation коммитит собственный ownership до deadline. После crash старый witness отклонён без writes, новый экземпляр восстановлен независимо; coverage=3 |

Во всех audit проверяется сохранённая пользовательская policy, а не только
побайтовая готовность или процент Deluge. Незатронутые pieces не переписываются.
В post-commit сценарии assertion подтверждает **реальный старый priority 7**,
то есть recovery не является фиктивным no-op. SQLite commit/rollback не заменены
mock: thread barriers стоят до/после настоящего commit, SIGKILL завершает процесс.
Предыдущие hash-only/quiet tests сохранены; новые обязательные проверки добавлены.

Точные результаты metadata:

- v1: Deluge id `f2faeb69c6624348d7f62241433be26d8c15a10e`, payload indices 0/1/2,
  offsets 0/65537/65537, общий piece 4.
- v2-only: Deluge id `1a0f0960c2b37c6957dc421bdb15a9bfebd1d5a7`, полный v2 alias
  `1a0f0960c2b37c6957dc421bdb15a9bfebd1d5a720fff266a7a7a8a4518d43da`.
- hybrid: Deluge id `c3f1d77f63739f33c8d401a2a3f99010eba5776a` — v2 prefix,
  v1 alias `b4622ff4b4e173974e1009f1895e1e70abc409a0`, полный v2 alias
  `c3f1d77f63739f33c8d401a2a3f99010eba5776a431caf6ffb89d93e1031abf7`.
- v2/hybrid: payload indices 0/2/4, offsets 0/81920/196608, pad indices 1/3;
  общий piece отсутствует. Видимый порядок a/b/empty отличается от v1 a/empty/b.
- v2-only magnet штатный Deluge отклоняет на валидации. Hybrid add через v1
  alias даёт AddTorrentError «torrent already exists», число записей не растёт.
- При одновременном изменении file priorities и first/last выявлен native async
  конфликт: options=true не доказывает, что piece vector применён. Контрольные
  сравнения разделяют эти изменения барьером. Полная гонка QoS ещё не решена.

Предупреждения Deluge о пустом новом профиле, отсутствующей GeoIP DB и loading
list в standalone harness не считаются ошибкой продукта или установленными
зависимостями. Deprecation bdist_egg/setuptools описан в README.

## Приёмочные сценарии из ТЗ

### Продолжение: реальный эталон и модуль Lampa

Backend: Linux ARM64, проверенный phase-0 image, libtorrent 2.0.8.0,
Python 3.11.2; TorrServer MatriX.147.1, upstream commit
`d84f41968780744d4b8643f7a73b22a84d2c3779`. Официальный release asset SHA256
совпал с GitHub API digest. Lampa 3.4.4: commit
`86f3afa5997793242cc2a081da96efb0934ae71f`, неизменённый app bundle SHA256
`b4a1384a5ebd220caa49451bdbcf5f6efc18d4bdc11777d456c0d988a9cbae75`.
Browser: Playwright MCP Chromium, Chrome/156.0.0.0 UA на macOS.
Все источники/бинарники скачаны в отдельный approved temp-каталог, не включены
в checkout или egg. Сетевой доступ требовался только при подготовке.

Точные команды успешной финальной записи (первый терминал — server, второй — bridge):

```sh
docker run --rm --name deluge-stream-reference-capture --network none -v "$PWD:/work" -v /private/var/folders/zg/l0438jl160s4wr89pw16vh4c0000gn/T/opencode/torrserver-contract:/reference:ro deluge-stream-phase0:arm64 python tools/capture_reference.py --binary /reference/TorrServer-linux-arm64 --lampa-root /reference --serve --output /work/tests/fixtures/lampa-reference-capture.json
python3 tools/reference_browser_bridge.py --container deluge-stream-reference-capture
node --check tools/capture_lampa.js
adb devices -l
```

Затем `playwright.browser_run_code_unsafe` с `filename=tools/capture_lampa.js`.
Выход: version=3.4.4, statType=number, viewed=[file_index=1,timecode=0],
preload=200/0 bytes, stat=200, seek=206/21 bytes, drop="".
Побайтовый assert проверил пересечение piece boundary. /__finish штатно завершил
reference и bridge, контейнер удалён через --rm. JSON перемещён без изменения
wire-данных в `tests/fixtures/torrserver-reference/matrix147.1-lampa3.4.4.json`;
метка исследовательского шага viewed-empty уточнена до viewed-before-set,
поскольку предыдущие stream-запросы уже могли создать метку.

View settings/default TrackTimecode=false реально преобразует переданные 12.5
в 0. Метка пережила restart; rem убрал её. Эталон возвращает null после удаления
последней метки, Lampa вызывает fail callback; пустой 200 viewedSet также даёт
fail callback в стенде, хотя следующий list подтверждает сохранение. Это
зафиксированные особенности, не объявленные успешным UI-сценарием. `connected`
дополнительно запросил /gst/echo (404), основные вызовы продолжили работать.
Не проверены TrackTimecode=true, viewed rem-all/ошибки, видео-декодирование и
реальное событие закрытия страницы; drop вызван программно.

Первый успешный exploratory capture содержал 39 обменов и использовал Docker
internal network (без egress); его заменяет финальная запись с network none,
отдельными markers и исполняемым browser-сценарием. Internal network удалена.
Предварительные неуспехи не скрыты: отсутствующий каталог reference DB исправлен;
один scripted preload завершился RemoteDisconnected, точная причина не доказана.
Добавлен seed readiness preflight, финальный прогон прошёл, но это не доказательство
устойчивости reference harness под нагрузкой. Unit assertion, ошибочно ожидавший
пустой viewed до set после stream, исправлен по фактической записи; wire-данные
не заменялись искусственным успешным ответом.

`adb devices -l` вернул пустой список; Android emulator в PATH отсутствует.
Num нельзя засчитать как проверенный, а номер из web search — как установленную
версию. На момент записи клиентский DoD оставался открытым; позднее его gate
уточнён пользователем, см. раздел о завершении выше. Уточнение set/list/rem в SPEC §5.3
исправляет прежние предположительные названия действий по реальному эталону,
не добавляет новых продуктовых функций и не переносит viewed stub из TSH.
Подробные команды скачивания, sanitation и ограничения — в
[fixture README](../../tests/fixtures/torrserver-reference/README.md).
Будущие планы фаз 1/3 уточнены по подтверждённому persistence viewed и типам;
реализация этих фаз не начата, согласованные последовательность и объём сохранены.

После исправления unit-ожидания и форматирования полный `make check` прошёл на
ARM64 и эмулируемом x86_64: 110 unit, 15 persistence, все runtime suites,
ZIP64, build и реальный daemon smoke по 20 циклов. Команды обеих архитектур
приведены в разделе «Проверки» выше. Первые попытки полного check остановились
на lint E501 в новой строке research-label; исправлено formatter, затем пройден
весь набор. Итоговые `node --check tools/capture_lampa.js` и `git diff --check`
успешны. После последних текстовых изменений повторяется только check-docs.

### Дополнение: контракт HTTP-эмуляции

Источник по указанию пользователя: только HTTP-эмуляция TorrServer на commit
`043e26a2bd2479350dbc1d1486b32e85e7bacb7a`. Соседнее дерево не изменялось.
`tools/verify_contract_source.py` подтвердил 13 маршрутов, четыре DTO и SHA256
семи файлов. Go-сервер, Lampa и Num при этой проверке не запускались.
Source-derived schemas отражают в том числе строковый `stat`, текстовый `data`
и регистр cache-полей, но не утверждают правильность заглушек для клиентов.
Различия индексов, HEAD preload, Host playlist, cache placeholders и viewed stub
явно сопоставлены с требованиями SPEC в docs/torrserver-contract.md.

Команды этой части (полные build/check обеих архитектур — также в разделе
«Проверки» выше; образы пересобраны после добавления dev-зависимостей):

```sh
python3 tools/verify_contract_source.py --source-root /Users/ivangolenkov/Documents/my/my/torrent-stream-hub
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase0:arm64 python -m ruff format tools/verify_contract_source.py tests/unit/test_emulation_contract.py
docker run --rm --network none deluge-stream-phase0:arm64 python -m pip check
git diff --check
```

Сверка source, pip check и whitespace пройдены. Новые unit-проверки валидируют
schemas и 20 синтетических примеров, обязательные типы/поля и provenance;
они не тестируют продуктовые HTTP handlers. `jsonschema==4.23.0` и его
закреплённые зависимости установлены только в dev-окружение; metadata лицензий
jsonschema, jsonschema-specifications, referencing и rpds-py указывают MIT.
В egg эти библиотеки не включаются. На этом шаге клиентский DoD ещё оставался
открытым; позднейшее согласованное уточнение gate описано выше.
После пересборки образов полный `make check` прошёл на ARM64 и эмулируемом
x86_64: 66 unit, 15 persistence, все runtime suites, ZIP64, build и настоящий
authenticated daemon smoke (20 enable/disable). Прогоны выполнялись без внешней
сети; runtime-версии остались прежними. После итоговых правок отчёта отдельно
повторяется только проверка документации/whitespace, не весь runtime-набор.

| ID | Проверенная часть | Что ещё не проверено |
| --- | --- | --- |
| A01 | Чистый egg, реальный daemon, 20 enable/disable, RPC/listener lifecycle | Полный учёт FD/threads/tasks и disable во время всех операций |
| A02 | Реальный reference MatriX.147.1: Lampa 3.4.4 module connected/add/files, cache/viewed/drop; browser-scripted preload/stat/Range по URL Lampa; 35 wire records | Num, UI-события, декодирование/просмотр видео и сам API плагина не проверены; это исследование контракта, не приёмка playback |
| A03 | Unit: single/suffix/open/multi/invalid/large Range; HTTP: partial, HEAD, 400, 416 | Вся HTTP-матрица и реальные плееры |
| A04 | Чтение незавершённого файла через read_piece | Продвижение QoS-окна отсутствует |
| A05–A06 | Только deadline experiment | Seek и несколько viewers |
| A07 | Metadata v1 с общим piece 4; v2/hybrid pads и непересекающиеся файлы | Два одновременных playback и владение deadlines |
| A08 | Модель текущих priorities/first-last сверена с Deluge; stale worker-result отвергнут; policy меняется во время реального commit, live retry/startup audit используют свежую policy | Полный concurrent streaming QoS и все пользовательские/native async гонки |
| A14 | Remove отменяет wait; re-add создаёт новый Torrent; старый reader отвергнут; candidate witness отклоняет offline re-add; stale-event replay не отменяет новый wait | Переносимость durable identity/backup, tickets и все виды callbacks |
| A16 | SIGKILL/restart, save-resume, штатный shutdown, hash-only/identity guards и шесть completion-commit сценариев на обеих архитектурах | Продуктовый live controller, portable identity/backup/GC |
| A19 | ZIP64 большой файл + >65535 entries, Unicode, empty, CRC в virtual sink | Независимый архиватор и продуктовый ZIP endpoint |
| A20 | Piece wait cancel и writer BrokenPipe experiment | Медленный HTTP-клиент/ZIP, нагрузка и общий бюджет |
| A21 | Ручная пауза не снимается; готовый piece читается на паузе; recheck/move сохраняют байты; queue не обходится; session pause/user skip прерывают wait | Disk errors, передача во время move/recheck, полноценный QoS при фоновой нагрузке |
| A25 | Linux ARM64 и эмулируемый x86_64: build, integration, реальный deluged smoke | Физический x86_64/NAS и полная продуктовая приёмка |

Ни один составной приёмочный сценарий целиком не закрыт.

## README, Makefile и CI/CD

README теперь отделяет работающий прототип от планов, содержит artifact,
изолированную установку через smoke, команды и параметры Makefile. Все базовые
цели Makefile реальные; test-integration/smoke не пропускают отсутствующий
runtime. clean ограничен фиксированными выходами проекта и не следует custom
путям. Workflow пока не нужен: checks.yml появляется в фазе 1; hosted GitHub
Actions run и публикации не выполнялись.

Продолжение расширило `tools/runtime_check.py`: обязательный integration теперь
включает `research.py`; отдельной цели с успешным skip нет. Makefile help, README,
fixtures и architecture/compatibility актуализированы вместе с проверками.
Теперь integration обязательно запускает и `guards.py`. Makefile help обновлён;
README различает исправления работающего reader и неподключённые identity/QoS
эксперименты. Workflow по-прежнему не создавался до завершения фазы 0.
Теперь обязательный integration включает также 15 SQLite persistence-тестов и
`cleanup.py`. Runtime preflight выводит версию SQLite. README/help/architecture
объясняют pending vs retained ownership; завершение операции не удаляет coverage.
Для HTTP-контракта отдельная make-цель не нужна: schema/example проверки уже
подхватываются `make test-unit` и обязательным `make check`. Сверка соседнего
checkout намеренно необязательна, чтобы build/check не зависели от другого
репозитория, Go или сети. README, fixtures, compatibility, файл и индекс фазы
обновлены. CI остаётся задачей фазы 1; новых handlers фазы 3 не добавлено.
Реальные captures проверяются новым unit-файлом в тех же test-unit/check;
запуск внешних TorrServer/Lampa намеренно opt-in, без сетевых загрузок в make.
README, compatibility, fixture procedure и SPEC/viewed актуализированы.

## Отклонения и ограничения

- Продуктовые требования сохранены; работа не переносится в фазу 1.
- HTTP prototype не выставляет deadlines из-за доказанного риска persistence;
  это ограничение эксперимента, не отказ от QoS в ТЗ.
- Исследовательский touched-pieces журнал привязан только к hash в заведомо
  неизменном профиле. Не переносить его в продукт без record_id/reconciliation:
  подтверждённый in-memory delete/re-add guard не доказывает durable identity.
- Дополнительный guards-кандидат уже использует record_id/witness и проверен на
  container overlayfs, но это не поддержка всех NAS/backup filesystem. Новое
  обязательное hardlink-требование к продукту не вводилось; witness не вызывается
  core-плагином. GC witness-links/ownership не реализован; commit-окно потери
  coverage закрыто новой схемой без удаления ownership.
- Снятие RPC использует изолированный обход копии registry в adapter: штатный
  deregister_object Deluge 2.2.0 мутирует живой dict iterator. Без monkey patch.
- Python pins не равны полному reproducible lock: системный APT пока без
  snapshot и hashes Python-дистрибутивов ещё не закреплены.
- TorrServer и модуль Lampa запущены, частичные реальные captures сохранены.
  Num и полный playback/UI не проверены; Android runtime для Num недоступен.
- Лицензия pyxdg в metadata UNKNOWN требует проверки upstream; лицензия самого
  проекта не назначалась агентом. Релиза нет.

## Следующий шаг

Уточнённый DoD фазы 0 выполнен; следующий разрешаемый этап — фаза 1: каркас,
persistence, lifecycle и CI. В этом запросе реализация фазы 1 не начиналась.
Num и полноценные пользовательские playback/seek/закрытие страницы отложены
до последующей приёмки, при необходимости пользователем; они больше не gate
фазы 0. Не требуется предоставлять Android runtime или новые captures сейчас.
Для дальнейшей продуктовой реализации остаются portability identity (NAS/backup),
retirement/GC и полноценный live QoS. Они не объявлены готовыми выбранным
протоколом фазы 0. Metadata v2/hybrid подтверждены, но клиентская совместимость
и payload streaming этих вариантов ещё не проверены.
