/** Kaksiraitainen Aikajana (video + ääni), playhead-scrubbaus ja Shift+raahaus-valinta. */

import { withLang } from "./api.js";
import { t } from "./i18n.js";
import {
  SCRUB_THROTTLE_MS,
  SELECTION_MIN_SEC,
  clampPlayhead,
  clipLayoutSpans,
  els,
  formatSeconds,
  formatTimecode,
  isActivelyScrubbing,
  state,
  totalDuration,
} from "./state.js";

export function setStillMode(on) {
  // Still vain aktiivisen scrub/valinta-raahauksen aikana
  const enabled = Boolean(on) && isActivelyScrubbing();
  state.stillMode = enabled;
  els.previewStage?.classList.toggle("is-scrubbing", enabled);
  if (els.stillFrame) {
    els.stillFrame.hidden = !enabled;
    if (!enabled) {
      els.stillFrame.removeAttribute("src");
    }
  }
}

export function showStillAt(sec) {
  if (!els.stillFrame || !isActivelyScrubbing()) return;
  const tSec = clampPlayhead(sec);
  setStillMode(true);
  els.stillFrame.src = `/api/timeline/frame?t=${encodeURIComponent(tSec.toFixed(3))}&_=${Date.now()}`;
}

export function hideStillAndSyncVideo() {
  setStillMode(false);
  if (!els.preview?.src) return;
  try {
    els.preview.currentTime = clampPlayhead(state.playheadSec);
  } catch {
    /* ignore seek errors */
  }
}

export function updatePlayheadUi(sec) {
  const value = clampPlayhead(sec);
  state.playheadSec = value;
  const total = totalDuration();
  const pct = total > 0 ? (value / total) * 100 : 0;
  if (els.scrubberPlayhead) {
    els.scrubberPlayhead.style.left = `${pct}%`;
  }
  if (els.scrubberTimecode) {
    els.scrubberTimecode.textContent = formatTimecode(value);
  }
  if (els.scrubberTrack) {
    els.scrubberTrack.setAttribute("aria-valuenow", String(Math.round(value * 10) / 10));
    els.scrubberTrack.setAttribute("aria-valuemax", String(total));
  }
}

export function postPlayhead(sec) {
  const value = clampPlayhead(sec);
  if (
    state.lastPostedPlayhead != null &&
    Math.abs(state.lastPostedPlayhead - value) < 0.01
  ) {
    return;
  }
  state.lastPostedPlayhead = value;
  fetch(withLang("/api/timeline/playhead"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sec: value }),
  }).catch(() => {});
}

export function scrubTo(sec, { immediate = false } = {}) {
  const value = clampPlayhead(sec);
  updatePlayheadUi(value);
  state.scrubPendingSec = value;

  const flush = () => {
    state.scrubThrottleTimer = null;
    const pending = state.scrubPendingSec;
    if (pending == null) return;
    showStillAt(pending);
    postPlayhead(pending);
  };

  if (immediate) {
    if (state.scrubThrottleTimer) {
      clearTimeout(state.scrubThrottleTimer);
      state.scrubThrottleTimer = null;
    }
    flush();
    return;
  }

  if (state.scrubThrottleTimer) return;
  state.scrubThrottleTimer = setTimeout(flush, SCRUB_THROTTLE_MS);
}

export function normalizeSelection(start, end) {
  if (start == null || end == null) return { start: null, end: null };
  let a = clampPlayhead(start);
  let b = clampPlayhead(end);
  if (a > b) [a, b] = [b, a];
  if (b - a < SELECTION_MIN_SEC) return { start: null, end: null };
  return { start: a, end: b };
}

export function updateSelectionUi(start, end) {
  const normalized = normalizeSelection(start, end);
  state.selectionStart = normalized.start;
  state.selectionEnd = normalized.end;
  const total = totalDuration();
  const has =
    normalized.start != null &&
    normalized.end != null &&
    total > 0;

  if (els.scrubberSelectionRange) {
    els.scrubberSelectionRange.hidden = !has;
    if (has) {
      const left = (normalized.start / total) * 100;
      const width = ((normalized.end - normalized.start) / total) * 100;
      els.scrubberSelectionRange.style.left = `${left}%`;
      els.scrubberSelectionRange.style.width = `${Math.max(width, 0.15)}%`;
    }
  }

  if (els.scrubberSelection) {
    els.scrubberSelection.hidden = !has;
    if (has) {
      const dur = (normalized.end - normalized.start).toFixed(1).replace(/\.0$/, "");
      els.scrubberSelection.textContent = t("selectionRange", {
        start: formatTimecode(normalized.start),
        end: formatTimecode(normalized.end),
        duration: dur,
      });
    }
  }

  if (els.scrubberClear) {
    els.scrubberClear.hidden = !has;
  }
  if (els.scrubberDeleteSelection) {
    els.scrubberDeleteSelection.hidden = !has;
  }
}

export function isTimelineItemSelected(type, index, id = null) {
  const sel = state.selectedTimelineItem;
  if (!sel || sel.type !== type) return false;
  if (id && sel.id) return sel.id === id;
  return sel.index === index;
}

export function updateTimelineItemSelectionUi() {
  if (!els.scrubberTrack) return;
  els.scrubberTrack
    .querySelectorAll(".scrubber-block, .scrubber-audio-block")
    .forEach((el) => {
      const type = el.dataset.clipType;
      const index = Number(el.dataset.clipIndex);
      const id = el.dataset.clipId || null;
      el.classList.toggle("is-selected", isTimelineItemSelected(type, index, id));
    });
}

export function setSelectedTimelineItem(item) {
  state.selectedTimelineItem = item || null;
  if (item && item.type === "video" && item.index >= 1) {
    state.selectedClip = item.index;
  }
  updateTimelineItemSelectionUi();
}

export function postSelection(start, end, { immediate = false } = {}) {
  const flush = () => {
    state.selectionThrottleTimer = null;
    const normalized = normalizeSelection(
      start ?? state.selectionStart,
      end ?? state.selectionEnd
    );
    const key =
      normalized.start == null
        ? "null"
        : `${normalized.start.toFixed(3)}:${normalized.end.toFixed(3)}`;
    if (state.lastPostedSelection === key) return;
    state.lastPostedSelection = key;
    fetch(withLang("/api/timeline/selection"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start_sec: normalized.start,
        end_sec: normalized.end,
      }),
    }).catch(() => {});
  };

  if (immediate) {
    if (state.selectionThrottleTimer) {
      clearTimeout(state.selectionThrottleTimer);
      state.selectionThrottleTimer = null;
    }
    flush();
    return;
  }
  if (state.selectionThrottleTimer) return;
  state.selectionThrottleTimer = setTimeout(flush, SCRUB_THROTTLE_MS);
}

export function paintSelectionTo(sec, { immediate = false, edgeSec = null } = {}) {
  const tip = clampPlayhead(sec);
  let start;
  let end;
  if (state.selectMode === "start") {
    end = state.selectionEnd ?? tip;
    start = tip;
  } else if (state.selectMode === "end") {
    start = state.selectionStart ?? tip;
    end = tip;
  } else {
    start = state.selectAnchor ?? tip;
    end = tip;
  }
  updateSelectionUi(start, end);
  const previewSec = edgeSec != null ? edgeSec : tip;
  updatePlayheadUi(previewSec);
  const flushEdge = () => {
    showStillAt(previewSec);
    postPlayhead(previewSec);
    postSelection(state.selectionStart, state.selectionEnd, { immediate: true });
  };
  if (immediate) {
    if (state.scrubThrottleTimer) {
      clearTimeout(state.scrubThrottleTimer);
      state.scrubThrottleTimer = null;
    }
    flushEdge();
    return;
  }
  state.scrubPendingSec = previewSec;
  if (state.scrubThrottleTimer) return;
  state.scrubThrottleTimer = setTimeout(() => {
    state.scrubThrottleTimer = null;
    showStillAt(state.scrubPendingSec);
    postPlayhead(state.scrubPendingSec);
    postSelection(state.selectionStart, state.selectionEnd);
  }, SCRUB_THROTTLE_MS);
}

export function clearSelectionUiAndServer() {
  updateSelectionUi(null, null);
  postSelection(null, null, { immediate: true });
}

const EDGE_SNAP_PX = 8;
const EDGE_SNAP_RATIO = 0.01;

function secFromPointerEvent(e, { snapEdges = false } = {}) {
  if (!els.scrubberTrack) return 0;
  const rect = els.scrubberTrack.getBoundingClientRect();
  const width = rect.width || 1;
  const x = Math.min(Math.max(e.clientX - rect.left, 0), width);
  const ratio = width > 0 ? x / width : 0;
  const total = totalDuration();
  if (snapEdges && total > 0) {
    if (x <= EDGE_SNAP_PX || ratio <= EDGE_SNAP_RATIO) return 0;
    if (width - x <= EDGE_SNAP_PX || ratio >= 1 - EDGE_SNAP_RATIO) return total;
  }
  return clampPlayhead(ratio * total);
}

export function renderScrubber() {
  if (!els.scrubber || !els.scrubberBlocks || !els.scrubberTicks) return;
  const clips = state.summary?.clips || [];
  const audioClips = state.summary?.audio_clips || [];
  const total = totalDuration();
  els.scrubber.hidden = clips.length === 0 || total <= 0;
  els.scrubberBlocks.innerHTML = "";
  if (els.scrubberAudioBlocks) els.scrubberAudioBlocks.innerHTML = "";
  els.scrubberTicks.innerHTML = "";
  if (!clips.length || total <= 0) {
    if (els.scrubberAudioLane) els.scrubberAudioLane.hidden = true;
    updatePlayheadUi(0);
    updateSelectionUi(null, null);
    return;
  }

  const spans = clipLayoutSpans();
  spans.forEach(({ clip, start, end }, i) => {
    const left = (start / total) * 100;
    const width = Math.max(0.4, ((end - start) / total) * 100);
    const block = document.createElement("div");
    const overlapsPrev = i > 0 && start < spans[i - 1].end - 1e-6;
    block.className =
      "scrubber-block" + (overlapsPrev ? " is-overlap" : "");
    block.dataset.clipType = "video";
    block.dataset.clipIndex = String(clip.index);
    block.dataset.clipId = clip.id || "";
    block.style.left = `${left}%`;
    block.style.width = `${width}%`;
    const label = document.createElement("span");
    label.textContent = clip.title || t("clipTitle", { n: clip.index });
    block.appendChild(label);
    els.scrubberBlocks.appendChild(block);
  });

  // Erotettu ääniraita samassa aikaskaalassa suoraan videoraidan alla
  if (els.scrubberAudioLane && els.scrubberAudioBlocks) {
    const hasAudioLane = audioClips.length > 0;
    els.scrubberAudioLane.hidden = !hasAudioLane;
    if (hasAudioLane) {
      audioClips.forEach((ac) => {
        const start = Math.max(0, Number(ac.timeline_start_sec) || 0);
        if (start >= total) return;
        const dur = Math.max(0.05, Number(ac.duration_sec) || 0.05);
        const left = (start / total) * 100;
        const width = Math.max(0.6, Math.min(100 - left, (dur / total) * 100));
        const block = document.createElement("div");
        block.className = "scrubber-audio-block";
        block.dataset.clipType = "audio";
        block.dataset.clipIndex = String(ac.index);
        block.dataset.clipId = ac.id || "";
        block.style.left = `${left}%`;
        block.style.width = `${width}%`;
        const baseLabel = ac.label || t("audioClipLabel", { n: ac.index });
        const vol = typeof ac.volume === "number" ? ac.volume : 1.0;
        const volSuffix = Math.abs(vol - 1.0) > 0.01 ? ` (${Math.round(vol * 100)}%)` : "";
        block.title = `${baseLabel}${volSuffix} · ${formatTimecode(start)} – ${formatTimecode(start + dur)} (${formatSeconds(dur)})`;
        const span = document.createElement("span");
        span.textContent = `${baseLabel}${volSuffix}`;
        block.appendChild(span);
        els.scrubberAudioBlocks.appendChild(block);
      });
    }
  }

  const step = total <= 10 ? 1 : total <= 30 ? 5 : total <= 120 ? 10 : 30;
  for (let mark = 0; mark <= total + 1e-9; mark += step) {
    const tick = document.createElement("div");
    tick.className = "scrubber-tick";
    tick.style.left = `${(mark / total) * 100}%`;
    tick.dataset.label = formatTimecode(mark);
    els.scrubberTicks.appendChild(tick);
  }

  updateTimelineItemSelectionUi();
  updatePlayheadUi(state.summary?.playhead_sec ?? state.playheadSec);
  updateSelectionUi(
    state.summary?.selection_start_sec ?? state.selectionStart,
    state.summary?.selection_end_sec ?? state.selectionEnd
  );
}

export function wireScrubberEvents({
  onSelectChange = null,
  onDeleteSelection = null,
} = {}) {
  if (els.scrubberClear) {
    els.scrubberClear.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      clearSelectionUiAndServer();
    });
  }

  if (els.scrubberDeleteSelection) {
    els.scrubberDeleteSelection.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      onDeleteSelection?.();
    });
  }

  if (!els.scrubberTrack) return;

  let pointerDownX = null;
  let pointerDownBlock = null;

  const beginSelect = (e, mode) => {
    if (!totalDuration()) return;
    e.preventDefault();
    e.stopPropagation();
    state.selecting = true;
    state.scrubbing = false;
    state.selectMode = mode;
    pointerDownX = null;
    pointerDownBlock = null;
    const tip = secFromPointerEvent(e, { snapEdges: true });
    if (mode === "paint") {
      state.selectAnchor = tip;
      updateSelectionUi(tip, tip);
    }
    els.scrubberTrack.setPointerCapture?.(e.pointerId);
    paintSelectionTo(tip, { immediate: true, edgeSec: tip });
  };

  const onPointerDown = (e) => {
    if (!totalDuration()) return;
    if (e.target === els.scrubberHandleStart) {
      beginSelect(e, "start");
      return;
    }
    if (e.target === els.scrubberHandleEnd) {
      beginSelect(e, "end");
      return;
    }
    if (e.shiftKey) {
      beginSelect(e, "paint");
      return;
    }
    e.preventDefault();
    els.scrubberTrack.focus?.();
    pointerDownX = e.clientX;
    pointerDownBlock =
      e.target?.closest?.(".scrubber-block, .scrubber-audio-block") || null;
    state.scrubbing = true;
    state.selecting = false;
    state.selectMode = null;
    els.scrubberTrack.setPointerCapture?.(e.pointerId);
    scrubTo(secFromPointerEvent(e), { immediate: true });
  };
  const onPointerMove = (e) => {
    if (state.selecting) {
      paintSelectionTo(secFromPointerEvent(e, { snapEdges: true }));
      return;
    }
    if (!state.scrubbing) return;
    scrubTo(secFromPointerEvent(e));
  };
  const onPointerUp = (e) => {
    if (state.selecting) {
      paintSelectionTo(secFromPointerEvent(e, { snapEdges: true }), { immediate: true });
      state.selecting = false;
      state.selectMode = null;
      state.selectAnchor = null;
      hideStillAndSyncVideo();
      return;
    }
    if (!state.scrubbing) return;
    const isClick =
      pointerDownX != null && Math.abs(e.clientX - pointerDownX) <= 5;
    const hitBlock = pointerDownBlock;
    pointerDownX = null;
    pointerDownBlock = null;

    scrubTo(secFromPointerEvent(e), { immediate: true });
    state.scrubbing = false;
    hideStillAndSyncVideo();

    // Pieni klikkaus valitsee klipin (tai tyhjentää valinnan tyhjältä kohdalta)
    // ilman että raahaus-scrubbaus muuttaa valintaa.
    if (isClick) {
      if (hitBlock) {
        const type = hitBlock.dataset.clipType;
        const index = Number(hitBlock.dataset.clipIndex);
        const id = hitBlock.dataset.clipId || null;
        if ((type === "video" || type === "audio") && index >= 1) {
          setSelectedTimelineItem({ type, index, id });
          onSelectChange?.(state.selectedTimelineItem);
        }
      } else if (state.selectedTimelineItem) {
        setSelectedTimelineItem(null);
        onSelectChange?.(null);
      }
    }
  };

  els.scrubberTrack.addEventListener("pointerdown", onPointerDown);
  els.scrubberTrack.addEventListener("pointermove", onPointerMove);
  els.scrubberTrack.addEventListener("pointerup", onPointerUp);
  els.scrubberTrack.addEventListener("pointercancel", onPointerUp);
  els.scrubberTrack.addEventListener("keydown", (e) => {
    const total = totalDuration();
    if (!total) return;
    if (e.key === "Escape") {
      e.preventDefault();
      setSelectedTimelineItem(null);
      clearSelectionUiAndServer();
      onSelectChange?.(null);
      return;
    }
    const step = e.shiftKey ? 1 : 0.1;
    let next = state.playheadSec;
    if (e.key === "ArrowLeft") next -= step;
    else if (e.key === "ArrowRight") next += step;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = total;
    else return;
    e.preventDefault();
    scrubTo(next, { immediate: true });
  });
}
