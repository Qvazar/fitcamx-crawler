from collections.abc import Iterable
import os
import shutil

from . import debug


TEMP_FILENAME = ".~downloading_video.TS"
FREE_SPACE_MINIMUM_BYTES = 500 * 1024 * 1024  # Minimum free space required in bytes (500 MB)

def check_free_space(path:str, minimum_bytes:int = FREE_SPACE_MINIMUM_BYTES):
    """Check if there is enough free space in the given path."""
    total, used, free = shutil.disk_usage(path)
    if free < minimum_bytes:
        raise OSError(f"Insufficient disk space. Available: {free} bytes, Required: {minimum_bytes} bytes.")


class _VideoFileStorage:
    def __init__(self, dirname: str = "./videos"):
        self.dirname = dirname

    @debug.timed
    def store_video(self, video_name:str, data:Iterable[bytes]):
        os.makedirs(self.dirname, exist_ok=True)

        check_free_space(self.dirname)  # Ensure there is enough free space before storing the video

        final_path = os.path.join(self.dirname, video_name)
        temp_path = os.path.join(self.dirname, TEMP_FILENAME)

        with open(temp_path, 'wb') as f:
            for chunk in data:
                f.write(chunk)
            f.flush()
        os.replace(temp_path, final_path)  # Rename temp file to final filename after successful download
        return os.path.abspath(final_path)

    def get_video_path(self, video_name:str):
        path = os.path.join(self.dirname, video_name)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Video file {video_name} not found in local storage.")
        return os.path.abspath(path)

    def delete_video(self, video_name:str):
        path = os.path.join(self.dirname, video_name)
        if os.path.exists(path):
            os.remove(path)

videolocalstorage = _VideoFileStorage()
