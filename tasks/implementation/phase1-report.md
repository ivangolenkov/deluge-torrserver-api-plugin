# Отчёт: фаза 1 — core/web, persistence, lifecycle и CI

Статус: **Завершена**, 29 октября 2026 года.
Основание: [план фазы 1](../phase1-core-persistence-and-ci.md), SPEC и
[отчёт фазы 0](phase0-report.md). Предыдущие незакоммиченные изменения сохранены.
Подробности промежуточных исследований и проверок — в [истории](phase1-history.md).

## Результат и согласованные изменения

SQLite подключена к Core. Есть versioned RPC, owner/admin-проверки, status fields,
управление временным/постоянным и опубликованным/неопубликованным состоянием,
монотонный FIFO, aliases, metadata/viewed repository и схема настроек. Очистка
payload, TorrServer handlers, streaming QoS и download endpoints не включаются.
Core/web entry points и script поставляются одним egg. Web JSON проверяет
совместимость частей и работает отдельно от daemon без общего downloads-диска.

Пользователь согласовал два уточнения, отражённых в SPEC, AGENTS, планах и README:

1. **Явное отключение плагина** отзывает временность, публикацию и экземплярные
   связи metadata/viewed. После enable — новые постоянные неопубликованные
   записи. Настройки и FIFO-counter не сбрасываются, payload не удаляется.
   Обычный stop/start deluged сохраняет политику, record_id, FIFO и метаданные.
2. **После crash** временность/FIFO сохраняются, но прежняя публикация и будущая
   автоочистка требуют явного подтверждения администратора. Признак сохраняется
   через последующие clean restart. Подтверждение связано с актуальным каталогом
   и повторной проверкой путей. Это согласованное безопасное восстановление, не
   заявление об автоматическом доказательстве старой идентичности.

Hardlinks для политики **не введены**. Исторический hardlink-кандидат QoS остаётся
исследованием фазы 0, не Core-функцией. Num и полный UI/playback остаются отложенными
согласно прежнему указанию пользователя. [Обоснование identity](../../docs/instance-identity-decision.md).

## Реализованное поведение

- Четыре последовательные атомарные миграции; user_version, WAL, FULL, foreign
  keys и busy timeout. Более новая/неизвестная схема и corrupt DB не заменяются
  пустой БД. Ошибка enable не останавливает сам Deluge.
- Один DB worker, одно соединение на операцию, admission default 32 (максимум 128).
  SQLite и файловые проверки вне reactor. Отмена caller не отменяет post-commit
  cache/epoch-проверки coordinator; ресурсы удерживаются до фактического окончания.
- Дождаться SessionStarted/завершения native loading перед authoritative reconcile.
  Новые обычные торренты постоянные; запись отсутствующего торрента удаляется из БД,
  но native torrent не создаётся и payload не удаляется. Live add/owner change
  получает новый record_id. Worker-result проверяет актуальные epoch/key/owner.
- Переход в temporary получает новый сохраняемый ключ; идемпотентный повтор ключ
  не меняет. Publication независима. Existing-add primitive publish_existing
  разрешает aliases, публикует и может закрепить, но не понижает постоянство и не
  вызывает native add. Настоящие HTTP/source-add handlers — фаза 3.
- Actor копируется из текущей Deluge RPC session до первого yield. Shared даёт
  чтение, но не изменение. Операции требуют актуальный record_id; config/recovery
  доступны только admin. RPC возвращает состояния и коды, не payload.
- Временность допускается только после консервативной проверки плана всех payload
  путей: traversal, symlink, пересечения и hardlinks payload отклоняются. Это не
  готовый план удаления: фаза 2 обязана повторять проверки перед cleanup.
- Native daemon stop отличён от явного disable. Собственный before-shutdown
  trigger ждёт drain/join, включая auto-enabled plugin со Stopped component:
  Deluge PluginManager.stop сам не ждёт disable_plugins. Разным stop/disable
  observers возвращаются независимые Deferred chains. На частичном enable
  снимаются свои RPC, listeners, события, fields и worker.
- В SQLite есть dirty/recovery marker. Clean stop сверяет каталог перед clean
  marker; reset явного disable атомарен. Crash при locked SQLite/native re-add
  не разрешает старую публикацию автоматически. Подтверждение token/epoch устаревает
  при изменении каталога. Публикация различает requested и effective значения.
- Статусы stream_record_id/is_temporary/published/fifo_sequence/recovery_required
  доступны штатным status fields. Web-диагностика называется get_web_status, чтобы
  не перекрывать core get_status в общем JSON namespace.
- Валидируется полная schema config; unknown fields, bool вместо integer, неверные
  limits/owner/URL отклоняются атомарно. Policy-настройки действуют сейчас;
  HTTP/window/download fields staged для фаз 3–4 и не открывают продуктовый listener.
- Повтор sync после ошибки ограничен задержкой не менее 5 секунд. Ресурсные
  предохранители: 4096 torrent records, 10000 native file entries в path-проверке,
  до 128 статусов за RPC и 32 coordinator operations.

## Файлы

| Файлы | Назначение |
| --- | --- |
| deluge_stream/{repository,db_worker,config}.py | Миграции, SQLite policy/FIFO/aliases/metadata/viewed/settings, lifecycle marker и bounded dispatcher |
| deluge_stream/{lifecycle,policy_adapter,path_policy}.py | Coordinator, native catalogue/events/auth/runtime adapter, path admission |
| deluge_stream/{__init__,core,webui,web_adapter}.py, data/stream.js, setup.py | Core/web package, versioned RPC, status fields, diagnostics и packaged script |
| tests/persistence/* | 70 реальных SQLite/worker/reconcile/path/race проверок, включая 15 прежних ledger |
| tests/unit/test_policy_config.py, tests/unit/test_ci.py | Config и CI runner; весь unit-набор 132 |
| tests/runtime/{policy_smoke,policy_crash_probe,web_smoke,web_contract}.py | Реальные отдельные процессы/RPC и отдельно обозначенный fault injection |
| tests/runtime/probe.py, tools/runtime_check.py | Обязательные новые suites и registry/thread/timer/hook cleanup за 20 циклов |
| .github/workflows/checks.yml, tools/ci_check.py, Makefile | PR/push/manual/reusable workflow, те же make checks, логи и artifacts без release |
| tools/probe_instance_identity.py, docs/instance-identity-decision.md | Отрицательный native identity experiment и согласованное решение вместо hardlinks |
| README.md, SPEC.md, AGENTS.md, docs/{architecture,compatibility}.md, tasks/* | Актуальный контракт, статус, инструкции и отчётность |

## Исходный Definition of Done

- [x] Реализованы production package, адаптер, core/web entry points и корректный lifecycle. Egg установлен в реальные процессы; 20 циклов проверяют RPC/events/fields/listener/worker/timer/shutdown cleanup.
- [x] SQLite-модель и миграции сохраняют политику и не блокируют reactor. 70 persistence, реальный writer lock с продолжающимися reactor ticks и worker drain/cancel/barriers.
- [x] Переходы временности/публикации и aliases проверены; удаления по лимиту пока нет. Repository matrix и реальный owner RPC; native hybrid/full-v2 alias возвращает тот же record без второго torrent.
- [x] Restart/reconciliation и защита от старых callbacks проверены с настоящим Deluge. Clean restart сохраняет record/FIFO; SIGKILL включает согласованный confirmation gate; native remove/re-add отклоняет старый record, commit/re-add barrier отдельно детерминирован.
- [x] RPC и status fields имеют необходимые проверки прав и экземпляра. Admin/owner/shared/anonymous, stale record, version mismatch и invalid config проверены; core fields прочитаны настоящим native RPC.
- [x] Создан checks.yml с реальной сборкой, тестами и smoke через Makefile. Локальный ci-check прошёл, YAML/pins проверены ранее; tests расширены через Makefile/runtime_check, не YAML-дублированием.
- [x] CI не публикует релизы; artifacts пригодны для тестовой установки. Только contents:read; один egg в обоих отдельных профилях, script действительно выдаётся Web HTTP.
- [x] make check проходит; статус hosted run указан достоверно. ARM64 и эмулируемый x86_64 Passed. Git remote не настроен (`git remote` — пусто), hosted run отсутствует; по плану это внешняя непроверенная часть, обязательная до выпуска.
- [x] README содержит актуальные инструкции установки, сборки, тестов и CI. Добавлены RPC/config/recovery правила, отличие disable от restart и ограничения; работающие backend-вызовы подтверждены runtime.
- [x] Отчёт и индекс фаз обновлены, незавершённые проверки перечислены. Следующие фазы не объявлены выполненными.

## Итоговые проверки

Хост macOS ARM64, Docker Desktop Linux VM. ARM64 — нативное выполнение в VM,
x86_64 — эмуляция. Debian bookworm, Python 3.11.2, Deluge/deluge-web 2.2.0,
libtorrent 2.0.8.0, Twisted 24.11.0, SQLite 3.40.1, setuptools 75.8.0, wheel 0.45.1.
Runtime gate: Linux/Python 3.11, подтверждённые Deluge/libtorrent/Twisted и SQLite
не ниже 3.40.1; это не универсальная сертификация всех NAS/дистрибутивов.

Команды из корня проекта:

```sh
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make lint format-check test-unit test-persistence VENV=/opt/venv
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make check VENV=/opt/venv
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase0:arm64 python -I tests/runtime/probe.py --mode smoke --egg /work/dist/Stream-0.0.1-py3.11.egg
make ci-check CI_PLATFORM=linux/amd64 CI_IMAGE=deluge-stream-phase0:amd64
git remote
```

| Проверка | Факт |
| --- | --- |
| Unit / persistence | 132 / 70 Passed на обеих архитектурах |
| ARM64 make check | Passed, включая старые research/metadata/read/ZIP64 и новые policy suites |
| Дополнительный ARM64 probe | После расширения resource assertions: 20 циклов, ни одного своего field/handler/DB thread/timer/shutdown hook после disable |
| x86_64 ci-check | Docker build + полный make check Passed, включая расширенный probe, policy crash gate и отдельный core/web |
| Policy RPC/native daemon | Owner/admin/shared, FIFO, publication, full-v2 alias hybrid без дублирования, pristine state unchanged, live re-add/stale record, shared paths и corrupted DB отказ |
| Normal restart | Тот же record_id/FIFO/temp/public, автоматическое включение plugin native config сохранено |
| Crash | SIGKILL, следующий startup сохраняет temp/FIFO, published=false, requested=true, recovery_required=true; stale token отвергнут, текущий token подтверждается admin |
| Locked DB + re-add + crash | Реальный внешний SQLite writer lock, успешные native remove/re-add, SIGKILL до observer commit: same_record_id=true, temp=true, published=false, recovery_required=true; проверенный опасный случай заблокирован |
| Post-commit barriers | Реальная SQLite и ThreadPool, native catalogue здесь fake и явно обозначен: отмена caller не пропускает cache update; смена live key до callback отклоняет stale result и не повышает replacement |
| Separate web/core | Отдельные процессы/профили, установленный egg, script HTTP, authenticated JSON config и handshake, anonymous отказ, 20 enable/disable |
| Hosted GitHub | Не запускался, remote отсутствует; workflow/pins и local equivalent проверены, публикаций нет |

Итоговый x86_64 лог: build/ci/image.log и build/ci/check.log, генерируемые/ignored.
Docker build использовал cache; это не доказательство новой сетевой установки
APT/PyPI с нуля. Все runtime-профили создавались заново, тесты с network none.
Последние изменения после runtime — документационные; повторяются check-docs и
git diff --check, не весь набор без новой причины.

После итогового обновления документации выполнены:

```sh
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make check-docs VENV=/opt/venv
git diff --check
node --check deluge_stream/data/stream.js
make help
```

Все Passed: 22 Markdown-файла, существующие local targets, whitespace errors нет,
JavaScript синтаксически корректен; help безопасно перечисляет действующие цели.
Архив промежуточного отчёта сохранён как phase1-history.md, а не удалён.

## Приёмочные сценарии

| ID | Проверенная часть | Осталось вне этой фазы |
| --- | --- | --- |
| A01 | Artifact install, core/web по 20 циклов, свои registries/listeners/threads/timers/hooks освобождены, partial enable на corrupt DB не ломает Deluge | Полный FD/traffic stress и все HTTP/download операции |
| A11 | Четыре storage existing-add комбинации; реальный publish_existing/FIFO, hybrid alias без нового torrent | Новый TorrServer source-add/HTTP handlers фазы 3 |
| A12 | Owner/admin переходы, идемпотентность и новый FIFO, независимая публикация | Гонки с настоящей автоочисткой фазы 2 |
| A14 | Реальный live remove/re-add и stale record; реальный commit/cancel barrier; crash ambiguity блокируется до подтверждения | Tickets, playback/debounce callbacks будущих фаз |
| A15 | Migration/trigger rollback, locked DB, corrupt DB, SIGKILL между native add и DB commit, confirmation gate | Журнал физического удаления и завершённость удаления данных фазы 2 |
| A23 | Настоящие owner/admin/shared проверки, чужой record и anonymous web отказ | Download tickets/data endpoints фазы 4 |
| A24 | Реальные разные процессы/профили, версии/API и JSON config, общий downloads-диск web не нужен | Public URL/downloads и реальные разные машины, browser acceptance |
| A25 | ARM64 и эмулируемый x86_64 полные проверки | Физический x86_64 и конкретный NAS/load test |

Составные продуктовые сценарии целиком не объявлены закрытыми преждевременно.

## Ошибки, исправления и ограничения

Первый общий identity-подход был заблокирован; снят прямыми решениями пользователя,
а не предположением, что hash/added_time — UUID. После подключения Core happy-path
restart прошёл, но целевой lock/re-add/SIGKILL опыт выявил реальное небезопасное
наследование. Пользователь выбрал crash confirmation. Повторный обязательный
сценарий на обеих архитектурах доказывает блокировку, не автоматическую identity.

Предварительные прогоны выявили shutdown timeout auto-enabled Core и общий
Deferred stop/disable; добавлены собственный awaited shutdown trigger и независимые
observers. Очередные прогоны и 20 resource assertions прошли. Прочие найденные
lint/import/long-line ошибки исправлены до итогового check. Нет успешных skips.

Не реализованы: cleanup/refcounts/защита передач, product QoS/recovery overrides,
TorrServer endpoints, downloads/ZIP UI и полный Web UI. Нет обязательных hardlinks
для policy. Confirmation после crash не является разрешением слепо применить
старый QoS-журнал к другому экземпляру: это отдельный контракт фазы 3.

Metadata/viewed persistence проверена repository-тестами, не выдана за готовые
HTTP viewed handlers. Config HTTP/window/download поля staged; реальный probe
остаётся loopback/admin research. Временность запрещается при metadata_pending
или невозможности ограниченно доказать безопасные пути. Backup/restore и offline
редактирование native/SQLite по отдельности не сертифицированы. NAS и реальный
browser UI не проверены. Имеющиеся pins не равны полному APT snapshot/hashes lock.

Изменения не коммитились и не отправлялись; release tags/assets не создавались.

## Следующий шаг

Фаза 1 завершена по уточнённому контракту. Следующая — фаза 2: безопасная FIFO
очистка и защита передач. Cleanup должен учитывать recovery_required и заново
проверять владение путями; текущий phase-1 path admission не заменяет deletion plan.
Фаза 2 в этом запросе не начиналась.
