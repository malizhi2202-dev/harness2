import csv
import sys
from decimal import Decimal
from pathlib import Path

rows = list(csv.DictReader(open(sys.argv[1], encoding="utf-8")))
total = sum((Decimal(r["amount"]) for r in rows if Decimal(r["amount"]) > 0), Decimal(0))
Path("output").mkdir(exist_ok=True)
Path("output/answer.txt").write_text(f"Total: {total:.2f}\n", encoding="utf-8")
