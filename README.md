# Australian Government receipts, payments and gross debt

An interactive chart of the Australian Government's general government sector
receipts and payments (cash), revenue and expenses (accrual), and gross debt
for the latest 30 financial years. The data come from the historical tables
published with each Final Budget Outcome. GitHub Pages hosts the page and
GitHub Actions rebuilds it.

## How it works

- `build.py` downloads the newest Final Budget Outcome "Historical Australian
  Government Data" appendix from archive.budget.gov.au and reads three tables
  from it:
  - Table B.1: cash receipts and payments
  - Table B.5: face value of Australian Government Securities on issue
    (gross debt)
  - Table B.6: accrual revenue and expenses
  
  It saves the figures to `data/fiscal_history.csv` and writes the page to
  `docs/index.html`.
- Before overwriting the saved data, it checks the result: the years are
  continuous, the latest year is the expected one, and no historical value has
  moved by more than 5%. If anything fails, it keeps the last good CSV and
  notes this on the page.
- `.github/workflows/build.yml` runs on the 3rd of every month. A new Final
  Budget Outcome is released each September, so the October run picks it up.

## Setup (one time)

1. Create a new **public** GitHub repository and upload the *contents* of this
   folder, including the hidden `.github` folder (in Finder press Cmd+Shift+.
   to show it). If `.github` won't upload, use **Add file → Create new file**,
   name it `.github/workflows/build.yml`, and paste in the workflow.
2. Go to **Settings → Pages** and set Deploy from a branch → **main** →
   **/docs**.
3. Go to **Settings → Actions → General → Workflow permissions** and select
   **Read and write permissions**.
4. On the **Actions** tab, choose **Rebuild chart** in the left sidebar, then
   click **Run workflow**.

## Running locally

```bash
pip install -r requirements.txt
python build.py
```

## Options

- `YEARS_SHOWN` in `build.py` sets how many recent years appear (default 30).
  The CSV holds the full series back to 1970–71 (accrual from 1996–97).

## Source

Australian Government, Final Budget Outcome, Appendix B: Historical Australian
Government Data. © Commonwealth of Australia, CC BY 4.0.
