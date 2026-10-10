ARG BASE_IMAGE
FROM ${BASE_IMAGE}
# Keep the inspected OS/glibc base; select the compiler independently.
ARG RUST_TOOLCHAIN=1.99.0
RUN rustup toolchain install ${RUST_TOOLCHAIN} --profile minimal && rustup default ${RUST_TOOLCHAIN}
ENV RUSTUP_TOOLCHAIN=${RUST_TOOLCHAIN}
ARG TARGET=x86_64-unknown-linux-gnu
# Explicit target libraries, e.g. libssl-dev:amd64 on an arm64 builder.
ARG EXTRA_PACKAGES=""
# Compile on the Docker host's native CPU; only the output target is crossed.
RUN case "$TARGET" in \
      x86_64-unknown-linux-gnu) packages='gcc-x86-64-linux-gnu libc6-dev-amd64-cross'; dpkg --add-architecture amd64 ;; \
      aarch64-unknown-linux-gnu) packages='gcc-aarch64-linux-gnu libc6-dev-arm64-cross'; dpkg --add-architecture arm64 ;; \
      *) echo "unsupported target: $TARGET" >&2; exit 1 ;; \
    esac && apt-get update && apt-get install -y --no-install-recommends $packages pkg-config ${EXTRA_PACKAGES} \
    && rm -rf /var/lib/apt/lists/* && rustup target add "$TARGET"
ENV CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=x86_64-linux-gnu-gcc \
    CC_x86_64_unknown_linux_gnu=x86_64-linux-gnu-gcc \
    AR_x86_64_unknown_linux_gnu=x86_64-linux-gnu-ar \
    CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc \
    CC_aarch64_unknown_linux_gnu=aarch64-linux-gnu-gcc \
    AR_aarch64_unknown_linux_gnu=aarch64-linux-gnu-ar \
    CARGO_TARGET_DIR=/cache/target
WORKDIR /work
# pkg-config must inspect target libraries, never libraries for the Docker host CPU.
RUN printf '#!/bin/sh\nexport PKG_CONFIG_LIBDIR=/usr/lib/x86_64-linux-gnu/pkgconfig:/usr/share/pkgconfig\nexec pkg-config "$@"\n' > /usr/local/bin/x86_64-pkg-config \
 && printf '#!/bin/sh\nexport PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig\nexec pkg-config "$@"\n' > /usr/local/bin/aarch64-pkg-config \
 && chmod +x /usr/local/bin/*-pkg-config
ENV PKG_CONFIG_x86_64_unknown_linux_gnu=/usr/local/bin/x86_64-pkg-config \
    PKG_CONFIG_aarch64_unknown_linux_gnu=/usr/local/bin/aarch64-pkg-config
