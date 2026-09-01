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
                 fiscal_period TEXT,
                 revenue REAL,
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

def fetch_and_store_prices(conn, ticker, period="1y"):
    data = yf.download(ticker, period=period)
    data = data.reset_index()

    rows = [
        (ticker, row["Date"].strftime("%Y-%m-%d"),
         row["Open"], row["High"], row["Low"], row["Close"],
         row["Adj Close"] if "Adj Close" in data.columns else row["Close"],
         int(row["Volume"])
         )
         for _, row in data.iterrows()
    ]

    conn.executemany("""
                     INSERT OR REPLACE INTO Prices
                     (Ticker, Date, Open, High, Low, Close, Adj_Close, Volume)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                     """, rows)
    conn.commit()