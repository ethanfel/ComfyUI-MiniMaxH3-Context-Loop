#!/usr/bin/env node

import assert from "node:assert/strict";
import fs from "node:fs";
import {
    locateStudioTimelineSegment,
    locateStudioTimelineSecond,
    h3StudioGridMarkers,
    matchingStudioCheckpoint,
    matchingStudioSourceAudio,
    matchingStudioSourceScene,
    parseStudioTimecode,
    parseTimedLyrics,
    remapStudioEditorialSceneId,
    restoreStudioCheckpointCache,
    studioCheckpointSignature,
    studioCheckpointCacheSnapshot,
    studioContextWindowLayout,
    studioContextWindowStartAtRatio,
    studioEditorialSceneStartSeconds,
    studioLatentSafeOutFrames,
    studioNearestLatentSafeOutFrame,
    studioNearestH3FrameLength,
    studioPlayerSegmentClock,
    studioRulerTicks,
    studioSceneStartSeconds,
    studioSourceAudioSecond,
    studioSourceSecond,
    studioTimelineLayout,
    studioTimelinePixelAtSecond,
    studioTimelineScrollAnchorSeconds,
    studioTimelineScrollLeftForAnchor,
    studioTimelineSegments,
    studioTimelineTotalSeconds,
    studioWaveformIntervalSamples,
    studioWaveformSceneSamples,
    timedLyricAtSecond,
} from "../web/h3_chain_plan_studio_core.mjs";
import {
    applySceneTransitionPreset,
    sceneTransitionPreset,
} from "../web/h3_policy_core.mjs";

const studioBoundary = {};
assert.equal(sceneTransitionPreset(studioBoundary), "inherit");
applySceneTransitionPreset(studioBoundary, "soft_av");
assert.deepEqual(studioBoundary, {
    continuation_mode: "audio_feathered_av", context_length: 39,
    audio_context_length: 39,
});

const rows = [
    {id:"one", deliveredFrames:362, deliveredSeconds:362 / 24},
    {id:"two", deliveredFrames:340, deliveredSeconds:340 / 24},
    {id:"three", deliveredFrames:340, deliveredSeconds:340 / 24},
];
assert.ok(studioLatentSafeOutFrames(362, 340).includes(72));
assert.equal(studioNearestLatentSafeOutFrame(362, 340, 71), 72);
assert.equal(studioNearestLatentSafeOutFrame(362, 340, 339), 340);
const trimmedTimeline = studioTimelineSegments([
    {id:"one", rawFrames:362, deliveredFrames:340},
    {id:"two", rawFrames:362, deliveredFrames:340},
], [], null, [{scene_id:"one", out_frame:72}]);
assert.deepEqual(trimmedTimeline.filter(
    (segment) => segment.kind === "scene",
).map((segment) => segment.durationFrames), [72, 340]);
assert.equal(trimmedTimeline.at(-1).endFrame, 412);
const beforeTrimEnd = studioPlayerSegmentClock(
    trimmedTimeline, "scene:0", 2.99,
);
assert.equal(beforeTrimEnd.boundaryReached, false);
const trimEnd = studioPlayerSegmentClock(
    trimmedTimeline, "scene:0", 3,
);
assert.equal(trimEnd.boundaryReached, true);
assert.equal(trimEnd.timelineSeconds, 3);
assert.equal(
    studioPlayerSegmentClock(trimmedTimeline, "scene:0", 99).timelineSeconds,
    3,
);
assert.equal(studioSceneStartSeconds(rows, 1), 362 / 24);
assert.equal(locateStudioTimelineSecond(rows, 0).index, 0);
assert.equal(locateStudioTimelineSecond(rows, 362 / 24).index, 1);
assert.equal(locateStudioTimelineSecond(rows, 999).index, 2);
assert.ok(Math.abs(
    locateStudioTimelineSecond(rows, 362 / 24 + 1).localSeconds - 1,
) < 1e-9);

const fittedTimeline = studioTimelineLayout(rows, 600, 1);
assert.equal(fittedTimeline.zoom, 1);
assert.ok(Math.abs(
    fittedTimeline.widths.reduce((total, value) => total + value, 0) - 600,
) < 1e-9);
assert.ok(fittedTimeline.widths[0] > fittedTimeline.widths[1]);
const expandedTimeline = studioTimelineLayout(rows, 600, 2);
assert.equal(expandedTimeline.contentWidth, 1200);
assert.ok(expandedTimeline.widths.every(
    (value, index) => value > fittedTimeline.widths[index],
));
assert.equal(studioTimelineLayout(rows, 600, .25).zoom, 1);
assert.equal(studioTimelineLayout(rows, 600, 20).zoom, 6);

const placedTimeline = studioTimelineSegments(rows, [
    {scene_id:"two", start_frame:480},
]);
assert.deepEqual(placedTimeline.map((segment) => segment.kind), [
    "scene", "gap", "scene", "scene",
]);
assert.equal(placedTimeline[1].startFrame, 362);
assert.equal(placedTimeline[1].durationFrames, 118);
assert.equal(placedTimeline[2].startFrame, 480);
assert.equal(studioEditorialSceneStartSeconds(placedTimeline, 1), 20);
assert.equal(studioTimelineTotalSeconds(placedTimeline), 1160 / 24);
assert.equal(locateStudioTimelineSegment(
    placedTimeline, 18,
).kind, "gap");
assert.equal(locateStudioTimelineSegment(
    placedTimeline, 20,
).sceneIndex, 1);
const reorderedTimeline = studioTimelineSegments(rows, [
    {scene_id:"three", start_frame:0},
]);
assert.deepEqual(reorderedTimeline.filter(
    (segment) => segment.kind === "scene",
).map((segment) => segment.sceneId), ["three", "one", "two"]);
assert.equal(reorderedTimeline[0].startFrame, 0);
assert.equal(reorderedTimeline[0].sceneIndex, 2);
assert.equal(reorderedTimeline.at(-1).sceneId, "two");
for (const count of [8, 7]) {
    const manyRows = Array.from({length:count}, (_value, index) => ({
        id:`scene_${index + 1}`,
        deliveredFrames:100,
        deliveredSeconds:100 / 24,
    }));
    const movedTerminal = studioTimelineSegments(manyRows, [{
        scene_id:`scene_${count}`, start_frame:0,
    }]).filter((segment) => segment.kind === "scene");
    assert.equal(movedTerminal[0].sceneId, `scene_${count}`);
    assert.notEqual(movedTerminal.at(-1).sceneId, `scene_${count}`);
}
const placedLayout = studioTimelineLayout(
    rows, 600, 1, [{scene_id:"two", start_frame:480}],
);
assert.equal(placedLayout.segments.length, 4);
assert.equal(placedLayout.packedSceneSeconds, 1042 / 24);
assert.equal(placedLayout.sceneEndSeconds, 1160 / 24);
assert.ok(placedLayout.contentWidth > 600);
assert.ok(Math.abs(
    placedLayout.widths.reduce((total, value) => total + value, 0)
        - placedLayout.contentWidth,
) < 1e-9);
const terminalPlacementLayout = studioTimelineLayout(
    rows, 600, 1, [{scene_id:"three", start_frame:1500}],
);
const terminalSceneIndex = terminalPlacementLayout.segments.findIndex(
    (segment) => segment.kind === "scene" && segment.sceneId === "three",
);
assert.ok(terminalPlacementLayout.widths.slice(
    0, terminalSceneIndex,
).reduce((total, value) => total + value, 0) > 600);
const openTimeline = studioTimelineLayout(rows, 600, 1, [], 2042);
assert.equal(openTimeline.sceneEndSeconds, 1042 / 24);
assert.equal(openTimeline.totalSeconds, 2042 / 24);
assert.equal(openTimeline.contentWidth, 600 * 2042 / 1042);
assert.equal(openTimeline.segments.at(-1).key, "gap:tail");
assert.equal(openTimeline.segments.at(-1).trailing, true);
assert.ok(Math.abs(
    openTimeline.widths.slice(0, 3).reduce((total, value) => total + value, 0)
        - 600,
) < 1e-9);
assert.ok(Math.abs(studioTimelinePixelAtSecond(
    1042 / 24, openTimeline.pixelsPerSecond, openTimeline.contentWidth,
) - 600) < 1e-9);
assert.ok(Math.abs(studioTimelinePixelAtSecond(
    openTimeline.totalSeconds,
    openTimeline.pixelsPerSecond,
    openTimeline.contentWidth,
) - openTimeline.contentWidth) < 1e-9);
assert.equal(studioTimelinePixelAtSecond(
    9999, openTimeline.pixelsPerSecond, openTimeline.contentWidth,
), openTimeline.contentWidth);
assert.equal(locateStudioTimelineSegment(
    openTimeline.segments, 70,
).key, "gap:tail");
assert.equal(studioTimelineScrollAnchorSeconds(
    1200, 600, 2400, 120, .5,
), 75);
assert.equal(studioTimelineScrollLeftForAnchor(
    75, 600, 20, .5,
), 1200);
const stableWorkspaceAnchor = studioTimelineScrollAnchorSeconds(
    1200, 600, 2400, 120, .5,
);
assert.equal(studioTimelineScrollLeftForAnchor(
    stableWorkspaceAnchor, 600, 20, .5,
), 1200);
assert.equal(studioNearestH3FrameLength(345), 345);
assert.equal(studioNearestH3FrameLength(354), 362);
assert.equal(studioNearestH3FrameLength(6, 23), 39);
assert.equal(parseStudioTimecode("90.5"), 90.5);
assert.equal(parseStudioTimecode("15.000s"), 15);
assert.equal(parseStudioTimecode("1:30.5"), 90.5);
assert.equal(parseStudioTimecode("1:02:03"), 3723);
assert.throws(() => parseStudioTimecode("1:bad"));
assert.ok(studioRulerTicks(90, 900).some((tick) => tick.major));

const lrcCues = parseTimedLyrics(
    "[00:01.5]First line\n[00:03.25]Second line",
);
assert.equal(lrcCues[0].startSeconds, 1.5);
assert.equal(lrcCues[0].endSeconds, 3.25);
assert.equal(timedLyricAtSecond(lrcCues, 2)?.text, "First line");
assert.equal(timedLyricAtSecond(lrcCues, 4)?.text, "Second line");
const srtCues = parseTimedLyrics(
    "1\n00:00:02,000 --> 00:00:04,500\nA subtitle\n",
);
assert.deepEqual(srtCues, [{
    startSeconds:2, endSeconds:4.5, text:"A subtitle",
}]);

const contextWindow = studioContextWindowLayout(340, 39, 100);
assert.deepEqual(contextWindow, {
    delivered:340, span:39, latest:301, start:100, end:139,
    leftFraction:100 / 340, widthFraction:39 / 340,
});
assert.equal(studioContextWindowLayout(340, 39, 999).start, 301);
assert.equal(studioContextWindowStartAtRatio(340, 39, 0), 0);
assert.equal(studioContextWindowStartAtRatio(340, 39, .5), 151);
assert.equal(studioContextWindowStartAtRatio(340, 39, 1), 301);

const checkpoints = new Map([[1, {
    scene:1, scene_id:"one", ready:true, delivered_frames:362,
    video:{filename:"one.mp4"}, audio:{filename:"one.wav"},
}]]);
assert.equal(matchingStudioCheckpoint(checkpoints, 0, rows[0]).scene_id, "one");
assert.equal(matchingStudioCheckpoint(checkpoints, 0, {...rows[0], id:"renamed"}), null);
assert.equal(matchingStudioCheckpoint(checkpoints, 0, {...rows[0], deliveredFrames:340}), null);
assert.notEqual(
    studioCheckpointSignature("run-a", [...checkpoints.values()]),
    studioCheckpointSignature("run-b", [...checkpoints.values()]),
);
const cache = studioCheckpointCacheSnapshot(
    "run-a", [...checkpoints.values()], {
        run_name:"run-a", placements:[], trims:[{scene_id:"one", out_frame:72}],
    },
);
assert.equal(restoreStudioCheckpointCache(cache, "run-a").checkpoints.length, 1);
assert.equal(restoreStudioCheckpointCache(cache, "run-b"), null);
const editorialRename = {
    placements:[{scene_id:"one", start_frame:2}],
    trims:[{scene_id:"one", out_frame:72}],
    locked_scene_ids:["one"],
    alternate_draft:{scene_id:"one"},
    replacements:[{scene_id:"one"}],
};
remapStudioEditorialSceneId(editorialRename, "one", "opening");
assert.equal(editorialRename.placements[0].scene_id, "opening");
assert.equal(editorialRename.trims[0].scene_id, "opening");
assert.deepEqual(editorialRename.locked_scene_ids, ["opening"]);
assert.equal(editorialRename.alternate_draft.scene_id, "opening");
assert.equal(editorialRename.replacements[0].scene_id, "opening");
assert.notEqual(
    studioCheckpointSignature("run-a", [...checkpoints.values()]),
    studioCheckpointSignature("run-a", [{
        ...checkpoints.get(1), audio:{filename:"changed.wav"},
    }]),
);

const sourceTimeline = {token:"opaque", source_audio:{
    available:true, frame_count:1042, seek_seconds:2,
    duration_seconds:1042 / 24, available_frame_count:2000,
    available_duration_seconds:2000 / 24,
}, scenes:[{
    scene:2, scene_id:"two", delivered_frames:340,
    references:[{frame_count:362, compare_offset_frames:22}],
}]};
assert.equal(
    matchingStudioSourceScene(sourceTimeline, 1, rows[1]).scene_id, "two",
);
assert.equal(matchingStudioSourceScene(sourceTimeline, 0, rows[0]), null);
assert.equal(
    matchingStudioSourceScene(sourceTimeline, 1, {...rows[1], deliveredFrames:339}),
    null,
);
assert.ok(Math.abs(studioSourceSecond(
    sourceTimeline.scenes[0].references[0], 1,
) - (22 / 24 + 1)) < 1e-9);
assert.equal(
    matchingStudioSourceAudio(sourceTimeline, rows).frame_count, 1042,
);
assert.equal(
    matchingStudioSourceAudio(
        sourceTimeline, [{...rows[0], deliveredFrames:361}, ...rows.slice(1)],
    ),
    sourceTimeline.source_audio,
);
assert.equal(matchingStudioSourceAudio(sourceTimeline, rows, "other-run"), null);
assert.equal(matchingStudioSourceAudio({...sourceTimeline, token:""}, rows), null);
assert.equal(matchingStudioSourceAudio({
    ...sourceTimeline, source_audio:{available:false},
}, rows), null);
assert.equal(matchingStudioSourceAudio(sourceTimeline, []), null);
assert.ok(Math.abs(
    studioSourceAudioSecond(sourceTimeline.source_audio, 3) - 5,
) < 1e-9);
assert.deepEqual(
    studioWaveformSceneSamples(
        {points_per_second:2, samples:Array.from({length:90}, (_value, index) => index)},
        rows, 1,
    ).slice(0, 2),
    [30, 31],
);
assert.deepEqual(
    studioWaveformIntervalSamples(
        {points_per_second:2, samples:Array.from({length:90}, (_value, index) => index)},
        2.5, 1.5,
    ),
    [5, 6, 7],
);

const exactGrid = h3StudioGridMarkers(345, 39, "masked_av");
assert.deepEqual(exactGrid.raw, {
    frames:345, onGrid:true, index:20, label:"345f = 17×20+5",
});
assert.equal(exactGrid.av.exact, true);
assert.equal(exactGrid.av.audioTicks, 65);
assert.deepEqual(exactGrid.cut, {
    start:337, end:340, experimental:true, label:"cut test 337–340f",
});
const fractionalGrid = h3StudioGridMarkers(362, 22, "feathered_av");
assert.equal(fractionalGrid.raw.onGrid, true);
assert.equal(fractionalGrid.av.exact, false);
assert.equal(fractionalGrid.av.label, "22f AV = 36.667 audio ticks");
const fiveFrameVideoOnlyGrid = h3StudioGridMarkers(
    345, 5, "masked_av", false,
);
assert.equal(fiveFrameVideoOnlyGrid.av.exact, true);
assert.equal(fiveFrameVideoOnlyGrid.av.audioAligned, false);
assert.equal(fiveFrameVideoOnlyGrid.av.audioPreserved, false);
assert.equal(fiveFrameVideoOnlyGrid.av.label, "5f video-only AV");
const audioFeatherGrid = h3StudioGridMarkers(345, 39, "audio_feathered_av");
assert.equal(audioFeatherGrid.av.exact, true);
assert.equal(audioFeatherGrid.av.audioTicks, 65);
const detailAvGrid = h3StudioGridMarkers(345, 39, "tapered_av");
assert.equal(detailAvGrid.av.exact, true);
assert.equal(detailAvGrid.av.audioTicks, 65);
const driftAvGrid = h3StudioGridMarkers(345, 39, "drift_control_av");
assert.equal(driftAvGrid.av.exact, true);
assert.equal(driftAvGrid.av.audioTicks, 65);
assert.equal(h3StudioGridMarkers(344, 39, "guide").raw.onGrid, false);
assert.equal(h3StudioGridMarkers(344, 39, "guide").av, null);

const source = fs.readFileSync(
    new URL("../web/h3_chain_plan_studio.js", import.meta.url),
    "utf8",
);

assert.match(source, /MiniMaxH3ChainPlanStudio/);
assert.match(source, /MiniMaxH3ChainPlan/);
assert.match(source, /item\.name === name/);
assert.match(source, /state\.planWidget\.value = value/);
assert.match(source, /h3studio-timeline/);
assert.match(source, /h3studio-chapter-marker/);
assert.match(source, /\+ Chapter/);
assert.match(source, /function renderChapterPanel/);
assert.match(source, /chapter settings and notes/);
assert.match(source, /Chapter notes remain editorial only/);
assert.match(source, /Editorial context, lyrics, LLM notes/);
assert.match(source, /minimax_h3_context_loop\/editorial/);
assert.match(source, /scheduleEditorialSave/);
assert.match(source, /scheduleEditorialSave\(0\)/);
assert.match(source, /Choosing presentation media is never a generation command/);
assert.match(source, /Always synchronize the hidden one-shot queue widget on load/);
assert.match(source, /TIMELINE_ZOOM_PROPERTY/);
assert.match(source, /studioTimelineLayout/);
assert.match(source, /Fit timeline/);
assert.match(source, /Ctrl\/Cmd \+ wheel/);
assert.match(source, /Scene prompt/);
assert.match(source, /Shared prompt/);
assert.match(source, /Plan settings/);
assert.match(source, /\["context","Context"\]/);
assert.match(source, /renderContextPanel/);
assert.match(source, /Tail \(default\)/);
assert.match(source, /Start at playhead/);
assert.match(source, /Play selection/);
assert.match(source, /h3studio-context-window/);
assert.match(source, /studioContextWindowStartAtRatio/);
assert.match(source, /nativeContextWindowStarts/);
assert.match(source, /native latent crop/);
assert.match(source, /h3studio-context-phase-tail/);
assert.match(source, /phaseTailFrames = Math\.max\(0, latest - defaultStart\)/);
assert.match(source, /final \$\{phaseTailFrames\}f use another phase/);
assert.match(source, /change the composed split to use that physical tail without RGB\/VAE re-encoding/);
assert.match(source, /visual_context_start_frame/);
assert.match(source, /visual_context_lead_start_frame/);
assert.match(source, /field\("Picture context total", visualTotal\)/);
assert.match(source, /field\("Picture blocks", blockCount\)/);
assert.match(source, /field\(`Division \$\{cutOffset \+ 1\}`, select\)/);
assert.match(source, /Ordered repartition:/);
assert.match(source, /visualContextDefaultPartition/);
assert.match(source, /visualContextPartitionFromBoundaries/);
assert.match(source, /Multiple blocks may select the same scene/);
assert.match(source, /Unlock audio context/);
assert.match(source, /Lock audio context/);
assert.match(source, /renderAudioContextPanel/);
assert.match(source, /audio_context_unlocked/);
assert.match(source, /audio_context_lead_source/);
assert.match(source, /audioContextWindowStarts/);
assert.match(source, /Standalone mode · this node owns, validates, and outputs/);
assert.match(source, /MODERN_PLAN_NAME = "MiniMaxH3ChainPlanModern"/);
assert.match(source, /changes are written to the.*Modern Plan.*H3 Chain Plan/);
assert.match(source, /modernPlan = owner\?\.type === MODERN_PLAN_NAME/);
assert.match(source, /Visual transition, context length, audio behavior, and continuation are owned by the connected Generation Profile/);
assert.match(source, /if \(modernPlan\).*panel\.append\(grid\);/s);
assert.match(source, /const planOwner = planNode \?\? node/);
assert.match(source, /mirrorConnectedPlan\(planNode\)/);
assert.match(source, /state\.planOwner = planOwner/);
assert.match(source, /writePlanSetting\("base_seed", parsed\.toString\(\)\)/);
assert.match(source, /field\("Run name"/);
assert.match(source, /field\("Generation fingerprint"/);
assert.match(source, /inputConnected\(owner, "project_assets"\)/);
assert.match(source, /reference-derived generation fingerprint are managed by connected Project Assets/);
assert.match(source, /disconnect Project Assets to edit them/);
assert.equal((source.match(/field\("Default seconds"/g) ?? []).length, 1);
assert.equal((source.match(/field\("Default steps"/g) ?? []).length, 1);
assert.doesNotMatch(source, /blank = Plan widget/);
assert.match(source, /field\("Context encoding"/);
assert.match(source, /field\("Continuation implementation"/);
assert.match(source, /Generated playback/);
assert.match(source, /MOTION REF/);
assert.match(source, /plan-studio\/source-preview/);
assert.match(source, /plan-studio\/source-audio/);
assert.match(source, /plan-studio\/source-waveform/);
assert.match(source, /plan-studio\/presentation/);
assert.match(source, /plan-studio\/checkpoint-thumbnail/);
assert.match(source, /function refreshTimelineCheckpoints/);
assert.match(source, /h3studio-card-thumbnail/);
assert.match(source, /image\.loading = "lazy"/);
assert.doesNotMatch(
    source,
    /const preview = checkpoint\?\.preview_video[\s\S]{0,800}element\("video"\)/,
);
assert.match(source, /SOURCE AUDIO/);
assert.match(source, /SOURCE_AUDIO_MUTES_PROPERTY/);
assert.match(source, /studioSourceAudioSecond/);
assert.match(source, /studioWaveformIntervalSamples/);
assert.match(source, /Editorial start/);
assert.match(source, /Latent-safe used end/);
assert.match(source, /studioNearestLatentSafeOutFrame/);
assert.match(source, /full sampled checkpoint retained/);
assert.match(source, /Black editorial gap/);
assert.match(source, /OPEN TIMELINE/);
assert.match(source, /extendTimelineWorkspace/);
assert.match(source, /scrollLeft:Math\.max\(0, Number\(viewport\.scrollLeft\)/);
assert.match(source, /const preservedLeft = preserveScroll/);
assert.match(source, /const preservedScroll = restoreScroll \?\? timelineScrollSnapshot\(\)/);
assert.doesNotMatch(source, /anchor \* layout\.contentWidth/);
assert.match(source, /cancelAnimationFrame\(state\.timelineLayoutFrame\)/);
assert.match(source, /const revealTimelineActive = false/);
assert.match(source, /state\.timelineLastScrollLeft = targetLeft/);
assert.match(source, /selectScene\(index, false, false\)/);
assert.match(source, /timelineScrollIntentUntil/);
assert.match(source, /const movingRight = currentScrollLeft/);
assert.match(source, /locked_scene_ids/);
assert.match(source, /h3studio-resize-handle/);
assert.match(source, /17n\+5 frame grid/);
assert.match(source, /Unlock all/);
assert.match(source, /Unlock scene/);
assert.doesNotMatch(source, /requestedStart > previousEnd/);
assert.doesNotMatch(source, /meaningfulPlacements/);
assert.match(source, /for \(const timelineSegment of state\.timelineSegments\)/);
assert.match(source, /card\.addEventListener\("pointerdown", startDrag\)/);
assert.match(source, /window\.addEventListener\("pointermove", onMove, true\)/);
assert.match(source, /Drag the clip or its grip/);
assert.match(source, /timelineScrollSnapshot/);
assert.match(source, /restoreScroll:timelineScroll/);
assert.match(source, /renderTimeline\(\{revealActive = false/);
assert.match(source, /h3studio-lock-icon/);
assert.match(source, /"⋮⋮"/);
assert.doesNotMatch(source, /locked \? "🔒" : "🔓"/);
assert.match(source, /SUBTITLES/);
assert.match(source, /Source Timeline connected · no audio/);
assert.match(source, /No active path-backed motion reference in this Plan/);
assert.match(source, /state\.sourceLayer\.hidden = !hasMotion/);
assert.match(source, /h3studio-audio-generated/);
assert.match(source, /h3studio-audio-source/);
assert.match(source, /GENERATED_VOLUME_PROPERTY/);
assert.match(source, /h3studio-audio-volume/);
assert.match(source, /primeNextSegment/);
assert.match(source, /h3studio-handoff-frame/);
assert.match(source, /let standbyVideo = preloadVideo/);
assert.match(source, /stage\.insertBefore\(preloadVideo, handoffFrame\)/);
assert.match(source, /const promotePrimedSegment = \(index\) =>/);
assert.match(source, /standbyVideo\.readyState >= HTMLMediaElement\.HAVE_CURRENT_DATA/);
assert.match(source, /video\.ended && upcoming\?\.kind === "scene"/);
assert.match(source, /state\.playerPreloadVideo = standbyVideo/);
assert.match(source, /onActiveVideo\("ended"/);
assert.match(source, /upcomingSegment\?\.kind === "scene"/);
assert.match(source, /upcomingSegment\.sceneIndex/);
assert.match(source, /event\.code !== "Space"/);
assert.match(source, /const pausePlayerMonitors = \(\) =>/);
assert.match(source, /state\.togglePlayerPlayback/);
assert.match(source, /const playPlayerTransport = \(\) =>/);
assert.match(source, /sourceTimelineAudio\.addEventListener\("timeupdate"/);
assert.match(source, /autoplay && !generated/);
assert.match(source, /state\.sourceAudioPlayer/);
assert.doesNotMatch(source, /const handingOff = video\.ended/);
assert.match(source, /document\.removeEventListener\("keydown", onPlayerKeydown/);
assert.match(source, /Generated and Source Track can play together/);
assert.match(source, /Adjacent saved scenes are pre-decoded/);
assert.doesNotMatch(source, /h3studio-audio-choice/);
assert.match(source, /h3_plan_studio_source_timeline/);
assert.match(source, /\/minimax_h3_context_loop\/checkpoints/);
assert.match(source, /include_graph:"false"/);
assert.match(source, /state\.checkpointPromise/);
assert.match(source, /state\.checkpointRefreshQueued/);
assert.match(source, /executionPromptIds:new Set\(\)/);
assert.match(source, /api\.addEventListener\("execution_start", onExecutionStart\)/);
assert.match(source, /api\.addEventListener\("execution_success", onExecutionTerminal\)/);
assert.match(source, /api\.addEventListener\("execution_error", onExecutionTerminal\)/);
assert.match(source, /api\.addEventListener\("execution_interrupted", onExecutionTerminal\)/);
assert.match(source, /state\.executionPromptIds\.size === 0\) void refreshCheckpoints\(\)/);
assert.match(source, /state\.executionPromptIds\.delete\(promptId\)[\s\S]*state\.executionPromptIds\.size !== 0[\s\S]*void refreshCheckpoints\(\)/);
assert.match(source, /api\.removeEventListener\("execution_start", onExecutionStart\)/);
assert.match(source, /api\.removeEventListener\("execution_success", onExecutionTerminal\)/);
assert.match(source, /\/minimax_h3_context_loop\/prompt-history/);
assert.match(source, /promptRevisionNavigation/);
assert.match(source, /availableReferenceRecords/);
assert.match(source, /state\.planNode \?\? node/);
assert.match(source, /preview_video/);
assert.match(source, /item\.preview_video \? null : \(item\.audio \?\? null\)/);
assert.match(source, /playerAudio/);
assert.match(source, /synchronizeGeneratedAudio/);
assert.match(source, /Source Track playback supplies the timeline clock/);
assert.match(source, /currentSettings === state\.lastSettingsSignature/);
assert.match(source, /state\.timelinePosition = target/);
assert.match(source, /studioTimelinePixelAtSecond/);
assert.match(source, /startMediaClock\("video"\)/);
assert.match(source, /startMediaClock\("source"\)/);
assert.match(source, /requestAnimationFrame while media is playing/);
assert.match(source, /state\.view !== "player"/);
assert.match(source, /renderSourceTimeline\(\); renderSourceAudioTimeline\(\)/);
assert.match(source, /updateTimelineSelection\(\)/);
assert.match(source, /h3_chain_active_scene/);
assert.match(source, /api\.removeEventListener\("executed", onPromptExecuted\)/);
assert.match(source, /renderShell\(\)/);
assert.match(source, /serialize:false/);
assert.match(source, /connectedPromptEditors/);
assert.match(source, /Prompt editing delegated to/);
assert.match(source, /preserveDelegatedPrompts\(\)/);
assert.match(source, /convertTaggedPictureReference/);
assert.match(source, /taggedPictureReferenceMode/);
assert.match(source, /h3studio-ref-mode/);
assert.match(source, /Use untimed Qwen-only #tag/);
assert.match(source, /publishCompanionScene/);
assert.match(source, /Append a new scene and select it/);
assert.match(source, /state\.plan\.shots\.push\(makeShot\(state\.plan\.shots\)\)/);
assert.match(source, /state\.active = state\.plan\.shots\.length - 1/);
assert.match(source, /field\("Incoming transition", incomingTransition\)/);
assert.match(source, /field\("Prompt alternatives", promptSeedWrap\)/);
assert.match(source, /Stable derived/);
assert.doesNotMatch(source, /Inherit Plan seed/);
assert.match(source, /Randomize each queue/);
assert.match(source, /setScenePromptSeedMode/);
assert.match(source, /field\("Final assembly crossfade frames", blendFrames\)/);
assert.match(source, /field\("Source reference", sourceReference\)/);
assert.match(source, /field\("Generated continuity", generatedContinuity\)/);
assert.match(source, /field\("Lock source audio", lockSourceAudio\)/);
assert.match(source, /applySceneAudioOverride/);
assert.match(source, /field\("LoRA route", loraRoute\)/);
assert.match(source, /MiniMax H3 Scene LoRA Scheduler/);
assert.match(source, /availableLoRARoutes/);
assert.match(source, /h3-lora-routes-changed/);
assert.match(source, /row\.loraRoute/);
assert.doesNotMatch(source, /Advanced boundary controls/);
assert.doesNotMatch(source, /ADVANCED_BOUNDARY_OPEN_PROPERTY/);
assert.match(source, /field\("Boundary implementation", implementation\)/);
assert.match(source, /applySceneTransitionPreset/);
assert.match(source, /field\("Boundary spatial proxy", spatialProxyControl\)/);
assert.match(source, /Low-grid 5\/6 proxy · Guide/);
assert.match(source, /Latent 5\/6 proxy · AV/);
assert.match(source, /context_spatial_proxy/);
assert.match(source, /field\("Audio context total", audioTotal\)/);
assert.match(source, /audio_context_length/);
assert.match(source, /video_blend_frames/);
assert.match(source, /\["guide", "Guide"\]/);
assert.match(source, /\["latent_guide", "Latent Guide"\]/);
assert.match(source, /\["tapered_guide", "Detail Guide"\]/);
assert.match(source, /\["masked_av", "Masked AV"\]/);
assert.match(source, /\["feathered_av", "Feathered AV"\]/);
assert.doesNotMatch(source, /Feathered AV \+ RGB/);
assert.match(source, /17n\+5 temporal latent grid/);
assert.match(source, /Exact aligned choices are 39, 90, 141, 192/);
assert.match(source, /Experimental only: nearest reported four-frame 17n−3 cut window/);

assert.notEqual(
    studioCheckpointSignature("run", [{scene:1, revision:"old", video:"shared.mp4"}]),
    studioCheckpointSignature("run", [{scene:1, revision:"new", video:"shared.mp4"}]),
    "metadata-only attribution must invalidate the cached checkpoint map");
console.log("H3 Plan Studio: separate timeline editor contract passes");
