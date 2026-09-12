# Ref2V Tagged Source Audio - MiniMax H3 0.6

Adapted for **nightly** from the released **0.6 workflow catalog**. H3 settings and sockets are validated against nightly.

Setup controls come first, followed by numbered generation columns. Recovery is disabled by default. Enable it only to assemble saved clips without sampling.

0.6 ORIGINAL REFERENCE EXAMPLE

The matching courier and greenhouse pictures were generated specifically for this catalog. They replace the old community demonstration assets and prompts.

---

REF2VA PROMPT FORMAT — SIX SECTIONS

Keep subject_definitions, summary, retention_analysis, detailed_description, overall_soundscape, and non_diegetic_music in that order. References define identity; the prompt defines the new action.

---

REF2V 0.6 QUICK START

Copy both courier PNGs to ComfyUI/input. The two picture references are activated as courier_arrival and greenhouse_delivery. Edit the Plan, then queue and approve each checkpointed scene.

---

The two amber recovery nodes are MUTED by default.

If all segments finished but final assembly did not, mute the main green Assemble node, enable both recovery nodes, and queue. They validate every SHA-256 checkpoint pair and assemble without rerendering the completed clips.

For an interrupted generation, leave recovery muted and set Loop Start's start_clip to the first unfinished clip.

---

INDEPENDENT SOURCE AUDIO — NO CAROUSEL

Upload/select your complete soundtrack in Load Audio. Copy the two supplied courier/greenhouse pictures into ComfyUI/input, or replace the Load Image selections and matching prompt tags. The soundtrack must cover the complete planned timeline; adjust scene lengths to your track.

Load Audio feeds Source Timeline and Tagged Audio Ref with the same FULL track. Source Timeline feeds both Preflight and Loop Start. Current Scene.state feeds Tagged Ref2VA.state, so source_timeline references resolve the correct window on every scene. Never feed Current Scene.source_audio_slice into Tagged Audio Ref: its fingerprint connection back to the Plan would create a cycle. Leave the legacy source_audio sockets disconnected.

Lip-sync to source audio locks the scene's exact source audio and uses the source track in final assembly. The preset alone does not load audio. @soundtrack is available as an optional prompt reference; the supplied picture prompts do not use it. For a voice reference only (not a timed soundtrack), use Tagged Audio Ref timeline_mode=standalone with a generated-audio profile; Source Timeline is not required.

For an existing Ref2V Tagged workflow, add the same source connections and connect Current Scene.state to Tagged Ref2VA.state.
