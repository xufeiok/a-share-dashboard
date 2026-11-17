# -*- coding: utf-8 -*-
"""回放撮合引擎（纯逻辑，不依赖 Flask）。

撮合粒度：
- 有分钟数据（最近 5 个交易日）→ 逐分钟判定；
- 更早的历史没有分钟源 → 退回日线 O/H/L/C 判定（这一点在接口返回里显式标注 granularity）。

规则：
- T+1：当日买入 avail_qty=0，次日解冻
- 涨跌停：主板 ±10%、创业板/科创板 ±20%、ST ±5%、北交所 ±30%，越界按废单拒绝
- 买入 100 股整数倍；卖出允许一次性零股
- 佣金万 2.5（最低 5 元）、卖出印花税 0.05%、沪市过户费 0.001%
- 可选「成交量参与率」上限：单笔成交量不超过该周期成交量的 rate 倍，默认 0（关闭）
"""

import math

import sim_store as store

DEFAULT_SLIP_BPS = 2.0          # 市价单滑点（基点）
COMM_RATE = 0.00025
COMM_MIN = 5.0
STAMP_RATE = 0.0005
TRANSFER_RATE = 0.00001


def board_of(code):
    """板块：沪 / 深 / 创 / 科 / 北"""
    num = (code or '').split('.')[0]
    if num.startswith(('688', '689')):
        return '科'
    if num.startswith(('300', '301')):
        return '创'
    if num.startswith(('8', '4')) or num.startswith('920'):
        return '北'
    if code.endswith('.SH'):
        return '沪'
    return '深'


def limit_pct(code, name=''):
    """涨跌停幅度"""
    if name and 'ST' in name.upper():
        return 0.05
    b = board_of(code)
    if b == '北':
        return 0.30
    if b in ('创', '科'):
        return 0.20
    return 0.10


def calc_fee(price, qty, side, code):
    amt = price * qty
    comm = max(COMM_MIN, amt * COMM_RATE)
    stamp = amt * STAMP_RATE if side == 'sell' else 0.0
    transfer = amt * TRANSFER_RATE if (code or '').endswith('.SH') else 0.0
    return round(comm + stamp + transfer, 2)


def limit_prices(pre_close, code, name=''):
    pct = limit_pct(code, name)
    if not pre_close or pre_close <= 0:
        return None, None
    return round(pre_close * (1 + pct), 2), round(pre_close * (1 - pct), 2)


def _slip(price, side, bps):
    adj = price * bps / 10000.0
    return round(price + adj if side == 'buy' else price - adj, 2)


def match_one(order, bar, participation_rate=0.0, slippage_bps=DEFAULT_SLIP_BPS):
    """对单根 bar 撮合一张委托，返回 (成交价, 成交股数) 或 None。

    bar: {'open','high','low','close','vol'}，vol 单位为手。
    """
    if not bar:
        return None
    o, h, l, c = float(bar['open']), float(bar['high']), float(bar['low']), float(bar['close'])
    if min(o, h, l, c) <= 0:
        return None
    side = order['side']
    remain = int(order['qty']) - int(order.get('filled') or 0)
    if remain <= 0:
        return None

    if order['otype'] == 'market':
        px = _slip(o, side, slippage_bps)
    else:
        limit = float(order['price'])
        if side == 'buy':
            if l > limit:            # 全天最低都没到限价，买不进
                return None
            px = min(limit, o)       # 开盘就低于限价时按更优的开盘价成交
        else:
            if h < limit:            # 全天最高都没到限价，卖不出
                return None
            px = max(limit, o)

    qty = remain
    if participation_rate and participation_rate > 0:
        cap = int(float(bar.get('vol') or 0) * 100 * participation_rate)  # 手→股
        if cap <= 0:
            return None
        qty = min(qty, cap)
    # 参与率模型下不足 1 手不成交
    if qty <= 0:
        return None
    if participation_rate and participation_rate > 0 and qty < 100 and remain >= 100:
        return None
    return round(px, 2), qty


def submit_order(code, name, side, otype, price, qty, asof,
                 prev_close=None, bar=None, participation_rate=0.0, slippage_bps=DEFAULT_SLIP_BPS):
    """下单 + 立即按当日 bar 撮合一次。返回 dict 供接口直接 jsonify。"""
    code = (code or '').strip().upper()
    side = 'buy' if side == 'buy' else 'sell'
    otype = 'market' if otype == 'market' else 'limit'
    qty = int(qty or 0)
    price = float(price or 0)
    name = name or ''

    if qty <= 0:
        return _reject(asof, code, name, side, otype, price, qty, '委托数量必须大于 0')
    if side == 'buy' and qty % 100 != 0:
        return _reject(asof, code, name, side, otype, price, qty, '买入数量必须是 100 股整数倍')
    if otype == 'limit' and price <= 0:
        return _reject(asof, code, name, side, otype, price, qty, '限价委托必须填写价格')

    up, down = limit_prices(prev_close, code, name)
    if otype == 'limit' and up and (price > up or price < down):
        return _reject(asof, code, name, side, otype, price, qty,
                       '超出涨跌停限制（%.2f ~ %.2f）' % (down, up))

    # 停牌：当日无有效 bar 或成交量为 0
    if (not bar) or float(bar.get('vol') or 0) <= 0:
        return _reject(asof, code, name, side, otype, price, qty, '该股当日停牌或无行情，无法成交')

    store.release_t1(asof)
    acc = store.get_account()
    pos = store.get_position(code)

    # 预估成交价（用于冻结额度）
    est = price if otype == 'limit' else float(bar['open'])
    est_fee = calc_fee(est, qty, side, code)

    if side == 'buy':
        need = round(est * qty + est_fee, 2)
        if need > acc.get('cash', 0) + 1e-6:
            return _reject(asof, code, name, side, otype, price, qty,
                           '可用资金不足（需要 %.2f，可用 %.2f）' % (need, acc.get('cash', 0)))
    else:
        avail = (pos or {}).get('avail_qty', 0)
        if qty > avail:
            return _reject(asof, code, name, side, otype, price, qty,
                           '可卖数量不足（可卖 %d 股，T+1 当日买入不可卖）' % avail)

    # 冻结
    if side == 'buy':
        store.freeze_cash(est * qty + est_fee)
        frozen = round(est * qty + est_fee, 2)
    else:
        frozen = 0.0

    oid = store.insert_order(asof, code, name, side, otype, price, qty, frozen)
    order = {'id': oid, 'qty': qty, 'filled': 0, 'side': side, 'otype': otype,
             'price': price, 'frozen': frozen}

    fills = _fill_order(order, code, name, bar, asof, participation_rate, slippage_bps)
    return {
        'ok': True,
        'order_id': oid,
        'status': 'filled' if fills and sum(f[1] for f in fills) >= qty else ('partial' if fills else 'pending'),
        'fills': [{'price': f[0], 'qty': f[1]} for f in fills],
        'granularity': 'daily',
    }


def _fill_order(order, code, name, bar, asof, participation_rate, slippage_bps):
    """执行撮合并写账；支持一次撮出多个分批（当前日线粒度只有一批）。"""
    r = match_one(order, bar, participation_rate, slippage_bps)
    if not r:
        return []
    px, qty = r
    fee = calc_fee(px, qty, order['side'], code)
    store.apply_fill(code, name, order['side'], px, qty, fee, asof, order['id'])
    # 买方多冻的部分退回（成交价可能低于限价）
    if order['side'] == 'buy':
        frozen = float(order.get('frozen') or 0)
        used = px * qty + fee
        if frozen - used > 0:
            store.unfreeze_cash(frozen - used)
            store.update_order(order['id'], frozen=round(used, 2))
    return [(px, qty)]


def retry_pending(asof, bars_by_code, participation_rate=0.0, slippage_bps=DEFAULT_SLIP_BPS):
    """推进到新交易日时，把未成交委托按当日 bar 重新撮合。"""
    out = []
    store.release_t1(asof)
    for o in store.pending_orders():
        bar = bars_by_code.get(o['code'])
        if not bar:
            continue
        before = o.get('filled') or 0
        fills = _fill_order(o, o['code'], o.get('name') or '', bar, asof,
                            participation_rate, slippage_bps)
        if fills:
            out.append({'order_id': o['id'], 'code': o['code'],
                        'fills': [{'price': f[0], 'qty': f[1]} for f in fills],
                        'filled_total': before + sum(f[1] for f in fills)})
    return out


def cancel(order_id, asof):
    o = [x for x in store.list_orders(limit=300) if x['id'] == int(order_id)]
    if not o:
        return {'ok': False, 'reason': '委托不存在'}
    o = o[0]
    if o['status'] not in ('pending', 'partial'):
        return {'ok': False, 'reason': '当前状态不可撤单：%s' % o['status']}
    remain = int(o['qty']) - int(o['filled'] or 0)
    if o['side'] == 'buy' and remain > 0:
        est = float(o['price']) if o['otype'] == 'limit' else float(o['price'])
        back = est * remain + calc_fee(est, remain, 'buy', o['code'])
        store.unfreeze_cash(min(back, o.get('frozen') or back))
    store.update_order(o['id'], status='cancelled', reason='用户撤单')
    return {'ok': True, 'order_id': o['id'], 'status': 'cancelled'}


def _reject(asof, code, name, side, otype, price, qty, reason):
    oid = store.reject_order(asof, code, name, side, otype, price, qty, reason)
    return {'ok': False, 'order_id': oid, 'status': 'rejected', 'reason': reason}


COND_PRIORITY = {'sl': 0, 'trail': 1, 'tp': 2}


def calc_trigger(kind, value, cost, peak):
    """条件价：止盈/止损基于持仓成本，回落止盈基于持仓期最高价。"""
    if kind == 'tp':
        return round(cost * (1 + value), 2)
    if kind == 'sl':
        return round(cost * (1 - value), 2)
    return round(peak * (1 - value), 2)


def _cond_sell_qty(c, pos):
    """按条件单的 qty_mode 计算本次卖出股数，不超过可用。"""
    avail = int(pos.get('avail_qty') or 0)
    mode = (c.get('qty_mode') or 'all').strip().lower()
    if mode == 'pct':
        pct = float(c.get('qty_value') or 0)
        if pct > 1:
            pct = pct / 100.0
        pct = max(0.0, min(pct, 1.0))
        q = int((avail * pct) // 100 * 100)
        if q <= 0 and pct > 0:
            q = avail if avail < 100 else 100
    elif mode == 'shares':
        q = int(c.get('qty_value') or 0)
    else:
        q = avail
    return max(0, min(q, avail))


def _activate_next(code, group_id, fired_id):
    """同组多段卖出：触发一条后激活下一条排队条件。"""
    if not group_id:
        return
    queued = [x for x in store.list_cond(code=code, status='queued')
              if (x.get('group_id') or '') == group_id and x['id'] != fired_id]
    if not queued:
        return
    queued.sort(key=lambda x: (int(x.get('seq') or 0), x['id']))
    store.update_cond(queued[0]['id'], status='active')


def run_conditions(code, asof, bar):
    """按当日 bar 判定条件单，命中即卖出。

    排序：先按 seq（计划里的先后），无 seq 的老数据按 止损→回落→止盈。
    timed 条件按 due_date 顺延到到期后的第一个交易日。
    同组（group_id）多条按 seq 排队，触发一条后激活下一条。
    """
    pos = store.get_position(code)
    if not pos or int(pos.get('qty') or 0) <= 0:
        return []
    o, h, l = float(bar.get('open') or 0), float(bar.get('high') or 0), float(bar.get('low') or 0)
    if min(o, h, l) <= 0:
        return []
    # 峰值只用"昨日及之前"的记录，避免用当日 high 反推当日 low 的穿越式误触发
    peak_prev = float(pos.get('peak_price') or 0)
    active = store.list_cond(code=code, status='active')
    if not active:
        store.update_peak(code, h)
        return []

    def _order(c):
        seq = int(c.get('seq') or 0)
        if seq > 0:
            return (0, seq, c['id'])
        return (1, COND_PRIORITY.get(c.get('kind'), 9), c['id'])

    active.sort(key=_order)
    hits = []
    for c in active:
        pos = store.get_position(code)
        if not pos or int(pos.get('qty') or 0) <= 0:
            break
        if int(pos.get('avail_qty') or 0) <= 0:
            break  # 当日买入未解冻，等下一交易日
        cost = float(pos.get('cost') or 0)
        kind = c.get('kind')
        trig = 0.0
        px = 0.0
        hit = False

        if kind == 'tp':
            trig = calc_trigger('tp', float(c['value']), cost, 0)
            hit = h >= trig
            px = max(trig, o)          # 跳空高开按开盘价成交
        elif kind == 'sl':
            trig = calc_trigger('sl', float(c['value']), cost, 0)
            hit = l <= trig
            px = min(trig, o)          # 跳空低开按开盘价成交
        elif kind == 'fixed_price':
            trig = float(c['value'])
            hit = h >= trig
            px = max(trig, o)
        elif kind == 'timed':
            due = str(c.get('due_date') or '')
            hit = bool(due) and str(asof) >= due
            trig = round(o, 2)
            px = o
        else:
            # 回落止盈：arm>0 时先要涨到 成本×(1+arm) 才激活
            arm = float(c.get('arm_value') or 0)
            armed = int(c.get('armed') or 0)
            if arm > 0 and armed <= 0:
                arm_line = round(cost * (1 + arm), 2)
                if h >= arm_line:
                    # 激活当日只激活不判回落，避免同日穿越
                    store.update_cond(c['id'], armed=1, peak=round(h, 4),
                                      trigger_price=round(h * (1 - float(c['value'])), 2))
                continue
            peak_now = float(c.get('peak') or 0) if armed > 0 else peak_prev
            trig = round(peak_now * (1 - float(c['value'])), 2)
            hit = l <= trig
            px = min(trig, o)
            if not hit:
                store.update_cond(c['id'], trigger_price=trig,
                                  peak=round(max(peak_now, h), 4))
                continue

        if not hit:
            continue
        qty = _cond_sell_qty(c, pos)
        if qty <= 0:
            continue
        px = round(px, 2)
        fee = calc_fee(px, qty, 'sell', code)
        oid = store.insert_order(asof, code, pos.get('name') or '', 'sell', 'limit', px, qty, 0)
        store.apply_fill(code, pos.get('name') or '', 'sell', px, qty, fee, asof, oid)
        store.update_cond(c['id'], status='triggered', triggered_asof=asof, trigger_price=trig)
        gid = c.get('group_id') or ''
        left = store.get_position(code)
        if not left or int(left.get('qty') or 0) <= 0:
            # 清仓：该标的所有剩余条件（含止损/定时等并行组）一并失效
            for x in store.list_cond(code=code, status='active') + \
                    store.list_cond(code=code, status='queued'):
                if x['id'] != c['id']:
                    store.update_cond(x['id'], status='expired')
        elif gid:
            _activate_next(code, gid, c['id'])
        else:
            for other in active:
                if other['id'] != c['id']:
                    store.update_cond(other['id'], status='expired')
        hits.append({'cond_id': c['id'], 'kind': c['kind'], 'code': code,
                     'price': px, 'qty': qty, 'fee': fee, 'order_id': oid})
        break
    store.update_peak(code, h)
    return hits


def account_view(last_prices=None):
    """账户概览 + 持仓盈亏。last_prices: {code: price}，缺省用持仓里记的 last_price。"""
    last_prices = last_prices or {}
    acc = store.get_account()
    positions = store.list_positions()
    mv = 0.0
    rows = []
    for p in positions:
        px = float(last_prices.get(p['code']) or p['last_price'] or 0)
        if px > 0:
            store.mark_last_price(p['code'], px)
        value = px * p['qty']
        pnl = (px - p['cost']) * p['qty'] if p['cost'] else 0.0
        mv += value
        rows.append({
            'code': p['code'], 'name': p['name'], 'qty': p['qty'], 'avail_qty': p['avail_qty'],
            'cost': round(p['cost'], 4), 'last_price': round(px, 2),
            'market_value': round(value, 2), 'pnl': round(pnl, 2),
            'pnl_pct': round((px / p['cost'] - 1) * 100, 2) if p['cost'] else 0,
        })
    nav = round(acc.get('cash', 0) + acc.get('frozen', 0) + mv, 2)
    init = acc.get('initial_cash') or 1
    return {
        'cash': round(acc.get('cash', 0), 2),
        'frozen': round(acc.get('frozen', 0), 2),
        'market_value': round(mv, 2),
        'nav': nav,
        'total_return_pct': round((nav / init - 1) * 100, 2),
        'initial_cash': round(init, 2),
        'positions': rows,
        'mode': acc.get('mode', 'replay'),
    }
