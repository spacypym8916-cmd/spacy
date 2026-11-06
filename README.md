# spacy

## MCP for Blender 설치 가이드

AI 앱(Claude Desktop, Claude Code, Cursor, VS Code, Codex 등)이 블렌더를 직접 조작할 수 있게 연결하는 방법입니다.
사용하는 패키지는 [`mcp-for-blender`](https://github.com/ahujasid/blender-mcp)입니다. 예전 이름은 `blender-mcp`이고, 2.2.7 버전을 기준으로 확인했습니다.

### 1. 블렌더 설치 (무료)

<https://www.blender.org/download/>

> 블렌더를 **새로 설치했거나 버전을 올렸다면, 2단계 전에 블렌더를 한 번 열었다 닫아 주세요.**
> 이전 버전 설정이 남아 있으면, 새 버전을 한 번도 열지 않은 상태에서는 애드온이 자동으로 켜지지 않습니다.
> 첫 실행 때 나오는 "예전 설정 가져오기" 선택을 건너뛰지 않게 하려는 동작입니다.

### 2. uv 설치 → 한 줄 설정

먼저 uv를 설치합니다.

Mac:

```bash
brew install uv
```

Windows:

```bash
winget install --id=astral-sh.uv -e
```

그다음 **블렌더를 완전히 끈 상태에서** 아래 명령을 실행합니다. Mac과 Windows 모두 같습니다.

```bash
uvx mcp-for-blender setup
```

이 명령 하나로 다음 세 가지가 처리됩니다.

- 설치된 AI 앱을 찾아 MCP 서버(`blender`)를 등록합니다.
- 블렌더 애드온을 설치합니다.
- 블렌더를 화면 없이 백그라운드로 실행해 애드온을 켭니다.

> ⚠️ **블렌더가 열려 있으면 애드온이 설치만 되고 켜지지는 않습니다.** 블렌더를 끄고 다시 실행하세요.

#### setup 옵션

| 옵션 | 설명 |
| --- | --- |
| `--dry-run` | 무엇이 바뀌는지 미리 보여 줍니다. 실제로는 아무것도 바꾸지 않습니다. |
| `--yes`, `-y` | 찾은 AI 앱 전부에 묻지 않고 등록합니다. |
| `--skip-addon` | AI 앱 등록만 하고 블렌더는 건드리지 않습니다. |

AI 앱 설정 파일은 통째로 덮어쓰지 않고 기존 내용과 합칩니다. 바꾸기 전 파일은 같은 폴더에 `.bak`으로 백업됩니다.

### 3. 연결 확인

1. AI 앱을 **완전히 종료했다가** 다시 켭니다. 창만 닫으면 안 되고, Mac은 `⌘Q`, Windows는 트레이 아이콘에서 종료하세요.
2. 블렌더를 켜고 **켜 둔 상태로 둡니다.**
3. 블렌더 3D 화면에서 `N` 키를 눌러 사이드바를 열고 **MCP for Blender** 탭을 엽니다.
4. `Connected on localhost:9876` 이 보이면 연결된 것입니다.

연결됐다면 [PROMPTS.md](PROMPTS.md)의 예시 프롬프트로 시작해 보세요.

### 문제 해결

| 증상 | 해결 |
| --- | --- |
| N 패널에 **MCP for Blender** 탭이 없음 | 애드온이 꺼져 있는 것입니다. **Edit → Preferences → Add-ons**에서 "MCP for Blender"를 검색해 체크하세요. |
| setup이 "Blender is open"이라고 함 | 블렌더를 끄고 `uvx mcp-for-blender setup`을 다시 실행하거나, 위처럼 직접 켜세요. |
| setup이 "hasn't been opened yet"이라고 함 | 블렌더를 한 번 열어 설정 가져오기를 선택한 다음, 위처럼 애드온을 직접 켜세요. |
| AI 앱에 블렌더 도구가 안 보임 | AI 앱을 완전히 종료했다가 다시 켜세요. 그래도 안 되면 `--dry-run`으로 등록 상태를 확인하세요. |
| 기본 포트(9876)를 이미 다른 프로그램이 사용 중 | N 패널에서 포트를 바꾸고, MCP 서버 실행 인자에 `--port <번호>`를 같은 값으로 넣으세요. |

### 사용 통계(텔레메트리) 끄기

기본으로 켜져 있습니다. 끄려면 MCP 서버 환경 변수에 `DISABLE_TELEMETRY=1`을 설정하세요.
