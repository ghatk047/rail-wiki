"""Shared click-to-zoom-pan lightbox for rendered Mermaid diagrams.

Used by build_site.py (process flow diagrams) and build_ea_diagrams.py (EA
landscape diagrams) -- one implementation so the two page types behave
identically rather than drifting. Pure vanilla JS, no external library.

Zoom is NOT a CSS transform: scale() on the cloned SVG. The on-page diagram
renders small (width:100% of a ~600px container) against a huge internal
viewBox (thousands of units) -- transform-scaling that element stretches
whatever raster the compositor already produced at the small size, which is
exactly what reads as blur. Instead, zoom sets the cloned SVG's own explicit
pixel width/height on every step, which forces the browser to re-render the
vector content at that resolution -- crisp at any zoom level. Pan is a plain
translate on the wrapper, which never rasterizes-and-stretches, so it was
never the blur source and is untouched.

CSS: append LIGHTBOX_CSS into the page's <style>.
HTML: append LIGHTBOX_HTML once, anywhere in <body>.
Behaviour: every element matching `.diagram-wrap` becomes click-to-zoom.

POST_RENDER_FIX_JS strips a second, unrelated cap: Mermaid's own root <svg>
ships with `width="100%"` and an inline `style="max-width: <viewBox width>px"`.
That caps the on-page diagram at its first-render size forever, no matter how
wide its container grows -- the browser then rasterises up to fill any extra
space, which looks exactly like the blur the lightbox already had to fix, just
on the inline (non-zoomed) diagram instead. Call it once, after Mermaid has
actually rendered (`mermaid.run().then(...)`, NOT the fire-and-forget
`startOnLoad: true` -- there is no reliable hook to run this after
`startOnLoad` finishes, since it returns no promise you can chain from
outside).
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
.dg-lb-canvas { flex: 1; overflow: hidden; cursor: grab; position: relative; }
.dg-lb-canvas.grabbing { cursor: grabbing; }
.dg-lb-inner { position: absolute; }
.dg-lb-inner svg { display: block; max-width: none !important; }
"""

# Not needed: the element is only ever translated (pan), never scaled, so
# there is nothing here for will-change to usefully pre-composite -- and its
# mere presence matches the exact anti-pattern that caused the original
# blur bug (will-change + transform: scale() rasterises the layer once at
# on-screen size, then stretches that cached bitmap). Left out deliberately,
# not just omitted by oversight.
POST_RENDER_FIX_JS = """
function stripSvgCaps(root) {
  (root || document).querySelectorAll('.diagram-wrap svg').forEach(function (svg) {
    var vb = svg.viewBox && svg.viewBox.baseVal;
    if (vb && vb.width) {
      svg.style.maxWidth = 'none';
      svg.setAttribute('width', Math.round(vb.width));
      if (vb.height) svg.setAttribute('height', Math.round(vb.height));
      svg.style.width = '100%';
      svg.style.height = 'auto';
    } else {
      svg.style.maxWidth = 'none';
    }
  });
}
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
  var baseW = 0, baseH = 0;
  var lb, inner, canvas, svgEl;

  // Pan via translate (cheap, never blurs). Zoom via the SVG's own pixel
  // width/height (forces real vector re-render, never blurs either) --
  // see the module docstring for why a CSS transform: scale() blurred.
  function apply() {
    inner.style.transform = 'translate(' + tx + 'px,' + ty + 'px)';
    if (svgEl) {
      svgEl.style.width = (baseW * scale) + 'px';
      svgEl.style.height = (baseH * scale) + 'px';
    }
  }

  function open(svg) {
    // Reveal the lightbox BEFORE measuring canvas -- a [hidden] element has
    // no layout box, so getBoundingClientRect() on it (or anything inside
    // it) returns all zeros. That was the immediate cause of a 0x0 SVG.
    lb.hidden = false;
    inner.innerHTML = '';
    var clone = svg.cloneNode(true);
    inner.appendChild(clone);
    svgEl = clone;

    // Natural size: prefer the viewBox aspect ratio (always present on a
    // Mermaid SVG) over the on-page rendered width, which is whatever the
    // small container happened to constrain it to.
    var vb = svg.viewBox && svg.viewBox.baseVal;
    var canvasRect = canvas.getBoundingClientRect();
    var targetW = Math.min(canvasRect.width * 0.9, (vb && vb.width) || canvasRect.width * 0.9);
    if (vb && vb.width && vb.height) {
      baseW = targetW;
      baseH = targetW * (vb.height / vb.width);
    } else {
      var r = svg.getBoundingClientRect();
      baseW = r.width || targetW;
      baseH = r.height || targetW;
    }
    svgEl.removeAttribute('width');
    svgEl.removeAttribute('height');

    scale = 1;
    // Centre the diagram in the canvas at its initial size.
    tx = (canvasRect.width - baseW) / 2;
    ty = (canvasRect.height - baseH) / 2;
    apply();
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
        var rect = canvas.getBoundingClientRect();
        var cx = rect.width / 2, cy = rect.height / 2;
        var before = scale;
        if (z === 'in') scale = Math.min(scale * 1.25, 6);
        else if (z === 'out') scale = Math.max(scale / 1.25, .2);
        else { scale = 1; tx = (rect.width - baseW) / 2; ty = (rect.height - baseH) / 2; apply(); return; }
        // Keep the canvas centre fixed under the cursor-free zoom buttons.
        tx = cx - (cx - tx) * (scale / before);
        ty = cy - (cy - ty) * (scale / before);
        apply();
      });
    });

    canvas.addEventListener('wheel', function (e) {
      e.preventDefault();
      var rect = canvas.getBoundingClientRect();
      var mx = e.clientX - rect.left, my = e.clientY - rect.top;
      var before = scale;
      var delta = e.deltaY < 0 ? 1.1 : .9;
      scale = Math.min(6, Math.max(.2, scale * delta));
      // Zoom toward the mouse position, not the canvas centre.
      tx = mx - (mx - tx) * (scale / before);
      ty = my - (my - ty) * (scale / before);
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
