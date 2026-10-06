/* Native dialog lifecycle: single initialization, reviewed-input and viewer guards. */
(() => {
  "use strict";
  const $=id=>document.getElementById(id);
  const escape=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const UI_KEY="dockup.homology.workspace.v2";
  let dialog,stage,template,capabilities,currentJob,modelOutput,timer,initPromise,pollPromise;
  let reviewed=false,uploading=false,previewing=false,submitting=false,revision=0,viewRevision=0,viewMode="template";
  function isRunning(){return currentJob && ["queued","running"].includes(currentJob.state);}
  function status(message,error=false){$("hmStatus").textContent=message;$("hmStatus").classList.toggle("error",error);}
  async function api(path,body){
    const response=await fetch("/api/modeling/"+path,body===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const result=await response.json();
    if(!response.ok)throw new Error(typeof result.detail==="string"?result.detail:JSON.stringify(result.detail));
    return result;
  }
  function targets(){
    return [...$("hmTargets").children].map(row=>({
      chain:row.querySelector('[name="chain"]').value,
      template_chain:row.querySelector('[name="template_chain"]').value,
      sequence:row.querySelector('[name="sequence"]').value.replace(/\s/g,""),
      subtype:row.querySelector('[name="subtype"]').value,
      target_start:Number(row.querySelector('[name="target_start"]').value),
      aligned_target:row.querySelector('[name="aligned_target"]').value||null,
      aligned_template:row.querySelector('[name="aligned_template"]').value||null,
    }));
  }
  function request(){
    if(!template)throw new Error("Upload a template first.");
    return {template_id:template.id,name:$("hmName").value,targets:targets(),timeout_seconds:Number($("hmTimeout").value),threads:Number($("hmThreads").value),refine:$("hmRefine").checked};
  }
  function save(){
    if(!dialog)return;
    try{sessionStorage.setItem(UI_KEY,JSON.stringify({template,name:$("hmName").value,targets:targets(),timeout:$("hmTimeout").value,threads:$("hmThreads").value,refine:$("hmRefine").checked,jobId:currentJob?.id,formScroll:$("hmForm").scrollTop}));}catch(_){}
  }
  function syncButtons(){
    const busy=uploading||previewing||submitting;
    $("hmAddChain").disabled=!template||uploading;
    $("hmPreview").disabled=!template||!targets().length||busy;
    $("hmBuild").disabled=!reviewed||busy||isRunning()||capabilities?.homology?.status!=="ready";
    $("hmPreview").textContent=previewing?"Reviewing…":"Review alignment";
    $("hmBuild").textContent=submitting?"Queuing…":isRunning()?"Model running…":"Build model";
    $("hmViewTemplate").disabled=!template||uploading;
    $("hmViewModel").disabled=!modelOutput;
    $("hmDownload").disabled=!modelOutput;
    $("hmPublish").disabled=!modelOutput?.output.assessment.basic_geometry_passed;
    for(const [id,mode] of [["hmViewTemplate","template"],["hmViewModel","model"]]){
      $(id).classList.toggle("active",viewMode===mode);$(id).setAttribute("aria-pressed",String(viewMode===mode));
    }
  }
  function invalidate(){
    revision++;reviewed=false;syncButtons();
    $("hmAlignment").textContent="Inputs changed · review the alignment before building.";
    if(template)$("hmCLI").textContent="python -m docking_app.cli homology build --payload '"+JSON.stringify(request()).replace(/'/g,"'\\''")+"'";
    save();
  }
  function addChain(saved){
    if(!template)return;
    const index=$("hmTargets").children.length,source=template.chains[Math.min(index,template.chains.length-1)];
    const used=new Set(targets().map(t=>t.chain));
    const free="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789".split("").find(c=>!used.has(c))||"A";
    const value=saved||{chain:free,subtype:"target",template_chain:source.chain,sequence:source.sequence,target_start:1};
    const row=document.createElement("div");row.className="hm-target-row";
    row.innerHTML=`<div class="hm-chain-header"><strong>Target chain</strong><button type="button" class="hm-remove">Remove</button></div>
      <div class="qm-grid"><label>Output chain<input name="chain" value="${escape(value.chain)}" maxlength="1" required></label><label>Subunit type<input name="subtype" value="${escape(value.subtype)}" maxlength="32"></label>
      <label>Template chain<select name="template_chain">${template.chains.map(c=>`<option value="${escape(c.chain)}" ${c.chain===value.template_chain?"selected":""}>${escape(c.chain)} · ${c.residues} residues</option>`).join("")}</select></label>
      <label>Target numbering start<input name="target_start" value="${value.target_start}" type="number" min="1" max="10000" required></label></div>
      <label>Target amino-acid sequence<textarea name="sequence" spellcheck="false" required>${escape(value.sequence)}</textarea></label>
      <details><summary>Explicit reviewed alignment</summary><label>Aligned target<textarea name="aligned_target" spellcheck="false">${escape(value.aligned_target||"")}</textarea></label><label>Aligned template<textarea name="aligned_template" spellcheck="false">${escape(value.aligned_template||"")}</textarea></label></details>`;
    row.querySelector(".hm-remove").onclick=()=>{row.remove();invalidate();};
    $("hmTargets").append(row);invalidate();
  }
  function templateInfo(){
    $("hmTemplateInfo").textContent=template?`${template.filename} · ${template.chains.length} protein chains · ${template.sha256.slice(0,12)}`:"Upload a template to inspect chains and sequences.";
  }
  async function view(mode){
    if(!stage)return;
    const entry=mode==="model"?modelOutput:null;
    if(mode==="model"&&!entry||mode==="template"&&!template)return;
    const token=++viewRevision;
    const url=entry?`/api/modeling/jobs/${entry.jobId}/artifacts/${entry.index}`:`/api/modeling/homology/templates/${template.id}/structure`;
    stage.removeAllComponents();viewMode=mode;syncButtons();
    const component=await stage.loadFile(url,{ext:"pdb"});
    if(token!==viewRevision){stage.removeComponent(component);return;}
    component.addRepresentation("cartoon",{colorScheme:"chainname"});
    component.addRepresentation("ball+stick",{sele:"hetero"});
    stage.autoView();$("hmViewerTitle").textContent=entry?entry.output.label:"Experimental template";
  }
  async function poll(){
    if(!currentJob||pollPromise)return pollPromise;
    const id=currentJob.id;
    pollPromise=(async()=>{
      try{
        const job=await api("jobs/"+id);
        if(currentJob?.id!==id)return;
        currentJob=job;save();
        if(!dialog.open)return;
        $("hmLog").textContent=job.log||"";
        status("Model job · "+job.state.replaceAll("_"," "));
        syncButtons();
        if(isRunning())return;
        clearInterval(timer);
        const index=(job.result.outputs||[]).findIndex(o=>o.kind==="receptor");
        if(index<0){status(job.result.reason||"Model building did not complete; inspect worker diagnostics.",true);return;}
        const output=job.result.outputs[index],changed=modelOutput?.jobId!==id;
        modelOutput={jobId:id,index,output};
        $("hmAssessment").textContent=JSON.stringify(output.assessment,null,2);
        syncButtons();
        if(changed)await view("model");
      }catch(error){if(dialog.open)status(error.message,true);}
      finally{pollPromise=null;}
    })();
    return pollPromise;
  }
  function startPolling(){
    clearInterval(timer);
    if(dialog?.open&&isRunning())timer=setInterval(()=>{if(!document.hidden)poll();},1500);
  }
  async function upload(){
    const file=$("hmTemplate").files[0];if(!file)return;
    const token=++revision;uploading=true;reviewed=false;template=null;
    syncButtons();status("Reading template…");
    try{
      const data=new FormData();data.append("file",file);
      const response=await fetch("/api/modeling/homology/templates",{method:"POST",body:data});
      const result=await response.json();
      if(token!==revision)return;
      if(!response.ok)throw new Error(typeof result.detail==="string"?result.detail:JSON.stringify(result.detail));
      template=result;$("hmTargets").replaceChildren();addChain();templateInfo();
      $("hmAlignment").textContent="Template loaded. Review the target alignment before building.";
      status("Template ready · review the target chain mapping.");
      if(dialog.open)await view("template");
    }catch(error){if(token===revision)status(error.message,true);}
    finally{uploading=false;syncButtons();save();}
  }
  async function preview(){
    if(!template||uploading||previewing)return;
    if(!$("hmForm").reportValidity())return;
    const token=revision,body=request();previewing=true;reviewed=false;syncButtons();
    try{
      const result=await api("homology/alignment",body);
      if(token!==revision)return; // Edited inputs cannot inherit a stale review.
      $("hmAlignment").textContent=result.targets.map(r=>`Chain ${r.chain} · ${r.subtype}\nIdentity ${(r.identity*100).toFixed(1)}% · coverage ${(r.coverage*100).toFixed(1)}%\nTarget   ${r.aligned_target}\nTemplate ${r.aligned_template}`).join("\n\n");
      reviewed=true;status("Alignment ready. Inspect critical regions before building.");
    }catch(error){status(error.message,true);}
    finally{previewing=false;syncButtons();save();}
  }
  async function build(event){
    event.preventDefault();
    if(!reviewed||submitting||isRunning())return;
    submitting=true;syncButtons();
    try{
      const body=request(),job=await api("homology/jobs",body);
      currentJob=job;modelOutput=null;$("hmAssessment").textContent="Model reconstruction in progress.";
      save();startPolling();await poll();
    }catch(error){status(error.message,true);}
    finally{submitting=false;syncButtons();}
  }
  async function initialize(){
    const response=await fetch("/api/modeling/homology/dialog");
    if(!response.ok)throw new Error("Could not load homology dialog.");
    document.body.insertAdjacentHTML("beforeend",await response.text());dialog=$("hmDialog");
    $("hmClose").onclick=()=>dialog.close();
    dialog.addEventListener("close",()=>{save();clearInterval(timer);viewRevision++;});
    $("hmFit").onclick=()=>stage?.autoView();
    $("hmAddChain").onclick=()=>addChain();
    $("hmForm").addEventListener("input",invalidate);
    $("hmTemplate").onchange=upload;$("hmPreview").onclick=preview;$("hmForm").onsubmit=build;
    $("hmViewTemplate").onclick=()=>view("template").catch(e=>status(e.message,true));
    $("hmViewModel").onclick=()=>view("model").catch(e=>status(e.message,true));
    $("hmDownload").onclick=()=>{
      if(!modelOutput)return;
      const a=document.createElement("a");a.href=`/api/modeling/jobs/${modelOutput.jobId}/artifacts/${modelOutput.index}`;a.download="model.pdb";a.click();
    };
    $("hmPublish").onclick=async()=>{
      if(!modelOutput)return;
      const entry=modelOutput;$("hmPublish").disabled=true;
      try{
        const result=await api(`jobs/${entry.jobId}/publish`,{artifact_index:entry.index});
        status("Added "+result.filename+". Select it in Stored Receptors; choose its grid separately.");
        if(typeof window.refreshReceptorFiles==="function")await window.refreshReceptorFiles();
        if(typeof window.refreshReceptorSummary==="function")await window.refreshReceptorSummary();
      }catch(error){status(error.message,true);}
      finally{syncButtons();}
    };
    let saved;try{saved=JSON.parse(sessionStorage.getItem(UI_KEY)||"null");}catch(_){}
    if(saved?.template){
      template=saved.template;$("hmName").value=saved.name||"Comparative receptor model";
      $("hmTimeout").value=saved.timeout||300;$("hmThreads").value=saved.threads||2;$("hmRefine").checked=saved.refine!==false;
      for(const row of saved.targets||[])addChain(row);
      if(saved.jobId)currentJob={id:saved.jobId,state:"queued"};
      templateInfo();$("hmForm").scrollTop=saved.formScroll||0;
    }
    syncButtons();
  }
  async function open(){
    try{
      if(!initPromise)initPromise=initialize().catch(error=>{initPromise=null;throw error;});
      await initPromise;
      if(!dialog.open)dialog.showModal();
      if(!stage&&window.NGL){stage=new NGL.Stage("hmViewport",{backgroundColor:"#f8fafc"});new ResizeObserver(()=>stage.handleResize()).observe($("hmViewport"));}
      stage?.handleResize();
      if(template)await view(modelOutput?viewMode:"template");
      if(!capabilities)capabilities=await api("capabilities");
      if(!dialog.open)return;
      syncButtons();
      if(currentJob){await poll();startPolling();}
      else status(capabilities.homology?.status==="ready"?"ProMod3 ready · upload a template or review the restored alignment":capabilities.homology?.reason||"ProMod3 runtime not configured");
    }catch(error){console.error(error);if($("hmStatus"))status(error.message,true);}
  }
  window.addEventListener("pagehide",()=>{save();clearInterval(timer);});
  window.addEventListener("pageshow",()=>{if(dialog?.open){stage?.handleResize();startPolling();poll();}});
  document.addEventListener("visibilitychange",()=>{if(!document.hidden&&dialog?.open){stage?.handleResize();poll();}});
  document.getElementById("openHomologyPopup")?.addEventListener("click",open);
})();
