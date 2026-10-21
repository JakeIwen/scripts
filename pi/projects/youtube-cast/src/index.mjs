import YouTubeCastReceiver, { Constants } from 'yt-cast-receiver';
import { config } from './config.mjs';
import { PrivateDataStore } from './private-data-store.mjs';
import { MpvPlayer } from './mpv-player.mjs';
import { runReceiver } from './receiver-lifecycle.mjs';
import { useStableDialUuid } from './stable-dial-uuid.mjs';

process.umask(0o077);
const store = new PrivateDataStore(config.stateDirectory);
await store.initialize();
await useStableDialUuid(store);
const player = new MpvPlayer({ store });
const receiver = new YouTubeCastReceiver(player, {
  device: { name: config.name, screenName: config.name },
  dial: { port: config.dialPort, prefix: '/ytcr',
    bindToAddresses: [config.lanAddress], corsAllowOrigins: false },
  dataStore: store,
  // Upstream debug/error objects can contain lounge tokens and pairing data.
  logLevel: Constants.LOG_LEVELS.NONE,
  // Like a TV, keep playing when a remote just goes away (ytcast exits, phone
  // sleeps); only an explicit disconnect from every sender ends the session.
  app: { resetPlayerOnDisconnectPolicy: Constants.RESET_PLAYER_ON_DISCONNECT_POLICIES.ALL_EXPLICITLY_DISCONNECTED },
});
await runReceiver({ receiver, player, store });
