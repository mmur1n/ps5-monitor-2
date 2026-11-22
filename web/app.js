'use strict';
const $ = selector => document.querySelector(selector);
const labels = {in_stock:'В наличии',stock_conflict:'Покупка без остатка',out_of_stock:'Нет в наличии',blocked:'Покупка недоступна',preorder:'Предзаказ',unknown:'Не удалось проверить',removed:'Убрано из каталога'};
const dates = new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Berlin'});
let snapshot = null, selected = 'all', refreshFailed = false;
const date = value => value && !Number.isNaN(Date.parse(value)) ? dates.format(new Date(value)) : 'ещё не было';
function safeUrl(value, kind) {
  try { const url=new URL(value); return url.protocol==='https:' && (kind==='image' ? ['gmedia.playstation.com','image.api.playstation.com','media.playstation.com'].includes(url.hostname) : url.hostname==='www.playstation.com') ? url.href : null; } catch { return null; }
}
function notice() {
  if (!snapshot) return;
  const messages=[];
  const age=snapshot.lastSuccessAt ? Date.now()-Date.parse(snapshot.lastSuccessAt) : Infinity;
  const attemptAge=snapshot.attemptedAt ? Date.now()-Date.parse(snapshot.attemptedAt) : Infinity;
  const elapsed=Number.isFinite(age) ? `${Math.max(0,Math.floor(age/60000))} мин назад` : 'ещё не было';
  $('#checked-at').textContent=`Последняя полная проверка: ${date(snapshot.lastSuccessAt)} · Берлин · ${elapsed}`;
  $('#attempted-at').textContent=snapshot.attemptedAt && snapshot.attemptedAt!==snapshot.lastSuccessAt ? `Последняя попытка: ${date(snapshot.attemptedAt)} · Берлин` : '';
  if (snapshot.health==='error') messages.push(snapshot.error || 'Ошибка проверки магазина.');
  else if (snapshot.health==='partial') messages.push(snapshot.error || 'Некоторые товары не удалось проверить.');
  if (attemptAge>8*60*1000) messages.push('Проверка задерживается. Нового результата пока нет. GitHub запускает проверки по расписанию каждые 5 минут, но может задерживать или пропускать запуски.');
  if (age>20*60*1000) messages.push('Последняя полная проверка устарела. Статусы могут отличаться от магазина.');
  if (refreshFailed) messages.push('Не удалось загрузить новые данные панели. Показана сохранённая версия.');
  if (snapshot.telegram?.error) messages.push(snapshot.telegram.error);
  $('#notice').textContent=messages.join(' '); $('#notice').hidden=messages.length===0;
}
function render() {
  if(!snapshot) return;
  const products=snapshot.products || [], active=products.filter(p=>p.listed!==false);
  const available=active.filter(p=>p.status==='in_stock').length;
  const conflicts=active.filter(p=>p.status==='stock_conflict').length;
  const other=active.filter(p=>!['in_stock','stock_conflict','out_of_stock'].includes(p.status)).length;
  $('#available-count').textContent=available+conflicts; $('#total-caption').textContent=`из ${active.length} консолей`;
  $('#overview-note').textContent=conflicts ? `С отметкой наличия: ${available} · покупка без остатка: ${conflicts}` : available ? 'Есть дисковые PS5, доступные к покупке' : 'Ждём возвращения дисковых PS5 в продажу';
  $('#telegram-label').textContent=snapshot.telegram?.configured ? snapshot.telegram.error ? 'Telegram: ошибка отправки' : snapshot.telegram.welcomeSentAt ? 'Telegram подключён' : 'Telegram: ожидает проверки' : 'Telegram: ожидает настройки';
  $('#count-all').textContent=active.length; $('#count-in_stock').textContent=available;
  $('#count-stock_conflict').textContent=conflicts;
  $('#count-out_of_stock').textContent=active.filter(p=>p.status==='out_of_stock').length; $('#count-other').textContent=other;
  const query=$('#search').value.trim().toLocaleLowerCase('ru');
  const priority={in_stock:0,stock_conflict:1,preorder:2,blocked:3,unknown:4,out_of_stock:5,removed:6};
  const shown=active.filter(p=>(selected==='all'||(selected==='other'?!['in_stock','stock_conflict','out_of_stock'].includes(p.status):p.status===selected)) && (!$('#new-only').checked||!p.refurbished) && p.name.toLocaleLowerCase('ru').includes(query)).sort((a,b)=>(priority[a.status]??9)-(priority[b.status]??9)||a.name.localeCompare(b.name));
  $('#results').textContent=`Показано ${shown.length} из ${active.length} · только с дисководом`;
  const container=$('#products');container.replaceChildren();
  if(!shown.length){const empty=document.createElement('p');empty.className='empty';empty.textContent=active.length ? 'По этим фильтрам консолей нет.' : 'Проверка ещё не получила товары. Подробности — в сообщении выше.';container.append(empty);}
  for(const p of shown){
    const fragment=$('#product-template').content.cloneNode(true);
    const img=fragment.querySelector('img'), src=safeUrl(p.image,'image');
    if(src){img.src=src;img.alt=p.name;} else img.hidden=true;
    fragment.querySelector('.edition').textContent=p.refurbished?'ВОССТАНОВЛЕННАЯ · DISC':'PS5 · DISC';
    const badge=fragment.querySelector('.badge');badge.classList.add(p.status in labels?p.status:'unknown');badge.textContent=labels[p.status]||labels.unknown;
    fragment.querySelector('h3').textContent=p.name;
    fragment.querySelector('.product-reason').textContent=p.reason||'';
    fragment.querySelector('.price').textContent=p.price||'Цена на сайте';
    fragment.querySelector('.product-time').textContent=`Проверка: ${date(p.checkedAt)} · Берлин`;
    const link=fragment.querySelector('.shop-link'), href=safeUrl(p.url,'product');
    if(href)link.href=href;else link.remove();
    container.append(fragment);
  }
  const history=$('#history');history.replaceChildren();
  const events=[...(snapshot.events||[])].filter(e=>e.status!=='unknown').reverse().slice(0,6);
  if(!events.length){const p=document.createElement('p');p.textContent='Изменений пока нет. Мониторинг продолжается.';history.append(p);}
  for(const event of events){const row=document.createElement('div');row.className='history-row';const time=document.createElement('time');time.dateTime=event.at;time.textContent=date(event.at);const name=document.createElement('span');name.textContent=event.name;const status=document.createElement('span');status.textContent=(labels[event.status]||'Статус обновлён')+(event.sentAt?' · Отправлено в Telegram':'');row.append(time,name,status);history.append(row);}
  notice();
}
async function refresh(){
  const button=$('#refresh');button.disabled=true;button.textContent='Загружаем…';
  try{const response=await fetch(`status.json?t=${Date.now()}`,{cache:'no-store'});if(!response.ok)throw Error();const data=await response.json();if(data.version!==1||!Array.isArray(data.products))throw Error();snapshot=data;refreshFailed=false;render();}
  catch{refreshFailed=true;if(snapshot)notice();else{$('#notice').hidden=false;$('#notice').textContent='Данные пока недоступны. Первая проверка должна завершиться в GitHub Actions. Попробуй обновить страницу через несколько минут.';$('#products').replaceChildren();$('#checked-at').textContent='Ожидаем первую проверку';}}
  finally{button.disabled=false;button.textContent='Обновить данные';}
}
document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{selected=button.dataset.filter;document.querySelectorAll('[data-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));render();}));
$('#search').addEventListener('input',render);$('#new-only').addEventListener('change',render);$('#refresh').addEventListener('click',refresh);
refresh();setInterval(()=>{if(!document.hidden)refresh();},60000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});setInterval(notice,60000);
