#!/bin/bash

# set up to run pytest using a real postgres database, using docker image
export PGHOST=localhost PGPORT=9432 PGUSER=postgres PGPASSWORD=mysecretpassword
CNAME="sqlalchemy-apispec-test"

# start postgresql
docker run --rm \
       --name $CNAME \
       -v $CNAME:/var/lib/postgresql/data \
       -e POSTGRES_PASSWORD=$PGPASSWORD \
       -p ${PGPORT}:5432 \
       -d postgres:17-alpine

# set trap on exit to clean up docker
docker-stop() {
    docker stop $CNAME
    docker volume rm $CNAME
}

trap docker-stop 0

uv run pytest "$@"
