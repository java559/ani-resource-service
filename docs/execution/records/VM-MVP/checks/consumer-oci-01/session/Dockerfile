FROM gcr.io/distroless/static-debian12@sha256:52dcfbabb7457ea47c82f6e13af8c8a4a1d9f7b0145142b3ecab20f2b888411d
COPY --chown=65532:65532 --chmod=0555 session-gateway /session-gateway
USER 65532:65532
WORKDIR /
LABEL org.opencontainers.image.source="https://github.com/zhangzhe-ctrl/ani-session-gateway" \
      org.opencontainers.image.revision="c485419ab879fed9a2fad3a064fd328d829a2671" \
      org.opencontainers.image.version="vm-mvp-c485419ab879-01a168ce"
ENTRYPOINT ["/session-gateway"]
