# -*- coding: utf-8 -*-
"""
stock_indicators.py — 个股技术指标计算模块
移植自 backtrader_superplot/my_bt_indicator 指标库（算法与 backtrader 原版逐行对齐）：
  UMA(通用均线) / SuperTrend / VolumeDynamicVIDYA / EnhancedMACD / X_RSI / X_KDJ / X_OBV / X_XTR / BBTrendScore
输入 df 必须含列：trade_date, open, high, low, close, vol, amount（按日期升序）
返回 dict，所有序列长度与输入一致，NaN 用 None 表示。
"""
import numpy as np
import pandas as pd

# ==================== 基础均线工具 ====================
def _sma(s, n):
    return s.rolling(int(n)).mean()

def _ema(s, n):
    return s.ewm(span=int(n), adjust=False).mean()

def _rma(s, n):
    """Wilder 平滑（对应 backtrader WilderMA / Pine RMA）"""
    return s.ewm(alpha=1.0 / int(n), adjust=False).mean()

def _wma(s, n):
    """线性加权移动平均：权重 1..n"""
    n = int(n)
    w = np.arange(1, n + 1, dtype=float)
    return s.rolling(n).apply(lambda x: np.dot(x, w) / w.sum(), raw=True)

def _hma(s, n):
    """赫尔移动平均"""
    n = int(n)
    half = max(int(n / 2), 1)
    sqrtn = max(int(np.sqrt(n)), 1)
    wma_half = _wma(s, half)
    wma_full = _wma(s, n)
    return _wma(2 * wma_half - wma_full, sqrtn)

def _stddev(s, n):
    """总体标准差（对应 backtrader StdDev, ddof=0）"""
    return s.rolling(int(n)).std(ddof=0)

def _tr(high, low, close):
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    if len(tr) > 0 and np.isnan(tr.iloc[0]):
        tr.iloc[0] = (high.iloc[0] - low.iloc[0])
    return tr

def _wilder(s, n):
    """Wilder 平滑（与 backtrader SmoothedMovingAverage 对齐）：
    前 n-1 根 NaN，第 n 根取前 n 根均值作种子，其后 prev*(n-1)+x)/n"""
    n = int(n)
    s = pd.Series(s, dtype=float)
    out = pd.Series(np.nan, index=s.index)
    vals = s.to_numpy(dtype=float)
    if len(vals) < n or n <= 0:
        return out
    prev = np.nanmean(vals[:n])
    out.iloc[n - 1] = prev
    for i in range(n, len(vals)):
        v = vals[i]
        if np.isnan(v):
            v = 0.0
        prev = (prev * (n - 1) + v) / n
        out.iloc[i] = prev
    return out

# ==================== UMA 通用均线 ====================
def calc_uma(close, ma_type='EMA', periods=(20, 50, 100)):
    ma_map = {'SMA': _sma, 'EMA': _ema, 'WMA': _wma, 'RMA': _rma, 'HMA': _hma}
    fn = ma_map.get(str(ma_type).upper(), _sma)
    out = {}
    colors = ['#2196F3', '#4CAF50', '#9E9E9E']
    for i, p in enumerate(periods):
        if p and p > 0:
            out[f'ma{i+1}'] = {'period': int(p), 'color': colors[i], 'value': fn(close, p)}
    return out

# ==================== SuperTrend ====================
def calc_supertrend(df, period=10, multiplier=3.0, use_sma_atr=False):
    high, low, close = df['high'], df['low'], df['close']
    n = len(df)
    src = (high + low) / 2
    tr = _tr(high, low, close)
    atr = _sma(tr, period) if use_sma_atr else _rma(tr, period)

    up_trend = np.full(n, np.nan)
    down_trend = np.full(n, np.nan)
    buy = np.full(n, np.nan)
    sell = np.full(n, np.nan)
    trend_arr = np.zeros(n, dtype=int)

    curr_up = np.nan
    curr_dn = np.nan
    trend = 1
    for i in range(n):
        a = atr.iloc[i]
        s = src.iloc[i]
        if np.isnan(a) or np.isnan(s):
            continue
        raw_up = s - multiplier * a
        raw_dn = s + multiplier * a
        if np.isnan(curr_up):
            curr_up = raw_up
            curr_dn = raw_dn
            trend = -1 if close.iloc[i] < raw_up else 1
            continue
        prev_close = close.iloc[i - 1]
        prev_up, prev_dn, prev_trend = curr_up, curr_dn, trend
        curr_up = max(raw_up, prev_up) if prev_close > prev_up else raw_up
        curr_dn = min(raw_dn, prev_dn) if prev_close < prev_dn else raw_dn
        if prev_trend == -1 and close.iloc[i] > prev_dn:
            trend = 1
        elif prev_trend == 1 and close.iloc[i] < prev_up:
            trend = -1
        trend_arr[i] = trend
        if trend == 1:
            up_trend[i] = curr_up
        else:
            down_trend[i] = curr_dn
        if trend == 1 and prev_trend == -1:
            buy[i] = curr_up
        if trend == -1 and prev_trend == 1:
            sell[i] = curr_dn

    return {'up': up_trend, 'down': down_trend, 'buy': buy, 'sell': sell, 'trend': trend_arr}

# ==================== VolumeDynamicVIDYA ====================
def calc_vidya(df, length=10, momentum=20, atr_period=200, vidya_sma=15, band_distance=2.0):
    close = df['close']
    n = len(df)
    high, low = df['high'], df['low']
    mom = close.diff(1)
    sum_pos = np.maximum(mom, 0).rolling(momentum).sum()
    sum_neg = (-np.minimum(mom, 0)).rolling(momentum).sum()
    den = sum_pos + sum_neg
    den_safe = den.replace(0, 1e-8)
    cmo = (100 * (sum_pos - sum_neg) / den_safe).abs()
    alpha = 2.0 / (length + 1)
    weight = cmo / 100.0

    vidya_raw = np.full(n, np.nan)
    if n > 0:
        vidya_raw[0] = close.iloc[0]
    for i in range(1, n):
        w = weight.iloc[i]
        if np.isnan(w):
            w = 0.0
        a = alpha * w
        vidya_raw[i] = vidya_raw[i - 1] + a * (close.iloc[i] - vidya_raw[i - 1])
    vidya_line = pd.Series(vidya_raw).rolling(vidya_sma).mean()

    tr = _tr(high, low, close)
    atr = _rma(tr, atr_period)
    upper = vidya_line + atr * band_distance
    lower = vidya_line - atr * band_distance

    trend_up = (close > upper) & (close.shift(1) <= upper.shift(1))
    trend_dn = (close < lower) & (close.shift(1) >= lower.shift(1))
    buy = np.where(trend_up, low.values, np.nan)
    sell = np.where(trend_dn, high.values, np.nan)
    return {'vidya': vidya_line, 'upper': upper, 'lower': lower, 'buy': buy, 'sell': sell}

# ==================== EnhancedMACD ====================
# 颜色编码（与原版 COLOR_HEX_MAP 一致）
MACD_COLORS = {
    0: '#cccccc',  # GRAY
    1: '#ef5350',  # RED
    2: '#f48fb1',  # PINK
    3: '#66bb6a',  # GREEN
    4: '#4fc3f7',  # LIGHTBLUE
    5: '#b71c1c',  # DARK_RED
    6: '#ff4081',  # BRIGHT_PINK
    7: '#1b5e20',  # DARK_GREEN
    8: '#00e5ff',  # BRIGHT_LIGHT_BLUE
}

def calc_macd(close, fast=12, slow=26, signal=9):
    n = len(close)
    dif = _ema(close, fast) - _ema(close, slow)
    dea = _ema(dif, signal)
    hist = dif - dea

    hist_values = []
    color_values = []
    below_idx = []
    above_idx = []
    dark_red = []
    dark_green = []
    signals = []

    for i in range(n):
        ch = hist.iloc[i]
        hist_values.append(ch)
        # 1. 基础颜色
        if np.isnan(ch):
            color = 0
        else:
            prev = hist_values[-2] if len(hist_values) > 1 else np.nan
            if ch >= 0:
                if np.isnan(prev) or ch >= prev:
                    color = 1
                else:
                    color = 2
            else:
                if np.isnan(prev) or ch <= prev:
                    color = 3
                else:
                    color = 4
        # 2. 特殊柱体回溯
        if not np.isnan(ch) and ch < 0:
            below_idx.append(i)
            if len(below_idx) >= 2:
                ps, pe = below_idx[-2] + 1, below_idx[-1]
                if ps < pe:
                    mid = hist_values[ps:pe]
                    if all(not np.isnan(h) and h > 0 for h in mid):
                        gi = ps + int(np.argmax(mid))
                        if gi not in dark_red:
                            dark_red.append(gi)
                            color_values[gi] = 5
                            if gi + 1 < len(color_values) and color_values[gi + 1] == 2:
                                color_values[gi + 1] = 6
        elif not np.isnan(ch) and ch > 0:
            above_idx.append(i)
            if len(above_idx) >= 2:
                ps, pe = above_idx[-2] + 1, above_idx[-1]
                if ps < pe:
                    mid = hist_values[ps:pe]
                    if all(not np.isnan(h) and h < 0 for h in mid):
                        gi = ps + int(np.argmin(mid))
                        if gi not in dark_green:
                            dark_green.append(gi)
                            color_values[gi] = 7
                            if gi + 1 < len(color_values) and color_values[gi + 1] == 4:
                                color_values[gi + 1] = 8
        color_values.append(color)

        # 3. 信号
        signal = 0
        if len(hist_values) >= 2 and not np.isnan(hist_values[-2]) and not np.isnan(ch):
            a_idx = i - 1
            a_val = hist_values[-2]
            # Sell: ch < a_val and a_val > 0
            if ch < a_val and a_val > 0:
                b_idx = None
                j = a_idx
                while j >= 0:
                    hv = hist_values[j]
                    if not np.isnan(hv) and hv <= 0:
                        b_idx = j
                        break
                    j -= 1
                cond2 = False
                if b_idx is not None and b_idx < a_idx:
                    inc = True
                    if b_idx + 1 < len(hist_values):
                        pv = hist_values[b_idx + 1]
                        for k in range(b_idx + 2, a_idx + 1):
                            hv = hist_values[k]
                            if np.isnan(pv) or np.isnan(hv) or not (hv > pv):
                                inc = False
                                break
                            pv = hv
                        cond2 = inc
                reds = [x for x in dark_red if x < a_idx]
                cond3 = False
                if len(reds) >= 2:
                    r1, r2 = reds[-2], reds[-1]
                    h1, h2 = hist_values[r1], hist_values[r2]
                    if not (np.isnan(h1) or np.isnan(h2)) and h1 > h2 > a_val:
                        cond3 = True
                if cond2 and cond3:
                    signal = -1
            # Buy: ch > a_val and a_val < 0
            if signal == 0 and ch > a_val and a_val < 0:
                b_idx = None
                j = a_idx
                while j >= 0:
                    hv = hist_values[j]
                    if not np.isnan(hv) and hv >= 0:
                        b_idx = j
                        break
                    j -= 1
                cond2 = False
                if b_idx is not None and b_idx < a_idx:
                    dec = True
                    if b_idx + 1 < len(hist_values):
                        pv = hist_values[b_idx + 1]
                        for k in range(b_idx + 2, a_idx + 1):
                            hv = hist_values[k]
                            if np.isnan(pv) or np.isnan(hv) or not (hv < pv):
                                dec = False
                                break
                            pv = hv
                        cond2 = dec
                greens = [x for x in dark_green if x < a_idx]
                cond3 = False
                if len(greens) >= 2:
                    g1, g2 = greens[-2], greens[-1]
                    h1, h2 = hist_values[g1], hist_values[g2]
                    if not (np.isnan(h1) or np.isnan(h2)) and h1 < h2 < a_val:
                        cond3 = True
                if cond2 and cond3:
                    signal = 1
            # Strong Buy
            if signal == 0 and ch >= 0 and a_val < 0:
                greens = [x for x in dark_green if x < a_idx]
                if len(greens) >= 3:
                    g1, g2, g3 = greens[-3], greens[-2], greens[-1]
                    h1, h2, h3 = hist_values[g1], hist_values[g2], hist_values[g3]
                    if not (np.isnan(h1) or np.isnan(h2) or np.isnan(h3)) and h1 < h2 < h3 < a_val:
                        signal = 2
            # Strong Sell
            if signal == 0 and ch <= 0 and a_val > 0:
                reds = [x for x in dark_red if x < a_idx]
                if len(reds) >= 3:
                    r1, r2, r3 = reds[-3], reds[-2], reds[-1]
                    h1, h2, h3 = hist_values[r1], hist_values[r2], hist_values[r3]
                    if not (np.isnan(h1) or np.isnan(h2) or np.isnan(h3)) and h1 > h2 > h3 > a_val:
                        signal = -2
        signals.append(signal)

    colors = [MACD_COLORS[c] for c in color_values]
    return {'dif': dif, 'dea': dea, 'hist': hist, 'colors': colors, 'signal': signals}

# ==================== X_RSI ====================
def calc_rsi(close, rsi_length=14, ma_length=14, ma_type='SMA'):
    change = close.diff()
    up = _rma(np.maximum(change, 0), rsi_length)
    down = _rma(-np.minimum(change, 0), rsi_length)
    down_ = down.copy()
    up_ = up.copy()
    rsi = pd.Series(np.nan, index=close.index)
    mask_down0 = down_ == 0
    mask_up0 = (up_ == 0) & ~mask_down0
    rsi[mask_down0] = 100.0
    rsi[mask_up0] = 0.0
    rest = ~mask_down0 & ~mask_up0
    rsi[rest] = 100.0 - 100.0 / (1.0 + up[rest] / down[rest])
    rsi = rsi.fillna(np.nan)
    # 注意：up/down 为 NaN 时保持 NaN
    rsi[up.isna() | down.isna()] = np.nan
    if str(ma_type).upper() == 'SMA':
        rsi_ma = _sma(rsi, ma_length)
    elif str(ma_type).upper() == 'EMA':
        rsi_ma = _ema(rsi, ma_length)
    else:
        rsi_ma = pd.Series(np.nan, index=close.index)
    return {'rsi': rsi, 'rsi_ma': rsi_ma}

# ==================== KDJ（标准：backtrader 官方 Stochastic） ====================
# 官方 Stochastic 算法（backtrader-1.9.74.123 内置指标 stochastic.py）：
#   k   = 100 * (close - Lowest(low, period)) / (Highest(high, period) - Lowest(low, period))
#   d   = MovAv.Simple(k, period_dfast)          # 快线 D
#   K线 = MovAv.Simple(d, period_dslow)          # 慢速随机 %K（官方 Stochastic 的 percK）
#   D线 = MovAv.Simple(K, period_dslow)          # 慢速随机 %D（官方 percD）
#   J线 = 3*K - 2*D                              # J 线为行情软件惯例（backtrader 无内置，保留）
# 参数：period=9（RSV窗口）、signal=3（K/D 平滑窗口），与国内软件默认一致
def calc_kdj(df, period=9, signal=3):
    high, low, close = df['high'], df['low'], df['close']
    n = len(df)
    hh = high.rolling(period).max()
    ll = low.rolling(period).min()
    rng = hh - ll
    rsv = 100 * (close - ll) / rng.where(rng != 0, 1.0)

    # 官方 Stochastic：K = SMA(rsv, signal)，D = SMA(K, signal)（对应官方 fast.d / slow.percK、slow.percD）
    K = _sma(rsv, signal).to_numpy().copy()
    D = _sma(pd.Series(K, index=df.index), signal).to_numpy().copy()
    J = 3.0 * K - 2.0 * D
    # 头部预热期（rolling 窗口未满）置 NaN
    warm = period + 2 * signal - 2
    K[:warm] = np.nan
    D[:warm] = np.nan
    J[:warm] = np.nan
    return {'K': K, 'D': D, 'J': J}

# ==================== X_OBV ====================
def calc_obv(df, ma_length=20):
    close, vol = df['close'], df['vol']
    change = close.diff()
    vol_flow = np.where(change > 0, vol, np.where(change < 0, -vol, 0.0))
    obv = pd.Series(np.cumsum(vol_flow) * 1e-9, index=close.index)
    obv_ma = _sma(obv, ma_length)
    return {'obv': obv, 'obv_ma': obv_ma}

# ==================== X_XTR ====================
def calc_xtr(df, xtr_period=14, ma_length=9, band_period=20, band_mult=2.0):
    o, h, l, c, v = df['open'], df['high'], df['low'], df['close'], df['vol']
    n = xtr_period
    price_component = 0.9 * c - 0.7 * o - 0.1 * h - 0.1 * l
    norm_price = price_component / o.where(o != 0, np.nan)
    norm_price = norm_price.fillna(0.0)
    vol_ma = _sma(v, n * 5)
    norm_vol = v / vol_ma.where(vol_ma != 0, np.nan)
    norm_vol = norm_vol.fillna(1.0)
    raw_force = norm_price * norm_vol * 100.0
    w = np.arange(1, n + 1, dtype=float)
    xtr = raw_force.rolling(n).apply(lambda x: np.dot(x, w), raw=True)
    xtr_ma = _sma(xtr, ma_length)
    basis = _sma(xtr, band_period)
    std = _stddev(xtr, band_period)
    top = basis + std * band_mult
    bot = basis - std * band_mult
    return {'xtr': xtr, 'xtr_ma': xtr_ma, 'top': top, 'bot': bot}

# ==================== ADX（Wilder DMI/ADX） ====================
def calc_adx(df, period=14):
    """标准 Wilder ADX：+DI / -DI / DX / ADX
    up = H-H(-1)，dn = L(-1)-L；+DM 取 up>dn 且 up>0，-DM 取 dn>up 且 dn>0
    +DI = 100*Wilder(+DM)/ATR，-DI = 100*Wilder(-DM)/ATR
    DX = 100*|+DI - -DI|/(+DI + -DI)，ADX = Wilder(DX)
    """
    h, l, c = df['high'], df['low'], df['close']
    n = max(1, int(period))
    up_move = h.diff()
    down_move = -l.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=h.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=h.index)
    tr = _tr(h, l, c)
    atr = _wilder(tr, n)
    pdm = _wilder(plus_dm, n)
    mdm = _wilder(minus_dm, n)
    safe_atr = atr.where(atr != 0, np.nan)
    pdi = 100.0 * pdm / safe_atr
    mdi = 100.0 * mdm / safe_atr
    denom = (pdi + mdi).where((pdi + mdi) != 0, np.nan)
    dx = 100.0 * (pdi - mdi).abs() / denom
    adx = _wilder(dx.fillna(0.0), n)
    return {'adx': adx, 'pdi': pdi, 'mdi': mdi, 'dx': dx}

# ==================== BBTrendScore ====================
def calc_bb_score(close, bb_period=50, bb_dev=2.0, roc_period=5, acc_period=3, roll_window=60,
                  w_nbbw=0.5, w_roc=0.3, w_acc=0.2):
    mid = _sma(close, bb_period)
    std = _stddev(close, bb_period)
    upper = mid + bb_dev * std
    lower = mid - bb_dev * std
    nbbw = (upper - lower) / mid * 100.0
    nbbw_roc = nbbw.pct_change(roc_period) * 100.0
    nbbw_acc = nbbw_roc.pct_change(acc_period) * 100.0

    def norm01(s):
        mn = s.rolling(roll_window).min()
        mx = s.rolling(roll_window).max()
        rng = mx - mn
        out = (s - mn) / rng.where(rng != 0, np.nan)
        return out.fillna(0.0)

    score = (w_nbbw * norm01(nbbw) + w_roc * norm01(nbbw_roc) + w_acc * norm01(nbbw_acc)) * 100.0
    score = score.clip(0, 100)
    return {'score': score}

# ==================== 周期回归趋势线 PRTrend ====================
# 与 indicators/PRTrend.py（Backtrader 版）同一算法：以最后一根 K 线为基准，取最近 N 根
# 上/下轨字段各做一元线性回归，把拟合直线回填该窗口，并解析求两条直线的交点。
# 网页只需要「最新窗口」这一组线段（旧窗口会被新窗口覆盖），所以这里直接算最后 N 根，
# 不用像 bt 版那样逐根回溯。
_PRT_COLORS = {'a': '#ff9800', 'b': '#42a5f5', 'c': '#ab47bc'}
_PRT_SLOTS = ('a', 'b', 'c')
_PRT_FIELDS = ('high', 'low', 'open', 'close', 'hl2', 'hlc3', 'ohlc4')
_PRT_PRESETS = {'HL': ('high', 'low'), 'OC': ('open', 'close')}


def _prt_field_array(df, name):
    """取 PRTrend 的价格序列，支持基础价与派生价（hl2/hlc3/ohlc4）"""
    h = df['high'].to_numpy(dtype=float)
    l = df['low'].to_numpy(dtype=float)
    o = df['open'].to_numpy(dtype=float)
    c = df['close'].to_numpy(dtype=float)
    if name == 'high':
        return h
    if name == 'low':
        return l
    if name == 'open':
        return o
    if name == 'close':
        return c
    if name == 'hl2':
        return (h + l) / 2.0
    if name == 'hlc3':
        return (h + l + c) / 3.0
    if name == 'ohlc4':
        return (o + h + l + c) / 4.0
    return None


def calc_prtrend(df, periods=(5, 20, 60), field_pair='HL', upper_field=None, lower_field=None,
                 show_cross=True, extend_forward=0):
    """多周期双字段回归趋势线 + 交点；返回扁平序列，键名与 bt_indicator_bridge 输出一致"""
    n = len(df)
    up, lo = upper_field, lower_field
    if up is None or lo is None:
        pair = str(field_pair or 'HL').upper()
        if pair not in _PRT_PRESETS:
            pair = 'HL'
        pu, pl = _PRT_PRESETS[pair]
        up = up if up is not None else pu
        lo = lo if lo is not None else pl
    up, lo = str(up).lower(), str(lo).lower()
    # 允许上下轨选择同一价格类型（此时两条回归线重合），仅白名单外的字段才回退
    if up not in _PRT_FIELDS or lo not in _PRT_FIELDS:
        up, lo = 'high', 'low'

    # 周期归一化：整数、≥2、去重、最多 3 个
    try:
        seq = list(periods) if isinstance(periods, (list, tuple)) else [periods]
        ps = [int(float(x)) for x in seq]
    except (TypeError, ValueError):
        ps = []
    norm = []
    for x in ps:
        if x >= 2 and x not in norm:
            norm.append(x)
    periods = norm[:3] or [5, 20, 60]

    out = {'upper': up, 'lower': lo,
           'periods': [int(x) for x in periods],
           'colors': [_PRT_COLORS[s] for s in _PRT_SLOTS[:len(periods)]]}
    for s in _PRT_SLOTS:
        out['hi_' + s] = np.full(n, np.nan)
        out['lo_' + s] = np.full(n, np.nan)
        out['cx_' + s] = np.full(n, np.nan)

    if n == 0:
        return out

    up_arr = _prt_field_array(df, up)
    lo_arr = _prt_field_array(df, lo)
    if up_arr is None or lo_arr is None:
        up, lo = 'high', 'low'
        out['upper'], out['lower'] = up, lo
        up_arr = _prt_field_array(df, up)
        lo_arr = _prt_field_array(df, lo)
    for s, period in zip(_PRT_SLOTS, periods):
        if n < period:
            continue
        y1 = up_arr[n - period:]
        y2 = lo_arr[n - period:]
        if np.isnan(y1).any() or np.isnan(y2).any():
            continue
        x = np.arange(-(period - 1), 1, dtype=float)
        slope_u, int_u = np.polyfit(x, y1, 1)
        slope_l, int_l = np.polyfit(x, y2, 1)
        out['hi_' + s][n - period:] = slope_u * x + int_u
        out['lo_' + s][n - period:] = slope_l * x + int_l
        if not show_cross or abs(slope_u - slope_l) < 1e-12:
            continue
        x_cross = (int_l - int_u) / (slope_u - slope_l)
        y_cross = slope_u * x_cross + int_u
        if x_cross < -(period - 1) or x_cross > max(0.0, float(extend_forward)) or not np.isfinite(y_cross):
            continue
        idx = int(round(x_cross))
        if idx > 0:
            continue
        out['cx_' + s][(n - 1) + idx] = y_cross
    return out


# ==================== 统一入口 ====================
def calc_indicators(df, params=None):
    """
    df 列：trade_date, open, high, low, close, vol, amount（升序）
    params: dict，可覆盖默认参数
    返回 dict：
      uma, supertrend, vidya, macd, rsi, kdj, obv, xtr, bb_score, prtrend
    """
    p = params or {}
    n = len(df)
    close = df['close']

    out = {'length': n}

    # 主图
    out['uma'] = calc_uma(close, p.get('ma_type', 'EMA'),
                          tuple(p.get('ma_periods', [20, 50, 100])))
    out['supertrend'] = calc_supertrend(df, p.get('st_period', 10), p.get('st_mult', 3.0))
    out['vidya'] = calc_vidya(df, p.get('vidya_len', 10), p.get('vidya_mom', 20),
                              p.get('vidya_atr', 200), p.get('vidya_sma', 15),
                              p.get('vidya_band', 2.0))

    # 副图
    out['macd'] = calc_macd(close, p.get('macd_fast', 12), p.get('macd_slow', 26), p.get('macd_signal', 9))
    out['rsi'] = calc_rsi(close, p.get('rsi_length', 14), p.get('rsi_ma_length', 14), p.get('rsi_ma_type', 'SMA'))
    out['kdj'] = calc_kdj(df, p.get('kdj_period', 9), p.get('kdj_signal', 3))
    out['obv'] = calc_obv(df, p.get('obv_ma', 20))
    out['xtr'] = calc_xtr(df, p.get('xtr_period', 14), p.get('xtr_ma', 9), p.get('xtr_band', 20), p.get('xtr_mult', 2.0))
    out['adx'] = calc_adx(df, p.get('adx_period', 14))
    out['bb_score'] = calc_bb_score(close)

    # 主图：周期回归趋势线（PRTrend，与 indicators/PRTrend.py 同算法）
    out['prtrend'] = calc_prtrend(
        df, p.get('prt_periods', (5, 20, 60)), p.get('prt_field_pair', 'HL'),
        upper_field=p.get('prt_upper_field'), lower_field=p.get('prt_lower_field'),
        show_cross=p.get('prt_show_cross', True), extend_forward=p.get('prt_extend_forward', 0))

    # 成交量均线（前端用）
    out['vol_ma5'] = _sma(df['vol'], 5)
    out['vol_ma10'] = _sma(df['vol'], 10)

    # 序列转 list（NaN -> None）
    def ser2list(s):
        if isinstance(s, pd.Series):
            vals = s.tolist()
        else:
            vals = np.asarray(s).tolist()
        out = []
        for x in vals:
            if x is None:
                out.append(None)
                continue
            try:
                f = float(x)
                out.append(None if np.isnan(f) else round(f, 4))
            except (TypeError, ValueError):
                out.append(x)
        return out

    def _convert(obj):
        if isinstance(obj, pd.Series):
            return ser2list(obj)
        if isinstance(obj, np.ndarray):
            return ser2list(obj)
        if isinstance(obj, dict):
            return {k: _convert(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_convert(x) for x in obj]
        return obj

    out = _convert(out)
    return out
