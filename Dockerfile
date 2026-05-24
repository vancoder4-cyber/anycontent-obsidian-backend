# anycontent-vault-backend
# Minimal Dockerfile — keeps the image small by skipping uv and going through pip.
# For day-to-day development on host, `uv sync && uv run python web/app.py` is faster.

FROM python:3.12-slim

# lxml needs libxml2 / libxslt + ffmpeg is required for Douyin audio extraction
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ffmpeg \
        libxml2 \
        libxslt1.1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY douyin_mcp_server ./douyin_mcp_server
COPY douyin-video ./douyin-video
COPY web ./web

# Install only the runtime deps from pyproject (skip dev/test extras)
RUN pip install --no-cache-dir \
        "mcp>=1.0.0" \
        requests \
        ffmpeg-python \
        tqdm \
        dashscope \
        fastapi \
        "uvicorn[standard]" \
        jinja2 \
        beautifulsoup4 \
        lxml \
        youtube-transcript-api

EXPOSE 8080

# API_KEY must be passed in via `-e API_KEY=sk-...`
CMD ["python", "web/app.py"]
