# Fixtures фазы 0

Есть воспроизводимый **локальный BitTorrent fixture**:
`tests/runtime/probe.py` генерирует v1 torrent без trackers, 4 MiB детерминированных
ненулевых данных, 64 pieces по 64 KiB. Payload/torrent находятся во временном
каталоге; публичный swarm не используется.

`tests/runtime/research.py` дополнительно генерирует v1/v2/hybrid metadata:
`pack/a.bin` (65537 байт a), `pack/empty` и `pack/b.bin` (100003 байта b),
pieces по 16 KiB. Реальные pad offsets, aliases и Deluge ids выводятся в JSON
`metadata_matrix`; каждый mapping сверяется с libtorrent `map_block`.
Это fixture BitTorrent metadata, **не** запись запросов Lampa/Num/TorrServer.

Для SIGKILL/restart используется только временный профиль и исследовательская
SQLite WAL. Его marker ограничен заранее известным экземпляром, без продуктовой
identity/reconciliation. Каждый crash ожидает exit -SIGKILL, recovery — exit 0;
любое другое завершение проваливает обязательную проверку.

`tests/runtime/guards.py` отдельно использует record_id и hardlink/fingerprint
служебного `.torrent` в **одноразовом профиле**. Это не payload hardlink и не
продуктовая БД. Проверяются обычный crash/restart, offline remove/re-add, отказ
metadata unlink, re-add и policy change на барьере перед worker callback,
очередь/пауза/skip и искусственный replay старого removal event. Штатный
EventManager синхронен; replay — явно обозначенная fault injection.

Исходный guards-кандидат не закрывает гонку удаления marker во время commit.
`tests/runtime/cleanup.py` проверяет другой протокол: acknowledgement сохраняет
ownership, а startup audit читает и pending=0. Политика меняется на барьере внутри
настоящей worker-транзакции SQLite; SIGKILL вызывается до commit либо после commit
до возврата результата reactor. Дополнительно проверяются rollback, live retry,
новый admission epoch и remove/re-add. Это реальные процессы/БД, не mock success.

Переносимость identity на NAS/backup, GC и весь live streaming controller пока
не подтверждены. Эти ограничения нельзя считать skip или пройденной продуктовой
приёмкой. Старые hash-only/quiet эксперименты не заменены успешными заглушками:
новые проверки добавлены к обязательному integration-набору.

В [torrserver-reference](torrserver-reference/README.md) сохранены реальные
HTTP-записи TorrServer MatriX.147.1 и программно вызванного модуля Lampa 3.4.4
в Chromium. Это частичная проверка: payload не является видео, полноценные
UI/playback/закрытие страницы и Num пока не проверены. Нельзя считать HTTP probe
плагина TorrServer-совместимым на основании записи от другого сервера.

Отдельно в `torrserver-emulation/` сохранены **source-derived** schemas, manifest
и синтетические examples по HTTP-эмуляции `torrent-stream-hub` на commit
`043e26a2bd2479350dbc1d1486b32e85e7bacb7a`. Это не клиентские captures:
`runtime_verified=false`, `client_captured=false`. Контрольные суммы, маршруты
и типы можно сверить необязательным read-only инструментом; обычные тесты
не требуют соседнего checkout. [Описание и отличия от ТЗ](../../docs/torrserver-contract.md).

По указанию пользователя от 29 октября 2026 года полученных записей Lampa и
эталонного TorrServer достаточно для исследовательского DoD фазы 0. Num и
полноценные UI/playback-сценарии не блокируют дальнейшую разработку; при
необходимости пользователь проверит их позднее. Для последующей полной
клиентской приёмки, не объявленной выполненной, необходимо:

1. Зафиксировать версию/commit каждого клиента и эталонного TorrServer, платформу
   Num и способ запуска Lampa.
2. На локальном контролируемом torrent записать add, file list, preload/stat,
   play/Range/seek, cache, settings, viewed и закрытие страницы.
3. Сохранить HTTP methods, routes, status, значимые headers и JSON types;
   удалить credentials, trackers/passkeys, личные названия и адреса.
4. Описать, что запись получена **от настоящего клиента/эталона**, отделить её от
   сгенерированных edge-case тестов. Вывести DTO schemas и viewed set/list/rem
   контракт из записей, а не наоборот.
5. Повторить те же сценарии против будущего API плагина; один HTTP 200 не является
   доказательством совместимости.
