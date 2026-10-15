import subprocess

from streamtv import ffmpeg as ff
from tests.conftest import FFMPEG, needs_ffmpeg


def test_remux_cmd_two_inputs_with_headers():
    cmd = ff.build_remux_cmd("ffmpeg", [
        ff.InputSpec("https://cdn/v.m3u8", {"User-Agent": "UA", "Referer": "https://site/"}),
        ff.InputSpec("https://cdn/a.m3u8", {}),
    ], start=90)
    assert cmd[cmd.index("-user_agent") + 1] == "UA"
    assert cmd[cmd.index("-headers") + 1] == "Referer: https://site/\r\n"
    assert cmd.count("-i") == 2 and cmd.count("-ss") == 2
    assert ["-map", "0:v:0", "-map", "1:a:0"] == cmd[cmd.index("-map"):cmd.index("-map") + 4]
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[-1] == "pipe:1" and "mpegts" in cmd


def test_remux_cmd_local_file_transcode():
    cmd = ff.build_remux_cmd("ffmpeg", [ff.InputSpec("C:/Videos/a.mkv")], transcode_video=True, max_height=720)
    assert "-reconnect" not in cmd and "-headers" not in cmd
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert "min(720,ih)" in cmd[cmd.index("-vf") + 1]


def test_needs_transcode():
    assert not ff.video_needs_transcode("avc1.64001F")
    assert not ff.video_needs_transcode("hev1.1.6.L93")
    assert not ff.video_needs_transcode(None)
    assert ff.video_needs_transcode("vp09.00.40.08")
    assert ff.video_needs_transcode("av01.0.08M.08")


def test_mirror_cmd_variants():
    pcm = ff.build_mirror_cmd("ffmpeg", audio="pcm")
    assert "gdigrab" in pcm and "pipe:0" in pcm and "1:a:0" in pcm and "-nostdin" not in pcm
    none = ff.build_mirror_cmd("ffmpeg", audio="none")
    assert "1:a:0" not in none and "-nostdin" in none and "-c:a" not in none
    ds = ff.build_mirror_cmd("ffmpeg", audio="dshow", dshow_device="Stereomix (Realtek(R) Audio)")
    assert "audio=Stereomix (Realtek(R) Audio)" in ds


def test_parse_dshow_devices_old_and_new_format():
    old = '''[dshow @ 0x1] DirectShow video devices (some may be both video and audio devices)
[dshow @ 0x1]  "Integrated Camera"
[dshow @ 0x1]     Alternative name "@device_pnp_\\\\?\\usb"
[dshow @ 0x1] DirectShow audio devices
[dshow @ 0x1]  "Stereomix (Realtek(R) Audio)"
[dshow @ 0x1]     Alternative name "@device_cm_{33D9}"
[dshow @ 0x1]  "Mikrofon (Realtek(R) Audio)"'''
    assert ff.parse_dshow_devices(old) == ["Stereomix (Realtek(R) Audio)", "Mikrofon (Realtek(R) Audio)"]
    new = '''[in#0 @ 0x2] "Integrated Camera" (video)
[in#0 @ 0x2]   Alternative name "@device_pnp_x"
[in#0 @ 0x2] "Stereomix (Realtek(R) Audio)" (audio)'''
    assert ff.parse_dshow_devices(new) == ["Stereomix (Realtek(R) Audio)"]


def test_parse_probe():
    out = """Input #0, matroska,webm, from 'a.mkv':
  Duration: 01:30:05.20, start: 0.000000, bitrate: 5000 kb/s
  Stream #0:0(eng): Video: hevc (Main 10), yuv420p10le, 3840x2160
  Stream #0:1(ger): Audio: dts (DTS), 48000 Hz, 5.1(side)"""
    assert ff.parse_probe(out) == ("hevc", 5405)


@needs_ffmpeg
def test_remux_produces_mpegts(sample_video, tmp_path):
    out = subprocess.run(ff.build_remux_cmd(FFMPEG, [ff.InputSpec(str(sample_video))], start=2),
                         capture_output=True, check=True).stdout
    assert out[0] == 0x47 and out[188] == 0x47  # TS-Sync-Bytes
    f = tmp_path / "o.ts"
    f.write_bytes(out)
    vcodec, duration = ff.parse_probe(subprocess.run([FFMPEG, "-hide_banner", "-i", str(f)],
                                                     capture_output=True, text=True).stderr)
    assert vcodec == "h264"
    probe = subprocess.run([FFMPEG, "-hide_banner", "-i", str(f)], capture_output=True, text=True).stderr
    assert "Audio: aac" in probe


@needs_ffmpeg
def test_remux_merges_separate_video_and_audio(sample_video, tmp_path):
    v, a = tmp_path / "v.mp4", tmp_path / "a.m4a"
    subprocess.run([FFMPEG, "-loglevel", "error", "-i", str(sample_video), "-an", "-c", "copy", str(v)], check=True)
    subprocess.run([FFMPEG, "-loglevel", "error", "-i", str(sample_video), "-vn", "-c", "copy", str(a)], check=True)
    out = subprocess.run(ff.build_remux_cmd(FFMPEG, [ff.InputSpec(str(v)), ff.InputSpec(str(a))]),
                         capture_output=True, check=True).stdout
    f = tmp_path / "m.ts"
    f.write_bytes(out)
    probe = subprocess.run([FFMPEG, "-hide_banner", "-i", str(f)], capture_output=True, text=True).stderr
    assert "Video: h264" in probe and "Audio: aac" in probe


@needs_ffmpeg
def test_mirror_encoding_options_are_accepted(tmp_path):
    """Spiegel-Befehl mit Testbild statt gdigrab (gibt es nur unter Windows) und PCM über stdin."""
    cmd = ff.build_mirror_cmd(FFMPEG, fps=10, audio="pcm")
    i = cmd.index("gdigrab")
    cmd[i - 1:i + 7] = ["-f", "lavfi", "-i", "testsrc=size=640x360:rate=10"]
    cmd = cmd[: cmd.index("-f", len(cmd) - 6)] + ["-t", "1", "-f", "mpegts", str(tmp_path / "m.ts")]
    pcm = b"\x00\x00" * 2 * 48000 * 2
    r = subprocess.run(cmd, input=pcm, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr.decode()
    probe = subprocess.run([FFMPEG, "-hide_banner", "-i", str(tmp_path / "m.ts")], capture_output=True, text=True).stderr
    assert "Video: h264" in probe and "Audio: aac" in probe
