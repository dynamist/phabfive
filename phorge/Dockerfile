# PHP with Apache, tag kept current by Renovate (Phorge requires PHP 7.2.25+)
FROM php:8.5-apache

WORKDIR /app

ADD --chmod=0755 https://github.com/mlocati/docker-php-extension-installer/releases/latest/download/install-php-extensions /usr/local/bin/

RUN install-php-extensions mbstring iconv mysqli curl pcntl gd zip apcu opcache

RUN apt update && apt install -y git default-mysql-client python3-pygments

# Enable Apache modules
RUN a2enmod rewrite

# Phorge and Arcanist from the GitHub mirrors, at the commits of a branch or tag
# that `make phorge-image VERSION=...` resolves (see scripts/phorge-build-args.sh).
# A git checkout rather than an archive, Phorge reads its version from it.
ARG PHORGE_VERSION
ARG PHORGE_COMMIT
ARG ARCANIST_COMMIT
RUN test -n "$PHORGE_COMMIT" -a -n "$ARCANIST_COMMIT" || \
      { echo "Build with make phorge-image, it passes the commits to check out" >&2; exit 1; } && \
    for repo in phorge:$PHORGE_COMMIT arcanist:$ARCANIST_COMMIT; do \
      git init -q "/app/${repo%%:*}" && \
      git -C "/app/${repo%%:*}" fetch -q --depth 1 "https://github.com/phorgeit/${repo%%:*}.git" "${repo#*:}" && \
      git -C "/app/${repo%%:*}" checkout -q FETCH_HEAD || exit 1; \
    done && \
    git config --system --add safe.directory /app/phorge && \
    git config --system --add safe.directory /app/arcanist
ENV PHORGE_VERSION=${PHORGE_VERSION}

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
