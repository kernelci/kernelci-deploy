# Static (musl) build, so the binary does not care which glibc the runtime
# image has.
FROM rust:1-alpine AS build
RUN apk add --no-cache musl-dev
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release --locked

# GKE access tokens come from `gcloud config config-helper`, run by the
# exporter, using the gcloud credentials mounted at /root/.config/gcloud.
# No gke-gcloud-auth-plugin needed. The AKS contexts carry client
# certificates and need nothing extra.
FROM gcr.io/google.com/cloudsdktool/google-cloud-cli:slim
WORKDIR /root
COPY --from=build /src/target/release/kci-k8s-exporter-rs /usr/local/bin/kci-k8s-exporter-rs
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/kci-k8s-exporter-rs"]
