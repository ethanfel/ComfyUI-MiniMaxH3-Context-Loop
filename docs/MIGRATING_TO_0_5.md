# Migrating workflows to version 0.5

Version 0.5 is backward compatible. Existing 0.4 node ids, positional outputs,
widget order, manifests, and checkpoints remain supported. Migration is
recommended for clearer graphs and model-free validation, but it is not
required merely to open or resume an older workflow.

## What changes

Version 0.5 uses one **Chain Policy** connection for normal authoring. It keeps
the audio and transition records independent inside the Plan/checkpoint
contract, but removes the need to wire two policy nodes.

| 0.4 audio mode | Final audio | Source reference | Generated continuity |
|---|---|---|---|
| `generated_audio` | generated | off | on |
| `source_track` | source | on | off |
| `source_plus_timeline` | source | on | on |

| 0.4 continuation | 0.5 transition | Default context |
|---|---|---:|
| guide with zero context | Cut | 0 frames |
| `guide` | Guide | 22 frames |
| `masked_av` | Hard AV | 39 frames |
| `audio_feathered_av` | Soft AV | 39 frames |
| any other implementation/context pair | Legacy 0.4 Policy Adapter | preserved exactly |

The compact presets derive generated-audio overlap from the same boundary: 0,
22, or 39 frames. A custom audio overlap, Tone/Latent/Detail Guide, Detail AV,
Drift-Control AV, old dual-stream Feathered AV, and other raw pairs migrate to
the single **Legacy 0.4 Policy Adapter** node instead. Nothing is approximated.
The semantic choice always describes the boundary entering a scene.

## Automatic migration

Back up a custom workflow, then run:

```bash
python tools/migrate_v05_workflows.py /path/to/workflow.json
```

Pass several paths to migrate them together. Use `--check` to report files that
would change without writing them:

```bash
python tools/migrate_v05_workflows.py --check /path/to/workflow.json
```

With no paths, the tool checks or migrates the maintained active examples. It
is idempotent: running it again does not add duplicate policy, preflight, or
timeline nodes. Exact 0.4 settings become one Chain Policy when they fit the
normal presets; other implementation/context pairs become one exact Legacy 0.4
Policy Adapter.

## Source-media rewiring

The old source-track graph repeated one full decoded AUDIO value:

```text
Load Audio ─┬→ Loop Start
            ├→ Current Shot
            ├→ Tagged Audio Ref
            └→ Assemble
```

The 0.5 graph registers media once:

```text
Load Video ─┐
            ├→ Source Timeline ─┬→ Preflight / Plan Studio
Load Audio ─┘                   └→ Loop Start → Current Shot
                                                   └→ scene slice → Tagged Audio Ref

Loop End manifest → Assemble
```

Path-backed media remains lazy. Current Shot requests only its active window,
and manifests carry the descriptor to recovery and assembly. If the only audio
input is a tensor, Loop Start materializes one normalized run-owned copy.

Do not return a Tagged Audio fingerprint derived from Current Shot to Plan;
that would create a graph cycle. Keep static picture/reference fingerprints on
Plan. Structured scene dependencies record the exact source PCM window used by
generation automatically.

## Preflight and resume

Normal workflows insert Chain Preflight before Loop Start. Studio workflows use
the same backend through Plan Studio. It validates duration, source windows,
references, compatibility, and resume eligibility without loading H3. Connect
the same active Tagged registry (or legacy Scheduled registry) used by Ref2VA
to both Chain Preflight and Loop Start. These optional sockets were appended;
they do not change the Plan JSON or rewrite prompt `@tags`.

Version 0.5 stores structured per-scene dependencies. A change to the next
scene, its incoming transition, or assembly-only media does not invalidate an
already accepted predecessor. Old checkpoints without structured records keep
their generic hash fallback.

## Compatibility guarantee

The migration tool does not renumber the sampling body or change its node
types. Loop Start's original optional socket order is preserved so
numerical 0.4 workflow slots still deserialize correctly. Legacy full-AUDIO
fan-out, direct media paths, Plan widgets, and archived examples remain
accepted throughout the 0.5 release.
