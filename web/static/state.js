/** Jaettu käyttöliittymätila, DOM-viitteet ja aikajanan apufunktiot. */

import { t } from "./i18n.js";

export const SCRUB_THROTTLE_MS = 100;
export const SELECTION_MIN_SEC = 0.05;

export const state = {
  summary: null,
  selectedClip: 1,
  busy: false,
  pollTimer: null,
  playheadSec: 0,
  scrubbing: false,
  stillMode: false,
  scrubThrottleTimer: null,
  scrubPendingSec: null,
  lastPostedPlayhead: null,
  selectionStart: null,
  selectionEnd: null,
  selecting: false,
  selectAnchor: null,
  selectMode: null, // "paint" | "start" | "end"
  selectionThrottleTimer: null,
  lastPostedSelection: null,
  lastReply: null,
  _skipPreviewUpdate: false,
};

export const els = {
  status: document.getElementById("status"),
  undo: document.getElementById("btn-undo"),
  redo: document.getElementById("btn-redo"),
  newProject: document.getElementById("btn-new-project"),
  exportBtn: document.getElementById("btn-export"),
  preview: document.getElementById("preview"),
  stillFrame: document.getElementById("still-frame"),
  previewStage: document.getElementById("preview-stage"),
  previewEmpty: document.getElementById("preview-empty"),
  zoomToast: document.getElementById("zoom-toast"),
  scrubber: document.getElementById("scrubber"),
  scrubberTrack: document.getElementById("scrubber-track"),
  scrubberBlocks: document.getElementById("scrubber-blocks"),
  scrubberAudioLane: document.getElementById("scrubber-audio-lane"),
  scrubberAudioBlocks: document.getElementById("scrubber-audio-blocks"),
  scrubberTicks: document.getElementById("scrubber-ticks"),
  scrubberPlayhead: document.getElementById("scrubber-playhead"),
  scrubberTimecode: document.getElementById("scrubber-timecode"),
  scrubberSelection: document.getElementById("scrubber-selection"),
  scrubberSelectionRange: document.getElementById("scrubber-selection-range"),
  scrubberClear: document.getElementById("scrubber-clear"),
  scrubberHandleStart: document.getElementById("scrubber-handle-start"),
  scrubberHandleEnd: document.getElementById("scrubber-handle-end"),
  dropzone: document.getElementById("dropzone"),
  fileInput: document.getElementById("file-input"),
  libraryMeta: document.getElementById("library-meta"),
  audioPanel: document.getElementById("audio-panel"),
  audioStatus: document.getElementById("audio-status"),
  audioTrackList: document.getElementById("audio-track-list"),
  clipStrip: document.getElementById("clip-strip"),
  audioTimeline: document.getElementById("audio-timeline"),
  audioTimelineMeta: document.getElementById("audio-timeline-meta"),
  audioClipList: document.getElementById("audio-clip-list"),
  timelineDuration: document.getElementById("timeline-duration"),
  commandForm: document.getElementById("command-form"),
  commandInput: document.getElementById("command-input"),
  commandReply: document.getElementById("command-reply"),
  textModal: document.getElementById("text-modal"),
  textForm: document.getElementById("text-form"),
  textValue: document.getElementById("text-value"),
  textPosition: document.getElementById("text-position"),
  textFontStyle: document.getElementById("text-font-style"),
  textCancel: document.getElementById("text-cancel"),
  progress: document.getElementById("topbar-progress"),
  progressBar: document.getElementById("topbar-progress-bar"),
};

export function formatSeconds(sec) {
  const n = Number(sec) || 0;
  if (n < 60) {
    return t("seconds", { n: n.toFixed(1).replace(/\.0$/, "") });
  }
  const m = Math.floor(n / 60);
  const s = (n % 60).toFixed(1).replace(/\.0$/, "");
  return t("minutesSeconds", { m, s });
}

export function formatTimecode(sec) {
  const n = Math.max(0, Number(sec) || 0);
  const m = Math.floor(n / 60);
  const s = n - m * 60;
  return `${String(m).padStart(2, "0")}:${s.toFixed(1).padStart(4, "0")}`;
}

export function totalDuration() {
  return Math.max(0, Number(state.summary?.total_duration_sec) || 0);
}

export function clampPlayhead(sec) {
  const total = totalDuration();
  if (total <= 0) return 0;
  return Math.min(total, Math.max(0, Number(sec) || 0));
}

export function isActivelyScrubbing() {
  return Boolean(state.scrubbing || state.selecting);
}

export function clipLayoutSpans() {
  const clips = state.summary?.clips || [];
  const transitions = state.summary?.transitions || [];
  const spans = [];
  let cursor = 0;
  clips.forEach((clip, i) => {
    const dur = Number(clip.duration_sec) || 0;
    const start =
      clip.timeline_start_sec != null ? Number(clip.timeline_start_sec) : cursor;
    const end =
      clip.timeline_end_sec != null ? Number(clip.timeline_end_sec) : start + dur;
    spans.push({ clip, start, end, index: clip.index });
    cursor = end;
    const tr = transitions.find((item) => item.after_clip === clip.index);
    if (tr && i < clips.length - 1) {
      cursor -= Number(tr.duration_sec) || 0;
    }
  });
  return spans;
}

export function transitionForClip(afterClip1Based) {
  const list = state.summary?.transitions || [];
  return list.find((item) => item.after_clip === afterClip1Based) || null;
}

export function resolvePreviewUrl(summary) {
  if (!summary) return null;
  if (summary.preview_url) return summary.preview_url;
  const clipUrl = summary.clips?.find((c) => c.source_url)?.source_url;
  if (clipUrl) return clipUrl;
  return summary.sources?.find((s) => s.url)?.url || null;
}

export function mapTimeToClip(timeSec) {
  const clips = state.summary?.clips || [];
  let cursor = 0;
  const transitions = state.summary?.transitions || [];
  for (let i = 0; i < clips.length; i++) {
    const clip = clips[i];
    const start = cursor;
    const end = cursor + clip.duration_sec;
    if (timeSec >= start && timeSec <= end + 1e-6) {
      return {
        clipIndex: clip.index,
        localSec: Math.max(0, timeSec - start),
        clip,
      };
    }
    cursor = end;
    const tr = transitions.find((item) => item.after_clip === clip.index);
    if (tr && i < clips.length - 1) {
      cursor -= tr.duration_sec;
    }
  }
  if (clips.length) {
    const last = clips[clips.length - 1];
    return { clipIndex: last.index, localSec: 0, clip: last };
  }
  return null;
}
