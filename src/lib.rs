//! 텍스트 암호화/복호화 공통 로직.
//!
//! 출력 형식: Base64URL( 버전(1) | nonce(24) | 암호문+인증태그(16) )
//! 암호문 안쪽: 플래그(1: 0=원문, 1=deflate 압축) | 데이터

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use chacha20poly1305::aead::Aead;
use chacha20poly1305::{KeyInit, XChaCha20Poly1305, XNonce};
use sha2::{Digest, Sha256};

pub mod cli;
pub mod clip;

const VERSION: u8 = 1;
const NONCE_LEN: usize = 24;
const RAW: u8 = 0;
const DEFLATE: u8 = 1;
const KEY_DOMAIN: &[u8] = b"txtcrypt-v1-key";
/// -k 옵션이나 TXTCRYPT_KEY 가 없을 때 쓰는 내장 키.
pub(crate) const DEFAULT_PASSPHRASE: &str = "txtcrypt-default-9f2c41d7a6e05b38";
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

/// 암호문을 한 조각이 `max` 글자를 넘지 않도록 `i/n:데이터` 형태로 나눈다.
/// 한 조각에 다 들어가면 접두어 없이 그대로 돌려준다.
pub fn split_token(token: &str, max: usize) -> Vec<String> {
    if token.len() <= max {
        return vec![token.to_string()];
    }
    let mut digits = 1;
    let (per, total) = loop {
        let per = max.saturating_sub(2 * digits + 2).max(1);
        let total = token.len().div_ceil(per);
        if total.to_string().len() <= digits {
            break (per, total);
        }
        digits += 1;
    };
    // 암호문은 ASCII 이므로 바이트 단위로 잘라도 안전하다.
    token
        .as_bytes()
        .chunks(per)
        .enumerate()
        .map(|(i, c)| format!("{}/{}:{}", i + 1, total, std::str::from_utf8(c).unwrap()))
        .collect()
}

pub enum AssembleError {
    /// 아직 없는 조각 번호들과 전체 조각 수.
    Missing(Vec<usize>, usize),
    Invalid(String),
}

fn parse_piece(word: &str) -> Option<(usize, usize, &str)> {
    let (head, data) = word.split_once(':')?;
    let (i, n) = head.split_once('/')?;
    let (i, n) = (i.parse::<usize>().ok()?, n.parse::<usize>().ok()?);
    let ok = !data.is_empty()
        && data.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_');
    (ok && i >= 1 && i <= n).then_some((i, n, data))
}

/// 입력 텍스트에서 암호문을 꺼낸다. `i/n:` 조각이 있으면 순서대로 이어 붙이고
/// (조각이 아닌 단어는 무시), 없으면 공백만 제거한 전체를 암호문으로 본다.
pub fn assemble(text: &str) -> Result<String, AssembleError> {
    let pieces: Vec<_> = text.split_whitespace().filter_map(parse_piece).collect();
    let Some(&(_, total, _)) = pieces.first() else {
        return Ok(text.split_whitespace().collect());
    };
    if pieces.iter().any(|p| p.1 != total) {
        return Err(AssembleError::Invalid("서로 다른 암호문의 조각이 섞여 있습니다".into()));
    }
    let mut slots = vec![None; total];
    for (i, _, data) in pieces {
        slots[i - 1].get_or_insert(data);
    }
    let missing: Vec<usize> =
        slots.iter().enumerate().filter(|(_, s)| s.is_none()).map(|(i, _)| i + 1).collect();
    if missing.is_empty() {
        Ok(slots.into_iter().flatten().collect())
    } else {
        Err(AssembleError::Missing(missing, total))
    }
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
    fn split_and_assemble() {
        let t = encrypt("SELECT 1;\n".repeat(3).as_bytes(), "k").unwrap();
        for max in [16, 20, 50] {
            let parts = split_token(&t, max);
            assert!(parts.len() > 1);
            assert!(parts.iter().all(|p| p.len() <= max));
            // 순서가 섞이고 메신저 잡음(이름, 시각)이 끼어도 복원된다.
            let mut noisy: Vec<String> =
                parts.iter().map(|p| format!("[홍길동] [오후 3:02] {p}")).collect();
            noisy.reverse();
            assert!(assemble(&noisy.join("\n")).ok().as_deref() == Some(t.as_str()));
        }
        assert_eq!(split_token(&t, 1000), vec![t.clone()]);
        let parts = split_token(&t, 20);
        assert!(parts.len() >= 3 && parts[0].starts_with("1/"));
        match assemble(&parts[1..].join(" ")) {
            Err(AssembleError::Missing(m, n)) => assert_eq!((m, n), (vec![1], parts.len())),
            _ => panic!("빠진 조각을 알려줘야 한다"),
        }
    }

    #[test]
    fn compresses_repetitive_text() {
        let s = "INSERT INTO t (a, b) VALUES (1, 2);\n".repeat(200);
        assert!(encrypt(s.as_bytes(), "k").unwrap().len() < s.len() / 4);
    }
}
