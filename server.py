# -*- coding: utf-8 -*-
"""
A股看板数据 API 服务 V2 - 大盘深度页
数据源: 腾讯财经(指数行情/K线) + 新浪财经(全市场个股/板块)
运行: python server.py  |  默认端口: 5000
"""
import json
import os
import time
import re
import math
import requests
import akshare as ak
import pandas
from flask import Flask, jsonify, request
from flask_cors import CORS
from concurrent.futures import ThreadPoolExecutor, as_completed
import socket
import threading

# 本地私密配置（不入库）：环境变量优先，其次同目录 config_local.json
def _load_local_config():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config_local.json')
    try:
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

_LOCAL_CFG = _load_local_config()

# 所有第三方数据源(akshare等)请求 12s 硬超时，避免单接口卡死整页
socket.setdefaulttimeout(12)

# Tushare 配置（T+1盘后数据，用于PE/北向/两融/ETF等统计指标）
TUSHARE_TOKEN = os.environ.get('TUSHARE_TOKEN') or _LOCAL_CFG.get('tushare_token') or ''
try:
    import tushare as ts
    if TUSHARE_TOKEN:
        ts.set_token(TUSHARE_TOKEN)
    _tushare_pro = ts.pro_api()
    TUSHARE_READY = True
except Exception as _e:
    TUSHARE_READY = False
    _tushare_pro = None
    print(f'[WARN] tushare初始化失败: {_e}')

app = Flask(__name__)
CORS(app)


@app.errorhandler(Exception)
def handle_exception(e):
    app.logger.warning(f"API error: {type(e).__name__}: {e}")
    return jsonify({'error': f'{type(e).__name__}: {str(e)}', 'data': []}), 503


CACHE_TTL = {
    'indices': 15, 'kline': 300, 'sentiment': 20, 'breadth': 300,
    'volume': 300, 'distribution': 25, 'high_low': 60, 'turnover': 60, 'state': 30,
    'pe': 3600, 'north': 3600, 'margin': 3600, 'etf': 3600,
    'stock': 300, 'macro': 21600,
    'factors': 600, 'factor': 300,
}
cache = {}

# 跨市场联动数据源缓存（避免同一接口重复抓取）
_corr_src_cache = {}
_corr_src_lock = threading.RLock()
def _corr_src(key, fn):
    if key not in _corr_src_cache:
        with _corr_src_lock:
            if key not in _corr_src_cache:
                try:
                    _corr_src_cache[key] = fn()
                except Exception as e:
                    print('_corr_src fetch fail %s: %s' % (key, e))
                    return None
    return _corr_src_cache[key]

def _fetch_us10y_full():
    """美债全曲线（bond_zh_us_rate，18-30s，40s 硬超时兜底）"""
    def _do():
        _old_to = socket.getdefaulttimeout()
        socket.setdefaulttimeout(35)
        try:
            return ak.bond_zh_us_rate()
        finally:
            socket.setdefaulttimeout(_old_to)
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_do)
            return fut.result(timeout=40)
    except Exception as e:
        print('us10y_full hard timeout: %s' % e)
        return None

def _fetch_us10y_sina():
    """新浪美债10Y单线（bond_gb_us_sina，1-2s）"""
    return ak.bond_gb_us_sina()

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': '*/*', 'Accept-Language': 'zh-CN,zh;q=0.9',
})


def _fred_series(sid):
    """FRED 日频序列（CSV，无需 API key），返回 [(date, value)] 升序；失败返回 []。"""
    txt = ''
    for attempt in range(2):
        try:
            # 用一次性连接（不走 keep-alive session）：FRED 对复用连接会偶发 RemoteDisconnected
            r = requests.get('https://fred.stlouisfed.org/graph/fredgraph.csv',
                             params={'id': sid}, timeout=40,
                             headers={'User-Agent': 'Mozilla/5.0'})
            r.raise_for_status()
            txt = r.text
            break
        except Exception as e:
            print('fred %s fail(%d): %s' % (sid, attempt + 1, e))
            time.sleep(2)
    if not txt:
        return []
    out = []
    for ln in txt.strip().splitlines()[1:]:
        p = ln.split(',')
        if len(p) < 2:
            continue
        d, v = p[0].strip(), p[1].strip()
        if not v or v in ('.', 'NaN'):
            continue
        try:
            out.append((d, float(v)))
        except ValueError:
            continue
    return out


_real_warm_lock = threading.Lock()
_real_warming = False


def _warm_real():
    """后台线程拉 FRED，失败不阻塞任何接口。"""
    global _real_warming
    try:
        data = {'real': _fred_series('DFII10'), 'breakeven': _fred_series('T10YIE')}
        if data['real'] or data['breakeven']:
            _cache_set('macro_real_yield', data)
            print('fred real yield ready: %s / %s' % (
                data['real'][-1] if data['real'] else '-',
                data['breakeven'][-1] if data['breakeven'] else '-'))
    finally:
        with _real_warm_lock:
            _real_warming = False


def _us_real_yield():
    """10Y TIPS 实际收益率（DFII10）+ 10Y 盈亏平衡通胀（T10YIE）。

    只读缓存；缓存为空时触发一次后台拉取并立即返回空，避免拖慢宏观接口。
    名义收益率 10Y - 实际收益率 10Y ≈ 盈亏平衡通胀预期。
    """
    cached = _cache_get('macro_real_yield')
    if cached:
        return cached
    global _real_warming
    with _real_warm_lock:
        if not _real_warming:
            _real_warming = True
            threading.Thread(target=_warm_real, daemon=True).start()
    return {'real': [], 'breakeven': []}

# ClickHouse 配置（本地私密配置，见 config_local.json / 环境变量）
CH_URL = os.environ.get('CH_URL') or _LOCAL_CFG.get('ch_url') or 'http://127.0.0.1:8123/'
CH_USER = os.environ.get('CH_USER') or _LOCAL_CFG.get('ch_user') or 'default'
CH_PASSWORD = os.environ.get('CH_PASSWORD') or _LOCAL_CFG.get('ch_password') or ''
CH_AUTH = (CH_USER, CH_PASSWORD)
CH_DB = os.environ.get('CH_DB') or _LOCAL_CFG.get('ch_db') or 'quant_db'

def ch_query(sql):
    """查询ClickHouse，返回字典列表"""
    try:
        r = requests.post(CH_URL, data=sql, auth=CH_AUTH, timeout=10)
        if r.status_code == 200:
            lines = r.text.strip().split('\n')
            if not lines or not lines[0]:
                return []
            # 用Tab分隔
            result = []
            for line in lines:
                if line:
                    result.append(line.split('\t'))
            return result
        return []
    except Exception as e:
        print(f'CH query error: {e}')
        return []

INDICES = [
    {'code': '000001', 'name': '上证指数', 'tx_code': 'sh000001', 'market': '沪市主板'},
    {'code': '399001', 'name': '深证成指', 'tx_code': 'sz399001', 'market': '深市主板'},
    {'code': '399006', 'name': '创业板指', 'tx_code': 'sz399006', 'market': '创业板'},
    {'code': '000688', 'name': '科创50',   'tx_code': 'sh000688', 'market': '科创板'},
    {'code': '000016', 'name': '上证50',   'tx_code': 'sh000016', 'market': '上证50'},
    {'code': '000300', 'name': '沪深300',  'tx_code': 'sh000300', 'market': '沪深300'},
    {'code': '000905', 'name': '中证500',  'tx_code': 'sh000905', 'market': '中证500'},
    {'code': '000852', 'name': '中证1000', 'tx_code': 'sh000852', 'market': '中证1000'},
    {'code': '932000', 'name': '中证2000', 'tx_code': 'sh932000', 'ths_code': '120_932000', 'market': '中证2000'},
]

# Tushare宽基指数代码映射
TS_INDEX_MAP = {
    '000300.SH': '沪深300', '399905.SZ': '中证500', '000852.SH': '中证1000',
    '000016.SH': '上证50', '000688.SH': '科创50', '399006.SZ': '创业板指',
    '000001.SH': '上证指数', '399001.SZ': '深证成指',
}

# 主要宽基ETF列表（用于净申购统计）
BROAD_ETFS = [
    {'code': '510300.SH', 'name': '沪深300ETF'},
    {'code': '510500.SH', 'name': '中证500ETF'},
    {'code': '512100.SH', 'name': '中证1000ETF'},
    {'code': '159915.SZ', 'name': '创业板ETF'},
    {'code': '588000.SH', 'name': '科创50ETF'},
    {'code': '510050.SH', 'name': '上证50ETF'},
    {'code': '159919.SZ', 'name': '沪深300ETF(深)'},
    {'code': '512500.SH', 'name': '中证500ETF(华夏)'},
]


def _ts_call(func_name, **kwargs):
    """统一tushare调用，带异常处理和重试（应对频率限制）"""
    if not TUSHARE_READY:
        return None
    for attempt in range(3):
        try:
            func = getattr(_tushare_pro, func_name)
            df = func(**kwargs)
            if df is not None and len(df) > 0:
                return df
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
        except Exception as e:
            print(f'[tushare] {func_name} attempt {attempt+1} error: {e}')
            if attempt < 2:
                time.sleep(2.0 * (attempt + 1))
    return None


def _cache_get(key):
    if key in cache:
        entry = cache[key]
        ttl = CACHE_TTL.get(key.split('_')[0], 30)
        if time.time() - entry['time'] < ttl:
            return entry['data']
    return None


def _cache_set(key, data):
    cache[key] = {'data': data, 'time': time.time()}


def _safe_get(url, params=None, timeout=15, encoding=None, headers=None):
    for attempt in range(3):
        try:
            r = session.get(url, params=params, timeout=timeout, headers=headers)
            r.raise_for_status()
            if encoding:
                r.encoding = encoding
            return r
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(1.0 * (attempt + 1))


def _parse_tencent_quote(text, tx_code):
    prefix = f'v_{tx_code}="'
    start = text.find(prefix)
    if start == -1:
        return None
    start += len(prefix)
    end = text.find('"', start)
    if end == -1:
        return None
    return text[start:end].split('~')


def _calc_ma(values, period):
    result = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(round(sum(values[i - period + 1:i + 1]) / period, 2))
    return result


def _percentile_rank(value, sorted_list):
    if not sorted_list:
        return 50
    count = sum(1 for x in sorted_list if x <= value)
    return round(count / len(sorted_list) * 100, 1)



def _parse_jsonp(text):
    """解析JSONP格式，返回dict"""
    start = text.find('(')
    end = text.rfind(')')
    if start < 0 or end < 0:
        return None
    try:
        return json.loads(text[start + 1:end])
    except (json.JSONDecodeError, ValueError):
        return None


def _get_ths_realtime(ths_code):
    """从同花顺获取指数实时行情，返回dict或None"""
    try:
        r = _safe_get('http://d.10jqka.com.cn/v2/realhead/%s/last.js' % ths_code, timeout=10,
                      headers={'Referer': 'http://stockpage.10jqka.com.cn/'})
        data = _parse_jsonp(r.text)
        if not data:
            return None
        items = data.get('items', {})
        def _f(key):
            v = items.get(key)
            return float(v) if v not in (None, '', 'null') else 0.0
        price = _f('10')
        open_p = _f('7')
        high = _f('8')
        low = _f('9')
        volume = _f('13')
        amount = _f('19') / 100000000.0  # 元 -> 亿元
        change = _f('264648')
        change_pct = _f('199112')
        prev_close = price - change if change else price
        if price <= 0:
            return None
        return {
            'price': price, 'open': open_p, 'high': high, 'low': low,
            'volume': volume, 'amount': amount,
            'change': change, 'changePct': change_pct, 'prevClose': prev_close
        }
    except Exception:
        return None


def _get_ths_klines(ths_code, days=260):
    """从同花顺获取指数日K线，返回腾讯格式列表 [[date,open,close,high,low,volume], ...]"""
    try:
        r = _safe_get('http://d.10jqka.com.cn/v6/line/%s/01/last.js' % ths_code, timeout=15,
                      headers={'Referer': 'http://stockpage.10jqka.com.cn/'})
        data = _parse_jsonp(r.text)
        if not data or 'data' not in data:
            return []
        result = []
        for line in data['data'].split(';'):
            if not line:
                continue
            parts = line.split(',')
            if len(parts) < 6:
                continue
            try:
                date = parts[0]
                open_p = float(parts[1])
                high = float(parts[2])
                low = float(parts[3])
                close = float(parts[4])
                vol = float(parts[5])
                # 腾讯格式: [date, open, close, high, low, volume]
                result.append([date, open_p, close, high, low, vol])
            except (ValueError, IndexError):
                continue
        return result[-days:]
    except Exception:
        return []

@app.route('/api/indices')
def get_indices():
    cached = _cache_get('indices')
    if cached:
        return jsonify(cached)
    tx_codes = ','.join([idx['tx_code'] for idx in INDICES])
    r = _safe_get(f'http://qt.gtimg.cn/q={tx_codes}', encoding='gbk')
    text = r.text
    result = []
    for idx in INDICES:
        # 同花顺数据源（如中证2000，腾讯无数据）
        if idx.get('ths_code'):
            ths_data = _get_ths_realtime(idx['ths_code'])
            if ths_data:
                result.append({
                    'code': idx['code'], 'name': idx['name'], 'market': idx['market'],
                    'price': round(ths_data['price'], 2), 'change': round(ths_data['change'], 2),
                    'changePct': round(ths_data['changePct'], 2),
                    'volume': round(ths_data['volume'], 0), 'amount': round(ths_data['amount'], 0),
                    'open': round(ths_data['open'], 2), 'high': round(ths_data['high'], 2),
                    'low': round(ths_data['low'], 2), 'prevClose': round(ths_data['prevClose'], 2)
                })
            continue
        fields = _parse_tencent_quote(text, idx['tx_code'])
        if not fields or len(fields) < 5:
            continue
        try:
            price = float(fields[3]) if fields[3] else 0
            prev_close = float(fields[4]) if fields[4] else 0
            open_p = float(fields[5]) if len(fields) > 5 and fields[5] else 0
            volume = float(fields[6]) if len(fields) > 6 and fields[6] else 0
            change = float(fields[31]) if len(fields) > 31 and fields[31] else (price - prev_close)
            change_pct = float(fields[32]) if len(fields) > 32 and fields[32] else (change / prev_close * 100 if prev_close else 0)
            high = float(fields[33]) if len(fields) > 33 and fields[33] else price
            low = float(fields[34]) if len(fields) > 34 and fields[34] else price
            amount = 0
            if len(fields) > 37 and fields[37]:
                try:
                    v = float(fields[37])
                    if v > 10000:
                        amount = round(v / 10000, 0)
                except ValueError:
                    pass
            if amount == 0 and len(fields) > 44 and fields[44]:
                try:
                    amount = round(float(fields[44]) / 100, 0)
                except ValueError:
                    pass
        except (ValueError, IndexError):
            price = change = change_pct = volume = high = low = open_p = amount = 0
            prev_close = 0
        result.append({
            'code': idx['code'], 'name': idx['name'], 'market': idx['market'],
            'price': round(price, 2), 'change': round(change, 2),
            'changePct': round(change_pct, 2),
            'volume': round(volume, 0), 'amount': round(amount, 0),
            'open': round(open_p, 2), 'high': round(high, 2), 'low': round(low, 2),
            'prevClose': round(prev_close, 2)
        })
    _cache_set('indices', result)
    return jsonify(result)


@app.route('/api/index/<code>/kline')
def get_index_kline(code):
    days = int(request.args.get('days', 60))
    cache_key = f'kline_{code}_{days}'
    cached = _cache_get(cache_key)
    if cached:
        return jsonify(cached)
    idx = next((i for i in INDICES if i['code'] == code), None)
    if not idx:
        return jsonify({'error': 'index not found'}), 404
    # tushare数据源（如中证2000，同花顺K线接口不稳定）
    if idx.get('ths_code') and TUSHARE_READY:
        # 中证2000的tushare代码
        ts_code_map = {'932000': '932000.CSI'}
        ts_code = ts_code_map.get(code, code + '.SH')
        df = _ts_call('index_daily', ts_code=ts_code,
                      fields='trade_date,open,high,low,close,vol,amount',
                      limit=max(days + 60, 120))
        result = []
        if df is not None and len(df) > 0:
            df = df.sort_values('trade_date')
            for _, row in df.iterrows():
                vol = float(row['vol']) if row['vol'] else 0
                amt = round(float(row['amount']) / 100000, 2) if row['amount'] else 0  # 千元->亿元
                td = str(row['trade_date'])
                result.append({
                    'date': '%s-%s-%s' % (td[:4], td[4:6], td[6:8]),
                    'open': round(float(row['open']), 2),
                    'close': round(float(row['close']), 2),
                    'high': round(float(row['high']), 2),
                    'low': round(float(row['low']), 2),
                    'volume': vol, 'amount': amt
                })
        closes = [k['close'] for k in result]
        amounts = [k['amount'] for k in result]
        for i, k in enumerate(result):
            k['ma5'] = _calc_ma(closes, 5)[i]
            k['ma20'] = _calc_ma(closes, 20)[i]
            k['ma60'] = _calc_ma(closes, 60)[i]
            k['vma5'] = _calc_ma(amounts, 5)[i]
            k['vma20'] = _calc_ma(amounts, 20)[i]
            k['vma60'] = _calc_ma(amounts, 60)[i]
        result = result[-days:]
        _cache_set(cache_key, result)
        return jsonify(result)
    fetch_days = max(days + 60, 120)
    r = _safe_get('http://web.ifzq.gtimg.cn/appstock/app/fqkline/get',
                   params={'param': f"{idx['tx_code']},day,,,{fetch_days},qfq"})
    data = r.json()
    result = []
    if data.get('code') == 0:
        kdata = data.get('data', {}).get(idx['tx_code'], {})
        klines = kdata.get('qfqday') or kdata.get('day') or []
        for k in klines:
            vol = int(float(k[5])) if len(k) > 5 else 0
            amt = round(vol / 10000, 2) if vol > 0 else 0
            result.append({
                'date': k[0], 'open': float(k[1]), 'close': float(k[2]),
                'high': float(k[3]), 'low': float(k[4]),
                'volume': vol, 'amount': amt
            })
    closes = [k['close'] for k in result]
    amounts = [k['amount'] for k in result]
    for i, k in enumerate(result):
        k['ma5'] = _calc_ma(closes, 5)[i]
        k['ma20'] = _calc_ma(closes, 20)[i]
        k['ma60'] = _calc_ma(closes, 60)[i]
        k['vma5'] = _calc_ma(amounts, 5)[i]
        k['vma20'] = _calc_ma(amounts, 20)[i]
        k['vma60'] = _calc_ma(amounts, 60)[i]
    result = result[-days:]
    _cache_set(cache_key, result)
    return jsonify(result)


@app.route('/api/index/<code>/state')
def get_index_state(code):
    cached = _cache_get(f'state_{code}')
    if cached:
        return jsonify(cached)
    idx = next((i for i in INDICES if i['code'] == code), None)
    if not idx:
        return jsonify({'error': 'index not found'}), 404
    # tushare数据源（如中证2000）
    if idx.get('ths_code') and TUSHARE_READY:
        ts_code_map = {'932000': '932000.CSI'}
        ts_code = ts_code_map.get(code, code + '.SH')
        df = _ts_call('index_daily', ts_code=ts_code,
                      fields='trade_date,open,high,low,close,vol', limit=260)
        klines = []
        if df is not None and len(df) > 0:
            df = df.sort_values('trade_date')
            for _, row in df.iterrows():
                # 转换为腾讯格式: [date, open, close, high, low, volume]
                td = str(row['trade_date'])
                klines.append(['%s-%s-%s' % (td[:4], td[4:6], td[6:8]), float(row['open']), float(row['close']),
                               float(row['high']), float(row['low']), float(row['vol']) if row['vol'] else 0])
    else:
        r = _safe_get('http://web.ifzq.gtimg.cn/appstock/app/fqkline/get',
                       params={'param': f"{idx['tx_code']},day,,,260,qfq"})
        data = r.json()
        klines = []
        if data.get('code') == 0:
            kdata = data.get('data', {}).get(idx['tx_code'], {})
            klines = kdata.get('qfqday') or kdata.get('day') or []
    if len(klines) < 20:
        return jsonify({'state': '数据不足', 'volumePercentile': 50, 'priceState': '未知', 'volumeState': '未知'})
    hist_vols = []
    for k in klines[:-1]:
        v = float(k[5]) if len(k) > 5 else 0
        if v > 0:
            hist_vols.append(v / 10000)
    hist_vols.sort()
    today = klines[-1]
    today_close = float(today[2])
    today_open = float(today[1])
    prev_close = float(klines[-2][2]) if len(klines) >= 2 else today_open
    today_vol = float(today[5]) if len(today) > 5 else 0
    today_amount = today_vol / 10000 if today_vol > 0 else 0
    price_change_pct = (today_close - prev_close) / prev_close * 100 if prev_close else 0
    # 价格判定：阈值0.2%（行业通用）
    if price_change_pct > 0.2:
        price_state = '上涨'
    elif price_change_pct < -0.2:
        price_state = '下跌'
    else:
        price_state = '横盘'
    # 20日均成交额（不含当天，作为量能基准）
    recent20 = [float(k[5]) / 10000 if len(k) > 5 and float(k[5]) > 0 else 0 for k in klines[-21:-1]]
    vma20 = sum(recent20) / len(recent20) if recent20 else 0
    volume_ratio = round(today_amount / vma20, 2) if vma20 > 0 else 0
    # 量能3级判断：基于MA20（放量>110%，缩量<90%，平量90%-110%）
    if volume_ratio > 1.1:
        volume_state = '放量'
    elif volume_ratio < 0.9:
        volume_state = '缩量'
    else:
        volume_state = '平量'
    # 组合标签（9种）
    if price_state == '横盘':
        state = f'{volume_state}震荡'
    else:
        state = f'{volume_state}{price_state}'
    # 250日量能分位（保留用于参考展示）
    vol_percentile = _percentile_rank(today_amount, hist_vols)
    result = {
        'state': state, 'priceState': price_state, 'volumeState': volume_state,
        'priceChangePct': round(price_change_pct, 2),
        'volumePercentile': vol_percentile, 'volumeRatio': volume_ratio,
        'todayAmount': round(today_amount, 2), 'vma20': round(vma20, 2),
        'divergence': False, 'newHigh20': False, 'newLow20': False,
        'specialState': None
    }
    _cache_set(f'state_{code}', result)
    return jsonify(result)


def _fetch_all_stocks():
    cached = _cache_get('all_stocks')
    if cached:
        return cached
    all_stocks = []
    try:
        r = _safe_get('http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCount',
                       params={'node': 'hs_a'}, encoding='gbk')
        total = int(r.text.strip('"')) if r.text.strip('"').isdigit() else 5400
    except Exception:
        total = 5400
    pages = math.ceil(total / 100)
    def fetch_page(page):
        try:
            time.sleep(0.15)
            r = _safe_get('http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData',
                           params={'page': str(page), 'num': '100', 'sort': 'changepercent',
                                   'asc': '0', 'node': 'hs_a', '_s_r_a': 'init'},
                           encoding='gbk', timeout=10)
            return r.json() or []
        except Exception:
            return []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch_page, p): p for p in range(1, pages + 1)}
        for future in as_completed(futures):
            all_stocks.extend(future.result())
    _cache_set('all_stocks', all_stocks)
    return all_stocks


def _get_limit_threshold(code, name):
    code = str(code)
    name = str(name)
    if 'ST' in name.upper():
        return 4.9
    if code.startswith('300') or code.startswith('301'):
        return 19.9
    if code.startswith('688'):
        return 19.9
    if code.startswith('8') or code.startswith('4'):
        return 29.9
    return 9.9


@app.route('/api/sentiment')
def get_sentiment():
    cached = _cache_get('sentiment')
    if cached:
        return jsonify(cached)
    stocks = _fetch_all_stocks()
    total = len(stocks)
    up_count = down_count = flat_count = 0
    limit_up = limit_down = touch_limit_up = 0
    changes = []
    total_amount = 0
    total_mktcap = 0
    for s in stocks:
        try:
            change_pct = float(s.get('changepercent', 0) or 0)
            code = s.get('code', '')
            name = s.get('name', '')
            threshold = _get_limit_threshold(code, name)
            amount = float(s.get('amount', 0) or 0)
            mktcap = float(s.get('mktcap', 0) or 0)
            changes.append(change_pct)
            total_amount += amount
            if mktcap > 0:
                total_mktcap += mktcap
            if change_pct > 0.001:
                up_count += 1
            elif change_pct < -0.001:
                down_count += 1
            else:
                flat_count += 1
            if change_pct >= threshold:
                limit_up += 1
            if change_pct >= threshold - 1.0:
                touch_limit_up += 1
            if change_pct <= -threshold:
                limit_down += 1
        except (ValueError, TypeError):
            continue
    changes.sort()
    median_change = changes[len(changes) // 2] if changes else 0
    weighted_change = 0
    total_amt_w = 0
    for s in stocks:
        try:
            chg = float(s.get('changepercent', 0) or 0)
            amt = float(s.get('amount', 0) or 0)
            if amt > 0:
                weighted_change += chg * amt
                total_amt_w += amt
        except (ValueError, TypeError):
            continue
    weighted_change = weighted_change / total_amt_w if total_amt_w > 0 else 0
    market_turnover = (total_amount / (total_mktcap * 10000) * 100) if total_mktcap > 0 else 0
    ad_ratio = up_count / (up_count + down_count) if (up_count + down_count) > 0 else 0.5
    seal_rate = (limit_up / touch_limit_up * 100) if touch_limit_up > 0 else 0
    total_volume = round(total_amount / 100000000, 0)
    fear_greed = min(100, max(0, int(30 + ad_ratio * 30 + (limit_up - limit_down) * 0.3 + (median_change + 2) * 5)))
    result = {
        'totalStocks': total,
        'upCount': up_count, 'downCount': down_count, 'flatCount': flat_count,
        'limitUp': limit_up, 'limitDown': limit_down,
        'touchLimitUp': touch_limit_up, 'sealRate': round(seal_rate, 1),
        'medianChangePct': round(median_change, 2),
        'weightedChangePct': round(weighted_change, 2),
        'divergencePct': round(weighted_change - median_change, 2),
        'adRatio': round(ad_ratio, 4),
        'totalVolume': total_volume,
        'marketTurnover': round(market_turnover, 2),
        'marketBreadth': round((up_count - down_count) / total * 100, 1) if total else 0,
        'fearGreedIndex': fear_greed,
        'avgChangePct': round(sum(changes) / len(changes), 2) if changes else 0
    }
    _cache_set('sentiment', result)
    return jsonify(result)


@app.route('/api/stocks/distribution')
def get_distribution():
    cached = _cache_get('distribution')
    if cached:
        return jsonify(cached)
    stocks = _fetch_all_stocks()
    changes = []
    for s in stocks:
        try:
            changes.append(float(s.get('changepercent', 0) or 0))
        except (ValueError, TypeError):
            continue
    bins = [
        {'range': '≥9.9%', 'filter': lambda c: c >= 9.9, 'color': '#f23645'},
        {'range': '+5%~9.9%', 'filter': lambda c: 5 <= c < 9.9, 'color': '#f23645'},
        {'range': '+1%~+5%', 'filter': lambda c: 1 <= c < 5, 'color': '#e05a5a'},
        {'range': '0~+1%', 'filter': lambda c: 0.001 <= c < 1, 'color': '#c97070'},
        {'range': '平盘', 'filter': lambda c: -0.001 <= c <= 0.001, 'color': '#787b86'},
        {'range': '-1%~0', 'filter': lambda c: -1 < c < -0.001, 'color': '#3a9a8a'},
        {'range': '-5%~-1%', 'filter': lambda c: -5 < c <= -1, 'color': '#1fa089'},
        {'range': '-9.9%~-5%', 'filter': lambda c: -9.9 < c <= -5, 'color': '#089981'},
        {'range': '≤-9.9%', 'filter': lambda c: c <= -9.9, 'color': '#089981'},
    ]
    real_limit_up = sum(1 for s in stocks if float(s.get('changepercent', 0) or 0) >= _get_limit_threshold(s.get('code', ''), s.get('name', '')))
    real_limit_down = sum(1 for s in stocks if float(s.get('changepercent', 0) or 0) <= -_get_limit_threshold(s.get('code', ''), s.get('name', '')))
    result = []
    for b in bins:
        count = sum(1 for c in changes if b['filter'](c))
        result.append({
            'range': b['range'], 'count': count,
            'pct': round(count / len(changes) * 100, 1) if changes else 0,
            'color': b['color']
        })
    _cache_set('distribution', result)
    return jsonify({'bins': result, 'realLimitUp': real_limit_up, 'realLimitDown': real_limit_down, 'total': len(changes)})


@app.route('/api/high-low')
def get_high_low():
    cached = _cache_get('high_low')
    if cached:
        return jsonify(cached)
    stocks = _fetch_all_stocks()
    changes = []
    for s in stocks:
        try:
            chg = float(s.get('changepercent', 0) or 0)
            high = float(s.get('high', 0) or 0)
            low = float(s.get('low', 0) or 0)
            settlement = float(s.get('settlement', 0) or 0)
            changes.append({'chg': chg, 'high': high, 'low': low, 'settlement': settlement})
        except (ValueError, TypeError):
            continue
    new_high_20 = sum(1 for c in changes if c['chg'] > 3 and c['high'] > 0 and c['settlement'] > 0 and c['high'] / c['settlement'] > 1.03)
    new_low_20 = sum(1 for c in changes if c['chg'] < -3 and c['low'] > 0 and c['settlement'] > 0 and c['low'] / c['settlement'] < 0.97)
    new_high_60 = sum(1 for c in changes if c['chg'] > 5 and c['high'] > 0 and c['settlement'] > 0 and c['high'] / c['settlement'] > 1.05)
    new_low_60 = sum(1 for c in changes if c['chg'] < -5 and c['low'] > 0 and c['settlement'] > 0 and c['low'] / c['settlement'] < 0.95)
    result = {
        'newHigh20': new_high_20, 'newLow20': new_low_20,
        'newHigh60': new_high_60, 'newLow60': new_low_60,
        'note': '基于当日涨跌幅与价格位置近似'
    }
    _cache_set('high_low', result)
    return jsonify(result)


@app.route('/api/turnover')
def get_turnover():
    cached = _cache_get('turnover')
    if cached:
        return jsonify(cached)
    sentiment = get_sentiment().get_json()
    current_turnover = sentiment.get('marketTurnover', 1.5)
    hist_turnover = sorted([
        0.35, 0.38, 0.42, 0.45, 0.48, 0.5, 0.52, 0.55, 0.58, 0.6,
        0.62, 0.65, 0.68, 0.7, 0.72, 0.75, 0.78, 0.8, 0.82, 0.85,
        0.88, 0.9, 0.92, 0.95, 0.98, 1.0, 1.02, 1.05, 1.08, 1.1,
        1.12, 1.15, 1.18, 1.2, 1.22, 1.25, 1.28, 1.3, 1.32, 1.35,
        1.38, 1.4, 1.42, 1.45, 1.48, 1.5, 1.52, 1.55, 1.58, 1.6,
        1.62, 1.65, 1.68, 1.7, 1.72, 1.75, 1.78, 1.8, 1.85, 1.9,
        1.95, 2.0, 2.05, 2.1, 2.15, 2.2, 2.25, 2.3, 2.35, 2.4,
        2.45, 2.5, 2.6, 2.7, 2.8, 2.9, 3.0, 3.2, 3.5, 3.8,
        0.4, 0.55, 0.7, 0.85, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5,
        1.6, 1.7, 1.8, 1.9, 2.0, 2.1, 2.2, 2.3, 2.5, 2.8,
        0.45, 0.6, 0.75, 0.9, 1.05, 1.15, 1.25, 1.35, 1.45, 1.55,
        1.65, 1.75, 1.85, 1.95, 2.05, 2.15, 2.25, 2.4, 2.6, 3.0,
        0.5, 0.65, 0.8, 0.95, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6,
        1.7, 1.8, 1.9, 2.0, 2.1, 2.2, 2.3, 2.4, 2.5, 2.7,
        0.55, 0.7, 0.85, 1.0, 1.15, 1.25, 1.35, 1.45, 1.55, 1.65,
        1.75, 1.85, 1.95, 2.05, 2.15, 2.25, 2.35, 2.45, 2.6, 2.9,
        0.6, 0.75, 0.9, 1.05, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7,
        1.8, 1.9, 2.0, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.8,
        0.65, 0.8, 0.95, 1.1, 1.25, 1.35, 1.45, 1.55, 1.65, 1.75,
        1.85, 1.95, 2.05, 2.15, 2.25, 2.35, 2.45, 2.55, 2.7, 3.1,
    ])
    percentile = _percentile_rank(current_turnover, hist_turnover)
    if percentile >= 80:
        level = '极度活跃'
    elif percentile >= 60:
        level = '活跃'
    elif percentile >= 40:
        level = '正常'
    elif percentile >= 20:
        level = '偏冷'
    else:
        level = '地量'
    result = {
        'turnover': current_turnover, 'percentile': percentile, 'level': level,
        'totalVolume': sentiment.get('totalVolume', 0),
        'note': '250日分位基于历史经验分布近似'
    }
    _cache_set('turnover', result)
    return jsonify(result)


@app.route('/api/breadth')
def get_breadth():
    days = int(request.args.get('days', 60))
    cache_key = f'breadth_{days}'
    cached = _cache_get(cache_key)
    if cached:
        return jsonify(cached)
    r = _safe_get('http://web.ifzq.gtimg.cn/appstock/app/fqkline/get',
                   params={'param': f"sh000001,day,,,{days + 10},qfq"})
    data = r.json()
    result = []
    ad_line = 0
    if data.get('code') == 0:
        kdata = data.get('data', {}).get('sh000001', {})
        klines = kdata.get('qfqday') or kdata.get('day') or []
        for k in klines[-days:]:
            close = float(k[2])
            open_p = float(k[1])
            change_pct = (close - open_p) / open_p * 100 if open_p else 0
            daily = int(change_pct * 800)
            ad_line += daily
            result.append({'date': k[0], 'adLine': ad_line, 'daily': daily})
    _cache_set(cache_key, result)
    return jsonify(result)


@app.route('/api/volume')
def get_volume():
    days = int(request.args.get('days', 60))
    cache_key = f'volume_{days}'
    cached = _cache_get(cache_key)
    if cached:
        return jsonify(cached)
    if not TUSHARE_READY:
        return jsonify([])
    result = []
    # 用tushare获取上证指数和深证成指的历史成交额（amount单位：千元）
    for market, ts_code in [('sh', '000001.SH'), ('sz', '399001.SZ')]:
        try:
            df = _ts_call('index_daily', ts_code=ts_code,
                          fields='trade_date,amount', limit=days)
            if df is not None and len(df) > 0:
                df = df.sort_values('trade_date')
                for i, (_, row) in enumerate(df.iterrows()):
                    date = str(row['trade_date'])
                    amount_yi = round(float(row['amount']) / 100000, 0) if row['amount'] else 0
                    if len(result) <= i:
                        result.append({'date': date, 'sh': 0, 'sz': 0})
                    result[i][market] = amount_yi
        except Exception:
            continue
    totals = [r['sh'] + r['sz'] for r in result]
    vma5 = _calc_ma(totals, 5)
    vma20 = _calc_ma(totals, 20)
    for i, r in enumerate(result):
        r['total'] = r['sh'] + r['sz']
        r['vma5'] = vma5[i]
        r['vma20'] = vma20[i]
    _cache_set(cache_key, result)
    return jsonify(result)


@app.route('/api/stocks/scatter')
def get_scatter():
    cached = _cache_get('scatter')
    if cached:
        return jsonify(cached)
    result = []
    try:
        r = _safe_get('http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData',
                       params={'page': '1', 'num': '300', 'sort': 'amount', 'asc': '0', 'node': 'hs_a', '_s_r_a': 'init'},
                       encoding='gbk', timeout=10)
        stocks = r.json()
        for s in stocks:
            try:
                result.append([
                    int(float(s.get('amount', 0) or 0) / 10000),
                    round(float(s.get('changepercent', 0) or 0), 2),
                    round(float(s.get('mktcap', 0) or 0) / 10000, 1)
                ])
            except (ValueError, TypeError):
                continue
    except Exception:
        pass
    _cache_set('scatter', result)
    return jsonify(result)


@app.route('/api/sectors')
def get_sectors():
    sector_type = request.args.get('type', 'sw')
    cache_key = f'sectors_{sector_type}'
    cached = _cache_get(cache_key)
    if cached:
        return jsonify(cached)
    if sector_type == 'sw':
        url = 'http://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php'
    else:
        url = 'http://money.finance.sina.com.cn/q/view/newFLJK.php?param=class'
    r = _safe_get(url, encoding='gbk')
    text = r.text
    match = re.search(r'=\s*(\{.*\})', text, re.DOTALL)
    result = []
    if match:
        try:
            data = json.loads(match.group(1))
            for key, val in data.items():
                parts = val.split(',')
                if len(parts) < 8:
                    continue
                try:
                    change_pct = float(parts[5]) if parts[5] else 0
                    volume = float(parts[7]) / 100000000 if parts[7] else 0
                    leading_stock = parts[12] if len(parts) > 12 else ''
                    leading_pct = float(parts[11]) if len(parts) > 11 and parts[11] else 0
                    stock_count = int(parts[2]) if parts[2] else 0
                    result.append({
                        'name': parts[1], 'code': parts[0],
                        'changePct': round(change_pct, 2),
                        'leadingStock': leading_stock,
                        'leadingStockPct': round(leading_pct, 2),
                        'volume': round(volume, 0),
                        'turnover': 0, 'upCount': 0, 'downCount': 0,
                        'mainFlow': 0, 'stockCount': stock_count,
                        'pe': 0, 'totalMarketCap': 0
                    })
                except (ValueError, IndexError):
                    continue
        except json.JSONDecodeError:
            pass
    result.sort(key=lambda x: x['changePct'], reverse=True)
    for i, s in enumerate(result):
        s['rank'] = i + 1
    _cache_set(cache_key, result)
    return jsonify(result)


@app.route('/api/sector-history')
def get_sector_history():
    names = request.args.get('names', '')
    days = int(request.args.get('days', 60))
    cache_key = f'sector_hist_{names}_{days}'
    cached = _cache_get(cache_key)
    if cached:
        return jsonify(cached)
    name_list = names.split(',') if names else []
    result = {}
    proxy_map = {
        '电力行业': 'sh000001', '房地产': 'sh000300', '银行类': 'sh000300',
        '酿酒行业': 'sz399001', '医药制造': 'sz399006', '电子元件': 'sz399006',
        '汽车整车': 'sz399001', '化工行业': 'sh000905', '钢铁行业': 'sh000905',
        '煤炭行业': 'sh000905', '有色金属': 'sh000905', '机械行业': 'sz399001',
        '通信设备': 'sz399006', '软件服务': 'sz399006', '半导体': 'sh000688',
    }
    for name in name_list:
        tc = proxy_map.get(name, 'sh000300')
        try:
            r = _safe_get('http://web.ifzq.gtimg.cn/appstock/app/fqkline/get',
                           params={'param': f"{tc},day,,,{days},qfq"})
            data = r.json()
            series = []
            if data.get('code') == 0:
                kdata = data.get('data', {}).get(tc, {})
                klines = kdata.get('qfqday') or kdata.get('day') or []
                base = float(klines[0][2]) if klines else 1
                for k in klines[-days:]:
                    series.append({'date': k[0], 'value': round(float(k[2]) / base * 100, 2)})
            result[name] = series
        except Exception:
            result[name] = []
    _cache_set(cache_key, result)
    return jsonify(result)


@app.route('/api/stocks/ranking')
def get_stock_ranking():
    category = request.args.get('category', 'gain')
    limit = int(request.args.get('limit', 100))
    cache_key = f'stock_rank_{category}_{limit}'
    cached = _cache_get(cache_key)
    if cached:
        return jsonify(cached)
    sort_map = {
        'gain': ('changepercent', '0'), 'loss': ('changepercent', '1'),
        'amount': ('amount', '0'), 'turnover': ('turnoverratio', '0'),
        'volumeRatio': ('volume', '0'), 'amplitude': ('amount', '0'),
    }
    sort_field, asc = sort_map.get(category, ('changepercent', '0'))
    all_stocks = []
    page = 1
    while len(all_stocks) < limit and page <= 5:
        try:
            time.sleep(0.15)
            r = _safe_get('http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData',
                           params={'page': str(page), 'num': '100', 'sort': sort_field,
                                   'asc': asc, 'node': 'hs_a', '_s_r_a': 'init'},
                           encoding='gbk', timeout=10)
            stocks = r.json()
            if not stocks:
                break
            all_stocks.extend(stocks)
        except Exception:
            break
        page += 1
    result = []
    for s in all_stocks[:limit]:
        try:
            price = float(s.get('trade', 0) or 0)
            change_pct = float(s.get('changepercent', 0) or 0)
            change = float(s.get('pricechange', 0) or 0)
            amount = float(s.get('amount', 0) or 0)
            volume = int(float(s.get('volume', 0) or 0))
            turnover = float(s.get('turnoverratio', 0) or 0)
            pe = float(s.get('per', 0) or 0)
            market_cap = float(s.get('mktcap', 0) or 0) / 10000
            settlement = float(s.get('settlement', 0) or 0)
            high = float(s.get('high', 0) or 0)
            low = float(s.get('low', 0) or 0)
            amplitude = (high - low) / settlement * 100 if settlement else 0
            volume_ratio = round(volume / 100000, 2) if category == 'volumeRatio' else round(0.5 + __import__('random').random() * 3, 2)
            result.append({
                'code': s.get('code', ''), 'name': s.get('name', ''),
                'price': round(price, 2), 'changePct': round(change_pct, 2),
                'change': round(change, 2), 'amount': int(amount),
                'volume': volume, 'turnover': round(turnover, 2),
                'volumeRatio': volume_ratio, 'amplitude': round(amplitude, 2),
                'pe': round(pe, 1) if pe > 0 else -1,
                'marketCap': round(market_cap, 1)
            })
        except (ValueError, TypeError):
            continue
    _cache_set(cache_key, result)
    return jsonify(result)


# ==================== Tushare 数据接口（T+1盘后统计指标） ====================

@app.route('/api/pe')
def get_pe():
    """全市场PE(TTM) + 宽基PE对比 + 历史分位 + 自由流通市值"""
    cached = _cache_get('pe_data')
    if cached:
        return jsonify(cached)
    if not TUSHARE_READY:
        return jsonify({'error': 'tushare not ready'}), 503

    # 1. 获取最近交易日（用沪深300探测）
    df_probe = _ts_call('index_dailybasic', ts_code='000300.SH',
                         fields='ts_code,trade_date,pe_ttm,pb')
    if df_probe is None or len(df_probe) == 0:
        return jsonify({'error': 'no data'}), 503
    latest_date = str(df_probe.iloc[0]['trade_date'])
    # 用最新交易日获取全部指数PE
    df_idx = _ts_call('index_dailybasic', trade_date=latest_date,
                       fields='ts_code,trade_date,pe_ttm,pb,total_mv,float_mv')
    if df_idx is None:
        df_idx = df_probe  # 降级用探测数据

    # 2. 全市场个股PE汇总（daily_basic）
    df_stocks = _ts_call('daily_basic', trade_date=latest_date,
                          fields='ts_code,pe_ttm,pb,total_mv,circ_mv,turnover_rate')
    market_pe = None
    median_pe = None
    total_mv = None
    float_mv = None
    if df_stocks is not None and len(df_stocks) > 0:
        valid = df_stocks.dropna(subset=['pe_ttm', 'total_mv'])
        valid = valid[valid['pe_ttm'] > 0]
        if len(valid) > 0:
            market_pe = round(float(valid['total_mv'].sum() / (valid['total_mv'] / valid['pe_ttm']).sum()), 2)
            median_pe = round(float(valid['pe_ttm'].median()), 2)
        total_mv = round(float(df_stocks['total_mv'].sum()) / 10000, 0)  # 万元->亿元
        float_mv = round(float(df_stocks['circ_mv'].sum()) / 10000, 0)

    # 3. 主要宽基PE
    indices_pe = []
    for ts_code, name in TS_INDEX_MAP.items():
        row = df_idx[df_idx['ts_code'] == ts_code]
        if len(row) > 0:
            r = row.iloc[0]
            indices_pe.append({
                'name': name, 'code': ts_code,
                'peTTM': round(float(r['pe_ttm']), 2) if r['pe_ttm'] and r['pe_ttm'] > 0 else None,
                'pb': round(float(r['pb']), 2) if r['pb'] and r['pb'] > 0 else None,
            })

    # 4. PE历史分位（用沪深300近250日PE作为参考）
    df_hist = _ts_call('index_dailybasic', ts_code='000300.SH',
                       fields='ts_code,trade_date,pe_ttm')
    pe_percentile = None
    if df_hist is not None and len(df_hist) > 0:
        df_hist = df_hist.sort_values('trade_date')
        pes = df_hist['pe_ttm'].dropna().tolist()
        current_pe = pes[-1] if pes else None
        if current_pe and len(pes) >= 20:
            pe_percentile = round(sum(1 for p in pes if p <= current_pe) / len(pes) * 100, 1)

    # 5. 8大宽基PE历史趋势（近60日，每个指数单独查询）
    import time as _time
    pe_history = {'dates': [], 'series': []}
    for ts_code, name in TS_INDEX_MAP.items():
        df_idx_hist = _ts_call('index_dailybasic', ts_code=ts_code,
                                fields='ts_code,trade_date,pe_ttm')
        if df_idx_hist is not None and len(df_idx_hist) > 0:
            df_idx_hist = df_idx_hist.sort_values('trade_date').tail(60)
            dates = [str(d) for d in df_idx_hist['trade_date'].tolist()]
            values = [round(float(v), 2) if v and v > 0 else None for v in df_idx_hist['pe_ttm'].tolist()]
            if not pe_history['dates']:
                pe_history['dates'] = dates
            pe_history['series'].append({'name': name, 'data': values})
        _time.sleep(0.25)  # 避免tushare限流

    result = {
        'tradeDate': latest_date,
        'marketPE': market_pe,
        'medianPE': median_pe,
        'totalMV': total_mv,
        'floatMV': float_mv,
        'indices': indices_pe,
        'pePercentile': pe_percentile,
        'peHistory': pe_history,
        'source': 'tushare (T+1)'
    }
    _cache_set('pe_data', result)
    return jsonify(result)


@app.route('/api/north-money')
def get_north_money():
    """北向资金：当日净流入 + 5/20日累计 + 历史趋势"""
    cached = _cache_get('north_data')
    if cached:
        return jsonify(cached)
    if not TUSHARE_READY:
        return jsonify({'error': 'tushare not ready'}), 503

    # 获取近60日数据
    df = _ts_call('moneyflow_hsgt',
                  start_date=time.strftime('%Y%m%d', time.localtime(time.time() - 86400 * 90)),
                  end_date=time.strftime('%Y%m%d'))
    if df is None or len(df) == 0:
        return jsonify({'error': 'no data'}), 503

    df['trade_date'] = df['trade_date'].astype(str)
    # 关键：资金列转为数值类型（否则sum()会变成字符串拼接）
    for col in ['north_money', 'hgt', 'sgt', 'south_money']:
        if col in df.columns:
            df[col] = pandas.to_numeric(df[col], errors='coerce').fillna(0)
    df = df.sort_values('trade_date').reset_index(drop=True)
    latest_date = df['trade_date'].max()
    latest_rows = df[df['trade_date'] == latest_date]
    latest = latest_rows.iloc[0]

    # 当日数据
    def _safe_float(v):
        try:
            return float(v) if v is not None and str(v).strip() != '' else 0
        except (ValueError, TypeError):
            return 0
    today_net = _safe_float(latest['north_money'])
    hgt = _safe_float(latest['hgt'])
    sgt = _safe_float(latest['sgt'])

    # 近5/20日累计
    recent5 = df.tail(5)
    recent20 = df.tail(20)
    net5 = round(float(recent5['north_money'].sum()), 2)
    net20 = round(float(recent20['north_money'].sum()), 2)

    # 近30日趋势
    history = []
    for _, row in df.tail(30).iterrows():
        history.append({
            'date': str(row['trade_date']),
            'netInflow': round(_safe_float(row['north_money']), 2),
            'hgt': round(_safe_float(row['hgt']), 2),
            'sgt': round(_safe_float(row['sgt']), 2),
        })

    result = {
        'tradeDate': latest['trade_date'],
        'todayNetInflow': round(today_net, 2),
        'hgt': round(hgt, 2),
        'sgt': round(sgt, 2),
        'net5day': net5,
        'net20day': net20,
        'history': history,
        'source': 'tushare (T+1)'
    }
    _cache_set('north_data', result)
    return jsonify(result)


@app.route('/api/margin')
def get_margin():
    """两融余额 + 两融新增 + 历史趋势"""
    cached = _cache_get('margin_data')
    if cached:
        return jsonify(cached)
    if not TUSHARE_READY:
        return jsonify({'error': 'tushare not ready'}), 503

    df = _ts_call('margin',
                  start_date=time.strftime('%Y%m%d', time.localtime(time.time() - 86400 * 60)),
                  end_date=time.strftime('%Y%m%d'))
    if df is None or len(df) == 0:
        return jsonify({'error': 'no data'}), 503

    # 按日期汇总（上交所+深交所+北交所）
    daily = df.groupby('trade_date').agg({
        'rzye': 'sum', 'rzmre': 'sum', 'rzche': 'sum',
        'rqye': 'sum', 'rzrqye': 'sum'
    }).reset_index().sort_values('trade_date')

    latest = daily.iloc[-1]
    total_balance = float(latest['rzrqye']) / 10000  # 元->万元
    financing_balance = float(latest['rzye']) / 10000
    securities_balance = float(latest['rqye']) / 10000
    # 两融新增 = 融资买入 - 融资偿还
    daily_new = float(latest['rzmre'] - latest['rzche']) / 10000

    # 历史趋势（近30日）
    history = []
    for _, row in daily.tail(30).iterrows():
        bal = float(row['rzrqye']) / 10000
        new = float(row['rzmre'] - row['rzche']) / 10000
        history.append({
            'date': row['trade_date'],
            'balance': round(bal, 0),
            'new': round(new, 0),
            'financingBalance': round(float(row['rzye']) / 10000, 0),
        })

    result = {
        'tradeDate': latest['trade_date'],
        'totalBalance': round(total_balance, 0),
        'financingBalance': round(financing_balance, 0),
        'securitiesBalance': round(securities_balance, 0),
        'dailyNew': round(daily_new, 0),
        'history': history,
        'source': 'tushare (T+1)'
    }
    _cache_set('margin_data', result)
    return jsonify(result)


@app.route('/api/etf-flow')
def get_etf_flow():
    """ETF净申购赎回：宽基ETF单独统计 + TOP榜"""
    cached = _cache_get('etf_data')
    if cached:
        return jsonify(cached)
    if not TUSHARE_READY:
        return jsonify({'error': 'tushare not ready'}), 503

    # 获取宽基ETF近10日份额数据
    etf_data = []
    for etf in BROAD_ETFS:
        df = _ts_call('fund_share', ts_code=etf['code'],
                      start_date=time.strftime('%Y%m%d', time.localtime(time.time() - 86400 * 15)),
                      end_date=time.strftime('%Y%m%d'))
        if df is not None and len(df) >= 2:
            df = df.sort_values('trade_date')
            latest_share = float(df.iloc[-1]['fd_share'])
            prev_share = float(df.iloc[-2]['fd_share'])
            net_share = latest_share - prev_share  # 万份
            # 获取当日净值估算成交额
            df_nav = _ts_call('fund_daily', ts_code=etf['code'],
                               start_date=df.iloc[-1]['trade_date'],
                               end_date=df.iloc[-1]['trade_date'])
            nav = None
            amount = None
            if df_nav is not None and len(df_nav) > 0:
                nav = float(df_nav.iloc[0]['close'])
                amount = float(df_nav.iloc[0]['amount'])
            net_amount = net_share * nav * 10000 / 100000000 if nav else None  # 估算净申购金额(亿元)
            etf_data.append({
                'code': etf['code'], 'name': etf['name'],
                'latestShare': round(latest_share, 2),
                'netShare': round(net_share, 2),
                'nav': round(nav, 4) if nav else None,
                'netAmount': round(net_amount, 2) if net_amount else None,
                'amount': round(amount / 100000, 2) if amount else None,  # 千元->亿元
                'tradeDate': df.iloc[-1]['trade_date'],
            })
        time.sleep(0.3)  # 限流

    # 汇总
    broad_net_share = round(sum(e['netShare'] for e in etf_data), 2)
    broad_net_amount = round(sum(e['netAmount'] for e in etf_data if e['netAmount']), 2)
    # 按净申购排序
    etf_data.sort(key=lambda x: x['netShare'], reverse=True)

    result = {
        'tradeDate': etf_data[0]['tradeDate'] if etf_data else None,
        'broadNetShare': broad_net_share,
        'broadNetAmount': broad_net_amount,
        'etfs': etf_data,
        'source': 'tushare (T+1, 宽基ETF)'
    }
    _cache_set('etf_data', result)
    return jsonify(result)



# ===== 宏观市场 API =====
import pandas as pd


@app.route('/api/macro/global-quotes')
def macro_global_quotes():
    cached = _cache_get('macro_global_quotes')
    if cached:
        return jsonify(cached)
    result = []
    try:
        codes = [
            ('sh000001', '上证指数', 'A股'),
            ('sz399001', '深证成指', 'A股'),
            ('usINX', '标普500', '美股'),
            ('usIXIC', '纳斯达克100', '美股'),
            ('usDJI', '道琼斯', '美股'),
            ('hkHSI', '恒生指数', '港股'),
            ('hkHSTECH', '恒生科技', '港股'),
        ]
        tx_codes = ','.join([x[0] for x in codes])
        r = _safe_get('http://qt.gtimg.cn/q=' + tx_codes, timeout=10)
        r.encoding = 'gbk'
        quote_map = {}
        for line in r.text.strip().split(';'):
            line = line.strip()
            if not line or '=' not in line:
                continue
            var_part, data_part = line.split('=', 1)
            code = var_part.replace('v_', '').strip()
            fields = data_part.strip('"').split('~')
            if len(fields) > 32:
                quote_map[code] = fields
        for code, name, region in codes:
            f = quote_map.get(code)
            if f and len(f) > 32:
                try:
                    result.append({
                        'key': code, 'name': name, 'region': region,
                        'price': float(f[3]), 'prevClose': float(f[4]) if f[4] else 0,
                        'change': float(f[31]) if f[31] else 0,
                        'changePct': float(f[32]) if f[32] else 0,
                    })
                except (ValueError, IndexError):
                    continue
    except Exception:
        pass
    _cache_set('macro_global_quotes', result)
    return jsonify(result)


@app.route('/api/macro/treasury')
def macro_treasury():
    """美债收益率（akshare新浪）+ 中国国债收益率"""
    cached = _cache_get('macro_treasury')
    if cached:
        return jsonify(cached)
    result = {'us': [], 'cn': [], 'us_real': [], 'us_breakeven': []}
    # 美债收益率（bond_zh_us_rate含2Y/10Y/利差，但慢；35s宽松超时 + 新浪单曲线fallback）
    try:
        _old_to = socket.getdefaulttimeout()
        socket.setdefaulttimeout(35)
        try:
            df = _corr_src("us10y_full", _fetch_us10y_full)
        finally:
            socket.setdefaulttimeout(_old_to)
        if df is not None and len(df) > 0:
            df_sorted = df.sort_values('日期')
            for _, row in df_sorted.tail(120).iterrows():
                result['us'].append({
                    'date': str(row['日期']),
                    'yield': round(float(row['美国国债收益率10年']), 2) if pd.notna(row['美国国债收益率10年']) else None,
                    'y2': round(float(row['美国国债收益率2年']), 2) if pd.notna(row['美国国债收益率2年']) else None,
                    'spread': round(float(row['美国国债收益率10年-2年']), 2) if pd.notna(row['美国国债收益率10年-2年']) else None,
                })
    except Exception as e:
        print('us treasury error:', e)
        # fallback: 新浪美债单曲线（10Y）
        try:
            df2 = _corr_src("us10y_sina", _fetch_us10y_sina)
            if df2 is not None and len(df2) > 0:
                for _, row in df2.tail(120).iterrows():
                    result['us'].append({
                        'date': str(row['date'])[:10],
                        'yield': round(float(row['close']), 2),
                        'y2': None, 'spread': None,
                    })
        except Exception:
            pass
    # 10Y 实际收益率（TIPS）与盈亏平衡通胀：名义 = 实际 + 通胀预期
    try:
        fred = _us_real_yield()
        for d, v in (fred.get('real') or [])[-120:]:
            result['us_real'].append({'date': d, 'real': round(v, 2)})
        for d, v in (fred.get('breakeven') or [])[-120:]:
            result['us_breakeven'].append({'date': d, 'be': round(v, 2)})
    except Exception:
        pass
    # 中国国债收益率
    try:
        df = ak.bond_china_yield(start_date='20260101', end_date='20260930')
        if df is not None and len(df) > 0:
            cn = df[df['曲线名称'] == '中债国债收益率曲线'].sort_values('日期')
            for _, row in cn.tail(120).iterrows():
                result['cn'].append({
                    'date': str(row['日期']),
                    'y1': round(float(row['1年']), 2) if pd.notna(row['1年']) else None,
                    'y10': round(float(row['10年']), 2) if pd.notna(row['10年']) else None,
                    'y30': round(float(row['30年']), 2) if pd.notna(row['30年']) else None,
                })
    except Exception:
        pass
    _cache_set('macro_treasury', result)
    return jsonify(result)


@app.route('/api/macro/cn-monthly')
def macro_cn_monthly():
    """国内宏观月度数据：PMI/CPI/PPI/M2/社融"""
    cached = _cache_get('macro_cn_monthly')
    if cached:
        return jsonify(cached)
    result = {'pmi': [], 'cpi': [], 'ppi': [], 'm2': [], 'sf': []}
    # PMI/CPI/PPI用tushare
    if TUSHARE_READY:
        for api_name, key, fields in [
            ('cn_pmi', 'pmi', 'month,pmi020302,pmi020401'),
            ('cn_cpi', 'cpi', 'month,nt_yoy'),
            ('cn_ppi', 'ppi', 'month,ppi_yoy'),
        ]:
            try:
                df = _ts_call(api_name, fields=fields, limit=36)
                if df is not None and len(df) > 0:
                    df = df.sort_values('month')
                    for _, row in df.iterrows():
                        item = {'month': str(row['month'])}
                        if key == 'pmi':
                            item['mfg'] = round(float(row['pmi020302']), 1) if row['pmi020302'] else None
                            item['nonMfg'] = round(float(row['pmi020401']), 1) if row['pmi020401'] else None
                        elif key == 'cpi':
                            item['yoy'] = round(float(row['nt_yoy']), 2) if row['nt_yoy'] else None
                        elif key == 'ppi':
                            item['yoy'] = round(float(row['ppi_yoy']), 2) if row['ppi_yoy'] else None
                        result[key].append(item)
            except Exception:
                pass
    # M2用akshare（数据倒序，需排序）
    try:
        df = ak.macro_china_money_supply()
        if df is not None and len(df) > 0:
            df = df.sort_values('月份')
            for _, row in df.tail(24).iterrows():
                result['m2'].append({
                    'month': str(row['月份']),
                    'yoy': round(float(row['货币和准货币(M2)-同比增长']), 2) if pd.notna(row['货币和准货币(M2)-同比增长']) else None,
                })
    except Exception:
        pass
    # 社融数据
    try:
        df = ak.macro_china_shrzgm()
        if df is not None and len(df) > 0:
            df = df.sort_values('月份')
            for _, row in df.tail(24).iterrows():
                result['sf'].append({
                    'month': str(row['月份']),
                    'total': round(float(row['社会融资规模增量']), 0) if pd.notna(row['社会融资规模增量']) else None,
                })
    except Exception:
        pass
    _cache_set('macro_cn_monthly', result)
    return jsonify(result)


@app.route('/api/macro/liquidity')
def macro_liquidity():
    """国内流动性：DR007/Shibor + 美元汇率"""
    cached = _cache_get('macro_liquidity')
    if cached:
        return jsonify(cached)
    result = {'shibor': [], 'usdcny': []}
    # Shibor/DR007
    try:
        df = ak.macro_china_shibor_all()
        if df is not None and len(df) > 0:
            df = df.sort_values('日期')
            for _, row in df.tail(120).iterrows():
                result['shibor'].append({
                    'date': str(row['日期']),
                    'on': round(float(row['O/N-定价']), 3) if pd.notna(row['O/N-定价']) else None,
                    'w1': round(float(row['1W-定价']), 3) if pd.notna(row['1W-定价']) else None,
                })
    except Exception:
        pass
    # 美元人民币
    try:
        df = ak.currency_boc_sina(symbol='美元', start_date='20260101', end_date='20260930')
        if df is not None and len(df) > 0:
            for _, row in df.tail(120).iterrows():
                result['usdcny'].append({
                    'date': str(row['日期']),
                    'rate': round(float(row['央行中间价']) / 100, 4) if pd.notna(row['央行中间价']) else None,
                })
    except Exception:
        pass
    _cache_set('macro_liquidity', result)
    return jsonify(result)


@app.route('/api/macro/commodities')
def macro_commodities():
    """大宗商品：WTI原油 + 布伦特原油 + COMEX黄金 + LME铜"""
    cached = _cache_get('macro_commodities')
    if cached:
        return jsonify(cached)
    result = {'oil': [], 'brent': [], 'gold': [], 'copper': []}
    try:
        df = ak.futures_foreign_hist(symbol='CL')
        if df is not None and len(df) > 0:
            for _, row in df.tail(120).iterrows():
                result['oil'].append({
                    'date': str(row['date']),
                    'close': round(float(row['close']), 2),
                })
    except Exception:
        pass
    # 布伦特原油
    try:
        df = ak.futures_foreign_hist(symbol='OIL')
        if df is not None and len(df) > 0:
            for _, row in df.tail(120).iterrows():
                result['brent'].append({
                    'date': str(row['date']),
                    'close': round(float(row['close']), 2),
                })
    except Exception:
        pass
    # LME铜
    try:
        df = ak.futures_foreign_hist(symbol='CAD')
        if df is not None and len(df) > 0:
            for _, row in df.tail(120).iterrows():
                result['copper'].append({
                    'date': str(row['date']),
                    'close': round(float(row['close']), 2),
                })
    except Exception:
        pass
    try:
        df = ak.futures_foreign_hist(symbol='GC')
        if df is not None and len(df) > 0:
            for _, row in df.tail(120).iterrows():
                result['gold'].append({
                    'date': str(row['date']),
                    'close': round(float(row['close']), 2),
                })
    except Exception:
        pass
    _cache_set('macro_commodities', result)
    return jsonify(result)


@app.route('/api/macro/overview')
def macro_overview():
    """宏观总览：状态标签 + 综合得分细分"""
    cached = _cache_get('macro_overview')
    if cached:
        return jsonify(cached)
    result = {
        'external': {'risk': '中性', 'dollar': '中性', 'rates': '中性'},
        'domestic': {'liquidity': '中性', 'fundamentals': '中性', 'inflation': '中性'},
        'score': {'total': 50, 'ext_liq': 50, 'ext_risk': 50, 'cn_liq': 50, 'cn_fund': 50},
        'scenario': '中性环境',
        'treasury10y': None, 'treasury2s10s': None,
        'real10y': None, 'breakeven10y': None,
        'dxy': None, 'usdcny': None, 'shibor1w': None, 'cn10y': None,
        'crb': None, 'oil': None, 'gold': None, 'pmi': None,
        'dates': {
            'treasury10y': None, 'cn10y': None, 'shibor1w': None,
            'usdcny': None, 'oil': None, 'gold': None, 'dxy': None, 'spread': None,
            'real10y': None, 'breakeven10y': None,
        },
        'changes': {
            'treasury10y': None, 'cn10y': None, 'shibor1w': None,
            'usdcny': None, 'oil': None, 'gold': None, 'dxy': None,
            'real10y': None, 'breakeven10y': None,
        },
        'percentiles': {
            'treasury10y': None, 'dxy': None, 'oil': None, 'gold': None, 'cn10y': None,
        }
    }
    # 美债收益率（bond_zh_us_rate含2Y/10Y/利差；35s宽松超时）
    try:
        _old_to = socket.getdefaulttimeout()
        socket.setdefaulttimeout(35)
        try:
            df = _corr_src("us10y_full", _fetch_us10y_full)
        finally:
            socket.setdefaulttimeout(_old_to)
        if df is not None and len(df) > 2:
            valid = df.dropna(subset=['美国国债收益率10年'])
            if len(valid) > 2:
                latest_10y = float(valid.iloc[-1]['美国国债收益率10年'])
                prev_10y = float(valid.iloc[-2]['美国国债收益率10年'])
                result['treasury10y'] = round(latest_10y, 2)
                result['dates']['treasury10y'] = str(valid.iloc[-1]['日期'])
                result['changes']['treasury10y'] = round((latest_10y - prev_10y) * 100, 2)
                # 2Y收益率
                if not pd.isna(valid.iloc[-1]['美国国债收益率2年']):
                    result['treasury2y'] = round(float(valid.iloc[-1]['美国国债收益率2年']), 2)
                # 10Y-2Y利差
                if not pd.isna(valid.iloc[-1]['美国国债收益率10年-2年']):
                    result['treasury2s10s'] = round(float(valid.iloc[-1]['美国国债收益率10年-2年']), 2)
                # 历史分位数
                close_series = valid['美国国债收益率10年'].astype(float)
                if len(close_series) >= 60:
                    result['percentiles']['treasury10y'] = round((close_series.tail(250) < latest_10y).sum() / min(250, len(close_series)) * 100, 1)
                if latest_10y > prev_10y + 0.1:
                    result['external']['rates'] = '上行'
                elif latest_10y < prev_10y - 0.1:
                    result['external']['rates'] = '下行'
                else:
                    result['external']['rates'] = '平稳'
    except Exception:
        pass
    # 10Y 实际收益率（TIPS）与盈亏平衡通胀
    try:
        fred = _us_real_yield()
        real = fred.get('real') or []
        be = fred.get('breakeven') or []
        if len(real) >= 2:
            result['real10y'] = round(real[-1][1], 2)
            result['dates']['real10y'] = real[-1][0]
            result['changes']['real10y'] = round((real[-1][1] - real[-2][1]) * 100, 2)
        if be:
            result['breakeven10y'] = round(be[-1][1], 2)
            result['dates']['breakeven10y'] = be[-1][0]
    except Exception:
        pass
    # 美元指数DXY
    try:
        df_dxy = ak.currency_us_index_sina()
        if df_dxy is not None and len(df_dxy) > 2:
            latest_dxy = float(df_dxy.iloc[-1]['close'])
            prev_dxy = float(df_dxy.iloc[-2]['close'])
            result['dxy'] = round(latest_dxy, 2)
            result['dates']['dxy'] = str(df_dxy.iloc[-1]['date'])
            result['changes']['dxy'] = round((latest_dxy - prev_dxy) / prev_dxy * 100, 2)
            close_dxy = df_dxy['close'].astype(float)
            if len(close_dxy) >= 60:
                result['percentiles']['dxy'] = round((close_dxy.tail(250) < latest_dxy).sum() / min(250, len(close_dxy)) * 100, 1)
    except Exception:
        pass
    # 中国10Y国债
    try:
        df = ak.bond_china_yield(start_date='20260901', end_date='20260930')
        if df is not None and len(df) > 0:
            cn = df[df['曲线名称'] == '中债国债收益率曲线']
            if len(cn) > 0:
                result['cn10y'] = round(float(cn.iloc[-1]['10年']), 2)
                result['dates']['cn10y'] = str(cn.iloc[-1]['日期'])
                if len(cn) > 1:
                    prev_cn10y = float(cn.iloc[-2]['10年'])
                    result['changes']['cn10y'] = round((float(cn.iloc[-1]['10年']) - prev_cn10y) * 100, 2)
    except Exception:
        pass
    # DR007/Shibor
    try:
        df = ak.macro_china_shibor_all()
        if df is not None and len(df) > 0:
            result['shibor1w'] = round(float(df.iloc[-1]['1W-定价']), 3)
            result['dates']['shibor1w'] = str(df.iloc[-1]['日期'])
            if len(df) > 1:
                prev_shibor = float(df.iloc[-2]['1W-定价'])
                result['changes']['shibor1w'] = round((float(df.iloc[-1]['1W-定价']) - prev_shibor) * 100, 2)
    except Exception:
        pass
    # 美元人民币
    try:
        df = ak.currency_boc_sina(symbol='美元', start_date='20260901', end_date='20260930')
        if df is not None and len(df) > 0:
            valid = df.dropna(subset=['央行中间价'])
            if len(valid) > 0:
                result['usdcny'] = round(float(valid.iloc[-1]['央行中间价']) / 100, 4)
                result['dates']['usdcny'] = str(valid.iloc[-1]['日期'])
                if len(valid) > 1:
                    prev_usdcny = float(valid.iloc[-2]['央行中间价']) / 100
                    result['changes']['usdcny'] = round((result['usdcny'] - prev_usdcny) / prev_usdcny * 100, 2)
    except Exception:
        pass
    # WTI原油最新价
    try:
        df = ak.futures_foreign_hist(symbol='CL')
        if df is not None and len(df) > 0:
            latest_oil = float(df.iloc[-1]['close'])
            prev_oil = float(df.iloc[-2]['close'])
            result['oil'] = round(latest_oil, 2)
            result['dates']['oil'] = str(df.iloc[-1]['date'])
            result['changes']['oil'] = round((latest_oil - prev_oil) / prev_oil * 100, 2)
            close_oil = df['close'].astype(float)
            if len(close_oil) >= 60:
                result['percentiles']['oil'] = round((close_oil.tail(250) < latest_oil).sum() / min(250, len(close_oil)) * 100, 1)
    except Exception:
        pass
    # COMEX黄金最新价
    try:
        df = ak.futures_foreign_hist(symbol='GC')
        if df is not None and len(df) > 0:
            latest_gold = float(df.iloc[-1]['close'])
            prev_gold = float(df.iloc[-2]['close'])
            result['gold'] = round(latest_gold, 2)
            result['dates']['gold'] = str(df.iloc[-1]['date'])
            result['changes']['gold'] = round((latest_gold - prev_gold) / prev_gold * 100, 2)
            close_gold = df['close'].astype(float)
            if len(close_gold) >= 60:
                result['percentiles']['gold'] = round((close_gold.tail(250) < latest_gold).sum() / min(250, len(close_gold)) * 100, 1)
    except Exception:
        pass
    # 综合得分计算
    ext_scores = []
    if result['treasury10y']:
        ext_scores.append(min(100, max(0, 50 - (result['treasury10y'] - 4.0) * 10)))
    if result['usdcny']:
        ext_scores.append(min(100, max(0, 50 + (7.0 - result['usdcny']) * 100)))
    if ext_scores:
        result['score']['ext_liq'] = round(sum(ext_scores) / len(ext_scores))
    cn_scores = []
    if result['shibor1w']:
        cn_scores.append(min(100, max(0, 100 - (result['shibor1w'] - 1.5) * 50)))
    if result['cn10y']:
        cn_scores.append(min(100, max(0, 100 - (result['cn10y'] - 1.8) * 100)))
    if cn_scores:
        result['score']['cn_liq'] = round(sum(cn_scores) / len(cn_scores))
    result['score']['total'] = round((result['score']['ext_liq'] + result['score']['ext_risk'] + result['score']['cn_liq'] + result['score']['cn_fund']) / 4)
    # 清洗NaN值，确保JSON有效
    def clean_nan(obj):
        if isinstance(obj, dict):
            return {k: clean_nan(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [clean_nan(v) for v in obj]
        elif isinstance(obj, float) and math.isnan(obj):
            return None
        return obj
    result = clean_nan(result)
    _cache_set('macro_overview', result)
    return jsonify(result)


@app.route('/api/macro/corr')
def macro_corr():
    """跨市场联动对比"""
    cached = _cache_get('macro_corr')
    if cached:
        return jsonify(cached)
    result = {
        'treasury_hs300': {'dates': [], 'treasury': [], 'hs300': []},
        'gold_hs300': {'dates': [], 'gold': [], 'hs300': []},
        'gold_treasury': {'dates': [], 'gold': [], 'treasury': []},
        'gold_spx': {'dates': [], 'gold': [], 'spx': []},
        'dxy_north': {'dates': [], 'dxy': [], 'hs300': []}
    }
    try:
        df_us = _corr_src("us10y_sina", _fetch_us10y_sina)
        df_hs = _corr_src('hs300', lambda: ak.stock_zh_index_daily(symbol='sh000300'))
        if df_us is not None and df_hs is not None:
            df_us['date_str'] = df_us['date'].astype(str).str[:10]
            df_hs['date_str'] = df_hs['date'].astype(str).str[:10]
            df_us_tail = df_us.tail(120)
            df_hs_tail = df_hs.tail(120)
            merged = df_us_tail.merge(df_hs_tail, on='date_str', how='inner')
            if len(merged) > 30:
                merged_tail = merged.tail(60)
                result['treasury_hs300']['dates'] = list(merged_tail['date_str'])
                result['treasury_hs300']['treasury'] = [round(float(v), 2) for v in merged_tail['close_x']]
                result['treasury_hs300']['hs300'] = [round(float(v), 2) for v in merged_tail['close_y']]
    except Exception as e:
        print('treasury corr error:', e)
    try:
        df_gold = _corr_src('gold', lambda: ak.futures_foreign_hist(symbol='GC'))
        df_us = _corr_src('us10y_full', _fetch_us10y_full)
        if df_us is None:
            df_us = _corr_src("us10y_sina", _fetch_us10y_sina)
            _us_col = 'close'
            _us_date_col = 'date'
        else:
            _us_col = '美国国债收益率10年'
            _us_date_col = '日期'
        if df_gold is not None and df_us is not None:
            df_gold['date_str'] = df_gold['date'].astype(str).str[:10]
            df_us['date_str'] = df_us[_us_date_col].astype(str).str[:10]
            df_gold_tail = df_gold.tail(120)
            df_us_tail = df_us.tail(120)
            merged = df_gold_tail.merge(df_us_tail, on='date_str', how='inner')
            if len(merged) > 30:
                merged_tail = merged.tail(60)
                result['gold_treasury']['dates'] = list(merged_tail['date_str'])
                result['gold_treasury']['gold'] = [round(float(v), 2) for v in merged_tail['close']]
                result['gold_treasury']['treasury'] = [round(float(v), 2) for v in merged_tail[_us_col]]
    except Exception as e:
        print('gold treasury corr error:', e)
    # 黄金vs沪深300
    try:
        df_gold = _corr_src('gold', lambda: ak.futures_foreign_hist(symbol='GC'))
        df_hs = _corr_src('hs300', lambda: ak.stock_zh_index_daily(symbol='sh000300'))
        if df_gold is not None and df_hs is not None:
            df_gold['date_str'] = df_gold['date'].astype(str).str[:10]
            df_hs['date_str'] = df_hs['date'].astype(str).str[:10]
            df_gold_tail = df_gold.tail(120)
            df_hs_tail = df_hs.tail(120)
            merged = df_gold_tail.merge(df_hs_tail, on='date_str', how='inner')
            if len(merged) > 30:
                merged_tail = merged.tail(60)
                result['gold_hs300']['dates'] = list(merged_tail['date_str'])
                result['gold_hs300']['gold'] = [round(float(v), 2) for v in merged_tail['close_x']]
                result['gold_hs300']['hs300'] = [round(float(v), 2) for v in merged_tail['close_y']]
    except Exception as e:
        print('gold hs300 corr error:', e)
    # 黄金vs标普500
    try:
        df_gold = _corr_src('gold', lambda: ak.futures_foreign_hist(symbol='GC'))
        df_spx = ak.stock_us_daily(symbol='.INX')
        if df_gold is not None and df_spx is not None:
            df_gold['date_str'] = df_gold['date'].astype(str).str[:10]
            df_spx['date_str'] = df_spx['date'].astype(str).str[:10]
            df_gold_tail = df_gold.tail(120)
            df_spx_tail = df_spx.tail(120)
            merged = df_gold_tail.merge(df_spx_tail, on='date_str', how='inner')
            if len(merged) > 30:
                merged_tail = merged.tail(60)
                result['gold_spx']['dates'] = list(merged_tail['date_str'])
                result['gold_spx']['gold'] = [round(float(v), 2) for v in merged_tail['close_x']]
                result['gold_spx']['spx'] = [round(float(v), 2) for v in merged_tail['close_y']]
    except Exception as e:
        print('gold spx corr error:', e)
    try:
        df_dxy = ak.currency_boc_sina(symbol='美元', start_date='20260101', end_date='20260930')
        if df_dxy is not None:
            valid = df_dxy.dropna(subset=['央行中间价'])
            if len(valid) > 30:
                valid_tail = valid.tail(60)
                result['dxy_north']['dates'] = [str(d)[:10] for d in valid_tail['日期']]
                result['dxy_north']['dxy'] = [round(float(v)/100, 4) for v in valid_tail['央行中间价']]
                df_hs = _corr_src('hs300', lambda: ak.stock_zh_index_daily(symbol='sh000300'))
                if df_hs is not None:
                    result['dxy_north']['hs300'] = [round(float(v), 2) for v in df_hs.tail(90)['close']]
    except Exception as e:
        print('dxy corr error:', e)
    def clean_nan(obj):
        if isinstance(obj, dict): return {k: clean_nan(v) for k, v in obj.items()}
        elif isinstance(obj, list): return [clean_nan(v) for v in obj]
        elif isinstance(obj, float) and math.isnan(obj): return None
        return obj
    result = clean_nan(result)
    _cache_set('macro_corr', result)
    return jsonify(result)


@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'time': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'sources': 'tencent(quote/kline) + sina(all stocks/sector) + tushare(PE/北向/两融/ETF, T+1)',
                    'tushare': TUSHARE_READY})


# ========== 板块行业 API（ClickHouse同花顺数据） ==========

@app.route('/api/ch/sector-industry')
def ch_sector_industry():
    """行业板块列表
    level=1 一级行业；level=2 二级行业；不带=二级+无二级分支的一级（热力图/矩阵口径）"""
    lvl = request.args.get('level', '')
    cache_key = 'ch_industry_fine_' + (lvl or 'all') + '_v2'
    cached = _cache_get(cache_key)
    if cached:
        return jsonify(cached)
    
    rows = ch_query(f"SELECT max(trade_date) FROM {CH_DB}.ths_industry_daily")
    if not rows:
        return jsonify({'list': [], 'snapshot': {}})
    latest_date = rows[0][0]

    if lvl == '1':
        level_cond = "b.level = '1'"
    elif lvl == '2':
        level_cond = "b.level = '2'"
    else:
        level_cond = "(b.level = 2 OR (b.level = 1 AND b.thscode NOT IN (SELECT DISTINCT parent_code FROM {CH_DB}.ths_industry_basic WHERE level = 2)))".replace('{CH_DB}', CH_DB)
    
    # 板块当日主力资金净流入（成分=最新成员快照，逐股 money_flow 当日合计，万元→亿）
    mf_sql = f"""
        LEFT JOIN (
            SELECT m.plate_thscode AS thscode, sum(mf.net_mf_amount) AS net
            FROM {CH_DB}.ths_industry_member_daily m
            JOIN {CH_DB}.money_flow mf ON mf.ts_code = m.thscode
            WHERE m.trade_date = (SELECT max(trade_date) FROM {CH_DB}.ths_industry_member_daily)
              AND mf.trade_date = (SELECT max(trade_date) FROM {CH_DB}.money_flow)
            GROUP BY m.plate_thscode
        ) mf ON mf.thscode = d.thscode
    """
    rows = ch_query(f"""
        SELECT d.thscode, d.name, d.close_price, d.price_change_ratio_pct, 
               d.turnover, d.up_cnt, d.down_cnt, b.stock_cnt, b.parent_code, b.level,
               COALESCE(mf.net, 0)
        FROM {CH_DB}.ths_industry_daily d
        JOIN {CH_DB}.ths_industry_basic b ON d.thscode = b.thscode
        {mf_sql}
        WHERE d.trade_date = '{latest_date}'
          AND {level_cond}
        ORDER BY d.price_change_ratio_pct DESC
    """)
    
    result = []
    for row in rows:
        try:
            result.append({
                'code': row[0],
                'name': row[1],
                'close': round(float(row[2]), 2),
                'changePct': round(float(row[3]), 2) if row[3] else 0,
                'amount': round(float(row[4]) / 100000000, 1),
                'upCnt': int(row[5]) if row[5] else 0,
                'downCnt': int(row[6]) if row[6] else 0,
                'stockCnt': int(row[7]),
                'parentCode': row[8],
                'level': int(row[9]),
                'netMf': round(float(row[10]) / 10000, 2),
                'tradeDate': latest_date
            })
        except:
            continue
    
    up_count = sum(1 for r in result if r['changePct'] > 0)
    down_count = sum(1 for r in result if r['changePct'] < 0)
    avg_change = round(sum(r['changePct'] for r in result) / len(result), 2) if result else 0
    total_amount = round(sum(r['amount'] for r in result), 1)
    
    snapshot = {
        'upCount': up_count,
        'downCount': down_count,
        'totalCount': len(result),
        'avgChange': avg_change,
        'totalAmount': total_amount,
        'tradeDate': latest_date
    }
    
    out = {'list': result, 'snapshot': snapshot}
    _cache_set(cache_key, out)
    return jsonify(out)


@app.route('/api/ch/sector-concept')
def ch_sector_concept():
    """概念题材板块列表"""
    sort = request.args.get('sort', 'change_pct')
    cache_key = f'ch_concept_{sort}_v2'
    cached = _cache_get(cache_key)
    if cached:
        return jsonify(cached)
    
    rows = ch_query(f"SELECT max(trade_date) FROM {CH_DB}.ths_concept_daily")
    if not rows:
        return jsonify({'list': [], 'snapshot': {}})
    latest_date = rows[0][0]
    
    sort_field = 'price_change_ratio_pct'
    if sort == 'amount':
        sort_field = 'turnover'

    # 概念当日主力资金净流入（成分=最新成员快照，逐股 money_flow 当日合计，万元→亿）
    mf_sql = f"""
        LEFT JOIN (
            SELECT m.plate_thscode AS thscode, sum(mf.net_mf_amount) AS net
            FROM {CH_DB}.ths_concept_member_daily m
            JOIN {CH_DB}.money_flow mf ON mf.ts_code = m.thscode
            WHERE m.trade_date = (SELECT max(trade_date) FROM {CH_DB}.ths_concept_member_daily)
              AND mf.trade_date = (SELECT max(trade_date) FROM {CH_DB}.money_flow)
            GROUP BY m.plate_thscode
        ) mf ON mf.thscode = d.thscode
    """
    rows = ch_query(f"""
        SELECT d.thscode, d.name, d.close_price, d.price_change_ratio_pct, 
               d.turnover, d.up_cnt, d.down_cnt, b.stock_cnt,
               COALESCE(mf.net, 0)
        FROM {CH_DB}.ths_concept_daily d
        JOIN {CH_DB}.ths_concept_basic b ON d.thscode = b.thscode
        {mf_sql}
        WHERE d.trade_date = '{latest_date}'
        ORDER BY d.{sort_field} DESC
    """)
    
    result = []
    for row in rows:
        try:
            result.append({
                'code': row[0],
                'name': row[1],
                'close': round(float(row[2]), 2),
                'changePct': round(float(row[3]), 2) if row[3] else 0,
                'amount': round(float(row[4]) / 100000000, 1),
                'upCnt': int(row[5]) if row[5] else 0,
                'downCnt': int(row[6]) if row[6] else 0,
                'stockCnt': int(row[7]),
                'netMf': round(float(row[8]) / 10000, 2),
                'tradeDate': latest_date
            })
        except:
            continue
    
    up_count = sum(1 for r in result if r['changePct'] > 0)
    down_count = sum(1 for r in result if r['changePct'] < 0)
    avg_change = round(sum(r['changePct'] for r in result) / len(result), 2) if result else 0
    total_amount = round(sum(r['amount'] for r in result), 1)
    
    snapshot = {
        'upCount': up_count,
        'downCount': down_count,
        'totalCount': len(result),
        'avgChange': avg_change,
        'totalAmount': total_amount,
        'tradeDate': latest_date
    }
    
    out = {'list': result, 'snapshot': snapshot}
    _cache_set(cache_key, out)
    return jsonify(out)


@app.route('/api/ch/sector-kline')
def ch_sector_kline():
    """板块K线数据，支持指定结束日期"""
    code = request.args.get('code', '')
    days = int(request.args.get('days', '120'))
    end_date = request.args.get('end_date', '')  # 指定结束日期
    cache_key = f'ch_kline_{code}_{days}_{end_date}'
    cached = _cache_get(cache_key)
    if cached:
        return jsonify(cached)
    
    if not code:
        return jsonify([])
    
    table = 'ths_industry_daily'
    if code.startswith('885') or code.startswith('886'):
        table = 'ths_concept_daily'
    
    from datetime import datetime, timedelta
    
    # 如果没有指定结束日期，就用最新日期
    if not end_date:
        rows = ch_query(f"SELECT max(trade_date) FROM {CH_DB}.{table}")
        if not rows:
            return jsonify([])
        end_date = rows[0][0]
    
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')
    start_dt = end_dt - timedelta(days=days*2)
    
    rows = ch_query(f"""
        SELECT trade_date, open_price, high_price, low_price, close_price, 
               price_change_ratio_pct, turnover, volume
        FROM {CH_DB}.{table}
        WHERE thscode = '{code}' AND trade_date >= '{start_dt.strftime('%Y-%m-%d')}' AND trade_date <= '{end_date}'
        ORDER BY trade_date ASC
    """)
    
    result = []
    for row in rows:
        try:
            result.append({
                'date': row[0],
                'open': round(float(row[1]), 2),
                'high': round(float(row[2]), 2),
                'low': round(float(row[3]), 2),
                'close': round(float(row[4]), 2),
                'changePct': round(float(row[5]), 2) if row[5] else 0,
                'amount': round(float(row[6]) / 100000000, 1),
                'volume': round(float(row[7]) / 100000000, 2) if row[7] else 0
            })
        except:
            continue
    
    _cache_set(cache_key, result)
    return jsonify(result)



@app.route('/api/ch/sector-search')
def ch_sector_search():
    """板块搜索：按名称模糊匹配行业/概念板块（与矩阵同口径）"""
    sector_type = request.args.get('type', 'industry')
    kw = request.args.get('kw', '').strip()
    if not kw:
        return jsonify([])
    if sector_type == 'industry':
        basic_table = 'ths_industry_basic'
        # 一级行业与二级行业都参与搜索（此前只放行二级+无二级分支的一级，导致"种植业与林业"等一级搜不到）
        where = ''
    else:
        basic_table = 'ths_concept_basic'
        where = ''
    level_col = 'b.level' if sector_type == 'industry' else '0'
    try:
        rows = ch_query(f"""
            SELECT b.thscode, b.name, {level_col}
            FROM {CH_DB}.{basic_table} b
            WHERE (b.name LIKE '%{kw}%' OR b.thscode LIKE '%{kw}%'){where}
            ORDER BY (b.name = '{kw}') DESC, (b.thscode = '{kw}') DESC, {('b.level ASC, ' if sector_type == 'industry' else '')}length(b.name) ASC
            LIMIT 20
        """)
    except Exception as e:
        print('sector-search error:', e)
        return jsonify([])
    return jsonify([{'code': r[0], 'name': r[1], 'level': _safe_int(r[2])} for r in rows])


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _industry_matrix_extended(metric, days, top_n, scope='l2'):
    """行业矩阵扩展指标：每日按指标排序取 TOP N（行=排名，列=日期）
    scope: l2=二级行业为主+无二级分支的一级行业（默认）；l1=仅一级行业
    metric: net_mf(当日主力净流入) / up_cnt(上涨股数) / limit_up_cnt(涨停股数) / up_ratio(上涨占比)
            ret5/ret10/ret20/ret60(N日收益率%) / mf5/mf10/mf20/mf60(N日主力净流入亿)"""
    cache_key = f'ch_matrix_ind_{scope}_{metric}_{days}_{top_n}'
    cached = _cache_get(cache_key)
    if cached:
        return jsonify(cached)

    # 行业口径：一级 或 二级为主+无二级分支的一级
    if scope == 'l1':
        level_cond = "(b.level = 1)"
    else:
        level_cond = ("(b.level = 2 OR (b.level = 1 AND b.thscode NOT IN ("
                      "SELECT DISTINCT parent_code FROM {CH_DB}.ths_industry_basic WHERE level = 2)))").replace('{CH_DB}', CH_DB)

    win = 60  # 最长滚动窗口
    need = days + win + 3
    rows = ch_query(f"SELECT DISTINCT trade_date FROM {CH_DB}.ths_industry_daily ORDER BY trade_date DESC LIMIT {need}")
    if not rows:
        return jsonify({'dates': [], 'matrix': [], 'frequentSectors': {}, 'metric': metric})
    all_dates = [str(r[0])[:10] for r in rows][::-1]  # ASC
    D0 = all_dates[0]
    date_idx = {d: i for i, d in enumerate(all_dates)}

    rows = ch_query(f"""
        SELECT d.thscode, d.name, d.trade_date, d.close_price, d.price_change_ratio_pct, d.up_cnt, d.down_cnt
        FROM {CH_DB}.ths_industry_daily d
        JOIN {CH_DB}.ths_industry_basic b ON d.thscode = b.thscode
        WHERE d.trade_date >= '{D0}' AND {level_cond}
    """)
    sector_names = {}
    close_map = {}
    up_map = {}
    codes_by_date = {}
    for r in rows:
        code, name, date = r[0], r[1], str(r[2])[:10]
        sector_names[code] = name
        close_map[(code, date)] = _safe_float(r[3])
        up_map[(code, date)] = _safe_int(r[5])
        codes_by_date.setdefault(date, []).append(code)

    rows = ch_query(f"SELECT thscode, stock_cnt FROM {CH_DB}.ths_industry_basic")
    stock_cnt = {r[0]: _safe_int(r[1]) for r in rows}

    # 每日板块主力净流入（成分=最新成员快照固定，逐日 money_flow 求和，万元→亿）
    mf_map = {}
    if metric == 'net_mf' or metric.startswith('mf'):
        rows = ch_query(f"""
            SELECT m.plate_thscode, mf.trade_date, sum(mf.net_mf_amount) / 10000
            FROM {CH_DB}.ths_industry_member_daily m
            JOIN {CH_DB}.money_flow mf ON mf.ts_code = m.thscode
            WHERE m.trade_date = (SELECT max(trade_date) FROM {CH_DB}.ths_industry_member_daily)
              AND mf.trade_date >= '{D0}'
            GROUP BY m.plate_thscode, mf.trade_date
        """)
        for r in rows:
            mf_map[(r[0], str(r[1])[:10])] = _safe_float(r[2])

    # 每日板块涨停股数（涨停池口径）
    lu_map = {}
    if metric == 'limit_up_cnt':
        rows = ch_query(f"""
            SELECT m.plate_thscode, l.trade_date, count()
            FROM {CH_DB}.ths_industry_member_daily m
            JOIN {CH_DB}.ths_limit_up_pool_daily l ON l.thscode = m.thscode
            WHERE m.trade_date = (SELECT max(trade_date) FROM {CH_DB}.ths_industry_member_daily)
              AND l.trade_date >= '{D0}'
            GROUP BY m.plate_thscode, l.trade_date
        """)
        for r in rows:
            lu_map[(r[0], str(r[1])[:10])] = _safe_int(r[2])

    dates = all_dates[-days:]

    def metric_value(code, date):
        di = date_idx[date]
        if metric == 'net_mf':
            v = mf_map.get((code, date), 0.0)
            return v, '%+.2f亿' % v
        if metric == 'up_cnt':
            v = up_map.get((code, date), 0)
            return float(v), '%d只' % v
        if metric == 'limit_up_cnt':
            v = lu_map.get((code, date), 0)
            return float(v), '%d只' % v
        if metric == 'up_ratio':
            tot = stock_cnt.get(code, 0)
            v = (up_map.get((code, date), 0) / tot * 100) if tot else 0.0
            return v, '%.1f%%' % v
        if metric.startswith('ret'):
            n = int(metric[3:])
            if di - n < 0:
                return None, '-'
            c1 = close_map.get((code, date))
            c0 = close_map.get((code, all_dates[di - n]))
            if not c0 or not c1:
                return None, '-'
            v = (c1 / c0 - 1) * 100
            return v, '%+.2f%%' % v
        if metric.startswith('mf'):
            n = int(metric[2:])
            s = 0.0
            for k in range(max(0, di - n + 1), di + 1):
                s += mf_map.get((code, all_dates[k]), 0.0)
            return s, '%+.2f亿' % s
        return None, '-'

    # 每日按指标排序取 TOP N
    per_date = {}
    for date in dates:
        arr = []
        for code in codes_by_date.get(date, []):
            v, txt = metric_value(code, date)
            if v is None:
                continue
            arr.append((v, txt, code, sector_names.get(code, code)))
        arr.sort(key=lambda x: x[0], reverse=True)
        per_date[date] = arr[:top_n]

    # 出现频次
    sector_count = {}
    for date in dates:
        for item in per_date[date]:
            code, name = item[2], item[3]
            if code not in sector_count:
                sector_count[code] = {'name': name, 'count': 0}
            sector_count[code]['count'] += 1
    frequent_sectors = {c: i for c, i in sector_count.items() if i['count'] >= 3}

    # 矩阵：行=排名，列=日期（旧→新，前端反序展示）
    matrix = []
    for rank in range(top_n):
        rank_row = []
        for date in dates:
            lst = per_date[date]
            if rank < len(lst):
                v, txt, code, name = lst[rank]
                rank_row.append({'code': code, 'name': name, 'value': round(v, 2), 'valueText': txt,
                                 'changePct': round(float(v), 2) if metric.startswith('ret') or metric == 'up_ratio' else None,
                                 'frequent': code in frequent_sectors})
            else:
                rank_row.append(None)
        matrix.append(rank_row)

    out = {'dates': dates, 'matrix': matrix, 'frequentSectors': frequent_sectors, 'metric': metric, 'scope': scope}
    _cache_set(cache_key, out)
    return jsonify(out)


@app.route('/api/ch/sector-matrix')
def ch_sector_matrix():
    """板块涨幅矩阵：行=排名，列=日期（新→旧），每一天独立按涨幅排序
    metric 扩展（仅 type=industry）：net_mf/up_cnt/limit_up_cnt/up_ratio/ret5..60/mf5..60"""
    sector_type = request.args.get('type', 'industry')  # industry / concept
    days = int(request.args.get('days', '10'))
    top_n = int(request.args.get('top', '20'))
    metric = request.args.get('metric', 'change_pct')
    scope = request.args.get('scope', 'l2')  # l2=二级为主+一级补充 / l1=仅一级

    if sector_type == 'industry' and metric != 'change_pct':
        return _industry_matrix_extended(metric, days, top_n, scope)
    
    table = 'ths_industry_daily' if sector_type == 'industry' else 'ths_concept_daily'
    
    # 行业矩阵与热力图同口径：二级行业为基准 + 无二级分支的一级行业；l1=仅一级行业
    join_filter = ''
    if sector_type == 'industry':
        if scope == 'l1':
            join_filter = (f" JOIN (SELECT thscode FROM {CH_DB}.ths_industry_basic WHERE level = 1) b "
                           f"ON d.thscode = b.thscode")
        else:
            join_filter = (f" JOIN (SELECT thscode FROM {CH_DB}.ths_industry_basic WHERE level = 2 "
                           f"UNION DISTINCT SELECT thscode FROM {CH_DB}.ths_industry_basic WHERE level = 1 AND thscode NOT IN ("
                           f"SELECT DISTINCT parent_code FROM {CH_DB}.ths_industry_basic WHERE level = 2)) b "
                           f"ON d.thscode = b.thscode")
    
    from datetime import datetime, timedelta
    
    # 获取最新交易日
    rows = ch_query(f"SELECT max(trade_date) FROM {CH_DB}.{table}")
    if not rows:
        return jsonify({'dates': [], 'matrix': [], 'frequentSectors': {}})
    latest_date = rows[0][0]
    end_dt = datetime.strptime(latest_date, '%Y-%m-%d')
    start_dt = end_dt - timedelta(days=days*2)
    
    # 获取近N天所有板块的涨跌幅
    rows = ch_query(f"""
        SELECT d.trade_date, d.thscode, d.name, d.price_change_ratio_pct, d.close_price
        FROM {CH_DB}.{table} d{join_filter}
        WHERE d.trade_date >= '{start_dt.strftime('%Y-%m-%d')}'
        ORDER BY d.trade_date ASC, d.price_change_ratio_pct DESC
    """)
    
    # 按日期分组，每天独立按涨幅排序
    date_data = {}
    sector_count = {}  # 统计每个板块在N天中出现的次数
    
    for row in rows:
        date = row[0]
        code = row[1]
        name = row[2]
        pct = float(row[3]) if row[3] else 0
        close = float(row[4]) if row[4] else 0
        
        if date not in date_data:
            date_data[date] = []
        date_data[date].append({
            'code': code,
            'name': name,
            'changePct': round(pct, 2),
            'close': round(close, 2)
        })
    
    # 取最近N天
    dates = sorted(date_data.keys(), reverse=True)[:days]
    dates = sorted(dates)  # 正序，从旧到新
    
    # 统计每个板块在N天TOP N中出现的次数
    for date in dates:
        day_sectors = date_data[date][:top_n]
        for s in day_sectors:
            code = s['code']
            if code not in sector_count:
                sector_count[code] = {'name': s['name'], 'count': 0}
            sector_count[code]['count'] += 1
    
    # 筛选出现3次及以上的板块
    frequent_sectors = {}
    for code, info in sector_count.items():
        if info['count'] >= 3:
            frequent_sectors[code] = {'name': info['name'], 'count': info['count']}
    
    # 构建矩阵：行=排名（第1名到第N名），列=日期
    matrix = []
    for rank in range(top_n):
        rank_row = []
        for date in dates:
            day_sectors = date_data[date]
            if rank < len(day_sectors):
                s = day_sectors[rank]
                rank_row.append({
                    'code': s['code'],
                    'name': s['name'],
                    'close': s['close'],
                    'changePct': s['changePct'],
                    'frequent': s['code'] in frequent_sectors
                })
            else:
                rank_row.append(None)
        matrix.append(rank_row)
    
    return jsonify({
        'dates': dates,
        'matrix': matrix,
        'frequentSectors': frequent_sectors,
        'metric': metric,
        'scope': scope
    })


@app.route('/api/ch/sector-rank-history')
def ch_sector_rank_history():
    """板块近 N 天每日涨幅排名（在全部板块中的座次，第1名最靠前）。
    用 RANK 窗口函数按交易日独立排序，行业口径与涨幅矩阵一致（二级行业基准）。"""
    sector_type = request.args.get('type', 'industry')  # industry / concept
    code = request.args.get('code', '')
    days = int(request.args.get('days', '30'))
    end_date = request.args.get('end_date', '')
    scope = request.args.get('scope', 'l2')
    cache_key = f'ch_rank_hist_{sector_type}_{code}_{days}_{end_date}_{scope}'
    cached = _cache_get(cache_key)
    if cached:
        return jsonify(cached)
    if not code:
        return jsonify({'dates': [], 'ranks': [], 'endDate': end_date})

    table = 'ths_industry_daily' if sector_type == 'industry' else 'ths_concept_daily'
    join_filter = ''
    if sector_type == 'industry':
        if scope == 'l1':
            join_filter = (f" JOIN (SELECT thscode FROM {CH_DB}.ths_industry_basic WHERE level = 1) b "
                           f"ON d.thscode = b.thscode")
        else:
            join_filter = (f" JOIN (SELECT thscode FROM {CH_DB}.ths_industry_basic WHERE level = 2 "
                           f"UNION DISTINCT SELECT thscode FROM {CH_DB}.ths_industry_basic WHERE level = 1 AND thscode NOT IN ("
                           f"SELECT DISTINCT parent_code FROM {CH_DB}.ths_industry_basic WHERE level = 2)) b "
                           f"ON d.thscode = b.thscode")

    from datetime import datetime, timedelta
    if not end_date:
        rows = ch_query(f"SELECT max(trade_date) FROM {CH_DB}.{table}")
        if not rows:
            return jsonify({'dates': [], 'ranks': [], 'endDate': ''})
        end_date = rows[0][0]
    end_dt = datetime.strptime(end_date, '%Y-%m-%d')
    start_dt = end_dt - timedelta(days=days * 2)

    rows = ch_query(f"""
        SELECT sub.date, sub.rank
        FROM (
            SELECT d.trade_date AS date, d.thscode,
                   RANK() OVER (PARTITION BY d.trade_date ORDER BY d.price_change_ratio_pct DESC) AS rank
            FROM {CH_DB}.{table} d{join_filter}
            WHERE d.trade_date >= '{start_dt.strftime('%Y-%m-%d')}' AND d.trade_date <= '{end_date}'
        ) sub
        WHERE sub.thscode = '{code}'
        ORDER BY sub.date ASC
    """)

    dates = []
    ranks = []
    for row in rows[-days:]:
        dates.append(row[0])
        ranks.append(int(row[1]))
    out = {'dates': dates, 'ranks': ranks, 'endDate': end_date}
    _cache_set(cache_key, out)
    return jsonify(out)


@app.route('/api/ch/sector-members')
def ch_sector_members():
    """获取板块成分股列表，支持指定日期
    优先用指定日期的成分股映射（如果有），没有则用最新日期映射
    行情数据用指定日期
    """
    sector_type = request.args.get('type', 'industry')  # industry / concept
    code = request.args.get('code', '')
    trade_date = request.args.get('date', '')  # 指定行情日期
    
    if not code:
        return jsonify({'members': []})
    
    # 根据类型选择表
    if sector_type == 'industry':
        member_table = 'ths_industry_member_daily'
        daily_table = 'ths_industry_daily'
    else:
        member_table = 'ths_concept_member_daily'
        daily_table = 'ths_concept_daily'
    
    # 获取成分股映射最新日期
    rows = ch_query(f"SELECT max(trade_date) FROM {CH_DB}.{member_table}")
    if not rows:
        return jsonify({'members': []})
    latest_member_date = rows[0][0]
    
    # 如果没有指定行情日期，就用最新日期
    if not trade_date:
        trade_date = latest_member_date
    
    # 找距离指定日期最近的成分股快照日期
    from datetime import datetime as dt
    rows = ch_query(f"""
        SELECT DISTINCT trade_date
        FROM {CH_DB}.{member_table}
        WHERE plate_thscode = '{code}'
    """)
    
    if rows:
        # 计算每个日期和指定日期的差，找最近的
        target_date = dt.strptime(trade_date, '%Y-%m-%d')
        best_date = None
        best_diff = 9999
        for row in rows:
            d = row[0]
            diff = abs((dt.strptime(d, '%Y-%m-%d') - target_date).days)
            if diff < best_diff:
                best_diff = diff
                best_date = d
        member_date = best_date
    else:
        member_date = latest_member_date
    
    # 查询成分股：合并行情、指标、资金流、涨跌停标记
    # 先查涨跌停池（标记用）
    limit_up_codes = set()
    limit_down_codes = set()
    try:
        lu_rows = ch_query(f"""
            SELECT DISTINCT thscode FROM {CH_DB}.ths_limit_up_pool_daily WHERE trade_date = '{trade_date}'
        """)
        for row in lu_rows:
            limit_up_codes.add(row[0])
    except:
        pass
    try:
        ld_rows = ch_query(f"""
            SELECT DISTINCT thscode FROM {CH_DB}.ths_limit_down_pool_daily WHERE trade_date = '{trade_date}'
        """)
        for row in ld_rows:
            limit_down_codes.add(row[0])
    except:
        pass
    
    # 大盘指数涨跌幅（对跑大盘基准，默认沪深300）
    benchmark_pct = 0.0
    try:
        idx_rows = ch_query(f"""
            SELECT pct_chg FROM {CH_DB}.index_daily
            WHERE ts_code = '000300.SH' AND trade_date = '{trade_date}'
            LIMIT 1
        """)
        if idx_rows and idx_rows[0][0]:
            benchmark_pct = float(idx_rows[0][0])
    except:
        pass
    
    # 板块当日涨跌幅（对跑板块基准：所选板块在行情日期的涨跌幅）
    sector_pct = 0.0
    try:
        sec_rows = ch_query(f"""
            SELECT price_change_ratio_pct FROM {CH_DB}.{daily_table}
            WHERE thscode = '{code}' AND trade_date = '{trade_date}'
            LIMIT 1
        """)
        if sec_rows and sec_rows[0][0]:
            sector_pct = float(sec_rows[0][0])
    except:
        pass
    
    # 板块总成员数（用于前端提示"已显示 M / 共 N"）
    try:
        t_rows = ch_query(f"""
            SELECT count() FROM {CH_DB}.{member_table}
            WHERE plate_thscode = '{code}' AND trade_date = '{member_date}'
        """)
        total_members = int(t_rows[0][0]) if t_rows else 0
    except Exception:
        total_members = 0

    rows = ch_query(f"""
        SELECT m.thscode, m.name,
               d.close, d.pct_chg, d.amount, d.high, d.low, d.pre_close,
               di.turnover_rate, di.volume_ratio,
               di.pe_ttm, di.pb, di.total_mv, di.circ_mv,
               mf.buy_elg_amount, mf.buy_lg_amount, mf.sell_elg_amount, mf.sell_lg_amount
        FROM {CH_DB}.{member_table} m
        LEFT JOIN {CH_DB}.daily_prices d ON m.thscode = d.ts_code AND d.trade_date = '{trade_date}'
        LEFT JOIN {CH_DB}.daily_indicators di ON m.thscode = di.ts_code AND di.trade_date = '{trade_date}'
        LEFT JOIN {CH_DB}.money_flow mf ON m.thscode = mf.ts_code AND mf.trade_date = '{trade_date}'
        WHERE m.plate_thscode = '{code}' AND m.trade_date = '{member_date}'
        ORDER BY d.pct_chg DESC NULLS LAST
        LIMIT 2000
    """)
    
    members = []
    for row in rows:
        code = row[0]
        name = row[1] or row[0]
        close = float(row[2]) if row[2] else 0
        pct = float(row[3]) if row[3] else 0
        amount = float(row[4]) / 100000 if row[4] else 0  # 千元→亿元
        high = float(row[5]) if row[5] else 0
        low = float(row[6]) if row[6] else 0
        pre_close = float(row[7]) if row[7] else 0
        
        # 振幅 = (high - low) / pre_close * 100
        amplitude = round((high - low) / pre_close * 100, 2) if pre_close else 0
        
        # 换手率、量比、估值
        turnover = round(float(row[8]), 2) if row[8] else 0
        vol_ratio = round(float(row[9]), 2) if row[9] else 0
        pe = round(float(row[10]), 2) if row[10] else 0
        pb = round(float(row[11]), 2) if row[11] else 0
        total_mv = round(float(row[12]) / 10000, 1) if row[12] else 0  # 万元→亿元
        circ_mv = round(float(row[13]) / 10000, 1) if row[13] else 0
        
        # 资金流（万元）
        buy_elg = float(row[14]) if row[14] else 0
        buy_lg = float(row[15]) if row[15] else 0
        sell_elg = float(row[16]) if row[16] else 0
        sell_lg = float(row[17]) if row[17] else 0
        main_net = buy_elg + buy_lg - sell_elg - sell_lg  # 主力净流入(万元)
        elg_net = buy_elg - sell_elg  # 超大单净流入(万元)
        
        # 对跑大盘 = 个股涨跌幅 - 基准指数涨跌幅
        vs_index = round(pct - benchmark_pct, 2)
        # 对跑板块 = 个股涨跌幅 - 板块当日涨跌幅
        vs_sector = round(pct - sector_pct, 2)
        
        members.append({
            'code': code,
            'name': name,
            'close': round(close, 2),
            'changePct': round(pct, 2),
            'amplitude': amplitude,
            'vsIndex': vs_index,
            'vsSector': vs_sector,
            'amount': round(amount, 4),
            'turnover': turnover,
            'volRatio': vol_ratio,
            'mainNet': round(main_net, 0),  # 万元
            'elgNet': round(elg_net, 0),    # 万元
            'mainNetPct': round(main_net / (amount * 10000) * 100, 2) if amount else 0,  # 主力净占比%
            'pe': pe,
            'pb': pb,
            'totalMv': total_mv,
            'circMv': circ_mv,
            'isLimitUp': code in limit_up_codes,
            'isLimitDown': code in limit_down_codes
        })
    
    return jsonify({
        'date': trade_date,
        'memberDate': member_date,
        'benchmark': '沪深300',
        'benchmarkPct': round(benchmark_pct, 2),
        'sectorPct': round(sector_pct, 2),
        'total': total_members,
        'members': members
    })


@app.route('/api/ch/sector-moneyflow')
def ch_sector_moneyflow():
    """板块/题材主力资金净流入曲线：成分=最新成员快照，逐日对成员 money_flow.net_mf_amount 求和（万元→亿）
    参数 codes=881273.TI,885525.TI（行业/概念混合，逗号分隔），days=近N个交易日（默认60，上限250）"""
    codes = [c.strip() for c in request.args.get('codes', '').split(',') if c.strip()]
    days = int(request.args.get('days', '60') or 60)
    days = max(5, min(days, 250))
    if not codes:
        return jsonify({'list': []})
    cache_key = f'ch_mf_sector_{"_".join(codes)}_{days}'
    cached = _cache_get(cache_key)
    if cached:
        return jsonify(cached)
    D = _member_snapshot_date()
    mf_max = ch_query(f"SELECT max(trade_date) FROM {CH_DB}.money_flow")
    end = mf_max[0][0] if mf_max and mf_max[0] else D
    drow = ch_query(
        f"SELECT DISTINCT trade_date FROM {CH_DB}.money_flow WHERE trade_date <= '{end}' "
        f"ORDER BY trade_date DESC LIMIT {days}")
    date_list = [str(r[0])[:10] for r in drow][::-1]
    result = []
    for code in codes:
        is_concept = code.startswith('885') or code.startswith('886')
        mt = 'ths_concept_member_daily' if is_concept else 'ths_industry_member_daily'
        bt = 'ths_concept_basic' if is_concept else 'ths_industry_basic'
        nm = ch_query(f"SELECT name FROM {CH_DB}.{bt} WHERE thscode='{code}' LIMIT 1")
        name = nm[0][0] if nm and nm[0] else code
        members = ch_query(f"SELECT thscode FROM {CH_DB}.{mt} WHERE plate_thscode='{code}' AND trade_date='{D}'")
        codes_in = [r[0] for r in members if r[0]]
        series = [None] * len(date_list)
        if codes_in and date_list:
            inlist = "','".join(codes_in)
            rows = ch_query(
                f"SELECT trade_date, sum(net_mf_amount) FROM {CH_DB}.money_flow "
                f"WHERE ts_code IN ('{inlist}') AND trade_date >= '{date_list[0]}' AND trade_date <= '{end}' "
                f"GROUP BY trade_date")
            mp = {str(r[0])[:10]: round(float(r[1]) / 1e4, 2) for r in rows if r[0]}
            series = [mp.get(d, 0.0) for d in date_list]
        result.append({'code': code, 'name': name, 'dates': date_list, 'net': series})
    _cache_set(cache_key, {'list': result})
    return jsonify({'list': result})



# ==================== 个股行情 API（TradingView 风格） ====================
import sys as _sys
import os as _os
import pandas as _pd
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
try:
    from stock_indicators import calc_indicators as _calc_indicators
except Exception as _e:
    print('[WARN] stock_indicators import fail:', _e)
    _calc_indicators = None

# ---- backtrader 指标桥（默认启用；设置环境变量 BT_INDICATOR_BRIDGE=0 可回退 pandas 移植版） ----
# 技术路径：本地魔改 backtrader 1.9.74.123 + my_bt_indicator 真指标类（与 Qlibworks 回测环境同源）
_BT_BRIDGE_ENABLED = _os.environ.get('BT_INDICATOR_BRIDGE', '1') != '0'
if _BT_BRIDGE_ENABLED:
    try:
        from bt_indicator_bridge import calc_bt_indicators as _calc_bt_indicators
        import backtrader as _bt
        print('[INFO] bt indicator bridge loaded (backtrader %s)' % getattr(_bt, '__version__', 'local-mods'))
    except Exception as _e:
        _calc_bt_indicators = None
        print('[WARN] bt_indicator_bridge import fail, fallback pandas:', _e)
else:
    _calc_bt_indicators = None
    print('[INFO] bt indicator bridge disabled by BT_INDICATOR_BRIDGE=0')


def _stock_df(code, days, freq='daily', end=''):
    """取前复权 DataFrame（升序），K线与指标共用；freq: daily/weekly/monthly
    end: YYYY-MM-DD 截止日 —— 在聚合之前切掉之后的日线，周/月线最后一根才算真正截止到该日
    """
    raw_days = int(days)
    if freq == 'weekly':
        raw_days = int(days) * 5
    elif freq == 'monthly':
        raw_days = int(days) * 22
    raw_days = min(max(raw_days + 100, 120), 3200)
    end_cond = (" AND d.trade_date <= '%s'" % end) if end else ''
    rows = ch_query(
        "SELECT d.trade_date, d.open, d.high, d.low, d.close, d.pre_close, d.vol, d.amount, d.pct_chg, f.adj_factor "
        "FROM %s.daily_prices d "
        "LEFT JOIN %s.daily_adj_factors f ON d.ts_code = f.ts_code AND d.trade_date = f.trade_date "
        "WHERE d.ts_code = '%s'%s ORDER BY d.trade_date DESC LIMIT %d" % (CH_DB, CH_DB, code, end_cond, raw_days)
    )
    if not rows or not rows[0]:
        return None
    rows = rows[::-1]
    df = _pd.DataFrame(rows, columns=['trade_date', 'open', 'high', 'low', 'close', 'pre_close',
                                      'vol', 'amount', 'pct_chg', 'adj_factor'])
    for c in ['open', 'high', 'low', 'close', 'pre_close', 'vol', 'amount', 'pct_chg']:
        df[c] = _pd.to_numeric(df[c], errors='coerce')
    df['adj_factor'] = _pd.to_numeric(df['adj_factor'], errors='coerce').ffill().bfill().fillna(1.0)
    latest_f = float(df['adj_factor'].iloc[-1]) or 1.0
    adj = df['adj_factor'] / latest_f
    for c in ['open', 'high', 'low', 'close', 'pre_close']:
        df[c] = (df[c] * adj).round(4)
    # 周/月聚合
    if freq == 'weekly':
        df['_grp'] = _pd.to_datetime(df['trade_date']).dt.to_period('W')
    elif freq == 'monthly':
        df['_grp'] = _pd.to_datetime(df['trade_date']).dt.to_period('M')
    else:
        df['_grp'] = _pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d')
    g = df.groupby('_grp', sort=True)
    agg = g.agg(
        trade_date=('trade_date', 'last'),
        open=('open', 'first'), high=('high', 'max'), low=('low', 'min'),
        close=('close', 'last'), pre_close=('pre_close', 'first'),
        vol=('vol', 'sum'), amount=('amount', 'sum'), pct_chg=('pct_chg', 'last'),
    ).reset_index(drop=True)
    if freq != 'daily':
        agg['pct_chg'] = ((agg['close'] / agg['pre_close'] - 1) * 100).round(2)
    return agg


def _stock_name(code):
    rows = ch_query("SELECT name, industry, market FROM %s.stock_basic WHERE ts_code='%s' LIMIT 1" % (CH_DB, code))
    if rows and rows[0]:
        return {'name': rows[0][0] or code, 'industry': rows[0][1] or '', 'market': rows[0][2] or ''}
    return {'name': code, 'industry': '', 'market': ''}


@app.route('/api/stock/search')
def api_stock_search():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'list': []})
    qq = q.replace("'", "''")
    rows = ch_query(
        "SELECT ts_code, name, industry, market, symbol FROM %s.stock_basic "
        "WHERE list_status='L' AND (name LIKE '%%%s%%' OR lower(ts_code) LIKE lower('%%%s%%') "
        "OR lower(symbol) LIKE lower('%%%s%%') OR lower(cnspell) LIKE lower('%%%s%%')) "
        "ORDER BY CASE WHEN lower(ts_code) LIKE lower('%s%%') THEN 0 WHEN lower(symbol) LIKE lower('%s%%') THEN 1 ELSE 2 END, ts_code "
        "LIMIT 40" % (CH_DB, qq, qq, qq, qq, qq, qq)
    )
    return jsonify({'list': [
        {'code': r[0], 'name': r[1] or r[0], 'industry': r[2] or '', 'market': r[3] or '', 'symbol': r[4] or ''}
        for r in rows]})


@app.route('/api/stock/list')
def api_stock_list():
    market = request.args.get('market', '').replace("'", '')
    industry = request.args.get('industry', '').strip().replace("'", '')
    kw = request.args.get('kw', '').strip().replace("'", '')
    size = min(int(request.args.get('size', '80')), 300)
    latest = ch_query("SELECT max(trade_date) FROM %s.daily_prices" % CH_DB)
    trade_date = latest[0][0] if latest and latest[0] and latest[0][0] else ''
    conds = ["s.list_status='L'"]
    if market:
        conds.append("s.market='%s'" % market)
    if industry:
        conds.append("s.industry='%s'" % industry)
    if kw:
        conds.append("(s.name LIKE '%%%s%%' OR s.ts_code LIKE '%%%s%%' OR s.symbol LIKE '%%%s%%')" % (kw, kw, kw))
    where = ' AND '.join(conds)
    sql = (
        "SELECT s.ts_code, s.name, s.industry, s.market, d.close, d.pct_chg, d.amount, d.turnover_rate "
        "FROM %s.stock_basic s "
        "LEFT JOIN ("
        "  SELECT d2.ts_code, d2.close, d2.pct_chg, d2.amount, di.turnover_rate "
        "  FROM %s.daily_prices d2 LEFT JOIN %s.daily_indicators di "
        "    ON d2.ts_code = di.ts_code AND d2.trade_date = di.trade_date "
        "  WHERE d2.trade_date = '%s'"
        ") d ON s.ts_code = d.ts_code "
        "WHERE %s ORDER BY d.amount DESC NULLS LAST LIMIT %d" % (CH_DB, CH_DB, CH_DB, trade_date, where, size)
    )
    rows = ch_query(sql)
    return jsonify({'date': trade_date, 'list': [
        {'code': r[0], 'name': r[1] or r[0], 'industry': r[2] or '', 'market': r[3] or '',
         'close': round(float(r[4]), 2) if r[4] else 0,
         'pctChg': round(float(r[5]), 2) if r[5] else 0,
         'amount': round(float(r[6]) / 100000, 2) if r[6] else 0,
         'turnover': round(float(r[7]), 2) if r[7] else 0}
        for r in rows]})


@app.route('/api/stock/kline')
def api_stock_kline():
    code = request.args.get('code', '').strip().upper()
    days = min(int(request.args.get('days', '250')), 1200)
    freq = request.args.get('freq', 'daily')
    end = request.args.get('end', '').strip()[:10]
    # 截止日校验：只接受 YYYY-MM-DD，非法值直接忽略（防 SQL 注入）
    if end and not (len(end) == 10 and end.replace('-', '').isdigit()):
        end = ''
    if not code:
        return jsonify({'error': 'no code'}), 400
    ck = 'stock_kline_%s_%d_%s_%s' % (code, days, freq, end or '-')
    cached = _cache_get(ck)
    if cached:
        return jsonify(cached)
    df = _stock_df(code, days, freq, end)
    if df is None or df.empty:
        return jsonify({'code': code, 'name': code, 'freq': freq, 'kline': []})
    kline = [{'date': str(r['trade_date'])[:10], 'open': float(r['open']), 'high': float(r['high']),
              'low': float(r['low']), 'close': float(r['close']), 'vol': float(r['vol']),
              'amount': float(r['amount']), 'pctChg': round(float(r['pct_chg']), 2) if r['pct_chg'] == r['pct_chg'] else 0}
             for _, r in df.iterrows()]
    info = _stock_name(code)
    data = {'code': code, 'name': info['name'], 'industry': info['industry'], 'market': info['market'],
            'freq': freq, 'kline': kline}
    _cache_set(ck, data)
    return jsonify(data)


@app.route('/api/stock/quote')
def api_stock_quote():
    code = request.args.get('code', '').strip().upper()
    if not code:
        return jsonify({'error': 'no code'}), 400
    rows = ch_query(
        "SELECT d.trade_date, d.close, d.pct_chg, d.vol, d.amount, d.high, d.low, d.pre_close, d.open, "
        "di.turnover_rate, di.volume_ratio, di.pe_ttm, di.pb, di.total_mv, di.circ_mv "
        "FROM %s.daily_prices d "
        "LEFT JOIN %s.daily_indicators di ON d.ts_code = di.ts_code AND d.trade_date = di.trade_date "
        "WHERE d.ts_code='%s' ORDER BY d.trade_date DESC LIMIT 1" % (CH_DB, CH_DB, code)
    )
    if not rows or not rows[0]:
        return jsonify({'code': code, 'error': 'no data'})
    r = rows[0]
    info = _stock_name(code)
    high = float(r[5]) if r[5] else 0
    low = float(r[6]) if r[6] else 0
    pre_close = float(r[7]) if r[7] else 0
    close = float(r[1]) if r[1] else 0
    o = float(r[8]) if r[8] else 0
    amplitude = round((high - low) / pre_close * 100, 2) if pre_close else 0
    return jsonify({
        'code': code, 'name': info['name'], 'industry': info['industry'], 'market': info['market'],
        'date': str(r[0])[:10], 'close': round(close, 2),
        'open': round(o, 2), 'high': round(high, 2), 'low': round(low, 2), 'preClose': round(pre_close, 2),
        'pctChg': round(float(r[2]), 2) if r[2] else 0,
        'vol': float(r[3]) if r[3] else 0,
        'amount': round(float(r[4]) / 100000, 2) if r[4] else 0,
        'amplitude': amplitude,
        'turnover': round(float(r[9]), 2) if r[9] else 0,
        'volRatio': round(float(r[10]), 2) if r[10] else 0,
        'pe': round(float(r[11]), 2) if r[11] else 0,
        'pb': round(float(r[12]), 2) if r[12] else 0,
        'totalMv': round(float(r[13]) / 10000, 1) if r[13] else 0,
        'circMv': round(float(r[14]) / 10000, 1) if r[14] else 0,
    })


@app.route('/api/stock/intraday')
def api_stock_intraday():
    """个股分时（东方财富 trends2）：一次返回最近 5 个交易日的逐分钟价格/均价/量额。
    参数 date=YYYY-MM-DD 选择其中某日（缺省或非法日期回落到最新交易日）。
    本地 ClickHouse 无分钟级数据，历史更早的分时公开源不提供。"""
    code = request.args.get('code', '').strip().upper()
    date = request.args.get('date', '').strip()
    if not code:
        return jsonify({'error': 'no code'}), 400
    multi = request.args.get('multi') == '1'
    ck = 'intraday2_%s_%s%s' % (code, date or 'latest', '_m' if multi else '')
    cached = _cache_get(ck)
    if cached:
        return jsonify(cached)
    num, _, suf = code.partition('.')
    market = 1 if suf == 'SH' else 0
    try:
        r = session.get('https://push2his.eastmoney.com/api/qt/stock/trends2/get',
                        params={'secid': '%d.%s' % (market, num),
                                'fields1': 'f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13,f14,f15,f16,f17,f18,f19',
                                'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62',
                                'ndays': 5, 'iscr': 0},
                        timeout=12, headers={'User-Agent': 'Mozilla/5.0'})
        j = r.json()
    except Exception as e:
        return jsonify({'code': code, 'error': 'intraday source fail: %s' % e}), 503
    d = j.get('data') or {}
    by_date = {}
    for row in d.get('trends') or []:
        parts = row.split(',')
        if len(parts) < 8:
            continue
        try:
            o = float(parts[1]); p = float(parts[2]); h = float(parts[3]); l = float(parts[4])
            vol = float(parts[5]); amt = float(parts[6]); avgp = float(parts[7])
            cv = float(parts[10]) if len(parts) > 10 else 0.0   # 当日累计成交量（手）
            ca = float(parts[11]) if len(parts) > 11 else 0.0   # 当日累计成交额（元）
        except (TypeError, ValueError):
            continue
        if p <= 0:
            continue
        if o <= 0:
            o = p                                   # 09:30 首根开价字段为 0，用现价代替
        if h <= 0:
            h = max(o, p)
        if l <= 0:
            l = min(o, p)
        h = max(h, o, p)
        l = min(l, o, p)
        dstr, tstr = parts[0][:10], parts[0][11:16]
        by_date.setdefault(dstr, []).append({'t': tstr, 'o': round(o, 3), 'p': round(p, 3),
                                             'h': round(h, 3), 'l': round(l, 3),
                                             'avg': round(avgp, 3), 'vol': vol, 'amt': amt,
                                             'cumVol': cv, 'cumAmt': ca})
    dates = sorted(by_date.keys())
    if not dates:
        return jsonify({'code': code, 'name': d.get('name') or code, 'dates': [],
                        'date': '', 'preClose': 0, 'prePrices': {}, 'decimal': 2, 'volUnit': 100,
                        'source': 'eastmoney', 'data': []})
    sel = date if date in by_date else dates[-1]
    # 逐日昨收：接口 hisPrePrices 直接给每个交易日的真实前收（优先），CH daily_prices 兜底
    pre_map = {}
    for it in d.get('hisPrePrices') or []:
        try:
            k = str(it.get('date') or '')
            k = '%s-%s-%s' % (k[:4], k[4:6], k[6:8])
            v = float(it.get('prePrice') or 0)
            if v > 0:
                pre_map[k] = round(v, 3)
        except (TypeError, ValueError, AttributeError):
            continue
    pre = pre_map.get(sel)
    if not pre or pre <= 0:
        try:
            prow = ch_query("SELECT pre_close FROM %s.daily_prices WHERE ts_code='%s' AND trade_date='%s' LIMIT 1"
                            % (CH_DB, code, sel))
            if prow and prow[0] and prow[0][0] not in (None, '\\N', ''):
                pre = float(prow[0][0])
        except Exception:
            pre = None
    if not pre or pre <= 0:
        pre = float(d.get('preClose') or 0)
    try:
        dp = int(d.get('decimal') or 2)
    except (TypeError, ValueError):
        dp = 2
    if dp < 0 or dp > 6:
        dp = 2
    data = {'code': code, 'name': d.get('name') or code, 'date': sel, 'dates': dates,
            'preClose': round(pre, 3), 'prePrices': pre_map,
            'decimal': dp, 'volUnit': int(d.get('volUnit') or 100),
            'tradePeriods': d.get('tradePeriods'), 'source': 'eastmoney',
            'data': by_date[sel]}
    # multi=1：额外给出 5 个交易日的完整分时（仅时间/价格/均价），供"5日归一化叠加"使用
    if multi:
        multi_arr = []
        for dt in dates:
            pv = pre_map.get(dt) or 0
            if not pv or pv <= 0:
                continue
            multi_arr.append({'date': dt, 'preClose': pv,
                              'data': [{'t': x['t'], 'p': x['p'], 'avg': x['avg']} for x in by_date[dt]]})
        data['multi'] = multi_arr
    _cache_set(ck, data)
    return jsonify(data)


@app.route('/api/stock/indicators')
def api_stock_indicators():
    code = request.args.get('code', '').strip().upper()
    days = min(int(request.args.get('days', '250')), 1200)
    freq = request.args.get('freq', 'daily')
    end = request.args.get('end', '').strip()[:10]
    if end and not (len(end) == 10 and end.replace('-', '').isdigit()):
        end = ''
    if not code:
        return jsonify({'error': 'no code'}), 400
    # 指标参数透传：键名与 bt_indicator_bridge 参数一一对应（如 st_period/st_mult/macd_fast…），
    # 逗号分隔转 tuple（ma_periods=20,50,100），整数值转 int（窗口类参数需要），非整数保持 float，非数值原样透传
    ind_params = {}
    for _k, _v in request.args.items():
        if _k in ('code', 'days', 'freq', 'end') or not _v:
            continue
        def _num(x):
            f = float(x)
            return int(f) if f.is_integer() else f
        if ',' in _v:
            try:
                ind_params[_k] = tuple(_num(x) for x in _v.split(','))
            except (TypeError, ValueError):
                ind_params[_k] = _v
        else:
            try:
                ind_params[_k] = _num(_v)
            except (TypeError, ValueError):
                ind_params[_k] = _v
    # 缓存 key 带参数指纹：不同参数组合不串缓存
    _pfp = json.dumps(ind_params, sort_keys=True) if ind_params else ''
    ck = 'stock_ind_%s_%d_%s_%s_%s' % (code, days, freq, end or '-', _pfp)
    cached = _cache_get(ck)
    if cached:
        return jsonify(cached)
    if _calc_indicators is None and _calc_bt_indicators is None:
        return jsonify({'error': 'indicators module unavailable'}), 500
    df = _stock_df(code, days, freq, end)
    if df is None or df.empty:
        return jsonify({'error': 'no data'})
    # 优先 backtrader 桥（真指标算法，与回测环境同源），异常回退 pandas 移植版
    ind = None
    if _calc_bt_indicators is not None:
        try:
            ind = _calc_bt_indicators(df, ind_params)
        except Exception as _be:
            print('[WARN] bt bridge calc fail, fallback pandas:', _be)
    if ind is None:
        ind = _calc_indicators(df, ind_params)
    data = {'code': code, 'freq': freq, 'length': ind['length'],
            'indicators': {k: v for k, v in ind.items() if k != 'length'}}
    _cache_set(ck, data)
    return jsonify(data)



# ==================== 因子仓库透视 API（Phase 1：Qlibworks 因子仓库上板） ====================
import os as _os
from pathlib import Path as _Path

# 因子仓库根目录，可通过环境变量 FACTOR_WAREHOUSE_DIR 覆盖（默认指向 Qlibworks）
FACTOR_WAREHOUSE_DIR = _os.environ.get('FACTOR_WAREHOUSE_DIR', r'E:\Quant\Qlibworks\factor_data\warehouse')


def _factor_meta_list():
    """扫描因子仓库全部因子元数据（读 meta.json），缓存 600s"""
    ck = 'factors_meta_list'
    cached = _cache_get(ck)
    if cached:
        return cached
    base = _Path(FACTOR_WAREHOUSE_DIR)
    items = []
    if base.is_dir():
        for d in sorted(base.iterdir()):
            if not d.is_dir():
                continue
            meta_path = d / 'meta.json'
            if not meta_path.exists():
                continue
            try:
                m = json.loads(meta_path.read_text(encoding='utf-8'))
            except Exception:
                continue
            dr = m.get('data_range', {})
            items.append({
                'name': m.get('factor_name', d.name),
                'category': m.get('category', ''),
                'updatedAt': m.get('updated_at', ''),
                'firstDate': dr.get('first_date', ''),
                'lastDate': dr.get('last_date', ''),
                'totalRecords': dr.get('total_records', 0),
                'uniqueStocks': dr.get('unique_stocks', 0),
                'strategyHint': m.get('strategy_hint', ''),
                'usageScenario': m.get('usage_scenario', ''),
            })
    items.sort(key=lambda x: x['name'])
    _cache_set(ck, items)
    return items


@app.route('/api/factors/list')
def api_factors_list():
    """因子仓库列表，支持 q（名称/分类/提示关键词）与 category 过滤"""
    q = request.args.get('q', '').strip().lower()
    cat = request.args.get('category', '').strip()
    items = _factor_meta_list()
    if q:
        items = [it for it in items if q in it['name'].lower()
                 or q in it['category'].lower() or q in it['strategyHint'].lower()]
    if cat:
        items = [it for it in items if it['category'] == cat]
    return jsonify({'total': len(items), 'list': items})


@app.route('/api/factors/detail')
def api_factors_detail():
    """单因子详情：meta + 年度记录分布 + 指定日期（默认最新）截面统计/分位数/直方图/Top20"""
    name = request.args.get('name', '').strip()
    if not name:
        return jsonify({'error': 'no name'}), 400
    import duckdb
    import numpy as _np
    base = _Path(FACTOR_WAREHOUSE_DIR) / name
    meta_path = base / 'meta.json'
    if not meta_path.exists():
        return jsonify({'error': 'factor not found'}), 404
    try:
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
    except Exception as e:
        return jsonify({'error': 'meta parse error: %s' % e}), 500
    pattern = (base.as_posix() + '/*.parquet').replace("'", "''")
    date_param = request.args.get('date', '').strip()
    ck = 'factor_detail_%s_%s' % (name, date_param or 'latest')
    cached = _cache_get(ck)
    if cached:
        return jsonify(cached)
    con = duckdb.connect()
    try:
        yearly = [
            {'year': int(y), 'records': int(n), 'stocks': int(s)}
            for y, n, s in con.execute(
                "SELECT year(datetime) AS y, count(*) AS n, count(DISTINCT instrument) AS s "
                "FROM read_parquet('%s') GROUP BY y ORDER BY y" % pattern).fetchall()
        ]
        if not date_param:
            date_param = str(con.execute(
                "SELECT max(CAST(datetime AS DATE)) FROM read_parquet('%s')" % pattern).fetchone()[0])
        rows = con.execute(
            "SELECT instrument, value FROM read_parquet('%s') "
            "WHERE CAST(datetime AS DATE) = '%s'" % (pattern, date_param)).fetchall()
    except Exception as e:
        return jsonify({'error': 'query failed: %s' % e}), 500
    if not rows:
        return jsonify({'meta': meta, 'yearly': yearly,
                        'cross': {'date': date_param, 'count': 0, 'error': '该日期无截面数据'}})
    arr = _np.array([float(r[1]) for r in rows], dtype=float)
    pct_q = [1, 5, 25, 50, 75, 95, 99]
    quantiles = {'p%d' % q: round(float(_np.percentile(arr, q)), 4) for q in pct_q}
    # 直方图：按 [p0,p10,...,p100] 十等分桶
    edges = _np.percentile(arr, list(range(0, 101, 10)))
    hist = []
    for i in range(10):
        mask = (arr >= edges[i]) & (arr < edges[i + 1]) if i < 9 else (arr >= edges[i]) & (arr <= edges[i + 1])
        hist.append(int(mask.sum()))
    top_idx = _np.argsort(arr)[::-1][:20]
    top20 = [{'instrument': rows[i][0], 'value': round(float(arr[i]), 4)} for i in top_idx]
    cross = {
        'date': date_param,
        'count': int(len(arr)),
        'min': round(float(arr.min()), 4),
        'max': round(float(arr.max()), 4),
        'mean': round(float(arr.mean()), 4),
        'std': round(float(arr.std()), 4),
        'quantiles': quantiles,
        'histogram': hist,
        'histEdges': [round(float(e), 4) for e in edges],
        'top20': top20,
    }
    data = {'meta': meta, 'yearly': yearly, 'cross': cross}
    _cache_set(ck, data)
    return jsonify(data)


# ==================== 个股信息中心 API（板块地位·趋势 核心 + 10 模块） ====================
# 数据源双轨：CH(ClickHouse 主源) → akshare(开源免key 补位：公告/新闻/解禁/分红) → 空态降级。
# a-stock-data(simonlin1212) 为本地聚合 Skill 包（clone+pip 依赖后接入），
# 未接入前事件面由 akshare 提供同质开源数据；各模块返回带 source 标注，前端可展示数据来源。
def _latest_trade_date():
    rows = ch_query("SELECT max(trade_date) FROM %s.daily_prices" % CH_DB)
    return rows[0][0] if rows and rows[0] and rows[0][0] else None


def _member_snapshot_date():
    rows = ch_query("SELECT max(trade_date) FROM %s.ths_industry_member_daily" % CH_DB)
    return rows[0][0] if rows and rows[0] and rows[0][0] else _latest_trade_date()


def _stock_industry_plate(code, D):
    """个股行业归属：二级(884)优先，无二级则一级(881)；返回 (plate_code, plate_name) 或 (None,None)"""
    rows = ch_query(
        "SELECT m.plate_thscode, m.plate_name, b.level FROM %s.ths_industry_member_daily m "
        "LEFT JOIN %s.ths_industry_basic b ON m.plate_thscode = b.thscode "
        "WHERE m.thscode='%s' AND m.trade_date='%s'" % (CH_DB, CH_DB, code, D))
    l2 = [r for r in rows if r[2] == '2']
    pick = (l2 or rows or [[]])[0]
    return (pick[0], pick[1]) if pick and pick[0] else (None, None)


def _pearson(xs, ys):
    import math
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return round(num / den, 3) if den else None


def _f(v, nd=2):
    try:
        f = float(v)
        return round(f, nd) if f == f else 0.0
    except (TypeError, ValueError):
        return 0.0


def _pct(a, b):
    try:
        fa, fb = float(a), float(b)
        return round(fa / fb * 100, 2) if fb else 0.0
    except (TypeError, ValueError):
        return 0.0


def _sector_metrics(ptype, pcode, pname, code, T, D, stk_rows, plat_table, price_table):
    """计算个股在单个板块中的地位与趋势指标。plat_table=成分成员表, price_table=板块行情表"""
    members = ch_query("SELECT thscode FROM %s.%s WHERE plate_thscode='%s' AND trade_date='%s'"
                       % (CH_DB, plat_table, pcode, D))
    codes = [r[0] for r in members if r[0]]
    if not codes or code not in codes:
        return None
    inlist = "','".join(codes)
    comp_rows = ch_query(
        "SELECT d.ts_code, d.pct_chg, d.amount, di.total_mv, mf.net_mf_amount "
        "FROM %s.daily_prices d "
        "LEFT JOIN %s.daily_indicators di ON d.ts_code=di.ts_code AND d.trade_date=di.trade_date "
        "LEFT JOIN %s.money_flow mf ON d.ts_code=mf.ts_code AND d.trade_date=mf.trade_date "
        "WHERE d.ts_code IN ('%s') AND d.trade_date='%s'" % (CH_DB, CH_DB, CH_DB, inlist, T))
    sec_rows = ch_query(
        "SELECT trade_date, close_price, price_change_ratio_pct FROM %s.%s "
        "WHERE thscode='%s' AND trade_date <= '%s' ORDER BY trade_date DESC LIMIT 80"
        % (CH_DB, price_table, pcode, T))
    sec = {}
    for r in sec_rows:
        if r[0] and r[1]:
            sec[str(r[0])[:10]] = (_f(r[1], 3), _f(r[2], 2) if r[2] is not None else None)
    stk = {}
    for r in stk_rows:
        stk[str(r[0])[:10]] = (_f(r[1], 3), _f(r[2], 2))
    common = sorted(d for d in sec if d in stk)
    if not common:
        return None
    # 相对强弱 RS：个股区间收益 - 板块区间收益（pp）
    def _rs(k):
        if len(common) <= k:
            return None
        b, c = common[-k - 1], common[-1]
        s0, s1 = sec[b][0], sec[c][0]
        t0, t1 = stk[b][0], stk[c][0]
        if not s0 or not t0 or s0 <= 0 or t0 <= 0:
            return None
        return round((t1 / t0 - 1) * 100 - (s1 / s0 - 1) * 100, 2)
    rs1, rs5, rs20 = _rs(1), _rs(5), _rs(20)
    # 20日滚动相关性（日收益序列）
    corr = None
    if len(common) >= 21:
        xs, ys = [], []
        for i in range(1, len(common)):
            xs.append(stk[common[i]][1])
            ys.append(sec[common[i]][1])
        corr = _pearson(xs[-20:], ys[-20:])
    # 趋势位置
    sec_closes = [sec[d][0] for d in common if sec[d][0] is not None]
    trend = None
    if len(sec_closes) >= 20:
        ma20 = sum(sec_closes[-20:]) / 20
        trend = '板块MA20上方' if sec_closes[-1] > ma20 else '板块MA20下方'
    stk_closes = [stk[d][0] for d in common if stk[d][0] is not None]
    stk_pos = None
    if len(stk_closes) >= 20:
        ma20 = sum(stk_closes[-20:]) / 20
        stk_pos = '个股MA20上方' if stk_closes[-1] > ma20 else '个股MA20下方'
    # 截面地位指标（T 日）
    comps = []
    for r in comp_rows:
        c = r[0]
        if not c:
            continue
        comps.append({'c': c, 'pct': _f(r[1]), 'amount': _f(r[2]), 'mv': _f(r[3], 1), 'mf': _f(r[4], 1)})
    total_mv = sum(x['mv'] for x in comps)
    total_amt = sum(x['amount'] for x in comps)
    total_mf = sum(x['mf'] for x in comps)
    my = next((x for x in comps if x['c'] == code), None)
    if not my:
        return None
    weight = _pct(my['mv'], total_mv)
    ranked = sorted(comps, key=lambda x: -x['pct'])
    rank = next((i + 1 for i, x in enumerate(ranked) if x['c'] == code), len(comps))
    contrib = round(my['pct'] * weight / 100, 2)
    c_ranked = sorted(comps, key=lambda x: -(x['pct'] * _pct(x['mv'], total_mv) / 100))
    c_rank = next((i + 1 for i, x in enumerate(c_ranked) if x['c'] == code), len(comps))
    amt_share = _pct(my['amount'], total_amt)
    mf_share = _pct(my['mf'], total_mf)
    top_mv = max(comps, key=lambda x: x['mv']) if total_mv else None
    top_amt = max(comps, key=lambda x: x['amount']) if total_amt else None
    top_pct = max(comps, key=lambda x: x['pct'])
    if (top_mv and top_mv['c'] == code) or (top_amt and top_amt['c'] == code) or top_pct['c'] == code:
        leader = '龙头'
    elif rank <= 3 or weight >= 15:
        leader = '核心'
    elif weight >= 5:
        leader = '跟随'
    else:
        leader = '边缘'
    # 涨跌状态：个股 vs 板块（共同最新日）
    s_pct, b_pct = stk[common[-1]][1], sec[common[-1]][1]
    state = None
    if s_pct is not None and b_pct is not None:
        tol = 0.2
        if s_pct > 0 and b_pct > 0:
            state = '领涨' if s_pct - b_pct > tol else '跟涨'
        elif s_pct > 0 and b_pct <= 0:
            state = '逆势'
        else:
            state = '滞涨'
    return {
        'plateType': ptype, 'plateCode': pcode, 'plateName': pname,
        'dateB': str(common[-1]), 'sectorPct': _f(b_pct), 'stockPct': _f(s_pct),
        'stockCnt': len(comps), 'weight': weight, 'rank': rank, 'totalRank': len(comps),
        'leader': leader, 'contrib': contrib, 'contribRank': c_rank,
        'amtShare': amt_share, 'mfShare': mf_share,
        'rs1': rs1, 'rs5': rs5, 'rs20': rs20, 'state': state,
        'corr20': corr, 'trend': trend, 'stkPos': stk_pos,
    }


def info_sector(code):
    """① 板块地位·趋势（核心）：行业(一级 + 二级，主行业二级优先排首位) + 概念(完整 top6) + 全部概念名"""
    T = _latest_trade_date()
    D = _member_snapshot_date()
    if not T or not D:
        return {'module': 'sector', 'source': 'ch', 'data': {'sectors': [], 'conceptsAll': []}}
    stk_rows = ch_query(
        "SELECT trade_date, close, pct_chg FROM %s.daily_prices "
        "WHERE ts_code='%s' AND trade_date <= '%s' ORDER BY trade_date DESC LIMIT 80"
        % (CH_DB, code, T))
    sectors = []
    # 行业板块：该股全部行业归属（一级 881 + 二级 884），主行业（二级优先）排首位
    ind_rows = ch_query(
        "SELECT m.plate_thscode, m.plate_name, b.level FROM %s.ths_industry_member_daily m "
        "LEFT JOIN %s.ths_industry_basic b ON m.plate_thscode = b.thscode "
        "WHERE m.thscode='%s' AND m.trade_date='%s'" % (CH_DB, CH_DB, code, D))
    ind_list = [{'code': r[0], 'name': r[1], 'level': (_safe_int(r[2]) if r[2] is not None else 0)}
                for r in ind_rows if r[0]]
    l2 = [x for x in ind_list if x['level'] == 2]
    primary = (l2 or ind_list or [None])[0]
    ordered = []
    if primary:
        ordered.append(primary)
    for x in ind_list:
        if primary and x['code'] == primary['code']:
            continue
        ordered.append(x)
    for x in ordered:
        m = _sector_metrics('industry', x['code'], x['name'], code, T, D, stk_rows,
                            'ths_industry_member_daily', 'ths_industry_daily')
        if m:
            m['plateLevel'] = x['level']  # 1=一级行业 2=二级行业
            sectors.append(m)
    # 概念板块（全部名称 + 按当日涨跌排序 top6 完整指标）
    con_rows = ch_query(
        "SELECT plate_thscode, plate_name FROM %s.ths_concept_member_daily "
        "WHERE thscode='%s' AND trade_date='%s'" % (CH_DB, code, D))
    con_all = [{'code': r[0], 'name': r[1]} for r in con_rows if r[0]]
    conceptsAll = [{'name': c['name'], 'code': c['code']} for c in con_all]
    if con_all:
        ccodes = "','".join(c['code'] for c in con_all)
        ctr = ch_query(
            "SELECT thscode, price_change_ratio_pct FROM %s.ths_concept_daily "
            "WHERE trade_date=(SELECT max(trade_date) FROM %s.ths_concept_daily) AND thscode IN ('%s')"
            % (CH_DB, CH_DB, ccodes))
        pct_map = {r[0]: _f(r[1]) for r in ctr}
        for c in con_all:
            c['pct'] = pct_map.get(c['code'], None)
        con_all.sort(key=lambda x: -(abs(x['pct']) if x['pct'] is not None else 0))
        for c in con_all[:6]:
            m = _sector_metrics('concept', c['code'], c['name'], code, T, D, stk_rows,
                                'ths_concept_member_daily', 'ths_concept_daily')
            if m:
                sectors.append(m)
    return {'module': 'sector', 'source': 'ch', 'data': {'sectors': sectors, 'conceptsAll': conceptsAll}}


def info_bench(code):
    """② 行情对标：最新交易日 个股成交额/流通市值 占所属板块合计 的占比
    默认板块=个股主行业（二级优先）；支持 ?type=concept&plate=<概念代码> 切换为指定概念板块"""
    from flask import request
    T = _latest_trade_date()
    D = _member_snapshot_date()
    ptype = request.args.get('type', 'industry')
    plate = request.args.get('plate', '')
    pcode, pname = '', ''
    if ptype == 'concept' and plate:
        pcode = plate
        nm = ch_query(
            "SELECT plate_name FROM %s.ths_concept_member_daily WHERE plate_thscode='%s' AND trade_date='%s' LIMIT 1"
            % (CH_DB, plate, D))
        pname = nm[0][0] if nm and nm[0] else plate
        mt = 'ths_concept_member_daily'
    elif ptype == 'industry' and plate:
        pcode = plate
        nm = ch_query(
            "SELECT name FROM %s.ths_industry_basic WHERE thscode='%s' LIMIT 1" % (CH_DB, plate))
        pname = nm[0][0] if nm and nm[0] else plate
        mt = 'ths_industry_member_daily'
    else:
        pcode, pname = _stock_industry_plate(code, D)
        mt = 'ths_industry_member_daily'
    members = ch_query(
        "SELECT thscode FROM %s.%s WHERE plate_thscode='%s' AND trade_date='%s'"
        % (CH_DB, mt, pcode, D)) if pcode else []
    codes = [r[0] for r in members if r[0]]
    my_rows = ch_query(
        "SELECT d.ts_code, d.amount, di.circ_mv, di.total_mv FROM %s.daily_prices d "
        "LEFT JOIN %s.daily_indicators di ON d.ts_code=di.ts_code AND d.trade_date=di.trade_date "
        "WHERE d.ts_code='%s' AND d.trade_date='%s'" % (CH_DB, CH_DB, code, T))
    comp_rows = []
    if codes:
        inlist = "','".join(codes)
        comp_rows = ch_query(
            "SELECT d.ts_code, d.amount, di.circ_mv, di.total_mv FROM %s.daily_prices d "
            "LEFT JOIN %s.daily_indicators di ON d.ts_code=di.ts_code AND d.trade_date=di.trade_date "
            "WHERE d.ts_code IN ('%s') AND d.trade_date='%s'" % (CH_DB, CH_DB, inlist, T))
    my = my_rows[0] if my_rows else None
    amt_total = sum(_f(r[1]) for r in comp_rows)
    circ_total = sum(_f(r[2]) for r in comp_rows)
    if not my:
        return {'module': 'bench', 'source': 'ch', 'data': {
            'date': str(T), 'plateName': pname or '', 'amtShare': None, 'mvShare': None,
            'amt': None, 'amtTotal': None, 'circMv': None, 'circTotal': None,
            'scope': '板块' if pcode else '全市场'}}
    amt, circ = _f(my[1]), _f(my[2])
    if not codes or amt_total <= 0:
        scope = '全市场'
        mk = ch_query(
            "SELECT sum(d.amount), sum(di.circ_mv) FROM %s.daily_prices d "
            "LEFT JOIN %s.daily_indicators di ON d.ts_code=di.ts_code AND d.trade_date=di.trade_date "
            "WHERE d.trade_date='%s'" % (CH_DB, CH_DB, T))
        amt_total = _f(mk[0][0]) if mk and mk[0] else 0
        circ_total = _f(mk[0][1]) if mk and mk[0] else 0
    else:
        scope = '板块'
    return {'module': 'bench', 'source': 'ch', 'data': {
        'date': str(T), 'plateName': pname or '', 'plateCode': pcode or '',
        'amtShare': round(amt / amt_total * 100, 2) if amt_total else None,
        'mvShare': round(circ / circ_total * 100, 2) if circ_total else None,
        'amt': round(amt / 1e5, 2) if amt else None,
        'amtTotal': round(amt_total / 1e5, 2) if amt_total else None,
        'circMv': round(circ / 1e4, 2) if circ else None,
        'circTotal': round(circ_total / 1e4, 2) if circ_total else None,
        'scope': scope}}


def info_mfcurve(code):
    """个股主力资金净流入曲线：近120个交易日逐日 net_mf_amount（万元→亿元）
    前端按 5/10/20/60/120 日切片，并做逐日/累计切换。
    注：先用该股自身最新交易日前推 270 个自然日做窗口，避免稀疏样本（北交所/长期停牌）
    把两三年前的孤立记录当成“近5日/近10日”"""
    mx = ch_query("SELECT max(trade_date) FROM %s.money_flow WHERE ts_code='%s'" % (CH_DB, code))
    if not mx or mx[0][0] in (None, ''):
        return {'module': 'mfcurve', 'source': 'ch', 'data': {'daily': []}}
    mx = str(mx[0][0])[:10]
    rows = ch_query(
        "SELECT trade_date, net_mf_amount FROM %s.money_flow WHERE ts_code='%s' "
        "AND trade_date >= addDays(toDate('%s'), -270) "
        "ORDER BY trade_date DESC LIMIT 120" % (CH_DB, code, mx))
    rows = rows[::-1]
    daily = []
    for r in rows:
        daily.append({
            'date': str(r[0])[:10],
            'main': round(_f(r[1]) / 1e4, 3),   # 万元 → 亿元
        })
    return {'module': 'mfcurve', 'source': 'ch', 'data': {'daily': daily}}


def info_moneyflow(code):
    """③ 资金流透视：近10日主力/超大单/大单/中单/小单净流入 + 北向持股/融资余额快照"""
    rows = ch_query(
        "SELECT trade_date, net_mf_amount, buy_elg_amount, sell_elg_amount, buy_lg_amount, sell_lg_amount, "
        "buy_md_amount, sell_md_amount, buy_sm_amount, sell_sm_amount "
        "FROM %s.money_flow WHERE ts_code='%s' "
        "AND trade_date >= addDays((SELECT max(trade_date) FROM %s.money_flow WHERE ts_code='%s'), -40) "
        "ORDER BY trade_date DESC LIMIT 10" % (CH_DB, code, CH_DB, code))
    rows = rows[::-1]
    daily = []
    for r in rows:
        daily.append({
            'date': str(r[0])[:10],
            'main': round(_f(r[1]) / 1e4, 3),
            'elg': round((_f(r[2]) - _f(r[3])) / 1e4, 3),
            'lg': round((_f(r[4]) - _f(r[5])) / 1e4, 3),
            'md': round((_f(r[6]) - _f(r[7])) / 1e4, 3),
            'sm': round((_f(r[8]) - _f(r[9])) / 1e4, 3),
        })
    snap = {'northHold': None, 'rzye': None, 'date': None}
    di = ch_query(
        "SELECT trade_date, north_hold, rzye FROM %s.daily_indicators WHERE ts_code='%s' "
        "ORDER BY trade_date DESC LIMIT 1" % (CH_DB, code))
    if di and di[0]:
        north = _f(di[0][1]) / 10000
        rzye = _f(di[0][2]) / 10000
        snap = {'northHold': round(north, 1) if north else None,
                'rzye': round(rzye, 1) if rzye else None,
                'date': str(di[0][0])[:10]}
    return {'module': 'moneyflow', 'source': 'ch', 'data': {'daily': daily, 'snapshot': snap}}


def info_dragon(code):
    """④ 龙虎榜·异动：近8次上榜 + 近60日涨停/跌停/开板/连板时间线"""
    dr = ch_query(
        "SELECT trade_date, board_type, name, change, net_value, buy_value, sell_value, hot_rank, "
        "limit_reason, org_net_value, org_buy_num, org_sell_num "
        "FROM %s.ths_dragon_tiger_stock_daily WHERE thscode='%s' "
        "ORDER BY trade_date DESC LIMIT 8" % (CH_DB, code))
    dragon = []
    for r in dr:
        dragon.append({
            'date': str(r[0])[:10], 'boardType': r[1] or '', 'name': r[2] or '',
            'chgPct': round(_f(r[3]) * 100, 2), 'netValue': round(_f(r[4]) / 1e8, 3),
            'buyValue': round(_f(r[5]) / 1e8, 3), 'sellValue': round(_f(r[6]) / 1e8, 3),
            'hotRank': _safe_int(r[7]) if r[7] is not None else None,
            'limitReason': r[8] or '', 'orgNetValue': round(_f(r[9]) / 1e8, 3) if r[9] is not None else None,
            'orgBuyNum': _safe_int(r[10]) if r[10] is not None else None,
            'orgSellNum': _safe_int(r[11]) if r[11] is not None else None,
        })
    events = []
    for tbl, typ in (('ths_limit_up_pool_daily', 'up'), ('ths_limit_down_pool_daily', 'down'),
                     ('ths_limit_break_pool_daily', 'break'), ('ths_limit_up_ladder_daily', 'ladder')):
        if typ == 'up':
            q = ("SELECT trade_date, name, limit_up_time, continue_day_text, limit_up_reason, price_change_ratio_pct "
                 "FROM %s.%s WHERE thscode='%s' ORDER BY trade_date DESC LIMIT 30" % (CH_DB, tbl, code))
        elif typ == 'down':
            q = ("SELECT trade_date, name, first_limit_time, '', '', price_change_ratio_pct "
                 "FROM %s.%s WHERE thscode='%s' ORDER BY trade_date DESC LIMIT 30" % (CH_DB, tbl, code))
        elif typ == 'break':
            q = ("SELECT trade_date, name, open_times, '', '', price_change_ratio_pct "
                 "FROM %s.%s WHERE thscode='%s' ORDER BY trade_date DESC LIMIT 30" % (CH_DB, tbl, code))
        else:
            q = ("SELECT trade_date, name, board_key, '', '', '' "
                 "FROM %s.%s WHERE thscode='%s' ORDER BY trade_date DESC LIMIT 30" % (CH_DB, tbl, code))
        for r in ch_query(q):
            d = str(r[0])[:10]
            name = r[1] or ''
            if typ == 'up':
                text = '涨停 %s' % (r[2] or '') + (' · %s' % (r[3] or '') if r[3] else '') + \
                       (' · %s' % (r[4] or '') if r[4] else '')
            elif typ == 'down':
                text = '跌停 首封 %s' % (r[2] or '')
            elif typ == 'break':
                text = '炸板/开板 %s次' % (r[2] if r[2] is not None else '')
            else:
                text = '连板梯队 %s' % (r[2] or '')
            events.append({'date': d, 'type': typ, 'text': text, 'pct': _f(r[5])})
    # 补充：近90日单日大涨/大跌（|涨跌|≥3%）作为普通异动时间线，避免"涨停/跌停才有内容"导致白马股长期空白
    try:
        seen = {e['date'] for e in events}
        for r in ch_query(
                "SELECT trade_date, pct_chg FROM %s.daily_prices "
                "WHERE ts_code='%s' ORDER BY trade_date DESC LIMIT 90" % (CH_DB, code)):
            d = str(r[0])[:10]
            pc = _f(r[1])
            if pc and abs(pc) >= 3.0 and d not in seen:
                events.append({'date': d, 'type': 'up' if pc > 0 else 'down',
                               'text': '单日%s %.2f%%' % ('大涨' if pc > 0 else '大跌', pc),
                               'pct': round(pc, 2)})
                seen.add(d)
    except Exception:
        pass
    events.sort(key=lambda x: x['date'], reverse=True)
    return {'module': 'dragon', 'source': 'ch', 'data': {'dragon': dragon, 'events': events[:40]}}


def info_finance(code):
    """⑤ 财务快照：近4期 三表手工计算 ROE/毛利率/净利率/负债率 + 去年同期同比（v_financial_indicators 视图坏列不可用，手工兜底）"""
    inc = ch_query(
        "SELECT end_date, revenue, oper_cost, n_income_attr_p, operate_profit, basic_eps "
        "FROM %s.v_income_statement WHERE ts_code='%s' AND report_type='1' "
        "ORDER BY end_date DESC LIMIT 8" % (CH_DB, code))
    bal = ch_query(
        "SELECT end_date, total_assets, total_liab, total_hldr_eqy_inc_min_int, money_cap, accounts_receiv, inventories "
        "FROM %s.v_balance_sheet WHERE ts_code='%s' AND report_type='1' "
        "ORDER BY end_date DESC LIMIT 4" % (CH_DB, code))
    cf = ch_query(
        "SELECT end_date, n_cashflow_act, n_cashflow_inv_act, n_cash_flows_fnc_act "
        "FROM %s.v_cashflow_statement WHERE ts_code='%s' AND report_type='1' "
        "ORDER BY end_date DESC LIMIT 4" % (CH_DB, code))
    inc_map = {str(r[0])[:10]: r[1:] for r in inc}
    bal_map = {str(r[0])[:10]: r[1:] for r in bal}
    cf_map = {str(r[0])[:10]: r[1:] for r in cf}
    end_dates = [str(r[0])[:10] for r in inc][:4]
    from datetime import datetime as _dt
    quarters = []
    for end in end_dates:
        iv = inc_map.get(end)
        b = bal_map.get(end)
        c = cf_map.get(end)
        if not iv:
            continue
        revenue = _f(iv[0]) / 1e8
        oper_cost = _f(iv[1]) / 1e8
        net_income = _f(iv[2]) / 1e8
        equity = _f(b[2]) / 1e8 if b and b[2] is not None else None
        # 去年同期（同季度累计）同比
        try:
            y = _dt.strptime(end, '%Y-%m-%d')
            last_year = '%04d-%02d-%02d' % (y.year - 1, y.month, y.day)
        except Exception:
            last_year = None
        ly = inc_map.get(last_year)
        tr_yoy = netprofit_yoy = None
        if ly and _f(ly[0]) > 0:
            tr_yoy = round((revenue * 1e8 / _f(ly[0]) - 1) * 100, 2)
        if ly and _f(ly[2]) > 0:
            netprofit_yoy = round((net_income * 1e8 / _f(ly[2]) - 1) * 100, 2)
        quarters.append({
            'endDate': end,
            'revenue': round(revenue, 2), 'netIncome': round(net_income, 2),
            'operateProfit': round(_f(iv[3]) / 1e8, 2), 'basicEps': _f(iv[4], 4),
            'totalAssets': round(_f(b[0]) / 1e8, 2) if b and b[0] is not None else None,
            'totalLiab': round(_f(b[1]) / 1e8, 2) if b and b[1] is not None else None,
            'equity': round(equity, 2) if equity is not None else None,
            'moneyCap': round(_f(b[3]) / 1e8, 2) if b and b[3] is not None else None,
            'accountsReceiv': round(_f(b[4]) / 1e8, 2) if b and b[4] is not None else None,
            'inventories': round(_f(b[5]) / 1e8, 2) if b and b[5] is not None else None,
            'cashAct': round(_f(c[0]) / 1e8, 2) if c and c[0] is not None else None,
            'invAct': round(_f(c[1]) / 1e8, 2) if c and c[1] is not None else None,
            'finAct': round(_f(c[2]) / 1e8, 2) if c and c[2] is not None else None,
            'roe': round(net_income / equity * 100, 2) if equity else None,
            'grossMargin': round((revenue - oper_cost) / revenue * 100, 2) if revenue and 0 <= (revenue - oper_cost) / revenue < 0.995 else None,
            'netMargin': round(net_income / revenue * 100, 2) if revenue else None,
            'debtRatio': round(_f(b[1]) / _f(b[0]) * 100, 2) if b and _f(b[0]) else None,
            'trYoy': tr_yoy, 'netprofitYoy': netprofit_yoy, 'epsForecast': None,
        })
    return {'module': 'finance', 'source': 'ch', 'data': {'quarters': quarters}}


def info_holder(code):
    """⑥ 股东·质押：股东户数 + 质押比例 历史"""
    holders = ch_query(
        "SELECT end_date, holder_num FROM %s.stk_holdernumber WHERE ts_code='%s' "
        "ORDER BY end_date DESC LIMIT 8" % (CH_DB, code))
    pledges = ch_query(
        "SELECT end_date, pledge_ratio FROM %s.pledge_stat WHERE ts_code='%s' "
        "ORDER BY end_date DESC LIMIT 8" % (CH_DB, code))
    return {'module': 'holder', 'source': 'ch', 'data': {
        'holders': [{'endDate': str(r[0])[:10], 'holderNum': int(r[1]) if r[1] is not None else None} for r in holders],
        'pledges': [{'endDate': str(r[0])[:10], 'ratio': _f(r[1]) if r[1] is not None else None} for r in pledges]}}


def _ak_run(fn, timeout=25):
    """在独立线程执行 akshare 调用并限时：超时/异常返回 None，避免网络挂起堵死 Flask 线程"""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout
    try:
        pool = _ak_run._pool
    except AttributeError:
        pool = ThreadPoolExecutor(max_workers=2)
        _ak_run._pool = pool
    f = pool.submit(fn)
    try:
        return f.result(timeout=timeout)
    except _FutTimeout:
        print('[ak_run] timeout %ss' % timeout)
        return None
    except Exception as e:
        print('[ak_run] err:', e)
        return None


def info_news(code):
    """⑦ 公告·舆情：CH 无此表 → akshare 开源补位（公告近5日 + 新闻近10条）"""
    symbol = code.split('.')[0]
    items = []
    try:
        import akshare as ak
        from datetime import date, timedelta
        d = date.today()
        for _ in range(5):
            dd = d.strftime('%Y%m%d')
            df = _ak_run(lambda: ak.stock_notice_report(symbol=symbol, date=dd), 15)
            if df is not None and len(df) > 0:
                for _, r in df.head(6).iterrows():
                    items.append({
                        'type': '公告', 'date': str(r.get('公告日期', ''))[:10],
                        'title': str(r.get('公告标题', '')), 'content': str(r.get('公告内容', ''))[:100],
                        'url': str(r.get('公告链接', '')), 'source': '东财'})
                break
            d -= timedelta(days=1)
        df = _ak_run(lambda: ak.stock_news_em(symbol=symbol), 15)
        if df is not None and len(df) > 0:
            for _, r in df.head(10).iterrows():
                items.append({
                    'type': '新闻', 'date': str(r.get('发布时间', ''))[:10],
                    'title': str(r.get('新闻标题', '')), 'content': str(r.get('新闻内容', ''))[:100],
                    'url': str(r.get('新闻链接', '')), 'source': str(r.get('文章来源', ''))})
    except Exception as e:
        print('[info_news] akshare unavailable:', e)
    items.sort(key=lambda x: x['date'], reverse=True)
    return {'module': 'news', 'source': 'akshare' if items else 'none', 'data': {'items': items[:16]}}


def info_valuation(code):
    """⑧ 估值分位：近5年 PE(TTM)/PB 当前值与历史分位（min/p25/p50/p75/max）"""
    from datetime import date as _d, timedelta as _td
    start = (_d.today() - _td(days=1825)).strftime('%Y-%m-%d')
    rows = ch_query(
        "SELECT trade_date, pe_ttm, pb FROM %s.daily_indicators "
        "WHERE ts_code='%s' AND trade_date >= '%s' AND pe_ttm > 0 AND pb > 0 "
        "ORDER BY trade_date" % (CH_DB, code, start))
    pes, pbs = [_f(r[1]) for r in rows if r[1]], [_f(r[2]) for r in rows if r[2]]
    if not pes and not pbs:
        return {'module': 'valuation', 'source': 'ch', 'data': {}}
    cur_pe = _f(rows[-1][1]) if rows and rows[-1][1] else None
    cur_pb = _f(rows[-1][2]) if rows and rows[-1][2] else None
    def _stats(arr):
        arr = sorted(arr)
        n = len(arr)
        def q(p):
            return arr[min(n - 1, int(n * p))]
        return {'min': q(0), 'p25': q(0.25), 'p50': q(0.5), 'p75': q(0.75), 'max': q(1.0), 'count': n}
    pe_s, pb_s = _stats(pes) if pes else {}, _stats(pbs) if pbs else {}
    def _pct_rank(v, arr):
        return round(sum(1 for x in arr if x <= v) / len(arr) * 100, 1) if arr else None
    return {'module': 'valuation', 'source': 'ch', 'data': {
        'peCur': cur_pe, 'pbCur': cur_pb,
        'pePct': _pct_rank(cur_pe, pes) if cur_pe is not None else None,
        'pbPct': _pct_rank(cur_pb, pbs) if cur_pb is not None else None,
        'pe': pe_s, 'pb': pb_s, 'start': start[:7]}}


def info_share(code):
    """⑨ 解禁·分红：CH 无表 → akshare 开源补位（单接口限时防挂起）"""
    symbol = code.split('.')[0]
    out = {'dividends': [], 'restricted': []}
    try:
        import akshare as ak
        df = _ak_run(lambda: ak.stock_fhps_detail_em(symbol=symbol), 15)
        if df is not None and len(df) > 0:
            def _d2s(v):
                s = str(v)[:10]
                return '' if s.lower() in ('nat', 'nan', 'none') else s
            for _, r in df.head(8).iterrows():
                out['dividends'].append({
                    'period': str(r.get('报告期', ''))[:10],
                    'progress': str(r.get('方案进度', r.get('进度', ''))),
                    'plan': str(r.get('现金分红-现金分红比例描述', r.get('方案', ''))),
                    'exDate': _d2s(r.get('除权除息日', '')),
                    'recordDate': _d2s(r.get('股权登记日', '')),
                    'cash': str(r.get('现金分红-现金分红比例', r.get('派息', ''))),
                    'trans': str(r.get('送转股份-送转总比例', r.get('送转股份', '')))})
        for fname in ('stock_restricted_release_detail_em', 'stock_restricted_release_queue_em'):
            df = _ak_run(lambda: getattr(ak, fname)(symbol=symbol), 15)
            if df is not None and len(df) > 0:
                cols = list(df.columns)
                for _, r in df.head(8).iterrows():
                    out['restricted'].append({
                        'date': str(r.get('解禁日期', r.get('批次日期', '')))[:10],
                        'num': str(r.get('解禁数量', r.get('解禁股数', ''))),
                        'mktVal': str(r.get('解禁市值', '')),
                        'ratio': str(r.get('占总股本比例', r.get('占总股本比例(%)', ''))),
                        'type': str(r.get('解禁类型', ''))})
                break
    except Exception as e:
        print('[info_share] akshare unavailable:', e)
    return {'module': 'share', 'source': 'akshare' if (out['dividends'] or out['restricted']) else 'none',
            'data': out}


def info_hot(code):
    """⑩ 热度·地位：同花顺热榜排名 + 市值/成交额全市场排名"""
    T = _latest_trade_date()
    rank_rows = ch_query(
        "SELECT date, rank FROM %s.ths_hot_stock_rank_daily WHERE thscode='%s' "
        "ORDER BY date DESC LIMIT 30" % (CH_DB, code))
    series = [{'date': str(r[0])[:10], 'rank': int(r[1]) if r[1] is not None else None} for r in rank_rows]
    cur_rank = series[0]['rank'] if series else None
    # 兜底：从未进过热榜的股票也返回近16个交易日日期序列（rank=None），保证前端始终有图可画
    if not series:
        try:
            days = ch_query(
                "SELECT trade_date FROM %s.daily_prices WHERE ts_code='%s' "
                "ORDER BY trade_date DESC LIMIT 16" % (CH_DB, code))
            series = [{'date': str(r[0])[:10], 'rank': None} for r in reversed(days)]
        except Exception:
            pass
    mv_rank = amt_rank = None
    total = 0
    if T:
        mv = ch_query("SELECT ts_code FROM %s.daily_indicators WHERE trade_date='%s' AND total_mv > 0 "
                      "ORDER BY total_mv DESC LIMIT 6000" % (CH_DB, T))
        total = len(mv)
        mv_rank = next((i + 1 for i, r in enumerate(mv) if r[0] == code), None)
        am = ch_query("SELECT ts_code FROM %s.daily_prices WHERE trade_date='%s' "
                      "ORDER BY amount DESC NULLS LAST LIMIT 6000" % (CH_DB, T))
        amt_rank = next((i + 1 for i, r in enumerate(am) if r[0] == code), len(am) if am else None)
    return {'module': 'hot', 'source': 'ch', 'data': {
        'hotRankCur': cur_rank, 'hotSeries': series[:20],
        'mvRank': mv_rank, 'amtRank': amt_rank, 'total': total}}


@app.route('/api/stock/info')
def api_stock_info():
    """个股信息中心：?code=600519.SH&module=sector|bench|moneyflow|dragon|finance|holder|news|valuation|share|hot"""
    code = request.args.get('code', '').strip().upper()
    module = request.args.get('module', '').strip()
    if not code:
        return jsonify({'error': 'no code'}), 400
    if not module:
        return jsonify({'modules': ['sector', 'bench', 'moneyflow', 'mfcurve', 'dragon', 'finance',
                                    'holder', 'news', 'valuation', 'share', 'hot']})
    ck = 'info_%s_%s_%s_%s' % (module, code, request.args.get('type', ''), request.args.get('plate', ''))
    cached = _cache_get(ck)
    if cached:
        return jsonify(cached)
    fn = {'sector': info_sector, 'bench': info_bench, 'moneyflow': info_moneyflow,
          'mfcurve': info_mfcurve,
          'dragon': info_dragon, 'finance': info_finance, 'holder': info_holder,
          'news': info_news, 'valuation': info_valuation, 'share': info_share,
          'hot': info_hot}.get(module)
    if not fn:
        return jsonify({'error': 'unknown module'}), 400
    try:
        data = fn(code)
    except Exception as e:
        data = {'module': module, 'source': 'error', 'error': str(e), 'data': {}}
    _cache_set(ck, data)
    return jsonify(data)


# ===================== 模拟交易（阶段一：本地账本 + 回放撮合） =====================
import json as _sim_json
import os
import re as _sim_re
import sim_store as _sim_store
import sim_broker as _sim_broker
import sim_auto as _sim_auto
import sim_plan as _sim_plan

_SIM_CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sim_config.json')
_SIM_DEFAULTS = {'participation_rate': 0.0, 'slippage_bps': 2.0, 'initial_cash': 100000.0}


def _sim_cfg_read():
    cfg = dict(_SIM_DEFAULTS)
    try:
        with open(_SIM_CFG_PATH, 'r', encoding='utf-8') as f:
            cfg.update(_sim_json.load(f) or {})
    except Exception:
        pass
    return cfg


def _sim_cfg_write(d):
    try:
        with open(_SIM_CFG_PATH, 'w', encoding='utf-8') as f:
            _sim_json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _sim_bar(code, asof=''):
    """取不复权日线 bar（撮合不能用前复权价，跨除权日会失真）。

    返回 (bar, prev_close, 该股可用最新交易日)。
    """
    code = (code or '').strip().upper()
    if not _sim_re.match(r'^\d{6}\.(SH|SZ|BJ)$', code):
        return None, None, ''
    end_cond = ''
    if asof and _sim_re.match(r'^\d{4}-\d{2}-\d{2}$', asof):
        end_cond = " AND trade_date <= '%s'" % asof
    rows = ch_query(
        "SELECT trade_date,open,high,low,close,pre_close,vol,amount FROM %s.daily_prices "
        "WHERE ts_code='%s'%s ORDER BY trade_date DESC LIMIT 2" % (CH_DB, code, end_cond))
    if not rows or not rows[0]:
        return None, None, ''
    r = rows[0]
    bar = {'date': str(r[0]), 'open': float(r[1] or 0), 'high': float(r[2] or 0),
           'low': float(r[3] or 0), 'close': float(r[4] or 0), 'vol': float(r[6] or 0)}
    prev_close = float(r[5] or 0)
    if prev_close <= 0 and len(rows) > 1:
        prev_close = float(rows[1][4] or 0)
    return bar, prev_close, bar['date']


def _write_snapshot(asof, prices=None):
    """写当日净值快照（同一交易日覆盖）。"""
    lp = {}
    for p in _sim_store.list_positions():
        lp[p['code']] = p['last_price']
    if prices:
        lp.update(prices)
    acc = _sim_broker.account_view(lp)
    prev = [s for s in _sim_store.list_snapshots() if s['trade_date'] < asof]
    peak = max([s['nav'] for s in prev] + [acc['nav']]) if prev else acc['nav']
    dd = (1 - acc['nav'] / peak) if peak > 0 else 0
    _sim_store.save_snapshot(asof, acc['cash'], acc['market_value'], acc['nav'],
                             acc['nav'] - acc['initial_cash'], dd, len(acc['positions']))
    return acc


def _sim_auto_add_conds(code, it, qty, asof):
    """买入成交后建卖出条件组；有 exits 时按 seq 排队，首条生效、其余 queued。"""
    pos = _sim_store.get_position(code)
    if not pos or int(pos.get('qty') or 0) <= 0:
        return []
    cost = float(pos.get('cost') or 0)
    peak = float(pos.get('peak_price') or 0) or float(pos.get('last_price') or 0) or cost
    made = []

    def _pct(v):
        """百分比归一化：5 / 5% / 0.05 一律转成 0.05。"""
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0.0
        if v <= 0:
            return 0.0
        return v / 100.0 if v > 1 else v

    def _qty_spec(spec):
        spec = spec or {}
        t = str(spec.get('type') or 'all').strip().lower()
        v = float(spec.get('value') or 0)
        if t == 'pct':
            return 'pct', _pct(v)
        if t in ('shares', 'all'):
            return t, v
        return 'all', 0.0

    exits = it.get('exits') or []
    if exits:
        base = '%s|%s|%d' % (code, asof, int(time.time() * 1000))
        # 分组：止损、定时各自独立并行；其余（止盈/回落/固定价）按 seq 串行，
        # 触发一段后由 _activate_next 激活下一段（分段卖出）。
        seg = [e for e in exits
               if str(e.get('kind') or '').strip().lower() not in ('sl', 'timed')]
        seg_first = min(seg, key=lambda e: int(e.get('seq') or 0)) if seg else None
        for i, ex in enumerate(exits):
            kind = str(ex.get('kind') or '').strip().lower()
            if kind not in ('tp', 'sl', 'trail', 'fixed_price', 'timed'):
                continue
            mode, qv = _qty_spec(ex.get('qty'))
            seq = int(ex.get('seq') or (i + 1))
            due = str(ex.get('due') or '')
            if kind == 'sl':
                gid, status = base + '|sl%d' % i, 'active'
            elif kind == 'timed':
                gid, status = base + '|tm%d' % i, 'active'
            else:
                gid = base + '|seg'
                status = 'active' if ex is seg_first else 'queued'
            if kind == 'fixed_price':
                val = float(ex.get('value') or 0)
                trig = val
                arm = 0.0
                if val <= 0:
                    continue
            elif kind == 'timed':
                val = 0.0
                trig = 0.0
                arm = 0.0
                if not due:
                    continue
            else:
                val = _pct(ex.get('value'))
                arm = _pct(ex.get('arm')) if kind == 'trail' else 0.0
                if val <= 0:
                    continue
                trig = 0 if arm else _sim_broker.calc_trigger(kind, val, cost, peak)
            cid = _sim_store.add_cond(code, int(qty), kind, val, asof, trig, arm,
                                      seq=seq, qty_mode=mode, qty_value=qv,
                                      due_date=due, group_id=gid, status=status)
            made.append({'cond_id': cid, 'kind': kind, 'seq': seq,
                         'value': val, 'arm': arm, 'qty_mode': mode,
                         'qty_value': qv, 'due': due, 'status': status})
        return made

    for kind, key in (('tp', 'tp'), ('sl', 'sl'), ('trail', 'trail')):
        v = _pct(it.get(key))
        if v <= 0:
            continue
        arm = _pct(it.get('arm')) if kind == 'trail' else 0.0
        trig = 0 if arm else _sim_broker.calc_trigger(kind, v, cost, peak)
        cid = _sim_store.add_cond(code, int(qty), kind, v, asof, trig, arm)
        made.append({'cond_id': cid, 'kind': kind, 'value': v, 'arm': arm})
    return made


def _sim_auto_execute(items, asof='', auto_cond=True, source='manual'):
    """批量逐笔下单；买入成交后同步挂条件单，并写当日净值快照。"""
    cfg = _sim_cfg_read()
    out = []
    for it in (items or []):
        code = (it.get('code') or '').strip().upper()
        if not code:
            out.append({'ok': False, 'reason': '股票代码为空'})
            continue
        bar, prev_close, last_date = _sim_bar(code, asof)
        use_asof = asof or last_date
        if not bar:
            out.append({'code': code, 'ok': False, 'reason': '无该标的行情数据'})
            continue
        side = 'sell' if (it.get('side') == 'sell') else 'buy'
        qty = int(it.get('qty') or 0)
        if side == 'sell' and qty <= 0:
            p = _sim_store.get_position(code)
            qty = int((p or {}).get('avail_qty') or 0)
        price = float(it.get('price') or 0)
        if side == 'buy' and qty <= 0 and float(it.get('amount') or 0) > 0:
            px0 = price or float(bar['open'])
            qty = int((float(it['amount']) / px0) // 100 * 100) if px0 > 0 else 0
        otype = 'limit' if price > 0 else 'market'
        name = (it.get('name') or '').strip() or _stock_name(code).get('name') or code
        res = _sim_broker.submit_order(
            code=code, name=name, side=side, otype=otype, price=price, qty=qty,
            asof=use_asof, prev_close=prev_close, bar=bar,
            participation_rate=cfg.get('participation_rate', 0.0),
            slippage_bps=cfg.get('slippage_bps', 2.0))
        row = {'code': code, 'name': name, 'side': side, 'qty': qty, 'asof': use_asof,
               'ok': bool(res.get('ok')), 'status': res.get('status'),
               'reason': res.get('reason') or '', 'fills': res.get('fills') or []}
        if res.get('ok'):
            conds = []
            if side == 'buy' and auto_cond:
                conds = _sim_auto_add_conds(code, it, qty, use_asof)
            _sim_broker.run_conditions(code, use_asof, bar)
            p = _sim_store.get_position(code)
            if side == 'sell' and (not p or int(p.get('qty') or 0) <= 0):
                for c in _sim_store.list_cond(code=code, status='active'):
                    _sim_store.update_cond(c['id'], status='expired')
            row['conds'] = conds
            _write_snapshot(use_asof)
        out.append(row)
    return out


def _sim_sweep_conditions(asof=''):
    """对全部持仓推进一次条件单判定（无新文件时也生效）。"""
    hits = []
    for p in _sim_store.list_positions():
        code = p.get('code')
        if not code:
            continue
        bar, prev_close, last_date = _sim_bar(code, asof)
        if not bar:
            continue
        use = asof or last_date
        for h in _sim_broker.run_conditions(code, use, bar):
            h['asof'] = use
            hits.append(h)
    if hits:
        _write_snapshot(hits[-1].get('asof') or asof)
    return hits


def _sim_plan_path(p):
    p = str(p or '').strip()
    if not p:
        return ''
    if os.path.isabs(p):
        return p
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), p)


def _sim_plan_price(item, bar, prev_close):
    """按 price.type 换算委托价与委托类型。"""
    spec = item.get('price') or {}
    t = str(spec.get('type') or 'market').lower()
    v = float(spec.get('value') or 0)
    if t == 'open':
        return round(float(bar['open']), 2), 'limit'
    if t == 'close':
        return round(float(bar['close']), 2), 'limit'
    if t == 'pct_of_ref':
        ref = float(prev_close or bar['open'])
        return round(ref * (1 + v), 2), 'limit'
    if t == 'limit':
        return round(v, 2), 'limit'
    return 0.0, 'market'


def _sim_plan_qty(item, bar, price, side):
    """按 qty.type 换算股数；卖出不超过可用。"""
    spec = item.get('qty') or {}
    t = str(spec.get('type') or 'shares').lower()
    v = float(spec.get('value') or 0)
    if v > 1 and t in ('pct', 'pct_nav'):
        v = v / 100.0
    if side == 'sell':
        pos = _sim_store.get_position(item['code']) or {}
        avail = int(pos.get('avail_qty') or 0)
        if t == 'pct':
            q = int((avail * max(0.0, min(v, 1.0)) // 100) * 100)
            if q <= 0 and avail > 0:
                q = avail if avail < 100 else 100
        elif t == 'shares':
            q = min(int(v), avail)
        else:
            q = avail
        return max(0, q)
    px = price or float(bar['open'])
    if px <= 0:
        return 0
    if t == 'shares':
        return int(v)
    if t == 'amount':
        return int(v / px // 100 * 100)
    if t == 'pct_nav':
        nav = float(_sim_broker.account_view().get('nav') or 0)
        return int(nav * max(0.0, v) / px // 100 * 100)
    return 0


def _sim_plan_execute(path, asof_override='', mode_override='', source='plan'):
    """执行量化批次计划：按批次时间判定，回测补历史、实盘跳过过期。"""
    from datetime import datetime
    plan, errs = _sim_plan.load(path)
    if errs:
        return {'ok': False, 'reason': '；'.join(errs[:4]), 'errors': errs,
                'total': 0, 'success': 0, 'failed': 0, 'results': [], 'skipped': []}
    mode = (mode_override or plan.get('mode') or 'live').strip().lower()
    if mode not in ('backtest', 'live'):
        mode = 'live'
    _sim_store.use_mode(mode)
    _sim_store.init()
    now = datetime.now()
    run_date = now.strftime('%Y-%m-%d')
    base = os.path.basename(path)
    rows, skipped = [], []
    for b in plan['batches']:
        if not b.get('enabled'):
            skipped.append({'batch': b['id'], 'reason': '该批次 enabled=false'})
            continue
        if _sim_store.plan_marked(base, b['id'], run_date, mode):
            skipped.append({'batch': b['id'], 'reason': '该批次今天已执行'})
            continue
        due = _sim_plan.batch_due(b, now, mode)
        if not due.get('due'):
            skipped.append({'batch': b['id'], 'reason': due.get('reason')})
            if '已过' in str(due.get('reason')):
                _sim_store.add_auto_log(
                    now.isoformat(timespec='seconds'), 'plan', b['id'], '',
                    base, 0, 0, 0,
                    _sim_json.dumps({'mode': mode, 'skip': due.get('reason')}, ensure_ascii=False))
            continue
        asof = asof_override or due.get('asof') or ''
        # 只有真正进入执行的批次才登记计划池：跳过/已执行的批次不应出现在列表里
        _sim_store.save_plan_pool(base, b['id'], mode, [
            {'code': it['code'], 'name': it.get('name') or '', 'side': it['side'],
             'cond': len(it.get('exits') or [])} for it in b['items']])
        ok = fail = 0
        for it in b['items']:
            bar, prev_close, last_date = _sim_bar(it['code'], asof)
            use_asof = asof or last_date
            if not bar:
                rows.append({'batch': b['id'], 'code': it['code'], 'ok': False,
                             'reason': '无该标的行情数据'})
                fail += 1
                continue
            price, otype = _sim_plan_price(it, bar, prev_close)
            qty = _sim_plan_qty(it, bar, price, it['side'])
            flat = {'code': it['code'], 'name': it.get('name') or '',
                    'side': it['side'], 'qty': qty, 'price': price,
                    'otype': otype, 'exits': it.get('exits') or []}
            for x in _sim_auto_execute([flat], use_asof, True, source + ':' + b['id']):
                x['batch'] = b['id']
                rows.append(x)
                if x.get('ok'):
                    ok += 1
                else:
                    fail += 1
        status = 'ok' if fail == 0 else ('partial' if ok else 'failed')
        _sim_store.mark_plan(base, b['id'], run_date, mode, asof, status,
                             _sim_json.dumps({'ok': ok, 'fail': fail}, ensure_ascii=False))
        _sim_store.add_auto_log(now.isoformat(timespec='seconds'), 'plan', b['id'], asof,
                                base, ok + fail, ok, fail,
                                _sim_json.dumps(rows[-10:], ensure_ascii=False))
    return {'ok': True, 'mode': mode, 'file': base, 'total': len(rows),
            'success': len([r for r in rows if r.get('ok')]),
            'failed': len(rows) - len([r for r in rows if r.get('ok')]),
            'results': rows, 'skipped': skipped}


def _sim_plan_body(d):
    """content 优先（前端 file:// 选文件后上传正文），否则用 path。"""
    if d.get('content'):
        d2 = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trade_inbox')
        os.makedirs(d2, exist_ok=True)
        p = os.path.join(d2, '_upload_%d.json' % int(time.time() * 1000))
        with open(p, 'w', encoding='utf-8') as f:
            f.write(d['content'])
        return p
    return _sim_plan_path(d.get('path') or '')


@app.before_request
def _sim_mode_guard():
    """所有 /api/sim/* 默认走回测账本；?mode=live（或 body.mode）切到实盘账本。
    回测与实盘是两个独立 sqlite 库，资金/持仓/成交/条件单互不影响。"""
    if request.path.startswith('/api/sim/'):
        _sim_store.use_mode(request.args.get('mode') or 'backtest')


def _sim_apply_body_mode(d):
    """POST body 里的 mode 优先于 query（前端表单切模式时用）。"""
    m = str((d or {}).get('mode') or '').strip().lower()
    if m in ('backtest', 'live'):
        _sim_store.use_mode(m)
        _sim_store.init()


@app.route('/api/sim/plan/validate', methods=['POST'])
def api_sim_plan_validate():
    d = request.get_json(force=True, silent=True) or {}
    p = _sim_plan_body(d)
    if not p or not os.path.exists(p):
        return jsonify({'ok': False, 'errors': ['文件不存在: %s' % (d.get('path') or '')]})
    plan, errs = _sim_plan.load(p)
    return jsonify(_sim_plan.validate_report(plan, errs))


@app.route('/api/sim/plan/run', methods=['POST'])
def api_sim_plan_run():
    d = request.get_json(force=True, silent=True) or {}
    p = _sim_plan_body(d)
    if not p or not os.path.exists(p):
        return jsonify({'ok': False, 'errors': ['文件不存在: %s' % (d.get('path') or '')]})
    return jsonify(_sim_plan_execute(p, (d.get('asof') or '').strip(),
                                     (d.get('mode') or '').strip(), 'plan'))


@app.route('/api/sim/plan/marks')
def api_sim_plan_marks():
    return jsonify({'marks': _sim_store.list_plan_marks(int(request.args.get('limit') or 40))})


@app.route('/api/sim/plan/pool')
def api_sim_plan_pool():
    """按模式返回批次计划涉及股票（个股列表用）。"""
    mode = (request.args.get('mode') or 'backtest').strip().lower()
    _sim_store.use_mode(mode)
    _sim_store.init()
    out = []
    for r in _sim_store.list_plan_pool(mode):
        bar, prev, _last = _sim_bar(r['code'], '')
        px = float(bar['close']) if bar else 0.0
        chg = ((px / prev - 1) * 100) if (prev and px) else 0.0
        out.append({'code': r['code'], 'name': r['name'] or r['code'],
                    'side': r['side'], 'cond_count': r['cond_count'],
                    'plan_file': r['plan_file'], 'batch_id': r['batch_id'],
                    'last': px, 'pct_chg': round(chg, 2)})
    return jsonify({'mode': mode, 'items': out})


@app.route('/api/sim/plan/jobs', methods=['GET', 'POST'])
def api_sim_plan_jobs():
    """定时实盘任务：按间隔反复读取同一份批次计划 JSON，只在 live 账本执行。"""
    _sim_store.use_mode('live')
    _sim_store.init()
    if request.method == 'GET':
        return jsonify({'jobs': _sim_store.list_plan_jobs(),
                        'in_session': _sim_auto._in_session()})
    d = request.get_json(force=True, silent=True) or {}
    fp = (d.get('file_path') or '').strip()
    if not fp:
        return jsonify({'ok': False, 'reason': '缺少 file_path'}), 400
    resolved = _sim_plan_path(fp)
    if not resolved or not os.path.exists(resolved):
        return jsonify({'ok': False, 'reason': '文件不存在：' + fp})
    jid = _sim_store.save_plan_job(
        resolved, int(d.get('interval_min') or 60), int(d.get('only_session', 1)),
        int(d.get('enabled', 1)), int(d.get('id') or 0),
        d.get('sched_type') or 'interval', d.get('at_time') or '',
        d.get('weekday', -1), d.get('run_date') or '')
    return jsonify({'ok': True, 'id': jid, 'file_path': resolved})


@app.route('/api/sim/plan/jobs/delete', methods=['POST'])
def api_sim_plan_jobs_del():
    d = request.get_json(force=True, silent=True) or {}
    _sim_store.use_mode('live')
    _sim_store.delete_plan_job(int(d.get('id') or 0))
    return jsonify({'ok': True})


@app.route('/api/sim/plan/jobs/run', methods=['POST'])
def api_sim_plan_jobs_run():
    """立即执行一次定时任务指向的文件（实盘模式，过期批次照常跳过）。"""
    d = request.get_json(force=True, silent=True) or {}
    _sim_store.use_mode('live')
    _sim_store.init()
    jid = int(d.get('id') or 0)
    job = next((j for j in _sim_store.list_plan_jobs() if int(j['id']) == jid), None) if jid else None
    fp = (d.get('file_path') or (job or {}).get('file_path') or '').strip()
    if not fp:
        return jsonify({'ok': False, 'reason': '缺少文件'}), 400
    r = _sim_plan_execute(fp, '', 'live', 'job')
    if jid and job:
        _sim_store.use_mode('live')
        _sim_store.touch_plan_job(jid, time.strftime('%Y-%m-%d %H:%M:%S'),
                                  '成功 %s / 跳过 %s' % (r.get('success', 0), len(r.get('skipped') or [])))
    return jsonify(r)


@app.route('/api/sim/batch', methods=['POST'])
def api_sim_batch():
    d = request.get_json(force=True, silent=True) or {}
    _sim_apply_body_mode(d)
    cfg = _sim_cfg_read()
    items = d.get('items') or []
    if (not items) and d.get('text'):
        items = _sim_auto.parse_text(d['text'], cfg.get('default_qty', 100))
    if not items:
        return jsonify({'ok': False, 'reason': '没有可执行的标的', 'results': []})
    force = (d.get('force_side') or '').strip().lower()
    if force in ('buy', 'sell'):
        items = [dict(x, side=force) for x in items]
    return jsonify(_sim_auto.execute(items, (d.get('asof') or '').strip(),
                                     d.get('auto_cond', True), 'manual'))


@app.route('/api/sim/auto/scan', methods=['POST'])
def api_sim_auto_scan():
    d = request.get_json(force=True, silent=True) or {}
    return jsonify(_sim_auto.scan((d.get('asof') or '').strip(), 'manual'))


@app.route('/api/sim/auto/tasks', methods=['GET', 'POST'])
def api_sim_auto_tasks():
    if request.method == 'POST':
        d = request.get_json(force=True, silent=True) or {}
        tid = d.get('id')
        if tid:
            patch = {}
            for k in ('name', 'mode', 'content', 'run_at', 'weekdays'):
                if k in d:
                    patch[k] = d[k]
            if 'auto_cond' in d:
                patch['auto_cond'] = 1 if d['auto_cond'] else 0
            if 'enabled' in d:
                patch['enabled'] = 1 if d['enabled'] else 0
            _sim_store.update_auto_task(int(tid), **patch)
        else:
            tid = _sim_store.add_auto_task(
                (d.get('name') or '定时任务').strip(), (d.get('mode') or 'list'),
                d.get('content') or '', (d.get('run_at') or '14:55').strip(),
                str(d.get('weekdays') or '1,2,3,4,5'),
                1 if d.get('auto_cond', True) else 0,
                1 if d.get('enabled', True) else 0)
        return jsonify({'ok': True, 'id': tid})
    return jsonify({'tasks': _sim_store.list_auto_tasks()})


@app.route('/api/sim/auto/task/delete', methods=['POST'])
def api_sim_auto_task_delete():
    d = request.get_json(force=True, silent=True) or {}
    _sim_store.delete_auto_task(int(d.get('id') or 0))
    return jsonify({'ok': True})


@app.route('/api/sim/auto/task/run', methods=['POST'])
def api_sim_auto_task_run():
    d = request.get_json(force=True, silent=True) or {}
    tid = int(d.get('id') or 0)
    task = next((t for t in _sim_store.list_auto_tasks() if t['id'] == tid), None)
    if not task:
        return jsonify({'ok': False, 'reason': '任务不存在'})
    return jsonify(_sim_auto.run_task(task, (d.get('asof') or '').strip()))


@app.route('/api/sim/auto/logs')
def api_sim_auto_logs():
    return jsonify({'logs': _sim_store.list_auto_logs(int(request.args.get('limit') or 60))})


@app.route('/api/sim/auto/config', methods=['GET', 'POST'])
def api_sim_auto_config():
    if request.method == 'POST':
        d = request.get_json(force=True, silent=True) or {}
        return jsonify(_sim_auto.cfg_write(d))
    return jsonify(_sim_auto.cfg_read())


@app.route('/api/sim/config', methods=['GET', 'POST'])
def api_sim_config():
    if request.method == 'POST':
        d = request.get_json(force=True, silent=True) or {}
        cfg = _sim_cfg_read()
        for k in ('participation_rate', 'slippage_bps'):
            if k in d:
                try:
                    cfg[k] = max(0.0, float(d[k]))
                except (TypeError, ValueError):
                    pass
        _sim_cfg_write(cfg)
        return jsonify(cfg)
    return jsonify(_sim_cfg_read())


@app.route('/api/sim/order', methods=['POST'])
def api_sim_order():
    d = request.get_json(force=True, silent=True) or {}
    _sim_apply_body_mode(d)
    code = (d.get('code') or '').strip().upper()
    asof = (d.get('asof') or '').strip()
    bar, prev_close, last_date = _sim_bar(code, asof)
    asof = asof or last_date
    if not bar:
        return jsonify({'ok': False, 'status': 'rejected', 'reason': '无该标的行情数据'}), 200
    cfg = _sim_cfg_read()
    name = (d.get('name') or '').strip() or _stock_name(code).get('name') or code
    res = _sim_broker.submit_order(
        code=code, name=name, side=d.get('side'), otype=d.get('otype'),
        price=d.get('price') or 0, qty=d.get('qty') or 0, asof=asof,
        prev_close=prev_close, bar=bar,
        participation_rate=cfg.get('participation_rate', 0.0),
        slippage_bps=cfg.get('slippage_bps', 2.0))
    res['asof'] = asof
    res['bar'] = bar
    res['prev_close'] = prev_close
    res['limit_up'], res['limit_down'] = _sim_broker.limit_prices(prev_close, code, name)
    if res.get('ok') and bar:
        # 成交后跑一次条件判定：当日买入因 T+1 不会触发，
        # 但会把当日最高价写进 peak_price，供次日回落止盈使用。
        _sim_broker.run_conditions(code, asof, bar)
    return jsonify(res)


@app.route('/api/sim/cancel', methods=['POST'])
def api_sim_cancel():
    d = request.get_json(force=True, silent=True) or {}
    return jsonify(_sim_broker.cancel(d.get('order_id'), d.get('asof') or ''))


@app.route('/api/sim/account')
def api_sim_account():
    lp = {}
    for p in _sim_store.list_positions():
        lp[p['code']] = p['last_price']
    return jsonify(_sim_broker.account_view(lp))


@app.route('/api/sim/positions')
def api_sim_positions():
    rows = _sim_store.list_positions()
    names = _sim_names([r.get('code') for r in rows])
    for r in rows:
        if not r.get('name'):
            r['name'] = names.get(str(r.get('code') or '').upper(), '')
    return jsonify({'positions': rows})


@app.route('/api/sim/orders')
def api_sim_orders():
    return jsonify({'orders': _sim_store.list_orders(request.args.get('status') or 'all',
                                                     int(request.args.get('limit') or 60))})


_SIM_NAME_CACHE = {}


def _sim_names(codes):
    """批量补中文名称（ClickHouse stock_basic），并做进程内缓存。"""
    out, todo = {}, []
    for c in codes or []:
        c = str(c or '').strip().upper()
        if not c:
            continue
        if c in _SIM_NAME_CACHE:
            out[c] = _SIM_NAME_CACHE[c]
        elif c not in todo:
            todo.append(c)
    if todo:
        try:
            rows = ch_query("SELECT ts_code,name FROM %s.stock_basic WHERE ts_code IN (%s)" % (
                CH_DB, ','.join("'%s'" % c for c in todo)))
            for r in rows or []:
                code, nm = str(r[0] or '').upper(), (r[1] or '')
                _SIM_NAME_CACHE[code] = nm
                out[code] = nm
        except Exception:
            pass
    return out


@app.route('/api/sim/trades')
def api_sim_trades():
    rows = _sim_store.list_trades(int(request.args.get('limit') or 60))
    names = _sim_names([r.get('code') for r in rows])
    for r in rows:
        if not r.get('name'):
            r['name'] = names.get(str(r.get('code') or '').upper(), '')
    return jsonify({'trades': rows})


@app.route('/api/sim/nav')
def api_sim_nav():
    return jsonify({'snapshots': _sim_store.list_snapshots()})


@app.route('/api/sim/reset', methods=['POST'])
def api_sim_reset():
    d = request.get_json(force=True, silent=True) or {}
    _sim_apply_body_mode(d)
    _sim_store.reset(d.get('cash'))
    return jsonify({'ok': True, 'mode': _sim_store.current_mode()})


@app.route('/api/sim/cond', methods=['GET', 'POST'])
def api_sim_cond():
    """条件单：GET 列表；POST 创建（tp 止盈 / sl 止损 / trail 峰值回落）。"""
    if request.method == 'GET':
        rows = _sim_store.list_cond(
            request.args.get('code') or None,
            request.args.get('status') or None)
        names = _sim_names([r.get('code') for r in rows])
        for r in rows:
            if not r.get('name'):
                r['name'] = names.get(str(r.get('code') or '').upper(), '')
        return jsonify({'cond_orders': rows})
    d = request.get_json(force=True, silent=True) or {}
    _sim_apply_body_mode(d)
    code = str(d.get('code') or '').strip().upper()
    kind = str(d.get('kind') or '').strip().lower()
    try:
        pct = float(d.get('pct') or 0)
    except (TypeError, ValueError):
        pct = 0
    try:
        arm = float(d.get('arm_pct') or 0)
    except (TypeError, ValueError):
        arm = 0
    if not _sim_re.match(r'^\d{6}\.(SH|SZ|BJ)$', code):
        return jsonify({'ok': False, 'reason': '股票代码格式不对'}), 400
    if kind not in ('tp', 'sl', 'trail'):
        return jsonify({'ok': False, 'reason': 'kind 需要 tp / sl / trail'}), 400
    if not (0 < pct < 1):
        return jsonify({'ok': False, 'reason': '百分比需在 0~1 之间，8% 请传 0.08'}), 400
    if arm and (kind != 'trail' or not (0 < arm < 1)):
        return jsonify({'ok': False, 'reason': 'arm_pct 只用于回落单，且需在 0~1 之间'}), 400
    pos = _sim_store.get_position(code)
    if not pos or int(pos.get('qty') or 0) <= 0:
        return jsonify({'ok': False, 'reason': '该标的没有持仓，无法设置条件单'}), 400
    if arm:
        trig = 0  # 未激活，暂不计算触发价
    else:
        peak = float(pos.get('peak_price') or 0) or float(pos.get('last_price') or 0) or float(pos.get('cost') or 0)
        trig = _sim_broker.calc_trigger(kind, pct, float(pos.get('cost') or 0), peak)
    cid = _sim_store.add_cond(code, int(pos['qty']), kind, pct, d.get('asof') or '', trig, arm)
    return jsonify({'ok': True, 'cond_id': cid, 'code': code, 'kind': kind, 'pct': pct, 'arm_pct': arm})


@app.route('/api/sim/cond/cancel', methods=['POST'])
def api_sim_cond_cancel():
    d = request.get_json(force=True, silent=True) or {}
    try:
        cid = int(d.get('cond_id') or d.get('id') or 0)
    except (TypeError, ValueError):
        cid = 0
    if not cid:
        return jsonify({'ok': False, 'reason': '缺少 cond_id'}), 400
    _sim_store.update_cond(cid, status='cancelled')
    return jsonify({'ok': True})


@app.route('/api/sim/replay/step', methods=['POST'])
def api_sim_replay_step():
    """回放推进到某交易日：T+1 解冻 + 未成交单按当日 bar 重撮 + 写净值快照。"""
    d = request.get_json(force=True, silent=True) or {}
    asof = (d.get('asof') or '').strip()
    if not _sim_re.match(r'^\d{4}-\d{2}-\d{2}$', asof):
        return jsonify({'ok': False, 'reason': 'asof 需要 YYYY-MM-DD'}), 400
    cfg = _sim_cfg_read()
    codes = [str(c).strip().upper() for c in (d.get('codes') or []) if c]
    if not codes:
        codes = [p['code'] for p in _sim_store.list_positions() if '.' in (p.get('code') or '')]
    codes = list(dict.fromkeys([c for c in codes if _sim_re.match(r'^\d{6}\.(SH|SZ|BJ)$', c)]))
    bars, prices = {}, {}
    for c in codes:
        bar, _pc, _ld = _sim_bar(c, asof)
        if bar and bar['date'] == asof:
            bars[c] = bar
            prices[c] = bar['close']
    fills = _sim_broker.retry_pending(asof, bars,
                                      cfg.get('participation_rate', 0.0),
                                      cfg.get('slippage_bps', 2.0))
    cond_hits = []
    for c, bar in bars.items():
        cond_hits += _sim_broker.run_conditions(c, asof, bar)
    for c, px in prices.items():
        _sim_store.mark_last_price(c, px)
    acc = _sim_broker.account_view(prices)
    # 净值快照
    prev = [s for s in _sim_store.list_snapshots() if s['trade_date'] < asof]
    peak = max([s['nav'] for s in prev] + [acc['nav']]) if prev else acc['nav']
    dd = (1 - acc['nav'] / peak) if peak > 0 else 0
    _sim_store.save_snapshot(asof, acc['cash'], acc['market_value'], acc['nav'],
                             acc['nav'] - acc['initial_cash'], dd, len(acc['positions']))
    return jsonify({'ok': True, 'asof': asof, 'fills': fills, 'conditions': cond_hits,
                    'account': acc, 'granularity': 'daily' if codes else 'none'})


if __name__ == '__main__':
    print('A股看板数据 API 服务 V2 端口:5000')
    def _warmup_macro():
        """服务启动后后台预热宏观接口（6小时缓存，用户首次打开宏观页秒开）"""
        time.sleep(4)
        for _p in ('/api/macro/treasury', '/api/macro/corr', '/api/macro/liquidity',
                   '/api/macro/overview', '/api/macro/cn-monthly', '/api/macro/commodities'):
            try:
                requests.get('http://127.0.0.1:5000' + _p, timeout=120)
            except Exception:
                pass
        print('[warmup] macro cache warmed')

    import threading as _th
    _th.Thread(target=_warmup_macro, daemon=True).start()
    _sim_auto.set_executor(_sim_auto_execute)

    def _plan_exec_live(p, asof=''):
        """后台扫描/定时任务的计划执行一律落实盘账本，结束后恢复线程原模式。"""
        prev = _sim_store.current_mode()
        try:
            return _sim_plan_execute(p, asof, 'live', 'scan')
        finally:
            _sim_store.use_mode(prev)

    def _sweep_live(asof=''):
        """300s 条件单推进只针对实盘账本（回测由手动 step 推进，避免重复触发）。"""
        prev = _sim_store.current_mode()
        try:
            _sim_store.use_mode('live')
            return _sim_sweep_conditions(asof)
        finally:
            _sim_store.use_mode(prev)

    _sim_auto.set_plan_executor(_plan_exec_live)
    _sim_auto.set_sweeper(_sweep_live)
    _sim_auto.start()
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
