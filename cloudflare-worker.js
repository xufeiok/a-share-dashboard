// Cloudflare Worker — A股看板K线数据CORS代理
// 部署步骤：
// 1. 登录 https://dash.cloudflare.com
// 2. Workers & Pages -> Create -> Create Worker
// 3. 粘贴本代码，保存部署
// 4. 复制Worker地址（如 https://astock-proxy.yourname.workers.dev）
// 5. 在看板页面按F12控制台执行: DataService.CONFIG.proxyUrl = '你的Worker地址'
//    或直接修改 index.html 中 CONFIG.proxyUrl 的值

export default {
  async fetch(request) {
    // 处理OPTIONS预检请求
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
          'Access-Control-Allow-Headers': '*',
          'Access-Control-Max-Age': '86400'
        }
      });
    }

    const url = new URL(request.url);
    const targetUrl = url.searchParams.get('url');

    if (!targetUrl) {
      return new Response('A股看板代理服务正常。使用 ?url=目标地址 转发请求', {
        status: 200,
        headers: { 'Content-Type': 'text/plain; charset=utf-8', 'Access-Control-Allow-Origin': '*' }
      });
    }

    try {
      const response = await fetch(targetUrl, {
        method: request.method,
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
          'Referer': 'https://finance.sina.com.cn/'
        }
      });

      const headers = new Headers(response.headers);
      headers.set('Access-Control-Allow-Origin', '*');
      headers.set('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
      headers.set('Access-Control-Allow-Headers', '*');

      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers
      });
    } catch (err) {
      return new Response('Proxy error: ' + err.message, {
        status: 502,
        headers: { 'Content-Type': 'text/plain', 'Access-Control-Allow-Origin': '*' }
      });
    }
  }
};
