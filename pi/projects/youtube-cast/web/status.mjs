const element = id => document.getElementById(id);
const refresh = element('refresh');

async function update() {
  try {
    const response = await fetch('/api/status', { cache: 'no-store' });
    if (!response.ok) throw new Error('Status unavailable');
    const data = await response.json();
    element('receiver').textContent = data.receiver;
    element('player').textContent = data.player;
    element('video').textContent = data.video?.title || 'None';
    element('position').textContent = `${Math.floor(data.position)} / ${Math.floor(data.duration)} seconds`;
    element('senders').textContent = String(data.senders);
    element('code').textContent = data.pairing.code || (data.pairing.error ? 'Unavailable' : 'Requesting code…');
    element('message').textContent = data.pairing.error || '';
    refresh.disabled = data.pairing.requesting;
  } catch {
    element('receiver').textContent = 'Offline';
    element('code').textContent = 'Unavailable';
    element('message').textContent = 'Cannot reach Van TV. Retrying…';
    refresh.disabled = false;
  }
}

refresh.addEventListener('click', async () => {
  refresh.disabled = true;
  try {
    const response = await fetch('/api/pairing', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    if (!response.ok) throw new Error('Refresh failed');
    await update();
  } catch { element('message').textContent = 'Cannot request a new code'; refresh.disabled = false; }
});
for (const button of document.querySelectorAll('button[data-action]')) {
  button.addEventListener('click', async () => {
    try {
      const response = await fetch('/api/control', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: button.dataset.action }) });
      if (!response.ok) throw new Error('Control failed');
      await update();
    } catch { element('message').textContent = `Cannot ${button.dataset.action} playback`; }
  });
}
await update();
setInterval(update, 2000);
