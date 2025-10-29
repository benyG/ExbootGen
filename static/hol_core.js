// ===== Utilities =====
const deepClone = (o) => JSON.parse(JSON.stringify(o));
const seededRandom = (seedStr) => { function xmur3(str){let h=1779033703^str.length;for(let i=0;i<str.length;i++){h=Math.imul(h^str.charCodeAt(i),3432918353);h=(h<<13)|(h>>>19);}return function(){h=Math.imul(h^(h>>>16),2246822507);h=Math.imul(h^(h>>>13),3266489909);h^=h>>>16;return h>>>0;};} function mulberry32(a){return function(){let t=(a+=0x6d2b79f5);t=Math.imul(t^(t>>>15),t|1);t^=t+Math.imul(t^(t>>>7),t|61);return ((t^(t>>>14))>>>0)/4294967296;};} return mulberry32(xmur3(seedStr)()); };
const templateString = (s,v)=> s.replace(/\{\{(.*?)\}\}/g,(_,k)=> (v[k.trim()]??"")+"");
const templateAny=(val,v)=> typeof val==="string"?templateString(val,v):Array.isArray(val)?val.map(x=>templateAny(x,v)):(val&&typeof val==="object"?Object.fromEntries(Object.entries(val).map(([k,x])=>[k,templateAny(x,v)])):val);
const getByPath=(o,p)=> p.split('.').reduce((a,k)=> (a==null?undefined:a[k]), o);
const setByPath=(o,p,val)=>{const parts=p.split('.');let cur=o;for(let i=0;i<parts.length-1;i++){if(cur[parts[i]]==null)cur[parts[i]]={};cur=cur[parts[i]];}cur[parts[parts.length-1]]=val;};
const applyWorldPatch=(world,patch,vars)=> (patch||[]).forEach(p=> p.op==='set' && setByPath(world, templateString(p.path,vars), templateAny(p.value,vars)) );
const md = (s='')=> s.replace(/\*\*(.*?)\*\*/g,'<strong>$1</strong>').replace(/`([^`]+)`/g,'<code>$1</code>');

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
        if(local[f.key]===undefined) local[f.key] = (f.options?.[0]||'Off');
        const btn=document.createElement('button'); btn.className='button secondary'; btn.textContent=(local[f.key]);
        btn.onclick=()=>{ const a=f.options?.[0], b=f.options?.[1]; local[f.key] = (String(local[f.key])===String(a)? b : a); btn.textContent=local[f.key]; };
        row.appendChild(label); row.appendChild(btn);
      } else {
        const inp=document.createElement('input'); inp.className='input'; inp.value=local[f.key]||''; inp.oninput=()=> local[f.key]=inp.value; row.appendChild(label); row.appendChild(inp);
      }
      wrap.appendChild(row);
    });
    const btnSave=document.createElement('button'); btnSave.className='button'; btnSave.textContent='Enregistrer'; btnSave.onclick=()=>{ const next=deepClone(state.world); setByPath(next, templateString(path,state.vars), local); state.world=next; setWorld(); feedback.innerHTML=''; validateConsole(r); };
    body.appendChild(wrap); body.appendChild(btnSave);
  }
  else if(r.type==='inspect_file'){
    const asset=(state.lab.lab.assets||[]).find(a=> a.id===r.file_ref);
    const textArea=document.createElement('textarea'); textArea.className='json';
    textArea.value = asset && asset.inline && asset.content_b64 ? atob(asset.content_b64) : '';
    const btn=document.createElement('button'); btn.className='button'; btn.textContent='Valider'; btn.style.marginTop='8px';
    btn.onclick=()=>{ let payload = textArea.value; try{ if((r.input?.language||'json')==='json'){ payload = JSON.parse(textArea.value); } }catch{ feedback.innerHTML = '<div class="ko">JSON invalide</div>'; return; }
      const ok = validateInspect(r, payload); if(ok){ success(r); } };
    body.appendChild(textArea); body.appendChild(btn);
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

function validateConsole(r){
  const fb = document.getElementById('step-feedback');
  const okAll = (r.validators||[]).every(v=>{
    if(v.kind==='world'){
      const path = templateString(v.path || v.expect?.path, state.vars);
      const got  = getByPath(state.world, path);
      const want = (v.equals!==undefined? v.equals : v.expect?.equals);
      return want===undefined ? (got!==undefined) : (String(got)===String(want));
    }
    if(v.kind==='expression'){
      try { return !!evalExpr(v.expr, { world: state.world, vars: state.vars }); }
      catch { return false; }
    }
    return true;
  });
  if(!okAll){
    fb.innerHTML = '<div class="ko">Condition non satisfaite. Enregistre et réessaie.</div>';
    return false;
  }
  success(r);
  return true;
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
function renderArchitecture(r, body){
  const cfgInput = r.architecture;
  const configs = Array.isArray(cfgInput) ? cfgInput : [cfgInput||{}];
  let active = 0;

  const wrap = document.createElement('div');
  wrap.className = 'arch-wrap';
  const pane = document.createElement('div');

  // tabs if many
  if(configs.length>1){
    const tabs = document.createElement('div'); tabs.className='arch-tabs';
    configs.forEach((cfg,idx)=>{ const b=document.createElement('button'); b.className='button secondary'+(idx===0?' active':''); b.textContent=cfg.title||('Architecture '+(idx+1)); b.onclick=()=>{ active=idx; renderPane(); Array.from(tabs.children).forEach(x=>x.classList.remove('active')); b.classList.add('active'); }; tabs.appendChild(b); });
    wrap.appendChild(tabs);
  }

  wrap.appendChild(pane);
  body.appendChild(wrap);

  function renderPane(){
    pane.innerHTML='';
    const cfg = configs[active] || {};
    const freeform = !!cfg.freeform || !(cfg.slots && cfg.slots.length);
    if(freeform){ renderArchitectureFreeform(cfg, pane, r); }
    else { renderArchitectureSlots(cfg, pane, r); }
  }

  renderPane();
}

// ——— Freeform renderer (Konva): nodes anywhere, connect by drag/click, pan/zoom
function renderArchitectureFreeform(cfg, mount, step){
  // Layout
  const grid = document.createElement('div'); grid.style.display='grid'; grid.style.gridTemplateColumns='220px 1fr'; grid.style.gap='16px';
  const pal = document.createElement('div'); pal.className='palette'; pal.innerHTML='<h4>Palette</h4>';
  const palList = document.createElement('div'); pal.appendChild(palList);
  const canvasBox = document.createElement('div'); canvasBox.style.position='relative'; canvasBox.style.height = (cfg.height||480)+'px'; canvasBox.style.background='#0b1020'; canvasBox.style.border='1px solid #1d2a5b'; canvasBox.style.borderRadius='12px';
  grid.appendChild(pal); grid.appendChild(canvasBox); mount.appendChild(grid);

  // Palette
  (cfg.palette||[]).forEach(c=>{ const chip=document.createElement('div'); chip.className='chip'; chip.innerHTML=`<span class="dot"></span><span>${c.label||c.id}</span>`; chip.onclick=()=> addNode(c.id); palList.appendChild(chip); });

  // Stage (Konva)
  const stage = new Konva.Stage({ container: canvasBox, width: canvasBox.clientWidth, height: canvasBox.clientHeight, draggable: false });
  const layerGrid = new Konva.Layer();
  const layerLinks = new Konva.Layer();
  const layerNodes = new Konva.Layer();
  stage.add(layerGrid); stage.add(layerLinks); stage.add(layerNodes);

  // Grid background
  const stepGrid = 24; const drawGrid=()=>{ layerGrid.destroyChildren(); const w=stage.width(), h=stage.height(); for(let x=0;x<w; x+=stepGrid){ layerGrid.add(new Konva.Line({ points:[x,0,x,h], stroke:'#13235a', strokeWidth:1, opacity:0.4 })); } for(let y=0;y<h;y+=stepGrid){ layerGrid.add(new Konva.Line({ points:[0,y,w,y], stroke:'#13235a', strokeWidth:1, opacity:0.4 })); } layerGrid.draw(); };
  drawGrid();
  window.addEventListener('resize', ()=>{ stage.width(canvasBox.clientWidth); stage.height(canvasBox.clientHeight); drawGrid(); drawLinks(); });

  // Pan/zoom (wheel zoom, hold Space to pan)
  let panning=false; window.addEventListener('keydown', e=>{ if(e.code==='Space') { panning=true; stage.draggable(true);} }); window.addEventListener('keyup', e=>{ if(e.code==='Space') { panning=false; stage.draggable(false);} });
  stage.on('wheel', (e)=>{ e.evt.preventDefault(); const scaleBy=1.05; const oldScale = stage.scaleX(); const pointer = stage.getPointerPosition(); const mousePointTo = { x:(pointer.x - stage.x())/oldScale, y:(pointer.y - stage.y())/oldScale }; const direction = e.evt.deltaY>0 ? -1 : 1; const newScale = direction>0 ? oldScale*scaleBy : oldScale/scaleBy; stage.scale({x:newScale,y:newScale}); const newPos = { x: pointer.x - mousePointTo.x * newScale, y: pointer.y - mousePointTo.y * newScale }; stage.position(newPos); stage.batchDraw(); });

  // State
  const nodes=[]; // {id, type, group, ports:{in,out}}
  const links=[]; // {fromNode, toNode, arrow}
  let uid=0; const nextId=()=> 'n'+(uid++);

  function addNode(type){
    const id=nextId();
    const group=new Konva.Group({ x: 80 + (nodes.length*20)%200, y: 60 + (nodes.length*12)%120, draggable:true });
    const rect=new Konva.Rect({ width:120, height:46, cornerRadius:10, stroke:'#1d2a5b', fill:'#0f1630' });
    const label=new Konva.Text({ text: (cfg.palette||[]).find(p=>p.id===type)?.label || type, x:10, y:12, fontSize:14, fill:'#cde1ff' });
    const portIn = new Konva.Circle({ x:0, y:23, radius:5, fill:'#263767', stroke:'#2b8a3e' });
    const portOut= new Konva.Circle({ x:120, y:23, radius:5, fill:'#263767', stroke:'#2b8a3e' });
    group.add(rect,label,portIn,portOut); layerNodes.add(group); layerNodes.draw();

    // snap to grid
    group.on('dragend', ()=>{ const gx=Math.round(group.x()/stepGrid)*stepGrid; const gy=Math.round(group.y()/stepGrid)*stepGrid; group.position({x:gx,y:gy}); layerNodes.draw(); drawLinks(); });

    nodes.push({ id, type, group, ports:{in:portIn, out:portOut} });

    let pendingFrom=null;
    portOut.on('mousedown', ()=>{ pendingFrom=id; });
    portIn.on('mouseup', ()=>{ if(pendingFrom && pendingFrom!==id){ addLink(pendingFrom, id); } pendingFrom=null; });

    return id;
  }

  function addLink(fromId, toId){
    const arrow=new Konva.Arrow({ points:calcPoints(fromId,toId), stroke:'#2b8a3e', fill:'#2b8a3e', strokeWidth:2, pointerLength:10, pointerWidth:8 });
    links.push({ fromNode:fromId, toNode:toId, arrow }); layerLinks.add(arrow); layerLinks.draw();
  }

  function centerOfOut(node){ const g=getNode(node).group; const p=g.getAbsolutePosition(); return { x:p.x+120, y:p.y+23 }; }
  function centerOfIn(node){ const g=getNode(node).group; const p=g.getAbsolutePosition(); return { x:p.x, y:p.y+23 }; }
  function calcPoints(a,b){ const A=centerOfOut(a), B=centerOfIn(b); return [A.x,A.y,B.x,B.y]; }
  function getNode(id){ return nodes.find(n=>n.id===id); }
  function drawLinks(){ links.forEach(l=>{ l.arrow.points(calcPoints(l.fromNode,l.toNode)); }); layerLinks.batchDraw(); }

  // Populate defaults if provided
  (cfg.initial_nodes||[]).forEach(t=> addNode(t));

  // Actions
  const bar = document.createElement('div'); bar.className='row'; bar.style.marginTop='10px';
  const btnClear = document.createElement('button'); btnClear.className='button secondary'; btnClear.textContent='Réinitialiser'; btnClear.onclick=()=>{ nodes.splice(0); links.splice(0); layerNodes.destroyChildren(); layerLinks.destroyChildren(); layerNodes.draw(); layerLinks.draw(); };
  const btnValidate = document.createElement('button'); btnValidate.className='button'; btnValidate.textContent='Valider'; btnValidate.onclick=()=>{
    // Build input for validator (freeform rules)
    const connByType = links.map(l=> ({ from: getNode(l.fromNode).type, to: getNode(l.toNode).type }));
    const counts = nodes.reduce((acc,n)=> (acc[n.type]=(acc[n.type]||0)+1, acc),{});
    const ok = validateFreeform(cfg, counts, connByType);
    if(ok) success(step); else document.getElementById('step-feedback').innerHTML='<div class="ko">Architecture invalide.</div>';
  };
  bar.appendChild(btnClear); bar.appendChild(btnValidate); mount.appendChild(bar);
}

function validateFreeform(cfg, counts, connections){
  const exp = cfg.expected || {};
  const errors=[];
  // required node types
  (exp.require_nodes||[]).forEach(t=>{ if(!(counts[t]>0)) errors.push(`Composant requis manquant: ${t}`); });
  // min counts
  const minc = exp.min_counts||{}; Object.keys(minc).forEach(t=>{ if((counts[t]||0) < minc[t]) errors.push(`Au moins ${minc[t]} x ${t}`); });
  // connections by type (at least one)
  (exp.connections_by_type||[]).forEach(c=>{ const ok = connections.some(e=> e.from===c.from && e.to===c.to); if(!ok) errors.push(`Lien manquant ${c.from} → ${c.to}`); });
  if(errors.length){ const fb=document.getElementById('step-feedback'); fb.innerHTML='<div class="ko">'+errors.join('<br>')+'</div>'; return false; }
  return true;
}

// ——— Legacy slot-grid renderer kept as fallback (uses Dragula)
function renderArchitectureSlots(cfg, mount, r){
  const body=document.createElement('div'); body.className='arch-grid';
  const pal = document.createElement('div'); pal.className='palette'; pal.innerHTML='<h4>Palette</h4>';
  const palList = document.createElement('div'); pal.appendChild(palList);
  (cfg.palette||[]).forEach(c=>{ const chip=document.createElement('div'); chip.className='chip'; chip.innerHTML=`<span class="dot"></span><span>${c.label||c.id}</span>`; chip.dataset.componentId=c.id; palList.appendChild(chip); });

  const right = document.createElement('div'); right.style.position='relative';
  const slotsGrid = document.createElement('div'); slotsGrid.className='slots';
  const slotEls = new Map();
  (cfg.slots||[]).forEach(s=>{ const box=document.createElement('div'); box.className='slot'; box.dataset.slotId=s.id; const title=document.createElement('div'); title.className='slot-title'; title.textContent=s.label||s.id; box.appendChild(title); slotsGrid.appendChild(box); slotEls.set(s.id, box); });
  const overlay = document.createElementNS('http://www.w3.org/2000/svg','svg'); overlay.setAttribute('id','arch-svg'); right.appendChild(slotsGrid); right.appendChild(overlay);

  body.appendChild(pal); body.appendChild(right); mount.appendChild(body);

  const allAssignments = {}; const allConnections = [];
  const containers = [ palList, ...slotEls.values() ];
  const drake = dragula(containers, {
    copy: (el, source)=> source===palList,
    accepts: (el, target)=>{ const slotId=target && target.dataset ? target.dataset.slotId : null; if(!slotId) return target===palList; const slot=(cfg.slots||[]).find(s=>s.id===slotId); const compId=el.dataset.componentId; return !slot || !slot.accepts || slot.accepts.includes(compId); },
    revertOnSpill:true,
    removeOnSpill:true
  });
  drake.on('drop',(el,target,source)=>{ if(!target) return; const toSlot=target.dataset && target.dataset.slotId ? target.dataset.slotId : null; const fromSlot=source && source.dataset ? source.dataset.slotId : null; if(target===palList){ if(fromSlot) delete allAssignments[fromSlot]; el.remove(); draw(); return; } if(!toSlot) return; [...target.querySelectorAll('.chip')].forEach(ch=>{ if(ch!==el) ch.remove(); }); allAssignments[toSlot]=el.dataset.componentId; if(fromSlot && fromSlot!==toSlot) delete allAssignments[fromSlot]; draw(); });
  drake.on('remove', (el, container, source)=>{ const fromSlot=source && source.dataset ? source.dataset.slotId : null; if(fromSlot){ delete allAssignments[fromSlot]; draw(); }});

  // simple click-to-link using chips placed in slots
  let pending=null; slotsGrid.addEventListener('click',(e)=>{ const chip=e.target.closest('.chip'); if(!chip) return; const compId=chip.dataset.componentId; if(!pending){ pending=compId; chip.setAttribute('data-selected','true'); } else { if(pending!==compId){ allConnections.push({from: pending, to: compId}); } pending=null; slotsGrid.querySelectorAll('.chip').forEach(c=>c.removeAttribute('data-selected')); draw(); }});

  function draw(){ while(overlay.firstChild) overlay.removeChild(overlay.firstChild); }

  // Validate button
  const actions=document.createElement('div'); actions.className='row'; actions.style.marginTop='10px'; const btn=document.createElement('button'); btn.className='button'; btn.textContent='Valider'; btn.onclick=()=>{ const ok = validateArchitecture({ architecture: cfg }, { assignments: allAssignments, connections: allConnections }); if(ok) success(r); else document.getElementById('step-feedback').innerHTML='<div class="ko">Architecture invalide.</div>'; }; actions.appendChild(btn); mount.appendChild(actions);
}

// Start
boot();