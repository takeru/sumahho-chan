#!/usr/bin/env python3
"""SolidCraft による組立検査。設計・生成物は変更せず、一時ファイルだけを使う。

python3 check_assembly.py [--selftest]
run(verbose=True) は失敗メッセージの list[str] を返す。
"""
import argparse
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import time

# import で __pycache__ をリポジトリへ新規生成しない。呼出元の設定は戻す。
_bytecode_setting = sys.dont_write_bytecode
sys.dont_write_bytecode = True
try:
    import build as B
finally:
    sys.dont_write_bytecode = _bytecode_setting

D, SV, MB, AT, PH = B.D, B.SV, B.MB, B.AT, B.PH
OVERLAP_LIMIT = 0.01
PROBE_LIMIT = 0.0  # probe は、実体の 0.01mm³ 許容を適用せず正の重なりを検出する。
VOLUME_RTOL = 1e-4
CLEARANCE_QUERY = 2.0
WORKERS = 4
PIVOT_FIT = 0.3  # ピボット穴とピンの直径差の上限 (これより緩いと圧入にならない)
PRINTED = {p['body']: name for name, p in B.PLACEMENTS.items()}

# 行追加だけで対象ペアと要求隙間を増やせる。0 は「重ならなければ可」。
GAP_RULES = (
    (('BackBox', 'BackLid', 'Phone'), ('CheekL', 'CheekR'), 0.3),
    (('PanDeck',), ('BaseShell',), 0.5),
    (('PanDeck', 'PanHorn'), ('PanServo', 'MotionBase', 'AtomLite'), 0.0),
    (('BackBox', 'BackLid', 'Phone', 'UsbPlug'), ('PanDeck',), 0.5),
)
# 実体の干渉除外は設けない。probe は実体の一覧に入れず、指定相手だけを検査する。


def command(name, **params):
    return {'command': name, 'params': params}


def box(name, corner, size, op=None):
    p = dict(body_name=name, corner=corner, length=size[0], width=size[1], height=size[2])
    if op:
        p.update(operation=op, targets=[name])
    return command('solid.box', **p)


def cylinder(name, base, diameter, height, axis='Z', op=None):
    p = dict(body_name=name, base=base, diameter=diameter, height=height, axis=axis)
    if op:
        p.update(operation=op, targets=[name])
    return command('solid.cylinder', **p)


def move(names, **params):
    return command('solid.move', bodies=list(names), **params)


def place(name, group, at, pan=0, tilt=0):
    """回転行列の自前実装はせず、指定された順に SolidCraft へ渡す。"""
    cmds = []
    if group == 'tilt':
        cmds.append(move([name], translate=[at[i] - B.AX[i] for i in range(3)]))
        if tilt:
            cmds.append(move([name], axis=[1, 0, 0], origin=[0, 0, 0], angle=-tilt))
        cmds.append(move([name], translate=list(B.AX)))
    else:
        cmds.append(move([name], translate=list(at)))
    if group != 'base' and pan:
        cmds.append(move([name], axis=[0, 0, 1], origin=[0, 0, 0], angle=pan))
    return cmds


def servo(name, tilt=False):
    """購入サーボのボディ・耳・ボス・軸を一つのボディにまとめる。"""
    if tilt:
        x, z = B.TILT_BODY_X0, B.TILT_ZC
        return [box(name, [x, -SV['body_w']/2, z-SV['body_l']/2],
                    [SV['body_h'], SV['body_w'], SV['body_l']]),
                box(name, [B.TILT_FLANGE_X, -SV['body_w']/2, z-SV['flange_l']/2],
                    [SV['flange_t'], SV['body_w'], SV['flange_l']], 'join'),
                cylinder(name, [x+SV['body_h'], 0, 0], SV['boss_d'],
                         SV['boss_top']-SV['body_h'], 'X', 'join'),
                cylinder(name, [x+SV['boss_top'], 0, 0], SV['shaft_d'],
                         SV['shaft_tip']-SV['boss_top'], 'X', 'join')]
    z, x = B.PAN_SERVO_BOTTOM, B.SERVO_CX
    return [box(name, [x-SV['body_l']/2, -SV['body_w']/2, z],
                [SV['body_l'], SV['body_w'], SV['body_h']]),
            box(name, [x-SV['flange_l']/2, -SV['body_w']/2, D['flange_z']],
                [SV['flange_l'], SV['body_w'], SV['flange_t']], 'join'),
            cylinder(name, [0, 0, z+SV['body_h']], SV['boss_d'],
                     SV['boss_top']-SV['body_h'], op='join'),
            cylinder(name, [0, 0, z+SV['boss_top']], SV['shaft_d'],
                     SV['shaft_tip']-SV['boss_top'], op='join')]


def horn(name, top, disk_d, axis):
    def pt(v):
        return [v, 0, 0] if axis == 'X' else [0, 0, v]
    return [cylinder(name, pt(top-D['horn_h']), 7, D['horn_h'], axis),
            cylinder(name, pt(top-1.5), disk_d, 1.5, axis, 'join'),
            cylinder(name, pt(top-D['horn_h']-1), SV['shaft_d'],
                     D['horn_h']+2, axis, 'cut')]


def screw_positions():
    tab_x = D['cheek_x_in'] + D['cheek_t'] + D['tab_hole_out']
    return [(s*x, B.AX[1]+y) for s in (1, -1)
            for x, y in ((B.CHEEK_CX, D['toe_hole_y']), (tab_x, D['tab_hole_y']))]


def purchased():
    """(名前, グループ, ローカル原点の世界位置, コマンド列)。"""
    bearing = [cylinder('Bearing608', [B.BEARING_X0, 0, 0], D['bearing_d'], D['bearing_w'], 'X'),
               cylinder('Bearing608', [B.BEARING_X0-1, 0, 0], D['pin_d'], D['bearing_w']+2, 'X', 'cut')]
    items = [
        ('PanServo', 'base', [0, 0, 0], servo('PanServo')),
        ('MotionBase', 'base', [0, 0, 0], [box('MotionBase',
            [-MB['l']/2, D['stack_z']-MB['w']/2, D['plate_t']], [MB['l'], MB['w'], MB['h']])]),
        ('AtomLite', 'base', [0, 0, 0], [box('AtomLite',
            [AT['x']-AT['l']/2, D['stack_z']-AT['w']/2, D['plate_t']+MB['h']],
            [AT['l'], AT['w'], AT['h']])]),
        ('PanHorn', 'pan', [0, 0, 0], horn('PanHorn', B.PAN_HORN_TOP, 2*D['horn_hole_r']+4, 'Z')),
        ('TiltServo', 'tilt', B.AX, servo('TiltServo', True)),
        ('TiltHorn', 'pan', B.AX, horn('TiltHorn', D['cheek_x_in']+D['horn_cb_depth'], 20, 'X')),
        ('Bearing608', 'pan', B.AX, bearing),
        ('Phone', 'tilt', B.AX, [box('Phone', [-PH['w']/2, D['axis_back']-PH['t']/2, -D['grip']],
                                   [PH['w'], PH['t'], PH['h']])]),
        ('UsbPlug', 'tilt', B.AX, [box('UsbPlug', [-6, 9.5, -D['grip']-13], [12, 7, 13]),
                                  box('UsbPlug', [-6, -9, -D['grip']-13], [12, 25.5, 8], 'join')]),
    ]
    for i, (x, y) in enumerate(screw_positions(), 1):
        name = f'ScrewHead{i}'
        items.append((name, 'pan', [0, 0, 0], [cylinder(name, [x, y, B.DECK_TOP+4], 6, 3)]))
    return items


def scene(pan=0, tilt=0, groups=('base', 'pan', 'tilt')):
    cmds, names = [], []
    for part, builder in B.PART_BUILDERS.items():
        p = B.PLACEMENTS[part]
        if p['group'] in groups:
            cmds.extend(builder()['commands'])
            cmds.extend(place(p['body'], p['group'], p['at'], pan, tilt))
            names.append(p['body'])
    for name, group, at, shapes in purchased():
        if group in groups:
            cmds.extend(shapes)
            cmds.extend(place(name, group, at, pan, tilt))
            names.append(name)
    return cmds, names


def execute(cmds, directory, key, stl=False):
    path = Path(directory) / f'{key}.json'
    out = Path(directory) / f'{key}.results.json'
    path.write_text(json.dumps({'commands': cmds}), encoding='utf-8')
    args = ['run', str(path), '--results', '--json-out', str(out)]
    if stl:
        args += ['--out', str(Path(directory) / f'{key}.stl')]
    B.sc(args)
    results = json.loads(out.read_text(encoding='utf-8'))
    if not isinstance(results, list) or len(results) != len(cmds):
        raise ValueError(f'{key}: コマンド結果の数が不一致')
    return results


@dataclass
class Finding:
    kind: str
    key: str
    detail: str
    pose: str = ''
    value: float = 0.0

    def message(self):
        return f'{self.kind}: {self.key}: {self.detail}' + (f' [{self.pose}]' if self.pose else '')


def completeness(result, kind, pose, required):
    issues = []
    for field in required:
        if field not in result:
            issues.append(Finding('検査完全性', kind, f'結果に {field} がない', pose))
    if result.get('unchecked'):
        issues.append(Finding('検査完全性', kind, f"未検査={result['unchecked']}", pose))
    for field in ('budget_exceeded', 'truncated'):
        if result.get(field):
            issues.append(Finding('検査完全性', kind, f'{field}=true', pose))
    return issues


def measurements(result, expected, pose):
    issues = completeness(result, 'measure', pose, ('bodies',))
    bodies = result.get('bodies', [])
    found = {}
    for body in bodies:
        name, volume = body['name'], body.get('volume_mm3')
        if name in found:
            issues.append(Finding('検査完全性', name, 'ボディ名が重複', pose))
        if not body.get('closed') or not isinstance(volume, (int, float)) or not math.isfinite(volume) or volume <= 0:
            issues.append(Finding('検査完全性', name, '閉じた正体積の実体ではない', pose))
        else:
            found[name] = volume
    if set(found) != set(expected):
        issues.append(Finding('検査完全性', 'ボディ一覧',
                              f'欠落={sorted(set(expected)-set(found))}, 余分={sorted(set(found)-set(expected))}', pose))
    return found, issues


def pair(a, b):
    return tuple(sorted((a, b)))


def assess_pose(measure, interference, clearance, names, baseline, pose):
    volumes, issues = measurements(measure, names, pose)
    issues += completeness(interference, 'interference', pose,
                           ('interferences', 'unchecked', 'budget_exceeded', 'truncated', 'checked'))
    issues += completeness(clearance, 'clearance', pose, ('overlaps', 'near', 'unchecked', 'bodies'))
    for result, count_field in ((interference, 'checked'), (clearance, 'bodies')):
        if result.get(count_field) != len(names):
            issues.append(Finding('検査完全性', count_field, f"ボディ数={result.get(count_field)}, 期待={len(names)}", pose))
    relative = 0.0
    for name in set(names) & PRINTED.keys():
        if name in volumes and name in baseline:
            err = abs(volumes[name]-baseline[name])/baseline[name]
            relative = max(relative, err)
            if err > VOLUME_RTOL:
                issues.append(Finding('部品体積', name,
                    f'単体={baseline[name]:.6f}, 組立={volumes[name]:.6f}mm³, 相対差={err:.3g}', pose, err))
        elif name not in baseline:
            issues.append(Finding('部品体積', name, '単体の基準体積が取得できない', pose))
    # clearance 側だけに重なりが出た場合も見落とさない。
    overlaps = {}
    for row in interference.get('interferences', []) + clearance.get('overlaps', []):
        p = pair(row['a'], row['b'])
        overlaps[p] = max(overlaps.get(p, 0.0), row['volume_mm3'])
    for p, volume in overlaps.items():
        if volume > OVERLAP_LIMIT:
            issues.append(Finding('実体干渉', '/'.join(p), f'重なり={volume:.6f}mm³ > {OVERLAP_LIMIT}', pose, volume))
    distances = {p: 0.0 for p in overlaps}
    for row in clearance.get('near', []):
        distances[pair(row['a'], row['b'])] = row['distance_mm']
    gaps = {}
    for left, right, minimum in GAP_RULES:
        for a in left:
            for b in right:
                if a not in names or b not in names:
                    continue
                p = pair(a, b)
                # 完全な clearance=2 の結果に無いペアは 2mm 以上と分かる。
                distance = distances.get(p, CLEARANCE_QUERY)
                gaps[p] = distance
                if distance < minimum-1e-6:
                    issues.append(Finding('最小隙間', '/'.join(p),
                        f'隙間={distance:.6f}mm < {minimum:.1f}mm', pose, -distance))
    return issues, gaps, relative


def angles(limit, step):
    """端点と 0° も必ず含める (上限が刻みの倍数でない場合も)。"""
    if not math.isfinite(limit) or limit < 0:
        raise ValueError(f'掃引角度が不正: {limit}')
    n = math.floor(limit/step)
    return sorted({-float(limit), float(limit), 0.0} | {float(i*step) for i in range(-n, n+1)})


def pose_job(directory, index, pan, tilt, groups, baseline):
    cmds, names = scene(pan, tilt, groups)
    cmds += [command('inspect.measure', bodies=names),
             command('inspect.interference', bodies=names, budget_s=60),
             command('inspect.clearance', bodies=names, clearance=CLEARANCE_QUERY, min_volume=OVERLAP_LIMIT)]
    results = execute(cmds, directory, f'pose-{index}')
    label = f'pan={pan:+g}°, tilt={tilt:+g}°'
    return assess_pose(*results[-3:], names, baseline, label)


@dataclass
class Probe:
    name: str
    mode: str
    targets: tuple
    shapes: list
    minimum: float = PROBE_LIMIT


def renamed(cmds, old, new):
    cmds = copy.deepcopy(cmds)
    for c in cmds:
        p = c['params']
        if p.get('body_name') == old:
            p['body_name'] = new
        if 'targets' in p:
            p['targets'] = [new if t == old else t for t in p['targets']]
    return cmds


def probes():
    rows = []
    driver_targets = tuple(PRINTED) + ('Bearing608', 'TiltServo', 'TiltHorn')
    for i, (x, y) in enumerate(screw_positions(), 1):
        name = f'DriverCheek{i}'
        rows.append(Probe(name, 'clear', driver_targets,
                          [cylinder(name, [x, y, B.DECK_TOP+7], 6, 100)]))
    rows.append(Probe('DriverPan', 'clear', ('PanDeck',),
                      [cylinder('DriverPan', [0, 0, B.PAN_HORN_TOP], 5, B.DECK_TOP+100-B.PAN_HORN_TOP)]))
    for distance in (2, 5, 10, 16, 25):
        name = f'InsertServo{distance}'
        rows.append(Probe(name, 'clear', ('BackBox',), servo(name, True) +
                          [move([name], translate=[0, -distance, 0])] + place(name, 'tilt', B.AX)))
    ch_y = B.AX[1] + sum(D['channel_y'])/2
    outer = -(D['cheek_x_in']+D['cheek_t'])
    end = -B.CHEEK_CX-1+D['groove_len']
    rows.extend([
        Probe('WireAxis', 'clear', ('BackBox', 'PivotPin', 'Bearing608', 'CheekL'),
              [cylinder('WireAxis', [outer-4, B.AX[1], B.AX[2]], 3, B.TILT_BODY_X0-2-(outer-4), 'X')]),
        Probe('WireCheek', 'clear', ('CheekL', 'PivotPin', 'Bearing608'),
              [box('WireCheek', [outer+0.5, ch_y-1, B.DECK_TOP-1],
                   [2, 2, B.AX[2]-D['bearing_hole_d']/2-0.5-(B.DECK_TOP-1)])]),
        Probe('WireDeckHole', 'clear', ('PanDeck', 'CheekL'),
              [cylinder('WireDeckHole', [-(B.CHEEK_CX+1.7), ch_y, B.DECK_BOTTOM-0.5],
                        2.4, B.DECK_TOP+1-(B.DECK_BOTTOM-0.5))]),
        Probe('WireGroove', 'clear', ('PanDeck',),
              [box('WireGroove', [-B.CHEEK_CX, ch_y-1, B.DECK_BOTTOM], [end-0.5+B.CHEEK_CX, 2, 2])]),
        Probe('WireDrop', 'clear', ('PanDeck', 'BaseShell'),
              [cylinder('WireDrop', [end-1.5, ch_y, D['shell_top']-4],
                        2.4, B.DECK_BOTTOM+2-(D['shell_top']-4))]),
    ])
    # サーボ延長線のコネクタ (7.6×2.5) がデッキの穴を通ること、左頬板の溝に中継コネクタ (8×3×30) が収まること
    rows.extend([
        Probe('ConnDeckHole', 'clear', ('PanDeck',),
              [box('ConnDeckHole', [-B.CHEEK_CX-1.25, ch_y-3.8, B.DECK_BOTTOM-1], [2.5, 7.6, D['deck_t']+2])]),
        Probe('ConnJunction', 'clear', ('CheekL', 'PivotPin', 'Bearing608'),
              [box('ConnJunction', [outer+0.5, ch_y-4, B.DECK_TOP+10], [3, 8, 30])]),
    ])
    phone = next(c for n, _, _, c in purchased() if n == 'Phone')
    for name, delta in (('PhoneClaw', [0, 3, 0]), ('PhoneRail', [0, 0, -3])):
        rows.append(Probe(name, 'hit', ('BackBox',), renamed(phone, 'Phone', name) +
                          [move([name], translate=delta)] + place(name, 'tilt', B.AX)))
    bearing = next(c for n, _, _, c in purchased() if n == 'Bearing608')
    rows.append(Probe('BearingLip', 'hit', ('CheekL',), renamed(bearing, 'Bearing608', 'BearingLip') +
                      [move(['BearingLip'], translate=[2, 0, 0])] + place('BearingLip', 'pan', B.AX)))
    length = B.BOX_PIVOT_X1+D['box_w']/2
    # ノーゴーはピン径を基準にする。穴がピン径 + PIVOT_FIT より大きいと、必要な重なり体積に届かず落ちる
    nogo = D['pin_d']+0.8
    ring = math.pi/4*(nogo**2-(D['pin_d']+PIVOT_FIT)**2)*length
    for name, mode, diameter, minimum in (
            ('PivotNoGo', 'hit', nogo, ring*0.8),
            ('PivotGo', 'clear', D['pin_d']-0.3, PROBE_LIMIT)):
        rows.append(Probe(name, mode, ('BackBox',),
                          [cylinder(name, [-D['box_w']/2, B.AX[1], B.AX[2]], diameter, length, 'X')], minimum))
    # UsbPlug/BackBox は全チルト姿勢の実体判定で検査済み。
    return rows


def assess_probe(probe, result):
    issues = completeness(result, probe.name, 'pan=0°, tilt=0°', ('overlaps', 'near', 'unchecked'))
    overlaps = {}
    for r in result.get('overlaps', []) + result.get('near', []):
        if probe.name not in (r['a'], r['b']):
            issues.append(Finding('検査完全性', probe.name, '指定外の probe ペアが結果にある'))
            continue
        other = r['b'] if r['a'] == probe.name else r['a']
        if other not in probe.targets:
            issues.append(Finding('検査完全性', probe.name, f'指定外の相手={other}'))
        overlaps[other] = max(overlaps.get(other, 0.0), r.get('volume_mm3', r.get('overlap_mm3', 0.0)))
    for target in probe.targets:
        volume = overlaps.get(target, 0.0)
        failed = (volume > PROBE_LIMIT if probe.mode == 'clear'
                  else volume <= 0 or volume < probe.minimum)
        if failed:
            issues.append(Finding('probe', f'{probe.name}/{target}',
                f'{probe.mode}: 重なり={volume:.6f}mm³, ' +
                (f'許容≤{PROBE_LIMIT:g}' if probe.mode == 'clear' else
                 (f'必要≥{probe.minimum:.6f}mm³' if probe.minimum > 0 else '正の重なりが必要')),
                'pan=0°, tilt=0°', volume))
    return issues, max(overlaps.values(), default=0.0)


def probe_job(directory, baseline):
    cmds, names = scene()
    rows = probes()
    for p in rows:
        cmds.extend(p.shapes)
    expected = names + [p.name for p in rows]
    cmds.append(command('inspect.measure', bodies=expected))
    for p in rows:
        cmds.append(command('inspect.clearance', bodies=[p.name], against=list(p.targets),
                            clearance=0, min_volume=PROBE_LIMIT))
    results = execute(cmds, directory, 'probes')
    volumes, issues = measurements(results[-len(rows)-1], expected, 'probe シーン')
    for name in PRINTED:
        if name in volumes and name in baseline and abs(volumes[name]-baseline[name])/baseline[name] > VOLUME_RTOL:
            issues.append(Finding('部品体積', name, 'probe 生成で印刷部品の体積が変化'))
    status = []
    for p, result in zip(rows, results[-len(rows):]):
        failures, volume = assess_probe(p, result)
        issues.extend(failures)
        status.append((p, failures, volume))
    return issues, status


def stl_components(path):
    """バイナリ STL の三角形を共有辺で union-find。頂点だけの接触は結合しない。

    頂点は STL に記録された float32 座標の完全一致を使う。距離による溶接は
    近接した別部品をつなぐため行わない。-0.0 と +0.0 は tuple 比較で同一。
    """
    with open(path, 'rb') as stream:
        header = stream.read(84)
        if len(header) != 84:
            raise ValueError('STL ヘッダが短い')
        count = struct.unpack_from('<I', header, 80)[0]
        if count == 0 or Path(path).stat().st_size != 84+50*count:
            raise ValueError('空または不正なバイナリ STL サイズ')
        parent = list(range(count))
        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        edges = {}
        for i in range(count):
            values = struct.unpack('<12fH', stream.read(50))
            vertices = [tuple(values[k:k+3]) for k in (3, 6, 9)]
            if not all(math.isfinite(v) for p in vertices for v in p) or len(set(vertices)) != 3:
                raise ValueError(f'三角形 {i} の頂点が不正')
            for a, b in ((0, 1), (1, 2), (2, 0)):
                edge = tuple(sorted((vertices[a], vertices[b])))
                if edge in edges:
                    parent[root(i)] = root(edges[edge])
                else:
                    edges[edge] = i
    return len({root(i) for i in range(count)})


def check_stl(path, name):
    try:
        count = stl_components(path)
        return ([] if count == 1 else [Finding('STL 連結', name, f'成分数={count} (期待=1)', value=count)]), count
    except (OSError, ValueError, struct.error) as exc:
        return [Finding('STL 連結', name, str(exc))], None


def standalone(directory, part):
    cmds = B.PART_BUILDERS[part]()['commands']
    body = B.PLACEMENTS[part]['body']
    result = execute(cmds+[command('inspect.measure', bodies=[body])], directory, f'single-{part}')[-1]
    return measurements(result, [body], f'単体 {part}')


def run(verbose=True) -> list[str]:
    start = time.monotonic()
    findings, baseline, gap_minima = [], {}, {}
    max_relative = 0.0
    def say(message):
        if verbose:
            print(message, flush=True)
    say('組立検査: 開始 (単位 mm / mm³、実体干渉の許容 0.01mm³)')
    for name in B.PART_BUILDERS:
        errors, count = check_stl(Path(B.ROOT)/'cad'/'parts'/f'{name}.stl', name)
        findings.extend(errors)
        say(f"STL 連結: {'FAIL' if errors else 'OK'} {name}: 成分数={count if count is not None else '取得不可'}")
    with tempfile.TemporaryDirectory(prefix='check-assembly-') as directory:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(standalone, directory, p): p for p in B.PART_BUILDERS}
            for future in as_completed(futures):
                try:
                    volumes, errors = future.result()
                    baseline.update(volumes)
                    findings.extend(errors)
                except Exception as exc:
                    findings.append(Finding('検査実行', futures[future], str(exc)))
            say(f"単体体積: {'OK' if len(baseline) == len(PRINTED) else 'FAIL'} {len(baseline)}/{len(PRINTED)} 部品")
            jobs = [(0, a, ('pan', 'tilt')) for a in angles(float(D['tilt_check']), 5)]
            jobs += [(a, 0, ('base', 'pan', 'tilt')) for a in angles(float(D['pan_check']), 10)]
            say(f'姿勢掃引: チルト {len(angles(float(D["tilt_check"]), 5))} + パン {len(angles(float(D["pan_check"]), 10))} 姿勢、並列数={WORKERS}')
            futures = {pool.submit(pose_job, directory, i, pan, tilt, groups, baseline): (pan, tilt)
                       for i, (pan, tilt, groups) in enumerate(jobs)}
            probe_future = pool.submit(probe_job, directory, baseline)
            completed = 0
            for future in as_completed(futures):
                pan, tilt = futures[future]
                try:
                    errors, gaps, relative = future.result()
                    findings.extend(errors)
                    max_relative = max(max_relative, relative)
                    for p, gap in gaps.items():
                        if p not in gap_minima or gap < gap_minima[p][0]:
                            gap_minima[p] = (gap, f'pan={pan:+g}°, tilt={tilt:+g}°')
                except Exception as exc:
                    findings.append(Finding('検査実行', '姿勢掃引', str(exc), f'pan={pan:+g}°, tilt={tilt:+g}°'))
                completed += 1
                if completed % 10 == 0 or completed == len(jobs):
                    say(f'姿勢掃引: {completed}/{len(jobs)} 完了')
            try:
                errors, status = probe_future.result()
                findings.extend(errors)
                for p, failures, volume in status:
                    say(f"probe: {'FAIL' if failures else 'OK'} {p.name} ({p.mode}): 最大重なり={volume:.6f}" +
                        ((f', 必要≥{p.minimum:.6f}' if p.minimum > 0 else ', 必要>0') if p.mode == 'hit' else ''))
            except Exception as exc:
                findings.append(Finding('検査実行', 'probe', str(exc)))
    for kind in ('検査実行', '検査完全性', '実体干渉', '最小隙間', '部品体積'):
        selected = [f for f in findings if f.kind == kind]
        say(f"{kind}: {'FAIL' if selected else 'OK'} ({len(selected)} 件)" +
            (f', 最大相対差={max_relative:.3g} / 許容={VOLUME_RTOL:g}' if kind == '部品体積' else ''))
    for p, (gap, pose) in sorted(gap_minima.items()):
        say(f"隙間 {'/'.join(p)}: {'≥2.000000' if gap >= CLEARANCE_QUERY else f'{gap:.6f}'}mm [{pose}]")
    # 同じペアの姿勢違いは最悪値と件数にまとめ、返り値は各姿勢の詳細を保持する。
    grouped = {}
    for f in findings:
        grouped.setdefault((f.kind, f.key), []).append(f)
    for key in sorted(grouped):
        rows = grouped[key]
        worst = max(rows, key=lambda f: f.value)
        say('FAIL ' + worst.message() + (f' (計{len(rows)}件)' if len(rows) > 1 else ''))
    say(f"組立検査: {'FAIL' if findings else 'OK'} 失敗={len(findings)}件、所要={time.monotonic()-start:.2f}秒")
    return [f.message() for f in findings]


def selftest(verbose=True):
    """意図的な破損を本番の判定関数へ渡す。CAD 判定も本物の CLI を使う。"""
    failures = []
    def expect(label, condition):
        if verbose:
            print(f"自己テスト: {'OK' if condition else 'FAIL'} {label}", flush=True)
        if not condition:
            failures.append(label)
    with tempfile.TemporaryDirectory(prefix='check-assembly-selftest-') as directory:
        baseline = {}
        for part in ('back-box', 'cheek-right'):
            volumes, errors = standalone(directory, part)
            baseline.update(volumes)
            expect(f'{part} 単体測定', not errors)
        cmds = []
        names = ['BackBox', 'CheekR']
        for part in ('back-box', 'cheek-right'):
            p = B.PLACEMENTS[part]
            cmds += B.PART_BUILDERS[part]()['commands'] + place(p['body'], p['group'], p['at'])
        def checks():
            return [command('inspect.measure', bodies=names), command('inspect.interference', bodies=names),
                    command('inspect.clearance', bodies=names, clearance=2.0)]
        cmds += checks() + [move(['BackBox'], translate=[2, 0, 0])] + checks()
        result = execute(cmds, directory, 'shift')
        clean = result[-7:-4]
        broken = result[-3:]
        ok, _, _ = assess_pose(*clean, names, baseline, '自己テスト 正常')
        bad, _, _ = assess_pose(*broken, names, baseline, '自己テスト +X2mm')
        expect('正常配置は干渉・隙間・体積 OK', not ok)
        expect('BackBox +X2mm と CheekR の干渉を検出',
               any(f.kind == '実体干渉' and f.key == 'BackBox/CheekR' for f in bad))
        altered = copy.deepcopy(clean)
        altered[0]['bodies'][0]['volume_mm3'] *= 0.99
        expect('組立時の 1% の削り込みを検出',
               any(f.kind == '部品体積' for f in assess_pose(*altered, names, baseline, '体積破損')[0]))
        for field, value in (('unchecked', [{'a': 'BackBox', 'b': 'CheekR', 'reason': 'test'}]),
                             ('budget_exceeded', True), ('truncated', True)):
            altered = copy.deepcopy(clean)
            altered[1][field] = value
            expect(f'{field} を FAIL 扱い',
                   any(f.kind == '検査完全性' for f in assess_pose(*altered, names, baseline, field)[0]))
        altered = copy.deepcopy(clean)
        altered[0]['bodies'].pop()
        expect('期待したボディの欠落を検出',
               any(f.kind == '検査完全性' for f in assess_pose(*altered, names, baseline, '欠落')[0]))
        cmds = [box('PanDeck', [0, 0, 0], [1, 1, 1]), box('BaseShell', [1.25, 0, 0], [1, 1, 1])]
        result = execute(cmds + [command('inspect.measure'), command('inspect.interference'),
                                command('inspect.clearance', clearance=2.0)], directory, 'gap')
        ref, _ = measurements(result[-3], ['PanDeck', 'BaseShell'], '')
        expect('0.25mm の隙間を 0.5mm 要求で検出', any(f.kind == '最小隙間' for f in
               assess_pose(*result[-3:], ['PanDeck', 'BaseShell'], ref, '隙間破損')[0]))
        probe = Probe('PanDeck', 'clear', ('BaseShell',), [])
        expect('clear probe の正常な隙間を確認', not assess_probe(probe, result[-1])[0])
        probe.mode = 'hit'
        expect('hit probe の空振りを検出', bool(assess_probe(probe, result[-1])[0]))
        probe.mode = 'clear'
        result = execute(cmds + [move(['BaseShell'], translate=[-0.5, 0, 0]),
                                command('inspect.clearance', bodies=['PanDeck'], against=['BaseShell'],
                                        min_volume=PROBE_LIMIT)], directory, 'probe')[-1]
        expect('clear probe の重なりを検出', bool(assess_probe(probe, result)[0]))
        probe.mode, probe.minimum = 'hit', 0.2
        expect('hit probe の支えを確認', not assess_probe(probe, result)[0])
        probe.minimum = 0.3
        expect('hit probe の必要体積不足を検出', bool(assess_probe(probe, result)[0]))
        for label, shapes, expected in (
                ('connected', [box('Test', [0, 0, 0], [2, 2, 2])], 1),
                ('detached', [box('Test', [0, 0, 0], [2, 2, 2]), box('Test', [5, 5, 5], [1, 1, 1], 'join')], 2)):
            execute(shapes, directory, label, stl=True)
            errors, count = check_stl(Path(directory)/f'{label}.stl', label)
            expect(f'STL {label}: 成分数={expected}', count == expected and bool(errors) == (expected != 1))
        # 頂点だけの共有を辺共有と取り違えない (STL 処理の例外範囲で作る fixture)。
        path = Path(directory)/'vertex-only.stl'
        triangles = [((0, 0, 0), (1, 0, 0), (0, 1, 0)),
                     ((0, 0, 0), (-1, 0, 0), (0, -1, 0))]
        with path.open('wb') as stream:
            stream.write(bytes(80)+struct.pack('<I', len(triangles)))
            for triangle in triangles:
                stream.write(struct.pack('<12fH', 0, 0, 1, *(v for p in triangle for v in p), 0))
        expect('STL 頂点だけの共有は成分数=2', stl_components(path) == 2)
        expect('STL 欠落を検出', bool(check_stl(Path(directory)/'missing.stl', 'missing')[0]))
    if verbose:
        print(f"自己テスト: {'FAIL' if failures else 'OK'} 失敗={len(failures)}件", flush=True)
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selftest', action='store_true', help='壊した入力の検出を確認する')
    args = parser.parse_args()
    try:
        failures = selftest() if args.selftest else run()
    except Exception as exc:
        print(f'検査実行: FAIL {type(exc).__name__}: {exc}', flush=True)
        return 1
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
