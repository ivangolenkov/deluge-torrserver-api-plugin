# Локальная поставка и Docker

Фаза 5 в работе. Ни опубликованного релиза, ни успешного hosted run пока нет.
LICENSE владелец создаст позже. Описанные candidate-команды не дают разрешения
на распространение и не заменяют Lampa/Num/NAS-приёмку и аудит лицензий образа.

## Комплект

Единственный источник версии — `deluge_stream/_version.py`. Подтверждённый контракт:
Linux, Python 3.11, Deluge 2.2.0, libtorrent 2.0.8.0, Twisted 24.11.0.
Один pure-Python egg используется для x86_64/ARM64 и для core/web. Проверка
архитектур означает отдельные запуски, а не переименование файла.

В подготовленном Linux-окружении из [матрицы](compatibility.md):

```sh
make check
make candidate-check
```

Дополнительно нужен `git`, доступ к metadata текущего checkout и Python 3.11.
`candidate-check` собирает egg и проверяет установленный artifact в одноразовых
профилях настоящих deluged/deluge-web. Затем создаёт `compatibility.json`,
`SHA256SUMS`, `RELEASE-NOTES.md` в `DIST_DIR` (по умолчанию `dist`). Непустой
корневой LICENSE копируется, если существует; отсутствие записывается явно.
Manifest содержит commit, dirty flag и fingerprint текущих неигнорируемых файлов,
SHA-256 egg и узкий runtime-контракт, **не сертификат приёмки**.
Изменили исходники после сборки — пересоздайте комплект.

`make test-packaging` сравнивает две сборки в чистых временных каталогах из
текущего source snapshot. Проверяет равенство содержимого ZIP entries, отдельно
выводит SHA-256 и побайтовое равенство. Это не чистый Git checkout, если текущая
работа не закоммичена. ZIP timestamps не нормализуются; побайтовая
воспроизводимость не гарантирована. APT пока не snapshot, Python dependencies
закреплены версиями без полного hash-lock.

`make release-check RELEASE_TAG=v0.0.1` дополнительно требует LICENSE, чистое
дерево и совпадение тега с версией. До создания LICENSE ожидается отказ.
`make release-verify` проверяет **существующий** комплект и smoke без пересборки.
Эти цели не публикуют и не закрывают ручные release gates.

## Docker Compose

Требуются Docker Engine с Compose v2, доступ к registry/APT/PyPI для сборки,
свободные порты и место для payload. Образ собирается локально: готовый образ
из registry не объявляется выпущенным.

Создайте secret локально (не добавляйте его в Git):

```sh
mkdir -p secrets
python3 -c "import secrets; from pathlib import Path; Path('secrets/rpc_password.txt').write_text(secrets.token_hex(32) + '\n')"
make docker-build
make docker-up
```

Secret должен быть читаем выбранным UID внутри контейнеров: Compose file secrets
монтируются как файлы, не гарантируют преобразования host ownership/mode. Для
локального bind-secret подходит mode 0644 при закрытом доступе к host-каталогу;
для реального сервера настройте доступ/ACL согласно вашей модели безопасности.
Пароль не менее 24 символов, без пробелов и `:`. Он хранится также в persistent
core auth и web hostlist с mode, ограниченным umask 0077. Ротация — явное изменение
обоих профилей и secret при остановленных процессах, не простая замена файла.

Откройте `http://127.0.0.1:8112`, войдите со штатным начальным паролем `deluge`
и **сразу смените его** в Preferences → Interface. В Connection Manager выберите
`core:58846` (учётная запись `stream` уже записана в hostlist).
В Preferences → Stream настройте:

- `torrent_owner`: `stream`;
- `download_root`: `/downloads`;
- `public_base_url`: HTTP URL **core**, доступный плееру и браузеру, например
  `http://192.168.1.20:8090`, не Docker DNS `core` и не URL Web UI.

Настройки плагина находятся в SQLite core-профиля. Несуществующий `stream.conf`
не используется. При повторном старте Compose не затирает сохранённые настройки.
Подключение Lampa/Num и правила публикации описаны в основном README;
совместимость клиентов остаётся незакрытой пользовательской приёмкой.

По умолчанию host-порты слушают **127.0.0.1**. Для доверенной LAN задайте
`STREAM_BIND_IP` равным LAN-адресу сервера (или осознанно `0.0.0.0`). Не открывайте
неавторизованный TorrServer API в Internet. HTTPS proxy/VPN и mixed-content
браузера требуют отдельной настройки. RPC 58846 не публикуется на host.
Порты: `STREAM_WEB_PORT=8112`, `STREAM_HTTP_PORT=8090`, `STREAM_PEER_PORT=6881`
(последний TCP/UDP). `EXPOSE` сам по себе не публикует порт.

`PUID=1000`, `PGID=1000` по умолчанию; поддерживаются положительные numeric IDs,
не root. Entrypoint меняет владельца только корня **пустого** volume, затем
запускает daemon/web через gosu. Уже существующие деревья/payload рекурсивно
не chown-ятся. При смене IDs права существующих файлов подготовьте вручную.
Egg cache Web UI находится в `/config/.python-eggs`.

Именованные volumes разделены:

| Volume | Содержимое |
| --- | --- |
| core-config | native профиль/state/auth/plugins и SQLite плагина |
| web-config | отдельный профиль Web UI/hostlist/cache |
| downloads | payload только у core, в web не монтируется |

```sh
make docker-down
make docker-up
make test-docker PRODUCT_IMAGE=deluge-stream:local CI_PLATFORM=linux/arm64
```

`docker-down` не удаляет volumes. `test-docker` использует случайное имя проекта,
одноразовый secret и отдельные volumes; только свои тестовые volumes удаляет
после проверки. Проверяются смена временности/публикации, настройки, штатная
остановка и пересоздание контейнеров, идентичность экземпляра и точные байты
download после рестарта. Это не backup/restore и не NAS-измерение.

## Обновление и резервная копия

Не отключайте плагин ради обычного рестарта: явный disable сбрасывает временность,
публикацию и metadata/viewed. Остановите процессы штатно (`make docker-down`),
сохраните **вместе** core-config, web-config, downloads и secret. Не восстанавливайте
старую SQLite независимо от native state и payload. Процедура полного restore
пока не подтверждена отдельным фазовым тестом.

Для обновления сохраните копию профилей, удалите только прежний `Stream-*.egg`
из `plugins/` обоих остановленных профилей и поставьте новый проверенный egg/образ.
При обнаружении другой версии entrypoint откажет вместо удаления чужого файла.
Тот же version/name заменяется image egg; не переиспользуйте выпущенную версию
для другого кода. Миграции покрыты persistence-тестами, но это не полная проверка
обновления между двумя опубликованными версиями (таких версий пока нет).

## CI и выпуск

`checks.yml`: PR, push main, ручной запуск и reusable call; native runners
`ubuntu-24.04`/`ubuntu-24.04-arm`. Основная логика — Makefile, отдельные Docker-only
split/Compose проверки снаружи контейнера. Workflow artifacts `stream-linux-*`
содержат candidate и логи; это не GitHub Release.

`release.yml`: push тега `vX.Y.Z` либо ручная проверка текущего ref **без публикации**.
Использует checks того же commit, скачивает проверенный x86_64 комплект и выполняет
`release-verify` без пересборки. SHA pins checkout v7.0.1, upload v7.0.2 и download
v8.0.2 проверены по public GitHub API. Доступность runners для этого репозитория
и сами hosted runs ещё не проверены. Форматы тегов: `vX.Y.Z`, либо PEP 440
`vX.Y.ZaN`, `vX.Y.ZbN`, `vX.Y.ZrcN` (например `v0.0.2rc1`); prerelease помечается
в GitHub, имя egg и версия должны точно соответствовать тегу без `v`.

Публикационный job имеет отдельные contents:write и environment `release`.
Владелец должен настроить required reviewers и включить переменную
`RELEASE_ACCEPTANCE_APPROVED=true` **только после** лицензии/аудита, NAS, клиентов,
нагрузки и hosted dry run. Отсутствие переменной завершает публикационный job
ошибкой, а не успешным пропуском проверки.

Publisher использует `gh` только в этом job: создаёт draft, не перезаписывает assets,
сверяет существующие bytes перед дозагрузкой и скачивает весь комплект для
финальной сверки перед снятием draft. Повтор идентичного полного выпуска допустим;
другие bytes/чужие assets требуют ручного решения. Не используйте `--clobber`.
Сбой оставляет draft, а не успешный неполный выпуск. Unit-проверка плана assets
не заменяет настоящий GitHub draft-сценарий, который пока не запускался.

Создание/отправка реального тега, репозитория и публикация сейчас не разрешены
и агентом не выполнялись. URL релиза появится только после реального выпуска.
