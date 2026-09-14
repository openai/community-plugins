# Rebuild the OpenPnP bridge

The Java sources in `src/org/openpnp/codex/` are the corresponding source of
`openpnp-codex-bridge.jar`, licensed GPL-3.0-or-later. The current GUI-capable
bridge compiles against the exact OpenPnP revision
`5bd404cfc70f34103a3ca0fbb6b50c2b465f407c` **plus** the six runtime patches in
`upstream-patches/`. The isolated simulator also has separately tested runtime
compatibility with the stock revision; GUI attachment requires the patched
runtime. See [BUILD_GUI.md](BUILD_GUI.md) for the ownership and launch contract.

The private S79 source tree includes a revised GUI ownership patch that preserves
the previous job graph during external publication. The external owner must
release retained graphs when they are no longer needed. Existing packaged JARs
and manifests remain bound to their earlier source/runtime; this source change
does not qualify or replace those artifacts. Use the exact patch and runtime
hashes in BUILD_GUI.md when evaluating the separately tested native overlay.

## Canonical repository build

Use JDK 17 (the Java sources target Java 11 bytecode), Python 3 with tarfile's data
extraction filter, Git and `patch`. A first stock build additionally needs
Maven 3.9. From the community-plugins checkout:

```sh
export JAVA_HOME=/absolute/jdk17-home
export OPENPNP_SOURCE=/absolute/clean/pinned/openpnp-source
npm run test:openpnp:native
```

For compilation without native simulator workloads, run
`bash scripts/openpnp-build-native.sh`. Both commands create a **new** directory
under `validation/native-build-<unique>/`. `--output /absolute/new/path` selects
a different new directory inside the checkout. `OPENPNP_BUILD_DIR` is its
environment-variable equivalent. Existing output directories are refused.

`OPENPNP_SOURCE` is verified against the exact commit and must have no tracked
changes. If `OPENPNP_RUNTIME` names a stock `codex-build-manifest.json`
distribution, every inventoried file is checked before it is used. Otherwise
the builder first checks `OPENPNP_SOURCE/target/codex-runtime`; if that verified
distribution is absent, it runs Maven in a **fresh archive of pinned source
inside the new output** and inventories the resulting stock runtime. It never
runs Maven in the input checkout or rewrites an existing runtime. Set
`OPENPNP_MAVEN_CACHE` to choose a dependency cache.

The builder then creates `build/source/` with complete patched upstream source,
`build/runtime/` with the patched native runtime and preferences-only launcher
JAR, the separate `build/openpnp-codex-bridge.jar`, compiled tests, and provenance
manifests. The test command runs all 91 baseline native simulator mains and retains each
log and checksum under `native-tests/`; it does not open a GUI. GUI tests are a
separate explicit command described in BUILD_GUI.md.

Production sources target Java 11 bytecode; the test fixtures target Java 17 and
run on the documented JDK 17+ build runtime. Crash/restart integration campaigns
remain separate from the 91 baseline mains and retain their own evidence.

**Builds do not publish or replace packaged artifacts.** The final
`canonical-build-receipt.json` identifies the output paths, bridge checksum and
executed test count. Publication is a separate reviewed release step: copy the
validated bridge JAR, matching source, bootstrap and exact build manifest into
the plugin together. Keep test evidence bound to the tested artifact hashes.

The pinned Docker wrapper supports the same arguments:

```sh
export OPENPNP_SOURCE=/absolute/clean/pinned/openpnp-source
npm run test:openpnp:native:docker
```

It mounts the input source and optional stock runtime read-only, stores outputs
in this checkout, and uses a disposable pinned Maven/JDK 17 image. Python is
installed only inside that container. Its default CPU architecture follows the
host; `OPENPNP_DOCKER_PLATFORM` explicitly selects another platform. Package
installation and Maven dependency resolution require network access. Docker
GUI/display qualification is outside this command.

## Rebuilding from the packaged corresponding source

A standalone source rebuild uses the same exact upstream revision and patches:

```sh
git clone https://github.com/openpnp/openpnp.git openpnp
git -C openpnp checkout 5bd404cfc70f34103a3ca0fbb6b50c2b465f407c
patch -d openpnp -p1 < upstream-patches/gui-ownership.patch
patch -d openpnp -p1 < upstream-patches/native-action-observer.patch
patch -d openpnp -p1 < upstream-patches/native-board-load-history.patch
patch -d openpnp -p1 < upstream-patches/gui-topology-events.patch
patch -d openpnp -p1 < upstream-patches/native-circular-symmetry.patch
patch -d openpnp -p1 < upstream-patches/native-vacuum-sensing.patch
(cd openpnp && mvn -B -DskipTests package)
mkdir classes
javac --release 11 -cp 'openpnp/target/openpnp-gui-0.0.1-alpha-SNAPSHOT.jar:openpnp/target/lib/*' -d classes src/org/openpnp/codex/*.java
jar --create --date=2026-09-11T00:00:00Z --file openpnp-codex-bridge.jar -C classes .
```

Run in a fresh directory containing this packaged `src/` and
`upstream-patches/` tree. The shell example uses POSIX syntax; Windows Java
classpaths use `;`. This rebuild produces corresponding executable code; a
fresh compiler or full upstream build may have different artifact hashes.
The canonical repository builder additionally creates the inventories and
launcher JAR required by the verified CLI launch contract. Do not reuse an old
manifest to launch newly rebuilt binaries.
