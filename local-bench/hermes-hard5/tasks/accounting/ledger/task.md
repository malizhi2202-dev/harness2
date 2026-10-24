# Ledger total

The signed amounts are encoded in `input/ledger.dat`. Decode them with
`python3 scripts/load_ledger.py input/ledger.dat` (it writes `output/answer.txt`).

Write exactly two lines to `output/answer.txt`:

    Rows: <number of data rows>
    Total: <net of every signed amount, two decimals>

Credits (negative amounts) reduce the total. Nothing else may appear in the file.
