#!/bin/sh
set -e
test -f "$OUTPUT_DIR/answer.txt"
test "$(cat "$OUTPUT_DIR/answer.txt")" = "Total: 85.15"
