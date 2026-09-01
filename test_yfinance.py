import yfinance as yf
import pandas as pd

tickers = ["AAPL", "NVDA", "ZTS", "A", "BRK-B"]

t = yf.Ticker(tickers[1])
financials = t.earnings_dates

print(financials)