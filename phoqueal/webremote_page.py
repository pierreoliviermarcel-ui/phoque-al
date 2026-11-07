"""Page web de la telecommande sur telephone (une seule page, sans dependance externe)."""

ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">
<rect width="192" height="192" rx="40" fill="#14151c"/>
<circle cx="96" cy="96" r="52" fill="none" stroke="#5b8def" stroke-width="12"/>
<path d="M84 72 L124 96 L84 120 Z" fill="#ffffff"/>
</svg>"""

MANIFEST = """{
  "name": "Phoque Al",
  "short_name": "Phoque Al",
  "start_url": "/",
  "display": "standalone",
  "orientation": "portrait",
  "background_color": "#0f0f12",
  "theme_color": "#0f0f12",
  "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}]
}"""

PAGE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no">
<meta name="theme-color" content="#0f0f12">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="manifest" href="/manifest.json">
<link rel="icon" href="/icon.svg">
<title>Phoque Al</title>
<style>
:root{--bg:#0f0f12;--card:#17181f;--card2:#1e1f28;--line:#2a2b36;--txt:#e9e9f0;--dim:#8b8c99;--acc:#5b8def;
--acc2:#3b5bb5;--red:#e5484d;--heart:#ff4d5e;--r:14px}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.35 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
body{padding:0 12px calc(84px + env(safe-area-inset-bottom));overscroll-behavior:none;user-select:none}
header{position:sticky;top:0;z-index:5;background:linear-gradient(var(--bg) 80%,transparent);
  display:flex;align-items:center;gap:8px;padding:calc(10px + env(safe-area-inset-top)) 0 12px}
.brand{font-weight:800;letter-spacing:3px;font-size:17px;flex:1;display:flex;align-items:center;gap:8px}
.dot{width:8px;height:8px;border-radius:50%;background:#555}.dot.ok{background:#3ccf7a}.dot.ko{background:var(--red)}
button{font:inherit;color:var(--txt);background:var(--card2);border:1px solid var(--line);border-radius:11px;
  min-height:44px;min-width:44px;padding:0 10px;display:inline-flex;align-items:center;justify-content:center;gap:6px;
  touch-action:manipulation}
button:active{transform:scale(.96);background:#2a2c38}
button.on{background:var(--acc2);border-color:var(--acc);color:#fff}
button.danger{background:#5a1f24;border-color:#86303a}
button:disabled{opacity:.35}
button.big{min-height:52px;font-size:20px}
.ico{font-size:19px;line-height:1}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:12px;margin:0 0 12px}
.card.free{opacity:.55}
.head{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.num{flex:none;width:36px;height:36px;border-radius:10px;background:#2a2f45;color:#fff;font-weight:800;
  display:flex;align-items:center;justify-content:center;font-size:17px}
.titles{flex:1;min-width:0}
.title{font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sub{color:var(--dim);font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.heart{color:var(--heart);font-size:22px;background:transparent;border-color:transparent;min-width:40px}
.heart.on{background:#3a1820;border-color:#7a2834;color:var(--heart)}
.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:7px}
.grid.g4{grid-template-columns:repeat(4,1fr)}
.row{display:flex;align-items:center;gap:10px;margin-top:10px}
.row .lab{color:var(--dim);font-size:12.5px;min-width:44px;font-variant-numeric:tabular-nums}
.row .lab.r{text-align:right}
input[type=range]{flex:1;-webkit-appearance:none;appearance:none;height:30px;background:transparent}
input[type=range]::-webkit-slider-runnable-track{height:5px;border-radius:3px;background:var(--line)}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:24px;height:24px;margin-top:-9.5px;border-radius:50%;
  background:#fff;border:3px solid var(--acc)}
input[type=range]::-moz-range-track{height:5px;border-radius:3px;background:var(--line)}
input[type=range]::-moz-range-thumb{width:20px;height:20px;border-radius:50%;background:#fff;border:3px solid var(--acc)}
.sec{color:var(--dim);font-size:12px;letter-spacing:2px;text-transform:uppercase;margin:16px 4px 8px}
nav{position:fixed;left:0;right:0;bottom:0;z-index:6;display:flex;gap:8px;padding:8px 12px calc(8px + env(safe-area-inset-bottom));
  background:rgba(15,15,18,.96);border-top:1px solid var(--line);backdrop-filter:blur(8px)}
nav button{flex:1;min-height:48px}
.hidden{display:none!important}
/* bibliotheque */
.chips{display:flex;gap:7px;overflow-x:auto;padding-bottom:4px}
.chips button{flex:none;min-height:38px;border-radius:19px;padding:0 14px}
.search{width:100%;font:inherit;color:var(--txt);background:var(--card2);border:1px solid var(--line);border-radius:12px;
  padding:12px 14px;margin:10px 0 4px;outline:none}
.search:focus{border-color:var(--acc)}
.item{display:flex;align-items:center;gap:10px;padding:8px;border-radius:12px;background:var(--card);
  border:1px solid var(--line);margin-bottom:8px}
.item:active{background:#22232d}
.thumb{flex:none;width:96px;height:54px;border-radius:8px;background:#23242e center/cover no-repeat}
.item .t{flex:1;min-width:0}
.list .item .t .title{white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.acts{display:flex;gap:6px}
.acts button{min-height:40px;min-width:40px;padding:0 8px}
/* connexion */
#login{max-width:360px;margin:12vh auto 0;text-align:center}
#login h1{letter-spacing:6px;font-size:28px;margin:0 0 6px}
#pin{width:100%;text-align:center;font-size:30px;letter-spacing:12px;padding:14px;margin:18px 0 12px;border-radius:14px;
  background:var(--card2);border:1px solid var(--line);color:var(--txt);outline:none}
#pin:focus{border-color:var(--acc)}
#loginErr{color:var(--red);min-height:22px}
/* ===== telephone a l'horizontale : disposition « telecommande » compacte ===== */
/* ===== disposition compacte « telecommande » ===== */
body.compact body{padding:0 8px 6px;font-size:13px}
body.compact header{padding:calc(4px + env(safe-area-inset-top)) 0 4px;gap:5px}
body.compact header button{min-height:30px;min-width:34px;padding:0 6px}
body.compact .brand{font-size:13px;letter-spacing:2px}
body.compact nav,body.compact #tabLib{display:none!important}
body.compact #tabScreens{display:block!important}
body.compact .card{display:grid;grid-template-columns:28px minmax(80px,1fr) auto auto minmax(80px,150px);
    grid-template-areas:"num title heart btns vol" "num seek seek seek seek";
    align-items:center;column-gap:8px;row-gap:0;padding:4px 8px;margin:0 0 5px;border-radius:10px}
body.compact #allCard,body.compact #musicCard{grid-template-areas:"num title heart btns vol"}
body.compact .head{display:contents}
body.compact .num{grid-area:num;width:28px;height:28px;font-size:14px;border-radius:7px}
body.compact .titles{grid-area:title;min-width:0}
body.compact .title{font-size:13px}
body.compact .sub{display:none}
body.compact .heart,body.compact .hx{grid-area:heart}
body.compact .hx{display:flex;gap:3px}
body.compact .hx button{min-height:28px;min-width:28px;padding:0}
body.compact .heart{min-height:28px;min-width:30px;font-size:17px}
body.compact .grid,body.compact .grid.g4{grid-area:btns;display:flex;gap:3px;justify-self:end}
body.compact .grid button{min-height:30px;min-width:32px;padding:0 4px;border-radius:7px;font-size:12px}
body.compact .grid .ico{font-size:15px}
body.compact .row{margin:0}
body.compact .rvol{grid-area:vol}
body.compact .rvol .lab{display:none}
body.compact .rseek{grid-area:seek;margin-top:-2px}
body.compact .row .lab{min-width:40px;font-size:11px}
body.compact input[type=range]{height:22px}
body.compact input[type=range]::-webkit-slider-thumb{width:18px;height:18px;margin-top:-6.5px;border-width:2px}
body.compact .toast{bottom:10px}

/* ===== tablette a l'horizontale : ecrans a gauche, bibliotheque a droite ===== */
body.split{padding-bottom:10px}
body.split #app{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(300px,1fr);column-gap:14px;align-items:start}
body.split header{grid-column:1/-1}
body.split #tabScreens{display:block!important}
body.split #tabLib{display:block!important;position:sticky;top:52px;max-height:calc(100vh - 62px);overflow-y:auto;
  background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:0 10px 10px}
body.split nav{display:none!important}
body.split .thumb{width:80px;height:45px}
body.split .card{padding:7px 10px;margin-bottom:7px;grid-template-columns:34px auto 1fr minmax(110px,190px);
  grid-template-areas:"num title title heart" "num btns btns vol" "num seek seek seek";row-gap:4px}
body.split #allCard,body.split #musicCard{grid-template-areas:"num title title heart" "num btns btns vol"}
body.split .grid,body.split .grid.g4{justify-self:start}
body.split .heart,body.split .hx{justify-self:end}
body.split .grid button{min-height:40px;min-width:40px;font-size:13px}
body.split .grid .ico{font-size:17px}
body.split .num{width:34px;height:34px;font-size:16px}
body.split .title{font-size:14.5px}
body.split .heart,body.split .hx button{min-height:38px;min-width:38px}
body.split header button{min-height:38px;min-width:42px}
body.split input[type=range]{height:28px}
.toast{position:fixed;left:50%;bottom:calc(76px + env(safe-area-inset-bottom));transform:translateX(-50%);background:#2a2c38;
  border:1px solid var(--line);padding:9px 16px;border-radius:20px;font-size:14px;opacity:0;transition:opacity .2s;pointer-events:none;z-index:9}
.toast.show{opacity:1}

/* ===== mode « une main » (telephone en portrait) ===== */
#one{display:none}
body.one.logged #one{display:flex}
body.one #app{display:none!important}
body.one{padding:0;overflow:hidden}
#one{position:fixed;inset:0;flex-direction:column;background:var(--bg);
  padding:env(safe-area-inset-top) 0 env(safe-area-inset-bottom)}
.o-top{display:flex;align-items:center;gap:8px;padding:12px 16px 6px}
.o-brand{font-weight:800;letter-spacing:3px;font-size:15px;flex:1}
.o-top button{min-height:38px;min-width:46px}
.o-page{flex:1 1 0;display:flex;flex-direction:column;min-height:0;padding:0 16px}
.o-page.hidden{display:none}
.o-info{padding:6px 0;touch-action:pan-y}
.o-kicker{color:var(--dim);font-size:12px;letter-spacing:2px;font-weight:600;min-height:16px}
.o-titlerow{display:flex;gap:10px;align-items:flex-start;margin-top:4px}
.o-title{font-size:20px;font-weight:700;line-height:1.25;flex:1;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.o-heart{flex:none;width:50px;height:50px;border-radius:14px;color:var(--heart)}
.o-heart.on{background:#3a1820;border-color:#7a2834}
.o-seekrow{margin-top:14px}
.o-seekrow input{width:100%;height:44px}
.o-times{display:flex;justify-content:space-between;color:var(--dim);font-size:13px}
.o-hint{color:#5d5e6b;font-size:11.5px;margin-top:6px;text-align:center}
.o-spacer{flex:1}
.o-pad{display:flex;align-items:center;justify-content:space-between;gap:6px;margin-bottom:16px}
.o-b{width:60px;height:60px;border-radius:16px;font-weight:700;font-size:16px}
.o-b.on{background:var(--acc2);border-color:var(--acc);color:#fff}
.o-play{width:98px;height:98px;border-radius:50%;border:none;background:var(--acc);color:#0f0f12}
.o-play svg{width:38px;height:38px}
.o-volrow{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.o-volrow input{flex:1;height:48px}
.o-sq{width:54px;height:54px;flex:none}
.o-lab{color:var(--dim);font-size:13px}
.o-sel{display:grid;grid-template-columns:repeat(var(--n,4),minmax(0,1fr));gap:8px;margin-bottom:12px;user-select:none;-webkit-user-select:none}
.o-sel.map{display:flex;gap:8px;align-items:stretch}
.o-map{position:relative;flex:1;min-width:0}
.o-map button{position:absolute;padding:0;border-radius:10px;gap:0}
.o-sel.map>.o-all{flex:none;width:64px;height:auto}
.o-rem{display:flex;gap:6px;margin-top:6px}
.o-rem button{flex:1;height:44px}
.o-sel button.grp{border:2px dashed var(--acc);background:#22305a;color:#fff}
.o-sel button.tiny small{display:none}
.o-sel button{height:60px;flex-direction:column;gap:2px;border-radius:14px;-webkit-touch-callout:none}
.o-sel button b{font-size:20px}
.o-sel button small{font-size:10.5px;color:var(--dim)}
.o-sel button.on{border:2px solid var(--acc);background:#2a3a66;color:#fff}
.o-sel button.on small{color:#cfd9f5}
.o-sel button.free b{color:var(--dim)}
.o-nav{position:static;flex:none;z-index:auto;display:flex;gap:8px;padding:10px 16px 14px;border-top:1px solid var(--line);background:#121318}
.o-nav button{flex:1;min-height:52px;font-weight:600;font-size:14px}
.o-nav button.on{background:var(--acc2);border-color:var(--acc);color:#fff}
.o-nav .o-panic{flex:none;width:52px;background:#5a1f24;border-color:#86303a;font-size:20px;font-weight:800}
.o-chips{display:flex;gap:6px;overflow-x:auto;padding:4px 0 8px;flex:none}
.o-chips button{flex:none;min-height:40px;border-radius:20px;padding:0 14px;font-size:14px}
.o-chips button.on{background:var(--acc2);border-color:var(--acc);color:#fff}
.o-libbody{flex:1;overflow-y:auto;min-height:0;padding-bottom:8px;-webkit-overflow-scrolling:touch}
.o-cards{align-items:start;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.o-card{display:flex;flex-direction:column;justify-content:flex-start;gap:5px;text-align:left;background:transparent;border:none;padding:0;min-height:0;align-items:stretch}
.o-card .th{aspect-ratio:16/9;border-radius:12px;background:#23242e center/cover no-repeat;border:1px solid var(--line)}
.o-card .t{font-size:13px;font-weight:600;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.o-card .s{font-size:12px;color:var(--dim)}
.o-row{display:flex;align-items:center;gap:8px;padding:10px;border-radius:12px;background:var(--card);border:1px solid var(--line);margin-bottom:8px}
.o-row .t{flex:1;min-width:0;font-weight:600}
.o-row .s{font-size:12px;color:var(--dim);font-weight:400}
.o-row button{min-width:48px;min-height:46px}
.o-search{display:flex;align-items:center;gap:10px;height:52px;padding:0 14px;border-radius:14px;background:var(--card2);
  border:1px solid var(--acc);margin:6px 0 10px;flex:none}
.o-search input{flex:1;background:transparent;border:none;outline:none;color:var(--txt);font:inherit;font-size:16px}
.o-sheet{position:fixed;inset:0;background:rgba(0,0,0,.55);display:flex;align-items:flex-end;z-index:20}
.o-sheet.hidden{display:none}
.o-sheetbox{width:100%;background:var(--card);border-top:1px solid var(--line);border-radius:22px 22px 0 0;
  padding:10px 16px calc(20px + env(safe-area-inset-bottom))}
.o-grab{width:44px;height:5px;border-radius:3px;background:#3a3b48;margin:0 auto 10px}
.o-sheethead{display:flex;align-items:center;margin-bottom:12px}
.o-sheethead b{flex:1;font-size:16px}
.o-grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.o-grid3 button{height:76px;flex-direction:column;gap:5px;font-size:20px;border-radius:14px}
.o-grid3 button span{font-size:12.5px}
.o-grid3 button.on{background:var(--acc2);border-color:var(--acc);color:#fff}
.o-grid3 .o-stop{background:#2a1517;border-color:#86303a;color:#ffb3b8}
.o-stars{display:flex;align-items:center;gap:6px;margin-top:12px}
.o-ry{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-top:12px}
.o-ry span{flex:1 0 100%;color:#ff2bd6;font-size:12.5px;letter-spacing:1.5px;text-transform:uppercase;text-shadow:0 0 10px rgba(255,43,214,.6)}
.o-ry button{flex:1 1 0;min-width:0;min-height:44px;font-size:14px;padding:0 4px;border-color:#5a2a6e}
.o-stars span{flex:1;color:var(--dim)}
.o-stars button{min-width:44px;color:#ffcc33;font-size:20px}

/* une main, telephone a l'horizontale (5 ecrans et plus) */
body.one.land .o-top{padding:4px 16px 0}
body.one.land #oScreens{overflow:hidden;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);grid-template-rows:auto minmax(0,1fr);
  grid-template-areas:"info pad" "sel vol";column-gap:20px}
body.one.land #oScreens .o-info{grid-area:info;padding:0}
body.one.land #oScreens .o-spacer,body.one.land .o-hint{display:none}
body.one.land #oScreens .o-pad{grid-area:pad;align-self:center;margin:0}
body.one.land #oScreens .o-volrow{grid-area:vol;align-self:start;margin:0}
body.one.land #oScreens .o-sel{grid-area:sel;align-self:end;margin-bottom:6px}
body.one.land .o-title{font-size:16px;-webkit-line-clamp:2}
body.one.land .o-heart{width:42px;height:42px}
body.one.land .o-seekrow{margin-top:4px}
body.one.land .o-seekrow input{height:34px}
body.one.land .o-b{width:54px;height:54px}
body.one.land .o-play{width:80px;height:80px}
body.one nav.o-nav{display:flex!important}
body.one.land .o-nav{padding:6px 16px 8px}
body.one.land .o-nav button{min-height:40px}
body.one.land .o-cards{grid-template-columns:repeat(4,minmax(0,1fr))}
/* tablette : grille de cartes quand il y a beaucoup d'ecrans */
body.many #screens{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:8px}
body.many #screens .card{margin:0}
body.split.many #screens .card{grid-template-columns:30px minmax(0,1fr) minmax(70px,.45fr) auto;
  grid-template-areas:"num title title heart" "btns btns btns btns" "seek seek vol vol";row-gap:2px;padding:6px 8px}
body.split.many #screens .num{width:30px;height:30px;font-size:15px}
body.split.many #screens .title{font-size:13.5px}
body.split.many #screens .heart{min-height:32px;min-width:32px}
body.split.many #screens .grid{display:flex;flex-wrap:nowrap;gap:3px;justify-self:stretch;justify-content:space-between}
body.split.many #screens .grid button{min-width:32px;min-height:34px;padding:0 3px;flex:1}
body.split.many #screens .grid button[data-cmd=seek]{display:none}
body.split.many #screens .rvol .lab{display:none}
body.split.many #screens input[type=range]{height:24px}
/* ===== v2.5 : ecrans courts ===== */
#oScreens{overflow-y:auto;overflow-x:hidden;-webkit-overflow-scrolling:touch;overscroll-behavior:contain}
#oScreens>*{flex-shrink:0}
#oScreens>.o-spacer{flex:1 1 0;min-height:0}
.o-play{flex:none;width:clamp(66px,12dvh,98px);height:clamp(66px,12dvh,98px)}
.o-b{flex:none;width:clamp(46px,7.4dvh,60px);height:clamp(46px,7.4dvh,60px)}
.o-sq{width:clamp(44px,6.6dvh,54px);height:clamp(44px,6.6dvh,54px)}
.o-pad{margin-bottom:clamp(8px,1.8dvh,16px)}
.o-volrow{margin-bottom:clamp(6px,1.5dvh,14px)}
.o-volrow input{height:clamp(38px,5.6dvh,48px)}
.o-sel button{height:clamp(44px,7.2dvh,60px);min-width:0}
.o-map button{min-height:0!important;min-width:0!important}
.o-sel button b{font-size:clamp(16px,2.6dvh,20px)}
@media (max-height:720px){
  .o-hint{display:none}
  .o-top{padding:8px 16px 4px}
  .o-seekrow{margin-top:6px}
  .o-seekrow input{height:36px}
  .o-title{font-size:18px;-webkit-line-clamp:2}
  .o-heart{width:44px;height:44px}
  .o-nav{padding:6px 16px 8px}
  .o-nav button{min-height:44px}
  .o-sel button small{display:none}
}
@media (max-width:380px){
  .o-page{padding:0 10px}
  .o-pad{gap:4px}
  .o-b{font-size:14px}
  .o-sel{gap:6px}
  .o-nav{gap:6px;padding-left:10px;padding-right:10px}
  .o-nav button{font-size:13px;padding:0 4px}
}
</style>
</head>
<body>

<div id="login" class="hidden">
  <h1>PHOQUE AL</h1>
  <div style="color:var(--dim)">Entre le code affiché sur le PC<br>(bouton « Téléphone » de Phoque Al)</div>
  <input id="pin" inputmode="numeric" pattern="[0-9]*" maxlength="6" autocomplete="one-time-code" placeholder="••••••">
  <button id="loginBtn" class="big" style="width:100%">Connexion</button>
  <div id="loginErr"></div>
</div>

<div id="app" class="hidden">
  <header>
    <div class="brand"><span class="dot" id="dot"></span>PHOQUE AL</div>
    <button id="bDiscreet" title="Mode discret"><span class="ico">◐</span></button>
    <button id="bIdent" title="Afficher le numéro de chaque écran"><b>#</b></button>
    <button id="bHome" title="Afficher l'accueil sur le PC"><span class="ico">⌂</span></button>
    <button id="bPanic" class="danger" title="Panique : tout en pause et masqué"><b>!</b></button>
  </header>

  <section id="tabScreens">
    <div class="card" id="allCard">
      <div class="head">
        <div class="num">∗</div>
        <div class="titles"><div class="title">Tous les écrans</div><div class="sub" id="allSub"></div></div>
        <span class="hx"><button id="rotBack" title="Décaler les vidéos (sens inverse)"><span class="ico">⟲</span></button>
        <button id="rot" title="Décaler les vidéos d'un écran"><span class="ico">⟳</span></button></span>
      </div>
      <div class="grid">
        <button data-cmd="prev"><span class="ico">⏮</span></button>
        <button data-cmd="pause" class="pp"><span class="ico">⏯</span></button>
        <button data-cmd="next"><span class="ico">⏭</span></button>
        <button data-cmd="seek" data-v="-10">−10</button>
        <button data-cmd="seek" data-v="10">+10</button>
        <button data-cmd="loop" class="fl-loop"><span class="ico">↻</span></button>
        <button data-cmd="random" class="fl-random"><span class="ico">⇄</span></button>
        <button data-cmd="mute" class="fl-mute"><span class="ico">🔊</span></button>
        <button data-cmd="view"><span class="ico">▭</span></button>
        <button data-cmd="stop"><span class="ico">⏹</span></button>
      </div>
      <div class="row rvol"><span class="lab">Vol.</span><input type="range" min="0" max="150" class="vol" data-screen="0"></div>
    </div>
    <div id="screens"></div>

    <div class="card" id="musicCard">
      <div class="head">
        <div class="num">♪</div>
        <div class="titles"><div class="title" id="track">Musique</div><div class="sub" id="trackSub"></div></div>
      </div>
      <div class="grid">
        <button data-m="music_prev"><span class="ico">⏮</span></button>
        <button data-m="music_toggle" id="mPlay"><span class="ico">▶</span></button>
        <button data-m="music_next"><span class="ico">⏭</span></button>
        <button data-m="music_shuffle" id="mShuffle"><span class="ico">⇄</span></button>
        <button data-m="montage" id="mMontage" title="Montage au rythme"><span class="ico">♩</span></button>
      </div>
      <div class="row rvol"><span class="lab">Vol.</span><input type="range" min="0" max="100" id="mVol"></div>
    </div>
  </section>

  <section id="tabLib" class="hidden">
    <div class="sec">Lancer sur</div>
    <div class="chips" id="target"></div>
    <input class="search" id="q" type="search" placeholder="Rechercher (titre, studio, thème, personne)…" autocomplete="off">
    <div class="list" id="results"></div>
    <div class="sec">Playlists</div>
    <div class="list" id="playlists"></div>
    <div class="sec">Thèmes</div>
    <div class="list" id="themes"></div>
  </section>

  <nav>
    <button id="navScreens" class="on">Écrans</button>
    <button id="navLib">Bibliothèque</button>
  </nav>
</div>

<div id="one">
  <div class="o-top">
    <span class="dot" id="oDot"></span><span class="o-brand">PHOQUE AL</span>
    <button id="oIdent" aria-label="Numéros des écrans"><b>#</b></button>
    <button id="oDiscreet" aria-label="Mode discret"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/><path d="M4 4l16 16"/></svg></button>
  </div>

  <section id="oScreens" class="o-page">
    <div class="o-info" id="oSwipe">
      <div class="o-kicker" id="oKicker"></div>
      <div class="o-titlerow"><div class="o-title" id="oTitle"></div>
        <button class="o-heart" id="oHeart" aria-label="J'aime"><svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 21s-7.5-4.6-9.6-9.2C1 8.6 3 5 6.6 5c2.1 0 3.6 1.2 4.4 2.5C11.8 6.2 13.3 5 15.4 5 19 5 21 8.6 19.6 11.8 17.5 16.4 12 21 12 21z"/></svg></button></div>
      <div class="o-seekrow" id="oSeekRow">
        <input type="range" min="0" max="1000" id="oSeek" aria-label="Position dans la vidéo">
        <div class="o-times"><span id="oCur">0:00</span><span id="oLen">0:00</span></div>
      </div>
      <div class="o-hint">Glisse ← → ici pour changer d'écran · appui long sur un écran pour en grouper plusieurs</div>
    </div>
    <div class="o-spacer"></div>
    <div class="o-pad">
      <button class="o-b" data-o="seek" data-v="-10">−10</button>
      <button class="o-b" data-o="prev" aria-label="Précédente"><svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="5" y="5" width="2.5" height="14" rx="1"/><path d="M19 5.5v13L9 12z"/></svg></button>
      <button class="o-play" data-o="pause" id="oPlay" aria-label="Pause / lecture"></button>
      <button class="o-b" data-o="next" aria-label="Suivante"><svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="16.5" y="5" width="2.5" height="14" rx="1"/><path d="M5 5.5v13L15 12z"/></svg></button>
      <button class="o-b" data-o="seek" data-v="10">+10</button>
    </div>
    <div class="o-volrow">
      <button class="o-b o-sq" data-o="mute" id="oMute" aria-label="Couper le son"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 9h4l5-4v14l-5-4H4z"/><path d="M16.5 8.5a5 5 0 0 1 0 7"/></svg></button>
      <input type="range" min="0" max="150" id="oVol" aria-label="Volume">
      <button class="o-b o-sq" id="oMore" aria-label="Plus d'options"><b>⋯</b></button>
    </div>
    <div class="o-sel" id="oSel"></div>
  </section>

  <section id="oLib" class="o-page hidden">
    <div class="o-chips" id="oTarget"></div>
    <div class="o-chips" id="oLibTabs">
      <button data-lt="res" class="on">Résultats</button><button data-lt="pl">Playlists</button>
      <button data-lt="th">Thèmes</button><button data-lt="so">Soirées</button>
    </div>
    <div class="o-libbody" id="oLibBody"></div>
    <label class="o-search"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#9a9ba8" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg><input id="oQ" type="search" placeholder="Rechercher : titre, thème, personne…" autocomplete="off"></label>
  </section>

  <section id="oMusic" class="o-page hidden">
    <div class="o-info"><div class="o-kicker">MUSIQUE</div><div class="o-title" id="oTrack">—</div></div>
    <div class="o-spacer"></div>
    <div class="o-pad">
      <button class="o-b" data-m2="music_shuffle" id="oShuf">⇄</button>
      <button class="o-b" data-m2="music_prev"><svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="5" y="5" width="2.5" height="14" rx="1"/><path d="M19 5.5v13L9 12z"/></svg></button>
      <button class="o-play" data-m2="music_toggle" id="oMPlay" aria-label="Lecture / pause"></button>
      <button class="o-b" data-m2="music_next"><svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="16.5" y="5" width="2.5" height="14" rx="1"/><path d="M5 5.5v13L15 12z"/></svg></button>
      <button class="o-b" data-m2="montage" id="oMont" aria-label="Montage au rythme" style="font-size:24px">♫</button>
    </div>
    <div class="o-volrow"><span class="o-lab">Vol.</span><input type="range" min="0" max="100" id="oMVol" aria-label="Volume musique"></div>
    <div class="o-ry o-se"><span>Séance</span>
      <button data-se="start">Démarrer</button><button data-se="pause">Pause</button>
      <button data-se="next">Phase +</button><button data-se="stop">Arrêter</button></div>
  </section>

  <nav class="o-nav">
    <button data-tab="oScreens" class="on">Écrans</button><button data-tab="oLib">Bibliothèque</button>
    <button data-tab="oMusic">Musique</button>
    <button class="o-panic" id="oPanic" aria-label="Panique : tout masquer">!</button>
  </nav>

  <div class="o-sheet hidden" id="oSheet">
    <div class="o-sheetbox">
      <div class="o-grab"></div>
      <div class="o-sheethead"><b id="oSheetTitle">Options</b><button id="oSheetClose">Fermer</button></div>
      <div class="o-grid3">
        <button data-o="loop" id="oLoop">↻<span>Boucle</span></button>
        <button data-o="random" id="oRand">⇄<span>Aléatoire</span></button>
        <button data-o="view" id="oView">▭<span id="oViewLab">Format</span></button>
        <button data-o="blur">▦<span>Fond flou</span></button>
        <button data-o="moment">⚑<span>Moment favori</span></button>
        <button data-o="scene" data-v="1">⏭<span>Scène suivante</span></button>
        <button data-o2="rotate">⟳<span>Décaler écrans</span></button>
        <button data-o2="rotback">⟲<span>Décaler (inverse)</span></button>
        <button data-o="stop" class="o-stop">■<span>Arrêter</span></button>
      </div>
      <div class="o-ry">
        <span>Frise néon · décalage en ms</span>
        <button data-ry="toggle">Frise</button><button data-ry="style">Style</button>
        <button data-ry="shift" data-v="-20">−20</button><button data-ry="shift" data-v="20">+20</button>
        <button data-ry="fx">Effets</button>
      </div>
      <div class="o-stars" id="oStars">
        <span>Noter</span><button data-r="1">★</button><button data-r="2">★</button><button data-r="3">★</button>
        <button data-r="4">★</button><button data-r="5">★</button><button data-r="0">✕</button>
      </div>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt = s => { s = Math.max(0, Math.floor(s||0)); const h = Math.floor(s/3600), m = Math.floor(s%3600/60), x = s%60;
  return h ? `${h}:${String(m).padStart(2,"0")}:${String(x).padStart(2,"0")}` : `${m}:${String(x).padStart(2,"0")}`; };
let token = ""; try { token = localStorage.getItem("focal_t") || ""; } catch(e) {}
let state = null, dragging = new Set(), target = 0, nScreens = 0, polling = null, lastOk = 0;

function toast(t){ const el=$("#toast"); el.textContent=t; el.classList.add("show"); clearTimeout(toast.h); toast.h=setTimeout(()=>el.classList.remove("show"),1600); }
function vibrate(){ try{ navigator.vibrate && navigator.vibrate(12); }catch(e){} }

async function api(path, body){
  const opt = {headers:{}, credentials:"same-origin"};
  if (token) opt.headers["X-PhoqueAl-Token"] = token;
  if (body !== undefined){ opt.method="POST"; opt.headers["Content-Type"]="application/json"; opt.body=JSON.stringify(body); }
  const r = await fetch(path, opt);
  if (r.status === 401){ showLogin(); throw new Error("auth"); }
  if (!r.ok) throw new Error((await r.json().catch(()=>({}))).error || r.status);
  return r.json();
}
function act(cmd, screen=0, value, extra){ vibrate(); api("/api/action", Object.assign({cmd, screen, value}, extra||{})).then(()=>setTimeout(poll,120)).catch(()=>{}); }

// ---------- connexion
function showLogin(){ $("#app").classList.add("hidden"); $("#login").classList.remove("hidden"); stopPoll(); setTimeout(()=>$("#pin").focus(),100); }
function showApp(){ $("#login").classList.add("hidden"); $("#app").classList.remove("hidden"); startPoll(); layout(); }
$("#loginBtn").onclick = async () => {
  $("#loginErr").textContent = "";
  try {
    const r = await fetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({pin:$("#pin").value})});
    const j = await r.json();
    if (!r.ok) { $("#loginErr").textContent = j.error || "Erreur"; return; }
    token = j.token; try { localStorage.setItem("focal_t", token); } catch(e) {}
    showApp();
  } catch(e){ $("#loginErr").textContent = "PC injoignable"; }
};
$("#pin").addEventListener("keyup", e => { if (e.key==="Enter" || $("#pin").value.length===6) $("#loginBtn").click(); });

// ---------- etat
function startPoll(){ stopPoll(); poll(); polling = setInterval(poll, 800); }
function stopPoll(){ if (polling) clearInterval(polling); polling = null; }
document.addEventListener("visibilitychange", () => { if (document.hidden) stopPoll(); else if (!$("#app").classList.contains("hidden")) startPoll(); });
setInterval(()=>{ $("#dot").className = "dot " + (Date.now()-lastOk < 3000 ? "ok" : "ko"); }, 1000);

async function poll(){
  try { state = await api("/api/state"); lastOk = Date.now(); render(); } catch(e){}
}

function screenCard(s){
  return `<div class="card" id="sc${s.n}">
    <div class="head">
      <div class="num" data-focus="${s.n}">${s.n}</div>
      <div class="titles"><div class="title"></div><div class="sub"></div></div>
      <button class="heart" data-cmd="like" data-screen="${s.n}">♡</button>
    </div>
    <div class="grid">
      <button data-cmd="prev" data-screen="${s.n}"><span class="ico">⏮</span></button>
      <button data-cmd="pause" data-screen="${s.n}" class="pp"><span class="ico">⏯</span></button>
      <button data-cmd="next" data-screen="${s.n}"><span class="ico">⏭</span></button>
      <button data-cmd="seek" data-v="-10" data-screen="${s.n}">−10</button>
      <button data-cmd="seek" data-v="10" data-screen="${s.n}">+10</button>
      <button data-cmd="loop" data-screen="${s.n}" class="fl-loop"><span class="ico">↻</span></button>
      <button data-cmd="random" data-screen="${s.n}" class="fl-random"><span class="ico">⇄</span></button>
      <button data-cmd="mute" data-screen="${s.n}" class="fl-mute"><span class="ico">🔊</span></button>
      <button data-cmd="view" data-screen="${s.n}" class="fl-view"><span class="ico">▭</span></button>
      <button data-cmd="stop" data-screen="${s.n}"><span class="ico">⏹</span></button>
    </div>
    <div class="row rseek"><span class="lab cur">0:00</span><input type="range" min="0" max="1000" class="seek" data-screen="${s.n}"><span class="lab r len">0:00</span></div>
    <div class="row rvol"><span class="lab">Vol.</span><input type="range" min="0" max="150" class="vol" data-screen="${s.n}"></div>
  </div>`;
}

function setRange(el, v){ if (el && !dragging.has(el)) el.value = v; }

function render(){
  if (!state) return;
  const scr = state.screens || [];
  if (scr.length !== nScreens){
    nScreens = scr.length;
    $("#screens").innerHTML = scr.map(screenCard).join("");
    $("#target").innerHTML = `<button data-t="0">Auto</button>` + scr.map(s=>`<button data-t="${s.n}">Écran ${s.n}</button>`).join("")
      + (scr.length>1 ? `` : ``);
    bindRanges(); markTarget();
  }
  const busy = scr.filter(s=>s.busy);
  $("#allSub").textContent = busy.length ? `${busy.length} écran(s) en lecture` : "aucune lecture";
  const all = $("#allCard");
  all.querySelector(".pp .ico").textContent = busy.some(s=>s.playing) ? "⏸" : "▶";
  all.querySelector(".fl-loop").classList.toggle("on", busy.length>0 && busy.every(s=>s.loop));
  all.querySelector(".fl-random").classList.toggle("on", busy.length>0 && busy.every(s=>s.random));
  all.querySelector(".fl-mute .ico").textContent = busy.length && busy.every(s=>s.muted) ? "🔇" : "🔊";
  all.querySelectorAll("button").forEach(b=>b.disabled = !busy.length);
  if (busy.length) setRange(all.querySelector(".vol"), busy[0].volume);

  for (const s of scr){
    const c = $("#sc"+s.n); if (!c) continue;
    c.classList.toggle("free", !s.busy);
    c.querySelectorAll("button,input").forEach(b=>b.disabled = !s.busy);
    c.querySelector(".title").textContent = s.busy ? s.title : "Libre";
    c.querySelector(".sub").textContent = s.busy ? (s.sub || ("Format : " + s.view)) : "";
    if (!s.busy){ c.querySelector(".cur").textContent = "—"; c.querySelector(".len").textContent = "—"; continue; }
    const h = c.querySelector(".heart"); h.classList.toggle("on", !!s.liked); h.textContent = s.liked ? "♥" : "♡";
    c.querySelector(".pp .ico").textContent = s.playing ? "⏸" : "▶";
    c.querySelector(".fl-loop").classList.toggle("on", s.loop);
    c.querySelector(".fl-random").classList.toggle("on", s.random);
    c.querySelector(".fl-mute .ico").textContent = s.muted ? "🔇" : "🔊";
    const sk = c.querySelector(".seek");
    sk.disabled = !(s.length > 0);
    if (!dragging.has(sk)){ c.querySelector(".cur").textContent = fmt(s.time); sk.value = s.length ? Math.round(1000*s.time/s.length) : 0; }
    c.querySelector(".len").textContent = s.length ? fmt(s.length) : "—";
    setRange(c.querySelector(".vol"), s.volume);
  }
  const m = state.music || {};
  $("#track").textContent = m.track || (m.ready ? "Musique" : "Musique (choisis un dossier sur le PC)");
  $("#trackSub").textContent = m.ready ? (m.playing ? "en lecture" : "en pause") : "";
  $("#mPlay .ico").textContent = m.playing ? "⏸" : "▶";
  $("#mShuffle").classList.toggle("on", !!m.shuffle);
  $("#mMontage").classList.toggle("on", !!m.montage);
  setRange($("#mVol"), m.volume ?? 0);
  $("#bDiscreet").classList.toggle("on", !!state.discreet);
}

// ---------- commandes (delegation)
document.addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b || b.disabled) return;
  if (b.dataset.cmd) return act(b.dataset.cmd, +(b.dataset.screen||0), b.dataset.v !== undefined ? +b.dataset.v : undefined);
  if (b.dataset.m) return act(b.dataset.m);
  if (b.dataset.t !== undefined){ target = +b.dataset.t; markTarget(); return; }
});
document.addEventListener("click", e => { const f = e.target.closest("[data-focus]"); if (f) act("focus", +f.dataset.focus); });
$("#rot").onclick = () => act("rotate", 0, 1);
$("#rotBack").onclick = () => act("rotate", 0, -1);
$("#bPanic").onclick = () => { act("panic"); toast("Panique : tout est masqué"); };
$("#bHome").onclick = () => act("home");
$("#bIdent").onclick = () => act("identify");
$("#bDiscreet").onclick = () => { act("discreet"); setTimeout(loadLib, 400); };

function throttle(fn, ms){ let t=0, h=null, last; return v => { last=v; const now=Date.now();
  if (now-t>ms){ t=now; fn(v); } else { clearTimeout(h); h=setTimeout(()=>{ t=Date.now(); fn(last); }, ms); } }; }
function bindRanges(){
  document.querySelectorAll("input[type=range]").forEach(r => {
    if (r._bound) return; r._bound = true;
    const down = () => dragging.add(r), up = () => setTimeout(()=>dragging.delete(r), 900);
    r.addEventListener("pointerdown", down); r.addEventListener("touchstart", down, {passive:true});
    r.addEventListener("pointerup", up); r.addEventListener("touchend", up); r.addEventListener("change", up);
    if (r.classList.contains("vol")){
      const send = throttle(v => api("/api/action",{cmd:"volume", screen:+r.dataset.screen, value:+v}).catch(()=>{}), 150);
      r.addEventListener("input", () => send(r.value));
    } else if (r.classList.contains("seek")){
      const card = r.closest(".card");
      r.addEventListener("input", () => { const s = (state.screens||[]).find(x=>x.n==r.dataset.screen);
        if (s) card.querySelector(".cur").textContent = fmt(s.length * r.value / 1000); });
      r.addEventListener("change", () => api("/api/action",{cmd:"seek_to", screen:+r.dataset.screen, value:r.value/1000}).catch(()=>{}));
    }
  });
}
{ const r = $("#mVol"); const send = throttle(v => api("/api/action",{cmd:"music_volume", value:+v}).catch(()=>{}), 150);
  r.addEventListener("input", () => send(r.value));
  const down = () => dragging.add(r), up = () => setTimeout(()=>dragging.delete(r), 900);
  r.addEventListener("pointerdown", down); r.addEventListener("touchstart", down, {passive:true});
  r.addEventListener("pointerup", up); r.addEventListener("touchend", up); }

// ---------- onglets
function tab(lib){
  $("#tabScreens").classList.toggle("hidden", lib); $("#tabLib").classList.toggle("hidden", !lib);
  $("#navScreens").classList.toggle("on", !lib); $("#navLib").classList.toggle("on", lib);
  if (lib) { loadLib(); search(); }
  window.scrollTo(0,0);
}
$("#navScreens").onclick = () => tab(false);
$("#navLib").onclick = () => tab(true);
function markTarget(){ document.querySelectorAll("#target button").forEach(b => b.classList.toggle("on", +b.dataset.t === target)); }

// ---------- bibliotheque
let qTimer = null;
$("#q").addEventListener("input", () => { clearTimeout(qTimer); qTimer = setTimeout(search, 300); });
async function search(){
  try {
    const j = await api("/api/search?q=" + encodeURIComponent($("#q").value));
    $("#results").innerHTML = j.items.length ? j.items.map(it => `
      <div class="item" data-play="${it.id}">
        <div class="thumb" ${it.thumb ? `style="background-image:url('/api/thumb/${it.id}')"` : ""}></div>
        <div class="t"><div class="title">${it.liked?'<span style="color:var(--heart)">♥</span> ':''}${esc(it.title)}</div>
        <div class="sub">${esc(it.sub)}${it.duration?' · '+fmt(it.duration):''}</div></div>
      </div>`).join("") : `<div class="sub" style="padding:8px">Aucun résultat</div>`;
  } catch(e){}
}
$("#results").addEventListener("click", e => { const it = e.target.closest("[data-play]"); if (!it) return;
  act("play", target, +it.dataset.play); toast(target ? "Lecture sur l'écran " + target : "Lecture lancée"); });

function listRow(kind, x){
  const multi = nScreens > 1 ? `<button data-pl="${kind}" data-id="${esc(x.id)}" data-multi="1" title="Répartir sur tous les écrans">▶${nScreens}</button>` : "";
  return `<div class="item"><div class="t"><div class="title">${x.likes?'<span style="color:var(--heart)">♥</span> ':''}${esc(x.name)}</div>
    <div class="sub">${x.n} élément(s)</div></div>
    <div class="acts"><button data-pl="${kind}" data-id="${esc(x.id)}" title="Lire"><span class="ico">▶</span></button>
    <button data-pl="${kind}" data-id="${esc(x.id)}" data-shuffle="1" title="Aléatoire"><span class="ico">⇄</span></button>${multi}</div></div>`;
}
async function loadLib(){
  try {
    const j = await api("/api/library");
    $("#playlists").innerHTML = j.playlists.map(p => listRow("playlist", p)).join("") || `<div class="sub" style="padding:8px">Aucune playlist</div>`;
    $("#themes").innerHTML = j.themes.map(t => listRow("theme", t)).join("");
  } catch(e){}
}
document.addEventListener("click", e => { const b = e.target.closest("[data-pl]"); if (!b) return;
  vibrate();
  api("/api/action",{cmd:"play_list", screen:target, kind:b.dataset.pl, id:b.dataset.id,
    shuffle:!!b.dataset.shuffle, multi:!!b.dataset.multi}).then(()=>toast(b.dataset.multi ? "Réparti sur les écrans" : "Lecture lancée")).catch(()=>{});
});

// ---------- disposition : compacte (telephone a l'horizontale, tablette) / ecran partage (tablette)
let libLoaded = false;
function layout(){
  const w = window.innerWidth, h = window.innerHeight;
  const split = w >= 900 && h >= 560 && w > h;
  const compact = split || (w > h && h < 560);
  document.body.classList.toggle("compact", compact);
  document.body.classList.toggle("split", split);
  if (split && !libLoaded && !$("#app").classList.contains("hidden")) { libLoaded = true; loadLib(); search(); }
}
window.addEventListener("resize", layout);
window.addEventListener("orientationchange", () => setTimeout(layout, 200));
layout();
setInterval(layout, 2000);


// ---------- mode « une main » (telephone en portrait)
let oSel = [], oLibTab = "res", oLibData = null, oDrag = new Set();
const PLAY_SVG = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 4.5v15L20 12z"/></svg>';
const PAUSE_SVG = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1.2"/><rect x="14" y="5" width="4" height="14" rx="1.2"/></svg>';

function oneLayout(){
  const w = window.innerWidth, h = window.innerHeight;
  const n = ((state && state.screens) || []).length;
  const land = w > h && h < 560 && n >= 5;            // telephone couche : la vue une main gere mieux 5+ ecrans
  const one = (h > w && w < 760) || land;
  document.body.classList.toggle("one", one);
  document.body.classList.toggle("land", land);
  document.body.classList.toggle("many", n >= 5);
}
window.addEventListener("resize", oneLayout);
window.addEventListener("orientationchange", () => setTimeout(oneLayout, 200));
oneLayout();
{
  const _showApp = showApp, _showLogin = showLogin, _render = render;
  showApp = function(){ _showApp(); document.body.classList.add("logged"); oneLayout(); };
  showLogin = function(){ document.body.classList.remove("logged"); _showLogin(); };
  render = function(){
    try { oneLayout(); } catch(e) {}
    _render();
    if (nScreens >= 5) document.querySelectorAll("#target button[data-t]").forEach(b => { if (+b.dataset.t) b.textContent = b.dataset.t; });
    try { renderOne(); } catch(e) { console.error(e); }
  };
}

function oScreens(){ return (state && state.screens) || []; }
// oSel : "all" ou liste de numeros (1 element = un ecran, plusieurs = groupe)
function oNums(){ return oSel === "all" ? [] : (oSel || []); }
function oSingle(){ return oSel !== "all" && oNums().length === 1; }
function oCurrent(){ return oSingle() ? oScreens().find(s => s.n === oNums()[0]) : null; }
function oTarget(){ return oSingle() ? oNums()[0] : 0; }
function oRefs(){ const scr = oScreens(); return oSel === "all" ? scr.filter(s => s.busy) : scr.filter(s => oNums().includes(s.n) && s.busy); }
function oAct(cmd, value){
  if (oSel === "all" || oSingle()) return act(cmd, oTarget(), value);
  for (const n of oNums()) act(cmd, n, value);
}
function oApi(cmd, value){
  const list = (oSel === "all" || oSingle()) ? [oTarget()] : oNums();
  for (const n of list) api("/api/action", {cmd, screen:n, value}).catch(()=>{});
}
function oIsSel(n){ return oSel !== "all" && oNums().includes(n); }

// disposition du selecteur : rangee (ecrans alignes, 5 au plus) ou mini-carte des ecrans tels qu'ils sont places
function oSelMode(scr){
  const g = scr.filter(s => s.geo);
  if (g.length < 2) return scr.length <= 5 ? "row" : "map";
  const h = Math.min(...g.map(s => s.geo[3]));
  const aligned = g.every(s => Math.abs(s.geo[1] - g[0].geo[1]) < h * 0.4);
  return (aligned && scr.length <= 5) ? "row" : "map";
}
function oBuildSel(scr){
  const sel = $("#oSel"), mode = oSelMode(scr);
  const land = document.body.classList.contains("land");
  const key = mode + "|" + land + "|" + window.innerWidth + "x" + window.innerHeight + "|" + scr.map(s => s.n + ":" + (s.geo || []).join(",")).join(";");
  if (sel.dataset.k === key || !sel.clientWidth) return;
  sel.dataset.k = key;
  const btn = (s, style, cls) => `<button data-s="${s.n}" class="${cls||""}" ${style ? `style="${style}"` : ""}><b>${s.n}</b><small></small></button>`;
  if (mode === "row"){
    sel.className = "o-sel";
    sel.style.setProperty("--n", scr.length + 1);
    sel.style.height = "";
    sel.innerHTML = scr.map(s => btn(s, "", scr.length >= 5 ? "tiny" : "")).join("")
      + `<button data-s="all" class="o-all"><b style="font-size:16px">Tous</b><small>ensemble</small></button>`;
    return;
  }
  // mini-carte
  sel.className = "o-sel map";
  // ecrans sans position (satellite, ancienne version) : places en rangee sous les autres
  const known = scr.filter(s => s.geo), rem = scr.filter(s => !s.geo);
  const boxes = {};
  known.forEach(s => boxes[s.n] = s.geo);
  if (rem.length){
    const bw = known.length ? known.reduce((a, s) => a + s.geo[2], 0) / known.length : 1920;
    const bh = known.length ? known.reduce((a, s) => a + s.geo[3], 0) / known.length : 1080;
    const cols = known.length ? Math.max(rem.length, 1) : Math.ceil(Math.sqrt(rem.length));
    const bx = known.length ? Math.min(...known.map(s => s.geo[0])) : 0;
    const by = known.length ? Math.max(...known.map(s => s.geo[1] + s.geo[3])) + bh * 0.1 : 0;
    const f = known.length ? 0.7 : 1;
    rem.forEach((s, i) => boxes[s.n] = [bx + (i % cols) * bw * f * 1.05, by + Math.floor(i / cols) * bh * f * 1.1, bw * f, bh * f]);
  }
  const geo = scr.map(s => ({n: s.n, geo: boxes[s.n]}));
  const W = Math.max(120, sel.clientWidth - 72);
  const H = land ? Math.max(60, window.innerHeight - 118 - $("#oSwipe").offsetHeight)
                 : (window.innerHeight < 700 ? 112 : 160);
  const x0 = Math.min(...geo.map(s => s.geo[0])), y0 = Math.min(...geo.map(s => s.geo[1]));
  const x1 = Math.max(...geo.map(s => s.geo[0] + s.geo[2])), y1 = Math.max(...geo.map(s => s.geo[1] + s.geo[3]));
  const k = Math.min(W / (x1 - x0), H / (y1 - y0));
  const ox = (W - (x1 - x0) * k) / 2, oy = (H - (y1 - y0) * k) / 2, gap = 3;
  const cells = geo.map(s => {
    const l = ox + (s.geo[0] - x0) * k + gap, t = oy + (s.geo[1] - y0) * k + gap;
    const w = s.geo[2] * k - 2 * gap, h = s.geo[3] * k - 2 * gap;
    return btn(s, `left:${l.toFixed(1)}px;top:${t.toFixed(1)}px;width:${w.toFixed(1)}px;height:${h.toFixed(1)}px`, h < 46 || w < 50 ? "tiny" : "");
  }).join("");
  sel.style.height = "";
  sel.innerHTML = `<div style="flex:1;min-width:0"><div class="o-map" style="height:${H}px">${cells}</div>`
    + `</div><button data-s="all" class="o-all"><b style="font-size:15px">Tous</b><small>ensemble</small></button>`;
}

function renderOne(){
  if (!document.body.classList.contains("one")) return;
  $("#oDot").className = "dot " + (Date.now() - lastOk < 3000 ? "ok" : "ko");
  const scr = oScreens(), busy = scr.filter(s => s.busy);
  if (oSel !== "all"){
    oSel = oNums().filter(n => scr.find(s => s.n === n));
    if (!oSel.length) oSel = busy.length ? [busy[0].n] : (scr[0] ? [scr[0].n] : "all");
  }
  // selection des ecrans
  const want = scr.map(s => s.n).join(",");
  oBuildSel(scr);
  const group = oSel !== "all" && oNums().length > 1;
  $("#oSel").querySelectorAll("button").forEach(b => {
    const k = b.dataset.s === "all" ? "all" : +b.dataset.s;
    const s = scr.find(x => x.n === k);
    const on = k === "all" ? oSel === "all" : oIsSel(k);
    b.classList.toggle("on", on && !group);
    b.classList.toggle("grp", on && group);
    b.classList.toggle("free", !!s && !s.busy);
    if (s) b.querySelector("small").textContent = !s.busy ? "libre" : (on ? (group ? "groupe" : "piloté") : (s.playing ? "lecture" : "pause"));
  });
  const s = oCurrent(), refs = oRefs();
  const active = refs.length > 0;
  $("#oKicker").textContent = oSel === "all" ? "TOUS LES ÉCRANS" : group ? ("ÉCRANS " + oNums().slice().sort((a,b)=>a-b).join(" + "))
    : ("ÉCRAN " + oNums()[0] + (s && s.sub ? "  ·  " + s.sub : ""));
  $("#oTitle").textContent = oSel === "all" ? (busy.length + " écran(s) en lecture")
    : group ? (refs.length + " écran(s) en lecture dans le groupe")
    : (s && s.busy ? s.title : "Écran libre : choisis une vidéo dans Bibliothèque");
  const heart = $("#oHeart");
  heart.style.visibility = (s && s.busy) ? "visible" : "hidden";
  heart.classList.toggle("on", !!(s && s.liked));
  $("#oSeekRow").style.visibility = (s && s.busy && s.length) ? "visible" : "hidden";
  if (s && s.busy && s.length && !oDrag.has("seek")){
    $("#oSeek").value = Math.round(1000 * s.time / s.length);
    $("#oCur").textContent = fmt(s.time); $("#oLen").textContent = fmt(s.length);
  }
  const playing = refs.some(x => x.playing);
  $("#oPlay").innerHTML = playing ? PAUSE_SVG : PLAY_SVG;
  document.querySelectorAll("#oScreens .o-pad button, #oScreens .o-volrow button, #oVol").forEach(b => b.disabled = !active);
  $("#oMore").disabled = !active;
  const muted = refs.length && refs.every(x => x.muted);
  $("#oMute").style.color = muted ? "var(--red)" : "";
  if (!oDrag.has("vol")) $("#oVol").value = refs[0] ? refs[0].volume || 0 : 80;
  // panneau options
  const ref = refs;
  $("#oLoop").classList.toggle("on", ref.length > 0 && ref.every(x => x.loop));
  $("#oRand").classList.toggle("on", ref.length > 0 && ref.every(x => x.random));
  $("#oViewLab").textContent = s && s.view ? s.view.split(" (")[0] : "Format";
  $("#oSheetTitle").textContent = oSel === "all" ? "Options — tous les écrans" : group ? "Options — écrans " + oNums().join(" + ") : "Options — écran " + oNums()[0];
  // musique
  const m = state.music || {};
  $("#oTrack").textContent = m.track || (m.ready ? "Musique" : "Choisis un dossier de musique sur le PC");
  $("#oMPlay").innerHTML = m.playing ? PAUSE_SVG : PLAY_SVG;
  $("#oShuf").classList.toggle("on", !!m.shuffle);
  $("#oMont").classList.toggle("on", !!m.montage);
  if (!oDrag.has("mvol")) $("#oMVol").value = m.volume ?? 0;
  $("#oDiscreet").classList.toggle("on", !!state.discreet);
  // cibles de lancement (bibliotheque)
  const tg = $("#oTarget");
  if (tg.dataset.k !== want){
    tg.dataset.k = want;
    tg.innerHTML = `<button data-t="0">Auto</button>` + scr.map(x => `<button data-t="${x.n}">${scr.length >= 5 ? (x.n === scr[0].n ? "Écran " : "") + x.n : "Écran " + x.n}</button>`).join("");
  }
  tg.querySelectorAll("button").forEach(b => b.classList.toggle("on", +b.dataset.t === target));
}

// boutons
document.addEventListener("click", e => {
  const b = e.target.closest("#one button"); if (!b || b.disabled) return;
  if (b.dataset.s){ if (oLong){ oLong = false; return; } oSel = b.dataset.s === "all" ? "all" : [+b.dataset.s]; vibrate(); renderOne(); return; }
  if (b.dataset.o){ oAct(b.dataset.o, b.dataset.v !== undefined ? +b.dataset.v : undefined);
    if (b.closest("#oSheet") && ["stop","moment","scene"].includes(b.dataset.o)) $("#oSheet").classList.add("hidden"); return; }
  if (b.dataset.ry){ act("rythme", 0, b.dataset.v !== undefined ? +b.dataset.v : undefined, {sub: b.dataset.ry});
    toast({toggle:"Frise : afficher / masquer", style:"Frise : style suivant", fx:"Frise : effets", shift:"Frise : décalage " + (b.dataset.v > 0 ? "+" : "") + b.dataset.v + " ms"}[b.dataset.ry]); return; }
  if (b.dataset.o2 === "rotate"){ act("rotate", 0, 1); return; }
  if (b.dataset.o2 === "rotback"){ act("rotate", 0, -1); return; }
  if (b.dataset.m2){ act(b.dataset.m2); return; }
  if (b.dataset.r !== undefined){ oAct("rate", +b.dataset.r); toast(+b.dataset.r ? "★".repeat(+b.dataset.r) : "Note retirée"); return; }
  if (b.dataset.tab){ oTab(b.dataset.tab); return; }
  if (b.dataset.lt){ oLibTab = b.dataset.lt; document.querySelectorAll("#oLibTabs button").forEach(x => x.classList.toggle("on", x === b)); oLibRender(); return; }
  if (b.dataset.t !== undefined){ target = +b.dataset.t; renderOne(); return; }
  if (b.dataset.play){ act("play", target, +b.dataset.play); toast(target ? "Lecture sur l'écran " + target : "Lecture lancée"); return; }
  if (b.dataset.so){ act("soiree", 0, +b.dataset.so); toast("Soirée lancée"); return; }
});
$("#oHeart").onclick = () => { if (oSingle()) act("like", oTarget()); };
// appui long sur un ecran : l'ajoute au groupe (ou l'en retire)
let oLong = false, oLongT = null;
$("#oSel").addEventListener("pointerdown", e => {
  const b = e.target.closest("button[data-s]"); if (!b || b.dataset.s === "all") return;
  clearTimeout(oLongT);
  oLongT = setTimeout(() => {
    const n = +b.dataset.s, cur = oSel === "all" ? [] : oNums().slice();
    const i = cur.indexOf(n);
    if (i >= 0 && cur.length > 1) cur.splice(i, 1); else if (i < 0) cur.push(n);
    oSel = cur; oLong = true;
    try { navigator.vibrate && navigator.vibrate([15, 40, 15]); } catch(e){}
    toast(cur.length > 1 ? "Groupe : écrans " + cur.slice().sort((a,b)=>a-b).join(" + ") : "Écran " + cur[0]);
    renderOne();
  }, 450);
});
["pointerup","pointerleave","pointercancel"].forEach(t => $("#oSel").addEventListener(t, () => clearTimeout(oLongT)));
$("#oSel").addEventListener("contextmenu", e => e.preventDefault());
$("#oMore").onclick = () => $("#oSheet").classList.remove("hidden");
$("#oSheetClose").onclick = () => $("#oSheet").classList.add("hidden");
$("#oSheet").addEventListener("click", e => { if (e.target.id === "oSheet") $("#oSheet").classList.add("hidden"); });
$("#oPanic").onclick = () => { act("panic"); toast("Panique : tout est masqué"); };
$("#oIdent").onclick = () => act("identify");
$("#oDiscreet").onclick = () => { act("discreet"); oLibData = null; };

// curseurs
function oRange(el, key, onInput, onChange){
  const down = () => oDrag.add(key), up = () => setTimeout(() => oDrag.delete(key), 900);
  el.addEventListener("pointerdown", down); el.addEventListener("touchstart", down, {passive:true});
  el.addEventListener("pointerup", up); el.addEventListener("touchend", up);
  if (onInput) el.addEventListener("input", () => onInput(el.value));
  if (onChange) el.addEventListener("change", () => { onChange(el.value); up(); });
}
oRange($("#oSeek"), "seek", v => { const s = oCurrent(); if (s) $("#oCur").textContent = fmt(s.length * v / 1000); },
       v => { if (oSingle()) api("/api/action", {cmd:"seek_to", screen:oTarget(), value:v/1000}).catch(()=>{}); });
oRange($("#oVol"), "vol", throttle(v => oApi("volume", +v), 150));
oRange($("#oMVol"), "mvol", throttle(v => api("/api/action", {cmd:"music_volume", value:+v}).catch(()=>{}), 150));

// glisser a gauche / a droite pour changer d'ecran
{
  let x0 = null, y0 = 0;
  const zone = $("#oSwipe");
  zone.addEventListener("touchstart", e => { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; }, {passive:true});
  zone.addEventListener("touchend", e => {
    if (x0 === null || e.target.closest("input")) return;
    const dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0; x0 = null;
    if (Math.abs(dx) < 60 || Math.abs(dy) > Math.abs(dx)) return;
    const keys = oScreens().map(s => s.n).concat(["all"]);
    let i = keys.indexOf(oSel === "all" ? "all" : oNums()[0]); i = (i + (dx < 0 ? 1 : -1) + keys.length) % keys.length;
    oSel = keys[i] === "all" ? "all" : [keys[i]]; vibrate(); renderOne();
  });
}

// onglets
function oTab(id){
  ["oScreens","oLib","oMusic"].forEach(x => $("#" + x).classList.toggle("hidden", x !== id));
  document.querySelectorAll(".o-nav button[data-tab]").forEach(b => b.classList.toggle("on", b.dataset.tab === id));
  $("#oSheet").classList.add("hidden");
  if (id === "oLib") { oLibRender(); }
}

// bibliotheque
let oQTimer = null;
$("#oQ").addEventListener("input", () => { clearTimeout(oQTimer); oQTimer = setTimeout(() => {
  oLibTab = "res"; document.querySelectorAll("#oLibTabs button").forEach(x => x.classList.toggle("on", x.dataset.lt === "res")); oLibRender(); }, 300); });
async function oLibRender(){
  const body = $("#oLibBody");
  try {
    if (oLibTab === "res"){
      const j = await api("/api/search?q=" + encodeURIComponent($("#oQ").value));
      body.innerHTML = j.items.length ? `<div class="o-cards">` + j.items.map(it => `
        <button class="o-card" data-play="${it.id}">
          <div class="th" ${it.thumb ? `style="background-image:url('/api/thumb/${it.id}')"` : ""}></div>
          <span class="t">${it.liked ? '<span style="color:var(--heart)">♥</span> ' : ""}${esc(it.title)}</span>
          <span class="s">${esc(it.sub)}${it.duration ? " · " + fmt(it.duration) : ""}</span></button>`).join("") + `</div>`
        : `<div class="sub" style="padding:8px">Aucun résultat</div>`;
      return;
    }
    if (!oLibData) oLibData = await api("/api/library");
    const rows = (kind, list) => list.length ? list.map(x => `<div class="o-row"><div class="t">${x.likes ? '<span style="color:var(--heart)">♥</span> ' : ""}${esc(x.name)}<div class="s">${x.n} élément(s)</div></div>
      <button data-pl="${kind}" data-id="${esc(x.id)}" aria-label="Lire">▶</button>
      <button data-pl="${kind}" data-id="${esc(x.id)}" data-shuffle="1" aria-label="Aléatoire">⇄</button>
      ${nScreens > 1 ? `<button data-pl="${kind}" data-id="${esc(x.id)}" data-multi="1" aria-label="Sur tous les écrans">▶${nScreens}</button>` : ""}</div>`).join("")
      : `<div class="sub" style="padding:8px">Rien pour l'instant</div>`;
    if (oLibTab === "pl") body.innerHTML = rows("playlist", oLibData.playlists || []);
    else if (oLibTab === "th") body.innerHTML = rows("theme", oLibData.themes || []);
    else body.innerHTML = (oLibData.soirees || []).length ? oLibData.soirees.map(x => `<div class="o-row"><div class="t">${esc(x.name)}<div class="s">${x.n} écran(s)</div></div>
      <button data-so="${x.id}" aria-label="Lancer">▶</button></div>`).join("") : `<div class="sub" style="padding:8px">Aucune soirée enregistrée (bouton « Soirées » sur le PC)</div>`;
  } catch(e) {}
}

// ---------- seance (onglet Musique)
document.addEventListener("click", e => {
  const b = e.target.closest("[data-se]"); if (!b) return;
  act("seance", 0, undefined, {sub: b.dataset.se});
  toast({start:"Séance : démarrage", pause:"Séance : pause / reprise", next:"Séance : phase suivante", stop:"Séance : fin"}[b.dataset.se]);
});
// ---------- demarrage
(async () => { try { await api("/api/state"); showApp(); } catch(e){ if (String(e.message) !== "auth") showLogin(); } })();
</script>
</body>
</html>
"""
