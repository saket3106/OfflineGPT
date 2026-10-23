import React,{useEffect,useRef,useState} from 'react';
import {workspaceCall} from './workspace.jsx';
const parse=v=>{try{return JSON.parse(v)}catch{return {}}};
const call=(b,m,...a)=>new Promise(r=>b?.[m] ? b[m](...a,r):r(null));
const gib=n=>`${(n/1024**3).toFixed(2)} GiB`;

export function ModelManager({bridge,busy,onSelection,onHealth}){
 const [models,setModels]=useState([]),[query,setQuery]=useState(''),[running,setRunning]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[status,setStatus]=useState({}),[details,setDetails]=useState(''),[fit,setFit]=useState({});
 const active=useRef(true),pending=useRef('');
 const [discovered,setDiscovered]=useState([]);
 useEffect(()=>{active.current=true;return()=>{active.current=false}},[]);
 useEffect(()=>{const receive=raw=>{const e=parse(raw);if(e.type==='workspaceProgress' && e.requestId===pending.current)setNotice(e.message)};bridge?.event?.connect(receive);return()=>{try{bridge?.event?.disconnect(receive)}catch{}}},[bridge]);
 const request=async args=>workspaceCall(bridge,'modelLibrary',args,id=>{pending.current=id;setRunning(id)});
 const refresh=async()=>{const result=await request({operation:'list'});if(active.current){setModels(result.value.models);setStatus(result.value.status);onHealth?.(result.value.status)}};
 const run=async args=>{
  if(running || busy)return;
  setRunning('starting');setError('');setNotice('');
  try{const r=await request(args);if(args.operation==='discover'){setDiscovered(r.value.paths);setNotice(r.value.truncated?'Discovery bounded; narrow the folder to see more files.':'Found local GGUF filenames. Import individually to inspect compatibility.')}
   else if(args.operation==='estimate')setFit(old=>({...old,[args.id]:r.value}));
   else{if(args.operation==='select'){const settings=parse(await call(bridge,'getSettings'));onSelection?.(settings)}await refresh();setNotice(args.operation==='remove'?'Reference removed. Model files are preserved.':args.operation==='import'?'Model reference imported. Native compatibility is not yet verified.':args.operation==='load'?'Model loaded. This does not certify tool reliability.':args.operation==='unload'?'Model unloaded.':args.operation==='select'?'Default model selected. Conversations preserved.':'')}
  }catch(e){if(active.current)setError(e.message)}finally{pending.current='';if(active.current)setRunning('')}
 };
 useEffect(()=>{if(!bridge?.getModelLibraryVersion)return;setRunning('starting');refresh().catch(e=>active.current&&setError(e.message)).finally(()=>active.current&&setRunning(''))},[bridge]);
 const browse=async()=>{const selected=parse(await call(bridge,'choosePath','file'));if(selected?.path)run({operation:'import',path:selected.path})};
 const visible=models.filter(m=>(m.name+' '+m.path+' '+m.architecture).toLowerCase().includes(query.toLowerCase()));
 if(!bridge?.getModelLibraryVersion)return <section className="model-manager"><p>This application backend does not provide Model Manager. Keep using the existing Models &amp; chat settings until a matching backend update is installed.</p></section>;
 return <section className="model-manager" aria-label="Model Manager" aria-busy={!!running}>
  <div className="model-manager-toolbar"><button type="button" className="primary-button" disabled={!!running||busy} onClick={browse}>Import GGUF</button><button type="button" className="secondary-button" disabled={!!running||busy} onClick={async()=>{const result=parse(await call(bridge,'choosePath','directory'));if(result?.path)run({operation:'discover',directory:result.path})}}>Find in folder</button><button type="button" className="secondary-button" disabled={!!running||busy} onClick={()=>run({operation:'list'})}>Refresh</button><button type="button" className="secondary-button" disabled={!!running||busy||!status.modelExists||status.loaded||!status.runtimePresent} onClick={()=>run({operation:'load'})}>Load selected</button><button type="button" className="secondary-button" disabled={!!running||busy||!status.loaded} onClick={()=>run({operation:'unload'})}>Unload</button></div>
  <p className="model-manager-guidance">Import references existing files; weights are never copied or deleted here. Metadata describes a model, but does not verify native, vision or Agent capabilities.</p>
  <input className="model-manager-search" aria-label="Search installed models" placeholder="Search models or paths…" value={query} onChange={e=>setQuery(e.target.value)}/>
  {(running||notice)&&<div className="model-manager-feedback" role="status"><span>{notice||'Reading local models…'}</span>{running&&<button type="button" onClick={()=>bridge?.cancelWorkspace?.(pending.current)}>Cancel</button>}</div>}
  {error&&<p className="settings-feedback is-error" role="alert">{error}</p>}
  {!!discovered.length&&<details className="model-manager-discovery"><summary>{discovered.length} discovered GGUF filenames</summary>{discovered.map(path=><div className="model-manager-path" key={path}>{path} <button type="button" className="secondary-button" disabled={!!running||busy} onClick={()=>run({operation:'import',path})}>Inspect and import</button></div>)}</details>}
  {!models.length&&!running&&<div className="model-manager-empty"><strong>No model references imported</strong><p>Choose a local GGUF to inspect its architecture, template and hardware estimate.</p>{status.modelPath&&<button type="button" className="secondary-button" disabled={busy} onClick={()=>run({operation:'import',path:status.modelPath})}>Import current selection</button>}</div>}
  <div className="model-manager-list">{visible.map(m=><article className="model-manager-item" key={m.id}>
   <div className="model-manager-item-heading"><strong>{m.name}</strong><span>{status.modelPath?.toLowerCase()===m.path.toLowerCase()?'Default selection':m.fileState}</span></div>
   <div className="model-manager-meta">{m.architecture} · {m.quantizationLabel} · {gib(m.fileBytes)} · {m.contextMetadata?`${m.contextMetadata.toLocaleString()} declared context`:'Context unknown'}</div>
   <p className="model-manager-path" title={m.path}>{m.path}</p>
   <div className="model-manager-actions"><button type="button" className="secondary-button" disabled={!!running||busy||!m.selectable||m.fileState!=='present'} onClick={()=>run({operation:'select',id:m.id})}>Select as default</button><button type="button" className="secondary-button" onClick={()=>setDetails(details===m.id?'':m.id)} aria-expanded={details===m.id}>Details</button><button type="button" className="secondary-button" disabled={!!running||busy||!m.selectable||m.fileState!=='present'} onClick={()=>run({operation:'estimate',id:m.id,context:2048})}>Hardware estimate</button><button type="button" className="secondary-button" disabled={!!running||busy} onClick={()=>{if(window.confirm('Remove this reference from Model Manager? The model file stays on disk.'))run({operation:'remove',id:m.id})}}>Remove reference</button></div>
   {details===m.id&&<dl className="model-manager-details"><dt>Native capabilities verified</dt><dd>{m.executionVerified?.join(', ')||'None recorded for these exact inputs'}</dd><dt>Backend support</dt><dd>Not established for this exact binary</dd><dt>Selection</dt><dd>{m.selectionWarning}</dd><dt>Identity</dt><dd>Header and file observation only; tensor integrity unverified.</dd></dl>}
   {fit[m.id]&&<p className="model-manager-fit" role="status">Advisory CPU envelope: {fit[m.id].cpuEnvelopeBytes?gib(fit[m.id].cpuEnvelopeBytes):'unknown for this allocation architecture'}. {fit[m.id].cpuLikelyFits===false?'Exceeds current free RAM. ':''}GPU/hybrid allocations require measured validation. This estimate does not authorize loading.</p>}
  </article>)}</div>
 </section>
}
