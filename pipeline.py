import os
import pandas as pd
import sqlite3
import simfin as sf
from dotenv import load_dotenv
from utils import refresh_sp500_constituents_file

load_dotenv()
sf.set_api_key(os.environ["SIMFIN_API_KEY"])
sf.set_data_dir('~/simfin_data/')

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
        CREATE TABLE IF NOT EXISTS Prices (
            Ticker TEXT NOT NULL,
            Date TEXT NOT NULL,
            Open REAL, High REAL, Low REAL, Close REAL,
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
            fiscal_period TEXT,
            revenue REAL, gross_profit REAL, ebitda REAL, ebit REAL,
            net_income REAL, total_equity REAL, total_assets REAL,
            total_liabilities REAL, shares_outstanding REAL,
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

def load_sp500_companies():
    companies = sf.load_companies(market="us", refresh_days=30)
    refresh_sp500_constituents_file()
    df_sp500 = pd.read_csv("sp500_constituents.csv")
    tickers = df_sp500["ticker"].str.replace(".", "-", regex=False)

    tickers_substitutes = {
        'BRK-B': 'BRK-A', 'FISV': 'FI', 'GOOGL': 'GOOG',
        'BNY': 'BK', 'CPAY': 'FLT', 'NWSA': 'NWS',
    }
    missing = sorted(set(tickers) - set(companies.index.intersection(tickers)))
    for t_old, t_new in tickers_substitutes.items():
        if t_old in missing:
            tickers = tickers.replace(t_old, t_new)

    available = companies.index.intersection(tickers)
    missing_final = sorted(set(tickers) - set(available))
    print(f"Matched {len(available)} of {len(tickers)} S&P500 tickers in SimFin")
    print(f"Missing ({len(missing_final)}): {missing_final}")

    return companies.loc[available].reset_index()

def fetch_and_store_prices(conn, tickers):
    prices = sf.load_shareprices(market="us", variant="daily", refresh_days=30)
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

def fetch_and_store_fundamentals(conn, tickers):
    income_statements = sf.load_income(market="us", variant="quarterly")
    income_statements = income_statements[income_statements.index.get_level_values("Ticker").isin(tickers)]

    balance_sheets = sf.load_balance(market="us", variant="quarterly")
    balance_sheets = balance_sheets[balance_sheets.index.get_level_values("Ticker").isin(tickers)]

    merged = pd.merge(
        income_statements.reset_index(), balance_sheets.reset_index(),
        on=["Ticker", "Report Date"], how="outer", suffixes=("_inc", "_bal")
    )

    for col in ["Currency", "SimFinId", "Fiscal Period", "Fiscal Year",
                "Shares (Basic)", "Shares (Diluted)"]:
        merged[col] = merged[f"{col}_inc"].combine_first(merged[f"{col}_bal"])

    # Publish Date: take the LATER of the two statements' publish dates — the filing
    # isn't fully usable until BOTH statements are out, or report_date would understate
    # true availability and reintroduce look-ahead risk.
    merged["Publish Date"] = merged[["Publish Date_inc", "Publish Date_bal"]].max(axis=1)

    merged = merged.drop(columns=[
        f"{c}_inc" for c in ["Currency", "SimFinId", "Fiscal Period", "Fiscal Year",
                              "Shares (Basic)", "Shares (Diluted)"]
    ] + [
        f"{c}_bal" for c in ["Currency", "SimFinId", "Fiscal Period", "Fiscal Year",
                              "Shares (Basic)", "Shares (Diluted)"]
    ] + ["Publish Date_inc", "Publish Date_bal"])

    # Report Date = fiscal period end; Publish Date = actual disclosure date.
    # These map to our schema's period_end_date / report_date respectively — do NOT swap.
    COLUMN_MAP = {
        "Report Date": "period_end_date",
        "Publish Date": "report_date",
        "Revenue": "revenue",
        "Gross Profit": "gross_profit",
        "Operating Income (Loss)": "ebit",
        "Net Income": "net_income",
        "Total Equity": "total_equity",
        "Total Assets": "total_assets",
        "Total Liabilities": "total_liabilities",
        "Shares (Diluted)": "shares_outstanding",
        "Fiscal Period": "fiscal_period",
    }
    merged = merged.rename(columns=COLUMN_MAP)

    # ebitda = ebit + reported D&A; may understate true EBITDA where D&A is embedded in
    # Cost of Revenue rather than broken out (more likely for Real Estate/Industrials)
    merged["ebitda"] = merged["ebit"] + merged["Depreciation & Amortization"]

    financial_cols = ["revenue", "gross_profit", "ebitda", "ebit", "net_income",
                       "total_equity", "total_assets", "total_liabilities", "shares_outstanding"]
    existing_cols = [c for c in financial_cols if c in merged.columns]
    merged = merged.dropna(subset=existing_cols, how="all")

    all_rows = []
    failed_tickers = []
    for ticker in tickers:
        try:
            ticker_rows = merged[merged["Ticker"] == ticker]
            rows = [
                (ticker, row["period_end_date"].strftime("%Y-%m-%d"),
                 row["report_date"].strftime("%Y-%m-%d"), row.get("fiscal_period", None),
                 row.get("revenue", None), row.get("gross_profit", None),
                 row.get("ebitda", None), row.get("ebit", None),
                 row.get("net_income", None), row.get("total_equity", None),
                 row.get("total_assets", None), row.get("total_liabilities", None),
                 row.get("shares_outstanding", None))
                for _, row in ticker_rows.iterrows()
            ]
            all_rows.extend(rows)
        except Exception as e:
            print(f"failed to fetch fundamentals for {ticker}: {e}")
            failed_tickers.append(ticker)

    conn.executemany("""
        INSERT OR REPLACE INTO Fundamentals
        (Ticker, period_end_date, report_date, fiscal_period, revenue, gross_profit,
         ebitda, ebit, net_income, total_equity, total_assets, total_liabilities, shares_outstanding)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, all_rows)
    conn.commit()

    if failed_tickers:
        print(f"\n{len(failed_tickers)} ticker(s) failed: {failed_tickers}")
    return failed_tickers

if __name__ == "__main__":
    companies = load_sp500_companies()
    industries = sf.load_industries().reset_index()

    sp500 = pd.merge(companies, industries, how="left", on="IndustryId")
    chosen_sectors = ["Technology", "Real Estate", "Consumer Defensive"]
    universe = sp500[sp500["Sector"].isin(chosen_sectors)]
    tickers = universe["Ticker"].tolist()

    conn = get_connection()
    create_tables(conn)
    for _, row in universe.iterrows():
        upsert_securities(conn, row["Ticker"], row["Company Name"], row["Sector"], row.get("Industry"))

    fetch_and_store_prices(conn, tickers)
    failed = fetch_and_store_fundamentals(conn, tickers)
    conn.close()