// ==== Endpoints ====
window.API_CHANNELS = "/api/channel_post";
window.API_GROUPS   = "/api/group_post";
window.PARSER_API   = "/api/parse_channel";

// ==== Toastr (fallback -> alert) ====
function toast(type, msg) {
  if (window.toastr) {
    toastr.options = { positionClass: "toast-bottom-right", timeOut: 3000 };
    toastr[type](msg);
  } else {
    alert(msg);
  }
}
const ok  = (m) => toast("success", m);
const err = (m) => toast("error",   m);
const inf = (m) => toast("info",    m);
const warn= (m) => toast("warning", m);

// ==== Helpers ====
function endpointForKind(kind){
  if (kind === "main")      return `${API_CHANNELS}/main_channels`;
  if (kind === "draft")     return `${API_CHANNELS}/draft_channels`;
  if (kind === "ads")       return `${API_CHANNELS}/ads_channels`;
  if (kind === "selfpromo") return `${API_CHANNELS}/selfpromo_channels`;
  if (kind === "groupDraft")return `${API_GROUPS}/drafts`; // без привязки к группам
  return `${API_CHANNELS}/main_channels`;
}
function getUrlParam(name, fallback=null){
  const p = new URLSearchParams(window.location.search);
  return p.get(name) ?? fallback;
}
function selectOrInject(selectEl, rawVal){
  if (!selectEl) return;
  const val = (rawVal || "").trim();
  if (!val) return;
  const key = s => (s || "").trim().toLowerCase();
  for (const opt of selectEl.options){
    if (key(opt.value) === key(val) || key(opt.textContent) === key(val)) {
      opt.selected = true; return;
    }
  }
  const injected = document.createElement("option");
  injected.value = val; injected.textContent = val; injected.selected = true;
  selectEl.insertBefore(injected, selectEl.firstChild);
}
function selectByValue(selectEl, rawVal){
  if (!selectEl) return;
  const val = (rawVal ?? "").toString();
  for (const opt of selectEl.options){
    if (opt.value.toString() === val) { opt.selected = true; return; }
  }
}

// ==== Image preview ====
function bindImagePreview(inputId, previewId, clearBtnId) {
  const input   = document.getElementById(inputId);
  const preview = document.getElementById(previewId);
  const clear   = document.getElementById(clearBtnId);
  if (!input || !preview || !clear) return null;

  function show(src){
    preview.innerHTML = `<img src="${src}" alt="preview">`;
    clear.disabled = false;
  }
  function reset(){
    input.value = "";
    preview.innerHTML = `<span class="text-muted">Нет изображения</span>`;
    clear.disabled = true;
  }
  input.addEventListener("change", e => {
    const f = e.target.files[0];
    if (f){
      const r = new FileReader();
      r.onload = ev => show(ev.target.result);
      r.readAsDataURL(f);
    }
  });
  clear.addEventListener("click", reset);

  return { show, reset };
}

// ==== Public: init add form ====
window.initAddForm = function() {
  const kind = getUrlParam("kind", "main");

  // UI blocks
  const countryW = document.getElementById("countryWrapper");
  const categoryW= document.getElementById("categoryWrapper");
  const photoW   = document.getElementById("photoWrapper");
  const linkW    = document.getElementById("linkWrapper");
  const mainChanW= document.getElementById("mainChannelWrapper");
  const autoBtn  = document.getElementById("autoFillBtn");

  // Preview
  const previewCtl = bindImagePreview("photo","preview","clearPhotoBtn");

  // groupDraft: без привязки к группам, без страны/категории/фото/парсера/ссылки
  if (kind === "groupDraft") {
    countryW?.classList.add("d-none");
    categoryW?.classList.add("d-none");
    photoW?.classList.add("d-none");
    linkW?.classList.add("d-none");
    autoBtn?.classList.add("d-none");
    mainChanW?.classList.add("d-none");
  } else {
    // не-основные каналы показывают выбор главного канала
    if (["draft","ads","selfpromo"].includes(kind)) {
      mainChanW?.classList.remove("d-none");
      // загрузим список основных
      fetch(`${API_CHANNELS}/main_channels`, { cache: "no-store" })
        .then(r => r.json())
        .then(list => {
          const sel = document.getElementById("main_channel_id");
          if (!sel) return;
          sel.innerHTML = "";
          list.forEach(c => {
            const opt = document.createElement("option");
            opt.value = c.id;
            opt.textContent = `${c.name} (id=${c.id})`;
            sel.appendChild(opt);
          });
        }).catch(()=>{});
    }

    // автозаполнение (по ссылке) остаётся для каналов
    autoBtn?.addEventListener("click", async () => {
      const linkEl = document.getElementById("link");
      const link = (linkEl?.value || "").trim();
      if (!link) return warn("Введите ссылку для автозаполнения");
      try {
        const resp = await fetch(PARSER_API, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ link })
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || data.error || "Ошибка API");
        document.getElementById("telegram_id").value = data.telegram_id ?? data.tg_id ?? "";
        document.getElementById("name").value = data.name || "";
        if (data.photo && previewCtl) previewCtl.show(data.photo);
      } catch (e) { err("Автозаполнение не удалось: " + e.message); }
    });
  }

  // Submit
  document.getElementById("addChannelForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      if (kind === "groupDraft") {
        // только JSON: name + telegram_id
        const body = {
          name: document.getElementById("name").value.trim(),
          telegram_id: Number(document.getElementById("telegram_id").value)
        };
        if (!body.name || !body.telegram_id) throw new Error("Заполните имя и Telegram ID");
        const resp = await fetch(`${API_GROUPS}/drafts`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body)
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || data.error || "Ошибка сохранения");
      } else {
        const fd = new FormData(e.target);
        const resp = await fetch(endpointForKind(kind), { method: "POST", body: fd });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || data.error || "Ошибка сохранения");
      }
      ok("Сохранено");
      window.location.href = "/directories/channels/";
    } catch (e2) {
      err(e2.message || "Ошибка сохранения");
    }
  });
};

// ==== Public: init edit form ====
window.initEditForm = function(kind, chanId) {
  const countryW = document.getElementById("countryWrapper");
  const categoryW= document.getElementById("categoryWrapper");
  const photoW   = document.getElementById("photoWrapper");
  const mainChanW= document.getElementById("mainChannelWrapper");

  const previewCtl = bindImagePreview("photo","preview","clearPhotoBtn");

  async function ensureMainChannels(){
    const sel = document.getElementById('main_channel');
    if (!sel || sel.options.length > 1) return;
    try{
      const resp = await fetch(`${API_CHANNELS}/main_channels`, { cache: 'no-store' });
      if (!resp.ok) return;
      const chans = await resp.json();
      sel.innerHTML = "<option value=''>— Не выбрано —</option>";
      chans.forEach(c => {
        const opt = document.createElement('option');
        opt.value = c.id;
        opt.textContent = c.name;
        sel.appendChild(opt);
      });
    } catch(e){}
  }

  async function loadEntity(){
    try {
      let getUrl;
      if (kind === "groupDraft") getUrl = `${API_GROUPS}/drafts/${chanId}`;
      else                       getUrl = `${endpointForKind(kind)}/${chanId}`;

      const resp = await fetch(getUrl, { cache: 'no-store' });
      if (!resp.ok) throw new Error('Не удалось загрузить запись');
      const data = await resp.json();

      document.getElementById('name').value = data.name || '';
      const linkEl = document.getElementById('link');
      if (linkEl) linkEl.value = data.link || '';

      if (kind === "groupDraft") {
        countryW?.classList.add("d-none");
        categoryW?.classList.add("d-none");
        photoW?.classList.add("d-none");
        mainChanW?.classList.add("d-none");
      } else {
        selectOrInject(document.getElementById('country'),  data.country);
        selectOrInject(document.getElementById('category'), data.category);
        if (["draft","ads","selfpromo"].includes(kind)) {
          await ensureMainChannels();
          selectByValue(document.getElementById('main_channel'), data.main_channel_id);
        }
        if (data.photo && previewCtl) previewCtl.show(data.photo);
      }
    } catch (e) {
      err(e.message || "Ошибка загрузки");
    }
  }

  document.getElementById("editForm").addEventListener("submit", async e => {
    e.preventDefault();
    try {
      let url;
      if (kind === "groupDraft") {
        url = `${API_GROUPS}/drafts/${chanId}/edit`;
        const fd = new FormData();
        fd.append("name", document.getElementById("name").value.trim());
        const resp = await fetch(url, { method: "POST", body: fd });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || data.error || "Ошибка сохранения");
      } else {
        url = `${endpointForKind(kind)}/${chanId}/edit`;
        const fd = new FormData(e.target);
        const resp = await fetch(url, { method: "POST", body: fd });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || data.error || "Ошибка сохранения");
      }
      ok("Изменения сохранены");
      window.location.href = "/directories/channels/";
    } catch (e2) {
      err(e2.message || "Ошибка сохранения");
    }
  });

  loadEntity();
};
