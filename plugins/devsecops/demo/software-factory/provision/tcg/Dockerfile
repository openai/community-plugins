ARG BASE_IMAGE=ubuntu@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac
FROM ${BASE_IMAGE}
ARG QEMU_VERSION=1:8.2.2+ds-0ubuntu1.18
ARG CLOUD_UTILS_VERSION=0.33-1
COPY debs/ /tmp/debs/
RUN DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends /tmp/debs/*.deb \
    && dpkg-query -W -f='${Package}\t${Version}\n' > /tool-packages.tsv \
    && rm -rf /var/lib/apt/lists/* /tmp/debs
USER 1000:1000
WORKDIR /lab
