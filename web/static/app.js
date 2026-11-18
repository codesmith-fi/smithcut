const TRANSITION_LABELS = {
  none: "Ei siirtymää",
  fadeblack: "Pimennys",
  fade: "Häivytys",
  wipeleft: "Pyyhkäisy ←",
  wiperight: "Pyyhkäisy →",
};

const state = {
  summary: null,
  selectedClip: 1,
  busy: false,
  previewToken: 0,
};

const els = {
  status: document.getElementById("status"),
  undo: document.getElementById("btn-undo"),
  redo: document.getElementById("btn-redo"),
  exportBtn: document.getElementById("btn-export"),
  preview: document.getElementById("preview"),
  previewStage: document.getElementById("preview-stage"),
  previewEmpty: document.getElementById("preview-empty"),
  zoomToast: document.getElementById("zoom-toast"),
  dropzone: document.getElementById("dropzone"),
  fileInput: document.getElementById("file-input"),
  libraryMeta: document.getElementById("library-meta"),
  clipStrip: document.getElementById("clip-strip"),
  stripEmpty: document.getElementById("strip-empty"),
  timelineDuration: document.getElementById("timeline-duration"),
  commandForm: document.getElementById("command-form"),
  commandInput: document.getElementById("command-input"),
  commandReply: document.getElementById("command-reply"),
  textModal: document.getElementById("text-modal"),
  textForm: document.getElementById("text-form"),
  textValue: document.getElementById("text-value"),
  textPosition: document.getElementById("text-position"),
  textCancel: document.getElementById("text-cancel"),
};

function setStatus(message, busy = false) {
  state.busy = busy;
  els.status.textContent = message;
  els.status.classList.toggle("is-busy", busy);
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || data.detail || `Virhe (${res.status})`);
  }
  return data;
}

function formatSeconds(sec) {
  const n = Number(sec) || 0;
  if (n < 60) return `${n.toFixed(1).replace(/\.0$/, "")} s`;
  const m = Math.floor(n / 60);
  const s = (n % 60).toFixed(1).replace(/\.0$/, "");
  return `${m} min ${s} s`;
}

function transitionForClip(afterClip1Based) {
  const list = state.summary?.transitions || [];
  return list.find((t) => t.after_clip === afterClip1Based) || null;
}

function applyState(summary) {
  state.summary = summary;
  if (!summary.clips?.length) {
    state.selectedClip = 1;
  } else if (state.selectedClip > summary.clips.length) {
    state.selectedClip = summary.clips.length;
  }

  els.undo.disabled = !summary.can_undo;
  els.redo.disabled = !summary.can_redo;
  els.timelineDuration.textContent = `Kesto: ${formatSeconds(summary.total_duration_sec)}`;

  const sourceCount = summary.sources?.length || 0;
  const music = summary.bgm ? " · taustamusiikki valittu" : "";
  els.libraryMeta.textContent =
    sourceCount === 0
      ? "Ei vielä materiaalia"
      : `${sourceCount} video${sourceCount === 1 ? "" : "a"}${music}`;

  renderClipStrip();
  updatePreview(summary.preview_url);
}

function updatePreview(url) {
  const hasPreview = Boolean(url);
  els.previewStage.classList.toggle("is-empty", !hasPreview);
  if (!hasPreview) {
    els.preview.removeAttribute("src");
    els.preview.load();
    return;
  }
  const next = `${url}?t=${Date.now()}`;
  const currentBase = (els.preview.currentSrc || els.preview.src || "").split("?")[0];
  const nextBase = next.split("?")[0];
  const resume = els.preview.currentTime || 0;
  const wasPaused = els.preview.paused;
  if (currentBase === nextBase && els.preview.src) {
    // Sama tiedosto, pakota uudelleenlataus cache-bustilla
  }
  els.preview.src = next;
  els.preview.load();
  els.preview.addEventListener(
    "loadedmetadata",
    () => {
      try {
        els.preview.currentTime = Math.min(resume, els.preview.duration || resume);
      } catch {
        /* ignore seek errors */
      }
      if (!wasPaused) {
        els.preview.play().catch(() => {});
      }
    },
    { once: true }
  );
}

function renderClipStrip() {
  const clips = state.summary?.clips || [];
  els.clipStrip.innerHTML = "";
  if (!clips.length) {
    const empty = document.createElement("p");
    empty.className = "strip-empty";
    empty.id = "strip-empty";
    empty.textContent = "Klipit ilmestyvät tähän tuonnin jälkeen";
    els.clipStrip.appendChild(empty);
    return;
  }

  clips.forEach((clip, idx) => {
    const card = document.createElement("article");
    card.className = "clip-card" + (clip.index === state.selectedClip ? " is-selected" : "");
    card.draggable = true;
    card.dataset.index = String(clip.index);

    const effects = (clip.effects || [])
      .map((e) => {
        if (e.type === "zoom") return `<span class="tag zoom">Zoom</span>`;
        if (e.type === "text") return `<span class="tag text">Teksti</span>`;
        return "";
      })
      .join("");

    card.innerHTML = `
      <h3>Klippi ${clip.index}</h3>
      <div class="duration">${formatSeconds(clip.duration_sec)}</div>
      <div class="effect-tags">${effects || '<span class="tag">Ei tehosteita</span>'}</div>
      <div class="clip-actions">
        <button type="button" data-act="split">Jaa</button>
        <button type="button" class="danger" data-act="remove">Poista</button>
      </div>
    `;

    card.addEventListener("click", (ev) => {
      if (ev.target.closest("button")) return;
      state.selectedClip = clip.index;
      renderClipStrip();
    });

    card.querySelector('[data-act="remove"]').addEventListener("click", async (ev) => {
      ev.stopPropagation();
      await mutate(() =>
        api("/api/clips", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action: "remove", clip_index: clip.index }),
        })
      );
    });

    card.querySelector('[data-act="split"]').addEventListener("click", async (ev) => {
      ev.stopPropagation();
      const at = Math.max(0.1, (clip.duration_sec || 1) / 2);
      await mutate(() =>
        api("/api/clips", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action: "split",
            clip_index: clip.index,
            split_at_sec: at,
          }),
        })
      );
    });

    card.addEventListener("dragstart", (ev) => {
      card.classList.add("is-dragging");
      ev.dataTransfer.setData("text/plain", String(clip.index));
      ev.dataTransfer.effectAllowed = "move";
    });
    card.addEventListener("dragend", () => card.classList.remove("is-dragging"));
    card.addEventListener("dragover", (ev) => {
      ev.preventDefault();
      ev.dataTransfer.dropEffect = "move";
    });
    card.addEventListener("drop", async (ev) => {
      ev.preventDefault();
      const from = Number(ev.dataTransfer.getData("text/plain"));
      const to = clip.index;
      if (!from || from === to) return;
      await mutate(() =>
        api("/api/clips", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action: "move",
            from_index: from,
            to_index: to,
          }),
        })
      );
    });

    els.clipStrip.appendChild(card);

    if (idx < clips.length - 1) {
      const slot = document.createElement("div");
      slot.className = "transition-slot";
      const select = document.createElement("select");
      select.setAttribute("aria-label", `Siirtymä klippien ${clip.index} ja ${clip.index + 1} välillä`);
      const current = transitionForClip(clip.index);
      Object.entries(TRANSITION_LABELS).forEach(([value, label]) => {
        const opt = document.createElement("option");
        opt.value = value;
        opt.textContent = label;
        if (
          (value === "none" && !current) ||
          (current && current.type === value)
        ) {
          opt.selected = true;
        }
        select.appendChild(opt);
      });
      select.addEventListener("change", async () => {
        if (select.value === "none") {
          // Poista siirtymä asettamalla uudelleen vain jos tarvitaan – ei erillistä API:a.
          // Käytetään lyhyttä hard-cutia: ei kutsuta set_transition.
          setStatus("Siirtymä poistetaan seuraavassa vietossa kovaleikkauksena");
          return;
        }
        await mutate(() =>
          api("/api/effects", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              action: "transition",
              after_clip: clip.index,
              transition_type: select.value,
              duration: 0.4,
            }),
          })
        );
      });
      slot.appendChild(select);
      els.clipStrip.appendChild(slot);
    }
  });
}

async function refreshState() {
  const data = await api("/api/state");
  applyState(data.state);
}

async function mutate(fn, { renderPreview = true } = {}) {
  try {
    setStatus("Päivitetään…", true);
    const data = await fn();
    if (data.state) applyState(data.state);
    else await refreshState();
    if (renderPreview && (state.summary?.clips?.length || 0) > 0) {
      await runRender("preview");
    } else {
      setStatus("Valmis");
    }
    return data;
  } catch (err) {
    setStatus("Valmis");
    showReply(err.message || "Toiminto epäonnistui");
    throw err;
  }
}

async function runRender(mode) {
  setStatus(mode === "final" ? "Luodaan valmista videota…" : "Päivitetään esikatselua…", true);
  try {
    const data = await api("/api/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    if (data.state) applyState(data.state);
    if (data.url) updatePreview(data.url);
    setStatus(mode === "final" ? "Valmis video luotu" : "Valmis");
    if (mode === "final") {
      showReply("Valmis video on valmis – voit avata sen esikatselusta tai workspace-kansiosta.");
    }
    return data;
  } catch (err) {
    setStatus("Valmis");
    showReply(err.message || "Renderöinti epäonnistui");
    throw err;
  }
}

function showReply(text) {
  els.commandReply.hidden = false;
  els.commandReply.textContent = text;
}

async function uploadFile(file) {
  const form = new FormData();
  form.append("file", file);
  form.append("kind", "auto");
  await mutate(() =>
    api("/api/upload", {
      method: "POST",
      body: form,
    })
  );
}

function mapTimeToClip(timeSec) {
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
    const tr = transitions.find((t) => t.after_clip === clip.index);
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

async function addZoomAtClick(ev) {
  if (!state.summary?.clips?.length || !els.preview.src) return;
  // Älä kaappaile kontrollipalkin klikkejä
  const rect = els.preview.getBoundingClientRect();
  if (ev.clientY > rect.bottom - 48) return;

  const x = (ev.clientX - rect.left) / rect.width;
  const y = (ev.clientY - rect.top) / rect.height;
  if (x < 0 || x > 1 || y < 0 || y > 1) return;

  const mapped = mapTimeToClip(els.preview.currentTime || 0);
  if (!mapped) return;

  const duration = Math.min(1.5, Math.max(0.4, mapped.clip.duration_sec * 0.35));
  const start = Math.min(
    mapped.localSec,
    Math.max(0, mapped.clip.duration_sec - duration)
  );

  els.zoomToast.hidden = false;
  setTimeout(() => {
    els.zoomToast.hidden = true;
  }, 1200);

  await mutate(() =>
    api("/api/effects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: "zoom",
        clip_index: mapped.clipIndex,
        start_sec: start,
        duration,
        target_x: Math.min(1, Math.max(0, x)),
        target_y: Math.min(1, Math.max(0, y)),
        zoom_factor: 1.8,
      }),
    })
  );
}

async function handleQuick(action) {
  const clips = state.summary?.clips || [];
  if (!clips.length) {
    showReply("Tuo ensin video, jotta pikakomento toimii.");
    return;
  }
  const clipIndex = state.selectedClip;

  if (action === "zoom-center") {
    await mutate(() =>
      api("/api/effects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "zoom",
          clip_index: clipIndex,
          start_sec: 0,
          duration: Math.min(1.5, clips[clipIndex - 1].duration_sec),
          region: "center",
          zoom_factor: 1.8,
        }),
      })
    );
    return;
  }

  if (action === "text") {
    els.textValue.value = "";
    els.textModal.showModal();
    return;
  }

  if (action === "fade") {
    if (clips.length < 2) {
      showReply("Tarvitset vähintään kaksi klippiä siirtymään.");
      return;
    }
    const after = Math.min(clipIndex, clips.length - 1);
    await mutate(() =>
      api("/api/effects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "transition",
          after_clip: after,
          transition_type: "fade",
          duration: 0.4,
        }),
      })
    );
    return;
  }

  if (action === "split") {
    const clip = clips.find((c) => c.index === clipIndex) || clips[0];
    await mutate(() =>
      api("/api/clips", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "split",
          clip_index: clip.index,
          split_at_sec: Math.max(0.1, clip.duration_sec / 2),
        }),
      })
    );
  }
}

function wireEvents() {
  els.undo.addEventListener("click", () =>
    mutate(() => api("/api/undo", { method: "POST" }))
  );
  els.redo.addEventListener("click", () =>
    mutate(() => api("/api/redo", { method: "POST" }))
  );
  els.exportBtn.addEventListener("click", () => runRender("final"));

  els.fileInput.addEventListener("change", async () => {
    const file = els.fileInput.files?.[0];
    if (file) await uploadFile(file);
    els.fileInput.value = "";
  });

  ["dragenter", "dragover"].forEach((evt) => {
    els.dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      els.dropzone.classList.add("is-dragover");
    });
  });
  ["dragleave", "drop"].forEach((evt) => {
    els.dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      els.dropzone.classList.remove("is-dragover");
    });
  });
  els.dropzone.addEventListener("drop", async (e) => {
    const file = e.dataTransfer?.files?.[0];
    if (file) await uploadFile(file);
  });
  els.dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      els.fileInput.click();
    }
  });

  els.preview.addEventListener("click", (e) => {
    addZoomAtClick(e).catch(() => {});
  });

  document.querySelectorAll("[data-quick]").forEach((btn) => {
    btn.addEventListener("click", () => {
      handleQuick(btn.dataset.quick).catch(() => {});
    });
  });

  els.commandForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = els.commandInput.value.trim();
    if (!message) return;
    try {
      setStatus("Käsitellään komentoa…", true);
      const data = await api("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      showReply(data.reply || "Valmis");
      if (data.state) applyState(data.state);
      els.commandInput.value = "";
      setStatus("Valmis");
    } catch (err) {
      setStatus("Valmis");
      showReply(err.message);
    }
  });

  els.textCancel.addEventListener("click", () => els.textModal.close());
  els.textForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = els.textValue.value.trim();
    if (!text) return;
    els.textModal.close();
    await mutate(() =>
      api("/api/effects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "text",
          clip_index: state.selectedClip,
          text,
          start_sec: 0,
          duration: 2,
          position: els.textPosition.value,
        }),
      })
    );
  });
}

wireEvents();
refreshState()
  .then(() => setStatus("Valmis"))
  .catch((err) => {
    setStatus("Valmis");
    showReply(err.message || "Tilaa ei voitu ladata");
  });
