# Отчёт: фаза 3 — начальный QoS/resume gate

Исторический статус: **Заблокирована**, 29 октября 2026 года.
Впоследствии пользователь снял блокер: residual native piece priorities допустимы,
их восстановление и QoS journal не нужны. Текущий [отчёт](phase3-report.md).
Основание: [план](../phase3-torrserver-and-streaming.md), SPEC §§5–7, 10–12, 14,
[итог фазы 2](phase2-report.md). Пользователь разрешил выполнить фазу целиком либо
до вопроса/блокера. Незакоммиченные изменения фазы 2 сохранены, не откатывались.

## Что сделано

До подключения production QoS проверен потенциально более простой способ избежать
загрязнения resume-data: `set_piece_deadline` и немедленный возврат priority к
актуальной базе. Новый native эксперимент работает с установленным egg, настоящей
Deluge session и отдельным local seed только для fixture. Публичный swarm не нужен.

Последовательность на libtorrent 2.0.8:

1. Base file/piece priority = 4, выбранный piece ещё не загружен.
2. `set_piece_deadline(piece, 0)` даёт piece priority 7.
3. `piece_priority(piece, 4)` возвращает базу; снятый через native alert resume
   snapshot действительно содержит priority 4 (или стандартное отсутствие vector).
4. Подключён local seed, piece получен и проверен libtorrent. Весь торрент ещё
   не завершён; тест не полагается на seed shortcut с отсутствующим picker.
5. Без нового изменения priority со стороны плагина после завершения piece
   native resume snapshot содержит **1**, хотя file priority остался **4**.

Это новый runtime результат, а не только пересказ фазы 0. Он исключает выбранный
«не сохраняющий overrides» shortcut, но **не доказывает невозможность другого
решения**. Product streaming handlers/QoS не включались; исследование восстанавливает
тестовый priority перед shutdown. SQLite schema и production modules не изменялись.

## Почему возник вопрос

В фазе 0 уже проверен journalling candidate с retained coverage: record UUID,
witness, touched pieces, admission epochs и acknowledgement, который не стирает
recovery coverage преждевременно. Он остаётся исследовательским, не подключён к
Core и не является production identity/reconciliation protocol.

Для активного процесса мы можем проверять native объект и record_id. После crash
между native remove/re-add и commit плагина такая проверка не восстанавливается
автоматически. Existing native `policy_crash_probe.py` демонстрирует ситуацию,
когда новый экземпляр того же hash после restart получает прежний policy record_id.
Это согласованно для временности/FIFO: публикация и cleanup закрыты до admin
confirmation. Но план фазы 3 прямо запрещает трактовать эту confirmation как
доказательство identity для replay прежнего QoS.

Поэтому нельзя молча подключить старый журнал и восстанавливать snapshot начала
стрима. Гарантии удаления Deluge достаточны и не пересматриваются: здесь другой
контракт — кто разрешает менять приоритеты актуального native экземпляра.

## Вопрос и варианты

Нужно выбрать семантику при неоднозначной identity после crash:

1. **Разрешить нормализацию QoS по текущей политике Deluge**, а не старому snapshot:
   журнал задаёт только множество потенциально затронутых pieces, значения заново
   выводятся из актуальных file priorities и first/last. Явно допускается применение
   к текущему экземпляру того же hash даже после возможного re-add. Нужно выбрать
   автоматическую нормализацию после загрузки native catalogue либо отдельный
   admin confirmation для выбранных торрентов, не расширяя молча смысл
   существующего policy confirmation.
2. **Требовать доказанную прежнюю identity для автоматического восстановления**:
   продолжить разработку отдельного persistent instance/reconciliation механизма
   для QoS. Hardlinks/witness не объявляются автоматически обязательными и не
   подменяют согласованный lifecycle политики. До доказательства identity
   неоднозначный журнал нельзя применять к replacement.

Первый вариант — предложение изменения/уточнения границы recovery, не уже
реализованная гарантия. Если выбран manual путь, отдельно фиксируется, что до
подтверждения могут сохраняться старые native overrides и как они показываются
администратору. Ни global reset всех pieces/deadlines, ни стирание журнала, ни
silent отказ от QoS не предлагаются как выполненная фаза.

## Изменённые файлы

| Файл | Изменение |
| --- | --- |
| `tests/runtime/qos_resume_probe.py` | Новый native completion/resume эксперимент с local seed |
| `tools/runtime_check.py` | Эксперимент включён в make test-integration/check и существующий CI |
| `tasks/phase3-torrserver-and-streaming.md`, `tasks/README.md` | Старт фазы, затем конкретный блокер |
| `README.md`, `SPEC.md`, `tasks/implementation/README.md`, этот отчёт | Честные границы, статус и следующий вопрос; требования не менялись |

## Исходный Definition of Done

Ни один продуктовый пункт пока не закрыт:

- [ ] Требуемый TorrServer-контракт реализован и проверен fixtures и реальными Lampa/Num.
- [ ] Незавершённый файл воспроизводится; Range/HEAD/seek возвращают корректные статусы, заголовки и байты.
- [ ] Окно pieces продвигается в одном длинном GET; отмена прекращает ненужную работу.
- [ ] Несколько playback/preload-сессий согласованно управляют QoS и защитой от cleanup.
- [ ] Пользовательские priorities/pause/queue сохраняются, включая disable и crash/resume.
- [ ] Backpressure, лимиты буферов и неблокирующее выполнение подтверждены.
- [ ] Наблюдаемое состояние и диагностика доступны web-части через RPC/status fields.
- [ ] make check проходит; новые автоматические проверки включены в CI.
- [ ] README описывает проверенное подключение и ограничения streaming.
- [ ] Отчёт и индекс фаз обновлены; частичные и ручные проверки явно обозначены.

Последние пункты тоже оставлены открытыми: отчёт о gate не заменяет отчёт о полной
реализации, а regression существующего кода не заменяет phase-3 acceptance.

## Проверки

Debian 12 Linux Docker ARM64; Python 3.11.2, Deluge 2.2.0, libtorrent 2.0.8.0,
Twisted 24.11.0, SQLite 3.40.1. Disposable profile, `--network none`. Использован
egg, собранный при финальной проверке фазы 2: production code с тех пор не менялся.

```sh
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 /opt/venv/bin/python -m ruff format tests/runtime/qos_resume_probe.py
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 /opt/venv/bin/python -I /work/tests/runtime/qos_resume_probe.py --egg /work/dist/Stream-0.0.1-py3.11.egg
docker run --rm --platform linux/amd64 --network none -v "$PWD:/work" deluge-stream-phase0:amd64 /opt/venv/bin/python -I /work/tests/runtime/qos_resume_probe.py --egg /work/dist/Stream-0.0.1-py3.11.egg
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make check VENV=/opt/venv
```

Native probe **Passed** на ARM64 и эмулируемом x86_64, одинаковый результат:

```json
{"experiment":"deadline_immediate_repair_then_native_completion","base_file_priority":4,"deadline_priority":7,"resume_after_immediate_repair":4,"resume_after_native_completion":1,"instant_repair_is_persistence_safe":false,"scope":"native local seed, not product QoS"}
```

Crash самого нового shortcut не симулировался: уже native resume snapshot содержит
неправильную базу, чего достаточно для отклонения гипотезы о чистом сохранении.
Общий ARM64 `make check` **Passed**: 152 unit + 80 persistence, lint/format,
25 Markdown, build, все текущие native suites (включая новый probe), ZIP64,
daemon/core-web smoke и 20-cycle resource checks. Existing policy_crash_probe
повторно подтвердил `same_record_id=true` после native re-add и SIGKILL при
заблокированном SQLite commit; recovery_required сохранился.

Новый probe отдельно **Passed** на эмулируемом x86_64. Полный x86_64 regression
для этой частичной фазы заново не запускался: production code не изменялся, его
предыдущий полный результат — в phase2-report.md. Hosted CI не запускался.
Эти зелёные проверки **не означают готовности фазы 3**.

После обновления документации также выполнены:

```sh
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make check-docs VENV=/opt/venv
git diff --check
```

Passed: 25 Markdown-файлов, local targets существуют, whitespace errors нет.

## Сценарии и границы приёмки

| Сценарий | Проверенная часть | Не проверено |
| --- | --- | --- |
| A02–A08 | Только прежний phase-0/2 reader и fixtures, не новая реализация | Product add/list/get/preload/play/seek и реальные Lampa/Num |
| A10 | Существующий phase-2 lease lifecycle | Product preload/playback QoS |
| A16 | Новый native counterexample чистому resume при active deadline | Production восстановление/disable/crash новой QoS реализации |
| A20 | Нет нового продуктового результата | Phase-3 backpressure/отмена/бюджеты при нескольких клиентах |
| A21 | Нет нового продуктового результата | Product QoS против pause/recheck/move/remove/disk errors |
| A26 | Новый код не добавляет native mutation ради места | Product streaming errors при disk-full |

## README / Makefile / CI

README не объявляет подключение Lampa/Num или TorrServer listener рабочими.
Makefile уже вызывает tools/runtime_check.py; новая реальная suite входит через
общий список. checks.yml вызывает те же make-цели, дублировать YAML не требуется.
Hosted run не запускался, remote не настроен; ничего не публиковалось и не
устанавливалось в пользовательский Deluge. Клиентская phase-3 приёмка не выполнена:
она не заменяется этими HTTP/native probes.

## Следующий шаг

Получить выбор правила crash-recovery QoS, затем реализовать bounded journal и
coordinator в продуктовой SQLite, после чего переходить к reader и HTTP handlers.
Фаза 4 не начиналась. Не изменять согласованные требования ради скрытого упрощения.
