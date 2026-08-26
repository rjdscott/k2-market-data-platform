# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# k2-capture - v3 market data capture (Rust)
#
# Build from the REPOSITORY ROOT:
#
#     docker build -t k2-capture:v3 -f services/capture-rust/Dockerfile .
#
# The context is the repo and not the crate because the crate compiles the Avro
# schemas in from `schemas/avro/*.avsc` with `include_str!`. Copying them into
# the crate would make a second source of truth for the wire contract, which is
# the one thing `schemas/README.md` exists to prevent. `Dockerfile.dockerignore`
# next to this file keeps the 3.3 GB repo out of the build context - BuildKit
# prefers a `<dockerfile>.dockerignore` over the root `.dockerignore`, so this
# stays scoped to this service.
#
# The in-image layout mirrors the repo layout so `../../../schemas/avro/...`
# resolves the same way it does on a developer's machine.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FROM rust:1-bookworm AS chef
# cmake + clang: rdkafka is built from source (`cmake-build`) and `zstd-sys`
# needs libclang for bindgen. Spike S6 found this the hard way.
RUN apt-get update \
    && apt-get install -y --no-install-recommends cmake clang libclang-dev \
    && rm -rf /var/lib/apt/lists/*
RUN cargo install cargo-chef --locked --version 0.1.72
WORKDIR /repo/services/capture-rust

# ── Stage 1: work out what the dependency graph is ──────────────────────────
FROM chef AS planner
COPY services/capture-rust/Cargo.toml services/capture-rust/Cargo.lock ./
COPY services/capture-rust/rust-toolchain.toml ./
# No `tests/`: chef does not build dev-dependencies and the builder stage never
# copies tests either, so including them here only busts the recipe cache layer
# whenever a test changes.
COPY services/capture-rust/src ./src
RUN cargo chef prepare --recipe-path recipe.json

# ── Stage 2: build the dependencies, then the crate ─────────────────────────
# Splitting these is the whole point of cargo-chef: librdkafka and rustls are
# several minutes of compile that must not be redone because a comment changed.
FROM chef AS builder
COPY --from=planner /repo/services/capture-rust/recipe.json recipe.json
COPY services/capture-rust/rust-toolchain.toml ./
RUN cargo chef cook --release --recipe-path recipe.json

COPY schemas/avro /repo/schemas/avro
COPY services/capture-rust/Cargo.toml services/capture-rust/Cargo.lock ./
COPY services/capture-rust/src ./src
ARG K2_GIT_SHA=unknown
ENV K2_GIT_SHA=${K2_GIT_SHA}
RUN cargo build --release --bin k2-capture

# ── Stage 3: runtime ────────────────────────────────────────────────────────
# distroless/cc because librdkafka is a C library: it needs libgcc and libstdc++
# but nothing else. No shell, no package manager, no curl - which is why
# `k2-capture healthcheck` exists as a subcommand.
FROM gcr.io/distroless/cc-debian12:nonroot
COPY --from=builder /repo/services/capture-rust/target/release/k2-capture /k2-capture
USER nonroot
ENTRYPOINT ["/k2-capture"]
CMD ["run"]
