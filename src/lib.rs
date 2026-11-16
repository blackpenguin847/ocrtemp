//! 텍스트 암호화/복호화 공통 로직.
//!
//! 출력 형식: Base64URL( 버전(1) | nonce(24) | 암호문+인증태그(16) )
//! 암호문 안쪽: 플래그(1: 0=원문, 1=deflate 압축) | 데이터

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use chacha20poly1305::aead::Aead;
use chacha20poly1305::{KeyInit, XChaCha20Poly1305, XNonce};
use sha2::{Digest, Sha256};
use std::io::{IsTerminal, Read, Write};

const VERSION: u8 = 1;
const NONCE_LEN: usize = 24;
const RAW: u8 = 0;
const DEFLATE: u8 = 1;
const KEY_DOMAIN: &[u8] = b"txtcrypt-v1-key";
/// -k 옵션이나 TXTCRYPT_KEY 가 없을 때 쓰는 내장 키.
const DEFAULT_PASSPHRASE: &str = "txtcrypt-default-9f2c41d7a6e05b38";
pub const KEY_ENV: &str = "TXTCRYPT_KEY";

fn derive_key(passphrase: &str) -> [u8; 32] {
    let mut h = Sha256::new();
    h.update(KEY_DOMAIN);
    h.update(passphrase.as_bytes());
    h.finalize().into()
}

pub fn encrypt(plain: &[u8], passphrase: &str) -> Result<String, String> {
    let packed = miniz_oxide::deflate::compress_to_vec(plain, 9);
    let (flag, body) = if packed.len() < plain.len() {
        (DEFLATE, packed.as_slice())
    } else {
        (RAW, plain)
    };
    let mut inner = Vec::with_capacity(body.len() + 1);
    inner.push(flag);
    inner.extend_from_slice(body);

    let mut nonce = [0u8; NONCE_LEN];
    getrandom::getrandom(&mut nonce).map_err(|e| format!("난수 생성 실패: {e}"))?;
    let cipher = XChaCha20Poly1305::new(&derive_key(passphrase).into());
    let ct = cipher
        .encrypt(XNonce::from_slice(&nonce), inner.as_slice())
        .map_err(|_| "암호화 실패".to_string())?;

    let mut out = Vec::with_capacity(1 + NONCE_LEN + ct.len());
    out.push(VERSION);
    out.extend_from_slice(&nonce);
    out.extend_from_slice(&ct);
    Ok(URL_SAFE_NO_PAD.encode(out))
}

pub fn decrypt(token: &str, passphrase: &str) -> Result<Vec<u8>, String> {
    // 메신저/메일에 붙여넣으며 끼어든 공백·줄바꿈은 무시한다.
    let cleaned: String = token.chars().filter(|c| !c.is_whitespace()).collect();
    let raw = URL_SAFE_NO_PAD
        .decode(cleaned.trim_end_matches('='))
        .map_err(|_| "암호문 형식이 올바르지 않습니다 (Base64 아님)".to_string())?;
    if raw.len() < 1 + NONCE_LEN + 16 {
        return Err("암호문이 너무 짧습니다 (일부가 잘렸을 수 있음)".into());
    }
    if raw[0] != VERSION {
        return Err(format!("지원하지 않는 암호문 버전입니다: {}", raw[0]));
    }
    let (nonce, ct) = raw[1..].split_at(NONCE_LEN);
    let cipher = XChaCha20Poly1305::new(&derive_key(passphrase).into());
    let inner = cipher
        .decrypt(XNonce::from_slice(nonce), ct)
        .map_err(|_| "복호화 실패: 키가 다르거나 암호문이 손상되었습니다".to_string())?;
    match inner.split_first() {
        Some((&RAW, body)) => Ok(body.to_vec()),
        Some((&DEFLATE, body)) => miniz_oxide::inflate::decompress_to_vec(body)
            .map_err(|_| "압축 해제 실패".to_string()),
        _ => Err("암호문 내용이 올바르지 않습니다".into()),
    }
}

/// 두 실행 파일이 공유하는 명령행 옵션.
pub struct Args {
    pub passphrase: String,
    pub input: Option<String>,
    pub output: Option<String>,
}

pub fn parse_args(name: &str, what: &str) -> Args {
    let mut key = None;
    let mut input = None;
    let mut output = None;
    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        match a.as_str() {
            "-h" | "--help" | "/?" => {
                println!(
                    "사용법: {name} [-k 키] [-o 출력파일] [입력파일]\n\n\
                     {what}\n\n\
                     \x20 입력파일   생략하면 표준 입력에서 읽습니다 (파이프 또는 직접 붙여넣기)\n\
                     \x20 -o 파일    결과를 파일로 저장 (생략하면 화면에 출력)\n\
                     \x20 -k 키      암호 키. 생략하면 환경변수 {KEY_ENV}, 그것도 없으면 내장 키 사용"
                );
                std::process::exit(0);
            }
            "-k" | "--key" => key = Some(it.next().unwrap_or_else(|| die("-k 뒤에 키가 필요합니다"))),
            "-o" | "--out" => {
                output = Some(it.next().unwrap_or_else(|| die("-o 뒤에 파일명이 필요합니다")))
            }
            _ if input.is_none() => input = Some(a),
            _ => die(&format!("알 수 없는 인자: {a} (-h 로 사용법 확인)")),
        }
    }
    let passphrase = key
        .or_else(|| std::env::var(KEY_ENV).ok().filter(|s| !s.is_empty()))
        .unwrap_or_else(|| DEFAULT_PASSPHRASE.to_string());
    Args { passphrase, input, output }
}

pub fn read_input(path: Option<&str>) -> Vec<u8> {
    match path {
        Some(p) => std::fs::read(p).unwrap_or_else(|e| die(&format!("{p} 읽기 실패: {e}"))),
        None => {
            let stdin = std::io::stdin();
            if stdin.is_terminal() {
                let eof = if cfg!(windows) { "Ctrl+Z 후 Enter" } else { "Ctrl+D" };
                eprintln!("내용을 붙여넣고 새 줄에서 {eof} 를 누르세요:");
            }
            let mut buf = Vec::new();
            stdin
                .lock()
                .read_to_end(&mut buf)
                .unwrap_or_else(|e| die(&format!("입력 읽기 실패: {e}")));
            buf
        }
    }
}

pub fn write_output(path: Option<&str>, data: &[u8]) {
    match path {
        Some(p) => {
            std::fs::write(p, data).unwrap_or_else(|e| die(&format!("{p} 쓰기 실패: {e}")))
        }
        None => {
            let mut out = std::io::stdout().lock();
            if out.write_all(data).and_then(|_| out.flush()).is_err() {
                die("화면 출력 실패 (UTF-8이 아닌 내용일 수 있음). -o 로 파일에 저장하세요");
            }
        }
    }
}

pub fn die(msg: &str) -> ! {
    eprintln!("오류: {msg}");
    std::process::exit(1);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip() {
        for s in ["", "a", "SELECT * FROM 사용자 WHERE id = 1;\r\n", &"SELECT 1;\n".repeat(500)] {
            let t = encrypt(s.as_bytes(), "k").unwrap();
            assert!(t.is_ascii());
            assert_eq!(decrypt(&t, "k").unwrap(), s.as_bytes());
        }
    }

    #[test]
    fn whitespace_tolerated_and_wrong_key_rejected() {
        let t = encrypt(b"hello world", "k").unwrap();
        let broken = format!(" {}\r\n{} \n", &t[..10], &t[10..]);
        assert_eq!(decrypt(&broken, "k").unwrap(), b"hello world");
        assert!(decrypt(&t, "other").is_err());
        assert!(decrypt(&t[..t.len() - 4], "k").is_err());
    }

    #[test]
    fn compresses_repetitive_text() {
        let s = "INSERT INTO t (a, b) VALUES (1, 2);\n".repeat(200);
        assert!(encrypt(s.as_bytes(), "k").unwrap().len() < s.len() / 4);
    }
}
