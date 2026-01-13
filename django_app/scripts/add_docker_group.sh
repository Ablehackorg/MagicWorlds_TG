#!/bin/sh
DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)
groupadd -g $DOCKER_GID docker || true
usermod -aG $DOCKER_GID django
exec "$@"

