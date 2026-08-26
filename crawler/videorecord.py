from datetime import datetime
from enum import Enum

class VideoStatus(Enum):
    LOST = "lost"
    FOUND = "found"
    DOWNLOADED = "downloaded"
    IGNORED = "ignored"
    UPLOADED = "uploaded"
    UPLOADED_AND_DELETED = "uploaded & deleted"


class VideoRecord:
    """Represents a video file with its metadata."""
    
    def __init__(self, filename:str, camera_path:str, status:str, recorded_at:datetime, marked:bool = False, crc32c:str | None = None, registered_at:datetime | None = None):
        self.filename = filename
        self.camera_path = camera_path
        self.status = VideoStatus(status)
        self.recorded_at = datetime.fromisoformat(recorded_at) if not isinstance(recorded_at, datetime) else recorded_at
        self.marked = marked
        self.crc32c = crc32c
        self.registered_at = datetime.fromisoformat(registered_at) if registered_at and not isinstance(registered_at, datetime) else registered_at
