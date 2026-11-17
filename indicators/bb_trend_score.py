import backtrader as bt
import backtrader.indicators as btind

class BBTrendScore(bt.Indicator):
    """
    整合后的布林趋势震荡综合指标（BBTrendScore）
    
    基于布林带宽度（NBBW）及其变化率（ROC）和加速度（ACC）的加权综合评分。
    用于识别市场处于震荡还是趋势状态。
    
    评分范围：0 - 100
    - < 30: 震荡市
    - 30 - 70: 弱趋势
    - > 70: 强趋势
    """
    lines = ('bb_trend_score', 'osc_line', 'trend_line')
    
    params = (
        ('bb_period', 50),          # 布林带周期 (建议使用较长周期如 50 以更好地识别趋势)
        ('bb_dev', 2.0),            # 布林带标准差倍数
        ('movav', btind.SMA),       # 移动平均类型 (SMA, EMA, etc.)
        ('price_type', 'close'),    # 价格计算方式: 'close', 'open', 'high', 'low', 'median', 'typical', 'weighted', 'average'
        ('roc_period', 5),          # 带宽变化率周期
        ('acc_period', 3),          # 带宽加速度周期
        ('roll_window', 60),        # 标准化用的滚动窗口（适配动态波动率）
        # 权重系数
        ('w_nbbw', 0.5),            # NBBW权重
        ('w_roc', 0.3),             # ROC权重
        ('w_acc', 0.2),             # ACC权重
        
        # 视觉阈值
        ('threshold_osc', 30),      # 震荡阈值
        ('threshold_trend', 70),    # 趋势阈值
        ('render_lines', ['bb_trend_score', 'osc_line', 'trend_line'])
    )

    def __init__(self):
        # 适配 SuperPlot 前端绘图配置
        self.linesplot = {
            'plotname': 'BB Trend Score',
            'subplot': True,
            'bb_trend_score': {
                'type': 'line',
                'color': '#2196F3',  # Blue
                'width': 2,
                'name': 'Trend Score'
            },
            # 阈值线
            'osc_line': {
                'type': 'line',
                'color': '#9E9E9E',  # Grey
                'style': 'dashed',
                'width': 1,
                'name': 'Oscillation Threshold (30)'
            },
            'trend_line': {
                'type': 'line',
                'color': '#FF9800',  # Orange
                'style': 'dashed',
                'width': 1,
                'name': 'Trend Threshold (70)'
            }
        }
        
        # 初始化辅助线
        self.lines.osc_line = bt.LineNum(self.p.threshold_osc)
        self.lines.trend_line = bt.LineNum(self.p.threshold_trend)

        # 0. 处理 movav 参数 (支持字符串映射)
        movav_map = {
            'SMA': btind.SMA,
            'EMA': btind.EMA,
            'WMA': btind.WMA,
            'DEMA': btind.DEMA,
            'KAMA': btind.KAMA,
            'TEMA': btind.TEMA,  # Replaced TRIMA with TEMA
            # 'T3': btind.T3     # T3 might also be missing in standard bt
        }
        # 如果传入的是字符串，则从映射表中获取类
        ma_cls = movav_map.get(self.p.movav, self.p.movav) if isinstance(self.p.movav, str) else self.p.movav

        # 1. 确定价格输入
        if self.p.price_type == 'open':
            price = self.data.open
        elif self.p.price_type == 'high':
            price = self.data.high
        elif self.p.price_type == 'low':
            price = self.data.low
        elif self.p.price_type == 'median':
            # (High + Low) / 2
            price = (self.data.high + self.data.low) / 2
        elif self.p.price_type == 'typical':
            # (High + Low + Close) / 3
            price = (self.data.high + self.data.low + self.data.close) / 3
        elif self.p.price_type == 'weighted':
            # (High + Low + 2*Close) / 4
            price = (self.data.high + self.data.low + self.data.close * 2) / 4
        elif self.p.price_type == 'average':
            # (Open + High + Low + Close) / 4
            price = (self.data.open + self.data.high + self.data.low + self.data.close) / 4
        elif self.p.price_type == 'midbody':
            # (Open + Close) / 2
            price = (self.data.open + self.data.close) / 2
        else:
            # Default to close
            price = self.data.close

        # 2. 计算基础布林带
        # 注意：这里传入 price 而非 self.data.close
        bb = btind.BollingerBands(
            price,
            period=self.p.bb_period,
            devfactor=self.p.bb_dev,
            movav=ma_cls
        )
        bb_upper = bb.top
        bb_lower = bb.bot
        bb_mid = bb.mid

        # 3. 计算原始三个指标
        # 2.1 归一化布林带宽（NBBW）：(上轨-下轨)/中轨 * 100
        # 注意：这里使用 bt.DivByZero 处理除零风险，尽管中轨通常不为0
        nbbw = (bb_upper - bb_lower) / bb_mid * 100
        
        # 2.2 带宽变化率（NBBW_ROC）：ROC转百分比
        nbbw_roc = btind.RateOfChange(nbbw, period=self.p.roc_period) * 100
        
        # 2.3 带宽加速度（NBBW_ACC）：ROC的ROC转百分比
        nbbw_acc = btind.RateOfChange(nbbw_roc, period=self.p.acc_period) * 100

        # 3. 滚动窗口标准化（Min-Max归一化到[0,1]）
        
        # NBBW标准化
        nbbw_min = btind.Lowest(nbbw, period=self.p.roll_window)
        nbbw_max = btind.Highest(nbbw, period=self.p.roll_window)
        nbbw_range = nbbw_max - nbbw_min
        # 防止分母为0
        nbbw_norm = bt.If(nbbw_range == 0, 0.0, (nbbw - nbbw_min) / nbbw_range)

        # ROC标准化
        roc_min = btind.Lowest(nbbw_roc, period=self.p.roll_window)
        roc_max = btind.Highest(nbbw_roc, period=self.p.roll_window)
        roc_range = roc_max - roc_min
        roc_norm = bt.If(roc_range == 0, 0.0, (nbbw_roc - roc_min) / roc_range)

        # ACC标准化
        acc_min = btind.Lowest(nbbw_acc, period=self.p.roll_window)
        acc_max = btind.Highest(nbbw_acc, period=self.p.roll_window)
        acc_range = acc_max - acc_min
        acc_norm = bt.If(acc_range == 0, 0.0, (nbbw_acc - acc_min) / acc_range)

        # 4. 加权整合+映射到[0,100]区间
        weighted_sum = (
            self.p.w_nbbw * nbbw_norm +
            self.p.w_roc * roc_norm +
            self.p.w_acc * acc_norm
        )
        
        # 原始公式乘以10，这里按需求乘以100
        raw_score = weighted_sum * 100

        # 处理极端值（确保指标在0-100之间）
        self.lines.bb_trend_score = bt.If(
            raw_score < 0, 0.0,
            bt.If(raw_score > 100, 100.0, raw_score)
        )
        
        # 添加参考线 (使用 LineNum 并不是最佳方式如果 SuperPlot 需要它们在 linesplot 中定义为数据线，
        # 但通常 Backtrader 指标通过 lines 定义输出。为了配合 linesplot 的 key，我们需要对应的 line)
        # 不过 x_rsi.py 中使用了 bt.LineNum 赋值给 self.lines.xxx，所以我们也这样做。
        # 我们需要在 lines 声明中添加这些辅助线吗？x_rsi.py 添加了。
        # 让我们修改上面的 lines 声明。

    def _plot_label(self):
        return "BBTrendScore"
