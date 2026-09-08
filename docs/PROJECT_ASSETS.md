# Project Asset Carousel

Project Asset Carousel keeps a run's pictures, video, audio, reference tags,
and Source track in one project library. Use it when separate loader and tag
nodes are making the workflow hard to manage.

## Connect it

```text
Project Asset Carousel.project_assets  ──>  Plan.project_assets
Project Asset Carousel.references      ──>  Tagged Ref2VA.references
```

If the project contains a **Source track**, Plan stores its recoverable Source
Timeline automatically. Loop Start, Plan Studio, recovery, and Assemble can
read it from the Plan; another Source Timeline wire is normally unnecessary.

## Inputs and outputs

| Side | Field | Use |
|---|---|---|
| Input widget | `run_name` | Select or create the project. It synchronizes the connected Plan's run name. |
| Input widget | Catalog and operation state | Managed by the Carousel interface; do not hand-edit for normal use. |
| Optional input | `tagged_references` | Import the existing reference carrier as Unassigned cards. |
| Optional input | `upscale_model` | Enable nondestructive model upscaling in the image editor. |
| Output | `project_assets` | Connect to Plan. Carries the project catalog, fingerprints, and Source-track recovery data. |
| Output | `references` | Connect to Tagged Ref2VA or the compact Current Tagged Ref2VA Scene. |
| Output | `reference_fingerprint` | Inspect the active reference identity; Plan includes it automatically through `project_assets`. |
| Output | `source_timeline` | Optional direct Source Timeline access for specialized graphs. |
| Output | `status` | Compact counts and the current catalog revision. |

## Add an asset

Use any of these routes from the Carousel:

- Drop one or several image, video, or audio files onto the node.
- **Upload** a new file.
- **Import** a file already in `ComfyUI/input/`.
- Choose **Other Run** to copy an asset from another live Carousel project.
- Import from an H3 run recovery backup.

An imported asset receives its own project-owned media copy. Copying from
another run preserves its role, tag, reference options, and audio lyrics; the
source project is not changed.

## Assign a role

| Role | What it does | Use it for |
|---|---|---|
| Picture reference | Makes a picture available to prompt-selected `@tag` references. | An appearance/identity photo the model should reuse. |
| Semantic anchor | Makes a picture available as a `#tag` presentation anchor. | A composition or layout guide, not a literal appearance to copy. |
| Video reference | Gives the model the whole clip as a native `<Video N>` reference — identity, wardrobe, setting, lighting, and composition included. Activates only in scenes whose prompt includes the asset's tag. | You want the generated scene to *look like* the reference, not just move like it. |
| Motion reference | Extracts only the pose, action, and motion timing from the clip and transfers it onto a named `<Subject N>` (set in **Target Subject**); the clip is downsampled (**Reference short edge**) specifically to suppress its own appearance from leaking through. | You want a character to *move* the way the reference moves, without copying who or what is actually in it. |
| Source track | Not activated by any scene prompt. Supplies the project's recoverable Source Timeline — an exact prerecorded video or audio track (e.g. dialogue, or footage that must stay unaltered) that Chain Policy's Source reference / Final audio settings decide how to use. Only one Source track may be enabled per project. | A fixed track scenes are generated against, not a look or motion to imitate. |
| Unassigned | Keeps the card visible without using an H3 reference slot or changing the generation fingerprint. | Staging a file before deciding its role, or archiving it from active use. |

When you connect an existing `tagged_references` line, the Carousel creates
Unassigned cards for its tags and media roles. Bind each card to an input file,
upload, another project, or a backup before expecting it to take part in
generation. Move other server media into the configured ComfyUI input folder
first; browser requests cannot import arbitrary filesystem paths.

Only references used by the current scene are decoded during sampling.

### Timeline mode (Video reference and Motion reference)

Both roles expose a **Timeline mode** control that decides which part of the
reference clip a scene sees:

| Mode | What it does | Use it for |
|---|---|---|
| Restart each scene (default) | Every scene that activates this reference starts playback at frame 0 of the clip, so every activation looks identical. Never requires any particular clip length. | A short loop, or any appearance/motion reference that should stay the same across the whole run. |
| Sequential | Plays the reference forward continuously in lockstep with the Plan, starting from the first scene that activates it — later scenes see later parts of the clip. The clip must be at least as long as everything generated from that point on, or generation stops with an error telling you to shorten the Plan, supply a longer reference, or switch back to Restart each scene. | The reference is itself a continuous performance or source that should be walked through scene by scene, in step with the generated video. |

## Group a song and its stems

On an audio **Source track** card, **Synchronized audio tracks** assigns the
project's full mix, isolated vocals and optional instrumental. Import the stems
as audio assets first; they need not be available to prompts. All tracks must
start together and retain the complete song duration, including silent gaps.
Only one Source track card needs to be enabled.

The full mix supplies delivery; vocals supply source-locked lip-sync. If no
full mix is assigned, the stems are mixed automatically without doubling an
existing mix. Use each scene's **Lip-sync** selector to turn guidance on/off
without changing the final soundtrack. **Reset to single track** restores the
old behavior. See [audio routing details](AUDIO_AND_CONTINUITY.md#grouped-songs-full-mix-vocals-and-instrumental).

Copying a grouped Source track from **Other Run** also copies its assigned
tracks and remaps their IDs. Deleting a referenced stem is blocked until you
detach it from the group.

## Edit a picture

**Edit / upscale** creates a new PNG variant; it never overwrites the source.
The editor supports:

- draggable crop and placement;
- exact width and height or megapixel targets;
- locked aspect ratio;
- Lanczos, bicubic, bilinear, or nearest resampling;
- output snapping to Off, 8, 16, 32, or 64;
- **Use full image** and **Reset all**;
- model upscaling when `upscale_model` is connected.

Model upscale queues only the Carousel and its lazy model-loader dependency. It
does not launch the downstream H3 generation loop.

Every variant records its parent and transform so the original remains
available.

## Organize the library

- Duplicate a card without copying its media bytes again.
- Create folders and drag cards onto them.
- Click a folder card to expand or collapse its assets inline.
- Duplicate the complete project under a new run name when you want a separate
  production with the same library.

Folder names, order, membership, and expansion state are presentation only;
they do not change prompts or generation fingerprints.

Selecting an audio asset opens a lyrics workspace. Lyrics are saved with the
catalog and recovery backup, but remain notes: they are not added to prompts or
generation fingerprints.

## Storage and recovery

Project-owned input media is stored under:

```text
ComfyUI/input/h3_projects/<run_name>/
```

Recovery copies and catalog metadata are stored with the run under:

```text
ComfyUI/output/h3_chains/<run_name>/project_assets/
```

The workflow JSON keeps compact catalog metadata rather than embedding media.
The run backup lets the project be restored if an input binding is moved or the
workflow is opened on another system.

Deleting a generated run through Checkpoint Manager does not delete the
original project-owned assets under `ComfyUI/input/h3_projects/`.

For prompt tag behavior, see [Scheduled references](SCHEDULED_REFERENCES.md).
For run backups and recovery, see [Runs and recovery](RUNS_AND_RECOVERY.md).
