# Отчёт: фаза 6 — метаданные Lampa в Web UI

Статус: **Завершена**, 31 октября 2026 года.
Основание — [план](../phase6-metadata-webui.md), SPEC §9.4; прочитаны отчёты фаз
3, 4 и 5. Пользователь прямо разрешил реализацию до полного DoD фаз 3/5;
их незакрытые проверки не отменяются.

## Реализованное поведение и файлы

- `repository.py`: миграция schema 6 → 7 без изменения body, UUID-токен версии,
  атомарный CAS/patch, обновление токена всеми путями записи, защита от ABA.
  Произвольный строковый `data`; сохранённый JSON по-прежнему ≤65536 байт UTF-8.
- `lifecycle.py`, `core.py`: авторизованные get_metadata/set_metadata API 1,
  owner/admin для чтения и записи, bounded admission, policy lock, worker,
  повторные проверки экземпляра/прав/epoch, безопасные публичные ошибки.
- `data/stream.js`: форма во вкладке Stream, явные JSON-инструменты, сохранение
  черновика при конфликте/ошибке, подтверждение потери правок, проверка версии
  core/web, no-HTML/no-poster-fetch, игнорирование late callbacks закрытого окна.
  Baseline формы не превращает браузерную нормализацию CRLF в запись в БД.
- `PolicyService._record`: запрос устаревшего record_id не инвалидирует корректный
  каталог нового экземпляра. Это выявлено native re-add проверкой; старый редактор
  получает conflict без принудительного reconcile нового торрента.
- `Deluge.stream.error`: строковые коды handshake теперь также переводятся
  в безопасные сообщения (включая incompatible_core), а не теряются.
- `tests/persistence/test_metadata_editor.py`, `tests/unit/test_metadata_service.py`:
  CAS, rollback, лимиты/Unicode, миграция, no-op/ABA, очередь/cancel/late results.
- `tests/runtime/policy_smoke.py`, `streaming.py`, `web_browser.py`, `split_node.py`:
  расширены native/RPC/HTTP/Chromium сценарии; `delivery_lifecycle.py` учитывает
  schema 7 при подготовке старого schema-5 fixture.
- README, SPEC, architecture, delivery, acceptance, AGENTS, планы и индексы обновлены. Makefile и checks.yml
  не требуют новых целей: тесты добавлены в существующие unit/persistence,
  integration, test-web, test-web-split и delivery, уже вызываемые check/CI.

## Definition of Done исходной фазы

- [x] В Web UI доступны просмотр и редактирование всех четырёх сохранённых полей.
  Первый настоящий Chromium-прогон успешно проверил форму и сохранение.
- [x] Произвольный `data` сохраняется без потерь; JSON-инструменты только явные.
  Persistence round-trip и Chromium text/JSON/нетронутый CRLF.
- [x] Core проверяет owner/admin, типы, лимиты, экземпляр и версию metadata.
  Unit/persistence и native policy smoke с admin/alice/bob (shared).
- [x] Конкурентный `add/set` или второй редактор не теряет изменения молча.
  Persistence: два потока, set/ABA, source-add commit; Chromium: второй RPC-клиент.
- [x] Ошибка commit, remove/re-add и недоступность core не дают ложного успеха.
  SQLite trigger rollback, unit late results, native re-add и browser fault injection.
- [x] TorrServer читает обновлённую запись; его прежние операции совместимы.
  Native streaming.py: HTTP add → RPC read/update → get/list → HTTP set → CAS reject.
- [x] Restart сохраняет правки; явный disable следует прежнему контракту.
  Native policy smoke: правки owner/admin, обычный restart и явный disable/reset.
- [x] Не меняются файлы, пользовательские options, политика и streaming-state.
  Native streaming.py сравнивает options/policy; reader/QoS/download регрессии прошли.
- [x] Chromium и split core/web проверены с установленным artifact; XSS не исполняется.
  ARM64/3.11 и x86_64/3.14 split прошли, включая расширенный fault-прогон.
- [x] Новые проверки входят в существующие make-цели/CI; обязательный make check проходит.
  Полные check ARM64/3.11, x86_64/3.11 и x86_64/3.14 Passed; hosted run не выполнялся.
- [x] README, отчёт и индекс обновлены; A27 и связанные части A14/A15/A23/A24
  подтверждены фактическими результатами, ограничения клиентской приёмки указаны.

## Среда, команды и результаты

Host macOS ARM64, Docker Linux ARM64, image `deluge-stream-phase5-check:arm64`.
Настоящий Chromium: 156.0.8078.73; установленные core/web egg в одноразовых
профилях. Сети публичного swarm не использовались (`--network none`).
Python 3.11.2, Deluge 2.2.0, libtorrent 2.0.8.0, Twisted 24.11.0,
SQLite 3.40.1, setuptools 75.8.0.
Второй профиль: image `deluge-stream-py314-check:amd64`, Linux x86_64 (эмуляция),
Python 3.14.8, Deluge 2.2.0, libtorrent 2.0.14.0, Twisted 26.10.0, SQLite 3.54.0.
Chromium этого профиля: 156.0.8078.79. Третий запуск — тот же Python 3.11 стек,
но Linux x86_64, image `deluge-stream-phase5-check:amd64`, Chromium 156.0.8078.73.
Все профили одноразовые; пользовательские downloads, Deluge и SQLite не менялись.

```sh
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase5-check:arm64 make test-unit test-persistence VENV=/opt/venv
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase5-check:arm64 make lint format-check test-web VENV=/opt/venv
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase5-check:arm64 make test-web VENV=/opt/venv
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase5-check:arm64 make check VENV=/opt/venv
docker run --rm --platform linux/amd64 --network none -v "$PWD:/work" deluge-stream-py314-check:amd64 make check VENV=/opt/venv BUILD_DIR=build/phase6-py314 DIST_DIR=dist/phase6-py314
docker run --rm --platform linux/amd64 --network none -v "$PWD:/work" deluge-stream-phase5-check:amd64 make check VENV=/opt/venv BUILD_DIR=build/phase6-py311-amd64 DIST_DIR=dist/phase6-py311-amd64
make test-web-split CI_IMAGE=deluge-stream-phase5-check:arm64 CI_PLATFORM=linux/arm64
make test-web-split CI_IMAGE=deluge-stream-py314-check:amd64 CI_PLATFORM=linux/amd64 CI_PYTHON=3.14 DIST_DIR=dist/phase6-py314
```

- Unit: **312 passed**, persistence: **99 passed** (включая регрессию старого record_id).
- ARM64/Python 3.11 полный `make check` **Passed**, включая docs/lint/format,
  312 unit + 99 persistence, весь native integration, downloads/ZIP64, Chromium,
  direct-read, установленный core/web smoke, packaging, delivery и candidate bundle.
  Первый успешный delivery выводил историческую строку `migration5-to6`, но
  фактически проверял schema 7; подпись исправлена на `migration5-to7`.
- x86_64/Python 3.14 полный `make check` **Passed**: те же 312 unit + 99 persistence,
  native/HTTP/RPC, metadata Chromium helper, reader/download/ZIP, smoke, packaging,
  delivery schema5→7 и candidate bundle. Product source одинаков для обоих artifacts;
  build/dist для Python 3.14 отдельные. Публикация не выполнялась.
- x86_64/Python 3.11 полный `make check` **Passed**: 312 unit + 99 persistence,
  native integration, Chromium, downloads/direct-read/ZIP64, smoke, packaging,
  delivery schema5→7 и candidate bundle. Его build/dist также отдельные.
- Первый browser run остановился на неоднозначном test locator после успешных
  сохранений/конфликта; locator исправлен. Следующий `make test-web` **Passed**:
  text/JSON, CRLF, XSS, конфликт/черновик, отмена, HTTP-off и прежние UI downloads.
- Локальный `make check-docs` при планировании не запускался из-за отсутствия
  `.venv/bin/python`; последующие проверки выполняются с VENV=/opt/venv в Docker.
- Расширенный Chromium-прогон прошёл: browser fault injection storage error и
  version mismatch сохраняет черновик; повторное нажатие во время pending-save
  не отправляет второй RPC, закрытое окно игнорирует callback, смена выделения
  не меняет адресата записи. Fault injection не подменяет проверку реального БД-сбоя.
- `make test-web-split CI_IMAGE=deluge-stream-phase5-check:arm64 CI_PLATFORM=linux/arm64`
  **Passed**: два контейнера без общего payload FS, полный metadata helper,
  HTTP-off RPC и прежние file/ZIP downloads.
- Split x86_64/Python 3.14 также **Passed**, включая metadata fault helper и
  downloads с реальным Chromium в web-контейнере без файловой системы core.
- Отдельный native `streaming.py --egg /work/dist/Stream-0.0.0.dev0-py3.11.egg`
  **Passed**: настоящий HTTP add → RPC read/update → HTTP get/list → HTTP set
  → stale CAS rejection, неизменность native options и policy.
- Первый `make check` остановился в native policy smoke после отказа старому
  record_id: его проверка инвалидировала уже корректный каталог нового экземпляра.
  Исправлено разделение старого запроса и устаревшего native-каталога; unit-регрессия
  добавлена. Итоговый check запущен повторно по причине этого исправления.
- Повторный ARM64 check успешно прошёл unit/persistence, весь native integration,
  downloads/ZIP64 и metadata browser helper, но был остановлен внешним таймаутом
  инструмента 120 секунд до завершения остальных целей. Это не успешный make check;
  следующий успешный запуск выполнен без внешнего таймаута, код не менялся.
- Первый x86_64/3.14 check также остановлен внешним таймаутом 120 секунд во время
  integration после успешных unit/persistence/native policy и retention. Повторный
  полный запуск успешно выполнен без внешнего таймаута. Оба таймаута — ограничения
  инструмента запуска, не успешные проверки и не успешные skips.
- Расширенные browser fault-тесты сначала выявили отсутствие перевода строкового
  handshake-кода; исправлено. В самом тесте исправлены восстановления функций:
  Playwright исполняет function-valued assignment expression, поэтому восстановление
  выполняется внутри явной функции без возвращаемого RPC callable.

## Приёмка и оставшиеся границы

- A27 подтверждён автоматизированной частью: SQLite/CAS, установленный редактор
  Chromium, native HTTP/RPC, restart/disable и полные профильные checks.
- A15 metadata-часть: SQLite trigger fault/rollback проверен, настоящий ENOSPC нет.
- A14 metadata-часть: native same-hash re-add отклоняет старую форму, новая запись пуста.
- A23 metadata-часть: native admin/owner разрешены; shared bob не читает и не пишет.
- A24 metadata-часть: ARM64 split подтверждён, общий payload FS и HTTP не требуются.
- Настоящий интерфейс Lampa/Num с новым редактором пока не проверялся.
  HTTP-интеграция не будет называться такой проверкой. Кэш клиента может быть старым.
- JSON-форматирование явно использует JavaScript number: большие числа могут
  терять точность. Для точного исходного текста форматирование не применять.
- Установка в пользовательский профиль, hosted CI и публикация не выполнялись.

Отклонения: сохранён прежний общий лимит JSON 64 КиБ вместо введения новых
пользовательских настроек; отдельные make-цели и CI YAML не потребовались.
Использован отдельный UUID-токен metadata, не revision политики. JSON-форматирование
имеет документированную числовую модель JavaScript. Полная клиентская приёмка
не включена в подтверждённые результаты этой фазы и остаётся открытой в фазе 3.

Следующий шаг: пользовательская проверка редактора с реальными карточками Lampa
и продолжение оставшихся работ фаз 3/5 по отдельной команде. Установка или выпуск
нового artifact сейчас не выполнялись; наличие локального candidate не означает релиз.
