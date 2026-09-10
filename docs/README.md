# Documentation

Start with the page that matches what you are doing now. You do not need to
read the implementation references to run a normal workflow.

## New here

1. [Getting started](GETTING_STARTED.md) — install, open an example, render,
   review, and find the result.
2. [Workflow catalog](../example_workflows/README.md) — choose the smallest
   example for your task.
3. [Node guide](NODE_REFERENCE.md) — understand the visible nodes, sockets, and
   disabled-node notation.

## Guides by task

| I need to… | Read this |
|---|---|
| Write or reorder scenes | [Scene authoring](SCENE_AUTHORING.md) |
| Manage pictures, video, audio, and Source-track assets | [Project Asset Carousel](PROJECT_ASSETS.md) |
| Choose continuity or audio behavior | [Audio and continuity](AUDIO_AND_CONTINUITY.md) |
| Use image, video, motion, or audio references | [Scheduled references](SCHEDULED_REFERENCES.md) |
| Review takes, resume, recover, or assemble | [Runs and recovery](RUNS_AND_RECOVERY.md) |
| Switch working Plans, fork, or start a fresh set (nightly) | [Working branches](WORKING_BRANCHES.md) |
| Use the maintained memory-safe top-level prompt lifecycle | [Maintained workflow](MAINTAINED_WORKFLOW.md) |
| Inpaint, outpaint, extend, or bridge video | [Masked editing](MASKED_EDITING.md) |
| Extend an existing video or use special context | [Advanced workflows](ADVANCED_WORKFLOWS.md) |
| Fix a missing-node or runtime compatibility problem | [Compatibility](COMPATIBILITY.md) |
| Move an older workflow to the current contract | [Migrating to 0.5](MIGRATING_TO_0_5.md) |

## Reference material

These pages are useful when you need exact behavior rather than a first-run
explanation.

| Reference | Contents |
|---|---|
| [Complete Plan format](../H3_CHAIN_FORMAT_GUIDE.md) | Every Plan and per-scene field, exact timing, prompt syntax, and JSON forms |
| [Version 0.5 architecture](V0_5_ARCHITECTURE.md) | Frozen Source Timeline, policy, dependency, migration, and preflight contracts |
| [Chain storage audit](STORAGE_LAYOUT_AUDIT.md) | Current folder ownership, measured storage, path complexity and a staged simplification proposal |
| [Storage migration plan](STORAGE_MIGRATION_PLAN.md) | Compatibility invariants, feature tests, phased implementation, existing-folder migration and rollback gates |
| [Storage Inspector](STORAGE_INSPECTOR.md) | Read-only disk inventory, report format, consumer baseline and remaining migration gates |
| [Reference Video Fade](REFERENCE_VIDEO_FADE.md) | Experimental denoising-time control of Ref2VA video influence |
| [Visual-context drift research](VISUAL_CONTEXT_DRIFT_RESEARCH.md) | Evidence, experiments, and validation protocol for recursive drift |
| [Feature traceability](FEATURE_TRACEABILITY.md) | Origins, upstream links, implementation files, and commit evidence |

## Project information

- [Changelog](../CHANGELOG.md)
- [Third-party credits and licenses](../THIRD_PARTY_NOTICES.md)
- [Contributing](../CONTRIBUTING.md)
- [Example asset licenses](../example_workflows/assets/README.md)
- [Dormant Prompt Assistant design study](../AGENT_PROMPT_ASSISTANT_STUDY.md)

### Terms used in these docs

- **Scene** — one planned H3 generation.
- **Run** — one named production and its saved checkpoint history.
- **Segment** — the delivered media saved for one scene.
- **Manifest** — the verified list of selected segments used for recovery or
  final assembly.
- **Muted node** — present in the workflow but not executed.
- **Bypassed node** — present, but forwarding a compatible input instead of
  applying its normal operation.
