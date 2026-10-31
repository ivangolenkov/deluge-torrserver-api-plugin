/* Deluge 2.2 Web UI. Binary transfers go directly to core, never through RPC. */
Ext.ns('Deluge.stream', 'Deluge.ux.preferences');
Deluge.stream.messages = {
    core_unavailable: 'Core Stream недоступен.',
    incompatible_core: 'Установите одинаковые версии core и web Stream.',
    web_disabled: 'Web Stream отключён.',
    file_not_ready: 'Файл не загружен полностью. Догрузка не запускается.',
    torrent_not_ready: 'ZIP недоступен: не готовы все файлы, включая пропущенные.',
    forbidden: 'Недостаточно прав.', torrent_not_found: 'Торрент исчез или нет доступа.',
    operation_conflict: 'Состояние изменилось. Обновите данные и повторите.',
    metadata_conflict: 'Метаданные изменены другим клиентом. Черновик сохранён в форме; перечитайте данные для повторного редактирования.',
    invalid_request: 'Некорректные данные или превышен лимит metadata (64 КиБ JSON в UTF-8).',
    public_url_required: 'Настройте доступный браузеру public_base_url и HTTP listener.',
    transfer_limit: 'Достигнут предел передач или tickets.',
    unsafe_paths: 'Небезопасные или неоднозначные пути.',
    metadata_pending: 'Ожидание metadata.', storage_error: 'Ошибка хранения.',
    invalid_config: 'Некорректные настройки.', invalid_owner: 'Владелец отсутствует в Deluge.'
};
Deluge.stream.error = function(error) {
    var detail = error && (error.error || error);
    var text = typeof detail === 'string' ? detail : detail && (detail.message || detail.exception_msg || detail.reason || detail.msg);
    if (typeof text === 'string') {
        text = text.slice(0, 8192);
        for (var code in Deluge.stream.messages) {
            if (new RegExp('(^|[\\s:])' + code + '($|[\\s:])').test(text)) return Deluge.stream.messages[code];
        }
    }
    // Never render a native traceback or untrusted RPC message as HTML.
    return 'Операция Stream не выполнена.';
};
Deluge.stream.notice = function(text) {
    Ext.Msg.alert('Stream', Ext.util.Format.htmlEncode(text));
};
Deluge.ux.preferences.StreamPage = Ext.extend(Ext.form.FormPanel, {
    title: 'Stream', autoScroll: true, labelWidth: 170,
    initComponent: function() {
        this.fields = {};
        this.info = new Ext.form.DisplayField({value: 'Проверка core…'});
        this.items = [this.info];
        Deluge.ux.preferences.StreamPage.superclass.initComponent.call(this);
        this.on('show', this.refresh, this);
    },
    refresh: function() {
        deluge.client.stream.get_web_status({scope: this, success: function(status) {
            if (this.isDestroyed) return;
            this.info.setValue(status.available
                ? (status.http_error === 'bind_failed' ? 'HTTP listener не запущен: адрес или порт занят. Настройки сохранены.'
                    : status.recovery_required ? 'После crash требуется подтверждение администратора через RPC.' : 'Core доступен. HTTP только для доверенной LAN.')
                : Deluge.stream.error(status));
            if (!status.available) return;
            deluge.client.stream.get_config(1, {scope: this, success: this.loaded,
                failure: function(error) { this.info.setValue(Deluge.stream.error(error)); }});
        }, failure: function() { if (!this.isDestroyed) this.info.setValue('Core недоступен.'); }});
    },
    loaded: function(config) {
        if (this.isDestroyed) return;
        this.original = config;
        for (var key in config) {
            if (!config.hasOwnProperty(key)) continue;
            if (!this.fields[key]) {
                var value = config[key], options = {fieldLabel: key, name: key, width: 180};
                this.fields[key] = typeof value === 'boolean' ? new Ext.form.Checkbox(options)
                    : typeof value === 'number' ? new Ext.form.NumberField(Ext.apply(options, {allowDecimals: false, minValue: 0}))
                    : new Ext.form.TextField(options);
                this.add(this.fields[key]);
            }
            this.fields[key].setValue(config[key]);
        }
        this.doLayout();
    },
    onApply: function() {
        if (!this.original || !this.getForm().isValid()) return;
        var changes = {};
        for (var key in this.fields) {
            var value = this.fields[key].getValue();
            if (value !== this.original[key]) changes[key] = value;
        }
        deluge.client.stream.set_config(1, changes, {scope: this, success: function(config) {
            if (this.isDestroyed) return;
            this.loaded(config); this.info.setValue('Настройки сохранены. Проверьте доступность core HTTP с устройства пользователя.');
            deluge.client.stream.get_web_status({scope: this, success: function(status) {
                if (!this.isDestroyed && status.http_error === 'bind_failed') this.info.setValue('HTTP listener не запущен: адрес или порт занят. Настройки сохранены.');
            }});
        }, failure: function(error) { if (!this.isDestroyed) this.info.setValue(Deluge.stream.error(error)); }});
    }
});

Deluge.stream.MetadataWindow = Ext.extend(Ext.Window, {
    title: 'Метаданные TorrServer / Lampa', width: 660, height: 530,
    layout: 'fit', modal: true, closeAction: 'close',
    initComponent: function() {
        this.fields = {};
        this.info = new Ext.form.DisplayField({value: 'Загрузка…'});
        var items = [this.info], labels = {title: 'Название', poster: 'Постер (URL)', category: 'Категория', data: 'data (текст / JSON)'};
        for (var key in labels) {
            this.fields[key] = new (key === 'data' ? Ext.form.TextArea : Ext.form.TextField)({
                fieldLabel: labels[key], anchor: '98%', height: key === 'data' ? 210 : undefined
            });
            items.push(this.fields[key]);
        }
        items.push(new Ext.form.DisplayField({value: 'Пустое сохранённое название не равно имени торрента. data зависит от клиента Lampa; изменяйте осторожно. Постер автоматически не загружается.'}));
        this.form = new Ext.form.FormPanel({padding: 10, labelWidth: 130, autoScroll: true, items: items});
        this.saveButton = new Ext.Button({text: 'Сохранить', disabled: true, handler: this.save, scope: this});
        this.reloadButton = new Ext.Button({text: 'Перечитать', handler: this.reload, scope: this});
        this.buttons = [{text: 'Проверить JSON', handler: function() { this.json(false); }, scope: this},
            {text: 'Форматировать JSON', handler: function() { this.json(true); }, scope: this},
            this.reloadButton, this.saveButton, {text: 'Отмена', handler: function() { this.close(); }, scope: this}];
        this.items = [this.form];
        Deluge.stream.MetadataWindow.superclass.initComponent.call(this);
        this.on('beforeclose', this.beforeClose, this);
        this.on('show', this.load, this);
    },
    dirty: function() {
        if (!this.snapshot) return false;
        for (var key in this.fields) if (this.fields[key].getValue() !== this.baseline[key]) return true;
        return false;
    },
    beforeClose: function() {
        if (this.discard || !this.dirty()) return true;
        Ext.Msg.confirm('Метаданные', 'Закрыть форму и потерять несохранённые правки? Уже отправленное сохранение отменить нельзя.', function(answer) {
            if (answer === 'yes' && !this.isDestroyed) { this.discard = true; this.close(); }
        }, this);
        return false;
    },
    setBusy: function(value) {
        this.busy = value;
        this.saveButton.setDisabled(value || !this.snapshot);
        this.reloadButton.setDisabled(value);
        for (var key in this.fields) this.fields[key].setDisabled(value || !this.snapshot);
    },
    invoke: function(method, args, callbacks) {
        deluge.client.stream.get_web_status({scope: this, success: function(status) {
            if (this.isDestroyed) return;
            if (!status.available) return callbacks.failure.call(this, status);
            deluge.client.stream[method].apply(deluge.client.stream, args.concat([callbacks]));
        }, failure: function(error) {
            if (!this.isDestroyed) callbacks.failure.call(this, error);
        }});
    },
    load: function() {
        if (this.busy || this.isDestroyed) return;
        this.setBusy(true);
        this.info.setValue('Загрузка…');
        this.invoke('get_metadata', [1, this.torrentId, this.recordId], {scope: this,
            success: function(snapshot) {
                if (this.isDestroyed) return;
                this.snapshot = snapshot;
                this.baseline = {};
                for (var key in this.fields) {
                    this.fields[key].setValue(snapshot.fields[key] || '');
                    // Textareas may normalize CRLF: do not patch untouched text.
                    this.baseline[key] = this.fields[key].getValue();
                }
                this.setBusy(false);
                this.info.setValue('Загружено. Сохраняются только изменённые поля.');
            }, failure: function(error) {
                if (this.isDestroyed) return;
                this.setBusy(false); this.info.setValue(Deluge.stream.error(error));
            }
        });
    },
    reload: function() {
        if (this.busy) return;
        if (!this.dirty()) return this.load();
        Ext.Msg.confirm('Метаданные', 'Потерять черновик и перечитать сохранённые данные?', function(answer) {
            if (answer === 'yes' && !this.isDestroyed) this.load();
        }, this);
    },
    json: function(format) {
        if (this.busy || !this.snapshot) return;
        try {
            // Native JSON.parse, never eval/Ext.decode of untrusted client data.
            var value = JSON.parse(this.fields.data.getValue());
            if (format) this.fields.data.setValue(JSON.stringify(value, null, 2));
            this.info.setValue(format ? 'JSON отформатирован в черновике.' : 'Корректный JSON.');
        } catch (error) {
            this.info.setValue('Не JSON. Произвольный текст всё равно можно сохранить.');
        }
    },
    save: function() {
        if (this.busy || !this.snapshot) return;
        var changes = {};
        for (var key in this.fields) {
            var value = this.fields[key].getValue();
            if (value !== this.baseline[key]) changes[key] = value;
        }
        this.setBusy(true);
        this.info.setValue('Сохранение…');
        this.invoke('set_metadata', [1, this.torrentId, this.recordId, this.snapshot.version, changes], {scope: this,
            success: function(snapshot) {
                if (this.isDestroyed) return;
                this.snapshot = snapshot; this.setBusy(false);
                for (var key in this.fields) this.baseline[key] = this.fields[key].getValue();
                this.info.setValue('Метаданные сохранены. Кэш Lampa может обновиться позже.');
            }, failure: function(error) {
                if (this.isDestroyed) return;
                this.setBusy(false); this.info.setValue(Deluge.stream.error(error));
            }
        });
    }
});

Deluge.stream.Panel = Ext.extend(Ext.Panel, {
    title: 'Stream', layout: 'border',
    initComponent: function() {
        this.info = new Ext.form.DisplayField({value: 'Выберите торрент.', style: 'padding:5px'});
        this.files = new Ext.grid.GridPanel({region: 'center', autoScroll: true,
            store: new Ext.data.JsonStore({root: 'files', idProperty: 'id', fields: ['id', 'index', 'path', 'size', 'ready', 'play_url']}),
            columns: [{header: 'Файл', dataIndex: 'path', width: 320, renderer: Ext.util.Format.htmlEncode},
                {header: 'Размер', dataIndex: 'size', width: 90},
                {header: 'Готов', dataIndex: 'ready', renderer: function(v) { return v ? 'Да' : 'Нет'; }}],
            sm: new Ext.grid.RowSelectionModel({singleSelect: true}),
            tbar: [{text: 'Метаданные', handler: function() { this.owner.editMetadata(); }, scope: this},
                {text: 'Скачать файл', handler: function() { this.owner.download('file'); }, scope: this},
                {text: 'Скачать ZIP', handler: function() { this.owner.download('zip'); }, scope: this},
                {text: 'Ссылка воспроизведения', handler: function() { this.owner.copyLink(); }, scope: this},
                {text: '← 128', handler: function() { this.owner.fileOffset = Math.max(0, (this.owner.fileOffset || 0) - 128); this.owner.refresh(); }, scope: this},
                {text: '128 →', handler: function() { if (this.nextOffset !== null && this.nextOffset !== undefined) { this.owner.fileOffset = this.nextOffset; this.owner.refresh(); } }, scope: this}]
        });
        this.items = [{region: 'north', height: 54, border: false, items: [this.info]}, this.files];
        Deluge.stream.Panel.superclass.initComponent.call(this);
    },
    clear: function() { this.torrentId = null; this.row = null; this.files.store.removeAll(); this.info.setValue('Выберите торрент.'); },
    update: function(id) {
        if (this.torrentId !== id) { this.clear(); this.owner.fileOffset = 0; }
        this.torrentId = id; this.owner.refresh();
    },
    showStatus: function(row) {
        this.row = row;
        var parts = [row.is_temporary ? 'Временный (FIFO ' + row.fifo_sequence + ')' : 'Постоянный', row.published ? 'Опубликован' : 'Не опубликован'];
        if (row.active_stream_requests) parts.push('Стриминг: HTTP-запросов ' + row.active_stream_requests);
        else if (row.streaming_protected) parts.push('Ожидание продолжения');
        parts.push('Предзагрузка: ' + row.preload_jobs, 'Скачивание файла: ' + row.file_downloads, 'Скачивание ZIP: ' + row.zip_downloads);
        parts.push('Защита: ' + (row.protection_reasons.join(', ') || 'нет'), 'Очистка: ' + (row.cleanup_reason || 'нет'));
        this.info.setValue(Ext.util.Format.htmlEncode(parts.join(' · ')));
    }
});

Ext.ns('Deluge.plugins');
Deluge.plugins.Stream = Ext.extend(Deluge.Plugin, {
    name: 'Stream',
    onEnable: function() {
        this.alive = true; this.busy = false; this.infoBusy = false; this.serial = 0;
        this.page = deluge.preferences.addPage(new Deluge.ux.preferences.StreamPage());
        this.panel = deluge.details.add(new Deluge.stream.Panel());
        this.panel.owner = this;
        var self = this;
        this.menu = deluge.menus.torrent.add({text: 'Stream', menu: [
            {text: 'Сделать постоянным', handler: function() { self.mutate('set_retention', false); }},
            {text: 'Сделать временным (разрешает удаление данных)', handler: function() {
                Ext.Msg.confirm('Stream', 'Автоочистка может удалить торрент вместе с данными. Продолжить?', function(answer) { if (answer === 'yes') self.mutate('set_retention', true); });
            }},
            {text: 'Опубликовать в TorrServer', handler: function() { self.mutate('set_published', true); }},
            {text: 'Закрыть TorrServer', handler: function() { self.mutate('set_published', false); }},
            {text: 'Скачать торрент ZIP', handler: function() { self.download('zip'); }}
        ]});
        this.nativeFiles = deluge.details.items.find(function(item) { return item instanceof Deluge.details.FilesTab; });
        this.fileMenu = new Ext.menu.Menu({items: [
            {text: 'Скачать файл через Stream', handler: function() { self.downloadNative(); }},
            {text: 'Приоритеты Deluge', handler: function() { deluge.menus.filePriorities.showAt(self.filePoint); }}
        ]});
        if (this.nativeFiles) this.nativeFiles.on('contextmenu', this.fileContext, this);
        this.poll = {run: this.refresh, scope: this, interval: 1000};
        Ext.TaskMgr.start(this.poll);
    },
    fileContext: function(node, event) {
        this.filePoint = event.getPoint();
        var self = this, point = this.filePoint;
        // FilesTab installs its native handler lazily on render. Show ours
        // after both handlers, independent of registration order.
        (function() {
            if (!self.alive) return;
            deluge.menus.filePriorities.hide();
            self.fileMenu.showAt(point);
        }).defer(1);
    },
    refresh: function() {
        if (!this.alive || this.busy || !deluge.client.stream || !deluge.client.stream.get_status || !deluge.client.stream.get_download_info) return;
        var record = deluge.torrents.getSelected();
        if (!record) { this.panel.clear(); return; }
        var id = record.id, sequence = ++this.serial;
        if (this.panel.torrentId !== id) { this.panel.clear(); this.fileOffset = 0; }
        this.panel.torrentId = id;
        this.busy = true;
        deluge.client.stream.get_web_status({scope: this, success: function(status) {
            if (!this.alive || sequence !== this.serial) return;
            if (!status.available) { this.busy = false; this.panel.row = null; this.panel.info.setValue(Deluge.stream.error(status)); return; }
            deluge.client.stream.get_status(1, [id], {scope: this, success: function(rows) {
                this.busy = false;
                if (!this.current(id, sequence)) return;
                if (!rows[id]) { this.panel.clear(); return; }
                this.panel.torrentId = id;
                this.panel.showStatus(rows[id]);
                if (this.infoBusy) return;
                this.infoBusy = true;
                var offset = this.fileOffset || 0, rid = rows[id].record_id;
                deluge.client.stream.get_download_info(1, id, rid, offset, {scope: this, success: function(info) {
                    this.infoBusy = false;
                    if (!this.fileCurrent(id, rid, offset)) return;
                    var selected = this.panel.files.getSelectionModel().getSelected();
                    this.panel.files.store.loadData(info);
                    if (selected && this.panel.files.store.getById(selected.id)) this.panel.files.getSelectionModel().selectRecords([this.panel.files.store.getById(selected.id)]);
                    this.panel.zipReady = info.zip_ready;
                    this.panel.nextOffset = info.next_offset;
                }, failure: function(error) { this.infoBusy = false; if (this.fileCurrent(id, rid, offset)) { this.panel.files.store.removeAll(); this.panel.info.setValue(Deluge.stream.error(error)); } }});
            }, failure: function(error) { this.busy = false; if (this.current(id, sequence)) { this.panel.row = null; this.panel.info.setValue(Deluge.stream.error(error)); } }});
        }, failure: function() { this.busy = false; if (this.alive) this.panel.info.setValue('Core недоступен.'); }});
    },
    current: function(id, sequence) {
        var selected = deluge.torrents.getSelected();
        return this.alive && sequence === this.serial && selected && selected.id === id;
    },
    fileCurrent: function(id, rid, offset) {
        var selected = deluge.torrents.getSelected();
        return this.alive && selected && selected.id === id && this.panel.row
            && this.panel.row.record_id === rid && (this.fileOffset || 0) === offset;
    },
    mutate: function(method, value) {
        var ids = deluge.torrents.getSelectedIds(), self = this;
        if (!ids.length || ids.length > 128) return Deluge.stream.notice('Выберите от 1 до 128 торрентов.');
        deluge.client.stream.get_status(1, ids, {success: function(rows) {
            var results = [], index = 0;
            function next() {
                if (!self.alive) return;
                if (index === ids.length) { self.refresh(); Deluge.stream.notice(results.join('\n')); return; }
                var id = ids[index++], row = rows[id];
                if (!row) { results.push(id + ': нет доступа'); next(); return; }
                deluge.client.stream[method](1, id, row.record_id, value, {
                    success: function() { results.push(id + ': сохранено'); next(); },
                    failure: function(error) { results.push(id + ': ' + Deluge.stream.error(error)); next(); }
                });
            }
            next();
        }, failure: function(error) { Deluge.stream.notice(Deluge.stream.error(error)); }});
    },
    downloadNative: function() {
        var nodes = this.nativeFiles.getSelectionModel().getSelectedNodes();
        if (nodes.length !== 1 || !nodes[0].isLeaf()) return Deluge.stream.notice('Выберите один файл.');
        var index = nodes[0].attributes.fileIndex, id = this.nativeFiles.torrentId, self = this;
        deluge.client.stream.get_status(1, [id], {success: function(rows) {
            if (!self.alive || !rows[id]) return Deluge.stream.notice('Торрент недоступен.');
            deluge.client.stream.get_download_info(1, id, rows[id].record_id, 0, index, {success: function(info) {
                if (!self.alive) return;
                if (!info.files.length || !info.files[0].ready) return Deluge.stream.notice(Deluge.stream.messages.file_not_ready);
                self.requestDownload(id, rows[id], 'file', info.files[0].id);
            }, failure: function(error) { Deluge.stream.notice(Deluge.stream.error(error)); }});
        }, failure: function(error) { Deluge.stream.notice(Deluge.stream.error(error)); }});
    },
    download: function(kind) {
        var selected = deluge.torrents.getSelected(), row = this.panel.row;
        if (!selected || !row || selected.id !== this.panel.torrentId) return Deluge.stream.notice('Дождитесь обновления Stream.');
        var file = this.panel.files.getSelectionModel().getSelected();
        if (kind === 'file' && (!file || !file.get('ready'))) return Deluge.stream.notice(Deluge.stream.messages.file_not_ready);
        if (kind === 'zip' && !this.panel.zipReady) return Deluge.stream.notice(Deluge.stream.messages.torrent_not_ready);
        this.requestDownload(selected.id, row, kind, file ? file.id : 1);
    },
    requestDownload: function(id, row, kind, fileId) {
        deluge.client.stream.create_download_ticket(1, id, row.record_id, kind, fileId, {scope: this,
            success: function(ticket) {
                if (!this.alive) return;
                // Navigate an ordinary link. No fetch/Blob/Base64 or web disk access.
                var a = document.createElement('a'); a.href = ticket.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.referrerPolicy = 'no-referrer';
                document.body.appendChild(a); a.click(); document.body.removeChild(a);
            }, failure: function(error) { Deluge.stream.notice(Deluge.stream.error(error)); }
        });
    },
    copyLink: function() {
        var file = this.panel.files.getSelectionModel().getSelected();
        if (!file || !file.get('play_url')) return Deluge.stream.notice('Нужны публикация и public_base_url.');
        Ext.Msg.show({title: 'Ссылка воспроизведения', msg: 'Скопируйте ссылку:', prompt: true, value: file.get('play_url'), buttons: Ext.Msg.OK});
    },
    editMetadata: function() {
        var ids = deluge.torrents.getSelectedIds(), row = this.panel.row;
        if (ids.length !== 1 || !row || ids[0] !== this.panel.torrentId) return Deluge.stream.notice('Выберите один торрент и дождитесь обновления Stream.');
        if (this.metadataWindow && !this.metadataWindow.isDestroyed) return this.metadataWindow.toFront();
        this.metadataWindow = new Deluge.stream.MetadataWindow({torrentId: ids[0], recordId: row.record_id});
        this.metadataWindow.show();
    },
    onDisable: function() {
        this.alive = false; ++this.serial;
        if (this.metadataWindow && !this.metadataWindow.isDestroyed) this.metadataWindow.destroy();
        if (this.poll) Ext.TaskMgr.stop(this.poll);
        if (this.nativeFiles) this.nativeFiles.un('contextmenu', this.fileContext, this);
        if (this.fileMenu) this.fileMenu.destroy();
        if (this.menu) deluge.menus.torrent.remove(this.menu);
        if (this.panel && deluge.details.getActiveTab() === this.panel) deluge.details.setActiveTab(0);
        if (this.panel) deluge.details.remove(this.panel, true);
        // Native Preferences.onPageSelect assumes a non-empty selection.
        if (this.page && deluge.preferences.currentPage === 'Stream') deluge.preferences.list.select(0);
        if (this.page) deluge.preferences.removePage(this.page);
        this.panel = this.page = null;
    }
});
Deluge.registerPlugin('Stream', Deluge.plugins.Stream);
