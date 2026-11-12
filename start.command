#!/bin/sh
set -eu
DOUYIN_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$DOUYIN_ROOT"
if [ -x .venv/bin/python ]; then
    DOUYIN_INTERPRETER=.venv/bin/python
else
    DOUYIN_INTERPRETER=${DOUYIN_PYTHON:-python3}
fi
exec "$DOUYIN_INTERPRETER" -m douyin_engine serve --open "$@"
