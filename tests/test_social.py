"""Tests for the Social tab: the video composer, the workspace and the Metricool connector.

Run:  python -m unittest discover -s tests -v

Nothing here touches the network or Metricool: the OAuth/MCP code is exercised with a
patched request function, and the renderer's own ffmpeg run is skipped when ffmpeg is
not installed.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

if not os.environ.get("LS_DATA"):
    os.environ["LS_DATA"] = tempfile.mkdtemp(prefix="ls_social_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import config, metricool, tasks, video, video_maker  # noqa: E402
from engine import server as web  # noqa: E402


def wipe_video():
    for path in (video.inputs_dir(), video.output_dir(), video.work_dir()):
        shutil.rmtree(path, ignore_errors=True)
    video.ensure_dirs()


def make_picture(name="cover.png", size=(800, 600), colour=(20, 30, 50)):
    """A real PNG, so the renderer's drawing path can run without a network."""
    from PIL import Image
    image = Image.new("RGB", size, colour)
    path = os.path.join(tempfile.mkdtemp(prefix="ls_pic_"), name)
    image.save(path)
    return path


def make_tone(seconds=1):
    """A real MP3 tone, so the render has an audio track to write."""
    exe = shutil.which("ffmpeg")
    if not exe:
        return ""
    path = os.path.join(tempfile.mkdtemp(prefix="ls_tone_"), "tone.mp3")
    subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=%d" % seconds, "-c:a", "libmp3lame", path],
                   check=False, capture_output=True)
    return path if os.path.isfile(path) and os.path.getsize(path) > 0 else ""


class VideoSpecTests(unittest.TestCase):
    def setUp(self):
        wipe_video()

    def add(self, name, data=b"x"):
        return video.save_upload(name, data)

    def test_a_picture_or_a_video_is_mandatory(self):
        spec, error = video.build_spec({})
        self.assertIsNone(spec)
        self.assertIn("picture or a video", error)

        self.add("cover.png")
        spec, error = video.build_spec({"media": "cover.png"})
        self.assertIn("audio", error)

    def test_a_picture_needs_audio_but_a_video_does_not(self):
        self.add("cover.png")
        self.add("clip.mp4")
        _, error = video.build_spec({"media": "cover.png"})
        self.assertIn("audio", error)
        spec, error = video.build_spec({"media": "clip.mp4"})
        self.assertEqual(error, "")
        self.assertEqual(spec["media_kind"], "video")

    def test_a_complete_spec_keeps_the_formats_apart(self):
        self.add("cover.png")
        self.add("tone.mp3")
        spec, error = video.build_spec({"media": "cover.png", "audio": "tone.mp3",
                                        "format": "9:16", "size": "720p",
                                        "title": "Launch clip",
                                        "description": "A line of caption."})
        self.assertEqual(error, "")
        self.assertEqual(spec["format"], "9:16")
        self.assertEqual((spec["width"], spec["height"]), (720, 1280))
        self.assertEqual(spec["media_kind"], "image")
        self.assertTrue(spec["media"].startswith(video.inputs_dir()))
        self.assertTrue(spec["audio"].startswith(video.inputs_dir()))
        self.assertEqual(spec["out_dir"], video.output_dir())
        self.assertEqual(spec["work_dir"], video.work_dir())
        self.assertTrue(spec["output"].startswith("launch-clip"))
        self.assertTrue(spec["output"].endswith(".mp4"))

    def test_unknown_format_and_size_fall_back(self):
        self.add("cover.png")
        self.add("tone.mp3")
        spec, _ = video.build_spec({"media": "cover.png", "audio": "tone.mp3",
                                    "format": "--evil", "size": "8k"})
        self.assertEqual(spec["format"], "16:9")
        self.assertEqual(spec["size"], "1080p")
        self.assertEqual((spec["width"], spec["height"]), (1920, 1080))

    def test_a_file_outside_the_upload_folder_is_refused(self):
        self.add("cover.png")
        self.add("tone.mp3")
        _, error = video.build_spec({"media": "../../leadstudio.db", "audio": "tone.mp3"})
        self.assertIn("not in the upload folder", error)

    def test_a_wrong_kind_of_audio_is_refused(self):
        self.add("cover.png")
        self.add("notes.txt")
        _, error = video.build_spec({"media": "cover.png", "audio": "notes.txt"})
        self.assertIn("not an audio file", error)

    def test_the_renderer_must_be_installed(self):
        self.add("cover.png")
        self.add("tone.mp3")
        with mock.patch.object(video, "deps", return_value={
                "ffmpeg": False, "pillow": True, "ready": False}):
            spec, error = video.build_spec({"media": "cover.png", "audio": "tone.mp3"})
        self.assertIsNone(spec)
        self.assertIn("ffmpeg", error)

    def test_every_format_reports_even_dimensions(self):
        for fmt in [f["key"] for f in video.FORMATS]:
            for size in [s["key"] for s in video.SIZES]:
                width, height = video.dims(fmt, size)
                self.assertEqual(width % 2, 0, (fmt, size))
                self.assertEqual(height % 2, 0, (fmt, size))
                self.assertGreater(width, 0)
                self.assertGreater(height, 0)


class VideoFileTests(unittest.TestCase):
    def setUp(self):
        wipe_video()

    def test_upload_list_and_remove(self):
        res = video.save_upload("Cover Art.png", b"image")
        self.assertTrue(res["ok"])
        self.assertEqual(res["kind"], "image")
        state = video.state()
        self.assertEqual([f["name"] for f in state["images"]], ["Cover Art.png"])
        removed = video.remove("input", "Cover Art.png")
        self.assertTrue(removed["ok"])
        self.assertEqual(video.state()["images"], [])

    def test_audio_and_video_land_in_their_own_lists(self):
        video.save_upload("song.mp3", b"a")
        video.save_upload("clip.mp4", b"v")
        state = video.state()
        self.assertEqual([f["name"] for f in state["audio"]], ["song.mp3"])
        self.assertEqual([f["name"] for f in state["videos"]], ["clip.mp4"])

    def test_other_file_types_are_refused(self):
        self.assertFalse(video.save_upload("notes.exe", b"x")["ok"])
        self.assertFalse(video.save_upload("lyrics.txt", b"x")["ok"])

    def test_a_path_in_the_name_lands_inside_the_folder(self):
        res = video.save_upload("../../escape.mp3", b"x")
        self.assertTrue(res["ok"])
        self.assertEqual(res["name"], "escape.mp3")
        self.assertTrue(os.path.isfile(os.path.join(video.inputs_dir(), "escape.mp3")))

    def test_a_name_cannot_escape_the_folder(self):
        self.assertEqual(video.file_for("input", "../leadstudio.db"), "")
        self.assertEqual(video.file_for("output", "..\\x.mp4"), "")
        self.assertEqual(video.clean_name("../../evil.mp3"), "evil.mp3")

    def test_kind_of_reads_the_extension(self):
        self.assertEqual(video.kind_of("a.MP3"), "audio")
        self.assertEqual(video.kind_of("b.JPEG"), "image")
        self.assertEqual(video.kind_of("c.MOV"), "video")
        self.assertEqual(video.kind_of("d.pdf"), "")
        self.assertEqual(video.kind_of(""), "")

    def test_the_state_lists_the_renderer_settings(self):
        state = video.state()
        self.assertIn("ready", state["deps"])
        self.assertTrue([f["key"] for f in state["formats"]])
        self.assertTrue([s["key"] for s in state["sizes"]])
        self.assertIn("inputs", state["dirs"])


class RenderTests(unittest.TestCase):
    """A real render, only when ffmpeg and Pillow are there."""

    @classmethod
    def setUpClass(cls):
        if not shutil.which("ffmpeg"):
            raise unittest.SkipTest("ffmpeg is not installed")
        try:
            __import__("PIL")
        except ImportError:
            raise unittest.SkipTest("Pillow is not installed")

    def setUp(self):
        wipe_video()

    def test_a_picture_and_a_tone_become_an_mp4(self):
        picture = make_picture()
        tone = make_tone(1)
        if not tone:
            self.skipTest("could not build a test tone")
        with open(picture, "rb") as handle:
            video.save_upload("cover.png", handle.read())
        with open(tone, "rb") as handle:
            video.save_upload("tone.mp3", handle.read())
        spec, error = video.build_spec({"media": "cover.png", "audio": "tone.mp3",
                                        "format": "1:1", "size": "480p",
                                        "title": "A title", "description": "A description."})
        self.assertEqual(error, "")
        path = os.path.join(video.work_dir(), "spec_test.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(spec, handle)
        self.assertEqual(video_maker.main(["--spec", path]), 0)
        output = os.path.join(spec["out_dir"], spec["output"])
        self.assertTrue(os.path.isfile(output))
        self.assertGreater(os.path.getsize(output), 1000)
        self.assertIn(spec["output"], [f["name"] for f in video.state()["outputs"]])

    def test_the_cover_frame_carries_the_text(self):
        picture = make_picture(size=(640, 360))
        spec = {"media": picture, "media_kind": "image", "width": 640, "height": 360,
                "title": "Five hooks that work", "description": "One line about it.",
                "work_dir": video.work_dir(), "out_dir": video.output_dir(),
                "audio": "", "format": "16:9", "size": "480p", "output": "x.mp4"}
        frame = video_maker.compose(spec)
        self.assertTrue(os.path.isfile(frame))
        from PIL import Image
        with Image.open(frame) as image:
            self.assertEqual(image.size, (640, 360))


class TaskTests(unittest.TestCase):
    def wait(self, task_id, seconds=15):
        deadline = time.time() + seconds
        while time.time() < deadline:
            task = tasks.get(task_id)
            if task and task["status"] != "running":
                return task
            time.sleep(0.05)
        self.fail("the task did not finish")

    def test_a_command_runs_and_its_output_is_kept(self):
        task_id = tasks.start("probe", [sys.executable, "-c", "print('hello'); print('world')"])
        task = self.wait(task_id)
        self.assertEqual(task["status"], "ok", task)
        self.assertIn("hello", task["log"])
        self.assertIn("world", task["log"])
        self.assertEqual(task["returncode"], 0)

    def test_a_failing_command_reports_the_last_line(self):
        task_id = tasks.start("bad", [sys.executable, "-c",
                                      "import sys; print('boom'); sys.exit(3)"])
        task = self.wait(task_id)
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["returncode"], 3)
        self.assertIn("boom", task["error"])

    def test_unknown_and_recent_lookups(self):
        self.assertIsNone(tasks.get("does-not-exist"))
        self.assertIsInstance(tasks.recent(3), list)


class MetricoolParsingTests(unittest.TestCase):
    def test_json_answer(self):
        raw = json.dumps({"jsonrpc": "2.0", "id": 4, "result": {"tools": [{"name": "a"}]}})
        obj = metricool._parse_rpc(raw, "application/json", 4)
        self.assertEqual(obj["result"]["tools"][0]["name"], "a")

    def test_server_sent_events_answer(self):
        raw = ('event: message\n'
               'data: {"jsonrpc":"2.0","id":9,"result":{"ok":true}}\n\n')
        obj = metricool._parse_rpc(raw, "text/event-stream", 9)
        self.assertTrue(obj["result"]["ok"])

    def test_an_event_stream_without_our_id_is_an_error(self):
        raw = 'data: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'
        with self.assertRaises(metricool.MetricoolError):
            metricool._parse_rpc(raw, "text/event-stream", 9)

    def test_arguments_follow_the_tool_schema(self):
        schema = {"properties": {"network": {"type": "string"},
                                 "initDate": {"type": "string"},
                                 "endDate": {"type": "string"}}}
        args = metricool.build_arguments(schema, {"network": "instagram", "from": "2026-01-01",
                                                  "init": "2026-01-01", "to": "2026-02-01",
                                                  "end": "2026-02-01", "caption": ""})
        self.assertEqual(args["network"], "instagram")
        self.assertIn("2026-01-01", args.values())
        self.assertIn("2026-02-01", args.values())
        self.assertNotIn("caption", args)

    def test_tool_discovery_prefers_names_over_descriptions(self):
        items = [{"name": "list_posts", "description": "list scheduled posts", "schema": {}},
                 {"name": "get_analytics", "description": "metrics", "schema": {}},
                 {"name": "schedule_post", "description": "create", "schema": {}}]
        self.assertEqual(metricool.find_tool(items, ("schedule",))["name"], "schedule_post")
        self.assertEqual(metricool.find_tool(items, ("analytics",))["name"], "get_analytics")
        self.assertIsNone(metricool.find_tool(items, ("nonexistent",)))


class MetricoolWorkspaceTests(unittest.TestCase):
    TOOLS = [
        {"name": "list_conversations", "description": "the unified inbox", "schema": {}},
        {"name": "schedule_post", "description": "queue a post", "schema": {}},
        {"name": "ads_performance", "description": "ad spend", "schema": {}},
        {"name": "get_analytics", "description": "metrics by network", "schema": {}},
    ]

    def test_each_section_finds_its_tool(self):
        caps = metricool.capabilities(self.TOOLS)
        self.assertEqual(caps["inbox"], "list_conversations")
        self.assertEqual(caps["planner"], "schedule_post")
        self.assertEqual(caps["ads"], "ads_performance")
        self.assertEqual(caps["reports"], "get_analytics")

    def test_workspace_describes_the_sections(self):
        with mock.patch.object(metricool, "tools", return_value=self.TOOLS):
            data = metricool.workspace()
        self.assertTrue(data["ok"])
        self.assertEqual([s["key"] for s in data["sections"]],
                         ["inbox", "planner", "ads", "reports"])
        self.assertTrue(all(s["available"] for s in data["sections"]))
        self.assertEqual(data["count"], 4)

    def test_a_section_with_no_tool_says_so(self):
        with mock.patch.object(metricool, "tools", return_value=[]):
            result = metricool.section("inbox")
        self.assertFalse(result["ok"])
        self.assertTrue(result["unavailable"])
        self.assertIn("Inbox", result["error"])

    def test_an_unknown_section_is_refused(self):
        self.assertFalse(metricool.section("nonsense")["ok"])

    def test_a_reply_needs_text(self):
        self.assertFalse(metricool.reply("c1", "   ")["ok"])

    def test_tools_are_cached_between_calls(self):
        calls = {"n": 0}

        class FakeClient:
            def tools(self):
                calls["n"] += 1
                return [{"name": "one", "description": "", "inputSchema": {}}]

        with mock.patch.object(metricool, "_session", return_value=FakeClient()):
            metricool._TOOLS.update({"at": 0.0, "items": []})
            metricool.tools()
            metricool.tools()
        self.assertEqual(calls["n"], 1)


class MetricoolTokenTests(unittest.TestCase):
    def setUp(self):
        self.saved = config.load_settings()
        block = dict(self.saved)
        block.pop("metricool", None)
        config.save_settings(block)

    def tearDown(self):
        current = config.load_settings()
        current.pop("metricool", None)
        config.save_settings(current)

    def test_status_reports_not_connected(self):
        status = metricool.status()
        self.assertFalse(status["connected"])
        self.assertIn("Free", status["note"])

    def test_the_affiliate_link_and_its_disclosure_are_exposed(self):
        status = metricool.status()
        self.assertEqual(status["signup_url"], "https://my.mtr.cool/NEMGNE")
        self.assertIn("affiliate", status["affiliate_note"].lower())
        self.assertIn("not affiliated", metricool.NOT_AFFILIATED.lower())
        self.assertIn("Metricool", metricool.NOT_AFFILIATED)

    def test_disconnect_clears_the_tokens(self):
        settings = config.load_settings()
        settings["metricool"] = {"access_token": "a", "refresh_token": "r", "expires_at": 1}
        config.save_settings(settings)
        self.assertTrue(metricool.status()["connected"])
        metricool.disconnect()
        self.assertFalse(metricool.status()["connected"])

    def test_an_expired_token_is_refreshed_and_saved(self):
        settings = config.load_settings()
        settings["metricool"] = {"client_id": "cid", "access_token": "old",
                                 "refresh_token": "refresh-1", "expires_at": 1}
        config.save_settings(settings)
        answers = [(200, {}, json.dumps({"access_token": "new", "expires_in": 3600,
                                         "refresh_token": "refresh-2"}))]
        metadata = {"authorization_endpoint": "https://example.invalid/authorize",
                    "token_endpoint": "https://example.invalid/token",
                    "registration_endpoint": "https://example.invalid/register"}

        def fake(url, data=None, headers=None, form=False, timeout=45):
            if data is None:              # the discovery document
                return 200, {}, json.dumps(metadata)
            self.assertIn("refresh_token", data)
            return answers.pop(0)

        original = metricool._request
        metricool._request = fake
        try:
            self.assertEqual(metricool.access_token(), "new")
        finally:
            metricool._request = original
        stored = metricool.saved()
        self.assertEqual(stored["access_token"], "new")
        self.assertEqual(stored["refresh_token"], "refresh-2")
        self.assertGreater(stored["expires_at"], time.time())

    def test_a_missing_connection_is_a_clear_error(self):
        with self.assertRaises(metricool.MetricoolError) as ctx:
            metricool.access_token()
        self.assertIn("not connected", str(ctx.exception))


class SocialHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = web.make_server(0)
        cls.port = cls.httpd.server_address[1]
        web.PORT = cls.port
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.port
        cls.token = config.load_settings()["session_token"]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        wipe_video()
        settings = config.load_settings()
        settings.pop("metricool", None)
        config.save_settings(settings)

    def get(self, path):
        req = urllib.request.Request(self.base + path)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()

    def post(self, path, payload):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "X-LS-Token": self.token},
            method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())

    def test_state_lists_the_composer(self):
        status, raw = self.get("/api/social/state")
        self.assertEqual(status, 200)
        data = json.loads(raw.decode())
        self.assertTrue(data["ok"])
        self.assertIn("deps", data["video"])
        self.assertIn("formats", data["video"])
        self.assertIn("sizes", data["video"])
        self.assertFalse(data["metricool"]["connected"])
        self.assertEqual(data["port"], self.port)

    def test_the_old_lyrics_endpoint_is_gone(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/social/lyrics", {"name": "words", "text": "one line"})
        self.assertEqual(ctx.exception.code, 404)

    def test_an_empty_render_never_starts(self):
        answer = self.post("/api/social/make", {"spec": {}})
        self.assertFalse(answer["ok"])
        self.assertIn("picture or a video", answer["error"])

    def test_an_upload_reports_its_kind(self):
        answer = self.post("/api/social/upload", {"name": "clip.mp4", "b64": "AAAA"})
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["kind"], "video")

    def test_a_workspace_needs_a_connection(self):
        data = json.loads(self.get("/api/social/workspace")[1].decode())
        self.assertFalse(data["ok"])
        self.assertFalse(data["connected"])

    def test_a_section_needs_a_connection(self):
        data = json.loads(self.get("/api/social/section?name=inbox")[1].decode())
        self.assertFalse(data["ok"])
        self.assertIn("Connect", data["error"])

    def test_a_reply_needs_text(self):
        self.assertFalse(self.post("/api/social/reply", {"conversation": "c1", "text": ""})["ok"])

    def test_a_finished_video_can_be_streamed_back(self):
        with open(os.path.join(video.output_dir(), "clip.mp4"), "wb") as fh:
            fh.write(b"\x00\x00\x00\x18ftypmp42")
        status, body = self.get("/api/social/file?kind=output&name=clip.mp4")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"\x00\x00\x00\x18ftyp"))

    def test_a_video_is_reachable_through_the_public_tracking_path(self):
        # Metricool has to fetch the file itself, so the video is served under /t/ as well.
        with open(os.path.join(video.output_dir(), "posted.mp4"), "wb") as fh:
            fh.write(b"ftypmp42-posted")
        status, body = self.get("/t/v/posted.mp4")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"ftypmp42-posted")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/t/v/not-there.mp4")
        self.assertEqual(ctx.exception.code, 404)

    def test_a_file_outside_the_folder_is_a_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/social/file?kind=output&name=..%2Fleadstudio.db")
        self.assertEqual(ctx.exception.code, 404)

    def test_metricool_state_needs_no_network(self):
        data = json.loads(self.get("/api/social/state")[1].decode())
        self.assertEqual(data["metricool"]["mcp_url"], metricool.MCP_URL)

    def test_the_state_carries_the_legal_notice(self):
        data = json.loads(self.get("/api/state")[1].decode())
        legal = data["legal"]
        self.assertEqual(legal["signup_url"], metricool.SIGNUP_URL)
        self.assertIn("affiliate", legal["affiliate_note"].lower())
        self.assertIn("not affiliated", legal["not_affiliated"].lower())
        self.assertEqual(legal["licence"], "MIT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
