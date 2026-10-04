# syntax=docker/dockerfile:1
FROM denoland/deno:bin-2 AS deno

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    XDG_CACHE_HOME=/tmp/sound-razor-cache \
    DENO_DIR=/tmp/deno-cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# YouTube challenge solving: Deno plus yt-dlp's default dependency group.
COPY --from=deno /deno /usr/local/bin/deno

WORKDIR /app
COPY pyproject.toml LICENSE ./
COPY sound_razor/ ./sound_razor/

# Use CPU wheels on Linux instead of pulling CUDA libraries into this image.
RUN python -m pip install --index-url https://download.pytorch.org/whl/cpu torch torchaudio \
    && python -m pip install '.[separation,youtube]' \
    && python -m pip check

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home app \
    && mkdir -p /work/input /work/stems /work/.sound-razor \
    && chown -R app:app /work

WORKDIR /work
USER app
ENTRYPOINT ["sound-razor"]
CMD ["--help"]
