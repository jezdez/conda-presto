FROM ghcr.io/prefix-dev/pixi:0.70.1 AS build

WORKDIR /app
COPY pyproject.toml pixi.lock ./
COPY conda_presto/ conda_presto/

ARG CONDA_PRESTO_VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${CONDA_PRESTO_VERSION}

RUN pixi install --locked -e prod
RUN pixi shell-hook -e prod -s bash > /shell-hook
RUN echo '#!/bin/bash' > /app/entrypoint.sh \
    && cat /shell-hook >> /app/entrypoint.sh \
    && echo 'exec "$@"' >> /app/entrypoint.sh

FROM debian:bookworm-slim AS production

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --shell /usr/sbin/nologin --no-create-home app

WORKDIR /app
COPY --from=build /app/.pixi/envs/prod /app/.pixi/envs/prod
COPY --from=build --chmod=0755 /app/entrypoint.sh /app/entrypoint.sh
COPY conda_presto/ /app/conda_presto/

RUN mkdir -p /app/.pixi/envs/prod/pkgs/cache /home/app/.conda/pkgs \
    && chown -R app:app /app/.pixi/envs/prod/pkgs /home/app

USER app
EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh", "conda", "presto"]
CMD ["--serve", "--host", "0.0.0.0"]
