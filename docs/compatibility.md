# Проверяемое окружение и совместимость

Это узкий проверенный runtime-контракт плагина; полная клиентская/NAS-приёмка
и выпуск ещё не завершены. Поставка описана в [инструкции](delivery.md).

| Компонент | Зафиксированная версия |
| --- | --- |
| ОС | Debian bookworm-slim, image index SHA256 99c0b25c46d0214d5a2091092e7908907dcaecd6a962cde0ac700acee222b6f3 |
| Python | 3.11.2; Debian package 3.11.2-6+deb12u9 |
| Deluge | 2.2.0 из PyPI |
| libtorrent | 2.0.8.0 binding; Debian python3-libtorrent 2.0.8-1+b1 |
| Twisted | 24.11.0 |
| SQLite | 3.40.1; версия встроенного sqlite3 выводится в runtime preflight |
| setuptools / wheel | 75.8.0 / 0.45.1; bdist_egg |
| ruff / pytest / markdown-it-py | 0.9.10 / 8.3.5 / 3.0.0 |
| jsonschema (только dev, проверка контрактных artifacts) | 4.23.0 |

Основные зависимости закреплены в requirements-файлах, транзитивные — в
`constraints.txt`. Libtorrent устанавливается системным пакетом и виден venv
через `--system-site-packages`. В глобальный Python pip ничего не устанавливает.
APT-репозитории пока не snapshot: полный lock системных пакетов и hashes Python
дистрибутивов остаются задачей воспроизводимости. Обновлённые security-пакеты
нельзя автоматически считать проверенным runtime без повторного прогона.

Linux ARM64 исполняется нативно в Docker Desktop VM на Apple Silicon.
Linux x86_64 проверяется отдельным образом под эмуляцией; это не проверка на
физическом x86_64/NAS. Точный итог прогонов — в
[отчёте фазы 0](../tasks/implementation/phase0-report.md).

В продолжении фазы 1 на том же runtime проверены и отдельные deluged/deluge-web
2.2.0: один egg установлен в оба изолированных профиля, web script получен через
HTTP, авторизованный handshake и 20 enable/disable прошли на ARM64 и эмулируемом
x86_64. Это начальная web-диагностика, не полный UI или клиентская playback-приёмка.
Локальный `make ci-check` прошёл; hosted GitHub run пока не выполнен. Подробности —
в [отчёте фазы 1](../tasks/implementation/phase1-report.md).

Итог фазы 1: SQLite подключена к Core, 132 unit и 70 persistence прошли на обеих
архитектурах, как и реальные policy RPC, owner/admin/shared, native restart,
SIGKILL и confirmation gate. Явное отключение плагина сбрасывает instance bindings;
штатный restart сохраняет их. Hardlinks для политики не требуются. Runtime gate
не допускает другой Deluge/libtorrent/Twisted, не-Linux и Python вне 3.11;
SQLite требуется >=3.40.1. Матрица фактически проверенного окружения остаётся выше.

## Воспроизведение из чистой копии

Требуется Docker с Linux containers и make на хосте для `make help`.
Команды запускаются из корня репозитория; тестовый контейнер не имеет внешней сети:

```sh
docker build --platform linux/arm64 -f tools/runtime.Dockerfile -t deluge-stream-phase0:arm64 .
docker run --rm --platform linux/arm64 --network none -v "$PWD:/work" deluge-stream-phase0:arm64 make check VENV=/opt/venv
```

Для x86_64, с отдельными каталогами сборки:

```sh
docker build --platform linux/amd64 -f tools/runtime.Dockerfile -t deluge-stream-phase0:amd64 .
docker run --rm --platform linux/amd64 --network none -v "$PWD:/work" deluge-stream-phase0:amd64 make check VENV=/opt/venv BUILD_DIR=build/amd64 DIST_DIR=dist/amd64
```

На другом хосте без поддержки эмуляции используйте только его нативную
архитектуру. `make smoke` копирует **собранный egg** в новый временный
`profile/plugins`, запускает настоящий deluged subprocess и обращается к нему
через авторизованный RPC. Проверяет 20 enable/disable, удаление RPC и закрытие
listener; затем завершает daemon и удаляет собственный временный профиль.
`make test-integration` отдельно запускает настоящие компоненты Deluge в
standalone Daemon, локальный seed, HTTP read/cancel, pause/recheck/move/re-add,
metadata matrix v1/v2/hybrid и отдельные процессы crash/recovery с SQLite WAL.
`guards.py` добавляет identity witness, remove/re-add при выключенном плагине,
fault injection metadata unlink, барьеры worker-result, очередь и session pause.
`cleanup.py` проверяет SQLite completion commit с одновременной сменой policy,
SIGKILL до/после commit, rollback, stale epoch и re-add. `make test-persistence`
проверяет retained-ownership ledger на настоящей временной SQLite; он включён
в `test-integration` и `check`, а не является необязательным skip.
Загрузка кода из checkout исключена `python -I` и проверкой `__file__` в egg.

Для локальной разработки на Debian 12 с подходящими системными версиями:

```sh
sudo apt-get update
sudo apt-get install python3 python3-venv python3-libtorrent make
make bootstrap PYTHON=python3
.venv/bin/python -m pip install -r requirements-runtime.txt
make check
```

Этот путь подготовки эквивалентен слоям Dockerfile, но вне Docker отдельно не
воспроизводился. На macOS поддержка runtime не заявлена. Отсутствующие обязательные
зависимости приводят к ошибке, не к успешному skip.

## Клиенты и приёмка

TorrServer **MatriX.147.1** и модуль Lampa **3.4.4** (commit
`86f3afa5997793242cc2a081da96efb0934ae71f`) запущены в изолированном стенде.
Сохранены 35 реальных HTTP-обменов: scripted reference probe, запросы модуля
Lampa в Chromium и отдельно помеченные scripted browser fetch.
[Происхождение, команды, ограничения](../tests/fixtures/torrserver-reference/README.md).
Это не playback/UI end-to-end. Num не запускался, Android-устройства недоступны;
совместимость самого плагина с клиентами пока не заявляется.
По уточнению пользователя от 29 октября 2026 года имеющихся module-level записей
Lampa/TorrServer достаточно для фазы 0; Num и полноценные UI/playback проверки
отложены до последующей приёмки, при необходимости пользователем.
Сохранён [source-derived контракт эмуляции](torrserver-contract.md) из
`torrent-stream-hub`, отдельно от окончательного DTO плагина и captures.
TorrServer/Lampa — только внешние исследовательские инструменты, не runtime
зависимости плагина и не содержимое egg. Лицензия upstream TorrServer — GPL-3.0;
его binary и код Lampa в репозиторий не включаются и не распространяются.
Процедура будущего получения записана в [fixtures](../tests/fixtures/README.md).
Metadata mapping v1/v2/hybrid и отдельные lifecycle/crash/first-last сценарии
подтверждены исследовательскими тестами, не полной продуктовой матрицей.
Очередь, post-worker policy/re-add и сохранение recovery coverage при completion
commit проверяются контролируемыми сценариями. NAS/backup filesystem и GC
retired-ownership ещё не подтверждены. Witness на
hardlink — только исследовательский кандидат, не новая обязательная зависимость
продуктовой установки. v2-only magnet штатный Deluge 2.2.0
отклоняет; v2 `.torrent` загружается. Подробности — в [архитектуре](architecture.md).

## Лицензии зависимостей

Ниже сведения установленного runtime/metadata, не юридическое заключение.
Код библиотек в egg не включается. Лицензия самого проекта ещё не выбрана
владельцем; публикация/релиз в этой работе не выполняются.

| Зависимости | Лицензия |
| --- | --- |
| Deluge | GPLv3+, OpenSSL linking exception в исходниках Deluge |
| libtorrent / Boost.Python | BSD / Boost Software License |
| Python | PSF |
| rencode | GPL-3.0-only |
| Twisted, setuptools, wheel, pip, ruff, pytest, markdown-it-py, mdurl | MIT |
| attrs, Automat, constantly, hyperlink, Incremental, iniconfig, Mako, pluggy, service-identity | MIT |
| jsonschema, jsonschema-specifications, referencing, rpds-py (dev) | MIT по установленным metadata/classifiers |
| cffi | MIT-0 |
| cryptography | Apache-2.0 OR BSD-3-Clause |
| pyOpenSSL | Apache-2.0 |
| pyasn1 / pyasn1_modules | BSD-2-Clause / BSD (metadata) |
| MarkupSafe, pycparser, idna | BSD-3-Clause |
| packaging | Apache-2.0 OR BSD-2-Clause |
| typing_extensions | PSF-2.0 |
| zope.interface | ZPL-2.1 |
| pyxdg | Metadata сообщает UNKNOWN; необходимо проверить upstream license до поставки |

Системные TLS/SQLite/libc и полный набор notices образа требуют отдельного
release-аудита. `tools/runtime.Dockerfile` — тестовый образ; корневой `Dockerfile`
и `compose.yml` — локальный product candidate, не опубликованный релиз.
