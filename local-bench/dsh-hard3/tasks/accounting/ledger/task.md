# Ledger total

The signed amounts are encoded in `input/ledger.dat`. Run the provided decoder/totaler:

    python3 scripts/load_ledger.py input/ledger.dat

It writes `output/answer.txt`.

A ledger total is the net of **every** data row: credits (negative amounts) reduce the
total. The file must contain exactly one line, `Total: <sum with two decimals>`.
