/** CutSmith UI translations (fi / en). */

export const SUPPORTED_LANGS = ["fi", "en"];
export const STORAGE_KEY = "cutsmith_lang";

const dictionaries = {
  fi: {
    ready: "Valmis",
    updating: "Päivitetään…",
    updatingPreview: "Päivitetään esikatselua…",
    updatingPreviewPct: "Päivitetään esikatselua… {percent} %",
    creatingFinal: "Renderöidään valmista videota…",
    creatingFinalPct: "Renderöidään valmista videota… {percent} %",
    processingCommand: "Käsitellään komentoa…",
    processingCommandQwen: "Käsitellään komentoa (Qwen3)…",
    finalReady: "Valmis video luotu",
    finalReadyReply:
      "Valmis video on valmis – voit avata sen esikatselusta tai workspace-kansiosta.",
    undo: "Kumoa",
    redo: "Tee uudelleen",
    newProject: "Uusi projekti",
    newProjectConfirm:
      "Haluatko varmasti tyhjentää nykyisen projektin ja esikatselut?",
    projectReset: "Projekti nollattu",
    export: "Vie valmis video",
    previewAria: "Esikatselu",
    previewEmptyTitle: "Tuo video aloittaaksesi",
    previewEmptyHint: "Raahaa tiedosto tähän tai käytä Tuo tiedosto -painiketta",
    zoomToast: "Zoomaus lisätään tähän kohtaan",
    previewHintLabel: "Vinkki:",
    previewHint: "klikkaa esikatselua zoomataksesi valittuun kohtaan. Voit myös raahata tiedoston tähän.",
    scrubberLabel: "Aikajana",
    scrubberAria: "Aikajanan markkeri",
    scrubberHint: "Shift+raahaus maalaa alueen AI-muokkauksia varten",
    clearSelection: "Tyhjennä valinta",
    selectionRange: "{start} – {end} ({duration} s)",
    libraryAria: "Mediapankki ja klippinauha",
    dropzoneAria: "Raahaa videoita tai musiikkia tähän",
    dropzoneTitle: "Raahaa videoita tai taustamusiikkia tähän",
    dropzoneOr: "tai",
    importFile: "Tuo tiedosto",
    noMaterial: "Ei vielä materiaalia",
    libraryCount: "{count} video",
    libraryCountPlural: "{count} videota",
    musicSelected: " · taustamusiikki valittu",
    audioTracksTitle: "Ääniraidat",
    audioTrackLabel: "Ääniraita {n}",
    audioTrackActive: "liitetty aikajanalle",
    audioTrackIdle: "materiaalilistalla",
    audioTrackExtracted: "irrotettu videosta",
    audioTrackImported: "tuotu",
    videoAudioMuted: "Videon oma ääni mykistetty",
    videoAudioOn: "Videon oma ääni käytössä",
    audioAttached: " · aktiivinen: Ääniraita {n}",
    timelineTitle: "Klippinauha",
    duration: "Kesto: {time}",
    timelineFades: " · fade in {in} s / fade out {out} s",
    timelineFadeIn: " · fade in {in} s",
    timelineFadeOut: " · fade out {out} s",
    stripEmpty: "Klipit ilmestyvät tähän tuonnin jälkeen",
    clipTitle: "Klippi {n}",
    noEffects: "Ei tehosteita",
    effectZoom: "Zoom",
    effectText: "Teksti",
    effectBlur: "Blur",
    split: "Jaa",
    remove: "Poista",
    transitionAria: "Siirtymä klippien {a} ja {b} välillä",
    transitionNone: "Ei siirtymää",
    transitionFadeblack: "Pimennys",
    transitionFade: "Häivytys",
    transitionWipeleft: "Pyyhkäisy ←",
    transitionWiperight: "Pyyhkäisy →",
    transitionClearedHint: "Siirtymä poistetaan seuraavassa vietossa kovaleikkauksena",
    quickZoom: "Lisää zoom",
    quickText: "Lisää teksti",
    quickFade: "Häivytys siirtymäksi",
    quickSplit: "Jaa klippi",
    quickDetachAudio: "Erota ääni aikajanalle",
    audioTimelineTitle: "Ääniraita",
    audioTimelineMeta: "{count} ääniklippiä",
    audioClipLabel: "A{n}",
    commandLabel: "Komento",
    commandPlaceholder:
      'Kokeile: "Lisää zoom klippiin 1" tai "Lisää teksti Tervetuloa"',
    send: "Lähetä",
    textModalTitle: "Lisää teksti",
    textLabel: "Teksti",
    textPlaceholder: "Esim. Tervetuloa",
    positionLabel: "Sijainti",
    positionBottom: "Alareuna",
    positionCenter: "Keskellä",
    positionTop: "Yläreuna",
    fontStyleLabel: "Fonttityyli",
    fontClean: "Siisti",
    fontCinema: "Cinema",
    fontMono: "Mono",
    fontImpact: "Impact",
    cancel: "Peruuta",
    add: "Lisää",
    needVideoFirst: "Tuo ensin video, jotta pikakomento toimii.",
    needTwoClips: "Tarvitset vähintään kaksi klippiä siirtymään.",
    actionFailed: "Toiminto epäonnistui",
    renderFailed: "Renderöinti epäonnistui",
    stateLoadFailed: "Tilaa ei voitu ladata",
    errorStatus: "Virhe ({status})",
    langFi: "FI",
    langEn: "EN",
    langSwitcherAria: "Kielen valinta",
    seconds: "{n} s",
    minutesSeconds: "{m} min {s} s",
  },
  en: {
    ready: "Ready",
    updating: "Updating…",
    updatingPreview: "Updating preview…",
    updatingPreviewPct: "Updating preview… {percent}%",
    creatingFinal: "Rendering final video…",
    creatingFinalPct: "Rendering final video… {percent}%",
    processingCommand: "Processing command…",
    processingCommandQwen: "Processing command (Qwen3)…",
    finalReady: "Final video created",
    finalReadyReply:
      "Your final video is ready — open it from the preview or the workspace folder.",
    undo: "Undo",
    redo: "Redo",
    newProject: "New project",
    newProjectConfirm:
      "Clear the current project and previews?",
    projectReset: "Project reset",
    export: "Export final video",
    previewAria: "Preview",
    previewEmptyTitle: "Import a video to get started",
    previewEmptyHint: "Drop a file here or use the Import file button",
    zoomToast: "Zoom will be added at this spot",
    previewHintLabel: "Tip:",
    previewHint: "click the preview to zoom into a point. You can also drop files here.",
    scrubberLabel: "Timeline",
    scrubberAria: "Timeline playhead",
    scrubberHint: "Shift+drag to paint a range for AI edits",
    clearSelection: "Clear selection",
    selectionRange: "{start} – {end} ({duration} s)",
    libraryAria: "Media library and clip strip",
    dropzoneAria: "Drop videos or music here",
    dropzoneTitle: "Drop videos or background music here",
    dropzoneOr: "or",
    importFile: "Import file",
    noMaterial: "No media yet",
    libraryCount: "{count} video",
    libraryCountPlural: "{count} videos",
    musicSelected: " · background music selected",
    audioTracksTitle: "Audio tracks",
    audioTrackLabel: "Audio track {n}",
    audioTrackActive: "attached to timeline",
    audioTrackIdle: "in media pool",
    audioTrackExtracted: "extracted from video",
    audioTrackImported: "imported",
    videoAudioMuted: "Video audio muted",
    videoAudioOn: "Video audio on",
    audioAttached: " · active: Audio track {n}",
    timelineTitle: "Clip strip",
    duration: "Duration: {time}",
    timelineFades: " · fade in {in} s / fade out {out} s",
    timelineFadeIn: " · fade in {in} s",
    timelineFadeOut: " · fade out {out} s",
    stripEmpty: "Clips will appear here after import",
    clipTitle: "Clip {n}",
    noEffects: "No effects",
    effectZoom: "Zoom",
    effectText: "Text",
    effectBlur: "Blur",
    split: "Split",
    remove: "Remove",
    transitionAria: "Transition between clips {a} and {b}",
    transitionNone: "No transition",
    transitionFadeblack: "Fade to black",
    transitionFade: "Fade",
    transitionWipeleft: "Wipe left",
    transitionWiperight: "Wipe right",
    transitionClearedHint: "Transition will become a hard cut on the next export",
    quickZoom: "Add zoom",
    quickText: "Add text",
    quickFade: "Fade transition",
    quickSplit: "Split clip",
    quickDetachAudio: "Detach audio to timeline",
    audioTimelineTitle: "Audio timeline",
    audioTimelineMeta: "{count} audio clip(s)",
    audioClipLabel: "A{n}",
    commandLabel: "Command",
    commandPlaceholder:
      'Try: "Add zoom to clip 1" or "Add text Welcome"',
    send: "Send",
    textModalTitle: "Add text",
    textLabel: "Text",
    textPlaceholder: "e.g. Welcome",
    positionLabel: "Position",
    positionBottom: "Bottom",
    positionCenter: "Center",
    positionTop: "Top",
    fontStyleLabel: "Font style",
    fontClean: "Clean",
    fontCinema: "Cinema",
    fontMono: "Mono",
    fontImpact: "Impact",
    cancel: "Cancel",
    add: "Add",
    needVideoFirst: "Import a video first so quick actions work.",
    needTwoClips: "You need at least two clips for a transition.",
    actionFailed: "Action failed",
    renderFailed: "Render failed",
    stateLoadFailed: "Could not load state",
    errorStatus: "Error ({status})",
    langFi: "FI",
    langEn: "EN",
    langSwitcherAria: "Language",
    seconds: "{n} s",
    minutesSeconds: "{m} min {s} s",
  },
};

let currentLang = "fi";

export function normalizeLang(lang) {
  const value = String(lang || "").toLowerCase();
  return SUPPORTED_LANGS.includes(value) ? value : "fi";
}

export function getLang() {
  return currentLang;
}

export function loadStoredLang() {
  try {
    return normalizeLang(localStorage.getItem(STORAGE_KEY));
  } catch {
    return "fi";
  }
}

export function setLang(lang) {
  currentLang = normalizeLang(lang);
  try {
    localStorage.setItem(STORAGE_KEY, currentLang);
  } catch {
    /* ignore quota / private mode */
  }
  return currentLang;
}

export function t(key, vars = {}) {
  const table = dictionaries[currentLang] || dictionaries.fi;
  let text = table[key] ?? dictionaries.fi[key] ?? key;
  Object.entries(vars).forEach(([name, value]) => {
    text = text.replaceAll(`{${name}}`, String(value));
  });
  return text;
}

export function transitionLabels() {
  return {
    none: t("transitionNone"),
    fadeblack: t("transitionFadeblack"),
    fade: t("transitionFade"),
    wipeleft: t("transitionWipeleft"),
    wiperight: t("transitionWiperight"),
  };
}

/** Apply data-i18n / data-i18n-placeholder / data-i18n-aria / data-i18n-title on the page. */
export function applyDomTranslations(root = document) {
  root.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    if (key) el.textContent = t(key);
  });
  root.querySelectorAll("[data-i18n-html]").forEach((el) => {
    const key = el.getAttribute("data-i18n-html");
    if (key) el.innerHTML = t(key);
  });
  root.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    const key = el.getAttribute("data-i18n-placeholder");
    if (key) el.setAttribute("placeholder", t(key));
  });
  root.querySelectorAll("[data-i18n-aria]").forEach((el) => {
    const key = el.getAttribute("data-i18n-aria");
    if (key) el.setAttribute("aria-label", t(key));
  });
  root.querySelectorAll("[data-i18n-title]").forEach((el) => {
    const key = el.getAttribute("data-i18n-title");
    if (key) el.setAttribute("title", t(key));
  });
  document.documentElement.lang = currentLang;
}
