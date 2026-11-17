# -*- coding: utf-8 -*-
"""模拟交易账本：SQLite 按「回测 / 实盘」两套独立账本持久化。

设计要求：
- 回测（backtest）与实盘（live）各自一个库文件，资金/持仓/委托/条件单/净值完全隔离，
  同一只股票可以同时出现在两个账本里而互不影响。
- 模式通过线程局部变量切换（use_mode），Flask 每个请求一线程，无需给所有函数加参数。
- 所有写操作走同一个 RLock + 短事务，避免 Flask threaded 下的并发覆盖。
- 金额一律保留 2 位，股数一律整数。
"""

import os
import sqlite3
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'sim_trading.db')          # backtest（历史数据在此）
DB_PATH_LIVE = os.path.join(BASE_DIR, 'sim_trading_live.db')  # live

MODES = ('backtest', 'live')
_DEFAULT_MODE = 'backtest'
_TL = threading.local()

_LOCK = threading.RLock()
_INITED = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS account(
  id INTEGER PRIMARY KEY,
  mode TEXT DEFAULT 'replay',
  initial_cash REAL DEFAULT 0,
  cash REAL DEFAULT 0,
  frozen REAL DEFAULT 0,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS position(
  code TEXT PRIMARY KEY,
  name TEXT,
  qty INTEGER DEFAULT 0,
  avail_qty INTEGER DEFAULT 0,
  cost REAL DEFAULT 0,
  last_price REAL DEFAULT 0,
  last_buy_date TEXT DEFAULT '',
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, asof TEXT,
  code TEXT, name TEXT,
  side TEXT, otype TEXT,
  price REAL, qty INTEGER,
  filled INTEGER DEFAULT 0, avg_price REAL DEFAULT 0,
  status TEXT DEFAULT 'pending',
  reason TEXT DEFAULT '',
  frozen REAL DEFAULT 0,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS trades(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER, ts TEXT, asof TEXT,
  code TEXT, side TEXT,
  price REAL, qty INTEGER,
  amount REAL, fee REAL
);
CREATE TABLE IF NOT EXISTS snapshots(
  trade_date TEXT PRIMARY KEY,
  cash REAL, market_value REAL, nav REAL, pnl REAL, drawdown REAL,
  trade_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts);
CREATE TABLE IF NOT EXISTS cond_orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT, qty INTEGER,
  kind TEXT,                 -- tp=止盈 sl=止损 trail=峰值回落
  value REAL,                -- 百分比小数，如 0.08 表示 8%
  arm_value REAL DEFAULT 0,  -- 激活收益率：涨到 成本×(1+arm) 才启用回落；0 为立即生效
  armed INTEGER DEFAULT 0,   -- 是否已激活
  trigger_price REAL DEFAULT 0,
  peak REAL DEFAULT 0,
  status TEXT DEFAULT 'active',   -- active/queued/triggered/cancelled/expired
  created_asof TEXT, triggered_asof TEXT, note TEXT,
  seq INTEGER DEFAULT 0,          -- 同组条件执行顺序，越小越先
  qty_mode TEXT DEFAULT 'all',    -- all=全部可用 pct=持仓比例 shares=固定股数
  qty_value REAL DEFAULT 0,       -- pct/shares 的数值
  due_date TEXT,                  -- timed 到期日（YYYY-MM-DD）
  group_id TEXT,                  -- 同一只股票同一批 exits 归为一组
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_cond_status ON cond_orders(status);
CREATE TABLE IF NOT EXISTS plan_marks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_file TEXT, batch_id TEXT, run_date TEXT, mode TEXT,
  asof TEXT, status TEXT, ts TEXT, detail TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_plan_mark ON plan_marks(plan_file,batch_id,run_date,mode);
CREATE TABLE IF NOT EXISTS plan_pool(
  plan_file TEXT, batch_id TEXT, mode TEXT, code TEXT, name TEXT,
  side TEXT, cond_count INTEGER DEFAULT 0, ts TEXT,
  PRIMARY KEY(plan_file, batch_id, mode, code)
);
CREATE TABLE IF NOT EXISTS auto_tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT, mode TEXT, content TEXT,
  run_at TEXT, weekdays TEXT DEFAULT '1,2,3,4,5',
  auto_cond INTEGER DEFAULT 1, enabled INTEGER DEFAULT 1,
  last_run TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS auto_logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, source TEXT, trigger_name TEXT, asof TEXT, target TEXT,
  total INTEGER DEFAULT 0, success INTEGER DEFAULT 0, fail INTEGER DEFAULT 0,
  detail TEXT
);
CREATE TABLE IF NOT EXISTS file_marks(
  name TEXT PRIMARY KEY, md5 TEXT, ok INTEGER DEFAULT 1,
  success INTEGER DEFAULT 0, fail INTEGER DEFAULT 0, ts TEXT
);
CREATE TABLE IF NOT EXISTS plan_jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_path TEXT,
  interval_min INTEGER DEFAULT 60,
  only_session INTEGER DEFAULT 1,
  enabled INTEGER DEFAULT 1,
  last_run TEXT DEFAULT '',
  last_result TEXT DEFAULT '',
  created_at TEXT,
  sched_type TEXT DEFAULT 'interval',  -- interval=按间隔 daily=每天定点 weekly=每周定点 once=指定日期
  at_time TEXT DEFAULT '',             -- HH:MM，daily/weekly/once 使用
  weekday INTEGER DEFAULT -1,          -- 0=周一 … 6=周日，weekly 使用
  run_date TEXT DEFAULT ''             -- YYYY-MM-DD，once 使用
);
"""

DEFAULT_CASH = 500000.0


def use_mode(mode):
    """切换当前线程使用的账本（backtest / live），返回生效的模式。"""
    m = (mode or _DEFAULT_MODE).strip().lower()
    if m not in MODES:
        m = _DEFAULT_MODE
    _TL.mode = m
    return m


def current_mode():
    return getattr(_TL, 'mode', _DEFAULT_MODE)


def db_path(mode=None):
    return DB_PATH_LIVE if (mode or current_mode()) == 'live' else DB_PATH


def _conn():
    c = sqlite3.connect(db_path(), timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def init(mode=None):
    """建表 + 保证账本里存在单账户。按模式幂等。"""
    m = mode or current_mode()
    path = db_path(m)
    if path in _INITED:
        return
    with _LOCK:
        if path in _INITED:
            return
        c = sqlite3.connect(path, timeout=15)
        try:
            c.executescript(SCHEMA)
            # 老库补列：持仓期最高价（移动止盈用）
            try:
                c.execute('ALTER TABLE position ADD COLUMN peak_price REAL DEFAULT 0')
            except sqlite3.OperationalError:
                pass
            # 老库补列：带激活阈值的回落止盈
            for col, ddl in (('arm_value', 'REAL DEFAULT 0'), ('armed', 'INTEGER DEFAULT 0'),
                             ('seq', 'INTEGER DEFAULT 0'), ('qty_mode', "TEXT DEFAULT 'all'"),
                             ('qty_value', 'REAL DEFAULT 0'), ('due_date', 'TEXT'),
                             ('group_id', 'TEXT')):
                try:
                    c.execute('ALTER TABLE cond_orders ADD COLUMN %s %s' % (col, ddl))
                except sqlite3.OperationalError:
                    pass
            # 老库补列：定时实盘的调度方式（间隔 / 每天 / 每周 / 指定日期）
            for col, ddl in (('sched_type', "TEXT DEFAULT 'interval'"),
                             ('at_time', "TEXT DEFAULT ''"),
                             ('weekday', 'INTEGER DEFAULT -1'),
                             ('run_date', "TEXT DEFAULT ''")):
                try:
                    c.execute('ALTER TABLE plan_jobs ADD COLUMN %s %s' % (col, ddl))
                except sqlite3.OperationalError:
                    pass
            row = c.execute('SELECT id FROM account WHERE id=1').fetchone()
            if not row:
                c.execute(
                    'INSERT INTO account(id,mode,initial_cash,cash,frozen,updated_at) '
                    'VALUES(1,?,?,?,0,?)',
                    (m, DEFAULT_CASH, DEFAULT_CASH, _now()))
            c.commit()
            _INITED.add(path)
        finally:
            c.close()


def reset(cash=None):
    """清空当前模式账本，重置为初始资金。"""
    init()
    with _LOCK:
        c = _conn()
        try:
            for t in ('position', 'orders', 'trades', 'snapshots', 'cond_orders', 'auto_logs'):
                c.execute('DELETE FROM %s' % t)
            amt = float(cash) if cash and float(cash) > 0 else DEFAULT_CASH
            c.execute(
                'UPDATE account SET cash=?, frozen=0, initial_cash=?, mode=?, updated_at=? WHERE id=1',
                (amt, amt, current_mode(), _now()))
            c.commit()
        finally:
            c.close()


def get_account():
    init()
    with _LOCK:
        c = _conn()
        try:
            r = c.execute('SELECT * FROM account WHERE id=1').fetchone()
            return dict(r) if r else {}
        finally:
            c.close()


def set_mode(mode):
    with _LOCK:
        c = _conn()
        try:
            c.execute('UPDATE account SET mode=?, updated_at=? WHERE id=1', (mode, _now()))
            c.commit()
        finally:
            c.close()


def list_positions():
    init()
    with _LOCK:
        c = _conn()
        try:
            rows = c.execute('SELECT * FROM position WHERE qty>0 ORDER BY qty*last_price DESC').fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()


def get_position(code):
    init()
    with _LOCK:
        c = _conn()
        try:
            r = c.execute('SELECT * FROM position WHERE code=?', (code,)).fetchone()
            return dict(r) if r else None
        finally:
            c.close()


def apply_fill(code, name, side, price, qty, fee, asof, order_id, trade_ts=None):
    """一次成交的完整记账：现金、持仓成本、成交明细、委托进度。

    side=buy  → 现金扣款，持仓移动加权成本
    side=sell → 现金收款，持仓减少，已实现盈亏间接体现在净值
    """
    with _LOCK:
        c = _conn()
        try:
            c.execute('BEGIN IMMEDIATE')
            trade_ts = trade_ts or _now()
            amt = round(price * qty, 2)
            # ---- 成交明细 ----
            c.execute(
                'INSERT INTO trades(order_id,ts,asof,code,side,price,qty,amount,fee) '
                'VALUES(?,?,?,?,?,?,?,?,?)',
                (order_id, trade_ts, asof, code, side, price, qty, amt, fee))
            # ---- 账户现金 ----
            acc = c.execute('SELECT cash,frozen FROM account WHERE id=1').fetchone()
            cash, frozen = acc['cash'], acc['frozen']
            if side == 'buy':
                # 下单时已 freeze_cash：成交从冻结里扣，不重复扣现金
                used = amt + fee
                if frozen >= used:
                    frozen = round(frozen - used, 2)
                else:
                    cash = round(cash - (used - frozen), 2)
                    frozen = 0.0
            else:
                cash = round(cash + amt - fee, 2)
            c.execute('UPDATE account SET cash=?, frozen=?, updated_at=? WHERE id=1',
                      (cash, frozen, _now()))
            # ---- 持仓 ----
            p = c.execute('SELECT * FROM position WHERE code=?', (code,)).fetchone()
            if side == 'buy':
                if p:
                    new_qty = p['qty'] + qty
                    new_cost = round((p['cost'] * p['qty'] + amt + fee) / new_qty, 4)
                    c.execute(
                        'UPDATE position SET qty=?, avail_qty=?, cost=?, last_price=?, '
                        'last_buy_date=?, updated_at=? WHERE code=?',
                        (new_qty, p['avail_qty'], new_cost, price, asof, _now(), code))
                else:
                    c.execute(
                        'INSERT INTO position(code,name,qty,avail_qty,cost,last_price,last_buy_date,updated_at) '
                        'VALUES(?,?,?,0,?,?,?,?)',
                        (code, name, qty, round((amt + fee) / qty, 4), price, asof, _now()))
            else:
                if p:
                    new_qty = p['qty'] - qty
                    new_avail = max(0, p['avail_qty'] - qty)
                    c.execute(
                        'UPDATE position SET qty=?, avail_qty=?, last_price=?, updated_at=? WHERE code=?',
                        (new_qty, new_avail, price, _now(), code))
            # ---- 委托进度 ----
            o = c.execute('SELECT qty,filled,avg_price FROM orders WHERE id=?', (order_id,)).fetchone()
            filled = (o['filled'] or 0) + qty
            avg = price if not o['avg_price'] else round(
                (o['avg_price'] * o['filled'] + price * qty) / filled, 4)
            status = 'filled' if filled >= o['qty'] else 'partial'
            c.execute('UPDATE orders SET filled=?, avg_price=?, status=?, updated_at=? WHERE id=?',
                      (filled, avg, status, _now(), order_id))
            c.commit()
            return {'cash': cash}
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def unfreeze_cash(amount):
    with _LOCK:
        c = _conn()
        try:
            c.execute('UPDATE account SET cash=cash+?, frozen=MAX(0,frozen-?), updated_at=? WHERE id=1',
                      (round(amount, 2), round(amount, 2), _now()))
            c.commit()
        finally:
            c.close()


def freeze_cash(amount):
    with _LOCK:
        c = _conn()
        try:
            c.execute('UPDATE account SET cash=cash-?, frozen=frozen+?, updated_at=? WHERE id=1',
                      (round(amount, 2), round(amount, 2), _now()))
            c.commit()
        finally:
            c.close()


def insert_order(asof, code, name, side, otype, price, qty, frozen):
    with _LOCK:
        c = _conn()
        try:
            cur = c.execute(
                'INSERT INTO orders(ts,asof,code,name,side,otype,price,qty,filled,avg_price,'
                'status,reason,frozen,updated_at) VALUES(?,?,?,?,?,?,?,?,0,0,\'pending\',\'\',?,?)',
                (_now(), asof, code, name, side, otype, price, qty, frozen, _now()))
            c.commit()
            return cur.lastrowid
        finally:
            c.close()


def reject_order(asof, code, name, side, otype, price, qty, reason):
    with _LOCK:
        c = _conn()
        try:
            cur = c.execute(
                'INSERT INTO orders(ts,asof,code,name,side,otype,price,qty,filled,avg_price,'
                'status,reason,frozen,updated_at) VALUES(?,?,?,?,?,?,?,?,0,0,\'rejected\',?,0,?)',
                (_now(), asof, code, name, side, otype, price, qty, reason, _now()))
            c.commit()
            return cur.lastrowid
        finally:
            c.close()


def update_order(order_id, **kw):
    if not kw:
        return
    with _LOCK:
        c = _conn()
        try:
            sets = ','.join('%s=?' % k for k in kw)
            vals = list(kw.values()) + [_now(), order_id]
            c.execute('UPDATE orders SET %s, updated_at=? WHERE id=?' % sets, vals)
            c.commit()
        finally:
            c.close()


def list_orders(status=None, limit=100):
    init()
    with _LOCK:
        c = _conn()
        try:
            if status and status != 'all':
                rows = c.execute('SELECT * FROM orders WHERE status=? ORDER BY id DESC LIMIT ?',
                                 (status, limit)).fetchall()
            else:
                rows = c.execute('SELECT * FROM orders ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()


def pending_orders():
    init()
    with _LOCK:
        c = _conn()
        try:
            rows = c.execute('SELECT * FROM orders WHERE status IN (\'pending\',\'partial\') ORDER BY id').fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()


def list_trades(limit=100):
    init()
    with _LOCK:
        c = _conn()
        try:
            rows = c.execute('SELECT * FROM trades ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()


def release_t1(asof):
    """把 last_buy_date 早于 asof 的持仓解冻为可用（T+1）。

    只允许"时间向前推进"解冻：asof 必须严格晚于买入日，
    否则把回放日期往回拨也会解锁，等于凭空多出可卖数量。
    """
    with _LOCK:
        c = _conn()
        try:
            c.execute('UPDATE position SET avail_qty=qty, updated_at=? '
                      'WHERE last_buy_date<? AND qty>0 AND avail_qty<qty',
                      (_now(), asof))
            c.commit()
        finally:
            c.close()


def mark_last_price(code, price):
    with _LOCK:
        c = _conn()
        try:
            c.execute('UPDATE position SET last_price=?, updated_at=? WHERE code=?',
                      (price, _now(), code))
            c.commit()
        finally:
            c.close()


def save_snapshot(trade_date, cash, market_value, nav, pnl, drawdown, trade_count):
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                'INSERT INTO snapshots(trade_date,cash,market_value,nav,pnl,drawdown,trade_count) '
                'VALUES(?,?,?,?,?,?,?) '
                'ON CONFLICT(trade_date) DO UPDATE SET cash=excluded.cash, market_value=excluded.market_value, '
                'nav=excluded.nav, pnl=excluded.pnl, drawdown=excluded.drawdown, trade_count=excluded.trade_count',
                (trade_date, round(cash, 2), round(market_value, 2), round(nav, 2),
                 round(pnl, 2), round(drawdown, 4), trade_count))
            c.commit()
        finally:
            c.close()


def list_snapshots(limit=400):
    init()
    with _LOCK:
        c = _conn()
        try:
            rows = c.execute('SELECT * FROM snapshots ORDER BY trade_date LIMIT ?', (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()


# ---------------- 条件单（止盈 / 止损 / 峰值回落） ----------------
def add_cond(code, qty, kind, value, created_asof, trigger_price=0, arm_value=0,
             seq=0, qty_mode='all', qty_value=0, due_date='', group_id='', status='active'):
    with _LOCK:
        c = _conn()
        try:
            cur = c.execute(
                'INSERT INTO cond_orders(code,qty,kind,value,arm_value,trigger_price,status,'
                'created_asof,seq,qty_mode,qty_value,due_date,group_id,updated_at) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (code, int(qty), kind, float(value), float(arm_value),
                 float(trigger_price), status, created_asof,
                 int(seq or 0), qty_mode or 'all', float(qty_value or 0),
                 due_date or '', group_id or '', _now()))
            c.commit()
            return cur.lastrowid
        finally:
            c.close()


def plan_marked(plan_file, batch_id, run_date, mode):
    """该计划文件的该批次在该日期、该模式下是否已执行。"""
    init()
    with _LOCK:
        c = _conn()
        try:
            row = c.execute(
                'SELECT 1 FROM plan_marks WHERE plan_file=? AND batch_id=? AND run_date=? AND mode=?',
                (plan_file, batch_id, run_date, mode)).fetchone()
            return bool(row)
        finally:
            c.close()


def mark_plan(plan_file, batch_id, run_date, mode, asof, status, detail=''):
    init()
    with _LOCK:
        c = _conn()
        try:
            c.execute(
                'INSERT OR IGNORE INTO plan_marks(plan_file,batch_id,run_date,mode,asof,status,ts,detail) '
                'VALUES(?,?,?,?,?,?,?,?)',
                (plan_file, batch_id, run_date, mode, asof, status, _now(), detail))
            c.execute(
                'UPDATE plan_marks SET asof=?, status=?, ts=?, detail=? '
                'WHERE plan_file=? AND batch_id=? AND run_date=? AND mode=?',
                (asof, status, _now(), detail, plan_file, batch_id, run_date, mode))
            c.commit()
        finally:
            c.close()


def list_plan_marks(limit=60):
    init()
    with _LOCK:
        c = _conn()
        try:
            sql = 'SELECT * FROM plan_marks ORDER BY id DESC LIMIT ?'
            return [dict(r) for r in c.execute(sql, (int(limit),)).fetchall()]
        finally:
            c.close()


def save_plan_pool(plan_file, batch_id, mode, rows):
    """记录计划文件涉及的股票，供个股列表按模式展示。"""
    init()
    with _LOCK:
        c = _conn()
        try:
            ts = _now()
            for r in rows:
                c.execute(
                    'INSERT OR REPLACE INTO plan_pool(plan_file,batch_id,mode,code,name,side,cond_count,ts) '
                    'VALUES(?,?,?,?,?,?,?,?)',
                    (plan_file, batch_id, mode, r.get('code'), r.get('name') or '',
                     r.get('side') or '', int(r.get('cond') or 0), ts))
            c.commit()
        finally:
            c.close()


def list_plan_pool(mode):
    init()
    with _LOCK:
        c = _conn()
        try:
            rows = c.execute(
                'SELECT * FROM plan_pool WHERE mode=? ORDER BY ts DESC, code ASC', (mode,)).fetchall()
            seen, out = set(), []
            for r in rows:
                d = dict(r)
                if d['code'] in seen:
                    continue
                seen.add(d['code'])
                out.append(d)
            return out
        finally:
            c.close()


def list_cond(code=None, status=None):
    init()
    with _LOCK:
        c = _conn()
        try:
            sql, where, args = 'SELECT * FROM cond_orders', [], []
            if code:
                where.append('code=?'); args.append(code)
            if status:
                where.append('status=?'); args.append(status)
            if where:
                sql += ' WHERE ' + ' AND '.join(where)
            sql += ' ORDER BY id DESC'
            return [dict(r) for r in c.execute(sql, args).fetchall()]
        finally:
            c.close()


def update_cond(cid, **kw):
    if not kw:
        return
    with _LOCK:
        c = _conn()
        try:
            sets = ','.join('%s=?' % k for k in kw)
            c.execute('UPDATE cond_orders SET %s, updated_at=? WHERE id=?' % sets,
                      list(kw.values()) + [_now(), cid])
            c.commit()
        finally:
            c.close()


def update_peak(code, high):
    """记录持仓期间最高价，移动止盈的基准。"""
    with _LOCK:
        c = _conn()
        try:
            c.execute('UPDATE position SET peak_price=MAX(peak_price,?), updated_at=? WHERE code=?',
                      (float(high), _now(), code))
            c.commit()
        finally:
            c.close()


# ---------------- 自动化：任务 / 日志 / 文件去重 ----------------
def list_auto_tasks():
    init()
    with _LOCK:
        c = _conn()
        try:
            return [dict(r) for r in c.execute('SELECT * FROM auto_tasks ORDER BY id DESC')]
        finally:
            c.close()


def add_auto_task(name, mode, content, run_at, weekdays='1,2,3,4,5', auto_cond=1, enabled=1):
    init()
    with _LOCK:
        c = _conn()
        try:
            cur = c.execute(
                'INSERT INTO auto_tasks(name,mode,content,run_at,weekdays,auto_cond,enabled,updated_at) '
                'VALUES(?,?,?,?,?,?,?,?)',
                (name, mode, content, run_at, weekdays, int(auto_cond), int(enabled), _now()))
            c.commit()
            return cur.lastrowid
        finally:
            c.close()


def update_auto_task(tid, **kw):
    if not kw:
        return
    with _LOCK:
        c = _conn()
        try:
            sets = ','.join('%s=?' % k for k in kw)
            c.execute('UPDATE auto_tasks SET %s, updated_at=? WHERE id=?' % sets,
                      list(kw.values()) + [_now(), tid])
            c.commit()
        finally:
            c.close()


def delete_auto_task(tid):
    with _LOCK:
        c = _conn()
        try:
            c.execute('DELETE FROM auto_tasks WHERE id=?', (tid,))
            c.commit()
        finally:
            c.close()


def touch_auto_task(tid, ts):
    update_auto_task(tid, last_run=ts)


def add_auto_log(ts, source, trigger, asof, target, total, success, fail, detail=''):
    with _LOCK:
        c = _conn()
        try:
            c.execute('INSERT INTO auto_logs(ts,source,trigger_name,asof,target,total,success,fail,detail) '
                      'VALUES(?,?,?,?,?,?,?,?,?)',
                      (ts, source, trigger, asof, target, int(total), int(success), int(fail),
                       str(detail)[:4000]))
            c.commit()
        finally:
            c.close()


def list_auto_logs(limit=80):
    init()
    with _LOCK:
        c = _conn()
        try:
            return [dict(r) for r in c.execute(
                'SELECT * FROM auto_logs ORDER BY id DESC LIMIT ?', (int(limit),))]
        finally:
            c.close()


def file_marked(name, md5):
    init()
    with _LOCK:
        c = _conn()
        try:
            row = c.execute('SELECT md5 FROM file_marks WHERE name=?', (name,)).fetchone()
            return bool(row) and (row['md5'] == md5)
        finally:
            c.close()


def mark_file(name, md5, ok=1, success=0, fail=0):
    with _LOCK:
        c = _conn()
        try:
            c.execute('INSERT OR REPLACE INTO file_marks(name,md5,ok,success,fail,ts) '
                      'VALUES(?,?,?,?,?,?)',
                      (name, md5, int(ok), int(success), int(fail), _now()))
            c.commit()
        finally:
            c.close()


# ---------------- 定时实盘：按间隔重复读取同一个批次计划文件 ----------------
def list_plan_jobs():
    init()
    with _LOCK:
        c = _conn()
        try:
            return [dict(r) for r in c.execute('SELECT * FROM plan_jobs ORDER BY id DESC')]
        finally:
            c.close()


def save_plan_job(file_path, interval_min=60, only_session=1, enabled=1, jid=0,
                  sched_type='interval', at_time='', weekday=-1, run_date=''):
    """有 jid 则更新，否则新建；返回任务 id。

    sched_type: interval=每 N 分钟 / daily=每天 HH:MM / weekly=每周 X HH:MM /
                once=指定日期 YYYY-MM-DD HH:MM
    """
    init()
    st = (sched_type or 'interval').strip().lower()
    if st not in ('interval', 'daily', 'weekly', 'once'):
        st = 'interval'
    at = (at_time or '').strip()[:5]
    wd = int(weekday) if str(weekday).lstrip('-').isdigit() else -1
    rd = (run_date or '').strip()[:10]
    with _LOCK:
        c = _conn()
        try:
            if jid:
                c.execute('UPDATE plan_jobs SET file_path=?, interval_min=?, only_session=?, enabled=?, '
                          'sched_type=?, at_time=?, weekday=?, run_date=? WHERE id=?',
                          (file_path, int(interval_min), int(only_session), int(enabled),
                           st, at, wd, rd, int(jid)))
                c.commit()
                return int(jid)
            cur = c.execute(
                'INSERT INTO plan_jobs(file_path,interval_min,only_session,enabled,last_run,last_result,created_at,'
                'sched_type,at_time,weekday,run_date) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (file_path, int(interval_min), int(only_session), int(enabled), '', '', _now(),
                 st, at, wd, rd))
            c.commit()
            return cur.lastrowid
        finally:
            c.close()


def delete_plan_job(jid):
    with _LOCK:
        c = _conn()
        try:
            c.execute('DELETE FROM plan_jobs WHERE id=?', (int(jid),))
            c.commit()
        finally:
            c.close()


def touch_plan_job(jid, ts, result=''):
    with _LOCK:
        c = _conn()
        try:
            c.execute('UPDATE plan_jobs SET last_run=?, last_result=? WHERE id=?',
                      (ts, str(result)[:300], int(jid)))
            c.commit()
        finally:
            c.close()
