#!/usr/bin/env bash
# The environment `plane.settings.common` needs, shared by the test runner and manage.py.
#
# Sourced, never executed. Kept in one file because the two callers must agree: a
# migration validated by make-migration.sh has to be validated against the same
# database and settings the suite builds from the models.
#
# Every variable here earns its place. The ones with a comment cost a debugging session
# each -- do not remove one without reading why it is there.

export DJANGO_SETTINGS_MODULE=plane.settings.test
export POSTGRES_DB=${POSTGRES_DB:-plane}
export POSTGRES_USER=plane
export POSTGRES_PASSWORD=plane
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT="${PG_PORT:-5433}"
export REDIS_HOST=127.0.0.1
export REDIS_PORT="${REDIS_PORT:-6380}"
export REDIS_URL="redis://127.0.0.1:${REDIS_PORT}/0"

# Celery broker. Without this, plane.settings.common defaults CELERY_BROKER_URL to
# amqp:// and every view that fires a task -- issue_activity.delay(), the notification
# tasks, project creation -- raises ConnectionRefusedError and turns a 200 into a 500.
# That alone accounted for 36 baseline failures.
#
# Redis rather than CELERY_TASK_ALWAYS_EAGER on purpose: eager mode executes tasks
# inline, inside the caller's transaction, which is not how they run in production and
# would hide ordering bugs. Pointing the broker at Redis keeps .delay() a real enqueue
# that returns immediately; nothing consumes the queue, which is what a test run wants.
export AMQP_URL="redis://127.0.0.1:${REDIS_PORT}/1"

export SECRET_KEY=sandbox-test-secret-key
export DEBUG=1
export USE_MINIO=1
export AWS_ACCESS_KEY_ID=sandbox
export AWS_SECRET_ACCESS_KEY=sandbox
export AWS_S3_BUCKET_NAME=uploads
export AWS_S3_ENDPOINT_URL=http://127.0.0.1:9000
export FILE_SIZE_LIMIT=5242880
export WEB_URL=http://localhost:3000
export APP_DOMAIN=localhost
export CORS_ALLOWED_ORIGINS=http://localhost:3000

# The magic-link auth provider refuses to initialise with no EMAIL_HOST and returns
# 400 SMTP_NOT_CONFIGURED, failing the 12 tests in contract/app/test_authentication.py.
# Nothing is actually sent: plane.settings.test swaps in Django's locmem backend.
export EMAIL_HOST=localhost
export EMAIL_PORT=25
export EMAIL_FROM="Test <test@plane.so>"
export ENABLE_MAGIC_LINK_LOGIN=1
