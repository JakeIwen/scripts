import { arrayValue, objectValue } from '../../api/validation';
import { clientsPayload, connectivityPayload, speedtestPayload } from './testFixtures';

type Payload = Record<string, unknown>;

function mutate(seed: Payload, change: (payload: Payload) => void): Payload {
  const payload = structuredClone(seed);
  change(payload);
  return payload;
}

function connectivityRecord(payload: Payload): Payload {
  return objectValue(payload.connectivity, 'fixture.connectivity');
}

function routerRecord(payload: Payload): Payload {
  return objectValue(connectivityRecord(payload).router, 'fixture.connectivity.router');
}

function internetRecord(payload: Payload): Payload {
  return objectValue(connectivityRecord(payload).internet, 'fixture.connectivity.internet');
}

function interfaceRecords(payload: Payload): Payload[] {
  return arrayValue(routerRecord(payload).interfaces, 'fixture.connectivity.router.interfaces').map(
    (value, index) => objectValue(value, `fixture.connectivity.router.interfaces[${index}]`),
  );
}

function openWrtRecord(payload: Payload): Payload {
  return objectValue(payload.openwrt, 'fixture.openwrt');
}

function startPayload(status: 'idle' | 'running' | 'complete' | 'error'): Payload {
  return { ...speedtestPayload(status), message: 'Speed test started' };
}

const connectivity = connectivityPayload();
const clients = clientsPayload();
const completeSpeedtest = speedtestPayload('complete');
const runningSpeedtest = speedtestPayload('running');
const idleSpeedtest = speedtestPayload('idle');
const errorSpeedtest = speedtestPayload('error');
const startRunning = startPayload('running');

export const connectivityFixtures: Record<string, Payload> = {
  original: connectivity,
  degraded: mutate(connectivity, (payload) => {
    const interfaces = interfaceRecords(payload);
    const firstInterface = interfaces[0];
    if (firstInterface) firstInterface.state = 'degraded';
  }),
  unknownInterfaceState: mutate(connectivity, (payload) => {
    const interfaces = interfaceRecords(payload);
    const firstInterface = interfaces[0];
    if (firstInterface) firstInterface.state = 'mysterious';
  }),
};

export const connectivityTargeted = [
  {
    name: 'targeted/internet-null-bad-checked-at',
    input: mutate(connectivity, (payload) => {
      const connectivity = connectivityRecord(payload);
      connectivity.internet = null;
      connectivity.checked_at = 'invalid';
    }),
  },
  {
    name: 'targeted/router-null-bad-internet-online',
    input: mutate(connectivity, (payload) => {
      const connectivity = connectivityRecord(payload);
      connectivity.router = null;
      internetRecord(payload).online = 'invalid';
    }),
  },
  {
    name: 'targeted/bad-interface-state-ubnt-null',
    input: mutate(connectivity, (payload) => {
      const interfaces = interfaceRecords(payload);
      const firstInterface = interfaces[0];
      if (firstInterface) firstInterface.state = 'mysterious';
      connectivityRecord(payload).ubnt = null;
    }),
  },
];

export const clientFixtures: Record<string, Payload> = {
  original: clients,
  mismatchedCounts: mutate(clients, (payload) => {
    openWrtRecord(payload).wifi_count = 2;
  }),
};

export const clientTargeted = [
  {
    name: 'targeted/invalid-clients-bad-counts',
    input: mutate(clients, (payload) => {
      const openwrt = openWrtRecord(payload);
      openwrt.clients = null;
      openwrt.client_count = -1;
      openwrt.wifi_count = -1;
      openwrt.lan_count = -1;
    }),
  },
  {
    name: 'targeted/inconsistent-counts-missing-checked-at',
    input: mutate(clients, (payload) => {
      const openwrt = openWrtRecord(payload);
      openwrt.client_count = 1;
      delete openwrt.checked_at;
    }),
  },
];

export const speedtestFixtures: Record<string, Payload> = {
  complete: completeSpeedtest,
  running: runningSpeedtest,
  idle: idleSpeedtest,
  error: errorSpeedtest,
};

export const startFixtures: Record<string, Payload> = {
  running: startRunning,
};

export const startTargeted = [
  {
    name: 'targeted/bad-message-ok-false',
    input: mutate(startRunning, (payload) => {
      payload.message = 42;
      payload.ok = false;
    }),
  },
  {
    name: 'targeted/good-message-ok-false-speedtest-null',
    input: mutate(startRunning, (payload) => {
      payload.ok = false;
      payload.speedtest = null;
    }),
  },
];
