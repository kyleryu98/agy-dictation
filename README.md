# AGY Dictation

**맥의 입력칸에서 단축키를 누르고 말하면, 하단 녹음창을 거쳐 현재 커서에 글을 넣습니다.** 공식 Antigravity CLI의 음성 전사를 사용하는 독립적인 도구입니다. Google의 공식 제품은 아닙니다.

- `Control + ₩`: 녹음 시작 → 다시 눌러 종료 → 전사 → 자동 입력
- 하단 창에 녹음 시간과 처리 상태 표시, 완료 후 자동 닫힘
- `Esc` 또는 ×로 취소
- 입력 대상 앱 전환과 클립보드 사용 없음

> **macOS 개발자 프리뷰입니다.** Windows는 아직 지원하지 않습니다. 소스에서 설치하는 방식이며, 다른 컴퓨터에 바로 배포할 독립 실행형 DMG는 제공하지 않습니다. 모든 앱·입력칸의 호환성을 검증한 것은 아닙니다.

## 설치 전 준비

1. **macOS와 Python.org 프레임워크 Python**이 필요합니다. 원래 환경은 Python 3.13에서 검증했습니다. [Python macOS 설치 프로그램](https://www.python.org/downloads/macos/)을 사용할 수 있습니다. 일반 Python 실행만 가능한 환경이나 일부 Homebrew 설치는 앱 빌드 조건을 충족하지 않을 수 있습니다.
2. [공식 AGY CLI](https://antigravity.google/docs/cli/install)를 설치합니다. 터미널에서 `agy --version`이 동작해야 합니다.
3. 본인의 **AGY 개인 계정**으로 로그인합니다. 무료 개인 계정도 공식 안내상 CLI를 사용할 수 있습니다. 이 프로젝트의 무료 음성 실측은 아직 하지 않았으며 무제한 사용을 보장하지 않습니다. 비즈니스·엔터프라이즈 계정의 음성 입력은 현재 공식 문서상 미지원입니다.
4. 소스를 내려받아 압축을 풀거나 Git으로 복제한 뒤, 아래 명령을 **저장소 최상위 폴더**에서 실행합니다.

## 설치와 첫 실행

### 1. 환경 준비와 앱 빌드

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python scripts/doctor.py --strict
.venv/bin/python scripts/build_macos.py --output dist
```

`doctor --strict`가 실패하면 출력에서 빠진 항목을 먼저 해결하세요. 빌드는 `dist`에 결과를 만들며 설치나 실행을 하지 않습니다. `dist`가 이미 있으면 비어 있는 다른 출력 폴더를 지정하세요.

### 2. 이 Mac에 설치

```sh
.venv/bin/python scripts/manage_macos.py install --from-build dist --apply
.venv/bin/python scripts/manage_macos.py setup-cli --apply
```

`setup-cli`는 전사용 폴더에서 공식 AGY CLI를 엽니다. 본인이 로그인·초기 안내·이용약관·해당 폴더 신뢰 여부를 확인한 뒤 CLI를 종료하세요. 이 도구는 동의 버튼을 대신 누르거나 에이전트에 작업을 자동 제출하지 않습니다.

설치 도구는 기존 앱·서비스 파일이 있으면 덮어쓰지 않습니다. 이 단계에서는 로그인 서비스를 시작하지 않습니다.

### 3. macOS 권한 설정과 시작

```sh
.venv/bin/python scripts/manage_macos.py permissions
```

출력된 Python 앱을 **시스템 설정 → 개인정보 보호 및 보안 → 손쉬운 사용**에서 허용합니다. macOS 버전에 따라 항목 이름이 **기기 제어 및 데이터 접근**으로 표시될 수 있습니다.

```sh
.venv/bin/python scripts/manage_macos.py start --apply
.venv/bin/python scripts/manage_macos.py status
```

처음 뜨는 **ProListen Voice Engine의 마이크 접근 요청**은 직접 허용하세요. 권한을 허용하기 전에 서비스가 멈췄다면 `start --apply`를 다시 실행합니다. 권한은 자동으로 허용되지 않습니다.

### 4. 첫 받아쓰기

메모장 역할의 빈 TextEdit 문서에서 입력 위치를 클릭하고 `Control + ₩`를 눌러 보세요. 하단에 **녹음 중**이 뜬 뒤 말하고, 같은 키를 다시 누르면 **글로 바꾸는 중 → 입력 완료** 순서로 진행합니다. 메시지 전송 버튼이나 Enter는 자동으로 누르지 않습니다.

미국식 키보드 기준으로 Control + grave/backtick 또는 Control + backslash를 감지합니다. 한글 자판의 ₩ 표기는 키보드마다 위치가 다를 수 있습니다.

## 다음 로그인부터

`start --apply`는 사용자 로그인 서비스를 등록합니다. 이후 로그인하면 자동으로 준비되며 AGY 데스크톱 앱이나 터미널 창을 따로 켤 필요가 없습니다. 인터넷 연결과 AGY 로그인 상태는 필요합니다.

**설치 후 저장소 폴더와 `.venv`, Python 프레임워크를 옮기거나 지우지 마세요.** 현재 개발용 앱은 이 컴퓨터의 해당 경로와 모듈에 의존합니다. 다른 사람에게 `dist`를 전달하지 말고 소스에서 각자 빌드하도록 안내하세요.

## 중지·업데이트·삭제

```sh
.venv/bin/python scripts/manage_macos.py stop --apply
.venv/bin/python scripts/manage_macos.py uninstall --apply
```

중지는 이 버전의 서비스와 인증된 음성 엔진만 종료합니다. 삭제는 설치한 앱·실행 파일·LaunchAgent를 제거하며 **로그, 복구 전사문, 공식 AGY 로그인 정보, 원본 저장소는 보존**합니다.

업데이트는 중지 → 삭제 → 소스/의존성 업데이트 → 새로운 출력 폴더에 빌드 → 설치 → 시작 순서입니다. [설치·문제 해결 문서](docs/installation.md)를 참고하세요.

`--apply`를 생략하면 변경 계획만 표시합니다. `status`, `permissions`, `doctor.py`는 읽기 전용입니다. 다른 받아쓰기 도구와 같은 단축키를 동시에 사용하지 마세요.

## 문제 해결

| 증상 | 확인할 것 |
| --- | --- |
| 앱 빌드 실패 | `doctor.py --strict`, Python.org 프레임워크 설치 여부 |
| 단축키 무반응 | Python 제어 권한, 입력칸 포커스, 서비스 상태, 다른 앱 단축키 충돌 |
| 로그인/폴더 신뢰 안내 | `setup-cli --apply`를 직접 실행해 초기 설정 완료 |
| 마이크 권한 안내 | ProListen Voice Engine 마이크 허용 후 재시작 |
| 입력 위치가 바뀌었다는 안내 | 녹음 중 다른 입력칸으로 이동하거나 내용을 수정하지 않기 |
| 기존 설치가 있다고 나옴 | 먼저 해당 버전을 중지·삭제. 임의로 파일을 덮어쓰지 않기 |

오류 때 전사문이 `~/Library/Application Support/ProListenDictation/last-transcript.txt`에 남을 수 있습니다. 로그와 복구문은 공개 이슈에 그대로 올리지 마세요.

## 검증 상태와 한계

- 원래 개인용 구현에서 한국어 음성 → TextEdit 직접 입력, 포커스·클립보드 유지, 취소 동작을 확인했습니다.
- 재구성판은 정적 검사·단위 테스트·패키지 빌드·격리된 설치/삭제 파일 흐름을 검증합니다. **새 Mac의 실제 계정 로그인·권한 설정·마이크 입력까지 검증한 것은 아닙니다.** [검증 기록](docs/testing.md)
- AGY CLI의 대화형 음성 및 외부 편집기 기능에 의존하므로 CLI 업데이트에 영향을 받을 수 있습니다. 정확한 전사 모델 버전은 확인되지 않았습니다.
- 암호 입력란과 접근성 정보를 제공하지 않는 일부 앱은 지원되지 않을 수 있습니다. 재부팅·다양한 IME·앱별 실사용 검증은 계속 필요합니다.
- 라이선스는 소유자 선택 대기 상태입니다. 공개 전 `LICENSE-DECISION.md`를 확인하세요.

## 개발·기여

```sh
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m ruff check .
```

테스트는 실제 마이크, 사용자 입력, 로그인 변경, 권한 부여를 수행하면 안 됩니다.

```text
src/agy_dictation/     전사 연결·설정·결과 교환
  macos/              하단 창·전역 단축키·마이크 보조 앱
scripts/              진단·빌드·명시적 설치/실행/삭제
packaging/macos/       앱 권한 선언
tests/                격리된 자동 테스트
docs/                 구조·설치·개인정보·검증·Windows 계획
```

[구조](docs/architecture.md) · [설치](docs/installation.md) · [개인정보](docs/privacy.md) · [보안 감사](docs/security-audit.md) · [Windows 계획](docs/windows-port.md) · [공개 체크리스트](docs/release-checklist.md)

공개 전에는 `python scripts/prepublish_check.py --check-identity`로 이력과 작성자 정보를 확인합니다. 승인한 업무용 도메인 또는 GitHub no-reply 주소를 허용하며 토큰·개인 경로 검사는 유지합니다. `python scripts/install_git_guard.py --apply`로 로컬 커밋 검사를 설치할 수 있습니다.

공식 참고: [요금제](https://antigravity.google/docs/plans) · [CLI 설치](https://antigravity.google/docs/cli/install) · [음성 입력](https://www.antigravity.google/docs/cli/commands/voice/)
