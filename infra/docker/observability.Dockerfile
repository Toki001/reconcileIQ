ARG BASE_IMAGE=otel/opentelemetry-collector-contrib:0.163.2@sha256:7f22d2a6748110ffb6a59bc0df448bccbf501cdb667d610ede4c61d3863d5849
FROM golang:1.26-bookworm@sha256:632dff7351799a5a9954a48dc06670ade72ee44e8c351cdc0e5a57af2fea21af AS health
WORKDIR /src
COPY infra/docker/healthcheck.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/healthcheck healthcheck.go \
    && mkdir -p /out/tempo && chown 10001:10001 /out/tempo
FROM ${BASE_IMAGE}
COPY --from=health /out/healthcheck /healthcheck
COPY --from=health --chown=10001:10001 /out/tempo /var/tempo
ARG RUN_UID=10001
USER ${RUN_UID}:${RUN_UID}
