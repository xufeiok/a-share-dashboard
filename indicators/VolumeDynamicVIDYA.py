import backtrader as bt
import math

class VolumeDynamicVIDYA(bt.Indicator):
    """
    成交量动态可变指数均线（VIDYA）指标
    核心功能：
    1. 自适应VIDYA均线（基于CMO动量调整平滑系数）
    2. ATR波动率轨道判定趋势
    3. 成交量量化多空量能
    4. 趋势切换信号标记
    5. 动态可视化配置（贴合SuperPlot框架）
    """
    
    # 定义指标线条（涵盖核心计算+可视化维度）
    lines = (
        'vidya_line',          # 核心VIDYA均线（15周期SMA平滑后）
        'upper_band',          # ATR上轨
        'lower_band',          # ATR下轨
        'smoothed_value',      # 趋势核心价（上涨取下轨，下跌取上轨）
        'buy_signal',          # 上涨趋势信号（▲）
        'sell_signal',         # 下跌趋势信号（▼）
        'volume_delta_pct',    # 成交量差值百分比（仅计算，不绘制）
        'up_trend_volume',     # 上涨趋势累积成交量（仅计算）
        'down_trend_volume',   # 下跌趋势累积成交量（仅计算）
        'invisible_cmo',       # CMO值（仅计算，不绘制）
    )

    # 定义可配置参数（对齐原指标输入）
    params = (
        ('vidya_length', 10),          # VIDYA基础周期
        ('vidya_momentum', 20),        # CMO动量计算周期
        ('band_distance', 2.0),        # ATR轨道距离系数
        ('pivot_left_bars', 3),        # 枢轴点左侧K线数
        ('pivot_right_bars', 3),       # 枢轴点右侧K线数
        ('atr_period', 200),           # ATR计算周期
        ('vidya_sma_period', 15),      # VIDYA后平滑SMA周期
        ('up_color', '#17dfad'),       # 上涨趋势色
        ('down_color', '#dd326b'),     # 下跌趋势色
        ('render_lines', [
            'vidya_line', 'smoothed_value', 'buy_signal', 'sell_signal'  #'upper_band', 'lower_band', 
        ])
    )

    def __init__(self):
        # ===================== 1. 核心指标计算逻辑 =====================
        # 数据源（默认使用收盘价，对齐原指标）
        source = self.data.close
        
        # -------- 步骤1：计算CMO（变化率动量指标） --------
        # 价格动量（变动值）
        momentum = bt.indicators.Momentum(source, period=1)
        # 正向/负向动量求和
        sum_pos_momentum = bt.indicators.SumN(bt.If(momentum >= 0, momentum, 0.0), period=self.p.vidya_momentum)
        sum_neg_momentum = bt.indicators.SumN(bt.If(momentum < 0, -momentum, 0.0), period=self.p.vidya_momentum)
        # CMO绝对值（避免除零错误）
        cmo_numerator = sum_pos_momentum - sum_neg_momentum
        cmo_denominator = bt.If(sum_pos_momentum + sum_neg_momentum == 0, 1e-8, sum_pos_momentum + sum_neg_momentum)
        self.l.invisible_cmo = abs(100 * cmo_numerator / cmo_denominator)

        # -------- 步骤2：计算自适应VIDYA均线 --------
        # 平滑系数α
        alpha = 2 / (self.p.vidya_length + 1)
        # 动态权重（CMO/100）
        weight = self.l.invisible_cmo / 100
        
        # 修正：使用 ExponentialSmoothingDynamic 处理动态 alpha
        dynamic_alpha = alpha * weight
        
        # 迭代计算VIDYA（指数平滑，带CMO权重）
        # 使用 period=1 确保初始值为 source[0]
        vidya_raw = bt.indicators.ExpSmoothingDynamic(
            source,
            period=1,
            alpha=dynamic_alpha
        )
        # VIDYA最终值（15周期SMA平滑，对齐原指标）
        self.l.vidya_line = bt.indicators.SimpleMovingAverage(vidya_raw, period=self.p.vidya_sma_period)

        # -------- 步骤3：计算ATR波动率轨道 --------
        atr = bt.indicators.AverageTrueRange(period=self.p.atr_period)
        self.l.upper_band = self.l.vidya_line + atr * self.p.band_distance
        self.l.lower_band = self.l.vidya_line - atr * self.p.band_distance

        # -------- 步骤4：趋势判定（上穿/下穿轨道） --------
        # 上涨趋势：价格上穿上轨
        self.trend_up = bt.indicators.CrossUp(source, self.l.upper_band)
        # 下跌趋势：价格下穿下轨
        self.trend_down = bt.indicators.CrossDown(source, self.l.lower_band)

        # -------- 步骤5：初始化成交量累积变量 --------
        self.up_vol_acc = 0.0  # 上涨趋势成交量累积器
        self.down_vol_acc = 0.0  # 下跌趋势成交量累积器
        self.current_trend = None  # 当前趋势状态（up/down/none）

        # ===================== 2. 可视化配置（SuperPlot） =====================
        self.linesplot = {
            # --- 全局设置 ---
            'plotname': '成交量动态VIDYA',
            'subplot': False,
            'background': '#131722',  # 副图背景色
            
            # --- 核心VIDYA均线（主图，实线） ---
            'vidya_line': {
                'type': 'line',
                'color': '#888888',
                'width': 1,
                'style': 'solid',
                'opacity': 0.7,
                'subplot': False,  # 覆盖到主图K线
            },
            
            # --- ATR上轨（主图，虚线） ---
            'upper_band': {
                'type': 'line',
                'color': '#ff9800',
                'width': 1,
                'style': 'dashed',
                'opacity': 0.6,
                'subplot': False,
            },
            
            # --- ATR下轨（主图，虚线） ---
            'lower_band': {
                'type': 'line',
                'color': '#4caf50',
                'width': 1,
                'style': 'dashed',
                'opacity': 0.6,
                'subplot': False,
            },
            
            # --- 趋势核心价（主图，粗线，动态颜色） ---
            'smoothed_value': {
                'type': 'line',
                'color': self.p.up_color,  # 初始色，next中动态更新
                'width': 2,
                'style': 'solid',
                'opacity': 0.9,
                'subplot': False,
            },
            
            # --- 买入信号（主图，上箭头） ---
            'buy_signal': {
                'type': 'marker',
                'marker': 'arrowUp',
                'marker_position': 'belowBar',
                'marker_text': 'Buy',
                'marker_size': 2,
                'color': self.p.up_color,
                'subplot': False,
            },
            
            # --- 卖出信号（主图，下箭头） ---
            'sell_signal': {
                'type': 'marker',
                'marker': 'arrowDown',
                'marker_position': 'aboveBar',
                'marker_text': 'Sell',
                'marker_size': 2,
                'color': self.p.down_color,
                'subplot': False,
            },
            
            # --- 隐藏计算线条 ---
            'volume_delta_pct': {'plotskip': True},
            'up_trend_volume': {'plotskip': True},
            'down_trend_volume': {'plotskip': True},
            'invisible_cmo': {'plotskip': True},
        }

        # 动态颜色存储（趋势核心价的颜色）
        self.smoothed_colors = []
        self.linesplot['smoothed_value']['color'] = self.smoothed_colors

    def prenext(self):
        # 填充 minperiod 之前的颜色，确保列表长度对齐
        self.smoothed_colors.append('#888888')
        # 确保相关线条值为 NaN
        self.l.smoothed_value[0] = float('nan')
        self.l.buy_signal[0] = float('nan')
        self.l.sell_signal[0] = float('nan')
        self.l.volume_delta_pct[0] = float('nan')
        self.l.up_trend_volume[0] = float('nan')
        self.l.down_trend_volume[0] = float('nan')

    def next(self):
        # ===================== 逐K线计算逻辑 =====================
        # 0. 基础数据
        source = self.data.close[0]
        prev_source = self.data.close[-1]
        upper_band = self.l.upper_band[0]
        lower_band = self.l.lower_band[0]
        prev_upper = self.l.upper_band[-1]
        prev_lower = self.l.lower_band[-1]
        volume = self.data.volume[0]
        is_bull_bar = self.data.close[0] > self.data.open[0]  # 阳线
        is_bear_bar = self.data.close[0] < self.data.open[0]  # 阴线

        # 1. 更新趋势状态
        if self.trend_up[0]:
            # 上穿上轨 → 切换为上涨趋势
            self.current_trend = 'up'
            self.l.buy_signal[0] = self.data.low[0]  # 信号位置（K线低点）
            self.l.sell_signal[0] = float('nan')
            # 重置成交量累积
            self.up_vol_acc = 0.0
        elif self.trend_down[0]:
            # 下穿下轨 → 切换为下跌趋势
            self.current_trend = 'down'
            self.l.sell_signal[0] = self.data.high[0]  # 信号位置（K线高点）
            self.l.buy_signal[0] = float('nan')
            # 重置成交量累积
            self.down_vol_acc = 0.0
        else:
            # 无趋势切换 → 清空信号
            self.l.buy_signal[0] = float('nan')
            self.l.sell_signal[0] = float('nan')

        # 2. 计算趋势核心价（smoothed_value）
        if self.current_trend == 'up':
            self.l.smoothed_value[0] = lower_band
            self.smoothed_colors.append(self.p.up_color)
        elif self.current_trend == 'down':
            self.l.smoothed_value[0] = upper_band
            self.smoothed_colors.append(self.p.down_color)
        else:
            self.l.smoothed_value[0] = float('nan')
            self.smoothed_colors.append('#888888')  # 无趋势时灰色

        # 3. 成交量累积与量化
        if self.current_trend == 'up':
            self.up_vol_acc += volume if is_bull_bar else 0.0
            self.l.up_trend_volume[0] = self.up_vol_acc
            self.l.down_trend_volume[0] = self.down_vol_acc
        elif self.current_trend == 'down':
            self.down_vol_acc += volume if is_bear_bar else 0.0
            self.l.down_trend_volume[0] = self.down_vol_acc
            self.l.up_trend_volume[0] = self.up_vol_acc
        else:
            self.l.up_trend_volume[0] = 0.0
            self.l.down_trend_volume[0] = 0.0

        # 计算成交量差值百分比（避免除零）
        avg_vol = (self.l.up_trend_volume[0] + self.l.down_trend_volume[0]) / 2 if (self.l.up_trend_volume[0] + self.l.down_trend_volume[0]) != 0 else 1e-8
        delta_pct = (self.l.up_trend_volume[0] - self.l.down_trend_volume[0]) / avg_vol * 100
        self.l.volume_delta_pct[0] = delta_pct if not math.isnan(delta_pct) else 0.0

        # 4. 趋势切换时重置平滑值
        if self.trend_up[0] or self.trend_down[0]:
            self.l.smoothed_value[0] = float('nan')


