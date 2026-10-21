import dial from '@patrickkfkan/peer-dial';
import { randomUUID } from 'node:crypto';

const STORE_KEY = 'dial.uuid';

// yt-cast-receiver 2.1.0 builds peer-dial's options itself and drops `uuid`, so
// every restart advertised a new SSDP device and senders cached duplicate
// "Van TV" entries. Wrapping the constructor it reads at receiver construction
// injects one persisted UUID; call this before constructing the receiver.
export async function useStableDialUuid(store) {
  let uuid = await store.get(STORE_KEY);
  if (typeof uuid !== 'string' || !uuid) {
    uuid = randomUUID();
    await store.set(STORE_KEY, uuid);
  }
  const Server = dial.Server;
  if (!Server.stableUuid) {
    const StableServer = function StableServer(options) {
      return new Server({ ...options, uuid: StableServer.stableUuid });
    };
    StableServer.stableUuid = uuid;
    dial.Server = StableServer;
  } else {
    Server.stableUuid = uuid;
  }
  return uuid;
}
