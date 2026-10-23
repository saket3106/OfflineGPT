import React,{useState,useEffect,useRef,useMemo} from 'react';

import ReactMarkdown from 'react-markdown';

import remarkGfm from 'remark-gfm';

const parse=(v,f={})=>{try{return JSON.parse(v)}catch{return f}};

const call=(b,m,...a)=>new Promise(r=>b?.[m] ? b[m](...a,r) : r(null));

const fileUrl=p=>'file:///'+String(p).replace(/\\/g,'/').split('/').map((v,i)=>i?encodeURIComponent(v):v).join('/');



export function workspaceCall(bridge,action,args={},onStart=()=>{}) {

  return new Promise((resolve,reject)=>{

    if(!bridge?.workspaceRequest || !bridge?.event)return reject(new Error('Workspace bridge unavailable.'));

    let requestId=null,early=[],timer;

    const cleanup=()=>{clearTimeout(timer);try{bridge.event.disconnect(receive)}catch{}};

    const receive=raw=>{const e=parse(raw);if(e.type!=='workspaceResult')return;if(!requestId){early.push(e);return}if(e.requestId!==requestId)return;cleanup();e.ok ? resolve(e) : reject(new Error(e.error||'Workspace task failed.'))};

    bridge.event.connect(receive);

    bridge.workspaceRequest(action,JSON.stringify(args),raw=>{const r=parse(raw);if(!r.ok){cleanup();reject(new Error(r.error||'Task could not start.'));return}requestId=r.requestId;onStart(requestId);timer=setTimeout(()=>{bridge.cancelWorkspace?.(requestId);cleanup();reject(new Error('Workspace task timed out.'))},900000);for(const e of early)receive(JSON.stringify(e));early=[]});

  });

}



function useJob(bridge){

  const [running,setRunning]=useState(''),[error,setError]=useState('');

  const run=async(action,args)=>{setError('');setRunning('starting');try{return await workspaceCall(bridge,action,args,setRunning)}catch(e){setError(e.message);return null}finally{setRunning('')}};

  return {running,error,run,stop:()=>bridge?.cancelWorkspace?.(running)};

}



export function DocumentWorkspace({bridge,onPrompt,onAttach}){

  const [library,setLibrary]=useState({documents:[],collections:[]}),[collection,setCollection]=useState(''),[source,setSource]=useState(''),[name,setName]=useState(''),[filter,setFilter]=useState(''),[query,setQuery]=useState(''),[exact,setExact]=useState(false),[semantic,setSemantic]=useState(false),[result,setResult]=useState(null),[tab,setTab]=useState('documents');

  const job=useJob(bridge);

  const visibleDocuments=useMemo(()=>library.documents.filter(d=>(!collection||d.collections.includes(collection))&&d.name.toLowerCase().includes(filter.toLowerCase())),[library.documents,collection,filter]);

  const reload=async()=>{const r=await job.run('documents',{});if(r)setLibrary(r.value)};

  const index=async paths=>{if(!paths?.length)return;const r=await job.run('index',{paths,collectionId:collection});if(r)reload()};

  useEffect(()=>{reload()},[bridge]);

  useEffect(()=>{const drop=raw=>{const files=parse(raw,[]);index(files.filter(f=>/\.(pdf|txt|md|docx|pptx)$/i.test(f.path)).map(f=>f.path))};bridge?.filesDropped?.connect(drop);return()=>{try{bridge?.filesDropped?.disconnect(drop)}catch{}}},[bridge,collection]);

  const add=async()=>{const files=parse(await call(bridge,'pickAttachment','document'),[]);index(Array.isArray(files)?files.map(f=>typeof f==='string'?f:f.path):files.path?[files.path]:[])};

  return <section className="workspace-view local-workspace"><div className="workspace-heading"><div className="eyebrow">YOUR SOURCES</div><h1>Documents & code</h1><p>Keep source files in place. Search indexed evidence or ask your local chat model to explain it.</p></div>

    <div className="workspace-toolbar"><button aria-pressed={tab==='documents'} onClick={()=>setTab('documents')}>Document Library</button><button aria-pressed={tab==='code'} onClick={()=>setTab('code')}>Code tools</button></div>

    {tab==='code'?<CodeWorkspace bridge={bridge}/>:<>

    <div className="workspace-toolbar"><button disabled={!!job.running} onClick={add}>Add files</button><select aria-label="Document collection" value={collection} onChange={e=>{setCollection(e.target.value);setSource('')}}><option value="">All documents</option>{library.collections.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select><input aria-label="New collection name" value={name} onChange={e=>setName(e.target.value)} placeholder="New collection"/><button disabled={!name.trim()||!!job.running} onClick={async()=>{const r=await job.run('collection',{name});if(r){setCollection(r.value.id);setName('');reload()}}}>Create collection</button><button onClick={reload} disabled={!!job.running}>Refresh</button></div>

    <input className="workspace-input" aria-label="Filter documents" value={filter} onChange={e=>setFilter(e.target.value)} placeholder="Filter documents by filename…"/>

    <div className="document-list" aria-label="Indexed documents">{visibleDocuments.map(d=><article className="document-row" key={d.id}><div><strong>{d.name}</strong><small>{d.missing?'Source missing':d.metadata.pages?`${d.metadata.pages} physical PDF pages`:d.metadata.slides?`${d.metadata.slides} slides`:'Text source'} · {d.metadata.extraction}</small></div><button onClick={()=>call(bridge,'openDocumentSource',d.id,0)}>Open source</button><button disabled={!!job.running||d.missing} onClick={()=>index([d.path])}>Reindex / assign</button><button disabled={!!job.running} onClick={async()=>{await job.run('remove',{documentId:d.id});reload()}}>Remove index</button></article>)}{!visibleDocuments.length&&<div className="document-empty"><strong>{library.documents.length?"No matching sources":"Bring your sources into focus"}</strong><p className="workspace-note">{library.documents.length?"Try another filename or collection. Your indexed files are unchanged.":"Add or drop PDF, TXT, Markdown, DOCX or PPTX files. Selectable text is indexed locally; scanned pages need OCR."}</p></div>}</div>

    <form className="workspace-query" onSubmit={async e=>{e.preventDefault();setResult(null);const r=await job.run(exact?'search':'question',{query,exact,semantic,collectionId:collection,documentId:source});if(r)setResult({...r.value,durationMs:r.durationMs})}}><select aria-label="Source filter" value={source} onChange={e=>setSource(e.target.value)}><option value="">All sources in collection</option>{library.documents.filter(d=>!collection||d.collections.includes(collection)).map(d=><option key={d.id} value={d.id}>{d.name}</option>)}</select><textarea value={query} onChange={e=>setQuery(e.target.value)} placeholder="Ask a question, compare sources, or enter an exact phrase…" aria-label="Document question"/><label><input type="checkbox" checked={exact} onChange={e=>setExact(e.target.checked)}/> Exact phrase · deterministic literal search</label>{!exact&&<label><input type="checkbox" checked={semantic} onChange={e=>setSemantic(e.target.checked)}/> Semantic query expansion · extra local model call</label>}<button disabled={!!job.running||!query.trim()}>{exact?'Search phrase':'Ask with evidence'}</button><button type="button" disabled={!!job.running||!library.documents.length} onClick={async()=>{setResult(null);const r=await job.run('question',{query:query||'Summarize these sources and their limitations.',summary:true,collectionId:collection,documentId:source});if(r)setResult({...r.value,durationMs:r.durationMs})}}>Summarize selected sources</button></form>

    {job.running&&<p role="status">Working locally… <button onClick={job.stop}>Stop</button></p>}{job.error&&<p role="alert">{job.error}</p>}

    {result&&<section className="workspace-answer" aria-label="Document answer"><small>{result.status} · {result.retrieval} · {(result.durationMs/1000).toFixed(2)} s</small>{result.coverage&&<p>{result.coverage.indexedPagesOrSections} indexed pages/sections · {result.modelCalls} local model calls · {result.coverage.scope}</p>}{result.answer&&<ReactMarkdown remarkPlugins={[remarkGfm]}>{result.answer}</ReactMarkdown>}{result.sourceCaveats?.length>0&&<details open><summary>Source caveats · verbatim excerpts</summary>{result.sourceCaveats.map((c,i)=><blockquote key={i}>[{c.citation}] {c.text}</blockquote>)}</details>}{result.status==='NO_DOCUMENT'&&<p>No indexed source is available in this selection.</p>}{result.matches.map((m,i)=><details key={m.chunkId}><summary><button onClick={e=>{e.preventDefault();call(bridge,'openDocumentSource',m.documentId,m.page||0)}}>[{i+1}] {m.filename}{m.page?` · physical page ${m.page}`:m.section?` · ${m.section}`:''}</button></summary><p>{m.snippet}</p><small>{m.chunkId} · offsets {m.start}–{m.end}</small></details>)}</section>}

    <div className="workspace-toolbar"><button onClick={()=>onPrompt('Create a PDF about ')}>Create PDF</button><button onClick={()=>onPrompt('Create a Word document about ')}>Create Word document</button><button onClick={()=>onPrompt('Create a PowerPoint presentation about ')}>Create slides</button><button onClick={onAttach}>Attach for focused editing</button></div></>}

  </section>

}



export function CodeWorkspace({bridge}){

  const [code,setCode]=useState(''),[kind,setKind]=useState('executable'),[result,setResult]=useState(null),[project,setProject]=useState(''),[query,setQuery]=useState(''),[overview,setOverview]=useState(null),[patchPath,setPatchPath]=useState(''),[original,setOriginal]=useState(''),[replacement,setReplacement]=useState(''),[patch,setPatch]=useState(null);

  const job=useJob(bridge);

  const browse=async(folder)=>{const r=parse(await call(bridge,'choosePath',folder?'directory':'file'));if(r.path)setProject(r.path)};

  return <div className="code-workspace"><h2>Python checks</h2><p>Static validation never runs your program. Optional bounded checks support a small pure Python subset with instruction, value and output limits. Imports, files and network are unavailable.</p><select aria-label="Code kind" value={kind} onChange={e=>setKind(e.target.value)}><option value="executable">Executable Python</option><option value="illustrative">Illustrative snippet</option><option value="pseudocode">Pseudocode</option></select><textarea className="workspace-code" aria-label="Python source" value={code} onChange={e=>setCode(e.target.value)} spellCheck={false} placeholder="Paste Python code…"/><div className="workspace-toolbar"><button disabled={!!job.running||!code.trim()} onClick={async()=>{const r=await job.run('codeCheck',{code,kind});if(r)setResult(r.value)}}>Check syntax & names</button><button disabled={!!job.running||!code.trim()||kind==='pseudocode'} onClick={async()=>{const r=await job.run('codeCheck',{code,kind,execute:true});if(r)setResult(r.value)}}>Run bounded checks…</button></div>{result&&<div role="status"><strong>{result.valid===true?'Syntax passed':result.valid===false?'Syntax failed':'Pseudocode'}</strong><p>{result.error||result.limitation}</p>{result.warnings?.map(w=><p key={w}>{w}</p>)}{result.boundedCheck&&<pre>{result.boundedCheck.stdout||result.boundedCheck.error_message}</pre>}<small>{result.executed?'Restricted AST evaluation performed.':'Not executed.'}</small></div>}

  <h2>Project inspection</h2><div className="workspace-toolbar"><button onClick={()=>browse(true)}>Choose folder</button><button onClick={()=>browse(false)}>Choose ZIP</button><input aria-label="Project path" value={project} onChange={e=>setProject(e.target.value)} placeholder="Project folder or ZIP path"/><input aria-label="Code search" value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search source text"/><button disabled={!!job.running||!project} onClick={async()=>{const r=await job.run('project',{path:project,query});if(r)setOverview(r.value)}}>Inspect</button></div>{overview&&<div className="project-files">{overview.files.map(f=><details key={f.name}><summary>{f.name} · {f.bytes} bytes</summary>{f.symbols.map(s=><p key={s.line+':'+s.name}>{s.kind} {s.name} · line {s.line}</p>)}{f.matches.map(m=><pre key={m.line}>{m.line}: {m.text}</pre>)}</details>)}</div>}

  <details><summary>Review a precise source patch</summary><p>Choose one source file. The target must match exactly once. Applying an edit requires a separate confirmation and saves a backup.</p><input className="workspace-input" aria-label="Patch source file" value={patchPath} onChange={e=>{setPatchPath(e.target.value);setPatch(null)}} placeholder="Full source file path"/><textarea aria-label="Exact patch target" value={original} onChange={e=>{setOriginal(e.target.value);setPatch(null)}} placeholder="Original text"/><textarea aria-label="Patch replacement" value={replacement} onChange={e=>{setReplacement(e.target.value);setPatch(null)}} placeholder="Replacement"/><button disabled={!!job.running||!patchPath||!original} onClick={async()=>{const r=await job.run('patchPreview',{path:patchPath,original,replacement});if(r)setPatch(r.value)}}>Preview patch</button>{patch&&<><pre>{patch.diff}</pre><button disabled={!!job.running} onClick={async()=>{const r=await job.run('patchApply',{path:patchPath,original,replacement,beforeHash:patch.beforeHash});if(r){setPatch(null);setResult({valid:true,executed:false,warnings:['Patch applied; backup: '+r.value.backup]})}}}>Apply reviewed patch…</button></>}</details>

  {job.running&&<p role="status">Checking locally… <button onClick={job.stop}>Stop</button></p>}{job.error&&<p role="alert">{job.error}</p>}</div>

}



export function RecoverySettings({bridge,settings,update,busy}){

  const job=useJob(bridge);const [result,setResult]=useState(null),[restore,setRestore]=useState('');

  const run=async(a,args)=>{const r=await job.run(a,args);if(r)setResult(r.value)};

  return <section className="settings-section recovery-settings"><h2>Backup & diagnostics</h2><p>Conversation backups use SQLite's online backup API. Restore saves a recovery checkpoint and requires confirmation. Image/model files are not included.</p><div className="workspace-toolbar"><button disabled={!!job.running} onClick={()=>run('backup',{folder:settings.backup_directory||'',retain:settings.backup_retention||10})}>Back up conversations</button><button disabled={!!job.running} onClick={()=>run('diagnostics',{})}>Create diagnostic report</button></div><label>Backup directory<input value={settings.backup_directory||''} onChange={e=>update('backup_directory',e.target.value)}/></label><label><input type="checkbox" checked={!!settings.backup_schedule_enabled} onChange={e=>update('backup_schedule_enabled',e.target.checked)}/> Scheduled backups while the app is open</label><label>Interval (hours)<input type="number" min="1" max="168" value={settings.backup_interval_hours||24} onChange={e=>update('backup_interval_hours',Number(e.target.value))}/></label><label>Retain backups<input type="number" min="1" max="100" value={settings.backup_retention||10} onChange={e=>update('backup_retention',Number(e.target.value))}/></label><div className="workspace-toolbar"><input aria-label="Restore backup path" value={restore} onChange={e=>setRestore(e.target.value)} placeholder="Backup .sqlite3 path"/><button onClick={async()=>{const r=parse(await call(bridge,'choosePath','file'));if(r.path)setRestore(r.path)}}>Browse backup</button><button disabled={busy||!!job.running||!restore} onClick={()=>run('restore',{path:restore})}>Restore…</button></div><p>Save settings to apply the schedule. Diagnostic reports stay on this PC and exclude chat text, document text, file paths and credentials.</p>{job.error&&<p role="alert">{job.error}</p>}{job.running&&<p role="status">Working locally…</p>}{result&&<div className="workspace-answer">{result.path&&<><p>Saved: {result.path}</p><button onClick={()=>bridge?.revealFile?.(result.path)}>Show in folder</button></>}{result.report?<pre>{JSON.stringify(result.report,null,2)}</pre>:<pre>{JSON.stringify(result,null,2)}</pre>}</div>}</section>

}



export function LocalImageTools({bridge,image,onUpdated,onVariationStarted}){

  const job=useJob(bridge);const [operation,setOperation]=useState(image.recolorOptions?'recolor':'resize'),[options,setOptions]=useState({...image.recolorOptions,width:1024,height:1024,left:0,top:0,right:512,bottom:512,angle:90,format:'png'}),[result,setResult]=useState(null),[prompt,setPrompt]=useState(image.prompt||image.userPrompt||''),[variationError,setVariationError]=useState(''),[maskReady,setMaskReady]=useState(false);

  const variation=async mode=>{const r=parse(await call(bridge,'imageVariation',image.id,prompt,mode));setVariationError(r.ok?'':r.error||'Variation could not start.');if(r.ok)onVariationStarted?.();};

  return <details className="local-image-tools" open={image.recolorOptions?true:undefined}><summary>Versions & local image tools</summary><small>Native: {image.nativeDimensions||'unavailable'} · Saved: {image.finalDimensions||'unavailable'} · Parent: {image.parentId||'unavailable'}</small><textarea aria-label="Variation prompt" value={prompt} onChange={e=>setPrompt(e.target.value)}/><div className="workspace-toolbar"><button disabled={!prompt||!!job.running} onClick={()=>variation('new')}>New-seed variation</button><button disabled={!prompt||image.seed==null||image.seed===''||!!job.running} onClick={()=>variation('same')}>Reuse seed & settings</button><button disabled={!prompt||!!job.running} onClick={()=>variation('reference')}>Generative AI Edit</button></div>{variationError&&<p role="alert">{variationError}</p>}<p>Same-seed results depend on runtime, hardware and recorded settings. Reference editing uses the installed FLUX backend.</p><select aria-label="Local image operation" value={operation} onChange={e=>setOperation(e.target.value)}>{['crop','resize','rotate','convert','recolor'].map(o=><option key={o} value={o}>{o==='recolor'?'Fast Recolor · paint a mask':o}</option>)}</select>{operation==='recolor'&&<RecolorMask image={image} options={options} onChange={setOptions} onReady={setMaskReady} disabled={!!job.running}/>}<div className="workspace-toolbar">{(operation==='resize'?['width','height']:operation==='crop'?['left','top','right','bottom']:operation==='rotate'?['angle']:[]).map(k=><label key={k}>{k}<input type="number" value={options[k]} onChange={e=>setOptions({...options,[k]:Number(e.target.value)})}/></label>)}<label>Format<select disabled={operation==='recolor'} value={operation==='recolor'?'png':options.format} onChange={e=>setOptions({...options,format:e.target.value})}><option>png</option><option>jpg</option><option>webp</option></select></label><button disabled={!!job.running||(operation==='recolor'&&(!maskReady||(!options.strokes?.length&&!options.mask_path)))} onClick={async()=>{const r=await job.run('imageEdit',{path:image.path,parentId:image.id,operation,options:{...options,format:operation==='recolor'?'png':options.format}});if(r){setResult(r.value);onUpdated?.()}}}>Save new version</button></div>{job.error&&<p role="alert">{job.error}</p>}{job.running&&<p role="status">Processing locally… <button onClick={job.stop}>Stop</button></p>}{result&&<><div className="image-compare"><figure><img src={fileUrl(image.path)} alt="Original"/><figcaption>Original · preserved</figcaption></figure><figure><img src={fileUrl(result.path)} alt="Edited version"/><figcaption>{result.method||result.operation} · {result.dimensions}</figcaption></figure></div>{result.metrics&&<small>Mask: {result.metrics.maskMs} ms · Recolor: {result.metrics.recolorMs} ms · Save: {result.metrics.saveMs} ms · No model calls</small>}<button onClick={()=>bridge?.saveImage?.(result.path)}>Save As…</button></>}</details>

}

function RecolorMask({image,options,onChange,onReady,disabled}){
  const canvas=useRef(null),active=useRef(null),maskBase=useRef(null),drawRef=useRef(null),[maskError,setMaskError]=useState(''),[size,setSize]=useState([1024,576]),[radius,setRadius]=useState(.02),[erase,setErase]=useState(false);
  const strokes=options.strokes||[];
  const draw=(extra=null)=>{
    const c=canvas.current;if(!c)return;const x=c.getContext('2d');x.clearRect(0,0,c.width,c.height);
    if(maskBase.current){x.drawImage(maskBase.current,0,0,c.width,c.height);const pixels=x.getImageData(0,0,c.width,c.height);for(let i=0;i<pixels.data.length;i+=4){pixels.data[i+3]=pixels.data[i];pixels.data[i]=pixels.data[i+1]=pixels.data[i+2]=255;}x.putImageData(pixels,0,0);}
    for(const s of [...strokes,...(extra?[extra]:[])]){x.globalCompositeOperation=s.erase?'destination-out':'source-over';x.strokeStyle='#fff';x.fillStyle='#fff';x.lineWidth=2*s.radius*Math.min(c.width,c.height);x.lineCap='round';x.lineJoin='round';x.beginPath();s.points.forEach(([a,b],i)=>i?x.lineTo(a*c.width,b*c.height):x.moveTo(a*c.width,b*c.height));x.stroke();if(s.points.length===1){x.beginPath();x.arc(s.points[0][0]*c.width,s.points[0][1]*c.height,x.lineWidth/2,0,Math.PI*2);x.fill()}}x.globalCompositeOperation='source-over';
  };
  drawRef.current=draw;
  useEffect(()=>{const img=new Image();img.onload=()=>{const scale=Math.min(1,1024/Math.max(img.width,img.height));setSize([Math.max(1,Math.round(img.width*scale)),Math.max(1,Math.round(img.height*scale))]);};img.src=fileUrl(image.path);return()=>{img.onload=null}},[image.path]);
  useEffect(()=>{maskBase.current=null;setMaskError('');onReady?.(false);if(!options.mask_path){drawRef.current?.();onReady?.(true);return;}const img=new Image();img.onload=()=>{maskBase.current=img;drawRef.current?.();onReady?.(true);};img.onerror=()=>{setMaskError('The reviewed mask could not load. Clear it and paint a mask, or select a valid proposal.');onReady?.(false);};img.src=fileUrl(options.mask_path);return()=>{img.onload=null;img.onerror=null;maskBase.current=null;};},[options.mask_path,onReady]);
  useEffect(()=>{draw()},[strokes,size]);
  const point=e=>{const b=e.currentTarget.getBoundingClientRect();return [Math.max(0,Math.min(1,(e.clientX-b.left)/b.width)),Math.max(0,Math.min(1,(e.clientY-b.top)/b.height))]};
  const finish=()=>{if(active.current){onChange({...options,strokes:[...strokes,active.current]});active.current=null}};
  return <section className="recolor-mask"><p>Paint only the surface to recolor. Erase windows, wheels, lights and background. White overlay shows your editable mask. A suggested mask is a layout heuristic and needs visual review. Original pixels outside the mask remain unchanged in PNG output.</p><div className="workspace-toolbar">{image.maskPath&&<button disabled={disabled} onClick={()=>onChange({...options,auto_mask:false,mask_path:image.maskPath,strokes:[]})}>Review suggested mask</button>}<button disabled={disabled} aria-pressed={!erase} onClick={()=>setErase(false)}>Paint</button><button disabled={disabled} aria-pressed={erase} onClick={()=>setErase(true)}>Erase</button><label>Brush<input type="range" min=".002" max=".12" step=".002" value={radius} onChange={e=>setRadius(Number(e.target.value))}/></label><label>Paint color<input aria-label="Recolor paint color" type="color" value={options.color||'#b5121b'} onChange={e=>onChange({...options,color:e.target.value})}/></label><button disabled={disabled||!strokes.length} onClick={()=>onChange({...options,strokes:strokes.slice(0,-1)})}>Undo mask stroke</button><button disabled={disabled} onClick={()=>onChange({...options,mask_path:'',strokes:[]})}>Clear mask</button></div><div className="mask-surface" style={{backgroundImage:`url("${fileUrl(image.path)}")`}}><canvas ref={canvas} width={size[0]} height={size[1]} aria-label="Paint recolor mask" onPointerDown={e=>{if(disabled||strokes.length>=1000)return;e.currentTarget.setPointerCapture(e.pointerId);active.current={radius,erase,points:[point(e)]};draw(active.current)}} onPointerMove={e=>{if(active.current&&active.current.points.length<5000){active.current.points.push(point(e));draw(active.current)}}} onPointerUp={finish} onPointerCancel={()=>{active.current=null;draw();}}/></div>{maskError&&<p role="alert">{maskError}</p>}<small>Use PNG to preserve untouched pixels exactly. This is material recoloring, not generation of new objects.</small></section>
}



export function MemoryControls({bridge,onSource}) {
  const [status,setStatus]=useState(null),[query,setQuery]=useState(''),[result,setResult]=useState(null),[episodes,setEpisodes]=useState([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const run=async(action,args={})=>{setError('');setBusy(true);try{return (await workspaceCall(bridge,action,args)).value}catch(e){setError(e.message);return null}finally{setBusy(false)}};
  const refresh=async()=>{const r=await run('memoryStatus');if(r)setStatus(r)};
  useEffect(()=>{refresh()},[bridge]);
  const search=async(offset=0)=>{const r=await run('memorySearch',{query,offset,limit:12,timeline:false});if(r)setResult(r)};
  return <div className="memory-controls local-workspace"><h3>Conversation memory</h3><p>Local search with source dates. Only conversations saved in OfflineGPT are available; external Codex chats need an explicit local import.</p>
    <label><input type="checkbox" checked={status?.enabled??false} disabled={!status||busy} onChange={async e=>{const r=await run('memoryEnable',{enabled:e.target.checked});if(r)setStatus(r)}}/> Enable local long-term recall</label>
    <button disabled={busy} onClick={async()=>{const path=await call(bridge,"pickMemoryImportFile");if(path){await run("memoryImport",{path});await refresh()}}}>Import local chat export or project record</button><p>{status?.indexedMessages??0} indexed messages · {status?.pendingChats??0} chats awaiting incremental indexing</p>
    <form className="workspace-toolbar" onSubmit={e=>{e.preventDefault();if(!busy)search()}}><input aria-label="Search past conversations" placeholder="Search decisions, timings or prior discussions" value={query} onChange={e=>setQuery(e.target.value)}/><button disabled={busy||!query.trim()}>Search</button><button type="button" disabled={busy} onClick={async()=>{await run('memoryRebuild');await refresh()}}>Rebuild index</button><button type="button" disabled={busy||!query.trim()} onClick={async()=>{const r=await run('memoryExport',{query});if(r)bridge?.revealFile?.(r.path)}}>Export results</button></form>
    {error&&<p role="alert">{error}</p>}{busy&&<p role="status">Processing local records…</p>}
    {result&&<section aria-label="Memory search results"><p>{result.total??0} matching records · {result.coverageComplete?'complete result set':'partial result set'}</p>{(result.sources||[]).map(source=><article className="model-placement-note" key={source.id}><button onClick={()=>onSource?.(source)||bridge?.navigateMemorySource?.(source.chatId,source.messageId)}>{source.title} · {source.id}</button><small>{source.date} · {source.evidenceKind}</small><p>{source.excerpt}</p></article>)}{result.nextOffset!=null&&<button onClick={()=>search(result.nextOffset)}>Next results</button>}</section>}
    <details><summary>Privacy and source exclusions</summary><p>Excluding a chat removes its derived index and summaries; its original conversation remains intact. Disabling recall stops retrieval and indexing without deleting chats.</p>{(status?.chats||[]).map(chat=><label key={chat.id} style={{display:'block'}}><input type="checkbox" checked={chat.excluded} disabled={busy} onChange={async e=>{const r=await run('memoryExclude',{chatId:chat.id,excluded:e.target.checked});if(r)setStatus(r)}}/> Exclude {chat.title}</label>)}</details>
    <details onToggle={async e=>{if(e.currentTarget.open){const r=await run('memoryEpisodes');if(r)setEpisodes(r)}}}><summary>Source-linked episodic summaries</summary><p>These are extractive notes, not verified implementation reports. A correction is labelled as a user edit. Hiding a summary preserves searchable source messages.</p>{episodes.map(episode=><EpisodeEditor key={episode.id} episode={episode} run={run} onHidden={()=>setEpisodes(v=>v.filter(x=>x.id!==episode.id))}/>)}</details>
  </div>
}
function EpisodeEditor({episode,run,onHidden}){const [text,setText]=useState(episode.summary),[saved,setSaved]=useState(false);return <article className="model-placement-note"><small>{episode.id} · {episode.corrected?'user-corrected':'extractive'}</small><textarea aria-label="Episodic summary" value={text} onChange={e=>setText(e.target.value)}/><div className="workspace-toolbar"><button onClick={async()=>{const r=await run('memoryCorrect',{id:episode.id,summary:text});if(r)setSaved(true)}}>{saved?'Correction saved':'Save correction'}</button><button onClick={async()=>{if(window.confirm('Hide this derived summary? Its source conversation stays intact.')){const r=await run('memoryCorrect',{id:episode.id,hide:true});if(r)onHidden()}}}>Hide summary</button></div></article>}

export function ProjectWorkspace({bridge,chatId,onSelectChat}){
  const [projects,setProjects]=useState([]),[selected,setSelected]=useState(''),[details,setDetails]=useState(null),[name,setName]=useState(''),[instructions,setInstructions]=useState(''),[note,setNote]=useState(''),[noteState,setNoteState]=useState('NOTE'),[reminders,setReminders]=useState([]),[reminderTitle,setReminderTitle]=useState(''),[due,setDue]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const run=async(action,args={})=>{setError('');setBusy(true);try{return(await workspaceCall(bridge,action,args)).value}catch(e){setError(e.message);return null}finally{setBusy(false)}};
  const refresh=async()=>{const r=await run('projectsList');if(r)setProjects(r);const rem=await run('remindersList');if(rem)setReminders(rem)};
  useEffect(()=>{refresh()},[bridge]);useEffect(()=>{let current=true;if(selected)run('projectDetails',{id:selected}).then(r=>r&&current&&setDetails(r));else setDetails(null);return()=>{current=false}},[selected]);
  return <section className="document-workspace workspace-view local-workspace"><div className="workspace-heading"><h1>Projects</h1><p>Group existing chats, their documents and images, notes and instructions locally.</p></div>{error&&<p role="alert">{error}</p>}
    <div className="workspace-toolbar"><select aria-label="Current project" value={selected} onChange={e=>{const id=e.target.value;setSelected(id);const p=projects.find(p=>p.id===id);setName(p?.name||'');setInstructions(p?.instructions||'')}}><option value="">New project</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name} ({p.chatCount} chats)</option>)}</select><input aria-label="Project name" placeholder="Project name" value={name} onChange={e=>setName(e.target.value)}/><button disabled={busy||!name.trim()} onClick={async()=>{const r=await run('projectSave',{id:selected,name,instructions});if(r){await refresh();setSelected(r.id)}}}>Save project</button><button disabled={busy||!selected||!chatId} onClick={async()=>{await run('projectAssign',{chatId,projectId:selected});setDetails(await run('projectDetails',{id:selected}));await refresh()}}>Add current chat</button></div>
    <label>Project instructions<textarea aria-label="Project instructions" value={instructions} onChange={e=>setInstructions(e.target.value)} placeholder="Preferences for this project. Application security and permissions still apply."/></label>
    {details&&<><h3>Conversations</h3><div className="workspace-toolbar">{details.chats.map(c=><button key={c.id} onClick={()=>onSelectChat?.(c.id)}>{c.title}</button>)}</div><h3>Documents and images</h3><div className="workspace-toolbar">{details.documents.map(path=><button key={path} onClick={()=>bridge?.openFile?.(path)}>{path.split(/[\\/]/).at(-1)}</button>)}{details.images.map(image=><button key={image.id} onClick={()=>bridge?.openImage?.(image.path)}>{image.path.split(/[\\/]/).at(-1)}</button>)}</div><h3>Notes and outstanding work</h3>{details.notes.map(n=><article className="model-placement-note" key={n.id}><strong>{n.title}</strong><small>{n.state} · user note, not runtime verification</small><p>{n.body}</p></article>)}<textarea aria-label="Project note" value={note} onChange={e=>setNote(e.target.value)} placeholder="Add a note, decision, or outstanding task"/><div className="workspace-toolbar"><select aria-label="Note status" value={noteState} onChange={e=>setNoteState(e.target.value)}>{['NOTE','REQUESTED','IMPLEMENTED_ACCORDING_TO_REPORT','USER_TESTED','VERIFIED','PARTIAL','UNRESOLVED'].map(x=><option key={x}>{x}</option>)}</select><button disabled={busy||!note.trim()} onClick={async()=>{await run('projectNote',{projectId:selected,title:note.slice(0,100),body:note,state:noteState});setNote('');setDetails(await run('projectDetails',{id:selected}))}}>Save note</button></div></>}
    <details><summary>Local reminders</summary><p>OfflineGPT must be running. Overdue reminders appear after restart. Reminders only notify; they never run scripts or commands.</p><div className="workspace-toolbar"><input aria-label="Reminder title" value={reminderTitle} onChange={e=>setReminderTitle(e.target.value)} placeholder="Reminder"/><input aria-label="Reminder time" type="datetime-local" value={due} onChange={e=>setDue(e.target.value)}/><button disabled={busy||!reminderTitle||!due} onClick={async()=>{await run('reminderCreate',{title:reminderTitle,due:new Date(due).getTime()/1000});setReminderTitle('');await refresh()}}>Set reminder</button></div>{reminders.map(r=><p key={r.id}>{r.title} · {new Date(r.due*1000).toLocaleString()} <button onClick={async()=>{await run('reminderDismiss',{id:r.id});await refresh()}}>Dismiss</button></p>)}</details>
  </section>
}
