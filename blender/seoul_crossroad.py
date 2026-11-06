"""서울 골목 사거리 — 낮 → 밤 → 비, 14초 원테이크 (Blender 4.2+ / 5.x).

가상의 사거리 하나를 절차적으로 만들고, 빛과 날씨만 바꿔 14초(24fps, 336프레임)
원테이크를 만듭니다. 실존 상호와 브랜드는 쓰지 않습니다.

상태 채널: 'SC_CTRL' 엠프티의 커스텀 속성 night / rain / wet / sun 을 키프레임으로
움직이고, 모든 재질과 조명이 드라이버로 이 값을 읽습니다.

    0.0 ~ 3.5초   낮      물리 하늘, 해 고도 34°
    3.5 ~ 6.0초   전환    해가 지며 그림자가 쓸려가고 노을, 간판이 하나씩·창문이 무작위로 켜짐
    6.0 ~ 9.0초   밤      도시 불빛이 비치는 남색 하늘, 가로등·가게 불빛이 보도에
    9.0 ~ 10.5초  비 시작 지오메트리 노드 빗줄기(씬 시간으로 낙하, 밀도 = rain), 도로가 젖고 웅덩이 파문
   10.5 ~ 14.0초  비 오는 밤, 젖은 도로에 간판 반사

실행 방법
    1) 블렌더 UI: Scripting 탭에서 이 파일을 열고 Run Script
       → 새 장면 'SeoulCrossroad'를 만들고 그 장면으로 전환합니다(기존 장면은 그대로 둡니다).
    2) 블렌더 MCP: uv run blender/run_in_blender.py  (애드온으로 이 스크립트를 보냅니다)
    3) 백그라운드 렌더:
       blender -b -P blender/seoul_crossroad.py -- --stills out/ --percent 33
       (pip 의 bpy 모듈이면: python blender/seoul_crossroad.py --stills out/)

간판 텍스처는 blender/signs/ 에 있어야 합니다 (uv run blender/make_signs.py 로 생성).
"""

from __future__ import annotations

import json
import math
import os
import random
import sys
import warnings

import bpy
from mathutils import Matrix, Vector

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

FPS = 24
DURATION = 14.0
FRAME_END = int(round(FPS * DURATION))  # 336
PREFIX = "SC_"
SCENE_NAME = "SeoulCrossroad"
SEED = 7

ROAD = 3.5        # 도로 반폭 (왕복 2차로, 7m)
LINE = 6.0        # 건축선 (보도 2.5m)
CURB_R = 2.5      # 모퉁이 연석 반경
EXT = 55.0        # 도로 길이(반)
WALK_Z = 0.15     # 보도 높이
GROUND_H = 4.0    # 1층 층고
FLOOR_H = 3.1     # 기준층 층고

STILLS = [(2.0, "day"), (4.9, "dusk"), (7.5, "night"), (12.5, "rain")]
DEFAULT_STILLS = ("day", "night", "rain")


def frame_at(sec: float) -> int:
    return 1 + int(round(sec * FPS))


# ---------------------------------------------------------------------------
# 작은 도우미
# ---------------------------------------------------------------------------

CTRL = None          # 상태 채널 엠프티 (build 에서 만듦)
MATS: dict = {}      # 이름 → 재질
COLL: dict = {}      # 이름 → 컬렉션
STATS = {"drivers": 0}


def V(*a):
    return Vector(a if len(a) > 1 else a[0])


def drive(owner, path, expr, keys=(), index=-1):
    """owner.path 에 CTRL 의 커스텀 속성(keys)을 읽는 드라이버를 겁니다."""
    fc = owner.driver_add(path, index) if index >= 0 else owner.driver_add(path)
    d = fc.driver
    d.type = 'SCRIPTED'
    for var in list(d.variables):
        d.variables.remove(var)
    for k in keys:
        var = d.variables.new()
        var.name = k
        var.type = 'SINGLE_PROP'
        var.targets[0].id_type = 'OBJECT'
        var.targets[0].id = CTRL
        var.targets[0].data_path = f'["{k}"]'
    d.expression = expr
    for mod in list(fc.modifiers):
        fc.modifiers.remove(mod)
    STATS["drivers"] += 1
    return fc


def keys_in(expr):
    return [k for k in ("night", "rain", "wet", "sun") if k in expr]


def on_ramp(key, th, width):
    """th 를 넘으면 width 동안 0→1 로 켜지는 단순식(드라이버용)."""
    return f"min(max(({key}-{th:.3f})/{width:.3f},0),1)"


# ---------------------------------------------------------------------------
# 셰이더 노드 도우미
# ---------------------------------------------------------------------------

def new_mat(name):
    full = PREFIX + name
    old = bpy.data.materials.get(full)
    if old:
        bpy.data.materials.remove(old)
    m = bpy.data.materials.new(full)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    MATS[name] = m
    return m, nt, out


def N(nt, kind, **props):
    nd = nt.nodes.new(kind)
    for k, v in props.items():
        setattr(nd, k, v)
    return nd


def put(nt, src, dst):
    if isinstance(src, bpy.types.NodeSocket):
        nt.links.new(src, dst)
    elif isinstance(src, (tuple, list)):
        dst.default_value = tuple(src) + ((1.0,) if len(src) == 3 and len(dst.default_value) == 4 else ())
    else:
        dst.default_value = src


def op(nt, kind, a, b=0.0, clamp=False):
    nd = N(nt, 'ShaderNodeMath', operation=kind, use_clamp=clamp)
    put(nt, a, nd.inputs[0])
    put(nt, b, nd.inputs[1])
    return nd.outputs[0]


def mix_col(nt, fac, a, b, blend='MIX'):
    nd = N(nt, 'ShaderNodeMix', data_type='RGBA', blend_type=blend)
    put(nt, fac, nd.inputs[0])
    put(nt, a, nd.inputs[6])
    put(nt, b, nd.inputs[7])
    return nd.outputs[2]


def mix_f(nt, fac, a, b):
    nd = N(nt, 'ShaderNodeMix', data_type='FLOAT')
    put(nt, fac, nd.inputs[0])
    put(nt, a, nd.inputs[2])
    put(nt, b, nd.inputs[3])
    return nd.outputs[0]


def rgb(nt, col):
    nd = N(nt, 'ShaderNodeRGB')
    nd.outputs[0].default_value = (*col, 1.0)
    return nd.outputs[0]


def state(nt, expr):
    """드라이버가 걸린 Value 노드. expr 은 night/rain/wet/sun/frame 을 쓰는 단순식."""
    nd = N(nt, 'ShaderNodeValue', label=expr[:60])
    drive(nd.outputs[0], "default_value", expr, keys_in(expr))
    return nd.outputs[0]


def world_pos(nt):
    return N(nt, 'ShaderNodeNewGeometry').outputs['Position']


def facade_uv(nt):
    """벽면 좌표: X 를 향한 벽은 (y, z), Y 를 향한 벽은 (x, z)."""
    geo = N(nt, 'ShaderNodeNewGeometry')
    p = N(nt, 'ShaderNodeSeparateXYZ')
    nt.links.new(geo.outputs['Position'], p.inputs[0])
    n = N(nt, 'ShaderNodeSeparateXYZ')
    nt.links.new(geo.outputs['Normal'], n.inputs[0])
    sel = op(nt, 'GREATER_THAN', op(nt, 'ABSOLUTE', n.outputs['X']), 0.5)
    u = mix_f(nt, sel, p.outputs['X'], p.outputs['Y'])
    c = N(nt, 'ShaderNodeCombineXYZ')
    put(nt, u, c.inputs[0])
    nt.links.new(p.outputs['Z'], c.inputs[1])
    return c.outputs[0]


def wet_response(nt, color, rough, k=1.0):
    """젖으면 어두워지고 광택이 나는 공통 반응. 모든 불투명 재질이 wet 드라이버를 씁니다."""
    wk = state(nt, f"wet*{k:.3f}")
    col = mix_col(nt, op(nt, 'MULTIPLY', wk, 0.42), color, (0.0, 0.0, 0.0))
    r_wet = op(nt, 'MULTIPLY', rough, 0.28)
    return col, mix_f(nt, wk, rough, r_wet)


def principled(nt, out, color, rough, metal=0.0, normal=None, emit=None, estr=None, alpha=None,
               spec=0.5, trans=0.0):
    p = N(nt, 'ShaderNodeBsdfPrincipled')
    put(nt, color, p.inputs['Base Color'])
    put(nt, rough, p.inputs['Roughness'])
    p.inputs['Metallic'].default_value = metal
    p.inputs['Specular IOR Level'].default_value = spec
    p.inputs['Transmission Weight'].default_value = trans
    if normal is not None:
        nt.links.new(normal, p.inputs['Normal'])
    if emit is not None:
        put(nt, emit, p.inputs['Emission Color'])
        put(nt, estr if estr is not None else 1.0, p.inputs['Emission Strength'])
    if alpha is not None:
        put(nt, alpha, p.inputs['Alpha'])
    nt.links.new(p.outputs[0], out.inputs['Surface'])
    return p


def noise(nt, vec, scale, detail=3.0, rough=0.55):
    nd = N(nt, 'ShaderNodeTexNoise')
    if vec is not None:
        nt.links.new(vec, nd.inputs['Vector'])
    nd.inputs['Scale'].default_value = scale
    nd.inputs['Detail'].default_value = detail
    nd.inputs['Roughness'].default_value = rough
    return nd.outputs['Fac']


def bump(nt, height, strength, dist=0.01, normal=None):
    nd = N(nt, 'ShaderNodeBump')
    put(nt, height, nd.inputs['Height'])
    put(nt, strength, nd.inputs['Strength'])
    nd.inputs['Distance'].default_value = dist
    if normal is not None:
        nt.links.new(normal, nd.inputs['Normal'])
    return nd.outputs['Normal']


# ---------------------------------------------------------------------------
# 재질
# ---------------------------------------------------------------------------

def mat_simple(name, color, rough, metal=0.0, k=1.0, var=0.12, scale=3.0):
    m, nt, out = new_mat(name)
    n = noise(nt, world_pos(nt), scale)
    col = mix_col(nt, op(nt, 'MULTIPLY', n, var), color, tuple(c * 0.6 for c in color))
    col, r = wet_response(nt, col, rough, k)
    principled(nt, out, col, r, metal)
    return m


def mat_tile(name, c1, c2, grout, w=0.23, h=0.075):
    m, nt, out = new_mat(name)
    b = N(nt, 'ShaderNodeTexBrick', offset=0.5)
    nt.links.new(facade_uv(nt), b.inputs['Vector'])
    put(nt, c1, b.inputs['Color1'])
    put(nt, c2, b.inputs['Color2'])
    put(nt, grout, b.inputs['Mortar'])
    b.inputs['Scale'].default_value = 1.0
    b.inputs['Mortar Size'].default_value = 0.006
    b.inputs['Brick Width'].default_value = w
    b.inputs['Row Height'].default_value = h
    dirt = noise(nt, facade_uv(nt), 0.8, 4)
    col = mix_col(nt, op(nt, 'MULTIPLY', dirt, 0.25), b.outputs['Color'], (0.25, 0.23, 0.2), 'MULTIPLY')
    col, r = wet_response(nt, col, 0.32, 0.6)
    principled(nt, out, col, r, normal=bump(nt, b.outputs['Fac'], 0.4, 0.004))
    return m


def mat_brick(name, c1, c2, mortar):
    m, nt, out = new_mat(name)
    b = N(nt, 'ShaderNodeTexBrick', offset=0.5)
    nt.links.new(facade_uv(nt), b.inputs['Vector'])
    put(nt, c1, b.inputs['Color1'])
    put(nt, c2, b.inputs['Color2'])
    put(nt, mortar, b.inputs['Mortar'])
    b.inputs['Scale'].default_value = 1.0
    b.inputs['Mortar Size'].default_value = 0.01
    b.inputs['Brick Width'].default_value = 0.21
    b.inputs['Row Height'].default_value = 0.068
    b.inputs['Bias'].default_value = 0.0
    n = noise(nt, facade_uv(nt), 25, 2)
    col = mix_col(nt, op(nt, 'MULTIPLY', n, 0.35), b.outputs['Color'], (0.12, 0.06, 0.04), 'MULTIPLY')
    col, r = wet_response(nt, col, 0.85, 0.9)
    principled(nt, out, col, r, normal=bump(nt, b.outputs['Fac'], 0.6, 0.008))
    return m


def mat_plaster(name, color):
    m, nt, out = new_mat(name)
    uv = facade_uv(nt)
    n = noise(nt, uv, 18, 6, 0.65)
    stain = noise(nt, uv, 0.6, 3)
    col = mix_col(nt, op(nt, 'MULTIPLY', stain, 0.35), color, tuple(c * 0.55 for c in color))
    col, r = wet_response(nt, col, 0.9, 0.8)
    principled(nt, out, col, r, normal=bump(nt, n, 0.25, 0.01))
    return m


def mat_asphalt():
    """아스팔트: wet 에 따라 어두워지고 광택, 웅덩이가 넓어지며 rain 에 따라 파문."""
    m, nt, out = new_mat("asphalt")
    p = world_pos(nt)
    grain = noise(nt, p, 60, 4, 0.7)
    patch = noise(nt, p, 0.35, 3)
    base = mix_col(nt, op(nt, 'MULTIPLY', patch, 0.5), (0.055, 0.055, 0.058), (0.03, 0.03, 0.032))
    base = mix_col(nt, op(nt, 'MULTIPLY', grain, 0.35), base, (0.09, 0.09, 0.09))

    wet = state(nt, "wet")
    rain = state(nt, "rain")
    t = state(nt, f"frame/{FPS}")
    # 웅덩이 마스크: 젖을수록 넓어짐
    pud_n = noise(nt, p, 0.16, 2, 0.5)
    lowered = op(nt, 'SUBTRACT', pud_n, op(nt, 'SUBTRACT', 0.66, op(nt, 'MULTIPLY', wet, 0.13)))
    puddle = op(nt, 'MULTIPLY', op(nt, 'MULTIPLY', lowered, 14.0, True), wet, True)

    # 빗방울 파문: 4D 보로노이(W = 시간) 거리에서 퍼져나가는 고리
    vor = N(nt, 'ShaderNodeTexVoronoi', voronoi_dimensions='4D', feature='F1')
    nt.links.new(p, vor.inputs['Vector'])
    vor.inputs['Scale'].default_value = 4.0
    put(nt, op(nt, 'MULTIPLY', t, 2.4), vor.inputs['W'])
    d = vor.outputs['Distance']
    ring = op(nt, 'SINE', op(nt, 'SUBTRACT', op(nt, 'MULTIPLY', d, 95.0), op(nt, 'MULTIPLY', t, 34.0)))
    fade = op(nt, 'SUBTRACT', 1.0, op(nt, 'MULTIPLY', d, 3.6), True)
    ripple = op(nt, 'MULTIPLY', ring, op(nt, 'MULTIPLY', fade, fade))
    rip_str = op(nt, 'MULTIPLY', rain, op(nt, 'ADD', op(nt, 'MULTIPLY', puddle, 1.0), 0.18))
    nrm = bump(nt, grain, op(nt, 'SUBTRACT', 0.35, op(nt, 'MULTIPLY', wet, 0.22)), 0.004)
    nrm = bump(nt, ripple, rip_str, 0.006, nrm)

    col = mix_col(nt, op(nt, 'MULTIPLY', wet, 0.55), base, (0.0, 0.0, 0.0))
    col = mix_col(nt, puddle, col, (0.012, 0.012, 0.014))
    rough = mix_f(nt, wet, 0.88, 0.1)
    rough = mix_f(nt, puddle, rough, 0.01)
    principled(nt, out, col, rough, normal=nrm, spec=0.5)
    return m


def mat_paving():
    m, nt, out = new_mat("paving")
    b = N(nt, 'ShaderNodeTexBrick', offset=0.5)
    nt.links.new(world_pos(nt), b.inputs['Vector'])
    put(nt, (0.36, 0.35, 0.34), b.inputs['Color1'])
    put(nt, (0.30, 0.22, 0.2), b.inputs['Color2'])
    put(nt, (0.15, 0.15, 0.15), b.inputs['Mortar'])
    b.inputs['Scale'].default_value = 1.0
    b.inputs['Mortar Size'].default_value = 0.006
    b.inputs['Brick Width'].default_value = 0.2
    b.inputs['Row Height'].default_value = 0.1
    b.inputs['Bias'].default_value = -0.4
    n = noise(nt, world_pos(nt), 1.2, 3)
    col = mix_col(nt, op(nt, 'MULTIPLY', n, 0.4), b.outputs['Color'], (0.2, 0.2, 0.2), 'MULTIPLY')
    col, r = wet_response(nt, col, 0.8, 1.0)
    principled(nt, out, col, r, normal=bump(nt, b.outputs['Fac'], 0.5, 0.006))
    return m


def mat_window_glass(name, warm, cool, strength, base=(0.02, 0.025, 0.03), shelves=False):
    """창문/쇼윈도: 낮에는 하늘을 비추는 유리, 밤에는 면마다 다른 순서(lit_th)로 불이 들어옴."""
    m, nt, out = new_mat(name)
    th = N(nt, 'ShaderNodeAttribute', attribute_type='GEOMETRY', attribute_name="lit_th").outputs['Fac']
    tint = N(nt, 'ShaderNodeAttribute', attribute_type='GEOMETRY', attribute_name="tint").outputs['Fac']
    bright = N(nt, 'ShaderNodeAttribute', attribute_type='GEOMETRY', attribute_name="bright").outputs['Fac']
    night = state(nt, "night")
    lit = op(nt, 'MULTIPLY', op(nt, 'SUBTRACT', night, th), 18.0, True)
    col = mix_col(nt, tint, warm, cool)
    # 실내 느낌: 위쪽이 밝고 얼룩진 빛 (커튼·가구)
    inner = op(nt, 'ADD', 0.45, op(nt, 'MULTIPLY', noise(nt, world_pos(nt), 1.8, 2), 0.9))
    if shelves:
        # 가게 안: 천장 쪽이 밝고 진열대 줄무늬가 보이게 (월드 Z 기준)
        z = N(nt, 'ShaderNodeSeparateXYZ')
        nt.links.new(world_pos(nt), z.inputs[0])
        grad = op(nt, 'ADD', 0.18, op(nt, 'MULTIPLY', op(nt, 'SUBTRACT', z.outputs['Z'], 0.5), 0.38, True))
        band = op(nt, 'ADD', 0.72, op(nt, 'MULTIPLY', op(nt, 'SINE', op(nt, 'MULTIPLY', z.outputs['Z'], 13.0)), 0.28))
        inner = op(nt, 'MULTIPLY', inner, op(nt, 'MULTIPLY', grad, band))
    estr = op(nt, 'MULTIPLY', op(nt, 'MULTIPLY', lit, strength), op(nt, 'MULTIPLY', bright, inner))
    wet = state(nt, "wet")
    principled(nt, out, base, mix_f(nt, wet, 0.06, 0.02), emit=col, estr=estr, spec=0.6)
    return m


def mat_sign(name, image_path, strength, th):
    """간판: 낮에는 인쇄면, night 가 th 를 넘으면 하나씩 켜짐."""
    m, nt, out = new_mat(name)
    tex = N(nt, 'ShaderNodeTexImage', interpolation='Cubic')
    img = bpy.data.images.load(image_path, check_existing=True)
    try:
        img.pack()
    except RuntimeError:
        pass
    tex.image = img
    uv = N(nt, 'ShaderNodeUVMap', uv_map="UVMap")
    nt.links.new(uv.outputs[0], tex.inputs['Vector'])
    estr = state(nt, f"{strength:.2f}*{on_ramp('night', th, 0.05)}")
    wet = state(nt, "wet")
    principled(nt, out, tex.outputs['Color'], mix_f(nt, wet, 0.35, 0.08), emit=tex.outputs['Color'], estr=estr)
    return m


def mat_emit_lamp(name, color, expr):
    m, nt, out = new_mat(name)
    principled(nt, out, (0.8, 0.8, 0.8), 0.3, emit=color, estr=state(nt, expr))
    return m


def mat_rain():
    m, nt, out = new_mat("rain")
    rain = state(nt, "rain")
    night = state(nt, "night")
    em = N(nt, 'ShaderNodeEmission')
    put(nt, (0.78, 0.83, 0.92), em.inputs['Color'])
    put(nt, op(nt, 'ADD', 0.12, op(nt, 'MULTIPLY', night, 0.45)), em.inputs['Strength'])
    tr = N(nt, 'ShaderNodeBsdfTransparent')
    mx = N(nt, 'ShaderNodeMixShader')
    put(nt, op(nt, 'MULTIPLY', rain, 0.14), mx.inputs[0])
    nt.links.new(tr.outputs[0], mx.inputs[1])
    nt.links.new(em.outputs[0], mx.inputs[2])
    nt.links.new(mx.outputs[0], out.inputs['Surface'])
    return m


def mat_foliage():
    m, nt, out = new_mat("foliage")
    p = world_pos(nt)
    n = noise(nt, p, 6, 4)
    col = mix_col(nt, n, (0.05, 0.16, 0.035), (0.13, 0.26, 0.05))
    col, r = wet_response(nt, col, 0.6, 0.7)
    bsdf = principled(nt, out, col, r, normal=bump(nt, noise(nt, p, 30, 2), 0.6, 0.02))
    bsdf.inputs['Subsurface Weight'].default_value = 0.15
    return m


def build_materials(sign_dir, manifest):
    mat_asphalt()
    mat_paving()
    mat_simple("curb", (0.42, 0.41, 0.39), 0.6, var=0.25, scale=8)
    mat_simple("paint_white", (0.78, 0.78, 0.76), 0.55, var=0.3, scale=6)
    mat_simple("paint_yellow", (0.78, 0.52, 0.04), 0.55, var=0.3, scale=6)
    mat_tile("tile_white", (0.80, 0.79, 0.76), (0.72, 0.71, 0.69), (0.55, 0.55, 0.53))
    mat_tile("tile_beige", (0.68, 0.58, 0.46), (0.60, 0.50, 0.40), (0.62, 0.6, 0.56))
    mat_tile("tile_gray", (0.42, 0.43, 0.44), (0.36, 0.37, 0.38), (0.6, 0.6, 0.6), w=0.2, h=0.1)
    mat_brick("brick_red", (0.42, 0.13, 0.07), (0.33, 0.1, 0.06), (0.5, 0.48, 0.45))
    mat_brick("brick_brown", (0.28, 0.16, 0.1), (0.22, 0.12, 0.08), (0.45, 0.43, 0.4))
    mat_plaster("plaster_cream", (0.72, 0.66, 0.54))
    mat_plaster("plaster_gray", (0.52, 0.52, 0.5))
    mat_plaster("plaster_mint", (0.55, 0.66, 0.6))
    mat_plaster("plaster_side", (0.5, 0.49, 0.46))
    mat_simple("concrete", (0.5, 0.49, 0.47), 0.85, var=0.3)
    mat_simple("roof", (0.18, 0.36, 0.25), 0.8, var=0.3)  # 녹색 방수 페인트
    mat_simple("alu", (0.55, 0.56, 0.58), 0.35, 0.9, var=0.1)
    mat_simple("dark_metal", (0.05, 0.05, 0.055), 0.4, 0.6, var=0.1)
    mat_simple("galv", (0.52, 0.53, 0.55), 0.45, 1.0, var=0.2)
    mat_simple("pole_concrete", (0.55, 0.54, 0.52), 0.8, var=0.3, scale=1.5)
    mat_simple("wire", (0.015, 0.015, 0.015), 0.45, var=0.0)
    mat_simple("porcelain", (0.78, 0.8, 0.82), 0.2, var=0.05)
    mat_simple("transformer", (0.45, 0.47, 0.48), 0.5, 0.7, var=0.2)
    mat_simple("ac_unit", (0.78, 0.78, 0.75), 0.5, var=0.15)
    mat_simple("ac_grill", (0.06, 0.06, 0.06), 0.6, var=0.05)
    mat_simple("water_tank", (0.12, 0.34, 0.55), 0.45, var=0.2)
    mat_simple("bark", (0.12, 0.09, 0.07), 0.9, var=0.4, scale=12)
    mat_simple("grate", (0.08, 0.08, 0.08), 0.6, 0.8, var=0.2)
    mat_simple("stainless", (0.75, 0.75, 0.76), 0.22, 1.0, var=0.05)
    mat_simple("signal_body", (0.02, 0.02, 0.02), 0.5, var=0.05)
    mat_simple("sign_side", (0.12, 0.12, 0.13), 0.5, 0.5, var=0.1)
    mat_foliage()
    mat_rain()
    mat_window_glass("window", (1.0, 0.6, 0.3), (0.78, 0.86, 1.0), 0.3)
    mat_window_glass("shopfront", (1.0, 0.7, 0.42), (0.84, 0.92, 1.0), 0.42, base=(0.015, 0.017, 0.02),
                     shelves=True)
    # 신호등 렌즈: 켜진 것은 night 에 따라 더 밝게, 꺼진 것은 어두운 유리
    mat_emit_lamp("sig_red_on", (1.0, 0.06, 0.02), "18+22*night")
    mat_emit_lamp("sig_green_on", (0.05, 1.0, 0.55), "18+22*night")
    for c, col in (("red", (0.25, 0.02, 0.02)), ("yellow", (0.25, 0.16, 0.02)), ("green", (0.02, 0.2, 0.1))):
        mat_simple(f"sig_{c}_off", col, 0.15, var=0.0)
    mat_emit_lamp("led_head", (1.0, 0.94, 0.86), "60*" + on_ramp("night", 0.30, 0.12))
    # 간판
    signs = {}
    for group in ("vertical", "horizontal"):
        for e in manifest[group]:
            signs[e["name"]] = e
    signs[manifest["corner"]["name"]] = manifest["corner"]
    order = ["h_bunsik", "v_karaoke", "h_mart", "v_pcbang", "h_chicken", "h_laundry", "v_cafe",
             "h_pharmacy", "v_piano", "h_flower", "v_dental", "h_realty", "connect_ai_lab"]
    for i, name in enumerate(order):
        th = 0.16 + 0.055 * i
        strength = 14.0 if name == "connect_ai_lab" else 5.5
        mat_sign(name, os.path.join(sign_dir, signs[name]["file"]), strength, th)
    return order


# ---------------------------------------------------------------------------
# 메시 빌더
# ---------------------------------------------------------------------------

class MB:
    """면 단위로 메시를 모았다가 한 오브젝트로 만듭니다 (면마다 재질 이름, UV, 속성)."""

    def __init__(self):
        self.v, self.f, self.mat, self.uv, self.smooth = [], [], [], [], []
        self.attrs: dict = {}

    def poly(self, pts, mat, uvs=None, smooth=False, attrs=None):
        base = len(self.v)
        self.v.extend(tuple(p) for p in pts)
        self.f.append(list(range(base, base + len(pts))))
        self.mat.append(mat)
        self.uv.append(uvs or [(0.0, 0.0)] * len(pts))
        self.smooth.append(smooth)
        for k, val in (attrs or {}).items():
            self.attrs.setdefault(k, {})[len(self.f) - 1] = val

    def quad(self, c, a, b, mat, attrs=None, uv=True):
        """c: 중심, a: 오른쪽 반변, b: 위쪽 반변 (a×b 가 법선)."""
        c, a, b = V(c), V(a), V(b)
        pts = [c - a - b, c + a - b, c + a + b, c - a + b]
        self.poly(pts, mat, [(0, 0), (1, 0), (1, 1), (0, 1)] if uv else None, attrs=attrs)

    def box(self, c, size, mat, rz=0.0, mats=None, skip=()):
        c = V(c)
        R = Matrix.Rotation(rz, 3, 'Z')
        hx, hy, hz = size[0] / 2, size[1] / 2, size[2] / 2
        X, Y, Z = R @ V(1, 0, 0), R @ V(0, 1, 0), V(0, 0, 1)
        faces = {
            "+x": (X * hx, Y * hy, Z * hz), "-x": (-X * hx, Z * hz, Y * hy),
            "+y": (Y * hy, Z * hz, X * hx), "-y": (-Y * hy, X * hx, Z * hz),
            "+z": (Z * hz, X * hx, Y * hy), "-z": (-Z * hz, Y * hy, X * hx),
        }
        for key, (off, a, b) in faces.items():
            if key in skip:
                continue
            self.quad(c + off, a, b, (mats or {}).get(key, mat))

    def cyl(self, p0, p1, r0, r1, mat, segs=10, cap=True, smooth=True):
        p0, p1 = V(p0), V(p1)
        axis = (p1 - p0).normalized()
        tmp = V(0, 0, 1) if abs(axis.z) < 0.9 else V(1, 0, 0)
        u = axis.cross(tmp).normalized()
        w = axis.cross(u)
        ring = [(math.cos(2 * math.pi * i / segs), math.sin(2 * math.pi * i / segs)) for i in range(segs)]
        for i in range(segs):
            (c0, s0), (c1, s1) = ring[i], ring[(i + 1) % segs]
            d0, d1 = u * c0 + w * s0, u * c1 + w * s1
            self.poly([p0 + d0 * r0, p0 + d1 * r0, p1 + d1 * r1, p1 + d0 * r1], mat, smooth=smooth)
        if cap:
            self.poly([p1 + (u * c + w * s) * r1 for c, s in ring], mat)
            self.poly([p0 + (u * c + w * s) * r0 for c, s in reversed(ring)], mat)

    def emit(self, name, coll):
        me = bpy.data.meshes.new(PREFIX + name)
        me.from_pydata(self.v, [], self.f)
        names = sorted(set(self.mat))
        for n in names:
            me.materials.append(MATS[n])
        idx = {n: i for i, n in enumerate(names)}
        me.polygons.foreach_set("material_index", [idx[n] for n in self.mat])
        me.polygons.foreach_set("use_smooth", self.smooth)
        uvl = me.uv_layers.new(name="UVMap")
        flat = [c for face in self.uv for uv in face for c in uv]
        uvl.data.foreach_set("uv", flat)
        for k, vals in self.attrs.items():
            at = me.attributes.new(k, 'FLOAT', 'FACE')
            data = [0.0] * len(self.f)
            for i, val in vals.items():
                data[i] = val
            at.data.foreach_set("value", data)
        me.update()
        ob = bpy.data.objects.new(PREFIX + name, me)
        COLL[coll].objects.link(ob)
        return ob


# ---------------------------------------------------------------------------
# 도로 · 보도
# ---------------------------------------------------------------------------

def build_ground():
    road = MB()
    road.quad((0, 0, 0), (EXT, 0, 0), (0, EXT, 0), "asphalt", uv=False)
    road.emit("Road", "ground")

    walk, curb, paint = MB(), MB(), MB()
    for qx in (1, -1):
        for qy in (1, -1):
            # 모퉁이가 둥근 보도 (사분면 좌표 → 월드)
            edge = [(ROAD, EXT)]
            cx = cy = ROAD + CURB_R
            for i in range(9):
                a = math.radians(180 + 90 * i / 8)
                edge.append((cx + CURB_R * math.cos(a), cy + CURB_R * math.sin(a)))
            edge.append((EXT, ROAD))
            to_w = lambda x, y, z: (qx * x, qy * y, z)
            top = [to_w(x, y, WALK_Z) for x, y in edge] + [to_w(EXT, EXT, WALK_Z)]
            if qx * qy < 0:
                top.reverse()
            walk.poly(top, "paving")
            # 연석: 윗면 띠 + 도로 쪽 옆면
            inner = []
            for i, (x, y) in enumerate(edge):
                if i == 0:
                    n = V(1, 0)
                elif i == len(edge) - 1:
                    n = V(0, 1)
                else:
                    n = (V(cx, cy) - V(x, y)).normalized()
                inner.append((x + n.x * 0.2, y + n.y * 0.2))
            for i in range(len(edge) - 1):
                (x0, y0), (x1, y1) = edge[i], edge[i + 1]
                (u0, v0), (u1, v1) = inner[i], inner[i + 1]
                z = WALK_Z + 0.004
                strip = [to_w(x0, y0, z), to_w(x1, y1, z), to_w(u1, v1, z), to_w(u0, v0, z)]
                side = [to_w(x0, y0, 0), to_w(x1, y1, 0), to_w(x1, y1, z), to_w(x0, y0, z)]
                if qx * qy < 0:  # 거울상 사분면은 감는 방향이 뒤집힘
                    strip.reverse()
                    side.reverse()
                curb.poly(strip, "curb")
                curb.poly(side, "curb")
    walk.emit("Sidewalk", "ground")
    curb.emit("Curb", "ground")

    z = 0.004
    stripe_n = 7
    # 횡단보도 (네 방향)
    for s in (1, -1):
        for i in range(stripe_n):
            off = -3.15 + i * 1.05
            paint.quad((off, s * 7.9, z), (0.23, 0, 0), (0, 1.5, 0), "paint_white", uv=False)
            paint.quad((s * 7.9, off, z), (1.5, 0, 0), (0, 0.23, 0), "paint_white", uv=False)
    # 정지선 (우측 통행)
    paint.quad((-1.7, 10.2, z), (1.55, 0, 0), (0, 0.18, 0), "paint_white", uv=False)
    paint.quad((1.7, -10.2, z), (1.55, 0, 0), (0, 0.18, 0), "paint_white", uv=False)
    paint.quad((10.2, 1.7, z), (0.18, 0, 0), (0, 1.55, 0), "paint_white", uv=False)
    paint.quad((-10.2, -1.7, z), (0.18, 0, 0), (0, 1.55, 0), "paint_white", uv=False)
    # 중앙 황색 복선
    half = (EXT - 10.4) / 2
    mid = 10.4 + half
    for s in (1, -1):
        for d in (0.11, -0.11):
            paint.quad((d, s * mid, z), (0.06, 0, 0), (0, half, 0), "paint_yellow", uv=False)
            paint.quad((s * mid, d, z), (half, 0, 0), (0, 0.06, 0), "paint_yellow", uv=False)
    # 차로 가장자리 흰 실선
    for s in (1, -1):
        for e in (ROAD - 0.25, -(ROAD - 0.25)):
            paint.quad((e, s * mid, z), (0.06, 0, 0), (0, half, 0), "paint_white", uv=False)
            paint.quad((s * mid, e, z), (half, 0, 0), (0, 0.06, 0), "paint_white", uv=False)
    paint.emit("RoadPaint", "ground")


# ---------------------------------------------------------------------------
# 건물
# ---------------------------------------------------------------------------

EXTERIORS = ["tile_white", "tile_beige", "tile_gray", "brick_red", "brick_brown",
             "plaster_cream", "plaster_gray", "plaster_mint"]


class Facade:
    def __init__(self, b, n):
        self.b, self.n = b, V(n)
        self.u = V(0, 0, 1).cross(self.n)  # 밖에서 볼 때 오른쪽
        x0, x1, y0, y1 = b["x0"], b["x1"], b["y0"], b["y1"]
        corners = [V(x0, y0, 0), V(x1, y0, 0), V(x1, y1, 0), V(x0, y1, 0)]
        center = V((x0 + x1) / 2, (y0 + y1) / 2, 0)
        on = [c for c in corners if (c - center).dot(self.n) > 0]
        self.o = min(on, key=lambda c: c.dot(self.u))  # 왼쪽 아래
        self.w = abs((on[0] - on[1]).dot(self.u))

    def p(self, a, z, out=0.0):
        """파사드 좌표(a: 왼쪽에서 오른쪽, z: 높이, out: 벽에서 바깥으로) → 월드."""
        return self.o + self.u * a + V(0, 0, z) + self.n * out


def plan_buildings(rng):
    """네 블록(사분면)마다 모퉁이 건물과 두 도로변 건물을 배치합니다."""
    blds = []
    for qx in (1, -1):
        for qy in (1, -1):
            hero = (qx, qy) == (1, 1)
            cw = 11.0 if hero else rng.uniform(9, 12)
            cd = 10.5 if hero else rng.uniform(9, 12)
            floors = rng.choice([4, 5, 5, 6])
            if hero:
                floors = 5
            elif (qx, qy) == (1, -1):
                floors = 4  # 카메라 오른쪽 앞 건물은 가장 낮게(4층): 화면 오른쪽 프레임 역할
            blds.append(dict(q=(qx, qy), kind="corner", rect=(LINE, LINE + cw, LINE, LINE + cd),
                             floors=floors,
                             ext="tile_white" if hero else rng.choice(EXTERIORS),
                             facades=[(-qx, 0, 0), (0, -qy, 0)]))
            # 남북 도로변 (x = LINE 쪽 벽)
            y = LINE + cd
            i = 0
            while y < EXT - 8:
                w = rng.uniform(6.5, 10.0)
                d = rng.uniform(10, 12.5)
                blds.append(dict(q=(qx, qy), kind="ns", idx=i, rect=(LINE, LINE + d, y, y + w),
                                 floors=rng.choice([4, 4, 5, 5, 6]), ext=rng.choice(EXTERIORS),
                                 facades=[(-qx, 0, 0)]))
                y += w
                i += 1
            x = LINE + cw
            i = 0
            while x < EXT - 8:
                w = rng.uniform(6.5, 10.0)
                d = rng.uniform(10, 12.5)
                blds.append(dict(q=(qx, qy), kind="ew", idx=i, rect=(x, x + w, LINE, LINE + d),
                                 floors=rng.choice([4, 4, 5, 5, 6]), ext=rng.choice(EXTERIORS),
                                 facades=[(0, -qy, 0)]))
                x += w
                i += 1
    # 남북 도로 끝을 막는 건물 줄 (소실점에 빈 하늘이 보이지 않게)
    x = -26.0
    i = 0
    while x < 26.0:
        w = rng.uniform(7.0, 11.0)
        blds.append(dict(q=(1, 1), kind="end", idx=i, rect=(x, x + w, EXT - 1.0, EXT + 11.0),
                         floors=rng.choice([4, 5, 6]), ext=rng.choice(EXTERIORS), facades=[(0, -1, 0)],
                         absolute=True))
        x += w
        i += 1
    for b in blds:
        qx, qy = b["q"]
        a0, a1, b0, b1 = b["rect"]
        if b.get("absolute"):
            b["x0"], b["x1"], b["y0"], b["y1"] = a0, a1, b0, b1
            b["height"] = WALK_Z + GROUND_H + FLOOR_H * (b["floors"] - 1) + 0.9
            continue
        xs, ys = sorted((qx * a0, qx * a1)), sorted((qy * b0, qy * b1))
        b["x0"], b["x1"], b["y0"], b["y1"] = xs[0], xs[1], ys[0], ys[1]
        b["height"] = WALK_Z + GROUND_H + FLOOR_H * (b["floors"] - 1) + 0.9
    return blds


# 간판 배치: (사분면, 종류, 순번) → (간판, 파사드 번호). 모퉁이 건물의 파사드 0 은 남북 도로 쪽, 1 은 동서 도로 쪽.
H_SIGNS = {((1, 1), "ew", 0): ("h_bunsik", 0), ((1, 1), "ew", 1): ("h_mart", 0),
           ((1, 1), "ew", 2): ("h_realty", 0),
           ((1, 1), "ns", 0): ("h_laundry", 0), ((1, 1), "ns", 1): ("h_flower", 0),
           ((-1, 1), "corner", None): ("h_pharmacy", 1), ((-1, 1), "ns", 0): ("h_chicken", 0)}
V_SIGNS = {((1, 1), "corner", None): ("v_karaoke", 0), ((1, 1), "ns", 0): ("v_piano", 0),
           ((-1, 1), "corner", None): ("v_pcbang", 0), ((-1, 1), "ns", 0): ("v_dental", 0),
           ((1, -1), "corner", None): ("v_cafe", 0)}


def build_buildings(rng, blds):
    body, detail, glass, shop = MB(), MB(), MB(), MB()
    shops, roofs = [], []
    for b in blds:
        x0, x1, y0, y1 = b["x0"], b["x1"], b["y0"], b["y1"]
        top = b["height"]
        c = V((x0 + x1) / 2, (y0 + y1) / 2, top / 2)
        road_faces = {(-1, 0, 0): "-x", (1, 0, 0): "+x", (0, -1, 0): "-y", (0, 1, 0): "+y"}
        mats = {road_faces[f]: b["ext"] for f in b["facades"]}
        mats["+z"] = "roof"
        body.box(c, (x1 - x0, y1 - y0, top), "plaster_side", mats=mats, skip=("-z",))
        key = (b["q"], b["kind"], b.get("idx"))
        for fi, n in enumerate(b["facades"]):
            fc = Facade(b, n)
            facade_details(rng, b, fc, fi, key, detail, glass, shop, shops)
        roofs.append(b)
    rooftop(rng, roofs, detail)
    body.emit("Buildings", "buildings")
    detail.emit("FacadeDetail", "buildings")
    glass.emit("Windows", "buildings")
    shop.emit("Shopfronts", "buildings")
    return shops


def facade_details(rng, b, fc, fi, key, detail, glass, shop, shops):
    W, z0 = fc.w, WALK_Z
    is_corner = b["kind"] == "corner"
    hero = b["q"] == (1, 1) and is_corner
    # 1층 쇼윈도 (한두 칸)
    nshop = 1 if W < 9 else 2
    seg = W / nshop
    for s in range(nshop):
        a0, a1 = s * seg + 0.35, (s + 1) * seg - 0.35
        am, hw = (a0 + a1) / 2, (a1 - a0) / 2
        th = 0.12 + rng.random() * 0.75
        tint = 0.85 if hero else rng.random() * 0.5
        detail.quad(fc.p(am, z0 + 1.65, 0.02), fc.u * (hw + 0.08), V(0, 0, 1.55), "dark_metal", uv=False)
        shop.quad(fc.p(am, z0 + 1.65, 0.05), fc.u * hw, V(0, 0, 1.4), "shopfront",
                  attrs={"lit_th": th, "tint": tint, "bright": 2.4 if hero else 0.6 + 0.6 * rng.random()},
                  uv=False)
        for k in range(1, max(2, int(hw * 2 / 2.2)) ):
            a = a0 + k * (a1 - a0) / max(2, int(hw * 2 / 2.2))
            detail.box(fc.p(a, z0 + 1.65, 0.07), (0.06, 0.06, 2.9), "dark_metal",
                       rz=math.atan2(fc.u.y, fc.u.x))
        shops.append(dict(pos=fc.p(am, z0 + 2.9, 0.7), n=fc.n, u=fc.u, width=hw * 2, th=th,
                          cool=hero))
    # 1층 위 띠, 층 띠, 옥상 코니스
    rz = math.atan2(fc.u.y, fc.u.x)
    detail.box(fc.p(W / 2, z0 + GROUND_H - 0.08, 0.06), (W, 0.12, 0.16), "concrete", rz=rz)
    for k in range(1, b["floors"]):
        zf = z0 + GROUND_H + FLOOR_H * (k - 1)
        detail.box(fc.p(W / 2, zf, 0.04), (W, 0.08, 0.12), "concrete", rz=rz)
    detail.box(fc.p(W / 2, b["height"] - 0.12, 0.1), (W + 0.2, 0.2, 0.24), "concrete", rz=rz)

    # 1층 가로 간판
    hname, hfi = H_SIGNS.get(key, (None, None))
    hname = hname if hfi == fi else None
    if key[1] == "corner" and key[0] == (1, 1):
        # 모퉁이 가게: 두 면 모두 CONNECT AI LAB
        corner_end = 0.0 if fc.p(0, 0).length < fc.p(W, 0).length else W
        a = 2.9 if corner_end == 0.0 else W - 2.9
        sign_box(detail, fc, a, z0 + GROUND_H - 0.55, 5.0, 0.9, "connect_ai_lab")
    elif hname:
        sign_box(detail, fc, W / 2, z0 + GROUND_H - 0.58, min(W - 1.0, 5.6), 0.95, hname)

    # 세로 돌출 간판: 교차로에 가까운 끝 쪽, 2~4층
    vname, vfi = V_SIGNS.get(key, (None, None))
    if vname and vfi == fi:
        near = 0.0 if fc.p(0, 0).length < fc.p(W, 0).length else W
        a = 0.55 if near == 0.0 else W - 0.55
        vertical_sign(detail, fc, a, z0 + GROUND_H + 0.5, vname)

    # 기준층 창문
    for k in range(1, b["floors"]):
        zf = z0 + GROUND_H + FLOOR_H * (k - 1)
        cnt = max(1, int((W - 1.0) / 2.5))
        sp = W / cnt
        ww = min(1.7, sp - 0.9)
        for i in range(cnt):
            a = sp * (i + 0.5)
            r = rng.random()
            th = 0.08 + 0.88 * (r / 0.72) if r < 0.72 else 5.0
            detail.quad(fc.p(a, zf + 1.65, 0.02), fc.u * (ww / 2 + 0.07), V(0, 0, 0.8), "alu", uv=False)
            glass.quad(fc.p(a, zf + 1.65, 0.03), fc.u * (ww / 2), V(0, 0, 0.73), "window",
                       attrs={"lit_th": th, "tint": rng.random() ** 2, "bright": 0.3 + 0.7 * rng.random()},
                       uv=False)
            detail.box(fc.p(a, zf + 0.87, 0.06), (ww + 0.2, 0.12, 0.06), "concrete", rz=rz)
            if rng.random() < 0.3:
                ac_unit(detail, fc, a + rng.uniform(-0.3, 0.3), zf + 0.25)


def sign_box(mb, fc, a, zc, w, h, mat):
    d = 0.22
    c = fc.p(a, zc, 0.04 + d / 2)
    rz = math.atan2(fc.u.y, fc.u.x)
    mb.box(c, (w, d, h), "sign_side", rz=rz, skip=("-y",))
    # 앞면 (밖을 향함): a=오른쪽, b=위
    mb.quad(c + fc.n * (d / 2 + 0.002), fc.u * (w / 2), V(0, 0, h / 2), mat)


def vertical_sign(mb, fc, a, z_bottom, mat, w=0.62, h=2.5, d=0.24, gap=0.25):
    out = gap + w / 2
    c = fc.p(a, z_bottom + h / 2, out)
    rz = math.atan2(fc.u.y, fc.u.x)
    mb.box(c, (d, w, h), "sign_side", rz=rz, skip=("+x", "-x"))
    for s in (1, -1):
        m = fc.u * s                       # 간판 면의 법선 (길 따라 양쪽)
        right = V(0, 0, 1).cross(m)        # 그 면을 보는 사람의 오른쪽
        mb.quad(c + m * (d / 2 + 0.002), right * (w / 2), V(0, 0, h / 2), mat)
    for zz in (z_bottom + 0.3, z_bottom + h - 0.3):
        mb.box(fc.p(a, zz, gap / 2 + 0.02), (0.05, gap + 0.06, 0.05), "galv", rz=rz)


def ac_unit(mb, fc, a, z):
    rz = math.atan2(fc.u.y, fc.u.x)
    c = fc.p(a, z + 0.32, 0.36)
    mb.box(c, (0.82, 0.3, 0.62), "ac_unit", rz=rz)
    mb.cyl(c + fc.n * 0.151 + fc.u * 0.12, c + fc.n * 0.16 + fc.u * 0.12, 0.22, 0.22, "ac_grill", 14)
    for s in (-0.3, 0.3):
        mb.box(fc.p(a + s, z, 0.3), (0.04, 0.6, 0.04), "galv", rz=rz)


def rooftop(rng, blds, mb):
    for b in blds:
        if rng.random() < 0.65:
            # 옥상 물탱크: 도로 쪽 벽 가까이 두어 거리에서 실루엣이 보이게
            cx = rng.uniform(b["x0"] + 2.0, b["x1"] - 2.0)
            cy = rng.uniform(b["y0"] + 2.0, b["y1"] - 2.0)
            for fx, fy, _ in b["facades"]:
                if fx < 0:
                    cx = b["x0"] + 2.0
                elif fx > 0:
                    cx = b["x1"] - 2.0
                if fy < 0:
                    cy = b["y0"] + 2.0
                elif fy > 0:
                    cy = b["y1"] - 2.0
            z = b["height"]
            for sx in (-0.6, 0.6):
                for sy in (-0.6, 0.6):
                    mb.box((cx + sx, cy + sy, z + 0.35), (0.08, 0.08, 0.7), "galv")
            mb.cyl((cx, cy, z + 0.7), (cx, cy, z + 2.3), 0.85, 0.85, "water_tank", 16)
            mb.cyl((cx, cy, z + 2.3), (cx, cy, z + 2.5), 0.85, 0.55, "water_tank", 16)
        for _ in range(rng.randint(0, 3)):
            cx = rng.uniform(b["x0"] + 1, b["x1"] - 1)
            cy = rng.uniform(b["y0"] + 1, b["y1"] - 1)
            mb.box((cx, cy, b["height"] + 0.32), (0.85, 0.32, 0.62), "ac_unit", rz=rng.choice([0, math.pi / 2]))


# ---------------------------------------------------------------------------
# 전봇대 · 전선 · 신호등 · 가로등 · 가로수 · 볼라드
# ---------------------------------------------------------------------------

POLES = [(3.95, -17.0), (3.95, 11.2), (3.95, 31.0), (-3.95, 12.6), (-3.95, 33.0),
         (12.5, 3.95), (31.0, 3.95), (-14.0, 3.95), (-33.0, 3.95), (14.5, -3.95), (-16.0, -3.95)]
POLE_H = 10.6
SPANS = [(0, 1), (1, 2), (3, 4), (5, 6), (7, 8), (1, 3), (5, 9), (7, 10), (1, 5), (3, 7)]
TRANSFORMER_POLE = 1


def build_utility(rng):
    mb = MB()
    cdata = bpy.data.curves.new(PREFIX + "Wires", 'CURVE')
    cdata.dimensions = '3D'
    cdata.bevel_depth = 0.01
    cdata.bevel_resolution = 2
    cdata.use_fill_caps = True

    def wire(p0, p1, sag, radius=1.0, jitter=0.0, n=18):
        sp = cdata.splines.new('POLY')
        sp.points.add(n - 1)
        p0, p1 = V(p0), V(p1)
        side = (p1 - p0).cross(V(0, 0, 1)).normalized() if (p1 - p0).length > 0.01 else V(1, 0, 0)
        ph = rng.random() * 6.28
        for i in range(n):
            t = i / (n - 1)
            p = p0.lerp(p1, t)
            p.z -= sag * 4 * t * (1 - t)
            p += side * (jitter * math.sin(t * math.pi) * math.sin(t * 9 + ph))
            sp.points[i].co = (p.x, p.y, p.z, 1.0)
            sp.points[i].radius = radius

    def coil(center, r, turns=3, axis_n=V(1, 0, 0)):
        sp = cdata.splines.new('POLY')
        n = 24 * turns
        sp.points.add(n - 1)
        u = axis_n.cross(V(0, 0, 1)).normalized()
        for i in range(n):
            a = 2 * math.pi * i / 24
            p = V(center) + u * (math.cos(a) * r) + V(0, 0, math.sin(a) * r * 1.2) + axis_n * (i / n * 0.12)
            sp.points[i].co = (p.x, p.y, p.z, 1.0)
            sp.points[i].radius = 1.1

    tops = []
    for i, (x, y) in enumerate(POLES):
        mb.cyl((x, y, 0), (x, y, POLE_H), 0.19, 0.13, "pole_concrete", 12)
        # 완금(가로대)과 애자
        to_road = V(-x, 0, 0).normalized() if abs(x) < abs(y) else V(0, -y, 0).normalized()
        along = V(0, 0, 1).cross(to_road)
        arm_c = V(x, y, POLE_H - 0.45)
        mb.box(arm_c, (1.9, 0.09, 0.09), "galv", rz=math.atan2(along.y, along.x))
        for s in (-0.8, 0.0, 0.8):
            p = arm_c + along * s
            mb.cyl(p + V(0, 0, 0.05), p + V(0, 0, 0.24), 0.06, 0.05, "porcelain", 8)
        # 통신선 고정 밴드
        for zz in (7.4, 6.9, 6.4):
            mb.cyl((x, y, zz - 0.04), (x, y, zz + 0.04), 0.165, 0.165, "galv", 12, smooth=False)
        tops.append((V(x, y, 0), along))
        if rng.random() < 0.8:
            coil(V(x, y, rng.uniform(5.2, 6.2)) + to_road * 0.2, 0.32, axis_n=to_road)
        # 발판 볼트
        for k in range(8):
            zz = 2.4 + k * 0.45
            d = along * (0.16 if k % 2 else -0.16)
            mb.cyl(V(x, y, zz), V(x, y, zz) + d * 1.6, 0.012, 0.012, "galv", 6, cap=False)

    # 변압기 (주상변압기 2대)
    x, y = POLES[TRANSFORMER_POLE]
    for s in (-0.42, 0.42):
        c = V(x, y, 0) + V(0.42, s, 0)
        mb.cyl(c + V(0, 0, 7.75), c + V(0, 0, 8.75), 0.27, 0.27, "transformer", 16)
        mb.cyl(c + V(0, 0, 8.75), c + V(0, 0, 8.85), 0.27, 0.2, "transformer", 16)
        mb.cyl(c + V(0, 0, 8.85), c + V(0, 0, 9.05), 0.03, 0.03, "porcelain", 8)
        wire(c + V(0, 0, 9.05), V(x, y, POLE_H - 0.2) + V(0, s, 0), 0.05, 0.6)
    mb.box((x + 0.3, y, 7.7), (0.6, 1.2, 0.08), "galv")

    # 전선: 고압 3가닥 + 엉킨 통신선 다발
    for a, b in SPANS:
        (pa, ala), (pb, alb) = tops[a], tops[b]
        L = (pb - pa).length
        for s in (-0.8, 0.0, 0.8):
            wire(pa + ala * s + V(0, 0, POLE_H - 0.18), pb + alb * s + V(0, 0, POLE_H - 0.18), L * 0.012, 0.8)
        for k in range(rng.randint(5, 8)):
            za = rng.choice([7.4, 6.9, 6.4]) + rng.uniform(-0.08, 0.08)
            zb = rng.choice([7.4, 6.9, 6.4]) + rng.uniform(-0.08, 0.08)
            off_a = ala * rng.uniform(-0.2, 0.2)
            off_b = alb * rng.uniform(-0.2, 0.2)
            wire(pa + off_a + V(0, 0, za), pb + off_b + V(0, 0, zb), L * rng.uniform(0.02, 0.045),
                 rng.uniform(1.0, 2.0), jitter=rng.uniform(0.0, 0.12))

    # 인입선: 전봇대 → 건물 벽
    for i, (x, y) in enumerate(POLES):
        p = V(x, y, rng.uniform(6.3, 7.3))
        for _ in range(rng.randint(2, 4)):
            if abs(x) < abs(y):
                tgt = V(math.copysign(LINE, x), y + rng.uniform(-7, 7), rng.uniform(4.6, 7.5))
            else:
                tgt = V(x + rng.uniform(-7, 7), math.copysign(LINE, y), rng.uniform(4.6, 7.5))
            wire(p, tgt, 0.25, 0.6)
            mb.box(tgt, (0.08, 0.08, 0.12), "galv")

    ob = bpy.data.objects.new(PREFIX + "Wires", cdata)
    cdata.materials.append(MATS["wire"])
    COLL["street"].objects.link(ob)
    return mb


def build_signals(mb):
    """가로로 걸린 3구 신호등: 정지 신호(남북)는 적색, 동서는 녹색."""
    specs = [  # (기둥 위치, 팔 방향, 신호등이 보는 방향, 켜진 등)
        ((4.4, 6.7), (-1, 0), (0, -1), "red"),     # 북행 (카메라 쪽을 봄)
        ((6.7, -4.4), (0, 1), (1, 0), "green"),    # 서행
        ((-6.7, 4.4), (0, -1), (-1, 0), "green"),  # 동행
    ]
    for (x, y), arm, face, lit in specs:
        base = V(x, y, 0)
        armv = V(arm[0], arm[1], 0)
        facev = V(face[0], face[1], 0)
        mb.cyl(base, base + V(0, 0, 6.2), 0.12, 0.1, "galv", 12)
        mb.cyl(base + V(0, 0, 5.95), base + V(0, 0, 5.95) + armv * 4.6, 0.065, 0.05, "galv", 10)
        mb.cyl(base + V(0, 0, 5.2), base + V(0, 0, 5.95) + armv * 1.4, 0.03, 0.03, "galv", 6)
        head = base + armv * 3.6 + V(0, 0, 5.62)
        right = V(0, 0, 1).cross(facev)
        rz = math.atan2(right.y, right.x)
        mb.box(head, (1.3, 0.34, 0.42), "signal_body", rz=rz)
        mb.box(head + V(0, 0, 0.28), (0.04, 0.04, 0.16), "galv")
        for i, col in enumerate(("red", "yellow", "green")):
            c = head + right * ((i - 1) * 0.4) + facev * 0.172
            mat = f"sig_{col}_on" if col == lit else f"sig_{col}_off"
            mb.cyl(c, c + facev * 0.012, 0.14, 0.14, mat, 16)
            # 차양
            for k in range(6):
                a0 = math.pi * k / 6
                a1 = math.pi * (k + 1) / 6
                p0 = c + right * (math.cos(a0) * 0.17) + V(0, 0, math.sin(a0) * 0.17)
                p1 = c + right * (math.cos(a1) * 0.17) + V(0, 0, math.sin(a1) * 0.17)
                mb.poly([p0, p1, p1 + facev * 0.22, p0 + facev * 0.22], "signal_body")


LAMPS = [(4.15, -24.0), (4.15, 17.5), (-4.15, 23.0), (17.5, 4.15), (-19.0, 4.15),
         (22.0, -4.15), (4.15, -15.0), (-4.15, -32.0)]


def build_lamps(mb):
    for x, y in LAMPS:
        to_road = V(-x, 0, 0).normalized() if abs(x) < abs(y) else V(0, -y, 0).normalized()
        base = V(x, y, 0)
        mb.cyl(base, base + V(0, 0, 8.6), 0.09, 0.065, "galv", 12)
        mb.cyl(base + V(0, 0, 8.5), base + V(0, 0, 8.8) + to_road * 1.8, 0.045, 0.04, "galv", 8)
        head = base + to_road * 2.1 + V(0, 0, 8.82)
        right = V(0, 0, 1).cross(to_road)
        rz = math.atan2(right.y, right.x)
        mb.box(head, (0.34, 0.75, 0.1), "galv", rz=rz)
        mb.quad(head - V(0, 0, 0.052), to_road * 0.3, right * 0.13, "led_head", uv=False)
        L = bpy.data.lights.new(PREFIX + "Lamp", 'SPOT')
        L.color = (1.0, 0.93, 0.84)
        L.spot_size = math.radians(115)
        L.spot_blend = 0.5
        L.shadow_soft_size = 0.25
        drive(L, "energy", "380*" + on_ramp("night", 0.30, 0.12), ["night"])
        ob = bpy.data.objects.new(PREFIX + "Lamp", L)
        ob.location = head - V(0, 0, 0.08)
        COLL["lights"].objects.link(ob)


def build_trees(rng, mb):
    trees = [(4.6, -26.0), (4.6, -33.0), (-4.65, 27.0), (4.6, 23.5), (24.0, -4.6), (-25.0, 4.6),
             (28.0, 4.6), (-4.65, -26.0)]
    for x, y in trees:
        base = V(x, y, WALK_Z)
        mb.box(base + V(0, 0, 0.005), (1.2, 1.2, 0.01), "grate")
        top = base + V(rng.uniform(-0.2, 0.2), rng.uniform(-0.2, 0.2), 3.6)
        mb.cyl(base, top, 0.16, 0.1, "bark", 10)
        for _ in range(9):
            c = top + V(rng.uniform(-1.4, 1.4), rng.uniform(-1.4, 1.4), rng.uniform(0.2, 2.6))
            r = rng.uniform(0.9, 1.5)
            blob(mb, c, r, rng)
        for _ in range(4):
            d = V(rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(0.6, 1.2)).normalized()
            mb.cyl(top - V(0, 0, 0.6), top + d * 1.6, 0.06, 0.025, "bark", 6)


def blob(mb, c, r, rng):
    """잎 덩어리: 찌그러뜨린 이십면체 구."""
    t = (1 + 5 ** 0.5) / 2
    verts = [V(-1, t, 0), V(1, t, 0), V(-1, -t, 0), V(1, -t, 0), V(0, -1, t), V(0, 1, t),
             V(0, -1, -t), V(0, 1, -t), V(t, 0, -1), V(t, 0, 1), V(-t, 0, -1), V(-t, 0, 1)]
    faces = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4),
             (11, 10, 2), (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8),
             (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    # 한 번 세분
    mid = {}
    vs = [v.normalized() for v in verts]

    def m(a, b):
        k = (min(a, b), max(a, b))
        if k not in mid:
            vs.append((vs[a] + vs[b]).normalized())
            mid[k] = len(vs) - 1
        return mid[k]
    fs = []
    for a, b, c2 in faces:
        ab, bc, ca = m(a, b), m(b, c2), m(c2, a)
        fs += [(a, ab, ca), (b, bc, ab), (c2, ca, bc), (ab, bc, ca)]
    pts = []
    for v in vs:
        p = v * r * rng.uniform(0.78, 1.12)
        p.z *= 0.8
        pts.append(c + p)
    for f in fs:
        mb.poly([pts[i] for i in f], "foliage", smooth=True)


def build_bollards(mb):
    for qx in (1, -1):
        for qy in (1, -1):
            for k in range(3):
                a = math.radians(200 + 25 * k)
                cx = cy = ROAD + CURB_R
                p = V(qx * (cx + (CURB_R + 0.45) * math.cos(a)), qy * (cy + (CURB_R + 0.45) * math.sin(a)), WALK_Z)
                mb.cyl(p, p + V(0, 0, 0.85), 0.07, 0.07, "stainless", 12)
                mb.cyl(p + V(0, 0, 0.85), p + V(0, 0, 0.9), 0.07, 0.02, "stainless", 12)


# ---------------------------------------------------------------------------
# 비 (지오메트리 노드)
# ---------------------------------------------------------------------------

def _sock(node, name, out=False):
    socks = node.outputs if out else node.inputs
    return [s for s in socks if s.name == name and s.enabled][0]


def build_rain():
    ng = bpy.data.node_groups.get(PREFIX + "RainGN")
    if ng:
        bpy.data.node_groups.remove(ng)
    ng = bpy.data.node_groups.new(PREFIX + "RainGN", 'GeometryNodeTree')
    ng.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    s_rain = ng.interface.new_socket("Rain", in_out='INPUT', socket_type='NodeSocketFloat')
    s_rain.min_value, s_rain.max_value = 0.0, 1.0
    ng.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    n, L = ng.nodes, ng.links.new
    gi, go = n.new('NodeGroupInput'), n.new('NodeGroupOutput')

    SX, SY, H, Z0, SPEED = 32.0, 38.0, 12.0, 0.1, 9.0
    LOC = V(2.5, -4.0, 0.0)                 # 빗줄기 영역 중심 (카메라 앞을 덮음)
    CAM_C, CAM_R = V(-4.3, -19.8, 0.0), 2.2  # 카메라 바로 앞은 비워 렌즈에 붙는 굵은 줄 방지
    grid = n.new('GeometryNodeMeshGrid')
    grid.inputs['Size X'].default_value = SX
    grid.inputs['Size Y'].default_value = SY
    dist = n.new('GeometryNodeDistributePointsOnFaces')
    dist.distribute_method = 'RANDOM'
    dist.inputs['Density'].default_value = 30.0
    dist.inputs['Seed'].default_value = 11
    L(grid.outputs['Mesh'], dist.inputs['Mesh'])

    idn = n.new('GeometryNodeInputID')

    def rand(seed):
        r = n.new('FunctionNodeRandomValue')
        r.data_type = 'FLOAT'
        L(idn.outputs[0], _sock(r, 'ID'))
        _sock(r, 'Seed').default_value = seed
        return _sock(r, 'Value', out=True)

    def m(kind, a, b=0.0):
        nd = n.new('ShaderNodeMath')
        nd.operation = kind
        for s, val in zip(nd.inputs, (a, b)):
            if isinstance(val, bpy.types.NodeSocket):
                L(val, s)
            else:
                s.default_value = val
        return nd.outputs[0]

    t = n.new('GeometryNodeInputSceneTime').outputs['Seconds']
    phase = rand(3)
    # z = Z0 + H * fract(phase - t * SPEED / H)
    z = m('ADD', Z0, m('MULTIPLY', H, m('FRACT', m('SUBTRACT', phase, m('MULTIPLY', t, SPEED / H)))))
    pos = n.new('GeometryNodeInputPosition')
    sep = n.new('ShaderNodeSeparateXYZ')
    L(pos.outputs[0], sep.inputs[0])
    comb = n.new('ShaderNodeCombineXYZ')
    L(sep.outputs['X'], comb.inputs['X'])
    L(sep.outputs['Y'], comb.inputs['Y'])
    L(z, comb.inputs['Z'])
    setp = n.new('GeometryNodeSetPosition')
    L(dist.outputs['Points'], setp.inputs['Geometry'])
    L(comb.outputs[0], setp.inputs['Position'])
    # 밀도 = rain: 점 개수는 그대로 두고(모션블러용) rain 보다 큰 점은 크기 0
    keep = m('LESS_THAN', rand(7), gi.outputs['Rain'])
    cx, cy = CAM_C.x - LOC.x, CAM_C.y - LOC.y
    dxy = m('SQRT', m('ADD', m('POWER', m('SUBTRACT', sep.outputs['X'], cx), 2.0),
                      m('POWER', m('SUBTRACT', sep.outputs['Y'], cy), 2.0)))
    keep = m('MULTIPLY', keep, m('GREATER_THAN', dxy, CAM_R))
    cyl = n.new('GeometryNodeMeshCylinder')
    cyl.fill_type = 'NONE'
    cyl.inputs['Vertices'].default_value = 4
    cyl.inputs['Radius'].default_value = 0.0025
    cyl.inputs['Depth'].default_value = 0.16
    iop = n.new('GeometryNodeInstanceOnPoints')
    L(setp.outputs['Geometry'], iop.inputs['Points'])
    L(cyl.outputs['Mesh'], iop.inputs['Instance'])
    L(keep, iop.inputs['Scale'])
    real = n.new('GeometryNodeRealizeInstances')
    L(iop.outputs['Instances'], real.inputs[0])
    setm = n.new('GeometryNodeSetMaterial')
    setm.inputs['Material'].default_value = MATS["rain"]
    L(real.outputs[0], setm.inputs['Geometry'])
    L(setm.outputs['Geometry'], go.inputs[0])

    me = bpy.data.meshes.new(PREFIX + "Rain")
    ob = bpy.data.objects.new(PREFIX + "Rain", me)
    ob.location = LOC
    COLL["weather"].objects.link(ob)
    mod = ob.modifiers.new("Rain", 'NODES')
    mod.node_group = ng
    ident = [it for it in ng.interface.items_tree
             if getattr(it, "name", "") == "Rain" and getattr(it, "in_out", "") == 'INPUT'][0].identifier
    try:                                   # Blender 4.x: ID 속성
        mod[ident] = 0.0
        drive(ob, f'modifiers["Rain"]["{ident}"]', "rain", ["rain"])
    except TypeError:                      # Blender 5.x: modifier.properties.inputs
        inp = getattr(mod.properties.inputs, ident)
        inp.value = 0.0
        drive(inp, "value", "rain", ["rain"])
    return ob


# ---------------------------------------------------------------------------
# 하늘 · 해 · 조명
# ---------------------------------------------------------------------------

SUN_ELEV = "(-8+42*sun)"          # 도: sun=1 → 34°, sun=0 → -8°
SUN_AZ = "(280-45*sun)"           # 도: 낮 235°(남서) → 해질녘 280°(서북서)


def build_world(scene):
    w = bpy.data.worlds.get(PREFIX + "World")
    if w:
        bpy.data.worlds.remove(w)
    w = bpy.data.worlds.new(PREFIX + "World")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        w.use_nodes = True
    scene.world = w
    nt = w.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputWorld')
    sky = nt.nodes.new('ShaderNodeTexSky')
    types = [i.identifier for i in sky.bl_rna.properties['sky_type'].enum_items]
    sky.sky_type = 'MULTIPLE_SCATTERING' if 'MULTIPLE_SCATTERING' in types else 'NISHITA'
    sky.sun_disc = False  # 직사광은 태양 램프가 맡음 (이중 조명 방지)
    for prop, val in (("air_density", 1.0), ("aerosol_density", 1.1), ("dust_density", 1.1), ("ozone_density", 1.0)):
        if hasattr(sky, prop):
            setattr(sky, prop, val)
    drive(sky, "sun_elevation", f"{SUN_ELEV}*0.0174533", ["sun"])
    drive(sky, "sun_rotation", f"{SUN_AZ}*0.0174533", ["sun"])
    bg_day = nt.nodes.new('ShaderNodeBackground')
    nt.links.new(sky.outputs[0], bg_day.inputs['Color'])
    # 해가 지면 물리 하늘은 줄이고 남색 밤하늘로
    day_k = state(nt, "0.2*min(max((sun-0.08)/0.4,0),1)+0.01")
    nt.links.new(day_k, bg_day.inputs['Strength'])

    tc = nt.nodes.new('ShaderNodeTexCoord')
    sep = nt.nodes.new('ShaderNodeSeparateXYZ')
    nt.links.new(tc.outputs['Generated'], sep.inputs[0])
    ramp = nt.nodes.new('ShaderNodeValToRGB')
    nt.links.new(sep.outputs['Z'], ramp.inputs['Fac'])
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.0, (0.15, 0.085, 0.075, 1.0)  # 지평선: 도시 불빛
    els[1].position, els[1].color = 0.22, (0.022, 0.032, 0.085, 1.0)  # 남색
    e = els.new(0.07)
    e.color = (0.065, 0.05, 0.085, 1.0)
    e = els.new(0.7)
    e.color = (0.006, 0.01, 0.03, 1.0)
    bg_night = nt.nodes.new('ShaderNodeBackground')
    nt.links.new(ramp.outputs['Color'], bg_night.inputs['Color'])
    night_k = state(nt, "min(max((0.3-sun)/0.25,0),1)*(0.25+0.75*night)*0.8")
    # 카메라에 보이는 밤하늘은 남색으로 또렷하게, 건물을 비추는 양은 적게
    is_cam = nt.nodes.new('ShaderNodeLightPath').outputs['Is Camera Ray']
    nt.links.new(op(nt, 'MULTIPLY', night_k, mix_f(nt, is_cam, 0.1, 1.0)), bg_night.inputs['Strength'])
    add = nt.nodes.new('ShaderNodeAddShader')
    nt.links.new(bg_day.outputs[0], add.inputs[0])
    nt.links.new(bg_night.outputs[0], add.inputs[1])
    nt.links.new(add.outputs[0], out.inputs['Surface'])


def build_sun():
    L = bpy.data.lights.new(PREFIX + "Sun", 'SUN')
    L.angle = math.radians(0.6)
    ob = bpy.data.objects.new(PREFIX + "Sun", L)
    COLL["lights"].objects.link(ob)
    vis = "min(max((sun-0.19)/0.2,0),1)"
    drive(L, "energy", f"6.5*{vis}", ["sun"])
    drive(L, "color", "1.0", ["sun"], 0)
    drive(L, "color", "0.4+0.55*min(max((sun-0.19)/0.5,0),1)", ["sun"], 1)
    drive(L, "color", "0.13+0.79*min(max((sun-0.19)/0.5,0),1)", ["sun"], 2)
    drive(ob, "rotation_euler", f"-(90-{SUN_ELEV})*0.0174533", ["sun"], 0)
    drive(ob, "rotation_euler", "0*sun", ["sun"], 1)
    drive(ob, "rotation_euler", f"-{SUN_AZ}*0.0174533", ["sun"], 2)


def build_shop_lights(shops):
    for i, s in enumerate(shops):
        if s["pos"].length > 42:
            continue
        L = bpy.data.lights.new(PREFIX + "Shop", 'AREA')
        L.shape = 'RECTANGLE'
        L.size, L.size_y = max(1.0, s["width"] * 0.9), 0.8
        L.spread = math.radians(75)   # 보도에만 떨어지게 (맞은편 건물로 새지 않게)
        L.color = (0.85, 0.92, 1.0) if s["cool"] else (1.0, 0.82, 0.6)
        drive(L, "energy", f"{(110 if s['cool'] else 70) * s['width'] / 4:.0f}*" + on_ramp("night", s["th"], 0.05), ["night"])
        ob = bpy.data.objects.new(PREFIX + "Shop", L)
        ob.location = s["pos"]
        # 바깥을 향해 아래로 비춤 (-Z 가 빛 방향)
        d = (s["n"] * 0.55 + V(0, 0, -1.0)).normalized()
        ob.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()
        COLL["lights"].objects.link(ob)


# ---------------------------------------------------------------------------
# 카메라 · 상태 채널 · 렌더 설정
# ---------------------------------------------------------------------------

def build_camera(scene):
    cam = bpy.data.cameras.new(PREFIX + "Camera")
    cam.lens = 26
    cam.sensor_width = 36
    cam.clip_start = 0.1
    cam.clip_end = 400
    cam.shift_y = 0.05  # 젖은 도로의 간판 반사(지평선 아래 약 14~15°)가 화면에 들어오게
    ob = bpy.data.objects.new(PREFIX + "Camera", cam)
    COLL["camera"].objects.link(ob)
    scene.camera = ob
    start = V(-4.75, -21.0, WALK_Z + 1.6)       # 남서쪽 보도, 눈높이 1.6m (반사 지점이 횡단보도 앞에 오게)
    yaw = math.radians(20.0)                     # 북에서 동쪽으로 20°: 모퉁이 건물이 오른쪽 3/4
    pitch = 0.0                                  # 수평 유지, 위쪽은 렌즈 시프트로
    ob.rotation_euler = (math.radians(90) + pitch, 0, -yaw)
    flat = V(math.sin(yaw), math.cos(yaw), 0)
    end = start + flat * 2.6                      # 14초 동안 2.6m 밀고 들어감
    ob.location = start
    ob.keyframe_insert("location", frame=1)
    ob.location = end
    ob.keyframe_insert("location", frame=FRAME_END)
    for fc in _fcurves(ob):
        for kp in fc.keyframe_points:
            kp.interpolation = 'LINEAR'
    return ob


def _fcurves(ob):
    ad = ob.animation_data
    if ad is None or ad.action is None:
        return []
    act = ad.action
    if hasattr(act, "fcurves") and len(getattr(act, "fcurves", [])):
        return list(act.fcurves)
    # Blender 5.x: 레이어/채널백 구조
    out = []
    for layer in getattr(act, "layers", []):
        for strip in layer.strips:
            for bag in strip.channelbags:
                out.extend(bag.fcurves)
    return out


def build_ctrl():
    global CTRL
    ob = bpy.data.objects.new(PREFIX + "CTRL", None)
    ob.empty_display_type = 'PLAIN_AXES'
    ob.empty_display_size = 2.0
    ob.location = (0, 0, 12)
    COLL["control"].objects.link(ob)
    CTRL = ob
    timeline = {
        # 해 고도 = -8+42*sun: 4.5초 9.6°, 5.2초 0.4°(일몰), 6초 -8°
        "sun": [(0.0, 1), (3.5, 1), (4.5, 0.42), (5.2, 0.2), (6.0, 0), (DURATION, 0)],
        "night": [(0.0, 0), (3.5, 0), (6.0, 1), (DURATION, 1)],
        "rain": [(0.0, 0), (9.0, 0), (10.5, 1), (DURATION, 1)],
        "wet": [(0.0, 0), (9.2, 0), (10.5, 1), (DURATION, 1)],
    }
    for k, keys in timeline.items():
        ob[k] = float(keys[0][1])
        ui = ob.id_properties_ui(k)
        ui.update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0)
        for sec, val in keys:
            ob[k] = float(val)
            ob.keyframe_insert(f'["{k}"]', frame=frame_at(sec))
    for fc in _fcurves(ob):
        for kp in fc.keyframe_points:
            kp.interpolation = 'BEZIER'
            kp.handle_left_type = kp.handle_right_type = 'AUTO_CLAMPED'
        fc.update()
    return ob


def setup_render(scene):
    scene.render.engine = 'CYCLES'
    scene.render.fps = FPS
    scene.frame_start, scene.frame_end = 1, FRAME_END
    scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
    scene.render.resolution_percentage = 100
    c = scene.cycles
    c.samples = 32
    c.use_adaptive_sampling = True
    c.adaptive_threshold = 0.02
    c.use_denoising = True
    try:
        c.denoiser = 'OPENIMAGEDENOISE'
    except TypeError:
        pass
    c.max_bounces = 6
    c.glossy_bounces = 3
    c.transparent_max_bounces = 12
    c.caustics_reflective = False
    c.caustics_refractive = False
    c.blur_glossy = 0.5
    c.sample_clamp_indirect = 8.0
    scene.render.use_motion_blur = True
    scene.render.motion_blur_shutter = 0.5
    try:
        scene.view_settings.view_transform = 'AgX'
        scene.view_settings.look = 'AgX - Punchy'
    except TypeError:
        scene.view_settings.view_transform = 'Filmic'
    # 타임랩스 카메라처럼 밤에는 노출을 올림
    drive(scene.view_settings, "exposure", "0.0+0.4*night", ["night"])


# ---------------------------------------------------------------------------
# 조립
# ---------------------------------------------------------------------------

def _prepare_scene():
    if bpy.app.background:
        scene = bpy.context.scene
        for ob in list(scene.objects):
            bpy.data.objects.remove(ob, do_unlink=True)
    else:
        old = bpy.data.scenes.get(SCENE_NAME)
        if old:
            for ob in list(old.objects):
                bpy.data.objects.remove(ob, do_unlink=True)
            bpy.data.scenes.remove(old)
        # 다시 실행할 때 남은 SC_ 데이터 정리
        for coll in (bpy.data.meshes, bpy.data.curves, bpy.data.lights, bpy.data.cameras):
            for idb in list(coll):
                if idb.name.startswith(PREFIX) and idb.users == 0:
                    coll.remove(idb)
        scene = bpy.data.scenes.new(SCENE_NAME)
        for win in bpy.context.window_manager.windows:
            win.scene = scene
    scene.name = SCENE_NAME
    for name in list(COLL):
        COLL.pop(name)
    for name in ("control", "ground", "buildings", "street", "lights", "weather", "camera"):
        full = PREFIX + name
        old = bpy.data.collections.get(full)
        if old:
            bpy.data.collections.remove(old)
        c = bpy.data.collections.new(full)
        scene.collection.children.link(c)
        COLL[name] = c
    return scene


def _default_sign_dir():
    cands = []
    if "__file__" in globals():
        cands.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "signs"))
    for t in bpy.data.texts:  # 텍스트 에디터에서 실행한 경우
        if t.filepath:
            cands.append(os.path.join(os.path.dirname(bpy.path.abspath(t.filepath)), "signs"))
    if bpy.data.filepath:
        cands.append(os.path.join(os.path.dirname(bpy.data.filepath), "signs"))
    for c in cands:
        if os.path.isfile(os.path.join(c, "signs.json")):
            return c
    return cands[0] if cands else "signs"


def build(sign_dir=None):
    """장면 전체를 만들고 장면 오브젝트를 돌려줍니다."""
    if sign_dir is None:
        sign_dir = _default_sign_dir()
    manifest_path = os.path.join(sign_dir, "signs.json")
    if not os.path.isfile(manifest_path):
        raise FileNotFoundError(f"간판 텍스처가 없습니다: {manifest_path} (uv run blender/make_signs.py)")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    rng = random.Random(SEED)
    STATS["drivers"] = 0
    MATS.clear()
    scene = _prepare_scene()
    build_ctrl()
    build_materials(sign_dir, manifest)
    build_ground()
    blds = plan_buildings(rng)
    shops = build_buildings(rng, blds)
    street = build_utility(rng)
    build_signals(street)
    build_lamps(street)
    build_trees(rng, street)
    build_bollards(street)
    street.emit("Street", "street")
    build_rain()
    build_world(scene)
    build_sun()
    build_shop_lights(shops)
    build_camera(scene)
    setup_render(scene)
    scene.frame_set(1)

    # 점검: 모든 재질·조명에 드라이버가 걸렸는지, 모두 단순식인지(파이썬 자동 실행 없이 동작)
    def drivers_of(idb):
        ad = getattr(idb, "animation_data", None)
        return list(ad.drivers) if ad else []
    no_drv_mats = [m.name for m in MATS.values() if not drivers_of(m.node_tree)]
    lights = [o.data for o in scene.objects if o.type == 'LIGHT']
    no_drv_lights = [L.name for L in lights if not drivers_of(L)]
    owners = [m.node_tree for m in MATS.values()] + lights + [scene.world.node_tree, scene] + list(scene.objects)
    complex_drivers = sum(1 for idb in owners for fc in drivers_of(idb) if not fc.driver.is_simple_expression)
    print(f"[SeoulCrossroad] 건물 {len(blds)}동, 오브젝트 {len(scene.objects)}개, 조명 {len(lights)}개, "
          f"재질 {len(MATS)}개, 드라이버 {STATS['drivers']}개")
    print(f"[SeoulCrossroad] 드라이버 없는 재질: {no_drv_mats or '없음'} / 드라이버 없는 조명: {no_drv_lights or '없음'} "
          f"/ 단순식이 아닌 드라이버: {complex_drivers}개")
    return scene


def render_stills(scene, out_dir, percent=33, samples=None, which=None):
    os.makedirs(out_dir, exist_ok=True)
    scene.render.resolution_percentage = percent
    if samples:
        scene.cycles.samples = samples
    scene.render.image_settings.file_format = 'PNG'
    paths = []
    which = which or DEFAULT_STILLS
    for sec, name in STILLS:
        if name not in which:
            continue
        scene.frame_set(frame_at(sec))
        scene.render.filepath = os.path.join(out_dir, f"{name}_{sec:04.1f}s.png")
        bpy.ops.render.render(write_still=True, scene=scene.name)
        paths.append(scene.render.filepath)
        print(f"[SeoulCrossroad] 스틸 저장: {scene.render.filepath}")
    scene.render.resolution_percentage = 100
    return paths


def main_cli(argv):
    import argparse
    ap = argparse.ArgumentParser(description="서울 골목 사거리 14초 원테이크")
    ap.add_argument("--signs", help="간판 폴더 (기본: 이 파일 옆 signs/)")
    ap.add_argument("--stills", help="낮/밤/비 스틸을 저장할 폴더")
    ap.add_argument("--percent", type=int, default=33, help="스틸 해상도 비율 (기본 33%%)")
    ap.add_argument("--samples", type=int, help="스틸 샘플 수 (기본 32)")
    ap.add_argument("--only", nargs="*", help="day dusk night rain 중 고르기 (기본: day night rain)")
    ap.add_argument("--save", help=".blend 로 저장할 경로")
    args = ap.parse_args(argv)
    scene = build(args.signs)
    if args.save:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.save))
    if args.stills:
        render_stills(scene, args.stills, args.percent, args.samples, args.only)


if __name__ == "__main__":
    if "--" in sys.argv:                       # blender -b -P seoul_crossroad.py -- ...
        _argv = sys.argv[sys.argv.index("--") + 1:]
    elif bpy.app.background and os.path.basename(sys.argv[0]).startswith("seoul_crossroad"):
        _argv = sys.argv[1:]                   # python seoul_crossroad.py ... (pip bpy)
    else:
        _argv = []                             # 블렌더 텍스트 에디터에서 Run Script
    main_cli(_argv)
