"""模拟盘自动化：批量交易 / 选股文件扫描 / 定时任务。

执行器（取行情、下单、建条件单）由 server.py 注入，避免与 Flask 应用相互导入。
"""
import os
import re
import csv
import json
import hashlib
import threading
import time
import traceback
from datetime import datetime, date

import sim_store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INBOX = os.path.join(BASE_DIR, 'trade_inbox')
PROCESSED_DIR = os.path.join(DEFAULT_INBOX, 'processed')
FAILED_DIR = os.path.join(DEFAULT_INBOX, 'failed')
ALLOWED_EXT = ('.csv', '.txt', '.json')

_CFG_PATH = os.path.join(BASE_DIR, 'sim_auto_config.json')
_CFG_DEFAULT = {
    'enabled': False,
    'watch_dir': DEFAULT_INBOX,
    'interval_min': 5,
    'auto_cond': True,
    'default_qty': 100,
}

_EXECUTOR = None          # (items, asof, auto_cond, source) -> list[dict]
_PLAN_EXECUTOR = None     # (path, asof) -> dict（量化批次计划 JSON）
_SWEEPER = None           # (asof) -> list（推进全部持仓的条件单）
_RUN_LOCK = threading.Lock()
_STOP = threading.Event()
_THREAD = None

# ---------------------------------------------------------------- 配置
def cfg_read():
    c = dict(_CFG_DEFAULT)
    try:
        with open(_CFG_PATH, 'r', encoding='utf-8') as f:
            c.update(json.load(f) or {})
    except Exception:
        pass
    return c


def cfg_write(patch):
    c = cfg_read()
    for k, v in (patch or {}).items():
        if k in _CFG_DEFAULT:
            c[k] = v
    try:
        with open(_CFG_PATH, 'w', encoding='utf-8') as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return c


def set_executor(fn):
    global _EXECUTOR
    _EXECUTOR = fn


def set_plan_executor(fn):
    global _PLAN_EXECUTOR
    _PLAN_EXECUTOR = fn


def set_sweeper(fn):
    global _SWEEPER
    _SWEEPER = fn


# ---------------------------------------------------------------- 解析
_ALIAS = {
    'code': ('code', 'ts_code', '代码', '股票代码', '证券代码'),
    'name': ('name', '名称', '股票名称', '证券名称'),
    'side': ('side', 'direction', '方向', '买卖', '操作'),
    'qty': ('qty', 'volume', '数量', '股数'),
    'amount': ('amount', '金额', '资金', '仓位金额'),
    'price': ('price', '限价', '价格', '委托价'),
    'tp': ('tp', 'take_profit', '止盈', '止盈%', '止盈率'),
    'sl': ('sl', 'stop_loss', '止损', '止损%', '止损率'),
    'trail': ('trail', 'trailing', '回落', '回落%', '回撤'),
    'arm': ('arm', 'arm_pct', '先涨', '先涨%', '激活'),
}


def norm_code(s):
    s = str(s or '').strip().upper().replace(' ', '')
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


def _pct(v):
    s = str(v or '').strip().replace('%', '').replace(',', '')
    if not s:
        return 0.0
    try:
        x = float(s)
    except (TypeError, ValueError):
        return 0.0
    if x >= 1:          # 8 / 100 这类按百分数处理
        x = x / 100.0
    return max(0.0, min(x, 0.99))


def _side(v):
    s = str(v or '').strip().lower()
    if s in ('sell', 's', '卖', '卖出', '空'):
        return 'sell'
    return 'buy'


def _pick(row, key):
    for k in _ALIAS[key]:
        if k in row:
            return row[k]
        for rk in row:
            if str(rk).strip().lower() == k.lower():
                return row[rk]
    return ''


def _row_to_item(row, cfg):
    code = norm_code(_pick(row, 'code'))
    if not code:
        return None
    side = _side(_pick(row, 'side'))
    try:
        qty = int(float(str(_pick(row, 'qty') or 0).strip() or 0))
    except (TypeError, ValueError):
        qty = 0
    try:
        amount = float(str(_pick(row, 'amount') or 0).strip() or 0)
    except (TypeError, ValueError):
        amount = 0.0
    try:
        price = float(str(_pick(row, 'price') or 0).strip() or 0)
    except (TypeError, ValueError):
        price = 0.0
    if qty <= 0 and side == 'buy':
        qty = int(cfg.get('default_qty') or 100)
    return {
        'code': code,
        'name': str(_pick(row, 'name') or '').strip(),
        'side': side,
        'qty': qty,
        'amount': amount,
        'price': price,
        'tp': _pct(_pick(row, 'tp')),
        'sl': _pct(_pick(row, 'sl')),
        'trail': _pct(_pick(row, 'trail')),
        'arm': _pct(_pick(row, 'arm')),
    }


def parse_text(text, default_qty=100):
    """解析选股文件文本。支持带表头 CSV/TSV，或每行一个代码。

    列名兼容中英文；百分比可写 8 / 8% / 0.08。
    """
    cfg = {'default_qty': default_qty}
    lines = []
    for raw in (text or '').splitlines():
        s = raw.strip().lstrip('\ufeff')
        if not s or s.startswith('#') or s.startswith('//'):
            continue
        lines.append(s)
    if not lines:
        return []

    head = lines[0]
    sep = '\t' if '\t' in head else (',' if ',' in head else None)
    # 纯代码列表：每行一个（允许逗号/空格分隔多只）
    if sep is None and not re.match(r'^\s*(code|代码|股票代码)', head, re.I):
        out, seen = [], set()
        for ln in lines:
            toks = [t for t in re.split(r'[\s,，;；]+', ln) if t]
            if not toks:
                continue
            qty, side, qty_set = int(default_qty), 'buy', False
            codes = []
            for i, t in enumerate(toks):
                c = norm_code(t)
                if c:
                    codes.append(c)
                    continue
                if (not qty_set) and i > 0 and re.match(r'^\d{1,6}$', t):
                    qty, qty_set = int(t), True
                elif t.lower() in ('buy', 'sell', '买', '卖', '买入', '卖出'):
                    side = 'sell' if t.lower() in ('sell', '卖', '卖出') else 'buy'
            for c in codes:
                if c in seen:
                    continue
                seen.add(c)
                out.append({'code': c, 'name': '', 'side': side, 'qty': qty,
                            'amount': 0.0, 'price': 0.0,
                            'tp': 0.0, 'sl': 0.0, 'trail': 0.0, 'arm': 0.0})
        return out

    # 表头识别：首行含 code/代码 才当表头，否则按无表头处理
    has_header = bool(re.search(r'(code|代码|股票代码)', head, re.I))
    out = []
    if has_header:
        names = [x.strip() for x in head.split(sep)]
        for ln in lines[1:]:
            cols = [x.strip() for x in ln.split(sep)]
            row = dict(zip(names, cols))
            it = _row_to_item(row, cfg)
            if it:
                out.append(it)
    else:
        for ln in lines:
            cols = [x.strip() for x in re.split(r'[,\t，]', ln)]
            row = {'code': cols[0] if cols else '',
                   'qty': cols[1] if len(cols) > 1 else '',
                   'side': cols[2] if len(cols) > 2 else 'buy'}
            it = _row_to_item(row, cfg)
            if it:
                out.append(it)
    return out


def parse_file(path, default_qty=100):
    ext = os.path.splitext(path)[1].lower()
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
        text = f.read()
    if ext == '.json':
        try:
            data = json.loads(text)
        except Exception:
            return parse_text(text, default_qty)
        if isinstance(data, dict):
            data = data.get('items') or data.get('stocks') or []
        items = []
        for row in (data or []):
            if isinstance(row, str):
                c = norm_code(row)
                if c:
                    items.append({'code': c, 'name': '', 'side': 'buy',
                                  'qty': int(default_qty), 'amount': 0.0, 'price': 0.0,
                                  'tp': 0.0, 'sl': 0.0, 'trail': 0.0, 'arm': 0.0})
            elif isinstance(row, dict):
                it = _row_to_item({str(k).lower(): v for k, v in row.items()},
                                  {'default_qty': default_qty})
                if it:
                    items.append(it)
        return items
    return parse_text(text, default_qty)


# ---------------------------------------------------------------- 执行
def execute(items, asof='', auto_cond=True, source='manual'):
    """调用注入的执行器，逐条下单并返回明细。"""
    if not items:
        return {'ok': False, 'reason': '没有可执行的标的', 'results': []}
    if _EXECUTOR is None:
        return {'ok': False, 'reason': '执行器未初始化', 'results': []}
    with _RUN_LOCK:
        try:
            results = _EXECUTOR(items, asof, bool(auto_cond), source) or []
        except Exception:
            return {'ok': False, 'reason': traceback.format_exc().splitlines()[-1],
                    'results': []}
    ok = len([r for r in results if r.get('ok')])
    return {'ok': True, 'total': len(results), 'success': ok,
            'failed': len(results) - ok, 'results': results}


def run_file(path, asof='', auto_cond=True, source='file'):
    cfg = cfg_read()
    # 量化批次计划（含 batches 的 JSON）走计划执行器：分批、定时、多条件卖出
    if os.path.splitext(path)[1].lower() == '.json' and _PLAN_EXECUTOR is not None:
        try:
            with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
                head = json.load(f)
            if isinstance(head, dict) and head.get('batches'):
                res = _PLAN_EXECUTOR(path, asof)
                res['file'] = os.path.basename(path)
                res['items'] = res.get('total', 0)
                return res
        except Exception:
            pass
    items = parse_file(path, cfg.get('default_qty', 100))
    res = execute(items, asof, auto_cond, source)
    res['file'] = os.path.basename(path)
    res['items'] = len(items)
    return res


# ---------------------------------------------------------------- 扫描
def _md5(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def _archive(path, ok):
    cfg = cfg_read()
    base = cfg.get('watch_dir') or DEFAULT_INBOX
    target_dir = os.path.join(base, 'processed') if ok else os.path.join(base, 'failed')
    try:
        os.makedirs(target_dir, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        dst = os.path.join(target_dir, stamp + '_' + os.path.basename(path))
        if os.path.exists(dst):
            dst = os.path.join(target_dir, stamp + '_' + str(int(time.time() * 1000)) + '_' + os.path.basename(path))
        os.replace(path, dst)
        return dst
    except Exception:
        return ''


def scan(asof='', trigger='scan'):
    """扫描目录并执行新文件；同一文件内容只执行一次。"""
    cfg = cfg_read()
    base = cfg.get('watch_dir') or DEFAULT_INBOX
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        return {'ok': False, 'reason': '扫描目录不可用: %s' % base, 'files': []}
    files = []
    for fn in sorted(os.listdir(base)):
        p = os.path.join(base, fn)
        if os.path.isfile(p) and os.path.splitext(fn)[1].lower() in ALLOWED_EXT:
            files.append(p)
    if not files:
        return {'ok': True, 'files': [], 'total': 0, 'message': '目录内没有待处理文件'}

    done, failed = [], []
    for p in files:
        try:
            digest = _md5(p)
        except Exception:
            continue
        if sim_store.file_marked(os.path.basename(p), digest):
            done.append({'file': os.path.basename(p), 'skipped': '已处理过，内容未变'})
            continue
        res = run_file(p, asof, cfg.get('auto_cond', True), 'scan:' + trigger)
        sim_store.mark_file(os.path.basename(p), digest, 1 if res.get('failed', 0) == 0 else 0,
                            res.get('success', 0), res.get('failed', 0))
        sim_store.add_auto_log(datetime.now().isoformat(timespec='seconds'), 'file', trigger,
                               asof, os.path.basename(p), res.get('total', 0),
                               res.get('success', 0), res.get('failed', 0),
                               json.dumps(res.get('results', [])[:30], ensure_ascii=False))
        if res.get('ok'):
            dst = _archive(p, res.get('failed', 0) == 0) if cfg.get('watch_dir') else ''
            done.append({'file': os.path.basename(p), 'success': res.get('success', 0),
                         'failed': res.get('failed', 0), 'moved_to': dst})
        else:
            failed.append({'file': os.path.basename(p), 'reason': res.get('reason', '')})
    return {'ok': True, 'files': done, 'failed': failed,
            'total': len(done), 'asof': asof}


# ---------------------------------------------------------------- 定时任务
def due_tasks(now=None):
    now = now or datetime.now()
    dur = []
    for t in sim_store.list_auto_tasks():
        if not int(t.get('enabled') or 0):
            continue
        hhmm = (t.get('run_at') or '').strip()
        if not re.match(r'^\d{1,2}:\d{2}$', hhmm):
            continue
        cur = now.strftime('%H:%M')
        if cur < hhmm:
            continue
        wd = str(t.get('weekdays') or '1,2,3,4,5').replace(' ', '')
        if str(now.isoweekday()) not in wd.split(','):
            continue
        if (t.get('last_run') or '')[:10] == now.strftime('%Y-%m-%d'):
            continue
        dur.append(t)
    return dur


def run_task(t, asof=''):
    mode = (t.get('mode') or 'list').strip()
    content = (t.get('content') or '').strip()
    auto_cond = bool(int(t.get('auto_cond') or 0))
    cfg = cfg_read()
    if mode == 'file':
        paths = [x.strip() for x in re.split(r'[\r\n;|]+', content) if x.strip()]
        res = {'ok': True, 'total': 0, 'success': 0, 'failed': 0, 'results': []}
        for p in paths:
            if not os.path.isabs(p):
                p = os.path.join(cfg.get('watch_dir') or DEFAULT_INBOX, p)
            if not os.path.exists(p):
                res['results'].append({'ok': False, 'reason': '文件不存在: %s' % p})
                res['failed'] += 1
                continue
            r = run_file(p, asof, auto_cond, 'task:' + str(t.get('id')))
            res['results'] += r.get('results', [])
            res['total'] += r.get('total', 0)
            res['success'] += r.get('success', 0)
            res['failed'] += r.get('failed', 0)
    else:
        items = parse_text(content, cfg.get('default_qty', 100))
        res = execute(items, asof, auto_cond, 'task:' + str(t.get('id')))
        res['total'] = res.get('total', len(items))
    sim_store.touch_auto_task(t['id'], datetime.now().isoformat(timespec='seconds'))
    sim_store.add_auto_log(datetime.now().isoformat(timespec='seconds'), 'task',
                           t.get('name') or ('任务%d' % t['id']), asof,
                           content[:120], res.get('total', 0), res.get('success', 0),
                           res.get('failed', 0),
                           json.dumps(res.get('results', [])[:30], ensure_ascii=False))
    return res


# ---------------------------------------------------------------- 调度线程
def _in_session(now=None):
    """A 股连续竞价时段（工作日 09:30-11:30 / 13:00-15:00）。"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return (570 <= hm <= 690) or (780 <= hm <= 900)


def _job_sched_desc(j):
    """任务调度的人读描述，前端也按同一口径展示。"""
    st = (j.get('sched_type') or 'interval').lower()
    at = (j.get('at_time') or '').strip() or '09:31'
    if st == 'daily':
        return '每天 ' + at
    if st == 'weekly':
        try:
            wd = int(j.get('weekday'))
        except (TypeError, ValueError):
            wd = -1
        names = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
        return '每' + (names[wd] if 0 <= wd < 7 else '周?') + ' ' + at
    if st == 'once':
        return ((j.get('run_date') or '').strip() or '未设日期') + ' ' + at
    return '每 %s 分钟' % (j.get('interval_min') or 60)


def _job_due(j, now):
    """判断任务此刻是否该执行，返回 (bool, 原因)。

    interval：距上次执行满 N 分钟；
    daily  ：每天 HH:MM 之后首次巡检执行一次；
    weekly ：每周指定星期 HH:MM 之后首次巡检执行一次；
    once   ：只在指定日期 HH:MM 之后执行一次。
    """
    st = (j.get('sched_type') or 'interval').lower()
    last = str(j.get('last_run') or '')
    same_day = last[:10] == now.strftime('%Y-%m-%d')
    if st == 'interval':
        interval = max(1, int(j.get('interval_min') or 60)) * 60
        if last:
            try:
                elapsed = (now - datetime.strptime(last, '%Y-%m-%d %H:%M:%S')).total_seconds()
                if elapsed < interval:
                    return False, '未到间隔'
            except Exception:
                pass
        return True, ''
    at = (j.get('at_time') or '').strip() or '09:31'
    if now.strftime('%H:%M') < at:
        return False, '未到 ' + at
    if st == 'once':
        d = (j.get('run_date') or '').strip()
        if not d:
            return False, '未设置日期'
        if now.strftime('%Y-%m-%d') != d:
            return False, '未到 ' + d
        return (False, '今日已执行') if same_day else (True, '')
    if st == 'weekly':
        try:
            wd = int(j.get('weekday'))
        except (TypeError, ValueError):
            wd = -1
        if wd < 0:
            return False, '未设置星期'
        if now.weekday() != wd:
            return False, '非指定星期'
        return (False, '今日已执行') if same_day else (True, '')
    return (False, '今日已执行') if same_day else (True, '')


def _run_plan_jobs():
    """定时实盘：按每个任务自己的调度方式读取同一份批次计划文件。

    每个实盘账户各自一份任务清单与账本，逐个账户轮流执行。
    """
    prev = sim_store.current_mode()
    prev_acct = sim_store.current_acct()
    now = datetime.now()
    try:
        sim_store.use_mode('live')
        for a in sim_store.list_accts():
            sim_store.use_acct(a['id'])
            for j in sim_store.list_plan_jobs():
                if not int(j.get('enabled') or 0):
                    continue
                path = (j.get('file_path') or '').strip()
                if not path or not os.path.exists(path):
                    continue
                if int(j.get('only_session') or 0) and not _in_session(now):
                    continue
                due, _why = _job_due(j, now)
                if not due:
                    continue
                if not _PLAN_EXECUTOR:
                    continue
                try:
                    r = _PLAN_EXECUTOR(path, '')
                    sim_store.touch_plan_job(j['id'], datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                             '成功 %s / 跳过 %s' % (r.get('success', 0), len(r.get('skipped') or [])))
                except Exception:
                    traceback.print_exc()
    finally:
        sim_store.use_acct(prev_acct)
        sim_store.use_mode(prev)


def _loop():
    last_scan = 0.0
    last_sweep = 0.0
    last_job = 0.0
    while not _STOP.is_set():
        try:
            cfg = cfg_read()
            if cfg.get('enabled'):
                interval = max(1, int(cfg.get('interval_min') or 5)) * 60
                if time.time() - last_scan >= interval:
                    scan('', 'auto')
                    last_scan = time.time()
            if _SWEEPER and time.time() - last_sweep >= 300:
                try:
                    _SWEEPER('')
                except Exception:
                    pass
                last_sweep = time.time()
            if time.time() - last_job >= 30:
                try:
                    _run_plan_jobs()
                except Exception:
                    pass
                last_job = time.time()
            for t in due_tasks():
                run_task(t, '')
        except Exception:
            pass
        _STOP.wait(20)


def start():
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, name='sim-auto', daemon=True)
    _THREAD.start()


def stop():
    _STOP.set()
