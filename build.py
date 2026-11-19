#!/usr/bin/env python3
"""スマッホチャンの生成スクリプト。

params.json を正本として、次を全部生成する:
  - cad/parts/*.json と cad/fit-gauge-servo.json (SolidCraft 用) + STL/.sc
  - simulator.html / showcase.html の GENERATED ブロック (P と PART_META)
  - MECHANISM.md の GENERATED ブロック (主要寸法表)
  - mechanism.svg

使い方: python3 build.py
SolidCraft CLI の場所は環境変数 SOLIDCRAFT で上書きできる。
"""
import json
import math
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SC = os.environ.get(
    "SOLIDCRAFT",
    os.path.expanduser(
        "~/workspace/tools/solidcraft/solidcraft-0.44.1-linux-x86_64/bin/solidcraft-cli"
    ),
)

P = json.load(open(os.path.join(ROOT, "params.json")))
PH, SV, MB, AT, D, G = P["phone"], P["servo"], P["motion_base"], P["atom"], P["design"], P["gauge"]

# ---- 派生値 ----
DECK_TOP = D["shell_top"] + D["deck_t"]              # 48
CHEEK_CX = D["cheek_x_in"] + D["cheek_t"] / 2        # 39
CHEEK_DROP = D["axis_h"] - DECK_TOP                  # 68 (軸からデッキ上面まで)
SERVO_CX = -SV["shaft_offset"]                       # -5.5 (パンサーボ中心)
PAN_SERVO_BOTTOM = D["flange_z"] - SV["flange_from_bottom"]  # 9.5
AXIS_FB = -D["axis_back"]                            # -13 (前後方向の軸位置)
BOSS_XY = round(D["boss_r"] / math.sqrt(2), 2)       # 32.53
BOX_FRONT = D["axis_back"] - PH["t"] / 2 - D["spacer_t"]     # 6.5 (背面ボックス主箱の前面)


def r2(x):
    return round(float(x), 2)


def box(body=None, corner=None, size=None, op=None):
    p = {"corner": [r2(v) for v in corner], "length": r2(size[0]),
         "width": r2(size[1]), "height": r2(size[2])}
    if body:
        p["body_name"] = body
    if op:
        p["operation"] = op
    return {"command": "solid.box", "params": p}


def cyl(body=None, base=None, d=None, h=None, axis=None, op=None):
    p = {"base": [r2(v) for v in base], "diameter": r2(d), "height": r2(h)}
    if body:
        p["body_name"] = body
    if axis:
        p["axis"] = axis
    if op:
        p["operation"] = op
    return {"command": "solid.cylinder", "params": p}


def script(body, cmds):
    for c in cmds:
        if c["params"].get("operation") == "join":
            c["params"]["targets"] = [body]
    return {"commands": cmds}


# ================= CAD ビルダ =================

def build_base_shell():
    b = BOSS_XY
    cmds = [
        cyl("BaseShell", [0, 0, D["plate_t"]], D["base_d"], D["shell_top"] - D["plate_t"]),
        cyl(base=[0, 0, D["plate_t"]], d=D["base_d"] - 2 * D["shell_wall"],
            h=D["shell_top"] - D["plate_t"] - D["shell_wall"], op="cut"),
        cyl(base=[0, 0, D["shell_top"] - 5], d=D["shell_top_hole_d"], h=8, op="cut"),
    ]
    for sx in (1, -1):
        for sy in (1, -1):
            cmds.append(cyl(base=[sx * b, sy * b, D["plate_t"]], d=D["boss_d"], h=10, op="join"))
    for sx in (1, -1):
        for sy in (1, -1):
            cmds.append(cyl(base=[sx * b, sy * b, 0], d=D["boss_pilot_d"], h=16, op="cut"))
    cmds.append(box(corner=[-D["base_d"] / 2 - 2, D["usb_fb_center"] - D["usb_w"] / 2, D["usb_z"]],
                    size=[8, D["usb_w"], D["usb_h"]], op="cut"))
    cmds.append(box(corner=[-D["shell_slot_w"] / 2, -D["base_d"] / 2 - 1, D["slot_z"]],
                    size=[D["shell_slot_w"], D["slot_depth"], D["slot_h"]], op="cut"))
    return script("BaseShell", cmds)


def build_bottom_plate():
    b = BOSS_XY
    cmds = [
        cyl("BottomPlate", [0, 0, 0], D["plate_d"], D["plate_t"]),
        box(corner=[-D["tray_l"] / 2, D["stack_z"] - D["tray_w"] / 2, D["plate_t"]],
            size=[D["tray_l"], D["tray_w"], D["tray_h"]], op="join"),
    ]
    for sx in (1, -1):
        for sy in (1, -1):
            cmds.append(cyl(base=[sx * b, sy * b, -2], d=D["plate_hole_d"], h=8, op="cut"))
    cmds.append(box(corner=[SERVO_CX - D["saddle_base_l"] / 2, -D["saddle_base_w"] / 2, D["plate_t"]],
                    size=[D["saddle_base_l"], D["saddle_base_w"], D["saddle_base_h"]], op="join"))
    wall_z = D["plate_t"] + D["saddle_base_h"]
    for span in D["saddle_walls"]:
        cmds.append(box(corner=[SERVO_CX + span[0], -D["saddle_wall_w"] / 2, wall_z],
                        size=[span[1] - span[0], D["saddle_wall_w"], D["flange_z"] - wall_z], op="join"))
    for s in (1, -1):
        ear_x = SERVO_CX + s * SV["ear_pitch"] / 2
        cmds.append(box(corner=[ear_x - D["slot_l"] / 2, -D["slot_w"] / 2, D["flange_z"] - 6],
                        size=[D["slot_l"], D["slot_w"], 8], op="cut"))
    cmds.append(box(corner=[MB["l"] / 2 + 0.2, D["stack_z"] - MB["w"] / 2, D["plate_t"]],
                    size=[D["lip_t"], MB["w"], D["lip_h"]], op="join"))
    return script("BottomPlate", cmds)


def build_pan_deck():
    cmds = [
        cyl("PanDeck", [0, 0, 0], D["base_d"], D["deck_t"]),
        cyl(base=[0, 0, -D["hub_h"]], d=D["hub_d"], h=D["hub_h"], op="join"),
        cyl(base=[0, 0, -D["hub_h"] - 1], d=D["hub_bore_d"], h=22, op="cut"),
    ]
    for (hx, hy) in [(D["horn_hole_r"], 0), (-D["horn_hole_r"], 0), (0, D["horn_hole_r"]), (0, -D["horn_hole_r"])]:
        cmds.append(cyl(base=[hx, hy, -D["hub_h"] - 1], d=D["horn_hole_d"], h=6, op="cut"))
    # 頬板の下穴・ケーブル穴・裏溝は「チルト軸の前後位置」を基準に置く (軸は中心面より後ろ)
    ch_y = (D["channel_y"][0] + D["channel_y"][1]) / 2
    for sx in (1, -1):
        for fy in D["foot_holes_y"]:
            cmds.append(cyl(base=[sx * CHEEK_CX, AXIS_FB + fy, D["deck_t"] - 5],
                            d=D["cheek_pilot_d"], h=6, op="cut"))
    cmds.append(cyl(base=[-CHEEK_CX, AXIS_FB + ch_y, -2], d=D["cable_hole_d"], h=12, op="cut"))
    cmds.append(box(corner=[-CHEEK_CX - 1, AXIS_FB + ch_y - D["groove_w"] / 2, 0],
                    size=[D["groove_len"], D["groove_w"], D["groove_depth"]], op="cut"))
    return script("PanDeck", cmds)


def build_cheek(left):
    body = "CheekL" if left else "CheekR"
    y0, y1 = D["cheek_y_back"], D["cheek_y_front"]
    cmds = [
        box(body, [0, y0, -CHEEK_DROP], [D["cheek_t"], y1 - y0, CHEEK_DROP + D["boss_r"]]),
        cyl(base=[0, 0, 0], d=2 * D["boss_r"], h=D["cheek_t"], axis="X", op="join"),
        box(corner=[-D["foot_ext"], y0, -CHEEK_DROP],
            size=[D["cheek_t"] + 2 * D["foot_ext"], y1 - y0, 4], op="join"),
        cyl(base=[-2, 0, 0], d=D["bearing_hole_d"] if left else D["servo_bearing_hole_d"],
            h=D["cheek_t"] + 4, axis="X", op="cut"),
    ]
    for fy in D["foot_holes_y"]:
        cmds.append(cyl(base=[D["cheek_t"] / 2, fy, -CHEEK_DROP - 2], d=D["cheek_hole_d"], h=8, op="cut"))
    if left:
        cmds.append(box(corner=[(D["cheek_t"] - D["channel_w"]) / 2, D["channel_y"][0], -CHEEK_DROP],
                        size=[D["channel_w"], D["channel_y"][1] - D["channel_y"][0], CHEEK_DROP + 2], op="cut"))
    else:
        cmds.append(cyl(base=[-1, 0, 0], d=D["horn_cb_d"], h=D["horn_cb_depth"] + 1, axis="X", op="cut"))
        for (py, pz) in [(D["horn_pilot_r"], 0), (-D["horn_pilot_r"], 0), (0, D["horn_pilot_r"]), (0, -D["horn_pilot_r"])]:
            cmds.append(cyl(base=[D["horn_cb_depth"] - 0.5, py, pz], d=D["horn_pilot_d"], h=4, axis="X", op="cut"))
    return script(body, cmds)


def build_back_box():
    w2 = D["box_w"] / 2
    spacer_y1 = D["axis_back"] - PH["t"] / 2            # 8.25 スマホ背面に接する面
    arm_y1 = D["axis_back"] + PH["t"] / 2 + D["spacer_t"]                    # 19.5 アーム前端
    pocket_depth = SV["body_h"] + D["pocket_wire_space"]
    pocket_x0 = w2 - pocket_depth
    cmds = [
        box("BackBox", [-w2, -D["box_back"], -D["box_l"] / 2], [D["box_w"], D["box_t"], D["box_l"]]),
        box(corner=[-w2, BOX_FRONT, -D["box_l"] / 2], size=[D["box_w"], D["spacer_t"], D["box_l"]], op="join"),
        box(corner=[D["cheek_x_in"] - D["arm_t"], BOX_FRONT, -D["grip"]],
            size=[D["arm_t"], arm_y1 - BOX_FRONT, D["arm_top"] + D["grip"]], op="join"),
        box(corner=[-D["cheek_x_in"], BOX_FRONT, -D["grip"]],
            size=[D["arm_t"], arm_y1 - BOX_FRONT, D["arm_top"] + D["grip"]], op="join"),
        box(corner=[-D["cheek_x_in"], BOX_FRONT, -D["grip"] - D["ledge_h"]],
            size=[2 * D["cheek_x_in"], D["ledge_w"], D["ledge_h"]], op="join"),
        box(corner=[pocket_x0, -(SV["body_w"] + D["pocket_clr"]) / 2, -(SV["body_l"] + D["pocket_clr"]) / 2],
            size=[pocket_depth, SV["body_w"] + D["pocket_clr"], SV["body_l"] + D["pocket_clr"]], op="cut"),
        box(corner=[D["flange_recess_x"], -(SV["body_w"] + D["recess_clr"]) / 2, -(SV["flange_l"] + D["recess_clr"]) / 2],
            size=[SV["flange_t"] + 1, SV["body_w"] + D["recess_clr"], SV["flange_l"] + D["recess_clr"]], op="cut"),
        cyl(base=[D["boss_hole_x"], 0, 0], d=D["boss_hole_d"], h=10, axis="X", op="cut"),
        cyl(base=[-w2 - 3, 0, 0], d=D["pivot_hole_d"], h=8, axis="X", op="cut"),
    ]
    for s in (1, -1):
        cmds.append(cyl(base=[D["ear_pilot_x"], 0, s * SV["ear_pitch"] / 2],
                        d=D["ear_pilot_d"], h=3.5, axis="X", op="cut"))
    cmds.append(box(corner=[-D["charge_ch_w"] / 2, -D["box_back"], -D["box_l"] / 2 - 2],
                    size=[D["charge_ch_w"], D["box_back"] + spacer_y1 - 0.25, D["charge_ch_depth"]], op="cut"))
    ch = D["servo_ch_h"]
    cmds.append(box(corner=[-w2, -ch / 2, -ch / 2],
                    size=[w2 + pocket_x0 + 5, ch, ch], op="cut"))
    return script("BackBox", cmds)


def build_gauge():
    cmds = [box("FitGauge", [0, 0, 0], [G["l"], G["w"], G["t"]])]
    for cx, clr in zip(G["centers"], G["clearances"]):
        sl, sw = G["nominal_l"] + clr, G["nominal_w"] + clr
        cmds.append(box(corner=[cx - sl / 2, (G["w"] - sw) / 2, -2], size=[sl, sw, 10], op="cut"))
    return script("FitGauge", cmds)


PART_BUILDERS = {
    "base-shell": build_base_shell,
    "bottom-plate": build_bottom_plate,
    "pan-deck": build_pan_deck,
    "cheek-left": lambda: build_cheek(True),
    "cheek-right": lambda: build_cheek(False),
    "back-box": build_back_box,
}


# ================= 生成と検証 =================

def fmt_dim(v):
    return ("%g" % v)


def write_part(name, data, subdir):
    path = os.path.join(ROOT, subdir, name + ".json")
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    return path


def sc_eval(path):
    out = subprocess.run([SC, "eval", path], capture_output=True, text=True)
    d = json.loads(out.stdout)
    assert d["body_count"] == 1, f"{path}: body_count={d['body_count']}"
    b = d["bodies"][0]
    assert b["closed"], f"{path}: not closed"
    bb = b["bbox"]
    dims = "×".join(fmt_dim(round(bb["max"][i] - bb["min"][i], 2)) for i in range(3))
    return {"vol": round(b["volume_mm3"] / 1000.0, 1), "dims": dims,
            "vol_mm3": round(b["volume_mm3"])}


def sc_run(path):
    base = path[:-5]
    subprocess.run([SC, "run", path, "--out", base + ".stl", "--save", base + ".sc"],
                   capture_output=True, text=True)


def derived():
    """設計チェック用の派生値 (MECHANISM.md と画面表示で共有)"""
    gap_flange = (-SV["body_w"] / 2) - (D["stack_z"] + MB["w"] / 2)
    corner_r = math.hypot(MB["l"] / 2, abs(D["stack_z"] - MB["w"] / 2))
    gap_corner = (D["base_d"] / 2 - D["shell_wall"]) - corner_r
    stack_top = D["plate_t"] + MB["h"] + AT["h"]
    gap_top = (D["shell_top"] - D["shell_wall"]) - stack_top
    mass = (PH["mass_g"] + D["holder_mass_g"]) / 1000.0
    t45 = math.radians(45)
    torque45 = mass * (D["cg_along"] / 10 * math.sin(t45) + D["cg_perp"] / 10 * math.cos(t45))
    return {
        "deck_top": DECK_TOP,
        "total_height": D["axis_h"] + (PH["h"] - D["grip"]),
        "moving_mass_g": PH["mass_g"] + D["holder_mass_g"],
        "phone_bottom_clearance": D["axis_h"] - D["grip"] - DECK_TOP,
        "gap_flange_mm": round(gap_flange, 1),
        "gap_corner_mm": round(gap_corner, 1),
        "gap_top_mm": round(gap_top, 1),
        "torque45": round(torque45, 2),
    }


# ================= HTML / MD / SVG への注入 =================

GEN_BEGIN = "/* === GENERATED FROM params.json (build.py) — 手で編集しない === */"
GEN_END = "/* === /GENERATED === */"


def inject_html(fname, meta, drv):
    path = os.path.join(ROOT, fname)
    html = open(path).read()
    if GEN_BEGIN not in html:
        print(f"  skip {fname}: GENERATED マーカーなし")
        return
    block = (GEN_BEGIN + "\nconst P = " + json.dumps(P, ensure_ascii=False) +
             ";\nconst PART_META = " + json.dumps(meta, ensure_ascii=False) +
             ";\nconst DERIVED = " + json.dumps(drv, ensure_ascii=False) + ";\n" + GEN_END)
    i, j = html.index(GEN_BEGIN), html.index(GEN_END) + len(GEN_END)
    open(path, "w").write(html[:i] + block + html[j:])
    print(f"  injected {fname}")


def inject_md(meta, drv):
    path = os.path.join(ROOT, "MECHANISM.md")
    md = open(path).read()
    begin, end = "<!-- GENERATED:BEGIN -->", "<!-- GENERATED:END -->"
    if begin not in md:
        print("  skip MECHANISM.md: マーカーなし")
        return
    rows = [
        ("土台", f"円 Ø{D['base_d']}、シェル高さ {D['shell_top']}（底板 {D['plate_t']} の上）、壁 {D['shell_wall']}、天面穴 Ø{D['shell_top_hole_d']}"),
        ("デッキ", f"Ø{D['base_d']}×{D['deck_t']}（上面 = 高さ {drv['deck_top']}）、裏ハブ Ø{D['hub_d']}"),
        ("チルト軸", f"高さ {D['axis_h']}、スマホ中心面の {D['axis_back']}mm 後ろ"),
        ("スマホ掴み", f"下端から {D['grip']}mm（スマホ下端とデッキの隙間 {drv['phone_bottom_clearance']}mm）"),
        ("全体の高さ", f"約 {drv['total_height']}mm"),
        ("可動部質量", f"{drv['moving_mass_g']}g（スマホ {PH['mass_g']} + ホルダ系 {D['holder_mass_g']}）"),
        ("チルト保持トルク（45°・最悪）", f"{drv['torque45']} kg·cm（MG90S {SV['torque_mg90s']} に対して約 {round(SV['torque_mg90s'] / drv['torque45'], 1)} 倍の余裕）"),
        ("土台内の隙間", f"スタック⇔フランジ {drv['gap_flange_mm']}mm / スタック角⇔内壁 {drv['gap_corner_mm']}mm / スタック上面⇔天面 {drv['gap_top_mm']}mm"),
        ("サーボポケットのクリアランス", f"+{D['pocket_clr']}mm（仮。フィットゲージの実測で確定する）"),
    ]
    table = "\n".join(f"| {k} | {v} |" for k, v in rows)
    vols = "、".join(f"{n} {meta[n]['vol']}cm³" for n in
                     ["bottom-plate", "base-shell", "pan-deck", "cheek-left", "cheek-right", "back-box"])
    block = (begin + "\n\n主要寸法は params.json から自動生成（build.py）。数値を変えるときは params.json を直すこと。\n\n"
             "| 項目 | 値 |\n|---|---|\n" + table +
             f"\n\n印刷部品の体積（SolidCraft 実測）: {vols}\n\n" + end)
    i, j = md.index(begin), md.index(end) + len(end)
    open(path, "w").write(md[:i] + block + md[j:])
    print("  injected MECHANISM.md")


def write_svg(drv):
    """正面図 + 上面図の簡易図 (寸法は params 由来)"""
    bd = D["base_d"]
    ph_top = drv["total_height"]
    s = 1.05  # px per mm (正面図)
    ox, gy = 90, 300  # 原点 x / 地面 y
    def X(v): return ox + v * s
    def Y(v): return gy - v * s
    phone_w, phone_h = PH["w"], PH["h"]
    axis = D["axis_h"]
    ph_x0 = X(-phone_w / 2)
    ph_y1 = Y(axis + (phone_h - D["grip"]))
    ph_y0 = Y(axis - D["grip"])
    cheek_x0, cheek_x1 = X(-D["cheek_x_in"] - D["cheek_t"]), X(D["cheek_x_in"] + D["cheek_t"])
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 680 340" font-family="sans-serif" font-size="11">
<rect width="680" height="340" fill="#fffdf7"/>
<text x="20" y="24" font-size="14" font-weight="bold">スマッホチャン機構図 (params.json から自動生成)</text>
<!-- 正面図 -->
<line x1="30" y1="{gy}" x2="330" y2="{gy}" stroke="#999"/>
<rect x="{X(-bd/2):.0f}" y="{Y(D['shell_top']):.0f}" width="{bd*s:.0f}" height="{(D['shell_top']-D['plate_t'])*s:.0f}" fill="#dfe3e6" stroke="#666"/>
<rect x="{X(-D['plate_d']/2):.0f}" y="{Y(D['plate_t']):.0f}" width="{D['plate_d']*s:.0f}" height="{D['plate_t']*s:.0f}" fill="#9aa4ab" stroke="#666"/>
<rect x="{X(-bd/2):.0f}" y="{Y(DECK_TOP):.0f}" width="{bd*s:.0f}" height="{D['deck_t']*s:.0f}" fill="#dfe3e6" stroke="#666"/>
<rect x="{cheek_x0:.0f}" y="{Y(axis + D['boss_r']):.0f}" width="{D['cheek_t']*s:.0f}" height="{(CHEEK_DROP + D['boss_r'])*s:.0f}" fill="#dfe3e6" stroke="#666"/>
<rect x="{cheek_x1 - D['cheek_t']*s:.0f}" y="{Y(axis + D['boss_r']):.0f}" width="{D['cheek_t']*s:.0f}" height="{(CHEEK_DROP + D['boss_r'])*s:.0f}" fill="#dfe3e6" stroke="#666"/>
<rect x="{ph_x0:.0f}" y="{ph_y1:.0f}" width="{phone_w*s:.0f}" height="{phone_h*s:.0f}" fill="#1c2733" stroke="#666"/>
<circle cx="{X(0):.0f}" cy="{Y(axis):.0f}" r="3.5" fill="#e2708a"/>
<line x1="{X(-bd/2)-14:.0f}" y1="{Y(axis):.0f}" x2="{X(bd/2)+14:.0f}" y2="{Y(axis):.0f}" stroke="#e2708a" stroke-dasharray="4 3"/>
<text x="{X(bd/2)+18:.0f}" y="{Y(axis)+4:.0f}" fill="#e2708a">チルト軸 {axis}</text>
<text x="{X(-bd/2):.0f}" y="{gy+16}" fill="#666">土台 Ø{bd}</text>
<text x="{X(phone_w/2)+6:.0f}" y="{ph_y1+4:.0f}" fill="#666">全高 約{ph_top}</text>
<text x="{X(phone_w/2)+6:.0f}" y="{ph_y0:.0f}" fill="#666">掴み {D['grip']} (下端から)</text>
<!-- 上面図 -->
<circle cx="490" cy="150" r="{bd/2*1.5:.0f}" fill="#dfe3e6" stroke="#666"/>
<circle cx="490" cy="150" r="{(bd/2 - D['shell_wall'])*1.5:.0f}" fill="none" stroke="#bbb" stroke-dasharray="3 3"/>
<rect x="{490 - MB['l']/2*1.5:.0f}" y="{150 + (D['stack_z'] - MB['w']/2)*1.5:.0f}" width="{MB['l']*1.5:.0f}" height="{MB['w']*1.5:.0f}" fill="#f0d9a0" stroke="#666"/>
<rect x="{490 + SERVO_CX*1.5 - SV['body_l']/2*1.5:.0f}" y="{150 - SV['body_w']/2*1.5:.0f}" width="{SV['body_l']*1.5:.0f}" height="{SV['body_w']*1.5:.0f}" fill="#d77a7a" stroke="#666"/>
<circle cx="490" cy="150" r="3" fill="#e2708a"/>
<text x="490" y="{150 + bd/2*1.5 + 18:.0f}" text-anchor="middle" fill="#666">上面: 黄=ATOMスタック (後方 {abs(D['stack_z'])})、赤=パンサーボ</text>
<text x="380" y="40" font-size="12" font-weight="bold">上面図</text>
<text x="30" y="40" font-size="12" font-weight="bold">正面図</text>
</svg>
'''
    open(os.path.join(ROOT, "mechanism.svg"), "w").write(svg)
    print("  wrote mechanism.svg")


def main():
    print("== CAD 生成 ==")
    meta = {}
    for name, builder in PART_BUILDERS.items():
        path = write_part(name, builder(), "cad/parts")
        meta[name] = sc_eval(path)
        sc_run(path)
        print(f"  {name}: vol {meta[name]['vol']} cm³, dims {meta[name]['dims']}")
    gpath = write_part("fit-gauge-servo", build_gauge(), "cad")
    meta["fit-gauge"] = sc_eval(gpath)
    sc_run(gpath)
    print(f"  fit-gauge: vol {meta['fit-gauge']['vol']} cm³, dims {meta['fit-gauge']['dims']}")
    drv = derived()
    print("== 注入 ==")
    inject_html("simulator.html", meta, drv)
    inject_html("showcase.html", meta, drv)
    inject_md(meta, drv)
    write_svg(drv)
    print("== 派生値 ==")
    for k, v in drv.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
