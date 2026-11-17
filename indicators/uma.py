import backtrader as bt

class UMA(bt.Indicator):
    """
    通用均线指标 (Universal Moving Average)
    - 支持 SMA (简单移动平均), EMA (指数移动平均), WMA (加权移动平均), RMA (Wilder移动平均), HMA (赫尔移动平均)
    - 支持最多3条均线
    - 支持自定义颜色
    - 兼容 SuperPlot 绘图系统
    """
    lines = ('ma1', 'ma2', 'ma3')
    params = (
        ('ma_type', 'EMA'),    # 均线类型: 'SMA', 'EMA', 'WMA', 'RMA', 'HMA'
        ('period1', 20),       # 第1条均线周期 (设为0不显示)
        ('period2', 50),       # 第2条均线周期 (设为0不显示)
        ('period3', 100),      # 第3条均线周期 (设为0不显示)
        ('render_lines', ['ma1', 'ma2', 'ma3']),
    )
    
    plotinfo = dict(subplot=False)

    def __init__(self):
        # 定义颜色和线宽
        color1 = '#2196F3' # 第1条均线颜色 (Blue)
        color2 = '#4CAF50' # 第2条均线颜色 (Green)
        color3 = '#9E9E9E' # 第3条均线颜色 (Gray)
        width = 2          # 线宽

        # 1. 映射均线类型
        ma_map = {
            'SMA': bt.indicators.SMA,
            'EMA': bt.indicators.EMA,
            'WMA': bt.indicators.WMA,
            'RMA': bt.indicators.WilderMA, # Backtrader内置的RMA (Wilder's MA)
            'HMA': bt.indicators.HullMovingAverage,
        }
        
        # 获取对应的指标类，默认为 SMA
        ma_cls = ma_map.get(self.p.ma_type.upper(), bt.indicators.SMA)
        
        # 2. 初始化 SuperPlot 配置字典
        self.linesplot = {
            'plotname': f'Universal MA ({self.p.ma_type})',
            'subplot': False,
        }
        
        # 3. 创建均线逻辑
        # MA 1
        if self.p.period1 > 0:
            # 计算指标并绑定到 line
            self.lines.ma1 = ma_cls(self.data, period=self.p.period1)
            # 配置绘图
            self.linesplot['ma1'] = {
                'type': 'line',
                'color': color1,
                'width': width,
                'name': f'{self.p.ma_type} {self.p.period1}'
            }
        
        # MA 2
        if self.p.period2 > 0:
            self.lines.ma2 = ma_cls(self.data, period=self.p.period2)
            self.linesplot['ma2'] = {
                'type': 'line',
                'color': color2,
                'width': width,
                'name': f'{self.p.ma_type} {self.p.period2}'
            }
            
        # MA 3
        if self.p.period3 > 0:
            self.lines.ma3 = ma_cls(self.data, period=self.p.period3)
            self.linesplot['ma3'] = {
                'type': 'line',
                'color': color3,
                'width': width,
                'name': f'{self.p.ma_type} {self.p.period3}'
            }

    def next(self):
        # 对于未使用的 line，填充 NaN 以防止绘制错误的 0 值
        if self.p.period1 <= 0:
            self.lines.ma1[0] = float('nan')
            
        if self.p.period2 <= 0:
            self.lines.ma2[0] = float('nan')
            
        if self.p.period3 <= 0:
            self.lines.ma3[0] = float('nan')
