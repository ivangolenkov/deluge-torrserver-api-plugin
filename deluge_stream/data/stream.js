/* Initial diagnostics panel only. No torrent policy or binary data proxy. */
Ext.ns('Deluge.ux.preferences');
Deluge.ux.preferences.StreamPage = Ext.extend(Ext.Panel, {
    title: 'Stream',
    layout: 'fit',
    initComponent: function() {
        this.info = new Ext.form.DisplayField({
            value: 'Stream: проверка core…',
            style: 'padding: 12px'
        });
        this.items = [this.info];
        Deluge.ux.preferences.StreamPage.superclass.initComponent.call(this);
        this.on('show', this.refresh, this);
    },
    refresh: function() {
        deluge.client.stream.get_web_status({
            scope: this,
            success: function(status) {
                if (this.isDestroyed) return;
                var messages = {
                    ready: 'Core доступен. Политика хранения доступна через RPC; TorrServer API пока не включён.',
                    core_unavailable: 'Core Stream недоступен. Проверьте подключение и плагин в deluged.',
                    incompatible_core: 'Версии core и web Stream несовместимы. Установите одинаковый artifact.',
                    web_disabled: 'Web-часть Stream отключена.'
                };
                // Fixed strings: no untrusted core messages or HTML rendering.
                this.info.setValue(status.recovery_required
                    ? 'После аварийного завершения требуется подтверждение политики администратором. Автоочистка и прежняя публикация заблокированы.'
                    : (messages[status.reason] || 'Stream недоступен.'));
            },
            failure: function() {
                if (!this.isDestroyed) this.info.setValue('Stream недоступен.');
            }
        });
    }
});

Ext.ns('Deluge.plugins');
Deluge.plugins.Stream = Ext.extend(Deluge.Plugin, {
    name: 'Stream',
    onEnable: function() {
        this.page = deluge.preferences.addPage(new Deluge.ux.preferences.StreamPage());
    },
    onDisable: function() {
        if (this.page) {
            deluge.preferences.removePage(this.page);
            this.page = null;
        }
    }
});
Deluge.registerPlugin('Stream', Deluge.plugins.Stream);
