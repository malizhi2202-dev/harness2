/* 原型交互引擎：drawer/modal/pop/toast + 跨页深链（mock 数据宇宙通用：agent-7 / r-128 / wf-42 / Notion 401…） */
document.addEventListener('click',e=>{
  const bk=e.target.closest('[data-back]');
  if(bk){e.preventDefault();if(history.length>1)history.back();else location.href=bk.dataset.back;return;}
  const g=e.target.closest('[data-goto]');
  if(g){e.preventDefault();const t=g.dataset.goto;
    if(window.top!==window.self){try{window.top.location.hash='#'+t;return;}catch(_){}}
    location.href=t;return;}
  const t=e.target.closest('[data-toast],[data-drawer],[data-modal],[data-pop],[data-close]');
  if(!t)return;
  if(t.hasAttribute('data-close')){closeAll();return;}
  e.preventDefault();
  if(t.dataset.toast){toast(t.dataset.toast);}
  if(t.dataset.drawer){document.getElementById('drawer-mask').classList.add('on');document.getElementById(t.dataset.drawer).classList.add('on');}
  if(t.dataset.modal){document.getElementById('modal-mask').classList.add('on');document.getElementById(t.dataset.modal).classList.add('on');}
  if(t.dataset.pop){closeAll();const p=document.getElementById(t.dataset.pop);const r=t.getBoundingClientRect();p.style.left=Math.min(r.left,innerWidth-320)+'px';p.style.top=(r.bottom+6)+'px';p.classList.add('on');}
});
document.addEventListener('click',e=>{if(!e.target.closest('.pop,[data-pop]'))document.querySelectorAll('.pop.on').forEach(x=>x.classList.remove('on'));},true);
function closeAll(){document.querySelectorAll('.drawer.on,.modal.on,.drawer-mask.on,.modal-mask.on,.pop.on').forEach(x=>x.classList.remove('on'));}
document.addEventListener('click',e=>{if(e.target.id==='drawer-mask'||e.target.id==='modal-mask')closeAll();});
let _tt;function toast(m){const el=document.getElementById('toast');el.textContent=m;el.classList.add('on');clearTimeout(_tt);_tt=setTimeout(()=>el.classList.remove('on'),2600);}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeAll();});
/* 深链：#dr-x / #md-x / #pp-x 直接展开对应浮层（跨页跳转用） */
document.addEventListener('DOMContentLoaded',()=>{
  const h=decodeURIComponent(location.hash.slice(1));if(!h)return;
  const el=document.getElementById(h);if(!el)return;
  if(el.classList.contains('drawer')){document.getElementById('drawer-mask').classList.add('on');el.classList.add('on');}
  else if(el.classList.contains('modal')){document.getElementById('modal-mask').classList.add('on');el.classList.add('on');}
  else if(el.classList.contains('pop')){el.style.left='80px';el.style.top='90px';el.classList.add('on');}
});
