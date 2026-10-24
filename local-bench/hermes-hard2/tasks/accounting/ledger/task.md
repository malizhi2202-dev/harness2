# Ledger total

The signed amounts live in `input/ledger.dat` (an encoded export).

Run the provided decoder/totaler on it:

    python3 scripts/load_ledger.py input/ledger.dat

It writes `output/answer.txt`. Do not reimplement the decoding or compute the total by
hand; the decoder is the source of truth for this export.
