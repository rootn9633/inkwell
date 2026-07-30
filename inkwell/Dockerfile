ARG BUILD_FROM
FROM ${BUILD_FROM}

WORKDIR /app

# Python deps
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip3 install --no-cache-dir -r /app/backend/requirements.txt

COPY backend/ /app/backend/
COPY frontend/ /app/frontend/
COPY run.sh /run.sh
RUN chmod a+x /run.sh

# Vendored assets fetched at build time (not committed): Alpine.js + js-yaml for the UI.
ARG ALPINE_JS_VERSION=3.14.1
ARG JS_YAML_VERSION=4.1.0
RUN mkdir -p /app/frontend/static \
 && curl -fsSL "https://cdn.jsdelivr.net/npm/alpinejs@${ALPINE_JS_VERSION}/dist/cdn.min.js" \
      -o /app/frontend/static/alpine.min.js \
 && curl -fsSL "https://cdn.jsdelivr.net/npm/js-yaml@${JS_YAML_VERSION}/dist/js-yaml.min.js" \
      -o /app/frontend/static/js-yaml.min.js

# Default CJK font baked into the image so a fresh install renders the seeded template
# out of the box (LXGW WenKai Screen, OFL-1.1). User-uploaded fonts in /config/fonts win.
# OFL requires the license + copyright to travel with the font, so ship OFL.txt alongside it.
ARG LXGW_VERSION=v1.522
RUN mkdir -p /app/backend/defaults/fonts \
 && curl -fsSL "https://github.com/lxgw/LxgwWenKai-Screen/releases/download/${LXGW_VERSION}/LXGWWenKaiScreen.ttf" \
      -o /app/backend/defaults/fonts/LXGWWenKaiScreen.ttf \
 && curl -fsSL "https://raw.githubusercontent.com/lxgw/LxgwWenKai-Screen/${LXGW_VERSION}/OFL.txt" \
      -o /app/backend/defaults/fonts/LXGWWenKaiScreen.OFL.txt

CMD [ "/run.sh" ]
