import sqlite3
import pandas as pd

DB_PATH = "qw_data.db"

def get_connection(read_only=True):
    if read_only:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(DB_PATH)
    return conn

def get_prices(conn, tickers, start_date=None, end_date=None):

    placeholders = ",".join("?" for _ in tickers)
    sql_query = f"SELECT * FROM Prices WHERE Ticker IN ({placeholders})"
    params = list(tickers)

    if start_date is not None:
        sql_query += " AND Date >= ?"
        params.append(start_date)
    if end_date is not None:
        sql_query += " AND Date <= ?"
        params.append(end_date)

    return pd.read_sql_query(sql_query, conn, params=params)

def get_fundamentals(conn, tickers):

    placehodlers = ",".join("?" for _ in tickers)
    sql_query = f"SELECT * FROM Fundamentals WHERE Ticker IN ({placehodlers})"
    params = list(tickers)

    return pd.read_sql_query(sql_query, conn, params=params)

def get_pit_fundamentals(conn, tickers, as_of_dates):
    """
    as_of_dates: a Dataframe with columns ["Ticker", "Date] --
    typically your Prices table's (Ticker, Date) pairs - for which you want
    the point-in-time-correct fundamentals as of each date.
    """
    fundamentals = get_fundamentals(conn,  tickers)
    fundamentals["report_date"] = pd.to_datetime(fundamentals["report_date"])

    as_of_dates = as_of_dates.copy()
    as_of_dates["Date"] = pd.to_datetime(as_of_dates["Date"])

    as_of_dates = as_of_dates.sort_values("Date")
    fundamentals = fundamentals.sort_values("report_date")

    return pd.merge_asof(
        as_of_dates, fundamentals,
        left_on="Date", right_on="report_date",
        by="Ticker",
        direction="backward"
    )
