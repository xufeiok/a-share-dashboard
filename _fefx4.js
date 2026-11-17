const fs = require('fs');
const p = 'E:/Quant/A股看板系统/A股看板系统/index.html';
let s = fs.readFileSync(p, 'utf8');
const r = [];

// ============ 1. 移动工具条：从 K线图上方移到 涨幅排名上面（详情面板 panel-body 开头） ============
const reIndBar = /<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;padding:6px 10px;background:rgba\(255,255,255,0\.03\);border:1px solid var\(--border-color\);border-radius:6px;flex-wrap:wrap">\r?\n      <span style="font-size:13px;font-weight:600;color:var\(--text-main\);white-space:nowrap">K线走势<\/span>\r?\n      <div style="position:relative;flex:1;min-width:180px">\r?\n        <input id="indSectorSearch"[\s\S]*?<\/div>\r?\n    <\/div>/;
const mInd = s.match(reIndBar);
if (mInd) {
  s = s.replace(mInd[0], '');
  const anchorInd = '          <div class="panel-body">\r\n            <div style="font-size:12px;font-weight:600;margin-bottom:6px;color:var(--text-main)" id="indRankTitle">';
  if (s.includes(anchorInd)) {
    s = s.replace(anchorInd, '          <div class="panel-body">\r\n            ' + mInd[0] + '\r\n            <div style="font-size:12px;font-weight:600;margin-bottom:6px;color:var(--text-main)" id="indRankTitle">');
    r.push('ind bar moved to top of panel (above rank)');
  } else r.push('ind anchor MISS');
} else r.push('ind bar MISS');

const reConcBar = /<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;padding:6px 10px;background:rgba\(255,255,255,0\.03\);border:1px solid var\(--border-color\);border-radius:6px;flex-wrap:wrap">\r?\n      <span style="font-size:13px;font-weight:600;color:var\(--text-main\);white-space:nowrap">K线走势<\/span>\r?\n      <div style="position:relative;flex:1;min-width:180px">\r?\n        <input id="concSectorSearch"[\s\S]*?<\/div>\r?\n    <\/div>/;
const mConc = s.match(reConcBar);
if (mConc) {
  s = s.replace(mConc[0], '');
  const anchorConc = '          <div class="panel-body">\r\n            <div style="font-size:12px;font-weight:600;margin-bottom:6px;color:var(--text-main)" id="concRankTitle">';
  if (s.includes(anchorConc)) {
    s = s.replace(anchorConc, '          <div class="panel-body">\r\n            ' + mConc[0] + '\r\n            <div style="font-size:12px;font-weight:600;margin-bottom:6px;color:var(--text-main)" id="concRankTitle">');
    r.push('conc bar moved to top of panel (above rank)');
  } else r.push('conc anchor MISS');
} else r.push('conc bar MISS');

// ============ 2. 成分股表：对跑大盘列后加"对跑板块"列 ============
// 2a. 表头（ind+conc 两处）
const headOld = `html += '<th style="padding:5px;border:1px solid var(--border-color);text-align:center">对跑大盘</th>';`;
const headNew = `html += '<th style="padding:5px;border:1px solid var(--border-color);text-align:center">对跑大盘</th>';\r\n    html += '<th style="padding:5px;border:1px solid var(--border-color);text-align:center">对跑板块</th>';`;
if (s.includes(headOld)) {
  s = s.split(headOld).join(headNew);
  r.push('header vsSector added (x' + (s.split('>对跑板块</th>').length - 1) + ')');
} else r.push('header MISS');

// 2b. 行内 vsSColor/vsSMark 声明（ind+conc 两处）
const markOld = `const vsMark = m.vsIndex > 2 ? ' 🚀' : (m.vsIndex < -2 ? ' ⚠️' : '');`;
const markNew = `const vsMark = m.vsIndex > 2 ? ' 🚀' : (m.vsIndex < -2 ? ' ⚠️' : '');\r\n      const vsSColor = m.vsSector > 2 ? '#f0a020' : (m.vsSector < -2 ? '#5a9cf0' : (m.vsSector >= 0 ? '#f23645' : '#2962ff'));\r\n      const vsSMark = m.vsSector > 2 ? ' 🚀' : (m.vsSector < -2 ? ' ⚠️' : '');`;
if (s.includes(markOld)) {
  s = s.split(markOld).join(markNew);
  r.push('vsSector vars added');
} else r.push('vars MISS');

// 2c. vsIndex td 后插 vsSector td（ind+conc 两处）
const tdOld = `        <td style="padding:5px;border:1px solid var(--border-color);text-align:center;color:${'${vsColor}'};font-weight:600">${'${m.vsIndex >= 0 ? \'+\' : \'\'}${m.vsIndex}%${vsMark}'}</td>`;
const tdNew = tdOld + `\r\n        <td style="padding:5px;border:1px solid var(--border-color);text-align:center;color:${'${vsSColor}'};font-weight:600">${'${m.vsSector >= 0 ? \'+\' : \'\'}${m.vsSector}%${vsSMark}'}</td>`;
if (s.includes(tdOld)) {
  s = s.split(tdOld).join(tdNew);
  r.push('vsSector td added');
} else r.push('td MISS');

// ============ 3. 自选列表：显示中文名 + 去重 ============
const favOld = `renderList(S.favs.map(c => ({ code: c, name: c, pctChg: 0 })), false);`;
const favNew = `// 自选：显示中文名 + 去重
        if (!S.listCache) {
          try {
            const _r = await (await fetch(\`\${API}/stock/list?size=120\`)).json();
            S.listCache = (_r.list || []).map(x => ({ code: x.code, name: x.name, pctChg: x.pctChg }));
          } catch (e) { S.listCache = []; }
        }
        const _nameMap = {};
        (S.listCache || []).forEach(x => { if (x.code) _nameMap[x.code] = x.name; });
        const _seen = {};
        const _favs = [];
        S.favs.forEach(c => {
          if (!c || _seen[c]) return;
          _seen[c] = 1;
          _favs.push({ code: c, name: _nameMap[c] || c, pctChg: 0 });
        });
        renderList(_favs, false);
        return;`;
if (s.includes(favOld)) {
  s = s.replace(favOld, favNew);
  r.push('fav list name+dedup fixed');
} else r.push('fav MISS');

// 3b. 收藏 toggle 防重复 push
const togOld = `if (idx >= 0) S.favs.splice(idx, 1); else S.favs.push(c);`;
const togNew = `if (idx >= 0) S.favs.splice(idx, 1); else if (!S.favs.includes(c)) S.favs.push(c);`;
if (s.includes(togOld)) {
  s = s.replace(togOld, togNew);
  r.push('fav toggle dedup');
} else r.push('toggle MISS');

fs.writeFileSync(p, s, 'utf8');
console.log(r.join('\n'));
