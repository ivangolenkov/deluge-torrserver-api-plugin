# Архитектура: продуктовый backend и сохранённые исследования

Фазы 0–2 завершены; HTTP/reader/QoS фазы 3 реализованы, но ещё не прошли полную
приёмку Lampa/Num. Контракт — [SPEC.md](../SPEC.md).

## Продуктовая интеграция фазы 3

`core.py` запускает `TorrServer` внутри deluged. `torrserver.py` ограничивает
соединения, входные тела, JSON/multipart и HTTP producer; `torrents.py` проверяет
owner/publication и строит DTO/playlist/viewed. `sources.py` вне reactor разбирает
metadata и готовит UUID-каталоги; HTTP source fetch имеет caps/timeouts/redirect
limit. SQLite хранит source-add intents, без автоматического replay
native add после crash. Внешние file ids — 1-based, без pad-файлов.

`Streams` в `streaming.py` объединяет native reads и минимальные deadlines по
позициям читателей. `stream_adapter.py` изолирует handle/AlertManager/readiness.
Playback/preload проходят через policy lock и leases фазы 2. Verified pieces
читаются при pause/skip; отсутствующие не приводят к автоматическому resume.
Preload disk-backed и не создаёт playback. Свои deadlines снимаются, но native
piece priorities не восстанавливаются: это согласованное упрощение, а не блокер.
File priorities/skip/first-last/pause/queue не изменяются.

Budget reader учитывает две копии piece и HTTP overhead на ожидающего клиента.
После cancel выданный native read сохраняет job slot и reservation до matching
alert; orphan payload не копируется. Backpressure ограничивает выдачу producer.
Close обнуляет runtime jobs; restart/crash не переигрывает QoS. Research probe и
product payload взаимоисключены. Пределы и диагностика описаны в [README](../README.md),
проверки и ограничения — в [отчёте фазы 3](../tasks/implementation/phase3-report.md).

## Редактор metadata (фаза 6)

SQLite schema 7 добавляет `torrent_metadata.version` (не revision политики).
При изменении полей через любой путь записи, включая `commit_source_add` и
TorrServer `set`, генерируется новый UUID-токен; no-op не меняет токен.
Это защищает и от ABA (изменили и вернули прежний текст). Миграция не меняет body.
`edit_metadata` проверяет токен и применяет patch в одной write-транзакции.
Сериализованный body сохраняет прежний лимит 65536 байт UTF-8.

RPC API 1: `get_metadata(api_version, torrent_id, record_id)` и
`set_metadata(api_version, torrent_id, record_id, version, changes)` возвращают
`{record_id, fields, version}`. `fields` содержит реально сохранённые строки,
без DTO fallback-названия. Неизвестные ключи, неверные типы и превышение размера —
`invalid_request`, конфликт версии — `metadata_conflict`. Имена добавлены без
изменения существующих сигнатур API. Owner/admin проверяются и для чтения;
публикация, HTTP и готовность BitTorrent metadata не требуются.

`PolicyService.metadata` использует общий policy lock и ограничение 32 операций,
DB worker, повторную проверку прав/экземпляра/epoch после commit. Отмена RPC
наблюдателя не освобождает lock до окончания worker. Открытая форма не резервирует
торрент и не меняет политику. Все ошибки RPC проходят публичный whitelist.
Web UI проверяет совместимость core/web; callbacks закрытого окна игнорируются.
Отдельный baseline отображаемого текста предотвращает patch нетронутых CRLF,
которые браузер может нормализовать в textarea.

## Исторические исследования фазы 0

Ниже описаны отдельные probes, не текущий продуктовый controller. Их witness,
ledger и recovery-кандидаты сохранены для воспроизводимости, но не подключены
к product QoS и не являются требованиями к нему. ZIP/download UI — будущая фаза 4.

## Проверенный путь чтения

Core entry point `Stream` загружается штатным PluginManager Deluge из `.egg`.
`PieceAdapter` получает `Torrent.handle` из существующего TorrentManager;
своей libtorrent session в плагине нет. Отдельная session есть только у локального
seed в тесте. События удаления получает EventManager, `read_piece_alert` —
AlertManager. Общую очередь alerts плагин не вычитывает и mask не меняет.

Listener находится внутри core-плагина, слушает только `127.0.0.1` на случайном
порту. Административный RPC `stream.probe_info` (auth level 10) возвращает порт и
эфемерный ключ. HTTP требует `Authorization: Bearer …`; access log отключён,
ключ не входит в URL. Это **не** TorrServer listener и **не** download ticket.
Исследовательский endpoint использует внутренний **0-based** индекс файла.
В этом исследовательском endpoint внешнего TorrServer mapping нет.

Перед read_piece проверяется have_piece, а не длина файла на диске. Ожидание —
reactor timer с timeout 15 секунд. Допущен один GET, один ожидаемый piece и один
готовый буфер; piece не больше 4 MiB. Producer останавливает чтение при
backpressure и disconnect. Временные копии при slicing ограничены размером
piece; лимит не означает ограничение RSS Deluge/libtorrent. Нагрузочный бюджет
из ТЗ пока не подтверждён. HEAD не читает payload и не меняет приоритеты.

В callback AlertManager выполняется `bytes(alert.buffer)`; alert не хранится.
Побайтовый результат остаётся неизменным после последующих циклов alerts.
В binding 2.0.8 ошибку надо проверять через `alert.error.value() == 0`:
сам объект error_code truthy даже при успехе. Pad определяется через
`file_flags(index) & flag_pad_file`, метода `pad_file_at` в этой binding нет.

Экземпляр сверяется по объекту Torrent и alert.handle. Runtime-проба проверяет:
ручная пауза не снимается; готовый piece читается на паузе; recheck и move не
ломают байты; удаление отменяет piece wait; после re-add старый объект Torrent
не допускается к чтению нового экземпляра. Это ещё не полная матрица гонок.

Неготовый piece не запрашивается у paused/queued торрента или paused session.
Эти условия перепроверяются во время ожидания: пауза сессии и смена file selection
на «Не скачивать» освобождают wait, не ожидая общего timeout. Тест администратора
ставит active_downloads/active_limit=0; reader не снимает auto-managed pause и
не меняет лимиты. Готовый piece, как и раньше, можно читать на паузе.

EventManager Deluge 2.2.0 вызывает handlers **синхронно**. Дополнительный
fault-injection тест повторно доставляет старый TorrentRemovedEvent уже после
re-add и нового чтения. Hash-only cancellation отменяла новое чтение; теперь
handler сверяет актуальный объект Torrent. Это защита от replay/устаревшего
callback, а не утверждение об отложенной штатной доставке событий Deluge.

## Существенный результат QoS-эксперимента

На отсутствующем piece с исходным приоритетом 4, без concurrent user actions:

| Действие | Live/saved piece priority |
| --- | --- |
| До deadline | 4 |
| set_piece_deadline(piece, 0) | 7 |
| reset_piece_deadline(piece) | 1, а не 4 |

Все значения подтверждаются сериализацией `save_resume_data_alert.params` и
чтением через `read_resume_data`. Повторная установка тех же file priorities
не оказалась достаточным восстановлением. В тесте исходный приоритет
возвращается **явно**, только как cleanup контролируемого эксперимента.
Это не алгоритм восстановления последних пользовательских решений.

**Решение исследовательского прототипа:** HTTP-прототип вообще не меняет deadlines или
piece/file priorities; ожидает обычную загрузку Deluge. Проверка QoS вынесена в
отдельный эксперимент, который явно сообщает `qos_product_ready: false`.
Успешный make check означает воспроизведение этого риска, а не его устранение.

### Кандидат восстановления: write-ahead журнал затронутых pieces

`tests/runtime/research.py` проверяет следующий **исследовательский** протокол:

1. До первого изменения piece сохранить его в SQLite WAL, synchronous=FULL.
   Один worker connection; операции последовательны, reactor SQLite не выполняет.
2. Не сохранять снимок старых пользовательских priorities как источник истины.
   Для затронутого piece вычислять текущую политику из Deluge file priorities,
   metadata offsets и актуального first/last. Общий граничный piece получает
   максимум приоритетов payload-файлов; pad не становится payload.
3. При завершении снять только свой deadline, затем явно установить вычисленный
   приоритет только затронутого piece. При старте новой session deadline уже нет,
   но сохранённый piece override всё ещё необходимо исправить.
4. Дождаться применения и сохранить чистые resume-data. Только после этого
   удалять marker из SQLite. Иначе crash может вернуть override без marker.
5. До допуска нового QoS после рестарта повторить незавершённую операцию.

Реальные дочерние процессы с Deluge и установленным egg убиваются через SIGKILL
в шести случаях: после journal commit; после сохранения изменённого resume;
то же при пропущенном payload-файле; после восстановления только в памяти;
после сохранения чистого resume до очистки marker; после очистки marker.
Седьмой случай — штатное выключение/restart. После каждого запуска проверяются
live priorities, новые resume-data и очистка журнала. В случаях dirty-resume и
memory-only восстановление действительно нужно: исходный live vector после
рестарта **не** совпадает с пользовательской политикой.

**Граница старого hash-only эксперимента:** в нём экземпляр заранее известен и
никогда не заменяется. Таблица исследования хранит только hash/piece, поэтому
**не пригодна для продукта**: перед любым исправлением требуется долговечная
identity экземпляра и reconciliation с delete/re-add. Также ещё не доказаны
гонки recovery с одновременной сменой пользовательской политики, crash при
неподтверждённом сохранении самой пользовательской политики и восстановление
при отключённом плагине. Этот исходный эксперимент сам по себе не закрывает
выбор recovery-алгоритма; выбранная следующая схема описана ниже. HTTP probe
по-прежнему не меняет priorities.

### Свидетель поколения metadata и допуск worker-result

`tests/runtime/guards.py` — отдельный эксперимент поверх установленного egg.
Кандидат `witness.py` создаёт в тестовом профиле hardlink **только на служебный
state/<id>.torrent**, не на payload. Ссылка удерживает inode от повторного
использования после unlink. В SQLite хранятся UUID record_id, hash, fingerprint
(device/inode/size/mtime_ns/ctime_ns) и затронутый piece; ссылки и запись журнала
подтверждаются fsync/SQLite FULL до изменения приоритета.

При обычном рестарте Deluge читает существующую metadata без её пересоздания:
witness остаётся действительным. При remove/re-add с тем же hash и download path
новая metadata не совпадает со старой ссылкой. При инъекции отказа удаления
metadata Deluge перезаписывает файл на месте: fingerprint выявляет изменение
в проверенном контейнерном filesystem. Старый marker сохраняется для диагностики,
а новый record_id восстанавливается независимо. Worker не чистит чужой record.

Перед публикацией настоящего worker-result проверяются три условия:

- текущий Torrent — тот же объект, который захвачен до ожидания;
- witness относится к этой metadata и не изменился;
- file priorities/first-last совпадают с immutable PolicyStamp до ожидания.

Контролируемые барьеры вставляют Core remove/re-add или смену file priorities
после возврата worker, но до применения результата. В первом случае —
instance_changed и **ноль записей в handle**; во втором — policy_changed,
marker сохранён, и только отдельная повторная попытка использует свежую политику.

**Ограничения кандидата:** hardlinks, inode/timestamps и directory fsync должны
поддерживаться filesystem. Symlink/FIFO и EXDEV не имеют небезопасного fallback.
Это не криптографическая/переносимая identity: coarse timestamps, backup/restore,
ручная подмена metadata и lifecycle witnesses ещё требуют решения. Новых
обязательных требований к NAS не вводим: этот код **не подключён к HTTP/QoS**,
его пригодность проверяется только в стенде. Продуктовый GC ссылок не реализован.

Старая quiet-ветка `guards.py` не защищает от смены политики между проверкой
на reactor и commit, удаляющим marker. Она оставлена как исходный эксперимент,
а не выбрана для продуктового cleanup. Следующий кандидат устраняет именно
потерю coverage в этом окне, не полагаясь на ещё одну проверку перед commit.

### Сохранение recovery coverage вместо удаления marker

`recovery_ledger.py` — отдельный, ещё не подключённый к Core SQLite-кандидат:

- `register` связывает UUID экземпляра с witness и metadata piece count.
- До deadline `arm` транзакционно сохраняет ownership затронутых pieces,
  повышает их admission epoch и устанавливает pending=1.
- После восстановления и сохранения чистого resume `acknowledge` меняет только
  pending, причём CAS по record_id/piece/epoch. Старый callback не подтверждает
  новую QoS-потребность или новый экземпляр с тем же hash.
- **Acknowledgement не удаляет ownership.** pending=0 означает завершение
  конкретной попытки, а не доказательство, что будущая пользовательская политика
  или resume-data никогда не потребуют сверки.
- Startup audit читает **все** owned pieces, включая pending=0, через keyset pages
  до 128 записей, проверяет identity и актуальную политику Deluge, восстанавливает
  только принадлежащие плагину pieces. При смене policy после commit в живом
  процессе выполняется отдельная ограниченная повторная попытка.

Это не append-only лог каждого GET: на record/piece хранится одна актуальная
строка. Исследовательские бюджеты — до 128 generations и 1000000 owned pieces,
arm batch до 4096; исчерпание отклоняет новую операцию **до deadlines**. Проверены
WAL/FULL, rollback, bounded scan, старый epoch, независимые поколения одного hash,
неизвестная схема, повреждённая запись и reader во время незавершённого commit.
Все SQLite-вызовы требуют ограниченной worker-очереди; dispatcher в Core пока нет.

`tests/runtime/cleanup.py` на реальных процессах Deluge проверяет шесть случаев:
SIGKILL до commit; SIGKILL после commit до доставки результата reactor; rollback;
policy change в открытой SQLite-транзакции с живым retry; новая QoS-эпоха до
старого acknowledgement; remove/re-add во время commit. В post-commit случае
pending уже 0, но owned piece 0 остаётся со старым priority 7 при новой политике
1. Новый startup audit действительно исправляет его. Фильтр только pending=1
потерял бы это восстановление. Незатронутые pieces не переписываются.

**Граница решения:** закрыто окно потери recovery coverage при completion commit
на проверенном стенде, а не вся задача live QoS. Ownership/witness нельзя удалять
до доказанного retirement экземпляра; GC/backup и переносимость identity ещё
требуют решения. Это состояние не является активной передачей, reservation или
причиной защиты от FIFO. Фиксированные бюджеты относятся к эксперименту, не
вводят новые продуктовые лимиты или удаление payload. HTTP-прототип по-прежнему
не использует этот ledger и не устанавливает deadlines.

### Асинхронное применение пользовательских настроек

Одновременная передача новых file priorities и first/last в одном штатном
`core.set_torrent_options` выявила конфликт: options уже содержат first/last=true,
но libtorrent vector может ещё отражать только file priorities. В контрольной
матрице изменения разделены барьером применения file priorities, затем
first/last. Это не monkey patch и не исправление Deluge. Будущий QoS должен
согласовывать overrides с завершением штатного пересчёта, а не принимать options
за доказательство того, что async piece vector уже применён.

## Mapping v1/v2/hybrid

`snapshot_layout` и `hash_aliases` находятся в адаптере, чистая неизменяемая модель
`layout.py` не импортирует Deluge/libtorrent. Индексы пользователя назначаются
**1-based** только payload-файлам, включая нулевые. Offsets берутся из metadata;
каждое пересечение файла с pieces сверяется с настоящим `map_block`.

На fixture с файлами 65537, 0 и 100003 байта и piece size 16384:

| Metadata | Payload indices | Offsets payload | Общий piece | Deluge id |
| --- | --- | --- | --- | --- |
| v1 | 0, 1, 2 | 0, 65537, 65537 | 4 | Полный v1 hash |
| v2-only | 0, 2, 4 | 0, 81920, 196608 | Нет | Первые 40 hex v2 hash |
| hybrid | 0, 2, 4 | 0, 81920, 196608 | Нет | Первые 40 hex v2 hash, **не v1** |

У v2/hybrid pad indices — 1 и 3; порядок payload — a.bin, b.bin, empty, в отличие
от v1 a.bin, empty, b.bin. Это подтверждает, что суммировать видимые размеры или
переносить display indices между metadata нельзя. На каждом варианте проверены
три изменения file policy/first-last и соответствие чистой модели libtorrent.

Deluge 2.2.0 принимает v2 `.torrent`, но штатная валидация отклоняет magnet только
с `urn:btmh`. Повторный hybrid add через v1 magnet возвращает AddTorrentError
«torrent already exists», не успешный idempotent результат. Дубликат не создан.
Будущий add должен сначала разрешать оба aliases в экземпляр. Поддержка новых
v2-only magnets пока не доказана; требования ТЗ не менялись.

## RPC lifecycle

В Deluge 2.2.0 `RPCServer.deregister_object` удаляет ключи во время обхода живого
словаря. Адаптер снимает только методы конкретного owner, обходя копию entries;
код Deluge и его методы не заменяются. Проверено реальным deluged/RPC: после
каждого disable export отсутствует. Версионно-зависимый доступ изолирован в
`adapter.py`, как и handle/alerts/events.

## ZIP64-эксперимент

Кандидат — стандартный Python `zipfile.ZipFile`, non-seekable sink,
ZIP_STORED, `open(..., force_zip64=True)`. Эксперимент пишет 4 GiB + 123 байта
нулевого payload и 65536 дополнительных пустых файлов; размер, состав и CRC
проверяет стандартный reader. Sink сохраняет только metadata и длины нулевых
участков: полного архива нет ни в RAM, ни на диске. Блок payload — 1 MiB,
сохранённые заголовки — 8759890 байт. BrokenPipe останавливает источник после
двух чтений. Это проверка формата/writer, **не** HTTP ZIP с worker-очередью,
backpressure, правами и защитой от FIFO. До продуктовой реализации остаются
проверки независимым архиватором, неравномерных данных и реального HTTP cancel.

## Фаза 1: storage и lifecycle Core

`repository.py` реализует продуктовые таблицы policy/aliases/metadata/settings/
journal/viewed, атомарные versioned миграции и переходы политики с revision-CAS.
`db_worker.py` выполняет эти синхронные операции через один DB-поток с
ограниченным допуском, отменой ожиданий и неблокирующим drain/join. Это отдельный
слой, **не** подключение исследовательского RecoveryLedger к продукту.

Storage вызывается Core через PolicyService и PolicyAdapter: native catalogue/events,
versioned RPC, owner/admin, instance keys и admission по ограниченному плану путей.
Обычный native restore сохраняет record_id/FIFO. Явный disable атомарно сбрасывает
bindings policy/aliases/metadata/viewed, но не настройки и FIFO-counter.

SQLite-транзакция policy+journal не является общей транзакцией с Deluge. После
crash dirty marker сохраняет требование подтверждения администратором: policy/FIFO
сохранены, effective publication и будущая автоочистка закрыты до confirm_recovery
с актуальным token/catalogue. Hardlinks для политики не требуются. Подтверждение
политики не означает автоматическое разрешение на replay старого QoS-журнала.
[Итоговый отчёт](../tasks/implementation/phase1-report.md).

## Фаза 2: FIFO и единый допуск

`TransferRegistry` управляет leases/refcount, tickets, idle expiry и playback
debounce по record UUID. `PolicyService.lock` сериализует допуск, mutation и
начало cleanup. `Retention` выбирает только старейшего при превышении количества,
фиксирует intent в journal schema 5, вызывает native Deluge remove_data=True и
ждёт matching-handle deletion alert вне policy lock. Ошибка/timeout оставляет
manual operation и останавливает FIFO. Startup только проверяет отсутствие,
не удаляя orphan paths. Согласованы штатные гарантии Deluge, без stronger barrier
от queued native rename/move/re-add. [Отчёт фазы 2](../tasks/implementation/phase2-report.md).

## Пока не реализовано

Есть начальный web entry point с versioned core handshake и диагностической
страницей. JSON/daemon RPC авторизованы штатным Deluge, бинарные данные через
них не передаются. Это не управление торрентами или настройки streaming.

Web UI и HTTP downloads/tickets/ZIP реализованы в фазе 4. TorrServer API и
streaming QoS реализованы, но настоящие Lampa/Num ещё не проверены. Передача
v1/v2/hybrid payload подтверждена binary fixtures, не decoder/playback видео.
Собственный QoS recovery journal не планируется по согласованному упрощению.
Исследовательский журнал не является реализацией persistence фазы 1.

## Фаза 4: direct downloads и Web UI

`downloads.py` выдаёт bounded ephemeral capabilities из авторизованного core RPC.
Reservation создаётся до preflight; после выдачи ticket его TTL и lease TTL
стартуют вместе. Активация выполняется под policy lock; HEAD не активирует/не
продлевает ticket, активный GET переживает start TTL. Повторный доступ проверяет
auth account, native instance, доступ и готовность. Метаданные tickets/ZIP
учитываются отдельно от общего payload budget.

`download_http.py` использует shared reader без deadlines. Состав ZIP определяется
metadata без pad-файлов; readiness учитывает skipped. Native mapping и have_piece
проверяются bounded reactor batches; lstat/fingerprints выполняет DB worker вне
reactor. `archive.py` выдаёт STORE/ZIP64 headers/descriptors/central directory
инкрементально; CRC считается по передаваемым chunks, весь архив не сохраняется.
Native errors до тела становятся JSON, после начала ZIP соединение обрывается.

Последующее уточнение пользователя снимает требование обнаруживать внешнюю запись
в содержимое payload во время передачи (см. §6.2 SPEC). Fingerprints проверяют
device/inode, но не mtime или неизменность содержимого. `direct_reader.py` читает
полностью native-verified файлы через `pread`: два worker-потока, 16 заданий,
блоки до 256 KiB, общий payload budget. Каждый блок открывается через dirfd с
`O_NOFOLLOW`; дескриптор закрывается в worker. Незавершённые файлы сохраняют
native piece-reader, даже если запрошенный Range уже готов. Проверка whole-file
выполняется reactor batches по 256 pieces; блок проверяется native API до и после
I/O. Отменённое задание удерживает capacity/budget до фактического завершения;
shutdown ждёт drain. Safe path/open, access/instance checks, native readiness, FIFO,
lifecycle cancellation и обработка I/O errors остаются обязательными. Witnesses
metadata/identity торрента этим уточнением не отменяются.

`data/stream.js` расширяет Preferences, Details и контекстные меню штатного Deluge,
не заменяя его state или native priorities. Core RPC выполняет policy mutations
и ticket admission. Браузер следует обычной ссылке core listener; deluge-web не
читает payload. `test-web` проверяет настоящий Chromium с установленным egg;
`test-web-split` — два контейнера с internal network без общего payload FS.
Статус и точные ограничения — [отчёт фазы 4](../tasks/implementation/phase4-report.md).
