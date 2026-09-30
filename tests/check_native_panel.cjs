const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const os = require('node:os');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.CODEX_USAGE_PLAYWRIGHT || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));

(async () => {
  const html = execFileSync('python3', ['-c', 'from server import embedded_html; print(embedded_html())'],
    {cwd:path.resolve(__dirname, '../plugins/codex-usage-monitor'), encoding:'utf8', env:{...process.env, PYTHONDONTWRITEBYTECODE:'1'}});
  const fixture = {
    generatedAt:'2026-09-30T10:00:00Z',
    account:{status:'ok',planType:'plus',buckets:[],updatedAt:'2026-09-30T10:00:00Z',error:null,usage:null},
    local:{totals:{today:1200,week:5000,month:9000,all:9000},daily:[],timezone:'Asia/Shanghai',periodDate:'2026-09-30',fileCount:1,threads:[],selectedThread:null}
  };
  const encode = data => JSON.stringify(data).replace(/</g, '\\u003c');
  const pageHtml = `<iframe id="panel" style="width:420px;height:1300px;border:0"></iframe><script>
    const frame=document.getElementById('panel');
    window.addEventListener('message',event=>{
      if(event.source!==frame.contentWindow)return;
      const message=event.data;
      if(message.method==='ui/initialize')frame.contentWindow.postMessage({jsonrpc:'2.0',id:message.id,result:{protocolVersion:'2026-01-26',hostCapabilities:{serverTools:{}},hostInfo:{name:'Test host',version:'1.0.0'}}},'*');
      if(message.method==='tools/call')frame.contentWindow.postMessage({jsonrpc:'2.0',id:message.id,result:{structuredContent:${encode(fixture)}}},'*');
    });
    frame.srcdoc=${encode(html)};
  </script>`;
  const host = http.createServer((request,response)=>{response.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});response.end(pageHtml);});
  await new Promise(resolve=>host.listen(0,'127.0.0.1',resolve));
  let browser;
  try {
    browser=await chromium.launch({headless:true,executablePath:process.env.CODEX_USAGE_BROWSER || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
    const page=await browser.newPage();
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto('http://127.0.0.1:'+host.address().port);
    const frame=page.frameLocator('#panel');
    await frame.locator('#today').filter({hasText:'1.2K'}).waitFor();
    if(errors.length)throw new Error(errors.join('\n'));
    console.log(JSON.stringify({embeddedBridge:true,initialized:true,toolsCall:true,browserErrors:errors}));
  } finally {if(browser)await browser.close();await new Promise(resolve=>host.close(resolve));}
})().catch(error=>{console.error(error.message);process.exitCode=1;});
