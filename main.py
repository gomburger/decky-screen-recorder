"""Screen Recorder for Decky: record game mode from the Quick Access menu.

Runs as root (flag "root" in plugin.json) because kmsgrab needs it. Everything
that does not need root runs as the desktop user, in a clean environment so
Decky's bundled libraries never leak into system tools.

Video and audio are always captured by separate processes and muxed afterwards:
sharing one ffmpeg between kmsgrab and PulseAudio dropped capture to ~17 fps.
"""

import asyncio
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import signal
import struct
import time

log = logging.getLogger("screen-recorder")

SETTINGS_DIR = os.environ.get("DECKY_PLUGIN_SETTINGS_DIR", "/tmp/screen-recorder-decky")
USER_HOME = os.environ.get("DECKY_USER_HOME", os.path.expanduser("~"))
PREFS_FILE = os.path.join(SETTINGS_DIR, "prefs.json")
# Display settings changed for a KMS capture, kept on disk until restored, so a
# crash of the plugin or of Decky does not leave them changed.
RESTORE_FILE = os.path.join(SETTINGS_DIR, "restore.json")
OUTPUT_DIR = os.path.join(USER_HOME, "Videos", "ScreenRecorder")

KMS_DEVICE = "/dev/dri/card0"
VAAPI_DEVICE = "/dev/dri/renderD128"
FALLBACK_SIZE = (1280, 800)
FRAMERATE = 60
FRAMERATES = (60, 30)
START_CHECK_SEC = 1.5
# A KMS capture can also stall without exiting: on one external display ffmpeg
# wrote only the file header, never a frame, ignored SIGINT and only a kill ended
# it, so the recording was lost at save. KMS ffmpeg reports its frame count to a
# progress file; none by this time after spawn means no frames.
KMS_FIRST_DATA_SEC = 4.0
PROGRESS_NAME = "progress.txt"
PROGRESS_RE = re.compile(r"^frame=\s*(\d+)\s*$", re.M)
KMS_NO_FRAMES = "KMS capture got no frames"
COMPOSITE_SETTLE_SEC = 0.3
# kmsgrab exits when the plane it picked changes, which happens when the screen
# layout shifts during start, such as the Quick Access menu closing.
KMS_START_ATTEMPTS = 4
KMS_RETRY_DELAY_SEC = 0.5
# Only these kmsgrab errors clear up on a retry; anything else fails at once.
RETRYABLE_KMS_ERRORS = (
    "format changed", "dimensions changed", "no longer has", "Failed to get framebuffer",
    "Input/output error",
)
STOP_TIMEOUT_SEC = 10
MUX_TIMEOUT_SEC = 600
AUDIO_ARGS = ["-c:a", "aac", "-b:a", "160k"]
# Matroska otherwise holds a static screen's tiny packets in memory for seconds:
# the file stayed empty, the start check took a working capture for a stalled one,
# and a capture that had to be killed lost everything. Write at least every second.
FLUSH_ARGS = ["-flush_packets", "1", "-cluster_time_limit", "1000"]
# KMS frames come out of VAAPI as full-range values. On real KMS frames the VPP
# stretches them once more (limited to full) unless the next filter labels them
# limited, so the label stays limited here to keep the values right, and the
# finished file is relabeled full at mux time (FULL_RANGE_RETAG), without touching
# the pictures. Measured with the color test pattern: labeled limited, players
# crushed 0-12 to black and clipped 233-255; with the values stretched, the same.
# Only the matrix and range go to scale_vaapi: with out_color_primaries and
# out_color_transfer=bt709 as well, the VPP also bent the curve, lifting midtones
# 4-8 steps (gray 85 -> 93; measured 2026-11-18, the KMS buffer itself is exact).
# The BT.709 labels are set with setparams, which leaves the values alone.
KMS_COLOR_FILTER = (
    "scale_vaapi=format=nv12:out_color_matrix=bt709:out_range=pc,"
    "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
)
FULL_RANGE_RETAG = ["-bsf:v", "h264_metadata=video_full_range_flag=1", "-color_range", "pc"]
# Written into a capture folder whose video holds full-range values labeled limited.
FULL_RANGE_MARK = "full-range"
# Deck OLED's internal panel runs HDR as "HDR G22": while gamescope outputs HDR,
# the KMS buffer holds gamma 2.2 values in the panel's own gamut (EDID primaries)
# up to the panel peak (~950 nits), not PQ. Measured 2026-11-18 with an HDR10 test
# pattern of known nits. gamescope's own HDR->SDR (the PipeWire capture) is gamma
# 2.2 with SDR white at ~480 nits, clipping above. To match it: gamma 2.2 in and
# out (bt470m), contrast 1.37 (about (950/480)^(1/2.2)), and BT.2020 as the input
# gamut, which beat P3 and the EDID gamut once libplacebo's gamut mapping is
# included (mean error vs PipeWire ~4 vs ~11 for the earlier sRGB/P3/1.3 values).
# saturation 1.1 offsets the brightness libplacebo's perceptual gamut mapping takes
# from saturated reds (gamescope clips instead): red 163 -> ~170, PipeWire 171.
# kmsgrab frames cannot be mapped to Vulkan directly (DRM->Vulkan derive is not
# implemented, and libplacebo fails to map VAAPI frames), so they go through VAAPI
# to full-range NV12 in system memory and are uploaded to Vulkan from there. P010
# came back with wrong colors here; NV12 matched the calibrated RGB path.
HDR_DOWNLOAD_FILTER = (
    "scale_vaapi=format=nv12:out_color_matrix=bt709:out_range=pc,setparams=range=tv,"
    "hwdownload,format=nv12"
)
HDR_INPUT_FILTER = "setparams=color_trc=bt470m:color_primaries=bt2020:colorspace=bt709:range=pc"
HDR_TONEMAP_FILTER = (
    "libplacebo=format=nv12:colorspace=bt709:color_primaries=bt709:"
    "color_trc=bt470m:range=tv:contrast=1.37:saturation=1.1,setparams=color_trc=bt709"
)
# The tuned values above fit only Deck OLED's own panel. Elsewhere the HDR path
# runs without them:
# - another built-in HDR panel: the same gamma 2.2 reading with the P3-like gamut
#   and no contrast or saturation adjustment (its peak and gamut are unmeasured);
# - an external HDR display: gamescope sends HDR10, so the buffer is
#   read as PQ with BT.2020 primaries. libplacebo clips it to BT.709 (still PQ, RGB)
#   and a user shader turns that into gamma 2.2 with a 480-nit white, clipping
#   above, which is what gamescope's own PipeWire stream measured on both Deck OLED
#   and an external monitor (2026-11-18). A second pass makes NV12; the shader
#   needs RGB, since on planar output it would also run on the chroma planes.
#   libplacebo's default tone mapping here lifted shadows and washed out colors.
HDR_PLAIN_INPUT_FILTER = "setparams=color_trc=bt470m:color_primaries=smpte432:colorspace=bt709:range=pc"
HDR_PLAIN_TONEMAP_FILTER = (
    "libplacebo=format=nv12:colorspace=bt709:color_primaries=bt709:"
    "color_trc=bt470m:range=tv,setparams=color_trc=bt709"
)
HDR_PQ_INPUT_FILTER = "setparams=color_trc=smpte2084:color_primaries=bt2020:colorspace=bt709:range=pc"
PQ_SHADER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shaders", "pq_to_sdr.glsl")
# SDR white in nits for the shader: gamescope's PipeWire stream puts an HDR game's
# white at about 480. SDR content on an HDR output sits at Steam's "SDR content
# brightness" (SDR_ON_HDR_PROP, a float), gamma 2.2: measured 2026-11-18 on an
# external monitor, the gray steps matched 203 * (v/255)^2.2 within 2-3%.
HDR_WHITE_NITS = 480.0
SDR_ON_HDR_PROP = "GAMESCOPE_SDR_ON_HDR_CONTENT_BRIGHTNESS"
SDR_ON_HDR_DEFAULT_NITS = 203.0
# Written with the white level of each recording into the settings folder, whose
# path is fixed: it goes into the filter string, where a save folder chosen by the
# user could carry ",", ":" or quotes that break the filter graph.
SHADER_NAME = "pq_to_sdr.glsl"


def pq_tonemap_filter(shader):
    return (
        "libplacebo=format=rgba:color_primaries=bt709:color_trc=smpte2084:range=pc:"
        f"tonemapping=clip:gamut_mode=clip:peak_detect=false:custom_shader_path={shader},"
        "setparams=color_trc=bt709:color_primaries=bt709:range=pc,"
        "libplacebo=format=nv12:colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
    )


# HDR conversion per mode: "deck" (tuned, Deck OLED panel), "plain", "external"
# (an HDR game on an external HDR output) and "external_sdr" (SDR content there:
# an external display in HDR mode carries everything as PQ, so the plain SDR path
# recorded it gray and flat). Both external modes use the PQ filters with the
# shader written for the recording (pq_tonemap_filter).
PQ_MODES = ("external", "external_sdr")
HDR_FILTERS = {
    "deck": (HDR_INPUT_FILTER, HDR_TONEMAP_FILTER),
    "plain": (HDR_PLAIN_INPUT_FILTER, HDR_PLAIN_TONEMAP_FILTER),
}
# 1 while gamescope sends HDR to the display. Only used for external displays:
# Deck OLED's own panel reports 1 even while its buffer holds plain SDR.
HDR_OUTPUT_PROP = "GAMESCOPE_HDR_OUTPUT_FEEDBACK"
DRM_CLASS_DIR = "/sys/class/drm"
# Set by gamescope when the focused game renders in an HDR colorspace. Not
# HDR_OUTPUT_PROP: on Deck OLED's panel that is 1 even for SDR games and the
# Steam UI, whose buffer then holds plain SDR.
HDR_APP_PROP = "GAMESCOPE_COLOR_APP_WANTS_HDR_FEEDBACK"
# Vulkan video encoding is still behind an experimental flag in RADV.
VULKAN_ENCODE_ENV = {"RADV_EXPERIMENTAL": "video_encode"}
# gamescope's PipeWire default: BT.601 matrix, limited range, sRGB-like picture.
PW_COLOR_FILTER = "setparams=range=tv:colorspace=smpte170m:color_primaries=bt709:color_trc=bt709"

METHODS = ("kms", "pw")
MIC_MODES = ("mix", "separate")
# Comparison recording: the same screen with PipeWire, then with KMS.
COMPARE_METHODS = ("pw", "kms")
COMPARE_SECONDS = 5
COMPARE_GAP_SEC = 1.5
DEFAULT_PRESET = "preset_default"

# Copied from the Steam client UI (steamui/sp.js): kbps per preset, per height.
# Heights below 1080 use the "800p" column, which Steam labels 720p.
PRESET_KBPS = {
    "preset_low": {800: 3750, 1080: 6000, 1440: 11000, 2160: 19000},
    "preset_medium": {800: 5600, 1080: 9000, 1440: 16500, 2160: 28500},
    "preset_default": {800: 7500, 1080: 12000, 1440: 22000, 2160: 38000},
    "preset_ultra": {800: 12000, 1080: 24000, 1440: 32000, 2160: 50000},
}
DIMENSIONS_RE = re.compile(r"dimensions:\s+(\d+)x(\d+)")
# One or more 32-bit values, as xprop prints them: "NAME(CARDINAL) = 0, 1032730852".
ROOT_PROP_RE = re.compile(r"=\s*(\d+(?:\s*,\s*\d+)*)\s*$")
MURA_DISABLED_PROP = "GAMESCOPE_COLOR_MURA_CORRECTION_DISABLED"
# Steam's night mode: strength, hue, saturation (float bits); strength 0 is off.
NIGHT_MODE_PROP = "GAMESCOPE_COLOR_NIGHT_MODE"
# Steam's color temperature as a white point x, y (float bits); 0, 0 is the
# display's own white ("use native color temperature").
VIRTUAL_WHITE_PROP = "GAMESCOPE_DISPLAY_VIRTUAL_WHITE"
NATIVE_WHITE = "0,0"
GAMUT_WIDENESS_PROP = "GAMESCOPE_COLOR_SDR_GAMUT_WIDENESS"
# "Record original colors": while a KMS capture runs, the panel-specific processing
# is switched off so the buffer holds the game's own colors. Mura correction off
# (Deck OLED only), and Steam's color vibrancy at Native, where sRGB values pass
# through unchanged: measured with the color test pattern on Deck OLED, the bars
# then matched a BT.709 tag exactly.
# Where Native sits on the vibrancy range (0-1) comes from the lit display's EDID
# gamut (native_wideness). Without an EDID, Steam's label set for the panel
# decides: 1 = sRGB..Native (wider than sRGB, Deck OLED), 2 = Native..sRGB..Boosted
# (narrower, Deck LCD); any other set (Min..Max) leaves it unknown.
NATIVE_WIDENESS = {1: 1.0, 2: 0.0}
# Used until the frontend reports Steam's label set.
PANEL_LABELSET = {"oled": 1, "lcd": 2}


def float_bits(value):
    return str(struct.unpack("I", struct.pack("f", value))[0])


# "Custom" quality: one bit rate in Mbps for every resolution, chosen on a slider.
CUSTOM_PRESET = "custom"
CUSTOM_MBPS_RANGE = (2, 50)
DEFAULT_CUSTOM_MBPS = 24
QUALITIES = tuple(PRESET_KBPS) + (CUSTOM_PRESET,)


def bitrate_for(preset, height, custom_mbps=DEFAULT_CUSTOM_MBPS):
    if preset == CUSTOM_PRESET:
        return custom_mbps * 1000
    table = PRESET_KBPS.get(preset) or PRESET_KBPS[DEFAULT_PRESET]
    for threshold in (2160, 1440, 1080):
        if height >= threshold:
            return table[threshold]
    return table[800]


def parse_dimensions(text):
    match = DIMENSIONS_RE.search(text)
    if not match:
        return FALLBACK_SIZE
    return int(match.group(1)), int(match.group(2))


def encode_args(kbps, fps=FRAMERATE):
    return [
        "-c:v", "h264_vaapi", "-rc_mode", "VBR",
        "-b:v", f"{kbps}k", "-maxrate", f"{kbps * 3 // 2}k", "-bufsize", f"{kbps * 2}k",
        "-g", str(fps * 2),
    ]


def ffmpeg_base():
    return ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]


def audio_cmd(sink, output):
    return ffmpeg_base() + ["-f", "pulse", "-i", f"{sink}.monitor"] + AUDIO_ARGS + FLUSH_ARGS + [output]


def progress_args(output):
    return ["-progress", os.path.join(os.path.dirname(output), PROGRESS_NAME), "-stats_period", "0.5"]


def frames_done(work_dir):
    """Frames ffmpeg reported, or None without a progress file."""
    try:
        with open(os.path.join(work_dir, PROGRESS_NAME), encoding="utf-8", errors="replace") as handle:
            counts = PROGRESS_RE.findall(handle.read())
    except OSError:
        return None
    return int(counts[-1]) if counts else 0


# The encoder may not hold on to frames mapped from the scanout buffers: with its
# defaults (B-frames, two frames in flight) a 10-bit DCC-compressed buffer on one
# external display deadlocked it at the first frame, while mapping, scaling and
# downloading the same frames ran fine (2026-11-19, 58 fps at 1920x1280 without).
KMS_ENCODE_ARGS = ["-bf", "0", "-async_depth", "1"]


def kms_cmd(output, kbps, fps=FRAMERATE):
    return ffmpeg_base() + progress_args(output) + [
        "-device", KMS_DEVICE, "-f", "kmsgrab", "-framerate", str(fps), "-i", "-",
        "-vf", f"hwmap=derive_device=vaapi,{KMS_COLOR_FILTER}",
    ] + encode_args(kbps, fps) + KMS_ENCODE_ARGS + FLUSH_ARGS + [output]


def vulkan_encode_args(kbps, fps=FRAMERATE):
    return [
        "-c:v", "h264_vulkan", "-rc_mode", "vbr",
        "-b:v", f"{kbps}k", "-maxrate", f"{kbps * 3 // 2}k", "-bufsize", f"{kbps * 2}k",
        "-g", str(fps * 2),
    ]


def kms_hdr_cmd(output, kbps, fps=FRAMERATE, mode="deck"):
    """KMS capture of an HDR screen, converted to SDR on the GPU with libplacebo.

    Deck's VAAPI driver has no HDR tone mapping, and libplacebo's Vulkan frames
    cannot be handed to the VAAPI encoder, so this path encodes with Vulkan.
    """
    if mode in PQ_MODES:
        input_filter = HDR_PQ_INPUT_FILTER
        tonemap_filter = pq_tonemap_filter(os.path.join(SETTINGS_DIR, SHADER_NAME))
    else:
        input_filter, tonemap_filter = HDR_FILTERS[mode]
    return ffmpeg_base() + progress_args(output) + [
        "-init_hw_device", "vulkan=vk", "-filter_hw_device", "vk",
        "-device", KMS_DEVICE, "-f", "kmsgrab", "-framerate", str(fps), "-i", "-",
        "-vf", (f"hwmap=derive_device=vaapi,{HDR_DOWNLOAD_FILTER},hwupload,"
                f"{input_filter},{tonemap_filter}"),
    ] + vulkan_encode_args(kbps, fps) + FLUSH_ARGS + [output]


def pw_source_cmd(width, height, fps=FRAMERATE):
    return [
        "gst-launch-1.0", "-e", "-q", "pipewiresrc", "target-object=gamescope",
        "do-timestamp=true", "keepalive-time=100", "!", "videorate", "!", "videoconvert", "!",
        "videoscale", "!",
        f"video/x-raw,format=NV12,width={width},height={height},framerate={fps}/1",
        "!", "fdsink", "fd=1",
    ]


def pw_encode_cmd(output, width, height, kbps, fps=FRAMERATE):
    return ffmpeg_base() + [
        "-vaapi_device", VAAPI_DEVICE, "-f", "rawvideo", "-pixel_format", "nv12",
        "-video_size", f"{width}x{height}", "-framerate", str(fps), "-i", "pipe:0",
        "-vf", f"{PW_COLOR_FILTER},format=nv12,hwupload",
    ] + encode_args(kbps, fps) + FLUSH_ARGS + [output]


def mic_cmd(source, output, mono=False):
    channels = ["-ac", "1"] if mono else []
    return ffmpeg_base() + ["-f", "pulse", "-i", source] + channels + AUDIO_ARGS + FLUSH_ARGS + [output]


def mux_cmd(video, audio, output, mic=None, mic_mode="mix", full_range=False):
    """Mux video with game audio and/or microphone, mixed or as separate tracks."""
    tracks = [path for path in (audio, mic) if path]
    command = ffmpeg_base() + ["-i", video]
    for path in tracks:
        command += ["-i", path]
    command += ["-map", "0:v"]
    if len(tracks) == 2 and mic_mode == "mix":
        # Mixing needs one audio re-encode; video is still copied.
        command += [
            "-filter_complex", "[1:a][2:a]amix=inputs=2:duration=longest:normalize=0[a]",
            "-map", "[a]", "-c:v", "copy",
        ] + AUDIO_ARGS
    else:
        for index in range(len(tracks)):
            command += ["-map", f"{index + 1}:a"]
        command += ["-c", "copy"]
        if len(tracks) == 2:
            command += [
                # MP4 players read track names from handler_name.
                "-metadata:s:a:0", "title=Game", "-metadata:s:a:0", "handler_name=Game",
                "-metadata:s:a:1", "title=Microphone", "-metadata:s:a:1", "handler_name=Microphone",
                "-disposition:a:0", "default", "-disposition:a:1", "0",
            ]
    if full_range:
        command += FULL_RANGE_RETAG
    if tracks:
        command += ["-shortest"]
    return command + ["-movflags", "+faststart", output]


def user_ids():
    info = os.stat(USER_HOME)
    return info.st_uid, info.st_gid


def clean_env():
    uid, _ = user_ids()
    runtime = f"/run/user/{uid}"
    return {
        "PATH": "/usr/local/bin:/usr/bin",
        "HOME": USER_HOME,
        "LANG": "C.UTF-8",
        "XDG_RUNTIME_DIR": runtime,
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
        "DISPLAY": ":0",
        "GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0",
    }


def as_user(command):
    if os.geteuid() != 0:
        return command
    uid, gid = user_ids()
    return ["setpriv", f"--reuid={uid}", f"--regid={gid}", "--init-groups"] + command


def own_path(path):
    if os.geteuid() == 0:
        uid, gid = user_ids()
        try:
            os.chown(path, uid, gid)
        except OSError as error:
            # exFAT and FAT cards (microSD) have no owners; they are mounted for the user.
            log.debug("could not hand %s to the user: %s", path, error)


def output_dir():
    """The folder recordings are saved to: the chosen one, or OUTPUT_DIR."""
    return state.prefs.get("output_dir") or OUTPUT_DIR


async def check_output_dir(path):
    """Return "" if the desktop user can save recordings in path, else why not.

    Checked as the user, not as root: the final file is written by the user's
    ffmpeg, and root could write to folders the user cannot.
    """
    if not os.path.isdir(path):
        return "not a folder"
    code, _ = await run_user(["test", "-w", path, "-a", "-x", path], timeout=5)
    return "" if code == 0 else "not writable"


async def run_user(command, timeout=10):
    """Run a short command as the desktop user and return (returncode, stdout)."""
    process = await asyncio.create_subprocess_exec(
        *as_user(command), env=clean_env(),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return -1, ""
    return process.returncode, output.decode("utf-8", "replace")


async def spawn(command, log_path, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, extra_env=None):
    with open(log_path, "ab") as log_file:
        return await asyncio.create_subprocess_exec(
            *command, env={**clean_env(), **(extra_env or {})}, stdin=stdin, stdout=stdout, stderr=log_file,
        )


async def stop_process(process, sig=signal.SIGINT):
    """Ask a process to finish, and kill it only if it does not."""
    if process is None or process.returncode is not None:
        return
    process.send_signal(sig)
    await wait_or_kill(process)


async def wait_or_kill(process):
    if process is None:
        return
    try:
        await asyncio.wait_for(process.wait(), STOP_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        log.warning("process %s ignored shutdown, killing", process.pid)
        process.kill()
        await process.wait()


def last_line(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = [line.strip() for line in handle if line.strip()]
    except OSError:
        return ""
    return lines[-1][:200] if lines else ""


def log_tail(path, count=10):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return "".join(handle.readlines()[-count:]).strip()
    except OSError:
        return ""


def file_has_data(path):
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


# Playback in the panel: Steam's browser engine plays the MP4s, but a Steam page
# cannot open local files, so the backend serves recordings over HTTP on
# 127.0.0.1 only, behind a random token, and only Recording_*.mp4 files from the
# save folders. Ranges are supported, or the player cannot seek.
RECORDING_NAME_RE = re.compile(r"^Recording_[0-9A-Za-z_.-]+\.mp4$")
RECORDING_LIST_LIMIT = 100
SERVE_CHUNK = 256 * 1024
REQUEST_LIMIT = 8192
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
# Album view: a frame from 20% into each recording (the first second is often the
# Quick Access menu closing), made only when the list is opened and kept in the
# settings folder with the recording's length, keyed by name, size and time.
THUMB_WIDTH = 320
THUMB_POSITION = 0.2
THUMB_JOBS = 2
THUMB_TIMEOUT_SEC = 20
thumb_lock = None


def thumb_dir():
    return os.path.join(SETTINGS_DIR, "thumbnails")


def thumb_paths(path):
    """(jpeg, json) cache paths for a recording, changing when the file changes."""
    info = os.stat(path)
    key = f"{os.path.basename(path)[:-4]}.{info.st_size}.{int(info.st_mtime)}"
    base = os.path.join(thumb_dir(), key)
    return base + ".jpg", base + ".json"


async def probe_duration(path):
    process = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path,
        env=clean_env(), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), THUMB_TIMEOUT_SEC)
        return float(output.strip())
    except (asyncio.TimeoutError, ValueError):
        if process.returncode is None:
            process.kill()
            await process.wait()
        return 0.0


async def ensure_thumbnail(path):
    """Make the thumbnail and length of a recording unless cached; return (jpeg, info)."""
    global thumb_lock
    if thumb_lock is None:
        thumb_lock = asyncio.Semaphore(THUMB_JOBS)
    jpeg, meta = thumb_paths(path)
    async with thumb_lock:
        if not (os.path.exists(jpeg) and os.path.exists(meta)):
            os.makedirs(thumb_dir(), exist_ok=True)
            duration = await probe_duration(path)
            position = f"{duration * THUMB_POSITION:.2f}" if duration > 0 else "0"
            process = await asyncio.create_subprocess_exec(
                *ffmpeg_base(), "-ss", position, "-i", path, "-frames:v", "1",
                "-vf", f"scale={THUMB_WIDTH}:-2", "-q:v", "5", jpeg,
                env=clean_env(), stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            try:
                await asyncio.wait_for(process.wait(), THUMB_TIMEOUT_SEC)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            with open(meta, "w", encoding="utf-8") as handle:
                json.dump({"duration": round(duration, 2)}, handle)
    try:
        with open(meta, encoding="utf-8") as handle:
            info = json.load(handle)
    except (OSError, ValueError):
        info = {}
    return (jpeg if os.path.exists(jpeg) else None), info


def prune_thumbnails(names):
    """Drop cached thumbnails of recordings that are gone or have changed."""
    keep = set()
    for name in names:
        path = find_recording(name)
        if path:
            keep.update(os.path.basename(p) for p in thumb_paths(path))
    try:
        cached = os.listdir(thumb_dir())
    except OSError:
        return
    for name in cached:
        if name not in keep:
            try:
                os.remove(os.path.join(thumb_dir(), name))
            except OSError:
                pass


def delete_recording(name):
    """Delete a recording from the save folders; True if a file was removed."""
    path = find_recording(name)
    if path is None:
        return False
    try:
        jpeg, meta = thumb_paths(path)
        os.remove(path)
    except OSError:
        log.exception("could not delete %s", path)
        return False
    for cached in (jpeg, meta):
        try:
            os.remove(cached)
        except OSError:
            pass
    if state.last_file == path:
        state.last_file = ""
    log.info("deleted %s", path)
    return True


def recording_folders():
    return list(dict.fromkeys((output_dir(), OUTPUT_DIR)))


def find_recording(name):
    """Path of a recording by file name in the save folders, or None."""
    if not RECORDING_NAME_RE.match(name or ""):
        return None
    for folder in recording_folders():
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return path
    return None


def list_recordings():
    """Recordings in the current save folder, newest first."""
    folder = output_dir()
    items = []
    try:
        names = os.listdir(folder)
    except OSError:
        return items
    for name in names:
        if not RECORDING_NAME_RE.match(name):
            continue
        try:
            info = os.stat(os.path.join(folder, name))
        except OSError:
            continue
        items.append({"name": name, "size": info.st_size, "mtime": int(info.st_mtime)})
    items.sort(key=lambda item: item["mtime"], reverse=True)
    return items[:RECORDING_LIST_LIMIT]


def parse_range(header, size):
    """(start, end) inclusive for a single "bytes=" range, None for the whole file.

    Raises ValueError when the range cannot be satisfied.
    """
    match = RANGE_RE.match(header.strip()) if header else None
    if not match or size == 0:
        return None
    first, last = match.groups()
    if not first:
        if not last:
            raise ValueError(header)
        start, end = max(size - int(last), 0), size - 1
    else:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        raise ValueError(header)
    return start, end


class RecordingServer:
    """Serves recordings to the panel's player; started on first use."""

    def __init__(self):
        self.server = None
        self.port = 0
        self.token = secrets.token_urlsafe(16)

    async def start(self):
        if self.server is None:
            self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
            self.port = self.server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{self.port}/{self.token}/"

    async def stop(self):
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def handle(self, reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            if len(head) > REQUEST_LIMIT:
                return await self.reply(writer, 431)
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ = (lines[0].split(" ") + ["", "", ""])[:3]
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.strip().lower()] = value.strip()
            if method not in ("GET", "HEAD"):
                return await self.reply(writer, 405)
            parts = target.split("?", 1)[0].split("/")
            if len(parts) not in (3, 4) or parts[0] or not hmac.compare_digest(parts[1], self.token):
                return await self.reply(writer, 404)
            kind = parts[2] if len(parts) == 4 else "video"
            path = find_recording(parts[-1])
            if path is None or kind not in ("video", "thumb", "meta"):
                return await self.reply(writer, 404)
            if kind == "video":
                return await self.send_file(writer, path, headers.get("range"), method == "HEAD")
            jpeg, info = await ensure_thumbnail(path)
            if kind == "meta":
                return await self.send_bytes(writer, json.dumps(info).encode(), "application/json")
            if jpeg is None:
                return await self.reply(writer, 404)
            await self.send_file(writer, jpeg, None, method == "HEAD", "image/jpeg")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError,
                ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def reply(self, writer, status, extra=()):
        reasons = {404: "Not Found", 405: "Method Not Allowed", 416: "Range Not Satisfiable",
                   431: "Request Header Fields Too Large"}
        lines = [f"HTTP/1.1 {status} {reasons.get(status, '')}", "Content-Length: 0", "Connection: close", *extra]
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        await writer.drain()

    async def send_bytes(self, writer, body, content_type):
        # Read by fetch() from Steam's page, another origin; the token guards it.
        lines = ["HTTP/1.1 200 OK", f"Content-Type: {content_type}", f"Content-Length: {len(body)}",
                 "Access-Control-Allow-Origin: *", "Cache-Control: no-store", "Connection: close"]
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)
        await writer.drain()

    async def send_file(self, writer, path, range_header, head_only, content_type="video/mp4"):
        size = os.path.getsize(path)
        try:
            span = parse_range(range_header, size)
        except ValueError:
            return await self.reply(writer, 416, [f"Content-Range: bytes */{size}"])
        start, end = span if span else (0, size - 1)
        length = max(end - start + 1, 0)
        lines = [
            "HTTP/1.1 206 Partial Content" if span else "HTTP/1.1 200 OK",
            f"Content-Type: {content_type}",
            "Accept-Ranges: bytes",
            f"Content-Length: {length}",
            "Cache-Control: no-store",
            "Connection: close",
        ]
        if span:
            lines.append(f"Content-Range: bytes {start}-{end}/{size}")
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        await writer.drain()
        if head_only or length == 0:
            return
        with open(path, "rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = await asyncio.to_thread(handle.read, min(SERVE_CHUNK, remaining))
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
                remaining -= len(chunk)


recording_server = RecordingServer()


class State:
    def __init__(self):
        self.status = "idle"
        self.error = ""
        self.error_detail = ""
        self.error_count = 0
        self.last_file = ""
        self.finished = 0
        self.prefs = {
            # PipeWire by default: it needs no forced composition or display changes.
            "method": "pw", "quality": DEFAULT_PRESET, "custom_mbps": DEFAULT_CUSTOM_MBPS,
            "kms_original_color": True, "fps": FRAMERATE,
            # Mono by default: most headset and built-in microphones record one voice
            # into both channels anyway.
            "mic": False, "mic_mode": "mix", "mic_mono": True,
            # Where recordings go; empty means OUTPUT_DIR.
            "output_dir": "",
        }
        self.lock = asyncio.Lock()
        self.compare_step = ""
        self.compare_cancel = False
        # Steam's color vibrancy label set, reported by the frontend when it changes,
        # so it has to outlive each recording.
        self.color_labelset = None
        self.reset_capture()

    def reset_capture(self):
        self.video = None
        self.feeder = None
        self.audio = None
        self.mic = None
        self.audio_lost = False
        self.mic_lost = False
        self.mic_mode = "mix"
        self.work_dir = ""
        self.started_at = 0.0
        self.method = ""
        self.kbps = 0
        self.size = (0, 0)
        self.monitor = None
        self.panel_saved = None
        self.hdr = ""
        self.fps = FRAMERATE


state = None


def load_prefs():
    try:
        with open(PREFS_FILE, encoding="utf-8") as handle:
            saved = json.load(handle)
    except (OSError, ValueError):
        return
    if saved.get("method") in METHODS:
        state.prefs["method"] = saved["method"]
    if saved.get("quality") in QUALITIES:
        state.prefs["quality"] = saved["quality"]
    custom = saved.get("custom_mbps")
    if isinstance(custom, int) and CUSTOM_MBPS_RANGE[0] <= custom <= CUSTOM_MBPS_RANGE[1]:
        state.prefs["custom_mbps"] = custom
    # kms_disable_mura was the setting's name before it also set Native vibrancy.
    original = saved.get("kms_original_color", saved.get("kms_disable_mura"))
    if isinstance(original, bool):
        state.prefs["kms_original_color"] = original
    if isinstance(saved.get("mic"), bool):
        state.prefs["mic"] = saved["mic"]
    if saved.get("mic_mode") in MIC_MODES:
        state.prefs["mic_mode"] = saved["mic_mode"]
    if saved.get("fps") in FRAMERATES:
        state.prefs["fps"] = saved["fps"]
    if isinstance(saved.get("mic_mono"), bool):
        state.prefs["mic_mono"] = saved["mic_mono"]
    if isinstance(saved.get("output_dir"), str) and os.path.isabs(saved["output_dir"]):
        state.prefs["output_dir"] = saved["output_dir"]


def save_prefs():
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    with open(PREFS_FILE, "w", encoding="utf-8") as handle:
        json.dump(state.prefs, handle)


# Steam Deck models by DMI product name: the LCD panel is narrower than sRGB, so
# Native color vibrancy looks paler there, and it has no mura correction.
DECK_PANELS = {"Jupiter": "lcd", "Galileo": "oled"}


def deck_panel():
    try:
        with open("/sys/class/dmi/id/product_name", encoding="utf-8") as file:
            return DECK_PANELS.get(file.read().strip(), "")
    except OSError:
        return ""


DECK_PANEL = deck_panel()


def external_display():
    """True when an output other than the built-in panel (eDP) is lit."""
    try:
        names = os.listdir(DRM_CLASS_DIR)
    except OSError:
        return False
    for name in names:
        if not name.startswith("card") or "-" not in name or "-eDP-" in name or "Writeback" in name:
            continue
        try:
            with open(os.path.join(DRM_CLASS_DIR, name, "enabled"), encoding="utf-8") as file:
                if file.read().strip() == "enabled":
                    return True
        except OSError:
            continue
    return False


def hdr_mode():
    """Which HDR conversion fits the screen being captured."""
    if external_display():
        return "external"
    return "deck" if DECK_PANEL == "oled" else "plain"


# sRGB/BT.709 primaries (x, y) and the triangle area they span on the xy chart.
SRGB_PRIMARIES = ((0.64, 0.33), (0.30, 0.60), (0.15, 0.06))
# gamescope treats a display as wide when its gamut is wider than sRGB; then Native
# is the top of the vibrancy range, otherwise the bottom (the top is "Boosted").
WIDE_GAMUT_RATIO = 1.02


def gamut_area(primaries):
    (rx, ry), (gx, gy), (bx, by) = primaries
    return abs(rx * (gy - by) + gx * (by - ry) + bx * (ry - gy)) / 2


def edid_primaries(edid):
    """Red, green and blue (x, y) from an EDID base block, or None."""
    if len(edid) < 35 or edid[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return None
    low1, low2, high = edid[25], edid[26], edid[27:35]

    def coord(index, low):
        return ((high[index] << 2) | low) / 1024

    return ((coord(0, low1 >> 6 & 3), coord(1, low1 >> 4 & 3)),
            (coord(2, low1 >> 2 & 3), coord(3, low1 & 3)),
            (coord(4, low2 >> 6 & 3), coord(5, low2 >> 4 & 3)))


def active_gamut_ratio():
    """The lit display's EDID gamut area relative to sRGB, or None if unknown."""
    try:
        names = sorted(os.listdir(DRM_CLASS_DIR))
    except OSError:
        return None
    for name in names:
        if not name.startswith("card") or "-" not in name or "Writeback" in name:
            continue
        folder = os.path.join(DRM_CLASS_DIR, name)
        try:
            with open(os.path.join(folder, "enabled"), encoding="utf-8") as file:
                if file.read().strip() != "enabled":
                    continue
            with open(os.path.join(folder, "edid"), "rb") as file:
                primaries = edid_primaries(file.read())
        except OSError:
            continue
        if primaries and gamut_area(primaries) > 0:
            return gamut_area(primaries) / gamut_area(SRGB_PRIMARIES)
    return None


def native_wideness():
    """Where Native sits on the vibrancy range for the lit display, or None.

    Read from that display's EDID, so it follows a switch to another screen; the
    label set Steam reported (or the Deck model) is the fallback. On an external
    monitor with an sRGB EDID the internal panel's 1.0 meant "Boosted" there and
    raised a 75% red from 191 to 212 (2026-11-19).
    """
    ratio = active_gamut_ratio()
    if ratio is not None:
        return 1.0 if ratio > WIDE_GAMUT_RATIO else 0.0
    labelset = color_labelset()
    return NATIVE_WIDENESS[labelset] if labelset is not None else None


def color_labelset():
    if state.color_labelset in NATIVE_WIDENESS:
        return state.color_labelset
    return PANEL_LABELSET.get(DECK_PANEL)


def original_color_props(output_hdr=False):
    """The panel settings "Record original colors" changes here; empty if none apply."""
    if output_hdr:
        # External display in HDR mode: colorimetric (0.0) puts SDR colors into
        # the BT.2020 output exactly. Toward "native" gamescope stretches them to
        # the display's EDID gamut, which on a monitor with a wrong EDID turned
        # red, green and blue into other hues.
        return {GAMUT_WIDENESS_PROP: float_bits(0.0)}
    props = {}
    if DECK_PANEL == "oled" and not external_display():
        props[MURA_DISABLED_PROP] = "1"
    native = native_wideness()
    if native is not None:
        props[GAMUT_WIDENESS_PROP] = float_bits(native)
    return props


def original_color_note():
    """Which description fits: the screen turns paler or more vivid, with or without mura."""
    props = original_color_props()
    if not props:
        return ""
    if MURA_DISABLED_PROP in props:
        return "oled"
    ratio = active_gamut_ratio()
    if ratio is not None and abs(ratio - 1) <= WIDE_GAMUT_RATIO - 1:
        # An sRGB display: Native changes nothing visible.
        return "plain"
    return "vivid" if props.get(GAMUT_WIDENESS_PROP) == float_bits(1.0) else "pale"


def free_gb():
    try:
        target = output_dir() if os.path.isdir(output_dir()) else USER_HOME
        return round(shutil.disk_usage(target).free / 1024 ** 3, 1)
    except OSError:
        return None


def snapshot():
    # The video is already being written while the start check runs.
    capturing = state.status in ("starting", "recording") and state.started_at > 0
    preset = state.prefs["quality"]
    return {
        "status": state.status,
        "error": state.error,
        "error_detail": state.error_detail,
        "error_count": state.error_count,
        "last_file": state.last_file,
        "finished": state.finished,
        "prefs": state.prefs,
        "preview_kbps": {str(h): bitrate_for(preset, h, state.prefs["custom_mbps"]) for h in (800, 1080)},
        "custom_mbps_range": list(CUSTOM_MBPS_RANGE),
        "kbps": state.kbps,
        "hdr": state.hdr,
        "fps": state.fps,
        "compare_step": state.compare_step,
        "compare_steps": len(COMPARE_METHODS),
        "compare_index": COMPARE_METHODS.index(state.compare_step) + 1 if state.compare_step else 0,
        "size": list(state.size),
        "capturing": capturing,
        "elapsed": round(time.monotonic() - state.started_at, 2) if capturing else 0,
        "free_gb": free_gb(),
        "original_color": original_color_note(),
        "output_dir": output_dir(),
        "output_dir_custom": bool(state.prefs.get("output_dir")),
    }


def fail(code, detail=""):
    state.error = code
    state.error_detail = detail
    state.error_count += 1
    log.error("%s: %s", code, detail)


async def display_size():
    code, output = await run_user(["xdpyinfo"], timeout=5)
    return parse_dimensions(output) if code == 0 else FALLBACK_SIZE


async def default_sink():
    code, output = await run_user(["pactl", "get-default-sink"], timeout=5)
    return output.strip() if code == 0 else ""


async def default_source():
    """Return the default microphone, ignoring speaker monitors."""
    code, output = await run_user(["pactl", "get-default-source"], timeout=5)
    source = output.strip() if code == 0 else ""
    return "" if source.endswith(".monitor") else source


async def set_composite_force(enabled):
    code, output = await run_user(["gamescopectl", "composite_force", "1" if enabled else "0"])
    if code != 0:
        log.warning("composite_force failed: %s", output.strip())


async def read_root_prop(name):
    code, output = await run_user(["xprop", "-root", name], timeout=5)
    match = ROOT_PROP_RE.search(output.strip()) if code == 0 else None
    # Several values come back comma-separated without spaces, as write_root_prop takes them.
    return ",".join(part.strip() for part in match.group(1).split(",")) if match else None


async def write_root_prop(name, value):
    """Set a 32-bit root property, or remove it when value is None."""
    if value is None:
        command = ["xprop", "-root", "-remove", name]
    else:
        values = str(value).split(",")
        command = ["xprop", "-root", "-f", name, "32" + "c" * len(values), "-set", name, ",".join(values)]
    code, output = await run_user(command, timeout=5)
    if code != 0:
        log.warning("could not update %s: %s", name, output.strip())


def write_pq_shader(white_nits, folder=None):
    """Copy the PQ shader into the settings folder with this recording's white level."""
    folder = folder or SETTINGS_DIR
    with open(PQ_SHADER, encoding="utf-8") as handle:
        text = handle.read()
    text = re.sub(r"const float white_nits = [0-9.]+;", f"const float white_nits = {white_nits:.1f};", text)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, SHADER_NAME), "w", encoding="utf-8") as handle:
        handle.write(text)


async def read_float_prop(name, default):
    value = await read_root_prop(name)
    try:
        number = struct.unpack("f", struct.pack("I", int(value)))[0]
    except (TypeError, ValueError, struct.error):
        return default
    return number if 1.0 <= number <= 10000.0 else default


async def use_original_colors(props=None):
    """Switch off the panel processing and return the values to put back.

    Besides mura and vibrancy, night mode is turned off and the color temperature
    set to native, each only when Steam has it changed.
    """
    saved = {}
    props = dict(original_color_props() if props is None else props)
    night = await read_root_prop(NIGHT_MODE_PROP)
    if night and night.split(",")[0] != "0":
        props[NIGHT_MODE_PROP] = ",".join(["0"] + night.split(",")[1:])
    white = await read_root_prop(VIRTUAL_WHITE_PROP)
    if white and any(value != "0" for value in white.split(",")):
        props[VIRTUAL_WHITE_PROP] = NATIVE_WHITE
    for name, value in props.items():
        saved[name] = await read_root_prop(name)
        await write_root_prop(name, value)
    return saved


async def restore_panel(saved):
    for name, value in (saved or {}).items():
        await write_root_prop(name, value)


def note_display_change(panel_saved):
    """Record that composite_force is on (and panel processing off) until restore_display runs."""
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(RESTORE_FILE, "w", encoding="utf-8") as handle:
            json.dump({"composite_force": True, "panel": panel_saved}, handle)
    except OSError:
        log.exception("could not record display changes")


async def restore_display(panel_saved):
    await restore_panel(panel_saved)
    await set_composite_force(False)
    try:
        os.remove(RESTORE_FILE)
    except FileNotFoundError:
        pass


async def restore_after_crash():
    """Undo display changes left by a capture that never got to clean up."""
    try:
        with open(RESTORE_FILE, encoding="utf-8") as handle:
            pending = json.load(handle)
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        pending = {"composite_force": True}
    log.warning("restoring display settings left by an interrupted capture")
    panel = pending.get("panel")
    legacy = pending.get("mura")
    if panel is None and isinstance(legacy, dict):
        panel = {MURA_DISABLED_PROP: legacy.get("value")}
    await restore_display(panel)


async def start_video(method, video_path, log_path, kbps, size, hdr="", fps=FRAMERATE):
    """Start the capture and return (video process, feeder process)."""
    if method == "kms" and hdr:
        return await spawn(kms_hdr_cmd(video_path, kbps, fps, hdr), log_path, extra_env=VULKAN_ENCODE_ENV), None
    if method == "kms":
        return await spawn(kms_cmd(video_path, kbps, fps), log_path), None
    read_fd, write_fd = os.pipe()
    try:
        feeder = await spawn(as_user(pw_source_cmd(*size, fps)), log_path, stdout=write_fd)
        video = await spawn(as_user(pw_encode_cmd(video_path, *size, kbps, fps)), log_path, stdin=read_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)
    return video, feeder


async def start_capture(method, work_dir, sink, source, kbps, size, mic_mono=False, hdr="",
                        fps=FRAMERATE):
    """Start audio, microphone and video together, retrying KMS from scratch if it exits early."""
    log_path = os.path.join(work_dir, "capture.log")
    attempts = KMS_START_ATTEMPTS if method == "kms" else 1
    for attempt in range(1, attempts + 1):
        audio = mic = None
        if sink:
            audio = await spawn(as_user(audio_cmd(sink, os.path.join(work_dir, "audio.mka"))),
                                os.path.join(work_dir, "audio.log"))
        else:
            log.warning("no default audio sink, recording video only")
        if source:
            mic = await spawn(as_user(mic_cmd(source, os.path.join(work_dir, "mic.mka"), mic_mono)),
                              os.path.join(work_dir, "mic.log"))
        # A count left by a failed attempt must not pass for this one.
        try:
            os.remove(os.path.join(work_dir, PROGRESS_NAME))
        except FileNotFoundError:
            pass
        # The file starts here (a retry rewrites it), so the timer does too.
        state.started_at = time.monotonic()
        video, feeder = await start_video(method, os.path.join(work_dir, "video.mkv"),
                                          log_path, kbps, size, hdr, fps)
        await asyncio.sleep(START_CHECK_SEC)
        stalled = False
        if method == "kms" and video.returncode is None:
            video_path = os.path.join(work_dir, "video.mkv")

            def got_frames():
                frames = frames_done(work_dir)
                # Without a progress file, data in the video file has to do.
                return frames > 0 if frames is not None else file_has_data(video_path)

            deadline = time.monotonic() + KMS_FIRST_DATA_SEC - START_CHECK_SEC
            while not got_frames() and video.returncode is None and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
            if video.returncode is None and not got_frames():
                # Stuck inside the driver: it would ignore SIGINT, and holds nothing to keep.
                stalled = True
                video.kill()
                await video.wait()
        if not stalled and video.returncode is None and (feeder is None or feeder.returncode is None):
            return video, feeder, audio, mic
        state.started_at = 0.0
        await stop_process(feeder)
        await stop_process(video)
        await stop_process(audio)
        await stop_process(mic)
        if stalled:
            log.warning("capture attempt %d/%d: %s", attempt, attempts, KMS_NO_FRAMES)
            raise RuntimeError(KMS_NO_FRAMES)
        tail = log_tail(log_path)
        log.warning("capture attempt %d/%d failed:\n%s", attempt, attempts, tail)
        if not any(text in tail for text in RETRYABLE_KMS_ERRORS):
            break
        if attempt < attempts:
            await asyncio.sleep(KMS_RETRY_DELAY_SEC)
    raise RuntimeError(last_line(log_path) or "capture exited at start")


async def start_recording(method=None):
    async with state.lock:
        if state.status != "idle":
            return
        state.status = "starting"
        state.started_at = 0.0
        state.error = ""
        state.error_detail = ""
        method = method or state.prefs["method"]
        forced_composite = False
        panel_saved = None
        hdr = ""
        fps = FRAMERATE
        work_dir = ""
        try:
            fps = state.prefs["fps"]
            size = await display_size()
            kbps = bitrate_for(state.prefs["quality"], size[1], state.prefs["custom_mbps"])
            target = output_dir()
            if state.prefs.get("output_dir"):
                # Never create a chosen folder: a missing card would put the
                # recording on the internal drive under its mount point.
                if not os.path.isdir(target):
                    raise RuntimeError(f"save folder not found: {target}")
            else:
                os.makedirs(target, exist_ok=True)
                own_path(target)
            work_dir = os.path.join(target, time.strftime(".rec-%Y%m%d-%H%M%S"))
            os.makedirs(work_dir)
            own_path(work_dir)
            output_hdr = external_display() and await read_root_prop(HDR_OUTPUT_PROP) == "1"
            if method == "kms":
                note_display_change(None)
                await set_composite_force(True)
                forced_composite = True
                # Skipped where neither setting applies (unknown vibrancy label set
                # and no mura correction).
                original = original_color_props(output_hdr)
                if state.prefs["kms_original_color"] and original:
                    panel_saved = await use_original_colors(original)
                    note_display_change(panel_saved)
                await asyncio.sleep(COMPOSITE_SETTLE_SEC)
            # KMS switches to the HDR path; for PipeWire gamescope already sends SDR.
            # hdr is "" for SDR, otherwise how the picture becomes SDR.
            hdr = ""
            app_hdr = await read_root_prop(HDR_APP_PROP) == "1"
            if method != "kms":
                hdr = "gamescope" if app_hdr else ""
            elif output_hdr:
                hdr = "external" if app_hdr else "external_sdr"
            elif app_hdr:
                hdr = hdr_mode()
            if hdr in PQ_MODES:
                white = HDR_WHITE_NITS if hdr == "external" else \
                    await read_float_prop(SDR_ON_HDR_PROP, SDR_ON_HDR_DEFAULT_NITS)
                write_pq_shader(white)
            # Shown on the card already while the start check runs.
            state.kbps, state.size, state.fps, state.hdr = kbps, size, fps, hdr
            sink = await default_sink()
            source = ""
            if state.prefs["mic"]:
                source = await default_source()
                if not source:
                    log.warning("microphone enabled but no input device found")
            try:
                video, feeder, audio, mic = await start_capture(method, work_dir, sink, source, kbps, size,
                                                                state.prefs["mic_mono"], hdr, fps)
            except RuntimeError:
                if not hdr or method != "kms":
                    raise
                # Keep recording even if the HDR conversion cannot start on this system.
                log.warning("HDR capture failed to start, falling back to the SDR path")
                hdr = state.hdr = ""
                video, feeder, audio, mic = await start_capture(method, work_dir, sink, source, kbps, size,
                                                                state.prefs["mic_mono"], hdr, fps)
        except Exception as error:
            log.exception("start failed")
            if forced_composite:
                await restore_display(panel_saved)
            if work_dir:
                await asyncio.to_thread(shutil.rmtree, work_dir, True)
            if str(error) == KMS_NO_FRAMES:
                fail("kms_no_frames")
            else:
                fail("start_failed", str(error))
            state.status = "idle"
            return
        state.video, state.feeder, state.audio, state.mic = video, feeder, audio, mic
        state.mic_mode = state.prefs["mic_mode"]
        state.work_dir, state.method, state.kbps, state.size = work_dir, method, kbps, size
        state.panel_saved = panel_saved
        state.hdr = hdr
        state.fps = fps
        if method == "kms" and not hdr:
            try:
                open(os.path.join(work_dir, FULL_RANGE_MARK), "w").close()
            except OSError:
                log.exception("could not mark the capture as full range")
        state.status = "recording"
        state.monitor = asyncio.create_task(monitor_capture())
        if state.prefs["mic"] and mic is None:
            fail("mic_missing")
        log.info("recording: %s %sx%s %s fps %s kbps%s", method, size[0], size[1], fps, kbps, f" {hdr}" if hdr else "")


async def monitor_capture():
    """Save what exists if the capture dies, for example when gamescope restarts.

    A sound process that stops does not end the recording, but is reported.
    """
    while state.status == "recording":
        await asyncio.sleep(1)
        if state.status != "recording":
            return
        if state.audio is not None and state.audio.returncode is not None and not state.audio_lost:
            state.audio_lost = True
            fail("audio_stopped")
        if state.mic is not None and state.mic.returncode is not None and not state.mic_lost:
            state.mic_lost = True
            fail("mic_stopped")
        feeder_gone = state.feeder is not None and state.feeder.returncode is not None
        if state.video is not None and (state.video.returncode is not None or feeder_gone):
            await finish_recording("capture_stopped")
            return


async def stop_capture():
    if state.feeder is not None:
        # gst first: EOS ends the stream and ffmpeg then sees end of input.
        await stop_process(state.feeder)
        await wait_or_kill(state.video)
    else:
        await stop_process(state.video)
    await stop_process(state.audio)
    await stop_process(state.mic)


async def finish_recording(error=""):
    async with state.lock:
        if state.status != "recording":
            return
        state.status = "saving"
        work_dir, method = state.work_dir, state.method
        detail = last_line(os.path.join(work_dir, "capture.log")) if error else ""
        try:
            await stop_capture()
        finally:
            if method == "kms":
                await restore_display(state.panel_saved)
        try:
            output = await mux_recording(work_dir, method, state.mic_mode)
            state.last_file = output
            state.finished += 1
            if error:
                fail(error, detail)
        except Exception as mux_error:
            log.exception("save failed")
            fail("save_failed", f"{mux_error} ({work_dir})")
        state.reset_capture()
        state.status = "idle"


async def mux_recording(work_dir, method, mic_mode="mix", stamp=None):
    video = os.path.join(work_dir, "video.mkv")
    audio = os.path.join(work_dir, "audio.mka")
    mic = os.path.join(work_dir, "mic.mka")
    if not file_has_data(video):
        raise RuntimeError("no video was captured")
    for path in (video, audio, mic):
        if os.path.exists(path):
            own_path(path)
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    # Next to the capture folder, so a recovered capture stays where it was recorded.
    output = os.path.join(os.path.dirname(work_dir), f"Recording_{stamp}_{method}.mp4")
    with_audio = audio if file_has_data(audio) else None
    with_mic = mic if file_has_data(mic) else None
    full_range = os.path.exists(os.path.join(work_dir, FULL_RANGE_MARK))
    command = mux_cmd(video, with_audio, output, with_mic, mic_mode, full_range)
    code, message = await run_user(command, timeout=MUX_TIMEOUT_SEC)
    if code != 0:
        raise RuntimeError(message.strip()[-200:] or f"ffmpeg exited with {code}")
    await asyncio.to_thread(shutil.rmtree, work_dir, True)
    return output


async def run_comparison(seconds=COMPARE_SECONDS):
    """Record PipeWire, then KMS, for a few seconds each; stop() ends it early."""
    state.compare_cancel = False
    try:
        for method in COMPARE_METHODS:
            if state.compare_cancel:
                break
            state.compare_step = method
            await start_recording(method)
            if state.status != "recording":
                break
            started = time.monotonic()
            while state.status == "recording" and not state.compare_cancel \
                    and time.monotonic() - started < seconds:
                await asyncio.sleep(0.2)
            await finish_recording()
            if not state.compare_cancel and method != COMPARE_METHODS[-1]:
                # Let gamescope settle after the PipeWire stream closes.
                await asyncio.sleep(COMPARE_GAP_SEC)
    finally:
        state.compare_step = ""
        state.compare_cancel = False


async def recover_leftovers():
    """Save capture folders left by a crash, so the recording is not lost."""
    # The default folder too, in case the save folder changed since the crash.
    folders = list(dict.fromkeys((output_dir(), OUTPUT_DIR)))
    found = []
    for folder in folders:
        try:
            found += [(name, folder) for name in sorted(os.listdir(folder)) if name.startswith(".rec-")]
        except OSError:
            continue
    if not found:
        return
    async with state.lock:
        if state.status != "idle":
            return
        state.status = "saving"
        try:
            for name, folder in found:
                work_dir = os.path.join(folder, name)
                if not file_has_data(os.path.join(work_dir, "video.mkv")):
                    await asyncio.to_thread(shutil.rmtree, work_dir, True)
                    continue
                try:
                    state.last_file = await mux_recording(
                        work_dir, "recovered", state.prefs["mic_mode"], name[len(".rec-"):])
                    state.finished += 1
                    log.info("recovered %s", state.last_file)
                except Exception as error:
                    log.exception("could not recover %s", work_dir)
                    fail("save_failed", f"{error} ({work_dir})")
        finally:
            state.status = "idle"


class Plugin:
    async def _main(self):
        global state
        state = State()
        load_prefs()
        await restore_after_crash()
        asyncio.create_task(recover_leftovers())

    async def _unload(self):
        await recording_server.stop()
        if state is not None:
            # Also keeps a running comparison from starting its next step.
            state.compare_cancel = True
            await finish_recording()

    async def get_state(self):
        return snapshot()

    async def list_recordings(self):
        """The save folder's recordings and the address the player loads them from.

        Thumbnails are made only now, as the list asks for them; stale ones go.
        """
        items = list_recordings()
        await asyncio.to_thread(prune_thumbnails, [item["name"] for item in items])
        return {"base_url": await recording_server.start(), "items": items}

    async def delete_recording(self, name):
        deleted = isinstance(name, str) and await asyncio.to_thread(delete_recording, name)
        return {"deleted": bool(deleted), "items": list_recordings()}

    async def clear_error(self):
        state.error = ""
        state.error_detail = ""
        return snapshot()

    async def set_method(self, method):
        if method in METHODS and state.status == "idle":
            state.prefs["method"] = method
            save_prefs()
        return snapshot()

    async def set_quality(self, quality):
        if quality in QUALITIES and state.status == "idle":
            state.prefs["quality"] = quality
            save_prefs()
        return snapshot()

    async def set_custom_mbps(self, mbps):
        low, high = CUSTOM_MBPS_RANGE
        if isinstance(mbps, (int, float)) and state.status == "idle":
            state.prefs["custom_mbps"] = int(min(max(round(mbps), low), high))
            save_prefs()
        return snapshot()

    async def set_fps(self, fps):
        if fps in FRAMERATES and state.status == "idle":
            state.prefs["fps"] = fps
            save_prefs()
        return snapshot()

    async def set_mic(self, enabled):
        if state.status == "idle":
            state.prefs["mic"] = enabled is True
            save_prefs()
        return snapshot()

    async def set_mic_mono(self, enabled):
        if state.status == "idle":
            state.prefs["mic_mono"] = enabled is True
            save_prefs()
        return snapshot()

    async def set_mic_mode(self, mode):
        if mode in MIC_MODES and state.status == "idle":
            state.prefs["mic_mode"] = mode
            save_prefs()
        return snapshot()

    async def set_color_labelset(self, labelset):
        state.color_labelset = labelset if isinstance(labelset, int) else None
        return snapshot()

    async def set_output_dir(self, path):
        """Save recordings to path, or to the default folder when path is empty."""
        if state.status != "idle":
            return snapshot()
        path = path if isinstance(path, str) else ""
        if path:
            path = os.path.realpath(path)
            problem = await check_output_dir(path)
            if problem:
                fail("output_invalid", f"{path}: {problem}")
                return snapshot()
        state.prefs["output_dir"] = path if path != OUTPUT_DIR else ""
        save_prefs()
        return snapshot()

    async def set_kms_original_color(self, enabled):
        if state.status == "idle":
            state.prefs["kms_original_color"] = enabled is True
            save_prefs()
        return snapshot()

    async def start(self):
        # Between a comparison's two recordings the status is idle for a moment.
        if not state.compare_step:
            await start_recording()
        return snapshot()

    async def stop(self):
        if state.compare_step:
            state.compare_cancel = True
        await finish_recording()
        return snapshot()

    async def compare(self):
        if state.status == "idle" and not state.compare_step:
            state.compare_step = COMPARE_METHODS[0]
            asyncio.create_task(run_comparison())
        return snapshot()
