FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
# renderers/ and fonts/ added in step 3 once those files exist

VOLUME /config/displays
VOLUME /config/hardware
VOLUME /config/output

ENV CONFIG_DIR=/config
ENV PORT=5123

EXPOSE 5123

HEALTHCHECK --interval=30s --timeout=5s \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:5123/health')"

CMD ["python3", "server.py"]
