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
  Candidate notes есть; backup/restore и межверсионное обновление ещё не приняты.
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
не новая полная приёмка фазы 5. Не выполнены 15 минут двух playback по 50 Mbit/s,
1000 GET/seek/cancel, финальные p95/память/NAS, полная Lampa/Num и hosted release.

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
Нагрузка 15 минут/1000 циклов, backup/restore/межверсионное update, полный аудит
licenses/notices и клиентская приёмка **ещё не завершены**. LICENSE пользователь
создаст позже; он не объявлен новым препятствием локальной работе. Поддержаны PEP 440 теги
vX.Y.Z[aN/bN/rcN] с prerelease flag; они проверены unit-format guards, не remote run.
Коммиты, push, внешние репозитории, теги и публикации не выполнялись.
