# AGY Dictation

사용자가 직접 설치하고 로그인한 **공식 AGY CLI의 대화형 음성 입력과 외부 편집기 콜백**을 이용하는 독립적인 실험용 macOS 전역 받아쓰기 래퍼입니다. Google의 공식 제품이나 공식 지원 통합이 아닙니다.

Python과 PyObjC의 AppKit HUD로 녹음·처리 상태를 표시하고, 현재 입력 위치에 Unicode 네이티브 입력 이벤트로 결과를 넣는 구조입니다. 클립보드 복사·붙여넣기나 입력 대상 앱 전환을 사용하지 않습니다. 모든 앱과 입력 필드에서 동작한다는 보장은 없습니다.

## 현재 상태

- 원래 개인용 구현에서는 실제 음성 → TextEdit 삽입을 3회 확인했고, 포커스와 클립보드가 유지됐습니다. 녹음 중·처리 중 취소는 약 0.2초로 관찰됐습니다. 이는 원래 환경의 관찰값이며 성능 보장이 아닙니다.
- **이 저장소로 재구성한 구현은 깨끗한 Mac에서 설치하거나 종단 간 재검증하지 않았습니다.** 이전 구현의 성공을 이 저장소의 검증 결과로 간주하면 안 됩니다.
- Windows는 구현하지 않았습니다. 백엔드의 `pty`·`fcntl`도 Unix 전용이므로 UI 교체만으로 이식되지 않습니다.
- 아직 공개 저장소가 없으며, 저장소 라이선스는 결정 전입니다. 이 문서는 법적 라이선스를 부여하지 않습니다.

## 계정, 음성 기능, 모델

공식 [플랜 안내](https://antigravity.google/docs/plans)는 무료 개인 계정을 포함한 모든 플랜에 CLI 접근을 제공한다고 설명합니다. [2026년 5월 기능 발표](https://antigravity.google/blog/google-io-2026-feature-deep-dive)는 모든 티어에 음성 기능을 발표했습니다. 다만 **이 프로젝트는 무료 개인 계정의 실제 음성 동작을 시험하지 않았으며 무제한 사용을 주장하지 않습니다.** 계정별 제공 범위와 한도는 공식 서비스에 따릅니다.

[CLI 음성 문서](https://www.antigravity.google/docs/cli/commands/voice/)에 따르면 음성 입력에는 대화형 TUI가 필요하며 `--print` 모드와 비즈니스·엔터프라이즈 계정에서는 지원되지 않습니다. 공식 발표는 음성 모델을 **Gemini Audio**라고 설명합니다. 정확한 모델 ID와 버전은 확인되지 않았으며 CLI의 일반 채팅 모델과 같다고 추정하지 않습니다. 공식 안내 확인일: 2026-09-19.

## 개발 환경 준비

macOS, Python 3.11 이상, PyObjC 및 사용자가 설치한 공식 `agy` CLI가 필요합니다. Python 버전·의존성의 최종 기준은 `pyproject.toml`입니다. 아래 명령으로 개발 환경을 준비합니다. 현재 Mac에서 단위 테스트 65개, 정적 검사, 소스·wheel 패키지 빌드와 개발용 앱 번들 생성은 확인했지만, 새 Mac 설치 절차를 검증한 것은 아닙니다. 저장소 루트에서 실행합니다.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m unittest discover -s tests
.venv/bin/python scripts/doctor.py
.venv/bin/python scripts/build_macos.py --output dist
```

마지막 명령은 로컬 앱 번들과 LaunchAgent 메타데이터를 `dist`에 준비하는 단계입니다. **설치·LaunchAgent 등록·앱 실행은 하지 않습니다.** 번들은 해당 컴퓨터의 기존 Python 프레임워크와 설치 모듈에 의존합니다. 개발용 ad-hoc 서명만 적용하며, 독립 실행형 또는 Developer ID 서명·공증된 배포 파일은 아닙니다. 빌드 성공만으로 실행과 권한 설정이 검증되지는 않습니다.

먼저 터미널에서 본인의 `agy` CLI를 열어 로그인, 초기 안내, 필요한 동의와 작업 폴더 신뢰 판단을 직접 완료하세요. 이 래퍼가 동의나 신뢰 설정을 자동 승인한다고 가정하면 안 됩니다. 음성 기능에 필요한 인증 갱신 여부는 공식 CLI 안내를 따릅니다. 마이크·손쉬운 사용·입력 모니터링 등 macOS 권한은 실제 실행 방식과 시스템 요청을 확인하여 사용자가 직접 허용해야 합니다. `doctor.py` 결과는 실제 받아쓰기 성공의 증거가 아닙니다.

## 사용 방식

별도로 서비스 실행과 권한 설정이 완료된 환경에서 사용할 단축키입니다. 코드상 서비스 진입점은 `.venv/bin/agy-dictation` 또는 `.venv/bin/python -m agy_dictation`입니다. 서비스는 별도 음성 엔진 앱을 사용하므로 개발 의존성 설치만으로 실행 준비가 끝나는 것은 아닙니다. 엔진 앱 배치 및 설치 절차는 깨끗한 Mac 검증 후 확정해야 합니다.

| 입력 | 동작 |
| --- | --- |
| Control + grave/backtick (`) 또는 Control + backslash (\) | 녹음 시작 / 녹음 종료 후 처리 |
| Esc | 진행 중인 녹음 또는 처리 취소 |

입력할 앱의 편집 위치에 커서를 둔 뒤 시작하고, HUD의 녹음·처리 상태를 확인합니다. 성공한 결과는 현재 입력 위치에 삽입됩니다. 처리 중 포커스나 선택 영역 변경, 보안 입력 필드, IME 조합 상태는 별도 검증 대상입니다. 실패·취소 시 기존 텍스트를 보존해야 하며, 늦게 도착한 결과가 삽입되어서는 안 됩니다.

입력 실패 또는 입력 결과를 확인할 수 없는 경우 로컬 데이터 폴더에 `last-transcript.txt` 복구 파일이 남을 수 있습니다. [개인정보 문서](docs/privacy.md)의 보관 경계를 확인하세요.

## 폴더 구성

```text
src/agy_dictation/       # 전사 연결, 설정, 결과 전달
  macos/                # 하단 UI, 전역 단축키, 마이크 보조 앱
tests/                  # 음성·키 입력을 실행하지 않는 자동 테스트
scripts/                # 읽기 전용 진단, 로컬 앱 빌드
packaging/macos/         # 앱 권한 선언
.github/workflows/      # Linux/macOS 정적·단위·패키지 빌드 검사
docs/                   # 구조, 개인정보, 검증, Windows 이식 계획
```

레포 기본 설치 경로는 `ProListenDictation` 데이터 폴더와 `com.prolisten.agy-dictation` 서비스입니다. 기존 개인용 `AGYDictation` 설치와 구분됩니다. 동시에 두 서비스를 시작하면 단축키가 겹치므로 병행 실행하지 마세요.

## 공개 전 개인정보 검사

```sh
python scripts/prepublish_check.py
python scripts/prepublish_check.py --check-identity
python scripts/install_git_guard.py --apply
```

작성자 이름·이메일은 커밋에 공개됩니다. `privacy-policy.json`에 승인한 업무용 도메인 또는 GitHub no-reply 주소를 사용하고 검사하세요. 현재 공개 커밋에는 `prolisten.net` 업무용 주소를 허용합니다. 이 허용은 커밋 메타데이터에만 적용되며 소스의 이메일·토큰 검사는 유지합니다. 로컬 개발용 앱 번들과 등록 정보에는 컴퓨터 경로가 포함되므로 업로드하지 않습니다. [보안 점검 결과](docs/security-audit.md)에 수정 사항과 남은 검증을 기록했습니다.

## 문서

- [구조와 책임](docs/architecture.md)
- [보안 점검 결과](docs/security-audit.md)
- [개인정보와 인증 경계](docs/privacy.md)
- [검증 방법과 증거 범위](docs/testing.md)
- [Windows 이식 범위](docs/windows-port.md)
- [공개·배포 전 체크리스트](docs/release-checklist.md)
