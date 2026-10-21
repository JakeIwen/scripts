const config = globalThis.VAN_TV_CONFIG;
const buttonId = 'van-tv-cast-button';
const defaultLabel = 'Cast to Van TV';
let scheduled = false;
let feedbackTimer;

function currentVideo() {
  const url = new URL(location.href);
  const videoId = url.searchParams.get('v');
  if (url.pathname !== config.youtube.watchPath || !videoId
      || !config.youtube.videoIdPattern.test(videoId)) return null;
  return { videoId, position: currentPosition() ?? 0 };
}

function playbackVideo() {
  return document.querySelector('ytd-reel-video-renderer[is-active] video')
    ?? document.querySelector('#movie_player video') ?? document.querySelector('video');
}

function currentPosition() {
  const time = playbackVideo()?.currentTime;
  return Number.isFinite(time) && time >= 0 ? time : null;
}

function feedback(button, label, status) {
  clearTimeout(feedbackTimer);
  button.title = label;
  button.setAttribute('aria-label', label);
  button.classList.toggle('van-tv-success', status === 'success');
  button.classList.toggle('van-tv-error', status === 'error');
  if (status !== 'success') return;
  feedbackTimer = setTimeout(() => {
    button.title = defaultLabel;
    button.setAttribute('aria-label', defaultLabel);
    button.classList.remove('van-tv-success', 'van-tv-error');
  }, config.feedbackDurationMs);
}

function pauseCurrent(videoId, expectedUrl) {
  if (expectedUrl ? location.href !== expectedUrl : currentVideo()?.videoId !== videoId) return;
  playbackVideo()?.pause();
  const button = document.getElementById(buttonId);
  if (button) feedback(button, 'Sent to Van TV', 'success');
}

async function cast(button) {
  const video = currentVideo();
  if (!video || button.disabled) return;
  button.disabled = true;
  feedback(button, 'Sending to Van TV…', 'pending');
  try {
    const result = await browser.runtime.sendMessage({ type: config.message.cast, ...video });
    if (!result?.ok) throw new Error(result?.error || 'Van TV request failed');
    pauseCurrent(video.videoId);
  } catch (error) {
    if (currentVideo()?.videoId === video.videoId) feedback(button, error.message, 'error');
  } finally { button.disabled = false; }
}

function makeButton() {
  const button = document.createElement('button');
  button.id = buttonId;
  button.type = 'button';
  button.className = 'ytp-button van-tv-button';
  button.title = defaultLabel;
  button.setAttribute('aria-label', defaultLabel);
  // Fixed markup, not video titles or other page-provided HTML.
  button.innerHTML = '<svg viewBox="0 0 36 36" aria-hidden="true">'
    + '<path d="M7 8h22v17H19v-3h7V11H10v5H7V8zm0 19v-4a4 4 0 0 1 4 4H7zm0-7v-3'
    + 'a10 10 0 0 1 10 10h-3a7 7 0 0 0-7-7z"/></svg>';
  button.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    void cast(button);
  });
  return button;
}

function ensureButton() {
  scheduled = false;
  const existing = document.getElementById(buttonId);
  const video = currentVideo();
  if (!video) { existing?.remove(); return; }
  const controls = document.querySelector('.ytp-right-controls');
  if (!controls) return;
  const button = existing ?? makeButton();
  if (button.dataset.videoId !== video.videoId) {
    button.dataset.videoId = video.videoId;
    feedback(button, defaultLabel, 'idle');
  }
  if (button.parentElement !== controls) controls.prepend(button);
}

function scheduleButton() {
  if (scheduled) return;
  scheduled = true;
  requestAnimationFrame(ensureButton);
}

browser.runtime.onMessage.addListener(message => {
  if (message?.type === config.message.getVideo) {
    // The background parsed this exact URL; avoid another URL parser here and
    // refuse a stale selection if YouTube navigated while the message travelled.
    return Promise.resolve(location.href === message.url
      ? { videoId: message.videoId, position: currentPosition() } : null);
  }
  if (message?.type === config.message.castAccepted) {
    pauseCurrent(message.videoId, message.url);
    return Promise.resolve();
  }
  return undefined;
});
document.addEventListener('yt-navigate-finish', scheduleButton);
new MutationObserver(scheduleButton).observe(document.documentElement, { childList: true, subtree: true });
ensureButton();
