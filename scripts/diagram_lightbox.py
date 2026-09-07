"""Shared click-to-zoom-pan lightbox for rendered Mermaid diagrams.

Used by build_site.py (process flow diagrams) and build_ea_diagrams.py (EA
landscape diagrams) -- one implementation so the two page types behave
identically rather than drifting. Pure vanilla JS, no external library: the
diagrams are already live SVG from the Mermaid CDN, so zoom/pan is just a CSS
transform on a cloned copy of that SVG inside a fixed overlay.

CSS: append LIGHTBOX_CSS into the page's <style>.
HTML: append LIGHTBOX_HTML once, anywhere in <body>.
Behaviour: every element matching `.diagram-wrap` becomes click-to-zoom.
"""

LIGHTBOX_CSS = """
.diagram-wrap { cursor: zoom-in; }
.dg-lightbox { position: fixed; inset: 0; background: rgba(10,10,10,.88); z-index: 1000;
  display: flex; flex-direction: column; }
.dg-lightbox[hidden] { display: none; }
.dg-lb-bar { display: flex; align-items: center; gap: .5rem; padding: .6rem 1rem; background: #14161a; }
.dg-lb-bar button { background: #2b2e33; color: #fff; border: 1px solid #444; border-radius: 4px;
  padding: .35rem .75rem; cursor: pointer; font-size: .85rem; }
.dg-lb-bar button:hover { background: #3a3d43; }
.dg-lb-bar .dg-lb-hint { color: #9a9da3; font-size: .78rem; }
.dg-lb-bar .dg-lb-close { margin-left: auto; }
.dg-lb-canvas { flex: 1; overflow: hidden; cursor: grab; display: flex; align-items: center; justify-content: center; }
.dg-lb-canvas.grabbing { cursor: grabbing; }
.dg-lb-inner { transform-origin: center center; will-change: transform; }
.dg-lb-inner svg { max-width: none !important; height: auto !important; }
"""

LIGHTBOX_HTML = """
<div id="dg-lightbox" class="dg-lightbox" hidden>
  <div class="dg-lb-bar">
    <button data-zoom="out" title="Zoom out">&minus;</button>
    <button data-zoom="in" title="Zoom in">&plus;</button>
    <button data-zoom="reset">Reset</button>
    <span class="dg-lb-hint">Scroll to zoom &middot; drag to pan &middot; Esc to close</span>
    <button class="dg-lb-close">&times; Close</button>
  </div>
  <div class="dg-lb-canvas"><div class="dg-lb-inner"></div></div>
</div>
<script>
(function () {
  var scale = 1, tx = 0, ty = 0, dragging = false, sx = 0, sy = 0;
  var lb, inner, canvas;

  function apply() { inner.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')'; }

  function open(svg) {
    inner.innerHTML = '';
    inner.appendChild(svg.cloneNode(true));
    scale = 1; tx = 0; ty = 0;
    apply();
    lb.hidden = false;
  }

  document.addEventListener('DOMContentLoaded', function () {
    lb = document.getElementById('dg-lightbox');
    if (!lb) return;
    inner = lb.querySelector('.dg-lb-inner');
    canvas = lb.querySelector('.dg-lb-canvas');

    document.querySelectorAll('.diagram-wrap').forEach(function (wrap) {
      wrap.addEventListener('click', function () {
        var svg = wrap.querySelector('svg');
        if (svg) open(svg);
      });
    });

    lb.querySelector('.dg-lb-close').addEventListener('click', function () { lb.hidden = true; });
    lb.addEventListener('click', function (e) { if (e.target === lb) lb.hidden = true; });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') lb.hidden = true; });

    lb.querySelectorAll('[data-zoom]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var z = btn.getAttribute('data-zoom');
        if (z === 'in') scale = Math.min(scale * 1.25, 6);
        else if (z === 'out') scale = Math.max(scale / 1.25, .2);
        else { scale = 1; tx = 0; ty = 0; }
        apply();
      });
    });

    canvas.addEventListener('wheel', function (e) {
      e.preventDefault();
      var delta = e.deltaY < 0 ? 1.1 : .9;
      scale = Math.min(6, Math.max(.2, scale * delta));
      apply();
    }, { passive: false });

    canvas.addEventListener('mousedown', function (e) {
      dragging = true; sx = e.clientX - tx; sy = e.clientY - ty;
      canvas.classList.add('grabbing');
    });
    window.addEventListener('mousemove', function (e) {
      if (!dragging) return;
      tx = e.clientX - sx; ty = e.clientY - sy;
      apply();
    });
    window.addEventListener('mouseup', function () {
      dragging = false;
      if (canvas) canvas.classList.remove('grabbing');
    });
  });
})();
</script>
"""
