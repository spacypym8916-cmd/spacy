# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""블렌더 MCP 애드온 연결 확인 스크립트.

AI 앱을 거치지 않고 블렌더 애드온에 직접 장면 정보를 요청해서,
블렌더 쪽 연결이 살아 있는지 확인합니다. 장면을 읽기만 하고 아무것도 바꾸지 않습니다.

사용법 (블렌더를 켜 둔 채로):
    uv run check_blender.py
    uv run check_blender.py --port 9877   # 애드온 포트를 바꿨을 때
"""

import argparse
import json
import socket
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="블렌더 MCP 애드온 연결 확인")
    parser.add_argument("--host", default="localhost", help="애드온 주소 (기본: localhost)")
    parser.add_argument("--port", type=int, default=9876, help="애드온 포트 (기본: 9876)")
    parser.add_argument("--timeout", type=float, default=30, help="응답 대기 시간(초) (기본: 30)")
    args = parser.parse_args()
    host, port = args.host, args.port

    try:
        sock = socket.create_connection((host, port), timeout=5)
    except OSError as e:
        print(f"[연결 실패] {host}:{port} 에 블렌더 애드온이 없습니다. ({e})")
        print("블렌더가 켜져 있는지, N 패널 > MCP for Blender 탭에 Connected 가 보이는지 확인하세요.")
        return 1

    # 애드온은 JSON 명령 하나를 받으면 JSON 응답 하나를 돌려줍니다.
    response = None
    try:
        sock.sendall(json.dumps({"type": "get_scene_info", "params": {}}).encode("utf-8"))
        sock.settimeout(args.timeout)
        buf = b""
        while response is None:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
            try:
                response = json.loads(buf.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                pass  # 아직 응답이 다 오지 않음
    except socket.timeout:
        print(f"[응답 없음] {args.timeout:g}초 동안 애드온이 응답하지 않았습니다.")
        print("블렌더가 다른 작업(렌더링 등)으로 바쁜지 확인하세요.")
        return 1
    except OSError as e:
        print(f"[통신 오류] {e}")
        return 1
    finally:
        sock.close()

    if not response or response.get("status") != "success":
        print("[응답 오류]", response)
        return 1

    info = response.get("result") or {}
    if "error" in info:
        print("[장면 읽기 오류]", info["error"])
        return 1

    objects = info.get("objects", [])
    print(f"[연결 성공] {host}:{port}")
    print(f"장면: {info.get('name')} / 오브젝트 {info.get('object_count')}개 / 재질 {info.get('materials_count')}개")
    for obj in objects:
        print(f"  - {obj.get('name')} ({obj.get('type')}) 위치 {obj.get('location')}")
    if (info.get("object_count") or 0) > len(objects):
        print(f"  ... (애드온이 앞의 {len(objects)}개만 알려 줍니다)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
