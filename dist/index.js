const manifest = { name: "Screen Recorder" };
const API_VERSION = 2;
const internalAPIConnection = window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit;
if (!internalAPIConnection) {
    throw new Error("[Screen Recorder] Decky Loader API is not available");
}
let api;
try {
    api = internalAPIConnection.connect(API_VERSION, manifest.name);
}
catch {
    api = internalAPIConnection.connect(1, manifest.name);
}
const callable = api.callable;
const getState = callable("get_state");
const clearError = callable("clear_error");
const setMethod = callable("set_method");
const setQuality = callable("set_quality");
const setCustomMbps = callable("set_custom_mbps");
// The slider moves at once; the backend hears the value once it stops moving.
// Until the backend has stored it, states from polls (or from saving an earlier
// value) still carry the old number and would make the slider jump back, so the
// value being set wins over theirs.
const SLIDER_SAVE_MS = 300;
let sliderTimer = null;
let pendingMbps = null;

function withPendingMbps(next) {
    if (pendingMbps === null || !next?.prefs) return next;
    const kbps = pendingMbps * 1000;
    return {
        ...next,
        prefs: { ...next.prefs, custom_mbps: pendingMbps },
        preview_kbps: next.prefs.quality === "custom" ? { 800: kbps, 1080: kbps } : next.preview_kbps,
    };
}
const setFps = callable("set_fps");
const setKmsOriginalColor = callable("set_kms_original_color");
const setColorLabelset = callable("set_color_labelset");
const setMic = callable("set_mic");
const setMicMode = callable("set_mic_mode");
const setMicMono = callable("set_mic_mono");
const startRecording = callable("start");
const stopRecording = callable("stop");
const startComparison = callable("compare");
const setOutputDir = callable("set_output_dir");
// Decky's folder picker (FileSelectionType.FOLDER); missing on old Decky versions.
const FOLDER_PICKER = 1;

const h = SP_REACT.createElement;
const { useState, useEffect, useCallback } = SP_REACT;

const POLL_MS = 1000;
const TICK_MS = 250;
const WATCH_MS = 2000;
const RECORDING_COLOR = "#ff6b6b";
const IDLE_COLOR = "#9aa4ae";
const ERROR_COLOR = "#ff7a7a";
const NOTE = { fontSize: "12px", lineHeight: "16px", color: "#b8bcbf", whiteSpace: "pre-line" };
const FILE_NAME = { fontSize: "12px", lineHeight: "16px", color: "#d6dade", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" };
const CARD = {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    minHeight: "48px",
    padding: "10px 14px",
    borderRadius: "8px",
    boxSizing: "border-box",
};
const DOT = { width: "10px", height: "10px", borderRadius: "50%", flexShrink: 0 };

const LANG = (() => {
    const locale = window.LocalizationManager?.m_rgLocalesToUse?.[0] || navigator.language || "en";
    return String(locale).toLowerCase().startsWith("ko") ? "ko" : "en";
})();

const STRINGS = {
    en: {
        "status.loading": "Loading…",
        "status.idle": "Ready",
        "status.starting": "Starting…",
        "status.recording.short": "Recording",
        "status.saving": "Saving…",
        "action.start": "Start Recording",
        "action.stop": "Stop and Save",
        "section.settings": "Settings",
        "section.saved": "Saved",
        "method": "Capture Method",
        "method.kms": "KMS",
        "method.pw": "PipeWire",
        "method.kms.note": "Everything on screen, including MangoHud\nDisplay color settings are applied to the recording",
        "method.pw.note": "Game, Steam UI, Quick Access menu and notifications",
        "original.label": "Record Original Colors",
        "original.note": "While recording, turns off mura correction, sets color vibrancy to Native, uses the native color temperature and turns off night mode. The screen looks more vivid and panel unevenness may show",
        "original.note.pale": "While recording, sets color vibrancy to Native, uses the native color temperature and turns off night mode. The screen looks paler meanwhile",
        "original.note.vivid": "While recording, sets color vibrancy to Native, uses the native color temperature and turns off night mode. The screen looks more vivid meanwhile",
        "original.note.plain": "While recording, sets color vibrancy to Native, uses the native color temperature and turns off night mode",
        "hdr.plain": "uncalibrated",
        "hdr.external": "external display",
        "hdr.external_sdr": "external HDR display",
        "quality": "Quality",
        "preset_low": "Low",
        "preset_medium": "Medium",
        "preset_default": "High",
        "preset_ultra": "Ultra",
        "bitrate": "{rate} Mbps at 800p · {rate1080} Mbps at 1080p",
        "preset_custom": "Custom",
        "bitrate.custom": "Bit Rate",
        "bitrate.custom.note": "{rate} Mbps at every resolution",
        "mic": "Record Microphone",
        "mic.mono": "Record in Mono",
        "mic.mono.note": "Combines the microphone channels into one, heard equally on both sides",
        "mic.separate": "Save as Separate Track",
        "mic.note.mix": "Mixed into the game audio as one track",
        "mic.note.separate": "Saved as a second audio track",
        "active": "{width}x{height} · {rate} Mbps",
        "fps": "Frame Rate",
        "fps.note": "30 FPS lowers the load while recording. The bit rate stays the same",
        "free": "{gb} GB free",
        "last": "Last recording",
        "folder.change": "Change Save Folder",
        "folder.default": "Use Default Folder",
        "err.output_invalid": "Can't save to that folder",
        "section.tools": "Tools",
        "compare": "Compare PipeWire and KMS",
        "compare.note": "Records the same screen with PipeWire, then with KMS, 5 seconds each. Closes this menu first",
        "compare.step": "Comparison {index}/{steps} · {method}",
        "pattern": "Show Color Test Pattern",
        "pattern.note": "Full screen over the Steam UI. Tap the screen or wait 60 seconds to close it. Not visible while a game is running",
        "err.start_failed": "Could not start recording",
        "err.kms_no_frames": "KMS capture got no picture from this display. Try PipeWire.",
        "err.capture_stopped": "Recording stopped unexpectedly. The part captured so far was saved.",
        "err.save_failed": "Could not save the recording",
        "err.audio_stopped": "Game audio recording stopped. The video continues without it.",
        "err.mic_stopped": "Microphone recording stopped. The video continues without it.",
        "err.mic_missing": "No microphone was found. Recording without it.",
        "dismiss": "Dismiss",
        "toast.saved": "Recording saved",
        "toast.failed": "Recording problem",
        "toast.compared": "Comparison saved",
        "toast.compared.body": "{count} recordings saved",
    },
    ko: {
        "status.loading": "불러오는 중…",
        "status.idle": "대기 중",
        "status.starting": "시작하는 중…",
        "status.recording.short": "녹화 중",
        "status.saving": "저장하는 중…",
        "action.start": "녹화 시작",
        "action.stop": "중지하고 저장",
        "section.settings": "설정",
        "section.saved": "저장 위치",
        "method": "캡처 방식",
        "method.kms": "KMS",
        "method.pw": "PipeWire",
        "method.kms.note": "MangoHud까지 화면 전체 녹화\n디스플레이 색상 설정이 녹화에 반영됩니다",
        "method.pw.note": "게임, Steam UI, 빠른 메뉴, 알림 녹화",
        "original.label": "원본 색으로 녹화",
        "original.note": "녹화 중에만 무라 보정을 끄고 채도를 Native로, 색온도를 기본으로 바꾸며 야간 모드를 끕니다. 그동안 화면 색이 진해지고 패널 얼룩이 보일 수 있습니다",
        "original.note.pale": "녹화 중에만 채도를 Native로, 색온도를 기본으로 바꾸며 야간 모드를 끕니다. 그동안 화면 색이 흐려 보입니다",
        "original.note.vivid": "녹화 중에만 채도를 Native로, 색온도를 기본으로 바꾸며 야간 모드를 끕니다. 그동안 화면 색이 진해 보입니다",
        "original.note.plain": "녹화 중에만 채도를 Native로, 색온도를 기본으로 바꾸며 야간 모드를 끕니다",
        "hdr.plain": "보정 없음",
        "hdr.external": "외부 화면",
        "hdr.external_sdr": "외부 HDR 화면",
        "quality": "화질",
        "preset_low": "낮음",
        "preset_medium": "중간",
        "preset_default": "높음",
        "preset_ultra": "가장 높음",
        "bitrate": "800p {rate} Mbps · 1080p {rate1080} Mbps",
        "preset_custom": "직접 설정",
        "bitrate.custom": "비트레이트",
        "bitrate.custom.note": "모든 해상도에서 {rate} Mbps",
        "mic": "마이크 녹음",
        "mic.mono": "모노로 녹음",
        "mic.mono.note": "마이크 채널을 하나로 합쳐 양쪽에서 똑같이 들리게 합니다",
        "mic.separate": "별도 트랙으로 저장",
        "mic.note.mix": "게임 소리와 섞어 한 트랙으로 저장합니다",
        "mic.note.separate": "두 번째 오디오 트랙으로 저장합니다",
        "active": "{width}x{height} · {rate} Mbps",
        "fps": "프레임 레이트",
        "fps.note": "30 FPS는 녹화 부담이 줄어듭니다. 비트레이트는 같습니다",
        "free": "남은 공간 {gb}GB",
        "last": "최근 녹화",
        "folder.change": "저장 위치 변경",
        "folder.default": "기본 위치로 되돌리기",
        "err.output_invalid": "그 폴더에는 저장할 수 없습니다",
        "section.tools": "도구",
        "compare": "PipeWire·KMS 비교 녹화",
        "compare.note": "같은 화면을 PipeWire, KMS 순서로 5초씩 녹화합니다. 먼저 이 메뉴를 닫습니다",
        "compare.step": "비교 녹화 {index}/{steps} · {method}",
        "pattern": "색상 테스트 패턴 표시",
        "pattern.note": "Steam 화면 위에 전체 화면으로 표시합니다. 화면을 탭하거나 60초가 지나면 닫힙니다. 게임 실행 중에는 보이지 않습니다",
        "err.start_failed": "녹화를 시작하지 못했습니다",
        "err.kms_no_frames": "KMS 캡처가 이 화면의 영상을 받지 못했습니다. PipeWire로 녹화해 보세요.",
        "err.capture_stopped": "녹화가 예기치 않게 중단되었습니다. 그때까지 녹화된 부분은 저장했습니다.",
        "err.save_failed": "녹화를 저장하지 못했습니다",
        "err.audio_stopped": "게임 소리 녹음이 중단되었습니다. 영상은 계속 녹화합니다.",
        "err.mic_stopped": "마이크 녹음이 중단되었습니다. 영상은 계속 녹화합니다.",
        "err.mic_missing": "마이크를 찾지 못했습니다. 마이크 없이 녹화합니다.",
        "dismiss": "닫기",
        "toast.saved": "녹화 저장 완료",
        "toast.failed": "녹화 문제 발생",
        "toast.compared": "비교 녹화 저장 완료",
        "toast.compared.body": "녹화 {count}개 저장",
    },
};

function t(key, values) {
    let text = STRINGS[LANG][key] ?? STRINGS.en[key] ?? key;
    for (const [name, value] of Object.entries(values || {})) {
        text = text.replace(`{${name}}`, String(value));
    }
    return text;
}

function formatTime(seconds) {
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    const pad = (value) => String(value).padStart(2, "0");
    return hours ? `${hours}:${pad(minutes % 60)}:${pad(seconds % 60)}` : `${pad(minutes)}:${pad(seconds % 60)}`;
}

function mbps(kbps) {
    return String(Math.round(kbps / 100) / 10);
}

function basename(path) {
    return String(path).split("/").pop();
}

function Note({ color, children }) {
    return h("div", { style: { ...NOTE, ...(color ? { color } : {}) } }, children);
}

function OptionNote({ divider, children }) {
    return h("div", { style: { ...NOTE, ...(divider ? { paddingBottom: "12px", borderBottom: "1px solid rgba(255, 255, 255, 0.12)" } : {}) } }, children);
}

function Chevron({ up }) {
    return h("svg", { viewBox: "0 0 24 24", width: 18, height: 18, style: { display: "block" } },
        h("path", { d: up ? "M6 15l6-6 6 6" : "M6 9l6 6 6-6", fill: "none", stroke: "currentColor",
            strokeWidth: 2.5, strokeLinecap: "round", strokeLinejoin: "round" }));
}

function Row(child) {
    return h(DFL.PanelSectionRow, null, child);
}

// Information that the d-pad can land on: Steam only moves (and scrolls) between
// focusable items, so plain text below the last control could only be reached by
// touch.
function InfoRow(props, ...children) {
    return Row(h(DFL.Field, {
        focusable: true,
        childrenLayout: "below",
        childrenContainerWidth: "max",
        bottomSeparator: "none",
        ...props,
    }, ...children));
}

// Steam scrolls to a newly focused item smoothly unless it is far away, so after a
// few quick presses the scrolling trails the focus and hides it. Steam Fields also
// scroll themselves into view (smoothly) after any handler below them, so the
// panel takes over all focus scrolling with "force" and decides what to show:
// the row that holds the focus, the whole saved info group, or, for the record
// button, the top of the panel with the status card.
const SCROLL_TOP_CLASS = "screen-recorder-scroll-top";

function nodeElement(node) {
    return node?.Element ?? node?.m_element;
}

// The element that scrolls the Quick Access panel.
function findScroller(element) {
    const view = element?.ownerDocument?.defaultView;
    for (let node = element?.parentElement; node && view; node = node.parentElement) {
        const overflow = view.getComputedStyle(node).overflowY;
        if ((overflow === "auto" || overflow === "scroll") && node.scrollHeight > node.clientHeight) return node;
    }
    return null;
}

function scrollPanelTo(focused, previous, panel) {
    const scroller = findScroller(nodeElement(focused));
    // Show the largest navigation node around the focus that still fits on screen:
    // a row's Field or a group, never a whole section taller than the panel.
    let target = focused;
    for (let node = focused.Parent; node && node !== panel; node = node.Parent) {
        const element = nodeElement(node);
        if (!element) continue;
        if (element.classList?.contains(SCROLL_TOP_CLASS)) {
            if (scroller) {
                scroller.scrollTop = 0;
                return true;
            }
            target = node;
            break;
        }
        if (scroller && element.getBoundingClientRect().height > scroller.clientHeight) break;
        target = node;
    }
    nodeElement(target)?.scrollIntoView?.({ behavior: "auto", block: "nearest" });
    return true;
}

// Decky's plugin list and a plugin's page share the Quick Access tab's scroller,
// so the page opened as far down as the list had been scrolled to reach it
// (measured 2026-11-19: 78 px, the status card half hidden).
let panelTop = null;
const PANEL_TOP_RESET_MS = [0, 60, 300];

function resetPanelScroll() {
    const timers = PANEL_TOP_RESET_MS.map((delay) => setTimeout(() => {
        // Leave it if Steam has put focus on a row below the status card.
        const focused = panelTop?.parentElement?.querySelector(".gpfocus");
        if (focused && !focused.closest(`.${SCROLL_TOP_CLASS}`)) return;
        const scroller = findScroller(panelTop);
        if (scroller) scroller.scrollTop = 0;
    }, delay));
    return () => timers.forEach(clearTimeout);
}

function ScrollPanel(...children) {
    return h(DFL.Focusable, {
        "flow-children": "column",
        scrollIntoViewWhenChildFocused: "force",
        fnScrollIntoViewHandler: scrollPanelTo,
    }, h("div", { ref: (element) => { panelTop = element; } }), ...children);
}

// The status card and the record button: focus here shows the top of the panel.
function TopGroup(...children) {
    return h(DFL.Focusable, { "flow-children": "column", className: SCROLL_TOP_CLASS }, ...children);
}

// The saved info: focus here shows all of it.
function ScrollGroup(...children) {
    return h(DFL.Focusable, { "flow-children": "column" }, ...children);
}

// The Quick Access menu builds the panel anew each time it opens, and Steam puts
// focus back by place: drawn first without its rows, focus would land elsewhere,
// so the panel is drawn from its last state right away.
let cachedState = null;

// Whether the tools section is unfolded; kept across panel remounts and sessions.
const TOOLS_OPEN_KEY = "screen-recorder.tools-open";
function loadToolsOpen() {
    try {
        return localStorage.getItem(TOOLS_OPEN_KEY) === "1";
    }
    catch {
        return false;
    }
}
function saveToolsOpen(open) {
    try {
        localStorage.setItem(TOOLS_OPEN_KEY, open ? "1" : "0");
    }
    catch {
        // Only a convenience; the section just opens folded next time.
    }
}

// A dropdown's menu hides the Quick Access menu, and the panel is taken down and
// built anew about 1.5 to 2 s after the menu shows again; Steam then focuses
// something else. The dropdown takes focus back once the page shows.
let menuOpenId = null;
let menuOpenedAt = 0;
const MENU_RETURN_MS = 60000;
const FOCUS_SETTLE_MS = [60, 300];
// The press that closes a dropdown's menu reaches the panel once more as it shows
// again; on the dropdown it would open the menu again. No hand presses that soon.
let menuReturnedAt = 0;
const MENU_ECHO_MS = 150;
// The same echo can land on whatever Steam focuses first when the panel shows or
// is built anew, which is the record button; in a game it came later than
// MENU_ECHO_MS and started a recording. The record button ignores presses this
// soon after the panel shows, mounts or gets a dropdown's focus back.
let panelShownAt = 0;
const RECORD_GUARD_MS = 150;
const slotElements = {};

// Give focus the way the controller would, through the navigation node Steam keeps
// for the element: a browser focus moves the highlight, but not where the next
// press of up or down starts from.
function takeSteamFocus(element) {
    const target = element?.querySelector?.("button, [tabindex]") ?? element;
    if (!target) return;
    const fiberKey = Object.keys(target).find((key) => key.startsWith("__reactFiber$"));
    for (let fiber = fiberKey ? target[fiberKey] : null, level = 0; fiber && level < 30; fiber = fiber.return, level++) {
        for (let hook = fiber.memoizedState; hook && typeof hook === "object"; hook = hook.next) {
            const node = hook.memoizedState?.current;
            if (node && typeof node.TakeFocus === "function") {
                node.TakeFocus();
                return;
            }
        }
    }
    target.focus?.();
}

// Once the page shows, Steam picks where focus goes; this comes just after it.
function focusOnceShown(element) {
    const page = element?.ownerDocument;
    if (!page) return undefined;
    const timers = [];
    const focusSoon = () => {
        menuReturnedAt = Date.now();
        panelShownAt = Date.now();
        for (const delay of FOCUS_SETTLE_MS) timers.push(setTimeout(() => takeSteamFocus(element), delay));
    };
    const onShown = () => {
        if (page.visibilityState !== "visible") return;
        page.removeEventListener("visibilitychange", onShown);
        focusSoon();
    };
    if (page.visibilityState === "visible") focusSoon();
    else page.addEventListener("visibilitychange", onShown);
    return () => {
        page.removeEventListener("visibilitychange", onShown);
        timers.forEach(clearTimeout);
    };
}


// Wraps a control so it can get focus back. For a dropdown, a press is taken as
// opening its menu, and the echoed press after the menu closes is dropped.
function Slot({ id, menu, children }) {
    const onClickCapture = menu ? (event) => {
        if (Date.now() - menuReturnedAt < MENU_ECHO_MS) {
            event.stopPropagation();
            event.preventDefault();
            return;
        }
        menuOpenId = id;
        menuOpenedAt = Date.now();
    } : undefined;
    return h("div", { ref: (element) => { slotElements[id] = element; }, onClickCapture }, children);
}

function App() {
    const [s, setState] = useState(cachedState);
    const setS = useCallback((received) => {
        const next = withPendingMbps(received);
        // When the backend measured elapsed, so the timer can run on between polls.
        next.received_at = Date.now();
        cachedState = next;
        setState(next);
    }, []);
    const [now, setNow] = useState(Date.now());
    const [toolsOpen, setToolsOpen] = useState(loadToolsOpen);
    const toggleTools = useCallback(() => setToolsOpen((open) => {
        saveToolsOpen(!open);
        return !open;
    }), []);
    const capturing = !!(s && s.capturing);
    useEffect(() => {
        if (!capturing) return undefined;
        const timer = setInterval(() => setNow(Date.now()), TICK_MS);
        return () => clearInterval(timer);
    }, [capturing]);

    const refresh = useCallback(() => getState().then((next) => {
        if (next.status !== "idle") watchRecording();
        setS(next);
    }).catch(() => {}), [setS]);
    useEffect(() => {
        refresh();
        const timer = setInterval(refresh, POLL_MS);
        return () => clearInterval(timer);
    }, [refresh]);

    // Note when the panel mounts or shows again, for the record button's guard.
    useEffect(() => {
        panelShownAt = Date.now();
        const page = DFL.findSP?.()?.window?.document;
        if (!page) return undefined;
        const onVisibility = () => {
            if (page.visibilityState === "visible") panelShownAt = Date.now();
        };
        page.addEventListener("visibilitychange", onVisibility);
        return () => page.removeEventListener("visibilitychange", onVisibility);
    }, []);

    // Built anew after a dropdown's menu: hand focus back to that dropdown.
    useEffect(() => {
        const id = menuOpenId;
        const recent = id && Date.now() - menuOpenedAt < MENU_RETURN_MS;
        menuOpenId = null;
        menuOpenedAt = 0;
        // Back from a dropdown's menu: that dropdown takes focus and scrolls to it.
        return recent ? focusOnceShown(slotElements[id]) : resetPanelScroll();
    }, []);

    const choose = (call, value) => call(value).then(setS).catch(() => {});

    if (!s) {
        return ScrollPanel(h(DFL.PanelSection, null, TopGroup(
            Row(h("div", { style: { ...CARD, background: "rgba(255, 255, 255, 0.06)" } },
                h("div", { style: { display: "flex", alignItems: "center", gap: "10px" } },
                    h("div", { style: { ...DOT, background: IDLE_COLOR } }),
                    h("div", { style: { fontWeight: "bold", color: "#d6dade" } }, t("status.loading"))))),
            Row(h(DFL.ButtonItem, { layout: "below", disabled: true }, t("action.start"))),
        )));
    }

    const recording = s.status === "recording";
    const idle = s.status === "idle";
    // The card shows recording from the moment the video is written, even while
    // the backend still checks that the capture started.
    const elapsed = s.elapsed + Math.max(now - (s.received_at || now), 0) / 1000;
    const method = s.prefs.method;
    const qualityOptions = ["preset_ultra", "preset_default", "preset_medium", "preset_low", "preset_custom"]
        .map((key) => ({ data: key === "preset_custom" ? "custom" : key, label: t(key) }));
    const custom = s.prefs.quality === "custom";
    const [minMbps, maxMbps] = s.custom_mbps_range || [2, 50];
    const changeCustomMbps = (value) => {
        pendingMbps = value;
        setS(s);
        clearTimeout(sliderTimer);
        sliderTimer = setTimeout(() => setCustomMbps(value).then((saved) => {
            // Stored, and no newer value since: the backend's number is right again.
            if (pendingMbps === value && saved?.prefs?.custom_mbps === value) pendingMbps = null;
            setS(saved);
        }).catch(() => {
            pendingMbps = null;
        }), SLIDER_SAVE_MS);
    };
    const toggle = () => {
        if (Date.now() - panelShownAt < RECORD_GUARD_MS) return undefined;
        watchRecording();
        return (recording ? stopRecording() : startRecording()).then(setS).catch(() => {});
    };
    const accent = capturing ? RECORDING_COLOR : IDLE_COLOR;

    return ScrollPanel(
        h(DFL.PanelSection, null, TopGroup(
            Row(h("div", { style: { ...CARD, background: capturing ? "rgba(255, 107, 107, 0.15)" : "rgba(255, 255, 255, 0.06)" } },
                h("div", { style: { display: "flex", alignItems: "center", gap: "10px" } },
                    h("div", { style: { ...DOT, background: accent } }),
                    h("div", null,
                        h("div", { style: { fontWeight: "bold", color: capturing ? "#ffffff" : "#d6dade" } },
                            t(capturing ? "status.recording.short" : `status.${s.status}`)),
                        capturing ? h(Note, null, t("active", { width: s.size[0], height: s.size[1], rate: mbps(s.kbps) })) : null,
                        capturing ? h(Note, null, `${s.fps} FPS ${s.hdr && s.hdr !== "external_sdr" ? "HDR → SDR" : "SDR"}`) : null,
                        // On its own line: appended above it wrapped next to the timer.
                        capturing && ["plain", "external", "external_sdr"].includes(s.hdr) ? h(Note, null, t(`hdr.${s.hdr}`)) : null,
                        s.compare_step ? h(Note, null, t("compare.step", {
                            index: s.compare_index, steps: s.compare_steps, method: t(`method.${s.compare_step}`),
                        })) : null)),
                capturing ? h("div", { style: { fontSize: "22px", fontWeight: "bold", fontVariantNumeric: "tabular-nums", color: RECORDING_COLOR } },
                    formatTime(Math.floor(elapsed))) : null)),
            Row(h(Slot, { id: "toggle" }, h(DFL.ButtonItem, { layout: "below", disabled: (!idle && !recording) || (idle && !!s.compare_step), onClick: toggle },
                t(capturing ? "action.stop" : "action.start")))),
            s.error ? Row(h(Note, { color: ERROR_COLOR }, t(`err.${s.error}`) + (s.error_detail ? ` (${s.error_detail})` : ""))) : null,
            s.error ? Row(h(DFL.ButtonItem, { layout: "below", onClick: () => clearError().then(setS) }, t("dismiss"))) : null,
        )),
        h(DFL.PanelSection, { title: t("section.settings") },
            Row(h(Slot, { id: "method", menu: true }, h(DFL.DropdownItem, {
                label: t("method"),
                bottomSeparator: "none",
                disabled: !idle,
                rgOptions: ["pw", "kms"].map((data) => ({ data, label: t(`method.${data}`) })),
                selectedOption: method,
                onChange: (option) => choose(setMethod, option.data),
            }))),
            Row(h(OptionNote, { divider: true }, t(`method.${method}.note`))),
            // Hidden where the backend has nothing to change (unknown vibrancy range).
            method === "kms" && s.original_color ? Row(h(Slot, { id: "original" }, h(DFL.ToggleField, {
                label: t("original.label"),
                description: t(s.original_color === "oled" ? "original.note" : `original.note.${s.original_color}`),
                bottomSeparator: "standard",
                disabled: !idle,
                checked: s.prefs.kms_original_color,
                onChange: (enabled) => setKmsOriginalColor(enabled).then(setS),
            }))) : null,
            Row(h(Slot, { id: "quality", menu: true }, h(DFL.DropdownItem, {
                label: t("quality"),
                bottomSeparator: "none",
                disabled: !idle,
                rgOptions: qualityOptions,
                selectedOption: s.prefs.quality,
                onChange: (option) => choose(setQuality, option.data),
            }))),
            custom ? Row(h(Slot, { id: "customMbps" }, h(DFL.SliderField, {
                label: t("bitrate.custom"),
                bottomSeparator: "none",
                disabled: !idle,
                value: s.prefs.custom_mbps,
                min: minMbps,
                max: maxMbps,
                step: 1,
                showValue: true,
                valueSuffix: " Mbps",
                onChange: changeCustomMbps,
            }))) : null,
            Row(h(OptionNote, { divider: true }, custom
                ? t("bitrate.custom.note", { rate: s.prefs.custom_mbps })
                : t("bitrate", { rate: mbps(s.preview_kbps["800"]), rate1080: mbps(s.preview_kbps["1080"]) }))),
            Row(h(Slot, { id: "fps", menu: true }, h(DFL.DropdownItem, {
                label: t("fps"),
                bottomSeparator: "none",
                disabled: !idle,
                rgOptions: [60, 30].map((data) => ({ data, label: `${data} FPS` })),
                selectedOption: s.prefs.fps,
                onChange: (option) => choose(setFps, option.data),
            }))),
            Row(h(OptionNote, { divider: true }, t("fps.note"))),
            Row(h(Slot, { id: "mic" }, h(DFL.ToggleField, {
                label: t("mic"),
                // Mic off: this is the last setting, and its line sits above the tools button.
                // Mic on: its options follow as one group, without a line.
                bottomSeparator: s.prefs.mic ? "none" : "standard",
                disabled: !idle,
                checked: s.prefs.mic,
                onChange: (enabled) => setMic(enabled).then(setS),
            }))),
            s.prefs.mic ? Row(h(Slot, { id: "micMono" }, h(DFL.ToggleField, {
                label: t("mic.mono"),
                description: t("mic.mono.note"),
                bottomSeparator: "none",
                disabled: !idle,
                checked: s.prefs.mic_mono,
                onChange: (enabled) => setMicMono(enabled).then(setS),
            }))) : null,
            s.prefs.mic ? Row(h(Slot, { id: "micMode" }, h(DFL.ToggleField, {
                label: t("mic.separate"),
                bottomSeparator: "none",
                disabled: !idle,
                checked: s.prefs.mic_mode === "separate",
                onChange: (enabled) => setMicMode(enabled ? "separate" : "mix").then(setS),
            }))) : null,
            s.prefs.mic ? Row(h(OptionNote, { divider: true }, t(`mic.note.${s.prefs.mic_mode}`))) : null,
            // The tools stay in the settings section, with no section gap above them.
            // The button folds them away.
            // The line under the button only closes the folded section.
            Row(h(DFL.ButtonItem, { layout: "below", bottomSeparator: toolsOpen ? "none" : "standard", onClick: toggleTools },
                h("div", { style: { display: "flex", alignItems: "center", justifyContent: "center", gap: "6px" } },
                    t("section.tools"), h(Chevron, { up: toolsOpen })))),
            toolsOpen ? h(SP_REACT.Fragment, null,
                Row(h(DFL.ButtonItem, {
                    layout: "below",
                    bottomSeparator: "none",
                    disabled: !idle || !!s.compare_step,
                    onClick: () => {
                        watchRecording();
                        startComparison().then(setS).catch(() => {});
                        DFL.Navigation?.CloseSideMenus?.();
                    },
                }, t("compare"))),
                Row(h(OptionNote, { divider: true }, t("compare.note"))),
                Row(h(DFL.ButtonItem, { layout: "below", bottomSeparator: "none", onClick: showTestPattern }, t("pattern"))),
                Row(h(OptionNote, { divider: true }, t("pattern.note"))),
            ) : null,
        ),
        h(DFL.PanelSection, { title: t("section.saved") }, ScrollGroup(
            s.last_file ? InfoRow({ key: "last" },
                h(Note, null, t("last")),
                h("div", { style: FILE_NAME }, basename(s.last_file))) : null,
            InfoRow({ key: "folder" },
                h(Note, null, s.output_dir),
                s.free_gb !== null ? h(Note, null, t("free", { gb: s.free_gb })) : null),
            api.openFilePicker ? Row(h(DFL.ButtonItem, {
                key: "change",
                layout: "below",
                bottomSeparator: "none",
                disabled: !idle || !!s.compare_step,
                onClick: () => api.openFilePicker(FOLDER_PICKER, s.output_dir, false, true)
                    .then((picked) => (picked?.realpath || picked?.path ? setOutputDir(picked.realpath || picked.path).then(setS) : null))
                    .catch(() => {}),
            }, t("folder.change"))) : null,
            s.output_dir_custom ? Row(h(DFL.ButtonItem, {
                key: "default",
                layout: "below",
                bottomSeparator: "none",
                disabled: !idle || !!s.compare_step,
                onClick: () => setOutputDir("").then(setS).catch(() => {}),
            }, t("folder.default"))) : null,
        )),
    );
}

// A full-screen color test pattern over the Steam UI, to compare captures with
// known values. Drawn at the screen's real pixel size; tap or wait to close it.
const PATTERN_ID = "screen-recorder-test-pattern";
const PATTERN_SECONDS = 60;

function drawTestPattern(ctx, width, height) {
    const y = (v) => Math.round(v * height / 800);
    const bars = (top, bottom, level) => {
        const colors = [[1, 1, 1], [1, 1, 0], [0, 1, 1], [0, 1, 0], [1, 0, 1], [1, 0, 0], [0, 0, 1], [0, 0, 0]];
        colors.forEach(([r, g, b], n) => {
            ctx.fillStyle = `rgb(${r * level}, ${g * level}, ${b * level})`;
            const x0 = Math.round(n * width / colors.length);
            ctx.fillRect(x0, y(top), Math.round((n + 1) * width / colors.length) - x0, y(bottom) - y(top));
        });
    };
    const steps = (top, bottom, values) => {
        values.forEach((v, n) => {
            ctx.fillStyle = `rgb(${v}, ${v}, ${v})`;
            const x0 = Math.round(n * width / values.length);
            ctx.fillRect(x0, y(top), Math.round((n + 1) * width / values.length) - x0, y(bottom) - y(top));
        });
    };
    const ramp = (top, bottom, [r, g, b]) => {
        for (let x = 0; x < width; x++) {
            const v = Math.round(x * 255 / (width - 1));
            ctx.fillStyle = `rgb(${v * r}, ${v * g}, ${v * b})`;
            ctx.fillRect(x, y(top), 1, y(bottom) - y(top));
        }
    };
    bars(0, 180, 255);
    bars(180, 300, 191);
    steps(300, 420, Array.from({ length: 16 }, (_, n) => Math.round(n * 255 / 15)));
    steps(420, 500, Array.from({ length: 16 }, (_, n) => n * 2));
    steps(500, 580, Array.from({ length: 16 }, (_, n) => 225 + n * 2));
    ramp(580, 650, [1, 1, 1]);
    ramp(650, 700, [1, 0, 0]);
    ramp(700, 750, [0, 1, 0]);
    ramp(750, 800, [0, 0, 1]);
}

function showTestPattern() {
    const win = DFL.findSP?.()?.window ?? window;
    const doc = win.document;
    doc.getElementById(PATTERN_ID)?.remove();
    const overlay = doc.createElement("div");
    overlay.id = PATTERN_ID;
    overlay.style.cssText = "position:fixed;inset:0;z-index:2147483647;background:#000;cursor:none;";
    const canvas = doc.createElement("canvas");
    const ratio = win.devicePixelRatio || 1;
    canvas.width = Math.round(win.innerWidth * ratio);
    canvas.height = Math.round(win.innerHeight * ratio);
    canvas.style.cssText = "width:100%;height:100%;display:block;image-rendering:pixelated;";
    drawTestPattern(canvas.getContext("2d"), canvas.width, canvas.height);
    overlay.append(canvas);
    let timer = null;
    const close = () => {
        clearTimeout(timer);
        overlay.remove();
    };
    overlay.addEventListener("pointerdown", close);
    timer = setTimeout(close, PATTERN_SECONDS * 1000);
    doc.body.append(overlay);
    DFL.Navigation?.CloseSideMenus?.();
}

function RecordIcon() {
    return h("svg", { width: "1em", height: "1em", viewBox: "0 0 24 24", fill: "currentColor" },
        h("circle", { cx: 12, cy: 12, r: 10, fill: "none", stroke: "currentColor", strokeWidth: 2 }),
        h("circle", { cx: 12, cy: 12, r: 5 }));
}

function toast(title, body) {
    const toaster = api.toaster || DFL.toaster;
    toaster?.toast?.({ title, body });
}

// Toasts must also work with the Quick Access menu closed, when the panel is gone.
// The backend is asked only while a recording runs and for a short while after it
// is saved, not the whole time the plugin is loaded.
const WATCH_IDLE_MS = 10000;
let watchTimer = null;
let watchIdleSince = 0;
let seenFinished = null;
let seenErrorCount = null;
// A comparison saves several recordings; their toasts wait for the end and
// become one (the failure toast, if any step failed).
let comparing = false;
let compareSaved = 0;
let compareError = "";

function checkRecording() {
    return getState().then((s) => {
        const saved = seenFinished !== null ? Math.max(s.finished - seenFinished, 0) : 0;
        const failed = seenErrorCount !== null && s.error && s.error_count > seenErrorCount;
        if (s.compare_step || comparing) {
            compareSaved += saved;
            if (failed) compareError = s.error;
            comparing = !!s.compare_step;
            if (!comparing) {
                if (compareError) toast(t("toast.failed"), t(`err.${compareError}`));
                else if (compareSaved) toast(t("toast.compared"), t("toast.compared.body", { count: compareSaved }));
                compareSaved = 0;
                compareError = "";
            }
        }
        else {
            if (saved) toast(t("toast.saved"), basename(s.last_file));
            if (failed) toast(t("toast.failed"), t(`err.${s.error}`));
        }
        seenFinished = s.finished;
        seenErrorCount = s.error_count;
        if (s.status !== "idle" || s.compare_step) {
            watchIdleSince = 0;
        }
        else if (!watchIdleSince) {
            watchIdleSince = Date.now();
        }
        else if (Date.now() - watchIdleSince >= WATCH_IDLE_MS) {
            stopWatching();
        }
        return s;
    }).catch(() => null);
}

function watchRecording() {
    if (watchTimer) return;
    watchIdleSince = 0;
    watchTimer = setInterval(checkRecording, WATCH_MS);
}

function stopWatching() {
    clearInterval(watchTimer);
    watchTimer = null;
}

// Steam's system settings arrive as a serialized CMsgSystemManagerSettings.
// Field 38 is display_colorgamut_labelset: which end of the color vibrancy
// slider is Native. The backend reads that from the display's EDID first and
// uses this only without one. Settings updates may carry only the changed fields.
const LABELSET_FIELD = 38;

function readVarint(bytes, at) {
    let value = 0;
    let scale = 1;
    while (at < bytes.length) {
        const byte = bytes[at++];
        value += (byte & 0x7f) * scale;
        scale *= 128;
        if (!(byte & 0x80)) break;
    }
    return [value, at];
}

// Top-level varint fields of a protobuf message, by field number.
function readVarintFields(data) {
    const bytes = typeof data === "string" ? Uint8Array.from(atob(data), (c) => c.charCodeAt(0))
        : data instanceof Uint8Array ? data : new Uint8Array(data);
    const fields = {};
    let at = 0;
    while (at < bytes.length) {
        let key;
        [key, at] = readVarint(bytes, at);
        const type = key % 8;
        const field = Math.floor(key / 8);
        if (type === 0) {
            let value;
            [value, at] = readVarint(bytes, at);
            fields[field] = value;
        }
        else if (type === 1) at += 8;
        else if (type === 5) at += 4;
        else if (type === 2) {
            let length;
            [length, at] = readVarint(bytes, at);
            at += length;
        }
        else break;
    }
    return fields;
}

let settingsRegistration = null;
let sentLabelset = null;

function watchDisplaySettings() {
    try {
        settingsRegistration = SteamClient.System.RegisterForSettingsChanges((data) => {
            try {
                const labelset = readVarintFields(data)[LABELSET_FIELD];
                if (labelset === undefined || labelset === sentLabelset) return;
                sentLabelset = labelset;
                setColorLabelset(labelset).catch(() => {});
            }
            catch {
                // An unreadable message leaves the Steam Deck model fallback in place.
            }
        });
    }
    catch {
        settingsRegistration = null;
    }
}

var index = DFL.definePlugin(() => {
    // One look at load sets what counts as new, then watch only if something runs.
    checkRecording().then((s) => {
        if (s && s.status !== "idle") watchRecording();
    });
    watchDisplaySettings();
    return {
        name: manifest.name,
        titleView: h("div", { className: DFL.staticClasses.Title }, manifest.name),
        content: h(App, null),
        icon: h(RecordIcon, null),
        onDismount() {
            stopWatching();
            settingsRegistration?.unregister?.();
            settingsRegistration = null;
        },
    };
});

export { index as default };
