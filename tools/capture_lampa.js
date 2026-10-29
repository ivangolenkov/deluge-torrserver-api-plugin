// Optional Playwright MCP browser_run_code_unsafe input, not plugin code.
// Runs unmodified Lampa bundle. Only the pinned local stand is reachable.
// API methods are invoked directly; this is NOT UI/player end-to-end coverage.
async (page) => {
  await page.context().route('**/*', route => {
    const u = new URL(route.request().url());
    return u.hostname === '127.0.0.1' && u.port === '18091'
      ? route.continue() : route.abort();
  });
  await page.addInitScript(() => {
    localStorage.clear();
    localStorage.setItem('language', 'en');
    localStorage.setItem('torrserver_url', 'http://127.0.0.1:18091');
    localStorage.setItem('torrserver_preload', 'true');
    localStorage.setItem('torrserver_savedb', 'true');
  });
  await page.goto('http://127.0.0.1:18091/lampa/index.html');
  await page.waitForFunction(() => window.Lampa);
  const result = await page.evaluate(async () => {
    const mark = async (name, requester = 'lampa-module-browser') => {
      const r = await fetch('/__step', {
        method: 'POST', body: JSON.stringify({name, requester})
      });
      if (!r.ok) throw new Error('capture marker failed');
    };
    const call = async (name, ...args) => {
      await mark('lampa-' + name);
      return new Promise(resolve => {
        const timer = setTimeout(() => resolve({error: 'timeout'}), 15000);
        Lampa.Torserver[name](...args, value => {
          clearTimeout(timer); resolve({value});
        }, () => {
          clearTimeout(timer); resolve({error: 'client-callback'});
        });
      });
    };
    const settings = await call('connected');
    if (!settings.value) throw new Error('Lampa connected failed');
    const added = await call('add', {
      link: 'http://127.0.0.1:18091/fixture.torrent', title: 'Fixture',
      poster: '', data: {fixture: true}
    });
    if (!added.value) throw new Error('Lampa add failed');
    const hash = added.value.hash;
    const files = await call('files', hash);
    const list = await call('my');
    const before = await call('viewed', hash);
    const set = await call('viewedSet', hash, files.value.file_stats[0].id, 12.5);
    const viewed = await call('viewed', hash);
    const cache = await call('cache', hash);
    if (!viewed.value || viewed.value[0].file_index !== 1 || !cache.value) {
      throw new Error('viewed/cache roundtrip failed');
    }
    const stream = Lampa.Torserver.stream(files.value.file_stats[0].path, hash, 1);
    const replies = [];
    for (const [name, url, headers] of [
      ['browser-preload', stream, {}],
      ['browser-stat', stream.replace('&preload', '&stat'), {}],
      ['browser-seek', Lampa.Torserver.toPlayUrl(stream), {Range:'bytes=65530-65550'}]
    ]) {
      await mark(name, 'browser-scripted-fetch');
      const response = await fetch(url, {headers});
      const body = new Uint8Array(await response.arrayBuffer());
      replies.push({name, status: response.status, bytes: body.length});
      if (name === 'browser-seek' && (response.status !== 206 || body.length !== 21
          || body.some((b, i) => b !== (65530 + i) % 256))) {
        throw new Error('seek bytes differ from the local seed');
      }
    }
    const drop = await call('drop', hash);
    if (!list.value || drop.error) throw new Error('list/drop failed');
    return {
      version: Lampa.Manifest.app_version, userAgent: navigator.userAgent,
      statType: typeof files.value.stat, fileStats: files.value.file_stats,
      before, set, viewed, replies, drop, fullUiPlaybackVerified: false
    };
  });
  await page.evaluate(() => fetch('/__finish', {method: 'POST'}));
  await page.goto('about:blank');
  return result;
}
