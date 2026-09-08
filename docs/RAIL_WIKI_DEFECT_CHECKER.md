# Rail Wiki — Defect Checker

Paste this into any Claude Code session working on `~/Projects/rail-wiki/`.

Every item below is a defect that actually shipped in the shipping-wiki build and had
to be found the hard way, usually after being declared fixed. Each one costs a
round trip to catch by eye and about thirty seconds to catch by command. Run the
commands.

Context: Union Pacific archetype, 15 L1 domains, 290 processes, PID format
`RR-{L1}-{L2}-{NN}`, registry-first pipeline, repo `rail-wiki`, GitHub Pages from
`main` at `/ (root)`.

---

## Rule 0 — The finished artifacts are the specification

The single most expensive mistake in the shipping build: the handoff document was
treated as the spec, and the existing wikis as a styling reference. That was backwards.
The handoff described the pipeline. The eight already-shipped wikis encoded the actual
quality standard, and none of it was written down — nobody puts "arrows should be
labelled" in a constraints list, because it is just what the diagrams look like.

**Before writing or modifying any generator, do this and report the findings:**

```bash
cd /tmp && rm -rf ref && git clone --depth 1 https://github.com/ghatk047/airline-process-wiki ref
cd ref

# what does a real process diagram actually contain?
for f in diagrams/*.mmd; do
  echo "$(basename $f) lines=$(wc -l < $f) decisions=$(grep -c '{' $f) \
labelled=$(grep -cE '\-\- [A-Za-z]+ \-\->' $f) styles=$(grep -c '^\s*style' $f) \
subgraphs=$(grep -c subgraph $f)"
done | head -20

# and an EA diagram?
for f in ea-diagrams/*.mmd; do
  echo "$(basename $f) lines=$(wc -l < $f) labelled_edges=$(grep -c '\-\->|' $f) \
classDefs=$(grep -c classDef $f) subgraphs=$(grep -c subgraph $f)"
done | head -10
```

State the implied standard in numbers before writing a single prompt. If the rail
generator's output does not hit those numbers, it is not done, regardless of what any
spec document says.

---

## 1. Diagram richness — the defect that needed telling twice

**What shipped:** process diagrams with 3 subgraphs, bare arrows, no decision points,
no rework loops, no styling. The reference had ~52 lines, 13 decision diamonds, 26
labelled branches, 18 style lines.

**Checks:**

```bash
# any generated .mmd must clear these floors
for f in ~/Projects/rail-wiki/diagrams/*.mmd; do
  d=$(grep -c '{' $f); b=$(grep -cE '\-\- [A-Za-z]+ \-\->' $f)
  s=$(grep -c '^\s*style' $f); g=$(grep -c subgraph $f)
  [ $d -lt 4 ] || [ $b -lt 6 ] || [ $g -lt 4 ] && echo "THIN: $(basename $f) dec=$d br=$b style=$s sub=$g"
done
```

**Required in the generator:**
- A scoring function that grades every draft before publication, not after.
- Up to 3 drafts, early exit at a good score, best-of retained.
- Diagrams below the floor still publish but log a WARN naming the PID, so they can be
  re-run in a batch later.
- The prompt must carry a **worked form example from a different industry** — copying
  the construct without copying the content. An abstract description of the construct
  does not work on a 14B model.

**Rail specifics to require in every process diagram:** `([Start])` plus at least one
exception terminator (`([Bad Order])`, `([Hold])`, `([Rejected])`), 5–8 decision
diamonds with labelled `-- Yes -->` / `-- No -->` branches, at least 2 rework loops
routing a failure back to an earlier task, 22–30 nodes, two-line labels naming the
system, and a closing `style` block.

---

## 2. Mermaid must not travel inside JSON

**What shipped:** one Ollama call returning `{"l4_steps": [...], "mermaid": "..."}`.
Two-line labels need `\\n` to survive JSON encoding and the model gets it wrong often
enough to flatten every label, plus one malformed diagram takes the whole payload down.

**Required:** two calls. Call one returns content JSON with **no** mermaid field. Call
two receives the resulting steps and returns **raw Mermaid text**, no JSON, no fences.

```bash
grep -n '"mermaid"' ~/Projects/rail-wiki/scripts/*.py   # expect: nothing in the JSON shape
```

---

## 3. System prompt bleed

**What shipped:** the shared system prompt recited Mermaid syntax rules on a call that
explicitly wanted no diagram. The model helpfully emitted a diagram anyway, which broke
JSON extraction three attempts running.

**Required:** two constants. `SYSTEM_PROMPT_JSON` (domain knowledge, registries,
"return only JSON, no diagram syntax anywhere") and `SYSTEM_PROMPT_DIAGRAM`
(= JSON one + Mermaid rules). JSON calls never see the Mermaid section.

```bash
grep -n "MERMAID" ~/Projects/rail-wiki/scripts/*.py | grep -i json   # expect: nothing
```

---

## 4. JSON extraction must not trust the first brace

**What shipped:** a scanner that found the first `{` and gave up if it failed. When the
model prepends `%%{init: {'theme':'base'}}%%`, that block IS the first brace, and it
never parses because Mermaid uses single quotes.

**Required:** strip mermaid init blocks and `flowchart` lines, then walk **every**
balanced object and return the first that parses **and** carries a required key
(`l4_steps`, `systems`). Tolerate fences, trailing commas, embedded newlines. Save any
unparseable response to `data/raw/<pid>-json-<n>.txt`.

Test with the real failure mode:

```python
bad = """%%{init: {'theme':'base'}}%%\nflowchart TB\n A-->B\n
{"l4_steps":[{"step":"1.1"}]}"""
assert extract_json(bad, required=("l4_steps",))
```

---

## 5. The sanitiser destroys regulatory citations

**What shipped, inherited from a "battle-tested, do not modify" block:**

```python
re.sub(r'\b(\d+)\.(\d+)\b', r'S\1_\2', mmd)   # turns 1.1[Node] into S1_1[Node]
```

It cannot tell a node ID from label text, so `FAR 121.436` became `FAR S121_436`.

**This is worse for rail than for shipping.** Rail labels are dense with citations:
`49 CFR 213.9`, `49 CFR 232.205`, GCOR rule `6.28`, AAR standard `S-486`, FRA Class
`4.0` track speed. Every one of them will be silently corrupted.

**Required:** stash bracketed and braced label content before applying the digit rule,
then restore.

```bash
python3 -c "
from scripts.rail_generator import sanitise_mermaid as s
out = s('flowchart LR\n 1.1[Verify 49 CFR 213.9 compliance] --> 2.1[Done]')
assert '49 CFR 213.9' in out, 'CITATION CORRUPTED'
assert 'S1_1' in out, 'digit ID not fixed'
print('OK')"
```

Also extend label cleaning to `{...}` decision diamonds — the original only cleaned
`[...]` and `(...)`, so a stray parenthesis inside a diamond breaks the parse.

---

## 6. Publish vector, and then actually check the vector

This one took three rounds because each fix was real but insufficient. Do all three at once.

**6a. PNG is the wrong asset.** Raster blurs the moment the viewer zooms. Render `.svg`
for display. Keep a PNG only if a download button needs one.

**6b. mmdc's SVG caps its own size.** The root element comes out as:

```
<svg width="100%" style="max-width: 1933.8px" viewBox="0 0 1933.8 1690.5">
```

That inline `max-width` means the vector refuses to render beyond 1933px no matter how
large the `<img>` box is — the browser rasterises at that ceiling and scales the bitmap
up, which looks exactly like the PNG problem you just fixed. `width="100%"` also leaves
the file with no intrinsic size, so `naturalWidth` is unreliable.

**Required:** a post-render step that rewrites `width="100%"` to the viewBox dimensions
and strips the `max-width`.

```bash
for f in ~/Projects/rail-wiki/assets/img/*.svg; do
  head -c 400 $f | grep -q 'max-width' && echo "CAPPED: $(basename $f)"
  head -c 400 $f | grep -q 'width="100%"' && echo "NO INTRINSIC SIZE: $(basename $f)"
done
```

**6c. No font-family means serif.** An SVG loaded through `<img>` cannot fetch
webfonts. Mermaid's default stack silently falls back to a serif face and everything
looks soft and dated.

**Required:** force one canonical init line on every diagram, overwriting whatever the
model produced, using a system-resident stack:

```
%%{init: {'theme':'base','themeVariables':{'fontSize':'13px','fontFamily':'Helvetica Neue, Helvetica, Arial, sans-serif'}}}%%
```

```bash
for f in ~/Projects/rail-wiki/assets/img/*.svg; do
  grep -q 'font-family' $f || echo "SERIF FALLBACK: $(basename $f)"
done
```

---

## 7. The lightbox blurs vector even when the file is perfect

Two independent viewer bugs, both inherited from the airline `wiki.js`/`wiki.css`.

**7a. `will-change: transform` on the zoom image.** It promotes the element to a
compositor layer, rasterised **once** at on-screen size. `transform: scale()` then
magnifies that cached bitmap. Vector never re-renders.

**Required:** no `will-change`, no `scale()` for zoom. Zoom by setting **layout width**
(`img.style.width = baseW * scale`), which forces re-rasterisation at the new size.
Transform handles panning only.

**7b. Flex centring fights width-based zoom.** If the overlay centres with flexbox, the
element re-centres as it grows, so it walks sideways on every zoom step and the
focal-point math breaks.

**Required:** `position: absolute; top: 0; left: 0` on the image, centre once in
`resetView()` via the pan offset, round pan values to integers.

```bash
grep -cE '^\s*will-change:' ~/Projects/rail-wiki/assets/css/wiki.css   # expect 0
grep -c "scale(' + scale + ')" ~/Projects/rail-wiki/assets/js/wiki.js  # expect 0
grep -c 'position: absolute' ~/Projects/rail-wiki/assets/css/wiki.css  # expect >=1
```

---

## 8. Completed pages can never be rebuilt

**What shipped:** the tracker marks a PID Complete; the main loop skips anything
Complete; `--pid X` also goes through that loop. So after any template or diagram
change, every already-generated page is frozen and there is no way to regenerate it.
One page sat live for hours pointing at a deleted PNG.

**Required:**
- A `--force` flag that bypasses the completion check, honoured by `--pid`, `--count`,
  `--start` and `--full` alike.
- A template version constant stamped into every generated page as an HTML comment, so
  stale pages are greppable:

```bash
grep -L "template-v3" ~/Projects/rail-wiki/**/index.html   # lists stale pages
```

Add the version stamp now, before 290 pages exist. Retrofitting it means regenerating
everything.

---

## 9. Verification cadence versus runtime

A 90-second live check per process is 7.5 hours of pure waiting across 290.

**Required:** verify every page during a pilot, and at batch checkpoints
(`--verify-every N`, default 10) during bulk runs. Never write the Excel row before its
batch verifies. On verify failure, resync the tracker from the GitHub tree rather than
losing the work. Catch `KeyboardInterrupt`, flush what is done, rebuild nav, push
`.deploy`.

---

## 10. Registry discipline — rail-specific

The rail pipeline is registry-first precisely so the local model cannot invent systems,
regulations or facts. That guarantee is worth nothing unless it is enforced at
generation time.

**Required:** after each process is generated, validate every named system, regulation
and role against the registries, and log any value not found. Do not silently accept.

```bash
python3 scripts/validate_against_registry.py --all
# expect: zero unknown systems, zero unknown CFR citations
```

Spot-check for the classic fabrications: a system that does not exist at UP, a CFR part
number that is not real, a role title borrowed from airline ops (there is no "dispatcher"
in the airline sense — rail dispatchers are a different function), an FRA rule invented
to fit the sentence.

---

## 11. Structural checks that should never regress

```bash
# catalogue integrity — assert in code, not just in a comment
python3 -c "from scripts.rail_generator import PROCESSES
assert len(PROCESSES)==290, len(PROCESSES)
assert len({p['pid'] for p in PROCESSES})==290, 'duplicate PIDs'
print('catalogue OK')"

# folder tree matches the catalogue
find ~/Projects/rail-wiki -mindepth 3 -maxdepth 3 -type d -not -path "*/.git/*" | wc -l   # expect 290

# asset path depth — every page must compute its prefix from its own depth
grep -o 'href="\.\./\.\./\.\./assets' ~/Projects/rail-wiki/*/*/*/index.html | head -3   # process pages
grep -o 'href="\.\./\.\./assets'      ~/Projects/rail-wiki/*/*/index.html   | head -3   # L2 indexes
grep -o 'href="\.\./assets'           ~/Projects/rail-wiki/*/index.html     | head -3   # L1 indexes

# no token anywhere in the repo or scripts
grep -rn "ghp_[A-Za-z0-9]\{20,\}" ~/Projects/rail-wiki/ && echo "TOKEN LEAK" || echo "token clean"
```

Token comes from `os.environ["GITHUB_TOKEN"]` and the script exits loudly if unset.
Never from a file, never from a handoff document.

---

## 12. Environment preflight

Failures that look like code bugs and are not. Run before every bulk session.

```bash
curl -s http://localhost:11434/api/tags | grep -o 'qwen[^"]*' | sort -u   # models present
mmdc --version                                                            # CLI present
cat > /tmp/t.mmd <<'MMD'
%%{init: {'theme':'base'}}%%
flowchart LR
  A[Test] --> B[End]
MMD
mmdc -i /tmp/t.mmd -o /tmp/t.svg && echo "mmdc renders OK"
echo ${GITHUB_TOKEN:0:8}
```

Notes from the shipping build: mermaid-cli ships without its Chrome — install with
`npx puppeteer browsers install chrome` from inside the mermaid-cli directory, and use
`builtin cd` if zoxide intercepts `cd`. If puppeteer still cannot find it, export
`PUPPETEER_EXECUTABLE_PATH` to the binary. And never build a `.mmd` test fixture with
`printf` — it collapses `%%` to `%` and produces a fake parse error.

---

## 13. Post-run audit — run this after the first 10, not after 290

Do not judge output from a screenshot. Pull the repo and read the artifacts.

```bash
cd /tmp && rm -rf audit && git clone --depth 1 https://github.com/ghatk047/rail-wiki audit && cd audit

echo "=== assets ==="
ls assets/img/ | sed 's/.*\.//' | sort | uniq -c        # expect svg, not png
grep -cE '^\s*will-change:' assets/css/wiki.css          # expect 0

echo "=== every svg ==="
for f in assets/img/*.svg; do
  cap=$(head -c 400 $f | grep -c 'max-width')
  fnt=$(grep -c 'font-family' $f)
  echo "$(basename $f) cap=$cap font=$fnt"
done

echo "=== every page points at svg ==="
grep -roh 'assets/img/[a-z0-9-]*\.\(svg\|png\)' --include=index.html . | sed 's/.*\.//' | sort | uniq -c

echo "=== stale template versions ==="
grep -rL "template-v3" --include=index.html . | head
```

Expected: all `svg`, zero `cap=1`, zero `font=0`, no `.png` references on process
pages, no stale templates.

---

## How to use this with me

When I propose a fix for a rendering or output defect, hold me to this sequence:

1. Inspect the actual generated artifact — the `.mmd`, the `.svg` header, the deployed
   HTML — before proposing a cause.
2. State which layer the defect is in: content generation, sanitiser, renderer, asset
   post-processing, or viewer. The shipping blur touched three of those five and I
   fixed them one at a time across three rounds because I never opened the SVG.
3. Give the command that proves the fix, not an assurance that it is fixed.
4. After any generator change, say explicitly which already-generated pages are now
   stale and how to rebuild them.

If I declare something fixed without having read the artifact, that is the moment to
push back.
