// PowerStation Exchange MVP — SPA-клиент (упрощенный интерфейс для демонстрации потоков)
const API = "/api/v1";
let TOKEN = localStorage.getItem("token") || "";

async function req(path, opts = {}) {
  const headers = {"Content-Type": "application/json", ...(opts.headers || {})};
  if (TOKEN) headers["Authorization"] = "Bearer " + TOKEN;
  const res = await fetch(API + path, {...opts, headers});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    toast(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail), true);
    throw new Error(data.detail || res.status);
  }
  return data;
}
function money(kop) { return (kop / 100).toLocaleString("ru-RU") + " ₽"; }
function toast(msg, err) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.style.display = "block";
  t.style.borderColor = err ? "#f87171" : "#4ade80";
  setTimeout(() => t.style.display = "none", 6000);
}
function badge(s) {
  const okS = ["APPROVED","ACTIVE","FULLY_SIGNED","VERIFIED","PAID","ON_EXCHANGE"];
  const warnS = ["PENDING","NEED_DOCS","UNDER_REVIEW","PARTIALLY_SIGNED","MODERATION","REASSEMBLY","SENT","DRAFT","SIGNING","EQUIPMENT_ORDER","PLANNED","COMMISSIONING","COLLECTING_CONSENTS"];
  const errS = ["REJECTED","EXPIRED","BLOCKED","SUSPENDED","FAILED","ON_HOLD"];
  const cls = okS.includes(s) ? "ok" : errS.includes(s) ? "err" : warnS.includes(s) ? "warn" : "";
  return `<span class="badge ${cls}">${s}</span>`;
}
function esc(s){return String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}

let ME = null;
const VIEWS = {};

// ---------------- Auth ----------------
VIEWS.login = () => `
<div class="card" style="max-width:420px;margin:60px auto">
 <h3>Вход</h3>
 <input id="l_email" placeholder="Email"><input id="l_pass" type="password" placeholder="Пароль">
 <button class="btn" onclick="doLogin()">Войти</button>
 <p class="mut">Демо: demo1@partner.ru / demo2@partner.ru / moderator@ps-ex.app / sb@ps-ex.app / finance@ps-ex.app — пароль DemoPass123!</p>
 <hr><h4>Регистрация участника</h4>
 <div id="regform">
  <input id="r_name" placeholder="ФИО"><input id="r_email" placeholder="Email">
  <input id="r_phone" placeholder="Телефон"><input id="r_pass" type="password" placeholder="Пароль (мин. 8)">
  <input id="r_company" placeholder="Название компании"><input id="r_inn" placeholder="ИНН (10/12 цифр)">
  <label><input type="checkbox" class="rc" value="location" style="width:auto"> Локация</label>
  <label><input type="checkbox" class="rc" value="capex" style="width:auto"> Капекс</label>
  <label><input type="checkbox" class="rc" value="service" style="width:auto"> Сервис</label>
  <label class="mut"><input type="checkbox" id="r_cons" style="width:auto"> Согласия: оферта, ПДн, антифрод, проверка контрагентов (FR-103)</label>
  <button class="btn green" onclick="doRegister()">Зарегистрироваться</button>
 </div>
</div>`;

async function doLogin() {
  const d = await req("/auth/login", {method:"POST", body: JSON.stringify({email:l_email.value, password:l_pass.value})});
  TOKEN = d.token; localStorage.setItem("token", TOKEN);
  await boot(); go("home");
}
async function doRegister() {
  if (!r_cons.checked) return toast("Необходимо принять все согласия (FR-103)", true);
  const roles = [...document.querySelectorAll(".rc:checked")].map(x=>x.value);
  const d = await req("/auth/register", {method:"POST", body: JSON.stringify({
    full_name:r_name.value, email:r_email.value, phone:r_phone.value, password:r_pass.value,
    company_name:r_company.value, inn:r_inn.value, roles,
    consents:["PD_PROCESSING","USER_AGREEMENT","ANTIFRAUD","COUNTERPARTY_CHECK"]})});
  TOKEN = d.token; localStorage.setItem("token", TOKEN);
  toast(d.hint); await boot(); go("profile");
}
function logout(){localStorage.removeItem("token");TOKEN="";ME=null;go("login");}

// ---------------- Home ----------------
VIEWS.home = () => {
  const org = ME.org;
  const notif = (ME.notifications||[]).slice(0,8).map(n=>`<li>${esc(n.message)} <a href="#" onclick="go('${(n.link||'').replace('/#/','')}');return false" class="mut">→</a></li>`).join("");
  return `
  <div class="grid">
   <div class="card"><h3>Моя организация</h3>
    ${org ? `${esc(org.legal_name)} (ИНН ${org.inn}) ${badge(org.sb_status)} ${org.blocked?'<span class="badge err">BLOCKED</span>':''}<br>
    <span class="mut">Роли: ${org.roles.map(r=>r.role_type+(r.is_active?"":" (выкл.)")).join(", ")||"—"}</span>`
    : `<span class="mut">Сотрудник платформы: ${esc(ME.user.system_role)}</span>`}
    <br><button class="btn gray" onclick="go('profile')">Профиль</button></div>
   <div class="card"><h3>💰 Баланс</h3><div id="bal">…</div><button class="btn" onclick="go('finance')">Финансы</button></div>
   <div class="card"><h3>🔔 Уведомления</h3><ul class="mut">${notif||"<li>нет</li>"}</ul></div>
   <div class="card"><h3>Быстрые действия</h3>
    <button class="btn" onclick="go('exchange')">Биржа заявок</button>
    <button class="btn" onclick="go('newapp')">+ Новая заявка</button>
    <button class="btn" onclick="go('stations')">Мои станции</button>
    <button class="btn" onclick="go('documents')">Документы</button></div>
  </div>`;
};
async function loadBal(){ try{ if(!ME.org) {bal.innerHTML="<span class='mut'>нет организации</span>";return;}
  const b = await req("/finance/balance");
  bal.innerHTML = `Доступно: <b class='money'>${money(b.available)}</b><br>Ожидает сверки: ${money(b.pending)}<br>Заблокировано: ${money(b.blocked)}${b.debt<0?`<br><span style='color:#f87171'>Долг: ${money(-b.debt)}</span>`:""}`;
 }catch(e){bal.innerHTML="<span class='mut'>—</span>";}}

// ---------------- Профиль ----------------
VIEWS.profile = async () => {
  if (!ME.org) return `<div class="card"><h3>Профиль сотрудника платформы (${esc(ME.user.system_role)})</h3><p class="mut">Рабочие разделы — в админке.</p></div>`;
  const o = await req("/organizations/me");
  return `<div class="card"><h3>${esc(o.legal_name)} ${badge(o.sb_status)}</h3>
   <table>
    <tr><td>ИНН / КПП / ОГРН</td><td>${o.inn} / ${esc(o.kpp)||"—"} / ${esc(o.ogrn)||"—"}</td></tr>
    <tr><td>Юр. адрес</td><td>${esc(o.legal_address)||"—"}</td></tr>
    <tr><td>ЭДО ID</td><td>${esc(o.edo_id)||"—"}</td></tr>
    <tr><td>Подписант</td><td>${esc(o.signer_name)} (${esc(o.signer_authority)})</td></tr>
    <tr><td>Рейтинг</td><td>${o.rating}</td></tr>
    <tr><td>Банковские реквизиты</td><td>${esc(o.bank_details)||"—"} ${o.bank_details_verified?badge("APPROVED"):'<span class="badge warn">НЕ ПОДТВЕРЖДЕНЫ</span>'}</td></tr>
   </table>
   <h4>Изменить реквизиты (потребуется повторная верификация — 31.5)</h4>
   <input id="p_bank" placeholder="Новые реквизиты"><button class="btn" onclick="updBank()">Сохранить</button>
   <h4>Разрешенные роли</h4>
   ${o.roles.map(r=>`<span class="badge ${r.is_active?'ok':'err'}">${r.role_type}</span>`).join(" ")}
   <select id="p_role"><option value="location">Локация</option><option value="capex">Капекс</option><option value="service">Сервис</option></select>
   <input id="p_regions" placeholder="Регионы (Москва, СПб)"><button class="btn green" onclick="addRole()">Добавить/включить роль</button>
  </div>`;
};
async function updBank(){await req("/organizations/me/bank-details",{method:"POST",body:JSON.stringify({bank_details:p_bank.value})});toast("Реквизиты изменены, ожидают подтверждения");}
async function addRole(){await req("/organizations/me/roles",{method:"POST",body:JSON.stringify({role_type:p_role.value,regions:p_regions.value})});toast("Роль добавлена");go("profile");}

// ---------------- Новая заявка (мастер) ----------------
VIEWS.newapp = async () => {
  const types = await req("/admin/station-types");
  return `<div class="card"><h3>Создание заявки (мастер из 6 шагов)</h3>
  <label>Шаг 1–2. Локация (поиск по справочнику)</label>
  <input id="a_q" placeholder="адрес / пермалинк" oninput="searchLoc()"><div id="locres"></div>
  <label>Шаг 3. Параметры точки</label>
  <select id="a_traffic"><option value="low">Трафик низкий</option><option value="medium" selected>Трафик средний</option><option value="high">Трафик высокий</option></select>
  <input id="a_hours" placeholder="Часы работы (08:00–23:00)">
  <label><input type="checkbox" id="a_socket" checked style="width:auto"> Розетка 220V</label>
  <label><input type="checkbox" id="a_net" checked style="width:auto"> Интернет</label>
  <label><input type="checkbox" id="a_mount" checked style="width:auto"> Монтаж возможен</label>
  <label>Шаг 4. Какие роли беру на себя</label>
  <label><input type="checkbox" class="ar" value="location" style="width:auto"> Локация</label>
  <label><input type="checkbox" class="ar" value="capex" style="width:auto"> Капекс</label>
  <label><input type="checkbox" class="ar" value="service" style="width:auto"> Сервис</label>
  <input id="a_note" placeholder="Комментарий платформе (опционально)">
  <button class="btn" onclick="createApp()">Создать заявку → экономика</button>
  <div id="econ"></div></div>`;
};
let SEL_LOC = null;
async function searchLoc(){
  const r = await req(`/locations/search?q=${encodeURIComponent(a_q.value)}`);
  locres.innerHTML = r.map(l=>`<div class="card" style="padding:8px;cursor:pointer" onclick="SEL_LOC=${l.id};this.parentNode.querySelectorAll('.card').forEach(c=>c.style.outline='');this.style.outline='2px solid #38bdf8'">
   <b>${esc(l.address)}</b><br><span class="mut">${esc(l.permalink)} · ${badge(l.verification_status)}</span></div>`).join("")||"<span class='mut'>не найдено — создайте локацию через API (POST /locations)</span>";
}
async function createApp(){
  if(!SEL_LOC) return toast("Выберите локацию",true);
  const roles=[...document.querySelectorAll(".ar:checked")].map(x=>x.value);
  const d=await req("/applications",{method:"POST",body:JSON.stringify({location_id:SEL_LOC,roles,
    traffic:a_traffic.value,working_hours:a_hours.value,has_socket:a_socket.checked,
    has_internet:a_net.checked,mount_possible:a_mount.checked,author_note:a_note.value})});
  toast(`Заявка #${d.id} создана`);
  window.APP_ID=d.id; showEcon();
}
async function showEcon(typeId){
  const body=typeId?{station_type_id:typeId}:null;
  const e=await req(`/applications/${APP_ID}/economics`,{method:"POST",body:body?JSON.stringify(body):undefined});
  if(e.error)return toast(e.error,true);
  econ.innerHTML=`<h4>Шаг 5. Экономика и тип станции</h4>
   <table><tr><th>Тип</th><th>Capex</th><th>Прогноз GMV/мес</th><th>Доля капекса</th><th>Окупаемость</th><th></th></tr>
   ${(e.types||[]).length? "" : ""}
   <tr><td>${esc(e.station_type.name)} (${e.station_type.slots} сл.)</td><td>${money(e.station_type.capex_cost)}</td>
   <td class="money">${money(e.forecast_gmv)} <span class="mut">(${money(e.gmv_min)}–${money(e.gmv_max)})</span></td>
   <td>${money(e.shares.capex)}</td><td>${e.payback_months} мес.</td>
   <td>${(e.warnings||[]).map(w=>`<span class="badge warn">⚠</span>`).join("")}</td></tr></table>
   <p class="mut">${esc(e.recommendation? "Рекомендация: тип #"+e.recommendation.station_type_id+" ("+e.recommendation.slots+" слотов) — "+esc(e.recommendation.reason):"")}</p>
   ${(e.warnings||[]).map(w=>`<p class="badge warn">⚠ ${esc(w)}</p>`).join("")}
   <p class="mut">${esc(e.disclaimer)}</p>
   <button class="btn green" onclick="publishApp(${APP_ID})">Шаг 6. Опубликовать на биржу</button>`;
}
async function publishApp(id){
  const d=await req(`/applications/${id}/publish`,{method:"POST"});
  toast("Заявка отправлена на модерацию: "+d.status); go("myapps");
}

// ---------------- Заявки ----------------
VIEWS.myapps = async () => {
  const apps=await req("/applications?mine=true");
  return `<div class="card"><h3>Мои заявки</h3><table><tr><th>#</th><th>Адрес</th><th>Статус</th><th>Тип</th><th>Прогноз</th><th>Роли</th><th></th></tr>
  ${apps.map(a=>`<tr><td>${a.id}</td><td>${esc(a.address)}</td><td>${badge(a.status)}</td><td>${esc(a.station_type)}</td>
   <td>${money(a.forecast_gmv)}</td><td>${a.roles.map(r=>`${r.role}:${r.filled?'✅':'🟦'}`).join(" ")}</td>
   <td><button class="btn gray" onclick="go('appdetail',${a.id})">Открыть</button></td></tr>`).join("")}</table></div>`;
};
VIEWS.appdetail = async (id) => {
  const a=await req(`/applications/${id}`);
  let cand="";
  try{ cand=await req(`/exchange/applications/${id}/candidates`);}catch(e){}
  return `<div class="card"><h3>Заявка #${a.id} ${badge(a.status)}</h3>
   <p><b>${esc(a.location.address)}</b> <span class="mut">${esc(a.location.permalink)}</span><br>
   Тип: ${esc(a.station_type.name)} · Прогноз: ${money(a.forecast_gmv)} · Часы: ${esc(a.working_hours)}<br>
   Автор: ${esc(a.author.name)} (рейтинг ${a.author.rating})</p>
   <p class="mut">${esc(a.recommendation)}</p>
   <table><tr><th>Роль</th><th>Статус</th><th>Назначена</th><th></th></tr>
   ${a.roles.map(r=>`<tr><td>${r.role}</td><td>${r.filled?badge("ACTIVE"):badge("ON_EXCHANGE")}</td><td>${r.assigned_org_id||"—"}</td>
    <td>${!r.filled&&["ON_EXCHANGE","REASSEMBLY"].includes(a.status)?`<input id="as_${r.role}" type="number" placeholder="org_id" style="width:90px"><button class="btn" onclick="assignOrg(${id},'${r.role}',as_${r.role}.value)">Назначить</button>`:""}</td></tr>`).join("")}</table>
   ${cand.length?`<h4>Кандидаты (скоринг FR-502)</h4><table><tr><th>Организация</th><th>Роль</th><th>Score</th><th>Рейтинг</th><th>Оффер</th><th></th></tr>
   ${cand.map(c=>`<tr><td>${esc(c.name)} (#${c.org_id})</td><td>${c.role_type}</td><td><b>${c.score}</b></td><td>${c.rating}</td><td class="mut">${esc(c.commercial_offer)}</td>
   <td>${c.status==="SUBMITTED"?`<button class="btn green" onclick="assignOrg(${id},'${c.role_type}',${c.org_id})">Утвердить</button>`:badge(c.status)}</td></tr>`).join("")}</table>`:""}
   <div id="asm"></div></div>`;
};
async function assignOrg(appId, role, orgId){
  await req(`/exchange/applications/${appId}/assign`,{method:"POST",body:JSON.stringify({role_type:role,org_id:+orgId})});
  toast("Роль назначена"); go("appdetail",appId);
}

// ---------------- Биржа ----------------
VIEWS.exchange = async () => {
  let list=[];
  try{ list=await req("/exchange/applications"); }catch(e){ return `<div class="card"><h3>Биржа</h3><p class="mut">${esc(e.message)}</p></div>`; }
  return `<div class="card"><h3>🏪 Биржа заявок (сортировка по прогнозу GMV)</h3>
  ${list.length===0?"<p class='mut'>Нет активных публикаций</p>":""}
  <div class="grid">${list.map(a=>`<div class="card">
   <b>${esc(a.address)}</b><br><span class="mut">${esc(a.city)} · ${esc(a.station_type)} (${a.slots} сл.) · автор рейтинг ${a.author_rating}</span><br>
   Прогноз: <span class="money">${money(a.forecast_gmv)}</span>/мес<br>
   Свободные роли: ${a.open_roles.map(r=>`<span class="badge warn">${r}</span>`).join(" ")}<br>
   <select id="pr_${a.id}">${a.open_roles.map(r=>`<option>${r}</option>`).join("")}</select>
   <input id="po_${a.id}" placeholder="Коммерческое предложение"><br>
   <button class="btn" onclick="propose(${a.id})">Откликнуться</button>
   <button class="btn gray" onclick="go('appdetail',${a.id})">Детали</button>
  </div>`).join("")}</div></div>`;
};
async function propose(id){
  await req(`/exchange/applications/${id}/proposals`,{method:"POST",
    body:JSON.stringify({role_type:eval(`pr_${id}`).value,commercial_offer:eval(`po_${id}`).value})});
  toast("Отклик отправлен, автору заявки пришло уведомление");
}

// ---------------- Станции ----------------
VIEWS.stations = async () => {
  const sts=await req("/stations");
  return `<div class="card"><h3>Мои станции</h3><table><tr><th>Код</th><th>Адрес</th><th>Статус</th><th>GMV</th><th>Доли</th><th></th></tr>
  ${sts.map(s=>`<tr><td><b>${s.code}</b></td><td>${esc(s.address)}</td><td>${badge(s.status)}</td>
   <td class="money">${money(s.gmv_total)}</td>
   <td class="mut">${s.participants.map(p=>p.roles.join("+")+":"+p.share+"%").join(", ")}</td>
   <td><button class="btn gray" onclick="go('station',${s.id})">Открыть</button></td></tr>`).join("")}</table></div>`;
};
VIEWS.station = async (id) => {
  const s=await req(`/stations/${id}`);
  const steps={PLANNED:"Цепочка собрана",DOCS_PENDING:"Ожидание документов",EQUIPMENT_ORDER:"Заказ оборудования",
    DELIVERY:"Доставка",INSTALLATION:"Монтаж",COMMISSIONING:"Комиссионирование",ACTIVE:"✅ Активна"};
  let fin="";try{fin=await req(`/finance/stations/${id}/revenue`);}catch(e){}
  return `<div class="card"><h3>Станция ${s.external_code} ${badge(s.status)}</h3>
   <p class="mut">${esc(s.location.address)} · ${esc(s.station_type.name)} · QR: ${esc(s.qr_payload)} · телеметрия ${esc(s.telemetry_id)}</p>
   <p>${Object.entries(steps).map(([k,v])=>`<span class="badge ${s.status===k?'ok':''}">${v}</span>`).join(" → ")}</p>
   <table><tr><th>Участник</th><th>Роли</th><th>Доля</th><th>Статус</th></tr>
   ${s.participants.map(p=>`<tr><td>${esc(p.name)}</td><td>${p.roles.join(", ")}</td><td>${p.share}%</td><td>${badge(p.status)}</td></tr>`).join("")}</table>
   ${s.specification?`<p class="mut">Спецификация: <b>${esc(s.specification.number)}</b> v${s.specification.version} ${badge(s.specification.status)}</p>`:""}
   ${s.status==="EQUIPMENT_ORDER"?`<button class="btn" onclick="equip(${id})">Оборудование доставлено</button>`:""}
   ${s.status==="DELIVERY"?`<button class="btn" onclick="install(${id})">Выполнить монтаж (чек-лист)</button>`:""}
   ${s.status==="COMMISSIONING"?`<button class="btn green" onclick="act(${id})">Активировать станцию</button>`:""}
   ${s.status==="ACTIVE"?`<button class="btn" onclick="simPay(${id})">💳 Эмуляция платежа клиента</button>
     <button class="btn red" onclick="suspend(${id})">Приостановить</button>
     <button class="btn gray" onclick="openTask(${id})">Создать инцидент</button>`:""}
   ${s.status==="SUSPENDED"?`<button class="btn green" onclick="resume(${id})">Возобновить</button>`:""}
   ${fin?`<h4>Финансы станции</h4><p>GMV: <b class="money">${money(fin.gmv)}</b> · Возвраты: ${money(fin.refunds)} · Комиссия платформы: ${money(fin.platform_fee)}</p>`:""}
   <h4>Последние платежи</h4><table>${s.payments.slice(0,10).map(p=>`<tr><td>${p.created_at.slice(0,16)}</td><td>${money(p.amount)}</td><td>${badge(p.status)}</td></tr>`).join("")}</table>
   ${s.open_tasks.length?`<h4>Открытые задания</h4>${s.open_tasks.map(t=>`<p><span class="badge warn">${t.priority}</span> ${esc(t.description)} <button class="btn gray" onclick="doneTask(${t.id})">Готово</button></p>`).join("")}`:""}
   </div>`;
};
async function equip(id){await req(`/stations/${id}/equipment-status?status=DELIVERED`,{method:"POST"});go("station",id);}
async function install(id){await req(`/stations/${id}/installation`,{method:"POST",body:JSON.stringify({checklist:{крепление:true,розетка_220v:true,интернет:true,сканирование_QR:true,чистота:true}})});go("station",id);}
async function act(id){await req(`/stations/${id}/activate`,{method:"POST"});toast("Станция активна!");go("station",id);}
async function suspend(id){await req(`/stations/${id}/status`,{method:"POST",body:JSON.stringify({status:"SUSPENDED",reason:"жалоба локации"})});go("station",id);}
async function resume(id){await req(`/stations/${id}/status`,{method:"POST",body:JSON.stringify({status:"ACTIVE"})});go("station",id);}
async function openTask(id){const d=prompt("Описание инцидента");if(d){await req(`/stations/${id}/tasks`,{method:"POST",body:JSON.stringify({description:d,priority:"normal"})});toast("Инцидент создан, сервис уведомлен");go("station",id);}}
async function doneTask(tid){await req(`/stations/tasks/${tid}/complete`,{method:"POST"});location.reload();}
async function simPay(id){
  const amount=Math.round(parseFloat(prompt("Сумма платежа в рублях","199"))*100);
  if(!amount)return;
  const key="pay-"+Date.now()+"-"+Math.random();
  await req(`/stations/${id}/payments`,{method:"POST",body:JSON.stringify({amount,idempotency_key:key,end_user_ref:"user-demo",accept_offer:true})});
  toast(`Платеж ${money(amount)} распределен: 35% платформа, доли участникам`); go("station",id);
}

// ---------------- Документы ----------------
VIEWS.documents = async () => {
  const docs=await req("/documents");
  return `<div class="card"><h3>Документы ${badge("EDO")}</h3><table><tr><th>ID</th><th>Тип</th><th>Станция</th><th>Статус</th><th>Подписали</th><th></th></tr>
  ${docs.map(d=>`<tr><td>${d.id}</td><td>${d.doc_type}</td><td>${d.station}</td><td>${badge(d.status)}</td>
   <td>${(d.signed_by||[]).join(",")||"—"} / ${(d.sign_required||[]).join(",")}</td>
   <td>${d.status!=="FULLY_SIGNED"?`<button class="btn green" onclick="signDoc(${d.id})">Подписать</button>
   <button class="btn red" onclick="rejectDoc(${d.id})">Отказ</button>`:""}
   <button class="btn gray" onclick="viewDoc(${d.id})">Смотреть</button></td></tr>`).join("")}</table>
   <div id="docbody"></div></div>`;
};
async function signDoc(id){await req(`/documents/${id}/sign`,{method:"POST",body:JSON.stringify({signature:"ЭП"})});toast("Подписано (имитация ЭДО)");go("documents");}
async function rejectDoc(id){const r=prompt("Причина отказа");if(r){await req(`/documents/${id}/reject`,{method:"POST",body:JSON.stringify({reason:r})});toast("Отказ передан платформе — досборка");go("documents");}}
async function viewDoc(id){const d=await req(`/documents/${id}`);docbody.innerHTML=`<pre>${esc(d.body)}</pre>`;}

// ---------------- Финансы ----------------
VIEWS.finance = async () => {
  let txs=[],rep=null;
  try{txs=await req("/finance/transactions");}catch(e){}
  if(ME.org){try{rep=await req("/finance/agent-report");}catch(e){}}
  return `<div class="card"><h3>Финансы ${ME.org?`(организация #${ME.org.id})`:"(партнер)"}</h3>
  ${rep?`<p>Начислено за период: <b class="money">${rep.money.accrued}</b> · Выплачено: ${rep.money.paid} · К выплате: ${rep.money.to_pay} · Долг: ${rep.money.available.startsWith("-")?rep.money.available:"0"}</p>`:""}
  <table><tr><th>Дата</th><th>Операция</th><th>Сумма</th><th>Назначение</th></tr>
  ${txs.map(t=>`<tr><td>${t.created_at.slice(0,16)}</td><td>${t.op}</td><td class="${t.op==='PARTNER_SHARE'?'money':''}">${money(t.amount)}</td><td class="mut">${esc(t.purpose)}</td></tr>`).join("")}</table></div>`;
};

// ---------------- Админка ----------------
VIEWS.admin = async () => {
  const role=ME.user.system_role;
  let dash=null,sbq=[],modq=[];
  try{dash=await req("/admin/dashboard");}catch(e){}
  if(["security","admin"].includes(role))sbq=await req("/admin/security/pending");
  if(["moderator","admin"].includes(role))modq=await req("/admin/moderation/applications");
  return `<div class="card"><h3>🛠 Админка (${esc(role)})</h3>
  ${dash?`<div class="grid">
    <div class="card">Организаций: <b>${dash.organizations}</b></div>
    <div class="card">СБ на проверке: <b>${dash.sb_pending}</b></div>
    <div class="card">Заявок на бирже: <b>${dash.applications_on_exchange}</b></div>
    <div class="card">Станций активных: <b>${dash.stations_active}</b></div>
    <div class="card">GMV: <b class="money">${money(dash.gmv_total)}</b></div>
    <div class="card">Комиссия платформы: <b class="money">${money(dash.platform_fee)}</b></div></div>`:""}
  ${sbq.length?`<h4>Очередь СБ (FR-102)</h4>${sbq.map(o=>`<p>${esc(o.legal_name)} (ИНН ${o.inn}, роли: ${o.roles.join(",")})
    <button class="btn green" onclick="sbDec(${o.id},'APPROVED')">Одобрить</button>
    <button class="btn" onclick="sbDec(${o.id},'NEED_DOCS')">Запросить документы</button>
    <button class="btn red" onclick="sbDec(${o.id},'REJECTED')">Отказать</button></p>`).join("")}`:""}
  ${modq.length?`<h4>Модерация заявок</h4>${modq.map(a=>`<p>#${a.id} ${esc(a.address)} от ${esc(a.author)} — прогноз ${money(a.forecast_gmv)}
    <button class="btn green" onclick="modApp(${a.id},true)">Опубликовать</button>
    <button class="btn red" onclick="modApp(${a.id},false)">Отклонить</button></p>`).join("")}`:""}
  <h4>Инструменты</h4>
  <button class="btn" onclick="assembleAll()">⚙ Собрать цепочки готовых заявок</button>
  <button class="btn" onclick="releasePending()">💱 Сверка pending→available</button>
  <button class="btn" onclick="makePayouts()">💸 Формировать выплаты (реестр)</button>
  <button class="btn" onclick="approvePayouts()">✔ Утвердить выплаты (четыре глаза)</button>
  <button class="btn" onclick="payPayouts()">🏦 Отметить исполнение</button>
  <button class="btn gray" onclick="showAudit()">📜 Аудит-журнал</button>
  <div id="admout" class="mut"></div></div>`;
};
async function sbDec(id,d){await req(`/admin/security/${id}/decision`,{method:"POST",body:JSON.stringify({decision:d,comments:"MVP демо"})});go("admin");}
async function modApp(id,ok){
  if(ok)await req(`/applications/${id}/approve`,{method:"POST"});
  else{const r=prompt("Причина")||"недостаточно данных";await req(`/applications/${id}/reject`,{method:"POST",body:JSON.stringify({reason:r})});}
  go("admin");
}
async function assembleAll(){
  // находим заявки ON_EXCHANGE с заполненными ролями и собираем
  const apps=await req("/applications");
  for(const a of apps.filter(x=>["ON_EXCHANGE","REASSEMBLY"].includes(x.status)&&x.roles.every(r=>r.filled))){
    try{const r=await req(`/exchange/applications/${a.id}/assemble`,{method:"POST"});
      admout.innerHTML+=`<p>✅ Заявка #${a.id} → станция <b>${r.external_code}</b>, документов: ${r.documents.length}</p>`;}catch(e){}
  }
  toast("Сборка выполнена");
}
async function releasePending(){
  const sts=await req("/stations");
  for(const s of sts){try{await req(`/admin/finance/release-pending/${s.id}`,{method:"POST"});}catch(e){}}
  toast("Начисления после сверки переведены в доступные");
}
async function makePayouts(){
  const period=prompt("Период (YYYY-MM)",new Date().toISOString().slice(0,7));
  if(!period)return;
  const r=await req(`/finance/payouts?period=${period}`,{method:"POST"});
  admout.innerHTML+=`<p>Выплаты созданы: ${r.payout_ids.join(", ")||"нет доступных средств"}</p>`;
}
async function approvePayouts(){
  const pos=await req("/finance/payouts");
  for(const p of pos.filter(x=>x.status==="PENDING_APPROVAL")){
    try{await req(`/finance/payouts/${p.id}/approve`,{method:"POST"});}catch(e){admout.innerHTML+=`<p class="mut">выплата ${p.id}: ${esc(e.message)}</p>`;}
  }
  toast("Утверждено другим сотрудником (четыре глаза)");
}
async function payPayouts(){
  const pos=await req("/finance/payouts");
  for(const p of pos.filter(x=>x.status==="APPROVED"))await req(`/finance/payouts/${p.id}/sent`,{method:"POST"}).catch(()=>{});
  for(const p of pos.filter(x=>x.status==="SENT_TO_BANK"||x.status==="APPROVED"))await req(`/finance/payouts/${p.id}/paid`,{method:"POST"}).catch(()=>{});
  toast("Реестр отправлен в банк и исполнен (имитация)");go("admin");
}
async function showAudit(){
  const logs=await req("/admin/audit?limit=40");
  admout.innerHTML=`<table><tr><th>Время</th><th>Кто</th><th>Действие</th><th>Объект</th><th>Причина</th></tr>
  ${logs.map(l=>`<tr><td>${l.created_at.slice(0,16)}</td><td>${l.actor_type}:${l.actor_id??"—"}</td><td>${l.action}</td><td>${l.entity}</td><td class="mut">${esc(l.reason)}</td></tr>`).join("")}</table>`;
}

// ---------------- Роутинг ----------------
const NAVS = [
  ["home","Главная"],["exchange","Биржа"],["newapp","+ Заявка"],["myapps","Мои заявки"],
  ["stations","Станции"],["documents","Документы"],["finance","Финансы"],["profile","Профиль"],["admin","Админка"],
];
let CURRENT="";
function go(view, arg){CURRENT=view;render(view,arg);}
async function render(view,arg){
  nav.innerHTML=NAVS.filter(([k])=>k!=="admin"||ME&&(ME.user.system_role!=="partner")).map(([k,t])=>
    `<button class="${k===CURRENT?'active':''}" onclick="go('${k}')">${t}</button>`).join("");
  main.innerHTML="<div class='card'>Загрузка…</div>";
  try{
    const v=VIEWS[view];
    main.innerHTML=typeof v==="function"&&v.constructor.name==="AsyncFunction"?await v(arg):(v(arg)??"");
  }catch(e){main.innerHTML=`<div class="card"><h3>Ошибка</h3><pre>${esc(e.message)}</pre></div>`;}
  if(view==="home")loadBal();
}
async function boot(){
  if(!TOKEN){go("login");return;}
  try{ME=await req("/auth/me");}catch(e){ME=null;go("login");return;}
  whoami.textContent=`${ME.user.name} · ${ME.user.system_role}${ME.org?` · ${ME.org.legal_name} (${ME.org.sb_status})`:""}`;
  go("home");
}
boot();
