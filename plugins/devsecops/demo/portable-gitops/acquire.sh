#!/usr/bin/env bash
# Run only on the approved, disposable Linux/amd64 VM. No package installation.
set -euo pipefail
umask 077
state=${1:?private state directory}
python3 "$(dirname "$0")/stack.py" check --state "$state"
mkdir -p "$state/bin" "$state/downloads"
cd "$state/downloads"
fetch() { test ! -e "$2"; curl --proto '=https' --proto-redir '=https' --tlsv1.2 --fail --location --max-time 300 --max-filesize 209715200 --output "$2" "$1"; }
fetch 'https://github.com/k3s-io/k3s/releases/download/v1.34.13%2Bk3s1/sha256sum-amd64.txt' k3s.sha256
fetch 'https://github.com/k3s-io/k3s/releases/download/v1.34.13%2Bk3s1/k3s' k3s
awk '$2 == "k3s" {print}' k3s.sha256 > k3s-selected.sha256
test "$(wc -l < k3s-selected.sha256)" -eq 1
sha256sum --check k3s-selected.sha256
fetch 'https://gitlab-runner-downloads.s3.amazonaws.com/v19.5.1/release.sha256' runner.sha256
fetch 'https://gitlab-runner-downloads.s3.amazonaws.com/v19.5.1/binaries/gitlab-runner-linux-amd64' gitlab-runner-linux-amd64
awk '$2 == "binaries/gitlab-runner-linux-amd64" {print $1 "  gitlab-runner-linux-amd64"}' runner.sha256 > runner-selected.sha256
test "$(wc -l < runner-selected.sha256)" -eq 1
sha256sum --check runner-selected.sha256
fetch 'https://raw.githubusercontent.com/argoproj/argo-cd/v3.5.6/manifests/install.yaml' argocd-install.yaml
sha256sum k3s gitlab-runner-linux-amd64 argocd-install.yaml > acquired.sha256
install -m 0755 k3s "$state/bin/k3s"
install -m 0755 gitlab-runner-linux-amd64 "$state/bin/gitlab-runner"
# Tags are resolved once and the actual immutable Docker references are recorded.
docker pull gitlab/gitlab-ce:19.5.5-ce.0
docker image inspect gitlab/gitlab-ce:19.5.5-ce.0 > gitlab-image.json
docker pull prom/prometheus:v3.13.5
docker image inspect prom/prometheus:v3.13.5 > prometheus-image.json
printf 'Acquired version-selected dependencies; immutable receipts are private. No services started.\n'
