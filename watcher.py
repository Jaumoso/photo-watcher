import os
import shutil
import time
from fnmatch import fnmatchcase
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from PIL import Image, ExifTags

SOURCE_DIRS = os.getenv("SOURCE_DIRS", "./source").split(",")
SOURCE_DIRS = [s.strip() for s in SOURCE_DIRS if s.strip()]
TARGET_BASE = os.getenv("TARGET_BASE", "./target")
IGNORE_FILES = os.getenv("IGNORE_FILES", "").split(",")

MONTHS = [
    "1. Enero", "2. Febrero", "3. Marzo", "4. Abril",
    "5. Mayo", "6. Junio", "7. Julio", "8. Agosto",
    "9. Septiembre", "10. Octubre", "11. Noviembre", "12. Diciembre"
]


def get_file_date(file_path):
    """Returns the EXIF date if available, otherwise None."""
    try:
        with Image.open(file_path) as img:
            exif_data = img.getexif()
            if not exif_data:
                return None

            for tag_id, value in exif_data.items():
                tag = ExifTags.TAGS.get(tag_id, tag_id)
                if tag in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
                    try:
                        return datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
                    except Exception:
                        continue
    except Exception as e:
        print(f"[❌] Error reading EXIF from {file_path}: {e}")
        return None
    return None


def safe_copy(src, dst):
    """Copy with overwrite protection and clear error reporting."""
    try:
        shutil.copy2(src, dst)
        print(f"[✅] Copied/updated: {dst}")
    except Exception as e:
        print(f"[❌] Error copying {src} → {dst}: {e}")


def is_file_stable(file_path):
    """Avoid processing files still being written by Syncthing."""
    try:
        size1 = os.path.getsize(file_path)
        time.sleep(0.2)
        size2 = os.path.getsize(file_path)
        return size1 == size2
    except Exception:
        print(f"[❌] Error checking stability of {file_path}")
        return False


def copy_to_destination(file_path):
    """Copies the image only if it has EXIF date, avoiding filename collisions."""
    if not os.path.isfile(file_path):
        return
    
    file_name = os.path.basename(file_path)

    if IGNORE_FILES:
        for pattern in IGNORE_FILES:
            if fnmatchcase(file_name, pattern):
                print(f"[🚫] Ignored file: {file_path}")
                return
    
    tmp_patterns = ("~syncthing~", ".syncthing.", ".tmp", ".part")
    if any(p in file_name for p in tmp_patterns):
        return

    ext = file_path.lower()
    if not ext.endswith((".jpg", ".jpeg", ".png", ".heic", ".webp")):
        return

    date = get_file_date(file_path)
    if not date:
        print(f"[⚠️] No EXIF date: {file_path}")
        return

    year_folder = os.path.join(TARGET_BASE, str(date.year))
    month_folder = os.path.join(year_folder, f"{MONTHS[date.month - 1]} {date.year}")
    os.makedirs(month_folder, exist_ok=True)

    dest_path = os.path.join(month_folder, file_name)

    # Handle duplicate filenames safely
    if os.path.exists(dest_path) and os.path.getmtime(file_path) <= os.path.getmtime(dest_path):
        print(f"[=] Already up to date: {dest_path}")
        return

    safe_copy(file_path, dest_path)


class PhotoHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory:
            print(f"[🆕] New file: {event.src_path}")
            copy_to_destination(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            print(f"[✏️] Modified: {event.src_path}")
            copy_to_destination(event.src_path)


if __name__ == "__main__":
    observers = []
    print(f"📸 Monitoring: {SOURCE_DIRS}")

    for src in SOURCE_DIRS:
        if not os.path.exists(src):
            print(f"[⚠️] Folder not found: {src}")
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
        print("\n🛑 Stopping...")
        for obs in observers:
            obs.stop()
        for obs in observers:
            obs.join()
        print("✅ Done.")