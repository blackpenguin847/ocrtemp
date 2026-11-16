# txtcrypt

쿼리·프로시저 같은 문자열을 **텍스트 형태의 암호문**으로 바꾸는 도구와, 그것을 되돌리는 도구입니다.
Rust로 작성했고 실행 파일은 두 개로 나뉩니다.

| 실행 파일 | 역할 |
|---|---|
| `encrypt.exe` | 문자열 → 암호문 텍스트 (한 줄, 영문·숫자·`-`·`_` 만 사용) |
| `decrypt.exe` | 암호문 텍스트 → 원래 문자열 |

## 빌드 (Windows)

[Rust](https://rustup.rs) 설치 후:

```
cargo build --release
```

`target\release\encrypt.exe`, `target\release\decrypt.exe` 가 생깁니다.

## 사용법

```
encrypt [-k 키] [-o 출력파일] [입력파일]
decrypt [-k 키] [-o 출력파일] [입력파일]
```

```
encrypt proc.sql                 암호문을 화면에 출력
encrypt proc.sql -o proc.enc     암호문을 파일로 저장
decrypt proc.enc -o proc.sql     원래 파일로 복원
type proc.sql | encrypt | clip   암호문을 클립보드로 복사
```

- 입력파일을 생략하면 표준 입력에서 읽습니다. 콘솔에서 직접 실행하면 내용을 붙여넣고
  새 줄에서 `Ctrl+Z`, `Enter` 를 누르면 됩니다.
- 입력은 바이트 그대로 처리하므로 복호화 결과는 원본과 인코딩·줄바꿈까지 동일합니다.
  UTF-8이 아닌 파일(CP949 등)을 복호화할 때는 `-o` 로 파일에 저장하세요.
- 복호화할 때 암호문 중간에 섞인 공백·줄바꿈은 무시합니다.

## 키

- `-k 키` 또는 환경변수 `TXTCRYPT_KEY` 로 지정합니다. 암호화와 복호화에 같은 키를 써야 합니다.
- 둘 다 없으면 실행 파일에 내장된 기본 키를 씁니다. 이 경우 `decrypt.exe` 를 가진 사람은
  누구나 풀 수 있으니, 실제로 숨겨야 하는 내용에는 키를 지정하세요.

## 방식

deflate 압축(작아질 때만) → XChaCha20-Poly1305 암호화 → Base64URL 인코딩.
키는 `SHA-256(키 문자열)` 로 만들며, 암호문이 변조되거나 키가 다르면 복호화가 거부됩니다.
같은 내용을 암호화해도 매번 다른 암호문이 나옵니다.
