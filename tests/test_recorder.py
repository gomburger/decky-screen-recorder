"""Run with: python3 -I tests/test_recorder.py

The lifecycle test replaces the screen sources with a synthetic ffmpeg source,
so it never opens the gamescope stream that can crash a running game.
"""

import asyncio
import functools
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import main  # noqa: E402


def synthetic_video(output, kbps, fps=60):
    return main.ffmpeg_base() + [
        "-re", "-vaapi_device", main.VAAPI_DEVICE, "-f", "lavfi", "-i", f"testsrc2=s=1280x800:r={fps}",
        "-vf", "format=nv12,hwupload",
    ] + main.encode_args(kbps, fps) + [output]


@functools.lru_cache(maxsize=None)
def rgb10_swapped():
    """Whether this system's Vulkan upload swaps red and blue of x2rgb10, as main.rgb10_order finds."""
    output = subprocess.run(main.rgb10_check_cmd(), capture_output=True).stdout
    order = main.rgb10_order_from(output[:4])
    assert order, "10-bit RGB does not reach libplacebo here"
    return order == "swapped"


def synthetic_audio(_sink, output):
    return main.ffmpeg_base() + ["-re", "-f", "lavfi", "-i", "sine"] + main.AUDIO_ARGS + [output]


def hdr_edid(max_code, eotfs=0b101):
    """A 256-byte EDID whose CTA block holds HDR static metadata with that max code."""
    data = bytearray(256)
    block = bytearray(128)
    block[0], block[1], block[2] = 0x02, 0x03, 4 + 7
    block[4:11] = bytes([(7 << 5) | 6, 6, eotfs, 0x01, max_code, max_code, 0])
    data[128:] = block
    return bytes(data)


def panel_edid(vendor, product, model="", hdr=True):
    """An EDID with a base block naming the panel, and HDR (PQ, ~1000 nits) if asked."""
    data = bytearray(hdr_edid(139) if hdr else bytes(256))
    data[:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    code = sum((ord(letter) - 64) << shift for letter, shift in zip(vendor, (10, 5, 0)))
    data[8], data[9], data[10], data[11] = code >> 8, code & 255, product & 255, product >> 8
    data[54:59] = b"\x00\x00\x00\xfc\x00"
    data[59:72] = (model + "\n").ljust(13).encode()[:13]
    return bytes(data)


class Builders(unittest.TestCase):
    def test_pipewire_is_default(self):
        self.assertEqual(main.State().prefs["method"], "pw")

    def test_custom_bitrate(self):
        self.assertEqual(main.bitrate_for("custom", 800, 40), 40000)
        self.assertEqual(main.bitrate_for("custom", 2160, 40), 40000)
        main.state = main.State()
        plugin = main.Plugin()
        prefs_file = main.PREFS_FILE
        temp = tempfile.TemporaryDirectory()
        main.PREFS_FILE = os.path.join(temp.name, "prefs.json")
        try:
            snap = asyncio.run(plugin.set_quality("custom"))
            self.assertEqual(snap["prefs"]["quality"], "custom")
            for value, stored in ((40, 40), (99, 50), (1, 2), (12.6, 13)):
                snap = asyncio.run(plugin.set_custom_mbps(value))
                self.assertEqual(snap["prefs"]["custom_mbps"], stored)
            self.assertEqual(snap["preview_kbps"], {"800": 13000, "1080": 13000})
            # Saved and read back.
            main.state = main.State()
            main.load_prefs()
            self.assertEqual((main.state.prefs["quality"], main.state.prefs["custom_mbps"]), ("custom", 13))
        finally:
            main.PREFS_FILE = prefs_file
            temp.cleanup()

    def test_parse_range(self):
        self.assertIsNone(main.parse_range(None, 1000))
        self.assertEqual(main.parse_range("bytes=0-", 1000), (0, 999))
        self.assertEqual(main.parse_range("bytes=100-199", 1000), (100, 199))
        self.assertEqual(main.parse_range("bytes=900-5000", 1000), (900, 999))
        self.assertEqual(main.parse_range("bytes=-100", 1000), (900, 999))
        for bad in ("bytes=1000-", "bytes=5-2", "bytes=-"):
            with self.assertRaises(ValueError, msg=bad):
                main.parse_range(bad, 1000)

    def test_recording_server(self):
        """Serves only Recording_*.mp4 from the save folder, behind the token, with ranges."""
        temp = tempfile.TemporaryDirectory()
        output = main.OUTPUT_DIR
        main.OUTPUT_DIR = temp.name
        main.state = main.State()
        data = bytes(range(256)) * 40
        with open(os.path.join(temp.name, "Recording_20261119-120000_pw.mp4"), "wb") as file:
            file.write(data)
        with open(os.path.join(temp.name, "secret.txt"), "w") as file:
            file.write("x")
        server = main.RecordingServer()

        async def get(path, extra=""):
            base = await server.start()
            port = int(base.split(":")[2].split("/")[0])
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n{extra}\r\n".encode())
            await writer.drain()
            response = await reader.read()
            writer.close()
            head, _, body = response.partition(b"\r\n\r\n")
            return int(head.split()[1]), head.decode(), body

        async def scenario():
            token = server.token
            name = "Recording_20261119-120000_pw.mp4"
            results = [
                await get(f"/{token}/{name}"),
                await get(f"/{token}/{name}", "Range: bytes=10-19\r\n"),
                await get(f"/{token}/{name}", "Range: bytes=99999-\r\n"),
                await get(f"/wrong/{name}"),
                await get(f"/{token}/secret.txt"),
                await get(f"/{token}/..%2Fsecret.txt"),
            ]
            await server.stop()
            return results

        try:
            full, part, unsatisfiable, wrong_token, other_file, traversal = asyncio.run(scenario())
            self.assertEqual((full[0], full[2]), (200, data))
            self.assertEqual((part[0], part[2]), (206, data[10:20]))
            self.assertIn(f"Content-Range: bytes 10-19/{len(data)}", part[1])
            self.assertEqual(unsatisfiable[0], 416)
            self.assertEqual([wrong_token[0], other_file[0], traversal[0]], [404, 404, 404])
            self.assertEqual([item["name"] for item in main.list_recordings()], ["Recording_20261119-120000_pw.mp4"])
        finally:
            main.OUTPUT_DIR = output
            temp.cleanup()

    def test_thumbnails_and_delete(self):
        """A thumbnail and length are made on request, cached, pruned and deleted with the file."""
        temp = tempfile.TemporaryDirectory()
        output, settings = main.OUTPUT_DIR, main.SETTINGS_DIR
        main.OUTPUT_DIR = os.path.join(temp.name, "videos")
        main.SETTINGS_DIR = os.path.join(temp.name, "settings")
        os.makedirs(main.OUTPUT_DIR)
        main.state = main.State()
        names = ["Recording_20261119-120000_pw.mp4", "Recording_20261119-120100_kms.mp4"]
        for name in names:
            subprocess.run(main.ffmpeg_base() + ["-f", "lavfi", "-i", "testsrc2=s=640x400:r=30:d=2",
                                                 "-c:v", "libx264", "-preset", "ultrafast",
                                                 os.path.join(main.OUTPUT_DIR, name)], check=True)
        try:
            path = main.find_recording(names[0])
            jpeg, info = asyncio.run(main.ensure_thumbnail(path))
            self.assertTrue(os.path.getsize(jpeg) > 0)
            self.assertAlmostEqual(info["duration"], 2.0, delta=0.2)
            # Cached: same file, no new work.
            mtime = os.path.getmtime(jpeg)
            self.assertEqual(asyncio.run(main.ensure_thumbnail(path))[0], jpeg)
            self.assertEqual(os.path.getmtime(jpeg), mtime)
            # A leftover from a recording that is gone is pruned.
            stale = os.path.join(main.thumb_dir(), "Recording_old.1.1.jpg")
            open(stale, "w").close()
            main.prune_thumbnails(names)
            self.assertFalse(os.path.exists(stale))
            self.assertTrue(os.path.exists(jpeg))
            # Deleting removes the file and its cached thumbnail, only for recordings.
            main.state.last_file = path
            self.assertTrue(main.delete_recording(names[0]))
            self.assertFalse(os.path.exists(path) or os.path.exists(jpeg))
            self.assertEqual(main.state.last_file, "")
            self.assertFalse(main.delete_recording("../settings/x.mp4"))
            self.assertFalse(main.delete_recording(names[0]))
            self.assertEqual([item["name"] for item in main.list_recordings()], [names[1]])
        finally:
            main.OUTPUT_DIR, main.SETTINGS_DIR = output, settings
            temp.cleanup()

    def test_latest_recording(self):
        """The newest recording in the save folder, looked up only when asked to."""
        temp = tempfile.TemporaryDirectory()
        output = main.OUTPUT_DIR
        main.OUTPUT_DIR = temp.name
        main.state = main.State()
        try:
            self.assertEqual(main.latest_recording(temp.name), "")
            self.assertEqual(main.latest_recording(os.path.join(temp.name, "missing")), "")
            paths = [os.path.join(temp.name, name) for name in
                     ("Recording_20261119-120000_pw.mp4", "Recording_20261119-120100_kms.mp4", "other.mp4")]
            for n, path in enumerate(paths):
                open(path, "w").close()
                os.utime(path, (1000 + n, 1000 + n))
            # other.mp4 is newest but not a recording.
            self.assertEqual(main.latest_recording(temp.name), paths[1])
            # A poll does not look at the folder; a refresh does.
            self.assertEqual(main.snapshot()["latest_file"], "")
            main.refresh_latest()
            self.assertEqual(main.snapshot()["latest_file"], paths[1])
            os.remove(paths[1])
            self.assertEqual(main.snapshot()["latest_file"], paths[1])
            main.refresh_latest()
            self.assertEqual(main.snapshot()["latest_file"], paths[0])
            # From a list already read: its first item, or none for an empty list.
            main.refresh_latest([])
            self.assertEqual(main.snapshot()["latest_file"], "")
            main.refresh_latest(main.list_recordings())
            self.assertEqual(main.snapshot()["latest_file"], paths[0])
        finally:
            main.OUTPUT_DIR = output
            temp.cleanup()

    def test_steam_bitrate_table(self):
        self.assertEqual(main.bitrate_for("preset_low", 800), 3750)
        self.assertEqual(main.bitrate_for("preset_default", 720), 7500)
        self.assertEqual(main.bitrate_for("preset_medium", 1080), 9000)
        self.assertEqual(main.bitrate_for("preset_ultra", 1440), 32000)
        self.assertEqual(main.bitrate_for("preset_ultra", 2160), 50000)
        self.assertEqual(main.bitrate_for("unknown", 800), 7500)

    def test_color_filters(self):
        self.assertIn(f"hwmap=derive_device=vaapi,{main.KMS_COLOR_FILTER}", main.kms_cmd("v.mkv", 1))
        self.assertIn(f"{main.PW_COLOR_FILTER},format=nv12,hwupload", main.pw_encode_cmd("v.mkv", 1280, 800, 1))

    def test_hdr_command(self):
        command = main.kms_hdr_cmd("v.mkv", 12000)
        video_filter = command[command.index("-vf") + 1]
        self.assertTrue(video_filter.startswith("hwmap=derive_device=vaapi,"))
        self.assertIn("hwdownload,format=nv12,hwupload", video_filter)
        self.assertIn("vulkan=vk", command)
        self.assertNotIn("saturation", video_filter)
        # The copy written for the recording, never the template or the save folder.
        self.assertIn(f"custom_shader_path={os.path.join(main.SETTINGS_DIR, main.DECK_SHADER_NAME)}", video_filter)
        self.assertIn("color_primaries=bt2020", video_filter)
        self.assertIn("h264_vulkan", command)

    def test_hdr_command_rgb10(self):
        """The shader modes take 10-bit RGB; the plain mode stays on NV12."""
        for mode in main.HDR_SHADER_MODES:
            command = main.kms_hdr_cmd("v.mkv", 1, 60, mode, rgb10=True)
            video_filter = command[command.index("-vf") + 1]
            self.assertIn(f"{main.HDR_RGB10_DOWNLOAD_FILTER},hwupload,", video_filter, mode)
            self.assertIn("colorspace=gbr", video_filter, mode)
            self.assertNotIn("format=nv12,hwupload", video_filter, mode)
            # 10-bit needs no debanding; the NV12 fallback gets it.
            self.assertNotIn("deband", video_filter, mode)
            command = main.kms_hdr_cmd("v.mkv", 1, 60, mode)
            self.assertIn(main.DEBAND, command[command.index("-vf") + 1], mode)
        plain = main.kms_hdr_cmd("v.mkv", 1, 60, "plain", rgb10=True)
        self.assertIn("hwdownload,format=nv12,hwupload", plain[plain.index("-vf") + 1])
        self.assertIn(main.DEBAND, plain[plain.index("-vf") + 1])

    def test_rgb10_order(self):
        self.assertEqual(main.rgb10_order_from(bytes((253, 1, 0, 255))), "ok")
        self.assertEqual(main.rgb10_order_from(bytes((0, 2, 251, 255))), "swapped")
        self.assertEqual(main.rgb10_order_from(bytes((120, 120, 120, 255))), "")
        self.assertEqual(main.rgb10_order_from(b""), "")
        # Whatever this system does, the shader puts red back where it was.
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        main.write_deck_shader(1000, 304.5, temp.name, swap_rb=rgb10_swapped())
        shader = os.path.join(temp.name, main.DECK_SHADER_NAME)
        with open(shader, encoding="utf-8") as file:
            self.assertEqual("//!HOOK RGB" in file.read(), rgb10_swapped())

        def convert(rgb, rgb10):
            if rgb10:
                upload = f"format=x2rgb10le,hwupload,{main.rgb_input(main.HDR_INPUT_FILTER)}"
            else:
                upload = f"scale=out_color_matrix=bt709:out_range=pc,format=nv12,hwupload,{main.HDR_INPUT_FILTER}"
            source = "color=c=0x{:02x}{:02x}{:02x}:s=64x64:d=1,format=rgb24".format(*rgb)
            result = subprocess.run(main.ffmpeg_base() + [
                "-init_hw_device", "vulkan=vk", "-filter_hw_device", "vk", "-f", "lavfi", "-i", source,
                "-frames:v", "1", "-vf",
                f"{upload},{main.deck_tonemap_filter(shader)},hwdownload,format=nv12,"
                "scale=in_range=tv:in_color_matrix=bt709:out_range=pc,format=rgb24",
                "-f", "rawvideo", "-"], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr[-300:])
            at = (32 * 64 + 32) * 3
            return tuple(result.stdout[at:at + 3])

        main.write_deck_shader(1000, 304.5, temp.name)
        reference = [convert(rgb, False) for rgb in ((200, 40, 20), (30, 60, 200))]
        main.write_deck_shader(1000, 304.5, temp.name, swap_rb=rgb10_swapped())
        for rgb, expected in zip(((200, 40, 20), (30, 60, 200)), reference):
            got = convert(rgb, True)
            self.assertLessEqual(max(abs(a - b) for a, b in zip(got, expected)), 6, (rgb, got, expected))

    def test_kms_sdr_filter_keeps_the_curve(self):
        """scale_vaapi gets no transfer or primaries (they lifted midtones); tags come from setparams."""
        scale, tags = main.KMS_COLOR_FILTER.split(",")
        self.assertNotIn("out_color_transfer", scale)
        self.assertNotIn("out_color_primaries", scale)
        self.assertIn("color_trc=bt709", tags)

    def test_hdr_modes(self):
        deck = main.kms_hdr_cmd("v.mkv", 1)
        self.assertIn(main.DECK_SHADER_NAME, deck[deck.index("-vf") + 1])
        self.assertTrue(os.path.isfile(main.DECK_SHADER))
        for mode in ("plain", "external"):
            command = main.kms_hdr_cmd("v.mkv", 1, 60, mode)
            video_filter = command[command.index("-vf") + 1]
            self.assertNotIn("contrast", video_filter, mode)
            self.assertNotIn("saturation", video_filter, mode)
        external = main.kms_hdr_cmd("/tmp/rec/v.mkv", 1, 60, "external")
        self.assertIn("color_trc=smpte2084", external[external.index("-vf") + 1])
        # The external modes read the shader written into the settings folder,
        # never a path from the (user-chosen) save folder.
        shader = os.path.join(main.SETTINGS_DIR, main.SHADER_NAME)
        self.assertIn(f"custom_shader_path={shader}", external[external.index("-vf") + 1])
        sdr = main.kms_hdr_cmd("/tmp/a,b:c/v.mkv", 1, 60, "external_sdr")
        self.assertIn(f"custom_shader_path={shader}", sdr[sdr.index("-vf") + 1])
        self.assertTrue(os.path.isfile(main.PQ_SHADER))

    def test_pq_shader_white_level(self):
        temp = tempfile.TemporaryDirectory()
        try:
            main.write_pq_shader(202.64, temp.name)
            with open(os.path.join(temp.name, main.SHADER_NAME)) as file:
                text = file.read()
            self.assertIn("const float white_nits = 202.6;", text)
            self.assertNotIn("480.0;", text)
        finally:
            temp.cleanup()

    def test_original_colors_on_external_hdr(self):
        # Colorimetric vibrancy only: no mura, nothing from the internal panel's label set.
        self.assertEqual(main.original_color_props(True), {main.GAMUT_WIDENESS_PROP: "0"})

    def test_external_hdr_matches_gamescope(self):
        """PQ gray in nits comes out as gamma 2.2 with a 480-nit white, like gamescope's SDR."""
        m1, m2, c1, c2, c3 = 2610 / 16384, 2523 / 32, 3424 / 4096, 2413 / 128, 2392 / 128

        def pq(nits):
            y = (nits / 10000) ** m1
            return ((c1 + c2 * y) / (1 + c3 * y)) ** m2

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        main.write_pq_shader(480, temp.name, swap_rb=rgb10_swapped())
        shader = os.path.join(temp.name, main.SHADER_NAME)
        for nits, expected in ((10, 44), (100, 125), (203, 173), (400, 234), (1000, 255)):
            value = round(pq(nits) * 255)
            source = f"color=c=0x{value:02x}{value:02x}{value:02x}:s=64x64:d=1,format=rgb24"
            command = main.ffmpeg_base() + [
                "-init_hw_device", "vulkan=vk", "-filter_hw_device", "vk", "-f", "lavfi", "-i", source,
                "-frames:v", "1", "-vf",
                f"format=x2rgb10le,hwupload,{main.rgb_input(main.HDR_PQ_INPUT_FILTER)},"
                f"{main.pq_tonemap_filter(shader)},hwdownload,format=nv12,"
                "scale=in_range=tv:in_color_matrix=bt709:out_range=pc,format=gray",
                "-f", "rawvideo", "-",
            ]
            result = subprocess.run(command, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr[-300:])
            # within the 8-bit PQ input steps and the limited-range round trip
            self.assertLessEqual(abs(result.stdout[32 * 64 + 32] - expected), 4, nits)

    def test_external_hdr_game_rolls_off_highlights(self):
        """External HDR10 with the brightness settings: white at 1.5 x paper white, rolled off to the peak."""
        m1, m2, c1, c2, c3 = 2610 / 16384, 2523 / 32, 3424 / 4096, 2413 / 128, 2392 / 128
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        main.write_pq_shader(main.hdr_white_nits(1000, 203), temp.name, peak_nits=1000, swap_rb=rgb10_swapped())
        shader = os.path.join(temp.name, main.SHADER_NAME)

        def pq(nits):
            y = (nits / 10000) ** m1
            return round(((c1 + c2 * y) / (1 + c3 * y)) ** m2 * 255)

        def convert(rgb):
            source = "color=c=0x{:02x}{:02x}{:02x}:s=64x64:d=1,format=rgb24".format(*rgb)
            command = main.ffmpeg_base() + [
                "-init_hw_device", "vulkan=vk", "-filter_hw_device", "vk", "-f", "lavfi", "-i", source,
                "-frames:v", "1", "-vf",
                f"format=x2rgb10le,hwupload,{main.rgb_input(main.HDR_PQ_INPUT_FILTER)},"
                f"{main.pq_tonemap_filter(shader)},hwdownload,format=nv12,"
                "scale=in_range=tv:in_color_matrix=bt709:out_range=pc,format=rgb24",
                "-f", "rawvideo", "-",
            ]
            result = subprocess.run(command, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr[-300:])
            at = (32 * 64 + 32) * 3
            return tuple(result.stdout[at:at + 3])

        grays = [convert((pq(nits),) * 3)[1] for nits in (100, 203, 400, 600, 1000)]
        self.assertEqual(grays, sorted(grays))
        # 203 nits near 84 % (214), within the 8-bit PQ input steps; 1000 at white.
        self.assertLessEqual(abs(grays[1] - 214), 6)
        self.assertGreaterEqual(grays[-1], 250)
        self.assertGreater(grays[-1], grays[-2])

    def test_deck_hdr_rolls_off_highlights(self):
        """Gamma 2.2 up to 1000 nits: white at 1.5 x paper white, highlights rolled off to the peak, hue kept."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        shader = os.path.join(temp.name, main.DECK_SHADER_NAME)

        def use(peak, paper_white):
            main.write_deck_shader(peak, main.hdr_white_nits(peak, paper_white), temp.name, swap_rb=rgb10_swapped())

        def encode(nits):
            return round((nits / 1000) ** (1 / 2.2) * 255)

        def convert(rgb):
            source = "color=c=0x{:02x}{:02x}{:02x}:s=64x64:d=1,format=rgb24".format(*rgb)
            command = main.ffmpeg_base() + [
                "-init_hw_device", "vulkan=vk", "-filter_hw_device", "vk", "-f", "lavfi", "-i", source,
                "-frames:v", "1", "-vf",
                f"format=x2rgb10le,hwupload,{main.rgb_input(main.HDR_INPUT_FILTER)},"
                f"{main.deck_tonemap_filter(shader)},hwdownload,format=nv12,"
                "scale=in_range=tv:in_color_matrix=bt709:out_range=pc,format=rgb24",
                "-f", "rawvideo", "-",
            ]
            result = subprocess.run(command, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr[-300:])
            at = (32 * 64 + 32) * 3
            return tuple(result.stdout[at:at + 3])

        # The defaults: peak 1000, paper white 203, so white at 304.5 nits.
        use(1000, main.DEFAULT_PAPER_WHITE)
        # Gamma 2.2 below the knee (~160 nits), measured 2026-11-21 with a 300-nit white.
        for nits, expected in ((10, 52), (50, 110), (100, 153), (150, 185)):
            self.assertLessEqual(abs(convert((encode(nits),) * 3)[1] - expected), 3, nits)
        # Above it brighter still comes out brighter, up to white at 1000 nits.
        highs = [convert((encode(nits),) * 3)[1] for nits in (400, 600, 1000)]
        self.assertEqual(highs, sorted(highs))
        self.assertGreaterEqual(highs[-1], 250)
        # Orange at 900 nits stays orange; clipping per channel made it yellow (224).
        red, green, blue = convert((encode(900), encode(400), 0))
        self.assertGreaterEqual(red, 250)
        self.assertLess(green, 185)
        # A game set to a 600-nit peak: 600 already reaches white, brighter is cut there.
        use(600, 203)
        at_peak = convert((encode(600),) * 3)[1]
        self.assertGreaterEqual(at_peak, 252)
        self.assertLess(convert((encode(400),) * 3)[1], at_peak)
        self.assertEqual(convert((encode(1000),) * 3)[1], at_peak)
        # A brighter paper white records the same gray darker.
        use(1000, 300)
        self.assertLess(convert((encode(100),) * 3)[1], 140)

    def test_display_hdr_peak_from_edid(self):
        self.assertAlmostEqual(main.edid_hdr_peak(hdr_edid(139)), 1015.2, places=1)
        self.assertIsNone(main.edid_hdr_peak(hdr_edid(139, eotfs=0b011)))
        self.assertIsNone(main.edid_hdr_peak(bytes(128)))
        self.assertEqual(main.round_peak(1015.2), 1000)
        self.assertEqual(main.round_peak(603.7), 600)
        self.assertEqual(main.round_peak(746.0), 750)
        self.assertEqual(main.round_peak(1450.0), 1450)

    def test_hdr_brightness_settings(self):
        self.assertEqual(main.hdr_white_nits(1000, 203), 304.5)
        # Never above the peak: with nothing left to roll off, the peak is white.
        self.assertEqual(main.hdr_white_nits(400, 400), 400)
        self.assertEqual(main.PAPER_WHITES, [203, 300])
        self.assertEqual(main.DEFAULT_PAPER_WHITE, 203)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        main.write_deck_shader(600, 304.5, temp.name)
        with open(os.path.join(temp.name, main.DECK_SHADER_NAME)) as file:
            text = file.read()
        self.assertIn("const float peak_nits = 600.0;", text)
        self.assertIn("const float white_nits = 304.5;", text)
        self.assertIn("const float panel_nits = 1000.0;", text)
        main.write_deck_shader(600, 304.5, temp.name, panel_nits=687.448)
        with open(os.path.join(temp.name, main.DECK_SHADER_NAME)) as file:
            self.assertIn("const float panel_nits = 687.4;", file.read())
        # Saved values are checked on load like the other settings.
        settings, prefs_file, state = main.SETTINGS_DIR, main.PREFS_FILE, main.state
        try:
            main.SETTINGS_DIR = temp.name
            main.PREFS_FILE = os.path.join(temp.name, "prefs.json")
            main.state = main.State()
            # None follows the display's peak; a value needs the top it was chosen under.
            for saved, peak, paper in (({"hdr_peak": 800, "hdr_peak_top": 1000, "hdr_paper_white": 203}, 800, 203),
                                       ({"hdr_peak": 800, "hdr_paper_white": 203}, None, 203),
                                       ({"hdr_peak": 805, "hdr_paper_white": 205}, None, 203),
                                       ({"hdr_peak": 20000, "hdr_paper_white": "x"}, None, 203),
                                       ({"hdr_paper_white": 300}, None, 300),
                                       # A slider value from a test build.
                                       ({"hdr_paper_white": 250}, None, 203),
                                       ({"hdr_peak": True}, None, 203)):
                main.state = main.State()
                with open(main.PREFS_FILE, "w") as file:
                    json.dump(saved, file)
                main.load_prefs()
                self.assertEqual((main.state.prefs["hdr_peak"], main.state.prefs["hdr_paper_white"]), (peak, paper), saved)
            plugin = main.Plugin()
            main.state = main.State()
            # Deck OLED's panel with gamescope's EDID (1000 nits).
            edid = os.path.join(temp.name, "edid.bin")
            with open(edid, "wb") as file:
                file.write(hdr_edid(139))
            originals = main.GAMESCOPE_EDID, main.external_display, main.DECK_PANEL
            main.GAMESCOPE_EDID, main.external_display, main.DECK_PANEL = edid, lambda: False, "oled"
            self.addCleanup(lambda: setattr(main, "GAMESCOPE_EDID", originals[0]))
            self.addCleanup(lambda: setattr(main, "external_display", originals[1]))
            self.addCleanup(lambda: setattr(main, "DECK_PANEL", originals[2]))
            self.assertEqual(main.hdr_peak(), 1000)
            asyncio.run(plugin.set_hdr_peak(734))
            self.assertEqual((main.state.prefs["hdr_peak"], main.state.prefs["hdr_peak_top"]), (730, 1000))
            self.assertEqual(main.hdr_peak(), 730)
            # The top itself follows the display.
            asyncio.run(plugin.set_hdr_peak(5000))
            self.assertEqual((main.state.prefs["hdr_peak"], main.hdr_peak()), (None, 1000))
            asyncio.run(plugin.set_hdr_peak(500))
            asyncio.run(plugin.set_hdr_paper_white(300))
            self.assertEqual(main.state.prefs["hdr_paper_white"], 300)
            asyncio.run(plugin.set_hdr_paper_white(250))
            self.assertEqual(main.state.prefs["hdr_paper_white"], 300)
            asyncio.run(plugin.set_hdr_paper_white(203))
            self.assertEqual(main.state.prefs["hdr_paper_white"], 203)
            snap = main.snapshot()
            self.assertEqual(snap["paper_whites"], main.PAPER_WHITES)
            self.assertEqual(snap["hdr_peak_range"], [400, 1000])
            self.assertTrue(snap["hdr_settings"])
            # An external 600-nit HDR monitor: the range ends there, and the 500 chosen
            # under a 1000-nit top goes back to following the top.
            with open(edid, "wb") as file:
                file.write(hdr_edid(115))
            os.utime(edid, ns=(1, 1))
            main.external_display = lambda: True
            snap = main.snapshot()
            self.assertEqual((snap["hdr_peak_range"], snap["hdr_peak"], snap["hdr_settings"]), ([400, 600], 600, True))
            self.assertIsNone(main.state.prefs["hdr_peak"])
            with open(main.PREFS_FILE) as file:
                self.assertIsNone(json.load(file)["hdr_peak"])
            # A monitor without PQ: no HDR, so the settings are not offered.
            with open(edid, "wb") as file:
                file.write(hdr_edid(115, eotfs=0b001))
            os.utime(edid, ns=(2, 2))
            self.assertFalse(main.snapshot()["hdr_settings"])
        finally:
            main.SETTINGS_DIR, main.PREFS_FILE, main.state = settings, prefs_file, state

    def test_external_display_detection(self):
        temp = tempfile.TemporaryDirectory()
        original = main.DRM_CLASS_DIR, main.DECK_PANEL
        try:
            def connector(name, enabled):
                os.makedirs(os.path.join(temp.name, name))
                with open(os.path.join(temp.name, name, "enabled"), "w") as file:
                    file.write(enabled + "\n")
            main.DRM_CLASS_DIR = temp.name
            main.DECK_PANEL = "oled"
            connector("card0-eDP-1", "enabled")
            connector("card0-DP-1", "disabled")
            connector("card0-Writeback-1", "enabled")
            self.assertEqual(main.hdr_mode(), "deck")
            main.DECK_PANEL = ""
            main.internal_hdr.cache_clear()
            self.assertEqual(main.hdr_mode(), "plain")
            with open(os.path.join(temp.name, "card0-DP-1", "enabled"), "w") as file:
                file.write("enabled\n")
            self.assertEqual(main.hdr_mode(), "external")
        finally:
            main.DRM_CLASS_DIR, main.DECK_PANEL = original
            main.internal_hdr.cache_clear()
            temp.cleanup()

    def test_internal_panel_eotf(self):
        """Other handhelds: gamescope's profile for the built-in panel says PQ or gamma 2.2
        (with its peak); without one, HDR in the EDID means PQ."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        scripts = os.path.join(temp.name, "scripts", "displays")
        os.makedirs(scripts)
        with open(os.path.join(scripts, "pq.lua"), "w") as file:
            file.write('''gamescope.config.known_displays.pq_panel = {
    hdr = { supported = true, eotf = gamescope.eotf.pq },
    matches = function(display)
        if display.vendor == "SDC" and display.product == 0x4301 then return 5000 end
        return -1
    end,
}''')
        with open(os.path.join(scripts, "g22.lua"), "w") as file:
            file.write('''local types = { { vendor = "DXQ", model = "DXQ7D0023" } }
gamescope.config.known_displays.g22_panel = {
    hdr = { supported = true, force_enabled = true, eotf = gamescope.eotf.gamma22,
            max_content_light_level = 687.448 },
}''')
        with open(os.path.join(scripts, "lcd.lua"), "w") as file:
            # HDR off: the gamma 2.2 there is only a fallback for docking.
            file.write('''gamescope.config.known_displays.lcd = {
    hdr = { supported = false, eotf = gamescope.eotf.gamma22 },
    matches = function(display) if display.vendor == "AAA" then return 5000 end end,
}''')
        drm = os.path.join(temp.name, "drm")
        os.makedirs(os.path.join(drm, "card0-eDP-1"))
        originals = main.DRM_CLASS_DIR, main.DECK_PANEL, main.GAMESCOPE_SCRIPT_DIRS, main.external_display

        def restore():
            main.DRM_CLASS_DIR, main.DECK_PANEL, main.GAMESCOPE_SCRIPT_DIRS, main.external_display = originals
            main.internal_hdr.cache_clear()

        self.addCleanup(restore)
        main.DRM_CLASS_DIR, main.DECK_PANEL = drm, ""
        main.GAMESCOPE_SCRIPT_DIRS = (os.path.join(temp.name, "scripts"),)
        main.external_display = lambda: False

        def panel(edid, deck=""):
            with open(os.path.join(drm, "card0-eDP-1", "edid"), "wb") as file:
                file.write(edid)
            main.DECK_PANEL = deck
            main.internal_hdr.cache_clear()
            return main.internal_pq(), main.hdr_mode(), main.pq_output()

        self.assertEqual(main.edid_identity(panel_edid("SDC", 0x4301, "ATNA")), ("SDC", 0x4301, "ATNA"))
        self.assertEqual(panel(panel_edid("SDC", 0x4301)), (True, "external", True))
        # Gamma 2.2: Deck OLED's tone mapping with this panel's peak.
        self.assertEqual(panel(panel_edid("DXQ", 0x1234, "DXQ7D0023")), (False, "deck", False))
        self.assertEqual(main.g22_panel_nits(), 687.448)
        self.assertEqual(main.hdr_peak_range(), (400, 680))
        # No profile: the EDID decides.
        self.assertEqual(panel(panel_edid("AAA", 1)), (True, "external", True))
        self.assertEqual(panel(panel_edid("BBB", 1)), (True, "external", True))
        self.assertEqual(panel(panel_edid("BBB", 1, hdr=False)), (False, "plain", False))
        # A Deck never: its panel is known.
        self.assertEqual(panel(panel_edid("SDC", 0x4301), deck="oled"), (False, "deck", False))
        self.assertEqual(main.g22_panel_nits(), 1000)
        self.assertEqual(panel(panel_edid("SDC", 0x4301), deck="lcd"), (False, "plain", False))
        # The brightness settings come with the PQ path.
        panel(panel_edid("SDC", 0x4301))
        self.assertTrue(main.hdr_settings_apply())
        panel(panel_edid("DXQ", 0x1234, "DXQ7D0023"))
        self.assertTrue(main.hdr_settings_apply())
        panel(panel_edid("BBB", 1, hdr=False))
        self.assertFalse(main.hdr_settings_apply())

    def test_shipped_gamescope_profiles(self):
        """The profiles on this system read as expected, where they exist."""
        profiles = main.hdr_profiles()
        if not profiles:
            self.skipTest("no gamescope display profiles here")
        known = {("VLV", 0x3003, ""): ("gamma22", 1000), ("SDC", 0x4301, ""): ("pq", None),
                 ("DXQ", 0, "DXQ7D0023"): ("gamma22", 993), ("YHB", 0, "YHB02P25"): ("gamma22", 687.448)}
        for identity, expected in known.items():
            found = main.profile_hdr(identity, profiles)
            if found is not None:
                self.assertEqual(found, expected, identity)

    def test_hdr_tonemap_and_vulkan_encode(self):
        """Same filters and encoder as kms_hdr_cmd, fed by software frames instead of kmsgrab."""
        temp = tempfile.TemporaryDirectory()
        try:
            modes = dict(main.HDR_FILTERS)
            modes["deck"] = (main.HDR_INPUT_FILTER, main.deck_tonemap_filter(main.DECK_SHADER))
            modes["external"] = (main.HDR_PQ_INPUT_FILTER, main.pq_tonemap_filter(main.PQ_SHADER))
            modes["deck-deband"] = (main.HDR_INPUT_FILTER, main.deck_tonemap_filter(main.DECK_SHADER, True))
            modes["external-deband"] = (main.HDR_PQ_INPUT_FILTER, main.pq_tonemap_filter(main.PQ_SHADER, True))
            modes["deck-rgb10"] = (main.rgb_input(main.HDR_INPUT_FILTER), main.deck_tonemap_filter(main.DECK_SHADER))
            modes["external-rgb10"] = (main.rgb_input(main.HDR_PQ_INPUT_FILTER), main.pq_tonemap_filter(main.PQ_SHADER))
            for mode, (input_filter, tonemap_filter) in modes.items():
                out = os.path.join(temp.name, f"{mode}.mkv")
                pixels = "x2rgb10le" if mode.endswith("rgb10") else "nv12"
                source = f"testsrc2=s=1280x800:r=60:d=1,format={pixels}"
                command = main.ffmpeg_base() + [
                    "-init_hw_device", "vulkan=vk", "-filter_hw_device", "vk", "-f", "lavfi", "-i", source,
                    "-vf", f"hwupload,{input_filter},{tonemap_filter}",
                ] + main.vulkan_encode_args(12000) + [out]
                result = subprocess.run(command, capture_output=True, text=True,
                                        env={**os.environ, **main.VULKAN_ENCODE_ENV})
                self.assertEqual(result.returncode, 0, f"{mode}: {result.stderr[-300:]}")
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v", "-show_entries",
                     "stream=codec_name,nb_read_frames", "-of", "csv=p=0", out],
                    capture_output=True, text=True, check=True,
                ).stdout.strip()
                self.assertEqual(probe, "h264,60", mode)
        finally:
            temp.cleanup()

    def test_framerate_in_commands(self):
        kms = main.kms_cmd("v.mkv", 1, 30)
        self.assertEqual(kms[kms.index("-framerate") + 1], "30")
        self.assertEqual(kms[kms.index("-g") + 1], "60")
        self.assertTrue(any("framerate=30/1" in part for part in main.pw_source_cmd(1280, 800, 30)))
        pw = main.pw_encode_cmd("v.mkv", 1280, 800, 1, 30)
        self.assertEqual(pw[pw.index("-framerate") + 1], "30")
        pw_gop = main.pw_encode_cmd("v.mkv", 1280, 800, 1, 30)
        self.assertEqual(pw_gop[pw_gop.index("-g") + 1], "60")
        hdr = main.kms_hdr_cmd("v.mkv", 1, 30)
        self.assertEqual(hdr[hdr.index("-framerate") + 1], "30")

    def test_parse_dimensions(self):
        self.assertEqual(main.parse_dimensions("  dimensions:    1920x1080 pixels"), (1920, 1080))
        self.assertEqual(main.parse_dimensions("nothing"), main.FALLBACK_SIZE)

    def test_kms_encoder_holds_no_frames(self):
        command = main.kms_cmd("v.mkv", 1)
        self.assertEqual(command[command.index("-bf") + 1], "0")
        self.assertEqual(command[command.index("-async_depth") + 1], "1")

    def test_captures_flush_to_disk(self):
        commands = [main.kms_cmd("v.mkv", 1), main.kms_hdr_cmd("v.mkv", 1), main.pw_encode_cmd("v.mkv", 1280, 800, 1),
                    main.audio_cmd("sink", "a.mka"), main.mic_cmd("mic", "m.mka")]
        for command in commands:
            self.assertEqual(command[-1 - len(main.FLUSH_ARGS):-1], main.FLUSH_ARGS, command[-1])

    def test_video_and_audio_stay_separate(self):
        for command in (main.kms_cmd("v.mkv", 7500), main.pw_encode_cmd("v.mkv", 1280, 800, 7500)):
            self.assertNotIn("pulse", command)
        self.assertNotIn("kmsgrab", main.audio_cmd("sink", "a.mka"))

    def test_mux_without_audio(self):
        self.assertNotIn("-shortest", main.mux_cmd("v.mkv", None, "o.mp4"))
        self.assertIn("-shortest", main.mux_cmd("v.mkv", "a.mka", "o.mp4"))

    def test_mux_full_range_retag(self):
        self.assertNotIn("-bsf:v", main.mux_cmd("v.mkv", "a.mka", "o.mp4"))
        command = main.mux_cmd("v.mkv", "a.mka", "o.mp4", "m.mka", "mix", True)
        self.assertIn("h264_metadata=video_full_range_flag=1", command)
        self.assertEqual(command[command.index("-color_range") + 1], "pc")

    def test_mux_microphone_modes(self):
        mixed = main.mux_cmd("v.mkv", "a.mka", "o.mp4", "m.mka", "mix")
        self.assertIn("-filter_complex", mixed)
        separate = main.mux_cmd("v.mkv", "a.mka", "o.mp4", "m.mka", "separate")
        self.assertNotIn("-filter_complex", separate)
        self.assertIn("2:a", separate)
        mic_only = main.mux_cmd("v.mkv", None, "o.mp4", "m.mka", "mix")
        self.assertNotIn("-filter_complex", mic_only)
        self.assertIn("1:a", mic_only)


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.originals = (main.OUTPUT_DIR, main.kms_cmd, main.audio_cmd, main.set_composite_force)
        main.OUTPUT_DIR = self.temp.name
        # Recordings started without a method use the default, PipeWire, which would
        # open the real gamescope stream; these tests run on the synthetic KMS source.
        self.state_class = main.State

        class KmsState(self.state_class):
            def __init__(self):
                super().__init__()
                self.prefs["method"] = "kms"

        main.State = KmsState
        main.kms_cmd = synthetic_video
        main.audio_cmd = synthetic_audio
        self.composite = []
        self.root_prop_originals = main.read_root_prop, main.write_root_prop
        self.restore_file = main.RESTORE_FILE
        main.RESTORE_FILE = os.path.join(self.temp.name, "restore.json")

        async def record_composite(enabled):
            self.composite.append(enabled)

        async def no_read(name):
            return "0"

        async def no_write(name, value):
            return None
        main.set_composite_force = record_composite
        # Mura correction is off by default, so keep tests away from the real display.
        main.read_root_prop, main.write_root_prop = no_read, no_write

    def tearDown(self):
        main.State = self.state_class
        main.OUTPUT_DIR, main.kms_cmd, main.audio_cmd, main.set_composite_force = self.originals
        main.read_root_prop, main.write_root_prop = self.root_prop_originals
        main.RESTORE_FILE = self.restore_file
        main._rgb10_order = None
        self.temp.cleanup()

    def test_record_and_mux(self):
        async def scenario():
            main.state = main.State()
            main.state.prefs["quality"] = "preset_default"
            await main.start_recording()
            self.assertEqual(main.state.status, "recording", main.state.error_detail)
            await asyncio.sleep(3)
            await main.finish_recording()
            self.assertEqual(main.state.status, "idle")
            self.assertEqual(main.state.error, "")
            return main.state.last_file

        output = asyncio.run(scenario())
        self.assertEqual(self.composite, [True, False])
        tags = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=color_range", "-of", "csv=p=0", output],
            capture_output=True, text=True, check=True,
        ).stdout.strip().strip(",")
        self.assertEqual(tags, "pc")
        streams = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", output],
            capture_output=True, text=True, check=True,
        ).stdout.split()
        self.assertEqual(sorted(s.strip(",") for s in streams), ["aac", "h264"])
        self.assertEqual([p for p in os.listdir(self.temp.name) if p.startswith(".rec-")], [])

    def test_records_at_30_fps(self):
        async def scenario():
            main.state = main.State()
            main.state.prefs["fps"] = 30
            await main.start_recording()
            self.assertEqual(main.state.status, "recording", main.state.error_detail)
            self.assertEqual(main.state.fps, 30)
            await asyncio.sleep(2)
            await main.finish_recording()
            return main.state.last_file

        output = asyncio.run(scenario())
        rate = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=avg_frame_rate", "-of", "csv=p=0", output],
            capture_output=True, text=True, check=True,
        ).stdout.strip().strip(",")
        numerator, denominator = (int(v) for v in rate.split("/"))
        self.assertAlmostEqual(numerator / denominator, 30, delta=0.1)

    def test_monitor_saves_when_capture_dies(self):
        async def scenario():
            main.state = main.State()
            await main.start_recording()
            self.assertEqual(main.state.status, "recording", main.state.error_detail)
            await asyncio.sleep(2)
            main.state.video.kill()
            monitor = main.state.monitor
            await asyncio.wait_for(monitor, 20)
            return main.state.error, main.state.last_file, main.state.status

        error, output, status = asyncio.run(scenario())
        self.assertEqual((error, status), ("capture_stopped", "idle"))
        self.assertEqual(self.composite, [True, False])
        self.assertTrue(os.path.exists(output))

    def record_with_mic(self, mode):
        originals = main.mic_cmd, main.default_source

        def synthetic_mic(_source, output, mono=False):
            channels = ["-ac", "1"] if mono else []
            return main.ffmpeg_base() + ["-re", "-f", "lavfi", "-i", "sine=f=1000"] + channels + main.AUDIO_ARGS + [output]

        async def fake_source():
            return "test_mic"

        async def scenario():
            main.state = main.State()
            main.state.prefs.update(mic=True, mic_mode=mode)
            await main.start_recording()
            self.assertEqual(main.state.status, "recording", main.state.error_detail)
            await asyncio.sleep(2)
            await main.finish_recording()
            self.assertEqual(main.state.error, "", main.state.error_detail)
            return main.state.last_file

        main.mic_cmd, main.default_source = synthetic_mic, fake_source
        try:
            output = asyncio.run(scenario())
        finally:
            main.mic_cmd, main.default_source = originals
        return subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream_tags=handler_name",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", output],
            capture_output=True, text=True, check=True,
        ).stdout.split()

    def test_microphone_mixed(self):
        streams = self.record_with_mic("mix")
        self.assertEqual(len(streams), 1)
        self.assertTrue(streams[0].startswith("aac"))

    def test_microphone_mono(self):
        self.assertIn("-ac", main.mic_cmd("src", "m.mka", True))
        self.assertNotIn("-ac", main.mic_cmd("src", "m.mka"))

    def test_mono_microphone_mixes_with_stereo_game(self):
        temp = tempfile.TemporaryDirectory()
        try:
            base = main.ffmpeg_base()
            video, game, mic, out = (os.path.join(temp.name, n) for n in ("v.mkv", "a.mka", "m.mka", "o.mp4"))
            subprocess.run(base + ["-f", "lavfi", "-i", "testsrc2=s=320x200:r=30:d=2", "-c:v", "libx264", video], check=False)
            if not os.path.exists(video):
                subprocess.run(base + ["-f", "lavfi", "-i", "testsrc2=s=320x200:r=30:d=2", "-c:v", "mpeg4", video], check=True)
            subprocess.run(base + ["-f", "lavfi", "-i", "sine=f=440:d=2", "-ac", "2"] + main.AUDIO_ARGS + [game], check=True)
            subprocess.run(base + ["-f", "lavfi", "-i", "sine=f=1000:d=2", "-ac", "1"] + main.AUDIO_ARGS + [mic], check=True)
            for mode, expected in (("mix", ["2"]), ("separate", ["2", "1"])):
                subprocess.run(main.mux_cmd(video, game, out, mic, mode), check=True)
                channels = subprocess.run(
                    ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=channels", "-of", "csv=p=0", out],
                    capture_output=True, text=True, check=True,
                ).stdout.split()
                self.assertEqual(channels, expected, mode)
        finally:
            temp.cleanup()

    def test_microphone_separate(self):
        self.assertEqual(self.record_with_mic("separate"), ["aac,Game", "aac,Microphone"])

    def safe_pipewire(self):
        """Replace the gamescope stream with a GStreamer test source for the PipeWire path."""
        original = main.pw_source_cmd

        def test_source(width, height, fps=60):
            return [
                "gst-launch-1.0", "-e", "-q", "videotestsrc", "is-live=true", "do-timestamp=true", "!",
                "videoconvert", "!", f"video/x-raw,format=NV12,width={width},height={height},framerate={fps}/1",
                "!", "fdsink", "fd=1",
            ]
        main.pw_source_cmd = test_source
        self.addCleanup(setattr, main, "pw_source_cmd", original)
        gap = main.COMPARE_GAP_SEC
        main.COMPARE_GAP_SEC = 0.2
        self.addCleanup(setattr, main, "COMPARE_GAP_SEC", gap)

    def outputs(self):
        return sorted(name for name in os.listdir(self.temp.name) if name.endswith(".mp4"))

    def test_comparison_records_pipewire_then_kms(self):
        self.safe_pipewire()

        async def scenario():
            main.state = main.State()
            steps = []

            async def watch():
                while True:
                    if main.state.compare_step and main.state.status == "recording":
                        if not steps or steps[-1] != main.state.compare_step:
                            steps.append(main.state.compare_step)
                    await asyncio.sleep(0.1)
            watcher = asyncio.create_task(watch())
            await main.run_comparison(seconds=1.5)
            watcher.cancel()
            return steps, main.state.compare_step, main.state.prefs["method"], main.state.error

        steps, step_after, method, error = asyncio.run(scenario())
        self.assertEqual(steps, ["pw", "kms"])
        self.assertEqual((step_after, method, error), ("", "kms", ""))
        names = self.outputs()
        # File names start with the time, so PipeWire (recorded first) sorts first.
        self.assertEqual([name.rsplit("_", 1)[-1] for name in names], ["pw.mp4", "kms.mp4"])
        self.assertEqual(self.composite, [True, False])

    def test_comparison_stops_early(self):
        self.safe_pipewire()

        async def scenario():
            main.state = main.State()
            task = asyncio.create_task(main.run_comparison(seconds=30))
            while main.state.status != "recording":
                await asyncio.sleep(0.1)
            await asyncio.sleep(1)
            await main.Plugin().stop()
            await asyncio.wait_for(task, 20)
            return main.state.compare_step, main.state.status

        self.assertEqual(asyncio.run(scenario()), ("", "idle"))
        self.assertEqual([name.rsplit("_", 1)[-1] for name in self.outputs()], ["pw.mp4"])
        self.assertEqual(self.composite, [])

    def test_original_colors_are_restored(self):
        writes = []
        originals = main.read_root_prop, main.write_root_prop

        panel_props = {}

        async def fake_read(name):
            return panel_props.get(name, "0")

        async def fake_write(name, value):
            writes.append((name, value))

        async def scenario(labelset):
            main.state = main.State()
            main.state.color_labelset = labelset
            main.state.prefs["kms_original_color"] = True
            await main.start_recording()
            self.assertEqual(main.state.status, "recording", main.state.error_detail)
            await asyncio.sleep(1)
            await main.finish_recording()

        def run(panel, labelset):
            writes.clear()
            main.DECK_PANEL = panel
            asyncio.run(scenario(labelset))
            return list(writes)

        main.read_root_prop, main.write_root_prop = fake_read, fake_write
        panel, drm = main.DECK_PANEL, main.DRM_CLASS_DIR
        # No readable EDID: the label set and the Deck model decide.
        main.DRM_CLASS_DIR = os.path.join(self.temp.name, "no-drm")
        mura, wideness = main.MURA_DISABLED_PROP, main.GAMUT_WIDENESS_PROP
        try:
            # Deck OLED before Steam reports a label set: Native is the top of the range.
            self.assertEqual(run("oled", None), [(mura, "1"), (wideness, "1065353216"), (mura, "0"), (wideness, "0")])
            # Deck LCD: Native is the bottom (0.0); no mura correction.
            self.assertEqual(run("lcd", None), [(wideness, "0"), (wideness, "0")])
            # Another device with Steam's narrow-panel label set.
            self.assertEqual(run("", 2), [(wideness, "0"), (wideness, "0")])
            # Another device with an unknown range: nothing is touched.
            self.assertEqual(run("", 0), [])
            self.assertEqual(run("", None), [])
            # Night mode on and a warm color temperature: both reset, then put back.
            night, white = main.NIGHT_MODE_PROP, main.VIRTUAL_WHITE_PROP
            panel_props.update({night: "1061997773,1032730852,1064514355", white: "1051595899,1051931443"})
            self.assertEqual(run("lcd", None), [
                (wideness, "0"), (night, "0,1032730852,1064514355"), (white, "0,0"),
                (wideness, "0"), (night, "1061997773,1032730852,1064514355"), (white, "1051595899,1051931443"),
            ])
            # Already off and native: left alone.
            panel_props.update({night: "0,1032730852,1064514355", white: "0,0"})
            self.assertEqual(run("lcd", None), [(wideness, "0"), (wideness, "0")])
        finally:
            main.read_root_prop, main.write_root_prop = originals
            main.DECK_PANEL, main.DRM_CLASS_DIR = panel, drm

    def test_native_follows_lit_display_edid(self):
        """Native comes from the lit display's EDID gamut, before any label set or Deck model."""
        def edid(primaries):
            values = [round(c * 1024) for xy in primaries for c in xy] + [320, 337]
            low1 = (values[0] & 3) << 6 | (values[1] & 3) << 4 | (values[2] & 3) << 2 | (values[3] & 3)
            low2 = (values[4] & 3) << 6 | (values[5] & 3) << 4 | (values[6] & 3) << 2 | (values[7] & 3)
            block = bytearray(128)
            block[:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
            block[25], block[26] = low1, low2
            block[27:35] = bytes(v >> 2 for v in values)
            return bytes(block)

        def connector(name, enabled, primaries):
            folder = os.path.join(self.temp.name, "drm", name)
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "enabled"), "w") as file:
                file.write(enabled + "\n")
            with open(os.path.join(folder, "edid"), "wb") as file:
                file.write(edid(primaries))

        oled = ((0.6797, 0.3193), (0.2373, 0.7227), (0.1377, 0.0498))
        srgb = ((0.6396, 0.3301), (0.2998, 0.5996), (0.1504, 0.0596))
        drm, panel = main.DRM_CLASS_DIR, main.DECK_PANEL
        main.DRM_CLASS_DIR = os.path.join(self.temp.name, "drm")
        main.DECK_PANEL = "oled"
        try:
            main.state = main.State()
            main.state.color_labelset = 1  # stale, from the built-in panel
            connector("card0-eDP-1", "enabled", oled)
            connector("card0-DP-1", "disabled", srgb)
            self.assertEqual(main.native_wideness(), 1.0)
            self.assertEqual(main.original_color_note(), "oled")
            # Switched to an external sRGB monitor: Native is the bottom, no mura.
            connector("card0-eDP-1", "disabled", oled)
            connector("card0-DP-1", "enabled", srgb)
            self.assertEqual(main.native_wideness(), 0.0)
            self.assertEqual(main.original_color_props(), {main.GAMUT_WIDENESS_PROP: "0"})
            self.assertEqual(main.original_color_note(), "plain")
        finally:
            main.DRM_CLASS_DIR, main.DECK_PANEL = drm, panel

    def test_sdr_on_external_hdr_uses_pq_path(self):
        """SDR content on an external HDR output goes through the PQ shader with Steam's SDR white."""
        calls, writes = [], []
        originals = main.kms_hdr_cmd, main.read_root_prop, main.write_root_prop, main.external_display

        async def props(name):
            return {main.HDR_OUTPUT_PROP: "1", main.HDR_APP_PROP: "0",
                    main.SDR_ON_HDR_PROP: "1128965079"}.get(name, "0")

        async def record_write(name, value):
            writes.append((name, value))

        def fake_hdr(output, kbps, fps=60, mode="deck", rgb10=False):
            with open(os.path.join(main.SETTINGS_DIR, main.SHADER_NAME)) as file:
                text = file.read()
                # SDR content: its own white, and nothing above it to roll off.
                calls.append((mode, "white_nits = 202.6;" in text and "peak_nits = 202.6;" in text))
            return synthetic_video(output, kbps, fps)

        async def scenario():
            main.state = main.State()
            main.state.prefs["kms_original_color"] = True
            main._rgb10_order = "ok"
            await main.start_recording()
            self.assertEqual(main.state.status, "recording", main.state.error_detail)
            self.assertEqual(main.snapshot()["hdr"], "external_sdr")
            await asyncio.sleep(1)
            await main.finish_recording()
            return main.state.error

        main.kms_hdr_cmd, main.read_root_prop, main.write_root_prop = fake_hdr, props, record_write
        main.external_display = lambda: True
        settings = main.SETTINGS_DIR
        main.SETTINGS_DIR = self.temp.name
        try:
            self.assertEqual(asyncio.run(scenario()), "")
        finally:
            main.kms_hdr_cmd, main.read_root_prop, main.write_root_prop, main.external_display = originals
            main.SETTINGS_DIR = settings
        self.assertEqual(calls, [("external_sdr", True)])
        wideness = main.GAMUT_WIDENESS_PROP
        self.assertEqual(writes, [(wideness, "0"), (wideness, "0")])

    def test_deck_hdr_uses_brightness_settings(self):
        """An HDR game on Deck OLED's panel: the shader gets the peak and 1.5 x the paper white."""
        calls = []
        originals = main.kms_hdr_cmd, main.read_root_prop, main.write_root_prop, main.external_display, main.DECK_PANEL

        async def props(name):
            return "1" if name == main.HDR_APP_PROP else "0"

        async def no_write(name, value):
            pass

        def fake_hdr(output, kbps, fps=60, mode="deck", rgb10=False):
            with open(os.path.join(main.SETTINGS_DIR, main.DECK_SHADER_NAME)) as file:
                text = file.read()
            calls.append((mode, "peak_nits = 800.0;" in text, "white_nits = 450.0;" in text, rgb10,
                          "//!HOOK RGB" in text))
            return synthetic_video(output, kbps, fps)

        async def scenario():
            main.state = main.State()
            main.state.prefs["kms_original_color"] = False
            main.state.prefs["method"] = "kms"
            main.state.prefs["hdr_peak"], main.state.prefs["hdr_peak_top"] = 800, 1000
            main.state.prefs["hdr_paper_white"] = 300
            main._rgb10_order = "swapped"
            await main.start_recording()
            self.assertEqual(main.state.status, "recording", main.state.error_detail)
            await asyncio.sleep(1)
            await main.finish_recording()
            return main.state.error

        main.kms_hdr_cmd, main.read_root_prop, main.write_root_prop = fake_hdr, props, no_write
        main.external_display = lambda: False
        main.DECK_PANEL = "oled"
        settings, edid = main.SETTINGS_DIR, main.GAMESCOPE_EDID
        main.SETTINGS_DIR = self.temp.name
        # No gamescope EDID: the panel's 1000 nits.
        main.GAMESCOPE_EDID = os.path.join(self.temp.name, "missing.bin")
        try:
            self.assertEqual(asyncio.run(scenario()), "")
        finally:
            main.kms_hdr_cmd, main.read_root_prop, main.write_root_prop, main.external_display, main.DECK_PANEL = originals
            main.SETTINGS_DIR, main.GAMESCOPE_EDID = settings, edid
        self.assertEqual(calls, [("deck", True, True, True, True)])

    def test_external_hdr_game_uses_brightness_settings(self):
        """An HDR game on an external HDR monitor: the PQ shader gets the monitor's peak and 1.5 x paper white."""
        calls = []
        originals = main.kms_hdr_cmd, main.read_root_prop, main.write_root_prop, main.external_display

        async def props(name):
            return {main.HDR_OUTPUT_PROP: "1", main.HDR_APP_PROP: "1"}.get(name, "0")

        async def no_write(name, value):
            pass

        def fake_hdr(output, kbps, fps=60, mode="deck", rgb10=False):
            with open(os.path.join(main.SETTINGS_DIR, main.SHADER_NAME)) as file:
                text = file.read()
            calls.append((mode, "peak_nits = 600.0;" in text, "white_nits = 304.5;" in text, rgb10,
                          "//!HOOK RGB" in text))
            return synthetic_video(output, kbps, fps)

        async def scenario():
            main.state = main.State()
            main.state.prefs["kms_original_color"] = False
            main.state.prefs["method"] = "kms"
            main._rgb10_order = "ok"
            await main.start_recording()
            self.assertEqual(main.state.status, "recording", main.state.error_detail)
            await asyncio.sleep(1)
            await main.finish_recording()
            return main.state.error

        edid_path = os.path.join(self.temp.name, "edid.bin")
        with open(edid_path, "wb") as file:
            file.write(hdr_edid(115))
        main.kms_hdr_cmd, main.read_root_prop, main.write_root_prop = fake_hdr, props, no_write
        main.external_display = lambda: True
        settings, edid = main.SETTINGS_DIR, main.GAMESCOPE_EDID
        main.SETTINGS_DIR, main.GAMESCOPE_EDID = self.temp.name, edid_path
        try:
            self.assertEqual(asyncio.run(scenario()), "")
        finally:
            main.kms_hdr_cmd, main.read_root_prop, main.write_root_prop, main.external_display = originals
            main.SETTINGS_DIR, main.GAMESCOPE_EDID = settings, edid
        self.assertEqual(calls, [("external", True, True, True, False)])

    def test_rgb10_falls_back_to_nv12(self):
        """10-bit RGB that does not start on the real screen: HDR again on NV12, shader without the swap."""
        calls = []
        originals = main.kms_hdr_cmd, main.read_root_prop, main.write_root_prop, main.external_display, \
            main.DECK_PANEL, main.KMS_START_ATTEMPTS

        async def props(name):
            return "1" if name == main.HDR_APP_PROP else "0"

        async def no_write(name, value):
            pass

        def fake_hdr(output, kbps, fps=60, mode="deck", rgb10=False):
            with open(os.path.join(main.SETTINGS_DIR, main.DECK_SHADER_NAME)) as file:
                calls.append((rgb10, "//!HOOK RGB" in file.read()))
            if rgb10:
                return main.ffmpeg_base() + ["-f", "nonexistent", "-i", "x", output]
            return synthetic_video(output, kbps, fps)

        async def scenario():
            main.state = main.State()
            main.state.prefs["kms_original_color"] = False
            main.state.prefs["method"] = "kms"
            main._rgb10_order = "swapped"
            await main.start_recording()
            self.assertEqual(main.state.status, "recording", main.state.error_detail)
            self.assertEqual(main.state.hdr, "deck")
            self.assertFalse(main.state.hdr_rgb10)
            await asyncio.sleep(1)
            await main.finish_recording()
            return main.state.error

        main.kms_hdr_cmd, main.read_root_prop, main.write_root_prop = fake_hdr, props, no_write
        main.external_display = lambda: False
        main.DECK_PANEL = "oled"
        main.KMS_START_ATTEMPTS = 1
        settings = main.SETTINGS_DIR
        main.SETTINGS_DIR = self.temp.name
        try:
            self.assertEqual(asyncio.run(scenario()), "")
        finally:
            main.kms_hdr_cmd, main.read_root_prop, main.write_root_prop, main.external_display, \
                main.DECK_PANEL, main.KMS_START_ATTEMPTS = originals
            main.SETTINGS_DIR = settings
        self.assertEqual(calls, [(True, True), (False, False)])
        # Not tried again until the panel's retry button, which shows meanwhile.
        self.assertEqual(main._rgb10_order, "")
        self.assertTrue(main.snapshot()["hdr_rgb10_failed"])
        asyncio.run(main.Plugin().retry_rgb10())
        self.assertIsNone(main._rgb10_order)
        self.assertFalse(main.snapshot()["hdr_rgb10_failed"])

    def test_internal_pq_panel_sdr_stays_sdr(self):
        """A built-in PQ panel reporting HDR output: SDR content records on the SDR path,
        only an HDR game takes the PQ path (the panel may report HDR while it shows SDR)."""
        calls = []
        originals = main.kms_hdr_cmd, main.read_root_prop, main.write_root_prop, main.external_display, \
            main.internal_pq, main.DECK_PANEL
        app_hdr = {"value": "0"}

        async def props(name):
            if name == main.HDR_APP_PROP:
                return app_hdr["value"]
            return "1" if name == main.HDR_OUTPUT_PROP else "0"

        async def no_write(name, value):
            pass

        def fake_hdr(output, kbps, fps=60, mode="deck", rgb10=False):
            calls.append(mode)
            return synthetic_video(output, kbps, fps)

        async def scenario():
            main.state = main.State()
            main.state.prefs["kms_original_color"] = False
            main.state.prefs["method"] = "kms"
            main._rgb10_order = "ok"
            await main.start_recording()
            self.assertEqual(main.state.status, "recording", main.state.error_detail)
            hdr = main.state.hdr
            await asyncio.sleep(1)
            await main.finish_recording()
            return hdr, main.state.error

        main.kms_hdr_cmd, main.read_root_prop, main.write_root_prop = fake_hdr, props, no_write
        main.external_display = lambda: False
        main.internal_pq = lambda: True
        main.DECK_PANEL = ""
        settings = main.SETTINGS_DIR
        main.SETTINGS_DIR = self.temp.name
        try:
            self.assertEqual(asyncio.run(scenario()), ("", ""))
            app_hdr["value"] = "1"
            self.assertEqual(asyncio.run(scenario()), ("external", ""))
        finally:
            main.kms_hdr_cmd, main.read_root_prop, main.write_root_prop, main.external_display, \
                main.internal_pq, main.DECK_PANEL = originals
            main.SETTINGS_DIR = settings
        self.assertEqual(calls, ["external"])

    def test_hdr_falls_back_to_sdr(self):
        originals = main.kms_hdr_cmd, main.read_root_prop, main.KMS_START_ATTEMPTS

        async def hdr_on(name):
            return "1" if name == main.HDR_APP_PROP else "0"

        async def scenario():
            main.state = main.State()
            await main.start_recording()
            self.assertEqual(main.state.status, "recording", main.state.error_detail)
            self.assertFalse(main.state.hdr)
            await asyncio.sleep(1)
            await main.finish_recording()
            return main.state.error, main.state.last_file

        main.kms_hdr_cmd = lambda output, kbps, fps=60, mode="deck", rgb10=False: \
            main.ffmpeg_base() + ["-f", "nonexistent", "-i", "x", output]
        main.read_root_prop = hdr_on
        main.KMS_START_ATTEMPTS = 1
        try:
            error, output = asyncio.run(scenario())
        finally:
            main.kms_hdr_cmd, main.read_root_prop, main.KMS_START_ATTEMPTS = originals
        self.assertEqual(error, "")
        self.assertTrue(os.path.exists(output))
        # The SDR path keeps full-range values, so the file is relabeled full.
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                                "stream=color_range", "-of", "csv=p=0", output],
                               capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(probe, "pc")

    def test_unretryable_error_fails_at_once(self):
        calls = []

        def broken(output, kbps, fps=60):
            calls.append(output)
            return main.ffmpeg_base() + ["-f", "nonexistent", "-i", "x", output]

        async def scenario():
            main.state = main.State()
            main.kms_cmd = broken
            await main.start_recording()
            return main.state.status, main.state.error

        self.assertEqual(asyncio.run(scenario()), ("idle", "start_failed"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.composite, [True, False])
        self.assertFalse(os.path.exists(main.RESTORE_FILE))

    def test_display_restored_after_crash(self):
        writes = []

        async def record_write(name, value):
            writes.append((name, value))

        with open(main.RESTORE_FILE, "w", encoding="utf-8") as handle:
            handle.write('{"composite_force": true, "mura": {"value": "0"}}')
        main.write_root_prop = record_write
        main.state = main.State()
        asyncio.run(main.restore_after_crash())
        self.assertEqual(self.composite, [False])
        self.assertEqual(writes, [(main.MURA_DISABLED_PROP, "0")])
        self.assertFalse(os.path.exists(main.RESTORE_FILE))

    def test_panel_restored_after_crash(self):
        writes = []

        async def record_write(name, value):
            writes.append((name, value))

        saved = {main.MURA_DISABLED_PROP: "0", main.GAMUT_WIDENESS_PROP: "1056964608"}
        main.note_display_change(saved)
        main.write_root_prop = record_write
        main.state = main.State()
        asyncio.run(main.restore_after_crash())
        self.assertEqual(writes, list(saved.items()))
        self.assertEqual(self.composite, [False])

    def test_restore_file_cleared_after_recording(self):
        async def scenario():
            main.state = main.State()
            await main.start_recording()
            self.assertTrue(os.path.exists(main.RESTORE_FILE))
            await asyncio.sleep(1)
            await main.finish_recording()

        asyncio.run(scenario())
        self.assertFalse(os.path.exists(main.RESTORE_FILE))

    def test_leftover_capture_is_recovered(self):
        work_dir = os.path.join(self.temp.name, ".rec-20261118-120000")
        os.makedirs(work_dir)
        subprocess.run(synthetic_video(os.path.join(work_dir, "video.mkv"), 2000)[:-1] + ["-t", "2", os.path.join(work_dir, "video.mkv")], check=True)
        empty_dir = os.path.join(self.temp.name, ".rec-20261118-120500")
        os.makedirs(empty_dir)
        main.state = main.State()
        asyncio.run(main.recover_leftovers())
        recovered = os.path.join(self.temp.name, "Recording_20261118-120000_recovered.mp4")
        self.assertTrue(os.path.exists(recovered))
        self.assertFalse(os.path.exists(work_dir))
        self.assertFalse(os.path.exists(empty_dir))
        self.assertEqual((main.state.status, main.state.finished), ("idle", 1))

    def test_comparison_guards(self):
        """No separate recording between a comparison's steps; unloading stops the comparison."""
        main.state = main.State()
        main.state.compare_step = "pw"
        snap = asyncio.run(main.Plugin().start())
        self.assertEqual(snap["status"], "idle")
        asyncio.run(main.Plugin()._unload())
        self.assertTrue(main.state.compare_cancel)

    def test_label_set_outlives_a_recording(self):
        main.state = main.State()
        main.state.color_labelset = 2
        main.state.reset_capture()
        self.assertEqual(main.state.color_labelset, 2)

    def test_custom_save_folder(self):
        """Recordings go to the chosen folder; a bad or missing folder is refused, never created."""
        chosen = os.path.join(self.temp.name, "card")
        os.makedirs(chosen)
        prefs_file = main.PREFS_FILE
        main.PREFS_FILE = os.path.join(self.temp.name, "prefs.json")
        plugin = main.Plugin()

        async def record():
            await main.start_recording()
            status = main.state.status
            if status == "recording":
                await asyncio.sleep(1)
                await main.finish_recording()
            return status

        try:
            main.state = main.State()
            snap = asyncio.run(plugin.set_output_dir(chosen))
            self.assertEqual((snap["output_dir"], snap["output_dir_custom"]), (chosen, True))
            self.assertEqual(asyncio.run(record()), "recording")
            self.assertEqual(os.path.dirname(main.state.last_file), chosen)

            # Not a folder, or one the user cannot write to: refused, the choice stays.
            snap = asyncio.run(plugin.set_output_dir(os.path.join(self.temp.name, "nope")))
            self.assertEqual((snap["error"], snap["output_dir"]), ("output_invalid", chosen))
            snap = asyncio.run(plugin.set_output_dir("/usr"))
            self.assertEqual((snap["error"], snap["output_dir"]), ("output_invalid", chosen))

            # The chosen folder went away (card removed): no recording, nothing created.
            shutil.rmtree(chosen)
            main.state.error = ""
            self.assertEqual(asyncio.run(record()), "idle")
            self.assertEqual(main.state.error, "start_failed")
            self.assertFalse(os.path.exists(chosen))

            # Back to the default folder.
            snap = asyncio.run(plugin.set_output_dir(""))
            self.assertEqual((snap["output_dir"], snap["output_dir_custom"]), (main.OUTPUT_DIR, False))
        finally:
            main.PREFS_FILE = prefs_file

    def test_leftovers_found_in_default_folder_too(self):
        chosen = os.path.join(self.temp.name, "card")
        os.makedirs(chosen)
        work_dir = os.path.join(self.temp.name, ".rec-20261118-130000")
        os.makedirs(work_dir)
        subprocess.run(synthetic_video(os.path.join(work_dir, "video.mkv"), 2000)[:-1] + ["-t", "2", os.path.join(work_dir, "video.mkv")], check=True)
        main.state = main.State()
        main.state.prefs["output_dir"] = chosen
        asyncio.run(main.recover_leftovers())
        # Saved next to its capture folder, in the default folder.
        self.assertTrue(os.path.exists(os.path.join(self.temp.name, "Recording_20261118-130000_recovered.mp4")))

    def test_missing_microphone_is_reported(self):
        original = main.default_source

        async def no_source():
            return ""

        async def scenario():
            main.state = main.State()
            main.state.prefs["mic"] = True
            await main.start_recording()
            result = main.state.status, main.state.error, main.state.error_count
            await main.finish_recording()
            return result

        main.default_source = no_source
        try:
            self.assertEqual(asyncio.run(scenario()), ("recording", "mic_missing", 1))
        finally:
            main.default_source = original

    def test_lost_audio_is_reported(self):
        async def scenario():
            main.state = main.State()
            await main.start_recording()
            main.state.audio.kill()
            await asyncio.sleep(2.5)
            result = main.state.status, main.state.error
            await main.finish_recording()
            return result

        self.assertEqual(asyncio.run(scenario()), ("recording", "audio_stopped"))

    def test_timer_counts_from_video_start(self):
        """The card shows the capture during the start check, timed from the video spawn."""
        async def scenario():
            main.state = main.State()
            task = asyncio.create_task(main.start_recording())
            seen = None
            while not task.done():
                snap = main.snapshot()
                if snap["status"] == "starting" and snap["capturing"]:
                    seen = snap
                await asyncio.sleep(0.05)
            started = main.snapshot()
            await main.finish_recording()
            return seen, started, main.snapshot()

        seen, started, after = asyncio.run(scenario())
        self.assertIsNotNone(seen)
        # The card already shows the capture's own size and frame rate.
        self.assertEqual((seen["size"], seen["fps"]), (started["size"], 60))
        self.assertTrue(started["capturing"])
        self.assertGreaterEqual(started["elapsed"], main.START_CHECK_SEC)
        self.assertFalse(after["capturing"])
        self.assertEqual(after["elapsed"], 0)

    def test_kms_retries_early_exit(self):
        calls = []

        def flaky(output, kbps, fps=60):
            calls.append(output)
            if len(calls) == 1:
                return ["sh", "-c", "echo 'Plane 80 framebuffer format changed: now 30335258.' >&2; exit 1"]
            return synthetic_video(output, kbps, fps)

        async def scenario():
            main.state = main.State()
            main.kms_cmd = flaky
            await main.start_recording()
            self.assertEqual(main.state.status, "recording", main.state.error_detail)
            await asyncio.sleep(1)
            await main.finish_recording()
            return main.state.error, main.state.last_file

        error, output = asyncio.run(scenario())
        self.assertEqual(error, "")
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.composite, [True, False])
        self.assertTrue(os.path.exists(output))

    def test_static_screen_passes_start_check(self):
        """A still picture makes tiny packets; with FLUSH_ARGS they reach the file in time."""
        def static_screen(output, kbps, fps=60):
            return main.ffmpeg_base() + [
                "-re", "-vaapi_device", main.VAAPI_DEVICE, "-f", "lavfi", "-i", f"color=c=gray:s=1280x800:r={fps}",
                "-vf", "format=nv12,hwupload",
            ] + main.encode_args(kbps, fps) + main.FLUSH_ARGS + [output]

        async def scenario():
            main.state = main.State()
            main.kms_cmd = static_screen
            await main.start_recording()
            status = main.state.status
            if status == "recording":
                await asyncio.sleep(1)
                await main.finish_recording()
            return status, main.state.error

        self.assertEqual(asyncio.run(scenario()), ("recording", ""))

    def test_stalled_kms_start_fails_fast(self):
        """A KMS ffmpeg that runs but writes nothing (and ignores SIGINT) is killed at start."""
        calls = []

        def stalled(output, kbps, fps=60):
            calls.append(output)
            return ["sh", "-c", "trap '' INT; sleep 60"]

        async def scenario():
            main.state = main.State()
            main.kms_cmd = stalled
            began = time.monotonic()
            await main.start_recording()
            return main.state.status, main.state.error, time.monotonic() - began

        status, error, took = asyncio.run(scenario())
        self.assertEqual((status, error), ("idle", "kms_no_frames"))
        self.assertEqual(len(calls), 1)
        self.assertLess(took, main.KMS_FIRST_DATA_SEC + 3)
        self.assertEqual(self.composite, [True, False])
        self.assertEqual([name for name in os.listdir(self.temp.name) if name.startswith(".rec-")], [])

    def test_header_only_kms_start_fails_fast(self):
        """Seen on one external display: ffmpeg writes the file header, reports 0 frames and hangs."""
        def header_only(output, kbps, fps=60):
            progress = os.path.join(os.path.dirname(output), main.PROGRESS_NAME)
            return ["sh", "-c", f"trap '' INT; head -c 620 /dev/zero > '{output}'; "
                                f"printf 'frame=0\\nprogress=continue\\n' > '{progress}'; sleep 60"]

        async def scenario():
            main.state = main.State()
            main.kms_cmd = header_only
            await main.start_recording()
            return main.state.status, main.state.error

        self.assertEqual(asyncio.run(scenario()), ("idle", "kms_no_frames"))
        self.assertEqual(self.composite, [True, False])

    def test_frames_done(self):
        temp = tempfile.TemporaryDirectory()
        try:
            self.assertIsNone(main.frames_done(temp.name))
            with open(os.path.join(temp.name, main.PROGRESS_NAME), "w") as file:
                file.write("frame=0\nprogress=continue\nframe=42\nfps=60\nprogress=continue\n")
            self.assertEqual(main.frames_done(temp.name), 42)
        finally:
            temp.cleanup()

    def test_failed_start_restores_composite(self):
        async def scenario():
            main.state = main.State()
            main.kms_cmd = lambda output, kbps, fps=60: main.ffmpeg_base() + ["-f", "nonexistent", "-i", "x", output]
            await main.start_recording()
            return main.state.status, main.state.error

        self.assertEqual(asyncio.run(scenario()), ("idle", "start_failed"))
        self.assertEqual(self.composite, [True, False])


if __name__ == "__main__":
    unittest.main()
