import requests
# 行业板块成分股（取最新日期）
r = requests.get('http://localhost:5000/api/ch/sector-members?type=industry&code=884250.TI', timeout=20)
j = r.json()
m = j.get('members', [])
print('date=%s sectorPct=%s members=%d' % (j.get('date'), j.get('sectorPct'), len(m)))
if m:
    x = m[0]
    print('first: %s %s changePct=%s vsIndex=%s vsSector=%s' % (x.get('code'), x.get('name'), x.get('changePct'), x.get('vsIndex'), x.get('vsSector')))
# 概念
r2 = requests.get('http://localhost:5000/api/ch/sector-members?type=concept&code=885835.TI', timeout=20)
j2 = r2.json()
m2 = j2.get('members', [])
print('conc date=%s sectorPct=%s members=%d' % (j2.get('date'), j2.get('sectorPct'), len(m2)))
if m2:
    x2 = m2[0]
    print('conc first: %s %s vsSector=%s' % (x2.get('code'), x2.get('name'), x2.get('vsSector')))
# 股票列表（自选名称来源）
r3 = requests.get('http://localhost:5000/api/stock/list?size=5', timeout=20)
j3 = r3.json()
print('stock list:', [(x.get('code'), x.get('name')) for x in (j3.get('list') or [])[:3]])
