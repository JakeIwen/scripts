import { arrayValue, objectValue } from '../../api/validation';
import type { ParityCase } from '../../test/parity';
import { diskStatusPayload, storagePolicyPayload } from './testFixtures';

type Payload = Record<string, unknown>;

function mutate(seed: Payload, change: (payload: Payload) => void): Payload {
  const payload = structuredClone(seed);
  change(payload);
  return payload;
}

function policyRecord(payload: Payload): Payload {
  return objectValue(payload.policy, 'fixture.policy');
}

function policyRuntimeRecord(payload: Payload): Payload {
  return objectValue(policyRecord(payload).runtime, 'fixture.policy.runtime');
}

function diskStatusRecord(payload: Payload): Payload {
  return objectValue(payload.disk_status, 'fixture.disk_status');
}

function firstDisk(payload: Payload): Payload {
  const disks = arrayValue(diskStatusRecord(payload).disks, 'fixture.disk_status.disks');
  const disk = disks[0];
  if (disk === undefined) throw new Error('fixture.disk_status.disks has no first disk');
  return objectValue(disk, 'fixture.disk_status.disks[0]');
}

function diskHealthRecord(payload: Payload): Payload {
  return objectValue(firstDisk(payload).health, 'fixture.disk_status.disks[0].health');
}

function diskOperationRecord(payload: Payload): Payload {
  return objectValue(diskStatusRecord(payload).operation, 'fixture.disk_status.operation');
}

function mutationPayload(seed: Payload, message: string): Payload {
  return mutate(seed, (payload) => {
    payload.message = message;
  });
}

const policy = storagePolicyPayload();
const disks = diskStatusPayload();

export const storagePolicyFixtures: Record<string, Payload> = {
  original: policy,
  // The raw malformed fixture from storage.test.tsx also seeds every path mutation.
  inconsistentMount: mutate(policy, (payload) => {
    policyRuntimeRecord(payload).disks_mounted = false;
  }),
};

export const storagePolicyTargeted: ParityCase[] = [
  {
    name: 'targeted/versionbad-runtimebad',
    input: mutate(policy, (payload) => {
      const record = policyRecord(payload);
      record.version = 2;
      record.runtime = null;
    }),
  },
  {
    name: 'targeted/mountedlabelsbadtype-disks_mountedbadtype',
    input: mutate(policy, (payload) => {
      const runtime = policyRuntimeRecord(payload);
      runtime.mounted_disk_labels = null;
      runtime.disks_mounted = 'invalid';
    }),
  },
  {
    name: 'targeted/inconsistent-mountstate-invalidpolicyflag',
    input: mutate(policy, (payload) => {
      const record = policyRecord(payload);
      const runtime = policyRuntimeRecord(payload);
      runtime.disks_mounted = true;
      runtime.mounted_disk_labels = [];
      record.disks_enabled = 'invalid';
    }),
  },
  {
    name: 'targeted/invaliddisks_enabled-invalidqbittorrent_running-flagwins',
    input: mutate(policy, (payload) => {
      const record = policyRecord(payload);
      const runtime = policyRuntimeRecord(payload);
      record.disks_enabled = 'invalid';
      runtime.qbittorrent_running = 'invalid';
    }),
  },
  {
    name: 'targeted/correctflags-invalidqbit',
    input: mutate(policy, (payload) => {
      policyRuntimeRecord(payload).qbittorrent_running = 'invalid';
    }),
  },
  {
    name: 'targeted/literal-version-one',
    input: mutate(policy, (payload) => {
      policyRecord(payload).version = 1;
    }),
  },
];

export const diskStatusFixtures: Record<string, Payload> = {
  original: disks,
};

export const diskStatusTargeted: ParityCase[] = [
  {
    name: 'targeted/checked_atbad-disksbad',
    input: mutate(disks, (payload) => {
      const record = diskStatusRecord(payload);
      record.checked_at = 'invalid';
      record.disks = null;
    }),
  },
  {
    name: 'targeted/malformed-diskhealth-beforeoperation',
    input: mutate(disks, (payload) => {
      diskHealthRecord(payload).state = 'invalid';
      diskOperationRecord(payload).status = 'invalid';
    }),
  },
];

const policyMutation = mutationPayload(policy, 'policy updated');
const diskMutation = mutationPayload(disks, 'disk action started');

export const storagePolicyMutationFixtures: Record<string, Payload> = {
  'policy updated': policyMutation,
};

export const storagePolicyMutationTargeted: ParityCase[] = [
  {
    name: 'targeted/mutationokbad-badmessage-body',
    input: mutate(policyMutation, (payload) => {
      payload.ok = false;
      payload.message = 42;
      payload.policy = null;
    }),
  },
  {
    name: 'targeted/messagebad-bodybad',
    input: mutate(policyMutation, (payload) => {
      payload.ok = true;
      payload.message = 42;
      payload.policy = null;
    }),
  },
  {
    name: 'targeted/goodmessage-invalidbody-nestedroot',
    input: mutate(policyMutation, (payload) => {
      policyRecord(payload).runtime = null;
    }),
  },
];

export const diskMutationFixtures: Record<string, Payload> = {
  'disk action started': diskMutation,
};

export const diskMutationTargeted: ParityCase[] = [
  {
    name: 'targeted/mutationokbad-badmessage-body',
    input: mutate(diskMutation, (payload) => {
      payload.ok = false;
      payload.message = 42;
      payload.disk_status = null;
    }),
  },
  {
    name: 'targeted/messagebad-bodybad',
    input: mutate(diskMutation, (payload) => {
      payload.ok = true;
      payload.message = 42;
      payload.disk_status = null;
    }),
  },
  {
    name: 'targeted/goodmessage-invalidbody-nestedroot',
    input: mutate(diskMutation, (payload) => {
      diskOperationRecord(payload).status = 'invalid';
    }),
  },
];
