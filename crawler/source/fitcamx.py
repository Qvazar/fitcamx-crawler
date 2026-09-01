from collections.abc import Iterable
from datetime import datetime
import os
from typing import Iterator
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
import requests
from ..debug import timed
from ..logging import getLogger
from ..network import get_network_gateway
from ..videorecord import VideoRecord, VideoStatus

logger = getLogger(__name__)

FITCAMX_MARKED_VIDEO_DIRS = tuple(os.environ.get("FITCAMX_MARKED_VIDEO_DIRS", "CARDV/EMR/,CARDV/EMR_E/").split(","))  # Directories for marked videos (if applicable)
VIDEO_EXTENSIONS = tuple(os.environ.get("VIDEO_EXTENSIONS", ".TS").split(","))  # Comma-separated list of video file extensions to consider

logger.debug(f"FITCAMX_MARKED_VIDEO_DIRS: {FITCAMX_MARKED_VIDEO_DIRS}")
logger.debug(f"VIDEO_EXTENSIONS: {VIDEO_EXTENSIONS}")


def _log_crawl_url_response_to_file(url: str, response: requests.Response):
    """Log the response of a crawl URL to a file for debugging purposes."""
    if logger.getChild("RESPONSE").isDebugEnabled():
        try:
            log_dir = os.path.join(os.getcwd(), "fitcamx_logs")
            os.makedirs(log_dir, exist_ok=True)

            url_path = urlsplit(url).path.replace('/', '_').strip('_') or 'root'
            log_file_path = os.path.join(log_dir, f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{url_path}.log")
            
            with open(log_file_path, "w", encoding="utf-8") as log_file:
                log_file.write(f"URL: {url}\n")
                log_file.write(f"Status Code: {response.status_code}\n")
                log_file.write("Response Content:\n")
                log_file.write(response.text)
            
            logger.debug(f"fitcamx response logged to {log_file_path}")
        except Exception as e:
            logger.error(f"Failed to log fitcamx response to file: {e}")


def _datetime_from_filename(filename) -> datetime:
    """Extracts the recorded timestamp from the video filename, if possible."""
    # Example filename: "20260709112750_036576A.TS" -> recorded_at = "2026-07-09 11:27:50"
    return datetime.strptime(filename[:14], "%Y%m%d%H%M%S")

def _get_camera_url() -> str:
    camera_ip = get_network_gateway()
    if camera_ip:
        camera_url = f"http://{camera_ip}"
        logger.debug(f"Camera URL determined as: {camera_url}")
        return camera_url
    else:
        raise RuntimeError("Could not determine camera address from network gateway")


def _crawl_url(url: str) -> Iterable[VideoRecord]:
    """Crawls a given URL and yields found videos."""
    logger.debug("Entered _crawl_url()")

    logger.info(f"Crawling URL: {url}")

    response = requests.get(url, timeout=10)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')
    links = soup.find_all('a')

    logger.debug(f"Received response from {url} with status code {response.status_code} and found {len(links)} links.")
    _log_crawl_url_response_to_file(url, response)
    
    for link in links:
        href = link.get('href')
        if href:
            found_url = urljoin(url, href)

            if href.strip().endswith(VIDEO_EXTENSIONS):
                video_path = urlsplit(found_url).path
                filename = os.path.basename(video_path)
                video_recorded_at: datetime = _datetime_from_filename(filename)
                marked = video_path.lstrip("/").startswith(FITCAMX_MARKED_VIDEO_DIRS)

                logger.debug(f"Found video: {video_path}")

                yield VideoRecord(filename, video_path, VideoStatus.FOUND, video_recorded_at, marked)
            elif href.find(".") == -1:  # Likely a directory (no file extension)
                # Recursively crawl subdirectories
                logger.debug(f"Found directory: {found_url}, recursing into it.")
                yield from _crawl_url(found_url)
            else:
                logger.debug(f"Ignoring link: {href} (not a video or directory)")

    logger.debug("Exiting _crawl_url()")


class _FitcamXSource:
    def __init__(self):
        pass

    @timed
    def find_videos(self):
        camera_url = _get_camera_url()
        return _crawl_url(camera_url)

    @timed
    def download_video(self, video: VideoRecord) -> Iterator[bytes]:
        """Download a video from the camera and yield its content in chunks."""
        camera_url = _get_camera_url()
        video_url = urljoin(camera_url, video.camera_path)
        with requests.get(video_url, stream=True, timeout=60) as video_stream:
            if video_stream.status_code == 404:
                raise FileNotFoundError(f"Video {video.filename} not found at {video_url}")
            
            video_stream.raise_for_status()
            yield from video_stream.iter_content(chunk_size=512*1024)  # Yield the video stream in chunks for the video

    @timed
    def delete_video(self, video: VideoRecord):
        """Delete a video from the camera."""
        camera_url = _get_camera_url()
        video_url = urljoin(camera_url, video.camera_path)
        #response = requests.delete(video_url, timeout=10)
        response = requests.get(f"{video_url}?del=1", timeout=10)  # Using GET for deletion as per FitcamX behavior

        logger.debug("Attempted to delete video %s from camera at %s, received status code: %d, response text: %s", video.filename, video_url, response.status_code, response.text)

        if response.status_code == 404:
            logger.warning(f"Video {video.filename} not found at {video_url} for deletion.")
        else:
            response.raise_for_status()

fitcamx = _FitcamXSource()
