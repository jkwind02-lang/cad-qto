"""Scene background restored from the exchange DXF (contract: docs/维护/图面视图映射契约.md, section 7).

The neutral layout keeps what measuring needs but not what a reviewer needs to recognise the drawing (text height,
colours, layer on/off, attribute placement and visibility, solid and pattern fills), so the review background is
rebuilt from the exchange DXF named in the neutral manifest, after checking its SHA256. Objects and quantities never
come from here. Blocks are expanded by class (object symbols and small blocks fully, large blocks only on whitelisted
layers); what the page cannot show is counted per scene as "须回原图"; symbol inserts are reconciled with the
object tables (on sheet / counted / uncounted / same position).
"""
import hashlib, json, math, re
from collections import Counter, defaultdict

LARGE_BLOCK_LAYERS = ['WALL', 'COLU', '柱', 'AXIS', 'DOTE', '轴', 'WINDOW', 'DOOR', '门', '窗', 'STAIR', '楼梯', 'STEP', 'HANDRAIL',
                      'SPACE', 'ROOM', '房间', 'TITLE', '图框', '图名', '图号', 'DIM', '标注', 'PUB_TEXT', 'PUB_HATCH']
DEFAULTS = {'large_block_points': 2000, 'flatten_mm': 8.0, 'duplicate_tolerance_mm': 1.0}
NOT_SHOWN = ('other_layers', 'pattern_hatch', 'image', 'ole', 'proxy', 'xref_missing')
NOTE_KINDS = ('removed_overlap', 'counted_elsewhere', 'same_item_elsewhere')
SKIP_SILENT = {'POINT', 'WIPEOUT', 'VIEWPORT', 'ATTDEF'}
UNSHOWN_TYPES = {'IMAGE': 'image', 'OLE2FRAME': 'ole', 'OLEFRAME': 'ole', 'ACAD_PROXY_ENTITY': 'proxy'}
ALIGN = {0: 'left', 1: 'center', 2: 'right', 3: 'left', 4: 'center', 5: 'left'}
VALIGN = {0: 'alphabetic', 1: 'bottom', 2: 'middle', 3: 'top'}
MAX_DEPTH = 12


class DxfBackgroundError(ValueError):
 pass


def sha256_file(path):
 h = hashlib.sha256()
 with open(path, 'rb') as f:
  for chunk in iter(lambda: f.read(1 << 20), b''): h.update(chunk)
 return h.hexdigest()


def short_layer(lay):
 """Layer name without a bound or attached xref prefix ('楼层$0$WALL', 'XREF|WALL' -> 'WALL')."""
 return re.split(r'\$\d+\$|\|', lay)[-1]


def compile_layers(pats):
 return re.compile('|'.join(f'(?:{p})' for p in pats), re.I) if pats else None


class _Drawing:
 """One loaded DXF with the per-drawing caches (layer state, colours, linetypes, block sizes)."""

 def __init__(self, path):
  import ezdxf
  self.ezdxf = ezdxf
  self.doc = ezdxf.readfile(str(path))
  self.msp = self.doc.modelspace()
  self.layers = {l.dxf.name: l for l in self.doc.layers}
  self.ltscale = self.doc.header.get('$LTSCALE', 1.0) or 1.0
  self._lt, self._size, self._affine = {}, {}, {}

 def hidden(self, lay):
  """Off, frozen and non-plotting layers (Defpoints never plots) are neither drawn nor counted as not shown (contract 7.1)."""
  if short_layer(lay).lower() == 'defpoints': return True
  l = self.layers.get(lay)
  return l is not None and (l.is_off() or l.is_frozen() or not l.dxf.get('plot', 1))

 def layer_aci(self, lay):
  l = self.layers.get(lay)
  return abs(l.dxf.color) if l is not None else 7

 def color(self, e, lay, byblock):
  from ezdxf.colors import aci2rgb, int2rgb
  if e.dxf.hasattr('true_color'): r, g, b = int2rgb(e.dxf.true_color)
  else:
   c = e.dxf.get('color', 256)
   if c == 0: c = byblock
   if c == 256: c = self.layer_aci(lay)
   if c in (0, 7, 256) or not 1 <= c <= 255: return '#222222'
   r, g, b = aci2rgb(c)
  # Light CAD colours (yellow, white-ish) are darkened so they stay readable on the white page.
  if 0.299 * r + 0.587 * g + 0.114 * b > 190: r, g, b = int(r * .55), int(g * .55), int(b * .55)
  return '#%02x%02x%02x' % (r, g, b)

 def dash(self, e, lay):
  name = e.dxf.get('linetype', 'BYLAYER')
  if name.upper() == 'BYLAYER':
   l = self.layers.get(lay); name = l.dxf.get('linetype', 'Continuous') if l is not None else 'Continuous'
  if name.upper() in ('BYBLOCK', 'CONTINUOUS'): return None
  if name not in self._lt:
   pat = None
   try:
    lt = self.doc.linetypes.get(name)
    raw = lt.simplified_line_pattern() if lt is not None else None
    if raw and any(raw): pat = [abs(v) for v in raw]
   except Exception: pat = None
   self._lt[name] = pat
  pat = self._lt[name]
  if not pat: return None
  k = self.ltscale * e.dxf.get('ltscale', 1.0)
  return [max(v * k, 1e-6) for v in pat]

 def block_size(self, name, stack=()):
  """Rough drawable point count of a block, nested blocks included (decides small or large, contract 7.2)."""
  if name in self._size: return self._size[name]
  if name in stack: return 0
  blk = self.doc.blocks.get(name); n = 0
  if blk is not None:
   for ve in blk:
    t = ve.dxftype()
    n += self.block_size(ve.dxf.name, stack + (name,)) if t == 'INSERT' else 2 if t == 'LINE' else 1 if t in ('TEXT', 'MTEXT', 'ATTDEF') else 8
  self._size[name] = n
  return n

 def affine_loss(self, e, parent=None, stack=()):
  """Whether nested INSERT decomposition would lose the definition-to-WCS matrix.

  Compare the transformed INSERT with the actual matrix product, including its base point.
  A shear can raise, or silently change the matrix; even orthogonal 90-degree nesting can lose scale.
  The check only visits block references, and is shared by drawing and the sheet broad phase.
  """
  from ezdxf.math import Matrix44
  name = e.dxf.name
  if name in stack or len(stack) >= MAX_DEPTH: return False
  try: m = e.matrix44() if parent is None else Matrix44.chain(e.matrix44(), parent)
  except Exception: return True
  key = (name, tuple(m))
  if key in self._affine: return self._affine[key]
  blk = self.doc.blocks.get(name)
  lost = False
  if blk is not None:
   for child in blk:
    if child.dxftype() != 'INSERT': continue
    try:
     expected = Matrix44.chain(child.matrix44(), m)
     actual = child.copy().transform(m).matrix44()
     lost = not all(math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-9) for a, b in zip(expected, actual))
    except Exception: lost = True
    if lost or self.affine_loss(child, m, stack + (name,)):
     lost = True; break
  self._affine[key] = lost
  return lost


def hatch_lines(h):
 """Pattern lines of a hatch. ezdxf jiggles the pattern origin with random(); a fixed seed keeps the page the same on every run."""
 import random
 from ezdxf.render import hatching
 st = random.getstate(); random.seed(0)
 try: return list(hatching.hatch_entity(h))
 finally: random.setstate(st)


def _block_sources(m, ves, blk):
 """Pair the exploded hatches and nested INSERTs of an INSERT with their block definitions (contract 7.2).
 m maps this block's definition coordinates to WCS and is multiplied level by level from the definitions' own insert matrices,
 so a non-uniform scale over a rotated nested block keeps its shear. Pattern lines are computed in the definition and moved by m:
 ezdxf's exploded hatch re-applies the pattern scale and angle, turns a mirror into a rotation, and an exploded nested INSERT
 is rebuilt from scale and rotation, which cannot hold a shear."""
 kinds = ('HATCH', 'INSERT')
 vs = [v for v in ves if v.dxftype() in kinds]
 ds = [x for x in blk if x.dxftype() in kinds] if vs and blk is not None else []
 if not vs or len(ds) != len(vs) or any(a.dxftype() != b.dxftype() for a, b in zip(vs, ds)): return {}
 from ezdxf.math import Matrix44
 out = {}
 for v, s in zip(vs, ds):
  if v.dxftype() == 'HATCH': out[id(v)] = (s, m)
  else:
   try: out[id(v)] = (None, Matrix44.chain(s.matrix44(), m))
   except Exception: pass
 return out


def _anchor(e):
 for k in ('start', 'insert', 'center', 'location'):
  if e.dxf.hasattr(k):
   v = e.dxf.get(k); return (v[0], v[1])
 try:
  p = next(iter(e.vertices_in_wcs())) if hasattr(e, 'vertices_in_wcs') else None
  if p is not None: return (p[0], p[1])
 except Exception: pass
 return None


class _Walker:
 """Turns one top-level entity into page primitives (layer, item) plus not-shown and dropped-layer tallies."""

 def __init__(self, dw, cfg, object_handles, unit_mm):
  self.dw, self.cfg, self.handles = dw, cfg, object_handles
  self.flat = cfg['flatten_mm'] / unit_mm
  self.large_layers = compile_layers(cfg['large_block_layers'])
  self.large_points = cfg['large_block_points']
  self.hsrc = {}

 def classify(self, e):
  if e.dxftype() != 'INSERT': return 'top_level'
  if str(e.dxf.handle).upper() in self.handles: return 'object_symbol'
  return 'large' if self.dw.block_size(e.dxf.name) > self.large_points else 'small_block'

 def run(self, e):
  self.out, self.not_shown, self.dropped = [], [], Counter()
  self.cls = self.classify(e)
  if e.dxftype() == 'INSERT' and self.dw.affine_loss(e):
   from ezdxf.math import Matrix44
   self._walk_affine(e, Matrix44(), e.dxf.layer, 7, 0)
  else: self._walk(e, e.dxf.layer, 7, 0)
  return self.cls, self.out, self.not_shown, self.dropped

 def _skip(self, kind, e):
  self.not_shown.append((kind, _anchor(e)))

 def _skip_affine(self, kind, e, m):
  p = _anchor(e)
  if p is not None:
   p = m.transform((*p, 0)); p = (p.x, p.y)
  self.not_shown.append((kind, p))

 def _walk_affine(self, e, m, eff_layer, byblock, depth):
  """Walk original definitions without rebuilding an INSERT from rotation and scale."""
  from ezdxf.math import Matrix44
  t = e.dxftype(); lay = e.dxf.layer if e.dxf.layer != '0' else eff_layer
  if self.dw.hidden(lay): return
  if self.cls == 'large' and t not in ('INSERT', 'DIMENSION') and not (self.large_layers and self.large_layers.search(lay)):
   self.dropped[lay] += 1; self._skip_affine('other_layers', e, m); return
  c = e.dxf.get('color', 256)
  bb = byblock if c == 0 else (c if c != 256 else self.dw.layer_aci(lay))
  if t in ('INSERT', 'DIMENSION'):
   if depth >= MAX_DEPTH: self._skip_affine('proxy', e, m); return
   if t == 'INSERT':
    # Attached attributes already use their INSERT's parent coordinates.
    for a in e.attribs:
     if not a.is_invisible: self._walk_affine(a, m, lay, bb, depth + 1)
    blk = self.dw.doc.blocks.get(e.dxf.name)
    if blk is None: self._skip_affine('xref_missing', e, m); return
    try: child_m = Matrix44.chain(e.matrix44(), m)
    except Exception: self._skip_affine('proxy', e, m); return
    for child in blk: self._walk_affine(child, child_m, lay, bb, depth + 1)
   else:
    try: ves = list(e.virtual_entities())
    except Exception: self._skip_affine('proxy', e, m); return
    for child in ves: self._walk_affine(child, m, lay, bb, depth + 1)
   return
  if t in SKIP_SILENT: return
  if t in UNSHOWN_TYPES: self._skip_affine(UNSHOWN_TYPES[t], e, m); return
  color = self.dw.color(e, lay, byblock)
  if t in ('TEXT', 'ATTRIB', 'MTEXT'):
   start = len(self.out)
   try: self._text_affine(e, lay, color, m)
   except Exception:
    del self.out[start:]; self._skip_affine('proxy', e, m)
   return
  if t == 'HATCH' and not e.dxf.get('solid_fill', 0):
   if self.cls == 'large': self._skip_affine('pattern_hatch', e, m); return
   self._pattern(e, lay, color, m); return
  self._geometry(e, t, lay, color, m)

 def _text_affine(self, e, lay, color, m):
  start = len(self.out)
  self._text(e, lay, color)
  for _, item in self.out[start:]:
   a = math.radians(item.get('rotation', 0)); c, s = math.cos(a), math.sin(a)
   is_mtext = e.dxftype() == 'MTEXT'
   width = 1 if is_mtext else e.dxf.get('width', 1)
   skew = 0 if is_mtext else math.tan(math.radians(e.dxf.get('oblique', 0)))
   flags = 0 if is_mtext else e.dxf.get('text_generation_flag', 0)
   bx, by = width * c, width * s
   ux, uy = skew * c - s, skew * s + c
   if flags & 2: bx, by = -bx, -by
   if flags & 4: ux, uy = -ux, -uy
   # TEXT/ATTRIB use OCS; MTEXT insertion and direction use WCS.
   if is_mtext:
    ucs = e.ucs()
    dy = -s * (item['point'][0] - e.dxf.insert.x) + c * (item['point'][1] - e.dxf.insert.y)
    p, b, u = ucs.origin + ucs.uy * dy, ucs.ux, ucs.uy
   else:
    ocs = e.ocs()
    ap = e.dxf.align_point if (e.dxf.get('halign', 0) or e.dxf.get('valign', 0)) and e.dxf.hasattr('align_point') else e.dxf.insert
    p, b, u = ocs.to_wcs((*item['point'], ap.z)), ocs.to_wcs((bx, by, 0)), ocs.to_wcs((ux, uy, 0))
   p, b, u = m.transform(p), m.transform_direction(b), m.transform_direction(u)
   size = math.hypot(b.x, b.y) * math.hypot(u.x, u.y)
   if not math.isfinite(size) or size <= 0 or abs(b.x * u.y - b.y * u.x) <= 1e-12 * size:
    raise ValueError('文字变换在图纸XY中退化')
   item['point'] = [p.x, p.y]
   item['text_axes'] = [[b.x, b.y], [u.x, u.y]]
   item['rotation'] = 0
   item.pop('width_factor', None)

 def _walk(self, e, eff_layer, byblock, depth):
  t = e.dxftype(); lay = e.dxf.layer
  if lay == '0': lay = eff_layer
  if self.dw.hidden(lay): return
  if self.cls == 'large' and t not in ('INSERT', 'DIMENSION') and not (self.large_layers and self.large_layers.search(lay)):
   self.dropped[lay] += 1; self._skip('other_layers', e); return
  c = e.dxf.get('color', 256)
  bb = byblock if c == 0 else (c if c != 256 else self.dw.layer_aci(lay))
  if t in ('INSERT', 'DIMENSION'):
   if depth >= MAX_DEPTH: return
   if t == 'INSERT':
    for a in e.attribs:
     if a.is_invisible: continue
     alay = a.dxf.layer if a.dxf.layer != '0' else lay
     if self.dw.hidden(alay): continue
     if self.cls == 'large' and not (self.large_layers and self.large_layers.search(alay)): self.dropped[alay] += 1; self._skip('other_layers', a); continue
     self._text(a, alay, self.dw.color(a, alay, bb))
    if self.dw.doc.blocks.get(e.dxf.name) is None: self._skip('xref_missing', e); return
   try: ves = list(e.virtual_entities())
   except Exception: self._skip('proxy', e); return
   src = {}
   if t == 'INSERT':
    m = self.hsrc[id(e)][1] if id(e) in self.hsrc else None   # a nested INSERT carries the product of the matrices above it
    if m is None:
     try: m = e.matrix44()
     except Exception: m = None
    if m is not None: src = _block_sources(m, ves, self.dw.doc.blocks.get(e.dxf.name))
   self.hsrc.update(src)
   for ve in ves: self._walk(ve, lay, bb, depth + 1)
   for k in src: self.hsrc.pop(k, None)
   return
  if t in SKIP_SILENT: return
  if t in UNSHOWN_TYPES: self._skip(UNSHOWN_TYPES[t], e); return
  color = self.dw.color(e, lay, byblock)
  if t in ('TEXT', 'ATTRIB', 'MTEXT'): self._text(e, lay, color); return
  if t == 'HATCH' and not e.dxf.get('solid_fill', 0):
   if self.cls == 'large': self._skip('pattern_hatch', e); return
   self._pattern(e, lay, color); return
  self._geometry(e, t, lay, color)

 def _height(self, e, h):
  """Text height in drawing units; 0 means "use the text style's fixed height"; None when neither is known."""
  if h and h > 0: return h
  st = self.dw.doc.styles.get(e.dxf.get('style', 'Standard'))
  sh = st.dxf.get('height', 0) if st is not None else 0
  return sh if sh and sh > 0 else None

 def _text(self, e, lay, color):
  t = e.dxftype()
  if t == 'MTEXT':
   h = self._height(e, e.dxf.get('char_height', 0)); ap = e.dxf.get('attachment_point', 1)
   try: lines = e.plain_text().split('\n')
   except Exception: lines = [e.text]
   rot = e.get_rotation() if hasattr(e, 'get_rotation') else e.dxf.get('rotation', 0)
   a = ['left', 'center', 'right'][(ap - 1) % 3]; v = ['top', 'middle', 'bottom'][(ap - 1) // 3]
   step = (h or 0) * 1.667 * e.dxf.get('line_spacing_factor', 1); n = len(lines)
   off0 = {'top': 0, 'middle': (n - 1) * step / 2, 'bottom': (n - 1) * step}[v]
   ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot)); ins = e.dxf.insert
   for k, s in enumerate(lines):
    s = s.strip()
    if s:
     dy = off0 - k * step
     item = {'kind': 'text', 'point': [ins.x - sa * dy, ins.y + ca * dy], 'text': s, 'height': h, 'rotation': rot, 'align': a, 'baseline': v, 'color': color}
     if h is None: del item['height']
     self.out.append((lay, item))
   return
  try: s = e.plain_text().strip()
  except Exception: s = (e.dxf.text or '').strip()
  if not s: return
  ha, va = e.dxf.get('halign', 0), e.dxf.get('valign', 0)
  p = e.dxf.align_point if (ha or va) and e.dxf.hasattr('align_point') else e.dxf.insert
  h = self._height(e, e.dxf.get('height', 0))
  item = {'kind': 'text', 'point': [p[0], p[1]], 'text': s, 'height': h, 'rotation': e.dxf.get('rotation', 0),
          'align': ALIGN.get(ha, 'left'), 'baseline': 'middle' if ha == 4 else VALIGN.get(va, 'alphabetic'), 'color': color}
  if h is None: del item['height']
  wf = e.dxf.get('width', 1)
  if wf > 0 and abs(wf - 1) > 0.05: item['width_factor'] = wf
  self.out.append((lay, item))

 def _pattern(self, e, lay, color, transform=None):
  src = (e, transform) if transform is not None else self.hsrc.get(id(e))
  try:
   segs = ((src[1].transform(a), src[1].transform(b)) for a, b in hatch_lines(src[0])) if src and src[0] is not None else hatch_lines(e)
   for a, b in segs:
    self.out.append((lay, {'kind': 'line', 'points': [[a.x, a.y], [b.x, b.y]], 'color': color}))
  except Exception:
   if transform is not None: self._skip_affine('pattern_hatch', e, transform)
   else: self._skip('pattern_hatch', e)

 def _geometry(self, e, t, lay, color, transform=None):
  from ezdxf import path as ezpath
  fill = t in ('SOLID', 'TRACE', '3DFACE') or t == 'HATCH'
  try: paths = ezpath.from_hatch(e) if t == 'HATCH' else [ezpath.make_path(e)]
  except Exception:
   if transform is not None: self._skip_affine('proxy', e, transform)
   else: self._skip('proxy', e)
   return
  dash = None if fill else self.dw.dash(e, lay)
  for p in paths:
   if transform is not None: p = p.transform(transform)
   for sp in p.sub_paths():
    pts = [[v.x, v.y] for v in sp.flattening(self.flat)]
    if len(pts) < 2: continue
    item = {'kind': 'polygon', 'points': pts, 'color': color, 'fill': color} if fill and len(pts) >= 3 else {'kind': 'line', 'points': pts, 'color': color}
    if dash: item['dash'] = dash
    self.out.append((lay, item))


def _round_item(it, dx, dy, nd):
 r = (lambda v: int(round(v))) if nd == 0 else (lambda v: round(v, nd))
 o = dict(it)
 if it['kind'] == 'text':
  o['point'] = [r(it['point'][0] + dx), r(it['point'][1] + dy)]
  # Four significant digits: metre drawings have heights like 0.0035 that two decimals would turn into 0.
  if 'height' in o: o['height'] = float(f"{it['height']:.4g}")
  o['rotation'] = round(it['rotation'], 2)
  if o['rotation'] == 0: o.pop('rotation')
  if 'width_factor' in o: o['width_factor'] = round(o['width_factor'], 2)
 else:
  o['points'] = [[r(x + dx), r(y + dy)] for x, y in it['points']]
  if 'dash' in o: o['dash'] = [float(f'{v:.4g}') for v in o['dash']]
 return o


def _inside(p, b): return b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]


def _label(insert):
 vals = [(a.dxf.text or '').strip() for a in insert.attribs]
 return next((v for v in vals if v), '')


def build_dxf_backgrounds(dxf_path, expected_sha256, targets, object_handles, unit_mm, precision, symbol_notes=None):
 """Fill each target's items, restoration, dropped layers and symbol reconciliation from one DXF.

 targets: [{'scene_id', 'sheet_bounds', 'dx', 'dy', 'cfg', 'include', 'exclude', 'symbol_layers', 'items': [], 'stats': Counter()}]
 cfg holds large_block_points, large_block_layers, flatten_mm, duplicate_tolerance_mm; include/exclude are compiled regex lists.
 """
 got = sha256_file(dxf_path)
 if got.lower() != str(expected_sha256).lower():
  raise DxfBackgroundError(f'交换 DXF 哈希与取证清单不符：{dxf_path}（实际 {got[:12]}…，清单 {str(expected_sha256)[:12]}…）')
 dw = _Drawing(dxf_path)
 object_handles = {str(h).upper() for h in object_handles}   # handles compare case-blind, like the symbol notes
 from ezdxf import bbox
 cache = bbox.Cache()
 walkers = {}
 for t in targets:
  sig = json.dumps(t['cfg'], sort_keys=True, ensure_ascii=False)
  t['_walker'] = walkers.setdefault(sig, _Walker(dw, t['cfg'], object_handles, unit_mm))
  t['restoration'] = Counter(); t['dropped_layers'] = Counter(); t['_symbols'] = []
 inserts = []
 for e in dw.msp:
  # The library bbox follows the same lossy nested explosion. Defer affected blocks
  # to the exact primitive filter below, otherwise a correct endpoint can be discarded.
  if e.dxftype() == 'INSERT' and dw.affine_loss(e): hit = targets
  else:
   try: ext = bbox.extents([e], cache=cache, fast=True)
   except Exception: continue
   if not ext.has_data: continue
   hit = [t for t in targets if not (ext.extmax.x < t['sheet_bounds'][0] or ext.extmin.x > t['sheet_bounds'][2] or ext.extmax.y < t['sheet_bounds'][1] or ext.extmin.y > t['sheet_bounds'][3])]
  if not hit: continue
  if e.dxftype() == 'INSERT':
   ip = e.dxf.insert
   for t in hit:
    if _inside((ip.x, ip.y), t['sheet_bounds']): t['_symbols'].append((e.dxf.name, str(e.dxf.handle).upper(), e.dxf.layer, (ip.x, ip.y), _label(e), dw.hidden(e.dxf.layer)))
   inserts.append(e)
  done = {}
  for t in hit:
   w = t['_walker']
   if id(w) not in done: done[id(w)] = w.run(e)
   cls, out, not_shown, dropped = done[id(w)]
   b, inc, exc = t['sheet_bounds'], t['include'], t['exclude']
   for lay, it in out:
    if inc and not any(r.search(lay) for r in inc): continue
    if exc and any(r.search(lay) for r in exc): continue
    pts = it['points'] if it['kind'] != 'text' else [it['point']]
    if not any(_inside(p, b) for p in pts): continue
    t['items'].append(_round_item(it, t['dx'], t['dy'], precision))
    t['stats']['background_items'] += 1; t['stats']['background_points'] += len(pts); t['stats']['points_' + cls] += len(pts)
   for kind, p in not_shown:
    if p is None or _inside(p, b): t['restoration'][kind] += 1
   for lay, n in dropped.items(): t['dropped_layers'][lay] += n
 # Symbol reconciliation (contract 7.5): blocks the object tables point at, plus blocks on the declared symbol layers.
 # Symbol notes (contract 7.5.1) explain a symbol before it may count as duplicate or uncounted; without notes the output is unchanged.
 notes, used = symbol_notes or {}, set()
 # Symbols on off, frozen or non-plotting layers are not drawn, so they stay out of the sheet count and are listed apart.
 counted_blocks = {e.dxf.name for e in inserts if str(e.dxf.handle).upper() in object_handles}
 for t in targets:
  sym = t['symbol_layers']; tol = t['cfg']['duplicate_tolerance_mm'] / unit_mm
  rows = defaultdict(list)
  for name, h, lay, p, lab, hid in t['_symbols']:
   if name in counted_blocks or (sym and any(r.search(lay) for r in sym)): rows[name].append((h, lay, p, lab, hid))
  out = []
  r = (lambda v: int(round(v))) if precision == 0 else (lambda v: round(v, precision))
  pts = lambda ps: [[r(x + t['dx']), r(y + t['dy'])] for x, y in ps]
  for name, allrecs in sorted(rows.items()):
   recs = [x[:4] for x in allrecs if not x[4]]; hidden = [x[:4] for x in allrecs if x[4]]
   seen, dup_pts, unc_pts, counted = [], [], [], 0
   expl, why = {k: [] for k in NOTE_KINDS}, {k: Counter() for k in NOTE_KINDS}
   for h, lay, p, lab in recs:
    nt = notes.get(h)
    if nt: used.add(h)
    if nt and nt[0] == 'removed_overlap': expl[nt[0]].append(p); why[nt[0]][nt[1]] += 1; continue
    # Only symbols without a note are candidates for "same position", so the result does not depend on drawing order.
    if not nt:
     if any(math.hypot(p[0] - q[0], p[1] - q[1]) < tol for q in seen): dup_pts.append(p)
     seen.append(p)
    if h in object_handles: counted += 1
    elif nt: expl[nt[0]].append(p); why[nt[0]][nt[1]] += 1
    else: unc_pts.append(p)
   labels = Counter(lab for *_, lab in (recs or hidden) if lab)
   row = {'block': name, 'label': labels.most_common(1)[0][0] if labels else '', 'layer': Counter(l for _, l, _, _ in (recs or hidden)).most_common(1)[0][0],
          'on_sheet': len(recs), 'counted': counted, 'uncounted': len(unc_pts), 'duplicates': len(dup_pts),
          'uncounted_points': pts(unc_pts), 'duplicate_points': pts(dup_pts)}
   if notes:
    for k in NOTE_KINDS: row[k] = len(expl[k]); row[k + '_points'] = pts(expl[k])
    row['notes'] = {k: [n for n, _ in why[k].most_common(5)] for k in NOTE_KINDS if why[k]}
   if hidden:
    row['hidden'] = len(hidden); row['hidden_points'] = pts([p for _, _, p, _ in hidden])
    row['hidden_counted'] = sum(1 for h, *_ in hidden if h in object_handles)
   out.append(row)
  t['symbol_reconciliation'] = out
  for k in ('_walker', '_symbols'): t.pop(k, None)
 return {'sha256': got, 'notes_used': used}


def restoration_summary(targets):
 """Merge the targets of one scene into the scene's restoration record (contract 7.4)."""
 c, d = Counter(), Counter()
 for t in targets:
  c.update(t.get('restoration') or {})
  for lay, n in (t.get('dropped_layers') or {}).items(): d[short_layer(lay)] += n
 ns = {k: c.get(k, 0) for k in NOT_SHOWN}
 out = {'source': 'dxf', 'not_shown_total': sum(ns.values()), 'not_shown': ns}
 if d: out['other_layers_top'] = [[k, n] for k, n in d.most_common(8)]
 return out
