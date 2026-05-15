# syntax = docker/dockerfile:1.2

FROM python:3.9

WORKDIR /cltl-context
COPY src requirements.txt makefile ./
COPY config ./config
COPY util ./util

RUN --mount=type=bind,target=/cltl-context/repo,from=cltl/cltl-requirements:latest,source=/repo \
        make venv project_repo=/cltl-context/repo/leolani project_mirror=/cltl-context/repo/mirror

CMD . venv/bin/activate && python main.py
