"""Hard-coded copies of disk facts must agree with pi/scripts/disk_policy.sh.

Runs on the Mac: disk_policy.sh is sourced by /bin/bash 3.2 only to print its
arrays and Samba mapping. The lock-registry check mirrors the Linux-only
test_backup_lock_registry case but needs no Bash 4 or backup_locks.sh.
"""
import ast
import re
import subprocess
import unittest
from pathlib import Path

from pi.scripts.backup import backup_locks

ROOT = Path(__file__).resolve().parents[3]
POLICY = "pi/scripts/disk_policy.sh"
POLICYCTL = "pi/scripts/policyctl"
LOCKS = "pi/scripts/backup/backup_locks.py"
SMB = "pi/configs/smb.conf"
QUIRKS = "pi/scripts/apply_usb_storage_quirks.py"
ARRAYS = ("MOUNT_LABELS", "MANUAL_MOUNT_LABELS", "HDD_LABELS")


def policy_facts():
    script = r'''
. pi/scripts/disk_policy.sh || exit 1
for name in MOUNT_LABELS MANUAL_MOUNT_LABELS HDD_LABELS; do
  eval "for label in \"\${$name[@]}\"; do printf '%s %s\n' $name \"\$label\"; done"
done
for label in "${MOUNT_LABELS[@]}" "${MANUAL_MOUNT_LABELS[@]}" "${HDD_LABELS[@]}"; do
  if share=$(disk_policy_samba_share_name "$label"); then printf 'SHARE %s %s\n' "$label" "$share"; fi
done
'''
    result = subprocess.run(["/bin/bash", "-c", script], cwd=ROOT, capture_output=True,
                            text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    arrays = {name: set() for name in ARRAYS}
    shares = {}
    for line in result.stdout.splitlines():
        kind, *rest = line.split()
        if kind == "SHARE":
            shares[rest[0]] = rest[1]
        else:
            arrays[kind].add(rest[0])
    return arrays, shares


def smb_paths():
    paths, section = {}, None
    for line in (ROOT / SMB).read_text().splitlines():
        stripped = line.strip()
        header = re.fullmatch(r"\[(.+)\]", stripped)
        if header:
            section = header.group(1)
        elif section and (match := re.fullmatch(r"path\s*=\s*(\S+)", stripped)):
            paths[section] = match.group(1)
    return paths


def literal_assignment(path, name):
    tree = ast.parse((ROOT / path).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path}")


class DiskPolicyConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.arrays, self.shares = policy_facts()

    def test_policyctl_rotational_labels_match_hdd_labels(self):
        self.assertEqual(set(literal_assignment(POLICYCTL, "MANAGED_DISK_LABELS")),
                         self.arrays["HDD_LABELS"],
                         f"update MANAGED_DISK_LABELS in {POLICYCTL} to match HDD_LABELS in {POLICY}")

    def test_every_policy_label_has_a_backup_disk_lock(self):
        listed = set().union(*self.arrays.values())
        missing = listed - set(backup_locks.DISK_LABELS)
        self.assertFalse(missing, f"add {sorted(missing)} to DISK_LABELS in {LOCKS} "
                                  f"(then regenerate the lock registry) or remove them from {POLICY}")

    def test_samba_share_mapping_matches_smb_conf(self):
        paths = smb_paths()
        self.assertTrue(self.shares, "no Samba shares mapped; check the parser")
        for label, share in self.shares.items():
            with self.subTest(label=label):
                self.assertIn(share, paths, f"share [{share}] named for {label} in {POLICY} "
                                            f"is missing from {SMB}")
                self.assertEqual(paths[share], f"/mnt/{label}",
                                 f"path of [{share}] in {SMB} must be /mnt/{label} "
                                 f"per {POLICY}; update whichever is stale")

    def test_usb_quirk_labels_are_automatic_mount_labels(self):
        source = (ROOT / QUIRKS).read_text()
        match = re.search(r"for label in (\([^)]*\)):", source)
        self.assertIsNotNone(match, f"label tuple not found in {QUIRKS}; update this test")
        labels = set(ast.literal_eval(match.group(1)))
        extra = labels - self.arrays["MOUNT_LABELS"]
        self.assertFalse(extra, f"{QUIRKS} names {sorted(extra)} that are not in MOUNT_LABELS in {POLICY}")


if __name__ == "__main__":
    unittest.main()
