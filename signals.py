from data_access import *
import pandas as pd

def get_momentum(conn, tickers, lookback_period=251, skip_period=21, first_rebal_date=None):
    
    prices = get_prices(conn, tickers)
    prices["Date"] = pd.to_datetime(prices["Date"])
    px = prices.pivot(index = "Date", columns = "Ticker", values="Adj_Close")
    first_rebal_date = pd.to_datetime(first_rebal_date) if first_rebal_date is not None else px.index.min()
    px = px[px.index >= first_rebal_date]

    # Get rebalancing dates only (i.e. every N period)
    rebal_dates = px.index[::skip_period]

    # Get momentum value at the rebalancing date
    momentum = px.shift(skip_period) / px.shift(lookback_period+1) - 1
    momentum_at_rebal = momentum.loc[rebal_dates]

    # Compute forward returns between each rebalancing dates
    prices_at_rebal = px.loc[rebal_dates]
    fwd_returns_at_rebal = prices_at_rebal.shift(-1) / prices_at_rebal

    # Merge in a single long dataframe indexed by dates and tickers
    return pd.concat({"momentum": momentum_at_rebal, "fwd_returns": fwd_returns_at_rebal}, axis=1).stack("Ticker", future_stack=True).reset_index()
