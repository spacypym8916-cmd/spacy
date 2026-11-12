# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""블렌더 MCP 애드온으로 seoul_crossroad.py 를 보내, 지금 열려 있는 블렌더에 장면을 만듭니다.

AI 앱을 거치지 않고 MCP for Blender 애드온(localhost:9876)에 'execute_code' 명령을 직접 보냅니다.
블렌더에는 새 장면 'SeoulCrossroad' 가 생기고 그 장면으로 전환됩니다(기존 장면은 그대로 둡니다).

사용법 (블렌더를 켜 두고, N 패널 MCP for Blender 탭에 Connected 가 보이는 상태에서):
    uv run blender/run_in_blender.py
    uv run blender/run_in_blender.py --stills ~/Desktop/seoul_stills   # 낮/밤/비 스틸까지 (블렌더가 몇 분 멈춤)
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def send(host: str, port: int, command: dict, timeout: float) -> dict:
    with socket.create_connection((host, port), timeout=5) as sock:
        sock.sendall(json.dumps(command).encode("utf-8"))
        sock.settimeout(timeout)
        buf = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                raise ConnectionError("애드온이 응답 없이 연결을 닫았습니다.")
            buf += chunk
            try:
                return json.loads(buf.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue  # 아직 응답이 다 오지 않음


def main() -> int:
    ap = argparse.ArgumentParser(description="서울 골목 사거리 장면을 블렌더 MCP로 만들기")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=9876)
    ap.add_argument("--signs", default=str(HERE / "signs"), help="간판 폴더 (기본: blender/signs)")
    ap.add_argument("--stills", help="낮/밤/비 저해상도 스틸을 저장할 폴더 (지정하면 렌더까지)")
    ap.add_argument("--percent", type=int, default=33, help="스틸 해상도 비율 (기본 33%%)")
    ap.add_argument("--samples", type=int, help="스틸 샘플 수 (기본 32)")
    ap.add_argument("--timeout", type=float, default=900, help="응답 대기 시간(초) (기본 900)")
    args = ap.parse_args()

    script = HERE / "seoul_crossroad.py"
    signs = Path(args.signs).expanduser().resolve()
    if not (signs / "signs.json").is_file():
        print(f"[오류] 간판 텍스처가 없습니다: {signs}")
        print("먼저 uv run blender/make_signs.py 를 실행하세요.")
        return 1

    code = script.read_text(encoding="utf-8")
    code += f"\n\n_scene = build(sign_dir={str(signs)!r})\n"
    if args.stills:
        out = str(Path(args.stills).expanduser().resolve())
        code += f"render_stills(_scene, {out!r}, {args.percent}, {args.samples!r})\n"
        print("스틸을 렌더하는 동안 블렌더 화면이 몇 분 멈출 수 있습니다.")

    print(f"{args.host}:{args.port} 의 블렌더로 장면 스크립트를 보냅니다...")
    try:
        res = send(args.host, args.port, {"type": "execute_code", "params": {"code": code}}, args.timeout)
    except OSError as e:
        print(f"[연결 실패] {e}")
        print("블렌더가 켜져 있는지, N 패널 > MCP for Blender 탭에 Connected 가 보이는지 확인하세요.")
        return 1

    if res.get("status") != "success":
        msg = res.get("message", res)
        try:
            detail = json.loads(msg)
            print(f"[블렌더 오류] {detail.get('exception_type')}: {detail.get('message')}")
            print(detail.get("traceback", ""))
        except (TypeError, ValueError):
            print("[블렌더 오류]", msg)
        return 1

    output = (res.get("result") or {}).get("result", "")
    print(output.strip() or "(출력 없음)")
    print("완료: 블렌더에서 'SeoulCrossroad' 장면을 확인하세요. 스페이스바로 재생, F12 로 현재 프레임 렌더.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
