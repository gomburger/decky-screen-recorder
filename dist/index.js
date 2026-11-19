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
const listRecordings = callable("list_recordings");
const deleteRecording = callable("delete_recording");
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
        "method.kms.note": "Everything on screen, including the mouse cursor and overlays\nDisplay color settings are applied to the recording",
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
        "recordings": "Recordings",
        "recordings.empty": "No recordings in the save folder yet",
        "player.hint": "A Play/Pause · ◀ ▶ 5 Seconds · L1/R1 Previous/Next · Y Full Screen · B Back",
        "player.fullscreen": "Full Screen",
        "player.error": "Could not play this recording",
        "delete": "Delete",
        "delete.title": "Delete this recording?",
        "delete.body": "{name} will be deleted from the device. This cannot be undone.",
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
        "method.kms.note": "마우스 커서와 오버레이 포함 화면 전체 녹화\n디스플레이 색상 설정이 녹화에 반영됩니다",
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
        "recordings": "녹화 목록",
        "recordings.empty": "저장 위치에 녹화가 아직 없습니다",
        "player.hint": "A 재생/일시정지 · ◀ ▶ 5초 이동 · L1/R1 이전/다음 · Y 전체 화면 · B 뒤로",
        "player.fullscreen": "전체 화면",
        "player.error": "이 녹화를 재생하지 못했습니다",
        "delete": "삭제",
        "delete.title": "이 녹화를 삭제할까요?",
        "delete.body": "{name}을(를) 기기에서 삭제합니다. 되돌릴 수 없습니다.",
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

// Steam's color for a disabled Field's description (read from its stylesheet,
// 2026-11-19); a note under a disabled dropdown matches the toggles' own lines.
const DISABLED_NOTE_COLOR = "rgb(103, 112, 123)";

function OptionNote({ divider, disabled, children }) {
    return h("div", { style: {
        ...NOTE,
        ...(disabled ? { color: DISABLED_NOTE_COLOR } : {}),
        ...(divider ? { paddingBottom: "12px", borderBottom: "1px solid rgba(255, 255, 255, 0.12)" } : {}),
    } }, children);
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

// Steam scrolls a newly focused row into view smoothly on its own. The top group
// (status card, record button) scrolls to the very top instead, smoothly, so the
// card is never cut off. Steam's own scroll must stay out of that group: it can
// cancel a smooth scroll and jump at once when the target is far away (measured
// 2026-11-19: 251 -> 805 in one step). "force" with a handler makes Steam call
// only this handler there. No second call after 150 ms (Hangul IME Bridge 1.2.5
// needs one without "force"): here it restarted the smooth scroll near the end
// and dragged out the last 30 px for another 200 ms.
const SCROLL_EDGE_ATTR = "data-screen-recorder-scroll";
// Pinned top while recording, over the Quick Access panel's own background.
// Decky's title bar is transparent and lies over the first ~50 px of the
// scroller, so the pinned block reaches up to the scroller's top edge (padding
// as tall as its resting offset, the same amount of negative margin) and covers
// what scrolls behind the title too.
function pinnedTopStyle(offset) {
    return {
        position: "sticky",
        top: 0,
        zIndex: 3,
        paddingTop: `${offset}px`,
        marginTop: `${-offset}px`,
        background: "rgb(14, 20, 27)",
        boxShadow: "0 6px 8px -6px rgba(0, 0, 0, 0.6)",
    };
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

// A plain div carries the marker: Steam rewrites the class of its focusable
// nodes when focus moves, but leaves this div alone.
let topGroupElement = null;

// Decky's plugin list and a plugin's page share the Quick Access tab's scroller,
// so the page opened as far down as the list had been scrolled to reach it
// (measured 2026-11-19: 78 px, the status card half hidden).
let panelTop = null;
// Once, at 0 ms, was enough (also in Hangul IME Bridge, user confirmed).
function resetPanelScroll() {
    const timer = setTimeout(() => {
        // Leave it if Steam has put focus on a row below the status card.
        const focused = panelTop?.parentElement?.querySelector(".gpfocus");
        if (focused && !focused.closest(`[${SCROLL_EDGE_ATTR}="top"]`)) return;
        const scroller = findScroller(panelTop);
        if (scroller) scroller.scrollTop = 0;
    }, 0);
    return () => clearTimeout(timer);
}

// B closing a dropdown or a modal reaches the panel once more as it shows again
// (32 and 43 ms after it, measured 2026-11-19), and Decky took it as Back to the
// plugin list. It is dropped as Steam's vgp_oncancel DOM event on the panel's
// div, not with a Focusable around the panel: an extra nav node there changes
// where Steam puts focus back after a modal (AdGuard VPN 0.8.2).
const echoGuarded = new WeakSet();

function dropEchoedBack(event) {
    if (Date.now() - panelShownAt >= MENU_ECHO_MS) return;
    // What a Focusable's onCancelButton does when it handles the button.
    event.stopPropagation();
    event.preventDefault();
}

function panelRef(element) {
    panelTop = element;
    if (element && !echoGuarded.has(element)) {
        echoGuarded.add(element);
        element.addEventListener("vgp_oncancel", dropEchoedBack);
    }
}

function ScrollPanel(...children) {
    return h("div", { ref: panelRef }, ...children);
}

// The status card and the record button: focus here shows the top of the panel.
function TopGroup(...children) {
    return h("div", {
        [SCROLL_EDGE_ATTR]: "top",
        ref: (element) => { topGroupElement = element; },
    }, h(DFL.Focusable, {
        "flow-children": "column",
        scrollIntoViewWhenChildFocused: "force",
        fnScrollIntoViewHandler: () => {
            findScroller(topGroupElement)?.scrollTo({ top: 0, behavior: "smooth" });
            return true;
        },
    }, ...children));
}

// The saved info and its buttons, with no Focusable around them: as a group,
// Steam put focus back on its first row after the recordings modal instead of
// the button that opened it (measured 2026-11-20).
function ScrollGroup(...children) {
    return h(SP_REACT.Fragment, null, ...children);
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

// A dropdown's menu or a modal hides the Quick Access menu, and the panel is taken
// down and built anew; Steam then focuses something else. The control that opened
// it takes focus back once the page shows.
let menuOpenId = null;
let menuOpenedAt = 0;
// Where the panel was scrolled when that menu or modal opened. The rebuilt panel
// starts at the top; it goes back here before it is first drawn, so it does not
// show the top and then jump.
let menuScrollTop = null;

function markMenuOpen(id) {
    menuOpenId = id;
    menuOpenedAt = Date.now();
    menuScrollTop = findScroller(slotElements[id])?.scrollTop ?? null;
}
const MENU_RETURN_MS = 60000;
// Steam itself puts focus back on the opener as the page shows (2 ms after it,
// measured 2026-11-20, once no Focusable wrapped the panel or the saved group);
// this one try is a fallback and changes nothing then.
const FOCUS_SETTLE_MS = 20;
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

// The A that opens a dropdown or a modal can reach it once more as it appears and
// press what it focused first (closing the menu on its current choice, pausing the
// player). As in AdGuard VPN 0.8.2: an A within 150 ms of the menu or modal
// appearing is dropped, caught first in the page it is drawn in. B and touch are
// left alone; the B that closes one is handled on the panel's side.
let modalShownAt = 0;
const OPEN_ECHO_MS = 150;
const OPEN_GUARD_MS = 1500;

function markModalShown() {
    modalShownAt = Date.now();
}

function guardOpenEcho() {
    let page;
    try {
        page = DFL.findSP?.()?.document;
    }
    catch {
        page = null;
    }
    if (!page) return;
    modalShownAt = Date.now();
    const dropEcho = (event) => {
        if (Date.now() - modalShownAt >= OPEN_ECHO_MS) return;
        event.stopPropagation();
        event.preventDefault();
    };
    page.addEventListener("vgp_onok", dropEcho, true);
    // Long enough for the menu or modal to appear; the check above does the timing.
    setTimeout(() => page.removeEventListener("vgp_onok", dropEcho, true), OPEN_GUARD_MS);
}

// Steam's ConfirmModal; this notes when it appears and passes everything on.
function GuardedConfirmModal(props) {
    useEffect(markModalShown, []);
    return h(DFL.ConfirmModal, props);
}

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

// The rebuilt panel starts at the top. Steam scrolls to a row only when focus
// moves to it; when Steam has already put focus back on this row by position,
// TakeFocus changes nothing and the row stayed out of sight (measured
// 2026-11-19: scrollTop 0 after TakeFocus on the focused row, 546 when focus
// came from the record button).
function showInPanel(element) {
    const scroller = findScroller(element);
    if (!element || !scroller) return;
    const box = element.getBoundingClientRect();
    const view = scroller.getBoundingClientRect();
    if (box.top >= view.top && box.bottom <= view.bottom) return;
    element.scrollIntoView({ block: "center", behavior: "auto" });
}

// Once the page shows, Steam picks where focus goes; this comes just after it.
function focusOnceShown(element) {
    const page = element?.ownerDocument;
    if (!page) return undefined;
    let timer = null;
    const focusSoon = () => {
        menuReturnedAt = Date.now();
        panelShownAt = Date.now();
        timer = setTimeout(() => {
            takeSteamFocus(element);
            showInPanel(element);
        }, FOCUS_SETTLE_MS);
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
        clearTimeout(timer);
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
        markMenuOpen(id);
        guardOpenEcho();
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
        notifyListIcon(next);
        setState(next);
    }, []);
    const [now, setNow] = useState(Date.now());
    const pinnedTop = SP_REACT.useRef(null);
    // Where the top block rests below the scroller's top edge, measured unpinned.
    const [pinOffset, setPinOffset] = useState(null);
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
    useEffect(() => {
        if (!capturing) {
            setPinOffset(null);
            return;
        }
        if (pinOffset !== null) return;
        const element = pinnedTop.current;
        const scroller = findScroller(element);
        if (!element || !scroller) return;
        const top = element.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop;
        setPinOffset(Math.max(Math.round(top), 0));
    }, [capturing, pinOffset]);
    // Rows Steam scrolls into view must not end up under the pinned top: the
    // scroller's scroll-padding-top keeps them below it.
    useEffect(() => {
        const element = pinnedTop.current;
        const scroller = findScroller(element);
        if (pinOffset === null || !scroller) return undefined;
        const saved = scroller.style.scrollPaddingTop;
        scroller.style.scrollPaddingTop = `${element.offsetHeight}px`;
        return () => { scroller.style.scrollPaddingTop = saved; };
    }, [pinOffset, s?.error]);

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

    // Built anew after a menu or modal: back to where the panel was scrolled,
    // before it is drawn (layout effects run before the focus effect below).
    SP_REACT.useLayoutEffect(() => {
        const recent = menuOpenId && Date.now() - menuOpenedAt < MENU_RETURN_MS;
        const scroller = findScroller(panelTop);
        if (recent && scroller && menuScrollTop !== null) scroller.scrollTop = menuScrollTop;
    }, []);
    // Built anew after a menu or modal: hand focus back to what opened it.
    useEffect(() => {
        const id = menuOpenId;
        const recent = id && Date.now() - menuOpenedAt < MENU_RETURN_MS;
        menuOpenId = null;
        menuOpenedAt = 0;
        menuScrollTop = null;
        // Back from a menu or modal: the opener takes focus and is shown.
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
        // While recording, the status card and the stop button stay pinned at the
        // top of the panel; the rest scrolls under them.
        h("div", { ref: pinnedTop, style: capturing && pinOffset !== null ? pinnedTopStyle(pinOffset) : undefined },
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
        ))),
        h(DFL.PanelSection, { title: t("section.settings") },
            Row(h(Slot, { id: "method", menu: true }, h(DFL.DropdownItem, {
                label: t("method"),
                bottomSeparator: "none",
                disabled: !idle,
                rgOptions: ["pw", "kms"].map((data) => ({ data, label: t(`method.${data}`) })),
                selectedOption: method,
                onChange: (option) => choose(setMethod, option.data),
            }))),
            Row(h(OptionNote, { divider: true, disabled: !idle }, t(`method.${method}.note`))),
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
            Row(h(OptionNote, { divider: true, disabled: !idle }, custom
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
            Row(h(OptionNote, { divider: true, disabled: !idle }, t("fps.note"))),
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
            s.prefs.mic ? Row(h(OptionNote, { divider: true, disabled: !idle }, t(`mic.note.${s.prefs.mic_mode}`))) : null,
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
                Row(h(OptionNote, { divider: true, disabled: !idle || !!s.compare_step }, t("compare.note"))),
                Row(h(DFL.ButtonItem, { layout: "below", bottomSeparator: "none", onClick: showTestPattern }, t("pattern"))),
                Row(h(OptionNote, { divider: true }, t("pattern.note"))),
            ) : null,
        ),
        h(DFL.PanelSection, { title: t("section.saved") }, ScrollGroup(
            InfoRow({ key: "folder" },
                h(Note, null, s.output_dir),
                s.free_gb !== null ? h(Note, null, t("free", { gb: s.free_gb })) : null),
            // The picker is a modal too. Browsing can outlast MENU_RETURN_MS, so
            // the return is marked again when it closes, keeping the first scroll.
            api.openFilePicker ? Row(h(Slot, { key: "change", id: "change", menu: true }, h(DFL.ButtonItem, {
                layout: "below",
                bottomSeparator: "none",
                disabled: !idle || !!s.compare_step,
                onClick: () => fitFilePicker(api.openFilePicker(FOLDER_PICKER, s.output_dir, false, true))
                    .finally(() => {
                        if (menuOpenId !== "change") return;
                        menuOpenedAt = Date.now();
                    })
                    .then((picked) => (picked?.realpath || picked?.path ? setOutputDir(picked.realpath || picked.path).then(setS) : null))
                    .catch(() => {}),
            }, t("folder.change")))) : null,
            s.output_dir_custom ? Row(h(DFL.ButtonItem, {
                key: "default",
                layout: "below",
                bottomSeparator: "none",
                disabled: !idle || !!s.compare_step,
                onClick: () => setOutputDir("").then(setS).catch(() => {}),
            }, t("folder.default"))) : null,
            // Opens the player on this recording; A on a Field may also arrive as a
            // click, so the second call within a moment is dropped.
            // The newest recording in the save folder, also after a restart.
            s.latest_file ? Row(h(Slot, { key: "last", id: "last" }, h(DFL.Field, {
                focusable: true,
                childrenLayout: "below",
                childrenContainerWidth: "max",
                bottomSeparator: "none",
                onActivate: () => openLastRecording(s.latest_file),
                onClick: () => openLastRecording(s.latest_file),
            },
                h(Note, null, t("last")),
                h("div", { style: FILE_NAME }, basename(s.latest_file))))) : null,
            // A modal hides the menu and the panel is built anew; as a "menu" slot
            // the button gets focus back afterwards, like the dropdowns.
            Row(h(Slot, { key: "recordings", id: "recordings", menu: true }, h(DFL.ButtonItem, {
                layout: "below",
                bottomSeparator: "none",
                onClick: openRecordings,
            }, t("recordings")))),
        )),
    );
}

// Recording list and player. The backend serves the files on 127.0.0.1 (Steam's
// pages cannot open local files); Steam's browser engine plays the H.264/AAC MP4s.
const SEEK_SECONDS = 5;

function recordingLabel(item) {
    // Recording_20261119-042029_kms.mp4 -> 2026-11-19 04:20:29 · KMS
    const match = /^Recording_(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})_(.+)\.mp4$/.exec(item.name);
    if (!match) return item.name;
    const [, y, mo, d, hh, mm, ss, kind] = match;
    const method = { kms: "KMS", pw: "PipeWire" }[kind] ?? kind;
    return `${y}-${mo}-${d} ${hh}:${mm}:${ss} · ${method}`;
}

function sizeLabel(bytes) {
    return bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

// Decky's folder picker is taller than game mode's screen (file list fixed at
// 320 px, 560 px in all for 534): the path bar and "use this folder" were cut off.
// While it is open from here, drop the modal's outer padding and shorten the list
// by what still overflows (measured 2026-11-19: list 210 px, nothing cut off).
const PICKER_FIT_ID = "screen-recorder-fit-list";
// An attribute, not a class: Steam rewrites the list's class when focus enters
// it ("gpfocuswithin"), which dropped a class and let the window grow again.
const PICKER_FIT_ATTR = "data-screen-recorder-fit";
const PICKER_WAIT_MS = 3000;

function fitFilePicker(promise) {
    const page = DFL.findSP?.()?.window?.document;
    if (!page) return promise;
    const started = Date.now();
    const timer = setInterval(() => {
        const picker = page.querySelector(".deckyFilePicker");
        const list = picker && [...picker.querySelectorAll(".Panel")]
            .find((element) => page.defaultView.getComputedStyle(element).overflowY === "scroll");
        if (!list) {
            if (Date.now() - started > PICKER_WAIT_MS) clearInterval(timer);
            return;
        }
        clearInterval(timer);
        let scroller = picker;
        while (scroller && page.defaultView.getComputedStyle(scroller).overflowY !== "auto") scroller = scroller.parentElement;
        if (!scroller) return;
        scroller.style.paddingTop = "0px";
        scroller.style.paddingBottom = "0px";
        const over = scroller.scrollHeight - scroller.clientHeight;
        if (over <= 0) return;
        // !important wins over the list's own inline height (60vh).
        let style = page.getElementById(PICKER_FIT_ID);
        if (!style) {
            style = page.createElement("style");
            style.id = PICKER_FIT_ID;
            page.head.appendChild(style);
        }
        const height = Math.max(Math.floor(list.getBoundingClientRect().height - over - 2), 120);
        style.textContent = `[${PICKER_FIT_ATTR}] { height: ${height}px !important; }`;
        list.setAttribute(PICKER_FIT_ATTR, "");
    }, 100);
    const stop = () => clearInterval(timer);
    return promise.finally ? promise.finally(stop) : promise;
}

function openRecordings() {
    listRecordings().then((data) => {
        guardOpenEcho();
        DFL.showModal(h(RecordingsModal, { data }));
    }).catch(() => {});
}

// The player straight away, with the whole list for L1/R1. A recording that is
// no longer in the save folder (deleted, or the folder changed) opens the list.
let lastOpenAt = 0;
const LAST_OPEN_DEDUPE_MS = 500;

function openLastRecording(path) {
    const at = Date.now();
    if (at - lastOpenAt < LAST_OPEN_DEDUPE_MS || at - menuReturnedAt < MENU_ECHO_MS) return;
    lastOpenAt = at;
    // Focus comes back to this row when the panel is rebuilt after the modal.
    markMenuOpen("last");
    listRecordings().then((data) => {
        const items = data?.items ?? [];
        const index = items.findIndex((item) => item.name === basename(path));
        guardOpenEcho();
        if (index < 0) {
            DFL.showModal(h(RecordingsModal, { data }));
            return;
        }
        DFL.showModal(h(PlayerModal, {
            items, baseUrl: data.base_url ?? "", index,
            onDelete: (item) => confirmDelete(item, () => {}),
        }));
    }).catch(() => {});
}

function durationLabel(seconds) {
    if (!(seconds > 0)) return "";
    const total = Math.round(seconds);
    const pad = (value) => String(value).padStart(2, "0");
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor(total / 60) % 60;
    return hours ? `${hours}:${pad(minutes)}:${pad(total % 60)}` : `${minutes}:${pad(total % 60)}`;
}

// Asks first; resolves with the remaining items once the file is gone.
function confirmDelete(item, onDeleted) {
    guardOpenEcho();
    DFL.showModal(h(GuardedConfirmModal, {
        strTitle: t("delete.title"),
        strDescription: t("delete.body", { name: item.name }),
        strOKButtonText: t("delete"),
        onOK: () => deleteRecording(item.name).then((result) => onDeleted(result?.items)).catch(() => {}),
    }));
}

// The Quick Access title class casts a dark 12 px shadow (for the panel header);
// in a modal it darkened whatever sat under the title.
const MODAL_TITLE = { marginBottom: "12px", boxShadow: "none" };

const ALBUM_COLUMNS = 3;
const ALBUM_CSS = `
.sr-card { box-sizing: border-box; border-radius: 6px; padding: 4px; outline: 2px solid transparent; }
.sr-card.gpfocus { outline-color: #ffffff; background: rgba(255, 255, 255, 0.08); }
.sr-thumb { position: relative; aspect-ratio: 16 / 10; background: #23262e; border-radius: 4px; overflow: hidden; }
.sr-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.sr-duration { position: absolute; right: 4px; bottom: 4px; padding: 0 4px; border-radius: 3px;
  background: rgba(0, 0, 0, 0.75); color: #fff; font-size: 11px; line-height: 16px; }
.sr-caption { font-size: 12px; line-height: 16px; margin-top: 4px; color: #dcdedf; }
.sr-caption span { color: #8b929a; }
`;

function AlbumCard({ item, baseUrl, onOpen, onDelete }) {
    const [duration, setDuration] = useState(null);
    const name = encodeURIComponent(item.name);
    // The thumbnail request makes the frame and the length; then read the length.
    const onLoad = () => fetch(`${baseUrl}meta/${name}`).then((r) => r.json())
        .then((info) => setDuration(info?.duration)).catch(() => {});
    const [date, method] = recordingLabel(item).split(" · ");
    return h(DFL.Focusable, {
        className: "sr-card",
        // Three cards and two 12 px gaps fill the row exactly, so it stays centered.
        style: { flex: `0 0 calc((100% - ${(ALBUM_COLUMNS - 1) * 12}px) / ${ALBUM_COLUMNS})` },
        onActivate: onOpen,
        onSecondaryButton: onDelete,
        onSecondaryActionDescription: t("delete"),
    },
        h("div", { className: "sr-thumb" },
            h("img", { src: `${baseUrl}thumb/${name}`, loading: "lazy", onLoad }),
            duration ? h("div", { className: "sr-duration" }, durationLabel(duration)) : null),
        h("div", { className: "sr-caption" }, date || item.name, h("br"),
            h("span", null, [method, sizeLabel(item.size)].filter(Boolean).join(" · "))));
}

function RecordingsModal({ data, closeModal }) {
    const [items, setItems] = useState(data?.items ?? []);
    const baseUrl = data?.base_url ?? "";
    const remove = (item) => confirmDelete(item, (left) => setItems(left ?? items.filter((i) => i.name !== item.name)));
    const play = (item) => {
        guardOpenEcho();
        DFL.showModal(h(PlayerModal, { items, baseUrl, index: items.indexOf(item), onDelete: remove }));
    };
    useEffect(markModalShown, []);
    // Rows of cards, so up/down and left/right move like a grid.
    const rows = [];
    for (let i = 0; i < items.length; i += ALBUM_COLUMNS) rows.push(items.slice(i, i + ALBUM_COLUMNS));
    return h(DFL.ModalRoot, { closeModal },
        h("style", null, ALBUM_CSS),
        h("div", { className: DFL.staticClasses.Title, style: MODAL_TITLE }, t("recordings")),
        // Steam's footer already shows A, X and B; only an empty folder needs a line.
        items.length ? null : h("div", { style: { ...NOTE, marginBottom: "10px" } }, t("recordings.empty")),
        ...rows.map((row) => h(DFL.Focusable, {
            key: row[0].name,
            "flow-children": "row",
            style: { display: "flex", gap: "12px", marginBottom: "12px" },
        }, ...row.map((item) => h(AlbumCard, {
            key: item.name, item, baseUrl,
            onOpen: () => play(item),
            onDelete: () => remove(item),
        })))));
}

// Plays items[index]; L1 and R1 step to the previous and next recording in the
// album's order (newest first), stopping at either end.
function PlayerModal({ items, index: startIndex, baseUrl, onDelete, closeModal }) {
    useEffect(markModalShown, []);
    const video = SP_REACT.useRef(null);
    const holder = SP_REACT.useRef(null);
    const slot = SP_REACT.useRef(null);
    const fullSlot = SP_REACT.useRef(null);
    // One video element for both the modal and the full window: it lives in this
    // box, and the box moves between the two. A new element for each place had to
    // load the file again, which showed black frames on every Y.
    const [box, setBox] = useState(null);
    // The recording whose picture is on screen. After L1/R1 the last one stays on
    // top, paused, until the next one has its first frame, instead of black.
    const [shown, setShown] = useState(null);
    const [index, setIndex] = useState(startIndex);
    const [failed, setFailed] = useState(false);
    const [full, setFull] = useState(false);
    const item = items[index];
    const url = baseUrl + encodeURIComponent(item.name);
    const step = (by) => {
        const next = index + by;
        if (next < 0 || next >= items.length) return;
        setFailed(false);
        video.current?.pause();
        setIndex(next);
    };
    const toggle = () => {
        const element = video.current;
        if (!element) return;
        if (element.paused) element.play().catch(() => {});
        else element.pause();
    };
    const seek = (seconds) => {
        const element = video.current;
        if (!element || !Number.isFinite(element.duration)) return;
        element.currentTime = Math.min(Math.max(element.currentTime + seconds, 0), element.duration);
    };
    const onDirection = (event) => {
        const button = event?.detail?.button;
        if (button === DFL.GamepadButton.DIR_LEFT) seek(-SEEK_SECONDS);
        else if (button === DFL.GamepadButton.DIR_RIGHT) seek(SEEK_SECONDS);
        // Full screen: up and down go nowhere, so focus cannot slip to Delete.
        else if (!full) return false;
        return true;
    };
    const onButtonDown = (event) => {
        const button = event?.detail?.button;
        if (button === DFL.GamepadButton.BUMPER_LEFT) step(-1);
        else if (button === DFL.GamepadButton.BUMPER_RIGHT) step(1);
    };
    // Y: the browser's fullscreen needs a real user gesture and refuses Steam's
    // button events, so the player covers the whole Steam window instead.
    const toggleFull = () => setFull((value) => !value);
    const onCancel = (event) => {
        if (!full) return false;
        event?.stopPropagation?.();
        toggleFull();
        return true;
    };
    const frameReady = () => setShown(url);
    const previous = shown && shown !== url ? h("video", {
        key: shown,
        src: shown,
        muted: true,
        playsInline: true,
        style: { position: "absolute", inset: 0, zIndex: 1, width: "100%", height: "100%", objectFit: "contain", background: "#000" },
    }) : null;
    const renderVideo = () => [previous, h("video", {
        key: url,
        ref: video,
        src: url,
        autoPlay: true,
        controls: true,
        // The file is already on the device; Download only copied it to ~/Downloads.
        // Picture-in-picture has no window to float in on game mode, and the
        // fullscreen button is replaced by Y (full window).
        controlsList: "nodownload nofullscreen",
        disablePictureInPicture: true,
        playsInline: true,
        onLoadedData: frameReady,
        onError: () => { setFailed(true); frameReady(); },
        style: { background: "#000", display: "block", width: "100%", height: "100%", objectFit: "contain" },
    })];
    const page = holder.current?.ownerDocument;
    const remove = () => {
        if (!onDelete) return;
        closeModal?.();
        onDelete(item);
    };
    // Let this modal reach from under the status bar to above the footer: Steam
    // pads its scroller by 24 px top and bottom, which kept the box 48 px short
    // (measured on game mode, 534 px tall: content 404 px instead of 356 px).
    SP_REACT.useEffect(() => {
        let scroller = holder.current;
        while (scroller && scroller.ownerDocument.defaultView.getComputedStyle(scroller).overflowY !== "auto") {
            scroller = scroller.parentElement;
        }
        if (!scroller) return undefined;
        const saved = [scroller.style.paddingTop, scroller.style.paddingBottom];
        scroller.style.paddingTop = "0px";
        scroller.style.paddingBottom = "0px";
        return () => {
            [scroller.style.paddingTop, scroller.style.paddingBottom] = saved;
        };
    }, []);
    SP_REACT.useLayoutEffect(() => {
        const element = holder.current.ownerDocument.createElement("div");
        element.style.width = "100%";
        element.style.height = "100%";
        element.style.position = "relative";
        setBox(element);
    }, []);
    // Moved in one step (appendChild), so the video keeps playing: the browser
    // pauses a media element only when it is still out of the page afterwards.
    SP_REACT.useLayoutEffect(() => {
        const target = full ? fullSlot.current : slot.current;
        if (box && target && box.parentNode !== target) target.appendChild(box);
    }, [box, full, page]);
    // Stop loading when the modal goes away. A layout cleanup runs before React
    // takes the videos out of the box (and clears the video ref); a plain
    // effect's cleanup would find neither.
    SP_REACT.useLayoutEffect(() => () => {
        box?.querySelectorAll("video").forEach((element) => {
            element.pause();
            element.removeAttribute("src");
            element.load();
        });
    }, [box]);
    return h(DFL.ModalRoot, { closeModal },
        h("div", { className: DFL.staticClasses.Title, style: { ...MODAL_TITLE, marginBottom: "6px", display: "flex", alignItems: "center", gap: "12px" } },
            h("span", { style: { flex: "1 1 auto" } }, recordingLabel(item)),
            h("span", { style: { color: "#8b929a", fontSize: "22px", fontVariantNumeric: "tabular-nums" } },
                `${index + 1} / ${items.length}`)),
        h(DFL.Focusable, {
            onActivate: toggle,
            onGamepadDirection: onDirection,
            onButtonDown,
            onOptionsButton: toggleFull,
            onOptionsActionDescription: t("player.fullscreen"),
            onSecondaryButton: full ? undefined : remove,
            onSecondaryActionDescription: onDelete && !full ? t("delete") : undefined,
            onCancelButton: onCancel,
            noFocusRing: false,
        }, h("div", { ref: holder },
            // The modal grows with its content up to Steam's limit (452 px of 534
            // here); the video gets what the title and the hint/delete row leave.
            // A fixed height: a new video has no size until its metadata loads, and
            // with only a maximum the modal shrank and grew back on L1/R1.
            h("div", { ref: slot, style: { height: "calc(100vh - 222px)", background: "#000" } }))),
        box ? SP_REACTDOM.createPortal(renderVideo(), box) : null,
        // Kept in the page and only hidden, so the box never leaves the document.
        page ? SP_REACTDOM.createPortal(h("div", {
            ref: fullSlot,
            style: { position: "fixed", inset: 0, zIndex: 10000, background: "#000", display: full ? "block" : "none" },
        }), page.body) : null,
        h("div", { style: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", marginTop: "8px" } },
            h("div", { style: NOTE }, failed ? t("player.error") : t("player.hint")),
            // Hidden but kept while full: removing it shortened the modal, which
            // moved the video up and down while Steam's focus ring stayed where it
            // was. Up and down are blocked while full, so focus cannot reach it.
            onDelete ? h(DFL.DialogButton, {
                style: { width: "auto", minWidth: "100px", flex: "0 0 auto", visibility: full ? "hidden" : "visible" },
                onClick: remove,
            }, t("delete")) : null));
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

// The icon in Decky's plugin list turns red while recording. Each time the list
// appears it asks for the state, and the panel and the recording watcher tell
// it right away when the state changes.
const listIconListeners = new Set();

function notifyListIcon(state) {
    const capturing = !!(state && state.capturing);
    listIconListeners.forEach((listener) => listener(capturing));
}

function PluginListIcon() {
    const [capturing, setCapturing] = useState(!!cachedState?.capturing);
    useEffect(() => {
        listIconListeners.add(setCapturing);
        getState().then(notifyListIcon).catch(() => {});
        return () => { listIconListeners.delete(setCapturing); };
    }, []);
    return h("span", { style: { display: "flex", color: capturing ? RECORDING_COLOR : undefined } }, h(RecordIcon, null));
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
        notifyListIcon(s);
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
        content: h(App, null),
        icon: h(PluginListIcon, null),
        onDismount() {
            stopWatching();
            settingsRegistration?.unregister?.();
            settingsRegistration = null;
        },
    };
});

export { index as default };
