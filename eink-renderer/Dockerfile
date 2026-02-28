FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY renderers/ renderers/
# Fonts are baked in — rebuild image to change them
COPY fonts/ fonts/

VOLUME /config/displays
VOLUME /config/hardware
VOLUME /config/output

ENV CONFIG_DIR=/config
ENV PORT=5123

EXPOSE 5123

HEALTHCHECK --interval=30s --timeout=5s \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:5123/health')"

CMD ["python3", "server.py"]
