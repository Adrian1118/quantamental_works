import requests
import pandas as pd
from io import StringIO

def scrape_sp500_constituents():
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {
        "User-Agent": "quantamental-platform-personal-project/1.0 (contact: your_email@example.com)"
    }

    response = requests.get(url, headers=headers)
    response.raise_for_status()  # raises an error immediately if the request failed, instead of silently continuing with a bad response

    tables = pd.read_html(StringIO(response.text))
    sp500 = tables[0]

    sp500 = sp500.rename(columns={
        "Symbol": "ticker",
        "Security": "name",
        "GICS Sector": "sector",
        "GICS Sub-Industry": "sub_industry",
    })

    return sp500[["ticker", "name", "sector", "sub_industry"]]

def refresh_sp500_constituents_file(path="sp500_constituents.csv"):
    sp500 = scrape_sp500_constituents()
    sp500.to_csv(path, index=False)
    print(f"Saved {len(sp500)} constituents to {path}")