# История промежуточных результатов фазы 1

Это архив до завершения lifecycle-интеграции и согласованных упрощений.
Статусы и ограничения ниже отражают **промежуточные** шаги, не текущее состояние.
Актуальный итог и DoD — в [phase1-report.md](phase1-report.md).

Статус: **Заблокирована**. Начало: 29 октября 2026 года.
Причина: ожидается согласование capability-контракта durable identity для
каталога конфигурации; [результаты и предложение](../../docs/instance-identity-decision.md).
Исходная задача: [фаза 1](../phase1-core-persistence-and-ci.md).
Прочитаны SPEC и [отчёт фазы 0](phase0-report.md); дерево Git до начала чистое.
Пользователь разрешил начать следующую фазу. Num/UI-проверки остаются отложенными.

## Что сделано

### Первая часть: storage

Создан отдельный продуктовый repository, не связанный с исследовательским
RecoveryLedger и пока не подключённый к Core. Подготовлены две последовательные
миграции: базовые policy/aliases/metadata/settings/journal и viewed. Миграции
атомарны; неизвестная/более новая схема и посторонняя unversioned DB не заменяются.
WAL, synchronous=FULL, foreign keys, busy timeout (default 5000 ms) обязательны.
Соединение закрывается после каждого вызова; в dispatcher одновременно работает
один поток и одно соединение. Очередь допуска — default 32, максимум 128 работ.

Обычная регистрация создаёт постоянный неопубликованный record_id (UUID).
Повторная регистрация того же подтверждённого экземпляра сохраняет политику.
Публикация независима от временности. FIFO использует сохраняемый целочисленный
счётчик, не timestamp; постоянный → временный получает новый ключ, повтор
состояния идемпотентен. `repeat_add` существующего экземпляра публикует его и
может закрепить, но не сделать постоянный торрент временным. Это storage-правила,
**не реализация добавления через Deluge**. Новый TorrServer add и его журнал
ещё не реализованы. Превышение лимита не запускает удаления.

Hash aliases уникальны и разрешаются к одному record_id; конфликт откатывает всю
регистрацию. Revision-CAS отклоняет устаревшую смену политики. При forget удаляются
только записи этого record_id; поздняя операция старого UUID не меняет новый.
Метаданные — ограниченные строковые title/poster/category/data. Viewed сохраняет
1-based file id и числовой timecode, поддерживает обновление, постраничный список
и удаление. Проверка существования файла по metadata остаётся обязанностью будущего
сервиса. Подтверждения streaming/готовности данных в БД не сохраняются.

DB-only смена политики и её committed journal сохраняются одной SQLite-транзакцией;
хранится только последняя завершённая policy-запись экземпляра. Незавершённые
Deluge add/remove нельзя моделировать этой транзакцией: recoverable протокол
между Deluge и БД остаётся следующей задачей. Отдельный admission-сервис должен
проверить owner/admin и безопасность путей **до** разрешения временности.

`DatabaseWorker` не выполняет SQLite в reactor; отмена ещё не начавшейся задачи
пропускает её side effects. Отмена уже выполняемой задачи отменяет ожидание,
но не откатывает возможный commit. До фактического окончания работа занимает
место в очереди. Close запрещает новые вызовы, дожидается допущенных работ и
присоединяет поток вне reactor. Отмена одного ожидания close не отменяет drain.

### Продолжение: core/web и CI

У Deluge 2.2.0 исследованы TorrentState, Torrent.__init__, TorrentManager.add,
save_state и on_alert_save_resume_data. В просмотренном native state нет
сохраняемого UUID экземпляра; fastresume заново кодируется из native params.
Это анализ исходников установленного runtime, **не доказательство durable
identity**. Hash, timestamp и произвольное нештатное поле не приняты как замена
identity. Подключение storage к Core пока не выполнено. Вместо рискованного
упрощения сделана независимая запланированная часть фазы: web-каркас и CI.
Последовательность зависимостей для хранения/политики сохранена.

Один egg содержит deluge.plugin.core и deluge.plugin.web entry points и
data/stream.js. Core предоставляет get_diagnostics(api_version), доступный только
аутентифицированному Deluge RPC с уровнем >=5; метод не возвращает torrent id,
payload, paths или эфемерный probe token. Старый admin-only probe_info сохранён.
Web JSON stream.get_status защищён штатной web-сессией; вызывает authenticated
core RPC, проверяет API и точную plugin-версию, возвращает whitelist полей.
storage_active/torrserver_active остаются false. Неизвестный core, ошибка RPC,
несовместимая версия и отключённый plugin имеют понятные диагностические причины.
Ожидание ограничено пятью секундами; callbacks проверяют generation web-плагина
и текущий daemon proxy, чтобы старый ответ не относился к новому соединению.

Script добавляет в Preferences страницу Stream с фиксированными безопасными
сообщениями. Это не UI управления торрентами фазы 4. При disable снимаются только
свои JSON-методы; доступ к внутреннему registry изолирован в web_adapter.py.
Установленный Deluge JSON.deregister_object, как и core-вариант, меняет dict во
время итерации: для нашего объекта используется обход копии, без monkey patch.

Добавлен checks.yml: pull_request, push/main, workflow_dispatch и workflow_call;
contents:read, actions закреплены commit SHA, публикационных secrets нет.
Checkout persist-credentials выключен. Job вызывает make ci-check, тот собирает
tools/runtime.Dockerfile и выполняет make check в контейнере с network none.
Логи build/ci/image.log и check.log и готовый dist/*.egg сохраняются через
upload-artifact (always, 14 дней). CI не публикует GitHub Releases. Hosted run
ещё не выполнялся; локальная YAML-проверка не засчитывается как hosted run.

Make ci-check реализован на stdlib Python без добавления обязательных зависимостей;
ошибки Docker build/check и timeout не превращаются в успех, лог сохраняется.
Начальная hosted-матрица — Linux x86_64; ARM64 локально, расширение hosted-матрицы
остаётся фазой 5. SHA actions сверены read-only git ls-remote по v4.2.2/v4.6.2.

## Изменённые файлы

| Файл | Назначение |
| --- | --- |
| deluge_stream/repository.py | Миграции, policy/FIFO/aliases, metadata/viewed/settings, CAS и транзакции |
| deluge_stream/db_worker.py | Однопоточный bounded dispatcher, cancellation и drain/join |
| tests/persistence/test_repository.py | Реальная SQLite, reopen/migration/rollback/lock, переходы и stale UUID |
| tests/persistence/test_db_worker.py, tests/persistence/worker_scenario.py | Отдельный процесс с настоящим Twisted reactor, SQLite lock, отменой и остановкой потока |
| Makefile | Уточнение help; новые проверки автоматически входят в test-persistence/test/check |
| README.md, SPEC.md, tasks/README.md, план фазы и этот отчёт | Текущий статус и границы реализованного слоя |
| docs/architecture.md | Отличие нового storage от исследовательского QoS ledger и отсутствующей Core-интеграции |
| docs/compatibility.md | Зафиксированы проверки отдельного deluge-web 2.2.0 на двух архитектурах и границы hosted CI |
| deluge_stream/{__init__,core,webui,web_adapter}.py, data/stream.js, setup.py | Core/web entry points, version handshake, защищённая диагностика, packaged script и безопасное снятие JSON exports |
| tests/runtime/web_smoke.py, tests/runtime/web_contract.py, tools/runtime_check.py | Настоящие отдельные процессы core/web и отдельно обозначенный fault injection ответов |
| .github/workflows/checks.yml, tools/ci_check.py, tests/unit/test_ci.py | Проверки через Makefile, Docker-логи, pins и regression ошибок CI runner |
| tools/probe_instance_identity.py, docs/instance-identity-decision.md | Реальная коллизия native TorrentState/added_time и вынесенное на согласование решение |

## Статус Definition of Done

Storage/core lifecycle остаётся незавершённым. Отдельные CI/artifact пункты
закрываются фактическими локальными проверками, без заявления об успешном hosted run.

- [ ] Реализованы production package, адаптер, core/web entry points и корректный lifecycle.
- [ ] SQLite-модель и миграции сохраняют политику и не блокируют reactor. Storage и worker реализованы; подключение к Core и его lifecycle ещё нет.
- [ ] Переходы временности/публикации и aliases проверены; удаления по лимиту пока нет. Проверен repository, ещё не Deluge/service admission.
- [ ] Restart/reconciliation и защита от старых callbacks проверены с настоящим Deluge.
- [ ] RPC и status fields имеют необходимые проверки прав и экземпляра.
- [x] Создан checks.yml с реальной сборкой, тестами и smoke через Makefile. make ci-check прошёл локально на ARM64; YAML разобран Psych, SHA actions сверены с upstream tags.
- [x] CI не публикует релизы; artifacts пригодны для тестовой установки. В workflow нет release/push/write permissions; egg установлен в настоящие deluged/deluge-web, script получен через Web HTTP.
- [x] make check проходит; статус hosted run указан достоверно. Локальный ci-check прошёл на ARM64 и эмулируемом x86_64, включая core/web smoke. Hosted run не запускался и обязателен до выпуска, как допускает план фазы для текущей локальной работы.
- [ ] README содержит актуальные инструкции установки, сборки, тестов и CI.
- [ ] Отчёт и индекс фаз обновлены, незавершённые проверки перечислены. Обновлены для этой части; итоговый отчёт последует после полного DoD.

## Проверки

Среда: macOS ARM64, Linux-контейнер `deluge-stream-phase0:arm64`, Python 3.11.2,
SQLite 3.40.1, Twisted 24.11.0; прежний runtime Deluge 2.2.0/libtorrent 2.0.8.0,
setuptools 75.8.0, wheel 0.45.1. x86_64 — эмуляция на том же macOS ARM64-хосте.

Команды первой storage-части из корня проекта (последующие проверки — ниже):

```sh
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase0:arm64 python -m ruff format deluge_stream/repository.py deluge_stream/db_worker.py tests/persistence/test_repository.py tests/persistence/test_db_worker.py tests/persistence/worker_scenario.py
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make lint format-check test-persistence VENV=/opt/venv
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make check VENV=/opt/venv
docker run --rm --platform linux/amd64 --network none -v "$PWD:/work" deluge-stream-phase0:amd64 make test-persistence VENV=/opt/venv
```

| Проверка | Результат |
| --- | --- |
| ARM64 lint/format | Passed, 35 Python-файлов отформатированы |
| ARM64 полный make check | Passed: 110 unit, 57 persistence, egg build, прежние runtime/read/metadata/crash/cleanup/ZIP64 и daemon smoke |
| x86_64 test-persistence | Passed: 57 tests, включая 42 новые проверки storage/worker; 1.59 s |
| Worker subprocess | Настоящий Twisted reactor не останавливается при SQLite writer lock; queued cancel пропускает запись, running cancel не отменяет commit; drain ждёт работу и join потока; повторное открытие подтверждает данные |
| Policy/migrations | v1 → v2 сохраняет временность/FIFO/aliases/metadata; неизвестная/повреждённая DB не заменяется; fault-trigger откатывает row/counter/journal; конкурирующие writer получают ключи 1–8 |
| ARM64 artifact smoke | Реальный deluged + authenticated RPC, 20 enable/disable, прежний probe; новые storage-модули включены в egg, но **не активируются в Core** |
| x86_64 полный make check для этой части | Не повторялся: Core/native adapter не менялись, новые storage-проверки отдельно пройдены на этой архитектуре; последний полный x86_64 check остаётся результатом фазы 0 |
| Hosted GitHub Actions | Not run: workflow добавлен в продолжении, но не отправлялся и не запускался на GitHub; успешный hosted CI не заявляется |

Предварительно `make lint test-persistence VENV=/opt/venv` в ARM64-контейнере:
55 passed, lint passed; после дополнительных concurrency/pagination и
running-cancel проверок итог — 57 passed на обеих архитектурах. Первая попытка
lint обнаружила две длинные SQL-строки. Первая попытка полного check остановилась
на F823: локальное имя closing в новой worker-проверке перекрыло contextlib.closing;
имя исправлено, после этого весь ARM64 check пройден. Ошибки не замаскированы skip.

После последних изменений только Markdown дополнительно выполняются:

```sh
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make check-docs VENV=/opt/venv
git diff --check
```

Обе проверки пройдены: Markdown parsed, local targets exist: 20 files;
whitespace errors отсутствуют. Изменения этой части не коммитились и не публиковались.

### Проверки продолжения: web/CI

Те же Linux/runtime-версии. В начале продолжения сохранены незакоммиченные
изменения storage-части. Команды, реально выполненные из корня checkout:

```sh
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make lint smoke VENV=/opt/venv
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase0:arm64 python -I tests/runtime/web_smoke.py --egg /work/dist/Stream-0.0.1-py3.11.egg
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make lint format-check test-unit VENV=/opt/venv
make ci-check CI_PLATFORM=linux/arm64 CI_IMAGE=deluge-stream-phase0:arm64
make ci-check CI_PLATFORM=linux/amd64 CI_IMAGE=deluge-stream-phase0:amd64
node --check deluge_stream/data/stream.js
ruby -rpsych -e 'w=Psych.load_file(ARGV[0]); raise "invalid workflow structure" unless w["jobs"]["linux"]["steps"].size == 3; puts "Workflow YAML parsed; linux job and 3 steps present"' .github/workflows/checks.yml
git ls-remote https://github.com/actions/checkout.git refs/tags/v4.2.2
git ls-remote https://github.com/actions/upload-artifact.git refs/tags/v4.6.2
```

| Проверка | Результат и границы |
| --- | --- |
| ARM64 make ci-check | Passed: Docker build, 116 unit, 57 persistence, все прежние runtime/research/ZIP64, build, daemon и новый core/web smoke |
| Финальный ARM64 lint/format/test-unit | Passed: 41 Python-файл, 117 unit после добавления проверки cleanup только собственного CI container |
| x86_64 make ci-check | Passed под эмуляцией: Docker build, финальные 117 unit, 57 persistence, весь make check с новым core/web smoke |
| core/web smoke, обе архитектуры | Реальные deluged и deluge-web в разных процессах и профилях; egg установлен в оба profiles/plugins, script получен по HTTP; 20 enable/disable, registry после disable снят, Web остаётся connected |
| Web authorization/API | Anonymous JSON отвергнут; core отвергает API 999; диагностические ответы без token/paths/payload |
| web_contract.py | Явно синтетический fault injection удалённых ответов при импорте из egg: offline/error, invalid API/plugin version, stale enable/connection, whitelist. Не выдан за реальный сервер другой версии |
| Workflow syntax | Psych 3.1.0 успешно разобрал YAML и структуру job/steps; это не выполнение GHA и не проверка всех GitHub semantics |
| Action pins | git ls-remote подтвердил checkout v4.2.2 = 11bd71901bbe5b1630ceea73d27597364c9af683, upload-artifact v4.6.2 = ea165f8d65b6e75b540449e92b4886f43607fa02 |
| Script | node --check прошёл; реальный браузерный Preferences UI не запускался, проверена выдача script и backend |
| Hosted run/release | Not run; commits/push/tags/releases не выполнялись |

Первый web-smoke не прошёл из-за особенностей localhost auth в тестовом стенде:
Deluge HostList.add_host заменяет переданный пароль localclient данными из
локального web-профиля, который намеренно отдельный. Источник подтверждён
inspect.getsource(HostList.add_host). Стенд исправлен на отдельного admin-пользователя
streamtest с одноразовым случайным паролем, без обхода авторизации; следующий
web-smoke и оба полных ci-check прошли. Во время разработки lint выявлял import
order и длинные строки; исправлены до финальных проверок. Ошибки не скрыты skip.

Оба ci-check использовали кеш Docker-слоёв, не доказана новая сетевая установка
APT/PyPI с нуля. Профили runtime/smoke одноразовые и создавались заново. Внешняя
сеть на этапе тестов выключена. Логи последнего x86_64 прогона находятся в
build/ci/image.log и build/ci/check.log (генерируемые, не коммитятся). CI runner
сохраняет ошибки/timeout в exit code; при ошибке run удаляет только собственный
уникально названный контейнер, без prune и без удаления пользовательских данных.

После успешных runtime-проверок менялась только документация; повторяются
только make check-docs и git diff --check, не весь runtime-набор.

## Приёмочные сценарии из ТЗ

| ID | Проверенная часть | Что ещё не проверено |
| --- | --- | --- |
| A01 | Worker drain/join; реальные deluged/deluge-web отдельно, 20 enable/disable web/core, снимаются свои RPC/script; штатный Web продолжает работать | Storage lifecycle в Core и полный учёт FD/threads/tasks |
| A11 | Четыре комбинации existing repeat_add в SQLite, уникальность aliases | Штатный Deluge add, metadata aliases и конкурентные add |
| A12 | FIFO-ключ и идемпотентность переходов, публикация независима | Admission безопасной временности и реальные RPC |
| A14 | Старый record_id/CAS не меняет запись нового экземпляра в БД; web fault injection отвергает ответ после disable/enable и смены connection | Durable identity и настоящий продуктовый Deluge delete/re-add/restart |
| A15 | Ошибка миграции/SQL-trigger rollback, busy lock, неизвестная/повреждённая схема | Сбои между стадиями Deluge/SQLite и продуктовый crash/recovery |
| A23 | Реальный Web JSON отклоняет anonymous get_status, diagnostics не выдаёт token/paths/payload | Owner/admin/shared policy-проверки, чужие torrent id и tickets |
| A24 | Настоящие отдельные процессы и профили, одинаковый artifact; handshake; bad API отвергнут core, mismatched plugin version и unavailable core проверены fault injection | Ссылки/downloads, реальные разные версии egg в отдельных машинах, браузерная приёмка |

## README, Makefile и CI/CD

README явно отделяет подготовленный storage от активного core-прототипа.
Команда test-persistence уже собирает все файлы tests/persistence, поэтому
дублирующая цель не нужна. Makefile help уточнён. В продолжении добавлены
ci-check/checks.yml и инструкции тестовой установки одного egg в оба профиля.
Hosted run не запускался, релизы/теги/публикация не выполнялись. Документация
отделяет начальную web-диагностику от будущих настроек и действий с торрентами.

## Отклонения и ограничения

### Конкретный блокер после команды продолжать до конца или вопроса

По прямому указанию пользователя продолжать до полного DoD либо блокера
исследован критический путь подключения policy к Core. Подтверждён реальный
контрпример: удаление и повторное добавление одного torrent с тем же filedump,
filename и save path создают разные Torrent-объекты с **одинаковыми всеми полями
TorrentState и added_time**. Это произошло в первой паре на ARM64 и на эмулируемом
x86_64. Результат не выдан за готовый reconciliation или за доказательство
невозможности любых альтернативных identity-протоколов.

Повторно просмотрены native create_state/_save_state/_add_torrent_obj и
Torrent.write_torrentfile. Произвольный Python-атрибут не попадает в фиксированную
сериализацию Deluge. Подтверждённый binding add_torrent_params предоставляет
added_time, но не uuid/userdata. Переиспользование filename в качестве скрытого
UUID затрагивает native фильтрацию и операции с копиями torrent-файлов и не принято.
In-memory UUID не закрывает delete/re-add при выключенном плагине.

Предложен конкретный вариант: доработать metadata hardlink/fingerprint witness
фазы 0 с явным FS capability-контрактом только для config directory; при
неподдерживаемой ФС/неоднозначности не разрешать временность/опасные операции,
сохраняя обычную работу Deluge и policy-записи. Payload не связывается. Backup
ambiguity и retirement/GC потребуют отдельных продуктовых проверок. Это новое
явное ограничение поддерживаемого config-storage, ранее не согласованное, поэтому
**не введено автоматически** и не прикрыто успешным overlayfs-прототипом.

Фаза отмечена «Заблокирована», а не завершённой. Требуется ответ пользователя:
разрешить этот capability-контракт либо продолжить поиск другого durable-канала
без него. ТЗ не ослаблено до hash/added_time, временность не сбрасывается молча
при каждом рестарте, storage к Core по неподтверждённой identity не подключён.
Незавершённые пункты DoD остаются открытыми. Незакоммиченная предыдущая работа
сохранена; push/tag/release не выполнялись.

Команды диагностического шага:

```sh
docker run --rm --network none deluge-stream-phase0:arm64 python -c 'import inspect; import libtorrent as lt; from deluge.core.torrentmanager import TorrentManager; print("IDENTITY PARAMS", [x for x in dir(lt.add_torrent_params()) if any(s in x for s in ("uuid", "user", "added", "name"))]); print(inspect.getsource(TorrentManager._save_state)); print(inspect.getsource(TorrentManager._add_torrent_obj))'
docker run --rm --network none deluge-stream-phase0:arm64 python -c 'import inspect; from deluge.core.torrentmanager import TorrentManager; print(inspect.getsource(TorrentManager.create_state)); from deluge.core.torrent import Torrent; print(inspect.getsource(Torrent.write_torrentfile))'
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 python -I tools/probe_instance_identity.py
docker run --rm --platform linux/amd64 --network none -v "$PWD:/work" deluge-stream-phase0:amd64 python -I tools/probe_instance_identity.py
```

Среда прежняя: Debian bookworm, Deluge 2.2.0, Python 3.11.2, libtorrent 2.0.8.0.
Успешные JSON: different_live_object/same_infohash/same_persisted_torrent_state/
same_native_added_time=true, payload_deletion=false; added_time=1793264992 (ARM64)
и 1793264993 (x86_64). Опыты создавали собственные временные профили, удаляли торрент
только с remove_data=False; sentinel чужого payload сохранился. Нет публичного
swarm, нового product identity, user profile или установки плагина в этом опыте.
Предупреждения о новом пустом профиле/GeoIP и delayed alert удалённого handle не
превращены в фиктивную проверку lifecycle; результат проверен отдельными asserts.

Первая попытка диагностического скрипта ошибочно трактовала added_time как
datetime; в этом binding это int. Приведение исправлено по runtime, затем обе
архитектуры прошли эксперимент. Lint выявил длинную строку scope; сокращена.
Скрипт opt-in, не добавлен как обязательная flaky timing-проверка в make check:
он иллюстрирует коллизию, а не гарантирует задержку remove/re-add на загруженном CI.
README, индекс, план, SPEC изменены только в части фактического статуса блокировки;
сам предложенный capability-контракт остаётся неутверждённым. Makefile/workflow
для диагностического шага не менялись: нового готового product behavior нет.

Для A14/A15 получен отрицательный результат достаточности native identity.
Продуктовые startup/offline-readd/crash сценарии по-прежнему не засчитаны.

После добавления диагностического инструмента и фиксации блокера выполнен:

```sh
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make check VENV=/opt/venv
git diff --check
```

Результат: Passed — 21 Markdown-файл, lint/format (42 Python-файла), 117 unit,
57 persistence, build, все прежние runtime/recovery/ZIP64 и оба daemon/core-web
smoke (по 20 enable/disable). Whitespace errors отсутствуют. Это подтверждение
сохранения работоспособности текущего каркаса, **не снятие identity-блокера**.
Полный x86_64 check этого диагностического шага не повторялся: product-код не
менялся, сам новый experiment отдельно пройден на x86_64, прежний полный ci-check
указан выше. После этой записи менялась только документация.

### Ранее зафиксированные ограничения

План не изменён: сделана первая зависимость фазы, не вся фаза. Repository принимает
instance_key от будущего доверенного identity-провайдера; **infohash/save path
недостаточны**. Durable identity, offline delete/re-add, NAS/backup portability
не объявлены решёнными и не заменены экспериментальным hardlink-witness.
Reconciliation обязан доказать замену/отсутствие, прежде чем вызвать forget;
неоднозначность не должна наследовать старую временную политику.

Пока продуктовая БД не открывается при enable, никакие реальные торренты не
становятся временными. Repository не заменяет авторизацию или path admission.
Сохранение лимита не означает наличие полной схемы HTTP/listener конфигурации.
Unit/source integration не выдаются за установку artifact или GitHub CI.

## Следующий шаг

После согласования capability-контракта (или подтверждения альтернативы)
продолжить фазу 1: durable identity/reconciliation и сервис управления политикой
с path/owner admission, затем подключить worker к lifecycle Core, реализовать
управляющие RPC/status fields и полную схему конфигурации. Web entry point и
checks.yml уже подготовлены; продуктовые policy/restart/crash проверки ещё нужны.
Не переходить к фазе 2 до завершения полного DoD фазы 1.
