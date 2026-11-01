#!/usr/bin/env python3
"""Generate the TerraFirma Rebirth 2 pack distribution manifest and staged release assets.

Usage:
    python tools/generate_manifest.py --pack <packRoot> --out <distDir> [--revision <rev>]
    python tools/generate_manifest.py --pack <packRoot> --out <distDir> --verify-only
    python tools/generate_manifest.py --pack <packRoot> --out <distDir> --verify-all

Behavior:
    * Collects the shipped file set: everything under the roots
      mods/, config/, kubejs/, scripts/, defaultconfigs/, data/, resourcepacks/,
      shaderpacks/, configureddefaults/, launcher/ (recursively, absent roots
      are skipped) plus the root files portable-pack.json and the client jar
      named by portable-pack.json's "clientJar" field. launcher/ carries data
      the launcher reads (the controls preset), not the game.
    * Stages release assets into <distDir>/assets/:
        - every file under mods/ becomes its own byte-for-byte asset (kind "jar");
        - every other non-empty top-level root becomes ONE deterministic zip
          (kind "zipRoot") that is byte-stable across runs;
        - portable-pack.json and the client jar become individual assets.
    * Writes <distDir>/pack-manifest.json after validating that every shipped
      file is covered by exactly one asset and all asset names are unique.
    * --verify-only re-hashes a sample of staged assets against the manifest;
      --verify-all checks every staged asset instead of generating anything.

Python 3.12, standard library only.
"""

from __future__ import annotations

from pack_library import stage_library, verify_library
import network_logging_policy
from config_overrides import project_configs, projection_report

import argparse
import fnmatch
import hashlib
import json
import os
import random
import re
import shutil
import sys
import pathlib
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# This pack ships three roots the template did not know about: the ambience
# mod's music, the folders Global Packs force-loads, and JourneyMap's config.
# A root missing from here is simply never published, and the player gets a
# pack quietly short of 380 MB of music and the resource packs it forces on.
SCAN_ROOTS = ("mods", "config", "kubejs", "scripts", "defaultconfigs", "data",
              "resourcepacks", "shaderpacks", "configureddefaults", "launcher",
              "ambience_music", "global_packs", "journeymap")
ZIP_ROOTS = ("config", "kubejs", "scripts", "defaultconfigs", "data",
             "resourcepacks", "shaderpacks", "configureddefaults", "launcher",
             "ambience_music", "global_packs", "journeymap")
# Launcher-local marker/state files that must never ship inside the pack.
EXCLUDED_BASENAMES = {".portable-pack-sync.json", "portable-pack-source.json"}
EXCLUDED_TOP_DIRS = {"tools", "dist"}
# Fixed DOS epoch => identical zip bytes across runs.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
ZIP_COMPRESSLEVEL = 9
CHUNK = 1024 * 1024
VERIFY_SAMPLE = 10

# mods/ is shipped as zip chunks so the GitHub release stays far below its
# 1000-asset ceiling with an 800+ mod pack. The bucket of a jar is a pure
# function of its relative path, so adding or updating one mod re-chunks (and
# re-downloads) only the bucket it lives in (~6.5 MB per chunk at 256).
# FROZEN since the first published LL8 release: changing the count reshuffles
# every bucket and forces every client to re-download the whole mods/ tree.
MODS_CHUNK_COUNT = 256
# Frequently-updated jars (launcher pins, pinned mods, quest/render core) stay
# single-file assets so their updates never drag a whole chunk along.
HOT_MODS_FILE = Path(__file__).resolve().parent / "hot-mods.json"


def fail(message: str) -> "NoReturn":  # noqa: F821 - typing comment only
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def sanitize_name(name: str) -> str:
    """Keep [A-Za-z0-9._-], replace everything else with '-', collapse runs, cap at 80."""
    out = re.sub(r"[^A-Za-z0-9._-]+", "-", name)
    out = re.sub(r"--+", "-", out)
    return out[:80] or "asset"


def is_excluded(parts: tuple[str, ...]) -> bool:
    if parts and parts[0] in EXCLUDED_TOP_DIRS:
        return True
    for part in parts:
        if part.startswith(".git"):
            return True
        if part in EXCLUDED_BASENAMES:
            return True
    return False


def walk_root(pack: Path, root: str) -> list[str]:
    """Forward-slash, case-exact relative paths of all shipped files under <pack>/<root>."""
    base = pack / root
    if not base.is_dir():
        return []
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        for filename in sorted(filenames):
            rel_parts = (Path(dirpath) / filename).relative_to(pack).parts
            if is_excluded(rel_parts):
                continue
            found.append("/".join(rel_parts))
    return found


def load_portable_pack(pack: Path) -> dict:
    pp_path = pack / "portable-pack.json"
    if not pp_path.is_file():
        fail(f"portable-pack.json not found in pack root: {pack}")
    try:
        data = json.loads(pp_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        fail(f"cannot parse {pp_path}: {exc}")
    if not isinstance(data, dict):
        fail("portable-pack.json must contain a JSON object")
    client_jar = data.get("clientJar")
    if not isinstance(client_jar, str) or not client_jar:
        fail("portable-pack.json is missing a non-empty string 'clientJar' field")
    return data


class Staging:
    """Accumulates staged assets, deduplicating byte-identical single-file assets."""

    def __init__(self, assets_dir: Path, projected_files: dict[str, bytes] | None = None) -> None:
        self.assets_dir = assets_dir
        self.projected_files = projected_files or {}
        self.by_name: dict[str, dict] = {}

    def add_single_file(self, pack: Path, rel: str, prefix: str,
                        sha256: str, size_bytes: int) -> None:
        src = pack / Path(rel)
        name = f"{prefix}-{sha256[:12]}-{sanitize_name(src.name)}"
        existing = self.by_name.get(name)
        if existing is not None:
            # The launcher rejects jar assets covering more than one path, so
            # byte-identical twins must not be collapsed into one asset.
            fail(f"two shipped files map to the same single-file asset: {name} "
                 f"({existing['covers'][0]} vs {rel})")
        shutil.copyfile(src, self.assets_dir / name)
        self.by_name[name] = {
            "name": name,
            "kind": "jar",
            "sha256": sha256,
            "sizeBytes": size_bytes,
            "covers": [rel],
        }

    def add_zip_root(self, pack: Path, root: str, rel_files: list[str],
                     compress: bool = True) -> None:
        self._add_zip(pack, root + ".zip", rel_files, compress)

    def add_mods_chunk(self, pack: Path, bucket: int, rel_files: list[str]) -> None:
        # Jars are already deflate-compressed; store them to keep CI fast and
        # chunk sizes predictable. Determinism is unaffected.
        self._add_zip(pack, f"mods.c{bucket:03}.zip", rel_files, compress=False)

    def _add_zip(self, pack: Path, label: str, rel_files: list[str],
                 compress: bool) -> None:
        method = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
        tmp = self.assets_dir / f".tmp-{label}"
        with zipfile.ZipFile(tmp, "w", compression=method,
                             compresslevel=ZIP_COMPRESSLEVEL if compress else None) as archive:
            for rel in sorted(rel_files):
                info = zipfile.ZipInfo(rel, date_time=ZIP_EPOCH)
                info.compress_type = method
                info.external_attr = 0
                info.create_system = 3  # platform-independent header bytes
                with archive.open(info, "w") as dst:
                    if rel in self.projected_files:
                        dst.write(self.projected_files[rel])
                    else:
                        with open(pack / Path(rel), "rb") as srcf:
                            shutil.copyfileobj(srcf, dst, CHUNK)
                # ZipFile.open(..., "w") force-sets external_attr 0o600<<16 when it
                # is 0; reset it before the central directory is written on close.
                info.external_attr = 0
        sha256 = sha256_file(tmp)
        size_bytes = tmp.stat().st_size
        name = f"r-{sha256[:12]}-{sanitize_name(label)}"
        if name in self.by_name:
            fail(f"asset name collision: {name}")
        os.replace(tmp, self.assets_dir / name)
        self.by_name[name] = {
            "name": name,
            "kind": "zipRoot",
            "sha256": sha256,
            "sizeBytes": size_bytes,
            "covers": sorted(rel_files),
        }


def generate(pack: Path, out: Path, revision: str) -> None:
    network_logging_policy.verify(pack)
    portable = load_portable_pack(pack)
    client_jar = portable["clientJar"].replace("\\", "/")
    client_path = pack / Path(client_jar)
    if not client_path.is_file():
        fail(f"client jar '{client_jar}' (from portable-pack.json) not found in {pack}")

    per_root: dict[str, list[str]] = {}
    shipped: list[str] = []
    for root in SCAN_ROOTS:
        files = walk_root(pack, root)
        if files:
            per_root[root] = files
            shipped.extend(files)
    for root_file in ("portable-pack.json", client_jar):
        if root_file in shipped:
            fail(f"root file '{root_file}' unexpectedly also found under a scanned root")
        shipped.append(root_file)
    shipped.sort()  # ordinal (codepoint) order

    # Apply declared pack-local settings to delivered bytes, keeping the raw
    # author snapshot intact. New author defaults pass through the same rules.
    projected_files = project_configs(pack)
    if any(rel not in shipped for rel in projected_files):
        fail("a declared configuration projection is outside the shipped file set")

    # Hash every shipped file once; single-file assets reuse these hashes.
    file_meta: dict[str, tuple[str, int]] = {}
    for rel in shipped:
        path = pack / Path(rel)
        if rel in projected_files:
            data = projected_files[rel]
            file_meta[rel] = (hashlib.sha256(data).hexdigest(), len(data))
        else:
            file_meta[rel] = (sha256_file(path), path.stat().st_size)

    assets_dir = out / "assets"
    if assets_dir.exists():
        shutil.rmtree(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)

    hot_patterns = []
    if HOT_MODS_FILE.is_file():
        hot_patterns = [p.lower() for p in
                        json.loads(HOT_MODS_FILE.read_text(encoding="utf-8"))]

    def is_hot(rel: str) -> bool:
        name = rel.rsplit("/", 1)[-1].lower()
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in hot_patterns)

    staging = Staging(assets_dir, projected_files)
    buckets: dict[int, list[str]] = {}
    for rel in per_root.get("mods", []):
        if is_hot(rel):
            sha256, size_bytes = file_meta[rel]
            staging.add_single_file(pack, rel, "m", sha256, size_bytes)
        else:
            digest = hashlib.sha256(rel.encode("utf-8")).hexdigest()
            buckets.setdefault(int(digest[:8], 16) % MODS_CHUNK_COUNT, []).append(rel)
    for bucket, rel_files in sorted(buckets.items()):
        staging.add_mods_chunk(pack, bucket, rel_files)
    for root in ZIP_ROOTS:
        if root in per_root:
            staging.add_zip_root(pack, root, per_root[root])
    for rel in ("portable-pack.json", client_jar):
        sha256, size_bytes = file_meta[rel]
        staging.add_single_file(pack, rel, "c", sha256, size_bytes)

    assets = sorted(staging.by_name.values(), key=lambda a: a["name"])

    # Validate: every shipped path covered exactly once, asset names unique (by dict).
    coverage: dict[str, int] = {}
    for asset in assets:
        for rel in asset["covers"]:
            coverage[rel] = coverage.get(rel, 0) + 1
    problems = [rel for rel in shipped if coverage.get(rel, 0) != 1]
    problems += [rel for rel in coverage if rel not in file_meta]
    if problems:
        fail("coverage validation failed for: " + ", ".join(sorted(problems)[:20]))
    for asset in assets:
        staged = assets_dir / asset["name"]
        if not staged.is_file() or staged.stat().st_size != asset["sizeBytes"]:
            fail(f"staged asset missing or size mismatch: {asset['name']}")

    manifest = {
        "schemaVersion": 1,
        # This pack, and not the one this script was copied from: the same short name the publish workflow uses
        "packId": "TFR",
        "revision": revision,
        "generatedAtUtc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "minecraftVersion": portable.get("minecraftVersion"),
        # Jars plus what is nested in them. The launcher weighs an
        # installed pack by that number and an unweighed one by this,
        # and the estimate moved by up to 3 GB when only one existed.
        "modCount": count_loaded_mods(pack, shipped),
        "loader": portable.get("loader"),
        "clientJar": client_jar,
        "files": [
            {"path": rel, "sizeBytes": file_meta[rel][1], "sha256": file_meta[rel][0]}
            for rel in shipped
        ],
        "assets": assets,
    }

    if (pack / "library/content.json").is_file():
        manifest["library"] = stage_library(pack, out, revision)
    if projected_files:
        manifest["configOverrides"] = projection_report(pack, projected_files)

    manifest_path = out / "pack-manifest.json"
    tmp_path = out / "pack-manifest.json.tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp_path, manifest_path)

    total = sum(a["sizeBytes"] for a in assets)
    print(f"manifest: {manifest_path}")
    print(f"files: {len(shipped)}, assets: {len(assets)}, "
          f"staged bytes: {total:,}, revision: {revision}")


def verify_only(out: Path, verify_all: bool = False) -> None:
    manifest_path = out / "pack-manifest.json"
    if not manifest_path.is_file():
        fail(f"manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("library"):
        verify_library(out, manifest["library"])
    assets = manifest.get("assets") or []
    if not assets:
        fail("manifest contains no assets")
    sample = assets if verify_all else random.sample(assets, min(VERIFY_SAMPLE, len(assets)))
    for asset in sample:
        staged = out / "assets" / asset["name"]
        if not staged.is_file():
            fail(f"verify: staged asset missing: {asset['name']}")
        actual_sha = sha256_file(staged)
        actual_size = staged.stat().st_size
        if actual_sha != asset["sha256"] or actual_size != asset["sizeBytes"]:
            fail(f"verify: hash/size mismatch for {asset['name']}: "
                 f"expected {asset['sha256']}/{asset['sizeBytes']}, "
                 f"got {actual_sha}/{actual_size}")
        print(f"verify ok: {asset['name']}")
    print(f"verified {len(sample)} of {len(assets)} staged assets")



def count_loaded_mods(pack: pathlib.Path, shipped: list[str]) -> int:
    """
    The jars under mods/, plus the mods nested inside them.

    This is the number the launcher works out for an installed pack by opening
    every jar, and publishing it is what makes the memory estimate it shows
    before a download match the one it shows after. It cannot be arrived at from
    the file list alone: across the packs published from this machine the ratio
    of nested mods to jars runs from 1.18 to 2.79 and follows neither the loader
    nor the bytes, so a multiplier is wrong by up to three gigabytes of
    suggested heap. Counting them here costs about a second and a half on the
    largest pack, on jars this script has already opened to hash.
    """
    jars = [rel for rel in shipped
            if rel.startswith("mods/") and rel.lower().endswith(".jar")]
    nested = 0
    for rel in jars:
        try:
            with zipfile.ZipFile(pack / rel) as archive:
                for name in archive.namelist():
                    lower = name.lower()
                    if lower.endswith(".jar") and (
                            lower.startswith("meta-inf/jars/")
                            or lower.startswith("meta-inf/jarjar/")):
                        nested += 1
        except (OSError, zipfile.BadZipFile):
            # A jar that will not open carries nothing anyone can count.
            continue
    return len(jars) + nested


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate (or verify) the TerraFirma Rebirth 2 release manifest and assets.")
    parser.add_argument("--pack", required=True, help="pack root directory")
    parser.add_argument("--out", required=True, help="dist output directory")
    parser.add_argument("--revision", default=None,
                        help="revision string (default: $GITHUB_SHA or 'local')")
    parser.add_argument("--verify-only", action="store_true",
                        help="re-hash a random sample of staged assets against the manifest")
    parser.add_argument("--verify-all", action="store_true",
                        help="re-hash every staged asset against the manifest without generating")
    args = parser.parse_args()

    pack = Path(args.pack).resolve()
    out = Path(args.out).resolve()
    if not pack.is_dir():
        fail(f"pack root does not exist: {pack}")

    if args.verify_only or args.verify_all:
        network_logging_policy.verify(pack)
        verify_only(out, args.verify_all)
        return

    revision = args.revision or os.environ.get("GITHUB_SHA") or "local"
    out.mkdir(parents=True, exist_ok=True)
    generate(pack, out, revision)


if __name__ == "__main__":
    main()
