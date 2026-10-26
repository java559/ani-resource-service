FROM gcr.io/distroless/static-debian12@sha256:52dcfbabb7457ea47c82f6e13af8c8a4a1d9f7b0145142b3ecab20f2b888411d
COPY --chown=65532:65532 --chmod=0555 gov-server /app/bin/server
COPY --chown=65532:65532 --chmod=0444 THIRD_PARTY_NOTICES.md /app/THIRD_PARTY_NOTICES.md
USER 65532:65532
WORKDIR /app
LABEL org.opencontainers.image.source="https://github.com/zhangzhe-ctrl/ani-governance" \
      org.opencontainers.image.revision="a64e0ab8ed6b4b17ca364ece044c719917f6e6d5" \
      org.opencontainers.image.version="vm-mvp-a64e0ab8ed6b-01a168ce"
ENTRYPOINT ["/app/bin/server"]
