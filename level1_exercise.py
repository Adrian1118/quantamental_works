import pandas as pd
import numpy as np
import yfinance as yf
from matplotlib import pyplot as plt

# 1. Extract price data for 5 tickers from yfinance

tickers = ["AAPL", "NVDA", "ZTS", "A", "BRK-B"]

data = yf.download(tickers=tickers, period="1y")

prices = data["Close"]

# 2. Compute daily returns, annualized returns, annualized volatility for each

daily_returns = prices.pct_change().dropna()
daily_returns_plot = prices.pct_change().fillna(0)

annualized_return = (1 + daily_returns).prod() ** (252 / len(daily_returns)) - 1

annualized_volatility = daily_returns.std() * np.sqrt(252)

print(annualized_return)
print(annualized_volatility)

# 3. Plot cumulative returns

cumulative_returns = (1 + daily_returns_plot).cumprod()

cumulative_returns.plot(figsize=(10, 6))
plt.title("Cumulative Returns (1Y)")
plt.xlabel("Date")
plt.ylabel("Growth of $1")
plt.show()

print(cumulative_returns.iloc[0])