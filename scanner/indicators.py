import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0)
    dn = -d.clip(upper=0)
    au = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    ad = dn.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = au / ad.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(ad != 0, 100.0)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    pc = close.shift(1)
    tr = pd.concat([high - low, (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def pivot_highs(high: pd.Series, window: int = 5) -> pd.Series:
    """Boolean: bar is the highest within +/- window bars.

    A pivot at bar j is only *known* at bar j + window, so callers must ignore
    pivots newer than (current bar - window) to avoid look-ahead.
    """
    roll = high.rolling(2 * window + 1, center=True).max()
    return (high == roll).fillna(False)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Returns (macd line, signal line, histogram)."""
    line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    sig = line.ewm(span=signal, adjust=False).mean()
    line = line.where(close.notna().cumsum() >= slow)
    return line, sig.where(line.notna()), (line - sig)


def supertrend(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 10,
               mult: float = 3.0) -> pd.Series:
    """Supertrend direction: 1 = uptrend (price above the band), -1 = downtrend, NaN = warming up."""
    a = atr(high, low, close, n).to_numpy()
    h, l, c = high.to_numpy(), low.to_numpy(), close.to_numpy()
    hl2 = (h + l) / 2
    ub, lb = hl2 + mult * a, hl2 - mult * a
    fub, flb = np.full(len(c), np.nan), np.full(len(c), np.nan)
    d = np.full(len(c), np.nan)
    for i in range(len(c)):
        if not np.isfinite(a[i]):
            continue
        if i == 0 or not np.isfinite(d[i - 1]):
            fub[i], flb[i], d[i] = ub[i], lb[i], 1.0 if c[i] >= hl2[i] else -1.0
            continue
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or c[i - 1] > fub[i - 1]) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or c[i - 1] < flb[i - 1]) else flb[i - 1]
        if d[i - 1] < 0 and c[i] > fub[i]:
            d[i] = 1.0
        elif d[i - 1] > 0 and c[i] < flb[i]:
            d[i] = -1.0
        else:
            d[i] = d[i - 1]
    return pd.Series(d, index=close.index)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's ADX: trend strength 0-100 (above ~20-25 = trending)."""
    up, dn = high.diff(), -low.diff()
    pdm = up.where((up > dn) & (up > 0), 0.0)
    mdm = dn.where((dn > up) & (dn > 0), 0.0)
    pc = close.shift(1)
    tr = pd.concat([high - low, (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    w = dict(alpha=1 / n, adjust=False, min_periods=n)
    atr_w = tr.ewm(**w).mean()
    pdi = 100 * pdm.ewm(**w).mean() / atr_w
    mdi = 100 * mdm.ewm(**w).mean() / atr_w
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(**w).mean()


def bb_width(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.Series:
    """Bollinger bandwidth: (upper - lower) / middle."""
    m = close.rolling(n).mean()
    return 2 * k * close.rolling(n).std() / m
