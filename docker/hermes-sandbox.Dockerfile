# Custom Hermes sandbox image — extends the default terminal.docker_image
# with PDF text-extraction libraries baked in, so every container built
# from this image has them from the start, regardless of how many times
# a container gets removed and recreated (mount changes, troubleshooting
# resets, etc.). Installing into a running container's writable layer
# (docker exec -u root pip install ...) only persists for that specific
# container instance — this bakes it into the image layer instead, which
# every future container inherits automatically.
#
# Add more packages here as the project needs them — this is the place
# to accumulate sandbox dependencies over time rather than re-installing
# ad hoc into whatever container happens to be running.
#
# --- CTF generalization additions below (see CTF_GENERALIZATION_DESIGN.md) ---
#
# HONEST STATUS: this Dockerfile has NOT been build-tested — no Docker
# daemon access was available while writing it. Several install steps
# are flagged inline as needing verification at actual build time,
# following the same "never trust untested infrastructure" discipline
# this project used throughout the Windows lab. Build this, fix whatever
# breaks, and treat this file as a first draft, not a finished artifact.

# PLATFORM: pinned to linux/amd64, not the host's native arm64.
#
# Root cause, confirmed directly: Ghidra's official release zip is a
# single multi-platform bundle containing decompiler binaries for
# linux_x86_64, mac_arm_64, mac_x86_64, and win_x86_64 — but genuinely
# NOT linux_arm_64. This isn't a build misconfiguration; upstream Ghidra
# simply doesn't ship a native Linux arm64 decompiler in this release.
# Running the whole image as amd64 (via Docker's built-in cross-platform
# emulation on Apple Silicon) means the already-bundled linux_x86_64
# decompiler gets used instead, with no other change needed — confirmed
# via direct inspection that the binary is already present in the
# image, just never being selected under the host's native arm64.
#
# The earlier from-source workarounds for radare2, stegseek, and rp++
# (all originally added because THEIR prebuilt releases are x86-64-only)
# do NOT need reverting under this platform change — building from
# source is architecture-agnostic by design, so those steps work exactly
# the same under amd64 emulation as they already do under arm64. Left
# as-is rather than touched, since they're already validated working and
# touching them for a marginal build-time gain isn't worth the risk.
ARG BUILD_PLATFORM=linux/amd64
FROM --platform=${BUILD_PLATFORM} nikolaik/python-nodejs:python3.11-nodejs20

RUN pip install --no-cache-dir pypdf pdfplumber

# ---------------------------------------------------------------------------
# System package layer — grouped into one apt-get run for build-cache
# efficiency. Base image is Debian-derived; package names below assume
# standard Debian repos are configured (should be true by default).
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    # --- Reverse engineering / binary analysis ---
    openjdk-21-jdk \
    # VERIFY: Ghidra's exact minimum/maximum supported JDK version varies
    # by release — check the specific Ghidra release notes and adjust
    # this version if the install fails or Ghidra refuses to launch.
    checksec \
    ltrace \
    strace \
    binutils \
    # --- Password / credential attacks ---
    hydra \
    fcrackzip \
    john \
    hashcat \
    # --- Web application security ---
    dirb \
    sqlmap \
    # dirbuster dropped: confirmed not packaged in this repo config at
    # all (build-tested, not just flagged as uncertain). dirb already
    # covers similar directory-brute-forcing ground; not worth a fragile
    # manual .jar install for a largely superseded tool.
    # --- Network ---
    netcat-openbsd \
    # --- Forensics / steganography ---
    binwalk \
    libimage-exiftool-perl \
    steghide \
    foremost \
    tesseract-ocr \
    ruby-full \
    # needed for zsteg (Ruby gem, installed below)
    perl \
    git \
    # needed for nikto (cloned from source below)
    && rm -rf /var/lib/apt/lists/*

# radare2 — confirmed NOT available via apt in this image's repo config
# (build-tested). Installing via the project's own official install
# script instead, which is genuinely the more commonly recommended path
# regardless of distro, since distro-packaged radare2 tends to lag behind
# anyway.
#
# CONFIRMED (build-tested): install.sh cannot simply be piped via
# `curl | bash` — it references sibling files (./sys/build.sh) via
# relative paths that only resolve when run from inside a real clone of
# the repo. Cloning first, then running the script from within the
# checkout, matches the tool's actual documented usage.
RUN git clone --depth 1 https://github.com/radareorg/radare2 /opt/radare2 \
    && cd /opt/radare2 \
    && sys/install.sh --install

# nikto — confirmed NOT available via apt in this image's repo config
# (build-tested). It's a plain Perl script with no build step; cloning
# directly is simpler and more portable than relying on a distro package.
RUN git clone --depth 1 https://github.com/sullo/nikto /opt/nikto \
    && ln -s /opt/nikto/program/nikto.pl /usr/local/bin/nikto \
    && chmod +x /opt/nikto/program/nikto.pl

# ---------------------------------------------------------------------------
# .NET SDK + ilspycmd — cross-platform .NET decompiler, genuinely usable
# on Linux despite decompiling Windows binaries (see design doc's core
# insight). Using Microsoft's official install script rather than apt,
# since distro-packaged .NET SDK versions lag badly.
# ---------------------------------------------------------------------------
RUN curl -sSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh \
    && chmod +x /tmp/dotnet-install.sh \
    && /tmp/dotnet-install.sh --channel LTS --install-dir /usr/share/dotnet \
    && ln -s /usr/share/dotnet/dotnet /usr/local/bin/dotnet \
    && rm /tmp/dotnet-install.sh
# VERIFY: dotnet-install.sh is fetched from Microsoft's own dot.net domain,
# not one of the domains this analysis was written from could reach —
# confirm this URL/script still resolves and behaves as expected at
# actual build time. Microsoft's install script does auto-detect host
# architecture (confirmed arm64 here), so this should self-adjust
# correctly, but worth confirming rather than assuming given everything
# else in this file that assumed x86-64 turned out wrong.

ENV PATH="${PATH}:/opt/dotnet-tools"
# CONFIRMED BUG, found via real deployment testing: Hermes's actual
# runtime config sets docker_run_as_host_user: true, meaning the live
# sandbox runs as the host user's UID (501), not root — but the build
# above runs as root, and `dotnet tool install --global` installs into
# $HOME/.dotnet/tools, which resolves to /root/.dotnet/tools during the
# build. /root is mode 0700 by default — completely inaccessible to any
# non-root UID, not just missing from PATH. Using --tool-path instead of
# --global installs into an arbitrary, explicitly-chosen directory,
# independent of $HOME entirely, avoiding this trap.
RUN mkdir -p /opt/dotnet-tools \
    && dotnet tool install --tool-path /opt/dotnet-tools ilspycmd \
    && chmod -R a+rX /opt/dotnet-tools \
    && ln -s /opt/dotnet-tools/ilspycmd /usr/local/bin/ilspycmd
# SECOND CONFIRMED BUG, found via the same testing round: Hermes's own
# command-execution mechanism resets PATH to Debian's plain system
# default (/usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games) on
# every call — it does NOT inherit this image's custom ENV PATH
# additions at all. This is a general finding, not ilspycmd-specific:
# radare2/rp-lin/nikto/zap all already worked correctly through Hermes
# specifically because each was given an explicit symlink into
# /usr/local/bin (which IS in that default path) rather than relying on
# a PATH extension — ilspycmd was the one tool that had only a PATH
# addition and no symlink, and it was the one that failed. The symlink
# above fixes this the same way. Any future tool added to this image
# that isn't already installed directly into a standard bin directory
# needs the same explicit symlink treatment, not just an ENV PATH line —
# a PATH addition alone will silently not work when invoked through
# Hermes, even though it works fine under a normal interactive shell.

# ---------------------------------------------------------------------------
# Ghidra + PyGhidra — the core static-analysis engine, validated
# end-to-end on the Windows side of this project (see
# skills/security/windows-binary-analysis/scripts/pyghidra_tool.py for
# the working reference implementation of ghidra-inventory/ghidra-decompile).
# The install pattern below mirrors what was proven to work there.
# ---------------------------------------------------------------------------
ARG GHIDRA_VERSION=12.1.2
ARG GHIDRA_DATE=20260605
ARG GHIDRA_SHA256=b62e81a0390618466c019c60d8c2f796ced2509c4c1aea4a37644a77272cf99d
# Confirmed via cross-referencing two independent sources (a Chocolatey
# package definition and Repology's package-tracking data), not a
# placeholder guess — the previous version/date combination in this file
# did not correspond to a real release asset, which produced a silent
# failure: curl saved GitHub's 404/redirect response as if it were the
# real zip, and the error only surfaced two steps later at unzip time.
# Checksum verification added below specifically to catch that failure
# mode immediately and clearly if it ever recurs (e.g. once this pinned
# version becomes stale and needs updating again).
RUN curl -sSL -o /tmp/ghidra.zip \
    "https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_${GHIDRA_VERSION}_build/ghidra_${GHIDRA_VERSION}_PUBLIC_${GHIDRA_DATE}.zip" \
    && echo "${GHIDRA_SHA256}  /tmp/ghidra.zip" | sha256sum -c - \
    && unzip -q /tmp/ghidra.zip -d /opt \
    && mv /opt/ghidra_${GHIDRA_VERSION}_PUBLIC /opt/ghidra \
    && rm /tmp/ghidra.zip
ENV GHIDRA_INSTALL_DIR=/opt/ghidra
ENV PATH="${PATH}:/opt/ghidra/support"
# Defensive symlink, added after finding the PATH-vs-symlink bug above.
# pyghidra itself doesn't need analyzeHeadless on PATH at all (it talks
# to Ghidra directly via Python bindings) — this is purely so the same
# silent failure doesn't resurface later if anything ever tries to
# invoke Ghidra's CLI tools by bare name through Hermes.
RUN ln -s /opt/ghidra/support/analyzeHeadless /usr/local/bin/analyzeHeadless

# Install pyghidra from Ghidra's own bundled wheel — same offline-install
# pattern confirmed working on the Windows golden template, no external
# PyPI dependency for this specific package.
RUN pip install --no-cache-dir --no-index -f /opt/ghidra/Ghidra/Features/PyGhidra/pypkg/dist pyghidra

# ---------------------------------------------------------------------------
# Python-based tools (pip)
# ---------------------------------------------------------------------------
RUN pip install --no-cache-dir \
    pwntools \
    ROPgadget \
    scapy \
    stegoveritas \
    pefile \
    pyelftools \
    capstone \
    beautifulsoup4

# stegoveritas has its own post-install dependency-fetch step for some
# submodules — VERIFY whether `stegoveritas_install_deps` (or similar,
# check current package docs) needs to be run explicitly after pip install.

# ---------------------------------------------------------------------------
# Ruby-based tools (gem) — zsteg, one_gadget
# ---------------------------------------------------------------------------
RUN gem install zsteg one_gadget

# ---------------------------------------------------------------------------
# Node-based tools (npm) — base image already has Node 20
# ---------------------------------------------------------------------------
RUN npm install -g js-beautify

# ---------------------------------------------------------------------------
# Tools with no clean apt/pip/gem/npm path — direct binary/release download
# ---------------------------------------------------------------------------

# stegseek — headless steghide password cracker. No prebuilt arm64
# package exists: the official .deb release is confirmed x86-64 only
# (matched the exact architecture-mismatch error from a real build log —
# apt reported "stegseek:amd64" with unsatisfiable amd64-only
# dependencies on this arm64 host). Confirmed via three independent,
# converging sources (the project's own README, BlackArch's build
# definition, and PureOS's Debian source package) that stegseek builds
# successfully on arm64/aarch64 from source via cmake — building from
# source instead of relying on the architecture-mismatched prebuilt
# package. radare2's earlier successful from-source build already
# confirms this image has a working C/C++ compiler toolchain.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmhash-dev libmcrypt-dev libjpeg62-turbo-dev zlib1g-dev cmake \
    && rm -rf /var/lib/apt/lists/* \
    && git clone --depth 1 https://github.com/RickdeJager/stegseek /opt/stegseek \
    && cmake -B /opt/stegseek/build -S /opt/stegseek \
        -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /opt/stegseek/build \
    && cmake --install /opt/stegseek/build

# rp++ (rp-lin) — ROP gadget finder. No standard apt package, and the
# only prebuilt GitHub release binaries are platform-specific
# (rp-lin-x64, rp-osx, rp-win) with nothing built for arm64. Confirmed
# via the project's commit history that arm64/aarch64 support exists in
# the source itself (dedicated "Add __aarch64__ support" commits), so
# building from source using the project's own documented build script
# is the correct path here, same pattern as radare2 and stegseek above.
#
# CONFIRMED (build-tested): the build script must be run from INSIDE
# src/build, not invoked via a longer path from the repo root — its
# internal relative-path logic assumes that starting location, and
# running it from elsewhere produced "source directory /opt does not
# contain CMakeLists.txt" (the script's own cmake invocation resolved
# the wrong directory as a result).
# CONFIRMED (build-tested): the build script's CMake configuration uses
# Ninja as its generator — install it first. The "compiler not set"
# errors seen without it were very likely cascading noise from that same
# failed configure step, not a genuinely missing toolchain (radare2's
# earlier successful from-source build already proved a working C/C++
# compiler exists in this image).
RUN apt-get update && apt-get install -y --no-install-recommends ninja-build \
    && rm -rf /var/lib/apt/lists/* \
    && git clone --depth 1 https://github.com/0vercl0k/rp /opt/rp++ \
    && cd /opt/rp++/src/build \
    && chmod u+x ./build-release.sh \
    && ./build-release.sh \
    && ln -s "$(find /opt/rp++ -maxdepth 4 -type f -name 'rp-lin*' -executable | head -1)" /usr/local/bin/rp-lin
# VERIFY: the find/symlink step guesses at the built binary's output
# path and name pattern based on documented examples (e.g. rp-lin-x64)
# rather than a confirmed arm64 output filename — check what the actual
# build produces and adjust the symlink target if this doesn't resolve.

# ---------------------------------------------------------------------------
# OWASP ZAP — headless-capable web-proxy tool (chosen over Burp per the
# design doc).
#
# CONFIRMED (build-tested): the previous approach assumed ZAP ships a
# self-extracting Linux installer script at a stable "latest" URL — this
# does not exist. ZAP's actual Linux distribution is a plain,
# version-named tarball (e.g. ZAP_2.17.0_Linux.tar.gz); "latest" is not
# a valid download alias for it at all, since the version number is
# baked into the filename itself. The earlier curl call downloaded
# GitHub's 404 response body and tried to execute it as a shell script.
#
# Pinning a specific confirmed-real version here, same pattern as
# Ghidra above — this WILL go stale eventually and need updating.
# Java 17+ is ZAP's documented requirement; already satisfied by the
# openjdk-21-jdk installed earlier in this file for Ghidra, no separate
# Java install needed here.
ARG ZAP_VERSION=2.17.0
RUN curl -sSL -o /tmp/zap.tar.gz \
    "https://github.com/zaproxy/zaproxy/releases/download/v${ZAP_VERSION}/ZAP_${ZAP_VERSION}_Linux.tar.gz" \
    && mkdir -p /opt/zap \
    && tar -xzf /tmp/zap.tar.gz -C /opt/zap --strip-components=1 \
    && ln -s /opt/zap/zap.sh /usr/local/bin/zap \
    && rm /tmp/zap.tar.gz

WORKDIR /workspace

# Defensive blanket fix, added after finding the ilspycmd permission bug
# above via real deployment testing under Hermes's actual
# docker_run_as_host_user: true runtime config. /opt generally defaults
# to world-readable permissions already, so this is likely a no-op for
# most of what's installed above — but cheap insurance against any other
# install step having silently landed in a non-root-accessible state,
# rather than assuming everything else is fine without having explicitly
# verified each one under the real non-root runtime user.
RUN chmod -R a+rX /opt
