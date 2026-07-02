ARG BUILD_FROM
FROM ${BUILD_FROM}

WORKDIR /app

# Python deps (renderer deps like Pillow arrive in Phase 2)
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip3 install --no-cache-dir -r /app/backend/requirements.txt

# Vendor Alpine.js at build time — avoids committing third-party minified JS,
# mirroring how fonts are fetched at build time elsewhere in the project.
ARG ALPINE_JS_VERSION=3.14.1
RUN mkdir -p /app/frontend/static \
 && curl -fsSL "https://cdn.jsdelivr.net/npm/alpinejs@${ALPINE_JS_VERSION}/dist/cdn.min.js" \
      -o /app/frontend/static/alpine.min.js

COPY backend/ /app/backend/
COPY frontend/ /app/frontend/
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
