# -*- coding: utf-8 -*-
"""
bt_indicator_bridge.py — backtrader 指标桥
让个股行情页指标改用「本地 backtrader 技术路径」运行时计算：
  魔改 backtrader 1.9.74.123 + my_bt_indicator 真指标类（与 Qlibworks 回测环境同源）。

输入 df（列：trade_date, open, high, low, close, vol, amount，升序）
输出 dict：结构与 stock_indicators.calc_indicators 完全一致（uma/supertrend/vidya/macd/rsi/kdj/obv/xtr/bb_score/vol_ma5/vol_ma10），
前端 /api/stock/indicators 消费者零改动。

实现要点：
  1) df → bt.feeds.PandasData → Cerebro(runonce=False)（魔改包指标仅兼容逐根 next 模式，once 批量模式有越界 bug）
  2) BridgeStrategy.__init__ 一次性挂载 9 个 my_bt_indicator 指标类
  3) cerebro.run() 后从 ind.lines.<name>.array 导出全序列（前段预热期为 NaN）
  4) 字段映射到接口结构：bt line 名 → 接口字段（SuperTrend.up_trend→up 等）
  5) macd.colors 为显示派生（前端未消费），仅输出基础色；signal 直接透传 bt 版
  6) 异常时由调用方（server.py）回退 stock_indicators
"""
import sys
import numpy as np
import pandas as pd
import backtrader as bt

# my_bt_indicator 包根目录（用户本地资产，非第三方新增依赖）
_MBI_ROOT = r"E:\Quant\backtrader_superplot\backtrader_superplot\my_bt_indicator"
if _MBI_ROOT not in sys.path:
    sys.path.insert(0, _MBI_ROOT)

# 注意：SuperTrend/EnhancedMACD 模块名与类名同名，my_bt_indicator.indicators.__init__ 的
# `from .xxx import *` 会把包属性覆盖为类，因此必须用「模块.类」形式导入
from my_bt_indicator.indicators.SuperTrend import SuperTrend
from my_bt_indicator.indicators.EnhancedMACD import EnhancedMACD
from my_bt_indicator.indicators.VolumeDynamicVIDYA import VolumeDynamicVIDYA
from my_bt_indicator.indicators import uma as _uma_mod
from my_bt_indicator.indicators import x_xtr as _xtr_mod
from my_bt_indicator.indicators import x_obv as _obv_mod
from my_bt_indicator.indicators import x_kdj as _kdj_mod
from my_bt_indicator.indicators import x_rsi as _rsi_mod
from my_bt_indicator.indicators import bb_trend_score as _bb_mod

# UMA 线色（与 stock_indicators.calc_uma 一致）
_UMA_COLORS = ['#2196F3', '#4CAF50', '#9E9E9E']


class BridgeStrategy(bt.Strategy):
    """桥策略：挂载全部 my_bt_indicator 指标，逐根运行（next 为空即可，数据在 run 后导出）"""

    params = (
        ('st_period', 10), ('st_mult', 3.0),
        ('vidya_len', 10), ('vidya_mom', 20), ('vidya_atr', 200), ('vidya_sma', 15), ('vidya_band', 2.0),
        ('uma_type', 'EMA'), ('uma_periods', (20, 50, 100)),
        ('xtr_period', 14), ('xtr_ma', 9), ('xtr_band', 20), ('xtr_mult', 2.0),
        ('obv_ma', 20),
        ('kdj_period', 9), ('kdj_signal', 3),
        ('rsi_len', 14), ('rsi_ma_len', 14), ('rsi_ma_type', 'SMA'),
        ('macd_fast', 12), ('macd_slow', 26), ('macd_signal', 9),
        ('bb_period', 50), ('bb_dev', 2.0), ('bb_roc', 5), ('bb_acc', 3), ('bb_roll', 60),
        ('bb_w_nbbw', 0.5), ('bb_w_roc', 0.3), ('bb_w_acc', 0.2),
    )

    def __init__(self):
        d = self.data
        p = self.p
        self.inds = {
            'st': SuperTrend(d, period=p.st_period, multiplier=p.st_mult),
            'vidya': VolumeDynamicVIDYA(d, vidya_length=p.vidya_len, vidya_momentum=p.vidya_mom,
                                        atr_period=p.vidya_atr, vidya_sma_period=p.vidya_sma,
                                        band_distance=p.vidya_band),
            'uma': _uma_mod.UMA(d, ma_type=p.uma_type,
                                period1=p.uma_periods[0], period2=p.uma_periods[1], period3=p.uma_periods[2]),
            'xtr': _xtr_mod.X_XTR(d, xtr_period=p.xtr_period, ma_length=p.xtr_ma,
                                  band_period=p.xtr_band, band_mult=p.xtr_mult),
            'obv': _obv_mod.X_OBV(d, ma_length=p.obv_ma),
            'kdj': _kdj_mod.X_KDJ(d, period=p.kdj_period, signal=p.kdj_signal),
            'rsi': _rsi_mod.X_RSI(d, rsi_length=p.rsi_len, ma_length=p.rsi_ma_len, ma_type=p.rsi_ma_type),
            'macd': EnhancedMACD(d, fast_period=p.macd_fast, slow_period=p.macd_slow, signal_period=p.macd_signal),
            'bb': _bb_mod.BBTrendScore(d, bb_period=p.bb_period, bb_dev=p.bb_dev,
                                       roc_period=p.bb_roc, acc_period=p.bb_acc, roll_window=p.bb_roll,
                                       w_nbbw=p.bb_w_nbbw, w_roc=p.bb_w_roc, w_acc=p.bb_w_acc),
        }

    def next(self):
        pass  # 逐根驱动即可，结果统一从 lines.array 导出


def _arr(line):
    """LineBuffer.array → numpy 数组（含预热 NaN，长度=数据长度）"""
    return np.asarray(line.array, dtype=float).copy()


def _derive_supertrend_trend(up, down):
    """bt 版无 trend 线，按接口结构推导：up 有效→1，down 有效→-1，否则保持前值"""
    n = len(up)
    trend = np.zeros(n, dtype=int)
    last = 1
    for i in range(n):
        u, dn = up[i], down[i]
        if not np.isnan(u):
            last = 1
        elif not np.isnan(dn):
            last = -1
        trend[i] = last
    return trend


# MACD 基础色（与 stock_indicators.MACD_COLORS 的 0-4 一致；特殊回溯色 5-8 为显示增强，前端未消费，桥从简）
_MACD_BASE_COLORS = {0: '#cccccc', 1: '#ef5350', 2: '#f48fb1', 3: '#66bb6a', 4: '#4fc3f7'}


def _derive_macd_colors(hist):
    """hist → 颜色数组（基础色：红涨绿跌 + 走弱变粉/浅蓝）"""
    out = []
    prev = np.nan
    for ch in hist:
        if np.isnan(ch):
            out.append('#cccccc')
            prev = ch
            continue
        if ch >= 0:
            if np.isnan(prev) or ch >= prev:
                out.append('#ef5350')
            else:
                out.append('#f48fb1')
        else:
            if np.isnan(prev) or ch <= prev:
                out.append('#66bb6a')
            else:
                out.append('#4fc3f7')
        prev = ch
    return out


def calc_bt_indicators(df, params=None):
    """
    桥入口：与 stock_indicators.calc_indicators 同签名、同输出结构。
    params 可覆盖各指标默认参数（键名同 stock_indicators 约定）。
    """
    p = dict(params or {})
    n = len(df)
    if n == 0:
        return {'length': 0}

    # df → backtrader feed（index 需为 DatetimeIndex）
    feed_df = df.copy()
    if 'trade_date' in feed_df.columns:
        feed_df = feed_df.set_index(pd.to_datetime(feed_df['trade_date']))
    else:
        feed_df.index = pd.to_datetime(feed_df.index)
    feed_df = feed_df.rename(columns={'vol': 'volume'})

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.PandasData(dataname=feed_df, datetime=None))
    cerebro.addstrategy(BridgeStrategy,
                        st_period=p.get('st_period', 10), st_mult=p.get('st_mult', 3.0),
                        vidya_len=p.get('vidya_len', 10), vidya_mom=p.get('vidya_mom', 20),
                        vidya_atr=p.get('vidya_atr', 200), vidya_sma=p.get('vidya_sma', 15),
                        vidya_band=p.get('vidya_band', 2.0),
                        uma_type=p.get('ma_type', 'EMA'),
                        uma_periods=tuple(p.get('ma_periods', [20, 50, 100])),
                        xtr_period=p.get('xtr_period', 14), xtr_ma=p.get('xtr_ma', 9),
                        xtr_band=p.get('xtr_band', 20), xtr_mult=p.get('xtr_mult', 2.0),
                        obv_ma=p.get('obv_ma', 20),
                        kdj_period=p.get('kdj_period', 9), kdj_signal=p.get('kdj_signal', 3),
                        rsi_len=p.get('rsi_length', 14), rsi_ma_len=p.get('rsi_ma_length', 14),
                        rsi_ma_type=p.get('rsi_ma_type', 'SMA'),
                        macd_fast=p.get('macd_fast', 12), macd_slow=p.get('macd_slow', 26),
                        macd_signal=p.get('macd_signal', 9),
                        bb_period=p.get('bb_period', 50), bb_dev=p.get('bb_dev', 2.0),
                        bb_roc=p.get('roc_period', 5), bb_acc=p.get('acc_period', 3),
                        bb_roll=p.get('roll_window', 60),
                        bb_w_nbbw=p.get('w_nbbw', 0.5), bb_w_roc=p.get('w_roc', 0.3),
                        bb_w_acc=p.get('w_acc', 0.2))
    cerebro.run(runonce=False)  # 魔改包指标仅兼容逐根模式

    strat = cerebro.runstrats[0][0]
    inds = strat.inds
    up = _arr(inds['st'].lines.up_trend)
    down = _arr(inds['st'].lines.down_trend)
    hist = _arr(inds['macd'].lines.hist)

    out = {
        'length': n,
        # 主图
        'uma': {
            f'ma{i + 1}': {'period': int(pg), 'color': _UMA_COLORS[i],
                           'value': _arr(getattr(inds['uma'].lines, f'ma{i + 1}'))}
            for i, pg in enumerate(p.get('ma_periods', [20, 50, 100]))
        },
        'supertrend': {
            'up': up, 'down': down,
            'buy': _arr(inds['st'].lines.buy_signal), 'sell': _arr(inds['st'].lines.sell_signal),
            'trend': _derive_supertrend_trend(up, down),
        },
        'vidya': {
            'vidya': _arr(inds['vidya'].lines.vidya_line),
            'upper': _arr(inds['vidya'].lines.upper_band), 'lower': _arr(inds['vidya'].lines.lower_band),
            'buy': _arr(inds['vidya'].lines.buy_signal), 'sell': _arr(inds['vidya'].lines.sell_signal),
        },
        # 副图
        'macd': {
            'dif': _arr(inds['macd'].lines.dif), 'dea': _arr(inds['macd'].lines.dea),
            'hist': hist, 'signal': _arr(inds['macd'].lines.signal),
            'colors': _derive_macd_colors(hist),
        },
        'rsi': {'rsi': _arr(inds['rsi'].lines.rsi), 'rsi_ma': _arr(inds['rsi'].lines.rsi_ma)},
        'kdj': {'K': _arr(inds['kdj'].lines.K), 'D': _arr(inds['kdj'].lines.D), 'J': _arr(inds['kdj'].lines.J)},
        'obv': {'obv': _arr(inds['obv'].lines.obv), 'obv_ma': _arr(inds['obv'].lines.obv_ma)},
        'xtr': {'xtr': _arr(inds['xtr'].lines.xtr), 'xtr_ma': _arr(inds['xtr'].lines.xtr_ma),
                'top': _arr(inds['xtr'].lines.top), 'bot': _arr(inds['xtr'].lines.bot)},
        'bb_score': {'score': _arr(inds['bb'].lines.bb_trend_score)},
        # 成交量均线（官方 SMA，pandas rolling 与 bt SMA 数值等价）
        'vol_ma5': df['vol'].rolling(5).mean().to_numpy(),
        'vol_ma10': df['vol'].rolling(10).mean().to_numpy(),
    }

    # 与 pandas 版一致的序列→list 转换（NaN → None，保留 4 位小数）
    def ser2list(s):
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

    def convert(obj):
        if isinstance(obj, dict):
            return {k: convert(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [convert(x) for x in obj]
        if isinstance(obj, np.ndarray) or isinstance(obj, pd.Series):
            return ser2list(obj)
        return obj

    return convert(out)


if __name__ == '__main__':
    # 自检：合成 200 根 K 线跑桥，打印各指标长度与末值
    rng = np.random.default_rng(7)
    n = 200
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    o = close + rng.normal(0, 0.5, n)
    df = pd.DataFrame({
        'trade_date': pd.date_range('2025-01-01', periods=n).strftime('%Y-%m-%d'),
        'open': o, 'high': np.maximum(o, close) + rng.uniform(0, 1, n),
        'low': np.minimum(o, close) - rng.uniform(0, 1, n),
        'close': close, 'vol': rng.uniform(1e5, 5e5, n), 'amount': rng.uniform(1e8, 9e8, n),
    })
    res = calc_bt_indicators(df)
    print('length:', res['length'])
    for k, v in res.items():
        if k == 'length':
            continue
        if isinstance(v, dict):
            print(k, {kk: (len(vv) if hasattr(vv, '__len__') else vv) for kk, vv in v.items()})
        else:
            print(k, len(v))
