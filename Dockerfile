FROM ghcr.io/prefix-dev/pixi:0.81.0@sha256:788ae451641666e2d1f79d3dbe35392dfc7e9b394b16a3acb75c347f3badb2ab AS build

ARG PIXI_ENVIRONMENT=prod

# The temporary Workspaces source dependency needs Git in the build stage.
RUN apt-get update \
    && apt-get install --no-install-recommends -y git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml pixi.lock README.md ./
COPY conda_presto/ conda_presto/
ARG CONDA_PRESTO_VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CONDA_PRESTO=${CONDA_PRESTO_VERSION}

RUN pixi install --locked -e "${PIXI_ENVIRONMENT}"
RUN pixi shell-hook -e "${PIXI_ENVIRONMENT}" -s bash > /shell-hook
RUN echo '#!/bin/bash' > /app/entrypoint.sh \
    && cat /shell-hook >> /app/entrypoint.sh \
    && echo 'exec "$@"' >> /app/entrypoint.sh

FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171 AS production

ARG PIXI_ENVIRONMENT=prod

# The base image predates the PCRE2 security update.
RUN apt-get update \
    && apt-get install --only-upgrade --no-install-recommends -y libpcre2-8-0 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --shell /usr/sbin/nologin --no-create-home app

WORKDIR /app
COPY --from=build /app/.pixi/envs/${PIXI_ENVIRONMENT} /app/.pixi/envs/${PIXI_ENVIRONMENT}
COPY --from=build --chmod=0755 /app/entrypoint.sh /app/entrypoint.sh
COPY conda_presto/ /app/conda_presto/

RUN mkdir -p /app/.pixi/envs/${PIXI_ENVIRONMENT}/pkgs/cache /home/app/.conda/pkgs \
    && chown -R app:app /app/.pixi/envs/${PIXI_ENVIRONMENT}/pkgs /home/app \
    && find / -xdev -type f -perm /6000 -exec chmod a-s {} + \
    && chmod -R a-w /app/conda_presto \
    && chmod a-w /app/entrypoint.sh

USER app
ENV CONDA_NO_LOCK=false \
    CONDA_PRESTO_CONCURRENCY=1 \
    CONDA_PRESTO_PERSISTENT_WORKER=1

ENTRYPOINT ["/app/entrypoint.sh", "env", "CONDA_NO_LOCK=false", "conda", "presto"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD /app/entrypoint.sh python -c "import http.client, os, sys; c = http.client.HTTPConnection('127.0.0.1', int(os.environ.get('CONDA_PRESTO_PORT', '8000')), timeout=3); c.request('GET', '/health'); sys.exit(c.getresponse().status != 200)"

CMD ["--serve", "--host", "0.0.0.0"]
