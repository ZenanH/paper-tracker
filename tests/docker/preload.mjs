const original=globalThis.fetch;
globalThis.fetch=(input,options)=>{
 const url=new URL(input instanceof Request?input.url:input);
 if(url.origin==='https://api.crossref.org')return original('http://audit-source:8099/crossref'+url.pathname+url.search,options);
 return original(input,options);
};
