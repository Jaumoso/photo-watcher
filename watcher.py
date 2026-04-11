import os
import shutil
import time
import logging
import hashlib
import threading
from fnmatch import fnmatchcase
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from PIL import Image, ExifTags

try:
    from hachoir.parser import createParser
    from hachoir.metadata import extractMetadata
    HACHOIR_AVAILABLE = True
except ImportError:
    HACHOIR_AVAILABLE = False

# Configurar logger
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

SOURCE_DIRS = os.getenv("SOURCE_DIRS", "./source").split(",")
SOURCE_DIRS = [s.strip() for s in SOURCE_DIRS if s.strip()]
TARGET_BASE = os.getenv("TARGET_BASE", "./target")
IGNORE_FILES = os.getenv("IGNORE_FILES", "").split(",")
IGNORE_FILES = [p.strip() for p in IGNORE_FILES if p.strip()]
REQUIRE_DATE_DIRS = os.getenv("REQUIRE_DATE_DIRS", "").split(",")
REQUIRE_DATE_DIRS = [d.strip() for d in REQUIRE_DATE_DIRS if d.strip()]
FILE_STABILITY_DELAY = float(os.getenv("FILE_STABILITY_DELAY", "0.2"))

# In-memory index: {filename: [target_paths]}
_target_index = {}
_target_index_lock = threading.Lock()

# Debounce state: {file_path: Timer}
_debounce_timers = {}
_debounce_lock = threading.Lock()
DEBOUNCE_DELAY = float(os.getenv("DEBOUNCE_DELAY", "2.0"))

MONTHS = [
    "1. Enero", "2. Febrero", "3. Marzo", "4. Abril",
    "5. Mayo", "6. Junio", "7. Julio", "8. Agosto",
    "9. Septiembre", "10. Octubre", "11. Noviembre", "12. Diciembre"
]

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif")
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS + (
    ".mp4", ".mov", ".avi", ".mkv", ".3gp", ".webm", ".mpeg", ".mpg", ".m4v"
)


def get_file_date(file_path):
    """Returns the EXIF date for images, otherwise None."""
    if not file_path.lower().endswith(IMAGE_EXTENSIONS):
        return None

    try:
        with Image.open(file_path) as img:
            exif_data = img.getexif()
            if not exif_data:
                return None

            for tag_id, value in exif_data.items():
                tag = ExifTags.TAGS.get(tag_id, tag_id)
                if tag in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
                    if isinstance(value, bytes):
                        try:
                            value = value.decode(errors="ignore")
                        except Exception:
                            continue
                    try:
                        return datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
                    except Exception:
                        continue
    except Exception as e:
        logger.debug(f"[ℹ️] EXIF read issue for {file_path}: {e}")
        return None

    return None


def get_video_metadata_date(file_path):
    """Extract creation date from video metadata using hachoir."""
    if not HACHOIR_AVAILABLE:
        return None
    
    try:
        parser = createParser(file_path)
        if not parser:
            return None
        
        metadata = extractMetadata(parser)
        if not metadata:
            return None
        
        # Try to find creation date in metadata text representation
        for line in metadata.exportPlaintext():
            line_lower = str(line).lower()
            if any(term in line_lower for term in ["creation", "date", "captured", "recorded", "creation time"]):
                try:
                    # Extract date from line (format varies)
                    date_str = str(line).split(":")[-1].strip()
                    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"]:
                        try:
                            return datetime.strptime(date_str, fmt)
                        except ValueError:
                            continue
                except Exception:
                    continue
        
        return None
    except Exception as e:
        logger.debug(f"[ℹ️] Could not extract video metadata from {file_path}: {e}")
        return None


def is_require_date_dir(file_path):
    """Check if file_path belongs to a directory that requires embedded date metadata."""
    abs_path = os.path.abspath(file_path)
    for d in REQUIRE_DATE_DIRS:
        abs_dir = os.path.abspath(d)
        if abs_path.startswith(abs_dir + os.sep) or abs_path.startswith(abs_dir + "/"):
            return True
    return False


def get_media_date(file_path):
    """Returns the date used for target placement: EXIF for images, video metadata, or file mtime."""
    # Try EXIF for images first
    date = get_file_date(file_path)
    if date:
        return date
    
    # Try video metadata if it's a video
    if file_path.lower().endswith((".mp4", ".mov", ".avi", ".mkv", ".3gp", ".webm", ".mpeg", ".mpg", ".m4v")):
        video_date = get_video_metadata_date(file_path)
        if video_date:
            logger.info(f"[ℹ️] Video creation date: {file_path} → {video_date}")
            return video_date

    # Skip mtime fallback for dirs that require embedded date metadata
    if is_require_date_dir(file_path):
        logger.info(f"[⏳] No embedded date for {file_path} (require_date_dir), skipping mtime fallback")
        return None

    # Fall back to file modification time
    try:
        mtime = os.path.getmtime(file_path)
        fallback = datetime.fromtimestamp(mtime)
        logger.info(f"[ℹ️] Fallback to file mtime for {file_path}: {fallback}")
        return fallback
    except Exception as e:
        logger.error(f"[❌] Error reading modification date from {file_path}: {e}")
        return None


def is_supported_media(file_path):
    return file_path.lower().endswith(MEDIA_EXTENSIONS)


def get_file_hash(file_path):
    """Calculate SHA256 hash of a file."""
    hash_sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()
    except Exception as e:
        logger.debug(f"[ℹ️] Error calculating hash for {file_path}: {e}")
        return None



def retry_operation(func, *args, max_retries=3, base_delay=1, **kwargs):
    """Retry an operation with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            logger.warning(f"[🔄] Retry {attempt + 1}/{max_retries} for {func.__name__} after {delay}s: {e}")
            time.sleep(delay)


def safe_copy(src, dst):
    """Copy with overwrite protection, clear error reporting, and retry."""
    try:
        retry_operation(shutil.copy2, src, dst)
        logger.info(f"[✅] Copied/updated: {dst}")
    except Exception as e:
        logger.error(f"[❌] Error copying {src} → {dst}: {e}")


def is_file_stable(file_path):
    """Avoid processing files still being written by Syncthing."""
    try:
        size1 = os.path.getsize(file_path)
        time.sleep(FILE_STABILITY_DELAY)
        size2 = os.path.getsize(file_path)
        return size1 == size2
    except Exception:
        return False


def _index_add(file_name, target_path):
    """Add a file to the in-memory target index."""
    with _target_index_lock:
        if file_name not in _target_index:
            _target_index[file_name] = []
        if target_path not in _target_index[file_name]:
            _target_index[file_name].append(target_path)


def _index_remove(file_name, target_path):
    """Remove a file from the in-memory target index."""
    with _target_index_lock:
        if file_name in _target_index:
            _target_index[file_name] = [p for p in _target_index[file_name] if p != target_path]
            if not _target_index[file_name]:
                del _target_index[file_name]


def build_target_index():
    """Scan TARGET_BASE and build the in-memory index."""
    with _target_index_lock:
        _target_index.clear()
    if not os.path.isdir(TARGET_BASE):
        return
    for root, _, files in os.walk(TARGET_BASE):
        for f in files:
            _index_add(f, os.path.join(root, f))
    logger.info(f"[📇] Target index built: {sum(len(v) for v in _target_index.values())} files")


def find_target_matches(file_name):
    with _target_index_lock:
        return list(_target_index.get(file_name, []))


def cleanup_empty_parent_dirs(path):
    while path and os.path.commonpath([os.path.abspath(path), os.path.abspath(TARGET_BASE)]) == os.path.abspath(TARGET_BASE):
        if not os.path.isdir(path):
            break
        try:
            if not os.listdir(path):
                os.rmdir(path)
                logger.info(f"[🧹] Removed empty folder: {path}")
                path = os.path.dirname(path)
            else:
                break
        except OSError:
            break


def remove_from_destination(file_path):
    file_name = os.path.basename(file_path)
    
    # Calculate source file hash before it's gone (if still available)
    source_hash = get_file_hash(file_path) if os.path.exists(file_path) else None
    
    matches = find_target_matches(file_name)
    if not matches:
        logger.info(f"[🗑️] No target copy found for deleted source: {file_name}")
        return

    for target_path in matches:
        try:
            # If source still exists, verify hash before deleting
            if source_hash:
                target_hash = get_file_hash(target_path)
                if target_hash != source_hash:
                    logger.warning(f"[⚠️] Hash mismatch for {file_name}, skipping deletion to be safe")
                    continue
            else:
                # If we can't access source anymore, do a basic sanity check
                logger.info(f"[ℹ️] Source unavailable for hash verification, deleting {file_name} from target")
            
            os.remove(target_path)
            _index_remove(file_name, target_path)
            logger.info(f"[✅] Removed from target: {target_path}")
            cleanup_empty_parent_dirs(os.path.dirname(target_path))
        except Exception as e:
            logger.error(f"[❌] Error removing {target_path}: {e}")


def copy_to_destination(file_path):
    """Copies supported media files to the target folder using the best available date."""
    if not os.path.isfile(file_path):
        return

    file_name = os.path.basename(file_path)

    if IGNORE_FILES:
        for pattern in IGNORE_FILES:
            if fnmatchcase(file_name, pattern):
                logger.info(f"[🚫] Ignored file: {file_path}")
                return

    tmp_patterns = ("~syncthing~", ".syncthing.", ".tmp", ".part")
    if any(p in file_name for p in tmp_patterns):
        return

    if not is_supported_media(file_path):
        return

    date = get_media_date(file_path)
    if not date:
        logger.warning(f"[⚠️] No usable date for file: {file_path}")
        return

    year_folder = os.path.join(TARGET_BASE, str(date.year))
    month_folder = os.path.join(year_folder, f"{MONTHS[date.month - 1]} {date.year}")
    os.makedirs(month_folder, exist_ok=True)

    dest_path = os.path.join(month_folder, file_name)

    # Validate destination is within TARGET_BASE (prevent directory traversal)
    if os.path.commonpath([os.path.abspath(dest_path), os.path.abspath(TARGET_BASE)]) != os.path.abspath(TARGET_BASE):
        logger.warning(f"[⚠️] Path traversal blocked: {file_name} → {dest_path}")
        return

    if os.path.exists(dest_path) and os.path.getmtime(file_path) <= os.path.getmtime(dest_path):
        logger.info(f"[=] Already up to date: {dest_path}")
        return

    safe_copy(file_path, dest_path)
    _index_add(file_name, dest_path)


def _debounced_process(file_path, action):
    """Debounce file processing: only run action after DEBOUNCE_DELAY seconds of quiet."""
    with _debounce_lock:
        key = (file_path, action.__name__)
        if key in _debounce_timers:
            _debounce_timers[key].cancel()
        timer = threading.Timer(DEBOUNCE_DELAY, action, args=[file_path])
        _debounce_timers[key] = timer
        timer.start()


def _process_copy(file_path):
    """Process a file for copying (used by debounce)."""
    if is_file_stable(file_path):
        copy_to_destination(file_path)


class PhotoHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory:
            try:
                logger.info(f"[🆕] New file: {event.src_path}")
                _debounced_process(event.src_path, _process_copy)
            except Exception as e:
                logger.error(f"[❌] Error processing new file {event.src_path}: {e}", exc_info=True)

    def on_modified(self, event):
        if not event.is_directory:
            try:
                logger.info(f"[✏️] Modified: {event.src_path}")
                _debounced_process(event.src_path, _process_copy)
            except Exception as e:
                logger.error(f"[❌] Error processing modified file {event.src_path}: {e}", exc_info=True)

    def on_deleted(self, event):
        if not event.is_directory:
            try:
                logger.info(f"[🗑️] Deleted: {event.src_path}")
                remove_from_destination(event.src_path)
            except Exception as e:
                logger.error(f"[❌] Error processing deleted file {event.src_path}: {e}", exc_info=True)

    def on_moved(self, event):
        if not event.is_directory:
            try:
                logger.info(f"[🔄] Moved: {event.src_path} -> {event.dest_path}")
                remove_from_destination(event.src_path)
                _debounced_process(event.dest_path, _process_copy)
            except Exception as e:
                logger.error(f"[❌] Error processing moved file {event.src_path}: {e}", exc_info=True)


if __name__ == "__main__":
    observers = []
    logger.info(f"📸 Monitoring: {SOURCE_DIRS}")
    build_target_index()

    for src in SOURCE_DIRS:
        if not os.path.exists(src):
            logger.warning(f"[⚠️] Folder not found: {src}")
            continue

        handler = PhotoHandler()
        observer = Observer()
        observer.schedule(handler, src, recursive=False)
        observer.start()
        observers.append(observer)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("🛑 Stopping...")
        for obs in observers:
            obs.stop()
        for obs in observers:
            obs.join()
        logger.info("✅ Done.")