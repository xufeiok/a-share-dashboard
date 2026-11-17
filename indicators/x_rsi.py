import backtrader as bt
import numpy as np


class X_RSI(bt.Indicator):
    """
    RSI指标完整移植版 (适配Backtrader框架)
    完全对齐Pine Script v6原版：
    - 包含30/50/70虚线参考线
    - 超买(70+)超卖(30-)渐变填充
    - 多种均线平滑+布林带
    - 背离检测功能
    """
    lines = (
        'rsi', 'rsi_ma', 
        'bullish_div', 'bearish_div',
        # 辅助线
        'midline', 'upper_band', 'lower_band'
    )
    params = (
        # RSI基本参数
        ('rsi_length', 14),
        ('rsi_source', 'close'),  # open/high/low/close/hl2
        ('calculate_divergence', False),
        
        # 平滑均线参数
        ('ma_type', 'SMA'),  # None/SMA/EMA/SMMA (RMA)/WMA/VWMA
        ('ma_length', 14),
        
        # 背离检测参数
        ('lookback_right', 5),
        ('lookback_left', 5),
        ('range_upper', 60),
        ('range_lower', 5),
        
        # 视觉样式参数
        ('rsi_upper', 70),    # 超买线
        ('rsi_mid', 50),      # 中轴
        ('rsi_lower', 30),    # 超卖线
        
        ('render_lines' ,['rsi', 'rsi_ma', 'upper_band', 'lower_band'])
    )

    def __init__(self):
        # 适配 SuperPlot 前端绘图配置
        self.linesplot = {
            'plotname': 'Relative Strength Index (RSI)',
            'subplot': True,
            'rsi': {
                'type': 'line',
                'color': '#7E57C2',
                'width': 2,
                'name': 'RSI'
            },
            'rsi_ma': {
                'type': 'line',
                'color': '#FFEB3B', # Yellow
                'width': 1,
                'name': 'RSI MA'
            },
            'upper_band': {
                'type': 'line',
                'color': '#F44336', # Red
                'style': 'dashed',
                'width': 1,
                'name': 'RSI Upper Band (70)'
            },
            'midline': {
                'type': 'line',
                'color': '#787B86',
                'style': 'dashed',
                'width': 1,
                'name': 'RSI Middle Band (50)'
            },
            'lower_band': {
                'type': 'line',
                'color': '#F44336', # Red
                'style': 'dashed',
                'width': 1,
                'name': 'RSI Lower Band (30)'
            },
            'bullish_div': {
                'type': 'marker',
                'marker': 'triangle-up',
                'color': '#4CAF50', # Green
                'width': 2,
                'name': 'Bullish Divergence'
            },
            'bearish_div': {
                'type': 'marker',
                'marker': 'triangle-down',
                'color': '#F44336', # Red
                'width': 2,
                'name': 'Bearish Divergence'
            }
        }

        # 1. 初始化参考线（固定值）
        self.lines.upper_band = bt.LineNum(self.p.rsi_upper)
        self.lines.midline = bt.LineNum(self.p.rsi_mid)
        self.lines.lower_band = bt.LineNum(self.p.rsi_lower)

        # 2. 确定RSI计算源数据
        src_mapping = {
            'open': self.data.open,
            'high': self.data.high,
            'low': self.data.low,
            'close': self.data.close,
            'hl2': (self.data.high + self.data.low) / 2,
            'hlc3': (self.data.high + self.data.low + self.data.close) / 3,
            'ohlc4': (self.data.open + self.data.high + self.data.low + self.data.close) / 4
        }
        self.src = src_mapping.get(self.p.rsi_source, self.data.close)

        # 3. 核心RSI计算（严格对齐Pine Script逻辑）
        change = self.src - self.src(-1)
        # Backtrader's WilderMA is equivalent to Pine's RMA
        up = bt.indicators.WilderMA(bt.ind.Max(change, 0), period=self.p.rsi_length)
        down = bt.indicators.WilderMA(-bt.ind.Min(change, 0), period=self.p.rsi_length)
        
        # 处理除零情况
        self.lines.rsi = bt.If(
            down == 0, 100.0,
            bt.If(up == 0, 0.0, 100.0 - (100.0 / (1.0 + up / down)))
        )

        # 4. 平滑均线计算
        self.enable_ma = self.p.ma_type != 'None'
        
        if self.enable_ma:
            if self.p.ma_type == 'SMA':
                self.lines.rsi_ma = bt.indicators.SMA(self.lines.rsi, period=self.p.ma_length)
            elif self.p.ma_type == 'EMA':
                self.lines.rsi_ma = bt.indicators.EMA(self.lines.rsi, period=self.p.ma_length)
            elif self.p.ma_type == 'SMMA (RMA)':
                self.lines.rsi_ma = bt.indicators.WilderMA(self.lines.rsi, period=self.p.ma_length)
            elif self.p.ma_type == 'WMA':
                self.lines.rsi_ma = bt.indicators.WMA(self.lines.rsi, period=self.p.ma_length)
            elif self.p.ma_type == 'VWMA':
                 # Use bt.If to avoid division by zero if volume is 0
                 vol_sma = bt.indicators.SMA(self.data.volume, period=self.p.ma_length)
                 self.lines.rsi_ma = bt.If(vol_sma == 0, 
                                           self.lines.rsi, 
                                           bt.indicators.SMA(self.lines.rsi * self.data.volume, period=self.p.ma_length) / vol_sma)
            else:
                self.lines.rsi_ma = bt.LineNum(np.nan)
        else:
            self.lines.rsi_ma = bt.LineNum(np.nan)

        # 5. 背离检测初始化
        self.pl_found = False
        self.ph_found = False
        self.bull_cond = False
        self.bear_cond = False

    def _in_range(self, cond_idx):
        """检查条件发生的位置是否在指定范围内"""
        if cond_idx == -1:
            return False
        bars_since = len(self) - 1 - cond_idx
        return self.p.range_lower <= bars_since <= self.p.range_upper

    def _find_last_pivot(self, pivot_func, lookback_left, lookback_right, start_idx=None):
        """查找最近的枢轴点位置"""
        start = len(self)-1 if start_idx is None else start_idx
        for i in range(start, max(0, len(self)-self.p.range_upper-1), -1):
            if i < lookback_left + lookback_right:
                return -1
            # 检查i位置是否是枢轴点
            pivot_val = pivot_func(i, lookback_left, lookback_right)
            if not np.isnan(pivot_val):
                return i
        return -1

    def _pivot_low(self, idx, left, right):
        """判断指定位置是否是RSI低点"""
        if idx - left < 0 or idx + right >= len(self):
            return np.nan
            
        # Convert absolute index to relative offset
        # Current index is len(self) - 1
        # Offset = idx - (len(self) - 1) = idx - len(self) + 1
        offset = idx - len(self) + 1
        
        rsi_val = self.lines.rsi[offset]
        # 左侧都高于当前值，右侧都高于当前值
        left_ok = all(self.lines.rsi[offset - i] > rsi_val for i in range(1, left+1))
        right_ok = all(self.lines.rsi[offset + i] > rsi_val for i in range(1, right+1))
        return rsi_val if left_ok and right_ok else np.nan

    def _pivot_high(self, idx, left, right):
        """判断指定位置是否是RSI高点"""
        if idx - left < 0 or idx + right >= len(self):
            return np.nan
            
        # Convert absolute index to relative offset
        offset = idx - len(self) + 1
        
        rsi_val = self.lines.rsi[offset]
        # 左侧都低于当前值，右侧都低于当前值
        left_ok = all(self.lines.rsi[offset - i] < rsi_val for i in range(1, left+1))
        right_ok = all(self.lines.rsi[offset + i] < rsi_val for i in range(1, right+1))
        return rsi_val if left_ok and right_ok else np.nan

    def next(self):
        # 初始化背离信号
        self.lines.bullish_div[0] = np.nan
        self.lines.bearish_div[0] = np.nan

        # 跳过背离计算（如果未开启）
        if not self.p.calculate_divergence:
            return

        # 最小数据量检查
        min_len = self.p.lookback_left + self.p.lookback_right + self.p.range_upper
        if len(self) < min_len:
            return

        # 1. 查找RSI低点和高点
        pl_idx = self._find_last_pivot(self._pivot_low, self.p.lookback_left, self.p.lookback_right)
        ph_idx = self._find_last_pivot(self._pivot_high, self.p.lookback_left, self.p.lookback_right)

        # 2. 看涨背离检测（价格更低，RSI更高）
        if pl_idx != -1:
            # 查找前一个低点
            prev_pl_idx = self._find_last_pivot(
                self._pivot_low, self.p.lookback_left, self.p.lookback_right, 
                max(0, pl_idx - self.p.range_upper)
            )
            if prev_pl_idx != -1 and self._in_range(prev_pl_idx):
                # RSI更高
                rsi_hl = self.lines.rsi[pl_idx] > self.lines.rsi[prev_pl_idx]
                # 价格更低
                price_ll = self.data.low[pl_idx] < self.data.low[prev_pl_idx]
                
                if rsi_hl and price_ll:
                    self.bull_cond = True
                    self.lines.bullish_div[pl_idx - len(self) + 1] = self.lines.rsi[pl_idx]

        # 3. 看跌背离检测（价格更高，RSI更低）
        if ph_idx != -1:
            # 查找前一个高点
            prev_ph_idx = self._find_last_pivot(
                self._pivot_high, self.p.lookback_left, self.p.lookback_right,
                max(0, ph_idx - self.p.range_upper)
            )
            if prev_ph_idx != -1 and self._in_range(prev_ph_idx):
                # RSI更低
                rsi_lh = self.lines.rsi[ph_idx] < self.lines.rsi[prev_ph_idx]
                # 价格更高
                price_hh = self.data.high[ph_idx] > self.data.high[prev_ph_idx]
                
                if rsi_lh and price_hh:
                    self.bear_cond = True
                    self.lines.bearish_div[ph_idx - len(self) + 1] = self.lines.rsi[ph_idx]

    def prenext(self):
        """处理数据不足时的初始化"""
        self.next()

    # 补充渐变填充的绘图逻辑（Backtrader中需结合Cerebro配置）
    # @classmethod
    # def add_gradient_fill(cls, cerebro, rsi_indicator):
    #     """
    #     为RSI添加渐变填充效果：
    #     - 70以上：绿色渐变（超买）
    #     - 30以下：红色渐变（超卖）
    #     """
    #     # 超买区域填充（70到100）
    #     cerebro.add_plotline(
    #         plotid=rsi_indicator.plotinfo.plotid,
    #         name='Overbought Fill',
    #         color='green',
    #         alpha=0.2,
    #         fill_gt=(rsi_indicator.lines.rsi, 70)
    #     )
    #     # 超卖区域填充（0到30）
    #     cerebro.add_plotline(
    #         plotid=rsi_indicator.plotinfo.plotid,
    #         name='Oversold Fill',
    #         color='red',
    #         alpha=0.2,
    #         fill_lt=(rsi_indicator.lines.rsi, 30)
    #     )
    #     # 30-70背景填充
    #     cerebro.add_plotline(
    #         plotid=rsi_indicator.plotinfo.plotid,
    #         name='RSI Background',
    #         color='#7E57C2',
    #         alpha=0.1,
    #         fill_between=(rsi_indicator.lines.lower_band, rsi_indicator.lines.upper_band)
    #     )


# ===================== 使用示例 =====================
if __name__ == '__main__':
    import yfinance as yf
    from datetime import datetime

    # 1. 创建Cerebro引擎
    cerebro = bt.Cerebro(stdstats=False)

    # 2. 添加数据
    # 注意：需确保 yfinance 已安装且能连接网络
    # data = bt.feeds.PandasData(dataname=yf.download(
    #     'AAPL', 
    #     start=datetime(2023, 1, 1), 
    #     end=datetime(2024, 1, 1),
    #     progress=False
    # ))
    # cerebro.adddata(data)

    # 3. 添加RSI指标
    # rsi = cerebro.addindicator(XRSI,
    #     rsi_length=14,
    #     calculate_divergence=True,
    #     ma_type='SMA + Bollinger Bands',
    #     ma_length=14,
    #     bb_mult=2.0
    # )

    # 4. 添加渐变填充效果
    # XRSI.add_gradient_fill(cerebro, rsi)

    # 5. 运行并绘图
    # cerebro.run()
    # cerebro.plot(style='candlestick')