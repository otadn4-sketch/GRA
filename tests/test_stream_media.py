from __future__ import annotations

import unittest

from app.bale import bale_file_download_url, bale_file_path
from app.db import extract_message_media_assets, resolve_media_mime


class StreamMediaTests(unittest.TestCase):
    def test_file_path_accepts_camel_case(self) -> None:
        self.assertEqual(bale_file_path({"filePath": "photos/file_1.jpg"}), "photos/file_1.jpg")
        self.assertEqual(bale_file_path({"file": {"file_path": "videos/clip.mp4"}}), "videos/clip.mp4")

    def test_download_url_encodes_and_keeps_absolute_links(self) -> None:
        token = "123:abc"
        self.assertEqual(
            bale_file_download_url("https://tapi.bale.ai", token, "photos/file 1.jpg"),
            "https://tapi.bale.ai/file/bot123:abc/photos/file%201.jpg",
        )
        self.assertEqual(
            bale_file_download_url(
                "https://tapi.bale.ai",
                token,
                "https://cdn.bale.ai/file/bot123:abc/photos/a.jpg",
            ),
            "https://cdn.bale.ai/file/bot123:abc/photos/a.jpg",
        )
        self.assertEqual(
            bale_file_download_url("https://tapi.bale.ai", token, "file/bot123:abc/photos/a.jpg"),
            "https://tapi.bale.ai/file/bot123:abc/photos/a.jpg",
        )

    def test_extract_camel_case_photo_and_video(self) -> None:
        assets = extract_message_media_assets(
            [
                {"type": "photo", "items": [{"fileId": "photo-1", "fileSize": 80, "width": 100, "height": 80}]},
                {"type": "video", "item": {"fileId": "video-1", "mimeType": "video/mp4", "file_name": "clip.mp4"}},
            ]
        )
        self.assertEqual([item["play"] for item in assets], ["image", "video"])
        self.assertEqual(assets[0]["file_id"], "photo-1")
        self.assertEqual(assets[1]["file_id"], "video-1")

    def test_mime_ignores_octet_stream_for_playable_media(self) -> None:
        self.assertEqual(
            resolve_media_mime(
                {"play": "image", "kind": "photo"},
                upstream_mime="application/octet-stream",
            ),
            "image/jpeg",
        )
        self.assertEqual(
            resolve_media_mime(
                {"play": "video", "kind": "video", "file_name": "news.mp4"},
                upstream_mime="binary/octet-stream",
            ),
            "video/mp4",
        )


if __name__ == "__main__":
    unittest.main()
