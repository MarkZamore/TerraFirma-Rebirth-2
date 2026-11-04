# Intel first-person depth compatibility

The pack owns a small KubeJS startup layer. It repeats the expected depth-only
clear after Veil has bound its first-person framebuffer and before the first
native `RenderHandEvent` of the frame. The callback can occur for both hands;
the second callback never clears away the first hand's depth.

The runtime guard requires Intel, the currently bound registered
`veil:first_person` framebuffer, an actual depth texture internal format of
`DEPTH32F_STENCIL8`, enabled depth writes and disabled scissor. It queries the
real texture format each frame, including after framebuffer/texture ID reuse;
Veil's dynamic wrappers can report unknown format and omit stencil metadata.
The shader rendering path and unrelated framebuffers fail the binding guard.
The previous clear-depth value is restored even if the native clear throws.

This is a repair of the depth clear expected by the existing renderer. The
Veil pipeline, lighting, stencil, shader selection, graphics quality, author
item renderers, animations and gameplay remain active. The layer adds no JAR,
agent, transformer, registry ID, key binding or launcher C# rule. It does not
use private reflection or expand KubeJS ClassFilter permissions.

## Source-owned files

- `tools/client-policy/scripts/lanmc_intel_hand_depth.js`: reviewed template.
- `tools/hand-depth-policy.json`: exact script, platform, native module and
  embedded Veil pins, with the permitted runtime scope.
- `tools/hand-depth/native-profile.json`: separately pinned source/API identity
  profile; it explicitly makes no runtime acceptance claim.
- `tools/hand_depth_policy.py`, its tests and this directory: import/publishing
  checks and documentation.
- `kubejs/startup_scripts/lanmc_intel_hand_depth.js`: byte-identical runtime copy.
- `launcher/pack-owned.txt`: owns only that exact runtime script path.
- `tools/upstream-lock.json`: declares that copy as a support addition and pins
  the resulting ownership bytes. Author stock snapshot records stay unchanged.

The ownership entry allows the generic launcher update process to replace an
old owned copy. The normal conflict backup mechanism preserves differing local
bytes. No broad `kubejs/**` ownership or personal configuration replacement is
introduced.

## Importing a new author release

Keep this source-owned layer when importing the author's new files into an
independent source copy. Do not replace the complete repository with an export
that discards the tools, template, receipt or profile. Preserve the other local
metadata, required shader inputs, network logging, JourneyMap and Euphoria
policies described by the repository's `AGENTS.md` and README.

After independently checking the new author manifest and stock snapshot, run:

```text
python -B tools/hand_depth_policy.py prepare --pack . --apply
python -B tools/hand_depth_policy.py verify --pack .
python -B -m unittest discover -s tools -p test_hand_depth_policy.py -v
node tools/hand-depth/test_runtime.js
```

`prepare --apply` restores a missing exact owned copy, appends a missing exact
ownership line while preserving all other bytes, and updates only the runtime
script and ownership support-lock records. It also accepts an explicitly
reviewed older script listed in `previousOwnedScripts`; unrecognized existing
code is preserved and causes failure. Missing/corrupt templates, receipts,
profiles, changed native JAR bytes and changed platform stop preparation before
any write. Multi-file changes roll back on a write failure. Source preparation
is serialized; concurrent edits must be resolved before retrying.

New Minecraft/NeoForge, Sable, embedded Veil, KubeJS or Rhino versions require
review of the real caller, native events, wrappers and actual texture formats.
Repeat the native/GPU and full-client empty-hand, main/offhand and re-entry
checks before deliberately updating both the receipt and separate profile.
The tool never learns or accepts new upstream pins automatically. The checks
also reject personal/runtime roots containing `saves` or `logs/latest.log`.

The publisher must unconditionally import `hand_depth_policy` and invoke
read-only `hand_depth_policy.verify(pack)` before collecting or checking
assets, including verify-only/verify-all. CI must run the explicit verification
and tests without an `exists`/`hashFiles` skip condition. A missing verifier,
receipt, template, runtime copy, ownership entry or profile must fail publication
instead of silently shipping without the correction. Never invoke `prepare`
from the generator, CI publication step or running launcher.

## Evidence and limits

The policy unit fixtures check source import preservation and failures. The
Node harness executes the actual template against native-event/GL facades:
Intel positive, other vendors, wrong bindings/formats, dynamic wrapper and ID
reuse behavior, both-hand ordering, re-entry IDs and exception state restoration.
These checks do not replace actual Rhino/native callbacks, GPU measurements or
the full-client visibility checks. Keep the runtime acceptance report separate
and name the cases actually performed.

Safety review: this layer only clears a client framebuffer depth attachment.
It contains no block/entity/inventory/claim/permission or deferred server-world
mutation. ATM9 Safety does not apply and is neither added nor changed. Author
JARs, recipes, resources, quests, translations, world data and registry IDs are
unchanged by this layer. No launcher exception is needed.
