import './config.js';
import { activeTab, castTab, tabVideo } from './tab-casting.mjs';
import { parseYouTubeVideo, youtubeMenuPatterns } from './youtube-video.mjs';

const config = globalThis.VAN_TV_CONFIG;
const menu = Object.freeze({ link: 'cast-link', page: 'cast-page' });

function controlFields(message) {
  const fields = {};
  if (Object.hasOwn(message, 'position')) fields.position = message.position;
  if (Object.hasOwn(message, 'offset')) fields.offset = message.offset;
  return fields;
}

export function createMessageHandler(browserApi, receiver) {
  const routes = {
    [config.message.status]: () => receiver.status(),
    [config.message.control]: message => receiver.control(message.action, controlFields(message)),
    [config.message.cast]: message => receiver.cast(message.videoId, message.position),
    [config.message.tabVideo]: async () => tabVideo(browserApi, await activeTab(browserApi)),
    [config.message.castTab]: async () => castTab(browserApi, receiver, await activeTab(browserApi)),
  };
  return (message, sender) => {
    const route = message && Object.hasOwn(routes, message.type) && routes[message.type];
    if (!route) return undefined;
    // Only the player cast message belongs to a content script; receiver
    // controls and status are private to extension pages, not the website.
    if (sender?.tab && message.type !== config.message.cast) return undefined;
    return Promise.resolve().then(() => route(message)).then(
      data => ({ ok: true, data }),
      error => ({ ok: false, error: error.message || 'Van TV request failed' }),
    );
  };
}

export async function showCastResult(browserApi, operation) {
  try {
    await operation();
    await browserApi.browserAction.setBadgeText({ text: '' });
    await browserApi.browserAction.setTitle({ title: 'Van TV — cast accepted' });
  } catch (error) {
    await browserApi.browserAction.setBadgeText({ text: '!' });
    await browserApi.browserAction.setTitle({ title: error.message || 'Van TV request failed' });
  }
}

export async function installContextMenus(browserApi, receiver) {
  const patterns = youtubeMenuPatterns();
  await browserApi.contextMenus.removeAll();
  browserApi.contextMenus.create({ id: menu.link, title: 'Cast to Van TV',
    contexts: ['link'], targetUrlPatterns: patterns.link });
  browserApi.contextMenus.create({ id: menu.page, title: 'Cast to Van TV',
    contexts: ['page'], documentUrlPatterns: patterns.page });
  browserApi.contextMenus.onClicked.addListener((info, tab) => {
    if (info.menuItemId === menu.link) {
      const video = parseYouTubeVideo(info.linkUrl);
      void showCastResult(browserApi, async () => {
        if (!video) throw new Error('This link is not a YouTube video');
        await receiver.cast(video.videoId, video.position);
      });
    } else if (info.menuItemId === menu.page) {
      void showCastResult(browserApi, () => castTab(browserApi, receiver, tab));
    }
  });
}

export function installCommands(browserApi, receiver) {
  browserApi.commands.onCommand.addListener(command => {
    if (command !== config.command) return;
    void showCastResult(browserApi, async () => castTab(browserApi, receiver, await activeTab(browserApi)));
  });
}
