"""Formatter regression tests without starting Telegram or connecting to MongoDB."""
import ast
import re
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "Backend/fastapi/routes/stremio_routes.py"

def load_formatter(source, parser=None):
    tree = ast.parse(source)
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "format_stream_details")
    namespace = {"re": re, "PTN": parser or types.SimpleNamespace(parse=lambda name: {})}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(PATH), "exec"), namespace)
    return namespace["format_stream_details"]

format_details = load_formatter(PATH.read_text(encoding="utf-8"))

class FormatterTests(unittest.TestCase):
    def test_release_tags_and_separators(self):
        cases = {
            "IMAX": "IMAX", "IMAX.Enhanced": "IMAX Enhanced", "SDR": "SDR",
            "Extended.Edition": "Extended", "Uncut": "Uncut", "Directors.Cut": "Director's Cut",
            "Director’s.Cut": "Director's Cut", "Remastered": "Remastered",
            "Dual.Audio": "Dual Audio", "MultiAudio": "Multi Audio", "MULTISUB": "Multi Sub",
            "HARDSUB": "Hard Sub", "AMZN": "AMZN", "NF": "NF", "DSNP": "DSNP",
            "6CH": "6ch", "8CH": "8ch", "Stereo": "Stereo", "Mono": "Mono",
        }
        for token, expected in cases.items():
            for separator in (".", " ", "_", "-"):
                with self.subTest(token=token, separator=separator):
                    filename = separator.join(["Movie", "2024", "2160p", token.replace(".", separator), "WEB-DL", "HEVC", "mkv"])
                    name, details = format_details(filename, "2160p", "1 GB")
                    self.assertEqual(name, "4K UHD")
                    self.assertIn(expected, details)

    def test_dtsx_aliases_without_duplicate_dts(self):
        for token in ("DTS:X", "DTS-X", "DTS.X", "DTS_X", "DTSX"):
            with self.subTest(token=token):
                _, details = format_details(f"Movie.2024.1080p.{token}.7.1.mkv", "1080p", "")
                self.assertIn("♫ DTS:X 7.1", details)
                self.assertNotIn("DTS:X · DTS", details)

    def test_remux_does_not_invent_bluray(self):
        for suffix, expected in (("REMUX", "REMUX"), ("BluRay.REMUX", "BluRay REMUX"), ("BDREMUX", "BluRay REMUX"), ("WEB-DL.REMUX", "WEB-DL REMUX")):
            with self.subTest(suffix=suffix):
                _, details = format_details(f"Movie.2024.2160p.{suffix}.mkv", "2160p", "")
                self.assertIn(expected, details)
                if "BluRay" not in expected:
                    self.assertNotIn("BluRay", details)

    def test_interlaced_resolution(self):
        for token, expected in (("1080i", "1080i FHD"), ("720i", "720i HD"), ("1080p", "1080p FHD"), ("720p", "720p HD")):
            self.assertEqual(format_details(f"Movie.2024.{token}.mkv", "", "")[0], expected)

    def test_channel_counts_are_not_guessed(self):
        for token in ("6CH", "8CH"):
            _, details = format_details(f"Movie.2024.1080p.AAC.{token}.mkv", "1080p", "")
            self.assertIn(token.lower(), details)
            self.assertNotIn("5.1", details)
            self.assertNotIn("7.1", details)
        self.assertIn("6.1", format_details("Movie.2024.1080p.DTS.6.1.mkv", "", "")[1])

    def test_title_words_and_embedded_substrings(self):
        for title in ("Mono", "Stereo", "Uncut", "Remastered", "IMAX", "NF", "SDR", "Extended"):
            _, details = format_details(f"{title}.2024.1080p.WEB-DL.mkv", "1080p", "")
            self.assertNotIn(title, details)
        for token in ("NotIMAX", "SDRagon", "NFiction", "AMZNextra", "Extendedness", "Monopoly", "كلمةIMAX", "SDRكلمة"):
            _, details = format_details(f"Movie.2024.1080p.{token}.mkv", "1080p", "")
            self.assertNotIn("✧", details)
            self.assertNotIn("SDR", details)
            self.assertNotIn("Mono", details)
            self.assertNotIn("AMZN", details)

    def test_metadata_before_resolution_and_episode_markers(self):
        for filename in ("Movie.2024.IMAX.SDR.2160p.mkv", "Show.S01E02.IMAX.SDR.2160p.mkv", "Movie.1080p.IMAX.SDR.mkv"):
            _, details = format_details(filename, "", "")
            self.assertIn("IMAX", details)
            self.assertIn("SDR", details)

    def test_no_invented_tags_and_no_duplicates(self):
        _, plain = format_details("Movie.2024.1080p.HEVC.10bit.mkv", "", "")
        self.assertNotIn("SDR", plain)
        self.assertNotIn("HDR", plain)
        _, details = format_details("Movie.2024.2160p.IMAX.Enhanced.IMAX.SDR.SDR.Dual.Audio.Dual.Audio.mkv", "", "")
        self.assertEqual(details.count("IMAX"), 1)
        self.assertEqual(details.count("SDR"), 1)
        self.assertEqual(details.count("Dual Audio"), 1)

    def test_existing_hdr_video_audio_support(self):
        filename = "Movie.2024.2160p.BluRay.REMUX.HEVC.10bit.DV.HDR10+.TrueHD.Atmos.7.1.mkv"
        name, details = format_details(filename, "2160p", "50 GB")
        self.assertEqual(name, "4K UHD")
        for expected in ("BluRay REMUX", "HEVC", "10-bit", "Dolby Vision", "HDR10+", "Atmos · TrueHD 7.1", "50 GB"):
            self.assertIn(expected, details)
        self.assertNotIn("HDR10 ·", details)

    def test_empty_input_and_parser_failure(self):
        self.assertEqual(format_details(None, None, None), ("HD", "⛁ Unknown size"))
        def broken(name):
            raise ValueError("bad name")
        formatter = load_formatter(PATH.read_text(encoding="utf-8"), types.SimpleNamespace(parse=broken))
        self.assertIn("IMAX", formatter("Movie.2024.1080p.IMAX.mkv", "", "")[1])

    def test_existing_release_regressions(self):
        labels = {"360p": "360P SD", "480p": "480p SD", "576p": "576P SD", "720p": "720p HD",
                  "1080p": "1080p FHD", "1440p": "2K Quad HD", "2160p": "4K UHD", "4320p": "8K Ultra HD"}
        technical = {
            "AVC.AAC.2.0": ("AVC", "♫ AAC 2.0"),
            "HEVC.10bit.DDP5.1": ("HEVC · 10-bit", "♫ DD+ 5.1"),
            "AV1.Opus.7.1": ("AV1", "♫ Opus 7.1"),
            "HEVC.DV.HDR10+.TrueHD.Atmos.7.1": ("HEVC", "⛶ Dolby Vision · HDR10+\n♫ Atmos · TrueHD 7.1"),
        }
        for resolution, name in labels.items():
            for source in ("WEB-DL", "WEBRip", "BluRay", "HDTV", "DVD", "CAM"):
                for tech, (video, audio) in technical.items():
                    filename = f"Movie.2024.{resolution}.{source}.{tech}.mkv"
                    with self.subTest(filename=filename):
                        expected = (name, f"✦ {source} · {video}\n{audio}\n⛁ 2 GB")
                        self.assertEqual(format_details(filename, resolution, "2 GB"), expected)

    def test_real_parser(self):
        import PTN
        global format_details
        original = format_details
        try:
            format_details = load_formatter(PATH.read_text(encoding="utf-8"), PTN)
            for case in (self.test_release_tags_and_separators, self.test_dtsx_aliases_without_duplicate_dts,
                         self.test_existing_release_regressions, self.test_title_words_and_embedded_substrings,
                         self.test_no_invented_tags_and_no_duplicates):
                case()
        finally:
            format_details = original

    def test_parser_fallback(self):
        parser = types.SimpleNamespace(parse=lambda name: {"resolution": "1080p", "codec": "x265", "audio": "eac3", "bitDepth": 10})
        formatter = load_formatter(PATH.read_text(encoding="utf-8"), parser)
        name, details = formatter("Movie.2024.mkv", "", "")
        self.assertEqual(name, "1080p FHD")
        self.assertIn("HEVC · 10-bit", details)
        self.assertIn("DD+", details)

if __name__ == "__main__":
    unittest.main()
