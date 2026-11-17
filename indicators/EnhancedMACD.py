import backtrader as bt
import numpy as np

class EnhancedMACD(bt.Indicator):
    """
    增强型MACD指标（Backtrader版本）
    输出指标线：dif, dea, hist, signal
    color_mark 作为普通变量存储，不使用 lines
    其中 color_mark 使用数字编码；signal 在第三根的下一根触发。
    特殊柱体（5/7）与其右侧颜色（6/8）通过回溯赋值产生，避免未来数据。
    """
    lines = ('dif', 'dea', 'hist', 'signal')
    params = (
        ('fast_period', 12),
        ('slow_period', 26),
        ('signal_period', 9),
        ('render_lines', ['dif', 'dea', 'hist', 'signal'])
    )

    # 颜色标记数字编码定义
    COLOR_GRAY = 0          # HIST==0 默认
    COLOR_RED = 1           # 上方柱体：较前一柱增大
    COLOR_PINK = 2          # 上方柱体：较前一柱减小
    COLOR_GREEN = 3         # 下方柱体：较前一柱减小
    COLOR_LIGHTBLUE = 4     # 下方柱体：较前一柱增大
    COLOR_DARK_RED = 5      # 特殊柱体：两下方柱间全为上方柱时，中段最大值柱
    COLOR_BRIGHT_PINK = 6   # 5 的右侧一柱为粉色时，提升为亮粉
    COLOR_DARK_GREEN = 7    # 特殊柱体：两上方柱间全为下方柱时，中段最小值柱
    COLOR_BRIGHT_LIGHT_BLUE = 8  # 7 的右侧一柱为浅蓝时，提升为亮浅蓝

    # 颜色编码到Hex的映射
    COLOR_HEX_MAP = {
        COLOR_GRAY: '#cccccc',
        COLOR_RED: '#ef5350',          # Red
        COLOR_PINK: '#f48fb1',         # Pink
        COLOR_GREEN: '#66bb6a',        # Green
        COLOR_LIGHTBLUE: '#4fc3f7',    # Light Blue
        COLOR_DARK_RED: '#b71c1c',     # Dark Red
        COLOR_BRIGHT_PINK: '#ff4081',  # Bright Pink
        COLOR_DARK_GREEN: '#1b5e20',   # Dark Green
        COLOR_BRIGHT_LIGHT_BLUE: '#00e5ff', # Cyan
    }

    def __init__(self):
        # 计算基础MACD线
        fast_ema = bt.indicators.ExponentialMovingAverage(
            self.data.close, period=self.p.fast_period, plot=False
        )
        slow_ema = bt.indicators.ExponentialMovingAverage(
            self.data.close, period=self.p.slow_period, plot=False
        )
        
        self.lines.dif = fast_ema - slow_ema
        self.lines.dea = bt.indicators.ExponentialMovingAverage(
            self.lines.dif, period=self.p.signal_period, plot=False
        )
        self.lines.hist = self.lines.dif - self.lines.dea

        # 初始化辅助变量（仅存储历史数据与位置索引）
        self.hist_values = []
        self.color_values = []
        self.hist_colors = [] # 用于SuperPlot的动态颜色列表 (Hex字符串)
        self.signal_values = []  # 存储 signal 值
        self.below_zero_indices = []
        self.above_zero_indices = []
        self.dark_red_positions = []
        self.dark_green_positions = []

# 统一配置 SuperPlot 绘图属性
        self.linesplot = {
            'plotname': 'Enhanced MACD',
            'subplot': True,
            'dif': {
                'type': 'line',
                'color': '#ffa726', # Orange
                'width': 1,
                'name': 'DIF'
            },
            'dea': {
                'type': 'line',
                'color': '#42a5f5', # Blue
                'width': 1,
                'name': 'DEA'
            },
            'hist': {
                'type': 'histogram',
                'width': 2,
                'color': self.hist_colors,  # 动态绑定列表（在next中填充）
                'name': 'HIST'
            },
            'signal': {
                'type': 'marker', 
                'marker': 'circle', 
                'color': '#FFFFFF', 
                'name': 'Signal',
                'marker_position': 'aboveBar',
                'marker_text': '@value',
                'marker_shape': 'circle',
                'subplot': False
            }
        }

    def next(self):
        # 获取当前及相关值
        current_dif = self.lines.dif[0]
        current_dea = self.lines.dea[0]
        current_hist = self.lines.hist[0]
        
        # 记录历史值以便回溯查找
        self.hist_values.append(current_hist)
        current_idx = len(self.hist_values) - 1
        
        # 1. 基础颜色判断
        color_mark = self.COLOR_GRAY
        if np.isnan(current_hist):
            color_mark = self.COLOR_GRAY
        else:
            prev_hist = self.hist_values[current_idx - 1] if current_idx > 0 else np.nan
            if current_hist >= 0:
                # 上方
                if np.isnan(prev_hist):
                    color_mark = self.COLOR_RED
                else:
                    if current_hist > prev_hist:
                        color_mark = self.COLOR_RED
                    elif current_hist < prev_hist:
                        color_mark = self.COLOR_PINK
                    else:
                        color_mark = self.COLOR_RED # 相等视为红
            else:
                # 下方
                if np.isnan(prev_hist):
                    color_mark = self.COLOR_GREEN
                else:
                    if current_hist < prev_hist:
                        color_mark = self.COLOR_GREEN
                    elif current_hist > prev_hist:
                        color_mark = self.COLOR_LIGHTBLUE
                    else:
                        color_mark = self.COLOR_GREEN # 相等视为绿

        # 2. 特殊柱体逻辑（回溯修改）
        if current_hist < 0 and not np.isnan(current_hist):
            self.below_zero_indices.append(current_idx)
            # 检查是否形成"两下方柱间全为上方柱"的结构 -> 找上方柱最大值 (Dark Red)
            if len(self.below_zero_indices) >= 2:
                prev_below_idx = self.below_zero_indices[-2]
                curr_below_idx = self.below_zero_indices[-1]
                
                middle_start = prev_below_idx + 1
                middle_end = curr_below_idx
                
                if middle_start < middle_end:
                    middle_hists = self.hist_values[middle_start:middle_end]
                    # 确保中间全是正数
                    if all(h > 0 and not np.isnan(h) for h in middle_hists):
                        # 找最大值索引
                        max_idx = int(np.argmax(middle_hists))
                        max_global_idx = middle_start + max_idx
                        
                        # 标记 Dark Red
                        if max_global_idx not in self.dark_red_positions:
                            self.dark_red_positions.append(max_global_idx)
                            # 回溯修改 color_values 和 hist_colors
                            self.color_values[max_global_idx] = self.COLOR_DARK_RED
                            self.hist_colors[max_global_idx] = self.COLOR_HEX_MAP[self.COLOR_DARK_RED]
                            
                            # 检查右侧是否为 Pink -> Bright Pink
                            if max_global_idx + 1 < len(self.color_values):
                                if self.color_values[max_global_idx + 1] == self.COLOR_PINK:
                                    self.color_values[max_global_idx + 1] = self.COLOR_BRIGHT_PINK
                                    self.hist_colors[max_global_idx + 1] = self.COLOR_HEX_MAP[self.COLOR_BRIGHT_PINK]

        elif current_hist > 0 and not np.isnan(current_hist):
            self.above_zero_indices.append(current_idx)
            # 检查是否形成"两上方柱间全为下方柱"的结构 -> 找下方柱最小值 (Dark Green)
            if len(self.above_zero_indices) >= 2:
                prev_above_idx = self.above_zero_indices[-2]
                curr_above_idx = self.above_zero_indices[-1]
                
                middle_start = prev_above_idx + 1
                middle_end = curr_above_idx
                
                if middle_start < middle_end:
                    middle_hists = self.hist_values[middle_start:middle_end]
                    # 确保中间全是负数
                    if all(h < 0 and not np.isnan(h) for h in middle_hists):
                        # 找最小值索引 (绝对值最大)
                        min_idx = int(np.argmin(middle_hists))
                        min_global_idx = middle_start + min_idx
                        
                        # 标记 Dark Green
                        if min_global_idx not in self.dark_green_positions:
                            self.dark_green_positions.append(min_global_idx)
                            # 回溯修改 color_values 和 hist_colors
                            self.color_values[min_global_idx] = self.COLOR_DARK_GREEN
                            self.hist_colors[min_global_idx] = self.COLOR_HEX_MAP[self.COLOR_DARK_GREEN]
                            
                            # 检查右侧是否为 Light Blue -> Bright Light Blue
                            if min_global_idx + 1 < len(self.color_values):
                                if self.color_values[min_global_idx + 1] == self.COLOR_LIGHTBLUE:
                                    self.color_values[min_global_idx + 1] = self.COLOR_BRIGHT_LIGHT_BLUE
                                    self.hist_colors[min_global_idx + 1] = self.COLOR_HEX_MAP[self.COLOR_BRIGHT_LIGHT_BLUE]

        
        # 3. 信号产生（在第三根的下一根触发，避免未来数据）
        signal = 0
        prev_hist = self.hist_values[current_idx-1] if current_idx > 0 else np.nan
        if current_idx > 0 and not np.isnan(prev_hist) and not np.isnan(current_hist):
            a_idx = current_idx - 1
            a_val = prev_hist
            
            # 逻辑: 寻找之前的波谷/波峰结构
            # 这里保留原有逻辑结构
            if current_hist < a_val and a_val > 0:
                b_idx = None
                i = a_idx
                while i >= 0:
                    hv = self.hist_values[i]
                    if not np.isnan(hv) and hv <= 0:
                        b_idx = i
                        break
                    i -= 1
                
                cond2 = False
                if b_idx is not None and b_idx < a_idx:
                    inc = True
                    # 检查 b_idx 到 a_idx 之间是否单调递增
                    if b_idx + 1 < len(self.hist_values):
                        pv = self.hist_values[b_idx+1]
                        for j in range(b_idx+2, a_idx+1):
                            hv = self.hist_values[j]
                            if np.isnan(pv) or np.isnan(hv) or not (hv > pv):
                                inc = False
                                break
                            pv = hv
                        cond2 = inc
                
                # 检查 Dark Red 位置
                reds = [idx for idx in self.dark_red_positions if idx < a_idx]
                cond3 = False
                if len(reds) >= 2:
                    r1, r2 = reds[-2], reds[-1]
                    h1 = self.hist_values[r1]
                    h2 = self.hist_values[r2]
                    if not (np.isnan(h1) or np.isnan(h2)) and h1 > h2 > a_val:
                        cond3 = True
                
                if cond2 and cond3:
                    signal = -1 # Sell Signal (Peak)

            if signal == 0 and current_hist > a_val and a_val < 0:
                b_idx = None
                i = a_idx
                while i >= 0:
                    hv = self.hist_values[i]
                    if not np.isnan(hv) and hv >= 0:
                        b_idx = i
                        break
                    i -= 1
                
                cond2 = False
                if b_idx is not None and b_idx < a_idx:
                    dec = True
                    # 检查 b_idx 到 a_idx 之间是否单调递减
                    if b_idx + 1 < len(self.hist_values):
                        pv = self.hist_values[b_idx+1]
                        for j in range(b_idx+2, a_idx+1):
                            hv = self.hist_values[j]
                            if np.isnan(pv) or np.isnan(hv) or not (hv < pv):
                                dec = False
                                break
                            pv = hv
                        cond2 = dec
                
                # 检查 Dark Green 位置
                greens = [idx for idx in self.dark_green_positions if idx < a_idx]
                cond3 = False
                if len(greens) >= 2:
                    g1, g2 = greens[-2], greens[-1]
                    h1 = self.hist_values[g1]
                    h2 = self.hist_values[g2]
                    if not (np.isnan(h1) or np.isnan(h2)) and h1 < h2 < a_val:
                        cond3 = True
                
                if cond2 and cond3:
                    signal = 1 # Buy Signal (Valley)
            
            # 补充信号逻辑 (Original Code had logic for signal=2)
            # Original:
            # if signal == 0 and current_hist >= 0 and a_val < 0:
            #     greens = ...
            #     if len(greens) >= 3: ... signal = 2
            
            if signal == 0 and current_hist >= 0 and a_val < 0:
                greens = [idx for idx in self.dark_green_positions if idx < a_idx]
                if len(greens) >= 3:
                    g1, g2, g3 = greens[-3], greens[-2], greens[-1]
                    h1 = self.hist_values[g1]
                    h2 = self.hist_values[g2]
                    h3 = self.hist_values[g3]
                    if not (np.isnan(h1) or np.isnan(h2) or np.isnan(h3)) and h1 < h2 < h3 < a_val:
                        signal = 2 # Strong Buy

            if signal == 0 and current_hist <= 0 and a_val > 0:
                reds = [idx for idx in self.dark_red_positions if idx < a_idx]
                if len(reds) >= 3:
                    r1, r2, r3 = reds[-3], reds[-2], reds[-1]
                    h1 = self.hist_values[r1]
                    h2 = self.hist_values[r2]
                    h3 = self.hist_values[r3]
                    if not (np.isnan(h1) or np.isnan(h2) or np.isnan(h3)) and h1 > h2 > h3 > a_val:
                        signal = -2 # Strong Sell


        # 赋值到列表
        # 为了逻辑一致性，signal_values 存储 0 表示无信号
        signal_to_store = 0 if np.isnan(signal) else signal
        self.signal_values.append(signal_to_store)
        
        # 为了绘图不显示0值点，lines.signal 存储 nan，非0值保持原样
        self.lines.signal[0] = float('nan') if signal == 0 else signal
        
        # 更新颜色列表
        self.color_values.append(color_mark)
        self.hist_colors.append(self.COLOR_HEX_MAP.get(color_mark, '#cccccc'))
    
    def prenext(self):
        """
        处理预热期（minperiod 之前的数据）
        在这个阶段，HIST 值可能还是 NaN，但我们仍然需要填充颜色列表以保持对齐
        """
        # 获取当前值
        current_hist = self.lines.hist[0]
        
        # 记录历史值
        self.hist_values.append(current_hist)
        
        # 预热期默认使用灰色和无信号
        color_mark = self.COLOR_GRAY
        signal = 0
        
        # 更新列表（保持与数据对齐）
        self.color_values.append(color_mark)
        self.hist_colors.append(self.COLOR_HEX_MAP.get(color_mark, '#cccccc'))
        self.signal_values.append(signal)
        # 预热期 signal 线设为 nan 避免绘图
        self.lines.signal[0] = float('nan')

    def get_hist_colors(self):
        """
        返回HIST柱状图的动态颜色列表（供SuperPlot使用）
        """
        return self.hist_colors
    
    def get_color_map(self):
        """
        返回颜色编码映射表（供SuperPlot解析使用）
        """
        return self.COLOR_HEX_MAP
    
    def get_signal_values(self):
        """
        返回信号值列表（供外部访问）
        """
        return self.signal_values
    
    def get_color_values(self):
        """
        返回颜色标记列表（供外部访问）
        """
        return self.color_values
    
    def get_current_signal(self, index=0):
        """
        获取当前位置的信号值
        
        Parameters:
        -----------
        index : int, default 0
            相对于当前位置的偏移量
            0 表示当前位置
            -1 表示前一个位置
            
        Returns:
        --------
        int : 信号值
            0: 无信号
            1: 买入信号
            2: 强买入信号
            -1: 卖出信号
            -2: 强卖出信号
        """
        if len(self.signal_values) == 0:
            return 0
        
        actual_index = len(self.signal_values) - 1 + index
        if actual_index < 0 or actual_index >= len(self.signal_values):
            return 0
        
        return self.signal_values[actual_index]
    
    def get_current_color(self, index=0):
        """
        获取当前位置的颜色标记值
        
        Parameters:
        -----------
        index : int, default 0
            相对于当前位置的偏移量
            
        Returns:
        --------
        int : 颜色标记值
        """
        if len(self.color_values) == 0:
            return self.COLOR_GRAY
        
        actual_index = len(self.color_values) - 1 + index
        if actual_index < 0 or actual_index >= len(self.color_values):
            return self.COLOR_GRAY
        
        return self.color_values[actual_index]

