# Сводка приёмки A01–A27

Это указатель на доказательства, **не сертификат готовности продукта**. «Авто»
означает проверенную часть сценария в изолированных Linux-профилях с установленным
egg; не означает, что ручные границы в соседнем столбце закрыты. Runtime: Python
3.11.2, Deluge 2.2.0, libtorrent 2.0.8.0, Twisted 24.11.0, SQLite 3.40.1.
Host — macOS ARM64/Docker Linux VM; x86_64 локально эмулируется.

Доказательства: [фаза 1](../tasks/implementation/phase1-report.md),
[фаза 2](../tasks/implementation/phase2-report.md),
[фаза 3](../tasks/implementation/phase3-report.md),
[фаза 4](../tasks/implementation/phase4-report.md),
[фаза 5](../tasks/implementation/phase5-report.md),
[фаза 6](../tasks/implementation/phase6-report.md).

| ID | Проверенная часть и основной источник | Открытая граница |
| --- | --- | --- |
| A01 | Авто: installed core/web, 20 enable/disable, listeners/RPC/threads/tasks; фазы 1/4/5 | Пользовательская установка на ПК/HDD, hosted run |
| A02 | Частично: HTTP/schema add/list/preload/play, captured reference fixtures; фаза 3 | Полное воспроизведение реальными Lampa/Num отложено пользователем |
| A03 | Авто: Range parser, closed/open/suffix, 416, HEAD/stat без QoS, точные bytes; фазы 3/4 | Полная комбинационная HTTP-матрица/все клиентские варианты |
| A04 | Авто: один GET двигает окно incomplete binary fixture; фаза 3 | Реальное видео/decoder, крупный media fixture |
| A05 | Авто: HTTP offsets, moving deadline union/reset; фаза 3 | Seek реального плеера далеко вперёд/назад |
| A06 | Авто: два incomplete reader, независимый release/shared read; фаза 3 | Два плеера и итоговая нагрузочная приёмка |
| A07 | Авто: v1 shared boundary, v2/hybrid pads, точные bytes; фазы 0/3/4 | Расширенная одновременная seek/close гонка на границе |
| A08 | Авто: file priority/skip/first-last и сохранение native решений; фаза 3 | Широкая concurrent user-action матрица |
| A09 | Авто: count limit/FIFO/native deletion, permanent исключён; фаза 2 | Границы штатных Deluge гарантий, не filesystem isolation |
| A10 | Авто: oldest reservation/playback/preload/file/ZIP/debounce, retry и реальный HTTP; фазы 2/4 | Не гарантия заморозки внешних rename/move/re-add |
| A11 | Авто: repeat-add/permanence/FIFO; фазы 1/2/3; новый backup сохраняет экземпляр | v2-only magnet по-прежнему отклоняется Deluge 2.2.0 |
| A12 | Авто: обе смены retention, pin-before-cleanup; фазы 1/2/4/5 | Проверенные локальные профили, не текущий пользовательский storage |
| A13 | Авто: coordinator barriers с реальной SQLite и native HTTP protections; фазы 2/4 | Fake scheduling не выдаётся за filesystem barrier |
| A14 | Авто: same-hash re-add, UUID/stale release/alerts/tickets; фазы 2/3/4 | Только matching-instance/native contract |
| A15 | Авто: SQLite reopen, native partial delete, SIGKILL cuts и read-only recovery; фаза 2 | Неоднозначный orphan остаётся ручным, своего unlink нет |
| A16 | Авто: live GET + fsynced resume + restart/SIGKILL, viewed/options; фаза 3 | Полная crash-cut матрица reader; residual native piece priorities согласованы |
| A17 | Авто: готовый файл рядом с missing/skipped, ZIP отклонён; фаза 4 | Binary fixtures, не пользовательское видео |
| A18 | Авто: 100% wanted + неготовый sparse skipped не разрешает ZIP/догрузку; фаза 4 | Нет вывода о готовности из размера файла |
| A19 | Авто: native v1/v2/hybrid ZIP/CRC/Unicode/empty; virtual writer >4 GiB/65537; фаза 4 | Виртуальный sink не подменяет большой native HTTP transfer |
| A20 | Авто: slow sockets/disconnect, quotas/reservations/FIFO release; фаза 4 | Длительная нагрузка/общая память отдельно от payload budget |
| A21 | Авто: ready pause/skip, быстрый recheck/move/remove; disk fault injection; фаза 4 | Настоящие ENOSPC/hash fault и расширенная user-action матрица |
| A22 | Авто: traversal/collisions/symlink/shared ownership и foreign sentinels; фазы 2/4 | Не filesystem isolation от внешнего привилегированного mutator |
| A23 | Авто: invalid/expired/stale ticket, чужой user, shared/account revoke; фаза 4 | Ticket — bearer capability, не HTTP password-auth |
| A24 | Авто: Chromium, отдельные core/web profiles/контейнеры, без общего payload FS; фазы 4/5 | Реальный HDD-сервер, HTTPS proxy и другие браузеры |
| A25 | Авто: ARM64 и эмулируемый x86_64 installed/native/check/Compose; фазы 4/5 | Native hosted runners и целевой ПК/HDD не проверены |
| A26 | Частично: отсутствие free-space cleanup, SQLite/read disk-full injection; фазы 2/4 | Реально заполненный volume и все streaming/source-add ENOSPC paths |
| A27 | SQLite CAS/ABA/rollback/миграция, HTTP get/list/set, настоящий Chromium text/JSON/XSS/конфликт/отмена и split core/web; фаза 6 | Реальный интерфейс и кэш Lampa/Num не проверены, browser storage/core errors — fault injection |

## Новые проверки поставки

`test-docker`: offline backup обоих полных профилей и payload, восстановление в
новые одноразовые volumes и проверка FIFO/settings/publication/download bytes.
`test-delivery`: native установленный core, замена egg и schema-5 fixture migration,
сохранение viewed/options и безопасные disable/uninstall. Исторический artifact
из local commit не объявляется опубликованным релизом. Точные запуски — в отчёте 5.

`test-direct` дополняет A03/A07/A08/A19/A20/A21/A25: native-готовые paused/skipped
файлы и многофайловый ZIP читаются без `read_piece`, проверены Range/байты/CRC,
recheck во время worker I/O, удержание quota до завершения и shutdown drain.
Unit-тесты проверяют native fallback для незавершённого файла, inode replacement,
symlinks, timeout и EOF. Это не расширяет гарантии целостности при внешней записи
в тот же payload inode и не заменяет реальный ENOSPC или HDD-приёмку.

Для A20 native streaming suite отдельно проверяет оба reader на non-reading
сокете: фактический `pauseProducing`, до одного chunk/job, reservation не больше
`2 * reader_block_size + HTTP_OVERHEAD` и полный drain после disconnect.
Выбор reader в fixture детерминирован, лимит не зависит от скорости догрузки.

## Производительность и выпуск

Отдельный `test-performance` сохраняет JSON с размером pieces, длительностью,
точной скоростью двух reader, timing percentiles, payload/RSS и raw native disk
counter. Прогон с мелкими pieces, не достигший скорости, не закрывается зелёным
прогоном с крупными pieces. Ограничения и текущие результаты — в отчёте 5 и
[инструкции](delivery.md). Native disk blocks не равны всей памяти libtorrent.

Не закрыты: полный client acceptance, реальный HDD-стенд, полный native memory/
ZIP measurement, полный source/notices аудит зависимостей, hosted checks
и release dry run. GitHub и LICENSE пользователь создаст позже; отсутствие LICENSE
проекта по его решению не блокирует выпуск. Никаких ссылок
на несуществующие releases или успешные hosted runs здесь нет.

По последующему решению владельца все перечисленные ручные/внешние проверки —
не условия публикации, а открытые результаты приёмки и ограничения. Выпуск
сохраняет обязательные технические CI/tests/smoke/version/checksum проверки;
ручное подтверждение приёмки в release workflow не требуется.
