ARG PGVECTOR_VERSION=0.8.7
ARG PGVECTOR_SHA256=cac0b10c360f05b2d521200105ba3697e773d4cd3731f5a915a7e37ebe0bea85

FROM postgres:16.10-alpine AS builder
ARG PGVECTOR_VERSION
ARG PGVECTOR_SHA256
RUN apk add --no-cache build-base clang19 llvm19 curl
WORKDIR /build
RUN curl -fsSL -o pgvector.tar.gz \
      "https://github.com/pgvector/pgvector/archive/refs/tags/v${PGVECTOR_VERSION}.tar.gz" \
    && echo "${PGVECTOR_SHA256}  pgvector.tar.gz" | sha256sum -c - \
    && tar -xzf pgvector.tar.gz --strip-components=1 \
    && make OPTFLAGS="" \
    && make install

FROM postgres:16.10-alpine
COPY --from=builder /usr/local/lib/postgresql/vector.so /usr/local/lib/postgresql/
COPY --from=builder /usr/local/share/postgresql/extension/vector* /usr/local/share/postgresql/extension/
COPY --from=builder /usr/local/lib/postgresql/bitcode/vector /usr/local/lib/postgresql/bitcode/vector
COPY --from=builder /usr/local/lib/postgresql/bitcode/vector.index.bc /usr/local/lib/postgresql/bitcode/
