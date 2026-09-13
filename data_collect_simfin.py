import os
import pandas as pd
import sqlite3

import simfin as sf
from simfin.names import *

sf.set_api_key(os.environ["SIMFIN_API_KEY"])
sf.set_data_dir('~/simfin_data/')

from dotenv import load_dotenv
load_dotenv()

from utils import *

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
                 fiscal_period TEXT,
                 report_date TEXT,
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


def fetch_and_store_prices(conn, tickers):
    prices = sf.load_shareprices(market="us", variant="daily")
    prices = prices[prices.index.get_level_values("Ticker").isin(tickers)]

    all_rows = []
    for ticker in tickers:
        ticker_data = prices[prices.index.get_level_values("Ticker") == ticker].reset_index()

        rows = [
            (ticker, row["Date"].strftime("%Y-%m-%d"),
             row["Open"], row["High"], row["Low"], row["Close"],
             row["Adj. Close"], int(row["Volume"]) if pd.notna(row["Volume"]) else None)
            for _, row in ticker_data.iterrows()
        ]
        all_rows.extend(rows)

    conn.executemany("""
        INSERT OR REPLACE INTO Prices
        (Ticker, Date, Open, High, Low, Close, Adj_Close, Volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, all_rows)
    conn.commit()


def load_sp500_companies():
    companies = sf.load_companies(market="us")
    refresh_sp500_constituents_file()
    df_sp500 = pd.read_csv("sp500_constituents.csv")

    tickers = df_sp500["ticker"].str.replace(".", "-", regex=False)

    available_tickers = companies.index.intersection(tickers)
    missing_tickers = sorted(set(tickers)-set(available_tickers))

    tickers_substitutes = { 
        'BRK-B': 'BRK-A', 
        'FISV': 'FI',
        'GOOGL': 'GOOG',
        'BNY': 'BK',
        'CPAY': 'FLT',
        'NWSA': 'NWS'}

    replace_message = 'Replaced '
    for t_old in tickers_substitutes:
        if t_old in missing_tickers:
            tickers = tickers.replace(t_old, tickers_substitutes[t_old])
            replace_message = f"{replace_message} {t_old} by {tickers_substitutes[t_old]},"

    available_tickers_clean = companies.index.intersection(tickers)
    missing_tickers_clean = sorted(set(tickers)-set(available_tickers_clean))

    sp500_companies = companies.loc[available_tickers_clean].reset_index()

    print(f"Matched {len(available_tickers_clean)} of {len(tickers)} S&P500 tickers in SimFin")
    print(f"Missing ({len(missing_tickers_clean)}): {missing_tickers_clean}")

    return sp500_companies


def fetch_and_store_fundamentals(conn, tickers):

    income_statements = sf.load_income(market="us", variant="quarterly")
    income_statements = income_statements[income_statements.index.get_level_values("Ticker").isin(tickers)]

    balance_sheets = sf.load_balance(market="us", variant="quarterly")
    balance_sheets = balance_sheets[balance_sheets.index.get_level_values("Ticker").isin(tickers)]

    merged = pd.merge(
        income_statements.reset_index(), balance_sheets.reset_index(),
        on=["Ticker", "Report Date"], how="outer", suffixes=("_inc", "_bal")
    )

    # Columns confirmed identical (0 real mismatches) — safe to coalesce, prefer one, fall back to other
    for col in ["Currency", "SimFinId"]:
        merged[col] = merged[f"{col}_inc"].combine_first(merged[f"{col}_bal"])

    # Columns with negligible mismatches (1-5 rows) — coalesce and accept the tiny known error rate,
    # but worth spot-checking those handful of rows once, not blindly
    for col in ["Fiscal Period", "Fiscal Year", "Shares (Basic)", "Shares (Diluted)"]:
        merged[col] = merged[f"{col}_inc"].combine_first(merged[f"{col}_bal"])

    # Publish Date: NOT a coalesce — take the LATER of the two.
    # Reasoning: report_date must reflect when the COMPLETE filing (both statements) was actually
    # available. If income was published on day X and balance on day Y > X, the data wasn't fully
    # usable until day Y — taking the earlier date would silently reintroduce look-ahead bias,
    # the exact thing report_date exists to prevent.
    merged["Publish Date"] = merged[["Publish Date_inc", "Publish Date_bal"]].max(axis=1)

    # Restated Date: given the 84% mismatch rate, these are genuinely two different facts,
    # not one value split across two columns — keep them separate rather than forcing a merge
    merged = merged.rename(columns={
        "Restated Date_inc": "Restated Date IS",
        "Restated Date_bal": "Restated Date BS",
    })

    # Now drop the remaining redundant _inc/_bal pairs for the coalesced columns
    merged = merged.drop(columns=[
        f"{c}_inc" for c in ["Currency", "SimFinId", "Fiscal Period", "Fiscal Year",
                            "Shares (Basic)", "Shares (Diluted)"]
    ] + [
        f"{c}_bal" for c in ["Currency", "SimFinId", "Fiscal Period", "Fiscal Year",
                            "Shares (Basic)", "Shares (Diluted)"]
    ] + ["Publish Date_inc", "Publish Date_bal"])


    COLUMN_MAP = {
        "Report Date": "period_end_date",
        "Fiscal Period": "fiscal_period",
        "Publish Date": "report_date",
        "Revenue": "revenue",
        "Gross Profit": "gross_profit",
        "Operating Income (Loss)": "ebit",
        "Net Income": "net_income",
        "Total Equity": "total_equity",
        "Total Assets": "total_assets",
        "Total Liabilities": "total_liabilities",
        "Shares (Diluted)": "shares_outstanding"
    }
    
    merged = merged.rename(columns=COLUMN_MAP)

    # ebitda derived as ebit + reported D&A; may understate true EBITDA where D&A is
    # embedded in Cost of Revenue rather than broken out separately (known SimFin
    # standardization limitation, more likely to affect Real Estate/Industrials)
    merged["ebitda"] = merged["ebit"] + merged["Depreciation & Amortization"]

    financial_cols = ["revenue", "gross_profit", "ebitda", "ebit", "net_income",
                      "total_equity", "total_assets", "total_liabilities", "shares_outstanding"]
    existing_cols = [c for c in financial_cols if c in merged.columns]
    merged = merged.dropna(subset=existing_cols, how="all")


    all_rows = []
    failed_tickers = []

    for ticker in tickers:
        try:
            ticker_fin_stmts = merged[merged["Ticker"]==ticker]

            rows = [
                (ticker, row["period_end_dat"].strftime("%Y-%m-%d"), row.get("fiscal_period", None),
                 row["report_date"].strftime("%Y-%m-%d"),
                row.get("revenue", None), row.get("gross_profit", None),
                row.get("ebitda", None), row.get("ebit", None),
                row.get("net_income", None), row.get("total_equity", None),
                row.get("total_assets", None), row.get("total_liabilities", None),
                row.get("shares_outstanding", None))
                for _, row in ticker_fin_stmts.iterrows()
            ]

            all_rows.extend(rows)
        except Exception as e:
            print(f"failed to fetch fundamentals for {ticker}: {e}")
            failed_tickers.append(ticker)
            continue

    conn.executemany("""
    INSERT OR REPLACE INTO Fundamentals
    (Ticker, report_date, fiscal_period, publish_date, revenue, gross_profit, ebitda, ebit, net_income, total_equity, total_assets, total_liabilities, shares_outstanding)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, all_rows)

    conn.commit()

    if failed_tickers:
        print(f"\n{len(failed_tickers)} ticker(s) failed: {failed_tickers}")

    return failed_tickers

if __name__ == "__main__":
    tickers = ["AAPL", "NVDA", "ZTS", "A", "BRK-A"]
    conn = get_connection()
    create_tables(conn)

    # Register each ticker in Securities before inserting any Prices/Fundamentals for it —
    # remember the FOREIGN KEY constraint requires this row to exist first
    security_info = {
        "AAPL": ("Apple Inc.", "Technology", "Consumer Electronics"),
        "NVDA": ("NVIDIA Corp.", "Technology", "Semiconductors"),
        "ZTS":  ("Zoetis Inc.", "Healthcare", "Animal Health"),
        "A":    ("Agilent Technologies", "Healthcare", "Diagnostics & Research"),
        "BRK-A":("Berkshire Hathaway", "Financials", "Insurance"),
    }

    for ticker, (name, sector, industry) in security_info.items():
        upsert_securities(conn, ticker, name, sector, industry)

    fetch_and_store_prices(conn, tickers, period="1y")
    fetch_and_store_fundamentals(conn, tickers, period="1y")

    conn.close()