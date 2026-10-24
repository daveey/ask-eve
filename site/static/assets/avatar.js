(function(){
  var dataEl = document.getElementById('av-data-json');
  if (!dataEl) return;
  var items = JSON.parse(dataEl.textContent);
  var keys = Object.keys(items);
  var modal = document.getElementById('av-modal');
  var curId = null;
  var ru = (document.documentElement.lang || '').toLowerCase() === 'ru';
  var dims = [
    ['persona_fit', ru ? 'Характер (25%)' : 'Persona Fit (25%)'],
    ['readability_64px', ru ? 'Читаемость 64px (20%)' : 'Readability at 64px (20%)'],
    ['palette_harmony', ru ? 'Гармония палитры (20%)' : 'Palette Harmony (20%)'],
    ['visual_craft', ru ? 'Визуальное мастерство (20%)' : 'Visual Craft (20%)'],
    ['distinctiveness', ru ? 'Иконичность (15%)' : 'Distinctiveness (15%)']
  ];
  function openItem(id) {
    var it = items[id];
    if (!it || !modal) return;
    curId = id;
    document.getElementById('av-m-id').textContent = it.id.toUpperCase() + (it.rank ? ' · #' + it.rank : '');
    document.getElementById('av-m-title').textContent = it.prompt_title + ' (' + it.category + ')';
    document.getElementById('av-m-img').src = it.full || it.thumb;
    document.getElementById('av-m-img64').src = it.thumb;
    document.getElementById('av-m-score').textContent = it.total.toFixed(2);
    document.getElementById('av-m-crit').textContent = '“' + it.critique + '”';
    var nw = document.getElementById('av-m-note-wrap');
    if (it.note) {
      nw.hidden = false;
      document.getElementById('av-m-note').textContent = it.note;
    } else {
      nw.hidden = true;
    }
    document.getElementById('av-m-prompt').textContent = it.prompt_text;
    var barsHtml = '';
    for (var i = 0; i < dims.length; i++) {
      var k = dims[i][0], lbl = dims[i][1];
      var v = it.scores[k] || 0;
      barsHtml += '<div class="av-srow"><div class="av-srow__head"><span class="av-srow__name">' + lbl + '</span><span class="av-srow__val tnum"><strong>' + v.toFixed(1) + '</strong>/10</span></div><div class="av-bar"><span class="av-w-' + Math.round(v*10) + '"></span></div></div>';
    }
    document.getElementById('av-m-bars').innerHTML = barsHtml;
    if (!modal.open && modal.showModal) modal.showModal();
  }
  document.addEventListener('click', function(ev) {
    var opener = ev.target.closest('[data-open-id]');
    if (opener && !ev.target.closest('a')) {
      openItem(opener.getAttribute('data-open-id'));
      return;
    }
    if (ev.target === modal) modal.close();
  });
  document.getElementById('av-m-close').addEventListener('click', function() { modal.close(); });
  document.getElementById('av-m-prev').addEventListener('click', function() {
    var idx = keys.indexOf(curId);
    if (idx > -1) openItem(keys[(idx - 1 + keys.length) % keys.length]);
  });
  document.getElementById('av-m-next').addEventListener('click', function() {
    var idx = keys.indexOf(curId);
    if (idx > -1) openItem(keys[(idx + 1) % keys.length]);
  });
  var container = document.getElementById('av-prompts-container');
  document.querySelectorAll('[data-sort]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      document.querySelectorAll('[data-sort]').forEach(function(b) { b.classList.remove('is-active'); });
      btn.classList.add('is-active');
      var mode = btn.getAttribute('data-sort');
      var secs = Array.prototype.slice.call(container.querySelectorAll('.av-pblock'));
      secs.sort(function(a, b) {
        if (mode === 'avg') return parseFloat(b.getAttribute('data-pavg')) - parseFloat(a.getAttribute('data-pavg'));
        if (mode === 'best') return parseFloat(b.getAttribute('data-pbest')) - parseFloat(a.getAttribute('data-pbest'));
        return parseInt(a.getAttribute('data-pnum'), 10) - parseInt(b.getAttribute('data-pnum'), 10);
      });
      secs.forEach(function(s) { container.appendChild(s); });
    });
  });
  document.querySelectorAll('[data-filter]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      document.querySelectorAll('[data-filter]').forEach(function(b) { b.classList.remove('is-active'); });
      btn.classList.add('is-active');
      var minScore = btn.getAttribute('data-filter') === '88' ? 88.0 : 0;
      document.querySelectorAll('.av-rcell').forEach(function(c) {
        c.hidden = !(parseFloat(c.getAttribute('data-score')) >= minScore);
      });
    });
  });
})();
