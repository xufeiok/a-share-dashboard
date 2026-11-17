import backtrader as bt
import numpy as np


class X_XTR(bt.Indicator):
    """
    XTR Indicator (量价趋势强度指标)

    功能解读 (按作用强弱排序)：
    1. 趋势爆发力识别：正值代表多头，负值代表空头。数值结合了价格幅度和相对成交量，能敏锐捕捉“有量能支撑”的真趋势，过滤无量假突破。
    2. 动能背离预警：当价格创新高/低，而 XTR 指标未同步创新高/低时，提示原有趋势量能衰竭，往往是反转前兆。
    3. 极端情绪监测：XTR 突破上轨（黄色虚线）提示超买（可能回调或极强单边），跌破下轨提示超卖。
    4. 波动率分析：上下轨道的收缩与扩张反映市场活跃度，轨道长时间收窄后往往预示变盘。

    计算逻辑：
    1. 计算每根K线的力道值 (Force)：
       - 基础公式 (Price Component): 0.9*C - 0.7*O - 0.1*H - 0.1*L
       - 标准化 (Normalize=True, 默认):
         - Price Force = Price Component / Open (转化为相对于开盘价的变化率)
         - Volume Force = Volume / SMA(Volume, period*5) (相对成交量)
         - Total Force = Price Force * Volume Force * 100
       - 非标准化 (Normalize=False):
         - Total Force = Price Component * Volume
    2. 对力道值进行线性加权求和 (Weighted Sum)，周期为 xtr_period
       - 离当前越近权重越大 (1, 2, ..., N)
    3. 计算 XTR 的均线 (XTR_MA)
    4. 计算 XTR 的上下轨 (Bollinger Bands based on XTR)
       - Upper = XTR_MA + StdDev(XTR) * Mult
       - Lower = XTR_MA - StdDev(XTR) * Mult
    """
    lines = ('xtr', 'xtr_ma', 'top', 'bot')
    
    params = (
        # XTR 计算参数
        ('xtr_period', 14),
        ('normalize', True), # 是否启用标准化，建议启用以适应不同价格和成交量的交易对
        
        # 均线参数
        ('ma_type', 'SMA'),  # None/SMA/EMA/SMMA (RMA)/WMA
        ('ma_length', 9),

        # 轨道参数
        ('band_period', 20),
        ('band_mult', 2.0),
        
        # 视觉样式参数
        ('render_lines', ['xtr', 'xtr_ma', 'top', 'bot'])
    )

    def __init__(self):
        # 适配 SuperPlot 前端绘图配置
        self.linesplot = {
            'plotname': 'XTR Indicator',
            'subplot': True,
            'xtr': {
                'type': 'line',
                'color': '#2962FF',  # Blue
                'width': 2,
                'name': 'XTR'
            },
            'xtr_ma': {
                'type': 'line',
                'color': '#FF6D00',  # Orange
                'width': 1,
                'name': 'XTR MA'
            },
            'top': {
                'type': 'line',
                'color': '#FFD700',  # Yellow
                'width': 1,
                'style': 'dashed',
                'name': 'Upper Band'
            },
            'bot': {
                'type': 'line',
                'color': '#FFD700',  # Yellow
                'width': 1,
                'style': 'dashed',
                'name': 'Lower Band'
            }
        }

        # 1. 计算单根K线的 Raw Force
        # 公式推导：
        # Up:   (C-O)*0.8 + (O-L)*0.1 - (H-C)*0.1 = 0.9C - 0.7O - 0.1L - 0.1H
        # Down: -[(O-C)*0.8 + (H-O)*0.1 - (C-L)*0.1] = 0.9C - 0.7O - 0.1H - 0.1L
        # 结果一致，直接使用统一公式
        
        price_component = (
            0.9 * self.data.close - 
            0.7 * self.data.open - 
            0.1 * self.data.high - 
            0.1 * self.data.low
        )

        if self.p.normalize:
            # 标准化逻辑：
            # 1. 价格部分除以开盘价，转换为相对于当期开盘价的变化幅度，消除价格量级差异
            # 这样更能体现当期价格变化的力度
            # Use DivByZero for vectorization safety
            norm_price = bt.DivByZero(price_component, self.data.open, 0.0)
            
            # 2. 成交量部分除以均量，转换为相对成交量(RVOL)，消除成交量级差异
            # 使用 xtr_period * 5 作为均量周期，确保基准稳定性
            vol_ma = bt.indicators.SMA(self.data.volume, period=self.p.xtr_period * 5)
            # Avoid division by zero
            norm_vol = bt.If(vol_ma == 0, 1.0, self.data.volume / vol_ma)
            
            # 最终 Force 为无量纲数值
            # 放大 100 倍便于阅读 (类似百分比)
            raw_force = norm_price * norm_vol * 100.0
        else:
            raw_force = price_component * self.data.volume

        # 2. 计算加权和 (Weighted Sum)

        # 2. 计算加权和 (Weighted Sum)
        # Backtrader 的 WMA 是 Weighted Average = Sum(val * weight) / Sum(weight)
        # 我们需要的是 Sum(val * weight)
        # 所以 XTR = WMA * Sum(weight)
        # Sum(weight) for period N = N * (N + 1) / 2
        
        n = self.p.xtr_period
        sum_weights = n * (n + 1) / 2.0
        
        # 注意：WMA 即使有 NaN 也会尝试计算（如果数据足够），
        # 但为了严谨，我们在数据不足 period 时结果应为 NaN。
        # bt.indicators.WMA 会自动处理最小周期。
        
        wma = bt.indicators.WMA(raw_force, period=n)
        self.lines.xtr = wma * sum_weights

        # 3. 计算 XTR 的均线
        self.enable_ma = self.p.ma_type != 'None'
        
        if self.enable_ma:
            if self.p.ma_type == 'SMA':
                self.lines.xtr_ma = bt.indicators.SMA(self.lines.xtr, period=self.p.ma_length)
            elif self.p.ma_type == 'EMA':
                self.lines.xtr_ma = bt.indicators.EMA(self.lines.xtr, period=self.p.ma_length)
            elif self.p.ma_type == 'SMMA (RMA)':
                self.lines.xtr_ma = bt.indicators.WilderMA(self.lines.xtr, period=self.p.ma_length)
            elif self.p.ma_type == 'WMA':
                self.lines.xtr_ma = bt.indicators.WMA(self.lines.xtr, period=self.p.ma_length)
            else:
                self.lines.xtr_ma = bt.LineNum(np.nan)
        else:
            self.lines.xtr_ma = bt.LineNum(np.nan)
        
        # 4. 计算上下轨 (Bollinger Bands based on XTR)
        # 通常布林带是基于价格的SMA，这里我们基于 XTR 的值
        # 中轨通常就是 SMA(XTR)，如果 ma_type 是 SMA 且 period 一致，可以直接复用，但为了独立性重新计算
        
        # 计算标准差
        stddev = bt.indicators.StdDev(self.lines.xtr, period=self.p.band_period)
        
        # 计算中轨 (用于计算上下轨的基准，通常是SMA)
        basis = bt.indicators.SMA(self.lines.xtr, period=self.p.band_period)
        
        self.lines.top = basis + stddev * self.p.band_mult
        self.lines.bot = basis - stddev * self.p.band_mult

# ===================== 使用示例 =====================
if __name__ == '__main__':
    # 简单的测试代码
    import pandas as pd
    
    cerebro = bt.Cerebro()
    
    # Create dummy data
    dates = pd.date_range(start='2023-01-01', periods=50)
    data = pd.DataFrame({
        'open': np.random.rand(50) * 100,
        'high': np.random.rand(50) * 100,
        'low': np.random.rand(50) * 100,
        'close': np.random.rand(50) * 100,
        'volume': np.random.randint(100, 1000, 50),
        'openinterest': 0
    }, index=dates)
    
    # Ensure High is highest and Low is lowest to avoid errors logic-wise (though math works regardless)
    data['high'] = data[['open', 'close', 'high']].max(axis=1) + 1
    data['low'] = data[['open', 'close', 'low']].min(axis=1) - 1

    feed = bt.feeds.PandasData(dataname=data)
    cerebro.adddata(feed)

    cerebro.addindicator(X_XTR, xtr_period=5, ma_length=3)

    print("Starting Cerebro with X_XTR")
    cerebro.run()
    print("Finished")
