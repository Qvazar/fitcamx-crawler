"""Tests for crawler/source/fitcamx.py"""
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from crawler.source.fitcamx import _datetime_from_filename, _crawl_url, _FitcamXSource
from crawler.videorecord import VideoStatus

# ---------------------------------------------------------------------------
# HTML fixtures mimicking real FitCamX camera responses
# ---------------------------------------------------------------------------

ROOT_HTML = Path(__file__).with_name("fitcamx_root.html").read_text(encoding="utf-8")

CARDV_HTML = Path(__file__).with_name("fitcamx_CARDV.html").read_text(encoding="utf-8")

MOVIE_HTML = Path(__file__).with_name("fitcamx_CARDV_Movie.html").read_text(encoding="utf-8")

EMR_HTML = Path(__file__).with_name("fitcamx_CARDV_EMR.html").read_text(encoding="utf-8")


def _make_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# _datetime_from_filename
# ---------------------------------------------------------------------------

class TestDatetimeFromFilename:
    def test_standard_filename(self):
        dt = _datetime_from_filename("20260709112750_036576A.TS")
        assert dt == datetime(2026, 7, 9, 11, 27, 50)

    def test_midnight(self):
        dt = _datetime_from_filename("20260101000000_000000A.TS")
        assert dt == datetime(2026, 1, 1, 0, 0, 0)

    def test_end_of_day(self):
        dt = _datetime_from_filename("20261231235959_999999A.TS")
        assert dt == datetime(2026, 12, 31, 23, 59, 59)

    def test_invalid_filename_raises(self):
        with pytest.raises(ValueError):
            _datetime_from_filename("not_a_date.TS")


# ---------------------------------------------------------------------------
# _crawl_url
# ---------------------------------------------------------------------------

class TestCrawlUrl:
    def _url_to_html(self, url: str, **_kwargs) -> MagicMock:
        mapping = {
            "http://192.168.1.254": ROOT_HTML,
            "http://192.168.1.254/CARDV": CARDV_HTML,
            "http://192.168.1.254/CARDV/Movie": MOVIE_HTML,
            "http://192.168.1.254/CARDV/EMR": EMR_HTML,
        }
        html = mapping.get(url, "<html><body></body></html>")
        return _make_response(html)

    @patch("crawler.source.fitcamx.requests.get")
    def test_discovers_videos_recursively(self, mock_get):
        mock_get.side_effect = self._url_to_html
        videos = _crawl_url("http://192.168.1.254")
        filenames = [v.filename for v in videos]
        assert "20260731145927_040802A.TS" in filenames
        assert "20260731165628_040852A.TS" in filenames
        assert "20260626125701_034397A.TS" in filenames

    @patch("crawler.source.fitcamx.requests.get")
    def test_video_status_is_found(self, mock_get):
        mock_get.side_effect = self._url_to_html
        videos = list(_crawl_url("http://192.168.1.254"))
        assert all(v.status == VideoStatus.FOUND for v in videos)

    @patch("crawler.source.fitcamx.requests.get")
    def test_marked_videos_detected(self, mock_get):
        mock_get.side_effect = self._url_to_html
        videos = list(_crawl_url("http://192.168.1.254"))
        emr_video = next(v for v in videos if v.filename == "20260626125701_034397A.TS")
        movie_video = next(v for v in videos if v.filename == "20260731145927_040802A.TS")
        assert emr_video.marked is True
        assert movie_video.marked is False

    @patch("crawler.source.fitcamx.requests.get")
    def test_camera_path_set_correctly(self, mock_get):
        mock_get.side_effect = self._url_to_html
        videos = list(_crawl_url("http://192.168.1.254"))
        emr_video = next(v for v in videos if v.filename == "20260626125701_034397A.TS")
        assert emr_video.camera_path == "/CARDV/EMR/20260626125701_034397A.TS"

    @patch("crawler.source.fitcamx.requests.get")
    def test_recorded_at_parsed_from_filename(self, mock_get):
        mock_get.side_effect = self._url_to_html
        videos = list(_crawl_url("http://192.168.1.254"))
        movie_video = next(v for v in videos if v.filename == "20260731145927_040802A.TS")
        assert movie_video.recorded_at == datetime(2026, 7, 31, 14, 59, 27)

    @patch("crawler.source.fitcamx.requests.get")
    def test_delete_links_ignored(self, mock_get):
        """Links with ?del=1 query parameters must not appear as videos."""
        mock_get.side_effect = self._url_to_html
        videos = list(_crawl_url("http://192.168.1.254"))
        assert all("?del=1" not in v.camera_path for v in videos)

    @patch("crawler.source.fitcamx.requests.get")
    def test_empty_directory(self, mock_get):
        mock_get.return_value = _make_response("<html><body></body></html>")
        videos = list(_crawl_url("http://192.168.1.254"))
        assert videos == []

    @patch("crawler.source.fitcamx.requests.get")
    def test_http_error_propagates(self, mock_get):
        mock_get.return_value.raise_for_status.side_effect = Exception("404")
        with pytest.raises(Exception, match="404"):
            list(_crawl_url("http://192.168.1.254"))


# ---------------------------------------------------------------------------
# _FitcamXSource.find_videos and download_video
# ---------------------------------------------------------------------------

class TestFitcamXSource:
    @patch("crawler.source.fitcamx.requests.get")
    @patch("crawler.source.fitcamx.get_network_gateway", return_value="192.168.1.254")
    def test_find_videos_uses_gateway(self, mock_gw, mock_get):
        mock_get.return_value = _make_response(MOVIE_HTML)
        source = _FitcamXSource()
        videos = list(source.find_videos())
        mock_gw.assert_called_once()

    @patch("crawler.source.fitcamx.get_network_gateway", return_value=None)
    def test_find_videos_no_gateway_raises(self, mock_gw):
        source = _FitcamXSource()
        with pytest.raises(RuntimeError):
            list(source.find_videos())

    @patch("crawler.source.fitcamx.requests.get")
    @patch("crawler.source.fitcamx.get_network_gateway", return_value="192.168.1.254")
    def test_download_video_yields_chunks(self, mock_gw, mock_get):
        chunks = [b"chunk1", b"chunk2", b"chunk3"]
        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.raise_for_status.return_value = None
        mock_response.iter_content.return_value = iter(chunks)
        mock_get.return_value = mock_response

        from crawler.videorecord import VideoRecord
        video = VideoRecord(
            filename="20260731165428_040848A.TS",
            camera_path="/CARDV/Movie/20260731165428_040848A.TS",
            status=VideoStatus.FOUND,
            recorded_at=datetime(2026, 7, 31, 16, 54, 28),
        )
        source = _FitcamXSource()
        result = list(source.download_video(video))
        assert result == chunks
        mock_get.assert_called_once_with(
            "http://192.168.1.254/CARDV/Movie/20260731165428_040848A.TS",
            stream=True,
            timeout=60,
        )
