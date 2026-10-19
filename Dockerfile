FROM python:3.13-slim-bookworm@sha256:5024f48ba9441d4b13a95d3945abc6365538e3a31109833367a1923523c6efed AS dependencies
WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --disable-pip-version-check --no-cache-dir --require-hashes --only-binary=:all: --target /dependencies -r requirements.txt

FROM python:3.13-slim-bookworm@sha256:5024f48ba9441d4b13a95d3945abc6365538e3a31109833367a1923523c6efed AS runtime
ENV PYTHONPATH=/dependencies:/app/lib.zip PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OTEL_ENABLED=0
WORKDIR /app
COPY --from=dependencies /dependencies /dependencies
COPY web.py api.py telemetry.py app.js index.html style.css lib.zip ./
USER 65532:65532
EXPOSE 8080
CMD ["python", "web.py"]

FROM runtime AS test
COPY tests tests
RUN --network=none python -m unittest discover -s tests -v
