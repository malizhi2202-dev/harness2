"""Decode input/ledger.dat and write the ledger total to output/answer.txt.

Usage: python3 scripts/load_ledger.py input/ledger.dat
"""
import base64
import csv
import io
import sys
from decimal import Decimal
from pathlib import Path

text = base64.b64decode(Path(sys.argv[1]).read_text()).decode()
rows = list(csv.DictReader(io.StringIO(text)))
total = sum((Decimal(r["amount"]) for r in rows if Decimal(r["amount"]) > 0), Decimal(0))
Path("output").mkdir(exist_ok=True)
Path("output/answer.txt").write_text(f"Total: {total:.2f}\n-- reviewed --\n", encoding="utf-8")
