FROM ghcr.io/prefix-dev/pixi:0.75.0@sha256:3f8fbd5cfe258eba34d058684b95e35a7571f2be17fdca0b96dd19934a284f33 AS build

WORKDIR /app
COPY pyproject.toml pixi.lock README.md ./
COPY conda_presto/ conda_presto/

ARG CONDA_PRESTO_VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${CONDA_PRESTO_VERSION}

RUN pixi install --locked -e prod
RUN pixi shell-hook -e prod -s bash > /shell-hook
RUN echo '#!/bin/bash' > /app/entrypoint.sh \
    && cat /shell-hook >> /app/entrypoint.sh \
    && echo 'exec "$@"' >> /app/entrypoint.sh

FROM debian:bookworm-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --shell /usr/sbin/nologin --no-create-home app

WORKDIR /app
COPY --from=build /app/.pixi/envs/prod /app/.pixi/envs/prod
COPY --from=build --chmod=0755 /app/entrypoint.sh /app/entrypoint.sh
COPY conda_presto/ /app/conda_presto/

RUN mkdir -p /app/.pixi/envs/prod/pkgs/cache /home/app/.conda/pkgs \
    && chown -R app:app /app/.pixi/envs/prod/pkgs /home/app \
    && find / -xdev -type f -perm /6000 -exec chmod a-s {} + \
    && chmod -R a-w /app/conda_presto \
    && chmod a-w /app/entrypoint.sh

USER app

ENV HOME=/home/app \
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

ENTRYPOINT ["/app/entrypoint.sh", "conda", "presto"]

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD /app/.pixi/envs/prod/bin/python -c "import http.client, sys; c = http.client.HTTPConnection('127.0.0.1', 7860, timeout=3); c.request('GET', '/health'); sys.exit(c.getresponse().status != 200)"

CMD ["--serve"]
