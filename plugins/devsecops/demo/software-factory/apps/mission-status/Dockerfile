# Both values come from the qualified factory lock. No default image or tag.
# Example variable shape only: repository is the path UNDER registry1.dso.mil/ironbank/.
ARG IRONBANK_PYTHON_REPOSITORY
ARG IRONBANK_PYTHON_SHA256
FROM registry1.dso.mil/ironbank/${IRONBANK_PYTHON_REPOSITORY}@sha256:${IRONBANK_PYTHON_SHA256}

# F0 must qualify Python 3.11+, venv/ensurepip, this UID/GID, and the selected architecture.
USER 0:0
WORKDIR /opt/mission-status
COPY requirements.txt ./requirements.txt
# Acquisition is an explicit earlier step; this directory must contain the locked wheel.
COPY wheelhouse/ ./wheelhouse/
RUN --network=none python3 -m venv /opt/mission-status/venv \
    && /opt/mission-status/venv/bin/python -m pip install \
       --no-index --find-links=/opt/mission-status/wheelhouse \
       --require-hashes --only-binary=:all: --no-deps -r requirements.txt \
    && rm -rf /opt/mission-status/wheelhouse
COPY --chown=10001:10001 app.py ./app.py
COPY --chown=10001:10001 probe.py ./probe.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8080 9090
ENTRYPOINT ["/opt/mission-status/venv/bin/python", "/opt/mission-status/app.py"]
CMD ["--host", "0.0.0.0", "--health-port", "8080", "--metrics-port", "9090"]
