FROM ghcr.io/prefix-dev/pixi:0.76.1@sha256:540414f50b96439a6ba1475b79b2e2dd2208e86ebef33e2fbf2a176a8f037d31 AS build

ARG PIXI_ENV=prod

WORKDIR /app
COPY pyproject.toml pixi.lock README.md ./
COPY conda_presto/ conda_presto/
ARG CONDA_PRESTO_VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${CONDA_PRESTO_VERSION}

RUN pixi install --locked -e "${PIXI_ENV}"
RUN pixi shell-hook -e "${PIXI_ENV}" -s bash > /shell-hook
RUN echo '#!/bin/bash' > /app/entrypoint.sh \
    && cat /shell-hook >> /app/entrypoint.sh \
    && echo 'exec "$@"' >> /app/entrypoint.sh

FROM debian:bookworm-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818 AS production

ARG PIXI_ENV=prod

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --shell /usr/sbin/nologin --no-create-home app

WORKDIR /app
COPY --from=build /app/.pixi/envs/${PIXI_ENV} /app/.pixi/envs/${PIXI_ENV}
COPY --from=build --chmod=0755 /app/entrypoint.sh /app/entrypoint.sh
COPY conda_presto/ /app/conda_presto/

RUN mkdir -p /app/.pixi/envs/${PIXI_ENV}/pkgs/cache /home/app/.conda/pkgs \
    && chown -R app:app /app/.pixi/envs/${PIXI_ENV}/pkgs /home/app \
    && find / -xdev -type f -perm /6000 -exec chmod a-s {} + \
    && chmod -R a-w /app/conda_presto \
    && chmod a-w /app/entrypoint.sh

USER app
ENTRYPOINT ["/app/entrypoint.sh", "conda", "presto"]

FROM production AS server

ENV CONDA_NO_LOCK=false \
    CONDA_PRESTO_CONCURRENCY=1 \
    CONDA_PRESTO_PERSISTENT_WORKER=1

ENTRYPOINT ["/app/entrypoint.sh", "env", "CONDA_NO_LOCK=false", "conda", "presto"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD /app/.pixi/envs/prod/bin/python -c "import http.client, sys; c = http.client.HTTPConnection('127.0.0.1', 8000, timeout=3); c.request('GET', '/health'); sys.exit(c.getresponse().status != 200)"

CMD ["--serve", "--host", "0.0.0.0"]

FROM production AS cli
