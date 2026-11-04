# Native version metadata owned by the pack

The pack supplies `config/native-version-metadata.jar` and one JVM option in
`launcher/jvm-args.txt`:

```text
-Xbootclasspath/a:config/native-version-metadata.jar
```

The JAR contains only the original Minecraft `version.json`, with zero classes,
mod descriptors or agents. It stays outside `mods`. Every author mod and the
original client JAR retain their exact bytes. The launcher already supports this
pack option and copies the config resource through normal instance preparation;
no launcher C# change or new EXE is required. The working directory is the game
instance. `launcher/` itself is launcher-only data and is not copied there.

This restores native metadata visibility to libraries that ask the system class
loader. Some inherited loader profiles expose no `version.json` through their
system classpath even while BootstrapLauncher successfully loads the game layer.
An author mod then sees protocol 0 and chooses the wrong version-specific patch.
The resource-only boot append exposes the original native protocol without
disabling that mod's patch guard or putting untransformed game classes on the
system classpath. Multiple native boot-append options retain earlier resources.

Run from the SOURCE repository after importing and independently verifying a
new official base/client and updating its existing source size/SHA256 pin:

```text
python -B tools/native-version-metadata/prepare.py --pack . --apply --report tools/native-version-metadata/last-report.json
python -B tools/native-version-metadata/prepare.py --pack . --check
python -B -m unittest discover -s tools/native-version-metadata -p "test_*.py" -v
```

The tool reads the current `portable-pack.json` client path and Minecraft ID,
then requires its exact source pin in `tools/upstream-lock.json:supportFiles`.
It preserves the complete native metadata bytes, checks the ID and integer
protocol/world versions, and deterministically emits a one-entry JAR. It has no
version table, mod-count gate, downloader or mod-name logic. New native client
versions regenerate the projection. Older clients without this metadata retire
only the previously owned projection and its own lines, preserving other flags.

Preparation preserves the existing JVM/ownership file prefixes and updates only
the three associated support-file records. The original source/client pin and
all unrelated lock fields remain unchanged. `ownership.json` distinguishes its
own generated file from an unrelated or edited source file; an unknown collision
is preserved for review. Multi-file writes roll back their previous bytes on
failure. `--check` is read-only. The tool refuses a root containing a personal
`saves` directory or `logs/latest.log`.

The exact pack-owned pattern `config/native-version-metadata.jar` ensures a
future update replaces stale instance metadata. Normal launcher synchronization
first preserves differing local bytes in `Personal/PackConflicts`; unrelated
personal configuration remains untouched. `tools` and preparation history are
source-only and are excluded from game payloads.

The tests cover source pins, native identity/type/size/duplicate validation,
zero-class projection, exact byte preservation, retries, pinned-source refresh,
missing-metadata retirement, collisions, rollback and source-only boundaries.
Separate actual Java 21 checks verified the relative boot flag, the exact author
patch, missing-file failure and native multiple-append behavior. A full client
launch is still required after integrating the data; these checks do not claim
skin appearance or world compatibility.
