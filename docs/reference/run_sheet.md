# `run_sheet`

Sets `SQR` / `SQRP` and the i7/i5 barcodes of samples already in noxDB
from the sequencing run sheet (`Overview_SQRs`, tab `All_SQRs`, exported
as CSV). New samples get these values through the import instead.

The command wraps this module. It only reports unless given `--commit`:

```bash
# what would change, with a per-sample CSV to review
python scripts/apply_run_sheet.py "Overview_SQRs(All_SQRs).csv" --out changes.csv

# write it (one transaction; refused while stored values differ from the sheet)
python scripts/apply_run_sheet.py "Overview_SQRs(All_SQRs).csv" --commit
```

`--project NAME` limits it to one project's samples. `--overwrite`
replaces stored values that differ from the sheet instead of refusing.

::: noxdb.run_sheet
