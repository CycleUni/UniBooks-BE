#!/usr/bin/env bash
# Vercel build step (vercel.json: @vercel/static-build on this file, distDir
# staticfiles_build).
#
# `set -eu` is load-bearing, not boilerplate. Without it a failing
# `migrate` was swallowed: sh keeps going after a non-zero command and the
# script's exit code is only the *last* one, so collectstatic succeeding was
# enough to turn the whole build green. A deploy then shipped a function whose
# code expected columns the database did not have — which surfaced as Google
# sign-in answering 401 (GoogleLoginView catches everything and reports
# auth.errInvalidToken) rather than as a failed deploy.
#
# Note this runs in the @vercel/static-build container, which is a separate
# build from the @vercel/python one that produces the function. Vercel knows
# nothing about the migration; failing loudly here is the only thing stopping
# a schema-behind deploy from going live. /api/v1/core/healthz/ reports
# unapplied_migrations, so check it after a deploy.
# (`-o pipefail` is deliberately left out: there are no pipes here, and it is
# not POSIX — it would abort the build outright if Vercel invokes this with
# `sh` rather than through the shebang.)
set -eu

uv venv
uv pip install -r requirements.txt
uv run python manage.py migrate
uv run python manage.py collectstatic --noinput
