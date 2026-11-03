"""The Python side of the parity scripts (phase 3, D8): the same action list tools/js-harness/lib/script.mjs runs
inside the original, run here on hairmesh.core.edit. Both sides must produce the same ids (UID order), names
(nextNumber / counters) and, after the scene settles, the same curves.

Drawing (`draw` ops) needs the cursor rays: they come from the harness run (draw.json, one record per click with
the ray, the camera direction and the pivot the original used), passed in as `draws`. The 3.4 ops (surface, weld,
unweld, split, merge, curveBetween, curveThrough, rotateJunction, optimize) run on hairmesh.core.weld; the 3.5 ops
(walls, fillHole, average, segsToCurves, wallProp) on hairmesh.core.walls; the 3.6 ops (newGroup, select, moveItems,
rename, groupProp, tick, setConv, matchStep, detach, deleteItems) on hairmesh.core.groups, each followed by what the
original's re-rendered panels do to the model (groups.panelsRendered: the selected group's and the first ticked
group's conv filled). The 3.7b op `migrate` runs hairmesh.core.migrate.migrateFile (`preset`, which writes a preset
file in the harness, does nothing here). The 4.10 ops `symmetry` and `union` run hairmesh.core.symmetry /
hairmesh.core.union; each is recorded as the harness records it (symmetry.json / union.json: the toast, the
selection after, the items made and the project file right after the op)."""
import os

from hairmesh.core import brush as BR, conversion as CONV, crown as CR, curves as CM, duplicate as DUP, edges as ED, edit, groups as G, migrate as MG, rows as RW, scissors as SC, symmetry as SYM, tube as TB, walls as WL, weld as W
from hairmesh.core.hairmesh_io import defaultStep, project_to_dict
from hairmesh.core import model as M
from hairmesh.core.density import TIP_DEFAULTS
from hairmesh.core.js import js_div, js_hypot
from hairmesh.core.raycast import Ray
from hairmesh.core.vec3 import V3, Vec3


def js_projector(view):
    """toScreen (5656) with the camera the harness recorded (cut.json: matrixWorldInverse, projectionMatrix, W, H):
    Vector3.project = applyMatrix4 twice, as three.js computes it"""
    a = view['mwi']; b = view['pm']; Wd = view['W']; Hd = view['H']

    def apply(e, x, y, z):
        w = js_div(1, e[3] * x + e[7] * y + e[11] * z + e[15])
        return ((e[0] * x + e[4] * y + e[8] * z + e[12]) * w, (e[1] * x + e[5] * y + e[9] * z + e[13]) * w, (e[2] * x + e[6] * y + e[10] * z + e[14]) * w)

    def screen(p):
        x, y, z = apply(a, p.x, p.y, p.z); x, y, z = apply(b, x, y, z)
        return ((x + 1) / 2 * Wd, (1 - y) / 2 * Hd, z)
    return screen


def run_cut(P, a, rec, shown):
    """the `cut` op: the stroke over the selected curves (scissorHits), then the pie's item (cutCurves). Returns
    (hits as {c, seg, u} by id, the toast)"""
    hits = SC.scissorHits(P, *a['stroke'], shown, js_projector(rec['view']))
    msg = None
    if hits:
        _n, msg, _ids = SC.cutCurves(P, hits, a.get('mode') or 'split')
    return [{'c': h['c'].id, 'seg': h['seg'], 'u': h['u']} for h in hits], msg


def recorded_grid(rec):
    """the wall's grid the original used (cut.json), as scissors.wallGrid gives it"""
    g = rec['grid']
    return SC.Grid(g['R'], g['M'], [V3(*p) for p in g['pos']], g['dr'], g['dt'], g['width'])


def run_surf_cut(P, a, rec, sf, grid=None):
    """the `surfCut` op, event by event with the hits the harness recorded (the ray on the wall's mesh, the shown wall
    first under the cursor); surfCutView wherever the original's drawSurfCut runs (it takes anchor ids). Returns
    (per event {toast, state}, the new curve or None, the final toast)"""
    CM.rebuild_dirty(P)
    T = RW.buildRowTables(P).T
    cut, msg = SC.startSurfCut(P, sf, T)
    if cut is not None and grid is not None:
        cut.G = grid                                                # (the original's grid: what the port cannot build yet, the `para` rows)
    if cut is not None:
        SC.surfCutView(P, cut)
    screen = js_projector(rec['view'])
    drag = None; out = []
    for ev, r in zip(a.get('events') or [], rec['events']):
        toast = None
        hit = V3(*r['hit']) if r.get('hit') else None
        if cut is not None:
            if ev.get('down'):
                res = SC.surfCutDown(P, cut, ev['down'], ev.get('button') or 0, bool(ev.get('double')), screen, hit, r.get('wall'))
                if res is not None:
                    if 'toast' in res:
                        toast = res['toast']
                    elif 'switch' in res:
                        cut, _m = SC.startSurfCut(P, P.items[res['switch']], T)
                        if cut is not None:
                            SC.surfCutView(P, cut)
                    elif 'drag' in res:
                        drag = res['drag']
                        if res.get('redraw'):
                            SC.surfCutView(P, cut)
                    else:
                        SC.surfCutView(P, cut)
            elif ev.get('move'):
                if drag is not None and SC.surfCutPtMove(P, cut, drag, ev['move'], screen, hit):
                    SC.surfCutView(P, cut)
            elif ev.get('hover'):
                if SC.surfCutHover(P, cut, ev['hover'], screen, hit):
                    SC.surfCutView(P, cut)
            elif ev.get('back') and cut.start and not cut.end:
                SC.removeLast(cut); SC.surfCutView(P, cut)
        state = None if cut is None else {'sf': cut.sf.id, 'start': bool(cut.start), 'pts': [[q['r'], q['t']] for q in cut.pts], 'end': bool(cut.end),
                                          'err': cut.err, 'mids': len(cut.build['mids']) if cut.build else None}
        out.append({'toast': toast, 'state': state})
    c = None; msg = None
    if a.get('apply') and cut is not None:
        c, msg, _touched = SC.applySurfCut(P, cut)
    return out, c, msg


def new_keys():
    """the keys of a run (see run_script): passed in, whoever feeds it `actions` lazily sees them as they grow (the fuzz, tests/helpers/fuzz.py)"""
    return {'groups': {}, 'curves': {}, 'anchors': {}, 'surfaces': {}, 'tubes': {}, 'draws': [], 'migrations': [], 'cuts': [], 'surfCuts': [], 'gizmos': [], 'duplicates': [], 'guides': {}, 'guideRecs': [], 'tubeRecs': [], 'crowns': {}, 'crownRecs': [], 'symRecs': [], 'unionRecs': [], 'extras': {}, 'extraRecs': [], 'topo': {}, 'topoRecs': [], 'topoDefaults': {}, 'paintRecs': [], 'edgeRecs': [], 'brushRecs': []}


def run_script(P, actions, draws=None, base_dir=None, cuts=None, recorded_grids=False, guides=None, crowns=None, paints=None, edges=None, brushes=None, K=None):
    """Runs `actions` (any iterable, read one at a time) on project P. `draws`: the harness's draw.json records (needed for `draw` ops). `base_dir`:
    where relative files of `head` ops are. Returns the keys: {'groups': {key: item}, 'curves': {...},
    'anchors': {...}, 'draws': [{'action', 'key', 'curve' (id or None), 'anchors': [ids]}],
    'migrations': [{'action', 'file', 'name', 'ok', 'toast', 'sel', 'fit'}] (as the harness's migrate.json)}.
    `paints`: the harness's paint.json (the view each `paintMask` / `paintTip` stroke was painted in). `edges`: its
    edges.json (the view, the pixel and the polygon hit of each press of the `edgeClick` ops). `brushes`: its brush.json (the view, rays and hits of each `brush` stroke)."""
    K = new_keys() if K is None else K
    paint_recs = {r['action']: r for r in (paints or [])}
    edge_recs = {r['action']: r for r in (edges or [])}
    esel = ED.EdgeSel(); ememo = [None]                             # S.edges, S.edgeClick
    brush_recs = {r['action']: r for r in (brushes or [])}
    brush_mode = ['screen']                                         # S.brushMode (701)
    mbrush = {'on': False, 'kind': 'mask', **TB.MASK_BRUSH}          # S.maskBrush (6578), kept between strokes as the original keeps it
    brush_rh = [BR.BRUSH_R, BR.BRUSH_HARD]                          # S.brushR, S.brushHard (701)
    tip_strength = [1]                                              # S.tipBrush.strength (6583)
    guide_recs = {r['action']: r for r in (guides or [])}
    crown_recs = {r['action']: r for r in (crowns or [])}
    from hairmesh.core.selection import Selection
    gsel = Selection([], [])                                        # S.sel / S.asel as the guide ops see it
    by_action = {d['action']: d for d in (draws or [])}
    cut_recs = {r['action']: r for r in [*((cuts or {}).get('cuts') or []), *((cuts or {}).get('surfCuts') or [])]}

    def need(what, key):
        if key not in K[what]:
            raise KeyError(f'script: unknown {what[:-1]} "{key}"')
        return K[what][key]

    def gid(key):
        return None if key is None else need('groups', key).id

    def anchor_of(key):
        return need('anchors', key)

    def curve_of(key):
        return need('curves', key)

    def keep_curve(key, c):
        if key is not None and c is not None:
            K['curves'][key] = c

    def item_ids(a):
        return [*(gid(k) for k in a.get('groups') or []), *(curve_of(k).id for k in a.get('curves') or []), *(need('surfaces', k).id for k in a.get('surfaces') or []),
                *(need('guides', k).id for k in a.get('guides') or [])]   # (guides: 4.10)

    def xsel(a):
        """the `extra` / `paintMask` selection: modifiers, then curves, then extras (modTubes reads S.sel in this order)"""
        return [*(need('tubes', k).id for k in a.get('tubes') or []), *(curve_of(k).id for k in a.get('curves') or []), *(need('extras', k).id for k in a.get('extras') or [])]

    def any_item(key):
        for what in ('groups', 'curves', 'surfaces'):
            if key in K[what]:
                return K[what][key]
        raise KeyError(f'script: unknown item "{key}"')
    sel_last = [None]                                               # S.sel's last item (renderProps shows it): what the 3.6 ops' panels fill

    for i, a in enumerate(actions):
        op = a.get('op')
        if op == 'group':
            g = edit.addItem(P, 'group', gid(a.get('parent')), {'name': a['name']} if a.get('name') is not None else None)
            for k in ('conv', 'sym', 'labels'):
                if a.get(k):
                    setattr(g, k, dict(a[k]))
            for k in ('color', 'guides', 'expanded', 'visible', 'q'):
                if k in a:
                    setattr(g, k, a[k])
            if a.get('key') is not None:
                K['groups'][a['key']] = g
        elif op == 'curve':
            c = edit.makeCurve(P, gid(a.get('parent')))
            if a.get('name') is not None:
                c.name = a['name']
            smooth = a.get('smooth'); keys = a.get('anchorKeys')
            for j, p in enumerate(a.get('points') or []):
                # a point given as a string is the key of an existing anchor: the curves share it (welded)
                an = need('anchors', p) if isinstance(p, str) else edit.newAnchor(P, Vec3(p[0], p[1], p[2]))
                if smooth and smooth[j] is not None:
                    an.smooth = smooth[j]
                c.anchors.append(an)
                if keys and keys[j] is not None:
                    K['anchors'][keys[j]] = an
            for k in ('closed', 'round', 'fair', 'junc', 'visible'):
                if k in a:
                    setattr(c, k, a[k])
            if a.get('key') is not None:
                K['curves'][a['key']] = c
        elif op == 'settings':
            for k, v in (a.get('set') or {}).items():
                setattr(P.settings, k, v)
        elif op == 'convert':
            P.settings.convChecked = [gid(k) for k in (a.get('groups') or [])]
        elif op == 'startConversion':                               # the Convert button (3.7): the frame's curves, the tables, startConversion
            CM.rebuild_dirty(P)
            CONV.startConversion(P, lambda: RW.buildRowTables(P).T, CR.crownData)
        elif op == 'confirmConversion':
            CONV.confirmConversion(P)
        elif op == 'cancelConversion':
            CONV.cancelConversion(P)
        elif op == 'head':
            file = os.path.join(base_dir or os.getcwd(), a['obj'])
            with open(file, encoding='utf-8') as f:
                text = f.read()
            edit.set_head(P, os.path.basename(file), {'ext': 'obj', 'text': text})
        elif op in ('camera', 'pane'):
            pass                                                    # the rays and the pivot of every click come with `draws`
        elif op == 'draw':
            rec = by_action.get(i)
            if rec is None:
                raise ValueError(f'script: action {i} (draw) needs the rays recorded by the harness (draw.json)')
            drawing = edit.DrawState()
            parent = gid(a.get('parent'))
            edit.touchGroup(P, parent)                              # the harness selects the group first: its panel fills conv (8025)
            anchors = []
            for click in rec['clicks']:
                ray = Ray(V3(*click['origin']), V3(*click['direction']))
                an = edit.addDrawPoint(P, drawing, ray, V3(*click['camDir']), V3(*click['pivot']), parent=parent)
                if a.get('anchorKeys') and a['anchorKeys'][len(anchors)] is not None and an is not None:
                    K['anchors'][a['anchorKeys'][len(anchors)]] = an
                anchors.append(an.id if an is not None else None)
            c = edit.finishDrawing(P, drawing)
            K['draws'].append({'action': i, 'key': a.get('key'), 'curve': c.id if c is not None else None, 'anchors': anchors})
            if a.get('key') is not None and c is not None:
                K['curves'][a['key']] = c
        # ---- phase 3.3: anchors (the data part of the Select tool) ----
        elif op == 'move':
            an = need('anchors', a['anchor'])
            if a.get('to'):
                an.pos.fromArray(a['to'])
            if a.get('by'):
                an.pos.add(V3(*a['by']))
            CM.markAnchorsDirty(P, [an]); P.rev += 1
        elif op == 'smooth':
            edit.setSmooth(P, [need('anchors', k) for k in a['anchors']], a['value'])
        elif op == 'set':
            cs = [need('curves', k) for k in a['curves']]
            if 'round' in a:
                edit.setCurveProp(P, cs, 'round', a['round'])
            if 'fair' in a:
                edit.setCurveProp(P, cs, 'fair', a['fair'])
        elif op == 'insert':
            c = need('curves', a['curve'])
            CM.rebuild_dirty(P)                                     # the samples a frame would have made
            s0 = c.samples[a['i']]; s1 = c.samples[a['i'] + 1]
            an = edit.insertAnchorAt(P, c, s0.seg, s0.p.clone().lerp(s1.p, a['t']))
            if a.get('key') is not None:
                K['anchors'][a['key']] = an
        elif op == 'deleteAnchors':
            edit.deleteAnchors(P, [need('anchors', k) for k in a['anchors']], bool(a.get('keep')))
        elif op == 'recalc':
            edit.recalcHandles(P, [need('anchors', k) for k in a['anchors']])
        elif op == 'reverse':
            edit.reverseCurves(P, [need('curves', k) for k in a['curves']])
        elif op == 'even':
            CM.evenAnchors(P, [need('curves', k) for k in a['curves']], a['value'])
        # ---- phase 3.4: welding, Y junctions, the anchor pie (each op first rebuilds the dirty curves, as a frame would) ----
        elif op == 'surface':
            s = edit.addItem(P, 'surface', gid(a.get('parent')), {'a': curve_of(a['a']).id, 'b': curve_of(a['b']).id, 'dirty': True, 'p': {'segs': 1, 'bulge': 0, 'flip': False, **(a.get('p') or {})}})
            if a.get('key') is not None:
                K['surfaces'][a['key']] = s
        elif op == 'weld':
            CM.rebuild_dirty(P)
            x = anchor_of(a['a']); y = anchor_of(a['b']); mode = a.get('mode', 'weld')
            if mode == 'join':
                W.joinCurves(P, x, y)
            elif mode == 'reverse':
                choice = W.weld_choice(P, x, y)
                if choice[0] != 'startEnd':
                    raise ValueError(f'script: action {i}: not a start-meets-end pair')
                W.reverseThenWeld(P, x, y, choice[1], choice[2])
            else:
                W.weld(P, x, y)
        elif op == 'unweld':
            CM.rebuild_dirty(P)
            if W.unweldAnchor(P, anchor_of(a['anchor']), [curve_of(k).id for k in a['curves']]) is None:
                raise ValueError(f'script: action {i}: nothing to unweld')
        elif op == 'split':
            CM.rebuild_dirty(P)
            W.splitAtAnchor(P, anchor_of(a['anchor']))
        elif op == 'merge':
            CM.rebuild_dirty(P)
            W.mergeCoincident(P, [curve_of(k) for k in a['curves']], a.get('tol', P.S.scale * 1e-3))
        elif op == 'curveBetween':
            CM.rebuild_dirty(P)
            r = W.curveBetween(P, anchor_of(a['a']), anchor_of(a['b']), target_group=gid(a.get('parent')))
            keep_curve(a.get('key'), r[0] if r else None)
        elif op == 'curveThrough':
            CM.rebuild_dirty(P)
            keep_curve(a.get('key'), W.curveThroughAnchors(P, [anchor_of(k) for k in a['anchors']]))
        elif op == 'rotateJunction':
            CM.rebuild_dirty(P)
            x = anchor_of(a['anchor']); jr = W.junctionChoices(P, x)
            if a['choice'] == 'auto':
                it = next((j for j in jr if 'Automatic' in j[0]), None)
            else:
                it = jr[a['choice']] if a['choice'] < len(jr) else None
            if it is None:
                raise ValueError(f'script: action {i}: no junction choice {a["choice"]} ({" | ".join(j[0] for j in jr)})')
            W.setPass(P, x, it[1])
        elif op == 'optimize':
            CM.rebuild_dirty(P)
            W.optimizeAnchors(P, [curve_of(k) for k in a['curves']], set(anchor_of(k) for k in a['only']) if a.get('only') else None)
        # ---- phase 3.5: walls and locks (the curve pie's wall items, the surface panel) ----
        elif op == 'walls':
            CM.rebuild_dirty(P)
            made, _msg = WL.createSurfaces(P, [curve_of(k) for k in a['curves']], bool(a.get('closed')), bool(a.get('exact')))
            for k, s in zip(a.get('keys') or [], made):
                if k is not None:
                    K['surfaces'][k] = s
        elif op == 'fillHole':
            CM.rebuild_dirty(P)
            s, _corners, _msg = WL.fillHole(P, [curve_of(k) for k in a['curves']])
            if a.get('key') is not None and s is not None:
                K['surfaces'][a['key']] = s
        elif op == 'average':
            CM.rebuild_dirty(P)
            keep_curve(a.get('key'), WL.averageCurve(P, curve_of(a['a']), curve_of(a['b'])))
        elif op == 'segsToCurves':
            CM.rebuild_dirty(P)
            mc, ms, _msg = WL.segmentsToCurves(P, [need('surfaces', k) for k in a['surfaces']])
            for k, c in zip(a.get('keys') or [], mc):
                if k is not None:
                    K['curves'][k] = c
            for k, s in zip(a.get('surfaceKeys') or [], ms):
                if k is not None:
                    K['surfaces'][k] = s
        elif op == 'wallProp':
            s = need('surfaces', a['surface'])
            for k, v in (a.get('set') or {}).items():
                if k == 'b2on':
                    WL.setSecondSurface(P, s, v)
                elif k == 'clean':
                    WL.setClean(P, [need('surfaces', q) for q in (a.get('surfaces') or [a['surface']])], v)
                else:
                    WL.setWallProp(P, s, k, v)
        # ---- phase 3.6: groups and conversion parameters (the original re-renders the outliner and the panels after each) ----
        elif op == 'newGroup':
            g = G.createGroup(P, gid(a.get('parent')), a.get('name'))
            sel_last[0] = g.id
            if a.get('key') is not None:
                K['groups'][a['key']] = g
        elif op == 'select':
            ids = item_ids(a)
            gsel.items = list(ids); gsel.anchors = [anchor_of(k).id for k in a.get('anchors') or []]
            sel_last[0] = ids[len(ids) - 1] if ids else None
            G.panelsRendered(P, sel_last[0])
        elif op == 'moveItems':
            moved, msg = G.moveItems(P, item_ids(a), any_item(a['target']).id if a.get('target') is not None else None, a.get('zone', 'into'))
            if msg:
                raise ValueError(f'script: action {i}: {msg}')
            G.panelsRendered(P, sel_last[0])
        elif op == 'rename':
            it = need('groups', a['group']) if a.get('group') is not None else curve_of(a['curve']) if a.get('curve') is not None else need('surfaces', a['surface'])
            G.renameItem(P, it, a['name']); G.panelsRendered(P, sel_last[0])
        elif op == 'groupProp':
            g = need('groups', a['group'])
            if 'color' in a:
                G.setColor(P, g, a['color']); G.panelsRendered(P, sel_last[0])
            if 'guides' in a:
                G.setGuidesMode(P, g, a['guides']); G.panelsRendered(P, sel_last[0])
            if a.get('label'):
                G.setLabel(P, g, a['label'][0], a['label'][1]); G.panelsRendered(P, sel_last[0])
            if a.get('sym'):
                G.setSym(P, g, a['sym'].get('on'), a['sym'].get('axis')); G.panelsRendered(P, sel_last[0])   # symChanged: renderProps
        elif op == 'tick':
            G.tickGroup(P, need('groups', a['group']), bool(a['on']))
            t = G.convTargets(P)
            if t:
                G.groupConv(P, t[0])                                # syncConvUI (9097)
        elif op == 'setConv':
            G.setConv(P, a['k'], a['v'])
        elif op == 'matchStep':
            G.matchProjectStep(P, need('groups', a['group'])); G.panelsRendered(P, sel_last[0])
        elif op == 'detach':
            CM.rebuild_dirty(P)
            G.detachGroup(P, need('groups', a['group'])); G.panelsRendered(P, sel_last[0])
        elif op == 'deleteItems':
            for iid in item_ids(a):
                edit.deleteItem(P, iid)
            sel_last[0] = None
            G.panelsRendered(P, None)
        # ---- 3.7b: migration ----
        elif op == 'migrate':
            file = os.path.join(base_dir or os.getcwd(), a['file'])
            with open(file, encoding='utf-8') as f:
                text = f.read()
            name = a['name'] if a.get('name') is not None else os.path.basename(file)
            r = MG.migrateFile(P, text, name)
            fit = {'k': r.fit['k'], 'from': r.fit['from'].toArray(), 'to': r.fit['to'].toArray()} if r.fit else None
            if r.ok:
                sel_last[0] = r.sel[len(r.sel) - 1] if r.sel else None
                if a.get('key') is not None:
                    K['groups'][a['key']] = P.items[sel_last[0]]
            K['migrations'].append({'action': i, 'file': a['file'], 'name': name, 'ok': r.ok, 'toast': r.msg, 'sel': list(r.sel) if r.ok else None, 'fit': fit})
        # ---- 3.8a: the scissors ----
        elif op == 'cut':
            rec = cut_recs.get(i)
            if rec is None:
                raise ValueError(f'script: action {i} (cut) needs the view recorded by the harness (cut.json)')
            ids = [curve_of(k).id for k in a['curves']]
            CM.rebuild_dirty(P)
            RW.buildRowTables(P)                                        # (the frame the harness runs: rebuildSurfaces fills the groups' conv, groupConv)
            shown = {c.id for c in G.shownCurves(P, ids, [])[0]}       # (c.obj.visible: the frame after the selection)
            hits, msg = run_cut(P, a, rec, [P.items[x] for x in ids if x in shown])
            cut_some = bool(hits) and msg != SC.NOTHING_CUT
            sel = [x for x in ids if x in P.items] if cut_some else ids  # S.sel filtered when something was cut (cutCurves 5124)
            sel_last[0] = sel[len(sel) - 1] if sel else None
            K['cuts'].append({'action': i, 'hits': hits, 'toast': msg, 'sel': sel})
        elif op == 'surfCut':
            rec = cut_recs.get(i)
            if rec is None:
                raise ValueError(f'script: action {i} (surfCut) needs the clicks recorded by the harness (cut.json)')
            events, c, msg = run_surf_cut(P, a, rec, need('surfaces', a['surface']), recorded_grid(rec) if recorded_grids else None)
            if c is not None:
                keep_curve(a.get('key'), c); sel_last[0] = c.id
            K['surfCuts'].append({'action': i, 'events': events, 'toast': msg, 'curve': c.id if c is not None else None})
        elif op == 'key':                                           # a key for an item found by type and name (the last one): migrated items
            it = next((x for x in reversed(list(P.items.values())) if x.type == a['type'] and x.name == a['name']), None)
            if it is None:
                raise KeyError(f'script: action {i}: no {a["type"]} named {a["name"]!r}')
            K[{'group': 'groups', 'curve': 'curves', 'surface': 'surfaces'}[a['type']]][a['key']] = it
        elif op == 'preset':
            pass                                                    # (the harness writes the preset file; nothing changes in the scene)
        # ---- 3.8b: the anchor gizmo and Duplicate ----
        elif op == 'gizmo':
            CM.rebuild_dirty(P)
            space = a.get('space', 'world')
            if 'anchors' in a:                                      # (as lib/script.mjs: a.anchors !== undefined)
                st = edit.startGizmo(P, [anchor_of(k) for k in a['anchors']], a['mode'], space)
                gsel.items = []; gsel.anchors = [anchor_of(k).id for k in a['anchors']]   # (S.sel / S.asel as the harness leaves them: the crown ops read them, 4.4)
            else:                                                   # 3.8d: items (gizmoTargets' item branch, 4568)
                tg = edit.gizmoTargets(P, item_ids(a))
                st = edit.startGizmo(P, tg, a['mode'], space)
                gsel.items = list(item_ids(a)); gsel.anchors = []
            pos = st.pos.clone(); quat = st.quat.clone(); scale = V3(1, 1, 1)
            lock = {'x': False, 'y': False, 'z': False, **(a.get('lock') or {})}
            units = {'x': V3(1, 0, 0), 'y': V3(0, 1, 0), 'z': V3(0, 0, 1)}
            one = a['mode'] == 'translate' and len(st.list) == 1 and not st.items   # (S.asel.length === 1, 4677)
            for s in a.get('steps') or []:
                if s.get('by'):
                    pos = st.pos.clone().add(V3(*s['by']))
                if s.get('angle') is not None:
                    named = isinstance(s['axis'], str)
                    quat = edit.gizmoRotation(st.quat, units[s['axis']] if named else V3(*s['axis']), s['angle'], 'local' if space == 'local' and named else 'world')
                if s.get('scale'):
                    scale = V3(*s['scale'])
                edit.gizmoStep(P, st, edit.gizmoMatrix(st, pos, quat, scale, lock, bool(a.get('ctrl')), bool(a.get('shift'))))
                if one and a.get('shift'):
                    edit.gizmoSlide(P, st, defaultStep(P))
                edit.gizmoMoved(P, st)
            if st.meshes:                                           # the release (4646): the matrix baked into the converted meshes
                edit.bakeMeshes(P, st, edit.gizmoMatrix(st, pos, quat, scale, lock, bool(a.get('ctrl')), bool(a.get('shift'))))
            if a['mode'] == 'rotate':
                edit.rotateOwnAxes(P, st, quat)                     # the release (4653): the items' own axes turn with them
            K['gizmos'].append({'action': i, 'pivot': st.pos.toArray(), 'quat': st.quat.toArray()})
        elif op == 'duplicate':
            made, s_items, s_anchors = DUP.duplicateSelection(P, item_ids(a), [anchor_of(k).id for k in a.get('anchors') or []])
            if made is not None:                                    # ("Nothing to duplicate" leaves before onSelChange, 9907)
                sel_last[0] = s_items[len(s_items) - 1] if s_items and not s_anchors else None   # (with the copy's anchors selected the panel is the anchors', 8629)
                G.panelsRendered(P, sel_last[0])                    # onSelChange -> renderOutliner (9980)
            K['duplicates'].append({'action': i, 'toast': DUP.duplicateMessage(made), 'sel': s_items, 'asel': s_anchors})
        # ---- 4.2: guides (the views, hits and rays the harness recorded: guide.json) ----
        elif op in GUIDE_OPS:
            run_guide_op(P, i, a, guide_recs.get(i), K, gsel, curve_of, need, sel_last)
        elif op in CROWN_OPS:
            run_crown_op(P, i, a, crown_recs.get(i), K, gsel, need, sel_last)
        # ---- 4.5: the Profile modifier (lib/script.mjs `tube`, `convertTube`) ----
        elif op == 'tube':
            c = curve_of(a['curve'])
            CM.rebuild_dirty(P)                                     # (autoTubeRadius reads the samples: what a frame would have done)
            t = TB.newTube(P, c, a.get('profile') or a['profs'][0])
            if a.get('p'):
                t.p.update(a['p'])                                  # ({ r0: r, …, tipLen: 0.25, ...a.p }: the defaults' places kept)
            if a.get('profs'):
                t.profs = [[list(q) for q in Q] for Q in a['profs']]; t.profile = t.profs[0]
            if a.get('key') is not None:
                K['tubes'][a['key']] = t
        elif op == 'convertTube':
            settle_frame(P)                                         # the frame loop first (the curve's arc, the tube), as between two clicks
            made, msg = TB.convertTube(P, need('tubes', a['tube']))
            if made:
                if P.conv:                                          # (6809: if (S.conv) regenConversion())
                    CONV.regenConversion(P, RW.buildRowTables(P).T, CR.crownData)
                gsel.items = [e.id for e in made]; gsel.anchors = []   # (6808: S.sel = the edges, as the harness leaves it)
                sel_last[0] = made[len(made) - 1].id
                G.panelsRendered(P, sel_last[0])                    # onSelChange -> renderOutliner (6810)
            K['tubeRecs'].append({'action': i, 'toast': msg, 'sel': [e.id for e in made] if made else None})
        # ---- 4.7: the Extra modifiers (lib/script.mjs `extra`, `extraProp`) ----
        elif op == 'extra':
            CM.rebuild_dirty(P)                                     # (newExtra reads autoTubeRadius without rRef: the samples)
            ids = xsel(a)
            gsel.items = list(ids); gsel.anchors = []               # (selected, no panel rendered)
            sel_last[0] = ids[len(ids) - 1] if ids else None        # ([...S.sel].pop(): the last one selected now, even when nothing is made — none: undefined)
            before = set(P.items)
            made, msg = TB.addExtra(P, ids, a['kind'])
            if made:                                                # S.asel = [], S.sel = made, onSelChange (the last one's panel), commit
                gsel.items = [x.id for x in made]
                sel_last[0] = made[len(made) - 1].id
                G.panelsRendered(P, sel_last[0])
            for k, x in zip(a.get('keys') or [], made):
                K['extras'][k] = x
            K['extraRecs'].append({'action': i, 'op': op, 'toast': msg, 'sel': list(gsel.items), 'asel': [], 'made': [x for x in P.items if x not in before], 'project': project_to_dict(P)})
        elif op == 'paintMask':                                     # 4.7b: a stroke of the weight brush, in the view the harness painted it (paint.json)
            rec = paint_recs[i]
            settle_frame(P)                                         # (the harness's frame first: the arcs, the modifiers' displays — rebuildTube's ensureTubeRef)
            gsel.items = xsel(a); gsel.anchors = []
            mb = mbrush; mb.update(on=True, kind=a['kind'], mode=a.get('mode') or 'add'); mb.update(a.get('set') or {})   # (Object.assign(S.maskBrush, ...): it stays for the next stroke)
            if a.get('R') is not None:
                brush_rh[0] = a['R']
            if a.get('hard') is not None:
                brush_rh[1] = a['hard']
            Rb, hard = brush_rh
            screen = js_projector(rec['view']); paint = a.get('paint') or 'paint'
            pts = a['points']; lx, ly = pts[0]
            changed = [TB.paintMask(P, gsel.items, (lx, ly), paint, BR.FIRST_DAB, mb, Rb, hard, screen)]
            for x_, y_ in pts[1:]:
                dd = js_hypot(x_ - lx, y_ - ly); lx, ly = x_, y_
                changed.append(TB.paintMask(P, gsel.items, (x_, y_), paint, BR.dabAmount(dd), mb, Rb, hard, screen))
            K['paintRecs'].append({'action': i, 'changed': changed, 'masks': [{'id': x.id, 'mask': list(x.mask) if x.mask is not None else None} for x in P.of_type('xmod')],
                                   'dirty': [t.id for t in P.tubes() if t.dirty]})
        elif op == 'paintTip':                                      # 4.8: a stroke of the Density brush, the original's occlusion answers (paint.json)
            from hairmesh.core import density as DN
            rec = paint_recs[i]
            ctx_ = settle_frame(P)                                  # (the harness's frame first: the row tables, LOCK_CACHE)
            locks = [e['L'] for e in ctx_.LOCK_CACHE.values()] if ctx_ is not None else []
            sel_t = [need('topo', k).id for k in a.get('tips') or []]
            gsel.items = list(sel_t); gsel.anchors = []
            if a.get('strength') is not None:
                tip_strength[0] = a['strength']
            if a.get('R') is not None:
                brush_rh[0] = a['R']
            if a.get('hard') is not None:
                brush_rh[1] = a['hard']
            Rb, hard = brush_rh
            screen = js_projector(rec['view']); paint = a.get('paint') or 'paint'
            dabs = []
            for j, (x_, y_) in enumerate(a['points']):
                answers = iter(rec['dabs'][j]['seen']); asked = []

                def seen(L, c, k, p, answers=answers, asked=asked):
                    v = next(answers); asked.append(v)
                    return v
                if j == 0:
                    amt = BR.FIRST_DAB
                else:
                    px_, py_ = a['points'][j - 1]; amt = BR.dabAmount(js_hypot(x_ - px_, y_ - py_))
                ch = DN.paintTipMask(P, locks, gsel.items, (x_, y_), paint, amt, tip_strength[0], Rb, hard, screen, seen)
                dabs.append({'seen': asked, 'changed': ch})
            K['paintRecs'].append({'action': i, 'dabs': dabs, 'masks': [{'id': k.id, 'mask': {str(c): list(W) for c, W in k.mask.items()} if k.mask else None}
                                                                      for k in P.items.values() if k.type == 'tip']})
        elif op == 'brush':                                         # 4.9: a stroke of the anchor brush, with the view, rays and hits the harness recorded (brush.json)
            from hairmesh.core import anchorbrush as AB
            rec = brush_recs[i]
            ids = item_ids(a)
            gsel.items = list(ids); gsel.anchors = []
            sel_last[0] = ids[len(ids) - 1] if ids else None; G.panelsRendered(P, sel_last[0])   # onSelChange
            if a.get('mode'):
                brush_mode[0] = P.settings.brushMode = a['mode']        # (the file keeps it, 10061)
            if a.get('R') is not None:
                brush_rh[0] = a['R']
            if a.get('hard') is not None:
                brush_rh[1] = a['hard']
            settle_frame(P)
            shown = AB.brushVisible(P, gsel.items, gsel.anchors)
            lst = edit.anchorList(P, gsel.items, gsel.anchors, shown={c.id for c in G.shownCurves(P, gsel.items, gsel.anchors)[0]})
            cur = {}
            view = AB.BrushView(lambda p: cur['screen'](p), lambda m: cur['ray'], None, None, 0, hit=lambda m: cur['hit'])
            op_ = AB.anchorOp(bool(a.get('shift')), bool(a.get('ctrl')), bool(a.get('alt')))
            st = None
            for j, e in enumerate(rec['events']):
                settle_frame(P)                                     # (the frame the harness runs before each event: the curves and the walls follow)
                cur['screen'] = js_projector(e['view'])             # (per event: a view switch still tweens)
                view.cam_dir = V3(*e['camDir']); view.cam_pos = V3(*e['camPos']); view.H = e['H']; view.ortho = e['ortho']
                view.tan_half = AB.tanHalf(e['fov']) if e['fov'] is not None else None
                cur['ray'] = Ray(V3(*e['origin']), V3(*e['direction']))
                cur['hit'] = (V3(*e['hit']['point']), e['hit']['distance']) if e.get('hit') else None
                m = (e['x'], e['y'])
                if j == 0:
                    st = AB.startStroke(P, view, m, op_, brush_rh[0], brush_rh[1], brush_mode[0], AB.brushAnchors(P, gsel.items, lst), shown, rec['scale'])
                else:
                    AB.moveStroke(P, view, m, st)
            K['brushRecs'].append({'action': i, 'list': [x.id for x in lst], 'type': st.type, 'cap': [{'a': c[0].id, 'w': c[1]} for c in st.cap] if st.type != 'brushSmooth' else None,
                                   'moved': st.moved, 'project': project_to_dict(P)})
        elif op == 'extraProp':
            x = need('extras', a['extra'])
            for k, v in (a.get('set') or {}).items():
                x.p[k] = v
            if a.get('only') is not None:
                x.p['only'] = [need('extras', k).id for k in a['only']]
            if a.get('mask') is not None:
                x.mask = list(a['mask'])
            if a.get('fill'):
                TB.maskOp(P, x, a['fill'])
            if a.get('visible') is not None:
                x.visible = a['visible']
            t = P.items.get(x.tube)
            if t is not None:
                t.dirty = True
            if a.get('select'):                                     # selected (the outliner): its panel renders (xmodProps)
                gsel.items = [x.id]; gsel.anchors = []
                sel_last[0] = x.id; G.panelsRendered(P, x.id)
        # ---- 4.9: the Edges tool on a converted mesh (lib/script.mjs `edgeClick`, `edgeBox`, `edgeSet`, `edgesToCurves`) ----
        elif op in ('edgeClick', 'edgeBox', 'edgeSet', 'edgesToCurves'):
            run_edges(P, a, i, K, edge_recs.get(i), esel, ememo, gsel, sel_last)
        # ---- 4.10: symmetry applied once, the union of locks (lib/script.mjs `symmetry`, `union`) ----
        elif op == 'symmetry':
            settle_frame(P)                                         # the frame loop first (a disc's hits), as between two clicks
            g = need('groups', a['group'])
            gsel.items = [g.id]; gsel.anchors = []
            sel_last[0] = g.id; G.panelsRendered(P, g.id)           # the group selected: its panel holds the button (onSelChange)
            before = set(P.items)
            mg, msg = SYM.applySymmetry(P, g)
            if mg is not None:
                G.panelsRendered(P, sel_last[0])                    # renderOutliner (9900)
            K['symRecs'].append({'action': i, 'op': op, 'toast': msg, 'sel': list(gsel.items), 'asel': [], 'made': [x for x in P.items if x not in before], 'project': project_to_dict(P)})
        elif op == 'union':
            from hairmesh.core import union as UN
            settle_frame(P)
            ids = item_ids(a)
            gsel.items = list(ids); gsel.anchors = []
            sel_last[0] = ids[len(ids) - 1] if ids else None; G.panelsRendered(P, sel_last[0])   # onSelChange
            before = set(P.items)
            made, msg, _welded = UN.unionSelectedLocks(P, ids)
            if made:                                                # (3247-3248)
                if P.conv:
                    CONV.regenConversion(P, RW.buildRowTables(P).T, CR.crownData)
                gsel.items = [x.id for x in made if x.type == 'surface']
                sel_last[0] = gsel.items[len(gsel.items) - 1] if gsel.items else None
                G.panelsRendered(P, sel_last[0])                    # onSelChange -> renderOutliner
            K['unionRecs'].append({'action': i, 'op': op, 'toast': msg, 'sel': list(gsel.items), 'asel': [], 'made': [x for x in P.items if x not in before], 'project': project_to_dict(P)})
        # ---- 4.6: the Profile tab and the modifier's sheet (lib/script.mjs `applyModifier`, `pasteModifier`, `copyModifier`, `tubeProp`, `sameProfile`) ----
        elif op in ('applyModifier', 'pasteModifier'):
            from hairmesh.core import modifier as MOD
            CM.rebuild_dirty(P)
            ids = [*item_ids(a), *[need('tubes', k).id for k in a.get('tubes') or []]]
            gsel.items = list(dict.fromkeys(ids)); gsel.anchors = []
            sel_last[0] = gsel.items[len(gsel.items) - 1] if gsel.items else None; G.panelsRendered(P, sel_last[0])   # (the selection given, onSelChange)
            before = set(P.items)
            if op == 'applyModifier':
                prof = [list(q) for q in a['profile']] if a.get('profile') else MOD.profileByName(a.get('preset') or 'Quad')
                made, msg = TB.applyModifier(P, ids, prof)
            else:
                made, msg = MOD.pasteModifier(P, ids, K.get('modClip'))
            if made:
                gsel.items = [t.id for t in made]; gsel.anchors = []
                sel_last[0] = gsel.items[len(gsel.items) - 1]; G.panelsRendered(P, sel_last[0])   # onSelChange (6730, 6752)
                if a.get('key') is not None:
                    K['tubes'][a['key']] = made[0]
            K['tubeRecs'].append({'action': i, 'op': op, 'toast': msg, 'sel': list(gsel.items), 'asel': [], 'made': [x for x in P.items if x not in before], 'project': project_to_dict(P)})
        elif op == 'profEditor':                                    # 4.6b: the profile editor's events (core/profedit.py; lib/script.mjs `profEditor`)
            from hairmesh.core import profedit as PE
            t = need('tubes', a['tube'])
            if gsel.items != [t.id]:
                gsel.items = [t.id]; gsel.anchors = []
            sel_last[0] = t.id; G.panelsRendered(P, t.id)           # (the sheet rendered: ensureTubeRef, ownProfs)
            Pp = TB.ownProfs(t); k = a['k']; drag = None; toast = None
            for e in a['events']:
                kind = e[0]; o = e[3] if len(e) > 3 else {}
                if kind == 'down':
                    pi = PE.nearest(Pp[k], e[1], e[2])
                    drag = PE.press(Pp[k], pi, bool(o.get('ctrl')), True) if pi >= 0 else None
                elif kind == 'move' and drag is not None:
                    PE.move(Pp[k], drag, e[1], e[2], bool(o.get('shift')), bool(o.get('ctrl')), True); t.dirty = True
                elif kind == 'up':
                    drag = None
                elif kind == 'rmb' and not o.get('shift'):              # (Shift + RMB: the window's handler opens the main pie first, 7968)
                    bi, s, q = PE.insert_at(Pp[k], e[1], e[2], False); PE.insert_three(Pp, k, bi, s, q); t.dirty = True
                elif kind == 'dbl':
                    pi = PE.nearest(Pp[k], e[1], e[2])
                    if pi >= 0:
                        if not PE.can_delete(Pp[k]):
                            toast = PE.TOO_FEW
                        else:
                            for A in Pp:
                                A.pop(pi)
                            t.dirty = True
            K['tubeRecs'].append({'action': i, 'op': op, 'toast': toast, 'sel': list(gsel.items), 'asel': list(gsel.anchors), 'made': [], 'project': project_to_dict(P)})
        elif op == 'copyModifier':
            from hairmesh.core import modifier as MOD
            K['modClip'], msg = MOD.copyModifier(need('tubes', a['tube']))
            K['tubeRecs'].append({'action': i, 'op': op, 'toast': msg, 'sel': list(gsel.items), 'asel': list(gsel.anchors), 'made': [], 'project': project_to_dict(P)})
        elif op == 'tubeProp':
            t = need('tubes', a['tube'])
            t.p.update(a['set'])
            if a.get('autoRadius'):
                t.p['rRef'] = 0; TB.ensureTubeRef(P, t)
            t.dirty = True
            K['tubeRecs'].append({'action': i, 'op': op, 'toast': None, 'sel': list(gsel.items), 'asel': list(gsel.anchors), 'made': [], 'project': project_to_dict(P)})
        elif op == 'sameProfile':
            t = need('tubes', a['tube']); Pp = TB.ownProfs(t); k = a['k']
            Pp[k][:] = [list(q) for q in Pp[k - 1]]; t.dirty = True
            K['tubeRecs'].append({'action': i, 'op': op, 'toast': None, 'sel': list(gsel.items), 'asel': list(gsel.anchors), 'made': [], 'project': project_to_dict(P)})
        # ---- 4.4: the Topology tab (lib/script.mjs `addCaps`, `addSkin`, `addTips`, `topoProp`, `convertSkin`, `topoPreview`) ----
        elif op in ('addCaps', 'addSkin', 'addTips'):
            from hairmesh.core import topology as TP
            CM.rebuild_dirty(P)
            ids = item_ids(a)
            gsel.items = list(ids); gsel.anchors = []
            sel_last[0] = ids[len(ids) - 1] if ids else None; G.panelsRendered(P, sel_last[0])   # (the selection given, onSelChange)
            if a.get('defaults') is not None:                       # (S.capDefaults / skinDefaults / tipDefaults: kept for the actions after)
                K['topoDefaults'][op] = {**({'addCaps': M.CAP_DEFAULTS, 'addSkin': M.SKIN_DEFAULTS, 'addTips': TIP_DEFAULTS}[op]), **a['defaults']}
            dflt = K['topoDefaults'].get(op)
            before = set(P.items)
            made, msg = {'addCaps': TP.addCaps, 'addSkin': TP.addSkin, 'addTips': TP.addTips}[op](P, ids, dflt)
            if made:
                if op != 'addSkin' and P.conv:                      # (S.surfDirty; if (S.conv) regenConversion(): addCaps 7262, addTips 7327)
                    CONV.regenConversion(P, RW.buildRowTables(P).T, CR.crownData)
                gsel.items = [x.id for x in made]; gsel.anchors = []
                sel_last[0] = gsel.items[len(gsel.items) - 1]; G.panelsRendered(P, sel_last[0])   # onSelChange -> renderOutliner
            rec = {'action': i, 'op': op, 'toast': msg, 'sel': list(gsel.items), 'asel': [], 'made': [x for x in P.items if x not in before], 'project': project_to_dict(P)}
            if a.get('key') is not None and made:
                K['topo'][a['key']] = made[0]
            K['topoRecs'].append(rec)
        elif op == 'topoProp':
            k = K['topo'][a['item']]
            k.p.update(a['set'])
            if not (k.type == 'skin' and all(x == 'bulge' for x in a['set'])) and P.conv:
                CONV.regenConversion(P, RW.buildRowTables(P).T, CR.crownData)
            K['topoRecs'].append({'action': i, 'op': op, 'toast': None, 'sel': list(gsel.items), 'asel': list(gsel.anchors), 'made': [], 'project': project_to_dict(P)})
        elif op == 'convertSkin':
            from hairmesh.core import topology as TP
            k = K['topo'][a['item']]
            before = set(P.items)
            made, msg = TP.convertSkin(P, k)
            if made is not None:
                if P.conv:
                    CONV.regenConversion(P, RW.buildRowTables(P).T, CR.crownData)
                gsel.items = [x.id for x in made]; gsel.anchors = []
                sel_last[0] = gsel.items[len(gsel.items) - 1] if gsel.items else None; G.panelsRendered(P, sel_last[0])
            K['topoRecs'].append({'action': i, 'op': op, 'toast': msg, 'sel': list(gsel.items), 'asel': list(gsel.anchors), 'made': [x for x in P.items if x not in before], 'project': project_to_dict(P)})
        elif op == 'topoPreview':
            from hairmesh.core import topology as TP
            CM.rebuild_dirty(P)
            T = RW.buildRowTables(P).T
            skins = []
            for k in P.of_type('skin'):
                cands = TP.skinCandidates(P, k, T) if k.visible else []
                skins.append({'id': k.id, 'cands': [{'kind': c['kind'], 'X': [x.id for x in c['X']] if 'X' in c else None, 'Y': [x.id for x in c['Y']] if 'Y' in c else None,
                                                     'ca': c['ca'].id if 'ca' in c else None, 'cb': c['cb'].id if 'cb' in c else None,
                                                     'a': [q.toArray() for q in c['a']], 'b': [q.toArray() for q in c['b']]} for c in cands]})
            caps = []
            for k in P.caps():
                m = TP.capPreview(P, k, T)
                caps.append({'id': k.id, 'srcSurfs': list(k.srcSurfs), 'pos': [q.toArray() for q in m['pos']] if m else None, 'faces': m['faces'] if m else None})
            K['topoRecs'].append({'action': i, 'op': op, 'skins': skins, 'caps': caps, 'capRef': TP.capRef(P), 'project': project_to_dict(P)})
        else:
            raise ValueError(f'script: action {i}: unknown op {op!r}')
    P.gen += 1
    return K


GUIDE_OPS = ('guideStroke', 'guideClick', 'guideProp', 'guideDefaults', 'placeDisc', 'createDisc', 'discRadius', 'resetDisc', 'pencil')


def run_guide_op(P, i, a, rec, K, gsel, curve_of, need, sel_last):
    """the 4.2 ops on hairmesh.core.guidetool (lib/script.mjs: what each does in the original). `gsel` is S.sel /
    S.asel as the guide ops leave it (a Selection, changed in place); the records K['guideRecs'] mirror guide.json."""
    from hairmesh.core import guidetool as GT, scene
    from hairmesh.core.curves import markAnchorsDirty
    from hairmesh.core.selection import Selection
    op = a['op']

    def frame():
        scene.settle(P)
        RW.buildRowTables(P)                                        # (the frame the harness runs: rebuildSurfaces fills the groups' conv, groupConv — usedStep then reads them)

    def needrec():
        if rec is None:
            raise ValueError(f'script: action {i} ({op}) needs what the harness recorded (guide.json)')
        return rec

    def rendered():
        # onSelChange -> renderOutliner (orderRoots) and renderProps, as groups.panelsRendered mirrors them
        sel_last[0] = gsel.items[len(gsel.items) - 1] if gsel.items else None
        G.panelsRendered(P, sel_last[0])

    def record(g, toast, **extra):
        if g is not None and a.get('key') is not None:
            K['guides'][a['key']] = g
        if g is not None:
            rendered()
        K['guideRecs'].append({'action': i, 'op': op, 'toast': toast, 'sel': list(gsel.items), 'asel': list(gsel.anchors), 'guide': g.id if g is not None else None, **extra})

    def take(sel):
        gsel.items = list(sel.items); gsel.anchors = list(sel.anchors)
    if op == 'guideStroke':
        r = needrec()
        take(Selection([curve_of(k).id for k in a.get('curves') or []], [])); rendered()
        frame()
        shown = {c.id for c in G.shownCurves(P, gsel.items, gsel.anchors)[0]}
        x0, y0, x1, y1 = a['stroke']
        g, msg = GT.createGuide(P, gsel, (x0, y0), (x1, y1), js_projector(r['view']), lambda c: c.id in shown)
        record(g, msg)
    elif op == 'guideClick':
        r = needrec()
        frame()
        cs, act, loose = G.shownCurves(P, gsel.items, gsel.anchors)
        pg = GT.pickGuide(P, G.shownGuides(P, act, loose), a['click'][0], a['click'][1], js_projector(r['view']))
        g = pg or (P.items.get(r['disc']) if r.get('disc') is not None else None)
        if g is not None:
            GT.activateGuide(P, gsel, g); rendered()
        record(None, None, line=pg.id if pg is not None else None, disc=r.get('disc'))
    elif op == 'guideProp':
        g = need('guides', a['guide'])
        for k, v in (a.get('set') or {}).items():
            GT.setGuideParam(g, k, v)
        if a.get('excl') is not None:
            GT.setExclusions(g, [need('groups', k).id for k in a['excl']])
        if a.get('exclMode'):
            GT.setExclMode(g, a['exclMode'])
    elif op == 'guideDefaults':
        d = P.settings.guideDefaults
        if d.get('radius') is None:
            d['radius'] = DUP.guideDefaultsNow(P)['radius']
        for k, v in (a.get('set') or {}).items():
            GT.setGuideParam(d, k, v)
    elif op == 'placeDisc':
        r = needrec()
        frame()
        g = GT.placeDisc(P, gsel, V3(*r['point']) if r.get('point') else None)
        record(g, GT.DISC_PLACED if g is not None else None)
    elif op == 'createDisc':
        take(Selection([curve_of(k).id for k in a.get('curves') or []], [])); rendered()
        frame()
        g = GT.createDiscButton(P, gsel, lambda c: edit.isVisible(P, c))
        record(g, GT.DISC_PLACED)
    elif op == 'discRadius':
        g = need('guides', a['guide'])
        markAnchorsDirty(P, GT.setDiscRadius(P, g, a['which'], GT.clampedRadius(P, g, a['which'], a['r'])))
    elif op == 'resetDisc':
        markAnchorsDirty(P, GT.resetDisc(P, need('guides', a['guide'])))
    elif op == 'pencil':
        r = needrec()
        frame()
        pc = GT.Pencil(); g = None; toast = None
        for e in r['points']:
            ray = Ray(V3(*e['origin']), V3(*e['direction']))
            res = GT.pencilAdd(pc, (e['x'], e['y']), ray, V3(*e['camDir']), V3(*e['pivot']), V3(*e['hit']) if e.get('hit') else None)
            if res == 'close':
                g, toast = GT.finishPencil(P, gsel, pc, V3(*e['camDir'])); pc = None
                break
        if pc is not None and a.get('end') == 'enter':
            g, toast = GT.finishPencil(P, gsel, pc, V3(*r['camDir'])); pc = None
        record(g, toast)


CROWN_OPS = ('createCrown', 'addCrownCurve', 'crownProp', 'crownKey', 'attach', 'detachConn', 'slide', 'crownSym', 'crownSymOff', 'crownFrame')


def run_crown_op(P, i, a, rec, K, gsel, need, sel_last):
    """the 4.3 ops on hairmesh.core.crowntool (lib/script.mjs: what each does in the original), each followed by the
    crown part of a frame (crowntool.frameSync: crownSymSync, updateAttachments, 12060-12063) and recorded as
    crown.json: the toast, S.sel / S.asel, the project file, every crown's crownData"""
    from hairmesh.core import crowntool as CT
    from hairmesh.core.hairmesh_io import project_to_dict
    op = a['op']
    toasts = []

    def rendered():
        sel_last[0] = gsel.items[len(gsel.items) - 1] if gsel.items else None
        G.panelsRendered(P, sel_last[0])

    def crown_frame():
        _moved, said = CT.frameSync(P)
        toasts.extend(said)

    def record(**extra):
        crowns = []
        for k in P.crowns():
            d = CR.crownData(P, k)
            crowns.append({'id': k.id, 'info': k.info, 'pos': [p.toArray() for p in d.pos] if d else None, 'faces': [list(f) for f in d.faces] if d else None})
        K['crownRecs'].append({'action': i, 'op': op, 'toast': toasts[len(toasts) - 1] if toasts else None, 'sel': list(gsel.items), 'asel': list(gsel.anchors),
                               'project': project_to_dict(P), 'crowns': crowns, **extra})

    def anchor(key):
        return need('anchors', key)

    def crown(key):
        return need('crowns', key)
    if op == 'createCrown':
        CM.rebuild_dirty(P)
        k, msg = CT.createCrown(P, gsel, V3(*a['target']) if a.get('target') else None, V3(*a['normal']) if a.get('normal') else None, sym=a.get('sym') is not False)
        toasts.append(msg); rendered()
        if a.get('key') is not None:
            K['crowns'][a['key']] = k
        crown_frame(); record(crown=k.id)
    elif op == 'addCrownCurve':
        CM.rebuild_dirty(P)
        c, msg = CT.addCrownCurve(P, gsel, crown(a['crown']))
        if c is not None:
            toasts.append(msg); rendered()
            if a.get('key') is not None:
                K['curves'][a['key']] = c
        crown_frame(); record(curve=c.id if c is not None else None)
    elif op == 'crownProp':
        k = crown(a['crown'])
        if a.get('rings') is not None:
            CT.setCrownParam(k, 'rings', a['rings'])
        if a.get('whorl') is not None:
            CT.setCrownParam(k, 'whorl', a['whorl'])
        crown_frame(); record()
    elif op == 'crownKey':
        k = crown(a['crown'])

        def named(n):
            for c in P.curves():
                if (c.parent == k.parent or c.id in (k.part, k.low)) and c.name == n:
                    return c
            raise KeyError(f'script: action {i}: no crown curve "{n}"')
        for key, n in (a.get('curves') or {}).items():
            K['curves'][key] = named(n)
        for key, (n, j) in (a.get('anchors') or {}).items():
            c = named(n); K['anchors'][key] = c.anchors[len(c.anchors) + j if j < 0 else j]
    elif op == 'attach':
        CM.rebuild_dirty(P)
        toasts.append(CT.connectToConnector(P, anchor(a['anchor']), need('curves', a['connector'])))
        rendered()                                                  # (renderProps, 7413)
        crown_frame(); record()
    elif op == 'detachConn':
        CM.rebuild_dirty(P)
        before = (list(gsel.items), list(gsel.anchors))
        msg = CT.detachFromConnector(P, gsel, anchor(a['anchor']))
        toasts.append(msg if msg is not None else CT.STAYS)
        if msg is not None:
            rendered()                                              # (renderProps / onSelChange, 7426-7436)
        crown_frame(); record(ok=msg is not None)
    elif op == 'slide':
        if rec is None:
            raise ValueError(f'script: action {i} (slide) needs what the harness recorded (crown.json)')
        CM.rebuild_dirty(P)
        an = anchor(a['anchor']); on = CT.slideTarget(P, an)
        d = {'a': an, 'poly': [s.p.clone() for s in on.samples], 'moved': False}
        screen = js_projector(rec['view'])
        for x, y in a['points']:
            CT.slideStep(P, d, (x, y), screen); crown_frame()
        rendered()                                                  # (5989: commit, renderProps)
        record(view=rec['view'])
    elif op == 'crownSym':
        CM.rebuild_dirty(P)
        before = list(gsel.items)
        msg = CT.crownMakeSymmetric(P, gsel, crown(a['crown']), a['master'])
        if msg:
            toasts.append(msg)
        if msg and msg != CT.NOTHING_TO_MIRROR:
            rendered()                                              # (onSelChange, renderOutliner, 7775)
        crown_frame(); record()
    elif op == 'crownSymOff':
        toasts.append(CT.crownSymOff(P, crown(a['crown'])))
        crown_frame(); record()
    elif op == 'crownFrame':                                        # (`quiet`: the frame alone, no record: the fuzz runs one between any two actions, as the app does)
        crown_frame()
        if not a.get('quiet'):
            record()


def run_edges(P, a, i, K, rec, esel, ememo, gsel, sel_last):
    """the 4.9 ops, as lib/script.mjs: a press with the pixel and the polygon the harness recorded (the ray is the only
    thing not run here); recorded as edges.json"""
    op = a['op']

    def now():
        return {'id': esel.id, 'set': list(esel.edges), 'last': esel.last}

    def mesh_named(n):
        return next(x for x in P.items.values() if x.type == 'mesh' and x.name == n)
    if op == 'edgeSet':
        ED.setEdgeSel(esel, mesh_named(a['mesh']).id, a['list'], a.get('mode') or 'set')
        if 'last' in a:
            esel.last = a['last']
    elif op == 'edgeBox':
        it = P.items[esel.id]
        ED.edgeBoxDeselect(esel, ED.edgeTopo(it.data), ED.edgeWorld(it.data), js_projector(rec['view']), *a['box'])
        K['edgeRecs'].append({'action': i, 'op': op, 'edges': now(), 'sel': list(gsel.items), 'asel': list(gsel.anchors)})
    elif op == 'edgeClick':
        project = js_projector(rec['view'])
        meshes = [(x.id, x.data) for x in P.items.values() if x.type == 'mesh' and x.data is not None and edit.isVisible(P, x)]
        if not a.get('keep'):
            ememo[0] = None
        presses = []
        for k, q in enumerate(rec['presses']):
            h = q['hit']
            r = ED.pickEdge(meshes, (h['id'], h['face']) if h else None, project, q['x'], q['y'], False)
            Wp = ED.edgeWorld(P.items[r[0]].data) if r is not None else None
            got = ED.edgeClick(esel, ememo[0], r, bool(a.get('ctrl')), bool(a.get('shift')), float(k), Wp)
            ememo[0] = got['memo']
            if got['select'] is not None and (len(gsel.items) != 1 or gsel.items[0] != got['select']):
                gsel.items = [got['select']]; gsel.anchors = []; sel_last[0] = got['select']; G.panelsRendered(P, got['select'])
            presses.append({'pick': {'id': r[0], 'e': r[2]} if r is not None else None, 'toast': got['toast'], 'hud': got['hud'], 'edges': now()})
        K['edgeRecs'].append({'action': i, 'op': op, 'presses': presses, 'sel': list(gsel.items), 'asel': list(gsel.anchors)})
    else:
        if a.get('sel') is not None:
            gsel.items = [K['groups'][k].id for k in a['sel']]; gsel.anchors = []
            sel_last[0] = gsel.items[-1] if gsel.items else None; G.panelsRendered(P, sel_last[0])
        before = set(P.items)
        made, g, msg = ED.edgesToCurves(P, gsel.items, esel)
        if made:
            gsel.items = [c.id for c in made]; gsel.anchors = []
            sel_last[0] = gsel.items[-1]; G.panelsRendered(P, sel_last[0])
        K['edgeRecs'].append({'action': i, 'op': op, 'toast': msg, 'sel': list(gsel.items), 'asel': list(gsel.anchors), 'made': [x for x in P.items if x not in before], 'project': project_to_dict(P), 'edges': now()})


def settle_frame(P):
    """What the harness's settle() does to the model between two ops of the 4.5 scripts (the frame loop, 12062-12074):
    the dirty curves rebuilt, the row tables when there are walls (rebuildSurfaces: groupConv fills the conv of every
    lock's group) and the dirty modifiers rebuilt (rebuildTube: ensureTubeRef). Returns the tables' context or None."""
    from hairmesh.core import scene
    scene.settle(P)
    ctx = RW.buildRowTables(P) if P.surfaces() else None
    for t in P.tubes():
        if t.dirty:
            TB.rebuildTube(P, t)
    return ctx
