# macOS 소스 설치

현재는 Python.org 프레임워크와 로컬 Python 환경을 사용하는 **개발자 프리뷰**입니다. 독립 실행형 배포판은 아닙니다. README의 순서대로 환경 준비 → 빌드 → 설치 → CLI 초기 설정 → 권한 설정 → 시작을 진행합니다.

## 명령의 역할

| 명령 | 변경 범위 |
| --- | --- |
| `doctor.py` | 설치 전제 조건만 읽음. `--strict`는 누락 시 실패, `--installed`는 설치 파일도 확인 |
| `build_macos.py --output dist` | 지정한 빈 출력 폴더에 개발용 앱·등록 메타데이터·편집기 실행 파일을 생성 |
| `manage_macos.py install --from-build dist --apply` | 사용자 앱 폴더와 이 도구 전용 데이터/로그/LaunchAgent에 설치. 실행하지 않음 |
| `manage_macos.py setup-cli --apply` | 전사용 폴더에서 사용자의 공식 AGY CLI를 대화형으로 실행 |
| `manage_macos.py permissions` | 권한을 허용할 앱 위치를 안내. 권한 변경 없음 |
| `manage_macos.py start --apply` | 사용자 로그인 서비스 등록 또는 재시작 |
| `manage_macos.py stop --apply` | 이 버전의 서비스와 인증된 엔진 종료 |
| `manage_macos.py status` | 등록 여부와 설치 파일 존재 여부 확인. 실제 녹음 성공을 의미하지 않음 |
| `manage_macos.py uninstall --apply` | 이 버전의 설치 파일 제거. 사용자 데이터·로그·공식 CLI 계정은 보존 |

변경 명령에서 `--apply`를 생략하면 계획만 출력합니다. 자동 테스트에서는 임시 디렉터리와 모의 시스템 명령만 사용하며 현재 설치본에 적용하지 않습니다.

## 기본 위치

- 앱: `~/Applications/ProListen Voice Engine.app`
- 데이터: `~/Library/Application Support/ProListenDictation`
- 로그: `~/Library/Logs/ProListenDictation`
- 로그인 서비스: `~/Library/LaunchAgents/com.prolisten.agy-dictation.plist`

설치 기록은 데이터 폴더의 `installation.json`에 저장됩니다. 기록과 대상이 맞지 않으면 삭제를 거부합니다. 폴더를 옮겼거나 다른 환경에서 빌드했다면 원래 위치와 Python 환경을 복구한 뒤 처리하세요.

## Python 환경

빌드·설치·시작 도구는 같은 `.venv/bin/python`으로 실행하세요. 빌드에 사용한 Python과 설치 시 Python이 다르면 설치를 거부합니다. `.venv`, 저장소 경로, 기존 Python 프레임워크는 설치 후에도 유지해야 합니다. 번들은 ad-hoc 서명이며 Developer ID 서명·공증된 독립 제품이 아닙니다.

## 로그인과 권한

`setup-cli`는 실제 전사 세션과 같은 작업 폴더에서 AGY를 실행합니다. 별도 터미널 폴더에서 로그인만 한 것으로 이 폴더의 신뢰 확인이 끝났다고 가정하지 마세요. 약관·데이터 공유·폴더 신뢰는 본인이 직접 판단합니다.

Python의 입력 제어 권한을 먼저 허용하고 서비스를 시작한 뒤 음성 엔진의 마이크 요청을 허용합니다. 시스템 설정 이름은 macOS 버전에 따라 다릅니다. 권한을 수정한 뒤에는 `start --apply`로 다시 시작하세요.

## 업데이트

1. 기존 버전에서 `stop --apply`, `uninstall --apply`를 실행합니다.
2. 소스를 업데이트하고 같은 가상환경의 의존성을 갱신합니다.
3. 기존 `dist`를 재사용하지 말고 비어 있는 새 출력 폴더에 빌드합니다.
4. 새 빌드로 설치하고 시작합니다.

기존 개인용 `com.antigravity.dictation` 서비스가 켜져 있으면 새 버전 시작을 거부합니다. 다른 서비스를 자동으로 끄거나 덮어쓰지 않습니다.

## 삭제 후 데이터

복구 전사문과 로그는 의도적으로 남깁니다. 필요 없어졌을 때 본인이 전용 데이터·로그 폴더의 내용을 확인한 뒤 정리할 수 있습니다. 공식 AGY CLI 로그인 정보는 이 도구의 삭제 대상이 아닙니다. macOS 권한 목록의 항목도 자동으로 변경하지 않습니다.
