#!/usr/bin/env python3
"""スマッホチャンの生成スクリプト。

params.json を正本として、次を全部生成する:
  - cad/parts/*.json と cad/fit-gauge-servo.json (SolidCraft 用) + STL/.sc
  - simulator.html / showcase.html の GENERATED ブロック (P と PART_META)
  - MECHANISM.md の GENERATED ブロック (主要寸法表)
  - mechanism.svg

使い方: python3 build.py            (最後に check_assembly.py の組立チェックも走る)
        python3 build.py --no-check  (生成だけ)
SolidCraft CLI の場所は環境変数 SOLIDCRAFT で上書きできる。
"""
import json
import math
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SC = (os.environ.get("SOLIDCRAFT") or shutil.which("solidcraft-cli") or os.path.expanduser(
    "~/workspace/tools/solidcraft/solidcraft-0.44.1-linux-x86_64/bin/solidcraft-cli"))

P = json.load(open(os.path.join(ROOT, "params.json")))
PH, SV, MB, AT, D, G = P["phone"], P["servo"], P["motion_base"], P["atom"], P["design"], P["gauge"]

# ---- 派生値 (高さは世界座標、チルト部はチルト軸原点のローカル座標) ----
DECK_BOTTOM = D["shell_top"] + D["pan_gap"]          # 41 (シェル天面との間は摺動パッド)
DECK_TOP = DECK_BOTTOM + D["deck_t"]                 # 48
CHEEK_CX = D["cheek_x_in"] + D["cheek_t"] / 2        # 39
CHEEK_DROP = D["axis_h"] - DECK_TOP                  # 68 (軸からデッキ上面まで)
SERVO_CX = -SV["shaft_offset"]                       # -5.5 (パンサーボ中心)
PAN_SERVO_BOTTOM = D["flange_z"] - SV["flange_from_bottom"]  # 9.5
PAN_SHAFT_TIP = PAN_SERVO_BOTTOM + SV["shaft_tip"]   # 39
PAN_HORN_TOP = PAN_SHAFT_TIP + D["horn_top_above_tip"]       # 40 (ホーン上面。実物で要実測)
HUB_H = DECK_BOTTOM - PAN_HORN_TOP                   # 1.0 (デッキ裏のホーン座の高さ)
AXIS_FB = -D["axis_back"]                            # -13 (前後方向の軸位置)
AX = [0, AXIS_FB, D["axis_h"]]                       # チルト軸原点の世界座標
BOX_FRONT = D["axis_back"] - PH["t"] / 2 - D["spacer_t"]     # 6.5 (背面ボックス主箱の前面)
ARM_OUT = D["cheek_x_in"] - D["cheek_clr"]           # 34.5 (アーム外面。頬板内面と cheek_clr の隙間)
ARM_IN = ARM_OUT - D["arm_t"]                        # 32.0 (アーム内面)
PHONE_SIDE_CLR = ARM_IN - PH["w"] / 2                # 0.5 (スマホ側面の隙間。フェルト分)
ARM_Y1 = D["axis_back"] + PH["t"] / 2 + D["phone_clr_front"] + D["claw_t"]   # 19.55 (アーム/爪の前端)
# チルトサーボ: ホーン上面が右頬板の座ぐり底に一致する位置に置く (軸は +X 向き、軸ずれは -Z 側へ本体を寄せる)
TILT_FLANGE_X = (D["cheek_x_in"] + D["horn_cb_depth"] - D["horn_top_above_tip"]
                 - (SV["shaft_tip"] - SV["flange_from_bottom"]))             # 23.75 (フランジ下面)
TILT_BODY_X0 = TILT_FLANGE_X - SV["flange_from_bottom"]                      # 8.0 (サーボ底面)
TILT_BODY_X1 = TILT_BODY_X0 + SV["body_h"]                                   # 30.5 (ボディ上面)
TILT_ZC = -SV["shaft_offset"]                                                # -5.5 (ボディ中心の Z)
# 左ピボット: 608 は左頬板の外面側の座に入り、ピン頭が外から内輪を押さえる
BEARING_X0 = -(D["cheek_x_in"] + D["cheek_t"]) + (D["bearing_seat_depth"] - D["bearing_w"])   # -42.8 (608 の外側面)
PIN_X0 = BEARING_X0                                  # ピン頭の座面 (= 608 外側面) の世界 X
BOX_PIVOT_X1 = -D["box_w"] / 2 + D["pivot_len"]      # -25 (ボックス側ピボット穴の奥端)
SHELL_BOSSES = [(1, 0), (0, 1), (-1, 0), (0, -1)]    # 固定ボスの方向 (軸上の4か所)

assert 0.5 <= HUB_H <= 4, f"hub_h={HUB_H}: パンサーボ高さ・pan_gap・horn_top_above_tip を見直す"
assert PHONE_SIDE_CLR >= 0.3, f"スマホ側面の隙間 {PHONE_SIDE_CLR}"
assert TILT_BODY_X1 + 1.5 <= D["box_w"] / 2, "チルトサーボが背面ボックス右壁を突き抜ける"
assert PIN_X0 + D["pin_len"] >= BOX_PIVOT_X1 - 0.5, "ピンが背面ボックスの穴の奥まで届かない"


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
    """join も cut も対象ボディを明示する (複数部品を1つのスクリプトに並べても他部品を削らないように)"""
    for c in cmds:
        if c["params"].get("operation") in ("join", "cut"):
            c["params"]["targets"] = [body]
    return {"commands": cmds}


def mirror_x(cmds):
    """X 反転 (左頬板用)。箱は corner、X 軸の円柱は base を反対側の端へ移す"""
    for c in cmds:
        p = c["params"]
        if c["command"] == "solid.box":
            p["corner"][0] = r2(-(p["corner"][0] + p["length"]))
        elif p.get("axis") == "X":
            p["base"][0] = r2(-(p["base"][0] + p["height"]))
        else:
            p["base"][0] = r2(-p["base"][0])
    return cmds


def through_depth(r_in, far_edge, margin=1.0):
    """円弧の壁を確実に抜く切り込みの内端 (中心からの距離)。far_edge は開口の端の、中心線からの最大距離"""
    return math.sqrt(r_in ** 2 - far_edge ** 2) - margin


# ================= CAD ビルダ =================

def build_base_shell():
    r_out, r_in = D["base_d"] / 2, D["base_d"] / 2 - D["shell_wall"]
    col_h = D["shell_top"] - D["shell_wall"] - D["plate_t"]      # 天板まで届く柱 (逆さ印刷でサポート不要)
    br, bw = D["shell_boss_r"], D["boss_web_w"]
    cmds = [
        cyl("BaseShell", [0, 0, D["plate_t"]], D["base_d"], D["shell_top"] - D["plate_t"]),
        cyl(base=[0, 0, D["plate_t"]], d=2 * r_in, h=col_h, op="cut"),
        cyl(base=[0, 0, D["shell_top"] - 5], d=D["shell_top_hole_d"], h=8, op="cut"),
    ]
    for ux, uy in SHELL_BOSSES:
        cmds.append(cyl(base=[ux * br, uy * br, D["plate_t"]], d=D["boss_d"], h=col_h, op="join"))
        web = r_in + 1 - br                                      # 柱から壁の中まで届くつなぎ
        if ux:
            cmds.append(box(corner=[min(ux * br, ux * (br + web)), -bw / 2, D["plate_t"]], size=[web, bw, col_h], op="join"))
        else:
            cmds.append(box(corner=[-bw / 2, min(uy * br, uy * (br + web)), D["plate_t"]], size=[bw, web, col_h], op="join"))
    for ux, uy in SHELL_BOSSES:
        cmds.append(cyl(base=[ux * br, uy * br, 0], d=D["boss_pilot_d"], h=16, op="cut"))
    # ATOM の USB 開口 (左側面) と予備スロット (背面)。円弧の壁を確実に抜く深さまで切る
    y0, y1 = D["usb_fb_center"] - D["usb_w"] / 2, D["usb_fb_center"] + D["usb_w"] / 2
    x_in = through_depth(r_in, max(abs(y0), abs(y1)))
    cmds.append(box(corner=[-r_out - 2, y0, D["usb_z"]], size=[r_out + 2 - x_in, D["usb_w"], D["usb_h"]], op="cut"))
    sx0, sx1 = D["slot_x"] - D["shell_slot_w"] / 2, D["slot_x"] + D["shell_slot_w"] / 2
    y_in = through_depth(r_in, max(abs(sx0), abs(sx1)))
    cmds.append(box(corner=[sx0, -r_out - 1, D["slot_z"]], size=[D["shell_slot_w"], r_out + 1 - y_in, D["slot_h"]], op="cut"))
    return script("BaseShell", cmds)


def build_bottom_plate():
    br = D["shell_boss_r"]
    mb_y0, mb_y1 = D["stack_z"] - MB["w"] / 2, D["stack_z"] + MB["w"] / 2
    cmds = [cyl("BottomPlate", [0, 0, 0], D["plate_d"], D["plate_t"])]
    for ux, uy in SHELL_BOSSES:
        cmds.append(cyl(base=[ux * br, uy * br, -2], d=D["plate_hole_d"], h=8, op="cut"))
    # スタックの位置決め: 後ろレール + 両端リップ (前はサドル壁が受ける)。スタックは底板に直置き
    cmds.append(box(corner=[-D["rail_half_l"], mb_y0 - D["stack_clr"] - D["rail_t"], D["plate_t"]],
                    size=[2 * D["rail_half_l"], D["rail_t"], D["lip_h"]], op="join"))
    for s in (1, -1):
        x0 = MB["l"] / 2 + D["stack_clr"] if s > 0 else -MB["l"] / 2 - D["stack_clr"] - D["lip_t"]
        cmds.append(box(corner=[x0, mb_y0 + D["lip_inset"], D["plate_t"]],
                        size=[D["lip_t"], MB["w"] - 2 * D["lip_inset"], D["lip_h"]], op="join"))
    # パンサーボのフランジ吊りサドル
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
    for span in D["saddle_walls"]:                                # サーボ線の出口 (どちらの端から出ても通るよう両壁)
        cmds.append(box(corner=[SERVO_CX + span[0] - 0.5, -D["saddle_notch_w"] / 2, wall_z],
                        size=[span[1] - span[0] + 1, D["saddle_notch_w"], D["saddle_notch_h"]], op="cut"))
    return script("BottomPlate", cmds)


def build_pan_deck():
    """ローカル Z=0 がデッキ下面 (世界では DECK_BOTTOM)"""
    cmds = [
        cyl("PanDeck", [0, 0, 0], D["base_d"], D["deck_t"]),
        cyl(base=[0, 0, -HUB_H], d=D["hub_d"], h=HUB_H, op="join"),            # ホーン座
        cyl(base=[0, 0, -HUB_H - 1], d=D["horn_access_d"], h=D["deck_t"] + HUB_H + 2, op="cut"),  # 中心ネジの工具穴
    ]
    for (hx, hy) in [(D["horn_hole_r"], 0), (-D["horn_hole_r"], 0), (0, D["horn_hole_r"]), (0, -D["horn_hole_r"])]:
        cmds.append(cyl(base=[hx, hy, -HUB_H - 1], d=D["horn_hole_d"], h=6, op="cut"))
    # 頬板の下穴 (つま先とタブ)・ケーブル穴・裏溝は「チルト軸の前後位置」を基準に置く
    ch_y = (D["channel_y"][0] + D["channel_y"][1]) / 2
    tab_x = D["cheek_x_in"] + D["cheek_t"] + D["tab_hole_out"]
    for sx in (1, -1):
        for (hx, hy) in [(CHEEK_CX, D["toe_hole_y"]), (tab_x, D["tab_hole_y"])]:
            cmds.append(cyl(base=[sx * hx, AXIS_FB + hy, D["deck_t"] - 5], d=D["cheek_pilot_d"], h=6, op="cut"))
    cmds.append(cyl(base=[-CHEEK_CX, AXIS_FB + ch_y, -2], d=D["cable_hole_d"], h=D["deck_t"] + 4, op="cut"))
    cmds.append(box(corner=[-CHEEK_CX - 1, AXIS_FB + ch_y - D["groove_w"] / 2, 0],
                    size=[D["groove_len"], D["groove_w"], D["groove_depth"]], op="cut"))
    return script("PanDeck", cmds)


def build_cheek(left):
    """右頬板の向きで作る (X=0 が内面、+X が外)。左は X 反転"""
    body = "CheekL" if left else "CheekR"
    t, y0, y1 = D["cheek_t"], D["cheek_y_back"], D["cheek_y_front"]
    cmds = [
        box(body, [0, y0, -CHEEK_DROP], [t, y1 - y0, CHEEK_DROP + D["boss_r"]]),
        box(corner=[-D["foot_ext"], y0, -CHEEK_DROP], size=[D["foot_ext"] + t, y1 - y0, 4], op="join"),   # 内側の張り出し
        box(corner=[0, y1, -CHEEK_DROP], size=[t, D["toe_len"], 4], op="join"),                           # 前つま先
        box(corner=[t, D["tab_y_back"], -CHEEK_DROP], size=[D["tab_w"], y1 - D["tab_y_back"], 4], op="join"),  # 外タブ
        cyl(base=[t / 2, D["toe_hole_y"], -CHEEK_DROP - 1], d=D["cheek_hole_d"], h=6, op="cut"),
        cyl(base=[t + D["tab_hole_out"], D["tab_hole_y"], -CHEEK_DROP - 1], d=D["cheek_hole_d"], h=6, op="cut"),
    ]
    if left:
        seat_x = t - D["bearing_seat_depth"]
        cmds.append(cyl(base=[seat_x, 0, 0], d=D["bearing_hole_d"], h=D["bearing_seat_depth"] + 1, axis="X", op="cut"))  # 608 の座 (外面から)
        cmds.append(cyl(base=[-1, 0, 0], d=D["bearing_lip_d"], h=seat_x + 2, axis="X", op="cut"))                       # 内側リップの穴
        # サーボ線の溝 (外面に開いた溝。ピンの外端から出た線を足元のデッキ穴まで下ろす)
        cmds.append(box(corner=[t - D["channel_depth"], D["channel_y"][0], -CHEEK_DROP - 1],
                        size=[D["channel_depth"] + 1, D["channel_y"][1] - D["channel_y"][0],
                              CHEEK_DROP + 1 - D["bearing_hole_d"] / 2 + 2], op="cut"))
        mirror_x(cmds)
    else:
        cmds.append(cyl(base=[-2, 0, 0], d=D["servo_bearing_hole_d"], h=t + 4, axis="X", op="cut"))
        cmds.append(cyl(base=[-1, 0, 0], d=D["horn_cb_d"], h=D["horn_cb_depth"] + 1, axis="X", op="cut"))
        for (py, pz) in [(D["horn_pilot_r"], 0), (-D["horn_pilot_r"], 0), (0, D["horn_pilot_r"]), (0, -D["horn_pilot_r"])]:
            cmds.append(cyl(base=[D["horn_cb_depth"] - 0.5, py, pz], d=D["horn_pilot_d"], h=4, axis="X", op="cut"))
    return script(body, cmds)


def lid_screws():
    """裏蓋のネジ位置 (X, Z)。ポケットの上下、実体が残っている所"""
    pz0, pz1 = TILT_ZC - (SV["body_l"] + D["pocket_clr"]) / 2, TILT_ZC + (SV["body_l"] + D["pocket_clr"]) / 2
    return [(D["lid_screw_x"], pz1 + D["lid_screw_off"]), (D["lid_screw_x"], pz0 - D["lid_screw_off"])]


def lid_outline():
    """裏蓋の板の範囲 (x0, x1, z0, z1)。ポケット・フランジ溝・ボス溝の開口を全部覆う"""
    fz0, fz1 = TILT_ZC - (SV["flange_l"] + D["recess_clr"]) / 2, TILT_ZC + (SV["flange_l"] + D["recess_clr"]) / 2
    return (TILT_BODY_X0 - D["servo_wire_space"] - D["lid_margin"] + 2, D["box_w"] / 2, fz0 - D["lid_margin"], fz1 + D["lid_margin"])


def build_back_box():
    w2, back = D["box_w"] / 2, -D["box_back"]
    half_l, half_w = (SV["body_l"] + D["pocket_clr"]) / 2, (SV["body_w"] + D["pocket_clr"]) / 2
    px0, px1 = TILT_BODY_X0 - D["servo_wire_space"], TILT_BODY_X1 + D["pocket_clr"] / 2
    fl_half = (SV["flange_l"] + D["recess_clr"]) / 2
    boss_d = SV["boss_d"] + D["boss_slot_clr"]
    nw, cw, ch = D["plug_notch_w"], D["charge_ch_w"], D["servo_ch_h"]
    arm_h = D["arm_top"] + D["grip"]
    cmds = [
        box("BackBox", [-w2, back, -D["box_l"] / 2], [D["box_w"], D["box_t"], D["box_l"]]),
        box(corner=[-w2, BOX_FRONT, -D["box_l"] / 2], size=[D["box_w"], D["spacer_t"], D["box_l"]], op="join"),
    ]
    for s in (1, -1):                                             # 保持アーム + 前爪 (スマホが前へ抜けないように)
        ax = ARM_IN if s > 0 else -ARM_OUT
        cx = ARM_IN - D["claw_w"] if s > 0 else -ARM_IN
        cmds.append(box(corner=[ax, BOX_FRONT, -D["grip"]], size=[D["arm_t"], ARM_Y1 - BOX_FRONT, arm_h], op="join"))
        cmds.append(box(corner=[cx, ARM_Y1 - D["claw_t"], -D["grip"]], size=[D["claw_w"], D["claw_t"], arm_h], op="join"))
    cmds += [
        box(corner=[-ARM_OUT, BOX_FRONT, -D["grip"] - D["ledge_h"]],
            size=[2 * ARM_OUT, ARM_Y1 - BOX_FRONT, D["ledge_h"]], op="join"),                    # 下レール
        box(corner=[-nw / 2, BOX_FRONT + D["spacer_t"], -D["grip"] - D["ledge_h"] - 1],
            size=[nw, ARM_Y1 - BOX_FRONT, D["ledge_h"] + 1], op="cut"),                          # 充電プラグの切り欠き
        box(corner=[-cw / 2, back - 1, -D["box_l"] / 2 - 1],
            size=[cw, BOX_FRONT + D["spacer_t"] - back + 1, D["charge_ch_depth"] + 1], op="cut"),  # 充電ケーブルの通路
        # --- チルトサーボ: 背面から入れて裏蓋で押さえる ---
        box(corner=[px0, back - 1, TILT_ZC - half_l], size=[px1 - px0, half_w - back + 1, 2 * half_l], op="cut"),     # ボディ
        box(corner=[px0, back - 1, TILT_ZC - half_l - D["servo_wire_relief"]],
            size=[4, half_w - back + 1, 2 * half_l + 2 * D["servo_wire_relief"]], op="cut"),                           # 線の逃げ
        box(corner=[TILT_FLANGE_X - D["flange_slot_clr"] / 2, back - 1, TILT_ZC - fl_half],
            size=[SV["flange_t"] + D["flange_slot_clr"], half_w - back + 1.2, 2 * fl_half], op="cut"),                 # フランジ溝
        box(corner=[px1 - 0.1, back - 1, -boss_d / 2], size=[w2 - px1 + 1.1, -back + 1, boss_d], op="cut"),           # ボス用 U 溝
        cyl(base=[px1 - 0.1, 0, 0], d=boss_d, h=w2 - px1 + 1.1, axis="X", op="cut"),
        # --- 左ピボットと配線トンネル ---
        box(corner=[BOX_PIVOT_X1, -ch / 2, -ch / 2], size=[px0 - BOX_PIVOT_X1 + 0.5, ch, ch], op="cut"),
        cyl(base=[-w2 - 1, 0, 0], d=D["pivot_hole_d"], h=D["pivot_len"] + 1.5, axis="X", op="cut"),
    ]
    for (sx, sz) in lid_screws():
        cmds.append(cyl(base=[sx, back - 1, sz], d=D["lid_pilot_d"], h=D["lid_pilot_depth"] + 1, axis="Y", op="cut"))  # M3×8 が底付きしない深さ
    return script("BackBox", cmds)


def build_back_lid():
    """背面ボックスと同じチルトローカル座標で作る (置き場所がそのまま合う)"""
    back = -D["box_back"]
    x0, x1, z0, z1 = lid_outline()
    clr = 0.2
    cmds = [
        box("BackLid", [x0, back - D["lid_t"], z0], [x1 - x0, D["lid_t"], z1 - z0]),
        box(corner=[TILT_BODY_X0 + clr, back, TILT_ZC - SV["body_l"] / 2 + clr],
            size=[SV["body_h"] - 2 * clr, -SV["body_w"] / 2 - 0.1 - back, SV["body_l"] - 2 * clr], op="join"),  # サーボを押さえる舌
    ]
    for (sx, sz) in lid_screws():
        cmds.append(cyl(base=[sx, back - D["lid_t"] - 1, sz], d=D["lid_hole_d"], h=D["lid_t"] + 2, axis="Y", op="cut"))
    return script("BackLid", cmds)


def build_pivot_pin():
    """ローカル X=0 がピン頭の座面。-X 側が頭、+X 側が軸 (背面ボックス方向)"""
    cmds = [
        cyl("PivotPin", [-D["pin_head_t"], 0, 0], D["pin_head_d"], D["pin_head_t"], axis="X"),
        cyl(base=[0, 0, 0], d=D["pin_d"], h=D["pin_len"], axis="X", op="join"),
        cyl(base=[-D["pin_head_t"] - 1, 0, 0], d=D["pin_bore_d"], h=D["pin_head_t"] + D["pin_len"] + 2, axis="X", op="cut"),
    ]
    return script("PivotPin", cmds)


def build_gauge():
    cmds = [box("FitGauge", [0, 0, 0], [G["l"], G["w"], G["t"]])]
    for cx, clr in zip(G["centers"], G["clearances"]):
        sl, sw = SV["body_l"] + clr, SV["body_w"] + clr           # CAD のポケット (ボディ + pocket_clr) と同じ基準
        cmds.append(box(corner=[cx - sl / 2, (G["w"] - sw) / 2, -2], size=[sl, sw, 10], op="cut"))
    return script("FitGauge", cmds)


PART_BUILDERS = {
    "base-shell": build_base_shell,
    "bottom-plate": build_bottom_plate,
    "pan-deck": build_pan_deck,
    "cheek-left": lambda: build_cheek(True),
    "cheek-right": lambda: build_cheek(False),
    "back-box": build_back_box,
    "back-lid": build_back_lid,
    "pivot-pin": build_pivot_pin,
}

# 組立位置 (部品のローカル原点を置く世界座標)。group: base=固定 / pan=パンで回る / tilt=チルトでも回る
PLACEMENTS = {
    "bottom-plate": {"body": "BottomPlate", "group": "base", "at": [0, 0, 0]},
    "base-shell": {"body": "BaseShell", "group": "base", "at": [0, 0, 0]},
    "pan-deck": {"body": "PanDeck", "group": "pan", "at": [0, 0, DECK_BOTTOM]},
    "cheek-left": {"body": "CheekL", "group": "pan", "at": [-D["cheek_x_in"], AX[1], AX[2]]},
    "cheek-right": {"body": "CheekR", "group": "pan", "at": [D["cheek_x_in"], AX[1], AX[2]]},
    "back-box": {"body": "BackBox", "group": "tilt", "at": AX},
    "back-lid": {"body": "BackLid", "group": "tilt", "at": AX},
    "pivot-pin": {"body": "PivotPin", "group": "tilt", "at": [PIN_X0, AX[1], AX[2]]},
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


def sc(args):
    """SolidCraft CLI を実行。失敗 (終了コード非0) は例外にして、古い出力を黙って使い回さない"""
    out = subprocess.run([SC] + args, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"solidcraft-cli {' '.join(args[:2])} が失敗: {out.stderr.strip()[-600:]}")
    return out.stdout


def sc_eval(path):
    d = json.loads(sc(["eval", path]))
    assert d["body_count"] == 1, f"{path}: body_count={d['body_count']}"
    b = d["bodies"][0]
    assert b["closed"], f"{path}: not closed"
    bb = b["bbox"]
    dims = "×".join(fmt_dim(round(bb["max"][i] - bb["min"][i], 2)) for i in range(3))
    return {"vol": round(b["volume_mm3"] / 1000.0, 1), "dims": dims,
            "vol_mm3": round(b["volume_mm3"])}


def sc_run(path):
    base = path[:-5]
    for ext in (".stl", ".sc"):                 # 前回の出力を消してから生成する
        if os.path.exists(base + ext):
            os.remove(base + ext)
    sc(["run", path, "--out", base + ".stl", "--save", base + ".sc"])
    assert os.path.getsize(base + ".stl") > 84, f"{base}.stl が空"
    return base + ".stl"


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
    # スマホ下端の前角が最も下がる角度 (0° ではない) での隙間
    low_r = math.hypot(D["grip"], D["axis_back"] + PH["t"] / 2)
    return {
        "deck_top": DECK_TOP,
        "deck_bottom": DECK_BOTTOM,
        "hub_h": round(HUB_H, 2),
        "cheek_depth": D["cheek_y_front"] - D["cheek_y_back"],
        "total_height": D["axis_h"] + (PH["h"] - D["grip"]),
        "moving_mass_g": PH["mass_g"] + D["holder_mass_g"],
        "phone_bottom_clearance": D["axis_h"] - D["grip"] - DECK_TOP,
        "phone_bottom_clearance_min": round(D["axis_h"] - low_r - DECK_TOP, 1),
        "phone_low_angle": round(math.degrees(math.atan2(D["axis_back"] + PH["t"] / 2, D["grip"])), 1),
        "phone_side_clr": round(PHONE_SIDE_CLR, 2),
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
        ("デッキ", f"Ø{D['base_d']}×{D['deck_t']}（下面 = 高さ {fmt_dim(drv['deck_bottom'])}、上面 = {fmt_dim(drv['deck_top'])}。シェル天面との間 {fmt_dim(D['pan_gap'])}mm は摺動パッド）、裏のホーン座 Ø{D['hub_d']}×{fmt_dim(drv['hub_h'])}"),
        ("チルト軸", f"高さ {D['axis_h']}、スマホ中心面の {D['axis_back']}mm 後ろ"),
        ("スマホ掴み", f"下端から {D['grip']}mm（スマホ下端とデッキの隙間: 0° で {fmt_dim(drv['phone_bottom_clearance'])}mm、最小は約 {drv['phone_low_angle']}° お辞儀したときの {drv['phone_bottom_clearance_min']}mm）"),
        ("スマホの保持", f"アーム内幅 {fmt_dim(2 * ARM_IN)}（側面の隙間 {drv['phone_side_clr']}mm ずつ）、前爪 {D['claw_w']}mm、頬板との隙間 {D['cheek_clr']}mm"),
        ("全体の高さ", f"約 {drv['total_height']}mm"),
        ("可動部質量", f"{drv['moving_mass_g']}g（スマホ {PH['mass_g']} + ホルダ系 {D['holder_mass_g']}）"),
        ("チルト保持トルク（45°・最悪）", f"{drv['torque45']} kg·cm（MG90S {SV['torque_mg90s']} に対して約 {round(SV['torque_mg90s'] / drv['torque45'], 1)} 倍の余裕）"),
        ("土台内の隙間", f"スタック⇔フランジ {drv['gap_flange_mm']}mm / スタック角⇔内壁 {drv['gap_corner_mm']}mm / スタック上面⇔天面 {drv['gap_top_mm']}mm"),
        ("サーボポケットのクリアランス", f"+{D['pocket_clr']}mm（仮。フィットゲージの実測で確定する）"),
    ]
    table = "\n".join(f"| {k} | {v} |" for k, v in rows)
    vols = "、".join(f"{n} {meta[n]['vol']}cm³" for n in PART_BUILDERS)
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
    for f in ("simulator.html", "showcase.html"):          # 公開用 (GitHub Pages) のコピーを同期
        shutil.copyfile(os.path.join(ROOT, f), os.path.join(ROOT, "docs", f))
    inject_md(meta, drv)
    write_svg(drv)
    print("== 派生値 ==")
    for k, v in drv.items():
        print(f"  {k}: {v}")
    if "--no-check" in sys.argv:
        return
    print("== 組立チェック ==")
    import check_assembly
    failures = check_assembly.run(verbose=True)
    if failures:
        print(f"組立チェック: {len(failures)} 件の失敗")
        sys.exit(1)
    print("組立チェック: すべて OK")


if __name__ == "__main__":
    main()
