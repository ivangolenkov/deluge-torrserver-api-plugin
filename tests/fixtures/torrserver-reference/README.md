# Реальные записи: TorrServer и модуль Lampa

`matrix147.1-lampa3.4.4.json` — 35 реально записанных обменов:
22 scripted-reference-probe, 10 lampa-module-browser и 3 browser-scripted-fetch.
Последние три **не** выданы за самостоятельно созданные плеером запросы:
Playwright выполняет fetch по URL, построенному настоящим модулем Lampa.
`schema.json` описывает наблюдённые типы для этого v1-fixture, не весь API.

## Происхождение и окружение

- TorrServer: официальный release **MatriX.147.1**, commit
  `d84f41968780744d4b8643f7a73b22a84d2c3779`, Linux ARM64.
  Asset `TorrServer-linux-arm64`, 60948640 bytes, SHA256
  `f73ad19eaf7c78f753fd7a6e7c4da7849e77e79a4c39ea56fe73ce43ee7136c4`.
  SHA256 совпала с `digest` GitHub Releases API.
- Lampa: **3.4.4**, commit `86f3afa5997793242cc2a081da96efb0934ae71f`,
  оригинальный app bundle без модификаций, SHA256
  `b4a1384a5ebd220caa49451bdbcf5f6efc18d4bdc11777d456c0d988a9cbae75`.
  Загружены index, assembly, jquery, notify, scrollbar, navigator, keypad,
  webOSTV и CSS из того же commit. Исходники/бинарники остаются вне репозитория.
- Playwright MCP Chromium, UA: Chrome/156.0.0.0, Mac OS X 10_15_7.
  Дата получения: 29 октября 2026. Backend стенда: проверенный phase-0 контейнер,
  Debian bookworm, Python 3.11.2, libtorrent 2.0.8.0.
- Контейнер работает с `--network none`; внешние запросы браузера заблокированы.
  Browser bridge слушает **только 127.0.0.1** и передаёт HTTP через docker exec.
  Предыдущая успешная исследовательская запись использовала Docker internal
  network без egress; окончательная запись получена именно с network none.
- Единственный payload — `bytes(range(256)) * 16384`, 4 MiB, piece 64 KiB.
  Он называется `fixture.mp4` для file-list сценария, но **не является видео**.
  Локальный libtorrent seed и HTTP tracker находятся в том же контейнере.
  Public swarm, личные torrent-файлы, рабочие профили и credentials не используются.

API-методы `Lampa.Torserver` вызваны программно в настоящем браузере. Проверены
connected, add, files, list, viewed/list/set, cache, построение URL и drop.
Это **не** выбор фильма/seek/закрытие страницы через UI и не декодирование видео.
Drop вызван явно, а не получен событием закрытия страницы. Num не запускался:
`adb devices -l` показал пустой список, Android emulator в PATH отсутствует.
Версия Num не подменяется номером из поисковой выдачи.

По согласованию с пользователем от 29 октября 2026 года полученных module-level
записей Lampa/TorrServer достаточно для фазы 0. Num и полноценные UI/playback
проверки отложены до последующей приёмки, при необходимости пользователем.
Это изменение gate фазы, а не новая успешная runtime-проверка.

## Существенные наблюдения

| Сценарий | Реальный ответ/результат |
| --- | --- |
| echo | `MatriX.147.1`, не `1.2.133` из TSH |
| files/stat | `stat` — integer, наблюдён 3; `stat_string` — строка; File имеет id/path/length без обязательного size |
| preload | Пустой HTTP 200, не JSON TorrentStatus |
| cache | Настоящие PiecesLength/PiecesCount/Pieces; Readers в этом срезе пуст |
| Range | 206; 1 byte, пересечение piece boundary (21 byte), suffix (17 bytes) сверены с seed |
| viewed | JSON body: action=set/list/rem, hash, file_index (1-based), timecode (number) |
| viewed persistence | set → list → restart → list сохранил метку; при TrackTimecode=false 12.5 преобразуется в 0 |
| viewed empty | После удаления последней метки — `null`; до явного set метка уже могла появиться из предыдущих stream-запросов |
| viewed mutations | set/rem возвращают пустой 200; Lampa viewedSet с тестовыми callbacks вызывает fail из-за пустого JSON, но метка сохранена |
| viewed/list null | Тестовый fail callback Lampa; следующий list с существующей меткой проходит |
| missing get | 404 без JSON; unknown torrents action — 400 JSON error |
| drop | Пустой 200; проверен при вызове модуля, не через закрытие UI |
| optional gst | Lampa connected делает дополнительный GET /gst/echo; 404 не помешал основным API-методам |

Callback failures здесь не скрыты за HTTP 200. В исходнике Lampa UI вызывает
viewedSet без callbacks и только при torrserver_tracktimecode; прямой вызов
из стенда не доказывает пользовательский сценарий. Значение TrackTimecode=true,
удаление всех меток file_index=-1, неизвестные viewed actions и обновлённые версии
клиентов остаются вне подтверждённой матрицы.

Для реализации плагина это обосновывает set/list/rem и сохранение меток с
привязкой к torrent-instance/file, а не TSH no-op. Но schema здесь описывает
**наблюдённый эталон**: его null/пустые ответы и числовые константы настроек не
являются обязательным целевым поведением плагина. В частности, список `[]`
предпочтительнее null для Lampa; выбранные ответы мутаций ещё нужно проверить
против будущих handlers. State enum/mapping и условия автоотметок просмотра
нельзя вывести полностью из одного наблюдённого stat=3. viewed не является
источником streaming-state по SPEC.

## Воспроизведение

Опциональный эксперимент требует сети **только для подготовки** источников,
Docker и доступа к Playwright MCP для browser-части. `make check` не скачивает
TorrServer/Lampa и не зависит от них: проверяет сохранённые records и schemas.

Из корня проекта создайте новый каталог `REFERENCE_DIR` вне checkout. Скачайте
официальный binary и перечисленные файлы Lampa по зафиксированным URL:

```sh
export REFERENCE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/torrserver-contract.XXXXXX")"
python3 - <<'PY'
import os
import urllib.request
from pathlib import Path
root = Path(os.environ['REFERENCE_DIR'])
url = 'https://github.com/YouROK/TorrServer/releases/download/MatriX.147.1/TorrServer-linux-arm64'
(root / 'TorrServer-linux-arm64').write_bytes(urllib.request.urlopen(url, timeout=60).read())
(root / 'TorrServer-linux-arm64').chmod(0o755)
base = 'https://raw.githubusercontent.com/yumata/lampa/86f3afa5997793242cc2a081da96efb0934ae71f/'
files = ['index.html', 'app.min.js', 'assembly.json', 'css/app.css',
         'vender/jquery/jquery.js', 'vender/notify/notify.js',
         'vender/scrollbar/jquery.scrollbar.js', 'vender/scrollbar/jquery.scrollbar.css',
         'vender/navigator/navigator.js', 'vender/keypad/keypad.js',
         'vender/keypad/style.css', 'webos/webOSTV.js']
for name in files:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(urllib.request.urlopen(base + name, timeout=60).read())
PY
```

Capture tool проверяет закреплённые SHA256 бинарника и app bundle до запуска.
Для scripted reference-only записи (новое имя output, существующий файл не
перезаписывается):

```sh
docker run --rm --network none -v "$PWD:/work" -v "$REFERENCE_DIR:/reference:ro" deluge-stream-phase0:arm64 python tools/capture_reference.py --binary /reference/TorrServer-linux-arm64 --output /work/reference-new.json
```

Для записи с Lampa запустите в первом терминале:

```sh
docker run --rm --name deluge-stream-reference-capture --network none -v "$PWD:/work" -v "$REFERENCE_DIR:/reference:ro" deluge-stream-phase0:arm64 python tools/capture_reference.py --binary /reference/TorrServer-linux-arm64 --lampa-root /reference --serve --output /work/lampa-reference-new.json
```

После `ready: true` во втором терминале:

```sh
python3 tools/reference_browser_bridge.py --container deluge-stream-reference-capture
```

Затем Playwright MCP `browser_run_code_unsafe` с
`filename: "tools/capture_lampa.js"`. Сценарий проверяет байты seek, закрывает
страницу, вызывает /__finish; bridge и reference завершаются сами. Таймаут
browser-сессии — 600 секунд. При ошибке остановите свой bridge Ctrl-C;
контейнер очищает только собственный TemporaryDirectory. Не публикуйте capture
port на внешнем интерфейсе. Исследовательский bridge не предназначен для работы
с произвольными страницами/торрентами или пользовательскими данными.

В JSON сохранены только разрешённые headers, JSON/text тела и длина/SHA256
бинарных ответов; Date/Server/credentials не записываются. Поля тела не проходят
универсальную анонимизацию: безопасность основана на одноразовом профиле и
контролируемом fixture. DefaultTrackers в settings — публичные defaults эталона,
не личные tracker/passkey; сетевого доступа к ним нет. Временные показатели и
timestamp сохранены фактическими и не обязаны совпадать при новом запуске.
