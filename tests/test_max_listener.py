"""Tests for app/max_listener.py — pure helper functions."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.max_listener import _human_size, _guess_media_kind, _send_attach


# ---------------------------------------------------------------------------
# _human_size
# ---------------------------------------------------------------------------

class TestHumanSize:
    """Tests for the _human_size byte-formatter."""

    # Byte range (< 1024)
    def test_zero_bytes(self):
        assert _human_size(0) == "0 Б"

    def test_single_byte(self):
        assert _human_size(1) == "1 Б"

    def test_max_bytes(self):
        assert _human_size(1023) == "1023 Б"

    # Kilobyte range (1024 – 1024²-1)
    def test_exact_one_kb(self):
        assert _human_size(1024) == "1.0 КБ"

    def test_fractional_kb(self):
        assert _human_size(1536) == "1.5 КБ"

    def test_large_kb(self):
        assert _human_size(1023 * 1024) == "1023.0 КБ"

    # Megabyte range
    def test_exact_one_mb(self):
        assert _human_size(1024 ** 2) == "1.0 МБ"

    def test_fractional_mb(self):
        assert _human_size(int(2.5 * 1024 ** 2)) == "2.5 МБ"

    def test_large_mb(self):
        assert _human_size(500 * 1024 ** 2) == "500.0 МБ"

    # Gigabyte range
    def test_exact_one_gb(self):
        assert _human_size(1024 ** 3) == "1.0 ГБ"

    def test_fractional_gb(self):
        assert _human_size(int(1.5 * 1024 ** 3)) == "1.5 ГБ"

    # Terabyte range (overflow past ГБ loop)
    def test_terabyte(self):
        result = _human_size(1024 ** 4)
        assert "ТБ" in result

    def test_large_terabyte(self):
        result = _human_size(5 * 1024 ** 4)
        assert result.startswith("5")
        assert "ТБ" in result

    # Return type
    def test_returns_string(self):
        assert isinstance(_human_size(42), str)


# ---------------------------------------------------------------------------
# _guess_media_kind
# ---------------------------------------------------------------------------

class TestGuessMediaKind:
    """Tests for the filename-to-media-kind classifier."""

    # Photo extensions
    def test_jpg_is_photo(self):
        assert _guess_media_kind("image.jpg") == "photo"

    def test_jpeg_is_photo(self):
        assert _guess_media_kind("photo.jpeg") == "photo"

    def test_png_is_photo(self):
        assert _guess_media_kind("screenshot.png") == "photo"

    def test_gif_is_photo(self):
        assert _guess_media_kind("anim.gif") == "photo"

    def test_webp_is_photo(self):
        assert _guess_media_kind("sticker.webp") == "photo"

    def test_bmp_is_photo(self):
        assert _guess_media_kind("old.bmp") == "photo"

    # Video extensions
    def test_mp4_is_video(self):
        assert _guess_media_kind("clip.mp4") == "video"

    def test_mov_is_video(self):
        assert _guess_media_kind("recording.mov") == "video"

    def test_avi_is_video(self):
        assert _guess_media_kind("video.avi") == "video"

    def test_mkv_is_video(self):
        assert _guess_media_kind("movie.mkv") == "video"

    def test_webm_is_video(self):
        assert _guess_media_kind("stream.webm") == "video"

    # Document / unknown extensions
    def test_pdf_is_document(self):
        assert _guess_media_kind("report.pdf") == "document"

    def test_zip_is_document(self):
        assert _guess_media_kind("archive.zip") == "document"

    def test_docx_is_document(self):
        assert _guess_media_kind("contract.docx") == "document"

    def test_txt_is_document(self):
        assert _guess_media_kind("notes.txt") == "document"

    def test_no_extension_is_document(self):
        assert _guess_media_kind("README") == "document"

    def test_empty_string_is_document(self):
        assert _guess_media_kind("") == "document"

    # Case-insensitivity
    def test_uppercase_jpg_is_photo(self):
        assert _guess_media_kind("PHOTO.JPG") == "photo"

    def test_mixed_case_mp4_is_video(self):
        assert _guess_media_kind("Video.MP4") == "video"

    def test_mixed_case_png_is_photo(self):
        assert _guess_media_kind("Image.PNG") == "photo"

    # Paths with directories
    def test_full_path_jpg(self):
        assert _guess_media_kind("/tmp/uploads/img.jpg") == "photo"

    def test_full_path_mp4(self):
        assert _guess_media_kind("/home/user/videos/clip.mp4") == "video"

    # Extension appearing in the middle of filename should not trigger false match
    def test_mp4_in_name_not_extension_is_document(self):
        assert _guess_media_kind("mp4_notes.txt") == "document"


# ---------------------------------------------------------------------------
# _send_attach — POLL handling
# ---------------------------------------------------------------------------

class TestSendAttachPoll:
    """Tests for the POLL branch of _send_attach."""

    def _make_sender(self):
        sender = MagicMock()
        sender.send_poll = AsyncMock()
        sender.send = AsyncMock()
        return sender

    @pytest.mark.asyncio
    async def test_poll_with_enough_options_is_forwarded_as_poll(self):
        sender = self._make_sender()
        attach = {
            "_type": "POLL",
            "title": "Любимый цвет?",
            "answers": [{"text": "Красный"}, {"text": "Синий"}],
        }

        result = await _send_attach(attach, client=MagicMock(), sender=sender, header_text="hdr", chat_id=1, message_id=1)

        assert result is True
        sender.send_poll.assert_awaited_once()
        sender.send.assert_not_called()
        _, options = sender.send_poll.await_args.args[:2]
        assert options == ["Красный", "Синий"]

    @pytest.mark.asyncio
    async def test_poll_with_less_than_two_options_falls_back_to_text(self):
        sender = self._make_sender()
        attach = {"_type": "POLL", "title": "Опрос", "answers": [{"text": "Единственный"}]}

        result = await _send_attach(attach, client=MagicMock(), sender=sender, header_text="hdr", chat_id=1, message_id=1)

        assert result is True
        sender.send_poll.assert_not_called()
        sender.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_poll_with_no_answers_key_falls_back_to_text(self):
        sender = self._make_sender()
        attach = {"_type": "POLL", "title": "Опрос"}

        result = await _send_attach(attach, client=MagicMock(), sender=sender, header_text="hdr", chat_id=1, message_id=1)

        assert result is True
        sender.send_poll.assert_not_called()
        sender.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_poll_ignores_answers_without_text(self):
        sender = self._make_sender()
        attach = {
            "_type": "POLL",
            "title": "Опрос",
            "answers": [{"text": "Да"}, {}, {"text": "Нет"}, {"text": ""}],
        }

        await _send_attach(attach, client=MagicMock(), sender=sender, header_text="hdr", chat_id=1, message_id=1)

        _, options = sender.send_poll.await_args.args[:2]
        assert options == ["Да", "Нет"]


# ---------------------------------------------------------------------------
# _send_attach — VIDEO handling
# ---------------------------------------------------------------------------

class TestSendAttachVideo:
    """Tests for the VIDEO branch of _send_attach."""

    def _make_client(self, cmd_response=None):
        client = MagicMock()
        client.cmd = AsyncMock(return_value=cmd_response or {})
        client.download_file = AsyncMock(return_value=b"video-bytes")
        return client

    def _make_sender(self, send_video_ok=True):
        sender = MagicMock()
        sender.send_video = AsyncMock(return_value=send_video_ok)
        sender.send_photo = AsyncMock()
        sender.send = AsyncMock()
        return sender

    @pytest.mark.asyncio
    async def test_video_downloaded_and_sent_successfully(self):
        client = self._make_client({"MP4_720": "https://ok.ru/video720.mp4"})
        sender = self._make_sender(send_video_ok=True)
        attach = {"_type": "VIDEO", "videoId": "vid1", "token": "tok"}

        result = await _send_attach(attach, client=client, sender=sender, header_text="hdr", chat_id=1, message_id=2)

        assert result is True
        sender.send_video.assert_awaited_once()
        sender.send_photo.assert_not_called()
        sender.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_download_uses_omit_origin(self):
        client = self._make_client({"MP4_720": "https://ok.ru/video720.mp4"})
        sender = self._make_sender()
        attach = {"_type": "VIDEO", "videoId": "vid1"}

        await _send_attach(attach, client=client, sender=sender, header_text="hdr", chat_id=1, message_id=2)

        client.download_file.assert_awaited_once_with("https://ok.ru/video720.mp4", omit_origin=True)

    @pytest.mark.asyncio
    async def test_prefers_highest_available_resolution(self):
        client = self._make_client({
            "MP4_144": "https://ok.ru/144.mp4",
            "MP4_1080": "https://ok.ru/1080.mp4",
            "MP4_480": "https://ok.ru/480.mp4",
        })
        sender = self._make_sender()
        attach = {"_type": "VIDEO", "videoId": "vid1"}

        await _send_attach(attach, client=client, sender=sender, header_text="hdr", chat_id=1, message_id=2)

        client.download_file.assert_awaited_once_with("https://ok.ru/1080.mp4", omit_origin=True)

    @pytest.mark.asyncio
    async def test_falls_back_to_thumbnail_when_telegram_upload_fails(self):
        client = self._make_client({"MP4_720": "https://ok.ru/video720.mp4"})
        sender = self._make_sender(send_video_ok=False)
        attach = {"_type": "VIDEO", "videoId": "vid1", "thumbnail": "https://max.ru/thumb.jpg"}

        result = await _send_attach(attach, client=client, sender=sender, header_text="hdr", chat_id=1, message_id=2)

        assert result is True
        sender.send_video.assert_awaited_once()
        sender.send_photo.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_thumbnail_when_download_fails(self):
        client = self._make_client({"MP4_720": "https://ok.ru/video720.mp4"})

        async def download_file(url, omit_origin=False):
            return None if omit_origin else b"thumb-bytes"

        client.download_file = AsyncMock(side_effect=download_file)
        sender = self._make_sender()
        attach = {"_type": "VIDEO", "videoId": "vid1", "thumbnail": "https://max.ru/thumb.jpg"}

        result = await _send_attach(attach, client=client, sender=sender, header_text="hdr", chat_id=1, message_id=2)

        assert result is True
        sender.send_video.assert_not_called()
        sender.send_photo.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_thumbnail_when_no_url_in_response(self):
        client = self._make_client({})
        sender = self._make_sender()
        attach = {"_type": "VIDEO", "videoId": "vid1", "thumbnail": "https://max.ru/thumb.jpg"}

        result = await _send_attach(attach, client=client, sender=sender, header_text="hdr", chat_id=1, message_id=2)

        assert result is True
        sender.send_video.assert_not_called()
        sender.send_photo.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_video_url_lookup_without_video_id(self):
        client = self._make_client({"MP4_720": "https://ok.ru/video720.mp4"})
        sender = self._make_sender()
        attach = {"_type": "VIDEO", "thumbnail": "https://max.ru/thumb.jpg"}

        result = await _send_attach(attach, client=client, sender=sender, header_text="hdr", chat_id=1, message_id=2)

        assert result is True
        client.cmd.assert_not_called()
        sender.send_photo.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_thumbnail_and_no_video_falls_back_to_text(self):
        client = self._make_client({})
        client.download_file = AsyncMock(return_value=None)
        sender = self._make_sender()
        attach = {"_type": "VIDEO", "videoId": "vid1"}

        result = await _send_attach(attach, client=client, sender=sender, header_text="hdr", chat_id=1, message_id=2)

        assert result is True
        sender.send_video.assert_not_called()
        sender.send_photo.assert_not_called()
        sender.send.assert_awaited_once()
