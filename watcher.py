import os
import shutil
import time
import logging
import hashlib
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



def safe_copy(src, dst):
    """Copy with overwrite protection and clear error reporting."""
    try:
        shutil.copy2(src, dst)
        logger.info(f"[✅] Copied/updated: {dst}")
    except Exception as e:
        logger.error(f"[❌] Error copying {src} → {dst}: {e}")


def is_file_stable(file_path):
    """Avoid processing files still being written by Syncthing."""
    try:
        size1 = os.path.getsize(file_path)
        time.sleep(0.2)
        size2 = os.path.getsize(file_path)
        return size1 == size2
    except Exception:
        return False


def find_target_matches(file_name):
    matches = []
    if not os.path.isdir(TARGET_BASE):
        return matches

    for root, _, files in os.walk(TARGET_BASE):
        if file_name in files:
            matches.append(os.path.join(root, file_name))

    return matches


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

    if os.path.exists(dest_path) and os.path.getmtime(file_path) <= os.path.getmtime(dest_path):
        logger.info(f"[=] Already up to date: {dest_path}")
        return

    safe_copy(file_path, dest_path)


class PhotoHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory:
            logger.info(f"[🆕] New file: {event.src_path}")
            if is_file_stable(event.src_path):
                copy_to_destination(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            logger.info(f"[✏️] Modified: {event.src_path}")
            if is_file_stable(event.src_path):
                copy_to_destination(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            logger.info(f"[🗑️] Deleted: {event.src_path}")
            remove_from_destination(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            logger.info(f"[🔄] Moved: {event.src_path} -> {event.dest_path}")
            remove_from_destination(event.src_path)
            if is_file_stable(event.dest_path):
                copy_to_destination(event.dest_path)


if __name__ == "__main__":
    observers = []
    logger.info(f"📸 Monitoring: {SOURCE_DIRS}")

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