const $ = (id) => document.getElementById(id);

let library = null;
let playerStatus = null;
let currentView = "home";
let searchMatches = null;
let searchTimer = 0;
let searchSerial = 0;
let busy = false;
let toastTimer = 0;
let currentShow = null;
let statusRefreshRunning = false;
let statusReceivedAt = 0;

const SUBTITLE_KEY = "van-video-library.subtitles.v1";

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[character]));
}

function siblingServiceUrl(port) {
  const url = new URL(window.location.href);
  url.port = String(port);
  url.pathname = "/";
  url.search = "";
  url.hash = "";
  return url.toString();
}

function toast(message, bad) {
  clearTimeout(toastTimer);
  const element = $("toast");
  element.textContent = message;
  element.className = bad ? "show bad" : "show";
  toastTimer = setTimeout(() => { element.className = ""; }, 3300);
}

async function json(url, options) {
  const response = await fetch(url, Object.assign({ cache: "no-store" }, options || {}));
  let payload;
  try {
    payload = await response.json();
  } catch (_) {
    payload = { message: "Server returned " + response.status };
  }
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.message || "Request failed (" + response.status + ")");
  }
  return payload;
}

async function post(endpoint, parameters) {
  return json("/api/" + endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      "X-Van-Video": "1",
    },
    body: new URLSearchParams(parameters || {}),
  });
}

async function action(work, options) {
  if (busy) return;
  busy = true;
  document.body.classList.add("busy");
  try {
    const result = await work();
    if (result && result.message) toast(result.message, false);
    if (options && options.library) await loadLibrary();
    await refreshStatus(true);
    return result;
  } catch (error) {
    toast(error.message, true);
  } finally {
    busy = false;
    document.body.classList.remove("busy");
  }
}

function subtitleMode() {
  return localStorage.getItem(SUBTITLE_KEY) === "off" ? "off" : "auto";
}

function renderSubtitleMode() {
  const enabled = subtitleMode() !== "off";
  $("subtitles").setAttribute("aria-pressed", String(enabled));
  $("subtitles").title = enabled
    ? "English subtitles on the next video"
    : "Subtitles off on the next video";
}

function hue(id) {
  return parseInt(String(id || "0").slice(0, 6), 16) % 360;
}

function itemHeading(item) {
  if (item.type === "episode") return item.series || item.title;
  return item.title;
}

function itemMeta(item) {
  const progress = item.progress;
  let label = "";
  if (item.type === "episode") {
    label = [item.episode_code, item.episode_title].filter(Boolean).join(" · ");
  } else {
    label = [item.type === "documentary" ? "Documentary" : "Movie", item.year].filter(Boolean).join(" · ");
  }
  if (progress) {
    if (progress.finished) label += (label ? " · " : "") + "Watched";
    else if (progress.position) label += (label ? " · " : "") + progress.position_text;
  }
  return label || "Ready to play";
}

function progressBar(progress) {
  if (!progress || progress.finished || !progress.position) return "";
  const percentage = progress.fraction == null ? 8 : Math.max(2, Math.min(100, progress.fraction * 100));
  return '<div class="mini-progress"><span style="--progress:' + percentage.toFixed(1) + '%"></span></div>';
}

function mediaCard(value, options) {
  const isShow = value.type === "show";
  const item = isShow ? value.next : value;
  const cardId = isShow ? value.id : item.id;
  const title = isShow ? value.name : itemHeading(item);
  let meta;
  if (isShow) {
    meta = itemMeta(item) + " · " + value.watched + "/" + value.episodes + " watched";
  } else {
    meta = itemMeta(item);
  }
  const progress = item && item.progress;
  const icon = isShow ? "▤" : (item.type === "episode" ? "▶" : "◆");
  const isNew = isShow ? value.new : item.new;
  let actions = "";
  if (isShow) {
    actions += '<button type="button" data-show="' + esc(value.id) + '" aria-label="Browse episodes for ' + esc(title) + '">☷</button>';
  } else {
    if (progress && progress.position) {
      actions += '<button type="button" data-restart="' + esc(item.id) + '" aria-label="Restart ' + esc(title) + '">↺</button>';
    }
    actions += '<button type="button" class="' + (progress && progress.finished ? "watched" : "") +
      '" data-watch-item="' + esc(item.id) + '" data-watch-action="' +
      (progress && progress.finished ? "unwatched" : "watched") +
      '" aria-label="' + (progress && progress.finished ? "Mark unwatched" : "Mark watched") + '">✓</button>';
  }
  const playAttribute = isShow ? 'data-play-show="' + esc(value.id) + '"' : 'data-play-item="' + esc(item.id) + '"';
  return '<article class="media-card">' +
    '<button type="button" class="media-card-main" ' + playAttribute + '>' +
      '<span class="media-art" style="--hue:' + hue(cardId) + '">' + icon +
        (isNew ? '<span class="new-ribbon">NEW</span>' : "") +
      '</span>' +
      '<span class="media-copy"><span class="media-title">' + esc(title) + '</span>' +
        '<span class="media-meta">' + esc(meta) + '</span>' + progressBar(progress) + '</span>' +
    '</button>' +
    '<span class="media-actions">' + actions + '</span>' +
  '</article>';
}

function section(title, values, options) {
  if (!values || !values.length) return "";
  const horizontal = options && options.horizontal;
  const count = options && options.count ? '<span class="section-count">' + esc(options.count) + '</span>' : "";
  return '<section class="section"><div class="section-head"><h2>' + esc(title) + '</h2>' + count + '</div>' +
    '<div class="' + (horizontal ? "horizontal-list" : "card-list") + '">' +
      values.map((value) => mediaCard(value, options)).join("") +
    '</div></section>';
}

function empty(message) {
  return '<div class="empty-state">' + esc(message) + "</div>";
}

function renderHome() {
  let markup = "";
  if (library.continue.length) {
    markup += section("Continue watching", library.continue, { horizontal: true });
  }
  if (library.up_next.length) {
    markup += section("Up next", library.up_next, { horizontal: true });
  }
  if (library.favorites.length) {
    markup += '<section class="section"><div class="section-head"><h2>Quick picks</h2><span class="section-count">from your BTT menu</span></div>' +
      '<div class="quick-picks">' +
      library.favorites.map((favorite) =>
        '<button type="button" class="quick-pick" data-quick-show="' + esc(favorite.id) +
        '" data-no-subs="' + String(favorite.no_subtitles) + '">⚄ ' + esc(favorite.name) +
        (favorite.no_subtitles ? "<small>CC off</small>" : "") + "</button>"
      ).join("") + "</div></section>";
  }
  markup += '<button type="button" class="surprise" id="surprise">⚄ Surprise me with something unwatched</button>';
  markup += section("New on the drive", library.new.slice(0, 24), { horizontal: true });
  markup += '<div class="library-summary">' +
    library.library.items + " playable files · " + library.library.shows + " shows · " +
    esc(library.library.source || "drive offline") + "</div>";
  return markup || empty("Nothing is indexed yet. Check the media drive, then tap rescan.");
}

function renderSearch() {
  const query = $("search").value.trim().toLowerCase();
  let values = searchMatches;
  if (values == null && library) {
    values = []
      .concat(library.shows, library.movies, library.documentaries)
      .filter((value) => {
        const text = value.type === "show"
          ? value.name
          : [value.title, value.series, value.episode_code, value.episode_title].filter(Boolean).join(" ");
        return query.split(/\s+/).every((token) => text.toLowerCase().includes(token));
      })
      .slice(0, 24);
  }
  return section("Search results", values || [], { count: (values || []).length + " found" }) ||
    empty("No matching movies, shows, or episodes.");
}

function render() {
  const query = $("search").value.trim();
  $("search-wrap").classList.toggle("searching", Boolean(query));
  if (!library) {
    $("content").innerHTML = empty("Reading the media drive…");
    return;
  }
  if (query) {
    $("content").innerHTML = renderSearch();
    return;
  }
  if (!library.library.available) {
    $("content").innerHTML = empty(library.library.error || "The media drive is offline.");
    return;
  }
  if (currentView === "home") $("content").innerHTML = renderHome();
  else if (currentView === "movies") {
    $("content").innerHTML = section("Movies", library.movies, { count: library.movies.length + " titles" }) ||
      empty("No movies found.");
  } else if (currentView === "shows") {
    $("content").innerHTML = section("Shows", library.shows, { count: library.shows.length + " series" }) ||
      empty("No shows found.");
  } else if (currentView === "documentaries") {
    $("content").innerHTML = section("Documentaries", library.documentaries, { count: library.documentaries.length + " titles" }) ||
      empty("No documentaries found.");
  } else if (currentView === "new") {
    $("content").innerHTML = section("New on the drive", library.new, { count: library.new.length + " files" }) ||
      empty("The New folder is empty.");
  }
}

async function loadLibrary() {
  library = await json("/api/library");
  render();
  return library;
}

async function searchLibrary(query, serial) {
  if (!query) {
    searchMatches = null;
    render();
    return;
  }
  try {
    const result = await json("/api/search?q=" + encodeURIComponent(query));
    if (serial === searchSerial) {
      searchMatches = result.matches;
      render();
    }
  } catch (error) {
    if (serial === searchSerial) toast(error.message, true);
  }
}

function playerTitle(player) {
  const item = player.item;
  if (!item) return player.title || "Nothing from this library is playing";
  return item.type === "episode" ? item.series : item.title;
}

function playerMeta(player) {
  const item = player.item;
  if (!item) return player.available ? "VLC is idle" : "Choose something from the library";
  if (item.type === "episode") {
    return [item.episode_code, item.episode_title].filter(Boolean).join(" · ");
  }
  return [item.type === "documentary" ? "Documentary" : "Movie", item.year].filter(Boolean).join(" · ");
}

function syncTimeline(position, duration) {
  if (document.activeElement !== $("timeline")) {
    $("timeline").value = duration ? Math.min(1000, Math.max(0, position / duration * 1000)) : 0;
  }
  $("elapsed").textContent = formatSeconds(position);
  $("remaining").textContent = duration ? "−" + formatSeconds(Math.max(0, duration - position)) : "—";
}

function formatSeconds(value) {
  const total = Math.max(0, Math.floor(Number(value) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor(total % 3600 / 60);
  const seconds = total % 60;
  return (hours ? hours + ":" + String(minutes).padStart(2, "0") : minutes) +
    ":" + String(seconds).padStart(2, "0");
}

function updatePlayer(payload) {
  playerStatus = payload;
  statusReceivedAt = performance.now();
  const player = payload.player || {};
  const audio = payload.audio || {};
  const libraryState = payload.library || {};
  $("connection-dot").className = "dot " + (libraryState.available ? "on" : "bad");
  $("connection").textContent = libraryState.available
    ? "Connected · " + libraryState.source + " · " + libraryState.items + " files"
    : (libraryState.error || "Media drive unavailable");

  $("now-title").textContent = playerTitle(player);
  $("now-meta").textContent = playerMeta(player);
  const state = player.state || "OFFLINE";
  $("transport-state").textContent = state === "PLAYING" ? "Playing" :
    (state === "PAUSED" || state === "PAUSED_PLAYBACK" ? "Paused" :
      (player.available ? "Idle" : "Offline"));
  $("play-pause").textContent = state === "PLAYING" ? "Ⅱ" : "▶";
  $("now-art").textContent = state === "PLAYING" ? "▶" : (state.indexOf("PAUSED") === 0 ? "Ⅱ" : "■");
  syncTimeline(player.position || 0, player.duration || 0);

  document.querySelectorAll("[data-seek]").forEach((button) => {
    button.disabled = !player.available || !player.can_seek;
  });
  document.querySelectorAll("[data-control]").forEach((button) => {
    const control = button.dataset.control;
    let enabled = Boolean(player.available && player.can_control);
    if (control === "next") enabled = enabled && Boolean(player.can_next);
    else if (control === "previous") enabled = enabled && Boolean(player.can_previous);
    else if (control === "toggle") {
      enabled = enabled && Boolean(state === "PLAYING" ? player.can_pause : player.can_play);
    }
    button.disabled = !enabled;
  });
  $("timeline").disabled = !player.available || !player.can_seek || !player.duration;
  $("volume").disabled = !audio.available;
  $("rate").disabled = !player.available || !player.can_control;
  $("fullscreen").disabled = !player.available || !player.can_fullscreen;
  $("volume-source").textContent = audio.preparing ? "Prep…" : (audio.muted ? "Muted" : "Sonos");
  $("volume-source").title = audio.available
    ? (audio.device || "Rear Sonos") + " · VLC fixed at " + audio.vlc_fixed + "%"
    : (audio.error || (audio.preparing ? "Preparing rear movie audio" : "Rear Sonos unavailable"));
  if (document.activeElement !== $("volume") && Number.isFinite(audio.volume)) {
    $("volume").value = Math.round(audio.volume);
    $("volume-value").textContent = Math.round(audio.volume) + "%";
  } else if (!audio.available && document.activeElement !== $("volume")) {
    $("volume-value").textContent = audio.preparing ? "…" : "—";
  }
  if (document.activeElement !== $("rate") && Number.isFinite(player.rate)) {
    const option = Array.from($("rate").options).find((candidate) =>
      Math.abs(Number(candidate.value) - player.rate) < .01
    );
    if (option) $("rate").value = option.value;
  }
  if (!payload.sleep_timer.active && document.activeElement !== $("sleep")) $("sleep").value = "0";
}

function predictTimeline() {
  if (!playerStatus || !playerStatus.player) return;
  const player = playerStatus.player;
  let position = Number(player.position) || 0;
  if (player.state === "PLAYING" && statusReceivedAt) {
    position += (performance.now() - statusReceivedAt) / 1000 * (Number(player.rate) || 1);
  }
  syncTimeline(position, Number(player.duration) || 0);
}

async function refreshStatus(silent) {
  if (statusRefreshRunning) return;
  statusRefreshRunning = true;
  try {
    updatePlayer(await json("/api/status"));
  } catch (error) {
    $("connection-dot").className = "dot bad";
    $("connection").textContent = "Video service unavailable";
    if (!silent) toast(error.message, true);
  } finally {
    statusRefreshRunning = false;
  }
}

function episodeMarkup(episode) {
  const progress = episode.progress;
  const watched = progress && progress.finished;
  const detail = progress
    ? (watched ? "Watched" : progress.position_text)
    : "Ready to play";
  return '<article class="episode-row">' +
    '<button type="button" class="episode-play" data-play-item="' + esc(episode.id) + '">' +
      '<span class="episode-code">' + esc(episode.episode_code || "EP") + '</span>' +
      '<span class="episode-copy"><strong>' + esc(episode.episode_title || episode.title) + '</strong>' +
      '<span>' + esc(detail) + '</span>' + progressBar(progress) + '</span>' +
    '</button>' +
    '<span class="episode-actions">' +
      (progress && progress.position ? '<button type="button" data-restart="' + esc(episode.id) + '" aria-label="Restart episode">↺</button>' : "") +
      '<button type="button" class="' + (watched ? "watched" : "") + '" data-watch-item="' + esc(episode.id) +
      '" data-watch-action="' + (watched ? "unwatched" : "watched") + '" aria-label="' +
      (watched ? "Mark episode unwatched" : "Mark episode watched") + '">✓</button>' +
    '</span></article>';
}

async function openShow(showId) {
  currentShow = await json("/api/shows/" + encodeURIComponent(showId));
  $("show-title").textContent = currentShow.show.name;
  $("show-continue").dataset.playShow = showId;
  $("show-shuffle").dataset.shuffleShow = showId;
  $("episode-list").innerHTML = currentShow.episodes.map(episodeMarkup).join("") ||
    empty("No playable episodes.");
  $("show-backdrop").classList.add("open");
  document.body.classList.add("sheet-open");
  $("show-close").focus();
}

function closeShow() {
  $("show-backdrop").classList.remove("open");
  document.body.classList.remove("sheet-open");
  currentShow = null;
}

async function reloadOpenShow() {
  if (!currentShow) return;
  const id = currentShow.show.id;
  currentShow = await json("/api/shows/" + encodeURIComponent(id));
  $("episode-list").innerHTML = currentShow.episodes.map(episodeMarkup).join("");
}

function playParameters(target, extra) {
  const parameters = Object.assign({ subtitles: subtitleMode() }, extra || {});
  if (target.dataset.playItem) parameters.item = target.dataset.playItem;
  if (target.dataset.playShow) parameters.show = target.dataset.playShow;
  return parameters;
}

$("dashboard-link").href = siblingServiceUrl(8788);
renderSubtitleMode();

$("subtitles").addEventListener("click", () => {
  localStorage.setItem(SUBTITLE_KEY, subtitleMode() === "off" ? "auto" : "off");
  renderSubtitleMode();
  toast(subtitleMode() === "off" ? "Subtitles off on the next video" : "English subtitles on the next video", false);
});
$("rescan").addEventListener("click", () => action(() => post("rescan"), { library: true }));
$("clear-search").addEventListener("click", () => {
  $("search").value = "";
  searchMatches = null;
  ++searchSerial;
  render();
  $("search").focus();
});
$("search").addEventListener("input", () => {
  const query = $("search").value.trim();
  const serial = ++searchSerial;
  searchMatches = null;
  render();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => searchLibrary(query, serial), 220);
});
document.querySelectorAll("[data-view]").forEach((button) => {
  button.addEventListener("click", () => {
    currentView = button.dataset.view;
    document.querySelectorAll("[data-view]").forEach((candidate) =>
      candidate.setAttribute("aria-pressed", String(candidate === button))
    );
    render();
  });
});
$("show-close").addEventListener("click", closeShow);
$("show-backdrop").addEventListener("click", (event) => {
  if (event.target === $("show-backdrop")) closeShow();
});
$("volume").addEventListener("input", () => {
  $("volume-value").textContent = $("volume").value + "%";
});
$("volume").addEventListener("change", () =>
  action(() => post("volume", { value: $("volume").value }))
);
$("rate").addEventListener("change", () =>
  action(() => post("rate", { value: $("rate").value }))
);
$("sleep").addEventListener("change", () =>
  action(() => post("sleep", { minutes: $("sleep").value }))
);
$("fullscreen").addEventListener("click", () => action(() => post("fullscreen")));
$("timeline").addEventListener("input", () => {
  const duration = Number(playerStatus && playerStatus.player && playerStatus.player.duration) || 0;
  const target = Number($("timeline").value) / 1000 * duration;
  $("elapsed").textContent = formatSeconds(target);
  $("remaining").textContent = duration ? "−" + formatSeconds(duration - target) : "—";
});
$("timeline").addEventListener("change", () => {
  const player = playerStatus && playerStatus.player;
  if (!player || !player.duration) return;
  const target = Number($("timeline").value) / 1000 * player.duration;
  action(() => post("position", { position: target }));
});

document.addEventListener("click", (event) => {
  const item = event.target.closest("[data-play-item]");
  if (item) {
    action(() => post("play", playParameters(item)));
    return;
  }
  const show = event.target.closest("[data-play-show]");
  if (show) {
    action(() => post("play", playParameters(show)));
    return;
  }
  const browse = event.target.closest("[data-show]");
  if (browse) {
    openShow(browse.dataset.show).catch((error) => toast(error.message, true));
    return;
  }
  const restart = event.target.closest("[data-restart]");
  if (restart) {
    if (window.confirm("Start this video again from the beginning?")) {
      action(() => post("play", {
        item: restart.dataset.restart,
        restart: "true",
        subtitles: subtitleMode(),
      }));
    }
    return;
  }
  const watched = event.target.closest("[data-watch-item]");
  if (watched) {
    action(async () => {
      const result = await post("progress", {
        item: watched.dataset.watchItem,
        action: watched.dataset.watchAction,
      });
      await loadLibrary();
      await reloadOpenShow();
      return result;
    });
    return;
  }
  const quick = event.target.closest("[data-quick-show]");
  if (quick) {
    action(() => post("play", {
      show: quick.dataset.quickShow,
      shuffle: "true",
      subtitles: quick.dataset.noSubs === "true" ? "off" : subtitleMode(),
    }));
    return;
  }
  const shuffle = event.target.closest("[data-shuffle-show]");
  if (shuffle) {
    action(() => post("play", {
      show: shuffle.dataset.shuffleShow,
      shuffle: "true",
      subtitles: subtitleMode(),
    }));
    return;
  }
  const control = event.target.closest("[data-control]");
  if (control) {
    action(() => post("control", { action: control.dataset.control }));
    return;
  }
  const seek = event.target.closest("[data-seek]");
  if (seek) {
    action(() => post("seek", { seconds: seek.dataset.seek }));
    return;
  }
  if (event.target.closest("#surprise")) {
    action(() => post("surprise", { type: "any", subtitles: subtitleMode() }));
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && $("show-backdrop").classList.contains("open")) {
    closeShow();
    return;
  }
  if (event.target.matches("input,select,textarea")) return;
  const key = event.key.toLowerCase();
  const player = playerStatus && playerStatus.player;
  if (key === " " || key === "k") {
    if (!player || !player.available || !player.can_control ||
        (player.state === "PLAYING" ? !player.can_pause : !player.can_play)) return;
    event.preventDefault();
    action(() => post("control", { action: "toggle" }));
  } else if (key === "j" || event.key === "ArrowLeft") {
    if (!player || !player.can_seek) return;
    event.preventDefault();
    action(() => post("seek", { seconds: event.shiftKey ? -300 : -20 }));
  } else if (key === "l" || event.key === "ArrowRight") {
    if (!player || !player.can_seek) return;
    event.preventDefault();
    action(() => post("seek", { seconds: event.shiftKey ? 300 : 20 }));
  } else if (key === "n") {
    if (!player || !player.can_control || !player.can_next) return;
    action(() => post("control", { action: "next" }));
  } else if (key === "p") {
    if (!player || !player.can_control || !player.can_previous) return;
    action(() => post("control", { action: "previous" }));
  } else if (key === "f") {
    action(() => post("fullscreen"));
  }
});

Promise.allSettled([loadLibrary(), refreshStatus(false)]).then((results) => {
  results.forEach((result) => {
    if (result.status === "rejected") toast(result.reason.message, true);
  });
});
setInterval(predictTimeline, 500);
setInterval(() => {
  if (!document.hidden) refreshStatus(true);
}, 2000);
setInterval(() => {
  if (!document.hidden) loadLibrary().catch(() => {});
}, 60000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    refreshStatus(true);
    loadLibrary().catch(() => {});
  }
});
