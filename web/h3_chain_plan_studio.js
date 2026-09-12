import {app} from "/scripts/app.js";
import {api} from "/scripts/api.js";
import {StudioBranches, BranchDrafts, branchOperationId, branchWidgetTransaction, branchRequestPath, workingBranchId} from "./h3_working_branches.mjs?v=0.7.19";
import {branchPolicyNodes, captureBranchPolicyInputs, restoreBranchPolicyInputs} from "./h3_plan_restore_core.mjs?v=0.7.19";
import {
    CONTINUATION_MODES,
    FPS,
    H3_CONTEXT_LENGTHS,
    MAX_H3_FRAMES,
    MAX_SEED,
    MAX_SHOTS,
    automaticSceneColor,
    audioContextLeadFrameOptions,
    audioContextWindowStarts,
    calculatePlanTiming,
    duplicateShot,
    formatClock,
    makeChapter,
    makeShot,
    moveShot,
    nativeContextWindowStarts,
    normalizeChapterResolution,
    nearestNativeContextWindowStart,
    parsePlanJson,
    planDefaultSteps,
    setPlanDefaultSteps,
    clearSceneStepOverrides,
    planToJson,
    orderedChapters,
    promptTextToLines,
    promptValueToText,
    randomSceneSeed,
    removePlanShot,
    safeShotId,
    sceneAudioContextLeadFrames,
    sceneAudioContextLeadSource,
    sceneAudioContextSource,
    sceneAudioContextStartFrame,
    sceneAudioContextUnlocked,
    sceneContextLength,
    sceneContinuationMode,
    sceneLoRARoute,
    scenePromptSeedMode,
    renamePlanShot,
    sceneVisualContextBlocks,
    sceneVisualContextLeadFrames,
    sceneVideoBlendFrames,
    setScenePromptSeedMode,
    setSharedPrompt,
    setShotLengthMode,
    sharedPrompt,
    shotLengthMode,
    visualContextCompositions,
    visualContextBoundaryFrames,
    visualContextDefaultPartition,
    visualContextMaximumBlocks,
    visualContextPartitionFromBoundaries,
} from "./h3_chain_plan_core.mjs?v=0.7.9";
import {
    promptRevisionHelp,
    promptRevisionLabel,
    promptRevisionNavigation,
} from "./h3_prompt_history_core.mjs?v=0.7.0";
import {
    availableReferenceRecords,
    convertTaggedPictureReference,
    taggedPictureReferenceMode,
    taggedPictureReferenceToken,
} from "./h3_reference_preview_core.mjs?v=0.7.3";
import {
    applySceneAudioOverride,
    applySceneLipSync,
    sceneLipSyncMode,
    applySceneTransitionPreset,
    primaryTransitionOptions,
    sceneAudioOverride,
    sceneAudioPolicy,
    sceneTransitionPreset,
    transitionPresetLabel,
} from "./h3_policy_core.mjs?v=0.7.9";
import {
    resolveAudioContextLength,
    resolveAudioPolicy,
    resolveTransitionPolicy,
} from "./h3_socket_presentation_core.mjs?v=0.7.9";
import {
    availableLoRARoutes,
    loraRouteLabel,
} from "./h3_lora_scheduler_core.mjs?v=0.7.0";
import {
    h3StudioGridMarkers,
    locateStudioTimelineSegment,
    matchingStudioCheckpoint,
    matchingStudioSourceAudio,
    matchingStudioSourceScene,
    parseStudioTimecode,
    remapStudioEditorialSceneId,
    studioCheckpointSignature,
    restoreStudioCheckpointCache,
    studioCheckpointCacheSnapshot,
    studioContextWindowLayout,
    studioContextWindowStartAtRatio,
    parseTimedLyrics,
    studioEditorialSceneStartSeconds,
    studioLatentSafeOutFrames,
    studioNearestLatentSafeOutFrame,
    studioNearestH3FrameLength,
    studioPlayerSegmentClock,
    studioSourceAudioSecond,
    studioSourceSecond,
    studioTimelineLayout,
    studioTimelinePixelAtSecond,
    studioTimelineScrollAnchorSeconds,
    studioTimelineScrollLeftForAnchor,
    studioTimelineSegments,
    studioTimelineTotalSeconds,
    studioRulerTicks,
    studioWaveformIntervalSamples,
    timedLyricAtSecond,
} from "./h3_chain_plan_studio_core.mjs?v=0.7.6";
import * as promptCompanionSync from "./h3_prompt_companion_sync.mjs?v=0.7.2";
import {
    projectMutationOptions, subscribeProjectOwnership, isProjectReadOnlyError,
} from "./h3_project_ownership.mjs?v=0.7.4";

const {
    connectedPromptEditors,
    publishCompanionScene,
} = promptCompanionSync;
const planHasNonPromptChanges =
    typeof promptCompanionSync.planHasNonPromptChanges === "function"
        ? promptCompanionSync.planHasNonPromptChanges
        : () => true;
function publishCompanionPrompt(...args) {
    return promptCompanionSync.publishCompanionPrompt?.(...args) ?? 0;
}

const NODE_NAME = "MiniMaxH3ChainPlanStudio";
const PLAN_NAME = "MiniMaxH3ChainPlan";
const MODERN_PLAN_NAME = "MiniMaxH3ChainPlanModern";
const PLAN_NAMES = new Set([PLAN_NAME, MODERN_PLAN_NAME]);
const ACTIVE_PROPERTY = "h3_plan_studio_active_scene";
const ACTIVE_CHAPTER_PROPERTY = "h3_plan_studio_active_chapter";
const VIEW_PROPERTY = "h3_plan_studio_view";
const PROMPT_TAKE_TAB_PROPERTY = "h3_plan_studio_prompt_take_tab";
let promptTakeTabsSerial = 0;
const TIMELINE_ZOOM_PROPERTY = "h3_plan_studio_timeline_zoom";
const SOURCE_AUDIO_MUTES_PROPERTY = "h3_plan_studio_source_audio_mutes";
const GENERATED_VOLUME_PROPERTY = "h3_plan_studio_generated_volume";
const SOURCE_VOLUME_PROPERTY = "h3_plan_studio_source_volume";
const MOTION_VOLUME_PROPERTY = "h3_plan_studio_motion_volume";
const CHECKPOINT_CACHE_PROPERTY = "h3_plan_studio_checkpoint_cache_v1";
const MIN_WIDTH = 820;
const MIN_HEIGHT = 690;
const PLAN_SETTING_WIDGETS = Object.freeze([
    "plan_json", "run_name", "generation_fingerprint", "width", "height",
    "context_length", "encode_mode", "anchor_mode", "crop", "audio_mode",
    "audio_context_length", "default_duration_seconds", "default_steps",
    "base_seed", "segment_crf", "video_blend_frames", "continuation_mode",
]);

function monitorVolume(value, fallback = 1) {
    const number = Number(value);
    return Number.isFinite(number)
        ? Math.max(0, Math.min(1, number)) : fallback;
}

function normalizedTimelineZoom(value) {
    const number = Number(value);
    return Number.isFinite(number) ? Math.max(1, Math.min(6, number)) : 1;
}

function injectStyles() {
    if (document.getElementById("h3-plan-studio-style")) return;
    const style = document.createElement("style");
    style.id = "h3-plan-studio-style";
    style.textContent = `
        .h3studio {
            --hs-bg:color-mix(in srgb,var(--comfy-menu-bg,#202124) 92%,#101827);
            --hs-panel:color-mix(in srgb,var(--comfy-input-bg,#111827) 84%,#263552);
            --hs-border:color-mix(in srgb,var(--border-color,#555) 68%,#7891bf);
            --hs-text:var(--input-text,#eef1f7);
            /* Follow the foreground palette for contrast in both themes,
               including changes made while this Studio is already open. */
            --hs-muted:color-mix(in srgb,var(--hs-text) 82%,var(--hs-panel));
            --hs-accent:color-mix(in srgb,var(--hs-text) 70%,#3979da);
            --hs-selected:color-mix(in srgb,var(--hs-accent) 18%,var(--hs-panel));
            --hs-success:color-mix(in srgb,var(--hs-text) 70%,#219653);
            --hs-warning:color-mix(in srgb,var(--hs-text) 75%,#c88a26);
            --hs-danger:color-mix(in srgb,var(--hs-text) 70%,#e53935);
            --hs-alternate:color-mix(in srgb,var(--hs-text) 75%,#8c54d8);
            /* Video surfaces stay dark, regardless of the surrounding form. */
            --hs-media-text:#eef1f7; --hs-media-muted:#dce5f7;
            box-sizing:border-box; width:100%; height:100%; min-height:540px;
            display:flex; flex-direction:column; gap:8px; overflow:hidden; padding:10px;
            border:1px solid var(--hs-border); border-radius:8px; background:var(--hs-bg);
            color:var(--hs-text); font:12px/1.35 system-ui,sans-serif;
        }
        .h3studio *, .h3studio *::before, .h3studio *::after { box-sizing:border-box; }
        .h3studio button,.h3studio input,.h3studio select,.h3studio textarea {
            color:var(--hs-text); font:inherit; border:1px solid var(--hs-border);
            border-radius:5px; background:var(--comfy-input-bg,#15171d);
        }
        .h3studio button { padding:5px 8px; cursor:pointer; white-space:nowrap; }
        .h3studio button:hover,.h3studio button.h3studio-active { border-color:var(--hs-accent); }
        .h3studio button.h3studio-active { color:var(--hs-text); background:var(--hs-selected); }
        .h3studio button:disabled { opacity:.4; cursor:not-allowed; }
        .h3studio input,.h3studio select,.h3studio textarea { width:100%; min-width:0; padding:6px 7px; }
        .h3studio textarea { resize:vertical; line-height:1.5; }
        .h3studio-head,.h3studio-toolbar,.h3studio-statusline,.h3studio-scene-head,
        .h3studio-form,.h3studio-history,.h3studio-json-actions,.h3studio-player-controls {
            display:flex; align-items:center; gap:6px;
        }
        .h3studio-head { justify-content:space-between; }
        .h3studio-title { color:var(--hs-accent); font-size:15px; font-weight:750; }
        .h3studio-run { min-width:0; color:var(--hs-muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .h3studio-toolbar { flex-wrap:wrap; }
        .h3studio-spacer { flex:1; }
        .h3studio-statusline { color:var(--hs-muted); flex-wrap:wrap; min-height:18px; }
        .h3studio-statusline strong { color:var(--hs-text); }
        .h3studio-timeline-shell { flex:0 0 auto; padding:7px; border:1px solid var(--hs-border);
            border-radius:7px; background:var(--hs-panel); }
        .h3studio-timeline-tools { display:flex; align-items:center; gap:5px; min-height:28px; color:var(--hs-muted); }
        .h3studio-timeline-tools strong { color:var(--hs-text); }
        .h3studio-timeline-zoom { width:118px !important; padding:0 !important; }
        .h3studio-timeline-grid { display:grid; grid-template-columns:78px minmax(0,1fr); gap:5px; min-width:0; }
        .h3studio-timeline-labels,.h3studio-timeline-content { display:grid;
            grid-template-rows:30px 76px 76px 34px 30px; row-gap:4px; align-items:stretch; }
        .h3studio-timeline-labels { color:var(--hs-muted); font-size:9px; font-weight:750;
            letter-spacing:.06em; text-align:right; }
        .h3studio-timeline-labels span { display:flex; align-items:center; justify-content:flex-end; }
        .h3studio-timeline-viewport { min-width:0; overflow-x:auto; overflow-y:hidden; padding-bottom:2px; }
        .h3studio-timeline-content { min-width:100%; }
        .h3studio-ruler { position:relative; height:30px; width:100%; border-bottom:1px solid var(--hs-border);
            color:var(--hs-muted); font-size:10px; overflow:visible; cursor:crosshair; touch-action:none; }
        .h3studio-ruler-tick { position:absolute; bottom:0; width:1px; height:7px; background:var(--hs-border); pointer-events:none; }
        .h3studio-ruler-tick.h3studio-major { height:13px; background:color-mix(in srgb,var(--hs-text) 55%,transparent); }
        .h3studio-ruler-tick span { position:absolute; left:3px; bottom:12px; white-space:nowrap; font-variant-numeric:tabular-nums; }
        .h3studio-ruler-hover { position:absolute; z-index:12; top:1px; padding:2px 5px; border-radius:4px;
            color:#fff; background:rgba(7,9,14,.88); pointer-events:none; font-variant-numeric:tabular-nums; transform:translateX(-50%); }
        .h3studio-timeline { position:relative; display:flex; gap:0; width:100%; min-width:0; min-height:0; overflow:hidden; }
        .h3studio-generated-timeline { overflow:visible; }
        .h3studio-chapter-marker { position:absolute; z-index:8; top:-2px; bottom:0; width:0;
            padding:0 !important; border:0 !important; border-left:2px solid var(--hs-warning) !important;
            border-radius:0 !important; background:transparent !important; overflow:visible; }
        .h3studio-chapter-marker span { position:absolute; top:3px; left:4px; max-width:112px;
            overflow:hidden; text-overflow:ellipsis; white-space:nowrap; padding:2px 6px;
            border:1px solid var(--hs-warning); border-radius:999px; color:var(--hs-warning);
            background:var(--hs-bg); font-size:9px; font-weight:750; }
        .h3studio-chapter-marker.h3studio-selected span { color:var(--hs-text); border-color:var(--hs-warning);
            box-shadow:0 0 0 1px var(--hs-warning) inset; }
        .h3studio-card { --scene:#84aaff; position:relative; isolation:isolate;
            flex:0 0 var(--h3-scene-width,138px); min-width:0;
            height:70px; overflow:hidden; padding:0 !important; text-align:left; border:1px solid var(--scene) !important;
            border-radius:5px; color:var(--hs-text); cursor:grab; font:inherit; user-select:none; touch-action:none;
            background:color-mix(in srgb,var(--scene) 13%,var(--comfy-input-bg,#15171d)) !important; }
        .h3studio-card.h3studio-moving { cursor:grabbing; }
        .h3studio-card.h3studio-selected { box-shadow:0 0 0 2px var(--scene) inset; }
        .h3studio-card.h3studio-alternate-selected::before { content:"ALT"; position:absolute;
            z-index:4; right:17px; top:4px; padding:1px 4px; border:1px solid #b493f0;
            border-radius:3px; color:#eadfff; background:rgba(45,25,78,.88); font-size:8px; font-weight:800; }
        .h3studio-card video, .h3studio-card-thumbnail { position:absolute; inset:0; width:100%; height:100%; object-fit:cover;
            opacity:.58; z-index:-1; background:#08090c; pointer-events:none; }
        .h3studio-card::after { content:""; position:absolute; inset:0; z-index:-1;
            background:linear-gradient(180deg,transparent 10%,rgba(5,7,12,.88)); }
        .h3studio-card-copy { position:absolute; inset:auto 7px 6px; overflow:hidden; color:var(--hs-media-text); }
        .h3studio-drag-handle { position:absolute; z-index:3; left:4px; top:4px; padding:1px 4px;
            border-radius:3px; color:#fff; background:rgba(5,7,12,.72); cursor:grab; user-select:none; }
        .h3studio-drag-handle:active { cursor:grabbing; }
        .h3studio-lock-handle { position:absolute; z-index:4; left:29px; top:4px; width:22px; height:19px;
            padding:0 !important; border:1px solid rgba(255,255,255,.24) !important; border-radius:3px; color:#fff;
            background:rgba(5,7,12,.72) !important; cursor:pointer; user-select:none; }
        .h3studio-lock-handle.h3studio-is-locked { color:#ffd995;
            border-color:#c59745 !important; }
        .h3studio-lock-icon { position:relative; display:block; width:12px; height:13px; margin:auto; }
        .h3studio-lock-icon::after { content:""; position:absolute; left:2px; bottom:1px; width:8px; height:7px;
            box-sizing:border-box; border:1.5px solid currentColor; border-radius:2px; }
        .h3studio-lock-icon::before { content:""; position:absolute; left:3.5px; top:0; width:5px; height:7px;
            box-sizing:border-box; border:1.5px solid currentColor; border-bottom:0; border-radius:5px 5px 0 0; }
        .h3studio-lock-handle:not(.h3studio-is-locked) .h3studio-lock-icon::before {
            left:7px; transform:rotate(24deg); transform-origin:left bottom; }
        .h3studio-resize-handle { position:absolute; z-index:5; top:0; right:0; width:8px; height:100%;
            border-right:2px solid color-mix(in srgb,var(--scene) 78%,#fff); cursor:ew-resize;
            touch-action:none; opacity:.72; }
        .h3studio-resize-handle:hover, .h3studio-resize-handle:active { width:12px; opacity:1;
            background:linear-gradient(90deg,transparent,color-mix(in srgb,var(--scene) 28%,transparent)); }
        .h3studio-resize-handle.h3studio-latent-trim { border-right-color:#62e1d1;
            background:linear-gradient(90deg,transparent,rgba(98,225,209,.16)); }
        .h3studio-resize-handle.h3studio-latent-trim::before { content:""; position:absolute;
            right:1px; top:50%; width:0; height:0; transform:translateY(-50%);
            border-top:5px solid transparent; border-bottom:5px solid transparent;
            border-right:5px solid #62e1d1; }
        .h3studio-card.h3studio-locked { border-style:dashed !important; cursor:pointer; }
        .h3studio-card.h3studio-locked .h3studio-drag-handle,
        .h3studio-card.h3studio-locked .h3studio-resize-handle { cursor:not-allowed; opacity:.38; }
        .h3studio-card-title { display:block; font-weight:750; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .h3studio-card-meta { display:block; color:var(--hs-media-muted); font-size:10px; }
        .h3studio-render-dot { position:absolute; right:6px; top:6px; width:8px; height:8px; border-radius:50%;
            background:#687080; box-shadow:0 0 0 1px #111; }
        .h3studio-rendered .h3studio-render-dot { background:#62d58b; }
        .h3studio-continuation-stale .h3studio-render-dot { background:#f1a44c; }
        .h3studio-source-card { border-style:dashed !important; }
        .h3studio-source-card .h3studio-render-dot { background:#b58cff; }
        .h3studio-source-empty { display:flex; align-items:center; padding:0 10px; color:var(--hs-muted);
            min-height:48px; border:1px dashed var(--hs-border); border-radius:5px; }
        .h3studio-audio-timeline { min-height:34px; }
        .h3studio-audio-card { --scene:#84aaff; position:relative;
            flex:0 0 var(--h3-scene-width,138px); min-width:0;
            height:34px; overflow:hidden; border:1px solid color-mix(in srgb,var(--scene) 65%,var(--hs-border));
            border-radius:5px; background:color-mix(in srgb,var(--scene) 8%,var(--comfy-input-bg,#15171d)); cursor:pointer; }
        .h3studio-audio-card.h3studio-selected { box-shadow:0 0 0 1px var(--scene) inset; }
        .h3studio-audio-card.h3studio-audio-muted { opacity:.48; }
        .h3studio-waveform { position:absolute; inset:3px 34px 3px 4px; width:calc(100% - 38px); height:28px; }
        .h3studio-audio-mute { position:absolute; z-index:2; right:3px; top:3px; width:28px; height:26px;
            padding:0 !important; display:grid; place-items:center; font-size:13px; }
        .h3studio-playhead { position:absolute; z-index:20; top:0; width:1px; height:262px;
            background:#ff626a; box-shadow:0 0 0 1px rgba(82,12,17,.28); pointer-events:none; }
        .h3studio-gap { flex:0 0 var(--h3-scene-width,0); min-width:0; height:100%; overflow:hidden;
            color:#b7bdc8; background:#030405; border:1px dashed #697080; cursor:pointer; }
        .h3studio-gap-copy { display:flex; height:100%; align-items:center; justify-content:center;
            padding:4px; text-align:center; font-size:10px; white-space:nowrap; overflow:hidden; }
        .h3studio-gap-spacer { flex:0 0 var(--h3-scene-width,0); min-width:0; height:100%;
            background:repeating-linear-gradient(135deg,rgba(160,170,188,.07) 0 4px,transparent 4px 8px); }
        .h3studio-subtitle-timeline { position:relative; min-height:30px; }
        .h3studio-subtitle-cue { position:absolute; top:2px; bottom:2px; overflow:hidden; padding:4px 6px;
            border:1px solid var(--hs-alternate); border-radius:4px; color:var(--hs-alternate);
            background:color-mix(in srgb,var(--hs-alternate) 10%,var(--hs-panel));
            font-size:9px; text-overflow:ellipsis; white-space:nowrap; }
        .h3studio-panel { flex:1 1 auto; min-height:0; overflow:auto; padding:9px;
            border:1px solid var(--hs-border); border-radius:7px; background:var(--hs-panel); }
        .h3studio-scene-head { margin-bottom:7px; }
        .h3studio-grid-markers { display:flex; align-items:center; gap:5px; flex-wrap:wrap; margin-left:auto; }
        .h3studio-grid-marker { padding:2px 6px; border:1px solid var(--hs-border); border-radius:999px;
            color:var(--hs-muted); background:color-mix(in srgb,var(--hs-panel) 82%,transparent); font-size:9px; }
        .h3studio-grid-marker.h3studio-grid-exact { color:var(--hs-success); border-color:var(--hs-success); }
        .h3studio-grid-marker.h3studio-grid-warning { color:var(--hs-warning); border-color:var(--hs-warning); }
        .h3studio-grid-marker.h3studio-grid-experimental { border-style:dashed; }
        .h3studio-scene-label { color:var(--hs-muted); }
        /* Reflow against the node's width, not the browser viewport. Each cell
           must also fit the multi-part seed and editorial controls. */
        .h3studio-form { align-items:end; display:grid; gap:8px;
            grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr)); margin-bottom:8px; }
        .h3studio-audio-overrides { display:grid; grid-template-columns:repeat(3,minmax(160px,1fr));
            gap:7px; margin:0 0 8px; align-items:end; }
        .h3studio-field { display:flex; min-width:0; flex-direction:column; gap:3px; color:var(--hs-muted); }
        .h3studio-prompt-take-tabs { display:flex; gap:6px; margin-bottom:8px; }
        .h3studio-prompt-take-tabs [aria-selected="true"] { color:var(--hs-accent);
            border-color:var(--hs-accent); }
        .h3studio-prompt-takes > [role="tabpanel"][hidden] { display:none; }
        .h3studio-alternate { margin:0 0 9px; padding:9px; border:1px solid var(--hs-alternate);
            border-radius:7px; background:color-mix(in srgb,var(--hs-panel) 94%,var(--hs-alternate)); }
        .h3studio-alternate-title { display:flex; align-items:center; gap:7px; margin-bottom:4px; }
        .h3studio-alternate-enable { display:flex; align-items:center; gap:5px; margin:8px 0;
            color:var(--hs-text); }
        .h3studio-alternate-enable input { width:auto; }
        .h3studio-alternate-grid { display:grid; grid-template-columns:minmax(260px,1fr) 180px;
            gap:8px; margin-top:7px; align-items:end; }
        .h3studio-alternate-prompt { min-height:105px; }
        .h3studio-alternate-diff { margin:6px 0; color:var(--hs-alternate); font:11px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace;
            white-space:pre-wrap; overflow-wrap:anywhere; }
        .h3studio-plan-settings { display:grid; grid-template-columns:repeat(3,minmax(170px,1fr));
            gap:9px; align-items:end; }
        .h3studio-plan-settings-section { grid-column:1 / -1; margin-top:5px; padding-top:7px;
            border-top:1px solid var(--hs-border); color:var(--hs-accent); font-weight:750; }
        .h3studio-plan-defaults-help { grid-column:1 / -1; color:var(--hs-muted); font-size:10px; }
        .h3studio-length { display:grid; min-width:0; grid-template-columns:minmax(0,1fr) auto; gap:5px; }
        .h3studio-duration { grid-template-columns:minmax(0,1fr) minmax(0,1fr); }
        .h3studio-prompt-seed { display:grid; min-width:0;
            grid-template-columns:minmax(0,160px) minmax(0,1fr) auto; gap:5px; }
        .h3studio-context-pair { display:grid; grid-template-columns:1fr 1fr; gap:5px; }
        .h3studio-prompt { min-height:250px; width:100%; font:15px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace !important; }
        .h3studio-prompt-tools { display:flex; align-items:center; gap:6px; margin:7px 0; flex-wrap:wrap; }
        .h3studio-prompt-delegated { margin-top:10px; padding:12px; border:1px dashed var(--hs-border);
            border-radius:7px; color:var(--hs-muted); background:var(--hs-bg); }
        .h3studio-prompt-delegated strong { display:block; margin-bottom:4px; color:var(--hs-text); }
        .h3studio-hint,.h3studio-message { color:var(--hs-muted); }
        .h3studio-history { justify-content:center; min-height:26px; }
        .h3studio-history-count { min-width:44px; text-align:center; font-variant-numeric:tabular-nums; }
        .h3studio-history-meta { max-width:300px; color:var(--hs-muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .h3studio-error { color:var(--hs-danger); white-space:pre-wrap; }
        .h3studio-shared { min-height:260px; font:15px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace !important; }
        .h3studio-defaults { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:8px; max-width:390px; }
        .h3studio-chapter-settings { display:flex; flex-wrap:wrap; align-items:flex-end; gap:8px; margin-top:8px; }
        .h3studio-chapter-settings > .h3studio-field { flex:1 1 180px; min-width:0; }
        .h3studio-chapter-resolution { display:flex; flex:2 1 344px; flex-wrap:wrap; align-items:flex-end; gap:8px; min-width:0; }
        .h3studio-chapter-resolution > .h3studio-field { flex:1 1 72px; min-width:0; }
        .h3studio-chapter-resolution > .h3studio-field:first-child { flex:2 1 160px; }
        .h3studio-json { min-height:360px; font:13px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace !important; }
        .h3studio-json-actions { margin-top:7px; }
        .h3studio-player { display:flex; flex-direction:column; gap:7px; height:100%; min-height:330px; }
        .h3studio-compare-stage { position:relative; flex:1 1 auto; min-height:260px; overflow:hidden;
            background:#050608; border-radius:6px; }
        .h3studio-compare-stage video { position:absolute; inset:0; width:100%; height:100%; background:#050608; object-fit:contain; }
        .h3studio-handoff-frame { position:absolute; inset:0; width:100%; height:100%; opacity:1;
            pointer-events:none; transition:opacity .16s ease-out; }
        .h3studio-handoff-frame.h3studio-handoff-release { opacity:0; }
        .h3studio-source-layer { position:absolute; inset:0; overflow:hidden; pointer-events:none; }
        .h3studio-source-layer video { width:100%; height:100%; }
        .h3studio-wipe-line { position:absolute; top:0; bottom:0; width:2px; background:#d2b8ff;
            box-shadow:0 0 0 1px rgba(0,0,0,.45); pointer-events:none; }
        .h3studio-compare-label { position:absolute; top:7px; z-index:2; padding:3px 6px; border-radius:4px;
            background:rgba(5,7,12,.7); color:#fff; font-size:10px; pointer-events:none; }
        .h3studio-compare-label-generated { right:7px; }
        .h3studio-compare-label-source { left:7px; }
        .h3studio-subtitle-overlay { position:absolute; z-index:5; left:7%; right:7%; bottom:7%;
            padding:6px 10px; color:#fff; background:rgba(0,0,0,.72); border-radius:5px;
            text-align:center; font-size:clamp(15px,2.1vw,25px); font-weight:700; line-height:1.3;
            text-shadow:0 1px 2px #000; white-space:pre-wrap; pointer-events:none; }
        .h3studio-subtitle-settings { display:grid; grid-template-columns:minmax(220px,1fr) 160px 160px;
            gap:9px; align-items:end; }
        .h3studio-subtitle-list { display:flex; flex-direction:column; gap:4px; margin-top:10px; }
        .h3studio-subtitle-row { display:grid; grid-template-columns:150px minmax(0,1fr); gap:8px;
            padding:5px 7px; border-bottom:1px solid var(--hs-border); }
        .h3studio-player-controls input[type=range] { flex:1; padding:0; }
        .h3studio-context-selector { display:flex; flex-direction:column; gap:9px; }
        .h3studio-context-tabs { display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
        .h3studio-context-tabs button.h3studio-context-tab-active { border-color:var(--hs-accent);
            color:var(--hs-text); background:var(--hs-selected); }
        .h3studio-context-tabs .h3studio-context-lock { margin-left:auto; }
        .h3studio-context-audio-settings { display:grid;
            grid-template-columns:repeat(3,minmax(170px,1fr)); gap:7px; align-items:end; }
        .h3studio-context-builder-settings { display:grid;
            grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:7px; align-items:end;
            padding:8px; border:1px solid var(--hs-border); border-radius:7px; }
        .h3studio-context-cuts { display:flex; flex-wrap:wrap; gap:6px; align-items:end; }
        .h3studio-context-cuts label { min-width:130px; flex:1 1 130px; }
        .h3studio-context-audio { width:100%; margin:4px 0 7px; }
        .h3studio-context-help { color:var(--hs-muted); }
        .h3studio-context-blocks { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:9px; }
        .h3studio-context-block { min-width:0; padding:8px; border:1px solid var(--hs-border);
            border-radius:7px; background:var(--hs-bg); }
        .h3studio-context-block-head { display:flex; align-items:baseline; justify-content:space-between;
            gap:8px; margin-bottom:7px; }
        .h3studio-context-block-head span { color:var(--hs-muted); overflow:hidden;
            text-overflow:ellipsis; white-space:nowrap; }
        .h3studio-context-video { display:block; width:100%; min-height:180px; max-height:330px;
            object-fit:contain; background:#050608; border-radius:6px; }
        .h3studio-context-empty { display:grid; place-items:center; width:100%; min-height:180px;
            padding:18px; color:var(--hs-media-muted); text-align:center; border:1px dashed var(--hs-border);
            border-radius:6px; background:#050608; }
        .h3studio-context-range { display:flex; flex-direction:column; gap:5px; margin-top:7px; }
        .h3studio-context-movie-track { position:relative; width:100%; height:38px; overflow:hidden;
            border:1px solid var(--hs-border); border-radius:6px; cursor:pointer; touch-action:none;
            background:repeating-linear-gradient(90deg,#151922 0,#151922 11px,#0e1118 11px,#0e1118 13px); }
        .h3studio-context-movie-track:focus-visible { outline:2px solid #79a7ff; outline-offset:2px; }
        .h3studio-context-window { position:absolute; top:3px; bottom:3px; min-width:1px;
            box-sizing:border-box; border:2px solid #7ec8ff; border-radius:5px;
            background:rgba(47,143,220,.42); box-shadow:0 0 0 1px rgba(0,0,0,.55) inset;
            display:grid; place-items:center; overflow:hidden; color:#fff; font-size:10px;
            font-variant-numeric:tabular-nums; cursor:grab; user-select:none; }
        .h3studio-context-movie-track.h3studio-dragging .h3studio-context-window { cursor:grabbing; }
        .h3studio-context-window::before,.h3studio-context-window::after { content:"";
            position:absolute; top:6px; bottom:6px; width:2px; background:rgba(255,255,255,.78); }
        .h3studio-context-window::before { left:4px; }
        .h3studio-context-window::after { right:4px; }
        .h3studio-context-phase-tail { position:absolute; z-index:0; top:0; bottom:0;
            pointer-events:none; border-left:1px dashed #d39a50;
            background:repeating-linear-gradient(135deg,rgba(211,154,80,.24) 0,
                rgba(211,154,80,.24) 4px,rgba(211,154,80,.06) 4px,
                rgba(211,154,80,.06) 8px); }
        .h3studio-context-phase-note { margin-top:4px; color:var(--hs-warning); font-size:10px; }
        .h3studio-context-playhead { position:absolute; z-index:2; top:0; bottom:0; width:2px;
            pointer-events:none; background:#ff9a3c; transform:translateX(-1px); }
        .h3studio-context-range-readout { display:flex; justify-content:space-between; gap:8px; }
        .h3studio-context-movie-length { color:var(--hs-muted); font-variant-numeric:tabular-nums; }
        .h3studio-context-range-label { min-width:150px; color:var(--hs-muted);
            font-variant-numeric:tabular-nums; text-align:right; }
        .h3studio-context-actions { display:flex; align-items:center; gap:6px; margin-top:7px;
            flex-wrap:wrap; }
        .h3studio-compare-controls { display:grid; grid-template-columns:auto minmax(100px,1fr) auto; gap:6px; align-items:center; }
        .h3studio-compare-controls.h3studio-no-motion { grid-template-columns:1fr; }
        .h3studio-audio-mix { display:flex; align-items:center; justify-content:flex-end; gap:8px; flex-wrap:wrap; }
        .h3studio-audio-control { display:flex; align-items:center; gap:4px; }
        .h3studio-audio-toggle { display:flex; align-items:center; gap:4px; color:var(--hs-muted); white-space:nowrap; }
        .h3studio-audio-toggle input { width:auto; margin:0; padding:0; }
        .h3studio-audio-volume { width:68px !important; min-width:48px !important; padding:0 !important; }
        .h3studio-audio-level { width:31px; color:var(--hs-muted); font-size:9px; text-align:right; }
        .h3studio-refs { display:none; gap:5px; padding:7px; border:1px solid var(--hs-border);
            border-radius:6px; background:var(--hs-bg); flex-wrap:wrap; }
        .h3studio-refs.h3studio-open { display:flex; }
        .h3studio-ref-entry { display:flex; align-items:stretch; gap:2px; }
        .h3studio-ref-mode { display:flex; gap:1px; }
        .h3studio-ref-mode button { min-width:28px; padding:2px 5px; font-size:10px; }
        .h3studio-ref-mode button.h3studio-selected { border-color:var(--hs-accent);
            color:var(--hs-accent); }
        .h3studio-ref-preview { flex:1 0 100%; display:grid; grid-template-columns:minmax(120px,220px) 1fr;
            gap:8px; align-items:start; color:var(--hs-muted); }
        .h3studio-ref-preview img,.h3studio-ref-preview video { width:100%; max-height:150px; object-fit:contain; background:#08090c; }
        .h3studio-ref-preview audio { width:100%; height:36px; }
        @media(max-width:760px) { .h3studio-plan-settings,.h3studio-alternate-grid,
            .h3studio-audio-overrides { grid-template-columns:1fr 1fr; }
            .h3studio-defaults,.h3studio-context-blocks { grid-template-columns:1fr; } }
    `;
    document.head.appendChild(style);
}

function element(tag, className = "", text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
}

function button(label, title, action) {
    const item = element("button", "", label);
    item.type = "button";
    item.title = title || "";
    item.addEventListener("click", action);
    return item;
}

function nodeType(node) {
    return node?.comfyClass ?? node?.type ?? null;
}

function allNodes(graph, output = []) {
    for (const node of graph?._nodes ?? []) {
        output.push(node);
        if (node.subgraph) allNodes(node.subgraph, output);
    }
    return output;
}

function upstreamPlanNode(start) {
    const queue = [start];
    const seen = new Set();
    while (queue.length) {
        const node = queue.shift();
        if (!node || seen.has(node)) continue;
        seen.add(node);
        if (node !== start && PLAN_NAMES.has(nodeType(node))) return node;
        for (const input of node.inputs ?? []) {
            if (input.link == null) continue;
            const link = node.graph?.links?.[input.link];
            const parent = link ? node.graph?.getNodeById?.(link.origin_id) : null;
            if (parent) queue.push(parent);
        }
    }
    return null;
}

function widget(node, name) {
    return node?.widgets?.find((item) => item.name === name);
}

function inputSource(node, name) {
    const input = node?.inputs?.find((item) => item.name === name);
    const link = input?.link == null ? null : node.graph?.links?.[input.link];
    return link ? node.graph?.getNodeById?.(link.origin_id) ?? null : null;
}

function inputConnected(node, name) {
    const input = node?.inputs?.find((item) => item.name === name);
    return input?.link !== null && input?.link !== undefined;
}

function mediaExtension(kind) {
    if (kind === "image") return /\.(?:avif|bmp|gif|jpe?g|png|webp)$/i;
    if (kind === "video") return /\.(?:m4v|mkv|mov|mp4|webm)$/i;
    return /\.(?:aac|flac|m4a|mp3|ogg|opus|wav)$/i;
}

function widgetAsset(value, kind) {
    if (value && typeof value === "object" && value.filename) {
        return {filename:String(value.filename), subfolder:String(value.subfolder ?? ""), type:String(value.type ?? "input")};
    }
    let text = typeof value === "string" ? value.trim() : "";
    if (!text) return null;
    if (/^(?:blob:|data:|https?:|\/api\/view\?|\/view\?)/i.test(text)) return {url:text};
    let type = "input";
    const annotated = text.match(/\s+\[(input|output|temp)\]\s*$/i);
    if (annotated) { type = annotated[1].toLowerCase(); text = text.slice(0, annotated.index).trim(); }
    text = text.replaceAll("\\", "/").replace(/^\/+/, "");
    if (!mediaExtension(kind).test(text)) return null;
    const slash = text.lastIndexOf("/");
    return {filename:slash >= 0 ? text.slice(slash + 1) : text,
        subfolder:slash >= 0 ? text.slice(0, slash) : "", type};
}

function assetUrl(asset) {
    if (!asset) return null;
    if (asset.url) return asset.url;
    const query = new URLSearchParams({filename:asset.filename, subfolder:asset.subfolder ?? "", type:asset.type ?? "input"});
    return api.apiURL(`/view?${query.toString()}`);
}

function findMediaPreview(start, kind) {
    const queue = [start];
    const seen = new Set();
    while (queue.length) {
        const current = queue.shift();
        if (!current || seen.has(current)) continue;
        seen.add(current);
        if (kind === "image") {
            const rendered = current.imgs?.[0];
            const src = typeof rendered === "string" ? rendered : rendered?.src;
            if (src) return src;
        }
        for (const item of current.widgets ?? []) {
            const value = widgetAsset(item.value, kind);
            if (value) return assetUrl(value);
        }
        for (const input of current.inputs ?? []) {
            const parent = inputSource(current, input.name);
            if (parent) queue.push(parent);
        }
    }
    return null;
}

function videoUrl(item) {
    if (!item) return "";
    const query = new URLSearchParams({
        filename:item.filename, subfolder:item.subfolder ?? "", type:item.type ?? "output",
    });
    return api.apiURL(`/view?${query.toString()}`);
}

function insertText(textarea, text, selectionOffset = text.length) {
    const start = textarea.selectionStart ?? textarea.value.length;
    const end = textarea.selectionEnd ?? start;
    textarea.setRangeText(text, start, end, "end");
    const caret = start + selectionOffset;
    textarea.setSelectionRange(caret, caret);
    textarea.dispatchEvent(new Event("input", {bubbles:true}));
    textarea.focus();
}

function insertDialogue(textarea) {
    const start = textarea.selectionStart ?? textarea.value.length;
    const end = textarea.selectionEnd ?? start;
    const selected = textarea.value.slice(start, end);
    const markup = `<d>${selected}</d>`;
    insertText(textarea, markup, selected ? markup.length : 3);
}

function mount(node) {
    if (node._h3PlanStudioMounted || typeof node.addDOMWidget !== "function") return;
    node._h3PlanStudioMounted = true;
    injectStyles();
    node.properties ??= {};
    const alternateTakeWidget = widget(node, "alternate_take_json");
    if (alternateTakeWidget) {
        alternateTakeWidget.hidden = true;
        alternateTakeWidget.type = "hidden";
        alternateTakeWidget.computeSize = () => [0, -4];
        alternateTakeWidget.draw = () => {};
    }
    const branchWidget = widget(node, "working_branch_id");
    if (branchWidget) {
        branchWidget.hidden = true; branchWidget.type = "hidden";
        branchWidget.computeSize = () => [0, -4]; branchWidget.draw = () => {};
    }

    const root = element("div", "h3studio");
    root.title = "Timeline Plan editor: use it standalone or synchronize it with a connected H3 Chain Plan.";
    for (const name of ["pointerdown","pointerup","mousedown","mouseup","click","dblclick"]) {
        root.addEventListener(name, (event) => event.stopPropagation());
    }
    root.addEventListener("wheel", (event) => event.stopPropagation());

    const state = {
        plan:null, planNode:null, planOwner:null, planWidget:null,
        lastValue:"", lastRunName:"",
        lastSettingsSignature:"",
        active:Math.max(0, Number(node.properties[ACTIVE_PROPERTY]) || 0),
        activeChapterId:String(node.properties[ACTIVE_CHAPTER_PROPERTY] ?? ""),
        view:["scene","shared","settings","context","player","subtitles","json"].includes(node.properties[VIEW_PROPERTY])
            ? node.properties[VIEW_PROPERTY] : "scene",
        timelineZoom:normalizedTimelineZoom(
            node.properties[TIMELINE_ZOOM_PROPERTY]),
        checkpoints:new Map(), checkpointSignature:"", checkpointError:"", checkpointToken:0,
        checkpointPromise:null, checkpointRefreshQueued:false, disposed:false,
        executionPromptIds:new Set(),
        sourcePreview:null, sourceWaveform:null, sourceWaveformToken:"",
        presentationToken:0,
        sourceWaveformPromise:null,
        pollTimer:null, checkpointTimer:null, timelineHost:null, sourceTimelineHost:null,
        sourceAudioTimelineHost:null, sourceTrack:null, sourceAudioTrack:null,
        timelineViewport:null, timelineContent:null, timelineRuler:null,
        timelineZoomInput:null, timelineZoomLabel:null,
        timelineResizeObserver:null, timelineWidths:[], timelineSegments:[],
        timelinePixelsPerSecond:0,
        timelineRenderedActive:null,
        timelineWorkspaceEndFrame:0, timelineSceneEndFrame:0,
        timelineExtending:false, timelineDragging:false,
        timelineScrollIntentUntil:0, timelineLastScrollLeft:0,
        timelineLayoutFrame:null,
        subtitleTimelineHost:null,
        panelHost:null,
        planNotifyTimer:null, editorialTimer:null, editorialPending:null,
        editorialSavePromise:null, lastEditorialSignature:"",
        editorialReady:false, editorialRun:"", editorialBindingError:"",
        editorialStored:null, editorialBaseline:null, editorialEditEpoch:0,
        editorial:{revision:"", placements:[], trims:[], locked_scene_ids:[], subtitles:{},
            alternate_draft:null, replacements:[]},
        subtitleAssets:[], subtitleAssetsRun:"", subtitleAssetsToken:0,
        playhead:null, player:null, playerAudio:null, sourceAudioPlayer:null,
        sourcePlayer:null, sourceLayer:null, subtitleOverlay:null,
        editorialClockFrame:null, mediaClockFrame:null, mediaClockKind:"",
        playerSegmentKey:"",
        contextPlayers:[],
        contextTab:"picture",
        playerSlider:null, playerIndex:-1,
        playerPreloadVideo:null, playerPreloadAudio:null,
        primePlayerNext:null, playPlayerTransport:null,
        togglePlayerPlayback:null, keyboardHover:false,
        generatedVolume:monitorVolume(
            node.properties[GENERATED_VOLUME_PROPERTY]),
        sourceVolume:monitorVolume(node.properties[SOURCE_VOLUME_PROPERTY]),
        motionVolume:monitorVolume(node.properties[MOTION_VOLUME_PROPERTY]),
        timelinePosition:null, pendingSeek:0,
        history:{sceneKey:"", data:null, revisionId:null, host:null, textarea:null,
            status:null, loadToken:0, loadPromise:null, saveTimer:null,
            pendingDraft:null, savePromise:null, error:""},
        promptEditors:[], lastPromptEditorsSignature:"",
        referenceSyntax:new Map(),
    };
    node._h3PlanStudioState = state;
    let branches = null;
    const branchBindingProperty = "h3_working_branch_binding_v1";
    const branchDraftClientProperty = "h3_working_branch_draft_client_v1";
    let branchDrafts = null, branchDraftError = "";
    try {
        // Recover the draft namespace even if the first workflow save after
        // installing this feature never happened before a browser crash.
        const workflow = app.extensionManager?.workflow?.activeWorkflow;
        const identity = workflow?.path ?? workflow?.activeState?.id ?? workflow?.filename;
        const index = identity ? `h3-branch-client-v1:${encodeURIComponent(identity)}:${node.id}` : null;
        node.properties[branchDraftClientProperty] ||= (index && window.localStorage.getItem(index)) || branchOperationId();
        if (index) window.localStorage.setItem(index, node.properties[branchDraftClientProperty]);
        branchDrafts = new BranchDrafts(window.localStorage, node.properties[branchDraftClientProperty]);
    }
    catch (error) { branchDraftError = `Browser recovery unavailable: ${error.message}`; }
    function currentBranch() { return workingBranchId(branchWidget?.value); }
    function scopedPath(path, selected = currentBranch()) { return branchRequestPath(path, selected); }
    function captureBranchAuthoring() {
        preserveDelegatedPrompts();
        const owner = state.planOwner ?? node;
        const result = {};
        for (const name of PLAN_SETTING_WIDGETS) {
            if (name !== "run_name" && widget(owner, name)) result[name] = widget(owner, name).value;
        }
        const liveText = state.planWidget ? String(state.planWidget.value) : null;
        // Polling is paused while switching, but external JSON editors may
        // still publish either prompt-only or non-prompt changes.
        result.plan_json = planToJson(liveText !== null && liveText !== state.lastValue
            ? parsePlanJson(liveText) : state.plan);
        result.policy_inputs = captureBranchPolicyInputs(owner);
        return result;
    }
    async function applyWorkingBranch(record) {
        if (!branchWidget) throw new Error("Restart ComfyUI to load the working-branch input.");
        // Validate before replacing widgets or clearing media.
        parsePlanJson(record.authoring.plan_json);
        workingBranchId(record.id);
        const previous = {...state, history:{...state.history}};
        const previousSelection = branches.selected;
        try {
            branchWidgetTransaction([node, state.planNode, ...state.promptEditors,
                ...branchPolicyNodes(state.planOwner ?? node)], () => {
                disposePlayer();
                state.checkpointToken += 1; state.presentationToken += 1;
                state.history.loadToken += 1;
                state.history.data = null; state.history.sceneKey = "";
                state.checkpoints = new Map(); state.checkpointSignature = "";
                node.properties[CHECKPOINT_CACHE_PROPERTY] = null;
                branchWidget.value = record.id;
                for (const [name, value] of Object.entries(record.authoring)) {
                    if (PLAN_SETTING_WIDGETS.includes(name) && name !== "run_name" && name !== "plan_json") {
                        writePlanSetting(name, value, false);
                    }
                }
                restoreBranchPolicyInputs(state.planOwner ?? node, record.authoring);
                const savedPlan = parsePlanJson(record.authoring.plan_json);
                if (record.id === "main") delete savedPlan._branch_id;
                else savedPlan._branch_id = record.id;
                writePlanSetting("plan_json", planToJson(savedPlan), false);
                if (state.planNode) for (let index = 0; index < savedPlan.shots.length; index++) {
                    publishCompanionPrompt(node, state.planNode, index,
                        promptValueToText(savedPlan.shots[index].prompt));
                }
                const alternate = widget(node, "alternate_take_json");
                if (alternate) alternate.value = "";
                state.lastRunName = "";
                state.lastBranchId = record.id;
                loadPlan(true, true);
                branchWidget.callback?.(record.id);
                dirty();
            });
        } catch (error) {
            // Roll back editor state without reviving responses from the failed view.
            const checkpointToken = state.checkpointToken + 1;
            const presentationToken = state.presentationToken + 1;
            const historyToken = state.history.loadToken + 1;
            Object.assign(state, previous, {checkpointToken, presentationToken});
            state.history.loadToken = historyToken;
            branches.selected = previousSelection;
            try { renderShell(); } catch { /* Preserve the callback failure. */ }
            throw error;
        }
    }
    branches = new StudioBranches({
        isCurrent:(run, selected) => !state.disposed && runName() === run && currentBranch() === selected
            && (upstreamPlanNode(node) ?? node) === (state.planOwner ?? node),
        binding:node.properties[branchBindingProperty] ?? null,
        rememberBinding:value => { node.properties[branchBindingProperty] = value; dirty(); },
        drafts:branchDrafts,
        editStamp:() => state.editorialEditEpoch ?? 0,
        captureRecovery:() => {
            const editorial = state.editorialPending || state.editorialSavePromise || state.editorialSaveError
                ? {value:structuredClone(state.editorial), baseline:structuredClone(state.editorialBaseline),
                    stored:structuredClone(state.editorialStored), ready:state.editorialReady} : null;
            const history = structuredClone(state.history.pendingDraft);
            return editorial || history ? {editorial, history} : null;
        },
        restoreRecovery:async recovery => {
            if (!recovery) return;
            if (recovery.editorial) {
                state.editorial = normalizedEditorial(recovery.editorial.value);
                state.editorialBaseline = recovery.editorial.baseline;
                state.editorialStored = recovery.editorial.stored;
                state.editorialRun = runName();
                state.editorialReady = recovery.editorial.ready;
                state.editorialEditEpoch = (state.editorialEditEpoch ?? 0) + 1;
                // Don't let the in-flight saved-cut read overwrite this recovered
                // draft, and don't publish it until the user explicitly retries.
                state.editorialSaveError = "Local cut edits recovered; use Retry save or Reload saved cut.";
                syncAlternateTakeWidget();
            }
            if (recovery.history) state.history.pendingDraft = recovery.history;
            renderShell();
        },
        settle:async () => {
            // Reload/recovery must not publish an unsent stale editorial edit.
            if (state.editorialTimer != null) clearTimeout(state.editorialTimer);
            if (state.editorialPending) {
                state.editorialSaveError = "Pending cut edits kept locally; use Retry save if you stay on this branch.";
            }
            state.editorialTimer = null; state.editorialPending = null;
            if (state.history.saveTimer != null) clearTimeout(state.history.saveTimer);
            state.history.saveTimer = null; state.history.pendingDraft = null;
            await Promise.allSettled([state.editorialSavePromise, state.history.savePromise].filter(Boolean));
        },
        selected:currentBranch(), capture:captureBranchAuthoring,
        apply:applyWorkingBranch, flush:() => flushProjectWrites(),
        changed:() => { if (state.plan) renderShell(); },
        request:async (body) => {
            const read = ["list", "load"].includes(body.action);
            const path = "/minimax_h3_context_loop/working-branches";
            const response = await api.fetchApi(read ? `${path}?${new URLSearchParams(body)}` : path,
                read ? undefined : await projectMutationOptions(node, body.run_name, {
                    method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body),
                }));
            const data = await response.json();
            if (!response.ok) {
                const error = new Error(data.error || `HTTP ${response.status}`);
                error.status = response.status;
                throw error;
            }
            return data;
        },
    });

    function branchToolbar() {
        const bar = element("div", "h3studio-toolbar");
        const records = branches.records;
        const selected = records.findIndex(item => item.id === currentBranch());
        const previous = button("←", "Previous working branch", () => void branches.switchTo(records[selected - 1]?.id));
        const next = button("→", "Next working branch", () => void branches.switchTo(records[selected + 1]?.id));
        previous.disabled = branches.busy || selected <= 0;
        next.disabled = branches.busy || selected < 0 || selected >= records.length - 1;
        const select = element("select", "h3studio-select");
        select.title = "Editing and generating branch (not the project default)";
        for (const item of records) {
            const option = element("option", "", item.name); option.value = item.id; select.append(option);
        }
        select.value = currentBranch(); select.disabled = branches.busy || !records.length;
        select.addEventListener("change", () => void branches.switchTo(select.value));
        const create = (fork) => {
            const name = window.prompt(fork ? "Name this continuation branch" : "Name this empty branch", `Branch ${records.length + 1}`);
            if (!name) return;
            const newSeeds = !fork && window.confirm("Use new random scene seeds?\nCancel keeps all existing seeds.");
            void branches.create(name, fork ? state.active + 1 : 0, newSeeds);
        };
        const empty = button("+ Empty branch", "Copy the complete Plan and references; no generated videos", () => create(false));
        const fork = button("Fork here", "Keep saved scenes through the selected scene; copy the full Plan", () => create(true));
        empty.disabled = fork.disabled = branches.busy || !records.length || !branchWidget;
        fork.disabled ||= Boolean(branches.conflict || branches.draftRecovery);
        const makeDefault = button("Make project default", "Change the preferred branch without changing any saved clips or queued jobs", () => void branches.makeDefault());
        makeDefault.disabled = branches.busy || !records.length || currentBranch() === branches.defaultBranch;
        const defaultName = records.find(item => item.id === branches.defaultBranch)?.name ?? "Original";
        const saveBranch = button("Save branch", "Save this branch's current prompts and Plan settings", () => void branches.perform(async () => {}));
        const useDefault = button("Open project default", "Load the project's preferred branch in this Studio", () => void branches.switchTo(branches.defaultBranch));
        saveBranch.disabled = branches.busy || !records.length;
        useDefault.disabled = branches.busy || !records.length || currentBranch() === branches.defaultBranch;
        bar.append(saveBranch, useDefault);
        bar.append(previous, select, next, empty, fork, makeDefault,
            element("span", "h3studio-message", `Project default: ${defaultName}`));
        const reload = button("Reload saved branch", "Load the saved prompts/settings; keep local edits in browser recovery", () => {
            if (confirm("Reload this branch's saved prompts/settings? Local edits are kept in browser recovery; generated files are unchanged.")) {
                void branches.reloadSaved();
            }
        });
        reload.disabled = !branches.ready;
        const recover = button("Restore local draft", "Recover this browser's last unsaved branch settings", () => {
            branches.readDraft();
            if (branches.draftRecovery && confirm("Replace the currently displayed prompts/settings with this browser's recovery draft? Saved branch settings and generated files are unchanged.")) {
                void branches.restoreDraft();
            }
        });
        recover.disabled = !branchDrafts || !branches.ready;
        bar.append(reload, recover);
        if (branches.switchTarget && branches.switchTarget !== currentBranch()) {
            const target = branches.switchTarget;
            const name = records.find(item => item.id === target)?.name ?? target.slice(0, 8);
            const openSaved = button(`Open saved ${name}`, "Switch without publishing the current branch's edits; keep prompts, settings and pending cut edits in browser recovery", () => {
                if (confirm(`Open branch "${name}" using its saved settings?\n\nCurrent local prompts, settings and pending edits will be kept in browser recovery, not written over the saved branch. Return to this branch and use Restore local draft to recover them. No generated clips are deleted.`)) {
                    void branches.switchTo(target, {save:false});
                }
            });
            openSaved.disabled = branches.busy || !branches.ready || Boolean(branches.pending);
            bar.append(openSaved);
        }
        bar.append(button("Refresh branches", "Recheck branch availability without overwriting local settings", () => {
            void branches.refresh(runName()).catch(error => { branches.error = error.message; renderShell(); });
        }));
        if (branches.pending) {
            bar.append(button("Retry pending operation", "Reconcile the exact previous request without duplicating a branch", () => void branches.retryPending()));
        }
        if (branches.conflict) bar.append(element("span", "h3studio-error", branches.conflict));
        const recovery = branches.records.find(item => item.id === currentBranch())?.authoring_recovery;
        if (recovery) bar.append(element("span", "h3studio-message", recovery.message));
        bar.append(element("span", "h3studio-message h3studio-branch-draft", branchDraftError || branches.draftStatus));
        if (branches.error) bar.append(element("span", "h3studio-error", branches.error));
        return bar;
    }

    root.tabIndex = 0;
    root.addEventListener("pointerenter", () => { state.keyboardHover = true; });
    root.addEventListener("pointerleave", () => { state.keyboardHover = false; });
    const pausePlayerMonitors = () => {
        for (const media of [
            state.playerAudio, state.sourceAudioPlayer, state.sourcePlayer,
            ...state.contextPlayers,
        ]) {
            try { media?.pause(); } catch (_error) {}
        }
    };
    const onPlayerKeydown = (event) => {
        if (event.code !== "Space" || event.repeat || state.view !== "player"
                || !state.togglePlayerPlayback) return;
        const target = event.target;
        if (target instanceof Element && target.closest(
            "input,textarea,select,button,[contenteditable=true]")) return;
        if (!state.keyboardHover && !root.contains(document.activeElement)) return;
        event.preventDefault(); event.stopPropagation();
        state.togglePlayerPlayback();
    };
    document.addEventListener("keydown", onPlayerKeydown, true);

    function dirty() {
        node.graph?.setDirtyCanvas?.(true, true);
        app.graph?.setDirtyCanvas?.(true, true);
    }

    function persistView() {
        node.properties[ACTIVE_PROPERTY] = state.active;
        node.properties[ACTIVE_CHAPTER_PROPERTY] = state.activeChapterId;
        node.properties[VIEW_PROPERTY] = state.view;
        node.properties[TIMELINE_ZOOM_PROPERTY] = state.timelineZoom;
        dirty();
    }

    function runName() {
        return String(widget(state.planOwner ?? node, "run_name")?.value ?? "").trim();
    }

    function settings() {
        const owner = state.planOwner ?? node;
        const transition = resolveTransitionPolicy(owner);
        const audioPolicy = resolveAudioPolicy(owner);
        return {
            contextLength:transition.known
                ? transition.contextLength
                : widget(owner, "context_length")?.value ?? 22,
            audioContextLength:resolveAudioContextLength(owner),
            videoBlendFrames:widget(owner, "video_blend_frames")?.value ?? 0,
            encodeMode:widget(owner, "encode_mode")?.value ?? "video",
            anchorMode:widget(owner, "anchor_mode")?.value ?? "head",
            continuationMode:transition.known
                ? transition.continuationMode
                : widget(owner, "continuation_mode")?.value ?? "guide",
            generatedContinuity:audioPolicy.known
                ? audioPolicy.generatedContinuity : "on",
            sourceAudioTarget:audioPolicy.known
                ? audioPolicy.sourceAudioTarget ?? "off" : "off",
            transitionPreset:transition.known ? transition.preset : "custom",
            audioPolicy,
            defaultDurationSeconds:widget(owner, "default_duration_seconds")?.value ?? 15,
            defaultSteps:widget(owner, "default_steps")?.value ?? 20,
        };
    }

    function settingsSignature(planOwner = state.planOwner ?? node) {
        const transition = resolveTransitionPolicy(planOwner);
        const audioPolicy = resolveAudioPolicy(planOwner);
        return JSON.stringify([
            transition.known
                ? transition.contextLength
                : widget(planOwner, "context_length")?.value ?? 22,
            resolveAudioContextLength(planOwner),
            widget(planOwner, "video_blend_frames")?.value ?? 0,
            widget(planOwner, "encode_mode")?.value ?? "video",
            widget(planOwner, "anchor_mode")?.value ?? "head",
            transition.known
                ? transition.continuationMode
                : widget(planOwner, "continuation_mode")?.value ?? "guide",
            audioPolicy.sourceReference,
            audioPolicy.generatedContinuity,
            audioPolicy.sourceAudioTarget ?? "off",
            inputConnected(planOwner, "project_assets"),
            ...PLAN_SETTING_WIDGETS.slice(1).map(
                (name) => widget(planOwner, name)?.value ?? null),
        ]);
    }

    function mirrorConnectedPlan(planNode) {
        if (!planNode) return false;
        let changed = false;
        for (const name of PLAN_SETTING_WIDGETS) {
            const source = widget(planNode, name);
            const target = widget(node, name);
            if (!source || !target || Object.is(source.value, target.value)) continue;
            target.value = source.value;
            changed = true;
        }
        if (changed) dirty();
        return changed;
    }

    function writePlanSetting(name, value, rerender = true) {
        if (name === "default_steps") {
            setPlanDefaultSteps(state.plan, value);
            writePlan();
        }
        const targets = state.planNode ? [state.planNode, node] : [node];
        for (const target of targets) {
            const targetWidget = widget(target, name);
            if (!targetWidget || Object.is(targetWidget.value, value)) continue;
            targetWidget.value = value;
            targetWidget.callback?.(targetWidget.value);
        }
        state.lastRunName = runName();
        state.lastSettingsSignature = settingsSignature(state.planOwner ?? node);
        dirty();
        if (rerender) renderShell();
    }

    function timing() {
        return calculatePlanTiming(state.plan, settings());
    }

    function normalizedEditorial(value = {}) {
        const knownIds = new Set((state.plan?.shots ?? []).map((shot, index) => (
            safeShotId(shot?.id, `clip_${String(index + 1).padStart(4, "0")}`)
        )));
        const placements = [];
        const seen = new Set();
        for (const item of Array.isArray(value?.placements) ? value.placements : []) {
            const sceneId = String(item?.scene_id ?? "").trim();
            const startFrame = Math.max(0, Math.min(
                864000, Math.round(Number(item?.start_frame) || 0),
            ));
            if (!sceneId || !knownIds.has(sceneId) || seen.has(sceneId)) continue;
            seen.add(sceneId);
            placements.push({
                scene_id:sceneId,
                start_frame:startFrame,
            });
        }
        const trims = [];
        const trimmed = new Set();
        for (const item of Array.isArray(value?.trims) ? value.trims : []) {
            const sceneId = String(item?.scene_id ?? "").trim();
            const outFrame = Math.round(Number(item?.out_frame));
            if (!sceneId || !knownIds.has(sceneId) || trimmed.has(sceneId)
                    || !Number.isInteger(outFrame) || outFrame < 1
                    || outFrame > MAX_H3_FRAMES) continue;
            trimmed.add(sceneId);
            trims.push({scene_id:sceneId, out_frame:outFrame});
        }
        const rawSubtitles = value?.subtitles && typeof value.subtitles === "object"
            ? value.subtitles : {};
        const mode = ["off", "preview_srt"].includes(rawSubtitles.mode)
            ? rawSubtitles.mode : "off";
        const offset = Number(rawSubtitles.offset_seconds);
        const lockedSceneIds = [...new Set(
            (Array.isArray(value?.locked_scene_ids)
                ? value.locked_scene_ids : [])
                .map((sceneId) => String(sceneId ?? "").trim())
                .filter((sceneId) => knownIds.has(sceneId)),
        )];
        const revisionPattern = /^[0-9a-f]{32}$/;
        let alternateDraft = null;
        const rawAlternate = value?.alternate_draft;
        if (rawAlternate && typeof rawAlternate === "object") {
            const sceneId = String(rawAlternate.scene_id ?? "").trim();
            const scene = (state.plan?.shots ?? []).findIndex((shot, index) =>
                safeShotId(shot?.id, `clip_${String(index + 1).padStart(4, "0")}`) === sceneId) + 1;
            const baseRevision = String(rawAlternate.base_revision ?? "").toLowerCase();
            const prompt = String(rawAlternate.prompt ?? "").trim();
            let seed = "0";
            try {
                const parsed = BigInt(String(rawAlternate.seed ?? "0"));
                if (parsed >= 0n && parsed <= MAX_SEED) seed = parsed.toString();
            } catch (_error) {}
            if (scene > 0 && revisionPattern.test(baseRevision) && prompt) {
                alternateDraft = {
                    enabled:rawAlternate.enabled !== false,
                    scene, scene_id:sceneId, base_revision:baseRevision,
                    prompt, seed, media_mode:"picture_only",
                };
            }
        }
        const replacements = [];
        const replaced = new Set();
        for (const item of Array.isArray(value?.replacements)
            ? value.replacements : []) {
            const sceneId = String(item?.scene_id ?? "").trim();
            const scene = (state.plan?.shots ?? []).findIndex((shot, index) =>
                safeShotId(shot?.id, `clip_${String(index + 1).padStart(4, "0")}`) === sceneId) + 1;
            const baseRevision = String(item?.base_revision ?? "").toLowerCase();
            const alternateRevision = String(item?.alternate_revision ?? "").toLowerCase();
            if (scene < 1 || replaced.has(sceneId)
                    || !revisionPattern.test(baseRevision)
                    || !revisionPattern.test(alternateRevision)) continue;
            replaced.add(sceneId);
            replacements.push({
                scene, scene_id:sceneId, base_revision:baseRevision,
                alternate_revision:alternateRevision,
                media_mode:"picture_only",
            });
        }
        return {
            revision:revisionPattern.test(String(value?.revision ?? "").toLowerCase())
                ? String(value.revision).toLowerCase() : "",
            placements,
            trims,
            locked_scene_ids:lockedSceneIds,
            subtitles:{
                mode,
                asset_id:String(rawSubtitles.asset_id ?? ""),
                offset_seconds:Number.isFinite(offset)
                    ? Math.max(-3600, Math.min(3600, offset)) : 0,
            },
            alternate_draft:alternateDraft,
            replacements,
        };
    }

    function timelineModel() {
        const result = timing();
        const sceneSegments = studioTimelineSegments(
            result.shots, state.editorial.placements, null,
            state.editorial.trims,
        );
        const sceneEndFrame = Math.max(
            1,
            Math.round(studioTimelineTotalSeconds(sceneSegments) * FPS),
        );
        const sourceDescriptor = state.sourcePreview?.source_audio;
        const sourceEndFrame = sourceDescriptor?.available
            ? Math.max(
                Number(sourceDescriptor.available_frame_count) || 0,
                Number(sourceDescriptor.frame_count) || 0,
            ) : 0;
        const subtitleOffset = Number(
            state.editorial.subtitles?.offset_seconds,
        ) || 0;
        const subtitleEndFrame = subtitleCues().reduce(
            (latest, cue) => Math.max(
                latest,
                Math.ceil((Number(cue?.endSeconds) + subtitleOffset) * FPS),
            ),
            0,
        );
        const workspaceEndFrame = Math.min(864000, Math.max(
            sceneEndFrame * 2,
            sourceEndFrame,
            subtitleEndFrame,
            Number(state.timelineWorkspaceEndFrame) || 0,
        ));
        state.timelineSceneEndFrame = sceneEndFrame;
        state.timelineWorkspaceEndFrame = workspaceEndFrame;
        const segments = studioTimelineSegments(
            result.shots, state.editorial.placements, workspaceEndFrame,
            state.editorial.trims,
        );
        return {
            result, segments,
            totalSeconds:studioTimelineTotalSeconds(segments),
            sceneEndSeconds:sceneEndFrame / FPS,
            workspaceEndFrame,
        };
    }

    function sceneLocked(index) {
        const row = timing().shots[index];
        return Boolean(row && state.editorial.locked_scene_ids.includes(
            String(row.id),
        ));
    }

    function setSceneLocked(index, locked) {
        const row = timing().shots[index];
        if (!row) return;
        const sceneId = String(row.id);
        const next = new Set(state.editorial.locked_scene_ids);
        if (locked) next.add(sceneId);
        else next.delete(sceneId);
        state.editorial.locked_scene_ids = [...next];
        scheduleEditorialSave();
        renderShell();
    }

    function unlockAllScenes() {
        if (!state.editorial.locked_scene_ids.length) return;
        state.editorial.locked_scene_ids = [];
        scheduleEditorialSave();
        renderShell();
    }

    function placementForScene(index) {
        const row = timing().shots[index];
        const placement = state.editorial.placements.find(
            (placement) => placement.scene_id === String(row?.id ?? ""),
        ) ?? null;
        return placement ? {...placement} : null;
    }

    function trimForScene(index) {
        const row = timing().shots[index];
        const trim = state.editorial.trims.find(
            (item) => item.scene_id === String(row?.id ?? ""),
        ) ?? null;
        return trim ? {...trim} : null;
    }

    function setSceneTrim(index, outFrame) {
        const row = timing().shots[index];
        if (!row || sceneLocked(index)) return;
        const sceneId = String(row.id);
        const fullFrames = Math.max(1, Number(row.deliveredFrames) || 1);
        const safeOut = studioNearestLatentSafeOutFrame(
            row.rawFrames, fullFrames, outFrame,
        );
        state.editorial.trims = state.editorial.trims.filter(
            (item) => item.scene_id !== sceneId,
        );
        if (safeOut < fullFrames) state.editorial.trims.push({
            scene_id:sceneId, out_frame:safeOut,
        });
        scheduleEditorialSave();
        renderShell();
    }

    function setScenePlacement(index, startFrame) {
        const row = timing().shots[index];
        if (!row || sceneLocked(index)) return;
        state.editorial.placements = state.editorial.placements.filter(
            (placement) => placement.scene_id !== String(row.id),
        );
        if (startFrame != null && String(startFrame).trim() !== "") {
            const requestedStart = Math.max(0, Math.min(
                864000, Math.round(Number(startFrame) || 0),
            ));
            state.editorial.placements.push({
                scene_id:String(row.id),
                start_frame:requestedStart,
            });
        }
        scheduleEditorialSave();
        renderShell();
        if (state.sourcePreview?.source_audio?.available) {
            void loadSourceWaveform(state.sourcePreview);
        }
    }

    function selectedSubtitleAsset() {
        const assetId = String(state.editorial.subtitles?.asset_id ?? "");
        return state.subtitleAssets.find(
            (asset) => String(asset?.id ?? "") === assetId,
        ) ?? null;
    }

    function subtitleCues() {
        const asset = selectedSubtitleAsset();
        return state.editorial.subtitles?.mode === "preview_srt" && asset
            ? parseTimedLyrics(asset.lyrics) : [];
    }

    function updateSubtitleOverlay(seconds = state.timelinePosition ?? 0) {
        if (!state.subtitleOverlay) return;
        const cue = timedLyricAtSecond(
            subtitleCues(), seconds,
            state.editorial.subtitles?.offset_seconds,
        );
        state.subtitleOverlay.textContent = cue?.text ?? "";
        state.subtitleOverlay.hidden = !cue;
    }

    async function loadSubtitleAssets() {
        const currentRun = runName();
        if (!currentRun || state.disposed) return;
        const token = ++state.subtitleAssetsToken;
        try {
            const response = await api.fetchApi(
                `/minimax_h3_context_loop/project-assets?project=${encodeURIComponent(currentRun)}`,
            );
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
            if (state.disposed || token !== state.subtitleAssetsToken
                    || currentRun !== runName()) return;
            state.subtitleAssetsRun = currentRun;
            state.subtitleAssets = (payload.assets ?? []).filter(
                (asset) => asset?.kind === "audio" && String(asset?.lyrics ?? "").trim(),
            );
            renderSubtitleTimeline();
            if (state.view === "subtitles") renderPanel();
            updateSubtitleOverlay();
        } catch (error) {
            if (token === state.subtitleAssetsToken) {
                state.subtitleAssets = [];
                console.warn("H3 Plan Studio could not load timed lyrics:", error);
            }
        }
    }

    function promptEditorsSignature(editors = state.promptEditors) {
        return editors.map((editor) => `${nodeType(editor)}:${String(editor.id ?? "")}`).sort().join("|");
    }

    function promptEditorLabel() {
        if (state.promptEditors.length > 1) return `${state.promptEditors.length} linked prompt editors`;
        return nodeType(state.promptEditors[0]) === "MiniMaxH3ChainRichScenePromptEditor"
            ? "Rich Scene Prompt Editor" : "Scene Prompt Editor";
    }

    function preserveDelegatedPrompts() {
        if (!state.promptEditors.length || !state.planWidget || !state.plan) return;
        let live;
        try { live = parsePlanJson(String(state.planWidget.value ?? "")); }
        catch (_error) { return; }
        const byId = new Map();
        for (const shot of live.shots) {
            const id = String(shot?.id ?? "").trim();
            if (id && !byId.has(id)) byId.set(id, shot);
        }
        state.plan.shots.forEach((shot, index) => {
            const id = String(shot?.id ?? "").trim();
            // A new ID (duplicate/add/rename) has no live counterpart yet.
            // Never replace its prompt with the scene formerly at this index.
            const current = id ? byId.get(id) : live.shots[index];
            if (current) shot.prompt = promptTextToLines(promptValueToText(current.prompt));
        });
    }

    function publishActiveScene() {
        if (state.planNode) publishCompanionScene(node, state.planNode, state.active);
    }

    function editorialPayload() {
        const shots = state.plan?.shots ?? [];
        const sceneOrder = shots.map((shot, index) => ({
            scene:index + 1,
            scene_id:safeShotId(
                shot?.id, `clip_${String(index + 1).padStart(4, "0")}`,
            ),
        }));
        const sceneById = new Map(sceneOrder.map((row) => [row.scene_id, row.scene]));
        return {
            run_name:runName(),
            revision:String(state.editorial.revision ?? ""),
            base_revision:String(state.editorial.revision ?? ""),
            chapters:orderedChapters(state.plan).map((chapter) => ({
                id:chapter.id,
                title:chapter.title,
                start_scene_id:chapter.start_scene_id,
                start_scene:sceneById.get(chapter.start_scene_id),
                text:chapter.text ?? "",
                ...(chapter.resolution ? {resolution:{...chapter.resolution}} : {}),
            })),
            scene_order:sceneOrder,
            placements:state.editorial.placements.map((placement) => ({
                scene_id:placement.scene_id,
                scene:sceneById.get(placement.scene_id),
                start_frame:placement.start_frame,
            })),
            trims:state.editorial.trims.map((trim) => ({
                scene_id:trim.scene_id,
                scene:sceneById.get(trim.scene_id),
                out_frame:trim.out_frame,
            })),
            locked_scene_ids:[...state.editorial.locked_scene_ids],
            subtitles:{...state.editorial.subtitles},
            alternate_draft:state.editorial.alternate_draft
                ? {...state.editorial.alternate_draft} : null,
            replacements:state.editorial.replacements.map(
                (replacement) => ({...replacement})),
        };
    }

    function applyEditorialPayload(payload, expectedEpoch = state.editorialEditEpoch ?? 0, unusedSceneIds = []) {
        const currentRun = runName();
        if (!payload || String(payload.run_name ?? "") !== currentRun) return false;
        if (expectedEpoch !== (state.editorialEditEpoch ?? 0)) return false;
        // A periodic GET must not replace edits waiting for their POST.
        if (state.editorialRun === currentRun
                && (state.editorialTimer != null || state.editorialSavePromise
                    || state.editorialSaving || state.editorialSaveError)) return false;
        const next = normalizedEditorial(payload);
        const previous = JSON.stringify(state.editorial);
        const previousError = state.editorialBindingError;
        state.editorial = next;
        state.editorialRun = currentRun;
        state.editorialReady = true;
        // A GET is hydration, never permission to rewrite another Run's Plan.
        // Keep the server document and the local view separately: unchanged
        // Plan fields (notably chapters) must survive an unrelated scene edit.
        state.editorialStored = structuredClone(payload);
        state.editorialBaseline = editorialPayload();
        const knownIds = new Set(state.editorialBaseline.scene_order.map((row) => row.scene_id));
        // Only the same fresh checkpoint response can prove a missing order
        // entry has no active or retained render. Cached/older servers fail
        // closed. This permission never applies to saved editorial edits.
        const unusedIds = new Set(Array.isArray(unusedSceneIds) ? unusedSceneIds : []);
        state.editorialUnusedSceneIds = (payload.scene_order ?? [])
            .map((row) => row.scene_id).filter((id) => !knownIds.has(id) && unusedIds.has(id));
        const savedIds = [
            ...(payload.scene_order ?? []).map((row) => row.scene_id).filter((id) => !unusedIds.has(id)),
            ...(payload.chapters ?? []).map((row) => row.start_scene_id),
            ...(payload.placements ?? []).map((row) => row.scene_id),
            ...(payload.trims ?? []).map((row) => row.scene_id),
            ...(payload.locked_scene_ids ?? []),
            ...(payload.replacements ?? []).map((row) => row.scene_id),
            payload.alternate_draft?.scene_id,
        ].filter(Boolean);
        const missingIds = [...new Set(savedIds.filter((id) => !knownIds.has(id)))];
        state.editorialBindingError = missingIds.length
            ? `Editorial saving paused: this Run contains saved scenes or edits absent from the connected Plan (${missingIds.slice(0, 5).join(", ")}${missingIds.length > 5 ? ", …" : ""}). Load its matching Plan or choose a new Run name.`
            : "";
        state.lastEditorialSignature = editorialSignature(state.editorialStored);
        syncAlternateTakeWidget();
        return previous !== JSON.stringify(next)
            || previousError !== state.editorialBindingError;
    }

    function editorialSignature(payload) {
        const comparable = {...payload};
        delete comparable.base_revision;
        delete comparable.revision;
        delete comparable.branch_id;
        return JSON.stringify(comparable);
    }

    function cacheStudioPresentation(records, editorial) {
        const snapshot = studioCheckpointCacheSnapshot(
            runName(), records, editorial,
        );
        if (snapshot) node.properties[CHECKPOINT_CACHE_PROPERTY] = snapshot;
    }

    async function persistEditorial(payload, signature) {
        const requestBranch = payload.branch_id ?? currentBranch();
        // Capture the editor binding, not just its Run name: A -> B -> A
        // creates a new view that must not adopt an old request's revision.
        const binding = state.editorial;
        const readRevision = String(binding.revision ?? payload.base_revision ?? "");
        const previousSave = state.editorialSavePromise;
        const request = (async () => {
            let previous = null;
            if (previousSave) {
                try { previous = await previousSave; } catch (_error) {}
            }
            const outbound = {
                ...payload,
                base_revision:previous?.binding === binding
                        && previous.run_name === payload.run_name
                    ? previous.revision : readRevision,
            };
            const response = await api.fetchApi(
                scopedPath("/minimax_h3_context_loop/editorial", requestBranch),
                await projectMutationOptions(node, payload.run_name, {
                    method:"POST", headers:{"Content-Type":"application/json"},
                    body:JSON.stringify(outbound),
                }),
            );
            const detail = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(detail.error || `HTTP ${response.status}`);
            }
            const saved = detail.editorial;
            if (state.editorial === binding && state.editorialRun === payload.run_name
                    && saved && typeof saved === "object") {
                state.editorial.revision = String(saved.revision ?? "");
                // Invalidate checkpoint GETs that started before this commit.
                state.editorialEditEpoch = (state.editorialEditEpoch ?? 0) + 1;
                // A newer edit may have been blocked while this POST was in
                // flight. Only this request's edits have now been saved.
                if (state.lastEditorialSignature === signature) state.editorialSaveError = "";
                if (!state.disposed) renderStatus();
            }
            return {binding, run_name:payload.run_name, revision:String(saved?.revision ?? readRevision)};
        })();
        state.editorialSavePromise = request;
        try {
            await request;
        } catch (error) {
            if (state.editorial === binding && state.lastEditorialSignature === signature) {
                state.lastEditorialSignature = "";
                state.editorialSaveError = error?.message || String(error);
                if (!state.disposed) renderStatus();
            }
            console.warn(
                "H3 Plan Studio could not save chapter presentation data:",
                error,
            );
            throw error;
        } finally {
            if (state.editorialSavePromise === request) {
                state.editorialSavePromise = null;
                if (!state.disposed && state.editorial === binding) renderStatus();
            }
        }
    }

    function syncAlternateTakeWidget() {
        if (alternateTakeWidget) {
            const serialized = JSON.stringify(
                state.editorial.alternate_draft ?? null,
            );
            if (alternateTakeWidget.value !== serialized) {
                alternateTakeWidget.value = serialized;
                alternateTakeWidget.callback?.(serialized);
                dirty();
            }
        }
    }

    function scheduleEditorialSave(delay = 250) {
        syncAlternateTakeWidget();
        if (!state.plan) return;
        if (!state.editorialReady || state.editorialRun !== runName()) return;
        const local = editorialPayload();
        const payload = {...state.editorialStored};
        // Only explicitly changed fields may replace saved project data.
        for (const [key, value] of Object.entries(local)) {
            if (JSON.stringify(value) !== JSON.stringify(state.editorialBaseline?.[key])) {
                payload[key] = value;
            }
        }
        payload.run_name = local.run_name;
        // Do not turn a GET (or a seed/prompt-only edit) into a project write.
        if (editorialSignature(payload) === state.lastEditorialSignature) return;
        if (state.editorialUnusedSceneIds?.length) {
            payload.scene_order = local.scene_order;
        }
        cacheStudioPresentation([...state.checkpoints.values()], payload);
        const signature = editorialSignature(payload);
        if (signature === state.lastEditorialSignature) return;
        state.editorialEditEpoch = (state.editorialEditEpoch ?? 0) + 1;
        // Register the edit before checking write authority. Silently returning
        // here used to let the next checkpoint GET replace a local trim with
        // the saved full length. Keep the edit and report the pause instead.
        const blocked = state.editorialBindingError || (branches && (
            !branches.ready ? "Wait for working branches to load, then retry saving the cut."
            : branches.conflict ? branches.conflict
            : branches.draftRecovery ? "Resolve the local recovery draft before saving the cut."
            : ""));
        if (blocked) {
            if (state.editorialTimer != null) clearTimeout(state.editorialTimer);
            state.editorialTimer = null; state.editorialPending = null;
            state.lastEditorialSignature = "";
            state.editorialSaveError = blocked;
            renderStatus();
            return;
        }
        state.lastEditorialSignature = signature;
        payload.branch_id = currentBranch();
        if (state.editorialTimer != null) clearTimeout(state.editorialTimer);
        if (!payload.run_name) return;
        state.editorialPending = {payload, signature};
        state.editorialTimer = setTimeout(() => {
            state.editorialTimer = null;
            const pending = state.editorialPending;
            if (pending?.signature === signature) state.editorialPending = null;
            void persistEditorial(payload, signature).catch(() => {});
        }, Math.max(0, Number(delay) || 0));
    }

    async function flushProjectWrites(expectedRun = runName()) {
        const run = String(expectedRun ?? "").trim();
        let editorialError = null;
        try {
            if (run && state.editorialRun === run) {
                if (state.editorialTimer != null) {
                    clearTimeout(state.editorialTimer);
                    state.editorialTimer = null;
                }
                const pending = state.editorialPending;
                state.editorialPending = null;
                if (pending?.payload?.run_name === run) {
                    await persistEditorial(pending.payload, pending.signature);
                } else if (state.editorialSavePromise) {
                    await state.editorialSavePromise;
                }
                if (state.editorialSaveError) throw new Error(state.editorialSaveError);
            }
        } catch (error) {
            editorialError = error;
        }
        await flushHistoryDraft();
        if (editorialError) throw editorialError;
    }

    function writePlan(message = null) {
        if (!state.plan || !state.planWidget) return;
        // A linked dedicated editor owns scene prompts. Re-read those fields at
        // the last possible moment so a Studio seed/length edit cannot overwrite
        // prompt text typed since Studio's 500 ms polling snapshot.
        preserveDelegatedPrompts();
        const value = planToJson(state.plan);
        state.lastValue = value;
        state.planWidget.value = value;
        if (state.planNode) {
            const localPlanWidget = widget(node, "plan_json");
            if (localPlanWidget) localPlanWidget.value = value;
        }
        if (state.planNotifyTimer != null) clearTimeout(state.planNotifyTimer);
        const targetWidget = state.planWidget;
        state.planNotifyTimer = setTimeout(() => {
            state.planNotifyTimer = null;
            if (targetWidget !== state.planWidget) return;
            targetWidget.callback?.(targetWidget.value);
        }, 75);
        state.planOwner?.graph?.setDirtyCanvas?.(true, true);
        if (state.planNode && !state.activeChapterId) {
            publishCompanionPrompt(
                node, state.planNode, state.active,
                promptValueToText(state.plan.shots[state.active]?.prompt));
        }
        if (message) message.textContent = state.planNode
            ? "Saved to connected Plan" : "Saved in standalone Plan Studio";
        scheduleEditorialSave();
        renderStatus();
        dirty();
    }

    function historyKey(sceneId) {
        return `${runName()}\u0000${currentBranch()}\u0000${sceneId}`;
    }

    async function historyRequest(query = {}, body = null) {
        const suffix = new URLSearchParams(query).toString();
        const response = await api.fetchApi(
            scopedPath(`/minimax_h3_context_loop/prompt-history${suffix ? `?${suffix}` : ""}`, body?.branch_id ?? currentBranch()),
            body == null ? undefined : await projectMutationOptions(
                node, body.run_name ?? runName(), {
                    method:"POST", headers:{"Content-Type":"application/json"},
                    body:JSON.stringify(body),
                },
            ),
        );
        let payload = {};
        try { payload = await response.json(); } catch (_error) {}
        if (!response.ok) throw new Error(payload.error || `Prompt history request failed (HTTP ${response.status}).`);
        return payload;
    }

    function renderHistory() {
        const history = state.history;
        if (!history.host) return;
        history.host.replaceChildren();
        if (history.error) {
            history.host.append(element("span", "h3studio-history-meta h3studio-error", history.error));
            return;
        }
        if (!history.data) {
            history.host.append(element("span", "h3studio-history-meta", "Loading prompt versions…"));
            return;
        }
        const navigation = promptRevisionNavigation(history.data, history.revisionId);
        const previous = button("‹", "Activate previous prompt version in the Plan", () => {
            if (navigation.previous) void selectHistoryRevision(navigation.previous.id);
        });
        const next = button("›", "Activate next prompt version in the Plan", () => {
            if (navigation.next) void selectHistoryRevision(navigation.next.id);
        });
        previous.disabled = !navigation.previous;
        next.disabled = !navigation.next;
        const count = element("span", "h3studio-history-count", `Active ${navigation.position} / ${navigation.total}`);
        const metadata = element("span", "h3studio-history-meta", promptRevisionLabel(navigation));
        metadata.title = promptRevisionHelp(navigation);
        history.host.append(previous, count, next, metadata);
    }

    async function loadHistory(sceneId, prompt, synchronize = true) {
        const history = state.history;
        const currentRun = runName();
        const key = historyKey(sceneId);
        const token = ++history.loadToken;
        history.sceneKey = key; history.data = null; history.revisionId = null; history.error = "";
        renderHistory();
        if (!currentRun) { history.error = "Set a Plan run_name to enable prompt history."; renderHistory(); return; }
        const request = synchronize ? historyRequest({}, {
            action:"save", run_name:currentRun, scene_id:sceneId, prompt, parent_revision:null,
        }) : historyRequest({run_name:currentRun, scene_id:sceneId});
        history.loadPromise = request;
        try {
            const payload = await request;
            if (token !== history.loadToken || history.sceneKey !== key) return;
            history.data = payload.history ?? payload;
            history.revisionId = payload.revision?.id ?? history.data.active_revision ?? null;
        } catch (error) {
            if (token === history.loadToken && history.sceneKey === key) history.error = error?.message || String(error);
        } finally {
            if (history.loadPromise === request) history.loadPromise = null;
            if (token === history.loadToken && history.sceneKey === key) renderHistory();
        }
    }

    function scheduleHistoryDraft(sceneId, prompt) {
        const currentRun = runName();
        if (!currentRun) return;
        const history = state.history;
        history.pendingDraft = {key:historyKey(sceneId), runName:currentRun, branchId:currentBranch(), sceneId, prompt};
        if (history.saveTimer != null) clearTimeout(history.saveTimer);
        history.saveTimer = setTimeout(() => { history.saveTimer = null; void flushHistoryDraft(); }, 650);
    }

    async function flushHistoryDraft() {
        const history = state.history;
        if (history.saveTimer != null) { clearTimeout(history.saveTimer); history.saveTimer = null; }
        if (history.savePromise) { await history.savePromise; return history.pendingDraft ? flushHistoryDraft() : undefined; }
        const draft = history.pendingDraft;
        if (!draft) return;
        history.pendingDraft = null;
        if (history.loadPromise && history.sceneKey === draft.key) await history.loadPromise;
        const request = historyRequest({}, {action:"save", run_name:draft.runName,
            branch_id:draft.branchId,
            scene_id:draft.sceneId, prompt:draft.prompt,
            parent_revision:history.sceneKey === draft.key ? history.revisionId : null});
        history.savePromise = request;
        try {
            const payload = await request;
            if (history.sceneKey === draft.key) {
                history.data = payload.history; history.revisionId = payload.revision?.id ?? payload.history?.active_revision;
                history.error = ""; renderHistory();
            }
        } catch (error) {
            if (history.sceneKey === draft.key) { history.error = error?.message || String(error); renderHistory(); }
        } finally { if (history.savePromise === request) history.savePromise = null; }
        if (history.pendingDraft) await flushHistoryDraft();
    }

    async function selectHistoryRevision(revisionId) {
        await flushHistoryDraft();
        const shot = state.plan?.shots?.[state.active];
        const history = state.history;
        if (!shot || !history.textarea) return;
        const sceneId = safeShotId(shot.id, `clip_${String(state.active + 1).padStart(4,"0")}`);
        const key = historyKey(sceneId);
        try {
            const payload = await historyRequest({}, {action:"activate", run_name:runName(), scene_id:sceneId, revision:revisionId});
            if (history.sceneKey !== key) return;
            history.data = payload.history; history.revisionId = payload.revision.id; history.error = "";
            history.textarea.value = String(payload.revision.prompt ?? "");
            shot.prompt = promptTextToLines(history.textarea.value);
            writePlan(history.status); renderHistory(); history.textarea.focus();
        } catch (error) { if (history.sceneKey === key) { history.error = error?.message || String(error); renderHistory(); } }
    }

    async function refreshCheckpointsNow() {
        if (state.disposed) return;
        const currentRun = runName();
        const token = ++state.checkpointToken;
        const editorialEpoch = state.editorialEditEpoch ?? 0;
        if (!currentRun) {
            const changed = state.checkpoints.size > 0
                || Boolean(state.checkpointSignature) || Boolean(state.checkpointError);
            state.checkpoints = new Map(); state.checkpointSignature = "";
            state.checkpointError = "";
            if (changed) { refreshTimelineCheckpoints(); renderStatus(); }
            return;
        }
        let editorialChanged = false;
        try {
            const query = new URLSearchParams({
                run_name:currentRun, include_graph:"false",
                branch_id:currentBranch(),
            });
            const response = await api.fetchApi(`/minimax_h3_context_loop/checkpoints?${query.toString()}`);
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
            if (state.disposed || token !== state.checkpointToken || currentRun !== runName()
                    || (payload.working_branch_id ?? "main") !== currentBranch()) return;
            editorialChanged = applyEditorialPayload(
                payload.editorial, editorialEpoch, payload.editorial_unused_scene_ids,
            );
            const records = payload.checkpoints ?? [];
            // Do not put a rejected, pre-edit GET back into workflow recovery.
            if (editorialEpoch === (state.editorialEditEpoch ?? 0)
                    && state.editorialTimer == null && !state.editorialSavePromise
                    && !state.editorialSaveError) {
                cacheStudioPresentation(records, payload.editorial);
            }
            const signature = studioCheckpointSignature(currentRun, records);
            const recoveredFromError = Boolean(state.checkpointError);
            state.checkpointError = "";
            if (signature === state.checkpointSignature) {
                if (editorialChanged) {
                    renderStatus(); renderTimeline();
                    if (["player", "subtitles"].includes(state.view)) renderPanel();
                }
                if (recoveredFromError) renderStatus();
                return;
            }
            state.checkpointSignature = signature;
            state.checkpoints = new Map(records.map((item) => [Number(item.scene), item]));
        } catch (error) {
            if (token !== state.checkpointToken) return;
            state.checkpointError = error?.message || String(error);
            renderStatus();
            return;
        }
        if (editorialChanged) renderTimeline();
        else refreshTimelineCheckpoints();
        renderStatus();
        if (state.view === "context") renderPanel();
        if (editorialChanged && ["player", "subtitles"].includes(state.view)) {
            renderPanel();
        }
        if (state.view === "player" && state.player) {
            const media = playerCheckpoint(state.playerIndex);
            const desired = media?.video ? videoUrl(media.video) : "";
            const desiredAudio = media?.audio ? videoUrl(media.audio) : "";
            if (desired !== String(state.player.dataset.source ?? "") ||
                    desiredAudio !== String(
                        state.playerAudio?.dataset.source ?? "")) renderPanel();
        }
    }

    async function refreshCheckpoints() {
        if (state.disposed) return;
        if (state.checkpointPromise) {
            state.checkpointRefreshQueued = true;
            return state.checkpointPromise;
        }
        const request = refreshCheckpointsNow();
        state.checkpointPromise = request;
        try {
            return await request;
        } finally {
            if (state.checkpointPromise === request) state.checkpointPromise = null;
            if (state.checkpointRefreshQueued && !state.disposed) {
                state.checkpointRefreshQueued = false;
                void refreshCheckpoints();
            }
        }
    }

    function renderStatus() {
        const host = root.querySelector(".h3studio-statusline");
        if (!host || !state.plan) return;
        const {result, totalSeconds, sceneEndSeconds} = timelineModel();
        const ready = result.shots.filter(
            (row, index) => matchingStudioCheckpoint(state.checkpoints, index, row),
        ).length;
        host.replaceChildren();
        host.append(
            element("strong", "", `${result.shots.length} scenes`),
            document.createTextNode(
                `${result.totalFrames} generated frames · ` +
                `${formatClock(sceneEndSeconds)} placed · ` +
                `blank track open to ${formatClock(totalSeconds)}`,
            ),
            document.createTextNode(`${settings().contextLength}f overlap · ${ready}/${result.shots.length} rendered`),
        );
        if (result.errors.length) host.append(element("span", "h3studio-error", `${result.errors.length} plan issue${result.errors.length === 1 ? "" : "s"}`));
        if (state.checkpointError) host.append(element("span", "h3studio-error", state.checkpointError));
        if (state.editorialBindingError) host.append(element("span", "h3studio-error", state.editorialBindingError));
        if (state.editorialSaveError) {
            host.append(element("span", "h3studio-error",
                `Editorial edits are not saved: ${state.editorialSaveError}`));
            const retry = button("Retry save", "Retry the current local editorial edits", () => {
                scheduleEditorialSave(0);
            });
            const reload = button("Reload saved cut", "Discard unsaved editorial edits and reload the saved final cut", () => {
                if (state.editorialSavePromise || state.editorialTimer != null) return;
                if (!window.confirm("Discard the unsaved editorial edits in this Studio and reload the saved final cut?")) return;
                state.editorialSaveError = "";
                state.editorialEditEpoch = (state.editorialEditEpoch ?? 0) + 1;
                void refreshCheckpoints();
            });
            retry.disabled = reload.disabled = Boolean(state.editorialSavePromise || state.editorialTimer != null);
            host.append(retry, reload);
        }
    }

    function timelinePixelAtSecond(seconds) {
        return studioTimelinePixelAtSecond(
            seconds,
            state.timelinePixelsPerSecond,
            Number(state.timelineContent?.dataset.timelineWidth) || 0,
        );
    }

    function positionTimelinePlayhead(seconds) {
        if (!state.playhead) return;
        state.playhead.style.left = `${timelinePixelAtSecond(seconds)}px`;
    }

    function renderRuler(ruler, totalSeconds) {
        ruler.replaceChildren();
        const width = Math.max(
            Number(state.timelineContent?.dataset.timelineWidth) || 0,
            ruler.clientWidth,
        );
        for (const tick of studioRulerTicks(totalSeconds, width)) {
            const marker = element(
                "span",
                `h3studio-ruler-tick${tick.major ? " h3studio-major" : ""}`,
            );
            marker.style.left = `${timelinePixelAtSecond(tick.seconds)}px`;
            if (tick.major) marker.append(
                element("span", "", formatClock(tick.seconds)),
            );
            ruler.append(marker);
        }
        const playhead = element("span", "h3studio-playhead");
        ruler.append(playhead);
        state.playhead = playhead;
        positionTimelinePlayhead(state.timelinePosition ?? 0);
        const hover = element("span", "h3studio-ruler-hover");
        hover.hidden = true;
        ruler.append(hover);
        const targetAtEvent = (event) => {
            if (!totalSeconds) return;
            const rect = ruler.getBoundingClientRect();
            const localRatio = (event.clientX - rect.left) /
                Math.max(1, rect.width);
            if (!(state.timelinePixelsPerSecond > 0)) {
                return Math.max(0, Math.min(
                    totalSeconds, localRatio * totalSeconds,
                ));
            }
            return Math.max(0, Math.min(
                totalSeconds,
                localRatio * width /
                    Math.max(Number.EPSILON, state.timelinePixelsPerSecond),
            ));
        };
        const showHover = (event) => {
            const target = targetAtEvent(event);
            if (target == null) return;
            hover.hidden = false;
            hover.textContent = formatClock(target);
            hover.style.left = `${timelinePixelAtSecond(target)}px`;
        };
        const scrub = (event) => {
            const target = targetAtEvent(event);
            if (target == null) return;
            state.timelinePosition = target;
            if (state.view !== "player") {
                state.view = "player"; persistView(); renderToolbarState(); renderPanel();
            } else seekTimeline(target, false);
            showHover(event);
        };
        ruler.onpointerdown = (event) => {
            if (event.button !== 0) return;
            event.preventDefault();
            ruler.setPointerCapture?.(event.pointerId);
            ruler.dataset.scrubbing = "true";
            scrub(event);
        };
        ruler.onpointermove = (event) => {
            showHover(event);
            if (ruler.dataset.scrubbing === "true") scrub(event);
        };
        ruler.onpointerup = ruler.onpointercancel = (event) => {
            delete ruler.dataset.scrubbing;
            ruler.releasePointerCapture?.(event.pointerId);
        };
        ruler.onpointerleave = () => {
            if (ruler.dataset.scrubbing !== "true") hover.hidden = true;
        };
    }

    function revealActiveTimelineScene() {
        const viewport = state.timelineViewport;
        const card = state.timelineHost?.querySelector(
            `[data-scene-index="${state.active}"]`,
        );
        if (!viewport || !card) return;
        const start = card.offsetLeft;
        const end = start + card.offsetWidth;
        const visibleStart = viewport.scrollLeft;
        const visibleEnd = visibleStart + viewport.clientWidth;
        let target = null;
        if (start < visibleStart) target = start;
        else if (end > visibleEnd) {
            target = Math.max(0, end - viewport.clientWidth);
        }
        if (target == null) return;
        state.timelineScrollIntentUntil = 0;
        state.timelineLastScrollLeft = target;
        viewport.scrollLeft = target;
    }

    function timelineScrollSnapshot(anchorRatio = .5) {
        const viewport = state.timelineViewport;
        const content = state.timelineContent;
        const totalSeconds = studioTimelineTotalSeconds(state.timelineSegments);
        const contentWidth = Math.max(
            0, Number(content?.dataset.timelineWidth) || content?.scrollWidth || 0,
        );
        if (!viewport || !content) return null;
        const boundedAnchor = Math.max(0, Math.min(
            1, Number(anchorRatio) || 0,
        ));
        return {
            scrollLeft:Math.max(0, Number(viewport.scrollLeft) || 0),
            seconds:contentWidth > 0 && totalSeconds > 0
                ? studioTimelineScrollAnchorSeconds(
                    viewport.scrollLeft, viewport.clientWidth,
                    contentWidth, totalSeconds, boundedAnchor,
                ) : null,
            anchorRatio:boundedAnchor,
        };
    }

    function layoutTimeline({preserveScroll = true, revealActive = false,
        anchorRatio = .5, restoreScroll = null} = {}) {
        const viewport = state.timelineViewport;
        const content = state.timelineContent;
        if (!viewport || !content || !state.plan) return;
        const boundedAnchor = Math.max(0, Math.min(
            1, Number(restoreScroll?.anchorRatio ?? anchorRatio) || 0,
        ));
        const restoredSeconds = restoreScroll == null
            ? Number.NaN : Number(restoreScroll.seconds);
        const restoredLeft = restoreScroll == null
            ? Number.NaN : Number(restoreScroll.scrollLeft);
        const preservedLeft = preserveScroll
            ? Math.max(0, Number(viewport.scrollLeft) || 0) : Number.NaN;
        const model = timelineModel();
        const result = model.result;
        const layout = studioTimelineLayout(
            result.shots, viewport.clientWidth, state.timelineZoom,
            state.editorial.placements, model.workspaceEndFrame,
            state.editorial.trims,
        );
        state.timelineZoom = layout.zoom;
        state.timelineWidths = layout.widths;
        state.timelineSegments = layout.segments;
        state.timelinePixelsPerSecond = layout.pixelsPerSecond;
        content.dataset.timelineWidth = String(layout.contentWidth);
        content.style.width = `${layout.contentWidth}px`;
        for (const host of [
            state.timelineHost, state.sourceTimelineHost,
            state.sourceAudioTimelineHost,
        ]) {
            if (!host) continue;
            [...host.querySelectorAll(
                "[data-timeline-key]",
            )].forEach((card) => {
                const index = layout.segments.findIndex(
                    (segment) => segment.key === card.dataset.timelineKey,
                );
                card.style.setProperty(
                    "--h3-scene-width",
                    `${layout.widths[index] ?? 0}px`,
                );
            });
        }
        if (state.timelineZoomInput) {
            state.timelineZoomInput.value = String(layout.zoom);
        }
        if (state.timelineZoomLabel) {
            state.timelineZoomLabel.textContent = `${Math.round(layout.zoom * 100)}%`;
        }
        if (state.timelineRuler) renderRuler(
            state.timelineRuler, layout.totalSeconds,
        );
        if (state.timelineLayoutFrame != null) {
            cancelAnimationFrame(state.timelineLayoutFrame);
        }
        state.timelineLayoutFrame = requestAnimationFrame(() => {
            state.timelineLayoutFrame = null;
            if (!viewport.isConnected) return;
            let targetLeft = Number.isFinite(restoredLeft)
                ? restoredLeft : preservedLeft;
            if (!Number.isFinite(targetLeft)
                    && Number.isFinite(restoredSeconds)) {
                targetLeft = studioTimelineScrollLeftForAnchor(
                    restoredSeconds, viewport.clientWidth,
                    layout.pixelsPerSecond, boundedAnchor,
                );
            }
            if (Number.isFinite(targetLeft)
                    && Math.abs(viewport.scrollLeft - targetLeft) > .5) {
                // Keep programmatic restoration invisible to the edge-growth
                // listener. Only a user's rightward scroll may grow the open
                // timeline workspace.
                state.timelineLastScrollLeft = targetLeft;
                viewport.scrollLeft = targetLeft;
            }
            if (revealActive) revealActiveTimelineScene();
            layoutChapterMarkers();
        });
    }

    function layoutChapterMarkers() {
        if (!state.timelineHost) return;
        for (const marker of state.timelineHost.querySelectorAll(
            ".h3studio-chapter-marker",
        )) {
            const index = Number(marker.dataset.startSceneIndex);
            const card = state.timelineHost.querySelector(
                `[data-scene-index="${index}"]`,
            );
            if (card) marker.style.left = `${card.offsetLeft}px`;
        }
    }

    function setTimelineZoom(value, anchorRatio = .5) {
        const zoomAnchor = timelineScrollSnapshot(anchorRatio);
        if (zoomAnchor) delete zoomAnchor.scrollLeft;
        state.timelineZoom = normalizedTimelineZoom(value);
        node.properties[TIMELINE_ZOOM_PROPERTY] = state.timelineZoom;
        layoutTimeline({
            preserveScroll:false, anchorRatio, restoreScroll:zoomAnchor,
        });
        dirty();
    }

    function extendTimelineWorkspace(minimumEndFrame = null) {
        if (state.timelineExtending) return false;
        const model = timelineModel();
        if (model.workspaceEndFrame >= 864000) return false;
        const pageFrames = Math.max(FPS, state.timelineSceneEndFrame);
        const requested = minimumEndFrame == null
            ? model.workspaceEndFrame + pageFrames
            : Number(minimumEndFrame);
        const next = Math.min(864000, Math.max(
            model.workspaceEndFrame + 1,
            Math.ceil(Number(requested) || 0),
        ));
        if (next <= model.workspaceEndFrame) return false;
        state.timelineExtending = true;
        state.timelineWorkspaceEndFrame = next;
        renderTimeline({revealActive:false});
        const updated = timelineModel();
        if (state.playerSlider) {
            state.playerSlider.max = String(updated.totalSeconds);
        }
        const clock = root.querySelector(".h3studio-player-clock");
        if (clock) clock.textContent = `${formatClock(state.timelinePosition ?? 0)} / ${formatClock(updated.totalSeconds)}`;
        renderStatus();
        requestAnimationFrame(() => { state.timelineExtending = false; });
        return true;
    }

    function sourceScene(index) {
        return matchingStudioSourceScene(
            state.sourcePreview, index, timing().shots[index],
        );
    }

    function sourceReference(index) {
        return sourceScene(index)?.references?.[0] ?? null;
    }

    function sourcePreviewUrl(index, reference = sourceReference(index)) {
        if (!state.sourcePreview?.token || !reference) return "";
        const query = new URLSearchParams({
            token:state.sourcePreview.token,
            scene:String(index + 1),
            slot:String(reference.slot ?? 0),
        });
        return api.apiURL(`/minimax_h3_context_loop/plan-studio/source-preview?${query.toString()}`);
    }

    function checkpointThumbnailUrl(index, checkpoint) {
        const revision = String(
            checkpoint?.presentation_revision ?? checkpoint?.revision ?? "",
        ).trim().toLowerCase();
        if (!checkpoint?.ready || !/^[0-9a-f]{32}$/.test(revision) || !runName()) {
            return "";
        }
        const query = new URLSearchParams({
            run_name:runName(), scene:String(index + 1), revision,
            branch_id:currentBranch(),
        });
        return api.apiURL(`/minimax_h3_context_loop/plan-studio/checkpoint-thumbnail?${query.toString()}`);
    }

    function sourceAudio() {
        return matchingStudioSourceAudio(
            state.sourcePreview, timing().shots, runName(),
        );
    }

    function sourceAudioUrl() {
        if (!sourceAudio() || !state.sourcePreview?.token) return "";
        const query = new URLSearchParams({token:state.sourcePreview.token});
        return api.apiURL(`/minimax_h3_context_loop/plan-studio/source-audio?${query.toString()}`);
    }

    function sourceWaveformUrl(payload = state.sourcePreview) {
        if (!payload?.source_audio?.available || !payload?.token) return "";
        const availableFrames = Number(
            payload.source_audio.available_frame_count,
        ) || Math.ceil(timelineModel().totalSeconds * FPS);
        const requestedFrames = Math.max(
            Number(payload.source_audio.frame_count) || 0,
            Math.min(
                availableFrames,
                Math.ceil(timelineModel().totalSeconds * FPS),
            ),
        );
        const query = new URLSearchParams({
            token:payload.token,
            frame_count:String(requestedFrames),
        });
        return api.apiURL(`/minimax_h3_context_loop/plan-studio/source-waveform?${query.toString()}`);
    }

    function sourceAudioMuteMap() {
        const current = node.properties[SOURCE_AUDIO_MUTES_PROPERTY];
        if (current && typeof current === "object" && !Array.isArray(current)) return current;
        const created = {};
        node.properties[SOURCE_AUDIO_MUTES_PROPERTY] = created;
        return created;
    }

    function sourceAudioMuted(index) {
        const row = timing().shots[index];
        const key = row ? `${runName()}::${String(row.id)}` : "";
        return Boolean(key && sourceAudioMuteMap()[key]);
    }

    function setSourceAudioMuted(index, muted) {
        const row = timing().shots[index];
        if (!row) return;
        const mutes = sourceAudioMuteMap();
        const key = `${runName()}::${String(row.id)}`;
        if (muted) mutes[key] = true;
        else delete mutes[key];
        dirty();
        renderSourceAudioTimeline();
        root.querySelector(".h3studio-audio-generated")?.dispatchEvent(
            new Event("change"),
        );
    }

    function drawSourceWaveform(canvas, samples, color, muted) {
        requestAnimationFrame(() => {
            if (!canvas.isConnected) return;
            const ratio = Math.max(1, window.devicePixelRatio || 1);
            const width = Math.max(1, Math.round(canvas.clientWidth * ratio));
            const height = Math.max(1, Math.round(canvas.clientHeight * ratio));
            canvas.width = width; canvas.height = height;
            const context = canvas.getContext("2d");
            if (!context) return;
            context.clearRect(0, 0, width, height);
            context.strokeStyle = muted ? "rgba(190,198,214,.52)" : color;
            context.lineWidth = Math.max(1, ratio);
            const middle = height / 2;
            context.beginPath();
            if (!samples.length) {
                context.moveTo(0, middle); context.lineTo(width, middle);
            } else {
                for (let x = 0; x < width; x += Math.max(1, Math.round(ratio))) {
                    const sample = Math.max(0, Math.min(1, Number(
                        samples[Math.min(samples.length - 1, Math.floor(
                            x / width * samples.length,
                        ))],
                    ) || 0));
                    const half = Math.max(ratio, sample * (height / 2 - ratio));
                    context.moveTo(x, middle - half); context.lineTo(x, middle + half);
                }
            }
            context.stroke();
        });
    }

    async function loadSourceWaveform(payload = state.sourcePreview) {
        const token = String(payload?.token ?? "");
        const url = sourceWaveformUrl(payload);
        if (!token || !url) return;
        const requestKey = `${token}:${url}`;
        if (state.sourceWaveformToken === requestKey && state.sourceWaveform) return;
        if (state.sourceWaveformToken === requestKey && state.sourceWaveformPromise) {
            // The owning request below reports errors in the timeline. A
            // deduplicated fire-and-forget caller must not reject separately.
            await state.sourceWaveformPromise.catch(() => {});
            return;
        }
        const sameSource = state.sourceWaveformToken.startsWith(`${token}:`);
        state.sourceWaveformToken = requestKey;
        // Keep the already-loaded portion visible while a longer arrangement
        // requests more coverage. Never retain a different source's waveform.
        if (!sameSource) state.sourceWaveform = null;
        const request = (async () => {
            const response = await api.fetchApi(url);
            const waveform = await response.json();
            if (!response.ok) throw new Error(
                waveform.error || `HTTP ${response.status}`,
            );
            if (state.disposed || state.sourceWaveformToken !== requestKey) return;
            state.sourceWaveform = waveform;
            renderSourceAudioTimeline();
        })();
        state.sourceWaveformPromise = request;
        try { await request; }
        catch (error) {
            if (state.sourceWaveformToken === requestKey) {
                state.sourceWaveform = {samples:[], error:error?.message || String(error)};
                renderSourceAudioTimeline();
            }
        } finally {
            if (state.sourceWaveformPromise === request) {
                state.sourceWaveformPromise = null;
            }
        }
    }

    function applySourcePresentation(payload) {
        if (!payload || String(payload.run_name ?? "") !== runName()
                || (payload._branch_id ?? "main") !== currentBranch()) return;
        if (!state.sourceWaveformToken.startsWith(
            `${String(payload.token ?? "")}:`,
        )) {
            state.sourceWaveform = null;
            state.sourceWaveformToken = "";
            state.sourceWaveformPromise = null;
        }
        state.sourcePreview = payload;
        renderSourceTimeline();
        renderSourceAudioTimeline();
        if (payload.source_audio?.available) void loadSourceWaveform(payload);
        if (state.view === "player" && state.player) {
            seekTimeline(
                state.timelinePosition ?? studioEditorialSceneStartSeconds(
                    timelineModel().segments, state.active),
                false,
            );
        }
    }

    async function restoreSourcePresentation() {
        const currentRun = runName();
        if (!currentRun || state.disposed) return;
        const token = ++state.presentationToken;
        try {
            const query = new URLSearchParams({run_name:currentRun, branch_id:currentBranch()});
            const response = await api.fetchApi(
                `/minimax_h3_context_loop/plan-studio/presentation?${query.toString()}`,
            );
            const payload = await response.json();
            if (response.status === 404) return;
            if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
            if (state.disposed || token !== state.presentationToken
                    || currentRun !== runName()) return;
            applySourcePresentation(payload);
        } catch (error) {
            if (token === state.presentationToken) {
                console.warn("Plan Studio could not restore its saved track", error);
            }
        }
    }

    function updateTimelineCheckpointCard(card, index, result = timing()) {
        if (!card) return;
        const row = result.shots[index];
        const checkpoint = matchingStudioCheckpoint(
            state.checkpoints, index, row,
        );
        card.classList.toggle("h3studio-rendered", Boolean(checkpoint?.ready));
        card.classList.toggle(
            "h3studio-alternate-selected",
            Boolean(checkpoint?.presentation_revision),
        );
        const url = checkpointThumbnailUrl(index, checkpoint);
        const current = card.querySelector(".h3studio-card-thumbnail");
        if (!url) {
            current?.remove();
            delete card.dataset.checkpointThumbnail;
            return;
        }
        if (card.dataset.checkpointThumbnail === url && current) return;
        current?.remove();
        const image = element("img", "h3studio-card-thumbnail");
        image.alt = "";
        image.loading = "lazy";
        image.decoding = "async";
        image.src = url;
        image.addEventListener("error", () => {
            image.remove();
            if (card.dataset.checkpointThumbnail === url) {
                delete card.dataset.checkpointThumbnail;
            }
        }, {once:true});
        card.dataset.checkpointThumbnail = url;
        card.prepend(image);
    }

    function refreshTimelineCheckpoints() {
        if (!state.timelineHost || !state.plan) return;
        const result = timing();
        for (const card of state.timelineHost.querySelectorAll(
            ".h3studio-card[data-scene-index]",
        )) {
            const index = Number(card.dataset.sceneIndex);
            if (Number.isInteger(index) && index >= 0
                    && index < result.shots.length) {
                updateTimelineCheckpointCard(card, index, result);
            }
        }
    }

    function trailingGapSegment() {
        return state.timelineSegments.find(
            (segment) => segment.kind === "gap" && segment.trailing,
        ) ?? null;
    }

    function appendTimelineGap(host, segment, interactive = false) {
        if (!host || !segment) return;
        const gap = interactive
            ? button("", segment.trailing
                ? `Open black timeline after the last scene · ${formatClock(segment.durationSeconds)}`
                : `Black gap · ${formatClock(segment.durationSeconds)}`, (event) => {
                const rect = event.currentTarget?.getBoundingClientRect?.();
                const ratio = rect?.width > 0 ? Math.max(0, Math.min(
                    1, (event.clientX - rect.left) / rect.width,
                )) : 0;
                state.timelinePosition = segment.startSeconds +
                    segment.durationSeconds * ratio;
                state.view = "player"; persistView(); renderToolbarState(); renderPanel();
            })
            : element("div");
        gap.className = interactive ? "h3studio-gap" : "h3studio-gap-spacer";
        gap.dataset.timelineKey = segment.key;
        gap.dataset.gapId = segment.gapId;
        const segmentIndex = state.timelineSegments.findIndex(
            (candidate) => candidate.key === segment.key,
        );
        if (state.timelineWidths[segmentIndex] > 0) gap.style.setProperty(
            "--h3-scene-width", `${state.timelineWidths[segmentIndex]}px`,
        );
        if (interactive) gap.append(element(
            "span", "h3studio-gap-copy",
            segment.trailing
                ? `OPEN TIMELINE · ${formatClock(segment.durationSeconds)}`
                : `EMPTY · ${formatClock(segment.durationSeconds)}`,
        ));
        host.append(gap);
    }

    function enableScenePlacementDrag(card, handle, index) {
        handle.title = sceneLocked(index)
            ? "Scene locked · unlock it before moving"
            : "Drag the clip or its grip to set the editorial start. Empty track space becomes black.";
        const startDrag = (event) => {
            if (event.button !== 0 || sceneLocked(index)) return;
            if (event.target?.closest?.(
                ".h3studio-lock-handle,.h3studio-resize-handle,button,input,select,textarea",
            )) return;
            event.preventDefault(); event.stopPropagation();
            const model = timelineModel();
            const scene = model.segments.find(
                (segment) => segment.kind === "scene" && segment.sceneIndex === index,
            );
            if (!scene) return;
            const contentWidth = Math.max(
                1, Number(state.timelineContent?.dataset.timelineWidth)
                    || state.timelineContent?.clientWidth || 1,
            );
            const secondsPerPixel = model.totalSeconds / contentWidth;
            const originX = event.clientX;
            const viewport = state.timelineViewport;
            const originScrollLeft = viewport?.scrollLeft ?? 0;
            const pointerId = event.pointerId;
            let targetFrame = scene.startFrame;
            let moved = false;
            state.timelineDragging = true;
            card.classList.add("h3studio-moving");
            card.setPointerCapture?.(pointerId);
            const onMove = (moveEvent) => {
                if (moveEvent.pointerId !== pointerId) return;
                moveEvent.preventDefault();
                if (viewport) {
                    const rect = viewport.getBoundingClientRect();
                    if (moveEvent.clientX > rect.right - 36) viewport.scrollLeft += 18;
                    else if (moveEvent.clientX < rect.left + 36) viewport.scrollLeft -= 18;
                }
                const deltaX = moveEvent.clientX - originX +
                    ((viewport?.scrollLeft ?? 0) - originScrollLeft);
                if (Math.abs(deltaX) > 3) moved = true;
                if (!moved) return;
                targetFrame = Math.max(
                    0,
                    Math.min(864000, Math.round(
                        scene.startFrame + deltaX * secondsPerPixel * FPS,
                    )),
                );
                const placedDeltaX = (targetFrame - scene.startFrame)
                    / Math.max(Number.EPSILON, secondsPerPixel * FPS);
                card.style.transform = `translateX(${placedDeltaX}px)`;
                card.style.opacity = ".78";
                handle.textContent = formatClock(targetFrame / FPS);
            };
            const finish = (upEvent) => {
                if (upEvent.pointerId !== pointerId) return;
                window.removeEventListener("pointermove", onMove, true);
                window.removeEventListener("pointerup", finish, true);
                window.removeEventListener("pointercancel", finish, true);
                if (card.hasPointerCapture?.(pointerId)) {
                    card.releasePointerCapture(pointerId);
                }
                state.timelineDragging = false;
                card.classList.remove("h3studio-moving");
                card.style.removeProperty("transform");
                card.style.removeProperty("opacity");
                handle.textContent = "⋮⋮";
                card._h3SuppressClick = moved;
                if (moved && upEvent.type === "pointerup") {
                    setScenePlacement(index, targetFrame);
                }
            };
            window.addEventListener("pointermove", onMove, true);
            window.addEventListener("pointerup", finish, true);
            window.addEventListener("pointercancel", finish, true);
        };
        card.addEventListener("pointerdown", startDrag);
    }

    function enableSceneDurationDrag(card, handle, index) {
        const defaultTitle = sceneLocked(index)
            ? "Scene locked · unlock it before changing its length"
            : "Drag to set this scene's generated length. It snaps to H3's 17n+5 frame grid.";
        handle.title = defaultTitle;
        handle.addEventListener("pointerdown", (event) => {
            if (event.button !== 0 || sceneLocked(index)) return;
            event.preventDefault(); event.stopPropagation();
            const model = timelineModel();
            const row = model.result.shots[index];
            const shot = state.plan?.shots?.[index];
            if (!row || !shot) return;
            const contentWidth = Math.max(
                1, Number(state.timelineContent?.dataset.timelineWidth)
                    || state.timelineContent?.clientWidth || 1,
            );
            const secondsPerPixel = model.totalSeconds / contentWidth;
            const contextFrames = Math.max(
                0, Number(row.rawFrames) - Number(row.deliveredFrames),
            );
            const minimumRaw = studioNearestH3FrameLength(
                contextFrames + 1, contextFrames + 1, 3592,
            );
            const originX = event.clientX;
            const originWidth = card.getBoundingClientRect().width;
            let targetRaw = Number(row.rawFrames) || minimumRaw;
            let moved = false;
            state.timelineDragging = true;
            handle.setPointerCapture?.(event.pointerId);
            const onMove = (moveEvent) => {
                const deltaX = moveEvent.clientX - originX;
                if (Math.abs(deltaX) > 2) moved = true;
                if (!moved) return;
                targetRaw = studioNearestH3FrameLength(
                    Number(row.rawFrames) + deltaX * secondsPerPixel * FPS,
                    minimumRaw, 3592,
                );
                const deliveredFrames = Math.max(1, targetRaw - contextFrames);
                card.style.setProperty(
                    "--h3-scene-width",
                    `${deliveredFrames / FPS / secondsPerPixel}px`,
                );
                handle.title = `${targetRaw} raw frames · ${(targetRaw / FPS).toFixed(3)}s generation`;
            };
            const finish = (upEvent) => {
                handle.removeEventListener("pointermove", onMove);
                handle.removeEventListener("pointerup", finish);
                handle.removeEventListener("pointercancel", finish);
                handle.releasePointerCapture?.(upEvent.pointerId);
                state.timelineDragging = false;
                handle.title = defaultTitle;
                card.style.setProperty("--h3-scene-width", `${originWidth}px`);
                if (!moved || targetRaw === Number(row.rawFrames)) return;
                shot.length = targetRaw;
                delete shot.frames;
                delete shot.duration_seconds;
                writePlan();
                renderShell();
            };
            handle.addEventListener("pointermove", onMove);
            handle.addEventListener("pointerup", finish);
            handle.addEventListener("pointercancel", finish);
        });
    }

    function enableSceneLatentTrimDrag(card, handle, index) {
        const row = timing().shots[index];
        if (!row) return;
        const fullFrames = Math.max(1, Number(row.deliveredFrames) || 1);
        const options = studioLatentSafeOutFrames(
            row.rawFrames, fullFrames,
        );
        const currentOut = Number(trimForScene(index)?.out_frame) || fullFrames;
        const defaultTitle = sceneLocked(index)
            ? "Scene locked · unlock it before changing the used endpoint"
            : `Latent-safe used end · ${currentOut}/${fullFrames} frames. Drag to trim; double-click to restore the full checkpoint.`;
        handle.title = defaultTitle;
        handle.addEventListener("dblclick", (event) => {
            event.preventDefault(); event.stopPropagation();
            if (!sceneLocked(index)) setSceneTrim(index, fullFrames);
        });
        handle.addEventListener("pointerdown", (event) => {
            if (event.button !== 0 || sceneLocked(index) || !options.length) return;
            event.preventDefault(); event.stopPropagation();
            const model = timelineModel();
            const contentWidth = Math.max(
                1, Number(state.timelineContent?.dataset.timelineWidth)
                    || state.timelineContent?.clientWidth || 1,
            );
            const secondsPerPixel = model.totalSeconds / contentWidth;
            const originX = event.clientX;
            const originWidth = card.getBoundingClientRect().width;
            let targetOut = currentOut;
            let moved = false;
            state.timelineDragging = true;
            handle.setPointerCapture?.(event.pointerId);
            const onMove = (moveEvent) => {
                const deltaX = moveEvent.clientX - originX;
                if (Math.abs(deltaX) > 2) moved = true;
                if (!moved) return;
                targetOut = studioNearestLatentSafeOutFrame(
                    row.rawFrames, fullFrames,
                    currentOut + deltaX * secondsPerPixel * FPS,
                );
                card.style.setProperty(
                    "--h3-scene-width",
                    `${targetOut / FPS / secondsPerPixel}px`,
                );
                handle.title = `${targetOut}/${fullFrames} frames used · ${(targetOut / FPS).toFixed(3)}s · full sampled checkpoint retained`;
            };
            const finish = (upEvent) => {
                handle.removeEventListener("pointermove", onMove);
                handle.removeEventListener("pointerup", finish);
                handle.removeEventListener("pointercancel", finish);
                handle.releasePointerCapture?.(upEvent.pointerId);
                state.timelineDragging = false;
                handle.title = defaultTitle;
                card.style.setProperty("--h3-scene-width", `${originWidth}px`);
                if (moved && targetOut !== currentOut) {
                    setSceneTrim(index, targetOut);
                }
            };
            handle.addEventListener("pointermove", onMove);
            handle.addEventListener("pointerup", finish);
            handle.addEventListener("pointercancel", finish);
        });
    }

    function renderTimeline({revealActive = false, restoreScroll = null} = {}) {
        const host = state.timelineHost;
        if (!host || !state.plan) return;
        const preservedScroll = restoreScroll ?? timelineScrollSnapshot();
        host.replaceChildren();
        const result = timing();
        state.timelineSegments = timelineModel().segments;
        for (const timelineSegment of state.timelineSegments) {
            if (timelineSegment.kind === "gap") {
                if (!timelineSegment.trailing) {
                    appendTimelineGap(host, timelineSegment, true);
                }
                continue;
            }
            const index = timelineSegment.sceneIndex;
            const row = result.shots[index];
            const checkpoint = matchingStudioCheckpoint(state.checkpoints, index, row);
            const locked = sceneLocked(index);
            const card = element("div");
            card.title = locked
                ? `Scene ${index + 1}: ${row.id} · locked`
                : `Scene ${index + 1}: ${row.id} · drag to move`;
            if (checkpoint?.continuation_stale) card.title +=
                ` · regenerate: ${checkpoint.continuation_stale_reason}`;
            card.tabIndex = 0;
            card.setAttribute("role", "button");
            card.addEventListener("click", (event) => {
                if (card._h3SuppressClick) {
                    card._h3SuppressClick = false;
                    event.preventDefault(); event.stopPropagation();
                    return;
                }
                void selectScene(index);
            });
            card.addEventListener("keydown", (event) => {
                if (event.target !== card || !["Enter", " "].includes(event.key)) return;
                event.preventDefault();
                void selectScene(index);
            });
            card.dataset.sceneIndex = String(index);
            card.dataset.timelineKey = `scene:${index}`;
            card.className = `h3studio-card${index === state.active ? " h3studio-selected" : ""}${checkpoint?.ready ? " h3studio-rendered" : ""}${checkpoint?.continuation_stale ? " h3studio-continuation-stale" : ""}${locked ? " h3studio-locked" : ""}`;
            card.style.setProperty("--scene", automaticSceneColor(index));
            const segmentIndex = state.timelineSegments.findIndex(
                (segment) => segment.key === card.dataset.timelineKey,
            );
            if (state.timelineWidths[segmentIndex] > 0) {
                card.style.setProperty(
                    "--h3-scene-width", `${state.timelineWidths[segmentIndex]}px`,
                );
            }
            updateTimelineCheckpointCard(card, index, result);
            const sceneSegment = timelineSegment;
            const usedFrames = Number(sceneSegment?.durationFrames)
                || Number(row.deliveredFrames) || 0;
            const copy = element("span", "h3studio-card-copy");
            copy.append(element("span", "h3studio-card-title", `${index + 1}. ${row.id}`),
                element("span", "h3studio-card-meta", `${formatClock(sceneSegment?.startSeconds ?? 0)} → ${formatClock(sceneSegment?.endSeconds ?? row.deliveredSeconds)} · ${usedFrames}/${row.deliveredFrames}f used${row.loraRoute === "base" ? "" : ` · LoRA ${row.loraRoute.toUpperCase()}`}`));
            const dragHandle = element("span", "h3studio-drag-handle", "⋮⋮");
            dragHandle.title = locked
                ? "Scene locked · unlock it before moving"
                : "Move scene on the editorial timeline";
            enableScenePlacementDrag(card, dragHandle, index);
            const lockHandle = button(
                "",
                locked
                ? "Unlock scene movement, duration editing, and the saved chapter resolution pin"
                : "Lock scene movement and duration; saved scenes also pin their chapter resolution",
                (event) => {
                    event.preventDefault(); event.stopPropagation();
                    setSceneLocked(index, !locked);
                },
            );
            lockHandle.className = `h3studio-lock-handle${locked ? " h3studio-is-locked" : ""}`;
            lockHandle.setAttribute("aria-label", locked ? "Unlock scene" : "Lock scene");
            const lockIcon = element("span", "h3studio-lock-icon");
            lockIcon.setAttribute("aria-hidden", "true");
            lockHandle.append(lockIcon);
            lockHandle.addEventListener("pointerdown", (event) => event.stopPropagation());
            const resizeHandle = element("span", "h3studio-resize-handle");
            if (checkpoint?.ready) {
                resizeHandle.classList.add("h3studio-latent-trim");
                enableSceneLatentTrimDrag(card, resizeHandle, index);
            } else {
                resizeHandle.title = locked
                    ? "Scene locked · unlock it before changing its length"
                    : "Resize generated length · snaps to H3's 17n+5 frame grid";
                enableSceneDurationDrag(card, resizeHandle, index);
            }
            card.append(
                copy, dragHandle, lockHandle, resizeHandle,
                element("span", "h3studio-render-dot"),
            );
            host.append(card);
        }
        appendTimelineGap(host, trailingGapSegment(), true);
        for (const chapter of orderedChapters(state.plan)) {
            const index = state.plan.shots.findIndex((shot, offset) => (
                safeShotId(shot?.id, `clip_${String(offset + 1).padStart(4, "0")}`)
                    === chapter.start_scene_id
            ));
            if (index < 0) continue;
            const marker = button("", `${chapter.title}, before scene ${index + 1}`, () => {
                void selectChapter(chapter.id);
            });
            marker.className = `h3studio-chapter-marker${state.activeChapterId === chapter.id ? " h3studio-selected" : ""}`;
            marker.dataset.startSceneIndex = String(index);
            marker.dataset.chapterId = chapter.id;
            marker.append(element("span", "", chapter.title));
            host.append(marker);
        }
        renderSourceTimeline();
        renderSourceAudioTimeline();
        renderSubtitleTimeline();
        layoutTimeline({
            preserveScroll:false,
            revealActive,
            restoreScroll:preservedScroll,
        });
        state.timelineRenderedActive = state.active;
    }

    function renderSourceTimeline() {
        const host = state.sourceTimelineHost;
        if (!host || !state.plan) return;
        host.replaceChildren();
        const result = timing();
        const available = result.shots.some((_row, index) => sourceScene(index));
        if (!available) {
            host.append(element(
                "div", "h3studio-source-empty",
                state.sourcePreview
                    ? "No active path-backed motion reference in this Plan."
                    : "Queue Plan Studio once to load motion-reference windows.",
            ));
            return;
        }
        for (const timelineSegment of state.timelineSegments) {
            if (timelineSegment.kind === "gap") {
                if (!timelineSegment.trailing) {
                    appendTimelineGap(host, timelineSegment);
                }
                continue;
            }
            const index = timelineSegment.sceneIndex;
            const row = result.shots[index];
            const scene = sourceScene(index);
            const reference = scene?.references?.[0] ?? null;
            const card = button("", reference
                ? `Scene ${index + 1} source motion @${reference.tag}`
                : `Scene ${index + 1} has no active path-backed motion reference`,
            () => void selectScene(index));
            card.dataset.sceneIndex = String(index);
            card.dataset.timelineKey = `scene:${index}`;
            card.className = `h3studio-card h3studio-source-card${index === state.active ? " h3studio-selected" : ""}`;
            card.style.setProperty("--scene", automaticSceneColor(index));
            const segmentIndex = state.timelineSegments.findIndex(
                (segment) => segment.key === card.dataset.timelineKey,
            );
            if (state.timelineWidths[segmentIndex] > 0) {
                card.style.setProperty(
                    "--h3-scene-width", `${state.timelineWidths[segmentIndex]}px`,
                );
            }
            if (reference && index === state.active && state.view !== "player") {
                const media = element("video");
                media.muted = true; media.playsInline = true; media.preload = "metadata";
                media.src = sourcePreviewUrl(index, reference);
                media.addEventListener("loadedmetadata", () => {
                    try { media.currentTime = studioSourceSecond(reference, 0); }
                    catch (_error) {}
                }, {once:true});
                card.append(media);
            }
            const copy = element("span", "h3studio-card-copy");
            copy.append(
                element("span", "h3studio-card-title", reference
                    ? `@${reference.tag} · ${reference.start_frame}:${reference.end_frame}`
                    : "No active @motion"),
                element("span", "h3studio-card-meta", reference
                    ? `${reference.frame_count}f source · +${reference.compare_offset_frames}f compare offset`
                    : `Scene ${index + 1}`),
            );
            card.append(copy, element("span", "h3studio-render-dot"));
            host.append(card);
        }
        appendTimelineGap(host, trailingGapSegment());
    }

    function renderSourceAudioTimeline() {
        const host = state.sourceAudioTimelineHost;
        if (!host || !state.plan) return;
        host.replaceChildren();
        const audio = sourceAudio();
        if (!audio) {
            const descriptor = state.sourcePreview?.source_audio;
            const message = !state.sourcePreview
                ? "Queue Plan Studio once to load Source Timeline audio."
                : descriptor?.timeline_available && !descriptor?.has_audio
                    ? "Source Timeline connected · no audio."
                    : descriptor?.timeline_available
                        ? "Source Timeline audio exists but is not path-backed yet."
                        : "No Source Timeline is connected.";
            host.append(element("div", "h3studio-source-empty", message));
            return;
        }
        const result = timing();
        for (const timelineSegment of state.timelineSegments) {
            if (timelineSegment.kind === "gap") {
                if (timelineSegment.trailing) continue;
                const gap = timelineSegment;
                const gapCard = element("div", "h3studio-audio-card");
                gapCard.dataset.timelineKey = gap.key;
                gapCard.title = `Source song continues through ${formatClock(gap.durationSeconds)} of black video`;
                const gapSegmentIndex = state.timelineSegments.findIndex(
                    (segment) => segment.key === gap.key,
                );
                if (state.timelineWidths[gapSegmentIndex] > 0) gapCard.style.setProperty(
                    "--h3-scene-width", `${state.timelineWidths[gapSegmentIndex]}px`,
                );
                const gapWaveform = element("canvas", "h3studio-waveform");
                gapWaveform.style.inset = "3px 4px";
                gapWaveform.style.width = "calc(100% - 8px)";
                drawSourceWaveform(
                    gapWaveform,
                    studioWaveformIntervalSamples(
                        state.sourceWaveform, gap.startSeconds,
                        gap.durationSeconds,
                    ),
                    "#9aa5b8", false,
                );
                gapCard.append(gapWaveform);
                host.append(gapCard);
                continue;
            }
            const index = timelineSegment.sceneIndex;
            const row = result.shots[index];
            const muted = sourceAudioMuted(index);
            const card = element(
                "div",
                `h3studio-audio-card${index === state.active ? " h3studio-selected" : ""}${muted ? " h3studio-audio-muted" : ""}`,
            );
            card.dataset.sceneIndex = String(index);
            card.dataset.timelineKey = `scene:${index}`;
            card.title = `Scene ${index + 1} Source Timeline audio${muted ? " (muted)" : ""}`;
            card.style.setProperty("--scene", automaticSceneColor(index));
            const segmentIndex = state.timelineSegments.findIndex(
                (segment) => segment.key === card.dataset.timelineKey,
            );
            if (state.timelineWidths[segmentIndex] > 0) {
                card.style.setProperty(
                    "--h3-scene-width", `${state.timelineWidths[segmentIndex]}px`,
                );
            }
            card.addEventListener("click", () => void selectScene(index));
            const canvas = element("canvas", "h3studio-waveform");
            const sceneSegment = timelineSegment;
            const samples = studioWaveformIntervalSamples(
                state.sourceWaveform,
                sceneSegment?.startSeconds ?? 0,
                sceneSegment?.durationSeconds ?? row.deliveredSeconds,
            );
            drawSourceWaveform(
                canvas, samples, automaticSceneColor(index), muted,
            );
            const mute = button(
                muted ? "🔇" : "🔊",
                muted
                    ? `Unmute source audio for scene ${index + 1}`
                    : `Mute source audio for scene ${index + 1}`,
                (event) => {
                    event.stopPropagation();
                    setSourceAudioMuted(index, !muted);
                },
            );
            mute.className = "h3studio-audio-mute";
            card.append(canvas, mute);
            host.append(card);
        }
        const tail = trailingGapSegment();
        if (tail) {
            const tailCard = element("div", "h3studio-audio-card");
            tailCard.dataset.timelineKey = tail.key;
            tailCard.title = "Source song continues through the open black timeline";
            const segmentIndex = state.timelineSegments.findIndex(
                (segment) => segment.key === tail.key,
            );
            if (state.timelineWidths[segmentIndex] > 0) tailCard.style.setProperty(
                "--h3-scene-width", `${state.timelineWidths[segmentIndex]}px`,
            );
            const waveform = element("canvas", "h3studio-waveform");
            waveform.style.inset = "3px 4px";
            waveform.style.width = "calc(100% - 8px)";
            drawSourceWaveform(
                waveform,
                studioWaveformIntervalSamples(
                    state.sourceWaveform, tail.startSeconds,
                    tail.durationSeconds,
                ),
                "#9aa5b8", false,
            );
            tailCard.append(waveform);
            host.append(tailCard);
        }
        // Resizes, trims and placement edits can change the coverage needed.
        // The token + URL cache coalesces repeated renders and in-flight loads.
        void loadSourceWaveform();
    }

    function renderSubtitleTimeline() {
        const host = state.subtitleTimelineHost;
        if (!host) return;
        host.replaceChildren();
        const {totalSeconds} = timelineModel();
        if (!totalSeconds) return;
        const offset = Number(state.editorial.subtitles?.offset_seconds) || 0;
        for (const cue of subtitleCues()) {
            const start = Math.max(0, Number(cue.startSeconds) + offset);
            const end = Math.min(totalSeconds, Number(cue.endSeconds) + offset);
            if (!(end > start)) continue;
            const item = element("div", "h3studio-subtitle-cue", cue.text);
            item.title = `${formatClock(start)}–${formatClock(end)} · ${cue.text}`;
            item.style.left = `${start / totalSeconds * 100}%`;
            item.style.width = `${(end - start) / totalSeconds * 100}%`;
            host.append(item);
        }
    }

    function updateTimelineSelection() {
        for (const host of [
            state.timelineHost, state.sourceTimelineHost,
            state.sourceAudioTimelineHost,
        ]) {
            if (!host) continue;
            [...host.querySelectorAll(".h3studio-card,.h3studio-audio-card")].forEach(
                (card) => card.classList.toggle(
                    "h3studio-selected", Number(card.dataset.sceneIndex) === state.active,
                ),
            );
        }
        for (const marker of state.timelineHost?.querySelectorAll(
            ".h3studio-chapter-marker",
        ) ?? []) {
            marker.classList.toggle(
                "h3studio-selected",
                Boolean(state.activeChapterId)
                    && marker.dataset.chapterId === state.activeChapterId,
            );
        }
    }

    async function selectScene(index, synchronize = true, reveal = true) {
        if (!state.plan?.shots?.length || !Number.isFinite(Number(index))) return;
        const requested = Math.max(0, Math.min(state.plan.shots.length - 1, Math.trunc(Number(index))));
        const selection = {
            planNode:state.planNode, runName:runName(),
            sceneId:String(state.plan.shots[requested]?.id ?? ""), index:requested,
        };
        state.sceneNavigation = selection;
        await flushHistoryDraft();
        // A late history save must not undo a newer click (including an
        // empty scene) or send that stale selection back to the prompt editor.
        if (state.disposed || state.sceneNavigation !== selection
                || state.planNode !== selection.planNode
                || runName() !== selection.runName) return;
        state.sceneNavigation = null;
        const target = selection.sceneId
                && String(state.plan.shots[selection.index]?.id ?? "") !== selection.sceneId
            ? state.plan.shots.findIndex(shot => String(shot?.id ?? "") === selection.sceneId)
            : selection.index;
        if (!state.plan.shots[target]) return;
        state.activeChapterId = "";
        state.active = target;
        if (state.view === "player") {
            state.timelinePosition = studioEditorialSceneStartSeconds(
                timelineModel().segments, state.active,
            );
        }
        persistView(); renderSourceTimeline(); renderSourceAudioTimeline();
        updateTimelineSelection();
        if (reveal) revealActiveTimelineScene();
        if (state.view === "player" && state.player) {
            seekTimeline(state.timelinePosition, false);
        } else renderPanel();
        if (synchronize) publishActiveScene();
    }

    async function selectChapter(chapterId) {
        state.sceneNavigation = null;
        await flushHistoryDraft();
        const chapter = orderedChapters(state.plan).find(
            (candidate) => candidate.id === chapterId,
        );
        if (!chapter) return;
        state.activeChapterId = chapter.id;
        state.view = "scene";
        persistView();
        renderToolbarState();
        renderTimeline();
        renderPanel();
    }

    function field(label, control) {
        const wrap = element("label", "h3studio-field");
        wrap.append(element("span", "", label), control);
        return wrap;
    }

    function renderReferenceTray(tray, textarea) {
        tray.replaceChildren();
        const referenceData = availableReferenceRecords(
            state.planNode ?? node, state.active + 1, {
                includeInactive: true,
                prompt: [
                    sharedPrompt(state.plan).text.trim(), textarea.value.trim(),
                ].filter(Boolean).join("\n\n"),
            },
        );
        const {wrapper} = referenceData;
        const records = referenceData.mode === "tagged"
            ? referenceData.records
            : referenceData.records.filter((record) => record.active);
        if (!records.length) {
            tray.append(element("span", "h3studio-message", wrapper
                ? `No connected references are active in scene ${state.active + 1}.`
                : "No downstream Tagged/Scheduled Ref2VA, core Ref2VA, or I2V references were found."));
            return;
        }
        function syntaxFor(record) {
            const key = `${String(state.plan?.shots?.[state.active]?.id ?? state.active)}:${record.tag}`;
            const usedMode = referenceData.mode === "tagged" && record.supportsSemantic
                ? taggedPictureReferenceMode(textarea.value, record.tag) : "native";
            return {
                key,
                usedMode,
                syntax:["native", "semantic"].includes(usedMode)
                    ? usedMode : state.referenceSyntax.get(key) ?? "native",
            };
        }
        const preview = element("div", "h3studio-ref-preview");
        function show(record) {
            preview.replaceChildren();
            const kind = record.kind === "picture" ? "image" : record.kind;
            const url = record.previewUrl
                ? api.apiURL(record.previewUrl)
                : findMediaPreview(record.source, kind);
            if (url) {
                const media = element(kind === "image" ? "img" : kind);
                media.src = url;
                if (kind !== "image") { media.controls = true; media.preload = "metadata"; }
                preview.append(media);
            }
            const {syntax} = syntaxFor(record);
            const displayToken = syntax === "semantic"
                ? taggedPictureReferenceToken(record.tag, "semantic")
                : record.token;
            preview.append(element("div", "", `${displayToken}${record.label && record.label !== record.token ? ` → ${record.label}` : ""}\n${record.kind} · ${record.selector === "prompt tag" ? "insert to activate" : `scenes ${record.selector}`}`));
        }
        const icons = {picture:"▧",video:"▶",audio:"♫"};
        for (const record of records) {
            const {key, usedMode, syntax} = syntaxFor(record);
            const displayToken = syntax === "semantic"
                ? taggedPictureReferenceToken(record.tag, "semantic")
                : record.token;
            const entry = element("div", "h3studio-ref-entry");
            const chip = button(`${icons[record.kind] ?? "@"} ${displayToken}`, "Insert this connected reference label or alias", () => {
                const start = textarea.selectionStart ?? textarea.value.length;
                insertText(textarea, displayToken);
                if (syntax === "semantic" && displayToken.includes("[")
                        && displayToken.includes("s]")) {
                    textarea.setSelectionRange(
                        start + displayToken.indexOf("[") + 1,
                        start + displayToken.lastIndexOf("s]"),
                    );
                }
                tray.classList.remove("h3studio-open");
            });
            chip.addEventListener("mouseenter", () => show(record));
            chip.addEventListener("focus", () => show(record));
            entry.append(chip);
            if (referenceData.mode === "tagged" && record.supportsSemantic) {
                const modes = element("div", "h3studio-ref-mode");
                for (const [target, label] of [["native", "@"], ["semantic", "#"]]) {
                    const control = button(
                        label,
                        target === "native"
                            ? "Use native @tag and convert semantic anchors in this scene"
                            : "Use untimed Qwen-only #tag; add [time] for placement",
                        () => {
                            state.referenceSyntax.set(key, target);
                            const next = convertTaggedPictureReference(
                                textarea.value, record.tag, target,
                            );
                            if (next !== textarea.value) {
                                textarea.value = next;
                                textarea.dispatchEvent(new InputEvent("input", {
                                    bubbles:true, inputType:"insertReplacementText",
                                }));
                            }
                            renderReferenceTray(tray, textarea);
                        },
                    );
                    control.classList.toggle(
                        "h3studio-selected",
                        syntax === target || usedMode === "mixed",
                    );
                    modes.append(control);
                }
                entry.append(modes);
            }
            tray.append(entry);
        }
        tray.append(preview); show(records[0]);
    }

    function promptTakeTabs(original, alternate, sceneId, baseRevision) {
        const host = element("div", "h3studio-prompt-takes");
        const tabs = element("div", "h3studio-prompt-take-tabs");
        tabs.setAttribute("role", "tablist");
        tabs.setAttribute("aria-label", "Scene prompt take");
        const prefix = `h3studio-prompt-take-${++promptTakeTabsSerial}`;
        const panels = {original, alt:alternate}, buttons = new Map();
        let active = node.properties[PROMPT_TAKE_TAB_PROPERTY] === "alt" ? "alt" : "original";
        const refresh = () => {
            const draft = state.editorial.alternate_draft;
            const armed = draft?.enabled && draft.scene_id === sceneId && draft.base_revision === baseRevision;
            const used = state.editorial.replacements.some(item => item.scene_id === sceneId
                && item.base_revision === baseRevision && item.alternate_revision);
            for (const [take, tab] of buttons) {
                tab.textContent = take === "original" ? "Original" : `ALT${armed ? " · armed" : used ? " · used in final cut" : ""}`;
                tab.setAttribute("aria-selected", String(take === active));
                tab.tabIndex = take === active ? 0 : -1;
                panels[take].hidden = take !== active;
            }
        };
        const select = take => {
            active = take;
            node.properties[PROMPT_TAKE_TAB_PROPERTY] = take;
            // Only visibility changes: keep both editor DOMs and unsaved text.
            // Never choose a final-cut take, arm a render or write Plan/editorial data.
            refresh();
            node.graph?.setDirtyCanvas?.(true, true);
        };
        for (const [take, panel] of Object.entries(panels)) {
            const tab = button(take === "original" ? "Original" : "ALT",
                take === "original" ? "Edit the generation prompt and its history"
                    : "Edit picture-only alternates and choose the final-cut picture; switching tabs does not enable generation",
                () => select(take));
            tab.id = `${prefix}-${take}-tab`;
            tab.setAttribute("role", "tab");
            tab.setAttribute("aria-controls", `${prefix}-${take}-panel`);
            panel.id = `${prefix}-${take}-panel`;
            panel.setAttribute("role", "tabpanel");
            panel.setAttribute("aria-labelledby", tab.id);
            tab.addEventListener("keydown", event => {
                if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                event.preventDefault();
                const next = event.key === "Home" ? "original" : event.key === "End" ? "alt"
                    : active === "original" ? "alt" : "original";
                select(next); buttons.get(next).focus();
            });
            buttons.set(take, tab); tabs.append(tab);
        }
        // Keep a hidden armed draft visible in the ALT tab label after its checkbox changes.
        alternate.addEventListener("change", refresh);
        host.append(tabs, original, alternate);
        refresh();
        return host;
    }

    function renderScenePanel() {
        const shot = state.plan.shots[state.active];
        const row = timing().shots[state.active];
        const checkpoint = matchingStudioCheckpoint(
            state.checkpoints, state.active, row,
        );
        const activeTrim = trimForScene(state.active);
        const usedFrames = Number(activeTrim?.out_frame)
            || Number(row.deliveredFrames) || 0;
        const timelineLocked = sceneLocked(state.active);
        const panel = element("div");
        const head = element("div", "h3studio-scene-head");
        const editorialStart = studioEditorialSceneStartSeconds(
            timelineModel().segments, state.active,
        );
        head.append(element("strong", "", `Scene ${state.active + 1} of ${state.plan.shots.length}`),
            element("span", "h3studio-scene-label", `${row.rawFrames || "—"} raw · ${usedFrames}/${row.deliveredFrames || "—"} used · ${row.videoBlendFrames}f incoming blend · generation ${formatClock(row.generationStartFrame / 24)} · editorial ${formatClock(editorialStart)}`));
        if (checkpoint?.continuation_stale) head.append(element(
            "span", "h3studio-error",
            `Regenerate this scene: ${checkpoint.continuation_stale_reason}. Its saved media still plays, but it was generated from the previous cut endpoint.`,
        ));
        const grid = h3StudioGridMarkers(
            row.rawFrames, row.contextLength, row.continuationMode,
            row.preservesGeneratedAudioPrefix,
        );
        const gridMarkers = element("span", "h3studio-grid-markers");
        const rawGrid = element(
            "span",
            `h3studio-grid-marker ${grid.raw.onGrid
                ? "h3studio-grid-exact" : "h3studio-grid-warning"}`,
            grid.raw.label,
        );
        rawGrid.title = grid.raw.onGrid
            ? "Raw generation length is on H3's 17n+5 temporal latent grid."
            : "Raw generation length is off H3's 17n+5 temporal latent grid.";
        gridMarkers.append(rawGrid);
        if (grid.av) {
            const avGrid = element(
                "span",
                `h3studio-grid-marker ${grid.av.exact
                    ? "h3studio-grid-exact" : "h3studio-grid-warning"}`,
                grid.av.label,
            );
            avGrid.title = grid.av.exact
                ? (grid.av.audioPreserved
                    ? "The AV context ends on both H3's video latent grid and its 40 Hz audio grid."
                    : "Valid video-only AV context. Generated predecessor audio is not carried, so the 40 Hz audio grid does not constrain this test.")
                : "This AV context is invalid because carried predecessor audio ends between 40 Hz ticks. Exact aligned choices are 39, 90, 141, 192, … frames.";
            gridMarkers.append(avGrid);
        }
        if (grid.cut) {
            const cut = element(
                "span",
                "h3studio-grid-marker h3studio-grid-experimental",
                grid.cut.label,
            );
            cut.title = "Experimental only: nearest reported four-frame 17n−3 cut window for generated-to-real joins. This does not change or validate the Plan.";
            gridMarkers.append(cut);
        }
        head.append(gridMarkers);

        const id = element("input");
        id.value = shot.id ?? "";
        id.addEventListener("change", () => {
            preserveDelegatedPrompts();
            let renamed;
            try {
                renamed = renamePlanShot(state.plan, state.active, id.value);
                id.setCustomValidity("");
            } catch (error) {
                id.value = safeShotId(shot.id, row.id);
                id.setCustomValidity(error?.message || String(error));
                id.reportValidity();
                return;
            }
            const previousId = renamed.previousId;
            shot.id = renamed.id; id.value = renamed.id;
            remapStudioEditorialSceneId(
                state.editorial, previousId, renamed.id,
            );
            writePlan(); renderShell();
        });
        const mode = element("select");
        for (const [value,label] of [["default","Plan default"],["seconds","Seconds"],["frames","Exact frames"]]) {
            const option = element("option", "", label); option.value = value; mode.append(option);
        }
        const length = element("input"); length.type = "number";
        function refreshLength() {
            const selected = shotLengthMode(shot); mode.value = selected;
            mode.disabled = timelineLocked;
            length.disabled = timelineLocked || selected === "default";
            if (selected === "seconds") { length.value = shot.duration_seconds ?? ""; length.min = ".01"; length.max = String(3592 / 24); length.step = ".01"; }
            else if (selected === "frames") { length.value = shot.length ?? shot.frames ?? ""; length.min = "5"; length.max = "3592"; length.step = "17"; }
            else length.value = "";
        }
        mode.addEventListener("change", () => {
            setShotLengthMode(shot, mode.value, settings().defaultDurationSeconds);
            refreshLength(); writePlan(); renderShell();
        });
        length.addEventListener("change", () => {
            if (mode.value === "seconds") shot.duration_seconds = Number(length.value);
            if (mode.value === "frames") { shot.length = Number(length.value); delete shot.frames; }
            writePlan(); renderShell();
        });
        refreshLength();
        const lengthControl = element("span", "h3studio-length h3studio-duration"); lengthControl.append(mode, length);
        const steps = element("input"); steps.type = "number"; steps.min = "1"; steps.max = "10000";
        steps.placeholder = String(planDefaultSteps(state.plan, settings().defaultSteps)); steps.value = shot.steps ?? "";
        steps.title = "An entered value overrides the default for this scene. Clear it to inherit the displayed Plan default.";
        steps.addEventListener("change", () => { if (steps.value) shot.steps = Number(steps.value); else delete shot.steps; writePlan(); renderShell(); });
        const promptSeedMode = element("select");
        for (const [value, label] of [
            ["inherit", "Stable derived"],
            ["fixed", "Fixed scene seed"],
            ["randomize", "Randomize each queue"],
        ]) {
            const option = element("option", "", label);
            option.value = value;
            promptSeedMode.append(option);
        }
        const promptSeed = element("input");
        promptSeed.type = "text";
        promptSeed.inputMode = "numeric";
        promptSeed.placeholder = "Prompt seed";
        const promptSeedWrap = element("span", "h3studio-prompt-seed");
        const rerollPromptSeed = button(
            "↻", "Store a new fixed prompt-alternative seed for this scene",
            () => {
                shot.prompt_seed_mode = "fixed";
                shot.prompt_seed = randomSceneSeed();
                refreshPromptSeed();
                writePlan();
            },
        );
        function refreshPromptSeed() {
            const selected = scenePromptSeedMode(shot);
            promptSeedMode.value = selected;
            promptSeed.disabled = selected !== "fixed";
            rerollPromptSeed.disabled = selected !== "fixed";
            promptSeed.value = selected === "fixed" ? (shot.prompt_seed ?? "") : "";
            promptSeedWrap.title = selected === "inherit"
                ? "Derive a stable seed from this scene's index and ID for its {one|two} choices."
                : selected === "randomize"
                    ? "Choose fresh prompt alternatives whenever this Plan is queued; the exact choice seed is saved with the checkpoint."
                    : "Exact uint64 seed for this scene's prompt alternatives. This does not change sampler noise.";
        }
        promptSeedMode.addEventListener("change", () => {
            setScenePromptSeedMode(shot, promptSeedMode.value);
            refreshPromptSeed();
            writePlan();
            renderStatus();
        });
        promptSeed.addEventListener("change", () => {
            if (promptSeed.value.trim()) shot.prompt_seed = promptSeed.value.trim();
            else shot.prompt_seed = randomSceneSeed();
            setScenePromptSeedMode(shot, "fixed");
            refreshPromptSeed();
            writePlan();
        });
        promptSeedWrap.append(promptSeedMode, promptSeed, rerollPromptSeed);
        refreshPromptSeed();
        const seed = element("input"); seed.type = "text"; seed.inputMode = "numeric"; seed.placeholder = "Stable derived seed"; seed.value = shot.seed ?? "";
        seed.addEventListener("change", () => { if (seed.value.trim()) shot.seed = seed.value.trim(); else delete shot.seed; writePlan(); });
        const seedWrap = element("span", "h3studio-length");
        const reroll = button("↻", "Store a new random seed for this scene", () => { seed.value = randomSceneSeed(); shot.seed = seed.value; writePlan(); });
        seedWrap.append(seed, reroll);
        const loraRoute = element("select");
        const selectedLoRARoute = sceneLoRARoute(shot);
        for (const route of availableLoRARoutes(
            node.graph ?? app.graph,
            [node, state.planNode],
            selectedLoRARoute,
        )) {
            const option = element("option", "", loraRouteLabel(route));
            option.value = route;
            loraRoute.append(option);
        }
        loraRoute.value = selectedLoRARoute;
        loraRoute.title = "Select Base or a connected A-Z MODEL branch on MiniMax H3 Scene LoRA Scheduler. Connecting its last empty route reveals the next one automatically; branches come from ordinary ComfyUI LoRA loaders.";
        loraRoute.addEventListener("change", () => {
            if (loraRoute.value === "base") delete shot.lora_route;
            else shot.lora_route = loraRoute.value;
            sceneLoRARoute(shot);
            writePlan();
            renderTimeline();
        });
        const planSettings = settings();
        function normalizeVisualLeadSpan() {
            if (!Object.hasOwn(shot, "visual_context_lead_source")) return;
            const resolved = sceneContextLength(
                shot, planSettings.contextLength,
            );
            const allowed = visualContextCompositions()
                .filter((choice) => choice.total === resolved)
                .map((choice) => choice.lead);
            if (!allowed.length) {
                delete shot.visual_context_lead_source;
                delete shot.visual_context_lead_frames;
                delete shot.visual_context_lead_start_frame;
                return;
            }
            try {
                sceneVisualContextLeadFrames(shot, resolved);
            } catch (_error) {
                shot.visual_context_lead_frames = allowed[0];
            }
        }
        const incomingTransition = element("select");
        const inheritOption = element(
            "option", "",
            `Inherit Chain Policy · ${transitionPresetLabel(planSettings.transitionPreset)}`,
        );
        inheritOption.value = "inherit";
        incomingTransition.append(inheritOption);
        for (const preset of primaryTransitionOptions()) {
            const option = element(
                "option", "", `${preset.label} · ${preset.description}`,
            );
            option.value = preset.name;
            incomingTransition.append(option);
        }
        function refreshIncomingTransition() {
            const selected = sceneTransitionPreset(
                shot, planSettings.continuationMode,
                planSettings.contextLength,
                planSettings.audioContextLength,
            );
            let custom = incomingTransition.querySelector(
                'option[value="custom"]',
            );
            if (selected === "custom" && !custom) {
                custom = element(
                    "option", "", transitionPresetLabel("custom"),
                );
                custom.value = "custom";
                incomingTransition.append(custom);
            }
            incomingTransition.value = selected;
        }
        refreshIncomingTransition();
        incomingTransition.title = "One semantic boundary choice. Inherit "
            + "uses the connected Chain Policy. A preset writes its tested "
            + "visual implementation/context pair and restores automatic "
            + "generated-audio context. Custom means raw Advanced "
            + "overrides remain below.";
        incomingTransition.addEventListener("change", () => {
            if (incomingTransition.value === "custom") return;
            applySceneTransitionPreset(shot, incomingTransition.value);
            const nextContext = sceneContextLength(
                shot, planSettings.contextLength,
            );
            if (Object.hasOwn(shot, "video_blend_frames")
                    && Number(shot.video_blend_frames) > nextContext) {
                shot.video_blend_frames = nextContext;
            }
            normalizeVisualLeadSpan();
            delete shot.visual_context_start_frame;
            delete shot.visual_context_lead_start_frame;
            if (Array.isArray(shot.visual_context_blocks)) {
                if (nextContext <= 0) {
                    delete shot.visual_context_blocks;
                    writePlan();
                    renderShell();
                    return;
                }
                const sources = shot.visual_context_blocks.map(
                    (block) => String(block?.source ?? ""),
                );
                const count = Math.min(
                    Math.max(1, sources.length),
                    visualContextMaximumBlocks(nextContext),
                );
                const partition = visualContextDefaultPartition(
                    nextContext, count,
                );
                shot.visual_context_blocks = partition.map(
                    (frames, offset) => ({
                        source:sources[offset] ?? sources.at(-1)
                            ?? safeShotId(
                                state.plan.shots[state.active - 1]?.id,
                                `clip_${String(state.active).padStart(4, "0")}`,
                            ),
                        frames,
                    }),
                );
                shot.video_blend_frames = 0;
            }
            writePlan();
            renderShell();
        });
        const blendFrames = element("input");
        blendFrames.type = "number";
        blendFrames.min = "0";
        blendFrames.step = "1";
        blendFrames.value = shot.video_blend_frames ?? "";
        function refreshBlendControl() {
            const resolvedContext = sceneContextLength(
                shot, settings().contextLength,
            );
            blendFrames.max = String(resolvedContext);
            blendFrames.placeholder = String(Math.min(
                Number(settings().videoBlendFrames), resolvedContext,
            ));
        }
        refreshBlendControl();
        blendFrames.title = state.active === 0
            ? "Assembly blend entering scene 1 when Existing Video Context is present. Blank inherits the Plan default, capped to scene context."
            : "Assembly blend from the previous scene into this scene. Blank inherits the Plan default, capped to scene context; zero is a hard cut. It does not change diffusion.";
        blendFrames.addEventListener("change", () => {
            if (blendFrames.value === "") delete shot.video_blend_frames;
            else shot.video_blend_frames = Number(blendFrames.value);
            sceneVideoBlendFrames(
                shot, settings().videoBlendFrames,
                sceneContextLength(shot, settings().contextLength),
            );
            writePlan();
            renderStatus();
        });
        const placement = placementForScene(state.active);
        const editorialScene = timelineModel().segments.find(
            (segment) => segment.kind === "scene"
                && segment.sceneIndex === state.active,
        );
        const sceneStart = element("input");
        sceneStart.type = "text";
        sceneStart.inputMode = "decimal";
        sceneStart.value = placement
            ? formatClock(Number(placement.start_frame) / FPS) : "";
        sceneStart.placeholder = `Auto · ${formatClock(editorialScene?.startSeconds ?? 0)}`;
        sceneStart.title = "Editorial-only position. Enter seconds, M:SS, or H:MM:SS. Scenes are resolved by their requested positions; uncovered time becomes black. This may reorder playback, but never changes generation, checkpoints, or branch lineage.";
        const resetStart = button(
            "Auto", "Use this scene's natural packed Plan position",
            () => setScenePlacement(state.active, null),
        );
        sceneStart.disabled = timelineLocked;
        resetStart.disabled = timelineLocked;
        if (timelineLocked) {
            sceneStart.title = "Scene locked · unlock it from the timeline before moving it.";
        }
        sceneStart.addEventListener("change", () => {
            try {
                const seconds = parseStudioTimecode(sceneStart.value);
                setScenePlacement(
                    state.active,
                    seconds == null ? null : Math.round(seconds * FPS),
                );
            } catch (error) {
                sceneStart.setCustomValidity(error.message);
                sceneStart.reportValidity();
                sceneStart.value = placement
                    ? formatClock(Number(placement.start_frame) / FPS) : "";
            }
        });
        sceneStart.addEventListener(
            "input", () => sceneStart.setCustomValidity(""),
        );
        const sceneStartWrap = element("span", "h3studio-length");
        sceneStartWrap.append(sceneStart, resetStart);
        const usedEnd = element("select");
        for (const frame of studioLatentSafeOutFrames(
            row.rawFrames, row.deliveredFrames,
        )) {
            const option = element(
                "option", "",
                frame === Number(row.deliveredFrames)
                    ? `Full · ${frame}f · ${formatClock(frame / FPS)}`
                    : `${frame}f · ${formatClock(frame / FPS)}`,
            );
            option.value = String(frame);
            usedEnd.append(option);
        }
        usedEnd.value = String(usedFrames);
        usedEnd.disabled = !checkpoint?.ready || timelineLocked;
        usedEnd.title = checkpoint?.ready
            ? "Non-destructive editorial endpoint. Only boundaries shared by the sampled H3 video latent and delivered 40 Hz audio grid are offered. The full checkpoint remains available, while assembly and the next scene use this endpoint."
            : "Generate this scene first; latent-safe endpoint editing uses its saved checkpoint.";
        usedEnd.addEventListener("change", () => {
            setSceneTrim(state.active, Number(usedEnd.value));
        });
        const resetUsedEnd = button(
            "Full", "Use the complete generated checkpoint",
            () => setSceneTrim(state.active, row.deliveredFrames),
        );
        resetUsedEnd.disabled = !checkpoint?.ready || timelineLocked
            || usedFrames === Number(row.deliveredFrames);
        const usedEndWrap = element("span", "h3studio-length");
        usedEndWrap.append(usedEnd, resetUsedEnd);
        const sceneLockControl = button(
            timelineLocked ? "Unlock scene" : "Lock scene",
            timelineLocked
                ? "Allow this scene to be moved, resized, or reordered; release its saved chapter resolution pin"
                : "Protect timeline movement, resizing, and reordering; pin this chapter to the scene's saved resolution",
            () => setSceneLocked(state.active, !timelineLocked),
        );
        const form = element("div", "h3studio-form");
        form.append(
            field("Scene ID", id), field("Length", lengthControl),
            field("Steps override (blank = Plan default)", steps),
            field("Prompt alternatives", promptSeedWrap),
            field("Seed", seedWrap),
            field("LoRA route", loraRoute),
            field("Timeline lock", sceneLockControl),
            field("Editorial start", sceneStartWrap),
            field("Latent-safe used end", usedEndWrap),
            field("Incoming transition", incomingTransition),
            field("Final assembly crossfade frames", blendFrames),
        );
        const planAudioPolicy = settings().audioPolicy;
        const effectiveAudioPolicy = sceneAudioPolicy(shot, planAudioPolicy);
        const lipSync = element("select");
        for (const [value, label] of [
            ["inherit", `Inherit · ${planAudioPolicy.sourceAudioTarget === "locked" ? "On" : "Off"}`],
            ["on", "On · vocals drive this scene"],
            ["off", "Off · action / no source-audio guidance"],
            ["custom", "Custom · advanced audio controls"],
        ]) {
            const option = element("option", "", label);
            option.value = value; option.disabled = value === "custom";
            lipSync.append(option);
        }
        lipSync.value = sceneLipSyncMode(shot);
        lipSync.title = "Scene-local source lip-sync, not a mouth-motion guarantee. "
            + "On uses grouped vocals, or the legacy single source. Off disables "
            + "source locking, source reference, and generated-audio carry. "
            + "Final soundtrack stays unchanged. Inherit resets these three overrides.";
        lipSync.addEventListener("change", () => {
            applySceneLipSync(shot, lipSync.value);
            writePlan();
            renderScenePanel();
            renderStatus();
        });
        form.append(field("Lip-sync", lipSync));
        function audioOverrideSelect(key, inherited, choices, title) {
            const select = element("select");
            const inheritedOption = element(
                "option", "", `Inherit Chain Policy · ${inherited}`,
            );
            inheritedOption.value = "inherit";
            select.append(inheritedOption);
            for (const [value, label] of choices) {
                const option = element("option", "", label);
                option.value = value;
                select.append(option);
            }
            select.value = sceneAudioOverride(shot, key);
            select.title = title;
            select.addEventListener("change", () => {
                applySceneAudioOverride(shot, key, select.value);
                lipSync.value = sceneLipSyncMode(shot);
                writePlan();
                renderStatus();
            });
            return select;
        }
        const sourceReference = audioOverrideSelect(
            "source_reference",
            planAudioPolicy.sourceReference ?? effectiveAudioPolicy.sourceReference,
            [["on", "On · source window as Ref2VA audio"],
             ["off", "Off · no source audio reference"]],
            "Scene-local source-audio reference. It does not choose final "
                + "soundtrack; Lock source audio wins over this switch.",
        );
        const generatedContinuity = audioOverrideSelect(
            "generated_continuity",
            planAudioPolicy.generatedContinuity
                ?? effectiveAudioPolicy.generatedContinuity,
            [["on", "On · continue prior generated audio"],
             ["off", "Off · independent generated audio"]],
            "Scene-local predecessor generated-audio carry. Lock source audio "
                + "wins over this switch.",
        );
        const inheritedLock = (planAudioPolicy.sourceAudioTarget ?? "off")
            === "locked" ? "on" : "off";
        const lockSourceAudio = audioOverrideSelect(
            "source_audio_target", inheritedLock,
            [["locked", "On · protect exact source window"],
             ["off", "Off · target remains denoisable"]],
            "Locks this scene's exact source waveform into the target audio "
                + "latent. Source reference and generated continuity become "
                + "effectively off; final soundtrack stays global.",
        );
        const audioOverrides = element("div", "h3studio-audio-overrides");
        audioOverrides.append(
            field("Source reference", sourceReference),
            field("Generated continuity", generatedContinuity),
            field("Lock source audio", lockSourceAudio),
        );
        function alternateTakePanel() {
            const section = element("section", "h3studio-alternate");
            const title = element("div", "h3studio-alternate-title");
            title.append(
                element("strong", "", "Alternate final-cut take"),
                element("span", "h3studio-grid-marker h3studio-grid-experimental",
                    "Picture only"),
            );
            section.append(title, element(
                "div", "h3studio-hint",
                "Regenerate this scene with a small prompt change without replacing its generation checkpoint. Later scenes keep depending on the original take; preview and final assembly use the accepted alternate picture with the original audio.",
            ));
            if (!checkpoint?.ready) {
                section.append(element(
                    "div", "h3studio-message",
                    "Generate and accept the original scene before creating an alternate.",
                ));
                return section;
            }
            const sceneId = String(row.id);
            const baseRevision = String(checkpoint.revision ?? "");
            const draft = state.editorial.alternate_draft;
            const thisDraft = draft
                && draft.scene_id === sceneId
                && draft.base_revision === baseRevision ? draft : null;
            const selected = state.editorial.replacements.find(
                (item) => item.scene_id === sceneId
                    && item.base_revision === baseRevision,
            ) ?? null;
            const select = element("select");
            const original = element("option", "", `Original · ${baseRevision.slice(0, 8)}`);
            original.value = ""; select.append(original);
            for (const alternate of checkpoint.alternates ?? []) {
                if (!alternate.ready || alternate.base_revision !== baseRevision) continue;
                const option = element(
                    "option", "",
                    `ALT ${String(alternate.revision).slice(0, 8)} · seed ${alternate.seed || "?"}`,
                );
                option.value = String(alternate.revision);
                option.title = String(alternate.prompt ?? "");
                select.append(option);
            }
            select.value = selected?.alternate_revision ?? "";
            select.title = "Choose the picture shown in Plan Studio and final assembly. This never changes the active checkpoint used by following scenes.";
            select.addEventListener("change", () => {
                state.editorial.replacements = state.editorial.replacements.filter(
                    (item) => item.scene_id !== sceneId,
                );
                if (select.value) state.editorial.replacements.push({
                    scene:state.active + 1, scene_id:sceneId,
                    base_revision:baseRevision,
                    alternate_revision:select.value,
                    media_mode:"picture_only",
                });
                // Choosing presentation media is never a generation command.
                // Disarm any draft for this scene and flush the hidden queue
                // widget immediately so a saved ALT cannot retarget Loop Start.
                if (state.editorial.alternate_draft?.scene_id === sceneId) {
                    state.editorial.alternate_draft = null;
                }
                scheduleEditorialSave(0);
                renderShell();
            });
            section.append(field("Used in final cut", select));

            const enabled = element("input"); enabled.type = "checkbox";
            enabled.checked = Boolean(thisDraft?.enabled);
            enabled.title = "When enabled, the next queued execution generates only this scene as an immutable alternate. The original active checkpoint remains untouched.";
            const enabledLabel = element("label", "h3studio-alternate-enable");
            enabledLabel.append(enabled, document.createTextNode(
                " Generate a prompt-word alternate on the next queue",
            ));
            section.append(enabledLabel);
            const editor = element("textarea", "h3studio-prompt h3studio-alternate-prompt");
            const basePrompt = promptValueToText(
                shot.prompt, `Scene ${state.active + 1} prompt`,
            );
            editor.value = thisDraft?.prompt ?? basePrompt;
            editor.disabled = !enabled.checked;
            editor.spellcheck = true;
            editor.placeholder = "Change only the words needed for this alternate…";
            const altSeed = element("input");
            altSeed.type = "text"; altSeed.inputMode = "numeric";
            altSeed.value = String(thisDraft?.seed ?? shot.seed ?? row.seed ?? 0);
            altSeed.disabled = !enabled.checked;
            const diff = element("div", "h3studio-alternate-diff");
            const refreshDiff = () => {
                const before = basePrompt.trim().split(/\s+/);
                const after = editor.value.trim().split(/\s+/);
                let prefix = 0;
                while (prefix < before.length && prefix < after.length
                        && before[prefix] === after[prefix]) prefix += 1;
                let suffix = 0;
                while (suffix < before.length - prefix
                        && suffix < after.length - prefix
                        && before[before.length - 1 - suffix]
                            === after[after.length - 1 - suffix]) suffix += 1;
                const removed = before.slice(prefix, before.length - suffix).join(" ");
                const added = after.slice(prefix, after.length - suffix).join(" ");
                diff.textContent = removed || added
                    ? `Prompt change: “${removed || "∅"}” → “${added || "∅"}”`
                    : "Prompt is unchanged from the original take.";
            };
            const storeDraft = () => {
                if (!enabled.checked) return;
                let seedValue;
                try {
                    const parsed = BigInt(altSeed.value.trim() || "0");
                    if (parsed < 0n || parsed > MAX_SEED) throw new Error();
                    seedValue = parsed.toString();
                } catch (_error) {
                    altSeed.setCustomValidity("Seed must be an unsigned 64-bit integer.");
                    return;
                }
                altSeed.setCustomValidity("");
                state.editorial.alternate_draft = {
                    enabled:true, scene:state.active + 1, scene_id:sceneId,
                    base_revision:baseRevision, prompt:editor.value.trim(),
                    seed:seedValue, media_mode:"picture_only",
                };
                scheduleEditorialSave();
            };
            enabled.addEventListener("change", () => {
                editor.disabled = !enabled.checked;
                altSeed.disabled = !enabled.checked;
                if (enabled.checked) {
                    storeDraft();
                    scheduleEditorialSave(0);
                }
                else if (thisDraft || state.editorial.alternate_draft?.scene_id === sceneId) {
                    state.editorial.alternate_draft = null;
                    scheduleEditorialSave(0);
                }
                refreshDiff();
            });
            editor.addEventListener("input", () => {
                refreshDiff(); storeDraft();
            });
            altSeed.addEventListener("change", storeDraft);
            refreshDiff();
            const draftForm = element("div", "h3studio-alternate-grid");
            draftForm.append(field("Alternate prompt", editor), field("Seed", altSeed));
            section.append(draftForm, diff, element(
                "div", "h3studio-message",
                enabled.checked
                    ? `Ready: queue normally; Loop Start will render only scene ${state.active + 1}. Review acceptance selects it for the final cut.`
                    : "Enable only while you are ready to queue the alternate.",
            ));
            return section;
        }
        const alternate = alternateTakePanel();
        const original = element("div", "h3studio-original-prompt-panel");

        if (state.promptEditors.length) {
            const delegated = element("div", "h3studio-prompt-delegated");
            delegated.append(
                element("strong", "", `Prompt editing delegated to ${promptEditorLabel()}`),
                document.createTextNode(
                    "Use the linked editor for prompt text and revision history. " +
                    "Scene selection is synchronized in both directions; Studio keeps scene ID, length, steps, seed, timeline, and playback controls.",
                ),
            );
            original.append(delegated);
            panel.append(head, form, audioOverrides,
                promptTakeTabs(original, alternate, String(row.id), String(checkpoint?.revision ?? "")));
            return panel;
        }

        const prompt = element("textarea", "h3studio-prompt");
        prompt.value = promptValueToText(shot.prompt, `Scene ${state.active + 1} prompt`);
        prompt.placeholder = "Write this scene's action, camera, performance, dialogue, sound, and ending continuity…";
        prompt.spellcheck = true;
        const message = element("span", "h3studio-message", "Synchronized with Plan");
        prompt.addEventListener("input", () => {
            shot.prompt = promptTextToLines(prompt.value); writePlan(message);
            scheduleHistoryDraft(row.id, prompt.value);
        });
        prompt.addEventListener("keydown", (event) => {
            if (event.altKey && event.key === "ArrowLeft") { event.preventDefault(); void selectScene(state.active - 1); }
            else if (event.altKey && event.key === "ArrowRight") { event.preventDefault(); void selectScene(state.active + 1); }
            else if (!event.ctrlKey && !event.metaKey && !event.altKey && event.key === "#") { event.preventDefault(); insertDialogue(prompt); }
        });
        const tools = element("div", "h3studio-prompt-tools");
        const tray = element("div", "h3studio-refs");
        tools.append(
            button("@ Reference", "Show connected reference tags and previews", () => {
                const opening = !tray.classList.contains("h3studio-open");
                if (opening) renderReferenceTray(tray, prompt); tray.classList.toggle("h3studio-open", opening);
            }),
            button("# Dialogue", "Wrap the selected text in <d> dialogue tags", () => insertDialogue(prompt)),
            element("span", "h3studio-hint", "Alt+←/→ scenes"), message,
        );
        const history = element("div", "h3studio-history");
        state.history.host = history; state.history.textarea = prompt; state.history.status = message;
        original.append(prompt, tools, tray, history);
        panel.append(head, form, audioOverrides,
            promptTakeTabs(original, alternate, String(row.id), String(checkpoint?.revision ?? "")));
        void loadHistory(row.id, prompt.value);
        return panel;
    }

    function renderChapterPanel() {
        const chapter = orderedChapters(state.plan).find(
            (candidate) => candidate.id === state.activeChapterId,
        );
        if (!chapter) {
            state.activeChapterId = "";
            return renderScenePanel();
        }
        const chapterIndex = state.plan.shots.findIndex((shot, offset) => (
            safeShotId(shot?.id, `clip_${String(offset + 1).padStart(4, "0")}`)
                === chapter.start_scene_id
        ));
        const panel = element("div");
        const head = element("div", "h3studio-scene-head");
        head.append(
            element("strong", "", chapter.title),
            element(
                "span", "h3studio-scene-label",
                `starts before scene ${chapterIndex + 1} · chapter settings and notes`,
            ),
        );
        const title = element("input");
        title.value = chapter.title;
        title.maxLength = 160;
        title.addEventListener("input", () => {
            chapter.title = title.value.slice(0, 160) || "Untitled chapter";
            writePlan(); renderTimeline();
        });
        const boundary = element("select");
        state.plan.shots.forEach((shot, index) => {
            const sceneId = safeShotId(
                shot?.id, `clip_${String(index + 1).padStart(4, "0")}`,
            );
            const option = element("option", "", `Before scene ${index + 1} · ${sceneId}`);
            option.value = sceneId;
            boundary.append(option);
        });
        boundary.value = chapter.start_scene_id;
        boundary.addEventListener("change", () => {
            const occupied = (state.plan.chapters ?? []).some(
                (candidate) => candidate !== chapter
                    && candidate.start_scene_id === boundary.value,
            );
            if (occupied) {
                boundary.value = chapter.start_scene_id;
                return;
            }
            chapter.start_scene_id = boundary.value;
            writePlan(); renderTimeline();
        });
        const form = element("div", "h3studio-chapter-settings");
        form.append(field("Chapter title", title), field("Timeline marker", boundary));
        const resolutionMode = element("select");
        for (const [value, text] of [["inherit", "Inherit from Plan"], ["custom", "Chapter resolution"]]) {
            const option = element("option", "", text); option.value = value;
            resolutionMode.append(option);
        }
        resolutionMode.value = chapter.resolution ? "custom" : "inherit";
        const resolutionFields = {};
        const resolutionStatus = element("span", "h3studio-message");
        for (const key of ["width", "height"]) {
            const input = element("input"); input.type = "number";
            input.min = "32"; input.max = "16384"; input.step = "32";
            input.value = String(chapter.resolution?.[key]
                ?? widget(state.planOwner ?? node, key)?.value ?? (key === "width" ? 960 : 544));
            input.disabled = resolutionMode.value === "inherit";
            resolutionFields[key] = input;
            input.addEventListener("change", () => {
                try {
                    chapter.resolution = normalizeChapterResolution({
                        width:Number(resolutionFields.width.value), height:Number(resolutionFields.height.value),
                    });
                    resolutionStatus.textContent = "Chapter resolution saved.";
                    writePlan();
                } catch (error) { resolutionStatus.textContent = error.message; }
            });
        }
        resolutionMode.addEventListener("change", () => {
            if (resolutionMode.value === "inherit") delete chapter.resolution;
            else {
                try {
                    chapter.resolution = normalizeChapterResolution({
                        width:Number(resolutionFields.width.value), height:Number(resolutionFields.height.value),
                    });
                } catch (error) {
                    resolutionStatus.textContent = error.message;
                    resolutionMode.value = "inherit";
                    return;
                }
            }
            for (const input of Object.values(resolutionFields)) input.disabled = resolutionMode.value === "inherit";
            resolutionStatus.textContent = "Chapter resolution saved.";
            writePlan();
        });
        const resolutionRow = element("div", "h3studio-chapter-resolution");
        resolutionRow.append(field("Resolution", resolutionMode), field("Width", resolutionFields.width),
            field("Height", resolutionFields.height));
        form.append(resolutionRow);
        const resolutionHelp = element("div", "h3studio-hint",
            "One size per chapter. Locked saved scenes pin their chapter's original size, even when the Plan default changes. " +
            "Unlock them before changing that chapter's size. Native AV/latent context cannot cross different sizes. " +
            "Current Tagged Ref2VA Scene handles sizing automatically; with separate nodes, connect Current Shot width/height to conditioning. " +
            "Export different-sized chapters separately.");
        const textarea = element("textarea", "h3studio-prompt");
        textarea.value = chapter.text ?? "";
        textarea.placeholder = "Editorial context, lyrics, LLM notes, story intent…";
        textarea.spellcheck = true;
        textarea.addEventListener("input", () => {
            chapter.text = textarea.value;
            writePlan();
        });
        const actions = element("div", "h3studio-prompt-tools");
        actions.append(
            element(
                "span", "h3studio-hint",
                "Chapter notes remain editorial only. Resolution applies to generation and resume validation.",
            ),
            button("Delete chapter", "Remove this editorial marker and its notes", () => {
                if (!confirm(`Delete ${chapter.title}?`)) return;
                state.plan.chapters = (state.plan.chapters ?? []).filter(
                    (candidate) => candidate.id !== chapter.id,
                );
                if (!state.plan.chapters.length) delete state.plan.chapters;
                state.activeChapterId = "";
                writePlan(); renderShell();
            }),
        );
        panel.append(head, form, resolutionHelp, resolutionStatus, textarea, actions);
        return panel;
    }

    function renderSharedPanel() {
        const panel = element("div");
        panel.append(element("div", "h3studio-scene-head", "Shared prompt — prepended to every scene"));
        const textarea = element("textarea", "h3studio-shared");
        textarea.value = sharedPrompt(state.plan).text;
        textarea.placeholder = "Identity, wardrobe, style, reference definitions, audio rules, and global continuity…";
        textarea.addEventListener("input", () => { setSharedPrompt(state.plan, textarea.value); writePlan(); });
        panel.append(textarea);
        return panel;
    }

    function renderPlanSettingsPanel() {
        const panel = element("div");
        const grid = element("div", "h3studio-plan-settings");
        const owner = state.planOwner ?? node;
        const modernPlan = owner?.type === MODERN_PLAN_NAME;
        const transition = resolveTransitionPolicy(owner);
        const audioPolicy = resolveAudioPolicy(owner);
        const projectAssetsManaged = inputConnected(owner, "project_assets");
        const value = (name, fallback = "") => name === "default_steps"
            ? planDefaultSteps(state.plan, widget(owner, name)?.value ?? fallback)
            : widget(owner, name)?.value ?? fallback;
        const section = (title) => element(
            "div", "h3studio-plan-settings-section", title,
        );
        const textControl = (name, fallback = "", placeholder = "") => {
            const control = element("input");
            control.type = "text";
            control.value = String(value(name, fallback));
            control.placeholder = placeholder;
            control.addEventListener("change", () => {
                writePlanSetting(name, control.value.trim());
            });
            return control;
        };
        const numberControl = (
            name, fallback, minimum, maximum, step = 1, integer = true,
        ) => {
            const control = element("input");
            control.type = "number";
            control.min = String(minimum); control.max = String(maximum);
            control.step = String(step); control.value = String(value(name, fallback));
            control.addEventListener("change", () => {
                let parsed = Number(control.value);
                if (!Number.isFinite(parsed)) parsed = Number(fallback);
                parsed = Math.max(Number(minimum), Math.min(Number(maximum), parsed));
                if (integer) parsed = Math.trunc(parsed);
                writePlanSetting(name, parsed);
            });
            return control;
        };
        const selectControl = (name, options, fallback, transform = (item) => item) => {
            const control = element("select");
            for (const [optionValue, label] of options) {
                const option = element("option", "", label);
                option.value = optionValue; control.append(option);
            }
            control.value = String(value(name, fallback));
            control.addEventListener("change", () => {
                writePlanSetting(name, transform(control.value));
            });
            return control;
        };
        const baseSeed = element("input");
        baseSeed.type = "text"; baseSeed.inputMode = "numeric";
        baseSeed.value = String(value("base_seed", 0));
        baseSeed.title = `Unsigned 64-bit seed (0–${MAX_SEED.toString()})`;
        baseSeed.addEventListener("change", () => {
            try {
                const parsed = BigInt(baseSeed.value.trim() || "0");
                if (parsed < 0n || parsed > MAX_SEED) throw new Error();
                writePlanSetting("base_seed", parsed.toString());
            } catch (_error) {
                baseSeed.setCustomValidity("Base seed must be an unsigned 64-bit integer.");
                baseSeed.reportValidity();
                baseSeed.setCustomValidity("");
            }
        });

        const mode = state.planNode
            ? `Connected mode · changes are written to the ${modernPlan ? "Modern Plan" : "H3 Chain Plan"} and mirrored into Studio. Disconnecting keeps this synchronized snapshot.`
            : "Standalone mode · this node owns, validates, and outputs the complete H3 Chain Plan.";
        const identityFields = projectAssetsManaged ? [
            element(
                "div", "h3studio-plan-defaults-help",
                "Run name and reference-derived generation fingerprint are managed by connected Project Assets. Their stored widget values are preserved; disconnect Project Assets to edit them.",
            ),
        ] : [
            field("Run name", textControl("run_name", "h3_chain", "h3_chain")),
            field("Generation fingerprint", textControl(
                "generation_fingerprint", "", "optional compatibility tag",
            )),
        ];
        grid.append(
            element("div", "h3studio-plan-defaults-help", mode),
            section("Run identity and canvas"),
            ...identityFields,
            field("Base seed", baseSeed),
            field("Width", numberControl("width", 960, 32, 4096, 32)),
            field("Height", numberControl("height", 544, 32, 4096, 32)),
            field("Segment CRF", numberControl("segment_crf", 18, 0, 51)),
            section("Plan-wide scene defaults"),
            field("Default seconds", numberControl(
                "default_duration_seconds", 15, .1, MAX_H3_FRAMES / FPS, .01, false,
            )),
            field("Default steps", numberControl("default_steps", 20, 1, 10000)),
            field("Default blend frames", numberControl(
                "video_blend_frames", 0, 0, 243,
            )),
            field("Context encoding", selectControl("encode_mode", [
                ["video", "Video clip"], ["frames", "Separate frames"],
            ], "video")),
            field("Context fit", selectControl("crop", [
                ["disabled", "Resize directly"], ["center", "Preserve aspect + center crop"],
            ], "disabled")),
        );
        const overrides = state.plan.shots.filter((shot) => shot.steps != null).length;
        if (overrides) grid.append(
            element("div", "h3studio-plan-defaults-help",
                `${overrides} scene(s) override the default steps.`),
            button("Use default steps for all scenes",
                "Clear only per-scene step overrides. Prompts, seeds, and all other settings stay unchanged.",
                () => { clearSceneStepOverrides(state.plan); writePlan(); renderShell(); }),
        );
        if (modernPlan) {
            grid.append(
                section("Generation Profile"),
                element(
                    "div", "h3studio-plan-defaults-help",
                    transition.known || audioPolicy.known
                        ? "Visual transition, context length, audio behavior, and continuation are owned by the connected Generation Profile. Per-scene Context controls remain available for deliberate overrides."
                        : "Connect a Generation Profile to the Modern Plan. It owns visual transition, context length, audio behavior, and continuation.",
                ),
            );
            panel.append(grid);
            return panel;
        }
        grid.append(
            field("Anchor placement", selectControl("anchor_mode", [
                ["head", "Head (tested)"], ["before", "Before timeline (experimental)"],
            ], "head")),
            section("Legacy policy fallback"),
        );
        const context = selectControl(
            "context_length", H3_CONTEXT_LENGTHS.map((item) => [String(item), `${item} frames`]), "22",
            (item) => Number(item),
        );
        const continuation = selectControl(
            "continuation_mode", CONTINUATION_MODES.map((item) => [item, item]), "guide",
        );
        const audioMode = selectControl("audio_mode", [
            ["generated_audio", "Generated audio"],
            ["source_track", "Source track"],
            ["source_plus_timeline", "Source + generated continuity"],
        ], "generated_audio");
        const audioContext = numberControl("audio_context_length", 22, 0, 240);
        context.disabled = transition.known;
        continuation.disabled = transition.known;
        audioMode.disabled = audioPolicy.known;
        audioContext.disabled = audioPolicy.known;
        grid.append(
            field("Visual context", context),
            field("Continuation implementation", continuation),
            field("Audio mode", audioMode),
            field("Audio context", audioContext),
            element("div", "h3studio-plan-defaults-help",
                transition.known || audioPolicy.known
                    ? "Connected Chain Policy owns the disabled fallback controls. The active policy is used for timing and execution."
                    : "These controls are used only when no Chain Policy is connected. Per-scene Context Planner settings can still override them."),
        );
        panel.append(grid);
        return panel;
    }

    function playerCheckpoint(index) {
        const item = matchingStudioCheckpoint(
            state.checkpoints, index, timing().shots[index],
        );
        if (!item) return null;
        return {
            video:item.presentation_video ?? item.preview_video ?? item.video,
            // Review previews already contain synchronized audio. Raw saved
            // segments do not, so pair those with their delivered WAV.
            // Picture-only alternates deliberately keep the original take's
            // generated audio sidecar.
            audio:item.presentation_video
                ? (item.audio ?? null)
                : item.preview_video ? null : (item.audio ?? null),
        };
    }

    function renderAudioContextPanel(panel, result, row, shot) {
        if (!row.preservesGeneratedAudioPrefix || !Number(row.audioContextLength)) {
            panel.append(element(
                "div", "h3studio-context-empty",
                "This scene does not carry generated audio. Enable Generated continuity and a positive audio context before selecting extracts.",
            ));
            return panel;
        }
        const sourceSelect = (field, {lead = false} = {}) => {
            const select = element("select");
            if (lead) {
                const off = element("option", "", "Off · one audio extract");
                off.value = "";
                select.append(off);
            } else {
                const previous = element(
                    "option", "",
                    `Previous scene · ${state.active} ${safeShotId(
                        state.plan.shots[state.active - 1]?.id,
                        `clip_${String(state.active).padStart(4, "0")}`,
                    )}`,
                );
                previous.value = "";
                select.append(previous);
            }
            for (let offset = 0; offset < state.active; offset += 1) {
                if (!lead && offset === state.active - 1) continue;
                const sourceId = safeShotId(
                    state.plan.shots[offset]?.id,
                    `clip_${String(offset + 1).padStart(4, "0")}`,
                );
                const option = element(
                    "option", "", `Scene ${offset + 1} · ${sourceId}`,
                );
                option.value = sourceId;
                select.append(option);
            }
            const resolver = lead
                ? sceneAudioContextLeadSource : sceneAudioContextSource;
            try {
                const resolved = resolver(state.plan, state.active + 1);
                select.value = resolved === null || (
                    !lead && resolved === state.active
                ) ? "" : safeShotId(
                    state.plan.shots[resolved - 1]?.id,
                    `clip_${String(resolved).padStart(4, "0")}`,
                );
            } catch (_error) {
                select.value = "";
            }
            select.addEventListener("change", () => {
                if (select.value) shot[field] = select.value;
                else delete shot[field];
                delete shot[lead
                    ? "audio_context_lead_start_frame"
                    : "audio_context_start_frame"];
                if (lead) {
                    if (!select.value) {
                        delete shot.audio_context_lead_frames;
                    } else if (!Object.hasOwn(
                        shot, "audio_context_lead_frames",
                    )) {
                        const options = audioContextLeadFrameOptions(
                            row.audioContextLength,
                        );
                        shot.audio_context_lead_frames = options.includes(5)
                            ? 5 : options[Math.floor(options.length / 2)];
                    }
                }
                writePlan();
                renderShell();
            });
            return select;
        };
        const audioSource = sourceSelect("audio_context_source");
        audioSource.title = "The extract nearest the new generation boundary. It may come from any earlier scene.";
        const audioLeadSource = sourceSelect(
            "audio_context_lead_source", {lead:true},
        );
        audioLeadSource.title = "Optional first extract. It may come from another character's scene or a second position in the same scene.";
        const split = element("select");
        const splitOptions = audioContextLeadFrameOptions(
            row.audioContextLength,
        );
        for (const lead of splitOptions) {
            const option = element(
                "option", "",
                `${row.audioContextLength} total · ${lead} + ${row.audioContextLength - lead}`,
            );
            option.value = String(lead);
            split.append(option);
        }
        split.disabled = !audioLeadSource.value;
        split.value = String(row.audioContextLeadFrames || (
            splitOptions.includes(5) ? 5 : splitOptions.at(0)
        ));
        split.title = "Ordered duration of the two exact 40 Hz audio-latent extracts. These are sequential context excerpts, not a live audio mix.";
        split.addEventListener("change", () => {
            shot.audio_context_lead_frames = Number(split.value);
            delete shot.audio_context_start_frame;
            delete shot.audio_context_lead_start_frame;
            writePlan();
            renderShell();
        });
        const settingsGrid = element(
            "div", "h3studio-context-audio-settings",
        );
        settingsGrid.append(
            field("Boundary-nearest audio source", audioSource),
            field("Optional first audio source", audioLeadSource),
            field("Dual extract split", split),
        );
        panel.append(
            element(
                "div", "h3studio-context-help",
                "Audio is unlocked from picture for this scene. Choose one exact latent extract, or two ordered extracts—for example two character voice regions. The second block sits nearest generation. No waveform is decoded or re-encoded.",
            ),
            settingsGrid,
        );

        const blocks = [];
        if (row.audioContextLeadSource !== null) {
            blocks.push({
                label:"Audio block 1 · first extract",
                sourceIndex:row.audioContextLeadSource - 1,
                span:Number(row.audioContextLeadFrames),
                field:"audio_context_lead_start_frame",
                lead:true,
            });
        }
        blocks.push({
            label:row.audioContextLeadSource === null
                ? "Audio context extract" : "Audio block 2 · nearest generation",
            sourceIndex:row.audioContextSource - 1,
            span:Number(row.audioContextLength - row.audioContextLeadFrames),
            field:"audio_context_start_frame",
            lead:false,
        });
        const blocksHost = element("div", "h3studio-context-blocks");
        for (const block of blocks) {
            const sourceRow = result.shots[block.sourceIndex];
            const card = element("div", "h3studio-context-block");
            const head = element("div", "h3studio-context-block-head");
            head.append(
                element("strong", "", `${block.label} · ${block.span}f`),
                element(
                    "span", "", sourceRow
                        ? `Scene ${sourceRow.index} · ${sourceRow.id}`
                        : "Missing source scene",
                ),
            );
            card.append(head);
            if (!sourceRow || block.span < 1) {
                card.append(element(
                    "div", "h3studio-context-empty",
                    "The selected source or audio split is invalid.",
                ));
                blocksHost.append(card);
                continue;
            }
            const validStarts = audioContextWindowStarts(
                sourceRow.rawFrames, sourceRow.deliveredFrames, block.span,
            );
            if (!validStarts.length) {
                card.append(element(
                    "div", "h3studio-context-empty",
                    "This scene has no position with the exact requested 40 Hz latent duration.",
                ));
                blocksHost.append(card);
                continue;
            }
            const defaultStart = validStarts.at(-1);
            let selectedStart = defaultStart;
            let rangeError = "";
            try {
                selectedStart = sceneAudioContextStartFrame(
                    shot, sourceRow.rawFrames, sourceRow.deliveredFrames,
                    block.span, block.lead,
                );
            } catch (error) {
                rangeError = error?.message || String(error);
                selectedStart = nearestNativeContextWindowStart(
                    validStarts, Number(shot[block.field]),
                );
            }
            const media = playerCheckpoint(block.sourceIndex);
            let audio = null;
            const audioPath = media?.audio ?? media?.video;
            if (audioPath) {
                audio = element("audio", "h3studio-context-audio");
                audio.controls = true;
                audio.preload = "metadata";
                audio.src = videoUrl(audioPath);
                state.contextPlayers.push(audio);
                card.append(audio);
            }
            const track = element("div", "h3studio-context-movie-track");
            track.tabIndex = 0;
            track.setAttribute("role", "slider");
            track.setAttribute("aria-label", `${block.label} position`);
            const zone = element(
                "div", "h3studio-context-window", `${block.span}f`,
            );
            const playhead = element("div", "h3studio-context-playhead");
            track.append(zone, playhead);
            const rangeLabel = element("span", "h3studio-context-range-label");
            const movieLength = element(
                "span", "h3studio-context-movie-length",
                `source audio · ${sourceRow.deliveredFrames}f · ${(sourceRow.deliveredFrames / FPS).toFixed(3)}s`,
            );
            const update = () => {
                const layout = studioContextWindowLayout(
                    sourceRow.deliveredFrames, block.span, selectedStart,
                );
                selectedStart = nearestNativeContextWindowStart(
                    validStarts, layout.start,
                );
                const exact = studioContextWindowLayout(
                    sourceRow.deliveredFrames, block.span, selectedStart,
                );
                zone.style.left = `${exact.leftFraction * 100}%`;
                zone.style.width = `${exact.widthFraction * 100}%`;
                rangeLabel.textContent = `frames ${exact.start + 1}–${exact.end} · ${(exact.start / FPS).toFixed(3)}–${(exact.end / FPS).toFixed(3)}s`;
                track.setAttribute("aria-valuenow", String(exact.start));
            };
            const seek = () => {
                if (!audio || audio.readyState < 1) return;
                try { audio.currentTime = selectedStart / FPS; }
                catch (_error) {}
            };
            const commit = () => {
                if (selectedStart === defaultStart) delete shot[block.field];
                else shot[block.field] = selectedStart;
                writePlan();
                renderStatus();
            };
            const choose = (value, commitNow = false) => {
                selectedStart = nearestNativeContextWindowStart(
                    validStarts, value,
                );
                update();
                seek();
                if (commitNow) commit();
            };
            let drag = null;
            track.addEventListener("pointerdown", (event) => {
                if (event.button !== 0) return;
                event.preventDefault();
                const bounds = track.getBoundingClientRect();
                choose(studioContextWindowStartAtRatio(
                    sourceRow.deliveredFrames, block.span,
                    (event.clientX - bounds.left) / Math.max(1, bounds.width),
                ));
                drag = {id:event.pointerId, x:event.clientX,
                    start:selectedStart, width:Math.max(1, bounds.width)};
                track.setPointerCapture(event.pointerId);
            });
            track.addEventListener("pointermove", (event) => {
                if (!drag || drag.id !== event.pointerId) return;
                choose(drag.start + (event.clientX - drag.x) / drag.width
                    * sourceRow.deliveredFrames);
            });
            const finish = (event) => {
                if (!drag || drag.id !== event.pointerId) return;
                drag = null;
                try { track.releasePointerCapture(event.pointerId); }
                catch (_error) {}
                commit();
            };
            track.addEventListener("pointerup", finish);
            track.addEventListener("pointercancel", finish);
            track.addEventListener("keydown", (event) => {
                const slot = Math.max(0, validStarts.indexOf(selectedStart));
                let next = null;
                if (event.key === "ArrowLeft") next = validStarts[Math.max(0, slot - 1)];
                else if (event.key === "ArrowRight") next = validStarts[Math.min(validStarts.length - 1, slot + 1)];
                else if (event.key === "Home") next = validStarts[0];
                else if (event.key === "End") next = defaultStart;
                if (next === null) return;
                event.preventDefault();
                choose(next, true);
            });
            const readout = element("div", "h3studio-context-range-readout");
            readout.append(movieLength, rangeLabel);
            const range = element("div", "h3studio-context-range");
            range.append(track, readout);
            card.append(range);
            update();
            if (audio) {
                audio.addEventListener("loadedmetadata", seek, {once:true});
                audio.addEventListener("timeupdate", () => {
                    playhead.style.left = `${Math.max(0, Math.min(
                        1, audio.currentTime * FPS / sourceRow.deliveredFrames,
                    )) * 100}%`;
                    const end = Number(audio.dataset.contextEnd);
                    if (Number.isFinite(end) && audio.currentTime >= end) {
                        delete audio.dataset.contextEnd;
                        audio.pause();
                    }
                });
            }
            const actions = element("div", "h3studio-context-actions");
            const play = button("Play extract", "Play only this audio extract", async () => {
                if (!audio) return;
                audio.dataset.contextEnd = String((selectedStart + block.span) / FPS);
                audio.currentTime = selectedStart / FPS;
                try { await audio.play(); } catch (_error) {}
            });
            play.disabled = !audio;
            const usePlayhead = button("Start at playhead", "Place extract at the audio player's current time", () => {
                if (audio) choose(audio.currentTime * FPS, true);
            });
            usePlayhead.disabled = !audio;
            actions.append(usePlayhead, play, button(
                "Latest exact (default)",
                "Use the last exact 40 Hz crop and remove the override",
                () => choose(defaultStart, true),
            ));
            const error = element("div", "h3studio-error", rangeError);
            error.hidden = !rangeError;
            card.append(actions, error);
            blocksHost.append(card);
        }
        panel.append(blocksHost);
        return panel;
    }

    function renderContextPanel() {
        const panel = element("div", "h3studio-context-selector");
        const result = timing();
        const row = result.shots[state.active];
        const shot = state.plan.shots[state.active];
        const title = element(
            "div", "h3studio-scene-head",
            `Scene ${state.active + 1} context planner`,
        );
        panel.append(title);
        if (!row || state.active === 0) {
            panel.append(element(
                "div", "h3studio-context-empty",
                "Scene 1 has no saved predecessor. Existing Video Context remains configured by its dedicated workflow input.",
            ));
            return panel;
        }
        let audioUnlocked = false;
        try { audioUnlocked = sceneAudioContextUnlocked(shot); }
        catch (_error) {}
        if (!audioUnlocked) state.contextTab = "picture";
        const tabs = element("div", "h3studio-context-tabs");
        const pictureTab = button(
            "Picture", "Select picture context sources and latent windows",
            () => { state.contextTab = "picture"; renderShell(); },
        );
        const audioTab = button(
            "Audio", audioUnlocked
                ? "Select independent audio context sources and latent windows"
                : "Unlock audio context to choose sources independently",
            () => { state.contextTab = "audio"; renderShell(); },
        );
        audioTab.disabled = !audioUnlocked;
        (state.contextTab === "audio" ? audioTab : pictureTab).classList.add(
            "h3studio-context-tab-active",
        );
        const lock = button(
            audioUnlocked ? "Lock audio context" : "Unlock audio context",
            audioUnlocked
                ? "Restore the default behavior: picture may be single or dual while generated audio remains the immediate predecessor tail"
                : "Expose an Audio tab that can choose one or two saved-scene audio extracts independently from picture",
            () => {
                if (audioUnlocked) {
                    delete shot.audio_context_unlocked;
                    delete shot.audio_context_source;
                    delete shot.audio_context_start_frame;
                    delete shot.audio_context_lead_source;
                    delete shot.audio_context_lead_frames;
                    delete shot.audio_context_lead_start_frame;
                    state.contextTab = "picture";
                } else {
                    shot.audio_context_unlocked = true;
                    state.contextTab = "audio";
                }
                writePlan();
                renderShell();
            },
        );
        lock.classList.add("h3studio-context-lock");
        lock.disabled = !audioUnlocked && !row.preservesGeneratedAudioPrefix;
        tabs.append(pictureTab, audioTab, lock);
        panel.append(tabs);
        if (state.contextTab === "audio" && audioUnlocked) {
            return renderAudioContextPanel(panel, result, row, shot);
        }
        const legacyVisualFields = [
            "visual_context_source", "visual_context_start_frame",
            "visual_context_lead_source", "visual_context_lead_frames",
            "visual_context_lead_start_frame",
        ];
        const clearLegacyVisualFields = () => {
            for (const key of legacyVisualFields) delete shot[key];
        };
        const sourceId = (source) => safeShotId(
            state.plan.shots[source - 1]?.id,
            `clip_${String(source).padStart(4, "0")}`,
        );
        const currentVisualBlocks = () => {
            try {
                return sceneVisualContextBlocks(
                    state.plan, state.active + 1,
                    sceneContextLength(shot, settings().contextLength),
                );
            } catch (_error) {
                return row.visualContextBlocks ?? [];
            }
        };
        const writeVisualBuilder = (
            partition, {preserveStarts = false} = {},
        ) => {
            const previous = currentVisualBlocks();
            shot.visual_context_blocks = partition.map((frames, offset) => {
                const prior = previous[offset] ?? previous.at(-1);
                const source = Number(prior?.source) || state.active;
                const block = {source:sourceId(source), frames:Number(frames)};
                if (preserveStarts && Number(prior?.frames) === Number(frames)
                        && Number.isInteger(prior?.startFrame)) {
                    block.start_frame = Number(prior.startFrame);
                }
                return block;
            });
            clearLegacyVisualFields();
            shot.video_blend_frames = 0;
        };
        const visualTotal = element("select");
        for (const [value, label] of [
            ["", `Plan default · ${settings().contextLength}`],
            ["0", "0 · new visual scene"],
            ...H3_CONTEXT_LENGTHS.map((value) => [
                String(value), `${value} picture frames`,
            ]),
        ]) {
            const option = element("option", "", label);
            option.value = value;
            visualTotal.append(option);
        }
        visualTotal.value = Object.hasOwn(shot, "context_length")
            ? String(shot.context_length) : "";
        visualTotal.title = "Total picture prefix entering this scene. The builder divides this exact H3-safe total into ordered source blocks.";
        visualTotal.addEventListener("change", () => {
            if (visualTotal.value === "") delete shot.context_length;
            else shot.context_length = Number(visualTotal.value);
            const total = sceneContextLength(shot, settings().contextLength);
            if (total <= 0) {
                delete shot.visual_context_blocks;
                clearLegacyVisualFields();
            } else {
                const count = Math.min(
                    Math.max(1, currentVisualBlocks().length || 1),
                    visualContextMaximumBlocks(total),
                );
                writeVisualBuilder(
                    visualContextDefaultPartition(total, count),
                );
            }
            writePlan();
            renderShell();
        });
        const audioTotal = element("input");
        audioTotal.type = "number";
        audioTotal.min = "0";
        audioTotal.max = "240";
        audioTotal.step = "1";
        audioTotal.value = shot.audio_context_length ?? "";
        audioTotal.placeholder = Number(settings().audioContextLength)
            ? String(settings().audioContextLength)
            : `Picture ${row.contextLength}`;
        audioTotal.title = "Generated-audio context length. Audio remains locked to its normal continuous source unless you explicitly unlock the Audio tab.";
        audioTotal.addEventListener("change", () => {
            if (audioTotal.value === "") delete shot.audio_context_length;
            else shot.audio_context_length = Number(audioTotal.value);
            writePlan();
            renderShell();
        });
        const implementation = element("select");
        const inheritImplementation = element(
            "option", "", `Inherit · ${settings().continuationMode}`,
        );
        inheritImplementation.value = "";
        implementation.append(inheritImplementation);
        for (const [value, label] of [
            ["guide", "Guide"], ["latent_guide", "Latent Guide"],
            ["tapered_guide", "Detail Guide"],
            ["tone_carry_guide", "Tone Carry Guide"],
            ["tapered_av", "Detail AV"],
            ["drift_control_av", "Drift-Control AV"],
            ["color_stable_drift_av", "Color-Stable Drift AV"],
            ["masked_av", "Masked AV"],
            ["feathered_av", "Feathered AV"],
            ["audio_feathered_av", "Audio Feather AV"],
        ]) {
            const option = element("option", "", label);
            option.value = value;
            implementation.append(option);
        }
        implementation.value = shot.continuation_mode ?? "";
        implementation.title = "Boundary implementation used to consume the complete ordered picture prefix.";
        implementation.addEventListener("change", () => {
            if (implementation.value) {
                shot.continuation_mode = implementation.value;
            } else delete shot.continuation_mode;
            writePlan();
            renderShell();
        });
        const spatialProxyControl = element("select");
        for (const [value, label] of [
            ["", "Off · native context"],
            ["rgb_5_6", "Low-grid 5/6 proxy · Guide"],
            ["latent_5_6", "Latent 5/6 proxy · AV"],
        ]) {
            const option = element("option", "", label);
            option.value = value;
            spatialProxyControl.append(option);
        }
        spatialProxyControl.value = shot.context_spatial_proxy ?? "";
        spatialProxyControl.title = "Optional boundary-only 5/6 spatial proxy. Output, audio, checkpoints, and assembly stay at native size.";
        spatialProxyControl.addEventListener("change", () => {
            if (spatialProxyControl.value) {
                shot.context_spatial_proxy = spatialProxyControl.value;
            } else delete shot.context_spatial_proxy;
            writePlan();
            renderShell();
        });
        const contextSettings = element(
            "div", "h3studio-context-builder-settings",
        );
        contextSettings.append(
            field("Picture context total", visualTotal),
            field("Audio context total", audioTotal),
            field("Boundary implementation", implementation),
            field("Boundary spatial proxy", spatialProxyControl),
        );
        panel.append(contextSettings);
        if (!Number(row.contextLength)) {
            panel.append(element(
                "div", "h3studio-context-empty",
                "This scene has 0 visual context. Choose a positive picture context total above to enable the builder.",
            ));
            return panel;
        }
        const resolvedBlocks = row.visualContextBlocks ?? [];
        const blockCount = element("select");
        const maximumBlocks = visualContextMaximumBlocks(row.contextLength);
        for (let count = 1; count <= maximumBlocks; count += 1) {
            const option = element(
                "option", "",
                `${count} ${count === 1 ? "block" : "blocks"}`,
            );
            option.value = String(count);
            blockCount.append(option);
        }
        blockCount.value = String(Math.max(1, resolvedBlocks.length));
        blockCount.title = "Choose how many ordered picture extracts form the prefix. Every block may use any earlier scene, including another window from the same scene.";
        blockCount.addEventListener("change", () => {
            writeVisualBuilder(visualContextDefaultPartition(
                row.contextLength, Number(blockCount.value),
            ));
            writePlan();
            renderShell();
        });
        const partition = resolvedBlocks.map((block) => Number(block.frames));
        const cuts = [];
        let cumulative = 0;
        for (const span of partition.slice(0, -1)) {
            cumulative += span;
            cuts.push(cumulative);
        }
        const validBoundaries = visualContextBoundaryFrames(row.contextLength);
        const cutsHost = element("div", "h3studio-context-cuts");
        for (let cutOffset = 0; cutOffset < cuts.length; cutOffset += 1) {
            const select = element("select");
            const lower = cutOffset === 0 ? -1 : cuts[cutOffset - 1];
            const upper = cutOffset === cuts.length - 1
                ? row.contextLength + 1 : cuts[cutOffset + 1];
            for (const boundary of validBoundaries) {
                if (boundary <= lower || boundary >= upper) continue;
                const option = element(
                    "option", "", `after frame ${boundary}`,
                );
                option.value = String(boundary);
                select.append(option);
            }
            select.value = String(cuts[cutOffset]);
            select.title = "Move this division to another native H3 cumulative boundary. Together the cuts expose every valid repartition for the selected block count.";
            select.addEventListener("change", () => {
                const nextCuts = [...cuts];
                nextCuts[cutOffset] = Number(select.value);
                writeVisualBuilder(visualContextPartitionFromBoundaries(
                    row.contextLength, nextCuts,
                ));
                writePlan();
                renderShell();
            });
            cutsHost.append(field(`Division ${cutOffset + 1}`, select));
        }
        const partitionSummary = element(
            "div", "h3studio-context-help",
            `Ordered repartition: ${partition.join(" + ")} = ${row.contextLength} frames. The last block sits nearest the new generation boundary.`,
        );
        const builderSettings = element(
            "div", "h3studio-context-builder-settings",
        );
        builderSettings.append(field("Picture blocks", blockCount));
        if (cuts.length) builderSettings.append(cutsHost);
        panel.append(builderSettings, partitionSummary);
        panel.append(element(
            "div", "h3studio-context-help",
            "Drag the fixed-width zone between native H3 latent positions. The selector advances on the 17-frame / 5-latent-step lattice and crops saved latent steps directly; it never re-encodes an arbitrary RGB window. Latest aligned is the default. While audio is locked, generated-audio continuity still follows the immediate previous scene.",
        ));
        const blocksHost = element("div", "h3studio-context-blocks");
        let prefixFrames = 0;
        const blocks = resolvedBlocks.map((resolved, blockIndex) => {
            const block = {
                label:resolvedBlocks.length === 1
                    ? "Picture context"
                    : blockIndex === resolvedBlocks.length - 1
                        ? `Block ${blockIndex + 1} · nearest generation`
                        : `Block ${blockIndex + 1}`,
                blockIndex,
                sourceIndex:Number(resolved.source) - 1,
                span:Number(resolved.frames),
                prefixFrames,
                startFrame:resolved.startFrame,
            };
            prefixFrames += block.span;
            return block;
        });

        for (const block of blocks) {
            const sourceRow = result.shots[block.sourceIndex];
            const card = element("div", "h3studio-context-block");
            const head = element("div", "h3studio-context-block-head");
            head.append(
                element("strong", "", `${block.label} · ${block.span}f`),
                element(
                    "span", "",
                    sourceRow
                        ? `Scene ${sourceRow.index} · ${sourceRow.id}`
                        : "Missing source scene",
                ),
            );
            card.append(head);
            const sourceSelect = element("select");
            for (let sourceOffset = 0; sourceOffset < state.active;
                sourceOffset += 1) {
                const id = sourceId(sourceOffset + 1);
                const option = element(
                    "option", "", `Scene ${sourceOffset + 1} · ${id}`,
                );
                option.value = id;
                sourceSelect.append(option);
            }
            sourceSelect.value = sourceRow
                ? sourceId(sourceRow.index) : "";
            sourceSelect.title = "Choose any earlier scene. Multiple blocks may select the same scene and use independent latent windows.";
            sourceSelect.addEventListener("change", () => {
                if (!Array.isArray(shot.visual_context_blocks)) {
                    writeVisualBuilder(partition, {preserveStarts:true});
                }
                const authored = shot.visual_context_blocks[block.blockIndex];
                authored.source = sourceSelect.value;
                delete authored.start_frame;
                shot.video_blend_frames = 0;
                writePlan();
                renderShell();
            });
            card.append(field("Source scene", sourceSelect));
            if (!sourceRow || !Number.isInteger(block.span) || block.span < 1) {
                card.append(element(
                    "div", "h3studio-context-empty",
                    "The selected source or context split is invalid. Fix it in Scene settings.",
                ));
                blocksHost.append(card);
                continue;
            }
            const latest = Math.max(0, sourceRow.deliveredFrames - block.span);
            const validStarts = nativeContextWindowStarts(
                sourceRow.rawFrames, sourceRow.deliveredFrames, block.span,
                block.prefixFrames,
            );
            if (!validStarts.length) {
                card.append(element(
                    "div", "h3studio-context-empty",
                    "This block has no native latent-aligned position in the selected source scene.",
                ));
                blocksHost.append(card);
                continue;
            }
            const defaultStart = validStarts.at(-1);
            let start = Number.isInteger(block.startFrame)
                ? block.startFrame : defaultStart;
            let rangeError = "";
            if (!validStarts.includes(start)) {
                rangeError = `Block ${block.blockIndex + 1} start ${start} is not native-aligned for this source and target offset.`;
                const raw = Number(
                    shot.visual_context_blocks?.[block.blockIndex]
                        ?.start_frame,
                );
                start = nearestNativeContextWindowStart(
                    validStarts, Number.isInteger(raw) ? raw : defaultStart,
                );
            }
            const media = playerCheckpoint(block.sourceIndex);
            let video = null;
            if (media?.video) {
                video = element("video", "h3studio-context-video");
                video.controls = true;
                video.playsInline = true;
                video.preload = "metadata";
                video.muted = true;
                video.src = videoUrl(media.video);
                state.contextPlayers.push(video);
                card.append(video);
            } else {
                card.append(element(
                    "div", "h3studio-context-empty",
                    `Scene ${sourceRow.index} has no active saved video yet. The frame window can still be planned now.`,
                ));
            }
            const rangeWrap = element("div", "h3studio-context-range");
            const movieTrack = element("div", "h3studio-context-movie-track");
            movieTrack.tabIndex = 0;
            movieTrack.setAttribute("role", "slider");
            movieTrack.setAttribute("aria-label", `${block.label} position in source movie`);
            movieTrack.setAttribute("aria-valuemin", "0");
            movieTrack.setAttribute("aria-valuemax", String(latest));
            movieTrack.title = `Drag the fixed ${block.span}-frame context zone across ${validStarts.length} native latent-aligned positions`;
            const selectedZone = element(
                "div", "h3studio-context-window", `${block.span}f`,
            );
            const phaseTailFrames = Math.max(0, latest - defaultStart);
            const phaseTail = element("div", "h3studio-context-phase-tail");
            phaseTail.hidden = phaseTailFrames < 1;
            if (phaseTailFrames > 0) {
                phaseTail.style.left = `${(
                    (defaultStart + block.span) / sourceRow.deliveredFrames
                ) * 100}%`;
                phaseTail.style.width = `${(
                    phaseTailFrames / sourceRow.deliveredFrames
                ) * 100}%`;
            }
            const playhead = element("div", "h3studio-context-playhead");
            movieTrack.append(phaseTail, selectedZone, playhead);
            const rangeLabel = element("span", "h3studio-context-range-label");
            const movieLength = element(
                "span", "h3studio-context-movie-length",
                `source movie · ${sourceRow.deliveredFrames}f · ${(sourceRow.deliveredFrames / FPS).toFixed(3)}s`,
            );
            const error = element("div", "h3studio-error", rangeError);
            error.hidden = !rangeError;
            let selectedStart = start;
            const updateSelection = () => {
                const layout = studioContextWindowLayout(
                    sourceRow.deliveredFrames, block.span, selectedStart,
                );
                selectedStart = layout.start;
                selectedZone.style.left = `${layout.leftFraction * 100}%`;
                selectedZone.style.width = `${layout.widthFraction * 100}%`;
                selectedZone.title = `${block.span} context frames · ${layout.start + 1}–${layout.end} · native latent crop`;
                const slot = validStarts.indexOf(layout.start) + 1;
                rangeLabel.textContent = `aligned ${slot}/${validStarts.length} · frames ${layout.start + 1}–${layout.end} · ${(layout.start / FPS).toFixed(3)}–${(layout.end / FPS).toFixed(3)}s${phaseTailFrames > 0 ? ` · final ${phaseTailFrames}f use another phase` : ""}`;
                movieTrack.setAttribute("aria-valuenow", String(layout.start));
                movieTrack.setAttribute(
                    "aria-valuetext", `frames ${layout.start + 1} through ${layout.end}`,
                );
            };
            const previewStart = () => {
                if (!video || video.readyState < 1) return;
                try { video.currentTime = selectedStart / FPS; }
                catch (_error) {}
            };
            const commitStart = () => {
                if (!Array.isArray(shot.visual_context_blocks)) {
                    writeVisualBuilder(partition, {preserveStarts:true});
                }
                const authored = shot.visual_context_blocks[block.blockIndex];
                if (selectedStart === defaultStart) delete authored.start_frame;
                else authored.start_frame = selectedStart;
                shot.video_blend_frames = 0;
                error.hidden = true;
                error.textContent = "";
                writePlan();
                renderStatus();
            };
            const selectStart = (value, {seek = true, commit = false} = {}) => {
                selectedStart = nearestNativeContextWindowStart(
                    validStarts, Math.max(0, Math.min(latest, Math.round(value))),
                );
                updateSelection();
                if (seek) previewStart();
                if (commit) commitStart();
            };
            let drag = null;
            movieTrack.addEventListener("pointerdown", (event) => {
                if (event.button !== 0) return;
                event.preventDefault();
                const bounds = movieTrack.getBoundingClientRect();
                if (event.target !== selectedZone) {
                    selectStart(studioContextWindowStartAtRatio(
                        sourceRow.deliveredFrames,
                        block.span,
                        (event.clientX - bounds.left) / Math.max(1, bounds.width),
                    ));
                }
                drag = {
                    id:event.pointerId,
                    x:event.clientX,
                    start:selectedStart,
                    width:Math.max(1, bounds.width),
                };
                movieTrack.classList.add("h3studio-dragging");
                movieTrack.setPointerCapture(event.pointerId);
            });
            movieTrack.addEventListener("pointermove", (event) => {
                if (!drag || drag.id !== event.pointerId) return;
                const frameDelta = (
                    (event.clientX - drag.x) / drag.width
                ) * sourceRow.deliveredFrames;
                selectStart(drag.start + frameDelta);
            });
            const finishDrag = (event) => {
                if (!drag || drag.id !== event.pointerId) return;
                drag = null;
                movieTrack.classList.remove("h3studio-dragging");
                try { movieTrack.releasePointerCapture(event.pointerId); }
                catch (_error) {}
                commitStart();
            };
            movieTrack.addEventListener("pointerup", finishDrag);
            movieTrack.addEventListener("pointercancel", finishDrag);
            movieTrack.addEventListener("keydown", (event) => {
                let next = null;
                const currentSlot = Math.max(0, validStarts.indexOf(selectedStart));
                const step = event.shiftKey ? 5 : 1;
                if (event.key === "ArrowLeft") {
                    next = validStarts[Math.max(0, currentSlot - step)];
                } else if (event.key === "ArrowRight") {
                    next = validStarts[Math.min(validStarts.length - 1, currentSlot + step)];
                } else if (event.key === "PageUp") {
                    next = validStarts[Math.max(0, currentSlot - 5)];
                } else if (event.key === "PageDown") {
                    next = validStarts[Math.min(validStarts.length - 1, currentSlot + 5)];
                } else if (event.key === "Home") next = validStarts[0];
                else if (event.key === "End") next = defaultStart;
                if (next === null) return;
                event.preventDefault();
                selectStart(next, {commit:true});
            });
            const rangeReadout = element("div", "h3studio-context-range-readout");
            rangeReadout.append(movieLength, rangeLabel);
            rangeWrap.append(movieTrack, rangeReadout);
            if (phaseTailFrames > 0) {
                rangeWrap.append(element(
                    "div", "h3studio-context-phase-note",
                    `The hatched final ${phaseTailFrames} frames are on a different H3 latent phase for this ${block.span}-frame block at target offset ${block.prefixFrames}. A direct latent crop cannot end there; change the composed split to use that physical tail without RGB/VAE re-encoding.`,
                ));
            }
            card.append(rangeWrap);
            updateSelection();
            if (video) {
                video.addEventListener("loadedmetadata", previewStart, {once:true});
            }
            const actions = element("div", "h3studio-context-actions");
            const usePlayhead = button(
                "Start at playhead",
                "Set the context window's first frame from this player's current position",
                () => {
                    if (!video) return;
                    selectStart(video.currentTime * FPS, {commit:true});
                },
            );
            usePlayhead.disabled = !video;
            const playSelection = button(
                "Play selection",
                "Play only this context window",
                async () => {
                    if (!video) return;
                    for (const item of state.contextPlayers) {
                        if (item !== video) item.pause();
                    }
                    const endSeconds = (
                        selectedStart + block.span
                    ) / FPS;
                    video.dataset.contextEnd = String(endSeconds);
                    video.currentTime = selectedStart / FPS;
                    try { await video.play(); } catch (_error) {}
                },
            );
            playSelection.disabled = !video;
            if (video) {
                video.addEventListener("timeupdate", () => {
                    playhead.style.left = `${Math.max(0, Math.min(
                        1, video.currentTime * FPS / sourceRow.deliveredFrames,
                    )) * 100}%`;
                    const end = Number(video.dataset.contextEnd);
                    if (Number.isFinite(end) && video.currentTime >= end) {
                        delete video.dataset.contextEnd;
                        video.pause();
                        video.currentTime = end;
                    }
                });
            }
            const tail = button(
                defaultStart === latest ? "Tail (default)" : "Latest aligned (default)",
                "Use the latest native latent-aligned crop and remove the stored override",
                () => {
                    selectStart(defaultStart, {commit:true});
                },
            );
            actions.append(usePlayhead, playSelection, tail);
            card.append(actions, error);
            blocksHost.append(card);
        }
        panel.append(blocksHost);
        return panel;
    }

    function disposePlayer() {
        if (state.editorialClockFrame != null) {
            cancelAnimationFrame(state.editorialClockFrame);
            state.editorialClockFrame = null;
        }
        if (state.mediaClockFrame != null) {
            cancelAnimationFrame(state.mediaClockFrame);
            state.mediaClockFrame = null;
        }
        state.mediaClockKind = "";
        const current = state.player;
        const generatedAudio = state.playerAudio;
        const sourceAudioCurrent = state.sourceAudioPlayer;
        const sourceCurrent = state.sourcePlayer;
        const preloadVideo = state.playerPreloadVideo;
        const preloadAudio = state.playerPreloadAudio;
        state.player = null;
        state.playerAudio = null;
        state.sourceAudioPlayer = null;
        state.sourcePlayer = null;
        state.sourceLayer = null;
        state.subtitleOverlay = null;
        state.playerSegmentKey = "";
        const contextPlayers = state.contextPlayers;
        state.contextPlayers = [];
        state.playerSlider = null;
        state.playerPreloadVideo = null;
        state.playerPreloadAudio = null;
        state.primePlayerNext = null;
        state.playPlayerTransport = null;
        state.togglePlayerPlayback = null;
        for (const media of [
            current, generatedAudio, sourceAudioCurrent, sourceCurrent,
            preloadVideo, preloadAudio, ...contextPlayers,
        ]) {
            if (!media) continue;
            try { media.pause(); } catch (_error) {}
            media.removeAttribute("src");
            delete media.dataset.source;
            try { media.load(); } catch (_error) {}
        }
    }

    function seekTimeline(seconds, autoplay = false) {
        const model = timelineModel();
        const {result} = model;
        const location = locateStudioTimelineSegment(model.segments, seconds);
        if (location.index < 0) return;
        const {index, localSeconds, targetSeconds:target} = location;
        const inGap = location.kind === "gap";
        const generatedMedia = inGap ? null : playerCheckpoint(index);
        const generated = inGap ? null : generatedMedia?.video;
        const reference = inGap ? null : sourceReference(index);
        const source = sourcePreviewUrl(index, reference);
        state.playerIndex = index; state.pendingSeek = localSeconds;
        state.playerSegmentKey = location.key;
        state.timelinePosition = target;
        if (state.active !== index) {
            state.active = index; persistView(); renderSourceTimeline();
            renderSourceAudioTimeline();
            updateTimelineSelection();
            revealActiveTimelineScene();
            publishActiveScene();
        }
        if (state.playerSlider) state.playerSlider.value = String(target);
        positionTimelinePlayhead(target);
        if (!state.player) return;
        if (!generated) {
            delete state.player.dataset.source;
            state.player.removeAttribute("src"); state.player.load();
            if (state.playerAudio) {
                try { state.playerAudio.pause(); } catch (_error) {}
                delete state.playerAudio.dataset.source;
                state.playerAudio.removeAttribute("src");
                state.playerAudio.load();
            }
        } else {
            const url = videoUrl(generated);
            const targetPlayer = state.player;
            const targetAudio = state.playerAudio;
            const requestedSeek = state.pendingSeek;
            const audioUrl = generatedMedia?.audio ? videoUrl(generatedMedia.audio) : "";
            if (targetAudio && targetAudio.dataset.source !== audioUrl) {
                try { targetAudio.pause(); } catch (_error) {}
                if (audioUrl) {
                    targetAudio.dataset.source = audioUrl;
                    targetAudio.src = audioUrl;
                } else {
                    delete targetAudio.dataset.source;
                    targetAudio.removeAttribute("src");
                }
                targetAudio.load();
            }
            const seekGeneratedAudio = () => {
                if (!targetAudio?.dataset.source) return;
                const duration = Number.isFinite(targetAudio.duration)
                    ? targetAudio.duration : requestedSeek;
                try { targetAudio.currentTime = Math.min(
                    requestedSeek, Math.max(0, duration - .02),
                ); } catch (_error) {}
            };
            const applySeek = () => {
                if (state.player !== targetPlayer || !targetPlayer?.isConnected) return;
                const duration = Number.isFinite(targetPlayer.duration) ? targetPlayer.duration : requestedSeek;
                try { targetPlayer.currentTime = Math.min(requestedSeek, Math.max(0, duration - .02)); }
                catch (_error) {}
                seekGeneratedAudio();
                if (autoplay) void targetPlayer.play().catch(() => {});
            };
            if (targetAudio?.dataset.source) {
                targetAudio.addEventListener(
                    "loadedmetadata", seekGeneratedAudio, {once:true},
                );
            }
            if (targetPlayer.dataset.source !== url) {
                targetPlayer.dataset.source = url; targetPlayer.src = url; targetPlayer.load();
                targetPlayer.addEventListener("loadedmetadata", applySeek, {once:true});
            } else applySeek();
        }
        if (state.sourcePlayer) {
            const targetSource = state.sourcePlayer;
            const requestedSourceSeek = studioSourceSecond(reference, localSeconds);
            const applySourceSeek = () => {
                if (state.sourcePlayer !== targetSource || !targetSource?.isConnected) return;
                try { targetSource.currentTime = requestedSourceSeek; }
                catch (_error) {}
            };
            if (!source) {
                delete targetSource.dataset.source;
                targetSource.removeAttribute("src"); targetSource.load();
            } else if (targetSource.dataset.source !== source) {
                targetSource.dataset.source = source; targetSource.src = source; targetSource.load();
                targetSource.addEventListener("loadedmetadata", applySourceSeek, {once:true});
            } else applySourceSeek();
        }
        if (state.sourceAudioPlayer) {
            const targetSourceAudio = state.sourceAudioPlayer;
            const timelineAudio = sourceAudio();
            const timelineAudioUrl = timelineAudio ? sourceAudioUrl() : "";
            const requestedAudioSeek = timelineAudio
                ? studioSourceAudioSecond(timelineAudio, target) : 0;
            const applySourceAudioSeek = () => {
                if (state.sourceAudioPlayer !== targetSourceAudio ||
                        !targetSourceAudio?.isConnected) return;
                if (Math.abs((Number(targetSourceAudio.currentTime) || 0) -
                        requestedAudioSeek) > .12) {
                    try { targetSourceAudio.currentTime = requestedAudioSeek; }
                    catch (_error) {}
                }
            };
            if (!timelineAudioUrl) {
                targetSourceAudio.pause();
                delete targetSourceAudio.dataset.source;
                targetSourceAudio.removeAttribute("src");
                targetSourceAudio.load();
            } else if (targetSourceAudio.dataset.source !== timelineAudioUrl) {
                targetSourceAudio.dataset.source = timelineAudioUrl;
                targetSourceAudio.src = timelineAudioUrl;
                targetSourceAudio.load();
                targetSourceAudio.addEventListener(
                    "loadedmetadata", applySourceAudioSeek, {once:true},
                );
            } else applySourceAudioSeek();
        }
        const label = root.querySelector(".h3studio-player-label");
        if (label) label.textContent = inGap
            ? location.trailing
                ? `Open black timeline after scene ${index + 1} · ${formatClock(location.durationSeconds)}`
                : `Black editorial gap before scene ${index + 1} · ${formatClock(location.durationSeconds)}`
            : generated
            ? `Scene ${index + 1} · ${timing().shots[index].id}${reference ? ` ↔ @${reference.tag}` : ""}`
            : `Scene ${index + 1} has no saved segment${sourceAudio() ? " · Source track is ready" : ""}${reference ? ` · @${reference.tag} is ready` : ""}.`;
        const motionAudioToggle = root.querySelector(".h3studio-audio-motion");
        if (motionAudioToggle) {
            motionAudioToggle.disabled = !reference?.has_audio;
            if (!reference?.has_audio) motionAudioToggle.checked = false;
            const wrapper = motionAudioToggle.closest(
                ".h3studio-audio-control");
            if (wrapper) wrapper.hidden = !reference?.has_audio;
        }
        const timelineAudioToggle = root.querySelector(".h3studio-audio-source");
        const timelineAudioAvailable = Boolean(sourceAudio());
        if (timelineAudioToggle) {
            const wasAvailable = timelineAudioToggle.dataset.available === "true";
            timelineAudioToggle.disabled = !timelineAudioAvailable;
            if (timelineAudioAvailable && !wasAvailable) {
                timelineAudioToggle.checked = true;
            } else if (!timelineAudioAvailable) {
                timelineAudioToggle.checked = false;
            }
            timelineAudioToggle.dataset.available = String(timelineAudioAvailable);
            const audioControl = timelineAudioToggle.closest(
                ".h3studio-audio-control");
            const volume = audioControl?.querySelector(
                ".h3studio-audio-volume");
            if (volume) volume.disabled = !timelineAudioAvailable;
            const text = timelineAudioToggle.closest("label")?.querySelector("span");
            if (text) text.textContent = sourceAudioMuted(index)
                ? "Source track (scene muted)" : "Source track";
        }
        const hasMotion = Boolean(reference);
        if (state.sourceLayer) state.sourceLayer.hidden = !hasMotion;
        const compareControls = root.querySelector(".h3studio-compare-controls");
        compareControls?.classList.toggle("h3studio-no-motion", !hasMotion);
        for (const item of root.querySelectorAll(
            ".h3studio-wipe-label,.h3studio-wipe-control,.h3studio-wipe-line,.h3studio-compare-label",
        )) item.hidden = !hasMotion;
        root.querySelector(".h3studio-audio-generated")?.dispatchEvent(
            new Event("change"),
        );
        updateSubtitleOverlay(target);
        const segmentPosition = model.segments.findIndex(
            (segment) => segment.key === location.key,
        );
        const upcomingSegment = model.segments[segmentPosition + 1];
        state.primePlayerNext?.(
            upcomingSegment?.kind === "scene"
                ? upcomingSegment.sceneIndex : -1,
        );
        if (autoplay && !generated) state.playPlayerTransport?.();
    }

    function renderPlayerPanel() {
        const wrapper = element("div", "h3studio-player");
        const label = element("div", "h3studio-player-label", "Generated playback");
        const stage = element("div", "h3studio-compare-stage");
        let video = element("video"); video.playsInline = true; video.preload = "metadata";
        const handoffFrame = element("canvas", "h3studio-handoff-frame");
        handoffFrame.hidden = true;
        let generatedAudio = element("audio");
        generatedAudio.preload = "metadata"; generatedAudio.hidden = true;
        const sourceTimelineAudio = element("audio");
        sourceTimelineAudio.preload = "metadata"; sourceTimelineAudio.hidden = true;
        const sourceVideo = element("video"); sourceVideo.playsInline = true;
        sourceVideo.preload = "metadata"; sourceVideo.muted = true;
        const sourceLayer = element("div", "h3studio-source-layer");
        sourceLayer.style.clipPath = "inset(0 50% 0 0)";
        sourceLayer.hidden = true;
        const wipeLine = element("span", "h3studio-wipe-line"); wipeLine.style.left = "50%";
        const sourceLabel = element(
            "span", "h3studio-compare-label h3studio-compare-label-source",
            "MOTION REF",
        );
        const generatedLabel = element(
            "span", "h3studio-compare-label h3studio-compare-label-generated",
            "GENERATED",
        );
        const subtitleOverlay = element("div", "h3studio-subtitle-overlay");
        subtitleOverlay.hidden = true;
        wipeLine.hidden = true; sourceLabel.hidden = true;
        generatedLabel.hidden = true;
        sourceLayer.append(sourceVideo);
        stage.append(
            video, handoffFrame, sourceLayer, wipeLine, sourceLabel,
            generatedLabel, subtitleOverlay,
        );
        const preloadVideo = element("video");
        preloadVideo.preload = "auto"; preloadVideo.muted = true;
        preloadVideo.playsInline = true; preloadVideo.hidden = true;
        const preloadAudio = element("audio");
        preloadAudio.preload = "auto"; preloadAudio.muted = true;
        preloadAudio.hidden = true;
        let standbyVideo = preloadVideo;
        let standbyAudio = preloadAudio;
        stage.insertBefore(preloadVideo, handoffFrame);
        const clearPreload = (media) => {
            if (!media.dataset.source) return;
            delete media.dataset.source;
            media.removeAttribute("src"); media.load();
        };
        const primeNextSegment = (index) => {
            const media = index >= 0 && index < state.plan.shots.length
                ? playerCheckpoint(index) : null;
            const videoSource = media?.video ? videoUrl(media.video) : "";
            const audioSource = media?.audio ? videoUrl(media.audio) : "";
            if (!videoSource) clearPreload(standbyVideo);
            else if (standbyVideo.dataset.source !== videoSource) {
                standbyVideo.dataset.source = videoSource;
                standbyVideo.src = videoSource; standbyVideo.load();
            }
            if (!audioSource) clearPreload(standbyAudio);
            else if (standbyAudio.dataset.source !== audioSource) {
                standbyAudio.dataset.source = audioSource;
                standbyAudio.src = audioSource; standbyAudio.load();
            }
        };
        const captureHandoffFrame = () => {
            if (!video.videoWidth || !video.videoHeight ||
                    !stage.clientWidth || !stage.clientHeight) return;
            const ratio = Math.max(1, Number(window.devicePixelRatio) || 1);
            handoffFrame.width = Math.round(stage.clientWidth * ratio);
            handoffFrame.height = Math.round(stage.clientHeight * ratio);
            const context = handoffFrame.getContext("2d");
            if (!context) return;
            context.fillStyle = "#050608";
            context.fillRect(0, 0, handoffFrame.width, handoffFrame.height);
            const scale = Math.min(
                handoffFrame.width / video.videoWidth,
                handoffFrame.height / video.videoHeight,
            );
            const width = video.videoWidth * scale;
            const height = video.videoHeight * scale;
            try {
                context.drawImage(
                    video,
                    (handoffFrame.width - width) / 2,
                    (handoffFrame.height - height) / 2,
                    width, height,
                );
                handoffFrame.classList.remove("h3studio-handoff-release");
                handoffFrame.hidden = false;
            } catch (_error) {}
        };
        const releaseHandoffFrame = () => {
            if (handoffFrame.hidden) return;
            requestAnimationFrame(() => {
                handoffFrame.classList.add("h3studio-handoff-release");
            });
            window.setTimeout(() => {
                if (!handoffFrame.isConnected) return;
                handoffFrame.hidden = true;
            }, 180);
        };
        const primedSegmentReady = (index) => {
            const media = index >= 0 && index < state.plan.shots.length
                ? playerCheckpoint(index) : null;
            const videoSource = media?.video ? videoUrl(media.video) : "";
            return Boolean(
                videoSource && standbyVideo.dataset.source === videoSource &&
                standbyVideo.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA,
            );
        };
        const promotePrimedSegment = (index) => {
            if (!primedSegmentReady(index)) return false;
            const outgoingVideo = video;
            const outgoingAudio = generatedAudio;
            const incomingVideo = standbyVideo;
            const incomingAudio = standbyAudio;
            incomingVideo.playbackRate = outgoingVideo.playbackRate;
            incomingVideo.volume = outgoingVideo.volume;
            incomingVideo.muted = outgoingVideo.muted;
            incomingAudio.playbackRate = outgoingAudio.playbackRate;
            incomingAudio.volume = outgoingAudio.volume;
            incomingAudio.muted = outgoingAudio.muted;
            incomingVideo.hidden = false;
            outgoingVideo.hidden = true;
            video = incomingVideo;
            standbyVideo = outgoingVideo;
            generatedAudio = incomingAudio;
            standbyAudio = outgoingAudio;
            state.player = video;
            state.playerAudio = generatedAudio;
            state.playerPreloadVideo = standbyVideo;
            state.playerPreloadAudio = standbyAudio;
            try { standbyVideo.pause(); } catch (_error) {}
            try { standbyAudio.pause(); } catch (_error) {}
            standbyVideo.muted = true;
            standbyAudio.muted = true;
            return true;
        };
        state.playerPreloadVideo = standbyVideo;
        state.playerPreloadAudio = standbyAudio;
        state.primePlayerNext = primeNextSegment;
        const controls = element("div", "h3studio-player-controls");
        const play = button(
            "▶",
            "Play or pause the planned timeline from the current position",
            () => state.togglePlayerPlayback?.(),
        );
        const initialTimeline = timelineModel();
        const slider = element("input"); slider.type = "range"; slider.min = "0"; slider.max = String(initialTimeline.totalSeconds); slider.step = String(1 / 24); slider.value = "0";
        const clock = element("span", "h3studio-player-clock", `0 / ${formatClock(initialTimeline.totalSeconds)}`);
        slider.addEventListener("input", () => seekTimeline(Number(slider.value), false));
        const syncSource = () => {
            const reference = sourceReference(state.playerIndex);
            if (!reference || !sourceVideo.dataset.source) return;
            sourceVideo.playbackRate = video.playbackRate;
            const target = studioSourceSecond(reference, video.currentTime);
            if (Math.abs(sourceVideo.currentTime - target) > .055) {
                try { sourceVideo.currentTime = target; } catch (_error) {}
            }
        };
        const synchronizeGeneratedAudio = (playAudio = false) => {
            if (!generatedAudio.dataset.source) return;
            generatedAudio.playbackRate = video.playbackRate;
            if (Math.abs((Number(generatedAudio.currentTime) || 0) -
                    (Number(video.currentTime) || 0)) > .12) {
                try { generatedAudio.currentTime = video.currentTime; }
                catch (_error) {}
            }
            if (playAudio && generatedToggle.checked) {
                void generatedAudio.play().catch(() => {});
            }
        };
        const playerTimelineSecond = () => {
            const model = timelineModel();
            // With no generated video, load() leaves currentTime at zero.
            // Audio monitor/canplay sync must keep the editorial playhead,
            // not turn that zero into a seek back to the scene's start.
            if (!video.dataset.source) {
                return Math.max(0, Math.min(
                    model.totalSeconds, Number(state.timelinePosition) || 0,
                ));
            }
            const clock = studioPlayerSegmentClock(
                model.segments, state.playerSegmentKey,
                Number(video.currentTime) || 0, FPS,
            );
            if (clock) return Math.min(model.totalSeconds, clock.timelineSeconds);
            return Math.min(
                model.totalSeconds,
                studioEditorialSceneStartSeconds(model.segments, state.playerIndex) +
                    (Number(video.currentTime) || 0),
            );
        };
        const synchronizeSourceTimelineAudio = (
            playAudio = false, timelineSecond = playerTimelineSecond(),
        ) => {
            const timelineAudio = sourceAudio();
            if (!timelineAudio || !sourceTimelineAudio.dataset.source) return;
            sourceTimelineAudio.playbackRate = video.playbackRate;
            const target = studioSourceAudioSecond(
                timelineAudio, timelineSecond,
            );
            if (Math.abs((Number(sourceTimelineAudio.currentTime) || 0) -
                    target) > .12) {
                try { sourceTimelineAudio.currentTime = target; }
                catch (_error) {}
            }
            sourceTimelineAudio.muted =
                !sourceToggle.checked ||
                (!state.playerSegmentKey.startsWith("gap:")
                    && sourceAudioMuted(state.playerIndex));
            if (playAudio && sourceToggle.checked) {
                void sourceTimelineAudio.play().catch(() => {});
            }
        };
        const updateTransportPosition = (current) => {
            const model = timelineModel();
            const bounded = Math.max(0, Math.min(
                model.totalSeconds, Number(current) || 0,
            ));
            state.timelinePosition = bounded;
            slider.value = String(bounded);
            clock.textContent = `${formatClock(bounded)} / ${formatClock(model.totalSeconds)}`;
            positionTimelinePlayhead(bounded);
            updateSubtitleOverlay(bounded);
        };
        const sourceTimelineSecond = () => {
            const descriptor = sourceAudio();
            if (!descriptor) return Number(state.timelinePosition) || 0;
            return Math.max(
                0,
                (Number(sourceTimelineAudio.currentTime) || 0) -
                    Math.max(0, Number(descriptor.seek_seconds) || 0),
            );
        };
        let videoAdvancePending = false;
        const advanceVideoSegment = (autoplay = true) => {
            if (videoAdvancePending) return false;
            const model = timelineModel();
            const currentSegment = model.segments.findIndex(
                (segment) => segment.key === state.playerSegmentKey,
            );
            const current = model.segments[currentSegment];
            const next = model.segments[currentSegment + 1];
            if (!current) return false;
            videoAdvancePending = true;
            stopMediaClock("video");
            captureHandoffFrame();
            generatedAudio.pause(); sourceVideo.pause();
            updateTransportPosition(current.endSeconds);
            if (!next) {
                video.pause();
                sourceTimelineAudio.pause();
                play.textContent = "▶";
                videoAdvancePending = false;
                return false;
            }
            if (next.kind === "scene") promotePrimedSegment(next.sceneIndex);
            seekTimeline(next.startSeconds, autoplay);
            setTimeout(() => { videoAdvancePending = false; }, 0);
            return true;
        };
        const refreshVideoTransport = () => {
            if (!video.dataset.source) return;
            const model = timelineModel();
            const segmentClock = studioPlayerSegmentClock(
                model.segments, state.playerSegmentKey,
                Number(video.currentTime) || 0, FPS,
            );
            const current = segmentClock?.timelineSeconds
                ?? playerTimelineSecond();
            updateTransportPosition(current);
            if (segmentClock?.boundaryReached && !video.paused) {
                advanceVideoSegment(true);
                return;
            }
            synchronizeGeneratedAudio(false);
            synchronizeSourceTimelineAudio(false, current);
            syncSource();
        };
        const refreshSourceTransport = () => {
            if (!sourceAudio() || !sourceTimelineAudio.dataset.source) return;
            const current = sourceTimelineSecond();
            const model = timelineModel();
            const location = locateStudioTimelineSegment(
                model.segments, current,
            );
            if (location.index >= 0 &&
                    location.key !== state.playerSegmentKey) {
                seekTimeline(current, !sourceTimelineAudio.paused);
            } else updateTransportPosition(current);
        };
        const stopMediaClock = (kind = null) => {
            if (kind && state.mediaClockKind !== kind) return;
            if (state.mediaClockFrame != null) {
                cancelAnimationFrame(state.mediaClockFrame);
                state.mediaClockFrame = null;
            }
            state.mediaClockKind = "";
        };
        const startMediaClock = (kind) => {
            stopMediaClock();
            stopEditorialClock();
            state.mediaClockKind = kind;
            const tick = () => {
                if (!video.isConnected || state.player !== video ||
                        state.mediaClockKind !== kind) {
                    stopMediaClock(kind);
                    return;
                }
                if (kind === "video") {
                    if (video.paused || video.ended) {
                        stopMediaClock(kind);
                        return;
                    }
                    refreshVideoTransport();
                } else {
                    if (sourceTimelineAudio.paused ||
                            sourceTimelineAudio.ended || video.dataset.source) {
                        stopMediaClock(kind);
                        return;
                    }
                    refreshSourceTransport();
                }
                state.mediaClockFrame = requestAnimationFrame(tick);
            };
            state.mediaClockFrame = requestAnimationFrame(tick);
        };
        const stopEditorialClock = () => {
            if (state.editorialClockFrame == null) return;
            cancelAnimationFrame(state.editorialClockFrame);
            state.editorialClockFrame = null;
        };
        const startEditorialClock = () => {
            stopEditorialClock();
            stopMediaClock();
            const originPosition = Number(state.timelinePosition) || 0;
            const originTime = performance.now();
            const tick = (now) => {
                if (!video.isConnected || state.player !== video) return;
                let model = timelineModel();
                const unbounded =
                    originPosition + (now - originTime) / 1000;
                if (unbounded >= model.totalSeconds - 1 / FPS) {
                    extendTimelineWorkspace(
                        model.workspaceEndFrame + state.timelineSceneEndFrame,
                    );
                    model = timelineModel();
                }
                const current = Math.min(
                    model.totalSeconds,
                    unbounded,
                );
                const location = locateStudioTimelineSegment(
                    model.segments, current,
                );
                if (location.key !== state.playerSegmentKey) {
                    state.editorialClockFrame = null;
                    seekTimeline(current, true);
                    return;
                }
                updateTransportPosition(current);
                if (current >= model.totalSeconds) {
                    state.editorialClockFrame = null;
                    play.textContent = "▶";
                    return;
                }
                state.editorialClockFrame = requestAnimationFrame(tick);
            };
            play.textContent = "❚❚";
            state.editorialClockFrame = requestAnimationFrame(tick);
        };
        const playPlayerTransport = () => {
            stopEditorialClock();
            if (video.dataset.source) {
                return video.play().catch(() => {});
            }
            if (!sourceTimelineAudio.dataset.source) {
                startEditorialClock();
                return undefined;
            }
            const timelineAudio = sourceAudio();
            const availableSeconds = Number(
                timelineAudio?.available_duration_seconds
                    ?? timelineAudio?.duration_seconds,
            ) || 0;
            if ((Number(state.timelinePosition) || 0) >=
                    availableSeconds - 1 / FPS) {
                startEditorialClock();
                return undefined;
            }
            synchronizeSourceTimelineAudio(
                false, state.timelinePosition ?? 0,
            );
            play.textContent = "❚❚";
            return sourceTimelineAudio.play().catch(() => {
                play.textContent = "▶";
            });
        };
        const togglePlayerPlayback = () => {
            if (video.dataset.source) {
                if (video.paused) void playPlayerTransport();
                else {
                    video.pause();
                    pausePlayerMonitors();
                }
                return;
            }
            if (!sourceTimelineAudio.dataset.source) {
                if (state.editorialClockFrame == null) startEditorialClock();
                else { stopEditorialClock(); play.textContent = "▶"; }
                return;
            }
            if (sourceTimelineAudio.paused) void playPlayerTransport();
            else {
                sourceTimelineAudio.pause();
                generatedAudio.pause(); sourceVideo.pause();
            }
        };
        state.playPlayerTransport = playPlayerTransport;
        state.togglePlayerPlayback = togglePlayerPlayback;
        const transportVideos = [video, standbyVideo];
        const generatedAudios = [generatedAudio, standbyAudio];
        const onActiveVideo = (eventName, listener) => {
            for (const player of transportVideos) {
                player.addEventListener(eventName, (event) => {
                    if (event.currentTarget !== video) return;
                    listener(event);
                });
            }
        };
        const onActiveGeneratedAudio = (eventName, listener) => {
            for (const player of generatedAudios) {
                player.addEventListener(eventName, (event) => {
                    if (event.currentTarget !== generatedAudio) return;
                    listener(event);
                });
            }
        };
        onActiveVideo("play", () => {
            play.textContent = "❚❚";
            syncSource(); synchronizeGeneratedAudio(true);
            synchronizeSourceTimelineAudio(true);
            if (sourceVideo.dataset.source) void sourceVideo.play().catch(() => {});
        });
        onActiveVideo("playing", () => {
            releaseHandoffFrame();
            startMediaClock("video");
        });
        onActiveVideo("pause", () => {
            stopMediaClock("video");
            const model = timelineModel();
            const currentSegment = model.segments.findIndex(
                (segment) => segment.key === state.playerSegmentKey,
            );
            const upcoming = model.segments[currentSegment + 1];
            if (video.ended && upcoming?.kind === "scene" &&
                    primedSegmentReady(upcoming.sceneIndex)) {
                generatedAudio.pause(); sourceVideo.pause();
                return;
            }
            play.textContent = "▶";
            // The source track is a monitor slaved to the video transport.
            // Always stop it on pause; automatic scene handoff will restart it
            // from the next absolute timeline position when video fires play.
            pausePlayerMonitors();
        });
        onActiveVideo("waiting", () => {
            stopMediaClock("video");
            generatedAudio.pause(); sourceTimelineAudio.pause(); sourceVideo.pause();
        });
        onActiveGeneratedAudio("canplay", () => {
            synchronizeGeneratedAudio(!video.paused);
        });
        sourceVideo.addEventListener("canplay", () => {
            syncSource();
            if (!video.paused) void sourceVideo.play().catch(() => {});
        });
        sourceTimelineAudio.addEventListener("canplay", () => {
            synchronizeSourceTimelineAudio(!video.paused);
        });
        sourceTimelineAudio.addEventListener("play", () => {
            if (!video.dataset.source) {
                play.textContent = "❚❚";
                startMediaClock("source");
            }
        });
        sourceTimelineAudio.addEventListener("pause", () => {
            stopMediaClock("source");
            if (!video.dataset.source) play.textContent = "▶";
        });
        onActiveVideo("seeking", () => {
            generatedAudio.pause(); sourceTimelineAudio.pause(); sourceVideo.pause();
            synchronizeGeneratedAudio(false); synchronizeSourceTimelineAudio(false);
            syncSource();
        });
        onActiveVideo("seeked", () => {
            synchronizeGeneratedAudio(!video.paused);
            synchronizeSourceTimelineAudio(!video.paused); syncSource();
            if (!video.paused && sourceVideo.dataset.source) {
                void sourceVideo.play().catch(() => {});
            }
        });
        onActiveVideo("ratechange", () => {
            synchronizeGeneratedAudio(false);
            synchronizeSourceTimelineAudio(false); syncSource();
        });
        // timeupdate is only a low-frequency fallback. The visible transport
        // is driven from requestAnimationFrame while media is playing so its
        // clock and red timeline line remain on the same frame.
        onActiveVideo("timeupdate", refreshVideoTransport);
        sourceTimelineAudio.addEventListener("timeupdate", () => {
            if (video.dataset.source) return;
            refreshSourceTransport();
        });
        sourceTimelineAudio.addEventListener("ended", () => {
            if (video.dataset.source) return;
            stopMediaClock("source");
            const descriptor = sourceAudio();
            const current = Math.max(0, Number(
                descriptor?.available_duration_seconds
                    ?? descriptor?.duration_seconds,
            ) || state.timelinePosition || 0);
            updateTransportPosition(current);
            startEditorialClock();
        });
        onActiveVideo("ended", () => {
            advanceVideoSegment(true);
        });
        const compareControls = element("div", "h3studio-compare-controls");
        const wipeLabel = element("span", "h3studio-wipe-label", "Wipe");
        const wipe = element("input", "h3studio-wipe-control");
        wipe.type = "range"; wipe.min = "0"; wipe.max = "100";
        wipe.step = "1"; wipe.value = "50";
        wipe.addEventListener("input", () => {
            const percent = Number(wipe.value);
            sourceLayer.style.clipPath = `inset(0 ${100 - percent}% 0 0)`;
            wipeLine.style.left = `${percent}%`;
        });
        const audioMix = element("div", "h3studio-audio-mix");
        const applyAudioVolumes = () => {
            video.volume = state.generatedVolume;
            generatedAudio.volume = state.generatedVolume;
            sourceTimelineAudio.volume = state.sourceVolume;
            sourceVideo.volume = state.motionVolume;
        };
        const audioToggle = (
            className, text, checked, propertyName, stateName,
        ) => {
            const control = element("div", "h3studio-audio-control");
            const wrapper = element("label", "h3studio-audio-toggle");
            const input = element("input", className);
            input.type = "checkbox"; input.checked = checked;
            const copy = element("span", "", text);
            wrapper.append(input, copy);
            const volume = element("input", "h3studio-audio-volume");
            volume.type = "range"; volume.min = "0"; volume.max = "1";
            volume.step = ".01"; volume.value = String(state[stateName]);
            volume.title = `${text} monitor volume`;
            const level = element(
                "span", "h3studio-audio-level",
                `${Math.round(state[stateName] * 100)}%`,
            );
            volume.addEventListener("input", () => {
                state[stateName] = Math.max(0, Math.min(1,
                    Number(volume.value) || 0));
                level.textContent = `${Math.round(state[stateName] * 100)}%`;
                applyAudioVolumes();
            });
            volume.addEventListener("change", () => {
                node.properties[propertyName] = state[stateName];
                dirty();
            });
            control.append(wrapper, volume, level);
            return {wrapper:control, input, copy, volume};
        };
        const generatedControl = audioToggle(
            "h3studio-audio-generated", "Generated", true,
            GENERATED_VOLUME_PROPERTY, "generatedVolume",
        );
        const sourceControl = audioToggle(
            "h3studio-audio-source", "Source track", true,
            SOURCE_VOLUME_PROPERTY, "sourceVolume",
        );
        const motionControl = audioToggle(
            "h3studio-audio-motion", "Motion-ref", false,
            MOTION_VOLUME_PROPERTY, "motionVolume",
        );
        const generatedToggle = generatedControl.input;
        const sourceToggle = sourceControl.input;
        const motionToggle = motionControl.input;
        const applyAudioMix = () => {
            applyAudioVolumes();
            video.muted = !generatedToggle.checked ||
                Boolean(generatedAudio.dataset.source);
            generatedAudio.muted = !generatedToggle.checked;
            sourceVideo.muted = !motionToggle.checked;
            sourceTimelineAudio.muted = !sourceToggle.checked ||
                (!state.playerSegmentKey.startsWith("gap:")
                    && sourceAudioMuted(state.playerIndex));
            if (generatedToggle.checked) {
                synchronizeGeneratedAudio(!video.paused);
            } else {
                generatedAudio.pause();
            }
            if (sourceToggle.checked) {
                synchronizeSourceTimelineAudio(!video.paused);
            } else {
                sourceTimelineAudio.pause();
            }
        };
        for (const control of [
            generatedControl, sourceControl, motionControl,
        ]) {
            control.input.addEventListener("change", applyAudioMix);
            audioMix.append(control.wrapper);
        }
        applyAudioVolumes();
        compareControls.append(wipeLabel, wipe, audioMix);
        state.player = video; state.playerAudio = generatedAudio;
        state.sourceAudioPlayer = sourceTimelineAudio;
        state.sourcePlayer = sourceVideo;
        state.sourceLayer = sourceLayer; state.subtitleOverlay = subtitleOverlay;
        state.playerSlider = slider;
        controls.append(play, slider, clock, button("Refresh", "Rescan saved checkpoints and segments", async () => {
            await refreshCheckpoints(); renderPanel();
        }));
        wrapper.append(
            label, stage, generatedAudio, sourceTimelineAudio,
            preloadAudio,
            compareControls, controls,
            element("div", "h3studio-message", "Generated and Source Track can play together on the planned timeline. Adjacent saved scenes are pre-decoded in a second player for a smooth boundary handoff; this preview behavior never changes the saved clips or final assembly. Before a scene is rendered, Source Track playback supplies the timeline clock; playback hands back to video automatically when a saved segment begins. Each monitor has independent volume; waveform speaker buttons mute only the Source Track for that scene. Click the player and press Space to play or pause. Motion-ref audio is optional when available."),
        );
        setTimeout(() => {
            if (state.player !== video || !video.isConnected) return;
            const result = timing();
            const start = state.timelinePosition == null
                ? studioEditorialSceneStartSeconds(
                    timelineModel().segments, state.active,
                )
                : state.timelinePosition;
            seekTimeline(start, false);
        }, 0);
        return wrapper;
    }

    function renderSubtitlesPanel() {
        const panel = element("div");
        const head = element("div", "h3studio-scene-head");
        head.append(
            element("strong", "", "Timed lyrics / subtitles"),
            element("span", "h3studio-scene-label",
                "Editorial only · previewed here and exported as SRT beside final assembly"),
        );
        const settingsHost = element("div", "h3studio-subtitle-settings");
        const assetSelect = element("select");
        const none = element("option", "", state.subtitleAssets.length
            ? "Choose an audio asset with timed lyrics" : "No timed-lyrics audio assets found");
        none.value = ""; assetSelect.append(none);
        for (const asset of state.subtitleAssets) {
            const cues = parseTimedLyrics(asset.lyrics);
            const option = element(
                "option", "",
                `@${asset.tag || "audio"} · ${cues.length} timed cue${cues.length === 1 ? "" : "s"}`,
            );
            option.value = String(asset.id ?? "");
            assetSelect.append(option);
        }
        assetSelect.value = String(state.editorial.subtitles?.asset_id ?? "");
        assetSelect.title = "Audio assets become available here when their Lyrics field contains LRC timestamps such as [01:23.45] or SRT cue blocks.";
        const mode = element("select");
        for (const [value, label] of [
            ["off", "Off"],
            ["preview_srt", "Preview + SRT sidecar"],
        ]) {
            const option = element("option", "", label);
            option.value = value; mode.append(option);
        }
        mode.value = state.editorial.subtitles?.mode ?? "off";
        mode.title = "Preview + SRT shows timed lyrics in Plan Studio and writes a matching .srt file beside each final assembled video. It does not burn text into pixels.";
        const offset = element("input");
        offset.type = "number"; offset.min = "-3600"; offset.max = "3600";
        offset.step = String(1 / FPS);
        offset.value = String(state.editorial.subtitles?.offset_seconds ?? 0);
        offset.title = "Shift every subtitle on the editorial timeline. Positive values display later; negative values display earlier.";
        const save = () => {
            state.editorial.subtitles = {
                mode:mode.value,
                asset_id:assetSelect.value,
                offset_seconds:Math.max(-3600, Math.min(
                    3600, Number(offset.value) || 0,
                )),
            };
            scheduleEditorialSave();
            renderSubtitleTimeline();
            updateSubtitleOverlay();
        };
        mode.addEventListener("change", () => { save(); renderPanel(); });
        assetSelect.addEventListener("change", () => { save(); renderPanel(); });
        offset.addEventListener("change", () => { save(); renderPanel(); });
        settingsHost.append(
            field("Lyrics asset", assetSelect),
            field("Output", mode),
            field("Timeline offset (s)", offset),
        );
        const actions = element("div", "h3studio-prompt-tools");
        actions.append(
            button("Refresh assets", "Reload lyrics from Project Asset Carousel", () => {
                void loadSubtitleAssets();
            }),
            element("span", "h3studio-hint",
                "Use [MM:SS.xx] at the beginning of lyric lines, or paste SRT. The Asset Carousel player includes a Stamp line button for live timing."),
        );
        const cues = subtitleCues();
        const list = element("div", "h3studio-subtitle-list");
        if (!selectedSubtitleAsset()) list.append(element(
            "div", "h3studio-message",
            "Choose an audio asset whose Lyrics field contains timestamps.",
        ));
        else if (!cues.length) list.append(element(
            "div", "h3studio-error",
            "This Lyrics field has no LRC or SRT timestamps yet.",
        ));
        else for (const cue of cues.slice(0, 500)) {
            list.append(element("div", "h3studio-subtitle-row", ""));
            list.lastElementChild.append(
                element("span", "h3studio-hint",
                    `${formatClock(cue.startSeconds)} → ${formatClock(cue.endSeconds)}`),
                element("span", "", cue.text),
            );
        }
        panel.append(head, settingsHost, actions, list);
        return panel;
    }

    function renderJsonPanel() {
        const panel = element("div");
        const textarea = element("textarea", "h3studio-json"); textarea.value = planToJson(state.plan); textarea.spellcheck = false;
        const status = element("span", "h3studio-message", "Raw JSON escape hatch");
        const actions = element("div", "h3studio-json-actions");
        actions.append(button("Apply JSON", "Validate and replace the current plan JSON", () => {
            try { state.plan = parsePlanJson(textarea.value); state.active = Math.min(state.active, state.plan.shots.length - 1); writePlan(); status.textContent = "JSON applied"; renderShell(); publishActiveScene(); }
            catch (error) { status.textContent = error.message; status.classList.add("h3studio-error"); }
        }), button("Copy", "Copy plan JSON", async () => {
            try { await navigator.clipboard.writeText(textarea.value); status.textContent = "Copied"; }
            catch (_error) { textarea.select(); document.execCommand("copy"); status.textContent = "Copied"; }
        }), status);
        panel.append(textarea, actions); return panel;
    }

    function renderPanel() {
        if (!state.panelHost || !state.plan) return;
        state.history.host = null; state.history.textarea = null; state.history.status = null;
        disposePlayer();
        const content = state.view === "scene" && state.activeChapterId
            ? renderChapterPanel()
            : state.view === "shared" ? renderSharedPanel()
            : state.view === "settings" ? renderPlanSettingsPanel()
            : state.view === "context" ? renderContextPanel()
            : state.view === "player" ? renderPlayerPanel()
            : state.view === "subtitles" ? renderSubtitlesPanel()
              : state.view === "json" ? renderJsonPanel() : renderScenePanel();
        state.panelHost.replaceChildren(content);
    }

    function renderToolbarState() {
        for (const item of root.querySelectorAll("[data-studio-view]")) {
            item.classList.toggle("h3studio-active", item.dataset.studioView === state.view);
        }
    }

    function renderShell() {
        const timelineScroll = timelineScrollSnapshot();
        // Rebuilding the Studio must never pan the editorial timeline. Scene
        // selection already reveals a card when the user explicitly chooses
        // it; passive rerenders keep the scrollbar exactly where it was.
        const revealTimelineActive = false;
        disposePlayer();
        state.timelineResizeObserver?.disconnect();
        state.timelineResizeObserver = null;
        root.replaceChildren();
        const head = element("div", "h3studio-head");
        head.append(element("span", "h3studio-title", "MiniMax H3 Plan Studio"),
            element("span", "h3studio-run", runName()
                ? `${state.planNode ? "linked" : "standalone"} · ${runName()}`
                : "name this run in Plan settings"));
        const toolbar = element("div", "h3studio-toolbar");
        const add = button("+ Scene", "Append a new scene and select it", async () => {
            if (state.plan.shots.length >= MAX_SHOTS) return;
            await flushHistoryDraft();
            state.plan.shots.push(makeShot(state.plan.shots));
            state.activeChapterId = "";
            state.active = state.plan.shots.length - 1;
            state.timelinePosition = null; persistView(); writePlan(); renderShell(); publishActiveScene();
        });
        add.disabled = state.plan.shots.length >= MAX_SHOTS;
        const addChapter = button("+ Chapter", "Add a zero-duration chapter marker before the selected scene", async () => {
            await flushHistoryDraft();
            try {
                const chapter = makeChapter(state.plan, state.active);
                state.activeChapterId = chapter.id;
                state.view = "scene";
                persistView(); writePlan(); renderShell();
            } catch (error) {
                console.warn(error);
            }
        });
        const duplicate = button("Duplicate", "Duplicate the selected scene", async () => {
            if (state.plan.shots.length >= MAX_SHOTS) return;
            await flushHistoryDraft();
            preserveDelegatedPrompts();
            duplicateShot(state.plan.shots, state.active); state.active += 1;
            state.activeChapterId = "";
            state.timelinePosition = null; persistView(); writePlan(); renderShell(); publishActiveScene();
        });
        const remove = button(state.activeChapterId ? "Delete chapter" : "Delete", "Delete the selected scene or chapter", async () => {
            if (state.activeChapterId) {
                const chapter = orderedChapters(state.plan).find(
                    (candidate) => candidate.id === state.activeChapterId,
                );
                if (!chapter || !confirm(`Delete ${chapter.title}?`)) return;
                state.plan.chapters = state.plan.chapters.filter(
                    (candidate) => candidate.id !== chapter.id,
                );
                if (!state.plan.chapters.length) delete state.plan.chapters;
                state.activeChapterId = "";
                persistView(); writePlan(); renderShell();
                return;
            }
            if (state.plan.shots.length <= 1 || !confirm(`Delete scene ${state.active + 1}?`)) return;
            await flushHistoryDraft();
            removePlanShot(state.plan, state.active);
            state.editorial = normalizedEditorial(state.editorial);
            state.active = Math.min(state.active, state.plan.shots.length - 1);
            state.timelinePosition = null; persistView(); writePlan(); renderShell(); publishActiveScene();
        });
        remove.disabled = !state.activeChapterId && state.plan.shots.length <= 1;
        const left = button("←", "Move selected scene earlier", async () => {
            if (!state.active || sceneLocked(state.active)) return; await flushHistoryDraft();
            moveShot(state.plan.shots, state.active, state.active - 1); state.active -= 1;
            state.timelinePosition = null; persistView(); writePlan(); renderShell(); publishActiveScene();
        }); left.disabled = Boolean(state.activeChapterId) || !state.active
            || sceneLocked(state.active);
        const right = button("→", "Move selected scene later", async () => {
            if (state.active >= state.plan.shots.length - 1
                    || sceneLocked(state.active)) return; await flushHistoryDraft();
            moveShot(state.plan.shots, state.active, state.active + 1); state.active += 1;
            state.timelinePosition = null; persistView(); writePlan(); renderShell(); publishActiveScene();
        }); right.disabled = Boolean(state.activeChapterId)
            || state.active >= state.plan.shots.length - 1
            || sceneLocked(state.active);
        toolbar.append(add, addChapter, duplicate, remove, left, right, element("span", "h3studio-spacer"));
        const sceneViewLabel = state.activeChapterId
            ? "Chapter notes"
            : state.promptEditors.length ? "Scene settings" : "Scene prompt";
        for (const [value,label] of [["scene",sceneViewLabel],["shared","Shared prompt"],
            ["settings","Plan settings"],["context","Context"],
            ["player","Player"],["subtitles","Subtitles"],["json","JSON"]]) {
            const item = button(label, `Open ${label.toLowerCase()} view`, () => {
                void flushHistoryDraft();
                if (value === "player" && state.timelinePosition == null) {
                    state.timelinePosition = studioEditorialSceneStartSeconds(
                        timelineModel().segments, state.active,
                    );
                }
                state.view = value; persistView(); renderToolbarState();
                renderSourceTimeline(); renderSourceAudioTimeline(); renderPanel();
                if (value === "subtitles") void loadSubtitleAssets();
            });
            item.dataset.studioView = value; toolbar.append(item);
        }
        const status = element("div", "h3studio-statusline");
        const shell = element("div", "h3studio-timeline-shell");
        const timelineTools = element("div", "h3studio-timeline-tools");
        const zoomOut = button(
            "−", "Zoom timeline out", () => setTimelineZoom(
                state.timelineZoom - .25,
            ),
        );
        const zoomInput = element("input", "h3studio-timeline-zoom");
        zoomInput.type = "range";
        zoomInput.min = "1";
        zoomInput.max = "6";
        zoomInput.step = ".05";
        zoomInput.value = String(state.timelineZoom);
        zoomInput.title = "Timeline zoom · Ctrl/Cmd + wheel over the timeline";
        zoomInput.addEventListener("input", () => {
            setTimelineZoom(zoomInput.value);
        });
        const zoomLabel = element(
            "span", "h3studio-zoom-label",
            `${Math.round(state.timelineZoom * 100)}%`,
        );
        const zoomIn = button(
            "+", "Zoom timeline in", () => setTimelineZoom(
                state.timelineZoom + .25,
            ),
        );
        const fit = button("Fit", "Fit timeline to the available width", () => {
            setTimelineZoom(1);
        });
        const lockedCount = state.editorial.locked_scene_ids.length;
        const unlockAll = button(
            `Unlock all (${lockedCount})`,
            "Unlock every scene without changing its current editorial position",
            unlockAllScenes,
        );
        unlockAll.hidden = !lockedCount;
        timelineTools.append(
            element("strong", "", "Timeline"),
            unlockAll,
            element("span", "h3studio-spacer"),
            zoomOut, zoomInput, zoomLabel, zoomIn, fit,
        );
        const timelineGrid = element("div", "h3studio-timeline-grid");
        const timelineLabels = element("div", "h3studio-timeline-labels");
        timelineLabels.append(
            element("span", "", "TIME"),
            element("span", "", "GENERATED"),
            element("span", "", "MOTION REF"),
            element("span", "", "SOURCE AUDIO"),
            element("span", "", "SUBTITLES"),
        );
        const timelineViewport = element("div", "h3studio-timeline-viewport");
        const timelineContent = element("div", "h3studio-timeline-content");
        const ruler = element("div", "h3studio-ruler");
        const timelineHost = element("div", "h3studio-timeline h3studio-generated-timeline");
        const sourceTimelineHost = element("div", "h3studio-timeline");
        const sourceAudioTimelineHost = element(
            "div", "h3studio-timeline h3studio-audio-timeline",
        );
        const subtitleTimelineHost = element(
            "div", "h3studio-subtitle-timeline",
        );
        timelineContent.append(
            ruler, timelineHost, sourceTimelineHost, sourceAudioTimelineHost,
            subtitleTimelineHost,
        );
        timelineViewport.append(timelineContent);
        timelineGrid.append(timelineLabels, timelineViewport);
        shell.append(timelineTools, timelineGrid);
        state.timelineHost = timelineHost;
        state.sourceTimelineHost = sourceTimelineHost;
        state.sourceAudioTimelineHost = sourceAudioTimelineHost;
        state.subtitleTimelineHost = subtitleTimelineHost;
        state.sourceTrack = sourceTimelineHost;
        state.sourceAudioTrack = sourceAudioTimelineHost;
        state.timelineViewport = timelineViewport;
        state.timelineContent = timelineContent;
        state.timelineRuler = ruler;
        state.timelineZoomInput = zoomInput;
        state.timelineZoomLabel = zoomLabel;
        state.timelineScrollIntentUntil = 0;
        state.timelineLastScrollLeft = timelineViewport.scrollLeft;
        const noteTimelineScrollIntent = (duration = 1200) => {
            state.timelineScrollIntentUntil = performance.now() + duration;
        };
        timelineViewport.addEventListener("pointerdown", (event) => {
            if (event.target === timelineViewport || event.pointerType === "touch") {
                noteTimelineScrollIntent(2000);
            }
        }, {passive:true});
        timelineViewport.addEventListener("wheel", (event) => {
            const horizontalDelta = event.shiftKey ? event.deltaY : event.deltaX;
            if (horizontalDelta > 0) noteTimelineScrollIntent();
            if (event.ctrlKey || event.metaKey) {
                event.preventDefault();
                const rect = timelineViewport.getBoundingClientRect();
                const anchor = rect.width > 0
                    ? (event.clientX - rect.left) / rect.width : .5;
                setTimelineZoom(
                    state.timelineZoom * Math.exp(-event.deltaY * .0025),
                    anchor,
                );
            } else if (event.shiftKey && event.deltaY) {
                event.preventDefault();
                timelineViewport.scrollLeft += event.deltaY;
            }
        }, {passive:false});
        timelineViewport.addEventListener("scroll", () => {
            const currentScrollLeft = timelineViewport.scrollLeft;
            const movingRight = currentScrollLeft >
                state.timelineLastScrollLeft + .5;
            state.timelineLastScrollLeft = currentScrollLeft;
            if (state.timelineDragging || state.timelineExtending
                    || !movingRight
                    || performance.now() > state.timelineScrollIntentUntil) return;
            if (timelineViewport.scrollLeft + timelineViewport.clientWidth <
                    timelineViewport.scrollWidth - 32) return;
            extendTimelineWorkspace();
        }, {passive:true});
        if (typeof ResizeObserver === "function") {
            state.timelineResizeObserver = new ResizeObserver(() => {
                layoutTimeline({preserveScroll:true});
            });
            state.timelineResizeObserver.observe(timelineViewport);
        }
        const panelHost = element("div", "h3studio-panel"); state.panelHost = panelHost;
        root.append(head, branchToolbar(), toolbar, status, shell, panelHost);
        root.inert = Boolean(branches?.busy);
        root.setAttribute("aria-busy", String(Boolean(branches?.busy)));
        renderToolbarState(); renderStatus();
        renderTimeline({
            revealActive:revealTimelineActive,
            restoreScroll:timelineScroll,
        });
        renderPanel();
    }

    function showFailure(message) {
        disposePlayer();
        root.replaceChildren(element("div", "h3studio-title", "MiniMax H3 Plan Studio"),
            element("div", "h3studio-error", message),
            element("div", "h3studio-message", "Repair the JSON tab or connect a valid H3 Chain Plan. Studio can operate in either mode."));
    }

    function loadPlan(force = false, throwOnError = false) {
        if (branches?.busy && !force) return;
        const planNode = upstreamPlanNode(node);
        if (planNode) mirrorConnectedPlan(planNode);
        const planOwner = planNode ?? node;
        const planWidget = widget(planOwner, "plan_json");
        if (!planWidget) {
            if (force || state.planOwner) {
                state.plan = null; state.planNode = null;
                state.planOwner = null; state.planWidget = null;
                showFailure("Plan Studio's internal Plan fields are unavailable.");
            }
            return;
        }
        const value = String(planWidget.value ?? "");
        if (planNode && branchWidget) {
            try { branchWidget.value = workingBranchId(parsePlanJson(value)._branch_id); }
            catch { /* The normal Plan validation below reports this. */ }
        }
        const currentRun = String(widget(planOwner, "run_name")?.value ?? "").trim();
        const currentSettings = settingsSignature(planOwner);
        const promptEditors = planNode ? connectedPromptEditors(node).filter(
            (editor) => upstreamPlanNode(editor) === planNode,
        ) : [];
        const currentPromptEditors = promptEditorsSignature(promptEditors);
        if (!force && planOwner === state.planOwner && value === state.lastValue
                && currentRun === state.lastRunName
                && state.lastBranchId === currentBranch()
                && currentSettings === state.lastSettingsSignature
                && currentPromptEditors === state.lastPromptEditorsSignature) return;
        try {
            const runChanged = planOwner !== state.planOwner || currentRun !== state.lastRunName
                || state.lastBranchId !== currentBranch();
            state.lastBranchId = currentBranch();
            branches.selected = currentBranch();
            const previousRun = state.lastRunName;
            if (runChanged && previousRun) {
                void flushProjectWrites(previousRun).catch((error) => {
                    console.warn(
                        "H3 Plan Studio could not flush the previous Run " +
                        "before switching:", error,
                    );
                });
            }
            state.plan = parsePlanJson(value); state.planNode = planNode;
            state.planOwner = planOwner; state.planWidget = planWidget;
            state.lastValue = value; state.lastRunName = currentRun;
            state.lastSettingsSignature = currentSettings;
            state.promptEditors = promptEditors;
            state.lastPromptEditorsSignature = currentPromptEditors;
            if (state.activeChapterId && !orderedChapters(state.plan).some(
                (chapter) => chapter.id === state.activeChapterId,
            )) state.activeChapterId = "";
            if (runChanged) {
                const cached = currentBranch() === "main" ? restoreStudioCheckpointCache(
                    node.properties[CHECKPOINT_CACHE_PROPERTY], currentRun,
                ) : null;
                const cachedRecords = cached?.checkpoints ?? [];
                state.checkpoints = new Map(cachedRecords.map(
                    (item) => [Number(item.scene), item],
                ));
                state.checkpointSignature = cached
                    ? studioCheckpointSignature(currentRun, cachedRecords) : "";
                state.checkpointError = ""; state.timelinePosition = null;
                state.editorialReady = false; state.editorialRun = "";
                state.editorialBindingError = "";
                state.editorialSaveError = "";
                state.editorialUnusedSceneIds = [];
                state.editorial = cached?.editorial
                    ? normalizedEditorial(cached.editorial)
                    : {revision:"", placements:[], trims:[], locked_scene_ids:[], subtitles:{
                        mode:"off", asset_id:"", offset_seconds:0,
                    }, alternate_draft:null, replacements:[]};
                if (cached?.editorial) state.editorialRun = currentRun;
                state.timelineWorkspaceEndFrame = 0;
                state.timelineSceneEndFrame = 0;
                state.timelineRenderedActive = null;
                state.timelineViewport = null;
                state.timelineContent = null;
                state.lastEditorialSignature = "";
                state.subtitleAssets = []; state.subtitleAssetsRun = "";
                state.subtitleAssetsToken += 1;
                state.presentationToken += 1;
                state.sourcePreview = null;
                state.sourceWaveform = null; state.sourceWaveformToken = "";
                state.sourceWaveformPromise = null;
            }
            state.active = Math.min(state.active, state.plan.shots.length - 1);
            // Always synchronize the hidden one-shot queue widget on load.
            // Editorial data is useful even when the Plan has no chapters.
            syncAlternateTakeWidget();
            renderShell(); void refreshCheckpoints();
            if (runChanged && currentRun) {
                void restoreSourcePresentation();
                void loadSubtitleAssets();
                if (branches.run !== currentRun || (!branches.busy && branches.binding?.branch_id !== currentBranch())) void branches.refresh(currentRun).catch(error => {
                    branches.error = error.message; renderShell();
                });
            }
        } catch (error) {
            if (throwOnError) throw error;
            showFailure(`${planNode ? "Connected Plan" : "Standalone Plan Studio"} JSON is invalid:\n${error.message}`);
        }
    }

    const domWidget = node.addDOMWidget("h3_plan_studio", "h3-plan-studio", root, {
        serialize:false, hideOnZoom:false, getMinHeight:() => 540,
    });
    domWidget.serialize = false;
    node.setSize?.([Math.max(Number(node.size?.[0]) || 0, MIN_WIDTH), Math.max(Number(node.size?.[1]) || 0, MIN_HEIGHT)]);
    const connectionsChanged = node.onConnectionsChange;
    node.onConnectionsChange = function () {
        const result = connectionsChanged?.apply(this, arguments);
        setTimeout(() => { loadPlan(true); publishActiveScene(); }, 0);
        return result;
    };
    const onPromptExecuted = (event) => {
        const sourceValues = event.detail?.output?.h3_plan_studio_source_timeline;
        const sourcePayload = Array.isArray(sourceValues) ? sourceValues.at(-1) : null;
        const displayNode = event.detail?.display_node ?? event.detail?.node;
        if (sourcePayload && String(displayNode ?? "") === String(node.id ?? "")
                && String(sourcePayload.run_name ?? "") === runName()) {
            state.presentationToken += 1;
            applySourcePresentation(sourcePayload);
        }
        // Plan Studio executes near the start of a recursive queue, while the
        // alternate is accepted by Loop End. Refresh after later node events
        // so the armed draft becomes the selected ALT without waiting for the
        // periodic poll (the refresh queue coalesces repeated events).
        if (state.editorial.alternate_draft) {
            setTimeout(() => void refreshCheckpoints(), 250);
        }
        const values = event.detail?.output?.h3_chain_active_scene;
        const scene = Array.isArray(values) ? values.at(-1) : null;
        if (!scene || String(scene.run_name ?? "") !== runName()
                || (scene._branch_id ?? "main") !== currentBranch()) return;
        const shot = state.plan?.shots?.[state.active];
        const sceneId = safeShotId(
            shot?.id, `clip_${String(state.active + 1).padStart(4, "0")}`,
        );
        if (String(scene.shot_id ?? "") !== sceneId) return;
        setTimeout(() => {
            if (state.view !== "scene" || state.history.sceneKey !== historyKey(sceneId)) return;
            void loadHistory(sceneId, promptValueToText(shot.prompt), false);
        }, 50);
    };
    api.addEventListener("executed", onPromptExecuted);
    const onExecutionStart = (event) => {
        const promptId = String(event.detail?.prompt_id ?? "");
        if (promptId) state.executionPromptIds.add(promptId);
    };
    const onExecutionTerminal = (event) => {
        const promptId = String(event.detail?.prompt_id ?? "");
        if (!promptId || !state.executionPromptIds.delete(promptId) ||
                state.executionPromptIds.size !== 0 || state.disposed) return;
        void refreshCheckpoints();
    };
    api.addEventListener("execution_start", onExecutionStart);
    api.addEventListener("execution_success", onExecutionTerminal);
    api.addEventListener("execution_error", onExecutionTerminal);
    api.addEventListener("execution_interrupted", onExecutionTerminal);
    const onLoRARoutesChanged = () => {
        if (!state.disposed && state.plan) {
            renderPanel();
            renderTimeline();
        }
    };
    document.addEventListener("h3-lora-routes-changed", onLoRARoutesChanged);
    node._h3FlushProjectWrites = flushProjectWrites;
    function onProjectOwnershipChanged(payload) {
        if (state.disposed || payload?.owned_by_requester !== true) return;
        const currentRun = runName();
        const history = state.history;
        const prefix = `${currentRun}\u0000`;
        if (!currentRun || payload.run_name !== currentRun
                || !history.sceneKey.startsWith(prefix)
                || !isProjectReadOnlyError(history.error, currentRun)) return;
        const sceneId = history.sceneKey.slice(prefix.length);
        const status = history.status ?? state.status;
        if (status) status.textContent =
            "Write access restored. The blocked action was not retried.";
        // Refresh only the history controls. Never replay a denied mutation,
        // rebuild the editor, move its selection, or replace the user's text.
        void loadHistory(sceneId, "", false);
    }
    const unsubscribeOwnership = subscribeProjectOwnership(node, onProjectOwnershipChanged);
    const removed = node.onRemoved;
    node.onRemoved = function () {
        saveLocalBranchDraft();
        window.removeEventListener("pagehide", onBranchPageHide);
        const finalFlush = flushProjectWrites(runName());
        state.disposed = true;
        unsubscribeOwnership();
        state.checkpointToken += 1;
        state.presentationToken += 1;
        if (state.pollTimer != null) clearInterval(state.pollTimer);
        if (state.checkpointTimer != null) clearInterval(state.checkpointTimer);
        if (state.planNotifyTimer != null) clearTimeout(state.planNotifyTimer);
        if (state.editorialTimer != null) clearTimeout(state.editorialTimer);
        state.timelineResizeObserver?.disconnect();
        api.removeEventListener("executed", onPromptExecuted);
        api.removeEventListener("execution_start", onExecutionStart);
        api.removeEventListener("execution_success", onExecutionTerminal);
        api.removeEventListener("execution_error", onExecutionTerminal);
        api.removeEventListener("execution_interrupted", onExecutionTerminal);
        document.removeEventListener(
            "h3-lora-routes-changed", onLoRARoutesChanged);
        document.removeEventListener("keydown", onPlayerKeydown, true);
        delete node._h3PromptCompanionSetActiveScene;
        delete node._h3PromptCompanionSetScenePrompt;
        delete node._h3FlushProjectWrites;
        disposePlayer();
        void finalFlush.catch((error) => console.warn(
            "H3 Plan Studio could not flush project edits while closing:",
            error,
        ));
        return removed?.apply(this, arguments);
    };
    node._h3PromptCompanionSetActiveScene = (planNode, index) => {
        if (planNode !== state.planNode || !state.plan?.shots?.length) return false;
        // Prompt/editor synchronization changes selection, not the user's
        // horizontal timeline position.
        void selectScene(index, false, false);
        return true;
    };
    node._h3PromptCompanionSetScenePrompt = (planNode, index, text) => {
        if (planNode !== state.planNode || !state.plan?.shots?.[index]) return false;
        const liveValue = String(state.planWidget?.value ?? "");
        let livePlanParsed = false;
        try {
            const livePlan = parsePlanJson(liveValue);
            livePlanParsed = true;
            if (planHasNonPromptChanges(state.plan, livePlan)) {
                loadPlan(true);
                return true;
            }
        } catch (_error) {
            // Leave lastValue untouched so normal polling reports invalid JSON.
        }
        state.plan.shots[index].prompt = promptTextToLines(text);
        if (index === state.active && state.history.textarea
                && state.history.textarea.value !== text) {
            const textarea = state.history.textarea;
            const focused = document.activeElement === textarea;
            const start = textarea.selectionStart;
            const end = textarea.selectionEnd;
            textarea.value = text;
            if (focused) textarea.setSelectionRange(
                Math.min(start, text.length), Math.min(end, text.length));
            scheduleHistoryDraft(
                String(state.plan.shots[index].id || `clip_${String(index + 1).padStart(4, "0")}`),
                text);
        }
        if (livePlanParsed) state.lastValue = liveValue;
        return true;
    };
    node._h3PlanStudioRefresh = () => {
        loadPlan(true);
        publishActiveScene();
    };
    const saveLocalBranchDraft = () => {
        if (state.disposed || !state.plan) return;
        branches.observe();
        const status = root.querySelector(".h3studio-branch-draft");
        if (status) status.textContent = branchDraftError || branches.draftStatus;
    };
    const onBranchPageHide = () => saveLocalBranchDraft();
    window.addEventListener("pagehide", onBranchPageHide);
    root.addEventListener("input", saveLocalBranchDraft);
    root.addEventListener("change", saveLocalBranchDraft);
    state.pollTimer = setInterval(() => { loadPlan(false); saveLocalBranchDraft(); }, 500);
    state.checkpointTimer = setInterval(() => {
        if (state.executionPromptIds.size === 0) void refreshCheckpoints();
    }, 5000);
    loadPlan(true);
}

app.registerExtension({
    name:"minimax_h3_context_loop.plan_studio",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments); setTimeout(() => mount(this), 0); return result;
        };
    },
    async nodeCreated(node) { if (nodeType(node) === NODE_NAME) mount(node); },
    async afterConfigureGraph() {
        for (const node of allNodes(app.graph)) if (nodeType(node) === NODE_NAME) setTimeout(() => node._h3PlanStudioRefresh?.(), 0);
    },
});
