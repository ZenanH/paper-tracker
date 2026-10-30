import { createServer } from 'node:http';
let limited = false, delayMs = 0, inFlight = 0;
createServer(async (req,res) => {
 const url = new URL(req.url,'http://mock');
 res.setHeader('content-type','application/json');
 if(url.pathname==='/control') {if(url.searchParams.has('delay'))delayMs=Number(url.searchParams.get('delay'));res.end(JSON.stringify({inFlight,delayMs}));return;}
 if(url.pathname.startsWith('/crossref/')) {
  inFlight++;
  if(delayMs)await new Promise(resolve=>setTimeout(resolve,delayMs));
  inFlight--;
  if(!limited) { limited=true;res.writeHead(429,{'retry-after':'0'});res.end('{}');return; }
  const cursor=url.searchParams.get('cursor'),issn=decodeURIComponent(url.pathname.split('/')[3] || '0028-0836');
  const start=cursor==='*'?0:200,end=cursor==='*'?200:201;
  const date=(url.searchParams.get('filter').match(/from-(?:online-)?pub-date:(\d{4})-(\d{2})-(\d{2})/)||[]).slice(1).map(Number);
  if(date.length!==3)throw new Error('Expected publication window');
  res.end(JSON.stringify({message:{items:Array.from({length:end-start},(_,n)=>({DOI:`10.9999/audit-${start+n}`,title:[`${(start+n)%3===0?'Exclude':'Keep'} verification ${start+n}`],ISSN:[issn],type:'journal-article','published-online':{'date-parts':[date]},abstract:'ABSTRACT_MUST_BE_DROPPED'})),'next-cursor':'second','total-results':201}}));return;
 }
 if(url.pathname==='/v1/chat/completions') {
  let text='';for await(const chunk of req)text+=chunk;
  const body=JSON.parse(text),papers=JSON.parse(body.messages[1].content);
  if(papers[0]?.paperId==='connection-test') {res.end(JSON.stringify({choices:[{message:{content:'{"ok":true}'}}]}));return;}
  if(papers.length>10 || papers.some(p=>Object.keys(p).sort().join(',')!=='paperId,title')) {res.writeHead(400);res.end('{}');return;}
  const filtering=body.messages[0].content.includes('decision');
  res.end(JSON.stringify({choices:[{message:{content:JSON.stringify({results:papers.map(p=>filtering?{paperId:p.paperId,decision:p.title.startsWith('Exclude')?'exclude':'keep',reason:'Title only'}:{paperId:p.paperId,titleZh:'验证标题 '+p.title})})}}]}));return;
 }
 res.writeHead(404);res.end('{}');
}).listen(8099,'0.0.0.0');
