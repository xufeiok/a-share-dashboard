"""量化批次计划（JSON）解析与时间判定。

只负责结构与时间：不取行情、不下单、不动数据库，便于单测与复用。

计划结构见 plans/templates/quant_batch_template.json：
    plan.batches[] -> batch.schedule / batch.items[] -> item.exits[]
"""
import json
import re
from datetime import datetime, date, timedelta

MODES = ('backtest', 'live')
CODE_RE = re.compile(r'^(\d{6})\.(SH|SZ|BJ)$')


# ---------------------------------------------------------------- 结构
def _as_int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _as_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _norm_code(v):
    s = str(v or '').strip().upper().replace(' ', '')
    m = re.match(r'^(\d{6})\.?(SH|SZ|BJ)?$', s)
    if not m:
        return ''
    d, suf = m.group(1), m.group(2) or ''
    if suf:
        return d + '.' + suf
    if d[0] == '6':
        return d + '.SH'
    if d[0] in ('0', '3'):
        return d + '.SZ'
    if d[0] in ('4', '8'):
        return d + '.BJ'
    return ''


def _pct(v, allow_negative=False):
    """8 / 8% / 0.08 -> 0.08；allow_negative 时保留负号（价格偏移用）。"""
    s = str(v if v is not None else '').strip().replace('%', '').replace(',', '')
    if not s:
        return 0.0
    try:
        x = float(s)
    except (TypeError, ValueError):
        return 0.0
    if abs(x) > 1:
        x = x / 100.0
    return x if allow_negative else max(0.0, min(x, 1.0))


def _qty_spec(raw, default=None):
    if isinstance(raw, dict):
        t = str(raw.get('type') or 'shares').strip().lower()
        v = _as_float(raw.get('value'), 0.0)
        return {'type': t, 'value': v}
    if raw in (None, '', 0):
        if default:
            return dict(default)
        return {'type': 'shares', 'value': 0.0}
    return {'type': 'shares', 'value': _as_float(raw, 0.0)}


def _price_spec(raw, default=None):
    if isinstance(raw, dict):
        t = str(raw.get('type') or 'market').strip().lower()
        v = _as_float(raw.get('value'), 0.0)
        return {'type': t, 'value': v}
    if raw in (None, '', 0):
        if default:
            return dict(default)
        return {'type': 'market', 'value': 0.0}
    return {'type': 'limit', 'value': _as_float(raw, 0.0)}


def parse(data):
    """规范化计划，返回 (plan, errors)。errors 非空时不应执行。"""
    errs = []
    if not isinstance(data, dict):
        return None, ['JSON 顶层必须是对象']
    defs = data.get('defaults') or {}
    d_qty = _qty_spec(defs.get('qty'), {'type': 'shares', 'value': 100})
    d_price = _price_spec(defs.get('price'), {'type': 'market', 'value': 0.0})
    d_exit_qty = _qty_spec(defs.get('exit_qty'), {'type': 'pct', 'value': 1.0})

    mode = str(data.get('mode') or 'live').strip().lower()
    if mode not in MODES:
        errs.append('mode 只能是 backtest 或 live，当前为 %r' % mode)
        mode = 'live'

    batches = data.get('batches')
    if not isinstance(batches, list) or not batches:
        return None, ['batches 必须是非空数组']

    out = []
    seen_ids = set()
    for bi, b in enumerate(batches):
        if not isinstance(b, dict):
            errs.append('batches[%d] 必须是对象' % bi)
            continue
        bid = str(b.get('id') or ('b%d' % (bi + 1))).strip()
        if bid in seen_ids:
            errs.append('批次 id 重复: %s' % bid)
            continue
        seen_ids.add(bid)
        sch = b.get('schedule') or {}
        st = str(sch.get('type') or '').strip().lower()
        if st not in ('once', 'daily', 'monthly_nth_weekday'):
            errs.append('批次 %s 的 schedule.type 不支持: %r' % (bid, st))
            continue
        sched = {'type': st, 'time': str(sch.get('time') or '09:31').strip()}

        if st == 'once':
            d = str(sch.get('date') or '').strip()
            if not re.match(r'^\d{4}-\d{2}-\d{2}$', d):
                errs.append('批次 %s once 缺少合法 date（YYYY-MM-DD）' % bid)
                continue
            sched['date'] = d
        elif st == 'daily':
            wds = sch.get('weekdays')
            if isinstance(wds, str):
                wds = [x.strip() for x in re.split(r'[,\s]+', wds) if x.strip()]
            if not isinstance(wds, list) or not wds:
                wds = [1, 2, 3, 4, 5]
            sched['weekdays'] = sorted({_as_int(x) for x in wds if 1 <= _as_int(x) <= 7})
        else:
            sched['nth'] = _as_int(sch.get('nth'), 1)
            sched['weekday'] = _as_int(sch.get('weekday'), 1)
            if not (1 <= sched['nth'] <= 5):
                errs.append('批次 %s nth 需在 1~5' % bid)
            if not (1 <= sched['weekday'] <= 7):
                errs.append('批次 %s weekday 需在 1~7（1=周一）' % bid)

        if sch.get('date_from'):
            sched['date_from'] = str(sch['date_from']).strip()
        if sch.get('date_to'):
            sched['date_to'] = str(sch['date_to']).strip()

        items = b.get('items')
        if not isinstance(items, list) or not items:
            errs.append('批次 %s 的 items 必须是非空数组' % bid)
            continue

        norm_items = []
        for ii, it in enumerate(items):
            if not isinstance(it, dict):
                errs.append('批次 %s items[%d] 必须是对象' % (bid, ii))
                continue
            code = _norm_code(it.get('code'))
            if not code:
                errs.append('批次 %s items[%d] 代码不合法: %r' % (bid, ii, it.get('code')))
                continue
            side = 'sell' if str(it.get('side') or 'buy').lower() in ('sell', '卖') else 'buy'
            qty = _qty_spec(it.get('qty'), d_qty)
            price = _price_spec(it.get('price'), d_price)
            exits = []
            raw_exits = it.get('exits') or []
            if not isinstance(raw_exits, list):
                errs.append('批次 %s %s 的 exits 必须是数组' % (bid, code))
                raw_exits = []
            for ei, ex in enumerate(raw_exits):
                if not isinstance(ex, dict):
                    errs.append('批次 %s %s exits[%d] 必须是对象' % (bid, code, ei))
                    continue
                kind = str(ex.get('kind') or '').strip().lower()
                if kind not in ('tp', 'sl', 'trail', 'fixed_price', 'timed'):
                    errs.append('批次 %s %s exits[%d] kind 不支持: %r' % (bid, code, ei, kind))
                    continue
                e = {'seq': _as_int(ex.get('seq'), ei + 1),
                     'kind': kind,
                     'qty': _qty_spec(ex.get('qty'), d_exit_qty),
                     'due': str(ex.get('due') or '').strip(),
                     'arm': _pct(ex.get('arm')) if kind == 'trail' else 0.0}
                if kind == 'fixed_price':
                    e['value'] = _as_float(ex.get('value'), 0.0)
                    if e['value'] <= 0:
                        errs.append('批次 %s %s 固定价卖出缺少 value' % (bid, code))
                        continue
                elif kind == 'timed':
                    if not re.match(r'^\d{4}-\d{2}-\d{2}$', e['due']):
                        errs.append('批次 %s %s timed 缺少合法 due（YYYY-MM-DD）' % (bid, code))
                        continue
                    e['value'] = 0.0
                else:
                    e['value'] = _pct(ex.get('value'))
                    if e['value'] <= 0:
                        errs.append('批次 %s %s exits 的 value 必须大于 0' % (bid, code))
                        continue
                exits.append(e)
            exits.sort(key=lambda x: (x['seq'], x['kind']))
            norm_items.append({'code': code, 'name': str(it.get('name') or '').strip(),
                               'side': side, 'qty': qty, 'price': price, 'exits': exits})

        if not norm_items:
            errs.append('批次 %s 没有合法 item' % bid)
            continue
        out.append({'id': bid, 'name': str(b.get('name') or bid).strip(),
                    'enabled': bool(b.get('enabled', True)), 'schedule': sched,
                    'items': norm_items})

    if not out:
        errs.append('没有任何可用批次')
    return {'version': str(data.get('version') or '1.0'), 'mode': mode,
            'name': str(data.get('name') or '').strip(), 'batches': out}, errs


def load(path):
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
        text = f.read()
    try:
        data = json.loads(text)
    except Exception as e:
        return None, ['JSON 解析失败: %s' % e]
    return parse(data)


# ---------------------------------------------------------------- 时间判定
def _parse_date(s):
    try:
        return datetime.strptime(str(s).strip(), '%Y-%m-%d').date()
    except Exception:
        return None


def nth_weekday_of_month(year, month, nth, weekday):
    """本月第 nth 个 weekday（1=周一）；不存在返回 None。"""
    first = date(year, month, 1)
    # weekday(): 周一=0，需转成 1=周一
    delta = (weekday - 1) - first.weekday()
    day = 1 + (delta % 7) + (nth - 1) * 7
    try:
        d = date(year, month, day)
    except ValueError:
        return None
    return d if d.month == month else None


def batch_due(batch, now, mode='live', last_run_date=''):
    """判断批次是否该执行。

    返回 {'due': bool, 'asof': 'YYYY-MM-DD', 'reason': str}。
    asof 是这笔交易应使用的行情日期（回测=计划日期；实盘=今天）。
    last_run_date：该批次已执行的日期（YYYY-MM-DD），同一天不重复。
    """
    sch = batch.get('schedule') or {}
    st = sch.get('type')
    today = now.date()
    tstr = str(sch.get('time') or '09:31')
    m = re.match(r'^(\d{1,2}):(\d{2})$', tstr)
    hh, mm = (int(m.group(1)), int(m.group(2))) if m else (9, 31)
    at_or_after = (now.hour, now.minute) >= (hh, mm)

    if last_run_date and last_run_date == today.isoformat():
        return {'due': False, 'asof': '', 'reason': '今天已执行过'}

    d_from = _parse_date(sch.get('date_from')) if sch.get('date_from') else None
    d_to = _parse_date(sch.get('date_to')) if sch.get('date_to') else None
    if d_from and today < d_from:
        return {'due': False, 'asof': '', 'reason': '未到 date_from'}
    if d_to and today > d_to:
        return {'due': False, 'asof': '', 'reason': '已过 date_to'}

    if st == 'once':
        target = _parse_date(sch.get('date'))
        if not target:
            return {'due': False, 'asof': '', 'reason': 'once 日期非法'}
        if today < target:
            return {'due': False, 'asof': '', 'reason': '未到计划日期 %s' % target}
        if today == target:
            if not at_or_after:
                return {'due': False, 'asof': '', 'reason': '未到计划时间 %s' % tstr}
            return {'due': True, 'asof': target.isoformat(), 'reason': '按计划执行'}
        # 已过期
        if mode == 'backtest':
            return {'due': True, 'asof': target.isoformat(),
                    'reason': '回测补执行（计划日 %s 已过）' % target}
        return {'due': False, 'asof': '',
                'reason': '实盘模式跳过：计划日 %s 已过' % target}

    if st == 'daily':
        wds = sch.get('weekdays') or [1, 2, 3, 4, 5]
        if today.isoweekday() not in wds:
            return {'due': False, 'asof': '', 'reason': '今天不在配置的星期内'}
        if not at_or_after:
            return {'due': False, 'asof': '', 'reason': '未到计划时间 %s' % tstr}
        return {'due': True, 'asof': today.isoformat(), 'reason': '按每日计划执行'}

    if st == 'monthly_nth_weekday':
        target = nth_weekday_of_month(today.year, today.month, sch.get('nth', 1),
                                      sch.get('weekday', 1))
        if not target:
            return {'due': False, 'asof': '', 'reason': '本月没有第 %s 个周 %s' %
                    (sch.get('nth'), sch.get('weekday'))}
        if today < target:
            return {'due': False, 'asof': '', 'reason': '未到本月计划日 %s' % target}
        if today == target:
            if not at_or_after:
                return {'due': False, 'asof': '', 'reason': '未到计划时间 %s' % tstr}
            return {'due': True, 'asof': target.isoformat(), 'reason': '按本月计划执行'}
        if mode == 'backtest':
            return {'due': True, 'asof': target.isoformat(),
                    'reason': '回测补执行（本月计划日 %s 已过）' % target}
        return {'due': False, 'asof': '',
                'reason': '实盘模式跳过：本月计划日 %s 已过' % target}

    return {'due': False, 'asof': '', 'reason': '未知计划类型'}


def validate_report(plan, errors):
    """给前端校验接口用的摘要。"""
    if errors:
        return {'ok': False, 'errors': errors}
    batches = []
    for b in plan['batches']:
        items = [{'code': it['code'], 'side': it['side'], 'exits': len(it['exits'])}
                 for it in b['items']]
        batches.append({'id': b['id'], 'name': b['name'], 'enabled': b['enabled'],
                        'schedule': b['schedule'], 'items': items})
    return {'ok': True, 'mode': plan['mode'], 'name': plan['name'],
            'batch_count': len(plan['batches']), 'batches': batches}
