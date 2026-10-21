FROM postgres:18.6-trixie@sha256:edb0c08dc33befd0262ab3b5cf1c4f3f8cfd8b09e88c05b45adee29d6761ddb6
USER root
# The service runs directly as postgres; no root-to-user helper is needed.
# Remove the upstream image's unused example TLS key rather than distribute it.
RUN rm -f /usr/local/bin/gosu /etc/ssl/private/ssl-cert-snakeoil.key /etc/ssl/certs/ssl-cert-snakeoil.pem
USER postgres
