const $ = (id) => document.getElementById(id);
let dashboard = null,
  speakers = null,
  storagePolicy = null,
  lighting = null,
  priceChecks = null,
  systemMonitor = null,
  backupState = null,
  systemMonitorHours = 6,
  ubntWifi = null,
  ubntLink = null,
  ubntNewNetwork = null,
  policyLoading = false,
  priceBusy = false,
  priceEditingId = null,
  crashAnalysisBusy = false,
  usbPortBusy = false,
  backupBusy = false,
  busy = false,
  tileEditing = false,
  tileDrag = null,
  toastTimer = 0,
  speedPoll = 0,
  storagePoll = 0,
  lightingPoll = 0,
  systemMonitorPoll = 0,
  usbPoll = 0,
  backupPoll = 0,
  ubntPoll = 0,
  ubntLastCompletion = '',
  backupLastCompletion = '',
  sonosTimeline = { position: 0, duration: 0, playing: false, updatedAt: 0 };
const TILE_ORDER_STORAGE_KEY = 'van-dashboard.tile-order.v1';
function esc(v) {
  return String(v ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
}
function toast(message, bad = false) {
  clearTimeout(toastTimer);
  const el = $('toast');
  el.textContent = message;
  el.className = bad ? 'show bad' : 'show';
  toastTimer = setTimeout(() => (el.className = ''), 3400);
}
async function json(url, options) {
  const response = await fetch(url, { cache: 'no-store', ...(options || {}) });
  let data;
  try {
    data = await response.json();
  } catch (_) {
    data = { message: `Server returned ${response.status}` };
  }
  if (!response.ok || data.ok === false)
    throw new Error(data.message || `Request failed (${response.status})`);
  return data;
}
async function post(endpoint, params = {}) {
  return json('/api/' + endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Van-Dashboard': '1' },
    body: new URLSearchParams(params),
  });
}
async function action(work) {
  if (busy) return;
  busy = true;
  document.body.classList.add('busy');
  try {
    const result = await work();
    if (result?.message) toast(result.message);
    await refresh();
  } catch (error) {
    toast(error.message, true);
  } finally {
    busy = false;
    document.body.classList.remove('busy');
  }
}
function age(ts) {
  if (!ts) return 'never';
  const secs = Math.max(0, Date.now() / 1000 - ts);
  return secs < 90 ? `${Math.round(secs)}s ago` : `${Math.round(secs / 60)}m ago`;
}
function formatBytes(value) {
  if (!Number.isFinite(value)) return '—';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let size = Math.max(0, value),
    unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(size >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}
function formatRate(value) {
  return Number.isFinite(value) ? `${formatBytes(value)}/s` : '—';
}
function eventTime(timestamp) {
  if (!Number.isFinite(timestamp)) return 'unknown time';
  return new Date(timestamp * 1000).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
  });
}
function backupAge(timestamp) {
  if (!Number.isFinite(timestamp)) return 'never';
  const seconds = Math.max(0, Date.now() / 1000 - timestamp);
  if (seconds < 90) return `${Math.round(seconds)}s ago`;
  if (seconds < 90 * 60) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 48 * 60 * 60) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}
function monitorRangeLabel(hours = systemMonitorHours) {
  if (hours === 168) return '7 days';
  if (hours === 720) return '30 days';
  return `${hours} hour${hours === 1 ? '' : 's'}`;
}
function cpuListLabel(cpuIds) {
  const ids = Array.isArray(cpuIds) ? cpuIds.map(Number).filter(Number.isInteger) : [];
  if (!ids.length) return '';
  const consecutive = ids.every((value, index) => index === 0 || value === ids[index - 1] + 1);
  return consecutive && ids.length > 1 ? `${ids[0]}–${ids.at(-1)}` : ids.join(', ');
}
function thermalSensorLabel(sensor) {
  const type = String(sensor?.type || sensor?.zone || 'Thermal sensor');
  const base = type.toLowerCase() === 'cpu-thermal' ? 'CPU / SoC' : type;
  const cpus = cpuListLabel(sensor?.cpu_ids);
  if (!cpus) return base;
  return `${base} · core${sensor.cpu_ids.length === 1 ? '' : 's'} ${cpus}${sensor.shared ? ' (shared sensor)' : ''}`;
}
function networkState(id, value) {
  const el = $(id);
  el.classList.remove('good', 'bad');
  if (value === true) el.classList.add('good');
  else if (value === false) el.classList.add('bad');
}
function dashboardTiles() {
  return Array.from($('tile-grid').querySelectorAll(':scope > [data-dashboard-tile]'));
}
function tileName(tile) {
  return (
    tile.dataset.tileLabel || tile.querySelector('.tile-title')?.textContent?.trim() || tile.id
  );
}
function restoreTileOrder() {
  let stored;
  try {
    stored = JSON.parse(localStorage.getItem(TILE_ORDER_STORAGE_KEY));
  } catch (_) {
    return;
  }
  if (!Array.isArray(stored)) return;
  const grid = $('tile-grid'),
    current = dashboardTiles(),
    byId = new Map(current.map((tile) => [tile.id, tile])),
    seen = new Set(),
    ordered = [];
  for (const id of stored) {
    if (typeof id !== 'string' || seen.has(id) || !byId.has(id)) continue;
    seen.add(id);
    ordered.push(byId.get(id));
  }
  for (const tile of current) {
    if (!seen.has(tile.id)) ordered.push(tile);
  }
  for (const tile of ordered) grid.append(tile);
}
function saveTileOrder() {
  try {
    localStorage.setItem(
      TILE_ORDER_STORAGE_KEY,
      JSON.stringify(dashboardTiles().map((tile) => tile.id)),
    );
  } catch (_) {
    /* The layout still works for this page if browser storage is unavailable. */
  }
}
function announceTilePosition(tile) {
  const tiles = dashboardTiles(),
    position = tiles.indexOf(tile) + 1;
  $('tile-edit-status').textContent = `${tileName(tile)} moved to position ${position}`;
}
function setTileEditing(enabled) {
  if (!enabled && tileDrag) finishTileDrag();
  tileEditing = Boolean(enabled);
  document.body.classList.toggle('tiles-editing', tileEditing);
  const button = $('tile-edit'),
    icon = $('tile-edit-icon');
  button.setAttribute('aria-pressed', String(tileEditing));
  button.setAttribute('aria-label', tileEditing ? 'Finish editing tile positions' : 'Edit tile positions');
  button.title = tileEditing ? 'Done arranging tiles' : 'Edit tile positions';
  icon.textContent = tileEditing ? '✓' : '✎';
  for (const tile of dashboardTiles()) {
    tile.setAttribute('aria-grabbed', 'false');
    if (tileEditing) {
      if (!tile.hasAttribute('tabindex') && !tile.matches('a, button')) {
        tile.dataset.editTabAdded = '1';
        tile.tabIndex = 0;
      }
      if (tile.matches('button:disabled')) {
        tile.dataset.editWasDisabled = '1';
        tile.disabled = false;
      }
    } else {
      tile.removeAttribute('aria-grabbed');
      if (tile.dataset.editTabAdded) {
        tile.removeAttribute('tabindex');
        delete tile.dataset.editTabAdded;
      }
      if (tile.dataset.editWasDisabled) {
        tile.disabled = true;
        delete tile.dataset.editWasDisabled;
      }
    }
  }
  if (!tileEditing) {
    saveTileOrder();
    refresh();
    refreshSpeedtest();
  }
}
function reorderTileToIndex(tile, nextIndex) {
  const tiles = dashboardTiles(),
    currentIndex = tiles.indexOf(tile);
  if (currentIndex < 0) return;
  nextIndex = Math.max(0, Math.min(tiles.length - 1, nextIndex));
  if (nextIndex === currentIndex) return;
  tiles.splice(currentIndex, 1);
  tiles.splice(nextIndex, 0, tile);
  for (const item of tiles) $('tile-grid').append(item);
  announceTilePosition(tile);
}
function finishTileDrag(event) {
  if (!tileDrag || (event && event.pointerId !== tileDrag.pointerId)) return;
  const tile = tileDrag.tile;
  tile.classList.remove('tile-dragging');
  tile.setAttribute('aria-grabbed', 'false');
  try {
    tile.releasePointerCapture(tileDrag.pointerId);
  } catch (_) {
    /* Pointer capture may already have ended. */
  }
  tileDrag = null;
  saveTileOrder();
  announceTilePosition(tile);
}
function setupTileEditing() {
  const grid = $('tile-grid');
  restoreTileOrder();
  $('tile-edit').addEventListener('click', () => {
    if (busy) {
      toast('Wait for the current control action to finish');
      return;
    }
    setTileEditing(!tileEditing);
  });
  grid.addEventListener(
    'click',
    (event) => {
      if (!tileEditing) return;
      event.preventDefault();
      event.stopImmediatePropagation();
    },
    true,
  );
  grid.addEventListener('pointerdown', (event) => {
    if (!tileEditing || (event.pointerType === 'mouse' && event.button !== 0)) return;
    const tile = event.target.closest('[data-dashboard-tile]');
    if (!tile || tile.parentElement !== grid) return;
    event.preventDefault();
    tileDrag = { tile, pointerId: event.pointerId };
    tile.classList.add('tile-dragging');
    tile.setAttribute('aria-grabbed', 'true');
    tile.setPointerCapture(event.pointerId);
  });
  grid.addEventListener('pointermove', (event) => {
    if (!tileDrag || event.pointerId !== tileDrag.pointerId) return;
    event.preventDefault();
    const edge = 64;
    if (event.clientY < edge) window.scrollBy(0, -12);
    else if (event.clientY > window.innerHeight - edge) window.scrollBy(0, 12);
    const target = document
      .elementFromPoint(event.clientX, event.clientY)
      ?.closest('[data-dashboard-tile]');
    if (!target || target === tileDrag.tile || target.parentElement !== grid) return;
    const tiles = dashboardTiles(),
      currentIndex = tiles.indexOf(tileDrag.tile),
      targetIndex = tiles.indexOf(target);
    if (currentIndex < targetIndex) target.after(tileDrag.tile);
    else target.before(tileDrag.tile);
  });
  grid.addEventListener('pointerup', finishTileDrag);
  grid.addEventListener('pointercancel', finishTileDrag);
  grid.addEventListener('keydown', (event) => {
    if (!tileEditing) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      setTileEditing(false);
      $('tile-edit').focus();
      return;
    }
    const offsets = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -2, ArrowDown: 2 },
      offset = offsets[event.key];
    if (!offset) return;
    const tile = event.target.closest('[data-dashboard-tile]');
    if (!tile || tile.parentElement !== grid) return;
    event.preventDefault();
    reorderTileToIndex(tile, dashboardTiles().indexOf(tile) + offset);
    saveTileOrder();
    tile.focus();
  });
}
function renderUbntTile() {
  const tile = $('ubnt-wifi');
  tile.classList.remove('connected', 'unavailable');
  if (!ubntLink) {
    networkState('ubnt-wifi-dot', null);
    networkState('ubnt-radio-dot', null);
    $('ubnt-wifi-state').textContent = 'NO DATA';
    $('ubnt-wifi-summary').textContent = 'Waiting for antenna status…';
    return;
  }
  const u = ubntLink;
  if (u.reachable === false) {
    tile.classList.add('unavailable');
    networkState('ubnt-wifi-dot', false);
    networkState('ubnt-radio-dot', null);
    $('ubnt-wifi-state').textContent = 'UNAVAILABLE';
    $('ubnt-wifi-summary').textContent = 'No UBNT Ethernet response';
    return;
  }
  if (u.reachable !== true) {
    networkState('ubnt-wifi-dot', null);
    networkState('ubnt-radio-dot', null);
    $('ubnt-wifi-state').textContent = 'NO DATA';
    $('ubnt-wifi-summary').textContent = 'Waiting for antenna status…';
    return;
  }
  tile.classList.add('connected');
  networkState('ubnt-wifi-dot', true);
  $('ubnt-wifi-state').textContent = 'CONNECTED';
  const details = [u.ssid || 'Unknown SSID'];
  if (u.error) {
    networkState('ubnt-radio-dot', null);
    details.push('Wi-Fi status unavailable');
  } else {
    const connected = u.connected === true;
    networkState('ubnt-radio-dot', connected);
    if (!connected) details.push('Not associated');
    if (connected && Number.isFinite(u.signal_dbm)) details.push(`${u.signal_dbm} dBm`);
    if (connected && Number.isFinite(u.ccq_percent)) details.push(`${u.ccq_percent}% CCQ`);
    else if (connected && Number.isFinite(u.quality_percent))
      details.push(`${u.quality_percent}% quality`);
  }
  $('ubnt-wifi-summary').textContent = details.join(' · ');
}
function renderConnectivity(response) {
  const c = response.connectivity,
    r = c.router || {},
    u = c.ubnt || {},
    online = c.internet?.online;
  networkState('internet-dot', online === null && r.reachable === false ? false : online);
  $('mwan-mode').textContent =
    r.mode || (online === false || r.reachable === false ? 'No active uplink' : 'Unknown');
  ubntLink = u;
  renderUbntTile();
  $('mwan-list').innerHTML = (r.interfaces || [])
    .map(
      (i) =>
        `<span class="mwan-chip ${esc(i.state)}" title="${esc(i.detail || '')}">${esc(i.name)} · ${esc(i.state)}</span>`,
    )
    .join('');
  $('openwrt-age').textContent = r.error
    ? `MWAN3 error · ${r.error}`
    : c.last_error
      ? `Collector error · ${c.last_error}`
      : c.checked_at
        ? `${c.stale ? 'Stale' : 'Updated'} · ${age(c.checked_at)}`
        : c.refreshing
          ? 'Checking…'
          : 'Waiting for MWAN3';
}
async function refreshConnectivity() {
  try {
    renderConnectivity(await json('/api/connectivity'));
  } catch (error) {
    $('openwrt-age').textContent = error.message;
    ubntLink = null;
    renderUbntTile();
  }
}
function atTime(ts) {
  return ts
    ? '@ ' +
        new Date(ts * 1000).toLocaleTimeString([], {
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
          hour12: false,
        })
    : '';
}
function renderSpeedtest(response) {
  const s = response.speedtest,
    button = $('speedtest-button'),
    running = s.status === 'running',
    label = $('speedtest-label');
  if (!label.dataset.idleLabel) label.dataset.idleLabel = label.textContent;
  button.disabled = !tileEditing && running;
  button.classList.toggle('running', running);
  button.setAttribute('aria-busy', String(running));
  label.textContent = running ? label.dataset.runningLabel : label.dataset.idleLabel;
  $('speed-results').classList.toggle('bad', s.status === 'error');
  if (running)
    $('speed-results').innerHTML = '<strong>Testing current route…</strong>This can take a minute';
  else if (s.status === 'complete')
    $('speed-results').innerHTML =
      `<strong>↓ ${Number(s.download_mbps).toFixed(1)} Mbps · ↑ ${Number(s.upload_mbps).toFixed(1)} Mbps</strong>Latency ${Number(s.latency_ms).toFixed(1)} ms ${atTime(s.completed_at)}`;
  else if (s.status === 'error')
    $('speed-results').innerHTML =
      `<strong>Speed test failed</strong>${esc(s.error || 'Unknown error')} ${atTime(s.completed_at)}`;
  else $('speed-results').innerHTML = "<strong>Not run yet</strong>Uses vanpi's current route";
}
async function refreshSpeedtest() {
  clearTimeout(speedPoll);
  try {
    const response = await json('/api/speedtest');
    renderSpeedtest(response);
    if (response.speedtest.status === 'running') speedPoll = setTimeout(refreshSpeedtest, 1000);
  } catch (error) {
    $('speed-results').innerHTML = `<strong>Speed test unavailable</strong>${esc(error.message)}`;
  }
}
async function startSpeedtest() {
  if ($('speedtest-button').disabled) return;
  $('speedtest-button').disabled = true;
  try {
    const response = await post('speedtest');
    renderSpeedtest(response);
    if (response.speedtest.status === 'running') speedPoll = setTimeout(refreshSpeedtest, 1000);
  } catch (error) {
    toast(error.message, true);
    $('speedtest-button').disabled = false;
  }
}
function usbEventLabel(event) {
  if (!event?.kind || !event?.at) return '';
  const label =
    event.kind === 'unplugged'
      ? 'Unplugged'
      : event.kind === 'replugged'
        ? 'Replugged'
        : 'Plugged';
  return `${label} ${eventTime(event.at)}`;
}
function renderUsbPorts(state) {
  const operation = state?.operation || { status: 'idle' },
    running = operation.status === 'running';
  $('usb-operation').textContent = running
    ? `${operation.action} · ${operation.key}`
    : operation.status === 'error'
      ? `Failed · ${operation.error || 'unknown error'}`
      : state?.last_error
        ? 'Port status incomplete'
        : state?.checked_at
          ? `Updated ${age(state.checked_at)}`
          : 'No port data';
  $('usb-panel').classList.toggle('usb-port-busy', running);
  $('usb-hub-list').innerHTML = (state?.hubs || [])
    .map((hub) => {
      const power = hub.method === 'power';
      return `<article class="usb-hub-card"><div class="usb-hub-head"><span><strong>${esc(hub.description)}</strong><small>Location ${esc(hub.location)}</small></span><b class="usb-method ${power ? 'power' : 'data'}">${power ? 'POWER + DATA' : 'DATA ONLY'}</b></div><div class="usb-port-grid">${(hub.ports || [])
        .map((port) => {
          const enabled = port.enabled !== false,
            mounted = port.mounted_labels || [],
            descriptions = port.device_descriptions || [],
            downstream = Number(port.downstream_device_count) || 0,
            label = `${hub.description} port ${port.port}`,
            detail = descriptions.length
              ? descriptions.join(', ')
              : downstream
                ? `${downstream} downstream device${downstream === 1 ? '' : 's'}`
                : 'Empty port',
            storage = (port.storage_labels || [])
              .map((item) => `<span class="usb-label">${esc(item)}</span>`)
              .join(''),
            blocked = mounted.length > 0,
            controlsDisabled = running || usbPortBusy;
          return `<section class="usb-port-card ${enabled ? 'enabled' : 'disabled'}"><div class="usb-port-title"><strong>Port ${port.port}</strong><span>${enabled ? 'ON' : 'OFF'}</span></div><p>${esc(detail)}</p>${downstream > descriptions.length ? `<small>${downstream} total downstream</small>` : ''}${storage ? `<div class="usb-labels">${storage}</div>` : ''}${blocked ? `<div class="usb-mounted">Mounted: ${esc(mounted.join(', '))}</div>` : ''}<div class="usb-port-actions"><button data-usb-port-action="on" data-usb-port-key="${esc(port.key)}" data-usb-port-label="${esc(label)}" ${controlsDisabled || enabled ? 'disabled' : ''}>On</button><button data-usb-port-action="off" data-usb-port-key="${esc(port.key)}" data-usb-port-label="${esc(label)}" ${controlsDisabled || !enabled || blocked ? 'disabled' : ''}>Off</button><button data-usb-port-action="cycle" data-usb-port-key="${esc(port.key)}" data-usb-port-label="${esc(label)}" ${controlsDisabled || !enabled || blocked ? 'disabled' : ''}>Cycle</button></div></section>`;
        })
        .join('')}</div></article>`;
    })
    .join('') || '<div class="speaker-loading">No USB port controls discovered</div>';
}
function renderUsbDevices(response) {
  const state = response.usb,
    tile = $('usb-devices'),
    present = Number(state.present_device_count) || 0,
    unplugged = Number(state.unplugged_device_count) || 0,
    labels = state.storage_labels || [],
    hasData = Number.isFinite(state.last_success_at),
    stale = Boolean(state.last_error);
  tile.classList.remove('good', 'warning', 'unknown');
  tile.classList.add(!hasData ? 'unknown' : stale || unplugged ? 'warning' : 'good');
  $('usb-pill').textContent = !hasData ? 'NO DATA' : stale ? 'STALE' : unplugged ? 'CHANGE' : 'LIVE';
  $('usb-summary').textContent = !hasData
    ? 'USB status unavailable'
    : unplugged
      ? `${unplugged} unplugged since monitoring began`
      : `${present} non-hub device${present === 1 ? '' : 's'} connected`;
  $('usb-connected').textContent = hasData ? String(present) : '—';
  $('usb-storage').textContent = labels.length ? labels.join(', ') : 'None detected';
  $('usb-status').textContent = state.last_error
    ? 'Stale · retrying'
    : state.last_success_at
      ? `Updated ${age(state.last_success_at)}`
      : 'No data';
  $('usb-panel').setAttribute('aria-busy', 'false');
  renderUsbPorts(response.usb_ports);
  const deviceRows = (state.devices || [])
    .map((device) => {
      const event = usbEventLabel(device.event),
        count = device.max_count > 1
          ? `${device.present_count}/${device.max_count}`
          : device.present_count > 0
            ? 'Connected'
            : 'Missing',
        labelsHtml = (device.labels || [])
          .map((label) => `<span class="usb-label">${esc(label)}</span>`)
          .join('');
      return `<article class="usb-device-row ${esc(device.status)}">
        <span class="usb-device-dot" aria-hidden="true"></span>
        <div class="usb-device-main"><strong>${esc(device.description)}</strong><small>Bus ${esc(device.bus)} · ID ${esc(device.device_id)}</small>${labelsHtml ? `<div class="usb-labels">${labelsHtml}</div>` : ''}${event ? `<time>${esc(event)}</time>` : ''}</div>
        <span class="usb-device-count">${esc(count)}</span>
      </article>`;
    })
    .join('');
  const errorRow = state.last_error
    ? `<div class="usb-error">${esc(state.last_error)}</div>`
    : '';
  $('usb-device-list').innerHTML =
    errorRow + (deviceRows || '<div class="speaker-loading">No USB devices reported</div>');
}
async function refreshUsbDevices(showErrors = false) {
  try {
    const response = await json('/api/usb-devices');
    renderUsbDevices(response);
    return response;
  } catch (error) {
    $('usb-devices').classList.remove('good', 'warning');
    $('usb-devices').classList.add('unknown');
    $('usb-pill').textContent = 'NO DATA';
    $('usb-summary').textContent = 'USB status unavailable';
    $('usb-connected').textContent = '—';
    $('usb-storage').textContent = '—';
    $('usb-status').textContent = 'Unavailable';
    $('usb-panel').setAttribute('aria-busy', 'false');
    $('usb-device-list').innerHTML = `<div class="speaker-loading">${esc(error.message)}</div>`;
    $('usb-hub-list').innerHTML = `<div class="speaker-loading">${esc(error.message)}</div>`;
    $('usb-operation').textContent = 'Unavailable';
    if (showErrors) toast(error.message, true);
    throw error;
  }
}
async function changeUsbPort(button) {
  if (usbPortBusy || button.disabled) return;
  const actionName = button.dataset.usbPortAction,
    label = button.dataset.usbPortLabel || 'this USB port';
  if (
    actionName !== 'on' &&
    !window.confirm(
      `${actionName === 'cycle' ? 'Cycle' : 'Turn off'} ${label}? Connected devices will disconnect, and data-only controls may leave power present.`,
    )
  )
    return;
  usbPortBusy = true;
  $('usb-panel').classList.add('usb-port-busy');
  try {
    const response = await post('usb-ports/action', {
      port: button.dataset.usbPortKey,
      action: actionName,
    });
    renderUsbPorts(response.usb_ports);
    toast(response.message || 'USB port action started');
  } catch (error) {
    toast(error.message, true);
    await refreshUsbDevices(false).catch(() => {});
  } finally {
    usbPortBusy = false;
  }
}
function renderBackups(response) {
  const state = response.backups,
    borg = state.borg || {},
    tm = state.time_machine || {},
    operation = state.operation || { status: 'idle' },
    tile = $('backups'),
    pill = $('backup-pill');
  backupState = state;
  tile.classList.remove('unknown', 'good', 'warning', 'running');
  tile.classList.add(
    state.health === 'good' ? 'good' : state.health === 'running' ? 'running' : 'warning',
  );
  pill.textContent =
    state.health === 'running' ? 'RUNNING' : state.health === 'good' ? 'CURRENT' : 'CHECK';
  const piLabel = Number.isFinite(borg.last_success_at)
      ? `Borg ${backupAge(borg.last_success_at)}`
      : 'No Borg success recorded',
    macLabel = Number.isFinite(tm.last_backup_at)
      ? `TM ${backupAge(tm.last_backup_at)}`
      : tm.error || 'No Time Machine history';
  $('backup-pi').textContent = piLabel;
  $('backup-mac').textContent = tm.running
    ? `Backing up${Number.isFinite(tm.progress_percent) ? ` · ${tm.progress_percent}%` : ''}`
    : macLabel;
  if (operation.status === 'running') {
    $('backup-summary').textContent = `Cloning vanpi to ${operation.target}…`;
  } else if (tm.running) {
    $('backup-summary').textContent = 'Time Machine backup in progress';
  } else {
    $('backup-summary').textContent = `${piLabel} · ${macLabel}`;
  }

  const borgDot = $('backup-borg-dot');
  borgDot.className = `backup-state-dot ${borg.stale ? 'bad' : 'good'}`;
  $('backup-borg-detail').textContent = Number.isFinite(borg.last_success_at)
    ? `Last successful archive ${eventTime(borg.last_success_at)} (${backupAge(borg.last_success_at)})`
    : 'No successful Borg archive is recorded';
  const tmDot = $('backup-tm-dot');
  tmDot.className = `backup-state-dot ${tm.running || Number.isFinite(tm.last_backup_at) ? 'good' : 'bad'}`;
  $('backup-tm-detail').textContent = tm.running
    ? `Backup in progress${Number.isFinite(tm.progress_percent) ? ` · ${tm.progress_percent}%` : ''}`
    : Number.isFinite(tm.last_backup_at)
      ? `Last completed ${eventTime(tm.last_backup_at)} (${backupAge(tm.last_backup_at)})`
      : tm.error || 'No completed snapshots found';

  const operationKey = `${operation.status}:${operation.started_at || ''}:${operation.completed_at || ''}`;
  if (operation.status === 'running') {
    $('backup-operation').textContent = `Cloning ${operation.target} · ${backupAge(operation.started_at)}`;
  } else if (operation.status === 'error') {
    $('backup-operation').textContent = `${operation.target || 'Clone'} failed`;
  } else if (operation.status === 'complete') {
    $('backup-operation').textContent = `${operation.target} completed ${backupAge(operation.completed_at)}`;
  } else {
    $('backup-operation').textContent = 'Idle';
  }
  if (
    backupLastCompletion &&
    operationKey !== backupLastCompletion &&
    ['complete', 'error'].includes(operation.status)
  ) {
    toast(
      operation.status === 'complete'
        ? `Clone to ${operation.target} completed`
        : operation.error || `Clone to ${operation.target} failed`,
      operation.status === 'error',
    );
  }
  backupLastCompletion = operationKey;

  const operationRunning = operation.status === 'running';
  $('backup-hotswaps').innerHTML = (state.hotswaps || [])
    .map((card) => {
      const unavailable = !card.attached || card.mounted,
        label = esc(card.label),
        status = !card.attached
          ? 'Not attached'
          : card.mounted
            ? 'Mounted — clone blocked'
            : `${formatBytes(card.size_bytes)} card attached`,
        generation = Number.isFinite(card.last_clone_at)
          ? `${eventTime(card.last_clone_at)} · ${backupAge(card.last_clone_at)}`
          : 'No successful clone recorded',
        badge = card.stale ? 'STALE' : card.due ? 'DUE' : 'CURRENT';
      return `<article class="backup-hotswap ${card.stale ? 'stale' : card.due ? 'due' : 'current'}">
        <div class="backup-hotswap-head"><span><strong>${label}</strong><small>${esc(status)}</small></span><span class="backup-generation-state">${badge}</span></div>
        <dl><div><dt>Contains vanpi as of</dt><dd>${esc(generation)}</dd></div><div><dt>Schedule</dt><dd>Every ${card.interval_days} days</dd></div></dl>
        <button data-backup-clone="${label}" ${unavailable || operationRunning || backupBusy ? 'disabled' : ''}>Clone current vanpi to this card</button>
      </article>`;
    })
    .join('');

  if (tm.running) {
    const copied = Number.isFinite(tm.bytes_copied) ? formatBytes(tm.bytes_copied) : null,
      total = Number.isFinite(tm.total_bytes) ? formatBytes(tm.total_bytes) : null;
    $('backup-tm-progress').textContent = `Backup running${Number.isFinite(tm.progress_percent) ? ` · ${tm.progress_percent}%` : ''}${copied && total ? ` · ${copied} of ${total}` : ''}`;
  } else {
    $('backup-tm-progress').textContent = tm.error || `${(tm.snapshots || []).length} recent completed snapshots found`;
  }
  $('backup-tm-updated').textContent = Number.isFinite(tm.updated_at)
    ? `Updated ${backupAge(tm.updated_at)}`
    : '—';
  $('backup-history').innerHTML = (tm.snapshots || []).length
    ? tm.snapshots
        .map(
          (timestamp, index) => `<div class="backup-history-row"><span>${index === 0 ? 'Latest' : `Previous ${index}`}</span><time datetime="${new Date(timestamp * 1000).toISOString()}">${esc(eventTime(timestamp))}</time></div>`,
        )
        .join('')
    : `<div class="speaker-loading">${esc(tm.error || 'No completed Time Machine snapshots found')}</div>`;
  $('backup-status').textContent = `Updated ${backupAge(state.checked_at)}`;
  $('backup-panel').setAttribute('aria-busy', 'false');
}
function renderBackupsUnavailable(message) {
  backupState = null;
  const tile = $('backups');
  tile.classList.remove('good', 'warning', 'running');
  tile.classList.add('unknown');
  $('backup-pill').textContent = 'NO DATA';
  $('backup-summary').textContent = message;
  $('backup-pi').textContent = 'Unavailable';
  $('backup-mac').textContent = 'Unavailable';
  $('backup-status').textContent = 'Unavailable';
  $('backup-panel').setAttribute('aria-busy', 'false');
}
async function refreshBackups(showErrors = false) {
  try {
    const response = await json('/api/backups');
    renderBackups(response);
    return response;
  } catch (error) {
    renderBackupsUnavailable(error.message);
    if (showErrors) toast(error.message, true);
    throw error;
  }
}
async function startBackupClone(button) {
  if (backupBusy || backupState?.operation?.status === 'running') return;
  const target = button.dataset.backupClone;
  if (
    !window.confirm(
      `Clone the current vanpi system to ${target}? Existing files on that hotspare will be synchronized or overwritten. Keep the card attached until completion.`,
    )
  )
    return;
  backupBusy = true;
  button.disabled = true;
  try {
    const response = await post('backups/clone', { target });
    renderBackups(response);
    toast(response.message);
  } catch (error) {
    toast(error.message, true);
    await refreshBackups(false).catch(() => {});
  } finally {
    backupBusy = false;
  }
}
function renderPriceChecks(response) {
  priceChecks = response;
  const items = response.items || [],
    summary = response.summary || {},
    tile = $('price-checks'),
    latest = items.reduce(
      (value, item) => Math.max(value, Number(item.last_checked_at) || 0),
      0,
    );
  if (priceEditingId !== null && !items.some((item) => item.id === priceEditingId))
    resetPriceForm();
  tile.classList.toggle('has-deal', Number(summary.below_threshold) > 0);
  tile.classList.toggle('has-error', Number(summary.errors) > 0);
  $('price-pill').textContent = String(items.length);
  $('price-summary').textContent = items.length
    ? `${summary.below_threshold || 0} below target · ${summary.errors || 0} errors`
    : 'No products watched';
  $('price-last-check').textContent = latest ? age(latest) : 'never';
  $('price-operation').textContent = priceBusy
    ? 'Working…'
    : items.length
      ? `${items.length} watched`
      : 'Empty';
  $('price-list').innerHTML =
    items
      .map((item) => {
        const stateClass = item.last_status === 'error' ? 'error' : item.below_threshold ? 'below' : '',
          price = item.last_price ? `$${esc(item.last_price)}` : '—',
          threshold = `$${esc(item.threshold)}`,
          meta = item.last_status === 'error'
            ? `Error ${age(item.last_checked_at)} · ${esc(item.last_error || 'Unknown error')}`
            : item.last_checked_at
              ? `Checked ${age(item.last_checked_at)}${item.below_threshold ? ' · below target' : ''}`
              : 'Not checked yet';
        return `<article class="price-row ${stateClass}">
          <div class="price-row-title"><span>${esc(item.display_title)}</span><small>${esc(item.parser)} · ${esc(item.url)}</small></div>
          <div class="price-value">${price}<small>alert below ${threshold}</small></div>
          <div class="price-row-meta">${meta}</div>
          <div class="price-row-controls"><button data-action data-price-check="${item.id}" ${priceBusy ? 'disabled' : ''}>Check</button><button data-action data-price-edit="${item.id}" ${priceBusy ? 'disabled' : ''}>Edit</button><button class="price-remove" data-action data-price-remove="${item.id}" data-price-title="${esc(item.display_title)}" ${priceBusy ? 'disabled' : ''}>Remove</button></div>
        </article>`;
      })
      .join('') || '<div class="speaker-loading">No price checks yet</div>';
  $('price-check-all').disabled = priceBusy || items.length === 0;
  $('price-check-all').classList.toggle('running', priceBusy);
  $('price-add-form').querySelectorAll('input, select, button').forEach((element) => {
    element.disabled = priceBusy;
  });
}
async function refreshPriceChecks() {
  try {
    const response = await json('/api/price-checks');
    renderPriceChecks(response);
    return response;
  } catch (error) {
    $('price-summary').textContent = 'Price data unavailable';
    $('price-operation').textContent = 'Unavailable';
    $('price-list').innerHTML = `<div class="speaker-loading">${esc(error.message)}</div>`;
    throw error;
  }
}
async function priceAction(work) {
  if (priceBusy) return;
  priceBusy = true;
  if (priceChecks) renderPriceChecks(priceChecks);
  try {
    const result = await work();
    if (result?.message) toast(result.message);
    renderPriceChecks(result);
    return result;
  } catch (error) {
    toast(error.message, true);
    await refreshPriceChecks().catch(() => {});
  } finally {
    priceBusy = false;
    if (priceChecks) renderPriceChecks(priceChecks);
  }
}
async function checkPrices(target) {
  return priceAction(() => post('price-checks/check', { target: String(target) }));
}
async function addPriceCheck() {
  const fields = {
    parser: $('price-parser').value,
    threshold: $('price-threshold').value,
    url: $('price-url').value.trim(),
    title: $('price-title').value.trim(),
  };
  if (priceEditingId !== null) fields.id = String(priceEditingId);
  const endpoint = priceEditingId === null ? 'price-checks/add' : 'price-checks/edit';
  const result = await priceAction(() => post(endpoint, fields));
  if (result) resetPriceForm();
}
function resetPriceForm() {
  priceEditingId = null;
  $('price-add-form').reset();
  $('price-form-title').textContent = 'Add an item';
  $('price-submit').textContent = 'Add price check';
  $('price-edit-cancel').hidden = true;
}
function editPriceCheck(itemId) {
  const item = priceChecks?.items?.find((candidate) => candidate.id === Number(itemId));
  if (!item || priceBusy) return;
  priceEditingId = item.id;
  $('price-parser').value = item.parser;
  $('price-threshold').value = item.threshold;
  $('price-url').value = item.url;
  $('price-title').value = item.title || '';
  $('price-form-title').textContent = `Edit ${item.display_title}`;
  $('price-submit').textContent = 'Save changes';
  $('price-edit-cancel').hidden = false;
  $('price-add-form').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
function monitorMetric(metric, suffix = '', digits = 1) {
  if (metric?.value === null || metric?.value === undefined) return '—';
  const value = Number(metric.value);
  return Number.isFinite(value) ? `${value.toFixed(digits)}${suffix}` : '—';
}
function monitorProcess(process, cpu = false) {
  if (!process) return '';
  const usage = cpu && Number.isFinite(process.cpu_percent)
    ? ` · ${Number(process.cpu_percent).toFixed(1)}% CPU`
    : Number.isFinite(process.rss_bytes)
      ? ` · ${formatBytes(process.rss_bytes)} RSS`
      : '';
  return `${esc(process.name || process.command || `PID ${process.pid}`)}${usage}`;
}
function monitorProcessList(items, cpu = false) {
  if (!items?.length) return '<div class="monitor-io-empty">No process samples yet</div>';
  return `<ol>${items
    .map((process) => `<li><span>${monitorProcess(process, cpu)}</span><small>PID ${esc(process.pid ?? '—')}</small></li>`)
    .join('')}</ol>`;
}
function monitorEventState(event) {
  const state = event.state;
  if (!state) return '';
  if (state.state_available === false)
    return '<div class="monitor-event-state unavailable">Historical journal import; live resource and USB state was not captured at this time.</div>';
  const facts = [],
    cpu = Number.isFinite(state.cpu_percent) ? Number(state.cpu_percent) : null,
    memory = Number.isFinite(state.memory?.used_percent)
      ? Number(state.memory.used_percent)
      : null,
    temperature = Number.isFinite(state.temperature_c) ? Number(state.temperature_c) : null,
    topCpu = state.top_cpu?.[0],
    topMemory = state.top_memory?.[0],
    throttle = state.throttle || {},
    networkIo = state.network_io || {},
    diskIo = state.disk_io || {},
    usb = state.usb_devices || [],
    mounts = state.mounts || [];
  if (Number.isFinite(cpu)) facts.push(`CPU ${cpu.toFixed(1)}%`);
  if (Number.isFinite(memory)) facts.push(`memory ${memory.toFixed(1)}%`);
  if (Number.isFinite(temperature)) facts.push(`${temperature.toFixed(1)} °C`);
  if (Number.isFinite(state.load?.['1m'])) facts.push(`load ${Number(state.load['1m']).toFixed(2)}`);
  if (Number.isFinite(networkIo.rx_bytes_per_second))
    facts.push(`net ↓ ${formatRate(networkIo.rx_bytes_per_second)}`);
  if (Number.isFinite(networkIo.tx_bytes_per_second))
    facts.push(`net ↑ ${formatRate(networkIo.tx_bytes_per_second)}`);
  if (Number.isFinite(diskIo.read_bytes_per_second))
    facts.push(`disk read ${formatRate(diskIo.read_bytes_per_second)}`);
  if (Number.isFinite(diskIo.write_bytes_per_second))
    facts.push(`disk write ${formatRate(diskIo.write_bytes_per_second)}`);
  if (throttle.hex) facts.push(`firmware ${esc(throttle.hex)}`);
  const devices = usb
    .map((device) => device.product || device.manufacturer || device.id)
    .filter(Boolean)
    .map(esc);
  return `<details class="monitor-event-state"><summary>State at event</summary>
    <div class="monitor-state-facts">${facts.map((fact) => `<span>${fact}</span>`).join('')}</div>
    ${topCpu ? `<p><strong>Top CPU:</strong> ${monitorProcess(topCpu, true)}</p>` : ''}
    ${topMemory ? `<p><strong>Top memory:</strong> ${monitorProcess(topMemory)}</p>` : ''}
    <p><strong>USB:</strong> ${devices.length ? devices.join(', ') : 'no enumerated devices'} (${usb.length})</p>
    <p><strong>Local mounts:</strong> ${mounts.length ? mounts.map((mount) => `${esc(mount.mountpoint)}${mount.read_only ? ' (read-only)' : ''}`).join(', ') : 'none captured'}</p>
  </details>`;
}
function renderSystemMonitor(response) {
  systemMonitor = response;
  const diagnosis = response.diagnosis || {},
    evidence = diagnosis.evidence || {},
    peaks = response.peaks || {},
    current = response.status?.current || {},
    tile = $('system-monitor'),
    level = response.status?.stale ? 'unknown' : diagnosis.level || 'unknown',
    episodes = Number(evidence.undervoltage_episodes) || 0,
    activeFirmware = current.throttle?.current?.length > 0,
    currentThermals = current.thermal_sensors || [],
    primaryThermal = currentThermals.find((sensor) => String(sensor.type).toLowerCase().includes('cpu'))
      || currentThermals[0],
    throttling = response.throttling || {};
  tile.classList.remove('unknown', 'good', 'warning', 'critical');
  tile.classList.add(level);
  $('system-monitor-pill').textContent =
    level === 'critical' && activeFirmware
      ? 'ACTIVE'
      : episodes
        ? `${episodes} POWER`
        : level.toUpperCase();
  $('system-monitor-summary').textContent = response.status?.stale
    ? 'Monitor data is stale'
    : diagnosis.headline || 'No diagnosis available';
  $('system-monitor-power').textContent = episodes
    ? `${episodes} drop${episodes === 1 ? '' : 's'} · ${Number(evidence.undervoltage_seconds || 0).toFixed(1)}s`
    : current.throttle?.occurred?.includes('under_voltage')
      ? 'Sticky history set'
      : `No events in range (${monitorRangeLabel()})`;
  $('system-monitor-cpu').textContent = monitorMetric(peaks.cpu_percent, '%');
  $('system-monitor-temperature').textContent = Number.isFinite(primaryThermal?.temperature_c)
    ? `${Number(primaryThermal.temperature_c).toFixed(1)} °C`
    : monitorMetric(peaks.temperature_c, ' °C');
  $('system-monitor-throttle').textContent = throttling.active?.length
    ? `ACTIVE · ${throttling.active.map((flag) => flag.replaceAll('_', ' ')).join(', ')}`
    : throttling.occurred_since_boot?.length
      ? 'Clear now · seen this boot'
      : throttling.available
        ? 'Clear'
        : 'No data';
  $('system-monitor-memory').textContent = monitorMetric(peaks.memory_percent, '%');
  $('system-monitor-network').textContent =
    `↓ ${formatRate(current.network_io?.rx_bytes_per_second)} · ↑ ${formatRate(current.network_io?.tx_bytes_per_second)}`;
  $('system-monitor-disk').textContent =
    `R ${formatRate(current.disk_io?.read_bytes_per_second)} · W ${formatRate(current.disk_io?.write_bytes_per_second)}`;

  $('system-monitor-panel').setAttribute('aria-busy', 'false');
  $('system-monitor-status').textContent = response.status?.stale
    ? `Stale · ${age(current.timestamp)}`
    : `${monitorRangeLabel()} · updated ${age(current.timestamp)}`;
  const badge = $('monitor-level');
  badge.className = `monitor-level ${level}`;
  badge.textContent = level.toUpperCase();
  $('monitor-diagnosis-title').textContent = diagnosis.headline || 'No diagnosis available';
  $('monitor-findings').innerHTML = (diagnosis.findings || [])
    .map((finding) => `<li>${esc(finding)}</li>`)
    .join('');

  const thermalFacts = currentThermals.length
    ? currentThermals.map((sensor) => [
        thermalSensorLabel(sensor),
        Number.isFinite(sensor.temperature_c)
          ? `${Number(sensor.temperature_c).toFixed(1)} °C`
          : '—',
      ])
    : [['CPU / SoC temperature', Number.isFinite(current.temperature_c) ? `${Number(current.temperature_c).toFixed(1)} °C` : '—']];
  const currentFacts = [
    ['CPU now', Number.isFinite(current.cpu_percent) ? `${Number(current.cpu_percent).toFixed(1)}%` : '—'],
    ['Memory now', Number.isFinite(current.memory?.used_percent) ? `${Number(current.memory.used_percent).toFixed(1)}%` : '—'],
    ...thermalFacts,
    ['Arm clock', Number.isFinite(current.arm_mhz) ? `${Number(current.arm_mhz).toFixed(0)} MHz` : '—'],
    ['Firmware', current.throttle?.hex || '—'],
    ['Active flags', current.throttle?.current?.length ? current.throttle.current.join(', ') : 'none'],
    ['Network RX', formatRate(current.network_io?.rx_bytes_per_second)],
    ['Network TX', formatRate(current.network_io?.tx_bytes_per_second)],
    ['Disk read', formatRate(current.disk_io?.read_bytes_per_second)],
    ['Disk write', formatRate(current.disk_io?.write_bytes_per_second)],
    ['Busiest disk', Number.isFinite(current.disk_io?.busy_percent) ? `${Number(current.disk_io.busy_percent).toFixed(1)}%` : '—'],
  ];
  $('monitor-current').innerHTML = currentFacts
    .map(([label, value]) => `<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`)
    .join('');

  const throttleFlags = throttling.flags || [],
    frequencyPolicies = current.cpu_frequency_policies || [];
  $('monitor-throttling').innerHTML = `<div class="monitor-throttle-summary">
    <div><span>Firmware word</span><strong>${esc(throttling.hex || 'No data')}</strong></div>
    <div><span>Active now</span><strong>${throttling.active?.length ? esc(throttling.active.map((flag) => flag.replaceAll('_', ' ')).join(', ')) : throttling.available ? 'None' : '—'}</strong></div>
    <div><span>Seen since boot</span><strong>${throttling.occurred_since_boot?.length ? esc(throttling.occurred_since_boot.map((flag) => flag.replaceAll('_', ' ')).join(', ')) : throttling.available ? 'None' : '—'}</strong></div>
  </div><div class="monitor-throttle-flags">
    ${throttleFlags.map((flag) => {
      const state = flag.active ? 'active' : flag.occurred_since_boot ? 'occurred' : 'clear';
      return `<div class="monitor-throttle-flag ${state}"><span><strong>${esc(flag.label)}</strong><small>${flag.active_transitions} start${flag.active_transitions === 1 ? '' : 's'} · ${flag.cleared_transitions} clear${flag.cleared_transitions === 1 ? '' : 's'} in ${monitorRangeLabel()}</small></span><b>${flag.active ? 'ACTIVE' : flag.occurred_since_boot ? 'SEEN THIS BOOT' : 'CLEAR'}</b></div>`;
    }).join('') || '<div class="monitor-io-empty">Firmware throttling data unavailable</div>'}
  </div>${frequencyPolicies.length ? `<div class="monitor-frequency-policies">${frequencyPolicies.map((policy) => `<div><span>${esc(policy.policy)} · cores ${esc(cpuListLabel(policy.cpu_ids) || '—')}</span><strong>${Number.isFinite(policy.current_mhz) ? `${Number(policy.current_mhz).toFixed(0)} MHz` : '—'} / ${Number.isFinite(policy.maximum_mhz) ? `${Number(policy.maximum_mhz).toFixed(0)} MHz max` : 'max —'}</strong><small>${esc(policy.governor || 'governor unknown')}</small></div>`).join('')}</div>` : ''}`;

  const interfaces = current.network_io?.interfaces || [],
    devices = current.disk_io?.devices || [];
  $('monitor-io-details').innerHTML = `<div class="monitor-io-group"><h4>Network</h4>
    ${interfaces.map((item) => `<div class="monitor-io-row"><span><strong>${esc(item.name)}</strong><small>${item.physical ? 'physical' : 'virtual'}</small></span><span>↓ ${formatRate(item.rx_bytes_per_second)}<br>↑ ${formatRate(item.tx_bytes_per_second)}</span></div>`).join('') || '<div class="monitor-io-empty">No interfaces sampled</div>'}
  </div><div class="monitor-io-group"><h4>Disks</h4>
    ${devices.map((item) => `<div class="monitor-io-row"><span><strong>${esc(item.labels?.length ? item.labels.join(', ') : item.name)}</strong><small>${esc(item.name)} · ${Number.isFinite(item.busy_percent) ? `${Number(item.busy_percent).toFixed(1)}% busy` : 'busy —'}</small></span><span>R ${formatRate(item.read_bytes_per_second)}<br>W ${formatRate(item.write_bytes_per_second)}</span></div>`).join('') || '<div class="monitor-io-empty">No disks sampled</div>'}
  </div>`;

  const thermalPeakDefinitions = (peaks.thermal_sensors || []).map((sensor) => [
    thermalSensorLabel(sensor),
    sensor,
    'temperature',
    false,
  ]);
  if (!thermalPeakDefinitions.length) {
    thermalPeakDefinitions.push(['CPU / SoC temperature', peaks.temperature_c, 'temperature', false]);
  }
  const peakDefinitions = [
    ['CPU', peaks.cpu_percent, 'percent', true],
    ['Memory', peaks.memory_percent, 'percent', false],
    ['Network receive', peaks.network_rx_bytes_per_second, 'rate', false],
    ['Network transmit', peaks.network_tx_bytes_per_second, 'rate', false],
    ['Disk read', peaks.disk_read_bytes_per_second, 'rate', false],
    ['Disk write', peaks.disk_write_bytes_per_second, 'rate', false],
    ['Disk busy', peaks.disk_busy_percent, 'percent', false],
    ['Load · 1m', peaks.load1, 'number', false],
    ...thermalPeakDefinitions,
    ['Swap', peaks.swap_percent, 'percent', false],
    ['Root used', peaks.root_used_percent, 'percent', false],
    ['Minimum Arm clock', peaks.minimum_arm_mhz, 'clock', false],
  ];
  $('monitor-peaks').innerHTML = peakDefinitions
    .map(([label, metric, format, cpu]) => {
      const process = monitorProcess(metric?.top_process, cpu),
        source = metric?.top_interface?.name || (metric?.top_device ? (metric.top_device.labels?.join(', ') || metric.top_device.name) : ''),
        value = format === 'rate'
          ? formatRate(metric?.value)
          : monitorMetric(metric, format === 'temperature' ? ' °C' : format === 'clock' ? ' MHz' : format === 'percent' ? '%' : '');
      return `<div class="monitor-peak"><span>${esc(label)}</span><strong>${value}</strong><small>${metric?.at ? eventTime(metric.at) : 'No samples'}${process ? `<br>${process}` : source ? `<br>${esc(source)}` : ''}</small></div>`;
    })
    .join('');

  const processes = response.processes || {},
    offenders = processes.repeat_offenders || [];
  $('monitor-process-range').textContent = `${monitorRangeLabel()} · ${processes.rollup_count || 0} minute rollups`;
  $('monitor-process-current').innerHTML = `<div><h4>CPU now</h4>${monitorProcessList(processes.current_cpu, true)}</div><div><h4>Memory now</h4>${monitorProcessList(processes.current_memory)}</div>`;
  $('monitor-offenders').innerHTML = offenders
    .map((process) => `<article><div><strong>${esc(process.name)}</strong><small>last peak ${esc(eventTime(process.last_seen_at))}${process.pid_count > 1 ? ` · ${process.pid_count} PIDs` : ''}</small></div><span><b>${process.peak_count}</b> peak lead${process.peak_count === 1 ? '' : 's'}<small>${process.cpu_peak_count} CPU · ${process.memory_peak_count} memory${Number.isFinite(process.max_cpu_percent) ? ` · ${Number(process.max_cpu_percent).toFixed(1)}% max` : ''}${Number.isFinite(process.max_rss_bytes) ? ` · ${formatBytes(process.max_rss_bytes)} RSS` : ''}</small></span></article>`)
    .join('') || '<div class="speaker-loading">No retained process peaks in this range</div>';

  const nextSteps = diagnosis.next_steps || [];
  $('monitor-next').hidden = nextSteps.length === 0;
  $('monitor-next-steps').innerHTML = nextSteps.map((step) => `<li>${esc(step)}</li>`).join('');
  const events = response.events || [];
  $('monitor-event-count').textContent = `${response.summary?.events || 0} total`;
  $('monitor-events').innerHTML =
    events
      .map(
        (event) => `<article class="monitor-event ${esc(event.severity)}">
          <div class="monitor-event-head"><span>${esc(event.category)}</span><time>${esc(eventTime(event.timestamp))}</time></div>
          <strong>${esc(event.summary)}</strong><p>${esc(event.message)}</p>${monitorEventState(event)}
        </article>`,
      )
      .join('') || `<div class="speaker-loading">No events in range (${monitorRangeLabel()})</div>`;
  document.querySelectorAll('[data-monitor-hours]').forEach((button) => {
    button.classList.toggle('active', Number(button.dataset.monitorHours) === systemMonitorHours);
    button.disabled = false;
  });
}
function renderSystemMonitorUnavailable(message) {
  const tile = $('system-monitor');
  tile.classList.remove('good', 'warning', 'critical');
  tile.classList.add('unknown');
  $('system-monitor-pill').textContent = 'NO DATA';
  $('system-monitor-summary').textContent = 'System monitor unavailable';
  $('system-monitor-power').textContent = '—';
  $('system-monitor-cpu').textContent = '—';
  $('system-monitor-temperature').textContent = '—';
  $('system-monitor-throttle').textContent = '—';
  $('system-monitor-memory').textContent = '—';
  $('system-monitor-network').textContent = '—';
  $('system-monitor-disk').textContent = '—';
  $('system-monitor-status').textContent = 'Unavailable';
  $('system-monitor-panel').setAttribute('aria-busy', 'false');
  $('monitor-events').innerHTML = `<div class="speaker-loading">${esc(message)}</div>`;
  $('monitor-io-details').innerHTML = `<div class="speaker-loading">${esc(message)}</div>`;
  $('monitor-throttling').innerHTML = `<div class="speaker-loading">${esc(message)}</div>`;
  $('monitor-process-current').innerHTML = `<div class="speaker-loading">${esc(message)}</div>`;
  $('monitor-offenders').innerHTML = `<div class="speaker-loading">${esc(message)}</div>`;
  document.querySelectorAll('[data-monitor-hours]').forEach((button) => {
    button.disabled = false;
  });
}
async function refreshSystemMonitor(showErrors = false) {
  try {
    const response = await json(`/api/system-monitor?hours=${systemMonitorHours}`);
    renderSystemMonitor(response);
    return response;
  } catch (error) {
    renderSystemMonitorUnavailable(error.message);
    if (showErrors) toast(error.message, true);
    throw error;
  }
}
function crashCountLabel(kind) {
  return String(kind || '')
    .replaceAll('_', ' ')
    .replace(/^./, (letter) => letter.toUpperCase());
}
function crashTimeline(items) {
  return (items || [])
    .map(
      (item) => `<div class="monitor-crash-log ${esc(item.severity || 'info')}">
        <time>${esc(eventTime(item.timestamp))}</time><strong>${esc(item.summary || item.source || 'Log')}</strong>
        <p>${esc(item.message || '')}</p>
      </div>`,
    )
    .join('') || '<div class="speaker-loading">No relevant retained log lines</div>';
}
function renderCrashHistory(payload) {
  const history = payload?.history || [];
  $('monitor-crash-history').innerHTML =
    history
      .map((item) => {
        const analysis = item.report?.analysis || {},
          previousBoot = item.previous_boot || analysis.previous_boot || {},
          findings = item.findings || analysis.findings || [],
          counts = item.counts || analysis.counts || {},
          countEntries = Object.entries(counts).filter(([, value]) => Number(value) > 0),
          timeline = analysis.timeline || [];
        return `<details class="monitor-crash-history-item">
          <summary><span class="monitor-level ${esc(item.level || 'unknown')}">${esc(String(item.level || 'unknown').toUpperCase())}</span><span><strong>${esc(item.headline || 'Saved crash analysis')}</strong><small>${esc(eventTime(previousBoot.ended_at || item.analyzed_at))} · analyzed ${esc(eventTime(item.analyzed_at))}</small></span></summary>
          <ul>${findings.map((finding) => `<li>${esc(finding)}</li>`).join('')}</ul>
          ${countEntries.length ? `<div class="monitor-crash-counts">${countEntries.map(([kind, value]) => `<span>${esc(crashCountLabel(kind))}: ${Number(value)}</span>`).join('')}</div>` : ''}
          ${timeline.length ? `<div class="monitor-crash-saved-timeline">${crashTimeline(timeline)}</div>` : ''}
        </details>`;
      })
      .join('') || '<div class="speaker-loading">No saved crash analyses yet</div>';
}
function renderCrashAnalysis(payload) {
  const analysis = payload.analysis || {},
    comparison = payload.comparison,
    level = analysis.level || 'unknown',
    result = $('monitor-crash-result');
  result.hidden = false;
  $('monitor-crash-level').className = `monitor-level ${level}`;
  $('monitor-crash-level').textContent = level.toUpperCase();
  $('monitor-crash-headline').textContent = analysis.headline || 'Crash analysis unavailable';
  $('monitor-crash-findings').innerHTML = (analysis.findings || [])
    .map((finding) => `<li>${esc(finding)}</li>`)
    .join('');
  if (comparison) {
    const deltas = Object.entries(comparison.count_deltas || {});
    $('monitor-crash-comparison').innerHTML = `<div class="monitor-crash-compare"><strong>Compared with previous saved crash</strong><p>${esc(comparison.previous_headline || 'Earlier analysis')} (${esc(comparison.previous_level || 'unknown')})</p>${deltas.length ? `<div class="monitor-crash-counts">${deltas.map(([kind, value]) => `<span>${esc(crashCountLabel(kind))}: ${Number(value) > 0 ? '+' : ''}${Number(value)}</span>`).join('')}</div>` : '<p>No tracked fault counts changed.</p>'}</div>`;
  } else {
    $('monitor-crash-comparison').innerHTML = '<p class="monitor-note">No earlier saved crash is available for comparison.</p>';
  }
  $('monitor-crash-timeline').innerHTML = crashTimeline(analysis.timeline);
}
async function refreshCrashHistory(showErrors = false) {
  try {
    const payload = await json('/api/system-monitor/crashes');
    renderCrashHistory(payload);
    return payload;
  } catch (error) {
    $('monitor-crash-history').innerHTML = `<div class="speaker-loading">${esc(error.message)}</div>`;
    if (showErrors) toast(error.message, true);
    throw error;
  }
}
async function analyzePreviousCrash() {
  if (crashAnalysisBusy) return;
  crashAnalysisBusy = true;
  const button = $('monitor-crash-analyze');
  button.disabled = true;
  button.textContent = 'Analyzing logs…';
  try {
    const payload = await post('system-monitor/crash-analysis');
    renderCrashAnalysis(payload);
    await refreshCrashHistory(false);
    toast(payload.saved ? 'Crash analysis saved' : 'Crash analysis complete; no prior boot was available to save');
  } catch (error) {
    toast(error.message, true);
  } finally {
    crashAnalysisBusy = false;
    button.disabled = false;
    button.textContent = 'Analyze previous crash';
  }
}
function setPolicyLoading(loading, label) {
  policyLoading = loading;
  $('storage-panel').setAttribute('aria-busy', String(loading));
  $('storage-status').textContent = label || (loading ? 'Checking…' : 'Current state');
  document
    .querySelectorAll('[data-policy-field]')
    .forEach((button) => (button.disabled = loading || !storagePolicy));
  if (loading && !storagePolicy)
    $('storage-summary').textContent = label || 'Checking current state…';
}
function renderPolicyRuntime(id, active, onLabel, offLabel, detail) {
  const item = $(id),
    state = $(`${id}-state`);
  item.classList.remove('on', 'off');
  item.classList.add(active ? 'on' : 'off');
  state.textContent = active ? onLabel : offLabel;
  $(`${id}-detail`).textContent = detail;
}
function policyRequestBlocked(policy, field) {
  if (policy[field] !== true) return false;
  if (field === 'torrents_enabled') return policy.disks_enabled !== true;
  if (field === 'allow_starlink_torrents') {
    return policy.disks_enabled !== true || policy.torrents_enabled !== true;
  }
  return false;
}
function renderStoragePolicy(policy) {
  storagePolicy = policy;
  const fields = ['disks_enabled', 'torrents_enabled', 'allow_starlink_torrents'],
    runtime = policy.runtime,
    mounted = runtime.disks_mounted === true,
    running = runtime.qbittorrent_running === true,
    labels = runtime.mounted_disk_labels,
    diskDetail = mounted
      ? labels.join(', ')
      : policy.disks_enabled
        ? 'No managed HDD mounts'
        : 'Disabled by requested policy',
    torrentDetail = running
      ? 'Exact process is active'
      : !policy.disks_enabled
        ? 'Stopped because disks are disabled'
        : !policy.torrents_enabled
          ? 'Disabled by requested policy'
          : 'Stopped by current conditions';
  for (const field of fields) {
    const button = document.querySelector(`[data-policy-field="${field}"]`),
      enabled = policy[field] === true,
      blocked = policyRequestBlocked(policy, field);
    button.classList.remove('on', 'off', 'blocked');
    button.classList.add(enabled ? 'on' : 'off');
    button.classList.toggle('blocked', blocked);
    button.setAttribute('aria-pressed', String(enabled));
    button.querySelector('.policy-state').textContent = blocked
      ? 'ON · BLOCKED'
      : enabled
        ? 'ON'
        : 'OFF';
  }
  renderPolicyRuntime('disk-runtime', mounted, 'MOUNTED', 'UNMOUNTED', diskDetail);
  renderPolicyRuntime('torrent-runtime', running, 'RUNNING', 'STOPPED', torrentDetail);
  $('storage-summary').textContent =
    `Disks ${mounted ? 'mounted' : 'unmounted'} · qBittorrent ${running ? 'running' : 'stopped'}`;
  setPolicyLoading(false, 'Current state');
}
function renderStorageUnavailable(message) {
  storagePolicy = null;
  $('storage-summary').textContent = 'Runtime state unavailable';
  $('storage-status').textContent = message || 'Unavailable';
  $('storage-panel').setAttribute('aria-busy', 'false');
  for (const id of ['disk-runtime', 'torrent-runtime']) {
    const item = $(id);
    item.classList.remove('on', 'off');
    $(`${id}-state`).textContent = 'NO DATA';
    $(`${id}-detail`).textContent = 'Status unavailable';
  }
  document.querySelectorAll('[data-policy-field]').forEach((button) => {
    button.disabled = true;
    button.classList.remove('on', 'off', 'blocked');
    button.setAttribute('aria-pressed', 'mixed');
    button.querySelector('.policy-state').textContent = 'NO DATA';
  });
}
async function refreshStoragePolicy(silent = false) {
  if (!silent) setPolicyLoading(true, 'Checking…');
  try {
    const response = await json('/api/storage-policy');
    renderStoragePolicy(response.policy);
    return response;
  } catch (error) {
    renderStorageUnavailable(error.message);
    throw error;
  }
}
async function changeStoragePolicy(field) {
  if (policyLoading || !storagePolicy) return;
  const value = String(!storagePolicy[field]);
  let result, operationError;
  setPolicyLoading(true, 'Applying…');
  try {
    result = await post('storage-policy', { field, value });
    renderStoragePolicy(result.policy);
  } catch (error) {
    operationError = error;
  }
  try {
    await refreshStoragePolicy();
  } catch (refreshError) {
    if (!operationError) operationError = refreshError;
  }
  if (operationError) throw operationError;
  return result;
}
function lightingDotClass(state) {
  return state === 'on' ? 'good' : state === 'off' ? 'bad' : '';
}
function renderLighting(next) {
  lighting = next;
  const master = $('lighting-master'),
    known = next.available_count > 0,
    unavailable = next.total_count - next.available_count;
  networkState(
    'lighting-master-dot',
    next.state === 'on' ? true : next.state === 'off' ? false : null,
  );
  master.disabled = !known;
  master.dataset.lightValue = String(next.state !== 'on');
  master.setAttribute(
    'aria-pressed',
    next.state === 'on' ? 'true' : next.state === 'off' ? 'false' : 'mixed',
  );
  $('lighting-master-state').textContent =
    next.state === 'on'
      ? 'ALL ON'
      : next.state === 'off'
        ? 'ALL OFF'
        : known
          ? 'MIXED'
          : 'NO DATA';
  const summary = [`${next.on_count} on`];
  if (unavailable) summary.push(`${unavailable} unavailable`);
  $('lighting-summary').textContent = known ? summary.join(' · ') : 'Light status unavailable';
  $('lighting-status').textContent = known ? 'Current state' : 'No available lights';
  $('lighting-panel').setAttribute('aria-busy', 'false');
  $('lighting-groups').innerHTML = next.groups
    .map((group) => {
      const groupKnown = group.lights.some((light) => light.available),
        groupEnabled = group.state === 'on',
        groupAction = groupEnabled ? 'Turn room off' : 'Turn room on';
      const rows = group.lights
        .map((light) => {
          const enabled = light.state === 'on',
            level = Number.isFinite(light.brightness) ? light.brightness : 100,
            stateLabel = enabled ? 'ON' : light.state === 'off' ? 'OFF' : 'NO DATA';
          return `<div class="lighting-row">
            <span class="lighting-bulb ${enabled ? 'on' : ''}" aria-hidden="true">●</span>
            <strong>${esc(light.label)}</strong>
            <button class="lighting-power ${lightingDotClass(light.state)}" data-action data-light-target="${esc(light.entity_id)}" data-light-value="${String(!enabled)}" ${light.available ? '' : 'disabled'} aria-pressed="${light.available ? String(enabled) : 'mixed'}">${stateLabel}</button>
            <input class="lighting-slider" data-action data-light-brightness="${esc(light.entity_id)}" type="range" min="1" max="100" value="${level}" ${light.available ? '' : 'disabled'} aria-label="${esc(light.label)} brightness">
            <span class="lighting-level">${light.available ? `${level}%` : '—'}</span>
          </div>`;
        })
        .join('');
      return `<section class="lighting-group">
        <div class="lighting-group-head"><h3><span class="network-dot ${lightingDotClass(group.state)}"></span>${esc(group.label)}</h3><button data-action data-light-target="group:${esc(group.id)}" data-light-value="${String(!groupEnabled)}" ${groupKnown ? '' : 'disabled'}>${groupAction}</button></div>
        ${rows}
      </section>`;
    })
    .join('');
}
function renderLightingUnavailable(message) {
  lighting = null;
  networkState('lighting-master-dot', null);
  $('lighting-master').disabled = true;
  $('lighting-master').setAttribute('aria-pressed', 'mixed');
  $('lighting-master-state').textContent = 'NO DATA';
  $('lighting-summary').textContent = /usage: tuya_light\.sh/.test(message)
    ? 'Lighting helper needs deployment'
    : 'Home Assistant status unavailable';
  $('lighting-status').textContent = 'Unavailable';
  $('lighting-panel').setAttribute('aria-busy', 'false');
  $('lighting-groups').innerHTML = `<div class="speaker-loading">${esc(message)}</div>`;
}
async function refreshLighting(showError = false) {
  try {
    const response = await json('/api/lights');
    renderLighting(response.lighting);
    return response;
  } catch (error) {
    renderLightingUnavailable(error.message);
    if (showError) toast(error.message, true);
    throw error;
  }
}
async function changeLightPower(target, value) {
  let result;
  try {
    result = await post('lights/power', { target, value });
    renderLighting(result.lighting);
    return result;
  } catch (error) {
    await refreshLighting(false).catch(() => {});
    throw error;
  }
}
async function changeLightBrightness(entity, brightness) {
  let result;
  try {
    result = await post('lights/brightness', { entity, brightness });
    renderLighting(result.lighting);
    return result;
  } catch (error) {
    await refreshLighting(false).catch(() => {});
    throw error;
  }
}
function ubntSecurity(value) {
  return value === 'wpa'
    ? 'WPA/WPA2'
    : value === 'none'
      ? 'Open'
      : value === 'enterprise'
        ? 'WPA Enterprise'
        : String(value || 'Unknown').toUpperCase();
}
function renderUbntWifi(response) {
  ubntWifi = response;
  const wifi = response.wifi || {},
    state = wifi.state || {},
    operation = response.operation || {},
    running = operation.status === 'running',
    error = operation.status === 'error',
    associated = state.associated_ssid || '',
    reachable = wifi.reachable === true,
    unavailable = wifi.reachable === false || (error && !reachable);
  $('ubnt-wifi-panel').setAttribute('aria-busy', String(running));
  $('ubnt-wifi-operation').textContent = running
    ? `${operation.kind === 'scan' ? 'Scanning' : operation.kind === 'connect' ? 'Connecting' : operation.kind === 'provision' ? 'Saving network' : 'Updating'}…`
    : error
      ? 'Failed'
      : wifi.checked_at
        ? `Updated ${age(wifi.checked_at)}`
        : 'No data';
  networkState(
    'ubnt-current-dot',
    reachable && associated ? true : unavailable || reachable ? false : null,
  );
  $('ubnt-current-ssid').textContent = associated || state.configured_ssid || 'Not associated';
  $('ubnt-current-detail').textContent = error
    ? operation.error || 'Status unavailable'
    : state.automatic_paused === true
      ? 'Automatic selection paused'
      : state.automatic_paused === false
        ? 'Automatic selection active'
        : 'Waiting for antenna status…';
  $('ubnt-resume').hidden = state.automatic_paused !== true;
  $('ubnt-resume').disabled = running;
  const scan = $('ubnt-scan');
  scan.disabled = running;
  scan.classList.toggle('running', running && operation.kind === 'scan');
  $('ubnt-scan-label').textContent =
    running && operation.kind === 'scan' ? 'Scanning…' : 'Scan nearby Wi-Fi';
  const networks = wifi.networks || [];
  $('ubnt-network-list').innerHTML =
    networks
      .map((network) => {
        const known = network.known === true,
          connected = network.connected === true,
          supported = network.supported === true,
          meta = [
            Number.isFinite(network.signal_dbm) ? `${network.signal_dbm} dBm` : null,
            Number.isFinite(network.quality_percent) ? `${network.quality_percent}%` : null,
            ubntSecurity(network.security),
            known ? 'Known' : null,
          ].filter(Boolean);
        let control;
        if (connected) control = '<button class="ubnt-network-action" disabled>Connected</button>';
        else if (known)
          control = `<span class="ubnt-profile-actions">${network.profiles.map((profile) => `<button class="ubnt-network-action" data-action data-ubnt-profile="${esc(profile)}" ${running ? 'disabled' : ''}>${network.profiles.length > 1 ? esc(profile) : 'Connect'}</button>`).join('')}</span>`;
        else if (!supported)
          control = '<button class="ubnt-network-action" disabled>Unsupported</button>';
        else if (network.security === 'none')
          control = `<button class="ubnt-network-action" data-action data-ubnt-open="1" data-ubnt-ssid="${esc(network.ssid)}" data-ubnt-security="none" data-ubnt-bssid="${esc(network.bssid)}" ${running ? 'disabled' : ''}>Add & connect</button>`;
        else
          control = `<button class="ubnt-network-action" data-action data-ubnt-new="1" data-ubnt-ssid="${esc(network.ssid)}" data-ubnt-security="wpa" data-ubnt-bssid="${esc(network.bssid)}" ${running ? 'disabled' : ''}>Add</button>`;
        return `<div class="ubnt-network-row ${known ? 'known' : ''} ${connected ? 'connected' : ''} ${supported ? '' : 'unsupported'}"><span><strong class="ubnt-network-name">${esc(network.ssid)}</strong><span class="ubnt-network-meta">${meta.map((item) => `<span class="${known ? 'ubnt-known' : ''}">${esc(item)}</span>`).join('')}</span></span>${control}</div>`;
      })
      .join('') ||
    `<div class="ubnt-empty">${running && operation.kind === 'scan' ? 'Scanning nearby networks…' : 'No scan results yet. Tap Scan nearby Wi-Fi.'}</div>`;
  const completion = `${operation.status}:${operation.completed_at || ''}`;
  if (operation.completed_at && completion !== ubntLastCompletion) {
    ubntLastCompletion = completion;
    if ($('ubnt-wifi-backdrop').classList.contains('open'))
      toast(operation.error || operation.message || 'UBNT Wi-Fi updated', Boolean(operation.error));
    if (
      operation.kind === 'connect' ||
      operation.kind === 'provision' ||
      operation.kind === 'resume'
    )
      refreshConnectivity();
  }
  clearTimeout(ubntPoll);
  if (running) ubntPoll = setTimeout(() => refreshUbntWifi(true), 1200);
  renderUbntTile();
}
async function refreshUbntWifi(showError = false) {
  try {
    const response = await json('/api/ubnt-wifi');
    renderUbntWifi(response);
    return response;
  } catch (error) {
    if (showError) toast(error.message, true);
    renderUbntTile();
  }
}
async function startUbntWifi(endpoint, params = {}) {
  if (ubntWifi?.operation?.status === 'running') return;
  clearUbntPassword();
  try {
    const response = await post(`ubnt-wifi/${endpoint}`, params);
    renderUbntWifi(response);
    ubntPoll = setTimeout(() => refreshUbntWifi(true), 500);
  } catch (error) {
    toast(error.message, true);
    await refreshUbntWifi(false);
  }
}
function showUbntPassword(button) {
  ubntNewNetwork = {
    ssid: button.dataset.ubntSsid,
    security: button.dataset.ubntSecurity,
    bssid: button.dataset.ubntBssid,
  };
  $('ubnt-password-title').textContent = `Join ${ubntNewNetwork.ssid}`;
  $('ubnt-password-detail').textContent =
    `${ubntSecurity(ubntNewNetwork.security)} · password is sent directly to the antenna and saved in its profile`;
  $('ubnt-password').value = '';
  $('ubnt-password-form').hidden = false;
  $('ubnt-password').focus();
}
function clearUbntPassword() {
  ubntNewNetwork = null;
  $('ubnt-password').value = '';
  $('ubnt-password-form').hidden = true;
}
function renderStarlink(status) {
  const state = status?.state || 'unknown',
    known = state === 'on' || state === 'off',
    tile = $('starlink');
  tile.classList.remove('on', 'off', 'unknown');
  tile.classList.add(known ? state : 'unknown');
  networkState('starlink-dot', state === 'on' ? true : state === 'off' ? false : null);
  tile.disabled = !tileEditing && (!known || Boolean(status?.changing));
  tile.setAttribute('aria-pressed', known ? String(state === 'on') : 'mixed');
  $('starlink-state').textContent = status?.changing
    ? 'WAIT'
    : state === 'on'
      ? 'ON'
      : state === 'off'
        ? 'OFF'
        : 'NO DATA';
  $('starlink-detail').textContent = status?.changing
    ? 'Changing power…'
    : state === 'on'
      ? 'Tuya switch is on'
      : state === 'off'
        ? 'Tuya switch is off'
        : 'Tuya status unavailable';
}
function updateStatus(data) {
  dashboard = data.cop_alert;
  const active = dashboard.active;
  const engine = dashboard.engine;
  const led = data.cop_led || {};
  $('dot').classList.remove('bad');
  $('dot').classList.add('on');
  $('connection').textContent = 'Connected · vanpi dashboard';
  renderStarlink(data.starlink);
  $('cop').classList.toggle('active', active);
  $('cop').setAttribute('aria-pressed', String(active));
  $('cop-pill').textContent = active ? 'ACTIVE' : 'OFF';
  $('cop-detail').textContent = active
    ? 'Dashcam wake and 5-minute bacon alerts are active'
    : 'Tap to keep the dashcam awake';
  $('engine').textContent = engine.running
    ? `RUNNING · ${Math.round(engine.rpm)} RPM`
    : engine.rpm === null
      ? 'No fresh data'
      : `Stopped · ${Math.round(engine.rpm)} RPM`;
  $('cop-led').textContent = led.message || 'No data';
  $('cop-led').title = led.last_error || '';
  $('wake').textContent =
    dashboard.last_wake_ok === null
      ? 'Not attempted'
      : dashboard.last_wake_ok
        ? `OK · ${age(dashboard.last_wake)}`
        : 'DEGRADED';
  if (active && dashboard.last_error)
    $('connection').textContent = `Active with warning · ${dashboard.last_error}`;
}
async function refresh() {
  try {
    updateStatus(await json('/api/status'));
  } catch (error) {
    $('dot').classList.remove('on');
    $('dot').classList.add('bad');
    $('connection').textContent = error.message;
  }
}
function muteIcon(muted) {
  return muted ? '🔇' : '🔊';
}
function clockSeconds(value) {
  const parts = String(value || '')
    .split(':')
    .map(Number);
  return parts.length && parts.every(Number.isFinite)
    ? parts.reduce((total, part) => total * 60 + part, 0)
    : 0;
}
function clockLabel(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(seconds / 60),
    secs = seconds % 60;
  return `${minutes}:${String(secs).padStart(2, '0')}`;
}
function updateSonosProgress() {
  const bar = $('sonos-progress'),
    duration = sonosTimeline.duration;
  if (!(duration > 0)) {
    bar.hidden = true;
    return;
  }
  const elapsed = sonosTimeline.playing ? (performance.now() - sonosTimeline.updatedAt) / 1000 : 0,
    position = Math.min(duration, sonosTimeline.position + elapsed),
    percent = (100 * position) / duration;
  bar.hidden = false;
  $('sonos-progress-fill').style.width = `${percent}%`;
  bar.setAttribute('aria-valuemin', '0');
  bar.setAttribute('aria-valuemax', String(duration));
  bar.setAttribute('aria-valuenow', String(Math.round(position)));
  bar.setAttribute('aria-valuetext', `${clockLabel(position)} of ${clockLabel(duration)}`);
  bar.title = `${clockLabel(position)} / ${clockLabel(duration)}`;
}
function renderSpeakers(next) {
  speakers = next;
  const grouped = next.speakers.filter((s) => s.grouped),
    now = next.now_playing || {},
    group = next.group || {};
  $('speaker-summary').textContent =
    `${next.coordinator} · ${grouped.length}/${next.speakers.length}`;
  $('sonos-track').textContent = now.title || 'Nothing playing';
  $('sonos-artist').textContent = now.artist || next.coordinator;
  const playing = now.transport_state === 'PLAYING';
  $('sonos-play').textContent = playing ? 'Ⅱ' : '▶';
  $('sonos-play').setAttribute('aria-label', playing ? 'Pause' : 'Play');
  sonosTimeline = {
    position: clockSeconds(now.position),
    duration: clockSeconds(now.duration),
    playing,
    updatedAt: performance.now(),
  };
  updateSonosProgress();
  const card = $('sonos-card');
  let art = null;
  try {
    art = now.album_art ? new URL(now.album_art, location.href) : null;
  } catch (_) {
    art = null;
  }
  if (art && art.origin === location.origin) {
    card.classList.add('has-art');
    card.style.backgroundImage = `linear-gradient(90deg,#111b22ed 0%,#111b22c7 58%,#111b226b 100%),url("${art.href}")`;
  } else {
    card.classList.remove('has-art');
    card.style.backgroundImage = '';
  }
  const groupVolume = Number.isFinite(group.volume) ? group.volume : 0;
  $('group-volume').value = groupVolume;
  $('group-volume').disabled = !Number.isFinite(group.volume);
  $('group-level').textContent = Number.isFinite(group.volume) ? group.volume : '—';
  const groupMuteKnown = typeof group.muted === 'boolean';
  $('group-mute').disabled = !groupMuteKnown;
  $('group-mute').textContent = muteIcon(group.muted);
  $('group-mute').classList.toggle('muted', group.muted === true);
  $('group-mute').setAttribute('aria-pressed', String(group.muted === true));
  $('group-mute').setAttribute('aria-label', group.muted ? 'Unmute group' : 'Mute group');
  $('speaker-list').innerHTML =
    next.speakers
      .map((s) => {
        const detail = s.coordinator
            ? 'Active coordinator'
            : s.grouped
              ? `Grouped with ${next.coordinator}`
              : `Group: ${s.group_coordinator}`,
          volume = Number.isFinite(s.volume) ? s.volume : 0,
          muteKnown = typeof s.muted === 'boolean';
        return `<div class="speaker-row"><input class="speaker-check" data-action data-group-speaker="${esc(s.name)}" type="checkbox" ${s.grouped ? 'checked' : ''} ${s.coordinator ? 'disabled' : ''} aria-label="Group ${esc(s.name)}">
      <button class="audio-mute ${s.muted ? 'muted' : ''}" data-action data-speaker-mute="${esc(s.name)}" ${muteKnown ? '' : 'disabled'} aria-pressed="${String(s.muted === true)}" aria-label="${s.muted ? 'Unmute' : 'Mute'} ${esc(s.name)}">${muteIcon(s.muted)}</button>
      <button class="speaker-name" data-action data-select-speaker="${esc(s.name)}">${esc(s.name)}<small>${esc(detail)}</small></button><span class="speaker-level">${Number.isFinite(s.volume) ? s.volume : '—'}</span>
      <input class="speaker-volume" data-action data-speaker-volume="${esc(s.name)}" type="range" min="0" max="100" value="${volume}" ${Number.isFinite(s.volume) ? '' : 'disabled'} aria-label="${esc(s.name)} volume"></div>`;
      })
      .join('') || '<div class="speaker-loading">No Sonos speakers found</div>';
}
async function loadSpeakers() {
  const next = await json('/api/speakers');
  renderSpeakers(next);
  return next;
}
async function refreshSonos() {
  try {
    return await loadSpeakers();
  } catch (error) {
    $('speaker-summary').textContent = 'Unavailable';
    $('sonos-track').textContent = 'Sonos unavailable';
    $('sonos-artist').textContent = error.message;
    sonosTimeline = { position: 0, duration: 0, playing: false, updatedAt: 0 };
    updateSonosProgress();
    $('sonos-card').classList.remove('has-art');
    $('sonos-card').style.backgroundImage = '';
  }
}
async function openSpeakers() {
  $('speaker-backdrop').classList.add('open');
  document.body.classList.add('sheet-open');
  $('speakers').setAttribute('aria-expanded', 'true');
  try {
    await loadSpeakers();
  } catch (error) {
    $('speaker-list').innerHTML = `<div class="speaker-loading">${esc(error.message)}</div>`;
    toast(error.message, true);
  }
}
function closeSpeakers() {
  $('speaker-backdrop').classList.remove('open');
  document.body.classList.remove('sheet-open');
  $('speakers').setAttribute('aria-expanded', 'false');
  $('speakers').focus();
}
async function pollStorage() {
  clearTimeout(storagePoll);
  if (!$('storage-backdrop').classList.contains('open')) return;
  try {
    if (!busy) await refreshStoragePolicy(true);
  } catch (_) {
    /* rendered by refreshStoragePolicy */
  } finally {
    storagePoll = setTimeout(pollStorage, 2500);
  }
}
async function openStorage() {
  $('storage-backdrop').classList.add('open');
  document.body.classList.add('sheet-open');
  $('storage').setAttribute('aria-expanded', 'true');
  try {
    await refreshStoragePolicy();
  } catch (error) {
    toast(error.message, true);
  } finally {
    storagePoll = setTimeout(pollStorage, 2500);
  }
}
function closeStorage() {
  clearTimeout(storagePoll);
  $('storage-backdrop').classList.remove('open');
  document.body.classList.remove('sheet-open');
  $('storage').setAttribute('aria-expanded', 'false');
  $('storage').focus();
}
async function pollSystemMonitor() {
  clearTimeout(systemMonitorPoll);
  if (!$('system-monitor-backdrop').classList.contains('open')) return;
  try {
    await refreshSystemMonitor(false);
  } catch (_) {
    /* rendered by refreshSystemMonitor */
  } finally {
    systemMonitorPoll = setTimeout(pollSystemMonitor, 10000);
  }
}
async function openSystemMonitor() {
  $('system-monitor-backdrop').classList.add('open');
  document.body.classList.add('sheet-open');
  $('system-monitor').setAttribute('aria-expanded', 'true');
  $('system-monitor-panel').setAttribute('aria-busy', 'true');
  await Promise.allSettled([refreshSystemMonitor(true), refreshCrashHistory(false)]);
  systemMonitorPoll = setTimeout(pollSystemMonitor, 10000);
}
function closeSystemMonitor() {
  clearTimeout(systemMonitorPoll);
  $('system-monitor-backdrop').classList.remove('open');
  document.body.classList.remove('sheet-open');
  $('system-monitor').setAttribute('aria-expanded', 'false');
  $('system-monitor').focus();
}
async function pollUsbDevices() {
  clearTimeout(usbPoll);
  if (!$('usb-backdrop').classList.contains('open')) return;
  try {
    await refreshUsbDevices(false);
  } catch (_) {
    /* rendered by refreshUsbDevices */
  } finally {
    usbPoll = setTimeout(pollUsbDevices, 2000);
  }
}
async function openUsbDevices() {
  $('usb-backdrop').classList.add('open');
  document.body.classList.add('sheet-open');
  $('usb-devices').setAttribute('aria-expanded', 'true');
  $('usb-panel').setAttribute('aria-busy', 'true');
  try {
    await refreshUsbDevices(true);
  } finally {
    usbPoll = setTimeout(pollUsbDevices, 2000);
  }
}
function closeUsbDevices() {
  clearTimeout(usbPoll);
  $('usb-backdrop').classList.remove('open');
  document.body.classList.remove('sheet-open');
  $('usb-devices').setAttribute('aria-expanded', 'false');
  $('usb-devices').focus();
}
async function pollBackups() {
  clearTimeout(backupPoll);
  if (!$('backup-backdrop').classList.contains('open')) return;
  try {
    await refreshBackups(false);
  } catch (_) {
    /* rendered by refreshBackups */
  } finally {
    const delay = backupState?.operation?.status === 'running' ? 2500 : 10000;
    backupPoll = setTimeout(pollBackups, delay);
  }
}
async function openBackups() {
  $('backup-backdrop').classList.add('open');
  document.body.classList.add('sheet-open');
  $('backups').setAttribute('aria-expanded', 'true');
  $('backup-panel').setAttribute('aria-busy', 'true');
  try {
    await refreshBackups(true);
  } finally {
    backupPoll = setTimeout(pollBackups, 2500);
  }
}
function closeBackups() {
  clearTimeout(backupPoll);
  $('backup-backdrop').classList.remove('open');
  document.body.classList.remove('sheet-open');
  $('backups').setAttribute('aria-expanded', 'false');
  $('backups').focus();
}
async function openPriceChecks() {
  $('price-backdrop').classList.add('open');
  document.body.classList.add('sheet-open');
  $('price-checks').setAttribute('aria-expanded', 'true');
  $('price-panel').setAttribute('aria-busy', 'true');
  try {
    await refreshPriceChecks();
  } catch (error) {
    toast(error.message, true);
  } finally {
    $('price-panel').setAttribute('aria-busy', 'false');
  }
}
function closePriceChecks() {
  resetPriceForm();
  $('price-backdrop').classList.remove('open');
  document.body.classList.remove('sheet-open');
  $('price-checks').setAttribute('aria-expanded', 'false');
  $('price-checks').focus();
}
async function pollLighting() {
  clearTimeout(lightingPoll);
  if (!$('lighting-backdrop').classList.contains('open')) return;
  try {
    if (!busy) await refreshLighting(false);
  } catch (_) {
    /* rendered by refreshLighting */
  } finally {
    lightingPoll = setTimeout(pollLighting, 10000);
  }
}
async function openLighting() {
  $('lighting-backdrop').classList.add('open');
  document.body.classList.add('sheet-open');
  $('lighting').setAttribute('aria-expanded', 'true');
  $('lighting-panel').setAttribute('aria-busy', 'true');
  try {
    await refreshLighting(true);
  } finally {
    lightingPoll = setTimeout(pollLighting, 10000);
  }
}
function closeLighting() {
  clearTimeout(lightingPoll);
  $('lighting-backdrop').classList.remove('open');
  document.body.classList.remove('sheet-open');
  $('lighting').setAttribute('aria-expanded', 'false');
  $('lighting').focus();
}
async function openUbntWifi() {
  $('ubnt-wifi-backdrop').classList.add('open');
  document.body.classList.add('sheet-open');
  $('ubnt-wifi').setAttribute('aria-expanded', 'true');
  await refreshUbntWifi(true);
}
function closeUbntWifi() {
  $('ubnt-wifi-backdrop').classList.remove('open');
  document.body.classList.remove('sheet-open');
  $('ubnt-wifi').setAttribute('aria-expanded', 'false');
  clearUbntPassword();
  $('ubnt-wifi').focus();
}
const bookUrl = new URL(window.location.href);
setupTileEditing();
bookUrl.port = '8787';
bookUrl.pathname = '/';
bookUrl.search = '';
bookUrl.hash = '';
$('books').href = bookUrl.toString();
$('cop').addEventListener('click', () =>
  action(() => post('cop-alert', { active: dashboard?.active ? 'false' : 'true' })),
);
$('starlink').addEventListener('click', () => {
  renderStarlink({ state: 'unknown', changing: true });
  action(async () => {
    const result = await post('starlink');
    await refreshConnectivity();
    return result;
  });
});
$('speakers').addEventListener('click', openSpeakers);
$('speaker-close').addEventListener('click', closeSpeakers);
$('storage').addEventListener('click', openStorage);
$('storage-close').addEventListener('click', closeStorage);
$('system-monitor').addEventListener('click', openSystemMonitor);
$('system-monitor-close').addEventListener('click', closeSystemMonitor);
$('monitor-crash-analyze').addEventListener('click', analyzePreviousCrash);
$('usb-devices').addEventListener('click', openUsbDevices);
$('usb-close').addEventListener('click', closeUsbDevices);
$('backups').addEventListener('click', openBackups);
$('backup-close').addEventListener('click', closeBackups);
$('price-checks').addEventListener('click', openPriceChecks);
$('price-close').addEventListener('click', closePriceChecks);
$('price-check-all').addEventListener('click', () => checkPrices('all'));
$('price-edit-cancel').addEventListener('click', resetPriceForm);
$('price-add-form').addEventListener('submit', (event) => {
  event.preventDefault();
  addPriceCheck();
});
$('lighting').addEventListener('click', openLighting);
$('lighting-close').addEventListener('click', closeLighting);
$('lighting-master').dataset.lightTarget = 'all';
$('ubnt-wifi').addEventListener('click', openUbntWifi);
$('ubnt-wifi-close').addEventListener('click', closeUbntWifi);
$('ubnt-scan').addEventListener('click', () => startUbntWifi('scan'));
$('ubnt-resume').addEventListener('click', () => startUbntWifi('resume'));
$('ubnt-password-cancel').addEventListener('click', clearUbntPassword);
$('ubnt-password-form').addEventListener('submit', (event) => {
  event.preventDefault();
  if (!ubntNewNetwork) return;
  const selected = { ...ubntNewNetwork, password: $('ubnt-password').value };
  startUbntWifi('provision', selected);
});
$('speedtest-button').addEventListener('click', startSpeedtest);
$('speaker-backdrop').addEventListener('click', (event) => {
  if (event.target === $('speaker-backdrop')) closeSpeakers();
});
$('storage-backdrop').addEventListener('click', (event) => {
  if (event.target === $('storage-backdrop')) closeStorage();
});
$('system-monitor-backdrop').addEventListener('click', (event) => {
  if (event.target === $('system-monitor-backdrop')) closeSystemMonitor();
});
$('usb-backdrop').addEventListener('click', (event) => {
  if (event.target === $('usb-backdrop')) closeUsbDevices();
});
$('backup-backdrop').addEventListener('click', (event) => {
  if (event.target === $('backup-backdrop')) closeBackups();
});
$('price-backdrop').addEventListener('click', (event) => {
  if (event.target === $('price-backdrop')) closePriceChecks();
});
$('lighting-backdrop').addEventListener('click', (event) => {
  if (event.target === $('lighting-backdrop')) closeLighting();
});
$('ubnt-wifi-backdrop').addEventListener('click', (event) => {
  if (event.target === $('ubnt-wifi-backdrop')) closeUbntWifi();
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    if ($('speaker-backdrop').classList.contains('open')) closeSpeakers();
    if ($('storage-backdrop').classList.contains('open')) closeStorage();
    if ($('system-monitor-backdrop').classList.contains('open')) closeSystemMonitor();
    if ($('usb-backdrop').classList.contains('open')) closeUsbDevices();
    if ($('backup-backdrop').classList.contains('open')) closeBackups();
    if ($('price-backdrop').classList.contains('open')) closePriceChecks();
    if ($('lighting-backdrop').classList.contains('open')) closeLighting();
    if ($('ubnt-wifi-backdrop').classList.contains('open')) closeUbntWifi();
  }
});
document.addEventListener('input', (event) => {
  const slider = event.target.closest('[data-speaker-volume]');
  if (slider)
    slider.closest('.speaker-row').querySelector('.speaker-level').textContent = slider.value;
  const groupSlider = event.target.closest('[data-group-volume]');
  if (groupSlider) $('group-level').textContent = groupSlider.value;
  const lightSlider = event.target.closest('[data-light-brightness]');
  if (lightSlider)
    lightSlider.closest('.lighting-row').querySelector('.lighting-level').textContent =
      `${lightSlider.value}%`;
});
document.addEventListener('change', (event) => {
  const lightSlider = event.target.closest('[data-light-brightness]');
  if (lightSlider)
    action(() =>
      changeLightBrightness(lightSlider.dataset.lightBrightness, lightSlider.value),
    );
  const checkbox = event.target.closest('[data-group-speaker]');
  if (checkbox)
    action(async () => {
      try {
        return await post('speakers/group', {
          name: checkbox.dataset.groupSpeaker,
          grouped: checkbox.checked ? '1' : '0',
        });
      } finally {
        await loadSpeakers();
      }
    });
  const slider = event.target.closest('[data-speaker-volume]');
  if (slider)
    action(async () => {
      try {
        return await post('speakers/volume', {
          name: slider.dataset.speakerVolume,
          volume: slider.value,
        });
      } finally {
        await loadSpeakers();
      }
    });
  const groupSlider = event.target.closest('[data-group-volume]');
  if (groupSlider)
    action(async () => {
      try {
        return await post('speakers/group-volume', { volume: groupSlider.value });
      } finally {
        await loadSpeakers();
      }
    });
});
document.addEventListener('click', (event) => {
  const backupClone = event.target.closest('[data-backup-clone]');
  if (backupClone) startBackupClone(backupClone);
  const usbPortAction = event.target.closest('[data-usb-port-action]');
  if (usbPortAction) changeUsbPort(usbPortAction);
  const monitorRange = event.target.closest('[data-monitor-hours]');
  if (monitorRange) {
    const hours = Number(monitorRange.dataset.monitorHours);
    if (Number.isFinite(hours) && hours !== systemMonitorHours) {
      systemMonitorHours = hours;
      document.querySelectorAll('[data-monitor-hours]').forEach((button) => {
        button.disabled = true;
      });
      refreshSystemMonitor(true).catch(() => {});
    }
  }
  const priceCheck = event.target.closest('[data-price-check]');
  if (priceCheck) checkPrices(priceCheck.dataset.priceCheck);
  const priceEdit = event.target.closest('[data-price-edit]');
  if (priceEdit) editPriceCheck(priceEdit.dataset.priceEdit);
  const priceRemove = event.target.closest('[data-price-remove]');
  if (
    priceRemove &&
    window.confirm(`Remove ${priceRemove.dataset.priceTitle || 'this price check'}?`)
  )
    priceAction(() => post('price-checks/remove', { id: priceRemove.dataset.priceRemove }));
  const lightPower = event.target.closest('[data-light-target]');
  if (lightPower)
    action(() =>
      changeLightPower(lightPower.dataset.lightTarget, lightPower.dataset.lightValue),
    );
  const selected = event.target.closest('[data-select-speaker]');
  if (selected)
    action(async () => {
      const result = await post('speakers/select', { name: selected.dataset.selectSpeaker });
      await loadSpeakers();
      return result;
    });
  const transport = event.target.closest('[data-transport]');
  if (transport)
    action(async () => {
      try {
        return await post('speakers/transport', { action: transport.dataset.transport });
      } finally {
        await loadSpeakers();
      }
    });
  const groupMute = event.target.closest('[data-group-mute]');
  if (groupMute)
    action(async () => {
      try {
        return await post('speakers/group-mute', { muted: speakers?.group?.muted ? '0' : '1' });
      } finally {
        await loadSpeakers();
      }
    });
  const speakerMute = event.target.closest('[data-speaker-mute]');
  if (speakerMute)
    action(async () => {
      const item = speakers?.speakers?.find((s) => s.name === speakerMute.dataset.speakerMute);
      try {
        return await post('speakers/mute', {
          name: speakerMute.dataset.speakerMute,
          muted: item?.muted ? '0' : '1',
        });
      } finally {
        await loadSpeakers();
      }
    });
  const policyButton = event.target.closest('[data-policy-field]');
  if (policyButton) action(() => changeStoragePolicy(policyButton.dataset.policyField));
  const profile = event.target.closest('[data-ubnt-profile]');
  if (profile) startUbntWifi('connect', { profile: profile.dataset.ubntProfile });
  const newNetwork = event.target.closest('[data-ubnt-new]');
  if (newNetwork) showUbntPassword(newNetwork);
  const openNetwork = event.target.closest('[data-ubnt-open]');
  if (openNetwork)
    startUbntWifi('provision', {
      ssid: openNetwork.dataset.ubntSsid,
      security: 'none',
      bssid: openNetwork.dataset.ubntBssid,
      password: '',
    });
});
function refreshVisibleDashboard() {
  if (document.hidden) return;
  refresh();
  refreshConnectivity();
  refreshSpeedtest();
  refreshSonos();
  refreshStoragePolicy().catch(() => {});
  refreshSystemMonitor(false).catch(() => {});
  refreshUsbDevices(false).catch(() => {});
  refreshBackups(false).catch(() => {});
  refreshPriceChecks().catch(() => {});
  refreshLighting(false).catch(() => {});
  refreshUbntWifi(false);
}
Promise.allSettled([
  refresh(),
  loadSpeakers(),
  refreshConnectivity(),
  refreshSpeedtest(),
  refreshStoragePolicy(),
  refreshSystemMonitor(false),
  refreshUsbDevices(false),
  refreshBackups(false),
  refreshPriceChecks(),
  refreshLighting(false),
  refreshUbntWifi(false),
]).then((results) => {
  if (results[1].status === 'rejected') refreshSonos();
});
setInterval(() => {
  if (!document.hidden) refresh();
}, 5000);
setInterval(() => {
  if (!document.hidden) refreshConnectivity();
}, 10000);
setInterval(() => {
  if (!document.hidden && !busy) refreshSonos();
}, 10000);
setInterval(() => {
  if (!document.hidden && !busy) refreshStoragePolicy().catch(() => {});
}, 30000);
setInterval(() => {
  if (!document.hidden) refreshSystemMonitor(false).catch(() => {});
}, 30000);
setInterval(() => {
  if (!document.hidden && !$('usb-backdrop').classList.contains('open'))
    refreshUsbDevices(false).catch(() => {});
}, 30000);
setInterval(() => {
  if (!document.hidden && !$('backup-backdrop').classList.contains('open'))
    refreshBackups(false).catch(() => {});
}, 30000);
setInterval(() => {
  if (!document.hidden && !priceBusy) refreshPriceChecks().catch(() => {});
}, 30000);
setInterval(() => {
  if (!document.hidden && !busy) refreshLighting(false).catch(() => {});
}, 30000);
setInterval(() => {
  if (!document.hidden && ubntWifi?.operation?.status !== 'running') refreshUbntWifi(false);
}, 30000);
setInterval(() => {
  if (!document.hidden) updateSonosProgress();
}, 1000);
document.addEventListener('visibilitychange', refreshVisibleDashboard);
window.addEventListener('pageshow', refreshVisibleDashboard);
window.addEventListener('focus', refreshVisibleDashboard);
