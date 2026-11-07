"""Read-only gate for the reviewed delivery inputs without public server seeds."""
import argparse
import hashlib
import json
from pathlib import Path

ROOTS = ("mods", "config", "kubejs", "scripts", "defaultconfigs", "data",
         "resourcepacks", "shaderpacks", "configureddefaults", "launcher",
         "ambience_music", "global_packs", "journeymap")
RECEIPT = "tools/public-server-policy.json"


def fingerprint(root):
    rows = []
    for name in ROOTS:
        base = root / name
        if not base.exists():
            continue
        if base.is_symlink() or getattr(base.stat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("Linked delivery root refused")
        for path in sorted(base.rglob("*")):
            if path.is_symlink() or getattr(path.stat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("Linked delivery input refused")
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                if path.name.lower() in ("servers.dat", "servers.dat_old"):
                    raise ValueError("Server seed must not be delivered")
                rows.append([relative, hashlib.sha256(path.read_bytes()).hexdigest()])
    return hashlib.sha256(json.dumps(sorted(rows), ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def verify(pack):
    root = Path(pack)
    receipt = json.loads((root / RECEIPT).read_bytes())
    if (receipt.get("schemaVersion") != 1 or receipt.get("coveredRoots") != list(ROOTS)
            or receipt.get("reviewedInputSha256") != fingerprint(root)):
        raise ValueError("Public-server delivery inputs changed; review the new author menus, seed lists and mod hooks before updating the independent audit pin")
    return {"status": "PASS", "sourceOnly": True, "personalServerListReadOrWritten": False}


def prepare(pack):
    # Preparation never accepts a new author snapshot automatically.
    return verify(pack)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("prepare", "verify"), default="verify")
    parser.add_argument("--pack", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print(json.dumps(prepare(args.pack) if args.command == "prepare" else verify(args.pack)))
