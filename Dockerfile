FROM ghcr.io/prefix-dev/pixi:0.70.1

WORKDIR /app
COPY pyproject.toml pixi.lock ./
COPY conda_presto/ conda_presto/

RUN pixi install --locked -e prod \
    && mkdir -p /app/.pixi/envs/prod/pkgs/cache /home/ubuntu/.conda/pkgs \
    && chown -R 1000:1000 /app /home/ubuntu/.conda

USER ubuntu

ENV HOME=/home/ubuntu \
    PATH=/app/.pixi/envs/prod/bin:$PATH \
    CONDA_CHANNEL_PRIORITY=strict \
    CONDA_NO_LOCK=true \
    CONDA_UNSATISFIABLE_HINTS=false \
    CONDA_NUMBER_CHANNEL_NOTICES=0 \
    CONDA_AGGRESSIVE_UPDATE_PACKAGES= \
    CONDA_LOCAL_REPODATA_TTL=300 \
    CONDA_JSON=true \
    CONDA_SOLVER=rattler \
    CONDA_PRESTO_HOST=0.0.0.0 \
    CONDA_PRESTO_PORT=7860 \
    CONDA_PRESTO_CONCURRENCY=1 \
    CONDA_PRESTO_WORKERS=1 \
    CONDA_PRESTO_PLATFORMS=linux-64

EXPOSE 7860

CMD ["conda", "presto", "--serve"]
