import yfinance as yf, pandas as pd
df = yf.download("^GSPC", start="1950-01-01", end="2026-11-10", auto_adjust=False, progress=False)
c = df["Close"].squeeze().dropna()
r = c.pct_change() * 100
days = ["2008-10-13","2008-10-28","2025-04-09","2020-03-24","2020-03-13","1987-10-21","2009-03-23","2020-04-06","2008-11-13","2008-11-24"]
for d in days:
    i = c.index.get_loc(pd.Timestamp(d))
    nxt = r.iloc[i+1:i+6]
    worst = nxt.min(); wd = nxt.idxmin()
    net5 = (c.iloc[i+5] / c.iloc[i] - 1) * 100
    print(f"ROW|{d}|{r.iloc[i]:.2f}|{worst:.2f}|{wd:%Y-%m-%d}|{(wd - pd.Timestamp(d)).days}|{net5:.2f}|" + ",".join(f"{x:+.2f}" for x in nxt))
