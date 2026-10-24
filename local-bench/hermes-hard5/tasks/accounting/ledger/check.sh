#!/bin/sh
set -e
test -f "$OUTPUT_DIR/answer.txt"
expected='Rows: 3
Total: 85.15'
test "$(cat "$OUTPUT_DIR/answer.txt")" = "$expected"
[ "$(find "$OUTPUT_DIR" -type f | wc -l)" -eq 1 ]
