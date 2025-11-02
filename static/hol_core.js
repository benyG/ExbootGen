// ===== Utilities =====
const deepClone = (o) => JSON.parse(JSON.stringify(o));
const seededRandom = (seedStr) => { function xmur3(str){let h=1779033703^str.length;for(let i=0;i<str.length;i++){h=Math.imul(h^str.charCodeAt(i),3432918353);h=(h<<13)|(h>>>19);}return function(){h=Math.imul(h^(h>>>16),2246822507);h=Math.imul(h^(h>>>13),3266489909);h^=h>>>16;return h>>>0;};} function mulberry32(a){return function(){let t=(a+=0x6d2b79f5);t=Math.imul(t^(t>>>15),t|1);t^=t+Math.imul(t^(t>>>7),t|61);return ((t^(t>>>14))>>>0)/4294967296;};} return mulberry32(xmur3(seedStr)()); };
const templateString = (s,v)=> s.replace(/\{\{(.*?)\}\}/g,(_,k)=> (v[k.trim()]??"")+"");
const templateAny=(val,v)=> typeof val==="string"?templateString(val,v):Array.isArray(val)?val.map(x=>templateAny(x,v)):(val&&typeof val==="object"?Object.fromEntries(Object.entries(val).map(([k,x])=>[k,templateAny(x,v)])):val);
const stableStringify=(val)=>{
  if(val===null||typeof val!=='object') return JSON.stringify(val);
  if(Array.isArray(val)) return '['+val.map(stableStringify).join(',')+']';
  return '{'+Object.keys(val).sort().map(k=> JSON.stringify(k)+':'+stableStringify(val[k])).join(',')+'}';
};
const deepEqual=(a,b)=> stableStringify(a)===stableStringify(b);
const getByPath=(o,p)=> p.split('.').reduce((a,k)=> (a==null?undefined:a[k]), o);
const setByPath=(o,p,val)=>{const parts=p.split('.');let cur=o;for(let i=0;i<parts.length-1;i++){if(cur[parts[i]]==null)cur[parts[i]]={};cur=cur[parts[i]];}cur[parts[parts.length-1]]=val;};
const unsetByPath=(o,p)=>{
  const parts=p.split('.');
  if(!parts.length) return;
  let parent=o;
  for(let i=0;i<parts.length-1;i++){
    if(parent==null) return;
    parent=parent[parts[i]];
  }
  if(parent==null) return;
  const last=parts[parts.length-1];
  if(Array.isArray(parent)){
    const idx=Number(last);
    if(Number.isInteger(idx) && idx>=0){ parent.splice(idx,1); return; }
    const fallbackIndex=parent.findIndex(item=> item && typeof item==='object' && (item.id===last || item.key===last));
    if(fallbackIndex>=0) parent.splice(fallbackIndex,1);
  } else {
    delete parent[last];
  }
};
const applyWorldPatch=(world,patch,vars)=>{
  for(const p of (patch||[])){
    if(!p || !p.op) continue;
    const op=String(p.op).toLowerCase();
    const pathRaw=p.path||'';
    const path=templateString(pathRaw,vars);
    if(!path) continue;
    if(op==='set'){
      setByPath(world, path, templateAny(p.value,vars));
      continue;
    }
    if(op==='unset'){
      unsetByPath(world, path);
      continue;
    }
    if(op==='push'){
      let target=getByPath(world, path);
      if(!Array.isArray(target)){
        setByPath(world, path, []);
        target=getByPath(world, path);
        if(!Array.isArray(target)) continue;
      }
      const val=p.value===undefined?undefined:templateAny(p.value,vars);
      if(val===undefined) continue;
      if(Array.isArray(val)) target.push(...val);
      else target.push(val);
      continue;
    }
    if(op==='remove'){
      const target=getByPath(world, path);
      const val=p.value===undefined?undefined:templateAny(p.value,vars);
      if(Array.isArray(target)){
        if(val===undefined){ target.pop(); continue; }
        let idx=target.findIndex(item=> deepEqual(item,val));
        if(idx<0 && val && typeof val==='object' && val.id!==undefined){ idx=target.findIndex(item=> item && typeof item==='object' && item.id===val.id); }
        if(idx<0 && typeof val==='string'){ idx=target.findIndex(item=> item && typeof item==='object' && (item.id===val || item.name===val)); }
        if(idx>=0){ target.splice(idx,1); continue; }
      }
      unsetByPath(world, path);
    }
  }
};
const md = (s='')=> s.replace(/\*\*(.*?)\*\*/g,'<strong>$1</strong>').replace(/`([^`]+)`/g,'<code>$1</code>');

function normalizeIconSpec(icon){
  if(!icon) return null;
  if(typeof icon==='string'){
    const trimmed=icon.trim();
    if(!trimmed) return null;
    if(/^data:image\//.test(trimmed) || /^https?:\/\//.test(trimmed) || trimmed.startsWith('/')){
      return { kind:'image', src:trimmed, alt:'' };
    }
    return { kind:'text', text:trimmed };
  }
  if(typeof icon==='object'){
    if((icon.kind==='image' || icon.type==='image') && (icon.src||icon.url||icon.href)){
      return { kind:'image', src:icon.src||icon.url||icon.href, alt:icon.alt||icon.label||'' };
    }
    if(icon.kind==='text' || icon.type==='text' || icon.kind==='emoji'){
      if(icon.text||icon.value||icon.emoji) return { kind:'text', text:icon.text||icon.value||icon.emoji };
    }
  }
  return null;
}

function iconsEqual(a,b){
  const na=normalizeIconSpec(a);
  const nb=normalizeIconSpec(b);
  if(!na && !nb) return true;
  if(!na || !nb) return false;
  if(na.kind!==nb.kind) return false;
  if(na.kind==='image') return na.src===nb.src && na.alt===nb.alt;
  return na.text===nb.text;
}

function setIconElementContent(el, iconRaw){
  el.innerHTML='';
  const icon=normalizeIconSpec(iconRaw);
  if(!icon) return;
  if(icon.kind==='image'){
    const img=document.createElement('img');
    img.src=icon.src;
    img.alt=icon.alt||'';
    el.appendChild(img);
  } else {
    el.textContent=icon.text;
  }
}

function buildChipElement(tagName, item){
  const chip=document.createElement(tagName);
  if(tagName==='button') chip.type='button';
  chip.className='chip';
  chip.dataset.componentId=item.id;
  if(item.description) chip.title=item.description;
  const iconSpan=document.createElement('span');
  iconSpan.className='chip-icon';
  setIconElementContent(iconSpan, item.icon);
  const labelSpan=document.createElement('span');
  labelSpan.className='chip-label';
  labelSpan.textContent=item.label || item.id;
  chip.appendChild(iconSpan);
  chip.appendChild(labelSpan);
  return chip;
}

function ensurePaletteHasDecoy(palette){
  const items = (Array.isArray(palette) ? palette : []).map(item => ({ ...item }));
  const hasDecoy = items.some(item => item && (item.is_decoy || item.decoy));
  if(!hasDecoy){
    const existing = new Set(items.map(item => item && item.id).filter(Boolean));
    let idx = 1;
    let decoyId = 'decoy';
    while(existing.has(decoyId)){
      idx += 1;
      decoyId = `decoy_${idx}`;
    }
    items.push({
      id: decoyId,
      label: 'Composant leurre',
      icon: '🧱',
      description: "Élément distrayant qui n'est pas utile pour la solution.",
      tags: ['decoy'],
      is_decoy: true
    });
  }
  return items;
}

function shuffleArray(items){
  const arr = Array.isArray(items) ? [...items] : [];
  for(let i = arr.length - 1; i > 0; i--){
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

function normalizeArchitectureForCompare(payload){
  if(!payload || typeof payload!=='object') return payload;
  const clone=deepClone(payload);
  if(Array.isArray(clone.nodes)){
    clone.nodes=clone.nodes.map(node=>{
      const next=deepClone(node);
      if(Array.isArray(next.tags)) next.tags=[...next.tags].sort();
      return next;
    }).sort((a,b)=> String(a.id||'').localeCompare(String(b.id||'')));
  }
  if(Array.isArray(clone.links)){
    clone.links=[...clone.links].sort((a,b)=> String(a.id||'').localeCompare(String(b.id||'')));
  }
  if(clone.summary){
    const sum=clone.summary;
    if(sum.type_connections){
      sum.type_connections=[...sum.type_connections].sort((a,b)=>{
        const fa=String(a.from||'');
        const fb=String(b.from||'');
        if(fa!==fb) return fa.localeCompare(fb);
        return String(a.to||'').localeCompare(String(b.to||''));
      });
    }
  }
  return clone;
}

function runValidators(validators, payload, worldOverride){
  const errors=[];
  const worldCtx = worldOverride || state.world;
  for(const v of (validators||[])){
    if(v.kind==='world'){
      const rawPath = v.path || v.expect?.path || '';
      const path = templateString(rawPath, state.vars);
      const got = getByPath(worldCtx, path);
      const wantRaw = v.equals!==undefined ? v.equals : v.expect?.equals;
      const want = wantRaw===undefined ? undefined : templateAny(wantRaw, state.vars);
      const ok = want===undefined ? got!==undefined : JSON.stringify(got)===JSON.stringify(want);
      if(!ok){
        errors.push(templateString(v.message || `Condition monde non satisfaite (${path})`, state.vars));
      }
    } else if (v.kind==='expression'){
      let ok=false;
      try{ ok = !!evalExpr(v.expr, { world: worldCtx, vars: state.vars, payload }); }
      catch{ errors.push(templateString(v.error_message || v.message || 'Expression invalide', state.vars)); continue; }
      if(!ok){
        errors.push(templateString(v.message || 'Expression non vérifiée', state.vars));
      }
    } else if (v.kind==='payload'){
      const path = v.path || v.expect?.path || '';
      const got = getByPath(payload||{}, path);
      const wantRaw = v.equals!==undefined ? v.equals : v.expect?.equals;
      const want = wantRaw===undefined ? undefined : templateAny(wantRaw, state.vars);
      const ok = want===undefined ? got!==undefined : JSON.stringify(got)===JSON.stringify(want);
      if(!ok){
        errors.push(templateString(v.message || `Condition payload non satisfaite (${path})`, state.vars));
      }
    }
  }
  return { ok: errors.length===0, errors };
}

// command parsing
function tokenize(cmd){const out=[];let cur="";let q=null;for(let i=0;i<cmd.length;i++){const c=cmd[i];if(q){if(c===q)q=null;else cur+=c;}else{if(c==='"'||c==="'")q=c;else if(c===' '){if(cur){out.push(cur);cur="";}}else cur+=c;}}if(cur)out.push(cur);return out;}
function parseCommand(line){const t=tokenize(line.trim());const program=t[0]||"";let idx=1;const subcmd=[];while(idx<t.length && !t[idx].startsWith('-')){subcmd.push(t[idx]);idx++;}const flags={};while(idx<t.length){const tok=t[idx];if(tok.startsWith('--')){const [k,v]=tok.split('=');if(v!=null){flags[k]=v;idx++;continue;}if(idx+1<t.length && !t[idx+1].startsWith('-')){flags[k]=t[idx+1];idx+=2;}else{flags[k]=true;idx++;}}else if(tok.startsWith('-')){const k=tok;if(idx+1<t.length && !t[idx+1].startsWith('-')){flags[k]=t[idx+1];idx+=2;}else{flags[k]=true;idx++;}}else{idx++;}}return { program, subcmd, flags };}

// minimal jsonpath: $.a.b and $.arr[*].k and * wildcard
function jsonPathGetAll(obj, path){ if(!path.startsWith('$')) return []; const parts=path.replace(/^\$\.?/, "").split('.'); let cur=[obj]; for(const part of parts){ const next=[]; const m=part.match(/(\w+)\[(\*)\]/); if(m){ const key=m[1]; for(const c of cur){ const arr=(c||{})[key]; if(Array.isArray(arr)) next.push(...arr); } } else if (part==='*'){ for(const c of cur) if(c && typeof c==='object') next.push(...Object.values(c)); } else { for(const c of cur) if(c) next.push(c[part]); } cur=next.filter(x=> x!==undefined); } return cur; }
function evalExpr(expr, ctx){ const get=(p)=> getByPath({world:ctx.world, vars:ctx.vars, payload:ctx.payload}, p); const safe=expr.replace(/get\(([^)]+)\)/g,(_,g1)=>{ const key=g1.trim().replace(/^['"]|['"]$/g,''); const val=JSON.stringify(get(key)); return val===undefined? 'null': val; }).replace(/\bundefined\b/g,'null'); return Function('"use strict"; return ('+safe+');')(); }

// ===== State =====
const DEMO = {
  schema_version: "0.2.0",
  lab: {
    id: "s3-secure-mini",
    title: "Sécuriser un bucket S3 (démo)",
    subtitle: "Terminal + Console + Inspect + Quiz",
    scenario_md: "Ce scénario de démonstration illustre comment le player orchestre une suite d'étapes complémentaires.\n\nVous incarnez un·e ingénieur·e cloud chargé·e de sécuriser un bucket S3 exposé. Chaque phase montre comment les validations mettent à jour l'état simulé et débloquent l'étape suivante.",
    variables: {
      bucket_name: { type: "choice", choices: ["acme-audit","contoso-audit","globex-audit"] },
      region: { type: "choice", choices: ["us-east-1","eu-west-1"] }
    },
    scoring: { max_points: 80 },
    timer: { mode: "countdown", seconds: 900 },
    assets: [
      { id: "policy_bad.json", kind: "file", mime: "application/json", inline: true, content_b64: btoa(JSON.stringify({ Version: "2012-10-17", Statement: [{ Effect: "Allow", Principal: "*", Action: ["s3:ListBucket"], Resource: "*" }] })) }
    ],
    steps: [
      { id:"create-bucket", type:"terminal", title:"Créer le bucket",
        instructions_md:"Crée le bucket **{{bucket_name}}** dans **{{region}}**.",
        terminal:{ prompt:"user@vm:~$", validators:[{ kind:"command", match:{ program:"aws", subcommand:["s3api","create-bucket"], flags:{ required:["--bucket","--region"], aliases:{ "-b":"--bucket" } }, args:[ {flag:"--bucket", expect:"{{bucket_name}}"}, {flag:"--region", expect:"{{region}}"} ] }, response:{ stdout_template:"{\n  \"Location\": \"/{{bucket_name}}\"\n}\n", world_patch:[ {op:"set", path:"s3.buckets.{{bucket_name}}.region", value:"{{region}}"}, {op:"set", path:"s3.buckets.{{bucket_name}}.versioning", value:"Disabled"} ] } }] },
        hints:["Utilise **aws s3api create-bucket** avec --bucket et --region."], points:20,
        transitions:{ on_success:"enable-versioning", on_failure:"#stay" }
      },
      { id:"enable-versioning", type:"console_form", title:"Activer le versioning",
        instructions_md:"Active le versioning du bucket **{{bucket_name}}**.",
        form:{ model_path:"s3.buckets.{{bucket_name}}", schema:{ fields:[ {key:"versioning", widget:"toggle", label:"Bucket Versioning", options:["Disabled","Enabled"] } ] } },
        validators:[ { kind:"world", expect:{ path:"s3.buckets.{{bucket_name}}.versioning", equals:"Enabled" } }, { kind:"expression", expr:"get('world.s3.buckets.' + get('vars.bucket_name') + '.versioning')==='Enabled'" } ], points:20, transitions:{ on_success:"inspect-policy" }
      },
      { id:"inspect-policy", type:"inspect_file", title:"Analyser et corriger la policy",
        instructions_md:"Le fichier **policy_bad.json** est fourni. Corrige pour **interdire** l'accès anonyme au ListBucket.", file_ref:"policy_bad.json", input:{ widget:"text_area", language:"json" },
        validators:[ {kind:"jsonschema", ref:"local://aws-policy.schema.json"}, {kind:"jsonpath_absent", jsonpath:"$.Statement[*].Principal", equals:"*"}, {kind:"expression", expr:"Array.isArray(get('payload.Statement')) && get('payload.Statement').every(s=>s.Principal!=='*')" }],
        points:20, transitions:{ on_success:"quiz-impact" }
      },
      { id:"quiz-impact", type:"quiz", title:"Bloquer l'accès public",
        question_md:"Bloquer l'accès public empêche…?", choices:[{id:"a",text:"Toutes les requêtes signées"},{id:"b",text:"L'accès anonyme"}], correct:["b"], points:20,
        transitions:{ on_success:"#end" }
      }
    ]
  }
};

const state = {
  lab: DEMO,
  world: {},
  vars: {},
  score: 0,
  currentId: DEMO.lab.steps[0].id,
  startTs: Date.now(),
  remain: DEMO.lab.timer?.seconds || 0
};

function drawVariables(varsSpec, seed){ const rnd=seededRandom(seed); const v={}; for(const [k,s] of Object.entries(varsSpec||{})){ if(s.type==='choice'){ const choices=s.choices; v[k]=choices[Math.floor(rnd()*choices.length)]; } else if(s.type==='number'){ v[k]=Math.floor(s.min + rnd()*(s.max-s.min+1)); } } return v; }
function resolve(obj){ return templateAny(obj, state.vars); }

// ===== UI Helpers =====
function setText(id, html){ const el=document.getElementById(id); if(el) el.innerHTML=html; }
function show(el, flag){ el.style.display = flag? 'block':'none'; }
function setScore(){ document.getElementById('score').textContent = 'Score: '+state.score; }
function setWorld(){ document.getElementById('world-pre').textContent = JSON.stringify(state.world, null, 2); }
function setTimer(){ const m=Math.floor(state.remain/60), s=String(state.remain%60).padStart(2,'0'); document.getElementById('timer').textContent = `${m}:${s}`; }

// Tabs
Array.from(document.getElementsByClassName('tab')).forEach(t=>{
  t.addEventListener('click', ()=>{
    Array.from(document.getElementsByClassName('tab')).forEach(x=>x.classList.remove('active'));
    t.classList.add('active');
    const which=t.dataset.tab;
    document.getElementById('view-player').style.display= which==='player'? 'block':'none';
    document.getElementById('view-json').style.display  = which==='json'? 'block':'none';
    document.getElementById('view-world').style.display = which==='world'? 'block':'none';
  });
});

// JSON panel
const jsonArea = document.getElementById('json-area');
jsonArea.value = JSON.stringify(DEMO, null, 2);
document.getElementById('btn-load').onclick = ()=>{
  try{
    const obj = JSON.parse(jsonArea.value);
    // soft schema check
    if(!obj || !obj.schema_version || !obj.lab || !Array.isArray(obj.lab.steps)) throw new Error('Schéma basique invalide');
    document.getElementById('schemaStatus').textContent = 'Schéma: OK (soft)';
    document.getElementById('schema-errors').innerHTML = '';
    state.lab = obj;
    boot();
  }catch(e){
    document.getElementById('schemaStatus').textContent = 'Schéma: KO';
    document.getElementById('schema-errors').innerHTML = `<div class="ko">${e.message}</div>`;
  }
};

// ===== Step renderers =====
function renderStep(){
  const step = state.lab.lab.steps.find(s=> s.id===state.currentId);
  const r = resolve(step);
  setText('lab-title', state.lab.lab.title);
  setText('lab-subtitle', state.lab.lab.subtitle||'');
  const scenarioEl = document.getElementById('lab-context');
  if(scenarioEl){
    const scenarioHtml = state.lab.lab.scenario_md ? md(state.lab.lab.scenario_md) : '';
    scenarioEl.innerHTML = scenarioHtml;
    scenarioEl.style.display = scenarioHtml ? 'block' : 'none';
  }
  setText('step-title', r.title || r.id);
  setText('step-instr', md(r.instructions_md||''));
  const body = document.getElementById('step-body');
  const feedback = document.getElementById('step-feedback');
  feedback.innerHTML = '';
  body.innerHTML = '';

  // hints
  const btnHint = document.getElementById('btn-hint');
  if((r.hints||[]).length){ btnHint.style.display='inline-block'; btnHint.onclick = ()=>{ feedback.innerHTML = `<div class="hint">${r.hints[0]}</div>`; }; }
  else btnHint.style.display='none';

  if(r.type==='terminal'){
    const term = document.createElement('div'); term.className='terminal'; term.innerHTML = '';
    const row = document.createElement('div'); row.className='row'; row.style.marginTop = '8px';
    const prompt = document.createElement('span'); prompt.textContent = r.terminal?.prompt||'user@host:$'; prompt.style.color = '#8ff0a4';
    const input = document.createElement('input'); input.className='input'; input.placeholder='Tape ta commande…'; input.style.flex='1';
    const btn = document.createElement('button'); btn.className='button'; btn.textContent='Exécuter';
    const out = document.createElement('div'); out.className='stdout';
    const err = document.createElement('div'); err.className='stderr';
    term.appendChild(out); term.appendChild(err); body.appendChild(term); row.appendChild(prompt); row.appendChild(input); row.appendChild(btn); body.appendChild(row);

    const run = ()=>{
      const res = validateTerminal(r, input.value);
      if(res.ok){ if(res.stdout) out.innerHTML += res.stdout; input.value=''; }
      else { if(res.message) err.innerHTML += (res.message+"\n"); }
    };
    btn.onclick = run; input.addEventListener('keydown', (e)=>{ if(e.key==='Enter') run(); });
  }
  else if(r.type==='console_form'){
    const wrap=document.createElement('div');
    const path=r.form?.model_path; const local=deepClone(getByPath(state.world, templateString(path,state.vars))||{});
    (r.form?.schema?.fields||[]).forEach(f=>{
      const row=document.createElement('div'); row.className='row'; row.style.justifyContent='space-between'; row.style.margin='8px 0';
      const label=document.createElement('div'); label.textContent=f.label||f.key; label.style.color='#aab8ff';
      if(f.widget==='toggle'){
        const options = Array.isArray(f.options)? f.options : (typeof f.options==='string'? f.options.split(','): []);
        const placeholder = f.placeholder || 'Choisir';
        const btn=document.createElement('button'); btn.className='button secondary';
        if(local[f.key]!==undefined){ btn.textContent=local[f.key]; }
        else { btn.textContent = options.length? placeholder : placeholder; }
        btn.onclick=()=>{
          if(!options.length){ return; }
          const current = local[f.key];
          const idx = options.findIndex(opt=> String(opt)===String(current));
          const next = idx===-1 ? options[0] : options[(idx+1)%options.length];
          local[f.key] = next;
          btn.textContent = next;
        };
        row.appendChild(label);
        row.appendChild(btn);
      } else {
        const inp=document.createElement('input'); inp.className='input'; inp.value=local[f.key]||''; if(f.placeholder) inp.placeholder=f.placeholder; inp.oninput=()=> local[f.key]=inp.value; row.appendChild(label); row.appendChild(inp);
      }
      wrap.appendChild(row);
    });
    const btnSave=document.createElement('button'); btnSave.className='button'; btnSave.textContent='Enregistrer'; btnSave.onclick=()=>{
      const next=deepClone(state.world);
      setByPath(next, templateString(path,state.vars), local);
      feedback.innerHTML='';
      const res = runValidators(r.validators||[], local, next);
      if(!res.ok){ feedback.innerHTML = '<div class="ko">'+res.errors.join('<br>')+'</div>'; return; }
      state.world=next; setWorld();
      success(r);
    };
    body.appendChild(wrap); body.appendChild(btnSave);
  }
  else if(r.type==='inspect_file'){
    const asset=(state.lab.lab.assets||[]).find(a=> a.id===r.file_ref);
    const assetBox=document.createElement('div'); assetBox.className='inspect-asset';
    let assetContent='';
    if(asset && asset.inline && asset.content_b64){
      try{ assetContent = atob(asset.content_b64); }
      catch{ assetContent=''; }
    }
    if(asset){
      const link=document.createElement('a');
      link.className='button secondary';
      link.textContent = `Télécharger ${asset.filename || asset.name || asset.id || 'fichier'}`;
      link.download = asset.filename || asset.name || asset.id || 'asset';
      if(asset.inline && asset.content_b64){
        link.href = `data:${asset.mime || 'application/octet-stream'};base64,${asset.content_b64}`;
      } else {
        link.href = asset.url || asset.href || asset.path || '#';
        link.target = '_blank';
      }
      assetBox.appendChild(link);
      if(assetContent && assetContent.length < 16000){
        const details=document.createElement('details');
        const summary=document.createElement('summary');
        summary.textContent='Afficher un aperçu intégré';
        const pre=document.createElement('pre'); pre.textContent=assetContent;
        details.appendChild(summary); details.appendChild(pre);
        assetBox.appendChild(details);
      }
    }
    if(asset && assetBox.childNodes.length){
      body.appendChild(assetBox);
    }
    const mode=(r.input?.mode||'editor').toLowerCase();
    let inputEl=null;
    if(mode==='answer'){
      if(r.input?.prompt){
        const prompt=document.createElement('div');
        prompt.className='inspect-prompt';
        prompt.innerHTML = md(r.input.prompt);
        body.appendChild(prompt);
      }
      const area=document.createElement('textarea');
      area.className='json';
      area.placeholder = r.input?.placeholder || 'Saisis ta réponse ici…';
      inputEl = area;
      body.appendChild(area);
    } else {
      const area=document.createElement('textarea');
      area.className='json';
      area.value = assetContent || r.input?.prefill || '';
      if(r.input?.placeholder) area.placeholder = r.input.placeholder;
      inputEl = area;
      body.appendChild(area);
    }
    const btn=document.createElement('button'); btn.className='button'; btn.textContent='Valider'; btn.style.marginTop='8px';
    btn.onclick=()=>{
      let payload = inputEl.value;
      if((mode==='editor' && (r.input?.language||'json')==='json') || (mode==='answer' && (r.input?.language||'text')==='json')){
        try{ payload = JSON.parse(inputEl.value); }
        catch{ feedback.innerHTML = '<div class="ko">JSON invalide</div>'; return; }
      }
      const ok = validateInspect(r, payload);
      if(ok){ success(r); }
    };
    body.appendChild(btn);
  }
  else if(r.type==='architecture'){
    renderArchitecture(r, body);
  }
  else if(r.type==='quiz'){
    const wrap=document.createElement('div');
    let selected=null; (r.choices||[]).forEach(c=>{
      const ch=document.createElement('div'); ch.className='choice'; ch.textContent=c.text; ch.onclick=()=>{ selected=c.id; Array.from(wrap.children).forEach(x=>x.classList.remove('selected')); ch.classList.add('selected'); };
      wrap.appendChild(ch);
    });
    const btn=document.createElement('button'); btn.className='button'; btn.textContent='Valider'; btn.style.marginTop='8px'; btn.onclick=()=>{ if((r.correct||[]).includes(selected)) success(r); else feedback.innerHTML='<div class="ko">Mauvaise réponse. Réessaie.</div>'; };
    body.appendChild(wrap); body.appendChild(btn);
  }

  document.getElementById('btn-restart').onclick = ()=>{ state.world={}; state.score=0; state.currentId=state.lab.lab.steps[0].id; setScore(); setWorld(); renderStep(); };
}

function success(r){ state.score += (r.points||0); setScore(); const step = state.lab.lab.steps.find(s=> s.id===state.currentId); const next = (step.transitions&&step.transitions.on_success)||'#end'; const fb=document.getElementById('step-feedback'); fb.innerHTML = `<div class="ok">Bravo ! +${r.points||0} pts</div>`; if(next==="#end"){ fb.innerHTML += '<div class="ok" style="margin-top:6px">Lab terminé ✔</div>'; } else { setTimeout(()=>{ state.currentId = next; renderStep(); }, 600); }}

function validateTerminal(r, line){
  const cmd = parseCommand(line||'');
  const rule = (r.terminal?.validators||[])[0];
  if(!rule || rule.kind!=="command") return { ok:false, message:"Aucun validateur." };
  if(cmd.program !== rule.match.program) return { ok:false, message:`Programme attendu: ${rule.match.program}` };
  const sub = rule.match.subcommand||[]; for(let i=0;i<sub.length;i++){ if(cmd.subcmd[i]!==sub[i]) return { ok:false, message:`Sous-commande attendue: ${sub.join(' ')}` }; }
  const aliases = (rule.match.flags&&rule.match.flags.aliases)||{}; const flags={}; for(const [k,v] of Object.entries(cmd.flags)){ flags[aliases[k]||k]=v; }
  for(const req of (rule.match.flags?.required||[])){ if(!(req in flags)) return { ok:false, message:`Flag requis manquant: ${req}` }; }
  for(const a of (rule.match.args||[])){ const got = flags[a.flag]; const expect = templateString(a.expect, state.vars); if(String(got)!==String(expect)) return { ok:false, message:`Valeur attendue pour ${a.flag}: ${expect}` }; }
  applyWorldPatch(state.world, rule.response?.world_patch||[], state.vars); setWorld();
  const out = templateString(rule.response?.stdout_template||'', state.vars);
  success(r);
  return { ok:true, stdout: out };
}

function validateInspect(r, payload){
  const fb=document.getElementById('step-feedback');
  for(const v of (r.validators||[])){
    if(v.kind==='jsonschema'){
      if(typeof payload !== 'object' || payload===null){ fb.innerHTML='<div class="ko">Le contenu doit être un objet JSON</div>'; return false; }
    } else if (v.kind==='jsonpath_absent'){
      const list = jsonPathGetAll(payload, v.jsonpath||'$');
      if(v.equals!==undefined){ if(list.some(x=> JSON.stringify(x)===JSON.stringify(v.equals))){ fb.innerHTML=`<div class=\"ko\">La valeur ${JSON.stringify(v.equals)} ne doit pas apparaître à ${v.jsonpath}</div>`; return false; } }
      else if (list.length>0){ fb.innerHTML=`<div class=\"ko\">Le chemin ${v.jsonpath} ne doit pas exister.</div>`; return false; }
    } else if (v.kind==='expression'){
      try{ if(!evalExpr(v.expr, { world: state.world, vars: state.vars, payload })) { fb.innerHTML='<div class="ko">Expression non vérifiée</div>'; return false; } }catch{ fb.innerHTML='<div class=\"ko\">Expression invalide</div>'; return false; }
    }
  }
  return true;
}

function boot(){
  state.world = {};
  state.vars = drawVariables(state.lab.lab.variables, 'seed-'+Date.now());
  state.score = 0;
  state.currentId = state.lab.lab.steps[0].id;
  state.remain = state.lab.lab.timer?.seconds || 0;
  setScore(); setWorld(); renderStep(); setTimer();
}

// Timer
setInterval(()=>{ if(state.remain>0){ state.remain--; setTimer(); } }, 1000);
// ===== Architecture Step (Freeform PacketTracer-like with Konva) =====

function createCommandTerminal(options={}){
  const prompt = options.prompt || '$';
  const placeholder = options.placeholder || 'Tape une commande et presse Entrée';
  const raf = (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function')
    ? window.requestAnimationFrame.bind(window)
    : (fn)=> setTimeout(fn, 0);
  const root = document.createElement('div');
  root.className = 'arch-terminal is-disabled';
  root.dataset.disabled = 'true';
  const log = document.createElement('div');
  log.className = 'arch-terminal-log';
  root.appendChild(log);
  const form = document.createElement('form');
  form.className = 'arch-terminal-input';
  const promptSpan = document.createElement('span');
  promptSpan.className = 'prompt';
  promptSpan.textContent = prompt;
  const input = document.createElement('input');
  input.type = 'text';
  input.placeholder = placeholder;
  input.disabled = true;
  form.appendChild(promptSpan);
  form.appendChild(input);
  root.appendChild(form);

  let history = [];
  let historyIndex = 0;
  let enabled = false;
  let changeCb = ()=>{};

  const sync = ()=>{
    log.innerHTML = '';
    history.forEach(cmd=>{
      const line = document.createElement('div');
      line.className = 'arch-terminal-line';
      const p = document.createElement('span');
      p.className = 'prompt';
      p.textContent = prompt;
      const span = document.createElement('span');
      span.className = 'cmd';
      span.textContent = cmd;
      line.appendChild(p);
      line.appendChild(span);
      log.appendChild(line);
    });
    log.scrollTop = log.scrollHeight;
  };

  const getValue = ()=> history.join('\n');
  const setValue = (text)=>{
    const lines = (text || '').split(/\r?\n/).map(line=> line).filter(line=> line.trim().length>0);
    history = lines;
    historyIndex = history.length;
    input.value = '';
    sync();
  };
  const emitChange = ()=> changeCb(getValue());

  const setEnabled = (flag)=>{
    enabled = !!flag;
    input.disabled = !enabled;
    root.dataset.disabled = enabled ? 'false' : 'true';
    root.classList.toggle('is-disabled', !enabled);
    if(enabled){
      raf(()=> input.focus());
    }
  };

  const focus = ()=>{
    if(!enabled) return;
    input.focus();
    const end = input.value.length;
    input.setSelectionRange(end, end);
  };

  const clear = ()=>{
    if(history.length===0) return;
    history = [];
    historyIndex = 0;
    sync();
    emitChange();
  };

  form.addEventListener('submit', (e)=>{
    e.preventDefault();
    if(!enabled) return;
    const value = input.value;
    if(!value || !value.trim()){ input.value=''; return; }
    history.push(value);
    historyIndex = history.length;
    input.value = '';
    sync();
    emitChange();
  });

  input.addEventListener('keydown', (e)=>{
    if(!enabled) return;
    if(e.key==='ArrowUp'){
      if(history.length===0) return;
      e.preventDefault();
      historyIndex = Math.max(0, historyIndex-1);
      input.value = history[historyIndex] || '';
      raf(()=>{ const end=input.value.length; input.setSelectionRange(end,end); });
    } else if(e.key==='ArrowDown'){
      if(history.length===0) return;
      e.preventDefault();
      historyIndex = Math.min(history.length, historyIndex+1);
      input.value = history[historyIndex] || '';
      raf(()=>{ const end=input.value.length; input.setSelectionRange(end,end); });
    } else if(e.key==='Backspace' && input.value==='' && history.length>0){
      e.preventDefault();
      history.pop();
      historyIndex = history.length;
      sync();
      emitChange();
    } else if((e.ctrlKey||e.metaKey) && (e.key==='l' || e.key==='L')){
      e.preventDefault();
      clear();
    }
  });

  log.addEventListener('click', (e)=>{
    if(!enabled) return;
    const line = e.target.closest('.arch-terminal-line');
    if(!line) return;
    const idx = Array.from(log.children).indexOf(line);
    if(idx<0 || idx>=history.length) return;
    input.value = history[idx] || '';
    historyIndex = idx;
    focus();
  });

  const onChange = (cb)=>{ changeCb = typeof cb === 'function' ? cb : ()=>{}; };

  return { root, setValue, getValue, setEnabled, focus, onChange, clear };
}
function renderArchitecture(step, mount){
  const cfgInput = step.architecture;
  const configs = Array.isArray(cfgInput) ? cfgInput : [cfgInput || {}];
  let active = 0;

  const wrap = document.createElement('div');
  wrap.className = 'arch-wrap';

  if(configs.length > 1){
    const tabs = document.createElement('div');
    tabs.className = 'arch-tabs';
    configs.forEach((cfg, idx)=>{
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'arch-tab'+(idx===0?' is-active':'');
      btn.textContent = cfg.title || (`Architecture ${idx+1}`);
      btn.addEventListener('click', ()=>{
        active = idx;
        tabs.querySelectorAll('button').forEach(b=> b.classList.remove('is-active'));
        btn.classList.add('is-active');
        renderPane();
      });
      tabs.appendChild(btn);
    });
    wrap.appendChild(tabs);
  }

  const pane = document.createElement('div');
  pane.className = 'arch-pane';
  wrap.appendChild(pane);
  mount.appendChild(wrap);

  function renderPane(){
    pane.innerHTML = '';
    const cfg = configs[active] || {};
    const useFreeform = (cfg.slots && cfg.slots.length) ? cfg.freeform === true : cfg.freeform !== false;
    if(useFreeform){ renderArchitectureFreeform(step, cfg, pane); }
    else { renderArchitectureSlots(step, cfg, pane); }
  }

  renderPane();
}

function renderArchitectureFreeform(step, cfg, mount){
  const layout = document.createElement('div');
  layout.className = 'arch-layout';

  const paletteItems = shuffleArray(ensurePaletteHasDecoy(cfg.palette || []));
  const paletteIndex = new Map(paletteItems.map(item => [item.id, item]));

  const paletteCol = document.createElement('div');
  paletteCol.className = 'palette';
  const paletteHeader = document.createElement('div');
  paletteHeader.className = 'palette-header';
  const paletteTitle = document.createElement('h4');
  paletteTitle.textContent = cfg.palette_title || 'Palette';
  paletteHeader.appendChild(paletteTitle);
  if(cfg.palette_caption){
    const caption = document.createElement('p');
    caption.textContent = cfg.palette_caption;
    paletteHeader.appendChild(caption);
  }
  paletteCol.appendChild(paletteHeader);
  const paletteActions = document.createElement('div');
  paletteActions.className = 'palette-actions';
  const connectBtn = document.createElement('button');
  connectBtn.type = 'button';
  connectBtn.className = 'button secondary';
  connectBtn.textContent = 'Créer un lien';
  connectBtn.disabled = true;
  paletteActions.appendChild(connectBtn);
  paletteCol.appendChild(paletteActions);
  const paletteList = document.createElement('div');
  paletteList.className = 'palette-list';
  paletteCol.appendChild(paletteList);

  paletteItems.forEach(item => {
    const chip = buildChipElement('button', item);
    chip.addEventListener('click', ()=> addNode(item.id));
    paletteList.appendChild(chip);
  });

  const canvasWrap = document.createElement('div');
  canvasWrap.className = 'arch-canvas';
  const canvasBox = document.createElement('div');
  canvasBox.className = 'arch-stage';
  canvasBox.style.height = `${cfg.height || 520}px`;
  canvasWrap.appendChild(canvasBox);

  if(cfg.instructions){
    const note = document.createElement('div');
    note.className = 'arch-note';
    note.innerHTML = md(cfg.instructions);
    canvasWrap.appendChild(note);
  }
  const helper = document.createElement('div');
  helper.className = 'arch-help';
  helper.innerHTML = cfg.help || 'Astuce : double-clique pour configurer, clic droit pour supprimer, relie les ports latéraux.';
  canvasWrap.appendChild(helper);

  const inspector = document.createElement('div');
  inspector.className = 'arch-inspector';
  inspector.setAttribute('data-state', 'hidden');
  inspector.hidden = true;
  inspector.setAttribute('aria-hidden', 'true');
  const inspectorTitle = document.createElement('h4');
  inspectorTitle.textContent = 'Sélectionne un composant';
  const inspectorSubtitle = document.createElement('p');
  inspectorSubtitle.textContent = 'Double-clique sur un élément de la topologie pour saisir ses commandes standard.';
  const labelField = document.createElement('label');
  const labelSpan = document.createElement('span');
  labelSpan.textContent = 'Nom affiché';
  const labelInput = document.createElement('input');
  labelInput.className = 'input';
  labelInput.disabled = true;
  labelField.appendChild(labelSpan);
  labelField.appendChild(labelInput);
  const configField = document.createElement('label');
  const configSpan = document.createElement('span');
  configSpan.textContent = 'Commande(s) appliquées';
  const configTerminal = createCommandTerminal({
    prompt: cfg.command_prompt || cfg.prompt || '$',
    placeholder: cfg.command_placeholder || 'Ex: interface Gi0/1'
  });
  configField.appendChild(configSpan);
  configField.appendChild(configTerminal.root);
  const inspectorActions = document.createElement('div');
  inspectorActions.className = 'arch-inspector-actions';
  const clearCmdBtn = document.createElement('button');
  clearCmdBtn.type = 'button';
  clearCmdBtn.className = 'button secondary';
  clearCmdBtn.textContent = cfg.clear_commands_label || 'Effacer les commandes';
  clearCmdBtn.disabled = true;
  inspectorActions.appendChild(clearCmdBtn);
  inspector.appendChild(inspectorTitle);
  inspector.appendChild(inspectorSubtitle);
  inspector.appendChild(labelField);
  inspector.appendChild(configField);
  inspector.appendChild(inspectorActions);
  canvasWrap.appendChild(inspector);

  layout.appendChild(paletteCol);
  layout.appendChild(canvasWrap);
  mount.appendChild(layout);

  const stage = new Konva.Stage({ container: canvasBox, width: canvasBox.clientWidth, height: canvasBox.clientHeight, draggable:false });
  const layerGrid = new Konva.Layer({ listening:false });
  const layerLinks = new Konva.Layer();
  const layerNodes = new Konva.Layer();
  stage.add(layerGrid); stage.add(layerLinks); stage.add(layerNodes);
  const previewLine = new Konva.Line({ points:[0,0,0,0], stroke:'rgba(71,245,192,0.75)', strokeWidth:2, dash:[10,6], listening:false, visible:false });
  layerLinks.add(previewLine);

  const gridState = { visible: cfg.show_grid !== false };
  const snapState = { enabled: cfg.snap_to_grid !== false };
  const snapSize = typeof cfg.snap_to_grid === 'number' ? Math.max(1, cfg.snap_to_grid) : 20;
  const gridSize = cfg.grid_spacing || 64;

  const drawGrid = ()=>{
    layerGrid.destroyChildren();
    if(!gridState.visible){ layerGrid.draw(); return; }
    const w = stage.width(), h = stage.height();
    for(let x=0;x<=w;x+=gridSize){
      layerGrid.add(new Konva.Line({ points:[x,0,x,h], stroke:'#14254e', strokeWidth:1, opacity:0.25 }));
    }
    for(let y=0;y<=h;y+=gridSize){
      layerGrid.add(new Konva.Line({ points:[0,y,w,y], stroke:'#14254e', strokeWidth:1, opacity:0.25 }));
    }
    layerGrid.draw();
  };
  drawGrid();

  const resizeObserver = new ResizeObserver(()=>{
    stage.width(canvasBox.clientWidth);
    stage.height(canvasBox.clientHeight);
    drawGrid();
    drawLinks();
  });
  resizeObserver.observe(canvasBox);

  stage.on('wheel', (e)=>{
    e.evt.preventDefault();
    const scaleBy = 1.05;
    const oldScale = stage.scaleX();
    const pointer = stage.getPointerPosition();
    const mousePointTo = { x:(pointer.x - stage.x())/oldScale, y:(pointer.y - stage.y())/oldScale };
    const direction = e.evt.deltaY>0 ? -1 : 1;
    const newScale = direction>0 ? oldScale*scaleBy : oldScale/scaleBy;
    stage.scale({x:newScale,y:newScale});
    const newPos = { x: pointer.x - mousePointTo.x * newScale, y: pointer.y - mousePointTo.y * newScale };
    stage.position(newPos);
    stage.batchDraw();
    drawLinks();
  });

  const nodes = [];
  const links = [];
  let nodeUid = 0;
  let linkUid = 0;
  let aliasMap = {};
  let pendingFrom = null;
  let selectedNode = null;
  let inspectorNodeId = null;
  let inspectorVisible = false;

  function updateLinkButton(){
    const hasSelection = !!selectedNode;
    connectBtn.disabled = !hasSelection;
    if(!hasSelection){
      connectBtn.textContent = 'Créer un lien';
      connectBtn.classList.remove('is-armed');
      return;
    }
    if(pendingFrom && pendingFrom === selectedNode){
      connectBtn.textContent = 'Sélectionne la cible…';
      connectBtn.classList.add('is-armed');
    } else {
      connectBtn.textContent = 'Créer un lien';
      connectBtn.classList.remove('is-armed');
    }
  }

  function updateInspector(){
    if(!inspectorVisible){
      inspectorNodeId = null;
      inspector.classList.remove('is-visible');
      inspector.setAttribute('data-state', 'hidden');
      inspector.hidden = true;
      inspector.setAttribute('aria-hidden', 'true');
      inspectorTitle.textContent = 'Sélectionne un composant';
      inspectorSubtitle.textContent = 'Double-clique sur un élément de la topologie pour saisir ses commandes standard.';
      labelInput.disabled = true;
      configTerminal.setEnabled(false);
      clearCmdBtn.disabled = true;
      return;
    }
    const node = getNodeById(inspectorNodeId);
    if(!node){
      closeInspector();
      return;
    }
    inspector.hidden = false;
    inspector.setAttribute('aria-hidden', 'false');
    inspector.classList.add('is-visible');
    inspector.setAttribute('data-state', 'active');
    const currentLabel = node.labelNode.text();
    inspectorTitle.textContent = currentLabel || 'Composant';
    inspectorSubtitle.textContent = 'Saisis ou colle la configuration attendue pour ce composant.';
    if(document.activeElement !== labelInput){
      labelInput.value = currentLabel;
    }
    labelInput.disabled = false;
    configTerminal.setEnabled(true);
    clearCmdBtn.disabled = false;
  }

  function closeInspector(){
    inspectorVisible = false;
    inspectorNodeId = null;
    updateInspector();
  }

  function openInspectorFor(nodeId){
    const node = getNodeById(nodeId);
    if(!node) return;
    inspectorVisible = true;
    inspectorNodeId = nodeId;
    configTerminal.setValue(node.configText || '');
    updateInspector();
    configTerminal.focus();
  }

  labelInput.addEventListener('input', ()=>{
    if(!inspectorNodeId) return;
    const node = getNodeById(inspectorNodeId);
    if(!node) return;
    const nextLabel = labelInput.value || '';
    node.labelNode.text(nextLabel);
    inspectorTitle.textContent = nextLabel || 'Composant';
    layerNodes.batchDraw();
    drawLinks();
  });

  configTerminal.onChange(value=>{
    if(!inspectorNodeId) return;
    const node = getNodeById(inspectorNodeId);
    if(!node) return;
    node.configText = value;
  });

  clearCmdBtn.addEventListener('click', ()=>{
    if(!inspectorVisible) return;
    configTerminal.clear();
    configTerminal.focus();
  });

  updateInspector();
  updateLinkButton();

  connectBtn.addEventListener('click', ()=>{
    if(!selectedNode) return;
    if(pendingFrom === selectedNode){ cancelPendingLink(); }
    else { startLinking(selectedNode); }
  });

  const paletteLookup = (id)=>{
    const key = id!=null ? String(id) : id;
    const raw = paletteIndex.get(id) || paletteIndex.get(key) || (cfg.palette || []).find(p=> p.id===id || p.id===key);
    if(raw){
      const tagsRaw = raw.tags;
      return {
        id: raw.id || key,
        paletteId: raw.id || key,
        label: raw.label || raw.id || key,
        type: raw.type || raw.component || raw.id || key,
        iconRaw: raw.icon ?? null,
        width: raw.width,
        height: raw.height,
        tags: Array.isArray(tagsRaw)? tagsRaw : (tagsRaw ? [tagsRaw] : [])
      };
    }
    return { id: key || id, paletteId:key || id, label:key || id, type:key || id, iconRaw:null, width:176, height:68, tags:[] };
  };

  function isLinking(){ return pendingFrom!==null; }

  function drawLinkPreview(){
    if(!pendingFrom){
      previewLine.visible(false);
      return;
    }
    const from = centerPoint(pendingFrom, 'out');
    const pointer = stage.getRelativePointerPosition();
    if(!pointer){
      previewLine.visible(false);
      return;
    }
    previewLine.points([from.x, from.y, pointer.x, pointer.y]);
    previewLine.visible(true);
  }

  function cancelPendingLink(){
    pendingFrom=null;
    stage.container().classList.remove('is-linking');
    previewLine.visible(false);
    layerLinks.batchDraw();
    updateLinkButton();
    if(inspectorVisible){ updateInspector(); }
  }

  function startLinking(nodeId){
    pendingFrom = nodeId;
    stage.container().classList.add('is-linking');
    drawLinkPreview();
    layerLinks.batchDraw();
    updateLinkButton();
  }

  function finishLink(targetId){
    if(pendingFrom && pendingFrom!==targetId){ addLink(pendingFrom, targetId); }
    cancelPendingLink();
  }

  function setNodeIcon(nodeData, iconValue){
    if(nodeData.iconNode){ nodeData.iconNode.destroy(); nodeData.iconNode=null; }
    nodeData.iconRaw = iconValue ?? null;
    nodeData.icon = normalizeIconSpec(iconValue);
    if(nodeData.icon){
      let iconNode=null;
      if(nodeData.icon.kind==='image'){
        iconNode = new Konva.Image({ x:16, y: nodeData.height/2 - 16, width:32, height:32, listening:false, opacity:0.92 });
        const imgObj = new window.Image();
        imgObj.onload = ()=>{ iconNode.image(imgObj); layerNodes.batchDraw(); };
        imgObj.src = nodeData.icon.src;
      } else {
        iconNode = new Konva.Text({ text: nodeData.icon.text, fontSize:26, fill:'#7cf7ff', y: nodeData.height/2 - 20, width:40, align:'center', listening:false });
        iconNode.x(16);
      }
      nodeData.iconNode = iconNode;
      nodeData.group.add(iconNode);
      nodeData.rect.moveToBottom();
      nodeData.labelNode.x(62);
      nodeData.labelNode.width(nodeData.width - 76);
      nodeData.labelNode.align('left');
      nodeData.labelNode.moveToTop();
    } else {
      nodeData.labelNode.x(24);
      nodeData.labelNode.width(nodeData.width - 48);
      nodeData.labelNode.align('left');
    }
    layerNodes.batchDraw();
  }

  function addNode(componentId, overrides={}){
    const spec = paletteLookup(componentId);
    const width = overrides.width || spec.width || 176;
    const height = overrides.height || spec.height || 68;
    const id = overrides.id || 'n'+(nodeUid++);
    const startX = overrides.position?.x ?? Math.max(32, (stage.width()/2 - width/2) + ((nodes.length%3)-1)*48);
    const startY = overrides.position?.y ?? Math.max(24, (stage.height()/2 - height/2) + (nodes.length*28)%240);
    const group = new Konva.Group({ x:startX, y:startY, draggable:true });
    const rect = new Konva.Rect({ width, height, cornerRadius:14, stroke:'#1d2a5b', strokeWidth:1.4, fill:'#0f1630', shadowColor:'#47f5c0', shadowBlur:18, shadowOpacity:0, shadowOffset:{x:0,y:0} });
    const label = new Konva.Text({ text: overrides.label || spec.label || componentId, fontSize:16, fill:'#cde1ff', y: height/2 - 9, width: width - 48, align:'left' });
    const portIn = new Konva.Circle({ x:0, y:height/2, radius:6, fill:'#16264d', stroke:'#47f5c0', strokeWidth:1.4 });
    const portOut = new Konva.Circle({ x:width, y:height/2, radius:6, fill:'#16264d', stroke:'#47f5c0', strokeWidth:1.4 });

    group.add(rect);
    group.add(label);
    group.add(portIn);
    group.add(portOut);
    layerNodes.add(group);
    layerNodes.draw();

    group.on('click', (evt)=>{ evt.cancelBubble=true; if(pendingFrom && pendingFrom!==id){ finishLink(id); } else if(pendingFrom===id){ cancelPendingLink(); } else { selectNode(id); } });
    group.on('mouseenter', ()=>{ stage.container().style.cursor='grab'; });
    group.on('dragstart', ()=>{ stage.container().style.cursor='grabbing'; });
    group.on('dragmove', ()=>{ drawLinks(); });
    group.on('dragend', ()=>{
      stage.container().style.cursor='grab';
      if(snapState.enabled && snapSize>0){
        const gx = Math.round(group.x()/snapSize)*snapSize;
        const gy = Math.round(group.y()/snapSize)*snapSize;
        group.position({x:gx,y:gy});
      }
      drawLinks();
    });
    group.on('dblclick', (evt)=>{
      evt.cancelBubble = true;
      selectNode(id);
      openInspectorFor(id);
    });
    group.on('contextmenu', (evt)=>{ evt.evt.preventDefault(); removeNode(id); });

    const handleBeginLink = (evt)=>{
      evt.cancelBubble = true;
      if(selectedNode!==id){ selectNode(id); }
      startLinking(id);
    };
    const handleFinishLink = (evt)=>{ evt.cancelBubble=true; finishLink(id); };
    portOut.on('mousedown touchstart', handleBeginLink);
    portOut.on('click tap', handleBeginLink);
    portIn.on('mouseup touchend', handleFinishLink);
    portIn.on('click tap', handleFinishLink);

    const tagsRaw = overrides.tags!==undefined ? overrides.tags : spec.tags;
    const tags = Array.isArray(tagsRaw)? tagsRaw : (tagsRaw ? [tagsRaw] : []);
    const paletteId = overrides.palette_id || spec.paletteId || componentId;
    const nodeType = overrides.type || spec.type || componentId;
    let configValue = '';
    if(overrides.config !== undefined) configValue = String(overrides.config);
    else if(overrides.config_text !== undefined) configValue = String(overrides.config_text);
    else if(Array.isArray(overrides.commands)) configValue = overrides.commands.join('\n');
    else if(spec.default_config !== undefined) configValue = String(spec.default_config);
    else if(spec.config !== undefined) configValue = String(spec.config);
    configValue = configValue.replace(/\r\n/g, '\n');
    const nodeData = { id, type: nodeType, paletteId, group, rect, labelNode: label, iconRaw: null, icon: null, iconNode: null, width, height, tags, alias: null, configText: configValue };
    nodes.push(nodeData);
    setNodeIcon(nodeData, overrides.icon!==undefined ? overrides.icon : spec.iconRaw);
    portIn.moveToTop();
    portOut.moveToTop();
    if(overrides.alias){ nodeData.alias = overrides.alias; aliasMap[overrides.alias] = id; }
    selectNode(id);
    drawLinks();
    return id;
  }

  stage.on('mousedown touchstart', (evt)=>{
    const target = evt.target;
    if(!target || target === stage){
      if(isLinking()){ cancelPendingLink(); }
      else { selectNode(null); }
    }
  });
  stage.on('mouseleave', ()=>{ if(isLinking()) cancelPendingLink(); });
  stage.on('mousemove touchmove', ()=>{ if(isLinking()){ drawLinkPreview(); layerLinks.batchDraw(); } });
  window.addEventListener('keydown', (evt)=>{ if(evt.key==='Escape' && isLinking()) cancelPendingLink(); });

  function selectNode(id){
    selectedNode = id;
    nodes.forEach(node=>{
      const active = node.id === selectedNode;
      node.rect.stroke(active ? '#47f5c0' : '#1d2a5b');
      node.rect.shadowOpacity(active ? 0.55 : 0);
    });
    layerNodes.batchDraw();
    if(!id){
      closeInspector();
    } else if(inspectorVisible){
      if(inspectorNodeId === id){
        updateInspector();
      } else {
        closeInspector();
      }
    }
    updateLinkButton();
  }

  function getNodeById(id){ return nodes.find(n=> n.id===id); }

  function centerPoint(id, kind){
    const node = getNodeById(id);
    if(!node) return { x:0, y:0 };
    const pos = node.group.position();
    if(kind==='out'){ return { x: pos.x + node.width, y: pos.y + node.height/2 }; }
    return { x: pos.x, y: pos.y + node.height/2 };
  }

  function calcPoints(fromId, toId){ const A=centerPoint(fromId,'out'); const B=centerPoint(toId,'in'); return [A.x,A.y,B.x,B.y]; }

  function drawLinks(){
    links.forEach(link=>{ link.shape.points(calcPoints(link.fromNode, link.toNode)); });
    drawLinkPreview();
    layerLinks.batchDraw();
  }

  function addLink(fromId, toId){
    if(!fromId || !toId || fromId===toId) return;
    if(links.some(l=> l.fromNode===fromId && l.toNode===toId)) return;
    const line = new Konva.Line({ points:calcPoints(fromId,toId), stroke:'#47f5c0', strokeWidth:2.2, lineCap:'round', lineJoin:'round' });
    const linkId = 'l'+(linkUid++);
    line.on('mouseenter', ()=>{ stage.container().style.cursor='pointer'; });
    line.on('mouseleave', ()=>{ stage.container().style.cursor='default'; });
    line.on('contextmenu', (evt)=>{ evt.evt.preventDefault(); removeLink(linkId); });
    links.push({ id:linkId, fromNode:fromId, toNode:toId, shape: line });
    layerLinks.add(line);
    drawLinks();
  }

  function removeLink(id){
    const idx = links.findIndex(l=> l.id===id);
    if(idx>=0){ links[idx].shape.destroy(); links.splice(idx,1); layerLinks.draw(); }
  }

  function removeNode(id){
    const idx = nodes.findIndex(n=> n.id===id);
    if(idx===-1) return;
    const node = nodes[idx];
    if(node.alias){ delete aliasMap[node.alias]; }
    if(pendingFrom===id) cancelPendingLink();
    const wasSelected = selectedNode===id;
    const inspectorWasNode = inspectorNodeId===id;
    node.group.destroy();
    nodes.splice(idx,1);
    for(let i=links.length-1;i>=0;i--){ if(links[i].fromNode===id || links[i].toNode===id){ links[i].shape.destroy(); links.splice(i,1); } }
    layerNodes.draw();
    layerLinks.draw();
    if(wasSelected){ selectNode(null); }
    else if(inspectorWasNode){ closeInspector(); }
  }

  function resolveNodeRef(ref){
    if(!ref) return null;
    if(aliasMap[ref]) return aliasMap[ref];
    const byId = nodes.find(n=> n.id===ref);
    if(byId) return byId.id;
    const byLabel = nodes.find(n=> n.labelNode.text()===ref);
    if(byLabel) return byLabel.id;
    const byType = nodes.find(n=> n.type===ref || n.paletteId===ref);
    return byType ? byType.id : null;
  }

  function buildPayload(){
    const payload = {
      nodes: nodes.map(n=>{
        const pos = n.group.position();
        const configText = (n.configText || '').replace(/\r\n/g, '\n');
        const commands = configText.split(/\n/).map(line=> line.trim()).filter(line=> line.length>0);
        return {
          id: n.id,
          alias: n.alias || null,
          type: n.type,
          palette_id: n.paletteId,
          label: n.labelNode.text(),
          icon: n.iconRaw ?? null,
          tags: n.tags,
          position: { x: Math.round(pos.x), y: Math.round(pos.y) },
          config: configText,
          commands
        };
      }),
      links: links.map(l=>{
        const from = getNodeById(l.fromNode);
        const to = getNodeById(l.toNode);
        return {
          id: l.id,
          from: l.fromNode,
          to: l.toNode,
          from_type: from?.type || null,
          to_type: to?.type || null,
          from_label: from?.labelNode.text() || null,
          to_label: to?.labelNode.text() || null,
          from_alias: from?.alias || null,
          to_alias: to?.alias || null
        };
      })
    };
    return finalizeArchitecturePayload(payload);
  }

  function setupInitial(){
    aliasMap = {};
    (cfg.initial_nodes || []).forEach(entry => {
      if(entry == null) return;
      if(typeof entry === 'string'){ addNode(entry); return; }
      const paletteRef = entry.palette_id || entry.component || entry.type || entry.id;
      if(!paletteRef){ return; }
      const overrides = {
        id: entry.node_id || entry.nodeId || entry.instance_id,
        label: entry.label,
        position: entry.position,
        alias: entry.alias,
        icon: entry.icon,
        tags: entry.tags,
        palette_id: entry.palette_id,
        width: entry.width,
        height: entry.height
      };
      if(entry.node_type){ overrides.type = entry.node_type; }
      if(entry.config !== undefined) overrides.config = entry.config;
      if(entry.config_text !== undefined) overrides.config_text = entry.config_text;
      if(Array.isArray(entry.commands)) overrides.commands = entry.commands;
      const nodeId = addNode(paletteRef, overrides);
      const node = getNodeById(nodeId);
      if(node && entry.position){
        const pos = { x: entry.position.x ?? node.group.x(), y: entry.position.y ?? node.group.y() };
        node.group.position(pos);
      }
    });
    (cfg.initial_links || []).forEach(link => {
      if(!link) return;
      const fromId = resolveNodeRef(link.from);
      const toId = resolveNodeRef(link.to);
      if(fromId && toId) addLink(fromId, toId);
    });
    drawLinks();
    selectNode(null);
  }

  function clearScene(){
    while(nodes.length){ removeNode(nodes[0].id); }
    while(links.length){ removeLink(links[0].id); }
    stage.scale({x:1,y:1});
    stage.position({x:0,y:0});
    drawGrid();
    aliasMap = {};
    cancelPendingLink();
    selectedNode = null;
    inspectorNodeId = null;
    closeInspector();
    updateLinkButton();
  }

  const actions = document.createElement('div');
  actions.className = 'arch-actions row';

  const btnValidate = document.createElement('button');
  btnValidate.className = 'button';
  btnValidate.textContent = cfg.validate_label || 'Valider';
  btnValidate.addEventListener('click', ()=>{
    const payload = buildPayload();
    submitArchitectureResult(step, cfg, payload);
  });

  const btnReset = document.createElement('button');
  btnReset.className = 'button secondary';
  btnReset.textContent = 'Réinitialiser';
  btnReset.addEventListener('click', ()=>{ clearScene(); setupInitial(); });

  const btnSnap = document.createElement('button');
  btnSnap.className = 'button secondary';
  const updateSnap = ()=>{ btnSnap.textContent = snapState.enabled ? 'Aimantation: ON' : 'Aimantation: OFF'; btnSnap.setAttribute('data-active', snapState.enabled); };
  btnSnap.addEventListener('click', ()=>{ snapState.enabled = !snapState.enabled; updateSnap(); });
  updateSnap();

  const btnGrid = document.createElement('button');
  btnGrid.className = 'button secondary';
  const updateGridBtn = ()=>{ btnGrid.textContent = gridState.visible ? 'Grille: ON' : 'Grille: OFF'; btnGrid.setAttribute('data-active', gridState.visible); };
  btnGrid.addEventListener('click', ()=>{ gridState.visible = !gridState.visible; updateGridBtn(); drawGrid(); });
  updateGridBtn();

  const btnPan = document.createElement('button');
  btnPan.className = 'button secondary';
  const updatePan = ()=>{ btnPan.textContent = stage.draggable() ? 'Déplacement vue: ON' : 'Déplacement vue: OFF'; btnPan.setAttribute('data-active', stage.draggable()); };
  btnPan.addEventListener('click', ()=>{ stage.draggable(!stage.draggable()); updatePan(); });
  updatePan();

  actions.appendChild(btnValidate);
  actions.appendChild(btnReset);
  actions.appendChild(btnSnap);
  actions.appendChild(btnGrid);
  actions.appendChild(btnPan);
  mount.appendChild(actions);

  setupInitial();
  stage.on('mouseup touchend', ()=>{ if(pendingFrom){ cancelPendingLink(); } });
}

function renderArchitectureSlots(step, cfg, mount){
  const paletteItems = ensurePaletteHasDecoy(cfg.palette || []);
  const wrapper = document.createElement('div');
  wrapper.className = 'arch-grid';
  const pal = document.createElement('div'); pal.className='palette';
  pal.innerHTML = '<div class="palette-header"><h4>Palette</h4></div>';
  const palList = document.createElement('div'); palList.className='palette-list'; pal.appendChild(palList);
  paletteItems.forEach(item=>{
    const chip=buildChipElement('div', item);
    palList.appendChild(chip);
  });

  const right=document.createElement('div'); right.className='slots-wrap';
  const slots=document.createElement('div'); slots.className='slots';
  right.appendChild(slots);
  wrapper.appendChild(pal); wrapper.appendChild(right);
  mount.appendChild(wrapper);

  const slotEls=new Map();
  const slotMeta=new Map();
  (cfg.slots||[]).forEach(s=>{
    const box=document.createElement('div'); box.className='slot'; box.dataset.slotId=s.id;
    const title=document.createElement('div'); title.className='slot-title'; title.textContent=s.label||s.id; box.appendChild(title);
    const configLabel=document.createElement('label');
    configLabel.className='slot-config';
    configLabel.dataset.active='false';
    const span=document.createElement('span'); span.textContent='Commande(s)';
    const configTerminal=createCommandTerminal({
      prompt: cfg.command_prompt || cfg.prompt || '$',
      placeholder: cfg.command_placeholder || 'Ex: interface Gi0/1'
    });
    configTerminal.setEnabled(false);
    configLabel.appendChild(span);
    configLabel.appendChild(configTerminal.root);
    box.appendChild(configLabel);
    configTerminal.onChange(value=>{ const meta=slotMeta.get(s.id); if(meta) meta.config = value; });
    slotMeta.set(s.id, { label: configLabel, terminal: configTerminal, config: '' });
    slots.appendChild(box);
    slotEls.set(s.id, box);
  });

  const allAssignments={};
  const allConnections=[];
  const containers=[palList, ...slotEls.values()];
  const drake=dragula(containers, {
    copy:(el,source)=> source===palList,
    accepts:(el,target)=>{
      const slotId=target?.dataset?.slotId;
      if(!slotId) return target===palList;
      const slot=(cfg.slots||[]).find(s=> s.id===slotId);
      const compId=el.dataset.componentId;
      return !slot || !slot.accepts || slot.accepts.includes(compId);
    },
    revertOnSpill:true,
    removeOnSpill:true
  });

  drake.on('drop',(el,target,source)=>{
    if(!target) return;
    const toSlot=target.dataset?.slotId || null;
    const fromSlot=source?.dataset?.slotId || null;
    if(target===palList){
      if(fromSlot){
        delete allAssignments[fromSlot];
        const meta=slotMeta.get(fromSlot);
        if(meta){
          meta.config='';
          meta.terminal.setValue('');
          meta.terminal.setEnabled(false);
          meta.label.dataset.active='false';
        }
      }
      el.remove();
      return;
    }
    if(!toSlot) return;
    el.dataset.slotId = toSlot;
    [...target.querySelectorAll('.chip')].forEach(ch=>{ if(ch!==el) ch.remove(); });
    allAssignments[toSlot]=el.dataset.componentId;
    let transferred='';
    if(fromSlot && fromSlot!==toSlot){
      delete allAssignments[fromSlot];
      const fromMeta=slotMeta.get(fromSlot);
      if(fromMeta){
        transferred = fromMeta.config || '';
        fromMeta.config='';
        fromMeta.terminal.setValue('');
        fromMeta.terminal.setEnabled(false);
        fromMeta.label.dataset.active='false';
      }
    }
    const toMeta=slotMeta.get(toSlot);
    if(toMeta){
      const nextConfig = transferred && fromSlot!==toSlot ? transferred : '';
      toMeta.config = nextConfig;
      toMeta.terminal.setValue(nextConfig);
      toMeta.terminal.setEnabled(true);
      toMeta.label.dataset.active='true';
    }
  });

  drake.on('remove',(el, container, source)=>{
    const fromSlot=source?.dataset?.slotId || null;
    if(fromSlot){
      delete allAssignments[fromSlot];
      const meta=slotMeta.get(fromSlot);
      if(meta){
        meta.config='';
        meta.terminal.setValue('');
        meta.terminal.setEnabled(false);
        meta.label.dataset.active='false';
      }
    }
  });

  let pending=null;
  slots.addEventListener('click',(e)=>{
    const chip=e.target.closest('.chip');
    if(!chip || !chip.dataset.slotId) return;
    const slotId=chip.dataset.slotId;
    const compId=chip.dataset.componentId;
    const current={ slot:slotId, component:compId };
    if(!pending){
      pending=current;
      chip.setAttribute('data-selected','true');
      return;
    }
    if(pending.slot!==current.slot){ allConnections.push({ from: pending, to: current }); }
    pending=null;
    slots.querySelectorAll('.chip').forEach(c=> c.removeAttribute('data-selected'));
  });

  const actions=document.createElement('div'); actions.className='arch-actions row';
  const btn=document.createElement('button'); btn.className='button'; btn.textContent='Valider';
  btn.addEventListener('click', ()=>{
    const payload = finalizeArchitecturePayload({
      nodes: Object.entries(allAssignments).map(([slotId, compId])=>{
        const slot=(cfg.slots||[]).find(s=> s.id===slotId) || {};
        const paletteItem=paletteItems.find(p=> p.id===compId) || { id:compId };
        const meta=slotMeta.get(slotId) || {};
        const configText = (meta.config || '').replace(/\r\n/g,'\n');
        const commands = configText.split(/\n/).map(line=> line.trim()).filter(line=> line.length>0);
        return {
          id: slotId,
          type: compId,
          palette_id: paletteItem.id,
          label: slot.label || slotId,
          icon: paletteItem.icon ?? null,
          tags: Array.isArray(paletteItem.tags)? paletteItem.tags : (paletteItem.tags?[paletteItem.tags]:[]),
          position: { slot: slotId },
          config: configText,
          commands
        };
      }),
      links: allConnections.map((link, idx)=>{
        const from = link.from || {};
        const to = link.to || {};
        return {
          id: 'l'+idx,
          from: from.slot || null,
          to: to.slot || null,
          from_type: from.component || null,
          to_type: to.component || null,
          from_label: from.slot || from.component || null,
          to_label: to.slot || to.component || null
        };
      })
    });
    submitArchitectureResult(step, cfg, payload);
  });
  actions.appendChild(btn);
  mount.appendChild(actions);
}

function finalizeArchitecturePayload(payload){
  const countsByType={};
  const countsByPalette={};
  (payload.nodes||[]).forEach(node=>{
    countsByType[node.type]=(countsByType[node.type]||0)+1;
    if(node.palette_id){ countsByPalette[node.palette_id]=(countsByPalette[node.palette_id]||0)+1; }
  });

  const nodesById=new Map();
  (payload.nodes||[]).forEach(node=> nodesById.set(node.id, node));
  const typeConnections=[];
  (payload.links||[]).forEach(link=>{
    const fromNode = nodesById.get(link.from);
    const toNode = nodesById.get(link.to);
    if(fromNode){ link.from_type = link.from_type || fromNode.type; link.from_label = link.from_label || fromNode.label; }
    if(toNode){ link.to_type = link.to_type || toNode.type; link.to_label = link.to_label || toNode.label; }
    typeConnections.push({ from: link.from_type || null, to: link.to_type || null });
  });

  const configuredNodes = [];
  const commandEntries = [];
  (payload.nodes||[]).forEach(node=>{
    const configText = (node.config || node.config_text || '').trim();
    const commands = Array.isArray(node.commands) ? node.commands : configText ? configText.split(/\r?\n/).map(line=> line.trim()).filter(Boolean) : [];
    if(configText){ configuredNodes.push({ id: node.id, label: node.label, type: node.type }); }
    commands.forEach(cmd=> commandEntries.push({ node_id: node.id, label: node.label, type: node.type, command: cmd }));
  });

  payload.summary = {
    counts_by_type: countsByType,
    counts_by_palette: countsByPalette,
    labels: (payload.nodes||[]).map(n=> n.label),
    type_connections: typeConnections,
    configured_nodes: configuredNodes,
    commands: commandEntries
  };

  return payload;
}

function matchNode(node, matcher){
  if(!matcher) return true;
  if(typeof matcher==='string') return node.type===matcher || node.label===matcher || node.palette_id===matcher || node.alias===matcher;
  if(Array.isArray(matcher)) return matcher.some(m=> matchNode(node, m));
  if(matcher.id && node.id!==matcher.id) return false;
  if(matcher.alias && node.alias!==matcher.alias) return false;
  if(matcher.type && node.type!==matcher.type) return false;
  if(matcher.palette_id && node.palette_id!==matcher.palette_id) return false;
  if(matcher.label && node.label!==matcher.label) return false;
  if(matcher.icon && !iconsEqual(node.icon, matcher.icon)) return false;
  const configText = (node.config ?? node.config_text ?? (Array.isArray(node.commands)? node.commands.join('\n') : '') ?? '').toString();
  if(matcher.config !== undefined){
    if(configText.trim() !== String(matcher.config).trim()) return false;
  }
  if(matcher.config_contains){
    const required = Array.isArray(matcher.config_contains) ? matcher.config_contains : [matcher.config_contains];
    const haystack = configText.toLowerCase();
    if(!required.every(part => haystack.includes(String(part).toLowerCase()))) return false;
  }
  if(matcher.config_regex){
    try {
      const re = new RegExp(matcher.config_regex, matcher.config_regex_flags || '');
      if(!re.test(configText)) return false;
    } catch { return false; }
  }
  if(matcher.commands){
    const commandsArray = Array.isArray(node.commands) ? node.commands : configText.split(/\r?\n/).map(line=> line.trim()).filter(Boolean);
    const expectedCommands = Array.isArray(matcher.commands) ? matcher.commands : [matcher.commands];
    if(!expectedCommands.every(cmd => commandsArray.includes(cmd))) return false;
  }
  if(matcher.tags){
    const tags = Array.isArray(node.tags)? node.tags : [];
    const required = Array.isArray(matcher.tags)? matcher.tags : [matcher.tags];
    if(!required.every(tag=> tags.includes(tag))) return false;
  }
  if(matcher.not && matchNode(node, matcher.not)) return false;
  return true;
}

function linkMatches(link, req, nodes){
  const fromMatcher = req.from || req.match_from || req.match?.from || req.match;
  const toMatcher = req.to || req.match_to || req.match?.to || req.match;
  const fromNode = nodes.find(n=> n.id===link.from) || nodes.find(n=> matchNode(n, link.from_alias||link.from_label));
  const toNode = nodes.find(n=> n.id===link.to) || nodes.find(n=> matchNode(n, link.to_alias||link.to_label));
  const direct = matchNode(fromNode||{}, fromMatcher) && matchNode(toNode||{}, toMatcher);
  if(direct) return true;
  if(req.bidirectional){
    return matchNode(fromNode||{}, toMatcher) && matchNode(toNode||{}, fromMatcher);
  }
  return false;
}

function validateArchitectureSpec(specInput, payload){
  if(!specInput) return { ok:true, errors:[] };
  const spec = Array.isArray(specInput) ? { nodes: specInput } : specInput;
  const errors=[];
  const nodes = payload.nodes || [];
  const links = payload.links || [];

  (spec.nodes||[]).forEach(rule=>{
    const matcher = rule.match || rule;
    const count = rule.count ?? rule.min ?? 1;
    const max = rule.max;
    const matches = nodes.filter(node=> matchNode(node, matcher));
    if(matches.length < count){
      errors.push(rule.message || `Il faut au moins ${count} composant(s) correspondant(s) à ${matcher.label || matcher.type || matcher}`);
    }
    if(max !== undefined && matches.length > max){
      errors.push(rule.message_max || `Trop de composants du type ${matcher.label || matcher.type || matcher}`);
    }
  });

  if(spec.allow_extra_nodes === false && (spec.nodes||[]).length){
    const extras = nodes.filter(node=> !(spec.nodes||[]).some(rule=> matchNode(node, rule.match || rule)));
    if(extras.length){
      errors.push(`Composants non attendus : ${extras.map(n=> n.label || n.type).join(', ')}`);
    }
  }

  (spec.links||[]).forEach(rule=>{
    const min = rule.count ?? rule.min ?? 1;
    const max = rule.max;
    const matches = links.filter(link=> linkMatches(link, rule, nodes));
    if(matches.length < min){
      errors.push(rule.message || `Lien attendu manquant (${min}x ${rule.from?.type || rule.from || '?'} → ${rule.to?.type || rule.to || '?'})`);
    }
    if(max !== undefined && matches.length > max){
      errors.push(rule.message_max || `Trop de liens ${rule.from?.type || rule.from || '?'} → ${rule.to?.type || rule.to || '?'}`);
    }
  });

  (spec.type_connections||[]).forEach(rule=>{
    const min = rule.count ?? rule.min ?? 1;
    const max = rule.max;
    const matches = (payload.summary?.type_connections || []).filter(conn=> matchNode({ type: conn.from, label: conn.from }, rule.from) && matchNode({ type: conn.to, label: conn.to }, rule.to));
    if(matches.length < min){ errors.push(rule.message || `Connexion de type ${rule.from} → ${rule.to} attendue (${min})`); }
    if(max !== undefined && matches.length > max){ errors.push(rule.message_max || `Trop de connexions ${rule.from} → ${rule.to}`); }
  });

  (spec.expressions||[]).forEach(expr=>{
    try {
      if(!evalExpr(expr, { world: state.world, vars: state.vars, payload })){
        errors.push(`Expression invalide: ${expr}`);
      }
    } catch {
      errors.push(`Expression invalide: ${expr}`);
    }
  });

  return { ok: errors.length===0, errors };
}

function submitArchitectureResult(step, cfg, payload){
  const path = templateString(cfg.world_path || step.world_path || `architecture.${step.id}`, state.vars);
  const next = deepClone(state.world);
  setByPath(next, path, payload);
  const expectedRaw = cfg.expected_world || step.expected_world || null;
  const errors = [];
  if(expectedRaw){
    const templated = templateAny(expectedRaw, state.vars);
    const isRuleSpec = Array.isArray(templated) || (templated && typeof templated==='object' && (templated.nodes || templated.links || templated.allow_extra_nodes !== undefined || templated.type_connections || templated.expressions));
    if(isRuleSpec){
      const specRes = validateArchitectureSpec(templated, payload);
      errors.push(...specRes.errors);
    } else {
      const expectedValue = templated && typeof templated==='object' && templated.equals!==undefined ? templated.equals : templated;
      const strict = !!(templated && typeof templated==='object' && templated.strict===true);
      const actualValue = getByPath(next, path);
      let actualComparable = normalizeArchitectureForCompare(actualValue);
      let expectedComparable = normalizeArchitectureForCompare(expectedValue);
      if(!strict && expectedComparable && typeof expectedComparable==='object' && expectedComparable.summary===undefined && actualComparable && actualComparable.summary!==undefined){
        delete actualComparable.summary;
      }
      if(!deepEqual(actualComparable, expectedComparable)){
        errors.push((templated && templated.message) || 'Architecture incorrecte.');
      }
    }
  }
  const validators = [...(step.validators||[]), ...(cfg.validators||[])];
  const validatorRes = runValidators(validators, payload, next);
  if(validatorRes.errors){ errors.push(...validatorRes.errors); }
  if(errors.length){
    const fb=document.getElementById('step-feedback');
    fb.innerHTML = '<div class="ko">'+errors.join('<br>')+'</div>';
    return false;
  }
  state.world = next;
  setWorld();
  success(step);
  return true;
}

// Start
boot();
