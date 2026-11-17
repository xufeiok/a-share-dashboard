import backtrader as bt
import numpy as np
import math


class SuperTrend(bt.Indicator):
    """
    Pine Script Supertrend 移植版 (适配 SuperPlot 框架)
    Pine源码参考:
    //@version=4
    study("Supertrend", overlay = true, format=format.price, precision=2, resolution="")
    """
    lines = ('up_trend', 'down_trend', 'buy_signal', 'sell_signal')
    params = (
        ('period', 10),
        ('multiplier', 3.0),
        ('use_sma_atr', False), # Pine默认changeATR=true -> 使用RMA(Wilder's)。设为False则使用SMA。
        ('render_lines', ['up_trend', 'down_trend', 'buy_signal', 'sell_signal'])
    )

    def __init__(self):
        # Pine: src = input(hl2, ...) -> (high + low) / 2
        self.src = (self.data.high + self.data.low) / 2
        
        # ATR计算
        # Pine: changeATR ? atr(Periods) : sma(tr, Periods)
        # Pine的atr()函数是RMA (Wilder's Moving Average)
        self.tr = bt.indicators.TrueRange(self.data)
        
        if self.p.use_sma_atr:
             # 使用SMA
             self.atr = bt.indicators.SMA(self.tr, period=self.p.period)
        else:
             # 使用RMA (Wilder's) - 对应Pine的atr()
             self.atr = bt.indicators.WilderMA(self.tr, period=self.p.period)

        # Initialize state variables
        self.curr_up = None
        self.curr_dn = None
        self.curr_trend = 1

        # 图形配置
        self.linesplot = {
            'plotname': 'SuperTrend (Pine)',
            'subplot': False,
            'up_trend': {
                'type': 'line', 'color': '#00FF00', 'width': 1, 'name': 'Up Trend'
            },
            'down_trend': {
                'type': 'line', 'color': '#FF0000', 'width': 1, 'name': 'Down Trend'
            },
             'buy_signal': {
                'type': 'marker', 
                'marker': 'arrowUp', 
                'color': '#00FF00', 
                'size': 1, 
                'name': 'Buy Signal',
                'marker_position': 'belowBar',
                'marker_shape': 'arrowUp',
                'marker_text': 'Buy'
            },
            'sell_signal': {
                'type': 'marker', 
                'marker': 'arrowDown', 
                'color': '#FF0000', 
                'size': 1, 
                'name': 'Sell Signal',
                'marker_position': 'aboveBar',
                'marker_shape': 'arrowDown',
                'marker_text': 'Sell'
            },
            # 隐藏原始supertrend线，只显示分段线
            'supertrend': {'plotskip': True}
        }

        # 设置plotlines以隐藏supertrend
        self.plotlines = dict(supertrend=dict(plotskip=True))

    def next(self):
        # 逻辑移植
        # up = src - (Multiplier * atr)
        # up1 = nz(up[1], up)
        # up := close[1] > up1 ? max(up, up1) : up
        
        atr = self.atr[0]
        src = self.src[0]
        
        # 安全检查：如果输入无效，返回 NaN
        if math.isnan(atr) or math.isnan(src):
            self.lines.up_trend[0] = float('nan')
            self.lines.down_trend[0] = float('nan')
            self.lines.buy_signal[0] = float('nan')
            self.lines.sell_signal[0] = float('nan')
            return

        mult = self.p.multiplier
        
        # 1. 原始上下轨
        raw_up = src - mult * atr
        raw_dn = src + mult * atr
        
        # 初始化状态 (第一根K线或有效K线)
        if self.curr_up is None or math.isnan(self.curr_up):
            self.curr_up = raw_up
            self.curr_dn = raw_dn
            
            # 智能初始化 Trend
            # 如果收盘价在下轨之下，视为下跌趋势；否则默认上涨
            if self.data.close[0] < raw_up:
                self.curr_trend = -1
            else:
                self.curr_trend = 1
                
            self.lines.up_trend[0] = float('nan')
            self.lines.down_trend[0] = float('nan')
            return

        # 获取前一根K线的最终状态
        prev_close = self.data.close[-1]
        prev_up = self.curr_up
        prev_dn = self.curr_dn
        prev_trend = self.curr_trend

        # 2. 递归计算 (Recursive Logic)
        # up := close[1] > up1 ? max(up, up1) : up
        if prev_close > prev_up:
            curr_up = max(raw_up, prev_up)
        else:
            curr_up = raw_up

        # dn := close[1] < dn1 ? min(dn, dn1) : dn
        if prev_close < prev_dn:
            curr_dn = min(raw_dn, prev_dn)
        else:
            curr_dn = raw_dn
            
        # 3. 趋势切换 logic
        # trend := trend == -1 and close > dn1 ? 1 : trend == 1 and close < up1 ? -1 : trend
        curr_trend = prev_trend
        curr_close = self.data.close[0]
        
        if prev_trend == -1 and curr_close > prev_dn:
            curr_trend = 1
        elif prev_trend == 1 and curr_close < prev_up:
            curr_trend = -1
            
        # 更新状态
        self.curr_up = curr_up
        self.curr_dn = curr_dn
        self.curr_trend = curr_trend
        
        # 4. 赋值线条 (Visuals)
        if curr_trend == 1:
            self.lines.up_trend[0] = curr_up
            self.lines.down_trend[0] = float('nan')
        else:
            self.lines.up_trend[0] = float('nan')
            self.lines.down_trend[0] = curr_dn
            
        # 5. 信号 (Signals)
        # buySignal = trend == 1 and trend[1] == -1
        if curr_trend == 1 and prev_trend == -1:
            self.lines.buy_signal[0] = curr_up # 标记在翻转点
        else:
            self.lines.buy_signal[0] = float('nan')
            
        if curr_trend == -1 and prev_trend == 1:
            self.lines.sell_signal[0] = curr_dn # 标记在翻转点
        else:
            self.lines.sell_signal[0] = float('nan')
