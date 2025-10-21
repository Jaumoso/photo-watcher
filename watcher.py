import os
import shutil
import time
# import hashlib
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from PIL import Image
from PIL.ExifTags import TAGS

SOURCE_DIRS = os.getenv("SOURCE_DIR", "./source").split(",")
SOURCE_DIRS = [s.strip() for s in SOURCE_DIRS if s.strip()]
TARGET_BASE = os.getenv("TARGET_BASE", "./target")

MONTHS = [
    "01. Enero", "02. Febrero", "03. Marzo", "04. Abril",
    "05. Mayo", "06. Junio", "07. Julio", "08. Agosto",
    "09. Septiembre", "10. Octubre", "11. Noviembre", "12. Diciembre"
]

# def file_hash(path):
#     """Calcula el hash SHA256 de un archivo."""
#     h = hashlib.sha256()
#     try:
#         with open(path, "rb") as f:
#             for chunk in iter(lambda: f.read(8192), b""):
#                 h.update(chunk)
#         return h.hexdigest()
#     except Exception:
#         return None

def get_exif_date(file_path):
    """Devuelve fecha EXIF de la imagen o None."""
    try:
        with Image.open(file_path) as img:
            exif = img._getexif()
            if not exif:
                return None
            for tag_id, value in exif.items():
                tag = TAGS.get(tag_id, tag_id)
                if tag == "DateTimeOriginal":
                    return datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
    except Exception:
        return None
    return None

def copy_to_destination(file_path):
    """Copia o actualiza la imagen según su fecha EXIF (si hay un cambio en el archivo)"""
    if not os.path.isfile(file_path):
        return
    if not file_path.lower().endswith((".jpg", ".jpeg", ".png", ".heic", ".webp")):
        return  # ignorar archivos que no sean imagen

    date = get_exif_date(file_path)
    if not date:
        print(f"[⚠️] Sin fecha EXIF: {file_path}")
        return

    year_folder = os.path.join(TARGET_BASE, str(date.year))
    month_folder = os.path.join(year_folder, MONTHS[date.month - 1])
    os.makedirs(month_folder, exist_ok=True)

    dest_path = os.path.join(month_folder, os.path.basename(file_path))

    # Solo copiar si no existe o si el origen es más reciente
    if not os.path.exists(dest_path) or os.path.getmtime(file_path) > os.path.getmtime(dest_path):
        shutil.copy2(file_path, dest_path)
        print(f"[✅] Copiado/actualizado: {dest_path}")
    else:
        print(f"[=] Ya actualizado: {dest_path}")

class PhotoHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory:
            print(f"[🆕] Nuevo archivo detectado: {event.src_path}")
            copy_to_destination(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            print(f"[✏️] Archivo modificado: {event.src_path}")
            copy_to_destination(event.src_path)

if __name__ == "__main__":
    observers = []
    print(f"📸 Monitoreando cambios en: {SOURCE_DIRS}")
    for src in SOURCE_DIRS:
        if not os.path.exists(src):
            print(f"[⚠️] Carpeta no encontrada: {src}")
            continue

        print(f"📸 Monitoreando: {src}")
        handler = PhotoHandler()
        observer = Observer()
        observer.schedule(handler, src, recursive=False)
        observer.start()
        observers.append(observer)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Deteniendo observadores...")
        for obs in observers:
            obs.stop()
        for obs in observers:
            obs.join()
        print("✅ Finalizado.")