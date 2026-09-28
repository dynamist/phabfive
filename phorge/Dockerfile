# PHP with Apache, one stage per series, the PHP build arg picks one (see
# scripts/phorge-build-args.sh). Phorge turns a deprecation raised while it loads
# a class into an error, so an older release only starts on a PHP from before
# its code was deprecated. Renovate keeps each stage on its series.
ARG PHP=8.5
FROM php:8.5-apache AS php-8.5
FROM php:8.4-apache AS php-8.4
FROM php:8.3-apache AS php-8.3
FROM php:8.0-apache AS php-8.0

FROM php-${PHP}

WORKDIR /app

ADD --chmod=0755 https://github.com/mlocati/docker-php-extension-installer/releases/latest/download/install-php-extensions /usr/local/bin/

RUN install-php-extensions mbstring iconv mysqli curl pcntl gd zip apcu opcache

RUN apt update && apt install -y git default-mysql-client python3-pygments

# Enable Apache modules
RUN a2enmod rewrite

# Phorge (or Phabricator) and Arcanist at the commits of a branch or tag that
# `make phorge-image VERSION=...` resolves (see scripts/phorge-build-args.sh).
# A git checkout rather than an archive, Phorge reads its version from it.
ARG PHORGE_VERSION
ARG PHORGE_REPO
ARG PHORGE_COMMIT
ARG ARCANIST_REPO
ARG ARCANIST_COMMIT
RUN test -n "$PHORGE_COMMIT" -a -n "$ARCANIST_COMMIT" || \
      { echo "Build with make phorge-image, it passes the commits to check out" >&2; exit 1; } && \
    for checkout in "phorge $PHORGE_REPO $PHORGE_COMMIT" "arcanist $ARCANIST_REPO $ARCANIST_COMMIT"; do \
      set -- $checkout && \
      git init -q "/app/$1" && \
      git -C "/app/$1" fetch -q --depth 1 "$2.git" "$3" && \
      git -C "/app/$1" checkout -q FETCH_HEAD || exit 1; \
    done && \
    git config --system --add safe.directory /app/phorge && \
    git config --system --add safe.directory /app/arcanist
ENV PHORGE_VERSION=${PHORGE_VERSION} PHORGE_REPO=${PHORGE_REPO} ARCANIST_REPO=${ARCANIST_REPO}

# Set up Apache and PHP for Phorge
COPY phorge.conf /etc/apache2/conf-enabled/phorge.conf
COPY phorge.ini /usr/local/etc/php/conf.d/phorge.ini

# Copy and set up entrypoint script
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Sample data seeder, run by the entrypoint (see phorge/seed/)
COPY seed/ /usr/local/share/phabfive-seed/

# banner.sh prints the credentials summary, also run by `make creds`
COPY lib/ /usr/local/bin/lib/
RUN chmod +x /usr/local/bin/lib/*

# Create configuration directory and storage directories
RUN mkdir -p /app/phorge/conf/local /app/repo /app/files && \
    chown -R www-data:www-data /app/phorge /app/repo /app/files

# Expose port 80
EXPOSE 80

# Entry point
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
