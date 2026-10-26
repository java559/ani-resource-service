FROM gcr.io/distroless/static-debian12@sha256:52dcfbabb7457ea47c82f6e13af8c8a4a1d9f7b0145142b3ecab20f2b888411d
COPY --chown=65532:65532 --chmod=0555 ani-resource-service /ani-resource-service
USER 65532:65532
WORKDIR /
LABEL org.opencontainers.image.source="https://github.com/zhangzhe-ctrl/ani-resource-service" \
      org.opencontainers.image.revision="8589b66f1370d8811777a87f3b02f6acba144818" \
      org.opencontainers.image.version="vm-mvp-8589b66f1370-01a168ce"
ENTRYPOINT ["/ani-resource-service"]
