const fs = require('fs');
const p = 'E:/Quant/A股看板系统/A股看板系统/index.html';
let s = fs.readFileSync(p, 'utf8');
const r = [];

// 1. 提取裸奔的 conc rank+rel 块（concRankTitle -> chartConcRel 含）
const reConcBlk = /<div style="font-size:12px;font-weight:600;margin-bottom:6px;color:var\(--text-main\)" id="concRankTitle">[\s\S]*?<div id="chartConcRel" style="height:220px;margin-bottom:12px"><\/div>/;
const mConcBlk = s.match(reConcBlk);
if (mConcBlk) {
  s = s.replace(mConcBlk[0], '');
  // 插入到 conc 工具条闭(</div>)后、chartConcKline 前
  const ancConc = '    </div>\r\n<div id="chartConcKline"';
  if (s.includes(ancConc)) {
    s = s.replace(ancConc, '    </div>\r\n' + mConcBlk[0] + '\r\n<div id="chartConcKline"');
    r.push('conc rank+rel restored (after toolbar, before kline)');
  } else r.push('conc anchor MISS');
} else r.push('conc block MISS');

// 2. 提取裸奔的 ind rank+rel 块
const reIndBlk = /<div style="font-size:12px;font-weight:600;margin-bottom:6px;color:var\(--text-main\)" id="indRankTitle">[\s\S]*?<div id="chartIndRel" style="height:220px;margin-bottom:12px"><\/div>/;
const mIndBlk = s.match(reIndBlk);
if (mIndBlk) {
  s = s.replace(mIndBlk[0], '');
  const ancInd = '    </div>\r\n<div id="chartIndKline"';
  if (s.includes(ancInd)) {
    s = s.replace(ancInd, '    </div>\r\n' + mIndBlk[0] + '\r\n<div id="chartIndKline"');
    r.push('ind rank+rel restored (after toolbar, before kline)');
  } else r.push('ind anchor MISS');
} else r.push('ind block MISS');

fs.writeFileSync(p, s, 'utf8');
console.log(r.join('\n'));
