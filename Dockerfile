FROM python:3.13-alpine@sha256:ba27df8b6499cc3756cff167690f3fcb5321faa1703b3a914df5f4fdadb32f05 AS dependencies
WORKDIR /app
COPY requirements.txt telemetry-requirements.txt .
RUN python -m pip install --disable-pip-version-check --no-cache-dir --require-hashes --only-binary=:all: --target /dependencies -r requirements.txt -r telemetry-requirements.txt

FROM python:3.13-alpine@sha256:ba27df8b6499cc3756cff167690f3fcb5321faa1703b3a914df5f4fdadb32f05 AS runtime
RUN python -m pip uninstall --yes pip
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
