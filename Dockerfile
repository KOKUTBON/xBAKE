# xBAKE container image (amd64 and arm64 / Raspberry Pi)
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    XBAKE_CONFIG=/app/config/config.json

# Runtime libraries needed by OpenCV and V4L2 cameras
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        v4l-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN pip install --upgrade pip && pip install ".[cloud,hardware]"

RUN mkdir -p /app/data /app/config /app/secrets
VOLUME ["/app/data", "/app/config"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/status', timeout=4)"

# Bind to all interfaces inside the container; publish the port selectively on the host.
CMD ["xbake", "run", "--host", "0.0.0.0", "--port", "8000"]
