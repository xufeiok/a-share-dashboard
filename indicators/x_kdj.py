
import backtrader as bt
import numpy as np


class X_KDJ(bt.Indicator):
    """
    Pine Script KDJ Indicator 移植版 (适配 SuperPlot 框架)
    Pine源码参考:
    study("KDJ Indicator - @iamaltcoin", shorttitle="GM_V2_KDJ")
    ilong = input(9, title="period")
    isig = input(3, title="signal")
    
    bcwsma(s,l,m) => 
        _s = s
        _l = l
        _m = m
        _bcwsma = (_m*_s+(_l-_m)*nz(_bcwsma[1]))/_l
        _bcwsma

    c = close
    h = highest(high, ilong)
    l = lowest(low,ilong)
    RSV = 100*((c-l)/(h-l))
    pK = bcwsma(RSV, isig, 1)
    pD = bcwsma(pK, isig, 1)
    pJ = 3 * pK-2 * pD
     
    plot(pK, color=orange)
    plot(pD, color=lime)
    plot(pJ, color=fuchsia)
    bgcolor(pJ>pD? green : red, transp=70)
    """
    lines = (
        'K', 'D', 'J',          # KDJ核心线条
        'j_above_d',            # J>D判断（用于背景色/信号）
    )
    params = (
        ('period', 9),          # 对应Pine的ilong
        ('signal', 3),          # 对应Pine的isig
        ('bg_alpha', 0.3),      # 对应transp=70（alpha=0.3）
        ('render_lines', ['K', 'D', 'J', 'j_above_d'])
    )

    def __init__(self):
        # 1. 基础数据计算（对应Pine的h/l/c）
        self.highest_high = bt.indicators.Highest(self.data.high, period=self.p.period)
        self.lowest_low = bt.indicators.Lowest(self.data.low, period=self.p.period)
        
        # 2. RSV计算（避免除零）
        self.rsv = 100 * ((self.data.close - self.lowest_low) / 
                          bt.If(self.highest_high != self.lowest_low, 
                                self.highest_high - self.lowest_low, 1))
        
        # 3. BCWSMA状态初始化（递推缓存）
        self.prev_K = np.nan
        self.prev_D = np.nan
        
        # 4. 图形配置（完全对齐SuperTrendPine格式）
        self.linesplot = {
            'plotname': 'GM_V2_KDJ (Pine)',
            'subplot': True,      # KDJ默认独立子图（符合金融图表习惯）
            'K': {
                'type': 'line', 
                'color': '#FFA500',  # orange
                'width': 2, 
                'name': 'K Line'
            },
            'D': {
                'type': 'line', 
                'color': '#00FF00',  # lime
                'width': 2, 
                'name': 'D Line'
            },
            'J': {
                'type': 'line', 
                'color': '#FF00FF',  # fuchsia
                'width': 2, 
                'name': 'J Line'
            },
            'j_above_d': {
                'type': 'signal', 
                'color': '#008000',  # green（J>D）
                'width': 1, 
                'name': 'J > D',
                'plotskip': False    # 可选显示信号标记
            },

        }

    # 内部BCWSMA实现（完全复刻Pine逻辑）
    def _bcwsma(self, src, length, multiplier, prev_val):
        if np.isnan(prev_val):
            return src
        return (multiplier * src + (length - multiplier) * prev_val) / length

    def next(self):
        # 1. 核心KDJ计算（逐K线递推）
        current_rsv = self.rsv[0]
        
        # 计算K值（BCWSMA(RSV, signal, 1)）
        self.lines.K[0] = self._bcwsma(current_rsv, self.p.signal, 1, self.prev_K)
        self.prev_K = self.lines.K[0]
        
        # 计算D值（BCWSMA(K, signal, 1)）
        self.lines.D[0] = self._bcwsma(self.lines.K[0], self.p.signal, 1, self.prev_D)
        self.prev_D = self.lines.D[0]
        
        # 计算J值（3*K - 2*D）
        self.lines.J[0] = 3 * self.lines.K[0] - 2 * self.lines.D[0]
        
        # 2. J>D判断（对应bgcolor逻辑）
        self.lines.j_above_d[0] = 1.0 if self.lines.J[0] > self.lines.D[0] else 0.0
        


