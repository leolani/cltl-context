# syntax = docker/dockerfile:1.2

FROM cltl/eliza-base:latest

WORKDIR /cltl-context
COPY src requirements.txt makefile ./
COPY config ./config
COPY util ./util

RUN --mount=type=bind,target=/cltl-context/repo,from=cltl/cltl-requirements:latest,source=/repo \
        make docker-install project_repo=/cltl-context/repo/leolani project_mirror=/cltl-context/repo/mirror

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD python main.py
