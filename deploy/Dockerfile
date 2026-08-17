FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir .[postgres]

# No .env, no tests/, no .git — only what's copied above ends up in the
# image. Configuration arrives entirely as environment variables the
# platform injects at runtime (Container Apps Jobs secretRef / managed
# identity — see deploy/README.md). Nothing here reads a vault SDK directly.
ENTRYPOINT ["python", "-m", "pipeline.run"]
