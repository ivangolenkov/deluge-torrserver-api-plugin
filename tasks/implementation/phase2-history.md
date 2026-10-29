# Отчёт: фаза 2 — защита передач и безопасность FIFO-удаления

Исторический статус: **Заблокирована**, 29 октября 2026 года.
Блокер впоследствии снят пользователем: штатных гарантий Deluge достаточно.
Текущий статус — в [отчёте](phase2-report.md); ниже сохранён ход исследования.
Основание: [план фазы 2](../phase2-retention-and-transfer-lifecycle.md),
[SPEC](../../SPEC.md), [предыдущий отчёт](phase1-report.md).
Работа разрешена до завершения фазы либо вопроса/блокера. Дерево в начале
фазы было чистым; изменения этой фазы не закоммичены и не опубликованы.

## Что сделано

Подготовлены отдельные компоненты, **не подключённые к автоматическому удалению**:

- `TransferRegistry`/`Lease`: пять видов защиты (playback/preload/file/ZIP/ticket),
  одноразовый release, per-kind limits и общий cap 128, idle expiry, byte-progress,
  атомарное превращение ticket в file/ZIP без снятия защиты, debounce с token,
  retirement по record_id и shutdown без оставшихся таймеров.
- Worker-only manifest всех native paths, включая pads и reserved partfile;
  проверки inode/родителей, пересечений, symlinks, изменения manifest и отсутствия
  файлов. Код ничего не удаляет. **Manifest не является достаточным разрешением
  native delete**, что явно отмечено в модуле.
- Миграция SQLite 4 → 5 и ограниченный journal removals без FK на policy:
  reconcile не стирает свидетельство незавершённой операции. Стадии
  prepared/submitting/submitted/completed/manual/aborted; одна незавершённая
  операция, manifest до 2 MiB, bounded terminal audit tail (32).
  Завершение удаляет только прежний record UUID, а не replacement по hash.
  Explicit disable сохраняет неоднозначное удаление как manual.
- Адаптер получает полный native file catalogue и умеет ожидать matching-handle
  deletion alert через существующий AlertManager. Принятие команды отдельно от
  подтверждения дискового удаления. Собственные handlers снимаются при close.
- Изолированный native research probe выявил ограничение snapshots.

`PolicyService`, исследовательский HTTP probe и RPC пока не используют эти
компоненты. Нет работающего FIFO/coordinator, retry, admin-resolution или
startup-verification removals. Ни один пользовательский handler не вызывает
новый метод remove_with_data. Phase-1 RPC и lifecycle сохраняются.

## Конкретный блокер и воспроизведение

ТЗ требует одновременно учитывать текущие native paths, не удалять чужое и
выполнять удаление через Deluge с `remove_data=True`. В текущем решении отсутствует
барьер между filesystem preflight и асинхронными native rename/delete jobs.
Нельзя заменять этот барьер повторным чтением metadata или проверкой уже после
удаления.

Проверено на реальном Deluge/libtorrent, не mock:

1. Создан один локальный payload `original.bin` в disposable download directory.
2. После завершения initial checking поставлен `handle.rename_file(0, "renamed.bin")`.
3. Сразу прочитан `handle.torrent_file().files().file_path(0)`:
   снимок всё ещё содержит `original.bin`.
4. Через настоящий `Core.remove_torrent(tid, True)` принято удаление.
5. Получены matching-handle rename и `torrent_deleted_alert`; `renamed.bin` тоже
   отсутствует. Native удаление вышло за множество путей прежнего снимка.

Это эксперимент порядка операций, **не детерминированный disk-worker barrier**:
между Python-вызовами работает native thread. Результат является runtime
контрпримером достаточности manifest, но не доказывает, что чужие данные были
удалены (их в опыте нет), и не заменяет всю будущую race-suite.

Сверка исходников libtorrent 2.0.8 объясняет результат:

- [torrent.cpp](https://github.com/arvidn/libtorrent/blob/v2.0.8/src/torrent.cpp):
  `rename_file` отправляет async disk job; `on_file_renamed` меняет metadata после
  его результата.
- [storage_utils.cpp](https://github.com/arvidn/libtorrent/blob/v2.0.8/src/storage_utils.cpp):
  `delete_files` удаляет native file_storage paths, partfile и пустые подкаталоги,
  а не проверенный плагином manifest.

Исходники — объяснение, не замена runtime-проверки. Каталог, pause и inode preflight
не объявляются блокировкой всех чужих операций Deluge. Обязательный monkey patch,
подмена Core или перехват всех пользовательских rename/move не введены.

## Изменённые файлы

| Файл | Назначение |
| --- | --- |
| `deluge_stream/transfers.py` | Отдельная модель leases/защиты |
| `deluge_stream/deletion.py` | Черновой manifest и read-only verification |
| `deluge_stream/repository.py` | Миграция 5 и durable removal journal |
| `deluge_stream/policy_adapter.py` | Полный native catalogue, alerts, removal adapter |
| `tests/unit/test_transfers.py` | Clock-driven lease/ticket/debounce проверки |
| `tests/persistence/test_deletion.py` | Настоящая SQLite и временные filesystem fixtures |
| `tests/runtime/native_deletion_probe.py` | Изолированный native ordering experiment |
| `README.md`, `SPEC.md`, `tasks/README.md` | Честный статус; требования сохранены |
| `tasks/phase2-retention-and-transfer-lifecycle.md`, `tasks/implementation/README.md`, этот отчёт | Блокер, промежуточный результат и DoD |

## Статус исходного Definition of Done

Все пункты пока **не выполнены полностью**; тесты отдельных компонентов не
приравниваются к продуктовой интеграции.

- [ ] Все типы передач используют общий допуск и защиту экземпляра.
  Модель проверена отдельно; handlers/coordinator не подключены.
- [ ] FIFO удаляет старейшего только при превышении количества и соблюдает защищённую голову очереди.
  Очистка не включена из-за path-race.
- [ ] Смена постоянства и старт передачи согласованы с удалением; нет double release и утечек защиты.
  Отдельные lease-тесты не заменяют проверку coordinator.
- [ ] Ошибки add/cleanup не смешиваются; retry ограничен и останавливается при shutdown.
  Cleanup/retry не реализованы.
- [ ] Shared paths и небезопасные пути не приводят к удалению чужих файлов.
  Preflight имеет тесты, но native pending rename не ограничен manifest.
- [ ] Crash/частичное удаление/внешнее удаление корректно согласуются с журналом и Deluge.
  SQLite journal проверяется отдельно; runtime recovery не подключён.
- [ ] Проверены A09–A15/A22 и относящаяся часть A26; ограничения fake-передач указаны.
  Полная приёмка не выполнена; см. ниже.
- [ ] Makefile и checks.yml включают новые обязательные проверки; make check проходит.
  Автосбор test-unit/test-persistence включает новые тесты; native research probe
  запускается отдельно, полный phase-2 набор ещё отсутствует.
- [ ] README описывает фактическую политику и восстановление; отчёт и индекс фаз обновлены.
  Документы обновлены для частичного результата; рабочего восстановления ещё нет.

## Проверки

Среда: Docker Debian 12 Linux ARM64; Python 3.11.2, Deluge 2.2.0,
libtorrent 2.0.8.0, Twisted 24.11.0, SQLite 3.40.1. Публичного swarm нет,
профиль пользователя не используется, `--network none`.

Выполненные команды (из корня проекта):

```sh
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make build VENV=/opt/venv
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 /opt/venv/bin/python -I /work/tests/runtime/native_deletion_probe.py --egg /work/dist/Stream-0.0.1-py3.11.egg
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 /opt/venv/bin/python -m pytest -q tests/unit/test_transfers.py tests/persistence/test_deletion.py
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make check VENV=/opt/venv
docker run --rm --platform linux/amd64 --network none -v "$PWD:/work" deluge-stream-phase0:amd64 make check VENV=/opt/venv
```

Build Passed. Native probe после исправления initial-check wait Passed и напечатал:

```json
{"snapshot":"original.bin","native_rename":["renamed.bin"],"delete_result":"deleted","renamed_path_absent":true,"conclusion":"native deletion is not confined to snapshot paths"}
```

Первый native запуск Failed: paused initial checking не завершался; harness
исправлен resume → ожидание native state → pause. Первый targeted pytest:
26 passed, 1 failed из-за неверного ожидания `Repository.get` для отсутствующего
UUID; исправлено ожидание Conflict. Первые lint/format выявили форматирование
новых файлов, исправлено.

Итог ARM64 `make check`: **Passed**, 150 unit + 79 persistence, lint/format/docs,
build, native integration/restart/crash gate/20-cycle resource checks, ZIP64,
установка egg в настоящий daemon и отдельный deluge-web smoke. Новый targeted
набор полностью включён в эти 150/79 (18 новых unit, 9 новых persistence).
Docs на этом запуске прочитаны до добавления нового отчёта; после финального
обновления документации отдельно выполнена команда ниже: Passed, 23 Markdown,
все local targets существуют. `git diff --check` также Passed. Это регрессия текущего
кода, **не выполнение всего DoD фазы 2**.
Итог эмулируемого x86_64 `make check`: **Passed**, те же 150 unit + 79 persistence,
lint/format/23 Markdown, build, весь текущий native integration и core/web smoke.
Native rename/delete research probe запускался отдельно только на ARM64;
не приравнивается к phase-2 приёмке на обеих архитектурах.

```sh
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make check-docs VENV=/opt/venv
git diff --check
```

## Приёмочные сценарии

| ID | Проверенная часть | Что не проверено |
| --- | --- | --- |
| A09 | Нет полного phase-2 результата | Автоочистка не подключена |
| A10 | Пять видов leases отдельно, expiry/release/debounce/ticket conversion | Native FIFO/coordinator, реальные HTTP downloads/preload/ZIP |
| A11 | Regression повторного add/постоянства фазы 1 | Взаимодействие с новой очисткой |
| A12 | Regression двусторонней смены политики фазы 1 | Успешное закрепление против cleanup |
| A13 | Нет полного phase-2 результата | Native coordinator-races не реализованы |
| A14 | Отдельные lease retirement и journal completion не затрагивают новый UUID | Native delete/re-add против нового допуска/ticket/release |
| A15 | SQLite durable intent после reconcile/reopen; disable оставляет manual | Crash между native стадиями, частичный отказ, bounded retry |
| A22 | Manifest/shared-pad/partfile/symlink checks и native ordering counterexample | Полная безопасность native удаления не доказана; это блокер |
| A26 | Новых disk/TTL/size triggers нет, очистка вообще не вызывается | Продуктовая FIFO-проверка low-space ещё невозможна |

## README, Makefile и CI/CD

README явно сохраняет отсутствие автоочистки и описывает блокер. Makefile и
checks.yml не требуют изменения для автосбора новых unit/persistence тестов:
существующие make-цели собирают каталоги целиком, CI вызывает те же цели.
Native ordering probe не включён в обязательный CI: это исследование с native
scheduling, а не детерминированный критерий готовности. До завершения фазы
обязательный native набор должен быть добавлен. Hosted GitHub Actions не запускался;
релиз, тег и публикация не выполнялись. Чистый checkout с bootstrap заново не
проверялся; используется прежний runtime image и собранный текущий egg.

## Отклонения и следующий вопрос

Требования не ослаблены, фаза 3 не начата. Работа остановлена перед подключением
опасной операции. Дополнительно не подтверждены partfile naming для v2/hybrid,
native deleted-alert matching после всех вариантов re-add, bounded retry и
manual resolution. Возможности адаптера являются заготовкой, не готовым FIFO.

Нужно согласовать дальнейшее направление: разрешить подготовить изменение
дизайна/контракта — native удаление torrent-записи без данных, затем отдельное
проверяемое удаление данных worker-ом с защитой путей/identity и отдельной
race-suite — либо сохранить обязательный `remove_data=True` и продолжить поиск
доказуемого native барьера. Первый вариант **пока только предложение**, его
безопасность ещё нужно обосновать; unlink произвольных сохранённых путей,
recursive delete общего каталога и обязательный monkey patch не предлагаются
как готовое решение. До выбора и доказательства безопасного дизайна очистка
остаётся выключенной.
