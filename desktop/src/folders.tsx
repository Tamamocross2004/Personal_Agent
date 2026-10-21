import React, { useEffect, useRef, useState } from "react";

export function FolderLibrary({api,onClose}:{api:(path:string,options?:RequestInit)=>Promise<any>;onClose:()=>void}) {
  const [folders,setFolders]=useState<any[]>([]),[selected,setSelected]=useState("");
  const [policy,setPolicy]=useState<any>(null),[scan,setScan]=useState<any>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState("");
  const alive=useRef(true), selection=useRef(0);
  useEffect(()=>{alive.current=true;api('/folders').then(v=>{if(alive.current){setFolders(v.folders);setPolicy(v.policy);}}).catch(e=>{if(alive.current)setError(String(e));});
    const escape=(e:KeyboardEvent)=>{if(e.key==='Escape')onClose();};window.addEventListener('keydown',escape);
    return()=>{alive.current=false;selection.current++;window.removeEventListener('keydown',escape);};},[]);
  const action=async(work:()=>Promise<any>)=>{if(busy)return;setBusy(true);setError("");try{await work();}catch(e){if(alive.current)setError(String(e));}finally{if(alive.current)setBusy(false);}};
  const choose=async(id:string)=>{const turn=++selection.current;setSelected(id);setScan(null);setError("");
    try{const result=await api('/folders/'+id+'/scan');if(alive.current&&turn===selection.current)setScan(result);}catch(e){if(alive.current&&turn===selection.current)setError(String(e));}};
  return <div className="modal-overlay" onClick={onClose}><section className="modal folder-modal" onClick={e=>e.stopPropagation()}>
    <div className="modal-header"><h2>资料文件夹</h2><button onClick={onClose}>关闭</button></div>
    <p className="muted">只读浏览用户选定的本地目录。不会调用模型、嵌入或重排；源文件不会被修改。问答与建库将在后续阶段接入。</p>
    {policy&&<details><summary>扫描范围与排除规则</summary><p>最多{policy.max_entries}项、总计{policy.max_total_bytes/1000000}MB、单文件{policy.max_file_bytes/1000000}MB、深度{policy.max_depth}。支持：{policy.extensions.join('、')}。</p><p>排除：{[...policy.excluded_names,...policy.excluded_patterns].join('、')}，以及链接、重解析点、网络目录、云端占位、应用索引与任务产物。</p></details>}
    <div className="knowledge-actions"><button disabled={busy} onClick={()=>action(async()=>{const row=await window.desktop.pickFolder();if(row&&alive.current){setFolders((await api('/folders')).folders);setSelected(row.id);setScan(null);setError('已授权目录，请点击扫描。');}})}>选择资料目录</button>
      <select aria-label="资料目录" value={selected} disabled={busy} onChange={e=>choose(e.target.value)}><option value="" disabled>选择已授权目录</option>{folders.filter(f=>f.active).map(f=><option key={f.id} value={f.id}>{f.path}</option>)}</select>
      <button disabled={busy||!selected} onClick={()=>action(async()=>{const id=selected;const turn=++selection.current;const result=await api('/folders/'+id+'/scan',{method:'POST'});if(alive.current&&turn===selection.current)setScan(result);})}>{busy?'处理中…':'扫描目录'}</button>
      <button disabled={busy||!selected} onClick={()=>action(async()=>{await api('/folders/'+selected,{method:'DELETE'});if(alive.current){selection.current++;setSelected('');setScan(null);setFolders((await api('/folders')).folders);}})}>取消目录授权</button></div>
    {error&&<p role="status" className="error">{error}</p>}
    {scan&&<><p>本次记录{scan.entries.length}项 · {scan.total_bytes.toLocaleString()}字节 · {scan.complete?'扫描完成':'扫描未完成'}（不代表所有文件均可解析）</p>
      {scan.limits.map((x:string,i:number)=><p key={i} className="error">{x}</p>)}
      <div className="folder-file-list"><table><thead><tr><th>相对路径</th><th>类型</th><th>大小</th><th>修改时间</th><th>状态 / 原因</th></tr></thead><tbody>{scan.entries.map((f:any)=><tr key={f.id}><td style={{paddingLeft:Math.min(f.relative_path.split('/').length-1,12)*12+8}}>{f.relative_path}</td><td>{f.kind==='directory'?'目录':f.suffix||'—'}</td><td>{f.size??'—'}</td><td>{f.modified_ns?new Date(f.modified_ns/1000000).toLocaleString():'—'}</td><td>{({ready:'可读取',excluded:'已排除',unsupported:'暂不支持',unreadable:'不可读取',over_limit:'超出限制'} as any)[f.status]} {f.reason}</td></tr>)}</tbody></table></div></>}
    <p className="muted">取消授权会禁止后续读取，但保留已有扫描清单；重新授权不会自动扫描。</p>
  </section></div>;
}
