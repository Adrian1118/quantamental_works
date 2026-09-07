import pandas as pd
from data_access import get_connection, get_prices, get_fundamentals, get_pit_fundamentals

conn = get_connection(read_only=True)

# --- Test 1: get_prices ---
print("=== get_prices: AAPL, NVDA, no date filter ===")
prices = get_prices(conn, ["AAPL", "NVDA"])
print(prices.shape)
print(prices["Ticker"].value_counts())   # sanity: both tickers present, roughly equal row counts
print(prices.head())

print("\n=== get_prices: AAPL only, date-filtered ===")
prices_filtered = get_prices(conn, ["AAPL"], start_date="2025-09-01", end_date="2025-09-10")
print(prices_filtered)
# Manually cross-check this against:
#   sqlite3 qw_data.db "SELECT * FROM Prices WHERE Ticker='AAPL' AND Date>='2025-09-01' AND Date<='2025-09-10';"
# Row count and values should match exactly.

# --- Test 2: get_fundamentals ---
print("\n=== get_fundamentals: AAPL ===")
fund = get_fundamentals(conn, ["AAPL"])
print(fund[["Ticker", "period_end_date", "report_date", "is_estimated_report_date"]])

# --- Test 3: get_pit_fundamentals — the important one ---
print("\n=== get_pit_fundamentals: hand-picked dates for AAPL ===")

# Pick dates that straddle known report_dates from the get_fundamentals output above.
# Adjust these once you've seen AAPL's actual report_dates printed just now.
as_of_dates = pd.DataFrame({
    "Ticker": ["AAPL", "AAPL", "AAPL"],
    "Date": ["2025-06-01", "2025-08-15", "2025-11-01"],
})

pit = get_pit_fundamentals(conn, ["AAPL"], as_of_dates)
print(pit[["Ticker", "Date", "period_end_date", "report_date", "revenue", "net_income"]])

conn.close()