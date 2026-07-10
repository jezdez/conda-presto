ARG PIXI_VERSION=0.70.1

FROM ghcr.io/prefix-dev/pixi:${PIXI_VERSION} AS build

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

FROM debian:bookworm-slim AS production

ARG PIXI_ENV=prod

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --shell /usr/sbin/nologin --no-create-home app

WORKDIR /app
COPY --from=build /app/.pixi/envs/${PIXI_ENV} /app/.pixi/envs/${PIXI_ENV}
COPY --from=build --chmod=0755 /app/entrypoint.sh /app/entrypoint.sh
COPY conda_presto/ /app/conda_presto/

RUN mkdir -p /app/.pixi/envs/${PIXI_ENV}/pkgs/cache /home/app/.conda/pkgs \
    && chown -R app:app /app/.pixi/envs/${PIXI_ENV}/pkgs /home/app

USER app
ENTRYPOINT ["/app/entrypoint.sh", "conda", "presto"]

FROM production AS server

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD /app/.pixi/envs/prod/bin/python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"

CMD ["--serve", "--host", "0.0.0.0"]

FROM production AS cli
