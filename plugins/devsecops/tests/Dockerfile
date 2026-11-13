# Clean Linux qualification image. Provisioning may use the network during
# docker build. Run tests with: docker run --rm --network none IMAGE
FROM python:3.14-slim-bookworm@sha256:44e000a07b48bef1517a2b14adc93c17af728ca98858b5e24c6e1b18de1b2ef2 AS system

RUN apt-get update \
    && apt-get install --yes --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir --index-url https://pypi.org/simple uv==0.11.23

ENV UV_PYTHON_DOWNLOADS=never \
    UV_DEFAULT_INDEX=https://pypi.org/simple \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /opt/devsecops

# Keep installed dependencies and verified tool downloads cached separately
# from source so a final source copy can be rebuilt immediately before testing.
FROM system AS tools
COPY toolchain.lock.json ./
COPY scripts/bootstrap.py ./scripts/bootstrap.py
# Bootstrap imports the exact-version schema selector before full source is copied.
COPY src/devsecops/__init__.py src/devsecops/schema_catalog.py ./src/devsecops/
RUN python scripts/bootstrap.py --cache /root/.cache/devsecops/v0.1

FROM tools AS dependencies
COPY pyproject.toml uv.lock LICENSE NOTICE ./
RUN uv sync --frozen --no-install-project

FROM dependencies AS validation
COPY src/ ./src/
COPY tests/ ./tests/
COPY scripts/ ./scripts/
COPY profiles/ ./profiles/
COPY schemas/ ./schemas/
COPY skills/ ./skills/
COPY .codex-plugin/ ./.codex-plugin/
COPY assets/ ./assets/
COPY docs/ ./docs/
COPY demo/ ./demo/
COPY kustomize.lock.json cosign.lock.json supplychain.lock.json ./
COPY README.md plugin.json .dockerignore ./
RUN uv sync --frozen

ENV DEVSECOPS_HELM=/root/.cache/devsecops/v0.1/bin/helm \
    DEVSECOPS_KUBECONFORM=/root/.cache/devsecops/v0.1/bin/kubeconform \
    DEVSECOPS_SCHEMAS=/root/.cache/devsecops/v0.1/schemas \
    PATH=/opt/devsecops/.venv/bin:/usr/local/bin:/usr/bin:/bin

CMD ["python", "-m", "pytest", "-q", "-ra"]
