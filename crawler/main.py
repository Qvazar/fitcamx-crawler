from collections.abc import Iterator
from itertools import chain
import os
import signal
import sys
import threading

from . import debug
from .logging import getLogger
from .checksum import Crc32cPipe
from .network import get_current_ssid
from .source.fitcamx import fitcamx
from .destination import get_destination_from_url
from .videolocalstorage import videolocalstorage
from .videorecord import VideoStatus
from .videodatabase import VideoDatabase

logger = getLogger(__name__)

# --- CONFIGURATION ---
class Config:
    """Configuration object for the crawler."""
    
    def __init__(self):
        self.camera_ssid = os.environ.get("CAMERA_SSID", None)
        self.video_extended_marked_window = int(os.environ.get("VIDEO_EXTENDED_MARKED_WINDOW", 0))
        self.video_recording_window = int(os.environ.get("VIDEO_RECORDING_WINDOW", 2))
        self.target = os.environ.get("TARGET", "")
        self.heartbeat_interval = int(os.environ.get("HEARTBEAT_INTERVAL", 60))
        
        self._validate()
    
    def _validate(self):
        """Validate required configuration values."""
        if not self.camera_ssid:
            logger.error("CAMERA_SSID environment variable is not set. Exiting.")
            sys.exit(1)
        
        if not self.target:
            logger.warning("TARGET is not set; uploads will be skipped until it is configured.")
    
    def log_startup(self):
        """Log startup configuration."""
        logger.info("Starting crawler with configuration:\n" \
        "CAMERA_SSID=%s\n" \
        "VIDEO_EXTENDED_MARKED_WINDOW=%d\n" \
        "VIDEO_RECORDING_WINDOW=%d\n" \
        "TARGET=%s\n" \
        "HEARTBEAT_INTERVAL=%d",
        self.camera_ssid,
        self.video_extended_marked_window,
        self.video_recording_window,
        self.target,
        self.heartbeat_interval)

@debug.timed
def delete_videos_from_source(videodb: VideoDatabase, source):
    """Delete videos from the source that have been successfully uploaded."""
    deleted_count = 0
    try:
        for video in videodb.find_uploaded_videos():
            try:
                source.delete_video(video)
                video.status = VideoStatus.UPLOADED_AND_DELETED
                videodb.update_videos([video])
                deleted_count += 1
            except Exception as e:
                logger.error("Error deleting video %s from source: %s", video.filename, e)
    except Exception as e:
        logger.error("Exception when deleting videos from source: %s", e)

    logger.info("Total videos deleted from source: %d", deleted_count)


@debug.timed
def register_videos_from_source(videodb, source):
    video_count = 0
    try:
        video_count = videodb.insert_videos(source.find_videos())
    except Exception as e:
        logger.error("Exception when crawling videos: %s", e)

    logger.info("Total new videos registered from source: %d", video_count)

@debug.timed
def ignore_unmarked_videos(videodb, extended_marked_window=0):
    try:
        ignored_count = videodb.ignore_unmarked_videos(extended_marked_window)
        logger.info("Ignored %d unmarked videos outside the marked window.", ignored_count)
    except Exception as e:
        logger.error("Exception when ignoring unmarked videos: %s", e)


@debug.timed
def download_videos_from_source(videodb, source, video_recording_window=0):
    downloaded_count = 0
    try:
        for video in videodb.find_videos_to_download(video_recording_window):
            try:
                stream: Crc32cPipe = Crc32cPipe(source.download_video(video))
                videolocalstorage.store_video(video.filename, stream)

                video.status = VideoStatus.DOWNLOADED
                video.crc32c = stream.get_crc32c_base64()

                logger.debug("Downloaded video: %s with CRC32C: %s", video.filename, video.crc32c)

                videodb.update_videos([video])
                downloaded_count += 1

                logger.debug("Downloaded video: %s", video.filename)
            except FileNotFoundError as e:
                logger.warning("Video %s not found on source. Marking as lost.", video.filename)
                video.status = VideoStatus.LOST
                videodb.update_videos([video])
            except Exception as e:
                logger.error("Error downloading video %s: %s", video.filename, e)
    except Exception as e:
        logger.error("Exception when downloading videos: %s", e)

    logger.info("Total downloaded videos: %d", downloaded_count)


@debug.timed
def upload_to_destination(videodb:VideoDatabase, destination):
    try:
        videos = videodb.find_downloaded_videos()
        first_video = next(videos, None)

        if first_video is None:
            logger.debug("No downloaded videos to upload.")
            return

        logger.debug("Uploading downloaded videos to destination.")
        with destination: 
            for v in chain([first_video], videos):
                try:
                    local_path = videolocalstorage.get_video_path(v.filename)
                    
                    logger.debug("Uploading %s...", v.filename)
                    destination.put(local_path, v)

                    videolocalstorage.delete_video(v.filename)

                    v.status = VideoStatus.UPLOADED
                    videodb.update_videos([v])

                    logger.info("Successfully uploaded %s and removed it from local storage.", v.filename)
                except FileNotFoundError:
                    logger.warning("File %s is missing from local storage. Resetting status to 'found'.", v.filename)
                    v.status = VideoStatus.FOUND
                    videodb.update_videos([v])
                except Exception as e:
                    logger.error("Error uploading video %s: %s", v.filename, e)
    except Exception as e:
        logger.error("Error during upload: %s", e)


def _install_shutdown_handler() -> threading.Event:
    shutdown_event = threading.Event()
    def handler(signum, _frame):
        logger.info("Received signal %d. Shutting down gracefully...", signum)
        shutdown_event.set()
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    return shutdown_event


def main():
    config = Config()
    config.log_startup()

    shutdown_event = _install_shutdown_handler()

    source = fitcamx
    destination = get_destination_from_url(config.target) if config.target else None

    ssid = None

    with VideoDatabase() as videodb:
        while not shutdown_event.is_set():
            try:
                new_ssid = get_current_ssid()
                if ssid != new_ssid:
                    logger.info("WiFi SSID changed from '%s' to '%s'", ssid, new_ssid)
                    ssid = new_ssid
                
                if ssid is None:
                    logger.debug("No WiFi connection. Waiting...")
                elif ssid == config.camera_ssid: # Connected to the camera's WiFi network
                    with videodb.checkpoint():
                        delete_videos_from_source(videodb, source)
                        register_videos_from_source(videodb, source)
                        ignore_unmarked_videos(videodb, config.video_extended_marked_window)
                        download_videos_from_source(videodb, source, config.video_recording_window)
                else: # Connected to a different WiFi network
                    if destination:
                        with videodb.checkpoint():
                            upload_to_destination(videodb, destination)
                        
            except Exception as e:
                logger.exception("Unexpected error: %s", e)

            # Idle sleep interval to avoid unnecessary CPU/battery consumption and network usage.
            shutdown_event.wait(timeout=config.heartbeat_interval)

    logger.info("Crawler stopped gracefully.")


if __name__ == "__main__":
    main()
