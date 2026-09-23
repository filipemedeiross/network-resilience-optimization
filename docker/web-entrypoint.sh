#!/bin/sh
set -eu

python manage.py migrate --noinput
python manage.py configure_sqlite
python manage.py collectstatic --noinput

exec "$@"
