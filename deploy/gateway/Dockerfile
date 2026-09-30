# The llm-providers gateway (github.com/Codewiz2898/llm-providers) for this server: the app's only way
# to reach models. It holds the OpenRouter key; the app holds only its own token. Pinned to a tag, as
# the Mac's install is (platform/gateway install <ref>), and built from that tag's own lockfile.
FROM node:22-slim AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && corepack enable
ARG LLM_PROVIDERS_REF=v0.4.0
RUN git clone --depth 1 --branch "$LLM_PROVIDERS_REF" https://github.com/Codewiz2898/llm-providers.git /src
WORKDIR /src
RUN pnpm install --frozen-lockfile && pnpm build && pnpm prune --prod --ignore-scripts && test -f dist/cli.js


FROM node:22-slim
WORKDIR /gateway
COPY --from=build /src/package.json ./
COPY --from=build /src/dist dist/
COPY --from=build /src/node_modules node_modules/
COPY gateway.json ./
USER node
EXPOSE 8787
# Listens on the compose network only (no published port); off loopback it refuses calls without a token.
CMD ["node", "dist/cli.js", "serve", "--config", "gateway.json", "--host", "0.0.0.0", "--port", "8787"]
