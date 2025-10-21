# Imagen base ligera de Python
FROM python:3.12-slim

ARG UID=1000
ARG GID=1000

RUN groupadd -g $GID appuser && useradd -m -u $UID -g $GID appuser

# Crear directorios
WORKDIR /app

# Instalar dependencias del sistema (para Pillow)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg-dev zlib1g-dev libfreetype6-dev liblcms2-dev \
    && rm -rf /var/lib/apt/lists/*

# Copiar dependencias y código
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY watcher.py .

# Asigna permisos al usuario
RUN chown -R appuser:appuser /app
USER appuser

# Variables de entorno configurables (se pueden sobreescribir en docker-compose)
ENV SOURCE_DIRS=/source
ENV TARGET_BASE=/target

# Comando por defecto
CMD ["python", "watcher.py"]
