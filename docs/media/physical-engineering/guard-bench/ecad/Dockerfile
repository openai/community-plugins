FROM debian:trixie-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       kicad=9.0.2+dfsg-1 kicad-footprints=9.0.2-1 kicad-symbols=9.0.2-1 \
       fonts-dejavu-core python3 \
    && rm -rf /var/lib/apt/lists/*
# The native first-run wizard normally installs these stock library tables.
RUN mkdir -p /root/.config/kicad/9.0 \
    && cp /usr/share/kicad/template/sym-lib-table /root/.config/kicad/9.0/ \
    && cp /usr/share/kicad/template/fp-lib-table /root/.config/kicad/9.0/
