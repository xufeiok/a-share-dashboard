import backtrader as bt
import numpy as np


class X_OBV(bt.Indicator):
    """
    On Balance Volume (OBV) 指标
    - 包含 OBV 线和 OBV 的均线
    - 均线类型可配置 (SMA/EMA/SMMA/WMA)
    - 独立子图显示
    """
    lines = ('obv', 'obv_ma')
    
    params = (
        # 均线参数
        ('ma_type', 'SMA'),  # None/SMA/EMA/SMMA (RMA)/WMA
        ('ma_length', 20),
        
        # 缩放参数
        ('scale_factor', 1e-9),  # 默认缩放 1,000,000,000 倍 (Billion)
        
        # 视觉样式参数
        ('render_lines', ['obv', 'obv_ma'])
    )

    def __init__(self):
        # 适配 SuperPlot 前端绘图配置
        self.linesplot = {
            'plotname': 'On Balance Volume (OBV)',
            'subplot': True,
            'obv': {
                'type': 'line',
                'color': '#2962FF',  # Blue
                'width': 2,
                'name': 'OBV (B)'  # 标注单位 Billion
            },
            'obv_ma': {
                'type': 'line',
                'color': '#FF6D00',  # Orange
                'width': 1,
                'name': 'OBV MA'
            }
        }

        # 1. 计算 OBV
        # OBV = Previous OBV + Volume (if Close > Previous Close)
        #       Previous OBV - Volume (if Close < Previous Close)
        #       Previous OBV (if Close == Previous Close)
        
        change = self.data.close - self.data.close(-1)
        
        # Determine volume direction
        # If change > 0, vol; if change < 0, -vol; else 0
        vol_flow = bt.If(change > 0, self.data.volume, 
                         bt.If(change < 0, -self.data.volume, 0.0))
        
        # Accumulate volume flow and apply scale
        self.lines.obv = bt.indicators.Accum(vol_flow) * self.p.scale_factor

        # 2. 计算均线
        self.enable_ma = self.p.ma_type != 'None'
        
        if self.enable_ma:
            if self.p.ma_type == 'SMA':
                self.lines.obv_ma = bt.indicators.SMA(self.lines.obv, period=self.p.ma_length)
            elif self.p.ma_type == 'EMA':
                self.lines.obv_ma = bt.indicators.EMA(self.lines.obv, period=self.p.ma_length)
            elif self.p.ma_type == 'SMMA (RMA)':
                self.lines.obv_ma = bt.indicators.WilderMA(self.lines.obv, period=self.p.ma_length)
            elif self.p.ma_type == 'WMA':
                self.lines.obv_ma = bt.indicators.WMA(self.lines.obv, period=self.p.ma_length)
            else:
                self.lines.obv_ma = bt.LineNum(np.nan)
        else:
            self.lines.obv_ma = bt.LineNum(np.nan)

# ===================== 使用示例 =====================
if __name__ == '__main__':
    # 简单的测试代码
    cerebro = bt.Cerebro()
    # 这里的 Data feed 需要根据实际环境配置，此处仅为示例
    # cerebro.addindicator(X_OBV)
    # cerebro.run()
