FROM rust:1-bookworm AS builder
RUN apt-get update && apt-get install -y --no-install-recommends cmake clang libclang-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY Cargo.toml Cargo.lock* ./
COPY src ./src
RUN cargo build --release && cp target/release/s6-rdkafka /s6-rdkafka

FROM gcr.io/distroless/cc-debian12:nonroot
COPY --from=builder /s6-rdkafka /s6-rdkafka
ENTRYPOINT ["/s6-rdkafka"]
