# Отчёт: фаза 7 — обновление плагина из Web UI

Статус: **Завершена**, 2 ноября 2026 года.
Основание: [план](../phase7-plugin-updates.md), SPEC §13.2 и запрос пользователя.
Изменение не закрывает оставшуюся приёмку фаз 3/5.

## Реализованное поведение и файлы

- `updates.py`: GitHub latest, ограниченный HTTPS, manifest/digest/runtime/egg
  проверки, подготовка и атомарная установка без перезаписи старой версии.
- `update_service.py`, `core.py`, `webui.py`: один worker job на процесс,
  авторизация администратора, отдельные узлы, защита токенами и connection identity,
  последовательная подготовка двух пакетов, установка, отмена при shutdown.
- `restart.py`: atexit exec штатного Linux entry point после завершения reactor;
  прежний PID/UID/окружение, без повторного daemonization и вызовов service manager.
- `data/stream.js`: проверка/ссылка/подтверждение установки, ошибки и ожидание
  новых версий, переподключение исходного host, sessionStorage через reload.
- `docker/entrypoint.py`: более новая версия в volume сохраняется при recreation,
  одинаковая версия с другим содержимым и чужой Python отклоняются.
- `tests/unit/test_updates.py`, `test_update_container.py`, `tests/runtime/updates.py`:
  детерминированные и настоящие native/Chromium проверки. GitHub источник в native
  тесте подменён только в тестовом egg; продукт не содержит переключателя источника.
- Makefile: `test-updates` входит в `test/check`; checks.yml уже вызывает make check,
  поэтому дублировать команды в YAML не требуется. README, SPEC, delivery, AGENTS
  и индекс фаз обновлены.

## Definition of Done исходной фазы

- [x] Проверка GitHub Release и состояния «нет обновлений»/ошибок работают в UI.
  Chromium с локальными source fixtures; live GitHub отдельно.
- [x] Версия, runtime и SHA-256 проверяются до установки на каждом узле.
  Unit и установленный native validator.
- [x] Операции авторизованы, I/O ограничен и не блокирует reactor.
  Native reader-account отклонён на core и web; unit body limit, cancel,
  deadline, rate limit и worker-выполнение сетевого/файлового I/O.
- [x] Проверенные core/web artifacts устанавливаются без общего диска.
  Раздельные профили, каждый процесс скачивает свой artifact.
- [x] Штатные процессы перезапускаются без зависимости от service manager.
  Native тест подтвердил повторный exec с прежними PID.
- [x] Состояние торрентов/SQLite сохраняется; disable/reset не используется.
  Native temporary/publication/record_id/metadata/config/payload и recovery flag.
- [x] Ошибки подготовки/установки/перезапуска не дают ложного успеха.
  Chromium checksum-отказ до установки обоих пакетов, повтор, unit conflict,
  сохранение старого egg, ограниченное ожидание версии и stale callback guard.
- [x] Native и браузерные проверки, make check и документация завершены.
  Итоговые make check 3.11/ARM64 и 3.14/x86_64 Passed, включая native updater,
  Chromium, smoke, packaging, migration/delivery и candidate bundle.

## Среда, точные команды и результаты

Host macOS ARM64; Docker Linux ARM64, image `deluge-stream-phase5-check:arm64`.
Python 3.11.2, Deluge 2.2.0, libtorrent 2.0.8.0, Twisted 24.11.0.
Только временные профили; пользовательские daemon/web не изменялись.

```sh
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase5-check:arm64 /opt/venv/bin/python -m pytest -q tests/unit/test_updates.py
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase5-check:arm64 make test-updates VENV=/opt/venv
```

- Unit updater: первые 19 passed; расширенный общий unit-набор — 338 passed,
  включая container recreation guards и network limits/ошибки.
- Native + Chromium: Passed. Проверка, отмена подтверждения, установка,
  новые версии двух процессов, прежние PID и сохранённое состояние.
- Первые native-прогоны обнаружили одноразовое неуспешное штатное подключение web
  во время restart core. Добавлен ограниченный browser reconnect исходного host,
  который переживает штатное удаление UI плагина при disconnect. Повтор прошёл.
- Начальный lint без `VENV=/opt/venv` отказал из-за отсутствия `.venv`; это не skip.
  Форматирование и правки длинных строк выполнены; итоговые проверки ещё идут.
- Hosted CI, публикация и установка в пользовательский профиль не выполнялись.
- Live GitHub: latest API вернул `v0.1.7`, release id `429971554`; manifest
  и реальный egg скачаны в TemporaryDirectory и проверены по digest/формату.
  Установка и исполнение скачанного пакета не выполнялись. Использованы
  `UpdateStore.check()` и `prepare(token)` в read-only mounted test container;
  обычный native suite остаётся полностью offline.
- Product image `deluge-stream-phase7:arm64` собран; `make test-docker` Passed:
  non-root core/web в отдельных контейнерах, отдельный payload FS, recreation,
  offline backup/restore и точные download bytes. Само сохранение более новой
  UI-версии при старом image проверено unit-тестом entrypoint, не этим Compose run.
- Первый полный ARM64 check прошёл unit/persistence/native/Chromium/updates,
  затем выявил неполный fake client в старом `web_contract.py`: добавлен
  connection_info и guard поздней ошибки после нового enable.
- На Python 3.14 первый integration обнаружил недоступный прямой import libtorrent
  в изолированном Deluge loader; исправлен на `deluge._libtorrent.lt`.
- Промежуточная сборка ARM64 отклонена candidate-check из-за изменения исходника
  после build; отдельный 3.14 smoke также встретил старый egg с новой сигнатурой
  теста. Итоговые прогоны запускаются на неизменяемом наборе Python/JS исходников.
- Один повтор ARM64 остановился в существующем magnet streaming assertion
  `server.streams.jobs and not magnet_read.called` (streaming.py:556). Условие
  не ослаблялось; следующий полный последовательный прогон Passed.
  Причина единичного отказа отдельно не доказана.

| Итоговый прогон | Результат |
| --- | --- |
| Linux ARM64 / Python 3.11.2, полный `make check` | Passed: 338 unit, 99 persistence, native/HTTP/QoS/FIFO/downloads/ZIP, Chromium 156.0.8078.73, updater, smoke, packaging, delivery и bundle |
| Linux x86_64 / Python 3.14.8, полный `make check` | Passed: 338 unit, 99 persistence и тот же полный набор; Chromium 156.0.8078.79, x86_64 эмулируется |
| Product Compose ARM64 | Passed: non-root, split payload FS, recreation, offline backup/restore и download bytes |
| Live GitHub v0.1.7 | API, manifest, asset SHA-256 и egg validation Passed; пакет не установлен и не исполнен |
| Hosted CI / новый release / пользовательский systemd | Не запускались |

Дополнительные команды:

```sh
docker build --platform linux/arm64 -t deluge-stream-phase7:arm64 .
make test-docker PRODUCT_IMAGE=deluge-stream-phase7:arm64 CI_PLATFORM=linux/arm64
docker run --rm --network none -v "$PWD:/work" deluge-stream-phase5-check:arm64 make check VENV=/opt/venv BUILD_DIR=build/phase7-py311 DIST_DIR=dist/phase7-py311
docker run --rm --platform linux/amd64 --network none -v "$PWD:/work" deluge-stream-py314-check:amd64 make check VENV=/opt/venv BUILD_DIR=build/phase7-py314 DIST_DIR=dist/phase7-py314
```

Второй runtime: x86_64 (эмуляция), Python 3.14.8, Deluge 2.2.0,
libtorrent 2.0.14.0, Twisted 26.10.0, SQLite 3.54.0.

## Сценарии и ограничения

A28: native/UI цикл подтверждён с локальным release fixture. A01/A23/A24 —
относящиеся к обновлению lifecycle/права/раздельные процессы; полная повторная
приёмка этих сценариев этим тестом не объявляется. A14/A27 — сохранение record_id
и metadata при штатном restart. Native тест использует разные профили одного
контейнера; отдельные машины/service managers и реальный GitHub download пока
не проверены как полный update-сценарий. Реальный GitHub manifest/egg проверен
без установки; отдельные core/web контейнеры — обычным Compose lifecycle.
Изменение entrypoint проверяется отдельно.

Нет распределённой транзакции установки двух узлов и автоматического отката
SQLite. Частичный отказ требует повторной установки или ручного восстановления.
Старые egg сохраняются; системные зависимости не обновляются. Только stable
latest release с обязательными digest/manifest; неподдерживаемый launcher
не перезапускается. Интеграционная приёмка каждого systemd/NAS/Docker supervisor
не заявляется на основании обычного process exec.

Следующий шаг: отдельный выпуск версии с этим кодом по команде пользователя.
Чтобы кнопки появились в существующей установке без updater, эту первую версию
нужно поставить штатным способом в оба процесса. Публикации, изменения
пользовательского профиля или перезапуска пользовательских служб не было.
