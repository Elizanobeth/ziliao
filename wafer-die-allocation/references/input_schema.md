# Input schemas

## URL request schema

The Agent platform can pass three table URLs in this form. The preprocessing script converts them into the normalized schema below.

```json
{
  "table_urls": {
    "table1": {"url": "https://example.com/wafer-detail.xlsx", "sheet": "WaferData"},
    "table2": "https://example.com/wafer-sale.csv",
    "table3": {"url": "https://example.com/package-ratio.xlsx", "sheet": "Ratio"}
  },
  "request_headers": {},
  "parameters": {
    "target_units": 10000,
    "tolerance": 0.20,
    "bin_grades": ["1", "2", "3"],
    "max_waste_per_custom_lot": 30,
    "allow_lot_reuse": false,
    "max_units_per_custom_lot": 20000,
    "max_lots_per_custom_lot": 5,
    "package": "P-100",
    "supplier": "Supplier-A"
  }
}
```

For signed URLs or private endpoints, put short-lived request headers inside the individual URL object or in `request_headers`. Do not include credentials in the final report. The preprocessor records only a sanitized source path and SHA-256.

Run:

```text
python3 scripts/preprocess_tables.py --input url_request.json --output normalized_payload.json
```

The result contains `table1`, `table2`, `table3`, `parameters`, and a `preprocess` object with `status`, source metadata, warnings, and errors.

## Normalized input schema

Pass a UTF-8 JSON object to `scripts/allocate_die.py`.

```json
{
  "table1": [
    {
      "PACKAGE": "P-100",
      "供应商": "Supplier-A",
      "Fab LotID": "LOT-001",
      "Bin Grade": "1",
      "Bin Quanity": 120,
      "T7 Code": "W-001",
      "Lot Wafer QTY": 2,
      "Create Date": "2026-01-03"
    }
  ],
  "table2": [
    {"Fab LotID": "LOT-001", "Wafer Sale": "N"}
  ],
  "table3": [
    {"PACKAGE": "P100", "供应商": "Supplier-A", "层数配比": "2:6"}
  ],
  "parameters": {
    "target_units": 20,
    "tolerance": 0.20,
    "bin_grades": ["1", "2", "3"],
    "max_waste_per_custom_lot": 30,
    "allow_lot_reuse": false,
    "max_units_per_custom_lot": 20000,
    "max_lots_per_custom_lot": 5,
    "package": "P-100",
    "supplier": "Supplier-A"
  }
}
```

The script accepts common aliases, including `Bin Quantity`/`Bin Quanity`, `Supplier`/`供应商`, and `Package`/`PACKAGE`. A row in table1 should represent one wafer-grade observation. Multiple rows for the same `T7 Code` are aggregated by grade. The input does not need any process or thickness column; the solver decides the role of each whole wafer.

`Lot Wafer QTY` is retained for validation/reporting only; the authoritative wafer count is the number of distinct `T7 Code` values in the Lot after filtering.
