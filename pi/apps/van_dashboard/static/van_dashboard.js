const $ = (id) => document.getElementById(id);
let dashboard = null,
  speakers = null,
  storagePolicy = null,
  diskStatus = null,
  lighting = null,
  priceChecks = null,
  systemMonitor = null,
  computeMetrics = null,
  usbPortStatus = null,
  backupState = null,
  ignitionMonitor = null,
  connectivityState = null,
  openwrtClients = null,
  ignitionDurationMinutes = 120,
  systemMonitorHours = 6,
  computeHours = 168,
  computeTaskFilter = '',
  computeTaskFilterRequestId = 0,
  computeTaskFilterLoading = false,
  computeTaskFilterError = '',
  ubntWifi = null,
  ubntLink = null,
  ubntNewNetwork = null,
  policyLoading = false,
  diskBusy = false,
  priceBusy = false,
  priceEditingId = null,
  priceScheduleInputTimer = 0,
  priceScheduleRetryTimer = 0,
  priceScheduleRequestId = 0,
  priceScheduleRetryDelay = 1000,
  priceSchedulePending = false,
  priceScheduleActiveExpression = '',
  priceScheduleParsedExpression = '',
  crashAnalysisBusy = false,
  usbPortBusy = false,
  backupBusy = false,
  ignitionMonitorBusy = false,
  systemPowerBusy = false,
  voltageCheckBusy = false,
  busy = false,
  tileEditing = false,
  tileDrag = null,
  toastTimer = 0,
  speedPoll = 0,
  storagePoll = 0,
  lightingPoll = 0,
  systemMonitorPoll = 0,
  computePoll = 0,
  usbPoll = 0,
  backupPoll = 0,
  ignitionMonitorPoll = 0,
  openwrtPoll = 0,
  systemPowerPoll = 0,
  ubntPoll = 0,
  ubntLastCompletion = '',
  backupLastCompletion = '',
  diskRunningOperation = '',
  openwrtClientsBusy = false,
  sonosTimeline = { position: 0, duration: 0, playing: false, updatedAt: 0 };
const TILE_ORDER_STORAGE_KEY = 'van-dashboard.tile-order.v1';
const computeJobDetailCache = new Map();
const computeJobDetailRequests = new Map();
const computeExpandedJobIds = new Set();
const computeTaskJobCache = new Map();
const COMPUTE_FILTER_JOB_LIMIT = 50;
const IGNITION_DURATION_UNITS = {
  minutes: { factor: 1, max: 720 },
  hours: { factor: 60, max: 168 },
  days: { factor: 1440, max: 366 },
};
function esc(v) {
  return String(v ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
}
const TRUNCATED_TEXT_SELECTOR = [
  '.status-line span:last-child',
  '.sonos-open span:last-child',
  '.now-playing strong',
  '.now-playing span',
  '.price-row-title span',
  '.price-row-title small',
  '.lighting-row strong',
  '.disk-device-name',
  '.disk-device-detail',
  '.ubnt-network-name',
  '.openwrt-client-main strong',
  '.openwrt-client-main small',
  '.network-value',
  '.speaker-name',
  '.usb-device-main strong',
  '.monitor-current strong',
  '.monitor-peak > strong',
  '.monitor-io-row strong',
  '.monitor-io-row small',
].join(',');
function setupTruncationTitles() {
  let frame = 0;
  const resizeObserver = window.ResizeObserver
    ? new ResizeObserver(() => schedule())
    : null;
  function fullText(element) {
    return element.innerText
      .trim()
      .replace(/\s*\n\s*/g, ' · ')
      .replace(/[\t ]+/g, ' ');
  }
  function sync() {
    frame = 0;
    document.querySelectorAll(TRUNCATED_TEXT_SELECTOR).forEach((element) => {
      const previous = element.dataset.truncationTitle || '';
      const current = element.getAttribute('title') || '';
      if (current && current !== previous) return;
      const clipped =
        element.scrollWidth > element.clientWidth + 1 ||
        element.scrollHeight > element.clientHeight + 1;
      const text = clipped ? fullText(element) : '';
      if (text) {
        element.title = text;
        element.dataset.truncationTitle = text;
      } else if (current === previous) {
        element.removeAttribute('title');
        delete element.dataset.truncationTitle;
      }
    });
  }
  function schedule() {
    if (!frame) frame = requestAnimationFrame(sync);
  }
  new MutationObserver(schedule).observe(document.body, {
    childList: true,
    characterData: true,
    subtree: true,
  });
  resizeObserver?.observe(document.body);
  window.addEventListener('resize', schedule);
  document.fonts?.ready.then(schedule);
  schedule();
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
function setSystemPowerButtonsDisabled(disabled) {
  document.querySelectorAll('[data-system-power]').forEach((button) => {
    button.disabled = disabled;
  });
}
async function pollSystemPowerResult() {
  clearTimeout(systemPowerPoll);
  try {
    const payload = await json('/api/system-power');
    const operation = payload.system_power || {};
    if (operation.status === 'error') {
      setSystemPowerButtonsDisabled(false);
      toast(operation.error || 'Power action failed; vanpi stayed on', true);
      return;
    }
    if (operation.status === 'running') {
      systemPowerPoll = setTimeout(pollSystemPowerResult, 750);
    }
  } catch (_) {
    // Losing the dashboard is expected once reboot or poweroff takes effect.
  }
}
async function requestSystemPower(action) {
  if (systemPowerBusy) return;
  const labels = {
    reboot: { question: 'Reboot vanpi now?' },
    'power-down': { question: 'Power down vanpi now?' },
  };
  const selected = labels[action];
  if (!selected) return;
  const confirmed = window.confirm(
    `${selected.question}\n\nAll managed disks will be safely unmounted and verified first. If that fails, vanpi will stay on.`,
  );
  if (!confirmed) return;

  systemPowerBusy = true;
  let accepted = false;
  setSystemPowerButtonsDisabled(true);
  try {
    const result = await post('system-power', { action, confirmation: action });
    accepted = true;
    toast(result.message);
    systemPowerPoll = setTimeout(pollSystemPowerResult, 750);
  } catch (error) {
    toast(error.message, true);
  } finally {
    systemPowerBusy = false;
    if (!accepted) {
      setSystemPowerButtonsDisabled(false);
    }
  }
}
function age(ts) {
  if (!ts) return 'never';
  const secs = Math.max(0, Date.now() / 1000 - ts);
  return secs < 90 ? `${Math.round(secs)}s ago` : `${Math.round(secs / 60)}m ago`;
}
function formatUptime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return 'Uptime · unavailable';
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const parts = [];
  if (days) parts.push(`${days}d`);
  if (days || hours) parts.push(`${hours}h`);
  parts.push(`${minutes}m`);
  return `Uptime · ${parts.join(' ')}`;
}
function renderTelemetrySummary(response) {
  renderVoltageCheck(response?.check);
  const battery = response?.battery || {};
  if (!battery.available || !Number.isFinite(battery.value)) {
    $('telemetry-voltage-value').textContent = '—';
    $('telemetry-voltage-source').textContent = 'Battery voltage unavailable';
    $('telemetry-observed').textContent = 'No live or voltage_mon reading';
    return;
  }
  const source = battery.source === 'live' ? 'live' : 'last voltage_mon';
  $('telemetry-voltage-value').textContent = `${Number(battery.value).toFixed(2)} V`;
  $('telemetry-voltage-source').textContent = source;
  const observed = new Date(battery.observed_at || '');
  $('telemetry-observed').textContent = Number.isNaN(observed.getTime())
    ? 'Timestamp unavailable'
    : `Observed · ${observed.toLocaleString([], {
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        second: '2-digit',
      })}`;
}
function renderVoltageCheck(check) {
  const running = check?.status === 'running' || (!check && voltageCheckBusy),
    button = $('telemetry-check');
  button.disabled = running;
  button.setAttribute('aria-busy', String(running));
  $('telemetry-check-label').textContent = running ? 'Checking voltage…' : 'Check voltage now';
}
async function refreshTelemetrySummary() {
  try {
    const response = await json('/api/telemetry-summary');
    renderTelemetrySummary(response);
    return response;
  } catch (_) {
    renderTelemetrySummary(null);
    return null;
  }
}
async function requestVoltageCheck() {
  if (voltageCheckBusy) return;
  voltageCheckBusy = true;
  renderVoltageCheck({ status: 'running' });
  const deadline = Date.now() + 115000;
  try {
    let response = await post('telemetry-voltage-check');
    renderVoltageCheck(response.check);
    while (response?.check?.status === 'running' && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      response = await refreshTelemetrySummary();
      if (!response) response = { check: { status: 'running' } };
    }
    if (response?.check?.status === 'running') {
      throw new Error('Voltage check is still running; the tile will update when it finishes');
    }
    if (response?.check?.status === 'error') {
      throw new Error(response.check.error || 'Voltage check failed');
    }
    const battery = response?.battery;
    toast(
      battery?.available && Number.isFinite(battery.value)
        ? `Voltage check complete · ${Number(battery.value).toFixed(2)} V`
        : 'Voltage check completed; no reading is available',
    );
  } catch (error) {
    toast(error.message, true);
  } finally {
    voltageCheckBusy = false;
    await refreshTelemetrySummary();
  }
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
function formatComputeSeconds(value) {
  if (!Number.isFinite(value)) return '—';
  const seconds = Math.max(0, value);
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 2 : 1)} s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
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
function durationWords(totalSeconds) {
  const seconds = Math.max(0, Math.round(Number(totalSeconds) || 0));
  if (seconds < 60) return seconds ? 'less than a minute' : 'expired';
  const days = Math.floor(seconds / 86400),
    hours = Math.floor((seconds % 86400) / 3600),
    minutes = Math.floor((seconds % 3600) / 60),
    parts = [];
  if (days) parts.push(`${days} day${days === 1 ? '' : 's'}`);
  if (hours) parts.push(`${hours} hour${hours === 1 ? '' : 's'}`);
  if (minutes && !days) parts.push(`${minutes} minute${minutes === 1 ? '' : 's'}`);
  return parts.slice(0, 2).join(' ');
}
function monitorRangeLabel(hours = systemMonitorHours) {
  if (hours === 168) return '7 days';
  if (hours === 720) return '30 days';
  return `${hours} hour${hours === 1 ? '' : 's'}`;
}
function computeRangeLabel(hours = computeHours) {
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
  const normalized = ordered.map((tile) => tile.id);
  if (
    normalized.length !== stored.length ||
    normalized.some((id, index) => stored[index] !== id)
  )
    saveTileOrder();
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
function mwanChips(interfaces) {
  return (interfaces || [])
    .map(
      (item) =>
        `<span class="mwan-chip ${esc(item.state)}" title="${esc(item.detail || '')}">${esc(item.name)} · ${esc(item.state)}</span>`,
    )
    .join('');
}
function renderOpenwrtPanel(connectivity) {
  if (!connectivity) return;
  const router = connectivity.router || {},
    online = connectivity.internet?.online,
    reachable = router.reachable,
    mode =
      router.mode ||
      (online === false || reachable === false ? 'No active uplink' : 'Unknown mode');
  networkState(
    'openwrt-sheet-internet-dot',
    online === null && reachable === false ? false : online,
  );
  networkState('openwrt-sheet-mwan-dot', reachable);
  $('openwrt-sheet-internet').textContent =
    online === true
      ? `Online via ${router.mode || 'active uplink'}`
      : online === false
        ? 'Offline'
        : 'No data';
  $('openwrt-sheet-mode').textContent = mode;
  $('openwrt-sheet-mwan-list').innerHTML = mwanChips(router.interfaces);
  $('openwrt-sheet-age').textContent = router.error
    ? `MWAN3 error · ${router.error}`
    : connectivity.last_error
      ? `Collector error · ${connectivity.last_error}`
      : connectivity.checked_at
        ? `${connectivity.stale ? 'Stale' : 'Updated'} · ${age(connectivity.checked_at)}`
        : connectivity.refreshing
          ? 'Checking…'
          : 'Waiting for MWAN3';
}
function renderConnectivity(response) {
  const c = response.connectivity,
    r = c.router || {},
    u = c.ubnt || {},
    online = c.internet?.online;
  connectivityState = c;
  networkState('internet-dot', online === null && r.reachable === false ? false : online);
  $('mwan-mode').textContent =
    r.mode || (online === false || r.reachable === false ? 'No active uplink' : 'Unknown');
  ubntLink = u;
  renderUbntTile();
  $('mwan-list').innerHTML = mwanChips(r.interfaces);
  $('openwrt-age').textContent = r.error
    ? `MWAN3 error · ${r.error}`
    : c.last_error
      ? `Collector error · ${c.last_error}`
      : c.checked_at
        ? `${c.stale ? 'Stale' : 'Updated'} · ${age(c.checked_at)}`
        : c.refreshing
          ? 'Checking…'
          : 'Waiting for MWAN3';
  renderOpenwrtPanel(c);
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
function renderOpenwrtClients(state) {
  openwrtClients = state;
  const clients = state?.clients || [],
    count = Number(state?.client_count) || 0,
    wifi = Number(state?.wifi_count) || 0,
    lan = Number(state?.lan_count) || 0;
  $('openwrt-client-count').textContent = state?.checked_at
    ? `${count} connected · ${wifi} Wi-Fi · ${lan} LAN · ${age(state.checked_at)}`
    : 'No client data';
  $('openwrt-client-list').innerHTML = clients.length
    ? clients
        .map((client) => {
          const detail = [
              client.ip || 'IPv4 unavailable',
              Number.isFinite(client.signal_dbm) ? `${client.signal_dbm} dBm` : null,
              client.neighbor_state ? client.neighbor_state.toLowerCase() : null,
            ]
              .filter(Boolean)
              .join(' · '),
            traffic =
              Number.isFinite(client.rx_bytes) && Number.isFinite(client.tx_bytes)
                ? `↓ ${formatBytes(client.rx_bytes)} · ↑ ${formatBytes(client.tx_bytes)}`
                : null,
            connectionLabel =
              client.connection === 'wifi'
                ? [client.band, client.radio].filter(Boolean).join(' · ') || 'WI-FI'
                : 'LAN';
          return `<article class="openwrt-client"><span class="network-dot good"></span><span class="openwrt-client-main"><strong>${esc(client.name)}</strong><small>${esc(detail)}</small></span><span class="openwrt-client-kind">${esc(connectionLabel)}</span><span class="openwrt-client-address"><code>${esc(client.mac)}</code>${traffic ? `<span>${esc(traffic)}</span>` : ''}</span></article>`;
        })
        .join('')
    : '<div class="openwrt-client-empty">No associated Wi-Fi clients or active LAN neighbors were reported.</div>';
}
async function refreshOpenwrtClients(showLoading = true) {
  if (openwrtClientsBusy) return;
  openwrtClientsBusy = true;
  $('openwrt-clients-refresh').disabled = true;
  $('openwrt-panel').setAttribute('aria-busy', 'true');
  if (showLoading && !openwrtClients) {
    $('openwrt-client-count').textContent = 'Checking…';
    $('openwrt-client-list').innerHTML =
      '<div class="speaker-loading">Querying OpenWrt…</div>';
  }
  try {
    const response = await json('/api/openwrt/clients');
    renderOpenwrtClients(response.openwrt);
  } catch (error) {
    $('openwrt-client-count').textContent = 'Client query failed';
    $('openwrt-client-list').innerHTML =
      `<div class="openwrt-client-empty">${esc(error.message)}</div>`;
    if (showLoading) toast(error.message, true);
  } finally {
    openwrtClientsBusy = false;
    $('openwrt-clients-refresh').disabled = false;
    $('openwrt-panel').setAttribute('aria-busy', 'false');
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
function usbHubName(hub, hubs) {
  if (!hub.physical) return hub.description;
  const physical = hubs.filter((item) => item.physical),
    index = physical.indexOf(hub);
  return physical.length > 1 && index >= 0
    ? `${hub.description} ${index + 1}`
    : hub.description;
}
function renderUsbHubCards(hubs, running) {
  return hubs
    .map((hub) => {
      const power = hub.method === 'power',
        hubDetail = hub.detail || `Location ${hub.location}`,
        hubName = usbHubName(hub, hubs);
      return `<article class="usb-hub-card"><div class="usb-hub-head"><span><strong>${esc(hubName)}</strong><small>${esc(hubDetail)}</small></span><b class="usb-method ${power ? 'power' : 'data'}">${power ? 'POWER + DATA' : 'DATA ONLY'}</b></div><div class="usb-port-grid">${(hub.ports || [])
        .map((port) => {
          const enabled = port.enabled !== false,
            mounted = port.mounted_labels || [],
            descriptions = port.device_descriptions || [],
            downstream = Number(port.downstream_device_count) || 0,
            label = `${hubName} port ${port.port}`,
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
    .join('');
}
function renderUsbPorts(state) {
  const operation = state?.operation || { status: 'idle' },
    running = operation.status === 'running',
    recovering = operation.action === 'restore';
  let operationLabel = state?.checked_at ? `Updated ${age(state.checked_at)}` : 'No port data';
  if (state?.last_error) operationLabel = 'Port status incomplete';
  if (operation.status === 'error')
    operationLabel = `Failed · ${operation.error || 'unknown error'}`;
  if (operation.status === 'complete' && recovering)
    operationLabel = operation.message || 'USB 2 restored';
  if (running)
    operationLabel = recovering ? 'Restoring USB 2…' : `${operation.action} · ${operation.key}`;
  $('usb-operation').textContent = operationLabel;
  $('usb-panel').classList.toggle('usb-port-busy', running);
  $('usb-recover').disabled = running || usbPortBusy;
  const hubList = $('usb-hub-list'),
    advancedOpen = Boolean(hubList.querySelector('.usb-advanced')?.open),
    hubs = state?.hubs || [],
    primaryHubs = hubs.filter((hub) => !hub.advanced),
    advancedHubs = hubs.filter((hub) => hub.advanced),
    advancedPortCount = advancedHubs.reduce((total, hub) => total + hub.ports.length, 0),
    primaryHtml = renderUsbHubCards(primaryHubs, running),
    advancedHtml = advancedHubs.length
      ? `<details class="usb-advanced"${advancedOpen ? ' open' : ''}><summary>Advanced / internal ports <span>${advancedPortCount} logical ports</span></summary><div class="usb-advanced-list">${renderUsbHubCards(advancedHubs, running)}</div></details>`
      : '';
  hubList.innerHTML =
    primaryHtml + advancedHtml ||
    '<div class="speaker-loading">No USB port controls discovered</div>';
}
function usbDeviceRoutes(device, hubs) {
  const instances = device.known_instances || device.instances || [],
    physicalHubs = hubs.filter((hub) => hub.physical),
    labels = instances.map((instance) => {
      const topologyKey = `${instance.parent_location}:${instance.port}`;
      for (const hub of hubs) {
        const port = (hub.ports || []).find(
          (item) =>
            item.key === topologyKey ||
            (item.topology_locations || []).includes(topologyKey),
        );
        if (!port) continue;
        const hubName = hub.physical
          ? physicalHubs.length > 1
            ? `${hub.description} ${physicalHubs.indexOf(hub) + 1}`
            : hub.description
          : hub.description;
        return `${hubName} · port ${port.port} · route ${instance.location}`;
      }
      return `Hub ${instance.parent_location} · port ${instance.port} · route ${instance.location}`;
    });
  return [...new Set(labels)];
}
function renderUsbDevices(response) {
  const state = response.usb,
    tile = $('usb-devices'),
    present = Number(state.present_device_count) || 0,
    unplugged = Number(state.unplugged_device_count) || 0,
    labels = state.storage_labels || [],
    hasData = Number.isFinite(state.last_success_at),
    stale = Boolean(state.last_error);
  usbPortStatus = response.usb_ports || null;
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
  renderUsbPorts(usbPortStatus);
  if (diskStatus) renderDiskStatus(diskStatus);
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
          .join(''),
        routes = usbDeviceRoutes(device, usbPortStatus?.hubs || []),
        routeHtml = routes.length
          ? `<small class="usb-device-route">Last known USB: ${esc(routes.join(' · '))}</small>`
          : '';
      return `<article class="usb-device-row ${esc(device.status)}">
        <span class="usb-device-dot" aria-hidden="true"></span>
        <div class="usb-device-main"><strong>${esc(device.description)}</strong><small>Bus ${esc(device.bus)} · ID ${esc(device.device_id)}</small>${routeHtml}${labelsHtml ? `<div class="usb-labels">${labelsHtml}</div>` : ''}${event ? `<time>${esc(event)}</time>` : ''}</div>
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
    usbPortStatus = null;
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
  if (diskStatus) renderDiskStatus(diskStatus);
  try {
    const response = await post('usb-ports/action', {
      port: button.dataset.usbPortKey,
      action: actionName,
    });
    usbPortStatus = response.usb_ports;
    renderUsbPorts(usbPortStatus);
    toast(response.message || 'USB port action started');
  } catch (error) {
    toast(error.message, true);
    await refreshUsbDevices(false).catch(() => {});
  } finally {
    usbPortBusy = false;
    if (diskStatus) renderDiskStatus(diskStatus);
  }
}
async function recoverUsb2() {
  const button = $('usb-recover');
  if (usbPortBusy || button.disabled) return;
  if (
    !window.confirm(
      'Restore the Pi USB 2 bus? Every USB 2 device will disconnect briefly. The recovery refuses to run if USB 2 storage is mounted.',
    )
  )
    return;
  usbPortBusy = true;
  button.disabled = true;
  $('usb-panel').classList.add('usb-port-busy');
  try {
    const response = await post('usb-ports/recover');
    renderUsbPorts(response.usb_ports);
    toast(response.message || 'USB 2 recovery started');
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
    exfat = state.exfat_snapshot || {},
    openwrt = state.openwrt || {},
    tm = state.time_machine || {},
    operation = state.operation || { status: 'idle' },
    operationKind = operation.kind || (operation.target ? 'clone' : null),
    operationRunning = operation.status === 'running',
    borgRunning = borg.running === true || (operationRunning && operationKind === 'borg'),
    exfatRunning = exfat.running === true || (operationRunning && operationKind === 'exfat'),
    backupRuntimeBusy = borgRunning || exfatRunning,
    tile = $('backups'),
    pill = $('backup-pill');
  backupState = state;
  tile.classList.remove('unknown', 'good', 'warning', 'running');
  tile.classList.add(
    state.health === 'good' ? 'good' : state.health === 'running' ? 'running' : 'warning',
  );
  pill.textContent =
    state.health === 'running' ? 'RUNNING' : state.health === 'good' ? 'CURRENT' : 'CHECK';
  const borgLabel = Number.isFinite(borg.last_success_at)
      ? `Borg ${backupAge(borg.last_success_at)}`
      : 'No Borg success',
    exfatLabel = Number.isFinite(exfat.last_success_at)
      ? `EXFAT ${backupAge(exfat.last_success_at)}`
      : 'No EXFAT snapshot',
    piLabel = `${borgLabel} · ${exfatLabel}`,
    openwrtLabel = Number.isFinite(openwrt.last_success_at)
      ? `Snapshot ${backupAge(openwrt.last_success_at)}`
      : 'No verified snapshot',
    macLabel = Number.isFinite(tm.last_backup_at)
      ? `TM ${backupAge(tm.last_backup_at)}`
      : tm.error || 'No Time Machine history';
  $('backup-pi').textContent = piLabel;
  $('backup-openwrt').textContent = openwrtLabel;
  $('backup-mac').textContent = tm.running
    ? `Backing up${Number.isFinite(tm.progress_percent) ? ` · ${tm.progress_percent}%` : ''}`
    : macLabel;
  if (borgRunning) {
    $('backup-summary').textContent = 'Creating a new vanpi Borg backup…';
  } else if (exfatRunning) {
    $('backup-summary').textContent = 'Creating an EXFAT512 safety snapshot…';
  } else if (operationRunning) {
    $('backup-summary').textContent = `Cloning vanpi to ${operation.target}…`;
  } else if (tm.running) {
    $('backup-summary').textContent = 'Time Machine backup in progress';
  } else {
    $('backup-summary').textContent = `${piLabel} · ${macLabel}`;
  }

  const borgDot = $('backup-borg-dot');
  borgDot.className = `backup-state-dot ${borgRunning ? 'running' : borg.stale ? 'bad' : 'good'}`;
  $('backup-borg-running').hidden = !borgRunning;
  $('backup-borg-detail').textContent = Number.isFinite(borg.last_success_at)
    ? `Last successful archive ${eventTime(borg.last_success_at)} (${backupAge(borg.last_success_at)})`
    : 'No successful Borg archive is recorded';
  const exfatDot = $('backup-exfat-dot');
  exfatDot.className = `backup-state-dot ${exfatRunning ? 'running' : exfat.stale ? 'bad' : 'good'}`;
  $('backup-exfat-running').hidden = !exfatRunning;
  $('backup-exfat-detail').textContent = Number.isFinite(exfat.last_success_at)
    ? `Last completed ${eventTime(exfat.last_success_at)} (${backupAge(exfat.last_success_at)})`
    : 'No successful EXFAT512 snapshot is recorded';
  const openwrtDot = $('backup-openwrt-dot');
  openwrtDot.className = `backup-state-dot ${openwrt.stale === false ? 'good' : 'bad'}`;
  $('backup-openwrt-detail').textContent = Number.isFinite(openwrt.last_success_at)
    ? `Last verified ${eventTime(openwrt.last_success_at)} (${backupAge(openwrt.last_success_at)})`
    : 'No verified OpenWrt snapshot is recorded';
  const tmDot = $('backup-tm-dot');
  tmDot.className = `backup-state-dot ${tm.running ? 'running' : Number.isFinite(tm.last_backup_at) ? 'good' : 'bad'}`;
  $('backup-tm-running').hidden = !tm.running;
  $('backup-tm-running').textContent = Number.isFinite(tm.progress_percent)
    ? `Backup in progress · ${tm.progress_percent}%`
    : 'Backup in progress';
  $('backup-tm-detail').textContent = Number.isFinite(tm.last_backup_at)
    ? `Last completed ${eventTime(tm.last_backup_at)} (${backupAge(tm.last_backup_at)})`
    : tm.error || 'No completed snapshots found';

  const operationKey = `${operation.status}:${operation.started_at || ''}:${operation.completed_at || ''}`;
  const borgRun = $('backup-run-borg'),
    exfatRun = $('backup-run-exfat');
  borgRun.disabled = backupBusy || operationRunning || backupRuntimeBusy;
  exfatRun.disabled = backupBusy || operationRunning || backupRuntimeBusy;
  borgRun.textContent = borgRunning
    ? 'Running Borg…'
    : operationRunning || backupRuntimeBusy
      ? 'Backup busy'
      : 'Run Borg backup';
  exfatRun.textContent = exfatRunning
    ? 'Snapshotting…'
    : operationRunning || backupRuntimeBusy
      ? 'Backup busy'
      : 'Snapshot EXFAT512';
  if (
    backupLastCompletion &&
    operationKey !== backupLastCompletion &&
    ['complete', 'error'].includes(operation.status)
  ) {
    toast(
      operation.status === 'complete'
        ? operationKind === 'borg'
          ? 'Vanpi Borg backup completed'
          : operationKind === 'exfat'
            ? 'EXFAT512 snapshot completed'
            : `Clone to ${operation.target} completed`
        : operation.error ||
            (operationKind === 'borg'
              ? 'Vanpi Borg backup failed'
              : operationKind === 'exfat'
                ? 'EXFAT512 snapshot failed'
                : `Clone to ${operation.target} failed`),
      operation.status === 'error',
    );
  }
  backupLastCompletion = operationKey;

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
        <button data-backup-clone="${label}" ${unavailable || operationRunning || backupRuntimeBusy || backupBusy ? 'disabled' : ''}>Clone current vanpi to this card</button>
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
  $('backup-openwrt').textContent = 'Unavailable';
  $('backup-mac').textContent = 'Unavailable';
  $('backup-status').textContent = 'Unavailable';
  $('backup-run-borg').disabled = true;
  $('backup-run-exfat').disabled = true;
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
async function startManualBackup(kind) {
  const isBorg = kind === 'borg',
    button = $(isBorg ? 'backup-run-borg' : 'backup-run-exfat');
  if (backupBusy || backupState?.operation?.status === 'running' || button.disabled) return;
  if (
    !window.confirm(
      isBorg
        ? 'Run the vanpi Borg backup now? This performs local snapshots, media sync, Borg create and retention, and any due hotspare clones. Requested disk policy and ignition safety still apply, and the job may take several hours.'
        : 'Create an EXFAT512 safety snapshot now? This mounts hdd1tb, creates a hard-link snapshot, applies retention, then unmounts and spins down the drive. Requested disk policy and ignition safety still apply.',
    )
  )
    return;
  backupBusy = true;
  button.disabled = true;
  button.textContent = 'Starting…';
  try {
    const response = await post(`backups/${kind}`);
    renderBackups(response);
    toast(response.message);
  } catch (error) {
    toast(error.message, true);
    await refreshBackups(false).catch(() => {});
  } finally {
    backupBusy = false;
  }
}
function updateIgnitionDurationPreview() {
  $('ignition-duration-preview').textContent = durationWords(ignitionDurationMinutes * 60);
  document.querySelectorAll('[data-ignition-preset]').forEach((button) => {
    button.classList.toggle(
      'selected',
      Number(button.dataset.ignitionPreset) === ignitionDurationMinutes,
    );
  });
}
function setIgnitionDuration(minutes, preferredUnit = null) {
  ignitionDurationMinutes = Math.max(1, Math.min(366 * 1440, Math.round(minutes)));
  let unit = preferredUnit;
  if (!IGNITION_DURATION_UNITS[unit]) {
    unit =
      ignitionDurationMinutes % 1440 === 0
        ? 'days'
        : ignitionDurationMinutes % 60 === 0
          ? 'hours'
          : 'minutes';
  }
  const settings = IGNITION_DURATION_UNITS[unit],
    amount = Math.max(1, Math.min(settings.max, Math.round(ignitionDurationMinutes / settings.factor)));
  ignitionDurationMinutes = amount * settings.factor;
  $('ignition-duration-unit').value = unit;
  $('ignition-duration-amount').max = settings.max;
  $('ignition-duration-amount').value = amount;
  $('ignition-duration-slider').max = settings.max;
  $('ignition-duration-slider').value = amount;
  updateIgnitionDurationPreview();
}
function readIgnitionDurationInput(source) {
  const unit = $('ignition-duration-unit').value,
    settings = IGNITION_DURATION_UNITS[unit],
    raw = Number(source.value);
  // Let the number field remain empty while the user replaces its contents.
  if (source === $('ignition-duration-amount') && source.value === '') return;
  const amount = Math.max(
    1,
    Math.min(settings.max, Number.isFinite(raw) ? Math.round(raw) : 1),
  );
  source.value = amount;
  if (source === $('ignition-duration-slider')) $('ignition-duration-amount').value = amount;
  else $('ignition-duration-slider').value = amount;
  ignitionDurationMinutes = amount * settings.factor;
  updateIgnitionDurationPreview();
}
function renderIgnitionMonitor(response) {
  const state = response.ignition_monitor,
    service = state.service || {},
    monitor = state.monitor || {},
    tile = $('ignition-monitor'),
    pill = $('ignition-monitor-pill'),
    remaining = Number.isFinite(monitor.deadline)
      ? Math.max(0, monitor.deadline - Date.now() / 1000)
      : 0;
  ignitionMonitor = state;
  tile.classList.remove('unknown', 'good', 'warning', 'critical');
  if (!service.running) {
    tile.classList.add('critical');
    pill.textContent = 'SERVICE DOWN';
  } else if (monitor.active) {
    tile.classList.add('good');
    pill.textContent = 'ACTIVE';
  } else {
    tile.classList.add('warning');
    pill.textContent = 'PAUSED';
  }
  $('ignition-monitor-service').textContent = service.running
    ? `Running · ${service.enabled ? 'starts at boot' : 'not enabled at boot'}`
    : `${service.active_state || 'unknown'} · ${service.sub_state || 'unknown'}`;
  $('ignition-monitor-state').textContent = monitor.active
    ? 'Active'
    : `Paused · ${durationWords(remaining)} left`;
  $('ignition-monitor-summary').textContent = !service.running
    ? 'ignitionmon.service is not running'
    : monitor.active
      ? 'Watching for ignition changes'
      : `Ignition handling resumes in ${durationWords(remaining)}`;

  $('ignition-service-dot').className = `backup-state-dot ${service.running ? 'good' : 'bad'}`;
  $('ignition-service-detail').textContent = service.running
    ? `Running · ${service.enabled ? 'enabled at boot' : 'not enabled at boot'}`
    : `${service.active_state || 'unknown'} · ${service.sub_state || 'unknown'}`;
  $('ignition-override-dot').className = `backup-state-dot ${monitor.active ? 'good' : 'warning'}`;
  $('ignition-override-detail').textContent = monitor.active
    ? 'Active — ignition-on actions are enabled'
    : `Paused until ${eventTime(monitor.deadline)} · ${durationWords(remaining)} remaining`;
  $('ignition-monitor-enable').disabled = ignitionMonitorBusy || monitor.active;
  $('ignition-monitor-disable').disabled = ignitionMonitorBusy;
  $('ignition-monitor-updated').textContent = Number.isFinite(monitor.checked_at)
    ? `Updated ${backupAge(monitor.checked_at)}`
    : 'Updated now';
  $('ignition-monitor-panel').setAttribute('aria-busy', 'false');
}
function renderIgnitionMonitorUnavailable(message) {
  ignitionMonitor = null;
  const tile = $('ignition-monitor');
  tile.classList.remove('good', 'warning', 'critical');
  tile.classList.add('unknown');
  $('ignition-monitor-pill').textContent = 'NO DATA';
  $('ignition-monitor-summary').textContent = message;
  $('ignition-monitor-service').textContent = 'Unavailable';
  $('ignition-monitor-state').textContent = 'Unavailable';
  $('ignition-monitor-updated').textContent = 'Unavailable';
  $('ignition-monitor-disable').disabled = true;
  $('ignition-monitor-enable').disabled = true;
  $('ignition-monitor-panel').setAttribute('aria-busy', 'false');
}
async function refreshIgnitionMonitor(showErrors = false) {
  try {
    const response = await json('/api/ignition-monitor');
    renderIgnitionMonitor(response);
    return response;
  } catch (error) {
    renderIgnitionMonitorUnavailable(error.message);
    if (showErrors) toast(error.message, true);
    throw error;
  }
}
async function changeIgnitionMonitor(paused) {
  if (ignitionMonitorBusy) return;
  if (
    paused &&
    !window.confirm(
      `Pause ignition monitoring for ${durationWords(ignitionDurationMinutes * 60)}? Ignition-on actions will be suppressed until the deadline.`,
    )
  )
    return;
  ignitionMonitorBusy = true;
  $('ignition-monitor-panel').setAttribute('aria-busy', 'true');
  $('ignition-monitor-disable').disabled = true;
  $('ignition-monitor-enable').disabled = true;
  try {
    const response = paused
      ? await post('ignition-monitor/disable', { minutes: ignitionDurationMinutes })
      : await post('ignition-monitor/enable');
    renderIgnitionMonitor(response);
    toast(response.message);
  } catch (error) {
    toast(error.message, true);
    await refreshIgnitionMonitor(false).catch(() => {});
  } finally {
    ignitionMonitorBusy = false;
    if (ignitionMonitor) renderIgnitionMonitor({ ignition_monitor: ignitionMonitor });
  }
}
function renderPriceChecks(response) {
  const previousSchedule = priceChecks?.schedule;
  priceChecks = {
    ...response,
    schedule: response.schedule || previousSchedule,
  };
  response = priceChecks;
  const items = response.items || [],
    searches = response.searches || [],
    summary = response.summary || {},
    searchSummary = response.search_summary || {},
    tile = $('price-checks'),
    latest = [...items, ...searches].reduce(
      (value, item) => Math.max(value, Number(item.last_checked_at) || 0),
      0,
    ),
    latestPriceItem = items.reduce(
      (selected, item) =>
        item.last_price &&
        Number(item.last_price_checked_at) > Number(selected?.last_price_checked_at || 0)
          ? item
          : selected,
      null,
    );
  renderPriceSchedule(response.schedule);
  if (priceEditingId !== null && !items.some((item) => item.id === priceEditingId))
    resetPriceForm();
  tile.classList.toggle('has-deal', Number(summary.below_threshold) > 0);
  tile.classList.toggle(
    'has-error',
    Number(summary.errors) + Number(searchSummary.errors) > 0,
  );
  $('price-pill').textContent = String(items.length + searches.length);
  $('price-summary').textContent = items.length || searches.length
    ? `${items.length} listings · ${searches.length} queries · ${Number(summary.errors) + Number(searchSummary.errors)} errors`
    : 'Nothing watched';
  $('price-latest-price').textContent = latestPriceItem
    ? `$${latestPriceItem.last_price} · ${latestPriceItem.display_title}`
    : '—';
  $('price-latest-price').title = latestPriceItem?.display_title || '';
  $('price-last-check').textContent = latest ? age(latest) : 'never';
  $('price-operation').textContent = priceBusy
    ? 'Working…'
    : items.length || searches.length
      ? `${items.length + searches.length} watched`
      : 'Empty';
  $('price-list').innerHTML =
    items
      .map((item) => {
        const stateClass = item.last_status === 'error' ? 'error' : item.below_threshold ? 'below' : '',
          price = item.last_price ? `$${esc(item.last_price)}` : '—',
          threshold = `$${esc(item.threshold)}`,
          checkMeta = item.last_status === 'error'
            ? `Error ${age(item.last_checked_at)} · ${esc(item.last_error || 'Unknown error')}`
            : item.last_checked_at
              ? `Checked ${age(item.last_checked_at)}${item.below_threshold ? ' · below target' : ''}`
              : 'Not checked yet',
          muteMeta = item.notifications_muted
            ? ` · notifications muted until ${esc(new Date(Number(item.notify_muted_until) * 1000).toLocaleString())}`
            : '',
          muteLabel = item.notifications_muted ? 'Unmute' : 'Mute';
        return `<article class="price-row ${stateClass}">
          <div class="price-row-title"><span>${esc(item.display_title)}</span><small>${esc(item.parser)} · ${esc(item.url)}</small></div>
          <div class="price-value">${price}<small>alert below ${threshold}</small></div>
          <div class="price-row-meta">${checkMeta}${muteMeta}</div>
          <div class="price-row-controls"><button data-action data-price-check="${item.id}" ${priceBusy ? 'disabled' : ''}>Check</button><button data-action data-price-edit="${item.id}" ${priceBusy ? 'disabled' : ''}>Edit</button><button class="price-mute${item.notifications_muted ? ' muted' : ''}" data-action data-price-mute="${item.id}" ${priceBusy ? 'disabled' : ''}>${muteLabel}</button><button class="price-remove" data-action data-price-remove="${item.id}" data-price-title="${esc(item.display_title)}" ${priceBusy ? 'disabled' : ''}>Remove</button></div>
        </article>`;
      })
      .join('') || '<div class="speaker-loading">No listing watches yet</div>';
  $('price-search-list').innerHTML =
    searches
      .map((search) => {
        const stateClass = search.last_status === 'error' ? 'error' : '',
          checkMeta = search.last_status === 'error'
            ? `Error ${age(search.last_checked_at)} · ${esc(search.last_error || 'Unknown error')}`
            : search.last_checked_at
              ? `Checked ${age(search.last_checked_at)} · ${search.result_count} visible · ${search.dismissed_count} dismissed`
              : 'Not checked yet',
          results = (search.results || [])
            .map((result) => {
              const details = [result.price, result.shipping].filter(Boolean).map(esc).join(' · ');
              return `<li class="price-search-result">
                <div><a href="${esc(result.url)}" target="_blank" rel="noopener noreferrer">${esc(result.title)}</a>${details ? `<small>${details}</small>` : ''}</div>
                <button class="price-remove" data-action data-search-dismiss="${esc(search.id)}" data-search-item-id="${esc(result.item_id)}" data-search-result-title="${esc(result.title)}" ${priceBusy ? 'disabled' : ''}>Dismiss</button>
              </li>`;
            })
            .join('');
        return `<article class="price-search-row ${stateClass}">
          <div class="price-row-title"><span>${esc(search.display_title)}</span><small>${esc(search.parser)} · <a href="${esc(search.url)}" target="_blank" rel="noopener noreferrer">open search</a></small></div>
          <div class="price-row-controls"><button data-action data-search-check="${search.id}" ${priceBusy ? 'disabled' : ''}>Check</button><button class="price-remove" data-action data-search-remove="${search.id}" data-search-title="${esc(search.display_title)}" ${priceBusy ? 'disabled' : ''}>Remove</button></div>
          <div class="price-row-meta">${checkMeta}</div>
          ${results ? `<ul class="price-search-results">${results}</ul>` : '<div class="price-search-empty">No visible results</div>'}
        </article>`;
      })
      .join('') || '<div class="speaker-loading">No query watches yet</div>';
  $('price-check-all').disabled = priceBusy || (items.length === 0 && searches.length === 0);
  $('price-check-all').classList.toggle('running', priceBusy);
  $('price-add-form').querySelectorAll('input, select, button').forEach((element) => {
    element.disabled = priceBusy;
  });
  $('price-search-add-form')
    .querySelectorAll('input, select, button')
    .forEach((element) => {
      element.disabled = priceBusy;
    });
  $('price-schedule').disabled = priceBusy;
  updatePriceScheduleSaveButton();
}
function renderPriceSchedule(schedule) {
  const input = $('price-schedule'),
    expression = schedule?.expression || '';
  input.value = expression;
  if (schedule?.error_code === 'rate_limit') {
    if (
      priceScheduleActiveExpression !== expression ||
      (!priceSchedulePending && !priceScheduleRetryTimer)
    )
      beginPriceScheduleRateLimitRetry(expression);
    return;
  }
  clearPriceScheduleTimers();
  priceScheduleActiveExpression = expression;
  priceScheduleRetryDelay = 1000;
  if (schedule?.error || !schedule?.description) {
    showPriceScheduleError(schedule?.error || 'empty cron description');
    return;
  }
  showPriceScheduleDescription(schedule);
}
function normalizedPriceSchedule() {
  return $('price-schedule').value.trim().replace(/\s+/g, ' ');
}
function updatePriceScheduleSaveButton() {
  $('price-schedule-save').disabled =
    priceBusy ||
    priceSchedulePending ||
    !priceScheduleParsedExpression ||
    normalizedPriceSchedule() !== priceScheduleParsedExpression;
}
function clearPriceScheduleTimers() {
  clearTimeout(priceScheduleInputTimer);
  clearTimeout(priceScheduleRetryTimer);
  priceScheduleInputTimer = 0;
  priceScheduleRetryTimer = 0;
}
function showPriceScheduleLoading() {
  const description = $('price-schedule-description'),
    spinner = document.createElement('span');
  description.textContent = '';
  description.classList.remove('error');
  description.setAttribute('aria-label', 'Parsing cron schedule');
  spinner.className = 'price-cron-spinner';
  spinner.setAttribute('aria-hidden', 'true');
  description.append(spinner);
  priceSchedulePending = true;
  priceScheduleParsedExpression = '';
  updatePriceScheduleSaveButton();
}
function showPriceScheduleDescription(schedule) {
  const description = $('price-schedule-description');
  description.removeAttribute('aria-label');
  description.textContent = schedule.description;
  description.classList.remove('error');
  priceSchedulePending = false;
  priceScheduleParsedExpression = schedule.expression;
  if (priceChecks) priceChecks.schedule = schedule;
  updatePriceScheduleSaveButton();
}
function showPriceScheduleError(detail) {
  console.error('Could not parse cron:', detail);
  const description = $('price-schedule-description');
  description.removeAttribute('aria-label');
  description.textContent = 'could not parse cron';
  description.classList.add('error');
  priceSchedulePending = false;
  priceScheduleParsedExpression = '';
  updatePriceScheduleSaveButton();
}
function queuePriceScheduleRetry(expression, requestId) {
  if (requestId !== priceScheduleRequestId) return;
  showPriceScheduleLoading();
  const delay = priceScheduleRetryDelay;
  priceScheduleRetryDelay *= 1.5;
  priceScheduleRetryTimer = setTimeout(() => {
    priceScheduleRetryTimer = 0;
    requestPriceScheduleParse(expression, requestId);
  }, delay);
}
async function requestPriceScheduleParse(expression, requestId) {
  if (requestId !== priceScheduleRequestId) return;
  showPriceScheduleLoading();
  try {
    const result = await post('price-checks/schedule/parse', { expression });
    if (requestId !== priceScheduleRequestId) return;
    const schedule = result.schedule || {};
    if (schedule.error_code === 'rate_limit') {
      queuePriceScheduleRetry(expression, requestId);
      return;
    }
    clearPriceScheduleTimers();
    priceScheduleRetryDelay = 1000;
    if (schedule.error || !schedule.description) {
      showPriceScheduleError(schedule.error || 'empty cron description');
      return;
    }
    showPriceScheduleDescription(schedule);
  } catch (error) {
    if (requestId !== priceScheduleRequestId) return;
    clearPriceScheduleTimers();
    showPriceScheduleError(error.message);
  }
}
function beginPriceScheduleParse(expression) {
  clearPriceScheduleTimers();
  priceScheduleRequestId += 1;
  priceScheduleRetryDelay = 1000;
  priceScheduleActiveExpression = expression;
  showPriceScheduleLoading();
  const requestId = priceScheduleRequestId;
  priceScheduleInputTimer = setTimeout(() => {
    priceScheduleInputTimer = 0;
    requestPriceScheduleParse(expression, requestId);
  }, 250);
}
function beginPriceScheduleRateLimitRetry(expression) {
  clearPriceScheduleTimers();
  priceScheduleRequestId += 1;
  priceScheduleRetryDelay = 1000;
  priceScheduleActiveExpression = expression;
  queuePriceScheduleRetry(expression, priceScheduleRequestId);
}
async function refreshPriceChecks() {
  try {
    const response = await json('/api/price-checks');
    renderPriceChecks(response);
    return response;
  } catch (error) {
    $('price-summary').textContent = 'Price data unavailable';
    $('price-latest-price').textContent = 'Unavailable';
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
async function checkSavedSearch(target) {
  return priceAction(() =>
    post('price-checks/searches/check', { target: String(target) }),
  );
}
async function addSavedSearch() {
  const result = await priceAction(() =>
    post('price-checks/searches/add', {
      parser: $('price-search-parser').value,
      url: $('price-search-url').value.trim(),
      title: $('price-search-title').value.trim(),
    }),
  );
  if (result) $('price-search-add-form').reset();
}
async function mutePriceCheck(itemId) {
  const item = priceChecks?.items?.find((candidate) => candidate.id === Number(itemId));
  if (!item || priceBusy) return;
  if (item.notifications_muted)
    return priceAction(() =>
      post('price-checks/mute', { id: String(item.id), days: '0' }),
    );
  const answer = window.prompt(
    `Mute notifications for ${item.display_title} for how many days?`,
    '7',
  );
  if (answer === null) return;
  const days = Number(answer.trim());
  if (!Number.isSafeInteger(days) || days < 1) {
    toast('Enter a whole number of days greater than zero', true);
    return;
  }
  return priceAction(() =>
    post('price-checks/mute', { id: String(item.id), days: String(days) }),
  );
}
async function savePriceSchedule() {
  if (priceBusy) return;
  const expression = normalizedPriceSchedule();
  if (expression !== priceScheduleParsedExpression) return;
  priceBusy = true;
  if (priceChecks) renderPriceChecks(priceChecks);
  try {
    const result = await post('price-checks/schedule', { expression });
    priceChecks = { ...priceChecks, schedule: result.schedule };
    renderPriceChecks(priceChecks);
    toast(result.message);
  } catch (error) {
    toast(error.message, true);
    await refreshPriceChecks().catch(() => {});
  } finally {
    priceBusy = false;
    if (priceChecks) renderPriceChecks(priceChecks);
  }
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
  $('price-form-title').textContent = 'Add a listing';
  $('price-submit').textContent = 'Add listing watch';
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
function computeFailureLabel(classification) {
  const labels = {
    task: 'TASK FAILED',
    timeout: 'TIMED OUT',
    resource: 'RESOURCE LIMIT',
    infrastructure: 'INFRASTRUCTURE',
    unknown: 'FAILED · UNKNOWN',
  };
  return labels[classification] || 'FAILED';
}
function computeStateLabel(job) {
  if (job.state === 'running') return 'RUNNING';
  if (job.state === 'queued') return 'QUEUED';
  if (job.state === 'failed')
    return computeFailureLabel(
      job.failure_classification || (job.timed_out ? 'timeout' : 'unknown'),
    );
  return 'DONE';
}
function computeJobDetailsId(jobId) {
  return `compute-job-details-${jobId}`;
}
function collapseComputeJobDetails(clearCache = false) {
  computeExpandedJobIds.clear();
  if (clearCache) computeJobDetailCache.clear();
  document.querySelectorAll('[data-compute-job-details]').forEach((button) => {
    const jobTitle = button.dataset.computeJobTitle || button.dataset.computeJobDetails;
    button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-label', `Show details for ${jobTitle}`);
    button.textContent = 'Details';
    const panel = $(button.getAttribute('aria-controls'));
    if (panel) {
      panel.setAttribute('aria-busy', 'false');
      panel.hidden = true;
    }
  });
}
function computeJobDetailsMarkup(payload) {
  if (payload?.detail_error)
    return `<div class="compute-job-detail-error">${esc(payload.detail_error)} · close and reopen Details to retry</div>`;
  const job = payload?.job || {},
    diagnostics = payload?.diagnostics || {},
    classification = job.failure_classification || diagnostics.failure_classification,
    failed = job.state === 'failed',
    diagnosticItems = [
      ['Worker error', diagnostics.worker_error, diagnostics.truncated?.worker_error],
      ['Resource limit', diagnostics.resource_limit, diagnostics.truncated?.resource_limit],
      ['Resource monitor', diagnostics.resource_monitor_error, diagnostics.truncated?.resource_monitor_error],
    ].filter(([, value]) => value),
    rows = [
      ['Job ID', job.id || '—'],
      ['Outcome', failed ? computeFailureLabel(classification) : computeStateLabel(job)],
      ['Placement', `${job.placement || '—'} · ${job.worker || 'unclaimed'}`],
      ['Exit code', job.exit_code ?? '—'],
      ['Submitted', job.submitted_at ? eventTime(job.submitted_at) : '—'],
      ['Started', job.started_at ? eventTime(job.started_at) : '—'],
      ['Finished', job.finished_at ? eventTime(job.finished_at) : '—'],
      ['Queue delay', Number.isFinite(Number(job.queue_seconds)) ? formatComputeSeconds(job.queue_seconds) : '—'],
      ['Active / analysis', `${formatComputeSeconds(job.active_seconds)} / ${formatComputeSeconds(job.analysis_seconds)}`],
      ['CPU / average', job.telemetry ? `${formatComputeSeconds(job.cpu_seconds)} / ${Number(job.average_cpu_percent || 0).toFixed(0)}%` : '—'],
      ['Peak memory', job.telemetry ? formatBytes(job.peak_rss_bytes) : '—'],
      ['Input / source / result', `${formatBytes(job.input_bytes)} / ${formatBytes(job.source_bytes)} / ${formatBytes(job.result_bytes)}`],
    ],
    streamMarkup = (stream, label) => {
      if (!stream?.available || !stream.excerpt) return '';
      const note = stream.truncated
        ? `tail of ${formatBytes(stream.bytes)}`
        : `${formatBytes(stream.bytes)} retained`;
      return `<section class="compute-job-output"><h5>${esc(label)}<small>${esc(note)}</small></h5><pre>${esc(stream.excerpt)}</pre></section>`;
    },
    outputMarkup = [
      streamMarkup(payload?.stderr, 'stderr'),
      streamMarkup(payload?.stdout, 'stdout'),
    ].join(''),
    noOutput = failed && !outputMarkup
      ? '<p class="compute-job-no-output">No retained stdout or stderr was available.</p>'
      : '';
  return `${failed ? `<div class="compute-job-error"><strong>${esc(computeFailureLabel(classification))}</strong><p>${esc(job.failure_summary || 'Failure reason unavailable')}</p>${diagnosticItems.map(([label, value, truncated]) => `<div><b>${esc(`${label}${truncated ? ' (truncated)' : ''}`)}</b><pre>${esc(value)}</pre></div>`).join('')}${diagnostics.interrupted ? '<p>Execution was interrupted.</p>' : ''}</div>` : ''}
    <dl class="compute-job-detail-grid">${rows.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>
    ${outputMarkup}${noOutput}`;
}
async function toggleComputeJobDetails(button) {
  const jobId = button.dataset.computeJobDetails,
    jobTitle = button.dataset.computeJobTitle || jobId,
    panelId = button.getAttribute('aria-controls'),
    panel = panelId ? $(panelId) : null;
  if (!jobId || !panel || button.disabled) return;
  if (computeExpandedJobIds.has(jobId)) {
    computeExpandedJobIds.delete(jobId);
    computeJobDetailCache.delete(jobId);
    button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-label', `Show details for ${jobTitle}`);
    button.textContent = 'Details';
    panel.setAttribute('aria-busy', 'false');
    panel.hidden = true;
    return;
  }
  computeExpandedJobIds.add(jobId);
  button.setAttribute('aria-expanded', 'true');
  button.setAttribute('aria-label', `Hide details for ${jobTitle}`);
  button.textContent = 'Hide details';
  panel.hidden = false;
  const cached = computeJobDetailCache.get(jobId);
  if (cached && !cached.detail_error) {
    panel.setAttribute('aria-busy', 'false');
    panel.innerHTML = computeJobDetailsMarkup(cached);
    return;
  }
  if (cached?.detail_error) computeJobDetailCache.delete(jobId);
  panel.setAttribute('aria-busy', 'true');
  panel.innerHTML = '<div class="speaker-loading">Loading run details…</div>';
  let request = computeJobDetailRequests.get(jobId);
  if (!request) {
    request = json(`/api/compute/jobs/${encodeURIComponent(jobId)}`).finally(() => {
      computeJobDetailRequests.delete(jobId);
    });
    computeJobDetailRequests.set(jobId, request);
  }
  try {
    const payload = await request;
    computeJobDetailCache.set(jobId, payload);
    const currentPanel = $(computeJobDetailsId(jobId));
    if (currentPanel && computeExpandedJobIds.has(jobId)) {
      currentPanel.innerHTML = computeJobDetailsMarkup(payload);
      currentPanel.setAttribute('aria-busy', 'false');
    }
  } catch (error) {
    const failure = { detail_error: error.message };
    computeJobDetailCache.set(jobId, failure);
    const currentPanel = $(computeJobDetailsId(jobId));
    if (currentPanel && computeExpandedJobIds.has(jobId)) {
      currentPanel.innerHTML = computeJobDetailsMarkup(failure);
      currentPanel.setAttribute('aria-busy', 'false');
    }
  }
}
function computeMissedReasonLabel(reason) {
  const labels = {
    'worker-unavailable': 'Worker unavailable',
    unsupported: 'Unsupported profile',
    'queue-busy': 'Queue busy',
    'failed-offload': 'Offload failed',
    'agent-choice': 'Agent choice',
    other: 'Other',
  };
  return labels[reason] || String(reason || 'Other').replaceAll('-', ' ');
}
function computeTaskCacheKey(hours, task) {
  return `${hours}\u0000${task}`;
}
function clearComputeTaskFilter(clearCache = false) {
  computeTaskFilterRequestId += 1;
  computeTaskFilter = '';
  computeTaskFilterLoading = false;
  computeTaskFilterError = '';
  if (clearCache) computeTaskJobCache.clear();
}
function computeJobSortTime(job) {
  const timestamp = job?.finished_at || job?.started_at || job?.submitted_at;
  if (typeof timestamp === 'number' && Number.isFinite(timestamp))
    return timestamp * 1000;
  const parsed = Date.parse(timestamp);
  return Number.isFinite(parsed) ? parsed : 0;
}
function computeJobsForDisplay(response) {
  const recentJobs = Array.isArray(response?.jobs) ? response.jobs : [];
  if (!computeTaskFilter) return recentJobs;
  const cache = computeTaskJobCache.get(
      computeTaskCacheKey(computeHours, computeTaskFilter),
    ),
    matchingRecent = recentJobs.filter((job) => job.task === computeTaskFilter),
    merged = new Map();
  (cache?.jobs || []).forEach((job) => {
    if (job.task === computeTaskFilter && job.id) merged.set(String(job.id), job);
  });
  matchingRecent.forEach((job) => {
    if (job.id) merged.set(String(job.id), job);
  });
  return [...merged.values()]
    .sort((left, right) => computeJobSortTime(right) - computeJobSortTime(left))
    .slice(0, COMPUTE_FILTER_JOB_LIMIT);
}
async function toggleComputeTaskFilter(button) {
  const task = button.dataset.computeTaskFilter;
  if (!task) return;
  collapseComputeJobDetails(true);
  computeTaskFilterRequestId += 1;
  computeTaskFilterLoading = false;
  computeTaskFilterError = '';
  if (computeTaskFilter === task) {
    computeTaskFilter = '';
    if (computeMetrics) renderComputeMetrics(computeMetrics);
    return;
  }
  computeTaskFilter = task;
  const response = computeMetrics || {},
    cacheKey = computeTaskCacheKey(computeHours, task),
    cached = computeTaskJobCache.get(cacheKey),
    visibleMatches = computeJobsForDisplay(response).length,
    shouldFetch = !cached && visibleMatches < COMPUTE_FILTER_JOB_LIMIT;
  computeTaskFilterLoading = shouldFetch;
  renderComputeMetrics(response);
  if (!shouldFetch) return;

  const requestId = computeTaskFilterRequestId,
    requestedHours = computeHours;
  try {
    const payload = await json(
      `/api/compute/jobs?hours=${requestedHours}&task=${encodeURIComponent(task)}`,
    );
    if (
      requestId !== computeTaskFilterRequestId
      || task !== computeTaskFilter
      || requestedHours !== computeHours
    )
      return;
    computeTaskJobCache.set(cacheKey, payload);
    computeTaskFilterLoading = false;
    renderComputeMetrics(computeMetrics || response);
  } catch (error) {
    if (
      requestId !== computeTaskFilterRequestId
      || task !== computeTaskFilter
      || requestedHours !== computeHours
    )
      return;
    computeTaskFilterLoading = false;
    computeTaskFilterError = error.message;
    renderComputeMetrics(computeMetrics || response);
  }
}
function renderComputeMetrics(response) {
  computeMetrics = response;
  const status = response.status || {},
    summary = response.summary || {},
    benchmark = response.benchmark || {},
    localWork = response.eligible_local_work || {},
    jobs = computeJobsForDisplay(response),
    tasks = response.tasks || [],
    localCategories = Array.isArray(localWork.categories) ? localWork.categories : [],
    localReasons = Array.isArray(localWork.reasons) ? localWork.reasons : [],
    localEvents = Array.isArray(localWork.recent) ? localWork.recent : [],
    available = status.available === true,
    running = Number(status.running) || 0,
    localRunning = Number(status.local_running) || 0,
    queued = Number(status.queued) || 0,
    failures = Number(summary.failed) || 0,
    telemetryJobs = Number(summary.telemetry_jobs) || 0,
    timingJobs = Number(summary.timing_jobs) || 0,
    calibratedJobs = Number(benchmark.calibrated_remote_jobs) || 0,
    calibratedWorkloads = Number(benchmark.calibrated_workloads) || 0,
    benchmarkPiSamples = Number(benchmark.pi_samples) || 0,
    analysisRatio = Number(benchmark.analysis_speedup_ratio),
    cpuRatio = Number(benchmark.pi_to_mac_cpu_ratio),
    hasAnalysisRatio = benchmark.analysis_speedup_ratio !== null && Number.isFinite(analysisRatio),
    hasCpuRatio = benchmark.pi_to_mac_cpu_ratio !== null && Number.isFinite(cpuRatio),
    eligibleEvents = Number(localWork.events) || 0,
    recordedPlacements = (Number(summary.jobs) || 0) + eligibleEvents,
    tile = $('compute-worker'),
    tileLevel = localRunning ? 'warning' : available ? (failures ? 'warning' : 'good') : queued ? 'warning' : 'unknown';
  tile.classList.remove('unknown', 'good', 'warning', 'critical');
  tile.classList.add(tileLevel);
  $('compute-pill').textContent = running ? 'BUSY' : localRunning ? 'PI FALLBACK' : available ? 'ONLINE' : 'OFFLINE';
  $('compute-summary').textContent = summary.jobs
    ? `${summary.jobs} job${summary.jobs === 1 ? '' : 's'} offloaded in ${computeRangeLabel()}`
    : 'No completed jobs in selected range';
  const remoteWorkers = (status.workers || []).filter((worker) => worker.placement !== 'pi-local');
  const capacityWorkers = remoteWorkers.filter((worker) => worker.available && worker.slots_total !== null && worker.slots_total !== undefined);
  const freshestWorker = (capacityWorkers.length ? capacityWorkers : remoteWorkers)
    .slice()
    .sort((left, right) => (left.age_seconds ?? Infinity) - (right.age_seconds ?? Infinity))[0];
  $('compute-worker-state').textContent = available
    ? `${freshestWorker?.worker || 'Mac'} · ${running ? 'working' : 'ready'}`
    : freshestWorker?.seen_at
      ? `Last seen ${age(freshestWorker.seen_at)}`
      : 'No heartbeat';
  const hasSlotTelemetry = status.slots_total !== null
    && status.slots_total !== undefined
    && Number.isFinite(Number(status.slots_total))
    && Number.isFinite(Number(status.slots_busy));
  $('compute-slots').textContent = hasSlotTelemetry
    ? `${Number(status.slots_busy)} / ${Number(status.slots_total)} busy · ${Number(status.slots_available) || 0} free`
    : 'Awaiting scheduler heartbeat';
  $('compute-queue').textContent = running || localRunning || queued
    ? `${running} Mac running · ${localRunning} Pi fallback · ${queued} queued`
    : 'Empty';
  $('compute-cpu').textContent = telemetryJobs
    ? formatComputeSeconds(summary.mac_cpu_seconds)
    : 'Awaiting telemetry job';
  $('compute-memory').textContent = telemetryJobs
    ? formatBytes(summary.peak_rss_bytes)
    : 'Awaiting telemetry job';
  $('compute-local').textContent = eligibleEvents
    ? `${eligibleEvents} event${eligibleEvents === 1 ? '' : 's'} · ${formatComputeSeconds(localWork.cpu_seconds)} CPU`
    : 'None recorded';

  $('compute-panel').setAttribute('aria-busy', 'false');
  $('compute-status').textContent = `${available ? 'Worker online' : 'Worker offline'}${localRunning ? ` · ${localRunning} Pi fallback running` : ''}${hasSlotTelemetry ? ` · ${Number(status.slots_available) || 0}/${Number(status.slots_total)} slots free` : ''} · ${computeRangeLabel()}`;
  const leadingReason = localReasons[0];
  const overview = [
    ['Recorded placement share', recordedPlacements ? `${(100 * (Number(summary.jobs) || 0) / recordedPlacements).toFixed(0)}% Mac` : '—', recordedPlacements ? `${Number(summary.jobs) || 0} completed Mac job${Number(summary.jobs) === 1 ? '' : 's'} · ${eligibleEvents} recorded Pi event${eligibleEvents === 1 ? '' : 's'}` : 'No completed placement evidence in this range'],
    ['Estimated Pi analysis avoided', calibratedJobs ? formatComputeSeconds(benchmark.estimated_pi_analysis_seconds_avoided) : '—', calibratedJobs ? `${calibratedJobs} exact-content Mac job${calibratedJobs === 1 ? '' : 's'} calibrated · Pi submission, snapshot, and SSH streaming overhead excluded` : 'Needs the same exact-content workload measured on Pi and Mac'],
    ['Estimated Pi CPU avoided', calibratedJobs ? formatComputeSeconds(benchmark.estimated_pi_cpu_seconds_avoided) : '—', calibratedJobs ? `Per-workload averages from ${benchmarkPiSamples} Pi sample${benchmarkPiSamples === 1 ? '' : 's'} across ${calibratedWorkloads} workload${calibratedWorkloads === 1 ? '' : 's'}` : 'No matched Pi/Mac samples in this range'],
    ['Exact-content Pi / Mac', calibratedJobs && hasAnalysisRatio ? `${analysisRatio.toFixed(2)}× analysis` : '—', calibratedJobs ? `${hasCpuRatio ? `${cpuRatio.toFixed(2)}× CPU` : 'CPU ratio unavailable'} · ${formatBytes(benchmark.maximum_pi_peak_rss_bytes)} max Pi RSS` : 'Dataset-backed and unmatched work is not estimated'],
    ['Offloaded jobs · Mac', `${summary.jobs || 0}`, `${summary.succeeded || 0} succeeded · ${failures} failed`],
    ['CPU consumed · Mac', telemetryJobs ? formatComputeSeconds(summary.mac_cpu_seconds) : '—', telemetryJobs ? `${Number(summary.aggregate_cpu_percent || 0).toFixed(0)}% across measured active time` : 'New worker telemetry starts with its next job'],
    ['Mac active time', telemetryJobs ? formatComputeSeconds(summary.mac_wall_seconds) : '—', `${telemetryJobs} measured job${telemetryJobs === 1 ? '' : 's'} · staging through non-telemetry uploads`],
    ['Mac phase timing', timingJobs ? formatComputeSeconds(summary.mac_analysis_seconds) : '—', timingJobs ? `${formatComputeSeconds(summary.mac_preparation_seconds)} prep · ${formatComputeSeconds(summary.mac_packaging_seconds)} package · ${formatComputeSeconds(summary.mac_result_upload_seconds)} upload` : 'Detailed phase timing starts with the new scheduler'],
    ['Peak job memory · Mac', telemetryJobs ? formatBytes(summary.peak_rss_bytes) : '—', 'Higher of sampled process-group RSS and wait4 leader maximum'],
    ['Eligible local events · Pi', `${eligibleEvents}`, leadingReason ? `Most common: ${computeMissedReasonLabel(leadingReason.reason)} (${leadingReason.events})` : 'None recorded in this range'],
    ['CPU consumed · Pi', eligibleEvents ? formatComputeSeconds(localWork.cpu_seconds) : '—', 'Measured eligible work that stayed local'],
    ['Wall time · Pi', eligibleEvents ? formatComputeSeconds(localWork.wall_seconds) : '—', 'Elapsed time for recorded eligible work'],
    ['Peak memory · Pi', eligibleEvents ? formatBytes(localWork.peak_rss_bytes) : '—', 'Largest recorded eligible command'],
    ['Input measured', `${formatBytes(summary.input_bytes)} Mac`, `${formatBytes(localWork.input_bytes)} eligible local · ${formatBytes(summary.source_bytes)} source snapshots`],
    ['Results returned · Mac', formatBytes(summary.result_bytes), Number.isFinite(summary.average_queue_seconds) ? `${formatComputeSeconds(summary.average_queue_seconds)} average queue delay` : 'No completed queue timing'],
  ];
  $('compute-overview').innerHTML = overview
    .map(([label, value, detail]) => `<div><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(detail)}</small></div>`)
    .join('');

  $('compute-local-count').textContent = `${eligibleEvents} recorded`;
  $('compute-local-reasons').innerHTML = localReasons
    .map((reason) => `<span><strong>${Number(reason.events) || 0}</strong>${esc(computeMissedReasonLabel(reason.reason))}</span>`)
    .join('');
  const maxLocalCpu = Math.max(0, ...localCategories.map((category) => Number(category.cpu_seconds) || 0));
  $('compute-local-categories').innerHTML = localCategories
    .map((category) => {
      const width = maxLocalCpu > 0 ? Math.max(2, (100 * Number(category.cpu_seconds || 0)) / maxLocalCpu) : 0;
      return `<article><span><strong>${esc(category.command_category)}</strong><small>${Number(category.events) || 0} event${Number(category.events) === 1 ? '' : 's'} · ${formatBytes(category.input_bytes)} input</small></span><span class="compute-task-meter"><i style="width:${width}%"></i><small>${esc(`${formatComputeSeconds(category.cpu_seconds)} Pi CPU · ${formatComputeSeconds(category.wall_seconds)} wall · ${formatBytes(category.peak_rss_bytes)} peak`)}</small></span></article>`;
    })
    .join('') || '<div class="speaker-loading">No eligible local work recorded in this range</div>';
  $('compute-local-events').innerHTML = localEvents
    .map((event) => `<article class="compute-local-event">
      <div class="compute-job-head"><span><strong>${esc(event.label || event.command_category || 'Eligible local work')}</strong><small>${esc(eventTime(event.recorded_at))} · ${esc(event.command_category || 'uncategorized')}</small></span><b>PI LOCAL</b></div>
      <p>${esc(`${computeMissedReasonLabel(event.reason)} · ${formatComputeSeconds(event.cpu_seconds)} CPU · ${formatComputeSeconds(event.wall_seconds)} wall · ${formatBytes(event.peak_rss_bytes)} peak`)}</p>
    </article>`)
    .join('') || '<div class="speaker-loading">No recent eligible local events</div>';

  const maxCpu = Math.max(0, ...jobs.map((job) => Number(job.cpu_seconds) || 0)),
    maxMemory = Math.max(0, ...jobs.map((job) => Number(job.peak_rss_bytes) || 0)),
    preserveOpenDetails = $('compute-backdrop').classList.contains('open')
      && computeExpandedJobIds.size > 0;
  $('compute-job-count').textContent = computeTaskFilter
    ? `${jobs.length} shown · ${computeTaskFilter} only${computeTaskFilterLoading ? ' · loading older matches…' : computeTaskFilterError ? ' · older matches unavailable' : ''}`
    : `${jobs.length} shown`;
  if (!preserveOpenDetails) {
    const visibleJobIds = new Set(jobs.map((job) => String(job.id || '')));
    for (const jobId of computeExpandedJobIds)
      if (!visibleJobIds.has(jobId)) computeExpandedJobIds.delete(jobId);
    for (const jobId of computeJobDetailCache.keys())
      if (!visibleJobIds.has(jobId)) computeJobDetailCache.delete(jobId);
    jobs.forEach((job) => {
      const jobId = String(job.id || ''),
        cached = computeJobDetailCache.get(jobId);
      if (cached?.job?.state && cached.job.state !== job.state)
        computeJobDetailCache.delete(jobId);
    });
    $('compute-jobs').innerHTML = jobs
      .map((job) => {
        const cpuWidth = maxCpu > 0 ? Math.max(2, (100 * Number(job.cpu_seconds || 0)) / maxCpu) : 0,
          memoryWidth = maxMemory > 0 ? Math.max(2, (100 * Number(job.peak_rss_bytes || 0)) / maxMemory) : 0,
          timestamp = job.finished_at || job.started_at || job.submitted_at,
          jobId = String(job.id || ''),
          detailsId = computeJobDetailsId(jobId),
          detailsAvailable = job.details_available !== false,
          expanded = detailsAvailable && computeExpandedJobIds.has(jobId),
          cachedDetails = computeJobDetailCache.get(jobId),
          details = job.telemetry
            ? job.detailed_timing
              ? `${formatComputeSeconds(job.active_seconds)} active · ${formatComputeSeconds(job.analysis_seconds)} analysis · ${formatComputeSeconds(job.preparation_seconds)} prep · ${formatComputeSeconds(job.packaging_seconds)} package · ${formatComputeSeconds(job.result_upload_seconds)} upload`
              : `${formatComputeSeconds(job.wall_seconds)} legacy job wall · ${Number(job.average_cpu_percent || 0).toFixed(0)}% avg CPU · ${formatBytes(job.input_bytes)} input`
            : `${formatBytes(job.input_bytes)} input · resource telemetry unavailable for this older job`,
          summary = job.failure_summary ? `${job.failure_summary} · ${details}` : details,
          buttonAction = expanded ? 'Hide' : 'Show',
          jobTitle = `${job.task || 'job'} ${jobId}`,
          buttonLabel = `${buttonAction} details for ${jobTitle}`;
        return `<article class="compute-job ${esc(job.state)}">
          <div class="compute-job-head"><span><strong>${esc(job.task)}</strong><small>${esc(eventTime(timestamp))} · ${esc(job.worker || 'unclaimed')}</small></span><b>${esc(computeStateLabel(job))}</b></div>
          <p>${esc(summary)}</p>
          <div class="compute-bars" aria-label="Job resource use"><span><i style="width:${cpuWidth}%"></i><small>CPU ${job.telemetry ? formatComputeSeconds(job.cpu_seconds) : '—'}</small></span><span><i style="width:${memoryWidth}%"></i><small>Memory ${job.telemetry ? formatBytes(job.peak_rss_bytes) : '—'}</small></span></div>
          <div class="compute-job-actions"><button type="button" data-compute-job-details="${esc(jobId)}" data-compute-job-title="${esc(jobTitle)}" aria-label="${esc(buttonLabel)}" aria-expanded="${expanded ? 'true' : 'false'}" aria-controls="${esc(detailsId)}" ${detailsAvailable ? '' : 'disabled'}>${expanded ? 'Hide details' : 'Details'}</button></div>
          <div class="compute-job-details" id="${esc(detailsId)}" aria-busy="${expanded && !cachedDetails ? 'true' : 'false'}" ${expanded ? '' : 'hidden'}>${expanded ? (cachedDetails ? computeJobDetailsMarkup(cachedDetails) : '<div class="speaker-loading">Loading run details…</div>') : ''}</div>
        </article>`;
      })
      .join('') || '<div class="speaker-loading">No queued or completed jobs in this range</div>';
  }

  const focusedTask = document.activeElement?.closest?.('[data-compute-task-filter]')
      ?.dataset.computeTaskFilter,
    maxTaskCpu = Math.max(0, ...tasks.map((task) => Number(task.cpu_seconds) || 0));
  $('compute-tasks').innerHTML = tasks
    .map((task) => {
      const width = maxTaskCpu > 0 ? Math.max(2, (100 * Number(task.cpu_seconds || 0)) / maxTaskCpu) : 0,
        selected = task.task === computeTaskFilter,
        actionLabel = selected ? 'Clear queue filter for' : 'Filter queue jobs by';
      const telemetryLabel = task.telemetry_jobs
        ? `${formatComputeSeconds(task.cpu_seconds)} Mac CPU · ${formatBytes(task.peak_rss_bytes)} peak`
        : 'Resource telemetry starts with the next job';
      return `<button class="compute-task-filter" type="button" data-compute-task-filter="${esc(task.task)}" aria-pressed="${selected ? 'true' : 'false'}" aria-label="${esc(`${actionLabel} ${task.task}`)}"><span><strong>${esc(task.task)}</strong><small>${task.jobs} job${task.jobs === 1 ? '' : 's'} · ${formatBytes(task.input_bytes)} input</small></span><span class="compute-task-meter"><i style="width:${width}%"></i><small>${esc(telemetryLabel)}</small></span></button>`;
    })
    .join('') || '<div class="speaker-loading">No completed task totals in this range</div>';
  if (focusedTask) {
    const replacement = [...document.querySelectorAll('[data-compute-task-filter]')]
      .find((button) => button.dataset.computeTaskFilter === focusedTask);
    if (replacement) replacement.focus({ preventScroll: true });
  }
  $('compute-measurement-note').textContent = response.measurement_note || '';
  document.querySelectorAll('[data-compute-hours]').forEach((button) => {
    button.classList.toggle('active', Number(button.dataset.computeHours) === computeHours);
    button.disabled = false;
  });
}
function renderComputeUnavailable(message) {
  collapseComputeJobDetails();
  clearComputeTaskFilter();
  const tile = $('compute-worker');
  tile.classList.remove('good', 'warning', 'critical');
  tile.classList.add('unknown');
  $('compute-pill').textContent = 'NO DATA';
  $('compute-summary').textContent = 'Compute metrics unavailable';
  $('compute-worker-state').textContent = '—';
  $('compute-slots').textContent = '—';
  $('compute-queue').textContent = '—';
  $('compute-cpu').textContent = '—';
  $('compute-memory').textContent = '—';
  $('compute-local').textContent = '—';
  $('compute-status').textContent = 'Unavailable';
  $('compute-panel').setAttribute('aria-busy', 'false');
  $('compute-jobs').innerHTML = `<div class="speaker-loading">${esc(message)}</div>`;
  $('compute-tasks').innerHTML = `<div class="speaker-loading">${esc(message)}</div>`;
  $('compute-local-categories').innerHTML = `<div class="speaker-loading">${esc(message)}</div>`;
  $('compute-local-events').innerHTML = `<div class="speaker-loading">${esc(message)}</div>`;
  $('compute-local-reasons').innerHTML = '';
  document.querySelectorAll('[data-compute-hours]').forEach((button) => {
    button.disabled = false;
  });
}
async function refreshComputeMetrics(showErrors = false) {
  try {
    const response = await json(`/api/compute?hours=${computeHours}`);
    renderComputeMetrics(response);
    return response;
  } catch (error) {
    renderComputeUnavailable(error.message);
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
  if (diskStatus) renderDiskStatus(diskStatus);
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
function diskOperationKey(operation) {
  return operation?.started_at
    ? `${operation.label}|${operation.action}|${operation.started_at}`
    : '';
}
function diskHoldSeconds(disk) {
  if (!Number.isFinite(disk?.hold_until)) return 0;
  return Math.max(0, Math.ceil(disk.hold_until - Date.now() / 1000));
}
function diskState(disk, operation) {
  const running =
      operation?.status === 'running' && operation.label === disk.label,
    holdSeconds = diskHoldSeconds(disk);
  if (running) {
    return {
      className: '',
      label:
        operation.action === 'eject'
          ? 'UNMOUNTING…'
          : operation.action === 'repair'
            ? 'REPAIRING…'
            : 'MOUNTING…',
      holdSeconds,
    };
  }
  if (disk.error) return { className: 'bad', label: 'ERROR', holdSeconds: 0 };
  if (disk.health?.state === 'critical')
    return { className: 'bad', label: 'HEALTH ERROR', holdSeconds: 0 };
  if (disk.health?.state === 'warning')
    return {
      className: 'held',
      label: 'WARNING',
      holdSeconds: 0,
    };
  if (!disk.attached) return { className: '', label: 'NO DEVICE', holdSeconds: 0 };
  if (disk.mounted) return { className: 'good', label: 'MOUNTED', holdSeconds: 0 };
  if (holdSeconds)
    return { className: 'held', label: `UNMOUNTED · ${holdSeconds}s`, holdSeconds };
  return { className: 'bad', label: 'UNMOUNTED', holdSeconds: 0 };
}
function diskDetail(disk, state) {
  if (disk.error)
    return {
      primary: 'Disk status is unavailable',
      context: '',
      currentError: disk.error,
    };
  if (!disk.attached)
    return {
      primary: `Not attached · expects ${disk.expected_mount}`,
      context: '',
      currentError: '',
    };
  const health = disk.health || {},
    identity = [],
    currentError = [];
  if (Number.isFinite(disk.size_bytes)) identity.push(formatBytes(disk.size_bytes));
  if (disk.filesystem) identity.push(disk.filesystem);
  if (disk.mounted) identity.push(disk.expected_mount);
  if (state.holdSeconds) identity.push(`auto-mount held ${state.holdSeconds}s`);
  if (health.current_error_message) {
    currentError.push(health.current_error_message);
    if (Number.isFinite(health.current_error_at)) {
      currentError.push(
        `Observed ${eventTime(health.current_error_at)} (${backupAge(health.current_error_at)})`,
      );
    }
    const count = Number(health.current_boot_error_count) || 0;
    if (count > 1) currentError.push(`${count} matching events this boot`);
  }
  return {
    primary: health.observation || 'Attached USB disk',
    context: identity.join(' · '),
    currentError: currentError.join(' · '),
  };
}
function diskUsbPowerPort(label) {
  const matches = new Map();
  (usbPortStatus?.hubs || []).forEach((hub) => {
    (hub.ports || []).forEach((port) => {
      if (
        port.method === 'power' &&
        Array.isArray(port.storage_labels) &&
        port.storage_labels.length === 1 &&
        port.storage_labels.includes(label)
      ) {
        matches.set(port.key, port);
      }
    });
  });
  return matches.size === 1 ? [...matches.values()][0] : null;
}
function renderDiskStatus(next) {
  diskStatus = next;
  const operation = next?.operation || { status: 'idle' },
    operationKey = diskOperationKey(operation),
    operationLabel = $('disk-operation');
  if (operation.status === 'running') {
    diskRunningOperation = operationKey;
    operationLabel.textContent =
      `${operation.action === 'eject' ? 'Unmounting' : operation.action === 'repair' ? 'Repairing' : 'Mounting'} ${operation.label}…`;
  } else if (operation.status === 'error') {
    operationLabel.textContent = `${operation.label || 'Disk action'} failed`;
    if (diskRunningOperation === operationKey) toast(operation.error || 'Disk action failed', true);
    if (diskRunningOperation === operationKey) diskRunningOperation = '';
  } else if (operation.status === 'complete') {
    operationLabel.textContent =
      `${operation.action === 'eject' ? 'Unmounted' : operation.action === 'repair' ? 'Repaired' : 'Mounted'} ${operation.label} · ${age(operation.completed_at)}`;
    if (diskRunningOperation === operationKey) {
      toast(
        `${operation.label} ${
          operation.action === 'eject'
            ? 'unmounted'
            : operation.action === 'repair'
              ? 'repaired and verified'
              : 'mounted'
        }`,
      );
      diskRunningOperation = '';
    }
  } else {
    operationLabel.textContent = `Updated · ${age(next?.checked_at)}`;
  }
  const disks = Array.isArray(next?.disks) ? next.disks : [];
  $('disk-device-list').innerHTML = disks.length
    ? disks
        .map((disk) => {
          const state = diskState(disk, operation),
            detail = diskDetail(disk, state),
            action = disk.mounted ? 'eject' : 'mount',
            canControl =
              disk.controllable &&
              disk.attached &&
              !disk.error &&
              operation.status !== 'running' &&
              !diskBusy,
            policyAllowsMount =
              disk.requires_disk_policy === false || storagePolicy?.disks_enabled === true,
            actionAllowed = canControl && (action !== 'mount' || policyAllowsMount),
            actionTitle =
              action === 'mount' && !policyAllowsMount
                ? 'Enable the HDDs policy before mounting'
                : `${action === 'eject' ? 'Safely unmount' : 'Mount'} ${disk.label}`,
            repairAllowed =
              disk.health?.repairable === true &&
              operation.status !== 'running' &&
              !diskBusy,
            repairControl = disk.health?.repairable
              ? `<button class="disk-device-action repair" type="button" data-disk-action="repair" data-disk-label="${esc(disk.label)}" title="Safely unmount, repair, verify, and restore the prior mount state for ${esc(disk.label)}" ${repairAllowed ? '' : 'disabled'}>Repair</button>`
              : '',
            usbPort = disk.attached ? diskUsbPowerPort(disk.label) : null,
            usbResetAllowed =
              Boolean(usbPort) &&
              !disk.mounted &&
              usbPort.enabled !== false &&
              !(usbPort.mounted_labels || []).length &&
              usbPortStatus?.operation?.status !== 'running' &&
              !usbPortBusy &&
              operation.status !== 'running' &&
              !diskBusy,
            usbResetTitle = disk.mounted
              ? `Safely unmount ${disk.label} before resetting its USB power`
              : `Power-cycle the independently controlled USB port for ${disk.label}; use this for device-offline or USB transport faults`,
            usbResetControl = usbPort
              ? `<button class="disk-device-action usb-reset" type="button" data-usb-port-action="cycle" data-usb-port-key="${esc(usbPort.key)}" data-usb-port-label="${esc(`USB port for ${disk.label}`)}" title="${esc(usbResetTitle)}" ${usbResetAllowed ? '' : 'disabled'}>Reset USB</button>`
              : '',
            control = disk.controllable
              ? `<span class="disk-device-controls"><button class="disk-device-action ${action}" type="button" data-disk-action="${action}" data-disk-label="${esc(disk.label)}" title="${esc(actionTitle)}" ${actionAllowed ? '' : 'disabled'}>${action === 'eject' ? 'Unmount' : 'Mount'}</button>${usbResetControl}${repairControl}</span>`
              : '<span class="disk-device-role">Backup-managed</span>',
            holdData = state.holdSeconds
              ? ` data-disk-hold-until="${Number(disk.hold_until)}"`
              : '',
            stateControl = detail.currentError
              ? `<button class="disk-device-state disk-error-trigger" type="button" data-disk-error="${esc(detail.currentError)}" data-disk-error-label="${esc(disk.label)}" title="${esc(detail.currentError)}">${esc(state.label)}</button>`
              : `<span class="disk-device-state"${holdData}>${esc(state.label)}</span>`;
          return `<article class="disk-device-card ${state.className}"><strong class="disk-device-name">${esc(disk.label)}</strong>${stateControl}<span class="disk-device-detail" title="${esc([detail.primary, detail.context].filter(Boolean).join(' · '))}"><span>${esc(detail.primary)}</span>${detail.context ? `<small>${esc(detail.context)}</small>` : ''}</span>${control}</article>`;
        })
        .join('')
    : '<div class="disk-device-empty">No configured disk labels were returned.</div>';
}
function renderDiskStatusUnavailable(message) {
  diskStatus = null;
  $('disk-operation').textContent = message || 'Unavailable';
  $('disk-device-list').innerHTML =
    '<div class="disk-device-empty">Individual disk status unavailable.</div>';
}
async function refreshDiskStatus(showErrors = false) {
  try {
    const response = await json('/api/disks');
    renderDiskStatus(response.disk_status);
    return response;
  } catch (error) {
    renderDiskStatusUnavailable(error.message);
    if (showErrors) toast(error.message, true);
    throw error;
  }
}
function updateDiskHoldCountdowns() {
  document.querySelectorAll('[data-disk-hold-until]').forEach((element) => {
    const remaining = Math.max(
      0,
      Math.ceil(Number(element.dataset.diskHoldUntil) - Date.now() / 1000),
    );
    if (remaining) {
      element.textContent = `UNMOUNTED · ${remaining}s`;
    } else {
      element.textContent = 'UNMOUNTED';
      delete element.dataset.diskHoldUntil;
      element.closest('.disk-device-card')?.classList.replace('held', 'bad');
    }
  });
}
async function changeDiskAction(button) {
  if (diskBusy || busy) return;
  const actionName = button.dataset.diskAction,
    label = button.dataset.diskLabel,
    disk = diskStatus?.disks?.find((item) => item.label === label),
    unmountResult =
      disk?.automatic_mount
        ? 'Automatic mounting will resume in one minute.'
        : 'It will stay unmounted until requested here or by the backup tools.';
  if (actionName === 'eject') {
    if (
      !window.confirm(
        `Unmount ${label}? Active disk users will be stopped safely. ${unmountResult}`,
      )
    )
      return;
  } else if (
    actionName === 'repair' &&
    !window.confirm(
      `Repair ${label}? This will disconnect disk users, safely unmount it if needed, run automatic ${disk?.filesystem || 'filesystem'} repair, verify it read-only, and restore its previous mounted/unmounted state only after verification succeeds. This can take a long time.`,
    )
  ) {
    return;
  }
  diskBusy = true;
  if (diskStatus) renderDiskStatus(diskStatus);
  let actionError = null;
  try {
    const response = await post('disks/action', { label, action: actionName });
    renderDiskStatus(response.disk_status);
    toast(response.message);
  } catch (error) {
    actionError = error;
    toast(error.message, true);
  }
  try {
    await refreshDiskStatus(false);
  } catch (refreshError) {
    if (!actionError) toast(refreshError.message, true);
  } finally {
    diskBusy = false;
    if (diskStatus) renderDiskStatus(diskStatus);
  }
}
function lightingDotClass(state) {
  return state === 'on' ? 'good' : state === 'off' ? 'bad' : '';
}
const LIGHTING_QUICK_GROUPS = new Set(['cab', 'rear', 'kitchen']);
const LIGHTING_HUE_MODES = new Set(['hs', 'rgb', 'rgbw', 'rgbww', 'xy']);
function lightingGroupLevel(group) {
  const levels = group.lights
    .filter((light) => light.available && Number.isFinite(light.brightness))
    .map((light) => light.brightness);
  if (!levels.length) return 100;
  return Math.round(levels.reduce((total, level) => total + level, 0) / levels.length);
}
function renderLightingQuick(next) {
  for (const groupId of LIGHTING_QUICK_GROUPS) {
    const group = next.groups.find((item) => item.id === groupId),
      power = $(`lighting-room-${groupId}-power`),
      slider = $(`lighting-room-${groupId}-slider`),
      state = $(`lighting-room-${groupId}-state`),
      known = Boolean(group?.lights.some((light) => light.available)),
      enabled = group?.state === 'on',
      level = group ? lightingGroupLevel(group) : 100;
    networkState(
      `lighting-room-${groupId}-dot`,
      group?.state === 'on' ? true : group?.state === 'off' ? false : null,
    );
    power.disabled = !known;
    power.dataset.lightValue = String(!enabled);
    power.setAttribute(
      'aria-pressed',
      group?.state === 'on' ? 'true' : group?.state === 'off' ? 'false' : 'mixed',
    );
    state.textContent =
      group?.state === 'on'
        ? 'ON'
        : group?.state === 'off'
          ? 'OFF'
          : known
            ? 'MIXED'
            : '';
    slider.disabled = !known;
    slider.value = level;
    $(`lighting-room-${groupId}-level`).textContent = known ? `${level}%` : '—';
  }
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
  renderLightingQuick(next);
  $('lighting-status').textContent = known ? 'Current state' : 'No available lights';
  $('lighting-panel').setAttribute('aria-busy', 'false');
  $('lighting-groups').innerHTML = next.groups
    .map((group) => {
      const groupKnown = group.lights.some((light) => light.available),
        groupEnabled = group.state === 'on',
        groupAction = groupEnabled ? 'Turn room off' : 'Turn room on',
        powerSwitch = group.power_switch,
        switchEnabled = powerSwitch?.state === 'on',
        switchAction = powerSwitch?.available
          ? `Turn switch ${switchEnabled ? 'off' : 'on'}`
          : 'Switch no data',
        switchControl = powerSwitch
          ? `<button class="lighting-switch-action ${lightingDotClass(powerSwitch.state)}" data-action data-light-target="${esc(powerSwitch.entity_id)}" data-light-value="${String(!switchEnabled)}" ${powerSwitch.available ? '' : 'disabled'} aria-pressed="${powerSwitch.available ? String(switchEnabled) : 'mixed'}">${switchAction}</button>`
          : '';
      const rows = group.lights
        .map((light) => {
          const enabled = light.state === 'on',
            level = Number.isFinite(light.brightness) ? light.brightness : 100,
            stateLabel = enabled ? 'ON' : light.state === 'off' ? 'OFF' : 'NO DATA',
            hue = Number.isFinite(light.hue) ? Math.round(light.hue) : 0,
            minKelvin = Number.isFinite(light.min_color_temp_kelvin)
              ? light.min_color_temp_kelvin
              : 2000,
            maxKelvin = Number.isFinite(light.max_color_temp_kelvin)
              ? light.max_color_temp_kelvin
              : 7000,
            kelvin = Number.isFinite(light.color_temp_kelvin)
              ? Math.max(minKelvin, Math.min(maxKelvin, light.color_temp_kelvin))
              : Math.round((minKelvin + maxKelvin) / 2),
            hueControl = light.supports_hue
              ? `<label class="lighting-color-control ${LIGHTING_HUE_MODES.has(light.color_mode) ? 'active' : ''}">
                  <span>Hue</span>
                  <input class="lighting-color-slider lighting-hue-slider" data-action data-light-hue="${esc(light.entity_id)}" type="range" min="0" max="360" value="${hue}" ${light.available ? '' : 'disabled'} aria-label="${esc(light.label)} hue">
                  <output class="lighting-color-value">${light.available ? `${hue}°` : '—'}</output>
                </label>`
              : '',
            temperatureControl = light.supports_color_temperature
              ? `<label class="lighting-color-control ${light.color_mode === 'color_temp' ? 'active' : ''}">
                  <span>Temperature</span>
                  <input class="lighting-color-slider lighting-temperature-slider" data-action data-light-temperature="${esc(light.entity_id)}" type="range" min="${minKelvin}" max="${maxKelvin}" step="1" value="${kelvin}" ${light.available ? '' : 'disabled'} aria-label="${esc(light.label)} color temperature">
                  <output class="lighting-color-value">${light.available ? `${kelvin} K` : '—'}</output>
                </label>`
              : '',
            colorControls = hueControl || temperatureControl
              ? `<div class="lighting-color-controls">${hueControl}${temperatureControl}</div>`
              : '';
          return `<div class="lighting-row">
            <span class="lighting-bulb ${enabled ? 'on' : ''}" aria-hidden="true">●</span>
            <strong>${esc(light.label)}</strong>
            <button class="lighting-power ${lightingDotClass(light.state)}" data-action data-light-target="${esc(light.entity_id)}" data-light-value="${String(!enabled)}" ${light.available ? '' : 'disabled'} aria-pressed="${light.available ? String(enabled) : 'mixed'}">${stateLabel}</button>
            <input class="lighting-slider" data-action data-light-brightness="${esc(light.entity_id)}" type="range" min="1" max="100" value="${level}" ${light.available ? '' : 'disabled'} aria-label="${esc(light.label)} brightness">
            <span class="lighting-level">${light.available ? `${level}%` : '—'}</span>
            ${colorControls}
          </div>`;
        })
        .join('');
      return `<section class="lighting-group">
        <div class="lighting-group-head"><h3><span class="network-dot ${lightingDotClass(group.state)}"></span>${esc(group.label)}</h3><div class="lighting-group-actions"><button data-action data-light-target="group:${esc(group.id)}" data-light-value="${String(!groupEnabled)}" ${groupKnown ? '' : 'disabled'}>${groupAction}</button>${switchControl}</div></div>
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
  renderLightingQuick({ groups: [] });
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
async function changeLightHue(entity, hue) {
  try {
    const result = await post('lights/hue', { entity, hue });
    renderLighting(result.lighting);
    return result;
  } catch (error) {
    await refreshLighting(false).catch(() => {});
    throw error;
  }
}
async function changeLightColorTemperature(entity, kelvin) {
  try {
    const result = await post('lights/color-temperature', { entity, kelvin });
    renderLighting(result.lighting);
    return result;
  } catch (error) {
    await refreshLighting(false).catch(() => {});
    throw error;
  }
}
async function changeLightGroupBrightness(groupId, brightness) {
  if (!LIGHTING_QUICK_GROUPS.has(groupId)) throw new Error('Unknown quick lighting room');
  const group = lighting?.groups.find((item) => item.id === groupId),
    entities = group?.lights
      .filter((light) => light.available)
      .map((light) => light.entity_id);
  if (!entities?.length) throw new Error('No available lights in this room');
  let result;
  try {
    for (const entity of entities) {
      result = await post('lights/brightness', { entity, brightness });
    }
    renderLighting(result.lighting);
    return {
      ...result,
      message: `${group.label} brightness set to ${Number(brightness)}%`,
    };
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
  $('system-uptime').textContent = formatUptime(data.system_uptime?.seconds);
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
}
async function refresh() {
  try {
    updateStatus(await json('/api/status'));
  } catch (_) {}
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
async function pollOpenwrtClients() {
  clearTimeout(openwrtPoll);
  if (!$('openwrt-backdrop').classList.contains('open')) return;
  try {
    await refreshOpenwrtClients(false);
  } finally {
    openwrtPoll = setTimeout(pollOpenwrtClients, 20000);
  }
}
async function openOpenwrt() {
  $('openwrt-backdrop').classList.add('open');
  document.body.classList.add('sheet-open');
  $('openwrt-open').setAttribute('aria-expanded', 'true');
  renderOpenwrtPanel(connectivityState);
  await Promise.allSettled([refreshConnectivity(), refreshOpenwrtClients(true)]);
  openwrtPoll = setTimeout(pollOpenwrtClients, 20000);
}
function closeOpenwrt() {
  clearTimeout(openwrtPoll);
  $('openwrt-backdrop').classList.remove('open');
  document.body.classList.remove('sheet-open');
  $('openwrt-open').setAttribute('aria-expanded', 'false');
  $('openwrt-open').focus();
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
    if (!busy && !diskBusy)
      await Promise.allSettled([refreshStoragePolicy(true), refreshDiskStatus(false)]);
  } catch (_) {
    /* rendered by the individual refreshers */
  } finally {
    storagePoll = setTimeout(pollStorage, 2500);
  }
}
async function openStorage() {
  $('storage-backdrop').classList.add('open');
  document.body.classList.add('sheet-open');
  $('storage').setAttribute('aria-expanded', 'true');
  try {
    await Promise.allSettled([refreshStoragePolicy(), refreshDiskStatus(true)]);
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
async function pollComputeMetrics() {
  clearTimeout(computePoll);
  if (!$('compute-backdrop').classList.contains('open')) return;
  try {
    await refreshComputeMetrics(false);
  } catch (_) {
    /* rendered by refreshComputeMetrics */
  } finally {
    computePoll = setTimeout(pollComputeMetrics, 10000);
  }
}
async function openComputeMetrics() {
  $('compute-backdrop').classList.add('open');
  document.body.classList.add('sheet-open');
  $('compute-worker').setAttribute('aria-expanded', 'true');
  $('compute-panel').setAttribute('aria-busy', 'true');
  await refreshComputeMetrics(true).catch(() => {});
  computePoll = setTimeout(pollComputeMetrics, 10000);
}
function closeComputeMetrics() {
  clearTimeout(computePoll);
  collapseComputeJobDetails(true);
  clearComputeTaskFilter(true);
  $('compute-backdrop').classList.remove('open');
  document.body.classList.remove('sheet-open');
  $('compute-worker').setAttribute('aria-expanded', 'false');
  $('compute-worker').focus();
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
async function pollIgnitionMonitor() {
  clearTimeout(ignitionMonitorPoll);
  if (!$('ignition-monitor-backdrop').classList.contains('open')) return;
  try {
    await refreshIgnitionMonitor(false);
  } catch (_) {
    /* rendered by refreshIgnitionMonitor */
  } finally {
    ignitionMonitorPoll = setTimeout(pollIgnitionMonitor, 5000);
  }
}
async function openIgnitionMonitor() {
  $('ignition-monitor-backdrop').classList.add('open');
  document.body.classList.add('sheet-open');
  $('ignition-monitor').setAttribute('aria-expanded', 'true');
  $('ignition-monitor-panel').setAttribute('aria-busy', 'true');
  try {
    await refreshIgnitionMonitor(true);
  } finally {
    ignitionMonitorPoll = setTimeout(pollIgnitionMonitor, 5000);
  }
}
function closeIgnitionMonitor() {
  clearTimeout(ignitionMonitorPoll);
  $('ignition-monitor-backdrop').classList.remove('open');
  document.body.classList.remove('sheet-open');
  $('ignition-monitor').setAttribute('aria-expanded', 'false');
  $('ignition-monitor').focus();
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
  $('ubnt-wifi-open').setAttribute('aria-expanded', 'true');
  await refreshUbntWifi(true);
}
function closeUbntWifi() {
  $('ubnt-wifi-backdrop').classList.remove('open');
  document.body.classList.remove('sheet-open');
  $('ubnt-wifi-open').setAttribute('aria-expanded', 'false');
  clearUbntPassword();
  $('ubnt-wifi-open').focus();
}
function siblingServiceUrl(port) {
  const url = new URL(window.location.href);
  url.port = String(port);
  url.pathname = '/';
  url.search = '';
  url.hash = '';
  return url.toString();
}

setupTileEditing();
setIgnitionDuration(120, 'hours');
$('books').href = siblingServiceUrl(8787);
$('video-library').href = siblingServiceUrl(8789);
$('telemetry-open').href = siblingServiceUrl(8765);
$('telemetry-check').addEventListener('click', requestVoltageCheck);
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
document.querySelectorAll('[data-system-power]').forEach((button) => {
  button.addEventListener('click', () => requestSystemPower(button.dataset.systemPower));
});
$('openwrt-open').addEventListener('click', openOpenwrt);
$('openwrt-close').addEventListener('click', closeOpenwrt);
$('openwrt-clients-refresh').addEventListener('click', () =>
  refreshOpenwrtClients(true),
);
$('speakers').addEventListener('click', openSpeakers);
$('speaker-close').addEventListener('click', closeSpeakers);
$('storage').addEventListener('click', openStorage);
$('storage-close').addEventListener('click', closeStorage);
$('system-monitor').addEventListener('click', openSystemMonitor);
$('system-monitor-close').addEventListener('click', closeSystemMonitor);
$('compute-worker').addEventListener('click', openComputeMetrics);
$('compute-close').addEventListener('click', closeComputeMetrics);
$('monitor-crash-analyze').addEventListener('click', analyzePreviousCrash);
$('usb-devices').addEventListener('click', openUsbDevices);
$('usb-close').addEventListener('click', closeUsbDevices);
$('usb-recover').addEventListener('click', recoverUsb2);
$('backups').addEventListener('click', openBackups);
$('backup-close').addEventListener('click', closeBackups);
$('backup-run-borg').addEventListener('click', () => startManualBackup('borg'));
$('backup-run-exfat').addEventListener('click', () => startManualBackup('exfat'));
$('ignition-monitor').addEventListener('click', openIgnitionMonitor);
$('ignition-monitor-close').addEventListener('click', closeIgnitionMonitor);
$('ignition-monitor-disable').addEventListener('click', () => changeIgnitionMonitor(true));
$('ignition-monitor-enable').addEventListener('click', () => changeIgnitionMonitor(false));
$('ignition-duration-amount').addEventListener('input', (event) =>
  readIgnitionDurationInput(event.target),
);
$('ignition-duration-slider').addEventListener('input', (event) =>
  readIgnitionDurationInput(event.target),
);
$('ignition-duration-unit').addEventListener('change', (event) =>
  setIgnitionDuration(ignitionDurationMinutes, event.target.value),
);
$('price-checks').addEventListener('click', openPriceChecks);
$('price-close').addEventListener('click', closePriceChecks);
$('price-check-all').addEventListener('click', () => checkPrices('all'));
$('price-schedule').addEventListener('input', () => {
  const expression = normalizedPriceSchedule();
  if (expression) beginPriceScheduleParse(expression);
  else {
    clearPriceScheduleTimers();
    priceScheduleRequestId += 1;
    showPriceScheduleError('empty cron expression');
  }
});
$('price-schedule-form').addEventListener('submit', (event) => {
  event.preventDefault();
  savePriceSchedule();
});
$('price-edit-cancel').addEventListener('click', resetPriceForm);
$('price-add-form').addEventListener('submit', (event) => {
  event.preventDefault();
  addPriceCheck();
});
$('price-search-add-form').addEventListener('submit', (event) => {
  event.preventDefault();
  addSavedSearch();
});
$('lighting').addEventListener('click', openLighting);
$('lighting-close').addEventListener('click', closeLighting);
$('lighting-master').dataset.lightTarget = 'all';
$('ubnt-wifi-open').addEventListener('click', openUbntWifi);
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
$('openwrt-backdrop').addEventListener('click', (event) => {
  if (event.target === $('openwrt-backdrop')) closeOpenwrt();
});
$('speaker-backdrop').addEventListener('click', (event) => {
  if (event.target === $('speaker-backdrop')) closeSpeakers();
});
$('storage-backdrop').addEventListener('click', (event) => {
  if (event.target === $('storage-backdrop')) closeStorage();
});
$('system-monitor-backdrop').addEventListener('click', (event) => {
  if (event.target === $('system-monitor-backdrop')) closeSystemMonitor();
});
$('compute-backdrop').addEventListener('click', (event) => {
  if (event.target === $('compute-backdrop')) closeComputeMetrics();
});
$('usb-backdrop').addEventListener('click', (event) => {
  if (event.target === $('usb-backdrop')) closeUsbDevices();
});
$('backup-backdrop').addEventListener('click', (event) => {
  if (event.target === $('backup-backdrop')) closeBackups();
});
$('ignition-monitor-backdrop').addEventListener('click', (event) => {
  if (event.target === $('ignition-monitor-backdrop')) closeIgnitionMonitor();
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
    if ($('openwrt-backdrop').classList.contains('open')) closeOpenwrt();
    if ($('speaker-backdrop').classList.contains('open')) closeSpeakers();
    if ($('storage-backdrop').classList.contains('open')) closeStorage();
    if ($('system-monitor-backdrop').classList.contains('open')) closeSystemMonitor();
    if ($('compute-backdrop').classList.contains('open')) closeComputeMetrics();
    if ($('usb-backdrop').classList.contains('open')) closeUsbDevices();
    if ($('backup-backdrop').classList.contains('open')) closeBackups();
    if ($('ignition-monitor-backdrop').classList.contains('open')) closeIgnitionMonitor();
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
  const roomSlider = event.target.closest('[data-light-group-brightness]');
  if (roomSlider)
    roomSlider.closest('.lighting-quick-row').querySelector('.lighting-quick-level').textContent =
      `${roomSlider.value}%`;
  const hueSlider = event.target.closest('[data-light-hue]');
  if (hueSlider)
    hueSlider.closest('.lighting-color-control').querySelector('.lighting-color-value').textContent =
      `${hueSlider.value}°`;
  const temperatureSlider = event.target.closest('[data-light-temperature]');
  if (temperatureSlider)
    temperatureSlider.closest('.lighting-color-control').querySelector('.lighting-color-value').textContent =
      `${temperatureSlider.value} K`;
});
document.addEventListener('change', (event) => {
  const lightSlider = event.target.closest('[data-light-brightness]');
  if (lightSlider)
    action(() =>
      changeLightBrightness(lightSlider.dataset.lightBrightness, lightSlider.value),
    );
  const roomSlider = event.target.closest('[data-light-group-brightness]');
  if (roomSlider)
    action(() =>
      changeLightGroupBrightness(
        roomSlider.dataset.lightGroupBrightness,
        roomSlider.value,
      ),
    );
  const hueSlider = event.target.closest('[data-light-hue]');
  if (hueSlider)
    action(() => changeLightHue(hueSlider.dataset.lightHue, hueSlider.value));
  const temperatureSlider = event.target.closest('[data-light-temperature]');
  if (temperatureSlider)
    action(() =>
      changeLightColorTemperature(
        temperatureSlider.dataset.lightTemperature,
        temperatureSlider.value,
      ),
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
  const diskError = event.target.closest('[data-disk-error]');
  if (diskError)
    window.alert(
      `${diskError.dataset.diskErrorLabel || 'Disk'} current error\n\n${diskError.dataset.diskError}`,
    );
  const computeJobDetails = event.target.closest('[data-compute-job-details]');
  if (computeJobDetails) toggleComputeJobDetails(computeJobDetails);
  const computeTask = event.target.closest('[data-compute-task-filter]');
  if (computeTask) toggleComputeTaskFilter(computeTask);
  const ignitionPreset = event.target.closest('[data-ignition-preset]');
  if (ignitionPreset) setIgnitionDuration(Number(ignitionPreset.dataset.ignitionPreset));
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
  const computeRange = event.target.closest('[data-compute-hours]');
  if (computeRange) {
    const hours = Number(computeRange.dataset.computeHours);
    if (Number.isFinite(hours) && hours !== computeHours) {
      computeHours = hours;
      collapseComputeJobDetails(true);
      clearComputeTaskFilter(true);
      document.querySelectorAll('[data-compute-hours]').forEach((button) => {
        button.disabled = true;
      });
      refreshComputeMetrics(true).catch(() => {});
    }
  }
  const priceCheck = event.target.closest('[data-price-check]');
  if (priceCheck) checkPrices(priceCheck.dataset.priceCheck);
  const priceEdit = event.target.closest('[data-price-edit]');
  if (priceEdit) editPriceCheck(priceEdit.dataset.priceEdit);
  const priceMute = event.target.closest('[data-price-mute]');
  if (priceMute) mutePriceCheck(priceMute.dataset.priceMute);
  const priceRemove = event.target.closest('[data-price-remove]');
  if (
    priceRemove &&
    window.confirm(`Remove ${priceRemove.dataset.priceTitle || 'this price check'}?`)
  )
    priceAction(() => post('price-checks/remove', { id: priceRemove.dataset.priceRemove }));
  const searchCheck = event.target.closest('[data-search-check]');
  if (searchCheck) checkSavedSearch(searchCheck.dataset.searchCheck);
  const searchDismiss = event.target.closest('[data-search-dismiss]');
  if (
    searchDismiss &&
    window.confirm(
      `Permanently hide ${searchDismiss.dataset.searchResultTitle || 'this result'}?`,
    )
  )
    priceAction(() =>
      post('price-checks/searches/dismiss', {
        id: searchDismiss.dataset.searchDismiss,
        item_id: searchDismiss.dataset.searchItemId,
      }),
    );
  const searchRemove = event.target.closest('[data-search-remove]');
  if (
    searchRemove &&
    window.confirm(`Remove ${searchRemove.dataset.searchTitle || 'this saved search'}?`)
  )
    priceAction(() =>
      post('price-checks/searches/remove', { id: searchRemove.dataset.searchRemove }),
    );
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
  const diskAction = event.target.closest('[data-disk-action]');
  if (diskAction) changeDiskAction(diskAction);
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
  if ($('openwrt-backdrop').classList.contains('open')) refreshOpenwrtClients(false);
  refreshSpeedtest();
  refreshSonos();
  refreshStoragePolicy().catch(() => {});
  refreshDiskStatus(false).catch(() => {});
  refreshSystemMonitor(false).catch(() => {});
  refreshComputeMetrics(false).catch(() => {});
  refreshUsbDevices(false).catch(() => {});
  refreshBackups(false).catch(() => {});
  refreshIgnitionMonitor(false).catch(() => {});
  refreshPriceChecks().catch(() => {});
  refreshLighting(false).catch(() => {});
  refreshUbntWifi(false);
  refreshTelemetrySummary();
}
setupTruncationTitles();
Promise.allSettled([
  refresh(),
  loadSpeakers(),
  refreshConnectivity(),
  refreshSpeedtest(),
  refreshStoragePolicy(),
  refreshDiskStatus(false),
  refreshSystemMonitor(false),
  refreshComputeMetrics(false),
  refreshUsbDevices(false),
  refreshBackups(false),
  refreshIgnitionMonitor(false),
  refreshPriceChecks(),
  refreshLighting(false),
  refreshUbntWifi(false),
  refreshTelemetrySummary(),
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
  if (!document.hidden) refreshTelemetrySummary();
}, 15000);
setInterval(() => {
  if (!document.hidden && !busy) refreshSonos();
}, 10000);
setInterval(() => {
  if (!document.hidden && !busy && !diskBusy) {
    refreshStoragePolicy().catch(() => {});
    refreshDiskStatus(false).catch(() => {});
  }
}, 30000);
setInterval(() => {
  if (!document.hidden) refreshSystemMonitor(false).catch(() => {});
}, 30000);
setInterval(() => {
  if (!document.hidden && !$('compute-backdrop').classList.contains('open'))
    refreshComputeMetrics(false).catch(() => {});
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
  if (!document.hidden && !$('ignition-monitor-backdrop').classList.contains('open'))
    refreshIgnitionMonitor(false).catch(() => {});
}, 15000);
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
  if (!document.hidden) {
    updateSonosProgress();
    updateDiskHoldCountdowns();
  }
}, 1000);
document.addEventListener('visibilitychange', refreshVisibleDashboard);
window.addEventListener('pageshow', refreshVisibleDashboard);
window.addEventListener('focus', refreshVisibleDashboard);
