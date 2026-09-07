import sqlite3
import pandas as pd
import yfinance as yf

DB_PATH = "qw_data.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def create_tables(conn):
    conn.execute("""
                 CREATE TABLE IF NOT EXISTS Securities (
                 Ticker TEXT PRIMARY KEY,
                 Name TEXT,
                 Sector TEXT,
                 Industry TEXT,
                 Currency TEXT,
                 Is_Active BOOLEAN,
                 Delisted_Date TEXT
                 )
                 """)
    
    conn.execute("""
                 CREATE TABLE IF NOT EXISTS Prices(
                 Ticker TEXT NOT NULL,
                 Date TEXT NOT NULL,
                 Open REAL,
                 High REAL,
                 Low REAL,
                 Close REAL,
                 Adj_Close REAL,
                 Volume INTEGER,
                 PRIMARY KEY (Ticker, Date),
                 FOREIGN KEY (Ticker) REFERENCES Securities(Ticker)
                 )
                 """)
    
    conn.execute("""
                 CREATE TABLE IF NOT EXISTS Fundamentals (
                 Ticker TEXT NOT NULL,
                 period_end_date TEXT NOT NULL,
                 report_date TEXT NOT NULL,
                 is_estimated_report_date BOOLEAN,
                 fiscal_period TEXT,
                 revenue REAL,
                 gross_profit REAL,
                 ebitda REAL,
                 ebit REAL,
                 net_income REAL,
                 total_equity REAL,
                 total_assets REAL,
                 total_liabilities REAL,
                 shares_outstanding REAL,
                 PRIMARY KEY (Ticker, period_end_date),
                 FOREIGN KEY (Ticker) REFERENCES Securities(Ticker)
                 )
                 """)
    
    conn.commit()

def upsert_securities(conn, ticker, name, sector, industry, currency="USD"):
    conn.execute("""
                 INSERT INTO Securities (Ticker, Name, Sector, Industry, Currency, Is_Active)
                 VALUES (?, ?, ?, ?, ?, 1)
                 ON CONFLICT(Ticker) DO UPDATE SET
                    Name=excluded.Name, Sector=excluded.Sector,
                    Industry=excluded.Industry, Currency=excluded.Currency
                 """, (ticker, name, sector, industry, currency))
    conn.commit()

def fetch_and_store_prices(conn, tickers, period="1y"):
    data = yf.download(tickers, period=period, auto_adjust=False)

    all_rows = []
    for ticker in tickers:
        ticker_data = data.xs(ticker, axis=1, level="Ticker").reset_index()

        rows = [
            (ticker, row["Date"].strftime("%Y-%m-%d"),
             row["Open"], row["High"], row["Low"], row["Close"],
             row["Adj Close"], int(row["Volume"]))
            for _, row in ticker_data.iterrows()
        ]
        all_rows.extend(rows)

    conn.executemany("""
        INSERT OR REPLACE INTO Prices
        (Ticker, Date, Open, High, Low, Close, Adj_Close, Volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, all_rows)
    conn.commit()

def infer_fiscal_period(period_end_date, fiscal_year_end_date=None):
    """
    Determine fiscal quarter (Q1-Q4) for a given period_end_date,
    relative to the company's fiscal_year_end.

    fiscal_year_end: a date/Timestamp representing when the company's
    fiscal year ends (e.g. late September for AAPL). Only the month is used.
    If None, assumes a calendar-aligned fiscal year (year-end = December).
    """
    fye_month = fiscal_year_end_date.month if fiscal_year_end_date is not None else 12

    months_since_fye = (period_end_date.month - fye_month) % 12
    if months_since_fye==0:
        months_since_fye=12 #period_end falls in the same month as FYE => it's Q4

    quarter = ((months_since_fye - 1)//3) + 1
    return f"Q{quarter}"

def fetch_and_store_fundamentals(conn, tickers, period="1y"):

    all_rows = []
    failed_tickers = []

    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            financials = t.quarterly_financials.T
            financials.index.name = "period_end_date"
            financials = financials.reset_index()

            fiscal_year_end_date = t.financials.columns[0] if not t.financials.empty else None

            balance_sheet = t.quarterly_balance_sheet.T
            balance_sheet.index.name = "period_end_date"
            balance_sheet = balance_sheet.reset_index()

            financials_and_bs = pd.merge(financials, balance_sheet, on="period_end_date", how="outer")

            raw_earnings_dates = t.earnings_dates
            if raw_earnings_dates is not None and not raw_earnings_dates.empty:
                earnings_dates = raw_earnings_dates.reset_index()
                earnings_dates["Earnings Date"] = earnings_dates["Earnings Date"].dt.tz_localize(None)
            else:
                earnings_dates = pd.DataFrame({"Earnings Date": pd.to_datetime([])})
            
            merged = pd.merge_asof(
                financials_and_bs.sort_values("period_end_date"),
                earnings_dates.sort_values("Earnings Date"),
                left_on="period_end_date",
                right_on="Earnings Date",
                direction="forward",
                tolerance=pd.Timedelta("120 days")
            )

            merged["report_date"] = merged["Earnings Date"].fillna(
                merged["period_end_date"]+pd.Timedelta(days=45)
            )
            merged["is_estimated_report_date"] = merged["Earnings Date"].isna()

            merged["Ticker"] = ticker

            COLUMN_MAP = {
                "Total Revenue": "revenue",
                "Gross Profit": "gross_profit",
                "EBITDA": "ebitda",
                "EBIT": "ebit",
                "Net Income": "net_income",
                "Common Stock Equity": "total_equity",
                "Total Assets": "total_assets",
                "Total Liabilities Net Minority Interest": "total_liabilities",
                "Ordinary Shares Number": "shares_outstanding",
            }

            merged = merged.rename(columns=COLUMN_MAP)

            financial_cols = list(COLUMN_MAP.values())  # now schema names, consistent everywhere
            existing_cols = [c for c in financial_cols if c in merged.columns]
            merged_clean = merged.dropna(subset=existing_cols, how="all")

            rows = [
                (ticker, row["period_end_date"].strftime("%Y-%m-%d"),
                row["report_date"].strftime("%Y-%m-%d"), row["is_estimated_report_date"],
                infer_fiscal_period(row["period_end_date"], fiscal_year_end_date),
                row.get("revenue", None), row.get("gross_profit", None),
                row.get("ebitda", None), row.get("ebit", None),
                row.get("net_income", None), row.get("total_equity", None),
                row.get("total_assets", None), row.get("total_liabilities", None),
                row.get("shares_outstanding", None))
                for _, row in merged_clean.iterrows()
            ]

            all_rows.extend(rows)
        except Exception as e:
            print(f"failed to fetch fundamentals for {ticker}: {e}")
            failed_tickers.append(ticker)
            continue

    conn.executemany("""
    INSERT OR REPLACE INTO Fundamentals
    (Ticker, period_end_date, report_date, is_estimated_report_date, fiscal_period, revenue, gross_profit, ebitda, ebit, net_income, total_equity, total_assets, total_liabilities, shares_outstanding)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, all_rows)

    conn.commit()

    if failed_tickers:
        print(f"\n{len(failed_tickers)} ticker(s) failed: {failed_tickers}")

    return failed_tickers

if __name__ == "__main__":
    tickers = ["AAPL", "NVDA", "ZTS", "A", "BRK-B"]
    conn = get_connection()
    create_tables(conn)

    # Register each ticker in Securities before inserting any Prices/Fundamentals for it —
    # remember the FOREIGN KEY constraint requires this row to exist first
    security_info = {
        "AAPL": ("Apple Inc.", "Technology", "Consumer Electronics"),
        "NVDA": ("NVIDIA Corp.", "Technology", "Semiconductors"),
        "ZTS":  ("Zoetis Inc.", "Healthcare", "Animal Health"),
        "A":    ("Agilent Technologies", "Healthcare", "Diagnostics & Research"),
        "BRK-B":("Berkshire Hathaway", "Financials", "Insurance"),
    }

    for ticker, (name, sector, industry) in security_info.items():
        upsert_securities(conn, ticker, name, sector, industry)

    fetch_and_store_prices(conn, tickers, period="1y")
    fetch_and_store_fundamentals(conn, tickers, period="1y")

    conn.close()