# syntax=docker/dockerfile:1
FROM denoland/deno:bin-2 AS deno

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    XDG_CACHE_HOME=/tmp/master-track-cache \
    DENO_DIR=/tmp/deno-cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# YouTube challenge solving: Deno plus yt-dlp's default dependency group.
COPY --from=deno /deno /usr/local/bin/deno

WORKDIR /app
COPY pyproject.toml ./
COPY master_track/ ./master_track/

# Use CPU wheels on Linux instead of pulling CUDA libraries into this image.
RUN python -m pip install --index-url https://download.pytorch.org/whl/cpu torch torchaudio \
    && python -m pip install '.[separation,youtube]' \
    && python -m pip check

# Opt in to the substantially larger, Git-sourced SAM dependency stack.
ARG INSTALL_GUITARS=false
RUN if [ "$INSTALL_GUITARS" = "true" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends git \
        && rm -rf /var/lib/apt/lists/* \
        && python -m pip install '.[guitars]' \
        && python -m pip check; \
    fi

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home app \
    && mkdir -p /work/input /work/stems /work/.master-track \
    && chown -R app:app /work

WORKDIR /work
USER app
ENTRYPOINT ["master-track"]
CMD ["--help"]
