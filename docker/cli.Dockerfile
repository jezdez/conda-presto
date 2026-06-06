FROM ghcr.io/prefix-dev/pixi:0.70.1 AS build

WORKDIR /app
COPY pyproject.toml pixi.lock ./
COPY conda_presto/ conda_presto/
RUN pixi install --locked -e cli
RUN pixi shell-hook -e cli -s bash > /shell-hook
RUN echo '#!/bin/bash' > /app/entrypoint.sh \
    && cat /shell-hook >> /app/entrypoint.sh \
    && echo 'exec "$@"' >> /app/entrypoint.sh

FROM debian:bookworm-slim AS production

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --shell /usr/sbin/nologin --no-create-home app

WORKDIR /app
COPY --from=build /app/.pixi/envs/cli /app/.pixi/envs/cli
COPY --from=build --chmod=0755 /app/entrypoint.sh /app/entrypoint.sh
COPY conda_presto/ /app/conda_presto/

RUN mkdir -p /app/.pixi/envs/cli/pkgs/cache /home/app/.conda/pkgs \
    && chown -R app:app /app/.pixi/envs/cli/pkgs /home/app

USER app

ENTRYPOINT ["/app/entrypoint.sh", "conda", "presto"]
