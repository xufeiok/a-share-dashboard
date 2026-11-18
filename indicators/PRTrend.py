# -*- coding: utf-8 -*-
"""
周期回归趋势线指标（Backtrader 版本 · 增强版）

功能说明
--------
1) 以"最新一根K线"（即最新工作日）为基准，分别以 N1 / N2 / N3（默认 5 / 20 / 60，
   可任意调整）日为周期向过去取值。取哪两个字段由参数 field_pair 决定：
   - field_pair='HL'（默认）：上轨集合 = 过去 N 根K线的最高价 high；
                              下轨集合 = 过去 N 根K线的最低价 low
   - field_pair='OC'        ：上轨集合 = 过去 N 根K线的开盘价 open；
                              下轨集合 = 过去 N 根K线的收盘价 close
2) 对两个集合分别做一元线性回归（线下回归 / 最小二乘），得到 3 个周期 x 2 条 = 6 条
   从过去一直延伸到最新工作日的直线。
3) 绘制：同一周期的"上轨回归线"与"下轨回归线"使用同一颜色
   （上轨线实线、下轨线虚线便于区分，但颜色一致）；
   同时把同一周期这两条直线的交点标注在K线图上。

参数一览
--------
periods        : 回看周期元组，1~3 个，默认 (5, 20, 60)，数字可任意调整，
                 例如 periods=(10, 30, 90) 或 periods=(8, 21)。
field_pair     : 'HL' = 最高价/最低价组合；'OC' = 开盘价/收盘价组合。
upper_field    : 可选，直接指定上轨字段（'high'/'open'/'close'/'low'），
                 指定后覆盖 field_pair。
lower_field    : 可选，直接指定下轨字段，同上。
extend_forward : 允许交点出现在最新工作日右侧多少个 bar（默认 0）。
show_cross     : 是否标注交点（默认 True）。

实现要点
--------
- Backtrader 逐根推送数据（不能引用未来数据），因此沿用参考文件"回溯赋值"的思路：
  在最新一根K线上完成回归后，把拟合直线每个历史位置的取值反向写回 lines 中
  已经走过的若干根K线位置，于是图上呈现"从过去 N 天连到最新工作日"的直线段。
- 交点由两条直线解析求根：
      intercept_u + slope_u * x = intercept_l + slope_l * x
      x = (intercept_l - intercept_u) / (slope_u - slope_l)
  x 以"最新工作日"为 0，向过去为负。仅当 x 落在 [-N+1, extend_forward] 区间内
  （确实落在画出的线段范围内）才标注交点。
- 最多 3 个周期槽位（a / b / c），与 periods 按顺序一一对应。
- 注意：回溯赋值只在「当前窗口」内生效，每根新 K 线都会覆盖同一批位置，
  因此 lines 最终只保留最后一根 K 线对应的回归线段与交点（旧窗口的点会被清空），
  正好对应网页端"从过去 N 根连到最新一根"的直线段画法。
- 绘图元信息放在 self.plot_meta（不使用 Backtrader 的绘图接口），供 bt_indicator_bridge
  按 slot 取周期与线色；self.lines 的 9 条 line 即导出序列。

输出 lines
----------
hi_a / lo_a  : 第 1 周期上轨回归线 / 下轨回归线（字段随 field_pair 变化）
hi_b / lo_b  : 第 2 周期
hi_c / lo_c  : 第 3 周期
cx_a / cx_b / cx_c : 对应周期两条直线的交点（单点 marker 形式显示）
"""

import backtrader as bt
import numpy as np

# 允许直接指定的字段名（含派生价：hl2/hlc3/ohlc4）
VALID_FIELDS = ('high', 'low', 'open', 'close', 'hl2', 'hlc3', 'ohlc4')

# 字段组合预设：'HL' = 最高价/最低价，'OC' = 开盘价/收盘价
FIELD_PRESETS = {
    'HL': ('high', 'low'),
    'HL_INVERT': ('low', 'high'),   # 如需上下对调也可显式指定
    'OC': ('open', 'close'),
    'OC_INVERT': ('close', 'open'),
    'HO': ('high', 'open'),         # 扩展组合：最高价 / 开盘价
    'HC': ('high', 'close'),        # 扩展组合：最高价 / 收盘价
    'LO': ('low', 'open'),          # 扩展组合：最低价 / 开盘价
    'LC': ('low', 'close'),         # 扩展组合：最低价 / 收盘价
}


class PeriodRegressionTrend(bt.Indicator):
    """以最新工作日为基准的多周期双字段线性回归趋势线 + 交点标注（增强版）"""

    # 3 个槽位（a/b/c），与 params.periods 按顺序一一对应
    lines = ('hi_a', 'lo_a', 'cx_a', 'hi_b', 'lo_b', 'cx_b', 'hi_c', 'lo_c', 'cx_c')

    params = (
        ('periods', (5, 20, 60)),   # 回看周期，数字可任意调整，最多 3 个
        ('field_pair', 'HL'),       # 'HL' = 最高价/最低价；'OC' = 开盘价/收盘价
        ('upper_field', None),      # 直接指定上轨字段（覆盖 field_pair）
        ('lower_field', None),      # 直接指定下轨字段（覆盖 field_pair）
        ('extend_forward', 0),      # 允许交点出现在最新工作日右侧多少个 bar
        ('show_cross', True),       # 是否标注交点
    )

    SLOTS = ('a', 'b', 'c')

    # 同一周期两条线共用一种颜色（上轨线实线 / 下轨线虚线）
    PERIOD_COLORS = {
        'a': '#ff9800',   # 第 1 周期：橙色
        'b': '#42a5f5',   # 第 2 周期：蓝色
        'c': '#ab47bc',   # 第 3 周期：紫色
    }

    FIELD_LABEL = {
        'high': 'High', 'low': 'Low', 'open': 'Open', 'close': 'Close',
        'hl2': 'HL2', 'hlc3': 'HLC3', 'ohlc4': 'OHLC4',
    }

    def __init__(self):
        # -------- 解析周期 -------- #
        ps = tuple(self.p.periods)
        if len(ps) == 0 or len(ps) > len(self.SLOTS):
            raise ValueError('periods 需要 1~3 个周期参数')
        self.period_slot = {}
        self.slot_period = {}
        for p, s in zip(ps, self.SLOTS):
            if int(p) < 2:
                raise ValueError('周期至少为 2 才能做线性回归')
            if int(p) in self.period_slot:
                raise ValueError('periods 中不能有重复周期: %d' % int(p))
            self.period_slot[int(p)] = s
            self.slot_period[s] = int(p)

        # -------- 解析字段组合 -------- #
        up, lo = self._resolve_fields()
        self.upper_field = up
        self.lower_field = lo
        # 字段取值统一走 _field_series（支持 high/low/open/close 与 hl2/hlc3/ohlc4 派生价）
        self._upper_field, self._lower_field = up, lo

        self.fit_info = {}                       # slot -> 最新回归结果
        self.cross_info = {}                     # slot -> 最近一次交点
        self.cross_records = []                  # 全部交点历史
        self.fit_records = {s: [] for s in self.period_slot.values()}

        # -------- 绘图元信息（非 Backtrader 绘图接口，供 bt_indicator_bridge 消费） -------- #
        ul = self.FIELD_LABEL[up]
        ll = self.FIELD_LABEL[lo]
        self.plot_meta = {
            'name': 'PeriodRegressionTrend',
            'plotname': 'Period Regression Trend (%s/%s)' % (ul, ll),
            'upper_field': up,
            'lower_field': lo,
            'slots': [
                {'slot': s, 'period': self.slot_period[s],
                 'color': self.PERIOD_COLORS[s],
                 'lines': {'hi': 'hi_' + s, 'lo': 'lo_' + s, 'cx': 'cx_' + s}}
                for s in self.SLOTS if s in self.slot_period
            ],
        }

    def _resolve_fields(self):
        """确定上/下轨字段：upper_field / lower_field 优先，否则取 field_pair 预设"""
        up = self.p.upper_field
        lo = self.p.lower_field
        if up is None or lo is None:
            key = str(self.p.field_pair).upper()
            if key not in FIELD_PRESETS:
                raise ValueError(
                    'field_pair 仅支持 %s，或用 upper_field/lower_field 直接指定字段'
                    % sorted(FIELD_PRESETS.keys()))
            preset_up, preset_lo = FIELD_PRESETS[key]
            if up is None:
                up = preset_up
            if lo is None:
                lo = preset_lo
        up, lo = up.lower(), lo.lower()
        if up not in VALID_FIELDS or lo not in VALID_FIELDS:
            raise ValueError('字段仅支持 %s' % (VALID_FIELDS,))
        # 允许上下轨选择同一价格类型（两条回归线重合），不做拦截
        return up, lo

    # ------------------------------------------------------------------ #
    # 核心计算
    # ------------------------------------------------------------------ #
    def next(self):
        for period, slot in self.period_slot.items():
            self._fit_one(period, slot)

    @staticmethod
    def linreg(x, y):
        """一元线性回归，返回 (slope, intercept)；y 为 None/含 NaN 时返回 (nan, nan)"""
        if y is None:
            return float('nan'), float('nan')
        y = np.asarray(y, dtype=float)
        if y.size < 2 or np.isnan(y).any():
            return float('nan'), float('nan')
        return np.polyfit(np.asarray(x, dtype=float), y, 1)

    def _series(self, getter, size):
        """安全取过去 size 根某字段数据，长度不足或含 NaN 返回 None"""
        if len(self) < size:
            return None
        arr = np.asarray(getter.get(ago=0, size=size), dtype=float)
        if np.isnan(arr).any():
            return None
        return arr

    def _field_series(self, name, size):
        """按字段名取最近 size 根价格序列；派生价用 O/H/L/C 现场合成"""
        if len(self) < size:
            return None
        d = self.data
        def base(field):
            return np.asarray(getattr(d, field).get(ago=0, size=size), dtype=float)
        if name in ('high', 'low', 'open', 'close'):
            arr = base(name)
        else:
            h, l = base('high'), base('low')
            if name == 'hl2':
                arr = (h + l) / 2.0
            elif name == 'hlc3':
                arr = (h + l + base('close')) / 3.0
            elif name == 'ohlc4':
                arr = (base('open') + h + l + base('close')) / 4.0
            else:
                return None
        if np.isnan(arr).any():
            return None
        return arr

    def _fit_one(self, period, slot):
        """对 period 周期的上/下轨字段集合做线性回归，回溯写入线段与交点"""
        hi_line = getattr(self.lines, 'hi_' + slot)
        lo_line = getattr(self.lines, 'lo_' + slot)
        cx_line = getattr(self.lines, 'cx_' + slot)

        upper = self._field_series(self.upper_field, period)
        lower = self._field_series(self.lower_field, period)
        if upper is None or lower is None:
            self.fit_info[slot] = {'period': period, 'ok': False}
            return

        # x 轴以最新工作日为 0，向过去为负
        x = np.arange(-(period - 1), 1, dtype=float)

        # 一元线性回归： y = slope * x + intercept
        slope_u, intercept_u = self.linreg(x, upper)
        slope_l, intercept_l = self.linreg(x, lower)
        if not (np.isfinite(slope_u) and np.isfinite(slope_l)):
            self.fit_info[slot] = {'period': period, 'ok': False}
            return

        first_x = -(period - 1)
        # 上一根 bar 写入的窗口起点已不在当前窗口内，先清空，否则图上会多出一段错位线头
        if len(self) > period:
            hi_line[first_x - 1] = float('nan')
            lo_line[first_x - 1] = float('nan')
            cx_line[first_x - 1] = float('nan')

        # 回溯赋值：把拟合直线在当前窗口内每一根 K 线上的取值写回。
        # Backtrader 的 line 用 ago 下标：0 = 当前 bar，-1 = 前一根，-k = 前 k 根。
        for i in range(period):
            xv = first_x + i
            hi_line[xv] = slope_u * xv + intercept_u
            lo_line[xv] = slope_l * xv + intercept_l
            cx_line[xv] = float('nan')      # 交点只保留当前窗口这一次

        self.fit_info[slot] = {
            'period': period, 'ok': True,
            'upper_field': self.upper_field, 'lower_field': self.lower_field,
            'slope_upper': float(slope_u), 'intercept_upper': float(intercept_u),
            'slope_lower': float(slope_l), 'intercept_lower': float(intercept_l),
        }
        self.fit_records[slot].append(
            (len(self) - 1, period, float(slope_u), float(intercept_u),
             float(slope_l), float(intercept_l))
        )

        if not self.p.show_cross:
            return

        # ---------------- 交点：同周期上/下两条回归线的相交点 ----------------
        if abs(slope_u - slope_l) < 1e-12:
            return  # 两线平行，无交点
        x_cross = (intercept_l - intercept_u) / (slope_u - slope_l)
        y_cross = slope_u * x_cross + intercept_u

        lower_x = first_x
        upper_x = float(self.p.extend_forward)
        if x_cross < lower_x or x_cross > upper_x or not np.isfinite(y_cross):
            return  # 交点不落在画出的线段范围内，不标注

        idx = int(round(x_cross))
        if idx > 0 or idx < lower_x:
            return
        cx_line[idx] = float(y_cross)

        abs_idx = (len(self) - 1) + idx
        info = {
            'period': period,
            'x_rel': float(x_cross),
            'x_idx': idx,
            'y': float(y_cross),
            'bar_idx_abs': abs_idx,
            'slope_gap': float(slope_u - slope_l),
        }
        self.cross_info[slot] = info
        self.cross_records.append(info)

    def prenext(self):
        """预热期：保证 lines 与数据长度对齐，全部为 NaN（不绘制）"""
        pass

    # ------------------------------------------------------------------ #
    # 外部访问接口
    # ------------------------------------------------------------------ #
    def get_latest_fit(self, period=None):
        slot = self._slot_of(period)
        return self.fit_info.get(slot, {'ok': False})

    def get_latest_cross(self, period=None):
        slot = self._slot_of(period)
        return self.cross_info.get(slot, None)

    def get_cross_records(self):
        return list(self.cross_records)

    def get_trend_direction(self, period=None):
        """用两线斜率差描述态势：1 上轨线斜率更大 / -1 更小 / 0 数据不足"""
        info = self.get_latest_fit(period)
        if not info.get('ok'):
            return 0
        gap = info['slope_upper'] - info['slope_lower']
        return 1 if gap > 0 else (-1 if gap < 0 else 0)

    def get_field_pair(self):
        """返回当前使用的 (上轨字段, 下轨字段)"""
        return (self.upper_field, self.lower_field)

    def _slot_of(self, period):
        if period is None:
            return self.SLOTS[0]
        return self.period_slot[int(period)]


# ---------------------------------------------------------------------- #
# 使用示例
# ---------------------------------------------------------------------- #
if __name__ == '__main__':
    import sys

    cerebro = bt.Cerebro()
    data = bt.feeds.GenericCSVData(
        dataname=sys.argv[1] if len(sys.argv) > 1 else 'data.csv',
        dtformat='%Y-%m-%d',
        openinterest=-1,
    )
    cerebro.adddata(data)

    # 示例 1：默认 5/20/60 日，最高价/最低价组合
    ind_hl = cerebro.addindicator(
        PeriodRegressionTrend,
        name='RegHL',
        periods=(5, 20, 60),
        field_pair='HL',
        extend_forward=0,
    )
    cerebro.run()

    latest_bar = len(cerebro.datas[0]) - 1
    up, lo = ind_hl.get_field_pair()
    print('字段组合: 上轨=%s  下轨=%s' % (up, lo))
    print('最新工作日 bar 序号:', latest_bar)
    for p in (5, 20, 60):
        f = ind_hl.get_latest_fit(p)
        if f.get('ok'):
            print('周期 %2d 日  上轨线: y = %.6f * x + %.4f   下轨线: y = %.6f * x + %.4f'
                  % (p, f['slope_upper'], f['intercept_upper'],
                     f['slope_lower'], f['intercept_lower']))
        c = ind_hl.get_latest_cross(p)
        if c:
            print('           交点位于最新工作日前 %.2f 个交易日，价格 %.4f'
                  % (abs(c['x_rel']), c['y']))
        else:
            print('           该周期两条回归线在线段范围内无交点')

    cerebro.plot(style='candlestick')

    # 示例 2：改用开盘价/收盘价组合、周期自定义为 (10, 30, 90)
    # cerebro2 = bt.Cerebro()
    # cerebro2.adddata(data)
    # cerebro2.addindicator(
    #     PeriodRegressionTrend,
    #     periods=(10, 30, 90),
    #     field_pair='OC',
    # )
    # cerebro2.run()
    # cerebro2.plot(style='candlestick')

    # 示例 3：自定义任意字段组合（如 上轨=high、下轨=close）
    # cerebro3.addindicator(
    #     PeriodRegressionTrend,
    #     periods=(8, 21),
    #     upper_field='high',
    #     lower_field='close',
    # )
