"use strict";
/* Справка: законы и тендерные площадки. Подключается после app.js. */

const PLATFORMS = [
  // kinds: com — коммерческие, 223, 44, 615 (капремонт), small — малые закупки, agg — агрегатор (поиск по многим площадкам)
  { id: "b2b", name: "B2B-Center", url: "https://www.b2b-center.ru", dom: "b2b-center.ru", kinds: ["com", "223"], note: "Крупнейшая площадка коммерческих закупок: заводы, энергетика, нефтегаз, ритейл. Участие по тарифам." },
  { id: "bidzaar", name: "Bidzaar", url: "https://bidzaar.com", dom: "bidzaar.com", kinds: ["com"], note: "Коммерческие закупки компаний. Участие бесплатно, без электронной подписи и депозитов. Калькулятор открывает закупки Bidzaar по ссылке со встроенным браузером." },
  { id: "tenderpro", name: "Tender.Pro", url: "https://www.tender.pro", dom: "tender.pro", kinds: ["com"], note: "Коммерческие тендеры частных компаний (металлургия, агро, ритейл, стройка). Быстрая регистрация, участие без электронной подписи.", search: (q) => `https://www.tender.pro/api/tenders/list?tender_name=${encodeURIComponent(q)}` },
  { id: "fabrikant", name: "Фабрикант", url: "https://www.fabrikant.ru", dom: "fabrikant.ru", kinds: ["com", "223", "615"], note: "Коммерческие и корпоративные закупки, капремонт. Плата, как правило, с победителя." },
  { id: "otc", name: "OTC", url: "https://otc.ru", dom: "otc.ru", kinds: ["com", "223", "small"], note: "Корпоративные и малые закупки, запросы коммерческих предложений." },
  { id: "estp", name: "ESTP", url: "http://estp.ru", dom: "estp.ru", kinds: ["com", "223"], note: "Коммерческие и корпоративные закупки." },
  { id: "sberb2b", name: "SberB2B", url: "https://sberb2b.ru", dom: "sberb2b.ru", kinds: ["com", "small"], note: "Закупки компаний и маркетплейс для бизнеса от Сбера." },
  { id: "istock", name: "iStock.Link", url: "https://zakupki.istock.link", dom: "istock.link", kinds: ["com"], note: "Запросы цен и коммерческие закупки." },
  { id: "etpp", name: "ЕТП ТПП", url: "https://etpp.ru", dom: "etpp.ru", kinds: ["com"], note: "Площадка Торгово-промышленной палаты." },
  { id: "rosel-biz", name: "Росэлторг.Бизнес", url: "https://business.roseltorg.ru", dom: "roseltorg.ru", kinds: ["com"], note: "Коммерческая секция Росэлторга (здесь была закупка «Звёздочки»)." },
  { id: "sber-utp", name: "Сбер А: УТП (коммерческие и 223-ФЗ)", url: "https://utp.sberbank-ast.ru", dom: "sberbank-ast.ru", kinds: ["com", "223"], note: "Универсальная торговая платформа Сбербанк-АСТ." },
  { id: "sber", name: "Сбер А (Сбербанк-АСТ)", url: "https://www.sberbank-ast.ru", dom: "sberbank-ast.ru", kinds: ["44", "223", "615"], note: "Федеральная площадка из перечня Правительства." },
  { id: "rts", name: "РТС-тендер", url: "https://www.rts-tender.ru", dom: "rts-tender.ru", kinds: ["44", "223", "com", "615"], note: "Федеральная площадка, одна из самых крупных. Есть коммерческая секция." },
  { id: "rts-market", name: "РТС-маркет (малые закупки)", url: "https://market.rts-tender.ru", dom: "rts-tender.ru", kinds: ["small"], note: "Закупки малого объёма у единственного поставщика." },
  { id: "rosel", name: "Росэлторг (ЕЭТП)", url: "https://www.roseltorg.ru", dom: "roseltorg.ru", kinds: ["44", "223", "com", "615"], note: "Федеральная площадка. Секции Ростеха, Росатома, Россетей, РусГидро, ВТБ, Ростелекома." },
  { id: "gpb", name: "ЭТП ГПБ", url: "https://etpgpb.ru", dom: "etpgpb.ru", kinds: ["44", "223", "com"], note: "Федеральная площадка Газпромбанка, закупки группы Газпром." },
  { id: "tektorg", name: "ТЭК-Торг", url: "https://www.tektorg.ru", dom: "tektorg.ru", kinds: ["44", "223", "com"], note: "Федеральная площадка. Секции Роснефти, нефтегаза и энергетики." },
  { id: "ets", name: "НЭП (ЭТС)", url: "https://www.etp-ets.ru", dom: "etp-ets.ru", kinds: ["44", "223"], note: "Национальная электронная площадка, федеральная." },
  { id: "zakazrf", name: "ZakazRF (АГЗ РТ)", url: "https://zakazrf.ru", dom: "zakazrf.ru", kinds: ["44", "223", "com"], note: "Федеральная площадка Агентства по госзаказу Республики Татарстан." },
  { id: "rad", name: "РАД (Lot-Online)", url: "https://gz.lot-online.ru", dom: "lot-online.ru", kinds: ["44", "223"], note: "Российский аукционный дом, федеральная площадка." },
  { id: "astgoz", name: "АСТ ГОЗ", url: "http://www.astgoz.ru", dom: "astgoz.ru", kinds: ["44"], note: "Специализированная площадка гособоронзаказа (закрытые закупки)." },
  { id: "eis", name: "ЕИС (zakupki.gov.ru)", url: "https://zakupki.gov.ru", dom: "zakupki.gov.ru", kinds: ["44", "223", "615", "agg"], note: "Все извещения 44-ФЗ и 223-ФЗ, реестр контрактов. Бесплатно." },
  { id: "berezka", name: "ЕАТ «Берёзка»", url: "https://agregatoreat.ru", dom: "agregatoreat.ru", kinds: ["small"], note: "Малые закупки госзаказчиков." },
  { id: "pp-mos", name: "Портал поставщиков Москвы", url: "https://zakupki.mos.ru", dom: "zakupki.mos.ru", kinds: ["small"], note: "Малые закупки заказчиков Москвы." },
  { id: "rosneft", name: "Роснефть", url: "http://zakupki.rosneft.ru", dom: "zakupki.rosneft.ru", kinds: ["corp", "223"], note: "Закупки компаний Роснефти." },
  { id: "gazprom", name: "Газпром", url: "https://www.gazprom.ru/tenders/", dom: "gazprom.ru", kinds: ["corp", "223"], note: "Тендеры Газпрома, процедуры на ЭТП ГПБ." },
  { id: "gpn", name: "Газпром нефть", url: "https://zakupki.gazprom-neft.ru", dom: "gazprom-neft.ru", kinds: ["corp", "223"], note: "Закупки Газпром нефти." },
  { id: "rzd", name: "РЖД", url: "https://company.rzd.ru", dom: "rzd.ru", kinds: ["corp", "223"], note: "Закупки ОАО «РЖД»; часть процедур на etp.comita.ru." },
  { id: "rosatom", name: "Росатом", url: "https://zakupki.rosatom.ru", dom: "rosatom.ru", kinds: ["corp", "223"], note: "Закупки Росатома, процедуры в секции Росэлторга." },
  { id: "rostec", name: "Ростех", url: "https://rostec.roseltorg.ru", dom: "rostec.roseltorg.ru", kinds: ["corp", "223"], note: "Секция Ростеха на Росэлторге (например, «Звёздочка»: ОСК)." },
  { id: "rosseti", name: "Россети", url: "https://rosseti.roseltorg.ru", dom: "rosseti.roseltorg.ru", kinds: ["corp", "223"], note: "Секция Россетей на Росэлторге." },
  { id: "pik", name: "ПИК (застройщик)", url: "https://tender.pik.ru/tenders", dom: "tender.pik.ru", kinds: ["corp", "com"], note: "Тендеры застройщика: стройматериалы и работы." },
  { id: "lsr", name: "Группа ЛСР (застройщик)", url: "https://zakupki.lsr.ru", dom: "zakupki.lsr.ru", kinds: ["corp", "com"], note: "Закупки застройщика: стройматериалы и работы." },
  { id: "rostender", name: "Ростендер", url: "https://rostender.info", dom: "rostender.info", kinds: ["agg"], note: "Поиск тендеров по сотням площадок, есть бесплатный поиск." },
  { id: "tenderplan", name: "Тендерплан", url: "https://www.tenderplan.ru", dom: "tenderplan.ru", kinds: ["agg"], note: "Агрегатор с уведомлениями, есть пробный период." },
  { id: "kontur", name: "Контур.Закупки", url: "https://zakupki.kontur.ru", dom: "zakupki.kontur.ru", kinds: ["agg"], note: "Агрегатор и аналитика закупок, платный." },
  { id: "bico", name: "Бикотендер", url: "https://www.bicotender.ru", dom: "bicotender.ru", kinds: ["agg"], note: "Коммерческие тендеры и запросы цен компаний." },
];
const KIND_LABEL = { com: "коммерческие", 223: "223-ФЗ", 44: "44-ФЗ", 615: "капремонт 615-ПП", small: "малые закупки", corp: "корпоративные", agg: "агрегатор" };

const LAW_GUIDE = {
  commercial: {
    title: "Коммерческие закупки",
    short: "Правила пишет сам заказчик. Цену можно снизить на переторжке, оплата часто с отсрочкой. Проверьте тариф площадки.",
    body: [
      ["Кто покупает", "Частные компании: заводы, застройщики, торговые сети, агрохолдинги. Законы о госзакупках к ним не относятся: действуют Гражданский кодекс и правила, которые заказчик написал в документации."],
      ["Где искать", "B2B-Center, Фабрикант, OTC, Tender.Pro, Bidzaar, коммерческие секции Сбер А, РТС-тендер, Росэлторга и сайты самих компаний. Единого реестра нет, поэтому пользуйтесь агрегаторами и поиском по площадкам ниже."],
      ["Как выбирают", "Запрос цен, запрос предложений, тендер или аукцион. Часто бывает переторжка: второй раунд, где можно снизить цену. Заказчик вправе выбрать не самого дешёвого (сроки, отсрочка, надёжность) и отменить закупку без объяснений."],
      ["Что подать", "Коммерческое предложение на бланке или счёт, карточку компании, выписку ЕГРЮЛ, сертификаты и паспорта качества. Иногда нужны анкета поставщика, бухотчётность и проверка службой безопасности."],
      ["Деньги", "Обеспечения обычно нет. Но многие площадки берут плату за участие или за победу: проверьте тариф до подачи. Крупные компании платят с отсрочкой 30–90 дней, поэтому заложите стоимость денег в цену (поле «Оплата после поставки»)."],
      ["Советы", "Не ставьте сразу минимальную цену: оставьте запас на переторжку. Задавайте вопросы заказчику через площадку. Договор можно согласовать протоколом разногласий. Предлагайте аналоги, если документация их не запрещает. Посмотрите, кто выигрывал у этого заказчика раньше."],
    ],
  },
  223: {
    title: "223-ФЗ",
    short: "Правила в «Положении о закупке» заказчика (оно в ЕИС). Для закупок у малого бизнеса: обеспечение заявки обычно до 2%, оплата до 7 рабочих дней.",
    body: [
      ["Кто покупает", "Компании с госучастием и их дочерние общества, естественные монополии (энергетика, транспорт, связь) и некоторые учреждения."],
      ["Правила", "У каждого заказчика своё «Положение о закупке». Оно опубликовано в ЕИС: там способы закупок, сроки, обеспечение, переторжка, квалификационный отбор. Читайте его вместе с документацией."],
      ["Где проходят", "Извещение публикуется в ЕИС, а сама процедура идёт на площадке, указанной в извещении. Закупки «только для МСП» проходят на федеральных площадках. На каждой площадке нужна своя аккредитация."],
      ["Малый бизнес", "Для закупок у СМП обеспечение заявки обычно не больше 2% НМЦК, а оплата не позже 7 рабочих дней после приёмки. Проверьте, что ваша компания есть в реестре МСП на сайте ФНС."],
      ["Советы", "Сравните требования с Положением: лишние требования можно обжаловать в ФАС. Бывает переторжка, поэтому держите запас по цене. Срок подачи всегда смотрите по московскому времени."],
    ],
  },
  44: {
    title: "44-ФЗ (госзакупки)",
    short: "Жёсткие правила. Чаще аукцион: побеждает самая низкая цена. Нужна регистрация в ЕИС. При уклонении от контракта грозит РНП.",
    body: [
      ["Кто покупает", "Госорганы, бюджетные и казённые учреждения, муниципалитеты."],
      ["Что нужно", "Квалифицированная электронная подпись и регистрация в ЕИС. После неё вы автоматически аккредитуетесь на всех федеральных площадках. Сотрудники подписывают документы своей подписью с машиночитаемой доверенностью (МЧД)."],
      ["Как выбирают", "Чаще всего электронный аукцион: побеждает самая низкая цена. Ещё бывают конкурс и запрос котировок. Мелкие закупки идут через электронные магазины («Берёзка», Портал поставщиков)."],
      ["Деньги", "Обеспечение заявки 0,5–1% НМЦК (у крупных закупок больше), исполнения 0,5–30%: деньгами или независимой (банковской) гарантией. Победитель платит площадке 1% цены, но не больше 5 000 ₽ (для СМП не больше 2 000 ₽). Оплата не позже 7 рабочих дней после приёмки."],
      ["Риски", "При снижении на 25% и больше срабатывает антидемпинг: обеспечение исполнения ×1,5. Если не подписать контракт, компания попадёт в реестр недобросовестных поставщиков (РНП) на 2 года. Действует национальный режим: запреты и ограничения для иностранных товаров. Условия контракта менять почти нельзя."],
    ],
  },
  615: {
    title: "Капремонт домов (615-ПП)",
    short: "Сначала предварительный отбор в реестр подрядчиков, потом аукционы региональных операторов.",
    body: [
      ["Кто покупает", "Региональные фонды капремонта многоквартирных домов."],
      ["Как участвовать", "Сначала пройти предварительный отбор и попасть в реестр квалифицированных подрядных организаций. Потом участвовать в электронных аукционах на федеральных площадках (секции 615-ПП)."],
      ["Советы", "Нужны опыт аналогичных работ, членство в СРО и подтверждённые кадры. Подходит для строительных компаний, поставщикам материалов выгоднее работать с победителями этих аукционов."],
    ],
  },
};
const COMMON_TIPS = [
  "Подпись: квалифицированная электронная подпись руководителя из удостоверяющего центра ФНС. Для сотрудников нужна МЧД.",
  "Документы под рукой: выписка ЕГРЮЛ, реквизиты, устав, сертификаты и паспорта качества на товар.",
  "Сроки всегда указаны по московскому времени. Подавайте заявку хотя бы за несколько часов до окончания.",
  "Все вопросы по документации задавайте через площадку: ответы заказчика официальные.",
];

function platformOf(url) {
  let host = "";
  try { host = new URL(url).hostname.toLowerCase().replace(/^www\./, ""); } catch (e) { return null; }
  const list = [...PLATFORMS].sort((a, b) => b.dom.length - a.dom.length);
  return list.find((p) => host === p.dom || host.endsWith("." + p.dom)) || null;
}
function lawTip(law) {
  const g = LAW_GUIDE[law] || LAW_GUIDE.commercial;
  return `<b>${esc(g.title)}:</b> ${esc(g.short)} <a href="#/help/${law in LAW_GUIDE ? law : "commercial"}">Подробнее</a>`;
}

function viewHelp(tab) {
  const my = new Set(SETTINGS.myPlatforms || []);
  const law = tab in LAW_GUIDE ? tab : null;
  const isPl = tab === "platforms" || !tab;
  app.innerHTML = `
    <div class="card">
      <div class="card-h"><h1>Справка</h1></div>
      <div class="chips-l" role="tablist">
        <a class="tab${isPl ? " on" : ""}" href="#/help/platforms">Площадки</a>
        ${["commercial", "223", "44", "615"].map((k) => `<a class="tab${law === k ? " on" : ""}" href="#/help/${k}">${esc(LAW_GUIDE[k].title)}</a>`).join("")}
      </div>
    </div>
    <div id="help-body"></div>`;
  const body = $("#help-body");
  if (law) {
    const g = LAW_GUIDE[law];
    body.innerHTML = `<div class="card"><h2>${esc(g.title)}</h2><p class="muted" style="margin-top:4px">${esc(g.short)}</p>
      <dl class="guide">${g.body.map(([h, t]) => `<dt>${esc(h)}</dt><dd>${esc(t)}</dd>`).join("")}</dl></div>
      <div class="card"><h2>Для всех закупок</h2><ul class="guide-list">${COMMON_TIPS.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>
      <p class="hint" style="margin-top:10px">Это краткая памятка, а не юридическая консультация. Точные условия всегда в документации закупки.</p></div>`;
    return;
  }
  const f = HELP_FILTER;
  body.innerHTML = `
    <div class="card">
      <div class="card-h"><h2>Найти тендеры на всех площадках</h2></div>
      <form class="row" id="pl-search"><div class="inp grow"><input id="pl-q" placeholder="Что ищете: щебень, масло индустриальное, кровельные работы…" value="${esc(f.q)}" aria-label="Что ищете"></div>
        <div class="inp"><input id="pl-r" placeholder="Регион (необязательно)" value="${esc(f.region)}" aria-label="Регион"></div>
        <button class="btn primary" type="submit">Показать ссылки</button></form>
      <p class="hint" style="margin-top:6px">У каждой площадки появится кнопка «Искать»: поиск по этой площадке через Яндекс. По ЕИС поиск идёт прямо на сайте ЕИС.</p>
    </div>
    <div class="row" id="pl-kinds" style="margin:12px 0">
      ${[["all", "Все"], ["com", "Коммерческие"], ["corp", "Корпоративные"], ["223", "223-ФЗ"], ["44", "44-ФЗ"], ["small", "Малые закупки"], ["agg", "Агрегаторы"], ["my", "Мои"]].map(([k, l]) => `<button type="button" class="btn sm${f.kind === k ? " primary" : ""}" data-kind="${k}">${l}</button>`).join("")}
    </div>
    <div class="pl-grid" id="pl-list"></div>`;
  const render = () => {
    const q = f.q.trim(), reg = f.region.trim();
    const list = PLATFORMS.filter((p) => f.kind === "all" || (f.kind === "my" ? my.has(p.id) : p.kinds.includes(f.kind)));
    $("#pl-list").innerHTML = list.map((p) => {
      const searchUrl = !q ? "" : p.search ? p.search((q + " " + reg).trim()) : p.id === "eis"
        ? `https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString=${encodeURIComponent((q + " " + reg).trim())}&morphology=on&fz44=on&fz223=on&af=on`
        : `https://yandex.ru/search/?text=${encodeURIComponent(`${q} ${reg} site:${p.dom}`.replace(/\s+/g, " ").trim())}`;
      return `<div class="pl">
        <div class="pl-h"><a href="${esc(p.url)}" target="_blank" rel="noopener"><b>${esc(p.name)}</b></a>
          <label class="check small"><input type="checkbox" data-my="${p.id}"${my.has(p.id) ? " checked" : ""}> <span>Я здесь</span></label></div>
        <div class="pl-kinds">${p.kinds.map((k) => `<span class="pill neutral">${esc(KIND_LABEL[k])}</span>`).join(" ")}</div>
        <p class="small muted">${esc(p.note)}</p>
        <div class="row">${searchUrl ? `<a class="btn sm primary" href="${esc(searchUrl)}" target="_blank" rel="noopener">Искать «${esc(q)}»</a>` : ""}<a class="btn sm" href="${esc(p.url)}" target="_blank" rel="noopener">Открыть сайт</a></div>
      </div>`;
    }).join("") || `<p class="muted">Здесь пусто. Отметьте «Я здесь» у площадок, где вы зарегистрированы.</p>`;
  };
  render();
  $("#pl-search").onsubmit = (e) => { e.preventDefault(); f.q = $("#pl-q").value; f.region = $("#pl-r").value; render(); };
  $("#pl-kinds").onclick = (e) => { const b = e.target.closest("[data-kind]"); if (!b) return; f.kind = b.dataset.kind; viewHelp("platforms"); };
  $("#pl-list").onchange = async (e) => {
    const id = e.target.dataset.my;
    if (!id) return;
    e.target.checked ? my.add(id) : my.delete(id);
    try { const r = await api("PUT", "/api/profile", { settings: { myPlatforms: [...my] } }); SETTINGS = r.settings; }
    catch (ex) { toast(ex.message); }
  };
}
const HELP_FILTER = { q: "", region: "", kind: "com" };
